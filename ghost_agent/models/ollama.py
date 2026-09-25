"""Ollama LLM client using the native /api/chat endpoint.

Docs: https://docs.ollama.com/api/chat

Handles chat completions with streaming support for real-time thinking display
in the TUI.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "user").upper()
        content = message.get("content", "")
        lines.append(f"{role}:\n{content}")
    lines.append("ASSISTANT:")
    return "\n\n".join(lines)


def _extract_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()
    if isinstance(data, dict) and "error" in data:
        return str(data["error"]).strip()
    return (response.text or "").strip()


def ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    timeout: int = 60,
    stream: bool = False,
    on_token: Optional[Callable[[str], None]] = None,
    response_format: str | Dict[str, Any] | None = None,
) -> str | Iterator[str]:
    """Chat with an Ollama model via the native /api/chat endpoint.

    Args:
        model: Model name (e.g. "qwen3-coder:8b")
        messages: Chat messages [{"role": "user", "content": "..."}]
        options: Ollama options (temperature, num_predict, etc.)
        base_url: Ollama base URL (default: http://localhost:11434)
        timeout: Request timeout in seconds
        stream: If True, return an iterator of tokens
        on_token: Callback for each token when streaming
        response_format: Optional "json" or JSON schema; never an authorization grant

    Returns:
        Complete response text (or iterator if stream=True)
    """
    base = base_url or "http://localhost:11434"
    base = base.rstrip("/")
    # Strip any trailing /api or /v1 paths the user may have set
    if base.endswith("/v1"):
        base = base[:-3]
    if base.endswith("/api"):
        base = base[:-4]
    url = f"{base}/api/chat"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if options:
        payload["options"] = options
    if response_format is not None:
        if response_format != "json" and not isinstance(response_format, dict):
            raise ValueError("response_format must be 'json' or a JSON schema object")
        payload["format"] = response_format

    if stream:
        return _stream_chat(url, payload, timeout, on_token, base, messages, model, options)

    response = requests.post(url, json=payload, timeout=timeout)
    return _handle_response(response, url, base, messages, model, timeout, options, response_format)


def _handle_response(
    response: requests.Response,
    url: str,
    base: str,
    messages: List[Dict[str, str]],
    model: str,
    timeout: int,
    options: Optional[Dict[str, Any]],
    response_format: str | Dict[str, Any] | None = None,
) -> str:
    """Handle a non-streaming response, with fallback to /api/generate."""
    if response.status_code == 404:
        error_text = _extract_error(response).lower()
        if "model" in error_text and ("not found" in error_text or "does not exist" in error_text):
            raise requests.HTTPError(
                f"Ollama model not found: {model}. "
                "Set GHOST_MODEL to an installed model or run `ollama pull <model>`.",
                response=response,
            )
        # Fallback to /api/generate
        generate_url = f"{base}/api/generate"
        generate_payload: Dict[str, Any] = {
            "model": model,
            "prompt": _messages_to_prompt(messages),
            "stream": False,
        }
        if options:
            generate_payload["options"] = options
        if response_format is not None:
            generate_payload["format"] = response_format
        generate_response = requests.post(
            generate_url, json=generate_payload, timeout=timeout
        )
        if generate_response.status_code == 404:
            raise requests.HTTPError(
                "Ollama API not found at base URL. "
                "Ensure GHOST_OLLAMA_BASE_URL is like http://localhost:11434 "
                "and not suffixed with /api or /v1.",
                response=generate_response,
            )
        generate_response.raise_for_status()
        data = generate_response.json()
        return data.get("response", "")

    response.raise_for_status()
    data = response.json()

    # Native Ollama response: message.content
    message = data.get("message", {})
    return message.get("content", "")


def _stream_chat(
    url: str,
    payload: Dict[str, Any],
    timeout: int,
    on_token: Optional[Callable[[str], None]],
    base: str,
    messages: List[Dict[str, str]],
    model: str,
    options: Optional[Dict[str, Any]],
) -> Iterator[str]:
    """Stream chat tokens from Ollama.

    Ollama streaming format: newline-delimited JSON objects, each with
    message.content (string) and done (bool). The last object has done=true
    and empty content.
    """
    try:
        response = requests.post(url, json=payload, timeout=timeout, stream=True)

        if response.status_code == 404:
            error_text = _extract_error(response).lower()
            if "model" in error_text and ("not found" in error_text or "does not exist" in error_text):
                raise requests.HTTPError(
                    f"Ollama model not found: {model}. "
                    "Set GHOST_MODEL to an installed model or run `ollama pull <model>`.",
                    response=response,
                )
            response.raise_for_status()

        response.raise_for_status()

        for line in response.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            if chunk.get("done"):
                break

            token = chunk.get("message", {}).get("content", "")
            if token:
                if on_token:
                    on_token(token)
                yield token

    except requests.ConnectionError:
        # Fallback: try non-streaming
        payload["stream"] = False
        result = _handle_response(
            requests.post(url, json=payload, timeout=timeout),
            url, base, messages, model, timeout, options,
            payload.get("format"),
        )
        if on_token:
            on_token(result)
        yield result


def ollama_chat_streaming(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    timeout: int = 60,
    on_token: Optional[Callable[[str], None]] = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Iterator[str]:
    """Convenience wrapper for streaming chat.

    Returns an iterator of tokens. If on_token is provided,
    it's called for each token as a side effect.
    """
    result = ollama_chat(
        model=model,
        messages=messages,
        options=options,
        base_url=base_url,
        timeout=timeout,
        stream=True,
        on_token=on_token,
        response_format=response_format,
    )
    if isinstance(result, str):
        # Fallback returned a complete string
        yield result
    else:
        yield from result


def fetch_models(base_url: Optional[str] = None, timeout: int = 5) -> List[Dict[str, Any]]:
    """Fetch installed models from Ollama via GET /api/tags.

    Returns a list of model dicts, each with at least 'name' and 'size' keys.
    Returns an empty list if Ollama is not reachable.
    """
    base = base_url or "http://localhost:11434"
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    if base.endswith("/api"):
        base = base[:-4]
    url = f"{base}/api/tags"

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data.get("models", [])
    except (requests.ConnectionError, requests.HTTPError, ValueError, OSError):
        return []


def format_model_size(size_bytes: int) -> str:
    """Format model size in human-readable form."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.0f} MB"
    return f"{size_bytes / 1_000:.0f} KB"
