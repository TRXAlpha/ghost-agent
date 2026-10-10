"""TP2: official-documentation tier in source_credibility."""
from ghost_agent.tools import web_tools


def test_listed_official_docs_hosts_map_to_official_docs() -> None:
    for url in (
        "https://docs.python.org/3/library/os.html",
        "https://doc.rust-lang.org/std/",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
        "https://core.telegram.org/bots/api",
        "https://developer.huawei.com/consumer/cn/doc/",
        "https://learn.microsoft.com/en-us/windows/",
        "https://docs.github.com/en/actions",
        "https://ollama.com/library",
    ):
        cred = web_tools.source_credibility(url)
        assert cred["tier"] == "official_docs", url
        assert cred["score"] == 0.95, url


def test_docs_prefix_host_matches_official_docs() -> None:
    cred = web_tools.source_credibility("https://docs.example.com/guide")
    assert cred["tier"] == "official_docs"
    assert cred["score"] == 0.95


def test_ordinary_site_keeps_old_tier() -> None:
    assert web_tools.source_credibility("https://example.com/post")["tier"] == "unclassified_web"
    assert web_tools.source_credibility("https://en.wikipedia.org/wiki/Science")["tier"] == "tertiary_or_social"
    assert web_tools.source_credibility("https://www.nasa.gov/example")["tier"] == "public_or_academic_institution"
