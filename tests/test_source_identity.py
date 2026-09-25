import pytest

from ghost_agent.tools import web_tools


@pytest.mark.parametrize("url", [
    "https://nature.com.untrusted.invalid/paper", "https://notnature.com/paper",
    "https://arxiv.org.attacker.invalid/paper", "https://pubmed.attacker.invalid/paper",
    "https://nature.com@attacker.invalid/paper", "file://nature.com/paper",
    "https://nature.com:invalid/paper", "https://[malformed/paper",
])
def test_lookalike_or_invalid_host_never_gets_scholarly_rank(url):
    assert web_tools.source_credibility(url)["score"] < 0.5


def test_exact_hosts_subdomains_ports_and_tertiary_classification():
    assert web_tools.source_credibility("https://WWW.NATURE.COM.:443/paper")["score"] == 0.85
    assert web_tools.source_credibility("https://journals.aps.org/paper")["score"] == 0.85
    assert web_tools.source_credibility("https://en.wikipedia.org/wiki/Science")["score"] == 0.15


def test_academic_records_keep_work_publisher_and_retrieval_identity(monkeypatch):
    abstract = "A controlled study measures carbon cycling and reports uncertainty across repeated observations. " * 3
    index = {}
    for offset, word in enumerate(abstract.split()):
        index.setdefault(word, []).append(offset)
    works = [{"id": f"https://openalex.org/W{i}", "title": f"Research study {i}", "type": "article",
              "doi": f"https://doi.org/10.1234/study{i}", "abstract_inverted_index": index,
              "primary_location": {"landing_page_url": f"https://publisher{i}.org/article",
                                   "source": {"id": f"https://openalex.org/S{i}",
                                              "host_organization": f"https://openalex.org/P{i}"}}}
             for i in range(1, 4)]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": works + [{**works[0], "is_retracted": True}]}

    monkeypatch.setattr(web_tools.requests, "get", lambda *a, **kw: Response())
    results = web_tools.academic_search("carbon cycling")
    assert len(results) == 3
    assert {item["domain"] for item in results} == {f"publisher{i}.org" for i in range(1, 4)}
    assert len({web_tools.source_group(item) for item in results}) == 3
    assert all(item["source_kind"] == "abstract" and item["retrieved_via"] == "openalex.org" for item in results)
    copy = {"url": "https://repository.edu/copy", "doi": "10.1234/STUDY1"}
    assert web_tools.evidence_identity(copy) == web_tools.evidence_identity(results[0])


def test_same_publisher_is_not_counted_as_independent_hosts():
    first = {"url": "https://journal-a.org/paper", "publisher_id": "https://openalex.org/P1"}
    second = {"url": "https://journal-b.org/paper", "publisher_id": "https://openalex.org/P1"}
    assert web_tools.source_group(first) == web_tools.source_group(second)


def test_url_fragments_do_not_create_new_evidence():
    assert web_tools.evidence_identity({"url": "https://example.edu/paper#a"}) == (
        web_tools.evidence_identity({"url": "https://example.edu/paper#b"}))


def test_work_alias_still_matches_when_one_copy_has_no_doi():
    first = {"work_id": "https://openalex.org/W1", "doi": "10.1234/example", "url": "https://publisher.org/a"}
    second = {"work_id": "https://openalex.org/W1", "url": "https://repository.edu/a"}
    assert web_tools.evidence_aliases(first) & web_tools.evidence_aliases(second)


def test_missing_abstract_remains_discovery_not_evidence(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": "https://openalex.org/W1", "title": "Study without indexed abstract",
                "doi": "https://doi.org/10.1234/example", "type": "article",
                "abstract_inverted_index": None, "primary_location": {}}]}

    monkeypatch.setattr(web_tools.requests, "get", lambda *a, **kw: Response())
    results = web_tools.academic_search("carbon cycling")
    assert len(results) == 1
    assert results[0]["source_kind"] == "metadata_only"
    assert "evidence_text" not in results[0]
    assert "evidence_excerpt" not in results[0]
    assert not results[0]["snippet"]


