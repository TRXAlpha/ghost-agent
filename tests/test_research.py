from ghost_agent import cli
from ghost_agent.research import ResearchProvenanceGraph
from ghost_agent.tools import web_tools


def test_source_credibility_prioritizes_scientific_sources() -> None:
    assert web_tools.source_credibility("https://www.nasa.gov/example")["score"] >= 0.8
    assert web_tools.source_credibility("https://example.medium.com/post")["score"] < 0.5


def test_research_topic_strips_intent_prefix_and_junk_query_terms() -> None:
    topic = cli._normalise_research_topic(
        "deep research on microbial iron cycling ocean carbon sequestration"
    )

    assert topic == "microbial iron cycling ocean carbon sequestration"
    queries = cli._generate_intersection_queries(topic)
    assert all("deep effect" not in query for query in queries)
    assert all("relates to research" not in query for query in queries)


def test_research_query_requires_multiple_topic_anchors() -> None:
    topic = "microbial iron cycling ocean carbon sequestration"

    assert not cli._is_valid_research_query("deep effect on research", topic)
    assert cli._is_valid_research_query("microbial iron cycling carbon export evidence", topic)


def test_research_provenance_graph_traces_and_deduplicates_queries(tmp_path) -> None:
    graph = ResearchProvenanceGraph(tmp_path, "iron cycling and carbon export")
    query_id = graph.add_query("microbial iron cycling carbon export", 1)
    source_id = graph.add_source(query_id, {
        "title": "Study",
        "url": "https://example.edu/study",
        "tier": "public_or_academic_institution",
        "score": 0.9,
    })
    claim_id = graph.add_claim({"claim": "A source statement", "source_quote": "quote"}, source_id)
    graph.add_verdict(claim_id, "DIRECTLY_SUPPORTED", {"quote_coverage": 1.0})
    path = graph.save()

    assert not graph.is_novel_query("carbon export microbial iron cycling")
    data = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 4
    assert any(edge["relation"] == "quoted_support" for edge in data["edges"])


def test_claim_validation_requires_a_verbatim_source_quote() -> None:
    findings = [{
        "title": "Primary source",
        "url": "https://www.nasa.gov/evidence",
        "tier": "public_or_academic_institution",
        "score": 0.9,
        "evidence_text": (
            "Researchers measured the effect with calibrated instruments during "
            "three independent experiments. The observed signal remained stable."
        ),
    }]
    valid = {
        "claim": "Researchers measured the effect in three independent experiments.",
        "source_index": 1,
        "source_quote": "Researchers measured the effect with calibrated instruments during three independent experiments.",
    }
    hallucinated = {
        "claim": "The effect proves a new law of physics.",
        "source_index": 1,
        "source_quote": "The effect proves a new law of physics.",
    }

    assert cli._validate_extracted_claims([valid, hallucinated], findings) == [
        cli._validate_extracted_claims([valid], findings)[0]
    ]


def test_claim_validation_rejects_generic_background_for_specific_topic() -> None:
    findings = [{
        "title": "Background model",
        "url": "https://doi.org/example",
        "tier": "scholarly_abstract_metadata",
        "score": 0.75,
        "evidence_text": "PISCES-v2 is an ocean biogeochemical model for carbon studies.",
    }]
    generic = {
        "claim": "PISCES-v2 is an ocean biogeochemical model for carbon studies.",
        "source_index": 1,
        "source_quote": "PISCES-v2 is an ocean biogeochemical model for carbon studies.",
    }

    assert not cli._validate_extracted_claims(
        [generic], findings, "microbial iron cycling ocean carbon sequestration"
    )


def test_claim_validation_cannot_use_topical_paraphrase_to_pad_generic_quote() -> None:
    findings = [{
        "title": "Background model",
        "url": "https://doi.org/example",
        "tier": "scholarly_abstract_metadata",
        "score": 0.75,
        "evidence_text": "The model predicts carbon cycling under global change scenarios.",
    }]
    padded = {
        "claim": "Microbial iron cycling affects ocean carbon sequestration scenarios.",
        "source_index": 1,
        "source_quote": "The model predicts carbon cycling under global change scenarios.",
    }

    assert not cli._validate_extracted_claims(
        [padded], findings, "microbial iron cycling ocean carbon sequestration"
    )


def test_claim_validation_requires_three_specific_anchors_for_multi_concept_topic() -> None:
    findings = [{
        "title": "Broad background",
        "url": "https://doi.org/example",
        "tier": "scholarly_abstract_metadata",
        "score": 0.75,
        "evidence_text": "Microbial carbon sequestration in the ocean is an active research area.",
    }]
    background = {
        "claim": "Microbial carbon sequestration in the ocean is an active research area.",
        "source_index": 1,
        "source_quote": "Microbial carbon sequestration in the ocean is an active research area.",
    }

    assert not cli._validate_extracted_claims(
        [background], findings, "microbial iron cycling ocean carbon sequestration"
    )


def test_source_statement_fallback_copies_rather_than_paraphrases() -> None:
    sentence = (
        "Microbial iron cycling can alter ocean carbon export through changes in "
        "nutrient availability and primary production."
    )
    statements = cli._extract_source_statements(
        [{
            "title": "Source",
            "url": "https://example.edu/paper",
            "tier": "public_or_academic_institution",
            "score": 0.9,
            "source_kind": "full_text",
            "evidence_excerpt": sentence,
        }],
        "microbial iron cycling ocean carbon",
    )

    assert statements[0]["claim"] == sentence
    assert statements[0]["source_quote"] == sentence
    assert statements[0]["claim_type"] == "source_statement"


