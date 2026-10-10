"""Tests for the private-network fetch block (TH3)."""
import socket

import pytest

from ghost_agent.tools import web_tools


def _addrinfo(host: str) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]


def _patch_resolution(monkeypatch, mapping):
    def fake_getaddrinfo(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("resolution failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in mapping[host]]

    monkeypatch.setattr(web_tools.socket, "getaddrinfo", fake_getaddrinfo)


def _never_requested(url, **kwargs):
    raise AssertionError("network request attempted to " + url)


def test_localhost_is_refused(monkeypatch):
    _patch_resolution(monkeypatch, {"localhost": ["127.0.0.1"]})
    monkeypatch.setattr(web_tools.requests, "get", _never_requested)
    assert web_tools.fetch_source_text("http://localhost/page") == {}
    assert web_tools.fetch_source_text("http://localhost/x") == {}


def test_private_ip_is_refused(monkeypatch):
    _patch_resolution(monkeypatch, {"10.1.2.3": ["10.1.2.3"]})
    monkeypatch.setattr(web_tools.requests, "get", _never_requested)
    assert web_tools.fetch_source_text("http://10.1.2.3/page") == {}


def test_metadata_ip_is_refused(monkeypatch):
    _patch_resolution(monkeypatch, {"169.254.169.254": ["169.254.169.254"]})
    monkeypatch.setattr(web_tools.requests, "get", _never_requested)
    assert web_tools.fetch_source_text("http://169.254.169.254/latest/meta-data") == {}


def test_ipv6_loopback_is_refused(monkeypatch):
    _patch_resolution(monkeypatch, {"ip6-localhost": ["::1"]})
    monkeypatch.setattr(web_tools.requests, "get", _never_requested)
    assert web_tools.fetch_source_text("http://ip6-localhost/page") == {}


def test_unresolvable_host_is_refused(monkeypatch):
    _patch_resolution(monkeypatch, {})  # nothing resolves
    monkeypatch.setattr(web_tools.requests, "get", _never_requested)
    assert web_tools.fetch_source_text("http://nonexistent.invalid/page") == {}


def test_public_ip_is_allowed(monkeypatch):
    _patch_resolution(monkeypatch, {"93.184.216.34": ["93.184.216.34"]})

    class _Response:
        status_code = 200
        headers = {"Content-Type": "text/html", "Content-Length": "300"}
        url = "http://93.184.216.34/page"
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"<p>" + b"x" * 300 + b"</p>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(web_tools.requests, "get", lambda url, **kw: _Response())
    result = web_tools.fetch_source_text("http://93.184.216.34/page")
    assert result["evidence_text"].startswith("xxx") or result["evidence_text"]


def test_redirect_to_private_ip_is_refused_at_hop(monkeypatch):
    hops = []

    def fake_get(url, **kwargs):
        hops.append(url)

        class _Response:
            status_code = 302
            headers = {"Location": "http://10.9.9.9/secret"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()

    _patch_resolution(monkeypatch, {"example.com": ["93.184.216.34"], "10.9.9.9": ["10.9.9.9"]})
    monkeypatch.setattr(web_tools.requests, "get", fake_get)
    assert web_tools.fetch_source_text("http://example.com/redirect") == {}
    assert hops == ["http://example.com/redirect"]


def test_env_override_allows_private_fetch(monkeypatch):
    _patch_resolution(monkeypatch, {"10.1.2.3": ["10.1.2.3"]})
    monkeypatch.setenv("GHOST_ALLOW_PRIVATE_FETCH", "1")

    class _Response:
        status_code = 200
        headers = {"Content-Type": "text/html", "Content-Length": "300"}
        url = "http://10.1.2.3/page"
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"<p>" + b"y" * 300 + b"</p>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(web_tools.requests, "get", lambda url, **kw: _Response())
    assert web_tools.fetch_source_text("http://10.1.2.3/page")


def test_is_forbidden_destination_classifies_addresses(monkeypatch):
    mapping = {
        "pub.example": ["8.8.8.8"],
        "lo.example": ["127.0.0.1"],
        "priv.example": ["192.168.0.1"],
        "meta.example": ["169.254.169.254"],
    }
    _patch_resolution(monkeypatch, mapping)
    assert not web_tools._is_forbidden_destination("pub.example")
    assert web_tools._is_forbidden_destination("lo.example")
    assert web_tools._is_forbidden_destination("priv.example")
    assert web_tools._is_forbidden_destination("meta.example")
