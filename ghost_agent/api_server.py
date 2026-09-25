"""Loopback-only JSON control-plane transport built on :mod:`control_plane`."""
from __future__ import annotations

import json
import os
import hmac
import socket
import threading
import io
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .control_plane import GhostSDK
from .dashboard import render_dashboard

MAX_REQUEST_BYTES = 64 * 1024


class _DeadlineReader(io.RawIOBase):
    """Socket reads share one deadline; partial arrivals never reset it."""

    def __init__(self, connection: socket.socket, seconds: float) -> None:
        self.connection = connection
        self.deadline = time.monotonic() + seconds

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Request read deadline exceeded")
        self.connection.settimeout(min(5.0, remaining))
        return self.connection.recv_into(buffer)


class ControlPlaneAPI:
    """Small local API facade; authentication is opt-in via GHOST_API_TOKEN."""

    def __init__(self, project_root: Path, token: str | None = None) -> None:
        self.sdk = GhostSDK(project_root)
        self.token = token if token is not None else os.getenv("GHOST_API_TOKEN", "")

    def dispatch(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 authorization: str = "") -> tuple[int, dict[str, Any]]:
        if self.token and not hmac.compare_digest(authorization.encode(), f"Bearer {self.token}".encode()):
            return HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}
        if payload is not None and not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object"}
        data = payload or {}
        try:
            json.dumps(data, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            return HTTPStatus.BAD_REQUEST, {"error": "body must contain finite JSON values"}
        if method == "GET" and path == "/v1/snapshot":
            return HTTPStatus.OK, self.sdk.snapshot()
        if method == "POST" and path == "/v1/goals":
            title = data.get("title", "")
            if not isinstance(title, str) or not title.strip():
                return HTTPStatus.BAD_REQUEST, {"error": "title is required"}
            kind = data.get("kind", "outcome")
            if kind not in ("mission", "project", "outcome", "task", "action"):
                return HTTPStatus.BAD_REQUEST, {"error": "invalid goal kind"}
            return HTTPStatus.CREATED, self.sdk.create_goal(title.strip(), kind=kind)
        if method == "POST" and path == "/v1/observations":
            required = ("entity_id", "kind", "state", "source")
            if any(key not in data for key in required) or not isinstance(data.get("state"), dict):
                return HTTPStatus.BAD_REQUEST, {"error": "entity_id, kind, state, and source are required"}
            if any(not isinstance(data[key], str) or not data[key].strip() for key in ("entity_id", "kind", "source")):
                return HTTPStatus.BAD_REQUEST, {"error": "observation identifiers must be nonempty strings"}
            confidence = data.get("confidence", 1.0)
            if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                return HTTPStatus.BAD_REQUEST, {"error": "confidence must be a finite number from 0 to 1"}
            return HTTPStatus.CREATED, self.sdk.observe(
                str(data["entity_id"]), str(data["kind"]), data["state"], str(data["source"]),
                float(confidence),
            )
        return HTTPStatus.NOT_FOUND, {"error": "endpoint not found"}


def create_server(project_root: Path, host: str = "127.0.0.1", port: int = 8765,
                  token: str | None = None, *, max_clients: int = 8,
                  read_timeout: float = 15.0) -> ThreadingHTTPServer:
    """Construct a loopback server; callers own its serving and shutdown lifecycle."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Control-plane API only permits a loopback host")
    if type(max_clients) is not int or not 1 <= max_clients <= 64:
        raise ValueError("max_clients must be an integer from 1 to 64")
    if type(read_timeout) not in (int, float) or not 0.05 <= read_timeout <= 300:
        raise ValueError("read_timeout must be a finite number from 0.05 to 300 seconds")
    api = ControlPlaneAPI(project_root, token=token)

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def setup(self) -> None:
            super().setup()
            self.rfile.close()
            self.rfile = io.BufferedReader(_DeadlineReader(self.connection, read_timeout))

        def end_headers(self) -> None:
            # The input budget is separate from response writes. It does not
            # claim to interrupt SDK execution or bound response generation.
            self.connection.settimeout(self.timeout)
            super().end_headers()

        def _trusted_request(self, authenticate: bool = True) -> bool:
            authorities = {f"127.0.0.1:{self.server.server_port}",
                           f"localhost:{self.server.server_port}", f"[::1]:{self.server.server_port}"}
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            if (len(hosts) != 1 or hosts[0].lower() not in authorities
                    or len(origins) > 1
                    or (origins and origins[0].lower() != "http://" + hosts[0].lower())
                    or self.headers.get("Sec-Fetch-Site", "") == "cross-site"):
                self._reply(HTTPStatus.FORBIDDEN, {"error": "untrusted request origin or host"})
                return False
            if authenticate and api.token and not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), f"Bearer {api.token}".encode()):
                self._reply(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return False
            return True

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if not self._trusted_request(authenticate=self.path != "/"):
                return
            if self.path == "/":
                encoded = render_dashboard().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(encoded)
                return
            status, body = api.dispatch("GET", self.path, authorization=self.headers.get("Authorization", ""))
            self._reply(status, body)

        def do_POST(self) -> None:  # noqa: N802
            if not self._trusted_request():
                return
            if self.headers.get("Transfer-Encoding") is not None:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "transfer encoding is unsupported"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "one nonnegative Content-Length is required"})
                return
            if len(lengths[0]) > 10 or int(lengths[0]) > MAX_REQUEST_BYTES:
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body is too large"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json is required"})
                return
            try:
                length = int(lengths[0])
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete body")
                def reject_constant(value: str) -> None:
                    raise ValueError(f"Non-JSON constant: {value}")

                payload = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            except TimeoutError:
                self._reply(HTTPStatus.REQUEST_TIMEOUT, {"error": "request read deadline exceeded"})
                return
            except (ValueError, RecursionError):
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            status, body = api.dispatch("POST", self.path, payload, self.headers.get("Authorization", ""))
            self._reply(status, body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET

        def __init__(self, address, handler):
            self.client_slots = threading.BoundedSemaphore(max_clients)
            super().__init__(address, handler)

        def process_request(self, request, client_address):
            # Admission occurs before thread creation, without blocking the
            # accept loop. Excess connections close without reading input.
            if not self.client_slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.client_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.client_slots.release()

    return Server(("127.0.0.1" if host == "localhost" else host, port), Handler)


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve only on loopback; public binding is intentionally refused."""
    with create_server(project_root, host, port) as server:
        server.serve_forever()