def test_adjacent_leads_are_traceable_to_source_text() -> None:
    sentence = (
        "The microbial carbon pump (MCP) forms recalcitrant dissolved organic carbon "
        "in the ocean and merits separate investigation."
    )
    leads = cli._adjacent_research_leads(
        "microbial iron cycling ocean carbon sequestration",
        [{"title": "Ocean study", "evidence_excerpt": sentence}],
    )

    assert leads[0]["term"] == "MCP"
    assert leads[0]["quote"] == sentence
    assert "MCP relationship to" in leads[0]["query"]


def test_verification_preserves_only_the_original_evidence(monkeypatch) -> None:
    claims = [{
        "claim": "Researchers measured the effect in three independent experiments.",
        "source_quote": "Researchers measured the effect with calibrated instruments during three independent experiments.",
        "source_url": "https://www.nasa.gov/evidence",
        "source_title": "Primary source",
        "source_tier": "public_or_academic_institution",
        "source_score": 0.9,
        "claim_type": "fact",
        "evidence_type": "observational",
    }]
    findings = [{"url": claims[0]["source_url"], "evidence_excerpt": "Context"}]
    monkeypatch.setattr(
        cli,
        "ollama_chat",
        lambda **kwargs: (
            '[{"claim_index": 1, "source_entailment": "DIRECTLY_SUPPORTED", '
            '"reasoning": "The quotation reports the measurement."}]'
        ),
    )

    verified = cli._verify_claims(claims, findings, "model")

    assert verified[0]["claim"] == claims[0]["claim"]
    assert verified[0]["source_quote"] == claims[0]["source_quote"]
    assert verified[0]["source_entailment"] == "DIRECTLY_SUPPORTED"


def test_exact_quote_overrides_a_spurious_contradiction_verdict(monkeypatch) -> None:
    claim = {
        "claim": "The experiment measured a stable signal in three independent trials.",
        "source_quote": "The experiment measured a stable signal in three independent trials.",
        "source_url": "https://www.nasa.gov/evidence",
        "source_title": "Primary source",
        "source_tier": "public_or_academic_institution",
        "source_score": 0.9,
    }
    monkeypatch.setattr(
        cli,
        "ollama_chat",
        lambda **kwargs: '[{"claim_index": 1, "source_entailment": "CONTRADICTED"}]',
    )

    verified = cli._verify_claims([claim], [{"url": claim["source_url"]}], "model")

    assert verified[0]["source_entailment"] == "DIRECTLY_SUPPORTED"


def test_model_description_is_not_labeled_as_a_direct_observation(monkeypatch) -> None:
    claim = {
        "claim": "The model simulates ocean carbon cycling.",
        "source_quote": "The model simulates ocean carbon cycling and nutrient transport.",
        "source_url": "https://doi.org/example",
        "source_title": "Model paper",
        "source_tier": "scholarly_abstract_metadata",
        "source_score": 0.75,
    }
    monkeypatch.setattr(
        cli,
        "ollama_chat",
        lambda **kwargs: (
            '[{"claim_index": 1, "source_entailment": "DIRECTLY_SUPPORTED", '
            '"observation_status": "directly_observed", "confidence_in_claim": "high"}]'
        ),
    )

    verified = cli._verify_claims([claim], [{"url": claim["source_url"]}], "model")

    assert verified[0]["claim_type"] == "model_dependent"
    assert verified[0]["observation_status"] == "theoretically_predicted"
    assert verified[0]["confidence_in_claim"] == "medium"


def test_research_report_excludes_model_claims_without_source_text(monkeypatch) -> None:
    source_text = (
        "A controlled experiment measured a stable signal in three independent trials. "
        "The authors state that the result requires further replication."
    )

    monkeypatch.setattr(
        web_tools,
        "web_search",
        lambda query: [{"title": "Evidence", "url": "https://www.nasa.gov/evidence", "snippet": "misleading"}],
    )
    monkeypatch.setattr(web_tools, "academic_search", lambda query: [])
    monkeypatch.setattr(
        web_tools,
        "fetch_source_text",
        lambda url: {
            "url": url,
            "evidence_text": source_text,
            "tier": "public_or_academic_institution",
            "score": 0.9,
            "domain": "nasa.gov",
        },
    )
    monkeypatch.setattr(web_tools, "source_credibility", lambda url: {"score": 0.9, "domain": "nasa.gov"})

    def fake_chat(**kwargs):
        system = kwargs["messages"][0]["content"]
        if "extracting ATOMIC CLAIMS" in system:
            return (
                '[{"claim": "A controlled experiment measured a stable signal in three trials.", '
                '"source_index": 1, '
                '"source_quote": "A controlled experiment measured a stable signal in three independent trials.", '
                '"claim_type": "established_fact", "evidence_type": "observational"}, '
                '{"claim": "The signal proves a new law of physics.", "source_index": 1, '
                '"source_quote": "The signal proves a new law of physics."}]'
            )
        if "rigorous scientific fact-checker" in system:
            return '[{"claim_index": 1, "source_entailment": "DIRECTLY_SUPPORTED"}]'
        return '{"queries": ["signal replication limitation"], "done": false}'

    monkeypatch.setattr(cli, "ollama_chat", fake_chat)

    report = cli._research_directly("signal experiment", "model", max_searches=1)

    assert "stable signal in three trials" in report
    assert "proves a new law of physics" not in report
    assert "Search snippets and unfetched pages are not treated as evidence" in report
