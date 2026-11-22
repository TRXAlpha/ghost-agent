"""Reuse upstream inference, without its automatic discovery or tool authority."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import re
import math
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import UPSTREAM_COMMIT, UPSTREAM_VERSION_PREFIX


class IntegrationError(RuntimeError):
    """An actionable integration/configuration error, not a model response."""


class UpstreamTransportError(ConnectionError):
    """Transient upstream failure (HTTP 5xx/429, unreachable, timeout).

    A ConnectionError, so models.chat retries it with bounded backoff and the
    orchestrator treats a final failure as one failed turn, not a dead job.
    """


_TRANSIENT_HTTP = re.compile(r"returned HTTP (\d{3})")


def _classify_engine_error(exc: Exception) -> Exception:
    """Map upstream EngineConnectionError to transient vs configuration errors."""
    from openjarvis.engine._base import EngineContextLengthError
    message = str(exc)
    if isinstance(exc, EngineContextLengthError):
        return IntegrationError(message)
    match = _TRANSIENT_HTTP.search(message)
    if match:
        status = int(match.group(1))
        return UpstreamTransportError(message) if status >= 500 or status == 429 else IntegrationError(message)
    if "not reachable" in message or "timed out" in message.lower():
        return UpstreamTransportError(message)
    return IntegrationError(message)


def require_upstream() -> str:
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise IntegrationError("OpenJarvis needs Python 3.11–3.13 with Ghost. Use scripts/bootstrap-openjarvis.ps1.")
    try:
        version = importlib.metadata.version("OpenJarvis")
    except importlib.metadata.PackageNotFoundError as exc:
        raise IntegrationError("OpenJarvis is not installed in this interpreter. Use scripts/bootstrap-openjarvis.ps1.") from exc
    if version != UPSTREAM_VERSION_PREFIX and not version.startswith(UPSTREAM_VERSION_PREFIX + ".d"):
        raise IntegrationError(f"Unqualified OpenJarvis version {version}; install the pinned source with scripts/bootstrap-openjarvis.ps1.")
    return version


def project_root() -> Path:
    return Path(os.environ.get("GHOST_PROJECT_ROOT", Path.cwd())).resolve()


def state_root(root: Path) -> Path:
    """One explicitly scoped upstream data directory; no implicit home migration."""
    path = Path(root).resolve() / ".ghost" / "openjarvis"
    if path.resolve() != path:
        raise IntegrationError("OpenJarvis state directory cannot follow links or junctions.")
    return path


def initialize_process(root: Path) -> None:
    """Call once in the dedicated launcher, before any OpenJarvis imports."""
    os.environ["GHOST_PROJECT_ROOT"] = str(Path(root).resolve())
    os.environ["OPENJARVIS_HOME"] = str(state_root(root))
    # We do not construct AnalyticsClient/SystemBuilder or load user recipes.
    # Those entry points have their own opt-in qualification, not implicit trust.


@dataclass(frozen=True)
class EngineSettings:
    engine: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    timeout: float = 60
    allow_remote: bool = False

    def __post_init__(self):
        if self.engine not in {"ollama", "openai-compat", "vllm", "sglang", "llamacpp", "lmstudio", "mlx", "exo", "nexa", "lemonade"}:
            raise IntegrationError(f"Unqualified engine: {self.engine}. Use openai-compat for an explicit compatible endpoint.")
        if isinstance(self.timeout, bool) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise IntegrationError("Inference timeout must be finite and between 0 and 300 seconds.")
        url = urlsplit(self.endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise IntegrationError("Endpoint must be an HTTP(S) URL without credentials, query, or fragment.")
        try:
            url.port
        except ValueError as exc:
            raise IntegrationError("Invalid endpoint port.") from exc
        if url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            if not self.allow_remote or url.scheme != "https":
                raise IntegrationError("Remote inference needs HTTPS and GHOST_ALLOW_REMOTE_INFERENCE=1; no automatic cloud fallback.")

    @classmethod
    def from_env(cls, *, base_url=None, timeout=60):
        engine = os.environ.get("GHOST_OPENJARVIS_ENGINE", "ollama")
        endpoint = os.environ.get("GHOST_OPENJARVIS_URL")
        if not endpoint:
            if engine != "ollama":
                raise IntegrationError("Set GHOST_OPENJARVIS_URL explicitly for non-Ollama engines.")
            endpoint = base_url or os.environ.get("GHOST_OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        endpoint = endpoint.rstrip("/")
        for suffix in (("/v1", "/api") if engine == "ollama" else ("/v1",)):
            if endpoint.endswith(suffix):
                endpoint = endpoint[:-len(suffix)]
        return cls(engine, endpoint, float(timeout), os.environ.get("GHOST_ALLOW_REMOTE_INFERENCE") == "1")


@contextmanager
def engine_session(settings: EngineSettings):
    require_upstream()
    # Explicit classes: never get_engine()/SystemBuilder provider discovery.
    if settings.engine == "ollama":
        from openjarvis.engine.ollama import OllamaEngine
        engine = OllamaEngine(host=settings.endpoint, timeout=settings.timeout)
    else:
        from openjarvis.engine.openai_compat_engines import OpenAICompatEngine
        engine = OpenAICompatEngine(host=settings.endpoint, timeout=settings.timeout,
                                    api_key=os.environ.get("GHOST_OPENJARVIS_API_KEY"))
    try:
        yield engine
    finally:
        engine.close()


def _request(messages, options, response_format, engine):
    from openjarvis.core.types import Message, Role

    if not messages or len(json.dumps(messages)) > 120_000:
        raise IntegrationError("Messages must be nonempty and fit the 120,000-character input cap.")
    converted = []
    for message in messages:
        if set(message) - {"role", "content"} or not isinstance(message.get("content"), str):
            raise IntegrationError("Ghost chat bridge accepts text role/content messages only.")
        converted.append(Message(role=Role(message["role"]), content=message["content"]))
    opts = dict(options or {})
    unknown = set(opts) - {"temperature", "num_predict", "num_ctx", "num_gpu"}
    if unknown:
        raise IntegrationError(f"Unsupported inference options: {sorted(unknown)}")
    tokens = opts.pop("num_predict", 2048)
    if type(tokens) is not int or not 1 <= tokens <= 8192:
        raise IntegrationError("num_predict must be an integer between 1 and 8192.")
    temp = opts.pop("temperature", 0.7)
    if type(temp) not in (int, float) or not math.isfinite(temp) or not 0 <= temp <= 2:
        raise IntegrationError("temperature must be between 0 and 2.")
    if engine != "ollama":
        # num_ctx/num_gpu are local Ollama resource hints (context window and GPU
        # layers). OpenAI-compatible servers manage these themselves, so the hint
        # is dropped rather than failing every coding turn (the orchestrator sends
        # num_ctx unconditionally). It never changes request semantics.
        opts.pop("num_ctx", None)
        opts.pop("num_gpu", None)
    for key, value in opts.items():
        if type(value) is not int or not 0 <= value <= 131072:
            raise IntegrationError(f"Invalid {key} option.")
    kwargs = {"temperature": temp, "max_tokens": tokens, **opts}
    effort = reasoning_effort(engine)
    if effort:
        kwargs["reasoning_effort"] = effort
    if response_format is not None:
        if response_format != "json" and not isinstance(response_format, dict):
            raise IntegrationError("response_format must be 'json' or a JSON schema.")
        if isinstance(response_format, dict) and engine == "ollama":
            # Pinned upstream reduces schemas to generic JSON. Never pretend
            # that the provider enforced a schema which it never received.
            raise IntegrationError("Pinned OpenJarvis Ollama supports JSON mode, not schema enforcement. Use 'json' and validate locally.")
        kwargs["response_format"] = ({"type": "json_object"} if response_format == "json" else
                                     {"type": "json_schema", "json_schema": {"name": "ghost", "schema": response_format}})
    return converted, kwargs


def reasoning_effort(engine: str) -> str:
    """GHOST_OPENJARVIS_REASONING_EFFORT for OpenAI-compatible engines (low|medium|high), else "".

    gonkarouter caps GLM-5.3-Flash output at ~4k tokens including reasoning; at the default
    effort it spent all 4000 on reasoning and returned no content (measured 2026-11-22),
    at "low" it returned a 7.4k-character file in ~2k tokens.
    """
    value = os.environ.get("GHOST_OPENJARVIS_REASONING_EFFORT", "").strip().lower()
    return value if engine != "ollama" and value in {"low", "medium", "high"} else ""


def chat(model, messages, options=None, base_url=None, timeout=60, stream=False,
         on_token=None, response_format=None):
    """Ghost's text protocol over upstream engines; no tools are dispatched here."""
    if not isinstance(model, str) or not model.strip():
        raise IntegrationError("Choose a model explicitly; the bridge never picks a paid fallback.")
    settings = EngineSettings.from_env(base_url=base_url, timeout=timeout)
    require_upstream()
    converted, kwargs = _request(messages, options, response_format, settings.engine)

    def generate():
        with engine_session(settings) as engine:
            result = engine.generate(converted, model=model, **kwargs)
            if result.get("tool_calls"):
                raise IntegrationError("Unexpected native tool calls in Ghost's text protocol; nothing was executed.")
            content = result.get("content")
            if not isinstance(content, str) or not content.strip():
                raise IntegrationError("Model returned no text content.")
            if len(content) > 200_000:
                raise IntegrationError("Model output exceeded the 200,000-character cap.")
            return content

    if not stream:
        from openjarvis.engine._base import EngineConnectionError
        try:
            return generate()
        except EngineConnectionError as exc:
            raise _classify_engine_error(exc) from exc

    def iterate():
        # The pinned upstream stream path omits response_format. Preserve JSON
        # semantics using generate, not an unformatted streaming request.
        if response_format is not None:
            result = generate()
            if on_token:
                on_token(result)
            yield result
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise IntegrationError("Consume synchronous streaming in a worker thread, not an active asyncio loop.")
        loop = asyncio.new_event_loop()
        try:
            with engine_session(settings) as engine:
                iterator = engine.stream_full(converted, model=model, **kwargs)
                chars = 0
                try:
                    while True:
                        try:
                            chunk = loop.run_until_complete(iterator.__anext__())
                        except StopAsyncIteration:
                            break
                        if chunk.tool_calls:
                            raise IntegrationError("Unexpected native tool calls; nothing was executed.")
                        token = chunk.content or ""
                        chars += len(token)
                        if chars > 200_000:
                            raise IntegrationError("Streaming output exceeded the character cap.")
                        if token:
                            if on_token:
                                on_token(token)
                            yield token
                    if not chars:
                        raise IntegrationError("Model returned no text content.")
                finally:
                    loop.run_until_complete(iterator.aclose())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    return iterate()


