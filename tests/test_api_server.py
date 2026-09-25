from pathlib import Path
import http.client
import json
import threading
import socket
import time

import pytest

from ghost_agent.api_server import ControlPlaneAPI, create_server, MAX_REQUEST_BYTES


def test_local_api_exposes_control_plane_and_optional_token(tmp_path: Path) -> None:
    api = ControlPlaneAPI(tmp_path, token="token")
    assert api.dispatch("GET", "/v1/snapshot")[0] == 401
    status, created = api.dispatch("POST", "/v1/goals", {"title": "Build API"}, "Bearer token")
    assert status == 201
    assert created["title"] == "Build API"
    assert api.dispatch("POST", "/v1/observations", {
        "entity_id": "service.api", "kind": "service", "state": {"up": True}, "source": "test"
    }, "Bearer token")[0] == 201


@pytest.fixture
def local_server(tmp_path, request):
    server = create_server(tmp_path, port=0, token=getattr(request, "param", ""))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def request(server, *, headers=None, body=b'{"title":"Safe goal"}', path="/v1/goals", method="POST"):
    # Submit one complete wire request. Header-only refusal has a separate
    # test; sending a late body after refusal may reset a Windows connection.
    fields = {"Host": f"127.0.0.1:{server.server_port}", "Content-Length": str(len(body)),
              "Content-Type": "application/json", **(headers or {})}
    wire = f"{method} {path} HTTP/1.0\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items())
    with socket.create_connection(server.server_address, timeout=2) as connection:
        connection.sendall(wire.encode() + b"\r\n" + body)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return response.status, response.read()


def test_same_origin_browser_and_local_client_work(local_server):
    assert request(local_server)[0] == 201
    assert request(local_server, headers={"Origin": f"http://127.0.0.1:{local_server.server_port}"})[0] == 201


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.invalid"}, {"Origin": "null"},
    {"Host": "attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"},
    {"Host": "127.0.0.1.attacker.invalid:8765"},
])
def test_external_browser_and_rebinding_requests_refused(local_server, headers):
    assert request(local_server, headers=headers)[0] == 403
    assert request(local_server, headers=headers, method="GET", path="/v1/snapshot")[0] == 403


@pytest.mark.parametrize("headers,status", [
    ({"Content-Length": "-1"}, 400), ({"Content-Length": "no"}, 400),
    ({"Content-Length": str(MAX_REQUEST_BYTES + 1)}, 413),
    ({"Content-Type": "text/plain"}, 415), ({"Transfer-Encoding": "chunked"}, 400),
])
def test_bad_framing_rejected_before_body_read(local_server, headers, status):
    assert request(local_server, headers=headers)[0] == status


@pytest.mark.parametrize("body", [b"null", b"[]", b'"text"', b"{bad", b"\xff"])
def test_invalid_json_and_nonobjects_return_400(local_server, body):
    assert request(local_server, body=body)[0] == 400


@pytest.mark.parametrize("confidence", [False, None, "bad", float("nan"), float("inf"), -1, 2])
def test_observation_confidence_is_validated(local_server, confidence):
    body = json.dumps({"entity_id": "test", "kind": "service", "state": {}, "source": "test",
                       "confidence": confidence}).encode()
    assert request(local_server, path="/v1/observations", body=body)[0] == 400


def test_public_binding_is_refused(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        create_server(tmp_path, host="0.0.0.0", port=0)


@pytest.mark.parametrize("local_server", ["test-token"], indirect=True)
def test_http_authentication_precedes_body_parsing(local_server):
    assert request(local_server, body=b"{invalid")[0] == 401
    assert request(local_server, headers={"Authorization": "Bearer wrong"})[0] == 401
    assert request(local_server, headers={"Authorization": "Bearer test-token"})[0] == 201
    assert request(local_server, method="GET", path="/v1/snapshot")[0] == 401
    # The dashboard shell has no user state; the snapshot remains protected.
    assert request(local_server, method="GET", path="/")[0] == 200


@pytest.mark.parametrize("header", ["Host", "Content-Length", "Origin"])
def test_duplicate_security_headers_refused(local_server, header):
    with socket.create_connection(local_server.server_address, timeout=2) as connection:
        wire = "POST /v1/goals HTTP/1.0\r\n"
        values = {"Host": f"127.0.0.1:{local_server.server_port}", "Content-Length": "2",
                  "Origin": f"http://127.0.0.1:{local_server.server_port}"}
        for name, value in values.items():
            wire += f"{name}: {value}\r\n"
            if name == header:
                wire += f"{name}: {value}\r\n"
        connection.sendall((wire + "Content-Type: application/json\r\n\r\n{}").encode())
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status in (400, 403)
        response.read()


def test_invalid_host_is_rejected_without_waiting_for_body(local_server, tmp_path):
    with socket.create_connection(local_server.server_address, timeout=2) as connection:
        connection.sendall(b"POST /v1/goals HTTP/1.0\r\nHost: attacker.invalid\r\n"
                           b"Content-Length: 1000\r\nContent-Type: application/json\r\n\r\n")
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 403
        response.read()
    assert not (tmp_path / ".ghost" / "goals.json").exists()


@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"1e999"])
def test_nonfinite_values_cannot_enter_observation_state(local_server, value):
    body = b'{"entity_id":"x","kind":"service","source":"test","state":{"value":' + value + b'}}'
    assert request(local_server, path="/v1/observations", body=body)[0] == 400


