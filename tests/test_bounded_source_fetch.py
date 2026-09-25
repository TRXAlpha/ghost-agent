import pytest
import requests
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ghost_agent.tools import web_tools


class Page:
    def __init__(self, chunks=(), headers=None, status=200, url="https://example.edu/paper"):
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.chunks = chunks
        self.status_code = status
        self.url = url
        self.encoding = "utf-8"
        self.closed = False
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("failure")

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def install(monkeypatch, pages):
    calls = []
    def get(url, **kwargs):
        assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        calls.append(url)
        return pages[len(calls) - 1]
    monkeypatch.setattr(web_tools.requests, "get", get)
    return calls


def test_html_download_keeps_evidence_and_reports_excerpt_truncation(monkeypatch):
    body = b"<article>" + b"Measured evidence with uncertainty. " * 15 + b"</article>"
    page = Page([body])
    install(monkeypatch, [page])
    result = web_tools.fetch_source_text(page.url, max_chars=250)
    assert result["download_bytes"] == len(body)
    assert len(result["evidence_text"]) == 250 and result["evidence_truncated"]
    assert page.closed and result["source_kind"] == "full_text"


@pytest.mark.parametrize("headers", [{"Content-Length": "99999999"}, {"Content-Length": "-1"},
    {"Content-Length": "nonsense"}, {"Content-Encoding": "gzip"},
    {"Content-Encoding": "br"}, {"Content-Type": "application/pdf"},
    {"Content-Type": "application/not-html"}])
def test_rejected_headers_never_download_body(monkeypatch, headers):
    page = Page([b"must not read"], headers=headers)
    install(monkeypatch, [page])
    assert web_tools.fetch_source_text(page.url) == {}
    assert page.reads == 0 and page.closed


def test_missing_length_cannot_bypass_byte_cap(monkeypatch):
    page = Page([b"x" * 100, b"x" * 100, b"must not read"])
    install(monkeypatch, [page])
    assert web_tools.fetch_source_text(page.url, max_bytes=150) == {}
    assert page.closed and page.reads == 2


def test_redirect_bodies_not_read_and_relative_target_resolved(monkeypatch):
    redirect = Page([b"must not read"], {"Location": "/final"}, status=302)
    final = Page([b"Evidence with uncertainty. " * 20], url="https://example.edu/final")
    calls = install(monkeypatch, [redirect, final])
    result = web_tools.fetch_source_text(redirect.url)
    assert calls == [redirect.url, final.url]
    assert result["url"] == final.url
    assert redirect.reads == 0 and redirect.closed and final.closed


def test_redirect_loop_is_bounded(monkeypatch):
    pages = [Page(headers={"Location": "/paper"}, status=302) for _ in range(6)]
    calls = install(monkeypatch, pages)
    assert web_tools.fetch_source_text(pages[0].url) == {}
    assert len(calls) == 6 and all(page.closed and page.reads == 0 for page in pages)


@pytest.mark.parametrize("location", ["file:///secret", "https://user:password@example.edu/", "http://["])
def test_invalid_redirect_is_not_followed(monkeypatch, location):
    page = Page(headers={"Location": location}, status=302)
    calls = install(monkeypatch, [page])
    assert web_tools.fetch_source_text(page.url) == {}
    assert len(calls) == 1 and page.closed


def test_interrupted_download_is_not_partial_evidence(monkeypatch):
    page = Page([b"Evidence. " * 30, requests.ConnectionError("interrupted")])
    install(monkeypatch, [page])
    assert web_tools.fetch_source_text(page.url) == {}
    assert page.closed


@pytest.mark.parametrize("limit", [0, -1, True, 0.5, 8388609])
def test_invalid_download_budget_refused(limit):
    with pytest.raises(ValueError, match="max_bytes"):
        web_tools.fetch_source_text("https://example.edu/paper", max_bytes=limit)


def test_real_http_fetch_respects_byte_limit():
    body = b"<html><body>" + b"Observed carbon cycling with uncertainty. " * 30 + b"</body></html>"
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # No Content-Length: the streamed byte count must enforce the cap.
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert web_tools.fetch_source_text(url, max_bytes=300) == {}
        result = web_tools.fetch_source_text(url, max_bytes=len(body))
        assert result["download_bytes"] == len(body)
        assert "Observed carbon cycling" in result["evidence_text"]
        assert not result["evidence_truncated"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