def doctor(root: Path, *, probe=False) -> dict:
    from ..models.chat import RETRY_ATTEMPTS, RETRY_BACKOFF_BASE, RETRY_BACKOFF_CAP  # OJ08 policy source
    report = {"expected_commit": UPSTREAM_COMMIT, "python": sys.version.split()[0],
              "state_root": str(state_root(root)), "executor": "ghost-compatibility",
              "automatic_provider_fallback": False, "external_analytics": False,
              "retry_policy": {"attempts": RETRY_ATTEMPTS, "backoff_base_seconds": RETRY_BACKOFF_BASE,
                               "backoff_cap_seconds": RETRY_BACKOFF_CAP,
                               "retryable": ["ConnectionError", "Timeout", "HTTP_5xx", "OSError"],
                               "never_retried": ["IntegrationError", "ValueError", "HTTP_4xx"]}}
    try:
        report["version"] = require_upstream()
        settings = EngineSettings.from_env(timeout=3)
        report["engine"] = settings.engine
        report["remote_inference_allowed"] = settings.allow_remote
        with engine_session(settings) as engine:
            report["engine_import"] = True
            if probe:
                report["healthy"] = engine.health()
                report["models"] = engine.list_models() if report["healthy"] else []
        try:
            from openjarvis._rust_bridge import get_rust_module
            get_rust_module()
            report["native_extension"] = True
        except ImportError:
            report["native_extension"] = False
    except (IntegrationError, ImportError) as exc:
        report["error"] = str(exc)
    return report