@pytest.mark.parametrize("fetch_case", ["unavailable", "low_quality_redirect", "full_text"])
def test_metadata_only_discovery_requires_acceptable_fetched_evidence(monkeypatch, fetch_case):
    from ghost_agent import cli

    captured = []
    fetched_urls = []
    source = {"title": "Carbon cycling study", "url": "https://doi.org/10.1234/example",
              "doi": "10.1234/example", "score": 0.75, "source_kind": "metadata_only",
              "snippet": "This discovery description is not source evidence."}
    monkeypatch.setattr(web_tools, "academic_search", lambda query: [source])
    monkeypatch.setattr(web_tools, "web_search", lambda query: [])

    def fetch(url):
        fetched_urls.append(url)
        if fetch_case == "unavailable":
            return {}
        final_url = ("https://example.edu/paper" if fetch_case == "full_text"
                     else "https://untrusted.invalid/paper")
        return {"url": final_url, **web_tools.source_credibility(final_url),
                "source_kind": "full_text",
                "evidence_text": "Carbon cycling was measured in controlled observations. " * 5}

    monkeypatch.setattr(web_tools, "fetch_source_text", fetch)
    monkeypatch.setattr(cli, "ollama_chat", lambda *a, **kw: '{"queries": [], "done": true}')
    monkeypatch.setattr(cli, "_extract_evidenced_claims", lambda findings, *a: captured.extend(findings) or [])
    monkeypatch.setattr(cli, "_extract_source_statements", lambda *a: [])
    monkeypatch.setattr(cli, "_fallback_verified_report", lambda *a: "evidence gap")
    report = cli._research_directly("carbon cycling", "scripted", max_searches=1)
    assert fetched_urls == [source["url"]]
    if fetch_case == "full_text":
        assert report == "evidence gap"
        assert len(captured) == 1
        assert captured[0]["source_kind"] == "full_text"
        assert captured[0]["url"] == "https://example.edu/paper"
        assert source["snippet"] not in captured[0]["evidence_excerpt"]
    else:
        assert "No fetchable, credible source text" in report
        assert not captured


@pytest.mark.parametrize("include_web", [False, True])
def test_research_retains_diverse_publishers_and_searches_both_channels(monkeypatch, include_web):
    from ghost_agent import cli
    captured = []
    searches = []
    sources = [{"title": f"Carbon cycling study {i}", "url": f"https://doi.org/10.1234/paper{i}",
                "doi": f"10.1234/paper{i}", "domain": "doi.org", "retrieved_via": "openalex.org",
                "publisher_id": f"https://openalex.org/P{i}", "score": 0.75,
                "tier": "scholarly_abstract_metadata", "source_kind": "abstract",
                "evidence_text": "Carbon cycling was measured with controlled observations. " * 5}
               for i in range(3)]
    monkeypatch.setattr(web_tools, "academic_search", lambda query: sources + [sources[0]])

    def search(query):
        searches.append(query)
        return ([{"title": "Public carbon cycling data", "url": "https://example.edu/carbon"}]
                if include_web else [])

    monkeypatch.setattr(web_tools, "web_search", search)
    monkeypatch.setattr(web_tools, "fetch_source_text", lambda url: {
        "url": url, "domain": "example.edu", "score": 0.9, "tier": "public_or_academic_institution",
        "evidence_text": "Public observations of carbon cycling show unresolved uncertainty. " * 5,
    })
    monkeypatch.setattr(cli, "ollama_chat", lambda *a, **kw: '{"queries": [], "done": true}')
    monkeypatch.setattr(cli, "_extract_evidenced_claims", lambda findings, *a: captured.extend(findings) or [])
    monkeypatch.setattr(cli, "_extract_source_statements", lambda *a: [])
    monkeypatch.setattr(cli, "_fallback_verified_report", lambda *a: "evidence gap")
    assert cli._research_directly("carbon cycling", "scripted", max_searches=1) == "evidence gap"
    assert len(searches) == 1
    assert len(captured) == 3
    assert len({web_tools.evidence_identity(item) for item in captured}) == 3
    assert len({web_tools.source_group(item) for item in captured}) == 3
    assert any(item["url"] == "https://example.edu/carbon" for item in captured) == include_web