@pytest.mark.parametrize("limit", [0, -1, 65, True, 1.5, "8"])
def test_invalid_client_limits_are_refused(tmp_path, limit):
    with pytest.raises(ValueError, match="max_clients"):
        create_server(tmp_path, port=0, max_clients=limit)


def test_connection_limit_closes_excess_clients_and_recovers(tmp_path, monkeypatch):
    server = create_server(tmp_path, port=0, token="", max_clients=1)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = server.finish_request
    original_thread = server.process_request_thread

    def hold_request(sock, address):
        entered.set()
        assert release.wait(3)
        original(sock, address)

    def finish_thread(sock, address):
        try:
            original_thread(sock, address)
        finally:
            finished.set()

    monkeypatch.setattr(server, "finish_request", hold_request)
    monkeypatch.setattr(server, "process_request_thread", finish_thread)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as first:
            first.sendall(f"GET / HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n\r\n".encode())
            assert entered.wait(2)
            with socket.create_connection(server.server_address, timeout=2) as excess:
                assert excess.recv(1) == b""
            release.set()
            assert b"200 OK" in first.recv(4096)
            while first.recv(4096):
                pass
            assert finished.wait(2)
        assert request(server)[0] == 201
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_handler_failure_releases_client_slot(tmp_path, monkeypatch):
    server = create_server(tmp_path, port=0, max_clients=1)
    first, second = socket.socketpair()
    try:
        def fail(*args):
            raise RuntimeError("injected handler failure")

        monkeypatch.setattr(server, "finish_request", fail)
        monkeypatch.setattr(server, "handle_error", lambda *args: None)
        assert server.client_slots.acquire(blocking=False)
        server.process_request_thread(first, ("127.0.0.1", 1))
        assert server.client_slots.acquire(blocking=False)
        server.client_slots.release()
    finally:
        first.close()
        second.close()
        server.server_close()


def test_thread_start_failure_releases_client_slot(tmp_path, monkeypatch):
    server = create_server(tmp_path, port=0, max_clients=1)
    first, second = socket.socketpair()
    try:
        def fail(*args):
            raise RuntimeError("injected thread start failure")

        monkeypatch.setattr(threading.Thread, "start", fail)
        with pytest.raises(RuntimeError, match="thread start"):
            server.process_request(first, ("127.0.0.1", 1))
        assert server.client_slots.acquire(blocking=False)
        server.client_slots.release()
    finally:
        first.close()
        second.close()
        server.server_close()


@pytest.mark.parametrize("limit", [0, -1, 301, True, "15", float("nan"), float("inf")])
def test_invalid_read_deadline_is_refused(tmp_path, limit):
    with pytest.raises(ValueError, match="read_timeout"):
        create_server(tmp_path, port=0, read_timeout=limit)


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_continuously_dripping_client_cannot_reset_read_deadline(tmp_path, phase, monkeypatch):
    server = create_server(tmp_path, port=0, token="", max_clients=1, read_timeout=0.3)
    completed = threading.Event()
    original = server.process_request_thread

    def finish(sock, address):
        try:
            original(sock, address)
        finally:
            completed.set()

    monkeypatch.setattr(server, "process_request_thread", finish)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    stop = threading.Event()
    sender = None
    sent = []
    try:
        with socket.create_connection(server.server_address, timeout=2) as client:
            if phase == "headers":
                prefix = b"GET / HTTP/1.0\r\nX-Slow: "
            else:
                prefix = (f"POST /v1/goals HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n"
                          "Content-Length: 1000\r\nContent-Type: application/json\r\n\r\n").encode()
            client.sendall(prefix)

            def drip():
                while not stop.wait(0.02):
                    try:
                        client.sendall(b" ")
                        sent.append(1)
                    except OSError:
                        return

            sender = threading.Thread(target=drip)
            sender.start()
            started = time.monotonic()
            response = b""
            try:
                while chunk := client.recv(4096):
                    response += chunk
            except (ConnectionResetError, ConnectionAbortedError):
                # Header expiry closes without a response. Windows may report
                # reset/abort instead of EOF while the sender is still dripping.
                if phase != "headers":
                    raise
            assert time.monotonic() - started < 1.5
            assert len(sent) >= 2
            if phase == "body":
                assert b"408 Request Timeout" in response
            assert completed.wait(1)
            assert request(server)[0] == 201
    finally:
        stop.set()
        if sender:
            sender.join(timeout=2)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
