"""Ghost Agent CLI — the main entry point.

Usage:
  ghost                     Start interactive mode (default)
  ghost run <task.json>     Run a task from a JSON file
  ghost resume <task_id>    Resume a previous run
  ghost audit [task_id]     Transparency report for any run
  ghost status              Show last run summary
  ghost init                Create a starter task.json
  ghost index               Build/update the codebase index
  ghost knowledge           Show knowledge graph report
  ghost search <query>      Search codebase via RAG
  ghost -h                  Show this help
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shlex
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .audit import audit_all, audit_run
from dataclasses import asdict
from .autonomy import CapabilityRegistry, OutcomeStore
from .kernel import (ActionProposal, CapabilityKernel, CapabilityManifest, GoalStore,
                     TransactionJournal)
from .runtime import (AttentionManager, Doctor, EmergencyStop, Event, EventBus, Notification,
                      ScheduledJob, Scheduler)
from .extensions import ExtensionManager
from .approvals import ApprovalStore
from .context_manager import ContextAssembler
from .control_plane import GhostSDK
from .models.routing import ModelOrchestrator, ModelReputationStore
from .promotion import PromotionService
from .api_server import serve as serve_control_plane
from .integrations import IntegrationPlanner
from .self_repair import SelfRepairManager
from .devices import DeviceRegistry
from .mcp_addons import MCPAddonManager
from .intent import IntentStore
from .utils.storage import atomic_json
from .worker import OutcomeWorker, WorkerResult
from .checkins import LocalCheckIns
from .config import get_model, get_research_model, set_model
from .models.ollama import fetch_models, format_model_size
from .models.chat import chat as ollama_chat
from .orchestrator import Orchestrator, Task
from .command_budget import CommandBudget
from .research import ResearchProvenanceGraph
from .tui import create_tui, _IS_TTY, _BOLD, _CYAN, _DIM, _GREEN, _RESET, _BRIGHT_CYAN
from .utils.watch import FileWatcher


# ── Banner ───────────────────────────────────────────────────────────────────

BANNER = f"""
{_BRIGHT_CYAN}{_BOLD}  \U0001f47b Ghost Agent{_RESET}  {_DIM}v0.2.0{_RESET}
{_DIM}  Local coding agent that plans, builds, and learns.{_RESET}
{_DIM}  Now with RAG context, knowledge graph, and parallel agents.{_RESET}
"""


# ── LLM intent classification ──────────────────────────────────────────────

INTENT_PROMPT = (
    "Classify this user input as exactly ONE word: chat, code, search, or research.\n"
    "- chat: greetings, questions, conversation, opinions\n"
    "- code: write/fix/debug/refactor code, build features, fix bugs, run tests\n"
    "- search: look up ONE specific thing (a doc, an API, a fact, a URL)\n"
    "- research: investigate a TOPIC deeply, explore multiple angles, find correlations, "
    "write a report, deep dive, multi-step exploration\n"
    "Examples: \"what is FastAPI\" -> search\n"
    "  \"deep dive into async Python\" -> research\n"
    "  \"compare React vs Vue\" -> research\n"
    "  \"find correlations between X and Y\" -> research\n"
    "  \"look up the requests library docs\" -> search\n"
    "Respond with ONLY the single word.\n"
)

# Keyword heuristics: if these appear, force research regardless of LLM
_RESEARCH_KEYWORDS = {
    "deep dive", "research", "investigate", "explore", "correlation",
    "compare", "analysis", "comprehensive", "thorough", "structured report",
    "multi-step", "multiple angles", "in depth", "in-depth", "write a report",
    "summary report", "detailed report", "find correlations",
}


def _classify_intent(
    text: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 15,
) -> str:
    """Classify user input as chat, code, search, or research.

    Uses keyword heuristics first (fast, reliable), then LLM as fallback.
    """
    lower = text.strip().lower()

    # Fast heuristic: if research keywords are present, skip the LLM call
    for kw in _RESEARCH_KEYWORDS:
        if kw in lower:
            return "research"

    # Fast heuristic: greetings are always chat
    greetings = {
        "hello", "hi", "hey", "yo", "sup", "howdy",
        "good morning", "good afternoon", "good evening",
        "what's up", "whats up", "how are you", "how's it going",
    }
    if lower.rstrip("!.?") in greetings:
        return "chat"

    # Otherwise, ask the LLM
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        {"role": "user", "content": text},
    ]
    try:
        result = ollama_chat(
            model=model,
            messages=messages,
            base_url=base_url,
            timeout=timeout,
        )
        answer = result.strip().lower() if isinstance(result, str) else ""
        match = re.search(r"\b(chat|code|research|search)\b", answer)
        if match:
            return match.group(1)
        return "code"
    except Exception:
        return "code"


def _chat_directly(
    message: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> str:
    """Send a conversational message directly to the LLM."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are Ghost, a friendly local coding assistant. "
                "Respond conversationally. Keep replies short and helpful. "
                "If the user asks you to do something with code, remind them "
                "you're ready to help with coding tasks."
            ),
        },
        {"role": "user", "content": message},
    ]
    try:
        result = ollama_chat(
            model=model,
            messages=messages,
            base_url=base_url,
            timeout=timeout,
        )
        return result if isinstance(result, str) else ""
    except Exception as exc:
        return f"[Error: {exc}]"


def _search_directly(
    query: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> str:
    """Quick single web search."""
    from .tools import web_tools

    results = web_tools.web_search(query)
    if not results:
        return f"No web results found for: {query}"

    formatted = web_tools.format_search_results(results, query)

    messages = [
        {
            "role": "system",
            "content": (
                "You are Ghost, a helpful coding assistant. "
                "Summarize these web search results concisely. "
                "Include the most relevant URLs. Keep it helpful and brief."
            ),
        },
        {"role": "user", "content": f"{formatted}\n\nSummarize these results for the user."},
    ]
    try:
        result = ollama_chat(
            model=model,
            messages=messages,
            base_url=base_url,
            timeout=timeout,
        )
        return result if isinstance(result, str) else formatted
    except Exception:
        return formatted


# Keywords that indicate a generic intro query (blocked by code)
_INTRO_PATTERNS = (
    "what is ", "what are ", "what was ", "what does ",
    "why are ", "why is ", "define ", "definition of ",
    "explain ", "what about ", "introduction to ", "overview of ",
    "summary of ", "tell me about ",
)

_RESEARCH_META_WORDS = {
    "about", "analysis", "deep", "dive", "explore", "full", "investigate",
    "question", "report", "research", "scientific", "study", "thorough",
}


def _normalise_research_topic(topic: str) -> str:
    """Remove conversational routing language before it reaches search engines."""
    cleaned = re.sub(
        r"^\s*(?:(?:please|can you)\s+)?(?:(?:do|run|start)\s+)?"
        r"(?:(?:a|an|the|full)\s+)?(?:deep\s+)?research"
        r"(?:\s+(?:on|into|about|for))?\s+",
        "",
        topic.strip(),
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" .?!") or topic.strip()


def _research_terms(topic: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", topic.lower())
        if word not in _RESEARCH_META_WORDS
    }


def _is_valid_research_query(query: str, topic: str) -> bool:
    """Check if a search query is specific enough for research.

    Blocks generic intro queries that small models keep generating.
    """
    q = query.strip().lower()
    # Block generic intro patterns
    for pattern in _INTRO_PATTERNS:
        if q.startswith(pattern):
            # Only allow if the FULL topic phrase appears in the query
            if topic.lower() in q:
                return True
            return False
    # Block very short queries
    if len(q.split()) < 3:
        return False
    topic_terms = _research_terms(topic)
    query_terms = _research_terms(q)
    if len(topic_terms) >= 2 and len(topic_terms & query_terms) < 2:
        return False
    return True


def _generate_intersection_queries(topic: str) -> list[str]:
    """Generate intersection-focused queries from a research topic.

    Used as fallback when the model generates bad queries.
    """
    # Never reduce a multi-concept research question to a generic pair such as
    # "carbon effect on cycling". Preserve the full intersection in every
    # fallback query so the retrieval budget cannot drift from the question.
    return [
        f"{topic} intersection relationship",
        f"{topic} causal mechanism",
        f"{topic} cross-system links",
        f"{topic} primary literature",
        f"{topic} research gap",
    ]


def _research_seed_queries(topic: str) -> list[str]:
    """Create a diverse query frontier before asking a small model to branch."""
    terms = _research_terms(topic)
    candidates = [
        f"{topic} systematic review",
        f"{topic} review evidence mechanism",
        f"{topic} observational study",
        f"{topic} experiment measurement",
        f"{topic} limitations uncertainty",
        f"{topic} contradictory evidence",
    ]
    # OpenAlex treats site: filters as literal terms and repeatedly returns the
    # same broad papers. Add domain-specific concept intersections instead.
    if {"microbial", "iron", "ocean", "carbon"} <= terms:
        candidates[1:1] = [
            "marine microbial iron cycling carbon export",
            "marine microbial iron acquisition carbon export",
            "ocean iron availability microbial carbon sequestration",
            "iron limitation phytoplankton microbial carbon export ocean",
        ]
    candidates.extend(_generate_intersection_queries(topic))
    seen: set[str] = set()
    return [q for q in candidates if not (q.lower() in seen or seen.add(q.lower()))]


def _evidence_excerpt(text: str, topic: str, max_chars: int = 5_000) -> str:
    """Select topic-relevant source sentences, preserving original wording."""
    tokens = {word for word in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", topic.lower())}
    sentences = re.split(r"(?<=[.!?])\s+", text)
    ranked = sorted(
        enumerate(sentences),
        key=lambda item: (
            sum(token in item[1].lower() for token in tokens),
            -item[0],
        ),
        reverse=True,
    )
    selected = sorted(index for index, sentence in ranked[:12] if sentence.strip())
    excerpt = " ".join(sentences[index].strip() for index in selected)
    return (excerpt or text)[:max_chars]


def _normalise_evidence(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _quote_is_in_source(quote: str, source_text: str) -> bool:
    """Require a meaningful, contiguous quotation from fetched source text."""
    normalized_quote = _normalise_evidence(quote)
    normalized_source = _normalise_evidence(source_text)
    return len(normalized_quote) >= 24 and normalized_quote in normalized_source


_ENTAILMENT_STOP_WORDS = {
    "about", "after", "also", "among", "and", "are", "been", "being", "but",
    "does", "from", "have", "into", "more", "that", "the", "their", "than",
    "then", "these", "they", "this", "those", "through", "using", "was",
    "were", "which", "with", "would",
}


def _claim_quote_coverage(claim: str, quote: str) -> float:
    """Measure how much of a claim is stated in its quoted evidence."""
    claim_words = {
        word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", claim.lower())
        if len(word) > 3 and word not in _ENTAILMENT_STOP_WORDS
    }
    quote_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", quote.lower()))
    if len(claim_words) < 3:
        return 0.0
    return len(claim_words & quote_words) / len(claim_words)


def _is_topic_grounded_claim(claim: str, topic: str) -> bool:
    """Keep claims about the question, not generic facts from a related paper.

    A source can be useful background, but a small model must not promote its
    title or a broad abstract sentence as an answer to a multi-concept query.
    """
    topic_words = _research_terms(topic)
    claim_words = _research_terms(claim)
    overlap = topic_words & claim_words
    if len(topic_words) < 4:
        return len(overlap) >= 1
    # Broad field words such as "ocean" and "carbon" alone do not establish
    # that a claim addresses the requested mechanism or entity.
    anchors = {
        word for word in topic_words
        if word not in {"ocean", "carbon", "marine", "research", "study"}
    }
    # A multi-concept question needs multiple *specific* concepts in the
    # quotation itself. This prevents a broad background paper mentioning
    # "microbial ocean carbon sequestration" from answering an iron-cycling
    # question simply because it shares generic field vocabulary.
    return len(overlap) >= 3 and len(overlap & anchors) >= 3


def _validate_extracted_claims(
    raw_claims: list[dict], findings: list[dict], topic: str = ""
) -> list[dict]:
    """Reject model output that has no exact, high-quality source evidence."""
    accepted: list[dict] = []
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        try:
            source_index = int(raw.get("source_index", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= source_index < len(findings):
            continue

        finding = findings[source_index]
        claim = str(raw.get("claim", "")).strip()
        quote = str(raw.get("source_quote", "")).strip()
        source_text = str(finding.get("evidence_text", ""))
        if (
            len(claim) < 20
            or len(claim) > 600
            or "http://" in claim.lower()
            or "https://" in claim.lower()
            or finding.get("score", 0.0) < 0.50
            or not _quote_is_in_source(quote, source_text)
            or (topic and not _is_topic_grounded_claim(claim, topic))
            or (topic and not _is_topic_grounded_claim(quote, topic))
        ):
            continue

        accepted.append({
            "claim": claim,
            "source_index": source_index + 1,
            "source_quote": quote,
            "claim_type": raw.get("claim_type", "unknown"),
            "evidence_type": raw.get("evidence_type", "unknown"),
            "source_url": finding.get("url", ""),
            "source_title": finding.get("title", ""),
            "source_tier": finding.get("tier", "unknown"),
            "source_score": finding.get("score", 0.0),
            "source_kind": finding.get("source_kind", "full_text"),
        })
    return accepted


def _extract_evidenced_claims(
    findings: list[dict], model: str, base_url: str | None, timeout: int, topic: str = ""
) -> list[dict]:
    """Extract in small batches, then enforce evidence checks in code."""
    claims: list[dict] = []
    for start in range(0, len(findings), 5):
        batch = findings[start:start + 5]
        raw_claims = _extract_claims(batch, model, base_url, timeout)
        claims.extend(_validate_extracted_claims(raw_claims, batch, topic))
    return claims


def _deduplicate_evidenced_claims(claims: list[dict]) -> list[dict]:
    """Deduplicate without letting the model rewrite evidence-backed claims."""
    unique: list[dict] = []
    for claim in claims:
        words = set(re.findall(r"\w+", claim["claim"].lower()))
        is_duplicate = False
        for existing in unique:
            other_words = set(re.findall(r"\w+", existing["claim"].lower()))
            union = words | other_words
            similarity = len(words & other_words) / len(union) if union else 0.0
            if similarity >= 0.82:
                is_duplicate = True
                break
        if not is_duplicate:
            unique.append(claim)
    return unique


def _extract_source_statements(findings: list[dict], topic: str, limit: int = 12) -> list[dict]:
    """Create a zero-hallucination fallback from relevant source sentences."""
    topic_words = {
        word for word in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", topic.lower())
        if word not in _ENTAILMENT_STOP_WORDS
    }
    candidates: list[tuple[int, int, str, dict]] = []
    for finding_index, finding in enumerate(findings):
        for sentence_index, sentence in enumerate(
            re.split(r"(?<=[.!?])\s+", finding.get("evidence_excerpt", ""))
        ):
            sentence = sentence.strip()
            words = set(re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", sentence.lower()))
            overlap = len(words & topic_words)
            if len(sentence) >= 60 and overlap >= 2 and _is_topic_grounded_claim(sentence, topic):
                candidates.append((overlap, -sentence_index, sentence, finding))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    statements: list[dict] = []
    seen: set[str] = set()
    for _, _, sentence, finding in candidates:
        normalized = _normalise_evidence(sentence)
        if normalized in seen:
            continue
        seen.add(normalized)
        statements.append({
            "claim": sentence,
            "source_index": len(statements) + 1,
            "source_quote": sentence,
            "claim_type": "source_statement",
            "evidence_type": "abstract_metadata" if finding.get("source_kind") == "abstract" else "source_text",
            "source_url": finding.get("url", ""),
            "source_title": finding.get("title", ""),
            "source_tier": finding.get("tier", "unknown"),
            "source_score": finding.get("score", 0.0),
            "source_kind": finding.get("source_kind", "full_text"),
        })
        if len(statements) >= limit:
            break
    return statements


def _research_legacy(
    topic: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
    max_searches: int = 15,
) -> str:
    """Deep multi-step research with dedup and synthesis."""
    from .tools import web_tools

    searched_queries: list[str] = []
    seen_urls: set[str] = set()
    all_findings: list[dict] = []
    total_searches = 0
    max_total_searches = max_searches * 2  # Hard cap on total searches

    # Pre-generate intersection-focused queries from the topic
    topic_words = topic.lower().split()
    auto_queries = _generate_intersection_queries(topic)

    conversation = [
        {
            "role": "system",
            "content": (
                "You are Ghost conducting deep research. You have web search access.\n"
                "For each round you output a JSON object:\n"
                '{"queries": ["search term 1", "search term 2"], '
                '"notes": "a scratchpad to record what you have found so far to avoid repeating yourself", '
                '"analysis": "what you learned so far", '
                '"done": false}\n'
                'When done, set done=true and include a "summary" key.\n'
                "RULES:\n"
                "- ADAPTIVE BRANCHING: First establish the intersection. Expand ONLY branches where credible literature actually exists.\n"
                "- Stop exploring a branch if searches yield no relevant scientific literature.\n"
                "- Research the INTERSECTION of the topics, not separate subtopics\n"
                "  e.g. for exoplanets AND dark matter: search for dark matter capture by planets or planetary spin\n"
                "  NOT separate generic searches for what exoplanets are or what dark matter is\n"
                "- Never search for vague introductory content\n"
                "- Every query must advance understanding of the RELATIONSHIP\n"
                "- Do NOT combine separately true facts into false conclusions\n"
                "- Flag extreme claims (most/first/ever/largest) for verification\n"
                "Output ONLY the JSON object."
            ),
        },
        {"role": "user", "content": f"Research topic: {topic}"},
    ]

    for search_round in range(max_searches):
        try:
            response = ollama_chat(
                model=model,
                messages=conversation,
                base_url=base_url,
                timeout=timeout,
            )
            response_text = response if isinstance(response, str) else ""
        except Exception as exc:
            break

        plan = _parse_research_plan(response_text)

        if plan.get("done"):
            # We break instead of returning to ensure we run the verification pipeline
            break

        queries = plan.get("queries", [])
        if not queries:
            queries = [topic]

        # Filter out generic intro queries the small model keeps generating
        valid_queries = []
        for q in queries:
            if _is_valid_research_query(q, topic):
                valid_queries.append(q)

        # If all queries were filtered, inject auto-generated intersection queries
        if not valid_queries and auto_queries:
            valid_queries = [auto_queries.pop(0)]

        # Hard search budget check
        if total_searches >= max_total_searches:
            print(f"  {_DIM}Search budget exhausted ({max_total_searches} searches){_RESET}")
            break

        new_results = []
        for q in valid_queries[:3]:
            if q in searched_queries:
                continue
            if total_searches >= max_total_searches:
                break
            searched_queries.append(q)
            total_searches += 1
            print(f"  {_DIM}\U0001f50d {q}{_RESET}")

            results = web_tools.web_search(q)
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    new_results.append({**r, "query": q})
                    all_findings.append({**r, "query": q})

        if not new_results:
            conversation.append({
                "role": "user",
                "content": "No new results found. Try different search queries.",
            })
            continue

        formatted = web_tools.format_search_results(
            new_results, " + ".join(queries[:3])
        )

        analysis = plan.get("analysis", "")
        notes = plan.get("notes", "")
        
        content = ""
        if notes:
            content += f"Scratchpad/Notes:\n{notes}\n\n"
        content += f"Analysis:\n{analysis}\n\n{formatted}\n\nBased on these results, generate more search queries or finish with done=true and a summary."
        
        conversation.append({
            "role": "user",
            "content": content,
        })

    if not all_findings:
        return f"No web results found for: {topic}. Try rephrasing your query."

    # ── Claim verification pipeline (always runs) ───────────────────────
    print(f"\n  {_DIM}Extracting claims...{_RESET}")
    claims = _extract_claims(all_findings, model, base_url, timeout)
    print(f"  {_DIM}Found {len(claims)} claims{_RESET}")

    if not claims:
        print(f"  {_DIM}No sufficiently strong claims extracted. Returning exploratory synthesis.{_RESET}")
        summary = plan.get("summary", "Exploratory synthesis based on findings...") if 'plan' in locals() else "Exploratory synthesis based on findings..."
        if all_findings:
            summary += _format_findings_appendix(all_findings)
        return summary

    print(f"  {_DIM}Deduplicating claims...{_RESET}")
    claims = _deduplicate_claims(claims, model, base_url, timeout)
    print(f"  {_DIM}Retained {len(claims)} unique claims{_RESET}")

    print(f"  {_DIM}Verifying claims against sources...{_RESET}")
    verified = _verify_claims(claims, all_findings, model, base_url, timeout)

    print(f"  {_DIM}Checking for contradictions...{_RESET}")
    contradictions = _check_contradictions(verified, model, base_url, timeout)

    verified = _apply_contradictions(verified, contradictions)

    # Detailed Evidence Assessment breakdown
    directly = sum(1 for c in verified if c.get("source_entailment") == "DIRECTLY_SUPPORTED")
    partially = sum(1 for c in verified if c.get("source_entailment") in ("PARTIALLY_SUPPORTED", "INDIRECTLY_SUPPORTED"))
    contradicted = sum(1 for c in verified if c.get("source_entailment") == "CONTRADICTED")
    unresolved = sum(1 for c in verified if c.get("source_entailment") in ("NOT_SUPPORTED", "UNKNOWN", None, ""))

    print(f"\n  {_BOLD}Evidence assessment:{_RESET}")
    print(f"    \u2713 {directly} directly supported")
    print(f"    ~ {partially} partially/indirectly supported")
    print(f"    ? {unresolved} unresolved")
    print(f"    \u2717 {contradicted} contradicted/disputed")
    if contradictions:
        print(f"  {_BRIGHT_CYAN}{len(contradictions)} contradiction(s) detected and applied{_RESET}")

    conf = "HIGH" if directly >= 3 and not contradictions else ("MODERATE" if directly >= 1 else "LOW")
    print(f"  Synthesis confidence: {_BOLD}{conf}{_RESET}\n")

    print(f"  {_DIM}Synthesizing verified report...{_RESET}")
    return _verified_synthesis(
        topic, verified, contradictions, all_findings,
        model, base_url, timeout,
    )


def _research_directly(
    topic: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
    max_searches: int = 24,
    graph_root: Path | None = None,
) -> str:
    """Research from fetched evidence, not search-result snippets.

    The model is used for query planning, claim extraction, and narrow
    entailment judgments. Code enforces source quality, exact quotations, and
    report construction so a small model cannot turn a related page into a
    confident but unsupported claim.
    """
    from .tools import web_tools

    topic = _normalise_research_topic(topic)
    graph = ResearchProvenanceGraph(graph_root, topic) if graph_root else None
    searched_queries: set[str] = set()
    seen_urls: set[str] = set()
    findings: list[dict] = []
    frontier = _research_seed_queries(topic)
    max_sources = 32
    min_rounds = min(6, max_searches)
    conversation = [
        {
            "role": "system",
            "content": (
                "You are planning a scientific literature search. Return only JSON with "
                'keys "queries", "notes", "analysis", and "done". '
                "Generate specific search queries only; do not make factual claims. "
                "Seek mechanisms, measurements, reviews, null results, limitations, "
                "and competing hypotheses. Prefer primary sources and scholarly indexes."
            ),
        },
        {"role": "user", "content": f"Research topic: {topic}"},
    ]

    for round_index in range(max_searches):
        if frontier:
            candidates = [frontier.pop(0)]
        else:
            try:
                response = ollama_chat(
                    model=model, messages=conversation,
                    base_url=base_url, timeout=timeout,
                )
                plan = _parse_research_plan(response if isinstance(response, str) else "")
            except Exception:
                break
            if plan.get("done") and round_index >= min_rounds:
                break
            candidates = [
                query for query in plan.get("queries", [])
                if isinstance(query, str) and _is_valid_research_query(query, topic)
            ]
            if not candidates:
                candidates = _generate_intersection_queries(topic)[:1]

        query = next(
            (candidate.strip() for candidate in candidates
             if candidate.strip().lower() not in searched_queries),
            "",
        )
        if not query or (graph and not graph.is_novel_query(query)):
            continue
        searched_queries.add(query.lower())
        query_id = graph.add_query(query, round_index + 1) if graph else ""
        print(f"  {_DIM}[search] {query}{_RESET}")

        discovery_results = web_tools.academic_search(query)
        results = sorted(
            discovery_results + web_tools.web_search(query),
            key=lambda item: item.get(
                "score", web_tools.source_credibility(item.get("url", ""))["score"]
            ),
            reverse=True,
        )
        new_findings: list[dict] = []
        for result in results:
            if len(findings) >= max_sources or len(new_findings) >= 3:
                break
            url = result.get("url", "")
            preview = {
                "score": result.get("score"),
                "tier": result.get("tier"),
                "domain": result.get("domain"),
            }
            fallback_quality = web_tools.source_credibility(url)
            if not isinstance(preview["score"], (int, float)):
                preview["score"] = fallback_quality.get("score", 0.0)
            if not preview["tier"]:
                preview["tier"] = fallback_quality.get("tier", "unknown")
            if not preview["domain"]:
                preview["domain"] = fallback_quality.get("domain", "")
            if not url or url in seen_urls or preview["score"] < 0.50:
                continue
            aliases = web_tools.evidence_aliases(result)
            if any(web_tools.evidence_aliases(item) & aliases for item in findings):
                continue
            group = web_tools.source_group({**result, "domain": preview["domain"]})
            if sum(web_tools.source_group(item) == group for item in findings) >= 2:
                continue
            fetched = (
                {
                    "url": url,
                    "evidence_text": result["evidence_text"],
                    "tier": preview["tier"],
                    "score": preview["score"],
                    "domain": preview["domain"],
                    "source_kind": result.get("source_kind", "full_text"),
                }
                if result.get("evidence_text")
                else web_tools.fetch_source_text(url)
            )
            if not fetched:
                continue
            finding = {
                **result,
                **fetched,
                "query": query,
                "evidence_excerpt": _evidence_excerpt(fetched["evidence_text"], topic),
            }
            # Redirects can change both the source host and its quality. Count
            # only accepted final evidence, not failed fetch attempts.
            if finding.get("score", 0) < 0.50 or finding["url"] in seen_urls:
                continue
            if any(web_tools.evidence_aliases(item) & web_tools.evidence_aliases(finding) for item in findings):
                continue
            if sum(web_tools.source_group(item) == web_tools.source_group(finding) for item in findings) >= 2:
                continue
            seen_urls.add(finding["url"])
            if graph:
                finding["_graph_source_id"] = graph.add_source(query_id, finding)
            findings.append(finding)
            new_findings.append(finding)

        ledger = "\n\n".join(
            f"[{len(findings) - len(new_findings) + index}] {item['title']} "
            f"({item['tier']}, score {item['score']:.2f})\n"
            f"{item['evidence_excerpt'][:800]}"
            for index, item in enumerate(new_findings, 1)
        )
        conversation.append({
            "role": "user",
            "content": (
                f"Round {round_index + 1} evidence ledger:\n{ledger or 'No usable source text.'}\n\n"
                f"{graph.summary() + chr(10) if graph else ''}"
                "Propose the next underexplored, evidence-seeking queries."
            ),
        })

    if not findings:
        if graph:
            graph.add_gap("No fetchable, credible source text was found.")
            graph.save()
        return f"No fetchable, credible source text found for: {topic}. Try rephrasing the question."

    print(f"\n  {_DIM}Extracting claims with exact-source checks...{_RESET}")
    claims = _extract_evidenced_claims(findings, model, base_url, timeout, topic)
    claims = _deduplicate_evidenced_claims(claims)
    if not claims:
        claims = _extract_source_statements(findings, topic)
        if claims:
            print(f"  {_DIM}Model extraction failed; using {len(claims)} exact source statements{_RESET}")
    print(f"  {_DIM}Retained {len(claims)} evidence-backed claims from {len(findings)} sources{_RESET}")
    if not claims:
        if graph:
            graph.add_gap("No claim met the exact-quotation gate.")
            graph.save()
        return _fallback_verified_report(topic, [], [], findings)

    print(f"  {_DIM}Running conservative entailment checks...{_RESET}")
    verified = _verify_claims(claims, findings, model, base_url, timeout)
    if graph:
        source_ids = {finding.get("url", ""): finding.get("_graph_source_id") for finding in findings}
        for claim in verified:
            claim_id = graph.add_claim(claim, source_ids.get(claim.get("source_url", "")))
            graph.add_verdict(
                claim_id,
                claim.get("source_entailment", "NOT_SUPPORTED"),
                {
                    "quote_coverage": claim.get("quote_coverage", 0.0),
                    "source_score": claim.get("source_score", 0.0),
                },
            )
    reportable = [
        claim for claim in verified
        if claim.get("source_entailment") in {"DIRECTLY_SUPPORTED", "PARTIALLY_SUPPORTED"}
    ]
    rejected = len(verified) - len(reportable)
    contradictions = _check_contradictions(reportable, model, base_url, timeout)
    reportable = _apply_contradictions(reportable, contradictions)
    reportable = [
        claim for claim in reportable
        if claim.get("source_entailment") != "CONTRADICTED"
    ]
    print(
        f"  {_DIM}{len(reportable)}/{len(verified)} claims survived conservative verification"
        f" ({rejected} rejected){_RESET}"
    )

    # Deliberately use the deterministic report. A free-form final synthesis
    # from a 1.5B model is the last place unsupported prose can re-enter.
    report = _fallback_verified_report(topic, reportable, contradictions, findings)
    if rejected:
        report = report.replace(
            "## Method Note",
            "## Evidence Gate\n"
            f"{rejected} candidate claim(s) were excluded because their wording went beyond "
            "the verified quotation or the model could not support them.\n\n"
            "## Method Note",
        )
    if graph:
        if not reportable:
            graph.add_gap("All candidate claims were rejected by conservative verification.")
        graph.save()
    return report


def _parse_research_plan(text: str) -> dict:
    """Extract research plan from LLM response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        stripped = "\n".join(lines[1:])
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r'\{.*\}', stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return {"queries": [], "notes": "", "analysis": text, "done": False}


def _format_findings_appendix(findings: list[dict]) -> str:
    """Format all findings as a reference appendix."""
    if not findings:
        return ""
    lines = ["\n\n--- Sources ---"]
    seen = set()
    for f in findings:
        url = f.get("url", "")
        if url in seen:
            continue
        seen.add(url)
        title = f.get("title", "?")
        snippet = f.get("snippet", "")
        lines.append(f"- {title}\n  {url}")
        if snippet:
            lines.append(f"  {snippet[:120]}")
    return "\n".join(lines)


# ── Claim verification pipeline ─────────────────────────────────────────────

CLAIM_EXTRACTION_PROMPT = (
    "You are a research analyst extracting ATOMIC CLAIMS from search results.\n"
    "An atomic claim is a single, specific, falsifiable proposition.\n\n"
    "Output a JSON array of objects, each with:\n"
    '  "claim": a single falsifiable proposition (NEVER a source title, URL, or paper name)\n'
    '  "source_index": which source (1-indexed) supports it\n'
    '  "source_quote": the exact text from the source that supports this claim\n'
    '  "claim_type": "established_fact" | "strong_inference" | "theoretical_proposal" | "model_dependent" | "speculation"\n'
    '  "evidence_type": "observational" | "theoretical" | "model_based" | "indirect" | "expert_opinion"\n'
    "Output ONLY the JSON array.\n"
    "RULES:\n"
    "- Extract ONLY specific, falsifiable propositions\n"
    "- The claim field must be a SENTENCE describing what is claimed, NOT a paper title or source name\n"
    "- BAD: 'Dark Matter (S)pins the Planet - arXiv.org'\n"
    "- GOOD: 'Planetary dark-matter capture has been proposed as a mechanism to probe dark matter at planetary scales'\n"
    "- BAD: 'Exoplanet - Wikipedia'\n"
    "- GOOD: 'Over 5,000 exoplanets have been confirmed as of 2024'\n"
    "- Each claim must be attributable to a specific passage in a single source\n"
    "- Copy source_quote as a contiguous, verbatim passage of at least 24 characters\n"
    "- If no exact quotation supports a claim, omit the claim entirely\n"
    "- Distinguish between what the source STATES vs what it HYPOTHESIZES\n"
    "- Do NOT infer beyond what the source text actually says\n"
    "- Flag extreme-ranking claims (most/first/ever/largest/only) separately\n"
)

CLAIM_VERIFICATION_PROMPT = (
    "You are a rigorous scientific fact-checker. For each claim:\n"
    "Output a JSON array of objects, each with:\n"
    '  "claim_index": the 1-indexed number of the original claim\n'
    '  "source_entailment": "DIRECTLY_SUPPORTED" | "PARTIALLY_SUPPORTED" | "INDIRECTLY_SUPPORTED" | "CONTRADICTED" | "NOT_SUPPORTED"\n'
    '  "reasoning": specific reasoning answering the entailment question below\n'
    '  "claim_type": "hypothesis" | "inference" | "observation" | "fact"\n'
    '  "evidence_independence": an integer 0 to 5\n'
    '  "confidence_in_claim": "high" | "medium" | "low" | "unknown"\n'
    '  "confidence_in_source": "high" | "medium" | "low" | "unknown"\n'
    '  "evidence_type": "observational" | "theoretical" | "model_based" | "none"\n'
    '  "observation_status": "directly_observed" | "indirectly_measured" | "theoretically_predicted" | "not_observed"\n'
    "Output ONLY the JSON array.\n"
    "RULES:\n"
    "- Do not rewrite, add, or remove claims; only assess the numbered claims supplied\n"
    "- A source cannot contradict a claim that faithfully restates its own supplied quotation\n"
    "- Entailment Question: If I showed only the cited passage to a skeptical scientist, would they agree that it supports this exact claim? Do NOT ask 'Is this source related?' but 'Does this evidence entail this claim?'\n"
    "- Confidence Distinction: Confidence in the source (e.g. a peer reviewed journal) is NOT the same as confidence in the claim (e.g. a wild hypothesis). Be strict.\n"
    "- Does the source ACTUALLY say this, or are you inferring beyond what it states?\n"
    "- Watch for qualifier amplification: 'probably exists' does NOT mean 'significantly affects'\n"
    "- False synthesis check: Are you combining two individually true statements into a false conclusion?\n"
    "- Extreme claims (most/first/ever/largest) need temporal verification\n"
    "- Distinguish theoretical prediction from observed fact\n"
)

CONTRADICTION_PROMPT = (
    "You are checking for contradictions between research claims.\n"
    "Look for BOTH:\n"
    "1. Direct contradictions: A says X, B says not-X\n"
    "2. Epistemic contradictions: Source says 'we cannot yet measure X' but claim says 'X has been observed extensively'\n"
    "Output a JSON array of contradiction objects (empty array if none).\n"
    'Each: {"claim_a": "...", "claim_b": "...", "explanation": "...", "type": "direct" | "epistemic"}\n'
    "Output ONLY the JSON array.\n"
)

SYNTHESIS_PROMPT = (
    "You are Ghost writing an evidence-constrained scientific research report.\n"
    "ABSOLUTE RULES:\n"
    "- Use ONLY the verified claims below. You may NOT add claims from your own knowledge.\n"
    "- NEVER combine separate facts into unsupported conclusions.\n"
    "- You are FORBIDDEN from presenting a 'theoretical_proposal' or 'model_dependent' claim as an established finding.\n"
    "- If a claim is tagged as CONTRADICTED, you MUST present it as disputed, NOT as a finding.\n"
    "- Your Executive Summary MUST NOT contain any claim stronger than 'theoretical_proposal' unless it has DIRECTLY_SUPPORTED evidence.\n"
    "- IMMUTABLE SOURCES: NEVER invent authors, journals, or dates. Cite only as [Source N].\n"
    "- SOURCE RANKING: A single primary source (arXiv, peer-reviewed) outweighs multiple blogs/Wikipedia.\n"
    "- Five websites repeating the same claim are NOT five independent pieces of evidence.\n"
    "\n"
    "STRENGTH LADDER (you may NEVER go UP this ladder beyond what the evidence supports):\n"
    "  established_fact > strong_inference > theoretical_proposal > model_dependent > speculation > not_established\n"
    "\n"
    "REQUIRED STRUCTURE:\n"
    "1. Executive Summary (hedged, evidence-constrained)\n"
    "2. Key Findings — include a Markdown table:\n"
    "   | Claim | Status | Evidence Type | Observation Status |\n"
    "   Status values: Established, Strong inference, Theoretical research, Model-dependent hypothesis, Not established, Plausible research direction\n"
    "3. Contradictions (MANDATORY if any exist — show what was contradicted and why)\n"
    "4. Epistemic Status\n"
    "5. Gaps (actively reason about unknowns; if a hypothesis lacks observational evidence, that IS a major gap)\n"
    "\n"
    "HEDGING: Use 'theoretically predicted', 'may', 'could', 'is expected to', 'has been proposed'.\n"
    "If something has NOT been directly observed, say so EXPLICITLY.\n"
)

DEDUPLICATION_PROMPT = (
    "You are merging duplicate scientific claims.\n"
    "Given a list of extracted claims, identify claims that are semantically identical or where one fully subsumes the other.\n"
    "Output a JSON array of merged claims. For each merged claim, include:\n"
    '  "claim": the merged or best-phrased claim\n'
    '  "source_indices": a list of integers (or strings) representing all source indices that support this claim\n'
    '  "source_quotes": a list of the exact quotes supporting it\n'
    '  "evidence_type": the predominant evidence type\n'
    "Output ONLY the JSON array.\n"
)


JSON_ARRAY_RE = re.compile(r'\[.*\]', re.DOTALL)


def _extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output, handling fences and prose."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        stripped = "\n".join(lines[1:])
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    # Direct parse
    try:
        obj = json.loads(stripped)
        if isinstance(obj, list):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    # Find array in text
    m = JSON_ARRAY_RE.search(stripped)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, list):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _extract_claims(
    findings: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> list[dict]:
    """Extract specific claims from search findings using the LLM."""
    # Format findings for the LLM
    source_text = []
    for i, f in enumerate(findings, 1):
        source_text.append(
            f"[Source {i}] {f.get('title', '?')}\n"
            f"  URL: {f.get('url', '?')}\n"
            f"  Evidence tier: {f.get('tier', 'unknown')} ({f.get('score', 0):.2f})\n"
            f"  Source text:\n{f.get('evidence_excerpt', f.get('snippet', ''))}"
        )
    sources_block = "\n\n".join(source_text)

    messages = [
        {"role": "system", "content": CLAIM_EXTRACTION_PROMPT},
        {"role": "user", "content": sources_block},
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages,
            base_url=base_url, timeout=timeout,
        )
        return _extract_json_array(result if isinstance(result, str) else "")
    except Exception:
        return []

def _deduplicate_claims(
    claims: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> list[dict]:
    """Merge semantically identical or subsumed claims."""
    if len(claims) < 2:
        return claims

    claims_text = json.dumps(claims, indent=2)
    messages = [
        {"role": "system", "content": DEDUPLICATION_PROMPT},
        {"role": "user", "content": claims_text},
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages,
            base_url=base_url, timeout=timeout,
        )
        merged = _extract_json_array(result if isinstance(result, str) else "")
        # If dedup returned something reasonable, use it; otherwise keep originals
        if merged:
            return merged
        return claims
    except Exception:
        return claims


def _verify_claims_legacy(
    claims: list[dict],
    findings: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> list[dict]:
    """Verify each claim against its source. Flag false syntheses."""
    if not claims:
        return []

    # Build verification input
    source_text = []
    for i, f in enumerate(findings, 1):
        source_text.append(
            f"[Source {i}] {f.get('title', '?')}\n  {f.get('snippet', '')}"
        )
    claims_text = "\n".join(
        f"- Claim: {c.get('claim', '?')} "
        f"(from Source(s) {c.get('source_index', c.get('source_indices', '?'))})\n"
        f"  Quote: {c.get('source_quote', c.get('source_quotes', '?'))}"
        for c in claims
    )

    messages = [
        {"role": "system", "content": CLAIM_VERIFICATION_PROMPT},
        {
            "role": "user",
            "content": (
                f"Sources:\n\n{chr(10).join(source_text)}\n\n"
                f"Claims to verify:\n{claims_text}"
            ),
        },
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages,
            base_url=base_url, timeout=timeout,
        )
        return _extract_json_array(result if isinstance(result, str) else "")
    except Exception:
        return []


def _verify_claims(
    claims: list[dict],
    findings: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> list[dict]:
    """Ask for narrow verdicts while preserving code-validated evidence."""
    if not claims:
        return []

    normalized_claims: list[dict] = []
    for claim in claims:
        source_ref = claim.get("source_index", claim.get("source_indices", "?"))
        quote = claim.get("source_quote", claim.get("source_quotes", "?"))
        source_url = claim.get("source_url", "")
        if not source_url:
            indices = source_ref if isinstance(source_ref, list) else [source_ref]
            for source_index in indices:
                try:
                    source_url = findings[int(source_index) - 1].get("url", "")
                except (IndexError, TypeError, ValueError):
                    continue
                if source_url:
                    break
        normalized_claims.append({
            **claim,
            "source_ref": source_ref,
            "source_quote": quote,
            "source_url": source_url,
            "source_tier": claim.get("source_tier", "unknown"),
            "source_score": claim.get("source_score", 0.0),
        })

    claim_blocks = []
    for index, claim in enumerate(normalized_claims, 1):
        claim_blocks.append(
            f"[Claim {index}] {claim['claim']}\n"
            f"Source reference(s): {claim['source_ref']}\n"
            f"Exact supporting quote: {claim['source_quote']}\n"
            f"Source tier: {claim['source_tier']} ({claim['source_score']:.2f})\n"
            f"Source context: {next((f.get('evidence_excerpt', '') for f in findings if f.get('url') == claim['source_url']), '')[:2_500]}"
        )
    messages = [
        {"role": "system", "content": CLAIM_VERIFICATION_PROMPT},
        {"role": "user", "content": "\n\n".join(claim_blocks)},
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages, base_url=base_url, timeout=timeout,
        )
        verdicts = _extract_json_array(result if isinstance(result, str) else "")
    except Exception:
        verdicts = []

    allowed = {
        "DIRECTLY_SUPPORTED", "PARTIALLY_SUPPORTED", "INDIRECTLY_SUPPORTED",
        "CONTRADICTED", "NOT_SUPPORTED",
    }
    by_index: dict[int, dict] = {}
    claim_lookup = {
        _normalise_evidence(claim["claim"]): index
        for index, claim in enumerate(normalized_claims)
    }
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        try:
            index = int(verdict.get("claim_index", 0)) - 1
        except (TypeError, ValueError):
            index = claim_lookup.get(_normalise_evidence(str(verdict.get("claim", ""))), -1)
        status = str(verdict.get("source_entailment", "")).upper()
        if 0 <= index < len(normalized_claims) and status in allowed:
            by_index[index] = verdict

    verified: list[dict] = []
    for index, claim in enumerate(normalized_claims):
        verdict = by_index.get(index, {})
        status = verdict.get("source_entailment", "NOT_SUPPORTED")
        coverage = _claim_quote_coverage(
            claim["claim"], str(claim["source_quote"]),
        )
        # The exact quotation is the hard evidence. A small model sometimes
        # labels its own supporting quote as a contradiction; do not permit
        # that error to overwrite a close lexical restatement.
        if coverage >= 0.85:
            status = "DIRECTLY_SUPPORTED"
        elif coverage >= 0.70 and status in {"CONTRADICTED", "NOT_SUPPORTED"}:
            status = "PARTIALLY_SUPPORTED"
        elif status in {
            "DIRECTLY_SUPPORTED", "PARTIALLY_SUPPORTED", "INDIRECTLY_SUPPORTED",
        } and coverage < 0.70:
            status = "NOT_SUPPORTED"
        statement = f"{claim['claim']} {claim['source_quote']}".lower()
        is_model_based = any(marker in statement for marker in (
            "model", "simulation", "simulates", "theoretical", "predicted",
            "projection", "hypothesis",
        ))
        source_confidence = (
            "high" if claim["source_score"] >= 0.85
            else "medium" if claim["source_score"] >= 0.50
            else "low"
        )
        verified.append({
            **claim,
            "source_entailment": status,
            "quote_coverage": round(coverage, 2),
            "reasoning": (
                "The claim is a close lexical restatement of its exact quoted source passage."
                if coverage >= 0.85
                else str(verdict.get("reasoning", "No conservative verdict returned."))[:600]
            ),
            "confidence_in_claim": source_confidence if coverage >= 0.70 else "unknown",
            "confidence_in_source": source_confidence,
            "claim_type": "model_dependent" if is_model_based else claim.get("claim_type", "unknown"),
            "evidence_type": "model_based" if is_model_based else claim.get("evidence_type", "unknown"),
            "observation_status": "theoretically_predicted" if is_model_based else "unknown",
            "evidence_independence": verdict.get("evidence_independence", 1),
        })
    return verified


def _check_contradictions(
    verified_claims: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> list[dict]:
    """Check for contradictions between verified claims."""
    if len(verified_claims) < 2:
        return []

    claims_text = "\n".join(
        f"- {c.get('claim', '?')} [{c.get('source_entailment', '?')}]"
        for c in verified_claims
    )

    messages = [
        {"role": "system", "content": CONTRADICTION_PROMPT},
        {"role": "user", "content": claims_text},
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages,
            base_url=base_url, timeout=timeout,
        )
        return _extract_json_array(result if isinstance(result, str) else "")
    except Exception:
        return []


def _apply_contradictions(
    verified_claims: list[dict],
    contradictions: list[dict],
) -> list[dict]:
    """Tag claims involved in contradictions and downgrade their entailment."""
    if not contradictions:
        return verified_claims

    contradicted_texts = []
    for c in contradictions:
        if c.get("claim_a"):
            contradicted_texts.append(str(c["claim_a"]).strip().lower())
        if c.get("claim_b"):
            contradicted_texts.append(str(c["claim_b"]).strip().lower())

    def _is_match(target: str, candidates: list[str]) -> bool:
        t = target.strip().lower()
        if not t:
            return False
        t_words = set(re.findall(r'\w+', t))
        for cand in candidates:
            if not cand:
                continue
            if t == cand or t in cand or cand in t:
                return True
            cand_words = set(re.findall(r'\w+', cand))
            if t_words and cand_words:
                overlap = len(t_words & cand_words)
                min_len = min(len(t_words), len(cand_words))
                if min_len > 0 and (overlap / min_len) >= 0.70:
                    return True
        return False

    updated = []
    for claim in verified_claims:
        claim_text = claim.get("claim", "")
        if _is_match(claim_text, contradicted_texts):
            claim = {**claim, "contradicted": True, "source_entailment": "CONTRADICTED"}
        updated.append(claim)

    return updated


def _verified_synthesis(
    topic: str,
    verified_claims: list[dict],
    contradictions: list[dict],
    findings: list[dict],
    model: str,
    base_url: str | None = None,
    timeout: int = 60,
) -> str:
    """Synthesize a report using only verified claims."""
    # Format verified claims for the LLM
    claims_block = "\n".join(
        f"- [{c.get('source_entailment', '?').upper()}] {c.get('claim', '?')}\n"
        f"  Type: {c.get('claim_type', '?')} | "
        f"Ev: {c.get('evidence_type', '?')} | "
        f"Obs: {c.get('observation_status', '?')} | "
        f"Conf (Claim): {c.get('confidence_in_claim', '?')} | "
        f"Conf (Source): {c.get('confidence_in_source', '?')}\n"
        f"  Reasoning: {c.get('reasoning', '?')}"
        for c in verified_claims
    )

    contradictions_block = ""
    if contradictions:
        contradictions_block = "\n\nCONTRADICTIONS FOUND:\n" + "\n".join(
            f"- {c.get('claim_a', '?')} VS {c.get('claim_b', '?')}\n"
            f"  Why: {c.get('explanation', '?')}"
            for c in contradictions
        )

    sources_block = _format_findings_appendix(findings)

    messages = [
        {"role": "system", "content": SYNTHESIS_PROMPT},
        {
            "role": "user",
            "content": (
                f"Research topic: {topic}\n\n"
                f"Verified claims ({len(verified_claims)} total):\n{claims_block}"
                f"{contradictions_block}\n\n"
                f"Write the report now."
            ),
        },
    ]
    try:
        result = ollama_chat(
            model=model, messages=messages,
            base_url=base_url, timeout=timeout,
        )
        report = result if isinstance(result, str) else ""
        if report.strip():
            return report + sources_block
    except Exception:
        pass

    # Fallback: structured report from verified claims
    return _fallback_verified_report(topic, verified_claims, contradictions, findings)


def _fallback_verified_report(
    topic: str,
    verified_claims: list[dict],
    contradictions: list[dict],
    findings: list[dict],
) -> str:
    """Build a verified report without LLM synthesis."""
    lines = [f"# Research: {topic}\n"]

    # Group by source_entailment
    directly = [c for c in verified_claims if c.get("source_entailment") == "DIRECTLY_SUPPORTED"]
    partially = [c for c in verified_claims if c.get("source_entailment") == "PARTIALLY_SUPPORTED"]
    indirectly = [c for c in verified_claims if c.get("source_entailment") == "INDIRECTLY_SUPPORTED"]
    contradicted = [c for c in verified_claims if c.get("source_entailment") == "CONTRADICTED"]
    unsupported = [c for c in verified_claims if c.get("source_entailment") == "NOT_SUPPORTED"]

    def _fmt_claim(c: dict) -> str:
        parts = [f"- {c.get('claim', '?')}\n"]
        parts.append(f"  Type: {c.get('claim_type', '?')} | ")
        parts.append(f"Ev: {c.get('evidence_type', '?')} | ")
        parts.append(f"Obs: {c.get('observation_status', '?')} | ")
        parts.append(f"Conf (Claim): {c.get('confidence_in_claim', '?')} | ")
        parts.append(f"Conf (Source): {c.get('confidence_in_source', '?')}\n")
        parts.append(
            f"  Evidence ({c.get('source_tier', 'unknown')}, "
            f"score {c.get('source_score', 0):.2f}): "
            f"\"{c.get('source_quote', '?')}\"\n"
        )
        if c.get("source_url"):
            parts.append(
                f"  Source: {c.get('source_title', '?')} — {c['source_url']}\n"
            )
        if c.get("reasoning"):
            parts.append(f"  Verification: {c['reasoning']}")
        return "".join(parts)

    if directly:
        lines.append("## Directly Supported")
        for c in directly:
            lines.append(_fmt_claim(c))
        lines.append("")

    if partially:
        lines.append("## Partially Supported")
        for c in partially:
            lines.append(_fmt_claim(c))
        lines.append("")

    if indirectly:
        lines.append("## Indirectly Supported")
        for c in indirectly:
            lines.append(_fmt_claim(c))
        lines.append("")

    if contradictions:
        lines.append("## Contradictions")
        for c in contradictions:
            ctype = c.get('type', '?')
            lines.append(
                f"- [{ctype}] {c.get('claim_a', '?')} VS {c.get('claim_b', '?')}\n"
                f"  {c.get('explanation', '?')}"
            )
        lines.append("")

    if contradicted:
        lines.append("## Contradicted Claims")
        for c in contradicted:
            lines.append(_fmt_claim(c))
        lines.append("")

    if unsupported:
        lines.append("## Unsupported Claims")
        for c in unsupported:
            lines.append(_fmt_claim(c))
        lines.append("")

    if not any((directly, partially, indirectly, contradicted, unsupported)):
        lines.append("## Evidence Status")
        lines.append(
            "No claim met the pipeline's source and entailment thresholds. "
            "This is an evidence gap, not a negative finding."
        )
        lines.append("")

    leads = _adjacent_research_leads(topic, findings)
    if leads:
        lines.append("## Adjacent Research Leads")
        lines.append(
            "These are source-mentioned leads, not conclusions about the primary question. "
            "Would you like a dedicated evidence pass on any of them?"
        )
        for lead in leads:
            lines.append(
                f"- **{lead['term']}** — mentioned in {lead['source_title']}: "
                f'"{lead["quote"]}"\n'
                f"  Suggested follow-up: `{lead['query']}`"
            )
        lines.append("")

    lines.append("## Method Note")
    lines.append(
        "Claims are included only when the model supplied a contiguous quotation "
        "that was found in fetched source text. Search snippets and unfetched pages "
        "are not treated as evidence."
    )
    lines.append("")
    lines.append(_format_findings_appendix(findings))
    return "\n".join(lines)


def _adjacent_research_leads(topic: str, findings: list[dict], limit: int = 4) -> list[dict]:
    """Offer only explicit source-mentioned offshoots, never model-invented ones."""
    topic_words = _research_terms(topic)
    leads: list[dict] = []
    seen: set[str] = set()
    for finding in findings:
        text = str(finding.get("evidence_excerpt") or finding.get("evidence_text") or "")
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            # Scientific abbreviations are concise, auditable follow-up handles.
            for acronym in re.findall(r"\b[A-Z][A-Z0-9-]{2,}\b", sentence):
                key = acronym.lower()
                if key in seen or key in topic_words or len(sentence.strip()) < 40:
                    continue
                seen.add(key)
                leads.append({
                    "term": acronym,
                    "quote": sentence.strip()[:500],
                    "source_title": finding.get("title", "source"),
                    "query": f"{acronym} relationship to {topic}",
                })
                if len(leads) >= limit:
                    return leads
    return leads


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ghost",
        description="Ghost -- a local coding agent that shows its work.",
    )
    sub = parser.add_subparsers(dest="command")
    from .openjarvis_bridge.cli import add_parser as add_openjarvis_parser
    add_openjarvis_parser(sub)

    run_parser = sub.add_parser("run", help="Run a task from a JSON file")
    run_parser.add_argument("task_path", help="Path to task.json")
    run_parser.add_argument("--model", default=None, help="Override model")
    run_parser.add_argument("--verbose", "-v", action="store_true")

    resume_parser = sub.add_parser("resume", help="Resume a previous run")
    resume_parser.add_argument("task_id", help="Task id to resume")
    resume_parser.add_argument("--verbose", "-v", action="store_true")

    audit_parser = sub.add_parser("audit", help="Transparency report for a run")
    audit_parser.add_argument("task_id", nargs="?", help="Task id (omit for aggregate)")

    sub.add_parser("status", help="Show last run summary")

    init_parser = sub.add_parser("init", help="Create a starter task.json")
    init_parser.add_argument("--path", default="task.json", help="Output path")

    index_parser = sub.add_parser("index", help="Build or update the codebase index")
    index_parser.add_argument("--path", default=".", help="Path to index")

    knowledge_parser = sub.add_parser("knowledge", help="Show knowledge graph report")
    knowledge_parser.add_argument("--strategy", action="store_true", help="Strategy effectiveness")

    kbase_parser = sub.add_parser("kbase", help="Owner reference knowledge base (Zim/notes/docs)")
    kbase_sub = kbase_parser.add_subparsers(dest="kb_action")
    kbase_ingest = kbase_sub.add_parser("ingest", help="Index a directory of reference docs")
    kbase_ingest.add_argument("path", help="File or directory to index")
    kbase_sub.add_parser("status", help="Show knowledge base stats")
    kbase_query = kbase_sub.add_parser("query", help="Retrieve relevant reference chunks")
    kbase_query.add_argument("text", nargs="+", help="Query text")
    kbase_delete = kbase_sub.add_parser("delete", help="Delete all chunks of a source")
    kbase_delete.add_argument("source", help="Source path label (see: ghost kbase sources)")
    kbase_sub.add_parser("sources", help="List indexed sources with versions and chunk counts")

    lora_parser = sub.add_parser("lora", help="Verified training-pair corpus (future fine-tune)")
    lora_sub = lora_parser.add_subparsers(dest="lora_action")
    lora_sub.add_parser("status", help="Number of verified pairs captured")
    lora_list = lora_sub.add_parser("list", help="Show recent captured pairs (goal/model only)")
    lora_list.add_argument("--limit", type=int, default=10)

    gaps_parser = sub.add_parser("gaps", help="Inspect/manage capability gap registry")
    gaps_sub = gaps_parser.add_subparsers(dest="gap_action")
    gaps_sub.add_parser("stats", help="Show gap summary")

    capability_parser = sub.add_parser("capability", help="Capability acquisition lifecycle (OJ10)")
    capability_sub = capability_parser.add_subparsers(dest="capability_action")
    capability_list = capability_sub.add_parser("list", help="List acquisition records")
    capability_list.add_argument("--status", default=None, help="Filter by lifecycle status")
    capability_propose = capability_sub.add_parser("propose", help="Register a discovered capability")
    capability_propose.add_argument("capability", help="Short capability name")
    capability_propose.add_argument("--symptom", default="", help="Observed symptom / motivation")
    capability_propose.add_argument("--gap-id", default="", help="Link to a trace-learning gap id")
    capability_decide = capability_sub.add_parser("decide", help="Record a researched decision")
    capability_decide.add_argument("record_id", help="Acquisition record id")
    capability_decide.add_argument("decision", choices=["reuse", "configure", "adapt", "build", "defer"], help="Decision")
    capability_decide.add_argument("--rationale", default="", help="Decision rationale")
    capability_decide.add_argument("--reuse-target", default="", help="Existing integration for reuse")
    capability_activate = capability_sub.add_parser("activate", help="Activate a ready capability")
    capability_activate.add_argument("record_id", help="Acquisition record id")
    capability_activate.add_argument("--candidate", default="", help="Path to the built candidate")
    capability_health = capability_sub.add_parser("health", help="Record capability health")
    capability_health.add_argument("record_id", help="Acquisition record id")
    capability_health.add_argument("--status", default="ok", help="Health status (ok/degraded)")
    capability_retire = capability_sub.add_parser("retire", help="Retire an active capability")
    capability_retire.add_argument("record_id", help="Acquisition record id")
    capability_retire.add_argument("--reason", default="", help="Retirement reason")
    capability_degrade = capability_sub.add_parser("degrade", help="Mark an active capability degraded")
    capability_degrade.add_argument("record_id", help="Acquisition record id")
    capability_degrade.add_argument("--reason", default="", help="Why the capability is degraded")
    capability_recover = capability_sub.add_parser("recover", help="Return a degraded capability to active")
    capability_recover.add_argument("record_id", help="Acquisition record id")
    capability_verify_live = capability_sub.add_parser("verify-live", help="Record live operational verification")
    capability_verify_live.add_argument("record_id", help="Acquisition record id")
    capability_verify_live.add_argument("--evidence", default="", help="Live verification evidence")
    capability_feasibility = capability_sub.add_parser("feasibility", help="Record or show a feasibility record (TJ1)")
    capability_feasibility.add_argument("record_id", help="Acquisition record id")
    capability_feasibility.add_argument("--set-json", default="", help="JSON file with feasibility data to record")

    memory_parser = sub.add_parser("memory",
        help="Manage validated memories (retract/purge/list/edit/export/retention)")
    memory_sub = memory_parser.add_subparsers(dest="memory_subcommand")
    for action in ("retract", "purge"):
        action_parser = memory_sub.add_parser(action,
            help=("mark retracted (soft delete)" if action == "retract"
                  else "hard-delete with cascade"))
        action_parser.add_argument("memory_id", help="Memory ID to retract or purge")
        action_parser.add_argument(
            "--reason", default="user requested",
            help="Reason for retraction or purge (defaults to 'user requested')")
    memory_list = memory_sub.add_parser("list", help="List active memories")
    memory_list.add_argument("--kind", default=None, help="Filter by memory kind")
    memory_list.add_argument("--json", action="store_true", help="Output JSON")
    memory_list.add_argument("--show-sensitive", action="store_true",
        help="Include health and secret sensitivity records")
    memory_edit = memory_sub.add_parser("edit", help="Edit an active memory")
    memory_edit.add_argument("memory_id", help="Memory ID to edit")
    memory_edit.add_argument("--content", default=None, help="New content (keeps revision history)")
    memory_edit.add_argument("--subject", default=None, help="New subject")
    memory_edit.add_argument("--sensitivity", default=None,
        choices=["normal", "personal", "health", "secret"], help="New sensitivity")
    memory_edit.add_argument("--verified", action="store_true",
        help="Confirm the memory as verified now")
    memory_export = memory_sub.add_parser("export", help="Export memories to a file")
    memory_export.add_argument("--out", required=True, help="Output path (refuses to overwrite)")
    memory_retention = memory_sub.add_parser("retention", help="Manage memory retention")
    retention_sub = memory_retention.add_subparsers(dest="retention_action")
    retention_set = retention_sub.add_parser("set", help="Set retention for a kind")
    retention_set.add_argument("--kind", required=True, help="Memory kind")
    retention_set.add_argument("--days", required=True, type=int, help="Retention days")
    retention_sub.add_parser("apply", help="Purge memories past their retention")

    search_parser = sub.add_parser("search", help="Search codebase via RAG")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results")
    search_parser.add_argument("--path", default=".", help="Path to search in")

    research_parser = sub.add_parser(
        "research", help="Run source-grounded deep scientific research"
    )
    research_parser.add_argument("topic", nargs="+", help="Research question or topic")
    research_parser.add_argument("--model", default=None, help="Override model")
    research_parser.add_argument(
        "--max-searches", type=int, default=24,
        help="Maximum search rounds (default: 24)",
    )

    accomplish_parser = sub.add_parser(
        "accomplish", help="Research, build, test, and repair an outcome in a sandbox"
    )
    accomplish_parser.add_argument("goal", nargs="+", help="Outcome to pursue")
    accomplish_parser.add_argument("--model", default=None, help="Coding/tool-use model")
    accomplish_parser.add_argument("--research-model", default=None, help="Research model")
    accomplish_parser.add_argument("--iterations", type=int, default=12)
    accomplish_parser.add_argument("--command-budget-seconds", type=float, default=300)
    accomplish_parser.add_argument("--command-budget-calls", type=int, default=32)
    accomplish_parser.add_argument("--command-timeout-seconds", type=float, default=30,
        help="Max seconds per command (default 30; increase for heavyweight test suites)")
    accomplish_parser.add_argument("--require-file", action="append", default=[],
                                   help="Required relative deliverable path; repeat for multiple files")
    accomplish_parser.add_argument("--workspace", choices=("project", "empty"), default="project",
                                   help="Sandbox start: copy of this project (default) or an empty directory for greenfield work")
    accomplish_parser.add_argument("--skip-research", action="store_true")
    accomplish_parser.add_argument("--dry-run", action="store_true")
    accomplish_parser.add_argument("--queue", action="store_true", help="Persist the outcome for ghost worker")

    sub.add_parser("doctor", help="Check Ghost runtime health")
    migrate_parser = sub.add_parser("migrate", help="Check or apply .ghost data schema migrations")
    migrate_group = migrate_parser.add_mutually_exclusive_group(required=True)
    migrate_group.add_argument("--check", action="store_true", help="Report version problems only")
    migrate_group.add_argument("--apply", action="store_true", help="Run pending migrations")
    migrate_group.add_argument("--stamp", action="store_true", help="Record current versions for existing store files")
    migrate_parser.add_argument("--dry-run", action="store_true", help="With --apply: report without writing")
    events_parser = sub.add_parser("events", help="Inspect the event log with cursors")
    events_parser.add_argument("action", choices=["tail", "failed"])
    events_parser.add_argument("--cursor", default="", help="tail: cursor name (a-z0-9_-)")
    events_parser.add_argument("--max", type=int, default=100, help="tail: max events")
    events_parser.add_argument("--no-commit", action="store_true", help="tail: do not advance the cursor")
    jobs_parser = sub.add_parser("jobs", help="Inspect jobs and control queued/paused work")
    jobs_parser.add_argument("action", choices=["list", "add", "remove", "pause", "resume", "retry", "cancel"])
    jobs_parser.add_argument("job_id", nargs="?", default="")
    jobs_parser.add_argument("--goal-id", default="", help="jobs add: goal id the recurring job serves")
    jobs_parser.add_argument("--recurrence", default="", help="jobs add: interval:SECONDS for a recurring job")
    jobs_parser.add_argument("--priority", type=int, default=50, help="jobs add: job priority (higher first)")
    jobs_parser.add_argument("--iterations", type=int, default=10, help="jobs add: iteration budget for the job")
    emergency_parser = sub.add_parser("emergency-stop", help="Stop or resume consequential jobs")
    emergency_parser.add_argument("action", choices=["engage", "clear", "status"])
    emergency_parser.add_argument("--reason", default="user requested emergency stop")

    extension_parser = sub.add_parser("extension", help="Propose or scaffold a reviewed extension")
    extension_parser.add_argument("action", choices=["propose", "scaffold", "test", "request-enable", "enable", "status"])
    extension_parser.add_argument("name")
    extension_parser.add_argument("--goal", default="")
    extension_parser.add_argument("--approval-id", default="")

    approval_parser = sub.add_parser("approval", help="Inspect or decide pending approvals")
    approval_parser.add_argument("action", choices=["list", "approve", "reject"])
    approval_parser.add_argument("approval_id", nargs="?")
    approval_parser.add_argument("--note", default="")
    policy_parser = sub.add_parser("policy", help="Show or set standing autonomy policy modes")
    policy_parser.add_argument("action", choices=["show", "set"])
    policy_parser.add_argument("mode", nargs="?")
    policy_parser.add_argument("state", nargs="?", choices=["on", "off"])
    export_parser = sub.add_parser("export", help="Export a versioned, secret-free copy of personal state")
    export_parser.add_argument("--out", required=True)
    export_parser.add_argument("--only", default="",
                               help="Comma-separated sections to include (default: all)")
    promote_parser = sub.add_parser("promote", help="Preview, approve, commit, or undo sandbox promotion")
    promote_parser.add_argument("action", choices=["request", "commit", "rollback", "list"])
    promote_parser.add_argument("value", nargs="?", default="", help="Sandbox path, transaction id, or promotion id")
    promote_parser.add_argument("--goal-id", default="", help="Goal ID required for request")
    promote_parser.add_argument("--approval-id", default="", help="Approved request required for commit")
    control_plane_parser = sub.add_parser("control-plane", help="Print local control-plane state as JSON")
    control_plane_parser.add_argument("--view", default="snapshot",
                                      choices=["snapshot", "goals", "opportunities", "resources",
                                               "integrations", "notifications"])
    api_parser = sub.add_parser("api", help="Serve the local loopback control-plane API")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8765)
    integration_parser = sub.add_parser("integration", help="Plan a user-owned external integration")
    integration_parser.add_argument("action", choices=["plan", "propose"])
    integration_parser.add_argument("name", help="Integration name, e.g. telegram")
    worker_parser = sub.add_parser("worker", help="Execute queued outcome jobs")
    worker_parser.add_argument("--watch", action="store_true", help="Keep polling for queued jobs")
    worker_parser.add_argument("--poll-seconds", type=float, default=5.0)
    checkin_parser = sub.add_parser("check-in", help="Configure opt-in local attention reminders")
    checkin_parser.add_argument("action", choices=["status", "enable", "disable", "tick"])
    checkin_parser.add_argument("--interval-seconds", type=int, default=86400)
    checkin_parser.add_argument("--message", default="Check in: review active goals and pending approvals.")
    checkin_parser.add_argument("--quiet-hours", type=int, nargs=2, default=[22, 8], metavar=("START", "END"))
    notifications_parser = sub.add_parser("notifications", help="List or acknowledge pending notifications")
    notifications_sub = notifications_parser.add_subparsers(dest="subaction", required=True)
    list_parser = notifications_sub.add_parser("list", help="Show deliverable notifications")
    list_parser.add_argument("--all", action="store_true", help="Show all pending, marking held items")
    ack_parser = notifications_sub.add_parser("ack", help="Acknowledge a notification")
    ack_parser.add_argument("notification_id")
    focus_parser = sub.add_parser("focus", help="Start, check, or stop a focus session")
    focus_sub = focus_parser.add_subparsers(dest="subaction", required=True)
    focus_start = focus_sub.add_parser("start", help="Start a focus session")
    focus_start.add_argument("--purpose", required=True, help="What this focus session is for")
    focus_start.add_argument("--minutes", type=int, required=True, help="Session length in minutes (1-600)")
    focus_start.add_argument("--hold-notifications", action="store_true",
                             help="Hold normal/silent notifications for the session")
    focus_sub.add_parser("status", help="Show the active focus session")
    focus_sub.add_parser("stop", help="Stop the active focus session immediately")
    why_parser = sub.add_parser("why", help="Explain why a job or opportunity exists")
    why_parser.add_argument("item_id", help="Job id or opportunity id")
    why_parser.add_argument("--json", action="store_true", help="Print JSON detail only")
    proactive_parser = sub.add_parser("proactive", help="Observe context and queue one bounded proactive experiment")
    proactive_parser.add_argument("action", choices=["tick", "watch", "status", "dismiss", "less", "ranking"])
    proactive_parser.add_argument("--model", default=None, help="Model used to propose the next experiment")
    proactive_parser.add_argument("--interval-seconds", type=float, default=900)
    proactive_parser.add_argument("--idle-after-seconds", type=float, default=900)
    proactive_parser.add_argument("--no-queue", action="store_false", dest="auto_queue", default=True)
    proactive_parser.add_argument("--run-worker", action="store_true",
                                  help="After a queued proposal, run it with Ghost's existing worker")
    proactive_parser.add_argument("--context-file", action="append", default=[],
                                  help="Project-relative text context for initiative planning; repeatable")
    proactive_parser.add_argument("opportunity_id", nargs="?", default="")
    # TN3: transparent ranking of proposed/queued opportunities by score.
    proactive_parser.add_argument("--limit", type=int, default=10,
                                   help="Max opportunities shown by `proactive ranking`")
    # TN3: JSON output for `proactive ranking` (read by args.json).
    proactive_parser.add_argument("--json", action="store_true",
                                  help="Print ranking as JSON")
    repair_parser = sub.add_parser("self-repair", help="Manage bounded Ghost self-repair proposals")
    repair_parser.add_argument("action", choices=["propose", "verify", "request-promotion"])
    repair_parser.add_argument("value", help="Diagnosis for propose or proposal ID otherwise")
    repair_parser.add_argument("--sandbox", default="", help="Candidate sandbox required for propose")
    repair_parser.add_argument("--goal-id", default="", help="Goal ID required for request-promotion")
    repair_parser.add_argument("--command", default="python -m pytest -q",
                               help="Evaluator command run inside the candidate to verify it")
    repair_parser.add_argument("--isolation", choices=["auto", "container", "host"], default="auto",
                               help="Where the evaluator runs: container when available, host fallback, or host only")
    device_parser = sub.add_parser("device", help="Register or inspect declared Ghost devices")
    device_parser.add_argument("action", choices=["list", "register", "pair", "revoke",
                                                  "status", "simulate", "ingest"])
    device_parser.add_argument("device_id", nargs="?")
    device_parser.add_argument("--name", default="")
    device_parser.add_argument("--kind", default="computer")
    device_parser.add_argument("--capability", action="append", default=[])
    device_parser.add_argument("--transport", default=None,
                               help="unknown|usb_serial|ble|wifi|cloud_api|simulated")
    device_parser.add_argument("--firmware", default=None, help="Firmware version string")
    # TF3: simulated pan/tilt command through the safety gate (no real hardware).
    device_parser.add_argument("--pan", type=float, default=None)
    device_parser.add_argument("--tilt", type=float, default=None)
    device_parser.add_argument("--duration", type=float, default=1.0,
                               help="Command duration in seconds")
    device_parser.add_argument("--disconnect", action="store_true",
                               help="Disconnect the simulator after the command")
    # TL4: telemetry ingest — validated data, never instructions.
    device_parser.add_argument("--file", default=None,
                               help="JSON file of telemetry samples (ingest)")
    # TF1/TF4: `ghost huawei` — synthetic/manual data only until Huawei
    # credentials are configured (S02). One CLI action per existing public
    # HuaweiRoute method; no network calls.
    huawei_parser = sub.add_parser(
        "huawei",
        help=("Huawei health route (synthetic/manual data only until Huawei "
              "credentials are configured)"))
    huawei_sub = huawei_parser.add_subparsers(dest="huawei_action")
    huawei_sub.add_parser("route", help="Show the provider route record")
    huawei_sub.add_parser("status", help="Show integration state (honest, not configured until it is)")
    huawei_register = huawei_sub.add_parser("register-route",
                                            help="Register the provider route record")
    huawei_register.add_argument("--name", required=True)
    huawei_register.add_argument("--source", action="append", required=True)
    huawei_register.add_argument("--prerequisite", action="append", default=[])
    huawei_register.add_argument("--limitation", action="append", default=[])
    huawei_set_state = huawei_sub.add_parser("set-state", help="Set the integration state")
    huawei_set_state.add_argument("state", choices=["discovered", "configured", "active",
                                                    "revoked", "unavailable"])
    huawei_ingest = huawei_sub.add_parser("ingest",
                                          help="Ingest synthetic observations from a JSON file")
    huawei_ingest.add_argument("file", help="JSON file of observation rows")
    huawei_correct = huawei_sub.add_parser("correct",
                                           help="Record an owner context correction")
    huawei_correct.add_argument("note", help="e.g. illness, travel, unworn watch, rest day")
    huawei_correct.add_argument("--effect", default="")
    huawei_trend = huawei_sub.add_parser("trend", help="Trend summary (measured only)")
    huawei_trend.add_argument("--metric", default="activity")
    huawei_trend.add_argument("--window-seconds", type=float, default=7 * 86400)
    huawei_retention = huawei_sub.add_parser("retention", help="Show or set retention days")
    huawei_retention.add_argument("--days", type=int, default=None)
    huawei_purge = huawei_sub.add_parser("purge", help="Purge stored observations")
    huawei_purge.add_argument("--all", action="store_true", dest="purge_all")
    mcp_parser = sub.add_parser("mcp", help="Scaffold a reviewable local MCP add-on")
    mcp_parser.add_argument("name")
    mcp_parser.add_argument("--goal", required=True)
    mcp_parser.add_argument("--tool", action="append", required=True)

    parallel_parser = sub.add_parser("parallel", help="Decompose and run sub-agents")
    parallel_parser.add_argument("task_path", help="Path to task.json")
    parallel_parser.add_argument("--model", default=None, help="Override model")
    parallel_parser.add_argument("--verbose", "-v", action="store_true")
    parallel_parser.add_argument("--dry-run", action="store_true", help="Show decomposition only")

    overnight_parser = sub.add_parser("overnight", help="Unattended overnight proactive+worker loop (OJ11/S06)")
    overnight_sub = overnight_parser.add_subparsers(dest="overnight_action")
    overnight_run = overnight_sub.add_parser("run", help="Run the overnight loop")
    overnight_run.add_argument("--interval", type=float, default=300,
                               help="Seconds between cycles (default 300)")
    overnight_run.add_argument("--idle-after-seconds", type=float, default=900,
                               help="OS idle threshold below which the owner counts as active")
    overnight_run.add_argument("--max-cycles", type=int, default=None,
                               help="Stop after N cycles (default: until emergency stop)")
    overnight_run.add_argument("--quiet-hours", nargs=2, type=int, default=None,
                               metavar=("START", "END"),
                               help="Hours 0-23 during which overnight activity is quiet")
    overnight_run.add_argument("--digest-window", nargs=2, type=int, default=None,
                               metavar=("START", "END"),
                               help="Hours 0-23 in which digests are produced")
    # TD5 (S06): durable daily cap on unattended proactive inference calls.
    overnight_run.add_argument("--max-inference-calls", type=int, default=48,
                               help="Daily cap on proactive inference calls (default 48)")
    overnight_idle = overnight_sub.add_parser("until-idle-ends", help="One cycle when the owner goes idle")
    overnight_idle.add_argument("--idle-after-seconds", type=float, default=900)
    trace_parser = sub.add_parser("trace-learning", help="Mine outcome traces for recurring failure signatures (OJ15/S08)")
    trace_sub = trace_parser.add_subparsers(dest="trace_action")
    trace_run_parser = trace_sub.add_parser("run", help="Analyze traces and propose improvements")
    trace_run_parser.add_argument("--min-count", type=int, default=2,
                                  help="Minimum recurrence count to act on (default 2)")
    trace_sub.add_parser("report", help="Print the last trace-learning analysis")

    # OJ-DAEMON: unified AI-OS daemon — composes all live loops
    daemon_parser = sub.add_parser("daemon", help="Always-on loop: overnight proactive+worker cycle plus trace-learning and capability improvement while the owner is away")
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_action")
    daemon_run = daemon_sub.add_parser("run", help="Start the continuous AI-OS daemon")
    daemon_run.add_argument("--interval", type=float, default=300,
                            help="Seconds between proactive ticks (default 300)")
    daemon_run.add_argument("--idle-after-seconds", type=float, default=900,
                            help="OS idle threshold (default 900)")
    daemon_run.add_argument("--max-cycles", type=int, default=None,
                            help="Stop after N cycles (default: until emergency stop)")
    # TD5 (S06): durable daily cap on unattended proactive inference calls.
    daemon_run.add_argument("--max-inference-calls", type=int, default=48,
                            help="Daily cap on proactive inference calls (default 48)")
    daemon_run.add_argument("--trace-every", type=int, default=10,
                            help="Run trace-learning every N cycles (default 10)")
    daemon_run.add_argument("--model", default=None, help="Model for proactive ticks and capability assessment")
    daemon_run.add_argument("--quiet-hours", nargs=2, type=int, default=None,
                            metavar=("START", "END"),
                            help="Hours 0-23 during which daemon activity is quiet")
    daemon_run.add_argument("--digest-window", nargs=2, type=int, default=None,
                            metavar=("START", "END"),
                            help="Hours 0-23 in which digests are produced")
    daemon_sub.add_parser("status", help="Show daemon health / last cycle summary")
    daemon_sub.add_parser("stop", help="Engage emergency stop to stop the daemon")

    # OJ12-S1: learner CLI — concepts, attempts, review, spaced repetition
    learn_parser = sub.add_parser("learn", help="Personal learning: concepts, spaced repetition, oracle-based assessment (OJ12)")
    learn_sub = learn_parser.add_subparsers(dest="learn_action")
    learn_sub.add_parser("status", help="Show learning progress and due concepts")
    learn_sub.add_parser("concepts", help="List registered concepts with mastery")
    add_concept = learn_sub.add_parser("add-concept", help="Register a new concept")
    add_concept.add_argument("concept_id", help="Unique concept identifier (e.g. rust-ownership)")
    add_concept.add_argument("name", help="Human-readable name")
    record_attempt = learn_sub.add_parser("record", help="Record a concept attempt (correct or wrong)")
    record_attempt.add_argument("concept_id", help="Concept identifier")
    record_attempt.add_argument("correct", help="'correct' or 'wrong'")
    record_attempt.add_argument("--submission", default="", help="The submitted answer (for oracle assessment)")
    review_cmd = learn_sub.add_parser("review", help="Review and assess a submission via oracle (RustCompileOracle etc.)")
    review_cmd.add_argument("concept_id", help="Concept identifier")
    review_cmd.add_argument("submission", help="Code or text to assess")
    review_cmd.add_argument("--oracle", default="rustc", help="Oracle: rustc (default), python, custom")
    path_cmd = learn_sub.add_parser("path", help="Generate a prerequisite-ordered learning path")
    path_cmd.add_argument("targets", nargs="+", help="Target concepts for the learning path")
    path_cmd.add_argument("--name", default="", help="Path name")
    path_cmd.add_argument("--description", default="", help="Path description")
    learn_sub.add_parser("next", help="Show the next due concept for spaced-repetition review")
    learn_sub.add_parser("progress", help="Show per-concept mastery and overall progress")
    lesson_cmd = learn_sub.add_parser("lessons", help="Show cited lesson/source ids for a concept")
    lesson_cmd.add_argument("concept_id", help="Concept identifier")
    attempt_cmd = learn_sub.add_parser("attempts", help="Show attempt history for a concept")
    attempt_cmd.add_argument("concept_id", help="Concept identifier")
    attempt_cmd.add_argument("--limit", type=int, default=20, help="Max attempts to show (default 20)")
    gen_cmd = learn_sub.add_parser("exercises", help="Generate practice exercises for a concept (TG3)")
    gen_cmd.add_argument("concept_id", help="Concept identifier")
    gen_cmd.add_argument("--count", type=int, default=3, help="Number of exercises (default 3)")
    gen_cmd.add_argument("--model", default=None, help="Model to generate with")
    answer_cmd = learn_sub.add_parser("answer", help="Submit an answer to an exercise (TG3)")
    answer_cmd.add_argument("exercise_id", help="Exercise identifier")
    answer_cmd.add_argument("submission", help="The submitted answer")
    hint_cmd = learn_sub.add_parser("hint", help="Show the next hint for an exercise (TG3)")
    hint_cmd.add_argument("exercise_id", help="Exercise identifier")
    # TG4: deadline-aware review scheduling
    deadline_cmd = learn_sub.add_parser("deadline", help="Attach a study deadline to concepts (TG4)")
    deadline_cmd.add_argument("label", help="Deadline label (e.g. 'midterm')")
    deadline_cmd.add_argument("--date", required=True, help="Due date YYYY-MM-DD")
    deadline_cmd.add_argument("--concepts", required=True,
                              help="Comma-separated concept ids the deadline applies to")
    challenge_cmd = learn_sub.add_parser("challenge", help="Practice challenges with protected oracles (TK2)")
    challenge_sub = challenge_cmd.add_subparsers(dest="challenge_action")
    challenge_create = challenge_sub.add_parser("create", help="Create a challenge from a spec JSON file")
    challenge_create.add_argument("--spec", required=True, help="Path to the challenge spec JSON")
    challenge_sub.add_parser("list", help="List practice challenges")
    challenge_check = challenge_sub.add_parser("check", help="Run the oracle on the learner's starter")
    challenge_check.add_argument("challenge_id", help="Challenge identifier")
    challenge_check.add_argument("--concept", default="", help="Concept to record the attempt against")
    challenge_reset = challenge_sub.add_parser("reset", help="Reset a challenge's starter files")
    challenge_reset.add_argument("challenge_id", help="Challenge identifier")
    learn_sub.add_parser("formats", help="Show which teaching format works and the recommendation (TK3)")

    # OJ14-S1: SelfImprover CLI
    improve_parser = sub.add_parser("self-improve", help="Propose, evaluate, and roll back structural improvements (OJ14/S08)")
    improve_sub = improve_parser.add_subparsers(dest="improve_action")
    improve_list = improve_sub.add_parser("list", help="List improvement candidates")
    improve_list.add_argument("--status", default=None, help="Filter by status: queued/handed_to_self_repair/failed")
    improve_propose = improve_sub.add_parser("propose", help="Propose a new improvement from trace-learning insights")
    improve_propose.add_argument("--model", default=None, help="Model to use (default: configured or $GHOST_MODEL)")
    improve_sub.add_parser("sync", help="Sync queued improvement jobs with worker results")
    improve_decide = improve_sub.add_parser("decide", help="Approve or reject an improvement candidate")
    improve_decide.add_argument("candidate_id", help="Candidate ID")
    improve_decide.add_argument("decision", help="approved/rejected/rollback")
    improve_decide.add_argument("reason", help="Reason for decision")

    # T11: ghost bench
    bench_parser = sub.add_parser("bench", help="Run the fixed benchmark of 8 non-toy goals with hidden acceptance tests")
    bench_sub = bench_parser.add_subparsers(dest="bench_action")
    bench_sub.add_parser("list", help="List all benchmark goals with their module files")
    bench_run = bench_sub.add_parser("run", help="Run the benchmark suite")
    bench_run.add_argument("--model", default=None, help="Model to use (default: configured or $GHOST_MODEL)")
    bench_run.add_argument("--goal", dest="goals", action="append", default=None, help="Goal id to run (default: all 8)")
    bench_run.add_argument("--iterations", type=int, default=10, help="Max iterations per goal (default: 10)")
    bench_sub.add_parser("report", help="Show the latest benchmark report")
    bench_sub.add_parser("history", help="List all benchmark runs with their rates")
    bench_cmp = bench_sub.add_parser("compare", help="Compare two benchmark runs")
    bench_cmp.add_argument("run_a", help="Older run id (or 'latest'/'previous')")
    bench_cmp.add_argument("run_b", help="Newer run id (or 'latest'/'previous')")

    # Protocol benchmark CLI (R05) — forwards to protocol_benchmark.main()
    protocol_bench_parser = sub.add_parser(
        "protocol-bench",
        help="Run the non-executing protocol benchmark (LLM protocol fidelity)"
    )
    protocol_bench_parser.add_argument("--output-root", type=Path, default=Path("outputs/benchmarks"))
    protocol_bench_parser.add_argument("--model", default="qwen2.5-coder:1.5b")
    protocol_bench_parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    protocol_bench_parser.add_argument("--repetitions", type=int, default=2)
    protocol_bench_parser.add_argument("--prompt-style", choices=["production", "compact"], default="production")
    protocol_bench_parser.add_argument("--dataset", choices=["baseline", "validation"], default="baseline")
    protocol_bench_parser.add_argument("--output-format", choices=["unconstrained", "json", "schema"], default="unconstrained")

    # TH2: ghost model-profile
    model_profile_parser = sub.add_parser("model-profile", help="Show, set or probe model profiles (unknown values stay unknown)")
    mp_sub = model_profile_parser.add_subparsers(dest="model_profile_action")
    mp_show = mp_sub.add_parser("show", help="Show a model profile")
    mp_show.add_argument("model", help="Model name")
    mp_set = mp_sub.add_parser("set", help="Set known values for a model")
    mp_set.add_argument("model", help="Model name")
    mp_set.add_argument("--context", type=int, default=None, help="Context window in tokens")
    mp_set.add_argument("--max-output", type=int, default=None, help="Max output tokens")
    mp_set.add_argument("--cost-in", type=float, default=None, help="Cost per 1k input tokens")
    mp_set.add_argument("--cost-out", type=float, default=None, help="Cost per 1k output tokens")
    mp_probe = mp_sub.add_parser("probe", help="Probe Ollama /api/show for a model")
    mp_probe.add_argument("model", help="Model name")
    mp_probe.add_argument("--base-url", default=None, help="Ollama base URL (default: configured or $OLLAMA_BASE_URL)")

    # Capabilities CLI
    perception_parser = sub.add_parser("perception", help="Read-only observation: filesystem, git, processes")
    perception_sub = perception_parser.add_subparsers(dest="perception_action")
    fs_perc = perception_sub.add_parser("filesystem", help="Observe workspace file states")
    fs_perc.add_argument("paths", nargs="+", help="Paths to observe (within project)")
    git_perc = perception_sub.add_parser("git", help="Observe git working tree state")
    proc_perc = perception_sub.add_parser("process", help="Observe process liveness")
    proc_perc.add_argument("pid", type=int, help="Process ID to check")

    # Context synthesizer CLI
    ctx_parser = sub.add_parser("context", help="Synthesize runtime context for model prompts")
    ctx_sub = ctx_parser.add_subparsers(dest="ctx_action")
    ctx_sub.add_parser("assemble", help="Assemble full context for a goal")
    ctx_sub.add_parser("conflicts", help="List detected context conflicts")

    args = parser.parse_args()

    if args.command is None:
        _cmd_interactive_default()
        return

    dispatch = {
        "openjarvis": lambda: _cmd_openjarvis(args),
        "run": lambda: _cmd_run(args),
        "resume": lambda: _cmd_resume(args),
        "audit": lambda: _cmd_audit(args),
        "status": _cmd_status,
        "init": lambda: _cmd_init(args.path),
        "index": lambda: _cmd_index(args),
        "knowledge": lambda: _cmd_knowledge(args),
        "kbase": lambda: _cmd_kbase(args),
        "lora": lambda: _cmd_lora(args),
        "search": lambda: _cmd_search(args),
        "research": lambda: _cmd_research(args),
        "accomplish": lambda: _cmd_accomplish(args),
        "doctor": _cmd_doctor,
        "migrate": lambda: _cmd_migrate(args),
        "events": lambda: _cmd_events(args),
        "jobs": lambda: _cmd_jobs(args),
        "emergency-stop": lambda: _cmd_emergency_stop(args),
        "extension": lambda: _cmd_extension(args),
        "approval": lambda: _cmd_approval(args),
        "policy": lambda: _cmd_policy(args),
        "export": lambda: _cmd_export(args),
        "promote": lambda: _cmd_promote(args),
        "control-plane": lambda: _cmd_control_plane(args),
        "api": lambda: _cmd_api(args),
        "integration": lambda: _cmd_integration(args),
        "worker": lambda: _cmd_worker(args),
        "check-in": lambda: _cmd_check_in(args),
        "notifications": lambda: _cmd_notifications(args),
        "focus": lambda: _cmd_focus(args),
        "why": lambda: _cmd_why(args),
        "proactive": lambda: _cmd_proactive(args),
        "self-repair": lambda: _cmd_self_repair(args),
        "device": lambda: _cmd_device(args),
        "huawei": lambda: _cmd_huawei(args),
        "mcp": lambda: _cmd_mcp(args),
        "parallel": lambda: _cmd_parallel(args),
        "gaps": lambda: _cmd_gaps(args),
        "capability": lambda: _cmd_capability(args),
        "memory": lambda: _cmd_memory(args),
        "overnight": lambda: _cmd_overnight(args),
        "digest": lambda: _cmd_digest(args),
        "trace-learning": lambda: _cmd_trace_learning(args),
        "daemon": lambda: _cmd_daemon(args),
        "learn": lambda: _cmd_learn(args),
        "self-improve": lambda: _cmd_self_improve(args),
        "perception": lambda: _cmd_perception(args),
        "context": lambda: _cmd_context(args),
        "bench": lambda: _cmd_bench(args),
        "protocol-bench": lambda: _cmd_protocol_bench(args),
        "model-profile": lambda: _cmd_model_profile(args),
    }
    fn = dispatch.get(args.command)
    if fn:
        fn()


# ── Commands ─────────────────────────────────────────────────────────────────


def _cmd_openjarvis(args):
    from .openjarvis_bridge.cli import dispatch
    dispatch(args)


def _cmd_proactive(args: argparse.Namespace) -> None:
    from .proactive import ProactiveEngine, ProactiveError, OpportunityStore
    root = Path.cwd().resolve()
    try:
        if args.action == "status":
            print(json.dumps([item.__dict__ for item in OpportunityStore(root).list()], indent=2))
            return
        if args.action == "ranking":
            _cmd_proactive_ranking(root, limit=args.limit, as_json=args.json)
            return
        model = _resolve_model(args.model)
        engine = ProactiveEngine(root, model, context_paths=args.context_file)
        if args.action == "tick":
            result = engine.tick(auto_queue=args.auto_queue)
        elif args.action == "watch":
            worker_fn = None
            if args.run_worker:
                from .worker import OutcomeWorker
                worker_fn = lambda: OutcomeWorker(root).run_next(
                    lambda scheduled: _execute_queued_outcome(root, scheduled))
            engine.watch(interval=args.interval_seconds, idle_after=args.idle_after_seconds,
                         auto_queue=args.auto_queue, run_worker=worker_fn)
            return
        elif args.action == "dismiss":
            if not args.opportunity_id:
                raise ProactiveError("dismiss requires an opportunity ID")
            result = {"status": "dismissed", "opportunity": engine.dismiss(args.opportunity_id).__dict__}
        elif args.action == "less":
            if not args.opportunity_id:
                raise ProactiveError("less requires an opportunity ID")
            result = {"status": "less_like_this",
                      "opportunity": engine.mark_less_like_this(args.opportunity_id).__dict__}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ProactiveError as exc:
        print(f"Proactive action refused: {exc}")


def _cmd_proactive_ranking(root, *, limit: int = 10, as_json: bool = False) -> None:
    """TN3: print proposed/queued opportunities sorted by score, with reasons."""
    from .proactive import OpportunityStore
    items = [item for item in OpportunityStore(root).list(100)
             if item.status in {"proposed", "queued"}]
    items.sort(key=lambda o: o.score, reverse=True)
    items = items[:max(0, limit)]
    if as_json:
        print(json.dumps([item.__dict__ for item in items], indent=2, ensure_ascii=False))
        return
    if not items:
        print("No proposed or queued opportunities.")
        return
    for item in items:
        print(f"{item.score:.3f}  [{item.status}] {item.opportunity_id} {item.goal}")
        for reason in item.score_reasons:
            print(f"    - {reason}")


def _resolve_model(cli_override: str | None = None) -> str:
    """Resolve the model: CLI override > env var > config file."""
    if cli_override:
        return cli_override
    env_model = os.getenv("GHOST_MODEL")
    if env_model:
        return env_model
    cfg_model = get_model()
    if cfg_model:
        return cfg_model
    return ""


def _resolve_research_model(cli_override: str | None = None) -> str:
    """Resolve research separately from the coding/tool-use model."""
    if cli_override:
        return cli_override
    env_model = os.getenv("GHOST_RESEARCH_MODEL")
    if env_model:
        return env_model
    return get_research_model() or _resolve_model()


def _configured_model_candidates(environment_name: str, fallback: str) -> list[str]:
    """Read an explicit local candidate list without probing or downloading models."""
    raw = os.getenv(environment_name, "")
    candidates = [value.strip() for value in raw.split(",") if value.strip()]
    return list(dict.fromkeys([*candidates, fallback] if fallback else candidates))


def _pick_model_interactive(base_url: str | None = None) -> str:
    """First-boot: fetch models from Ollama and let the user pick one."""
    print(f"\n{_BRIGHT_CYAN}Ghost{_RESET} needs a model to run.")
    print(f"{_DIM}Fetching installed models from Ollama...{_RESET}\n")

    models = fetch_models(base_url=base_url)

    if not models:
        print(f"{_DIM}No models found or Ollama is not running.{_RESET}")
        print(f"{_DIM}Start Ollama, then run `ollama pull <model>` to install one.{_RESET}")
        print(f"{_DIM}Set GHOST_MODEL env var or use /model in the REPL to pick later.{_RESET}\n")
        sys.exit(1)

    seen = set()
    unique_models = []
    for m in models:
        name = m.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique_models.append(m)

    print(f"{_BOLD}Available models:{_RESET}\n")
    for i, m in enumerate(unique_models, 1):
        name = m.get("name", "?")
        size = format_model_size(m.get("size", 0))
        details = m.get("details", {})
        param_size = details.get("parameter_size", "")
        quant = details.get("quantization_level", "")
        info_parts = [p for p in [param_size, quant] if p]
        info = f" ({', '.join(info_parts)})" if info_parts else ""
        print(f"  {_BRIGHT_CYAN}{i}{_RESET}. {name}{_DIM}{info}  [{size}]{_RESET}")

    print()
    while True:
        try:
            choice = input("Select model number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{_DIM}Goodbye!{_RESET}")
            sys.exit(0)
        if not choice:
            continue
        try:
            idx = int(choice)
            if 1 <= idx <= len(unique_models):
                selected = unique_models[idx - 1]["name"]
                set_model(selected)
                print(f"{_DIM}Model set to {selected} (saved to ~/.ghost/config.json){_RESET}\n")
                return selected
        except ValueError:
            for m in unique_models:
                if choice == m["name"]:
                    set_model(choice)
                    print(f"{_DIM}Model set to {choice} (saved to ~/.ghost/config.json){_RESET}\n")
                    return choice
        print(f"{_DIM}Invalid choice. Enter a number 1-{len(unique_models)} or the model name.{_RESET}")


def _cmd_run(args: argparse.Namespace) -> None:
    """Run a task from a JSON file."""
    task_path = Path(args.task_path)
    if not task_path.exists():
        print(f"Error: {task_path} not found")
        sys.exit(1)

    model = _resolve_model(args.model)
    if not model:
        base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
        model = _pick_model_interactive(base_url)
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))

    task_data = json.loads(task_path.read_text(encoding="utf-8"))
    task_id = task_data.get("id", task_path.stem)
    max_iters = task_data.get("constraints", {}).get("iteration_limit", 8)

    tui = create_tui(task_id=task_id, verbose=args.verbose)
    tui.start(task_id=task_id, max_iterations=max_iters)

    orchestrator = Orchestrator(
        repo_root=Path.cwd(),
        model=model,
        base_url=base_url,
        timeout=timeout,
        verbose=args.verbose,
        tui=tui,
    )

    try:
        orchestrator.run_task(task_path)
    except KeyboardInterrupt:
        print(f"\n{_DIM}Interrupted.{_RESET}")
    except Exception as exc:
        print(f"\nError: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
    finally:
        tui.finish()


def _cmd_resume(args: argparse.Namespace) -> None:
    """Resume a previous run."""
    model = _resolve_model()
    if not model:
        base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
        model = _pick_model_interactive(base_url)
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))

    tui = create_tui(task_id=args.task_id, verbose=args.verbose)
    tui.start(task_id=args.task_id)

    orchestrator = Orchestrator(
        repo_root=Path.cwd(),
        model=model,
        base_url=base_url,
        timeout=timeout,
        verbose=args.verbose,
        tui=tui,
    )

    try:
        orchestrator.resume_task(args.task_id)
    except KeyboardInterrupt:
        print(f"\n{_DIM}Interrupted.{_RESET}")
    except Exception as exc:
        print(f"\nError: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
    finally:
        tui.finish()


def _cmd_interactive_default() -> None:
    """Launch interactive mode with sensible defaults."""
    class _Args:
        project_root = "."
        watch = True
        test_cmd = "pytest -q"
        iteration_limit = 8
        quiet = False
        verbose = False
    _cmd_interactive(_Args())


def _cmd_interactive(args: argparse.Namespace) -> None:
    """Interactive mode with file watching."""
    project_root = Path(args.project_root).resolve()
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))

    model_state = {"model": _resolve_model()}
    if not model_state["model"]:
        model_state["model"] = _pick_model_interactive(base_url)

    print(f"{_DIM}Model: {model_state['model']}{_RESET}")

    artifacts_base = project_root / ".ghost" / "workspaces"
    artifacts_base.mkdir(parents=True, exist_ok=True)

    ignore_dirs = {
        ".ghost", ".git", ".venv", "__pycache__",
        "node_modules", "workspaces", "memories",
    }
    watcher = FileWatcher(project_root, ignore_dirs=ignore_dirs)
    run_lock = threading.Lock()
    state_lock = threading.Lock()
    last_run_time = {"value": 0.0}
    running = {"value": False}
    counter = itertools.count(1)

    tui = create_tui(task_id="interactive", verbose=args.verbose)

    def run_goal(goal: str, auto: bool) -> None:
        with run_lock:
            running["value"] = True
            task_id = f"live_{time.strftime('%Y%m%d_%H%M%S')}_{next(counter)}"
            task = Task(
                task_id=task_id,
                title="Interactive task",
                goal=goal,
                constraints={
                    "test_cmd": args.test_cmd,
                    "iteration_limit": args.iteration_limit,
                },
                context={"repo_path": str(project_root), "notes": "interactive"},
            )
            artifacts_root = artifacts_base / task_id

            tui.start(task_id=task_id, max_iterations=args.iteration_limit)
            orchestrator = Orchestrator(
                repo_root=project_root,
                model=model_state["model"],
                base_url=base_url,
                timeout=timeout,
                verbose=args.verbose,
                tui=tui,
            )
            orchestrator.run_task_with_roots(
                task, workspace_root=project_root, artifacts_root=artifacts_root
            )

            with state_lock:
                last_run_time["value"] = time.time()
            running["value"] = False

        if auto:
            print("[ghost] Auto-run complete.\n")

    def watch_loop() -> None:
        while True:
            time.sleep(2.0)
            if not args.watch:
                continue
            if running["value"]:
                continue
            with state_lock:
                if time.time() - last_run_time["value"] < 2.0:
                    continue
            changes = watcher.poll()
            if not changes:
                continue
            summary = "; ".join(changes)
            auto_goal = (
                "User edited files detected: "
                f"{summary}. Review these changes and improve or fix issues. "
                "Focus only on the changed files unless necessary."
            )
            if run_lock.locked():
                continue
            run_goal(auto_goal, auto=True)

    watcher_thread = threading.Thread(target=watch_loop, daemon=True)
    watcher_thread.start()

    print(f"{_BRIGHT_CYAN}Ghost{_RESET} interactive mode. Type a goal or /help.\n")
    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            if line.startswith("/watch"):
                args.watch = "off" not in line.lower()
                print(f"[ghost] watch={'on' if args.watch else 'off'}")
                continue
            if line == "/help":
                print(
                    "Commands:\n"
                    "  /model      Change the active model\n"
                    "  /watch on   Enable auto-run on file changes\n"
                    "  /watch off  Disable auto-run\n"
                    "  /audit      Show last run's audit report\n"
                    "  /knowledge  Show knowledge graph report\n"
                    "  /accomplish <goal>  Research, build, test, and repair a sandboxed outcome\n"
                    "  /exit       Quit\n"
                    "\n"
                    "Code and integration requests become durable sandboxed outcome jobs.\n"
                    "Just tell it what you need."
                )
                continue
            if line == "/model":
                current = model_state["model"]
                print(f"{_DIM}Current model: {current}{_RESET}\n")
                print(f"{_DIM}Fetching installed models from Ollama...{_RESET}")
                models = fetch_models(base_url=base_url)
                if models:
                    seen = set()
                    unique = []
                    for m in models:
                        name = m.get("name", "")
                        if name and name not in seen:
                            seen.add(name)
                            unique.append(m)
                    print(f"\n{_BOLD}Installed models:{_RESET}\n")
                    for i, m in enumerate(unique, 1):
                        name = m.get("name", "?")
                        size = format_model_size(m.get("size", 0))
                        marker = " <-" if name == current else ""
                        print(f"  {_BRIGHT_CYAN}{i}{_RESET}. {name}{_DIM}  [{size}]{_RESET}{marker}")
                    print()
                    print(f"  {_DIM}Type a number to select, or type any model name to use it.{_RESET}")
                else:
                    print(f"{_DIM}Ollama not reachable. Type a model name directly.{_RESET}\n")
                try:
                    choice = input("Model: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if not choice:
                    continue
                try:
                    idx = int(choice)
                    if models and 1 <= idx <= len(unique):
                        choice = unique[idx - 1]["name"]
                    else:
                        print(f"{_DIM}Invalid number. Type a model name instead.{_RESET}")
                        continue
                except ValueError:
                    pass
                model_state["model"] = choice
                set_model(choice)
                print(f"{_DIM}Model set to {choice}{_RESET}\n")
                continue
            if line == "/audit":
                runs = sorted(artifacts_base.iterdir()) if artifacts_base.exists() else []
                if runs:
                    audit_run(runs[-1])
                else:
                    print("[ghost] No runs yet.")
                continue
            if line == "/knowledge":
                _cmd_knowledge_simple(project_root)
                continue
            if line.startswith("/accomplish "):
                outcome_goal = line[len("/accomplish "):].strip()
                if not outcome_goal:
                    print("Usage: /accomplish <outcome>\n")
                    continue
                _cmd_accomplish(argparse.Namespace(
                    goal=[outcome_goal], model=model_state["model"],
                    research_model=None, iterations=12, skip_research=False, dry_run=False, queue=False,
                    workspace="project",
                ))
                continue
            # LLM-driven intent classification
            intent = _classify_intent(
                line, model_state["model"], base_url, timeout=15
            )
            if intent == "chat":
                reply = _chat_directly(
                    line, model_state["model"], base_url, timeout
                )
                print(f"{_BRIGHT_CYAN}Ghost{_RESET}: {reply}\n")
            elif intent == "search":
                print(f"{_DIM}Searching the web...{_RESET}")
                reply = _search_directly(
                    line, model_state["model"], base_url, timeout
                )
                print(f"{_BRIGHT_CYAN}Ghost{_RESET}: {reply}\n")
            elif intent == "research":
                print(f"{_DIM}Starting deep research...{_RESET}\n")
                reply = _research_directly(
                    line, _resolve_research_model(), base_url, timeout,
                    graph_root=project_root / ".ghost" / "research",
                )
                print(f"\n{_BRIGHT_CYAN}Ghost{_RESET}: {reply}\n")
            else:
                # A code/outcome request in the REPL uses the same durable
                # research → sandbox → test/repair path as `ghost accomplish`.
                _cmd_accomplish(argparse.Namespace(
                    goal=[line], model=model_state["model"],
                    research_model=None, iterations=12, skip_research=False, dry_run=False, queue=False,
                    workspace="project",
                ))
    except KeyboardInterrupt:
        print(f"\n{_DIM}Goodbye!{_RESET}")


def _cmd_audit(args: argparse.Namespace) -> None:
    """Show transparency report."""
    repo_root = Path.cwd()
    workspaces_root = repo_root / "workspaces"

    if args.task_id:
        workspace = workspaces_root / args.task_id
        if not workspace.exists():
            print(f"Workspace not found: {workspace}")
            sys.exit(1)
        audit_run(workspace)
    else:
        audit_all(workspaces_root)


def _cmd_status() -> None:
    """Show last run summary."""
    repo_root = Path.cwd()
    workspaces_root = repo_root / "workspaces"

    if not workspaces_root.exists():
        print("No runs found.")
        return

    runs = sorted(
        [d for d in workspaces_root.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    if not runs:
        print("No runs found.")
        return

    last_run = runs[-1]
    state_path = last_run / "state.json"
    task_path = last_run / "task.json"

    if not state_path.exists():
        print(f"No state found for {last_run.name}")
        return

    state = json.loads(state_path.read_text(encoding="utf-8"))
    task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}

    phase = state.get("phase", "?")
    result = state.get("last_result", "?")
    iters = state.get("iteration", 0)
    files = state.get("files_touched", [])

    print(f"\n  {_BOLD}Last Run:{_RESET} {_BRIGHT_CYAN}{task.get('id', last_run.name)}{_RESET}")
    print(f"  {_DIM}Title:{_RESET}    {task.get('title', '?')}")
    print(f"  {_DIM}Phase:{_RESET}    {phase}")
    print(f"  {_DIM}Result:{_RESET}   {result}")
    print(f"  {_DIM}Iters:{_RESET}   {iters}")
    print(f"  {_DIM}Files:{_RESET}   {len(files)} touched")
    print()


def _cmd_init(path: str) -> None:
    """Create a starter task.json."""
    task_path = Path(path)
    if task_path.exists():
        print(f"File already exists: {task_path}")
        return

    starter = {
        "id": "my_task_001",
        "title": "My task",
        "goal": "Describe what you want the agent to do here.",
        "constraints": {"test_cmd": "pytest -q", "iteration_limit": 8},
        "context": {"notes": "Optional notes for the agent."},
    }

    task_path.write_text(json.dumps(starter, indent=2) + "\n", encoding="utf-8")
    print(f"Created {task_path}")
    print(f"\nEdit it, then run:")
    print(f"  ghost run {task_path}")


def _cmd_index(args: argparse.Namespace) -> None:
    """Build/update the codebase index."""
    from .context import CodeIndex

    path = Path(args.path).resolve()
    print(f"Building code index for {path}...")

    index = CodeIndex()
    exclude = {".git", ".venv", "__pycache__", "node_modules", ".ghost", "memories", "workspaces"}
    index.build(path, exclude_dirs=exclude)
    stats = index.stats()

    print(f"\n{_BOLD}Code Index Built{_RESET}")
    print(f"  Files:      {stats['files']}")
    print(f"  Lines:      {stats['total_lines']:,}")
    print(f"  Functions:  {stats['functions']}")
    print(f"  Classes:    {stats['classes']}")
    print(f"  Tokens:     {stats['unique_tokens']:,}")

    if index.summaries:
        print(f"\n{_DIM}Indexed files:{_RESET}")
        for path_str, summary in sorted(index.summaries.items()):
            parts = []
            if summary.functions:
                parts.append(f"{len(summary.functions)} funcs")
            if summary.classes:
                parts.append(f"{len(summary.classes)} classes")
            detail = ", ".join(parts) if parts else "empty"
            print(f"  {path_str} ({summary.line_count}L, {detail})")


def _cmd_knowledge(args: argparse.Namespace) -> None:
    """Show knowledge graph report."""
    repo_root = Path.cwd()
    _cmd_knowledge_simple(repo_root, args.strategy if hasattr(args, "strategy") else False)


def _cmd_knowledge_simple(repo_root: Path, strategy_only: bool = False) -> None:
    """Show knowledge graph report."""
    from .knowledge import KnowledgeGraph

    kg = KnowledgeGraph(repo_root / "memories")

    if strategy_only or kg.tasks:
        print(f"\n{_BOLD}{_BRIGHT_CYAN}Knowledge Graph Report{_RESET}\n")

        if kg.tasks:
            print(f"{_DIM}Tasks completed:{_RESET} {len(kg.tasks)}")
            successes = sum(1 for t in kg.tasks if t.success)
            print(f"{_DIM}Success rate:{_RESET}   {successes}/{len(kg.tasks)} "
                  f"({successes/len(kg.tasks)*100:.0f}%)\n")

            print(f"{_DIM}Recent tasks:{_RESET}")
            for task in kg.tasks[-5:]:
                status = "[OK]" if task.success else "[FAIL]"
                print(f"  {status} {task.task_id}: {task.goal[:50]}")
            print()

        if kg.strategies:
            print(kg.get_strategy_report())
        else:
            print(f"  {_DIM}No strategy data yet. Run some tasks first.{_RESET}\n")

        if kg.files:
            hot = kg.get_hot_files(5)
            if hot:
                print(f"\n{_DIM}Most modified files:{_RESET}")
                for path, count in hot:
                    print(f"  {path}: {count}x")
    else:
        print(f"\n{_DIM}No knowledge graph data yet.{_RESET}")
        print(f"Run some tasks with `ghost run` to build knowledge.")


def _cmd_search(args: argparse.Namespace) -> None:
    """Search codebase via RAG."""
    _cmd_search_simple(Path(args.path).resolve(), args.query, args.limit)


def _cmd_search_simple(repo_root: Path, query: str, limit: int = 10) -> None:
    """Search codebase via RAG."""
    from .context import CodeIndex

    print(f"Searching for: {query}\n")

    index = CodeIndex()
    exclude = {".git", ".venv", "__pycache__", "node_modules", ".ghost", "memories", "workspaces"}
    index.build(repo_root, exclude_dirs=exclude)

    results = index.search(query, limit=limit)

    if not results:
        print(f"{_DIM}No results found.{_RESET}")
        return

    for i, result in enumerate(results, 1):
        score_color = _GREEN if result.score > 2 else _CYAN if result.score > 1 else _DIM
        print(f"{_BOLD}{i}.{_RESET} {score_color}{result.file_path}{_RESET} "
              f"(score: {result.score:.2f}, {result.match_type})")
        for line in result.snippet.split("\n"):
            print(f"   {_DIM}{line}{_RESET}")
        print()


def _cmd_research(args: argparse.Namespace) -> None:
    """Run the source-grounded research pipeline without intent classification."""
    model = _resolve_research_model(args.model)
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    if not model:
        model = _pick_model_interactive(base_url)
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))
    topic = " ".join(args.topic).strip()
    if not topic:
        print("Research topic cannot be empty.")
        return
    report = _research_directly(
        topic, model, base_url, timeout, max_searches=max(1, args.max_searches),
        graph_root=Path.cwd() / ".ghost" / "research",
    )
    print(f"\n{report}")


def _cmd_kbase(args: argparse.Namespace) -> None:
    """Ingest / status / query for the owner reference knowledge base."""
    from .knowledge_base import OwnerKnowledgeBase
    kb = OwnerKnowledgeBase(Path.cwd())
    action = getattr(args, "kb_action", None)
    if action == "ingest":
        stats = kb.ingest(args.path)
        print(f"Indexed: {stats.document_count} documents, {stats.chunk_count} chunks, "
              f"{stats.total_chars} chars.")
    elif action == "query":
        query = " ".join(args.text).strip()
        hits = kb.retrieve(query, limit=4)
        for chunk in hits:
            print(f"[{chunk.source}]\n{chunk.content}\n")
        if not hits:
            print("No relevant reference chunks found.")
    elif action == "delete":
        removed = kb.delete_source(args.source)
        if removed:
            print(f"Deleted {removed} chunks from source '{args.source}'.")
        else:
            print(f"No chunks found for source '{args.source}'.")
    elif action == "sources":
        rows = kb.sources()
        if not rows:
            print("No sources indexed.")
        for row in rows:
            print(f"[v{row['version']}] {row['chunks']} chunks  {row['source']}")
    else:  # status or bare
        stats = kb.stats()
        print(f"knowledge base: {stats.document_count} documents, "
              f"{stats.chunk_count} chunks, {stats.total_chars} chars")
        print(f"last ingest: {stats.last_ingest_at}")
        print("Use: ghost kbase ingest <dir> | ghost kbase query \"<text>\"")


def _cmd_gaps(args) -> None:
    from .autonomy import CapabilityRegistry
    reg = CapabilityRegistry(Path.cwd())
    data = reg._load()
    gaps = data.get("gaps", [])
    action = getattr(args, "gap_action", None) or "list"
    if action == "stats":
        by_status = {}
        for g in gaps:
            by_status[g.get("status","unknown")] = by_status.get(g.get("status","unknown"),0)+1
        print(f"Total gaps: {len(gaps)}")
        for s,c in sorted(by_status.items()): print(f"  {s}: {c}")
    else:
        queued = [g for g in gaps if g.get("status")=="queued"]
        print(f"Queued gaps: {len(queued)}")
        for g in sorted(queued, key=lambda x: x.get("count",1), reverse=True):
            print(f"  [{g['id']}] count={g.get('count')} {g.get('goal','')[:80]}")
        if not queued: print("No queued gaps.")


def _cmd_capability(args: argparse.Namespace) -> None:
    """OJ10: drive the CapabilityAcquisition lifecycle from the CLI."""
    from .capability_acquisition import CapabilityAcquisition, AcquisitionError
    store = CapabilityAcquisition(Path.cwd())
    try:
        action = args.capability_action
        if action == "list":
            records = store.list(status=getattr(args, "status", None))
            print(json.dumps([{**r.__dict__} for r in records], indent=2))
        elif action == "propose":
            record = store.discover(args.capability, args.symptom or "")
            if getattr(args, "gap_id", ""):
                record = store.set_prerequisite(record.record_id, f"gap:{args.gap_id}", False)
            print(json.dumps(record.__dict__, indent=2))
        elif action == "decide":
            record = store.research(args.record_id, args.decision, args.rationale or "",
                                    getattr(args, "reuse_target", "") or "")
            print(json.dumps(record.__dict__, indent=2))
        elif action == "activate":
            record = store.get(args.record_id)
            if record is None:
                raise ValueError("Acquisition record not found")
            if record.status != "ready_to_activate":
                raise ValueError(f"Only ready_to_activate records can activate (status: {record.status})")
            blocked = [name for name, resolved in record.prerequisites.items() if resolved is False]
            if blocked:
                raise ValueError(f"Unresolved prerequisites: {sorted(blocked)}")
            store.bind_candidate(args.record_id, args.candidate or "")
            record = store.advance(args.record_id, "active")
            print(json.dumps(record.__dict__, indent=2))
        elif action == "health":
            record = store.mark_health(args.record_id, args.health or "unknown")
            if args.health == "degraded":
                EventBus(Path.cwd()).emit(Event("capability.degraded",
                                                {"record_id": args.record_id, "health": args.health}))
            print(json.dumps(record.__dict__, indent=2))
        elif action == "degrade":
            record = store.advance(args.record_id, "degraded")
            EventBus(Path.cwd()).emit(Event("capability.degraded",
                                            {"record_id": args.record_id, "reason": args.reason or ""}))
            print(json.dumps(record.__dict__, indent=2))
        elif action == "recover":
            record = store.advance(args.record_id, "active")
            print(json.dumps(record.__dict__, indent=2))
        elif action == "verify-live":
            record = store.mark_operationally_verified(args.record_id, args.evidence or "")
            print(json.dumps(record.__dict__, indent=2))
        elif action == "retire":
            record = store.get(args.record_id)
            if record is None:
                raise ValueError("Acquisition record not found")
            if record.status == "active":
                # active -> disabled -> retired is the lifecycle path.
                store.advance(args.record_id, "disabled")
            record = store.retire(args.record_id, args.reason or "")
            print(json.dumps(record.__dict__, indent=2))
        elif action == "feasibility":
            if getattr(args, "set_json", ""):
                payload = json.loads(Path(args.set_json).read_text(encoding="utf-8"))
                record = store.record_feasibility(args.record_id, payload)
                print(json.dumps(record.__dict__, indent=2))
            else:
                record = store.get(args.record_id)
                if record is None:
                    raise ValueError("Acquisition record not found")
                feasibility = record.feasibility or {}
                if not feasibility:
                    print(f"No feasibility record for {args.record_id}")
                else:
                    print(f"Feasibility for {record.record_id} (status: {record.status})")
                    print(f"need: {feasibility.get('need', '') or '(empty)'}")
                    for key in ("approaches", "constraints", "prerequisites", "scopes", "unknowns"):
                        items = feasibility.get(key) or []
                        print(f"{key}:")
                        for item in items:
                            print(f"  - {item}")
                    if "expected_data" in feasibility:
                        print(f"expected_data: {feasibility['expected_data'] or '(empty)'}")
                    print(f"cost: {feasibility.get('cost') or 'unknown'}")
                    print(f"maintenance: {feasibility.get('maintenance') or 'unknown'}")
                    if "next_step" in feasibility:
                        print(f"next_step: {feasibility['next_step'] or '(empty)'}")
                    sources = feasibility.get("sources") or []
                    print(f"sources ({len(sources)}):")
                    for source in sources:
                        location = source.get("url") or source.get("path") or ""
                        checked = time.strftime("%Y-%m-%d", time.gmtime(source.get("checked_at", 0)))
                        print(f"  - [{source.get('kind', '?')}] {location} (checked {checked})")
        else:
            raise ValueError(f"Unknown capability action: {action}")
    except (AcquisitionError, ValueError, OSError) as exc:
        print(f"Capability operation refused: {exc}")


def _cmd_lora(args: argparse.Namespace) -> None:
    """Show the verified training-pair corpus (captures only; never trains)."""
    from .training_capture import TrainingPairStore
    store = TrainingPairStore(Path.cwd())
    action = getattr(args, "lora_action", None)
    if action == "list":
        for raw in store.list(limit=getattr(args, "limit", 10)):
            print(f"{raw.get('job_id','?')} | {raw.get('model','?')} | "
                  f"{raw.get('goal','')[:80]}")
        if store.count() == 0:
            print("No verified training pairs captured yet.")
    else:  # status or bare
        print(f"Verified training-pair corpus: {store.count()} pairs")
        print("  corpus: .ghost/lora/training_pairs.jsonl")
        print("  These are captured from passing jobs; nothing trained yet.")
        print("  Use: ghost lora list")


def _cmd_accomplish(args: argparse.Namespace) -> None:
    """Execute an outcome as a durable research → sandbox → verification job."""
    project_root = Path.cwd().resolve()
    goal = " ".join(args.goal).strip()
    if not goal:
        print("Outcome cannot be empty.")
        return
    try:
        required_files = Task.validate_relative_files(getattr(args, "require_file", []))
        Task("preview", "preview", goal, {"iteration_limit": args.iterations}, {})
        command_seconds = getattr(args, "command_budget_seconds", 300)
        command_calls = getattr(args, "command_budget_calls", 32)
        command_timeout = getattr(args, "command_timeout_seconds", 30)
        if type(command_timeout) not in (int, float) or not 5 <= command_timeout <= 86400:
            print("Outcome contract refused: command_timeout_seconds must be between 5 and 86400.")
            return
        CommandBudget(project_root / ".ghost" / "budget-validation", command_seconds, command_calls)
    except ValueError as exc:
        print(f"Outcome contract refused: {exc}")
        return
    if args.dry_run:
        goal_record = GoalStore(project_root).create(goal, kind="outcome")
        intent = IntentStore(project_root).capture(goal, goal, goal_record.goal_id)
        IntentStore(project_root).set_plan(intent.intent_id, ["preview permissions", "review side effects"])
        kernel = CapabilityKernel()
        kernel.register(CapabilityManifest(
            "filesystem.write", ["path", "content"], ["artifact"],
            filesystem_scopes=["sandbox/**"], side_effects=["writes sandbox artifact"],
        ))
        proposal = ActionProposal("filesystem.write", {"path": "sandbox/"},
                                  target=str(project_root / ".ghost" / "dry-run-sandbox" / "proposal.txt"),
                                  reason="A coding outcome may create sandbox artifacts.")
        authorization = kernel.authorize(proposal, project_root / ".ghost" / "dry-run-sandbox")
        approval = None
        if authorization.approval_required:
            approval = ApprovalStore(project_root).request(
                proposal.capability, proposal.reason, {"target": proposal.target},
                "medium", "No live change was made; discard the preview transaction."
            )
        journal = TransactionJournal(project_root)
        transaction = journal.prepare(goal_record.goal_id, proposal)
        journal.transition(transaction, "previewed", {
            "goal": goal,
            "models": {"research": _resolve_research_model(args.research_model),
                       "coding": _resolve_model(args.model)},
            "research": not args.skip_research,
            "required_files": required_files,
            "workspace_mode": getattr(args, "workspace", "project"),
            "command_budget": {"seconds": command_seconds, "calls": command_calls},
            "permissions": [authorization.__dict__],
            "approval_id": approval.approval_id if approval else None,
            "side_effects": ["Creates only .ghost job and transaction artifacts."],
            "rollback": "Mark transaction rolled_back; no live project change is made.",
        })
        print(json.dumps({"goal_id": goal_record.goal_id, "transaction": transaction.transaction_id,
                          "state": transaction.state, "preview": transaction.preview}, indent=2))
        return
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    coding_model = _resolve_model(args.model)
    research_model = _resolve_research_model(args.research_model)
    if not coding_model:
        coding_model = _pick_model_interactive(base_url)

    store = OutcomeStore(project_root)
    reputation = ModelReputationStore(project_root)
    routing = ModelOrchestrator(reputation)
    coding_route = routing.route(goal, _configured_model_candidates("GHOST_CODING_MODELS", coding_model), coding_model)
    research_route = routing.router.choose(
        "research", _configured_model_candidates("GHOST_RESEARCH_MODELS", research_model), research_model
    )
    coding_model = coding_route["primary"]
    research_model = research_route
    goal_record = GoalStore(project_root).create(goal, kind="outcome")
    intent = IntentStore(project_root).capture(goal, goal, goal_record.goal_id)
    IntentStore(project_root).set_plan(intent.intent_id, ["research", "sandbox implementation", "verification", "review promotion"])
    events = EventBus(project_root)
    job = store.create(goal)
    job.acceptance_contract["required_files"] = list(required_files)
    job.acceptance_contract["workspace_mode"] = getattr(args, "workspace", "project")
    from .models.chat import route_identity
    job.acceptance_contract["inference_route"] = route_identity()
    job.acceptance_contract.update(command_budget_seconds=command_seconds, command_budget_calls=command_calls, command_timeout_seconds=command_timeout)
    job.coding_model = coding_model
    job.research_model = research_model
    job.goal_id = goal_record.goal_id
    job.iterations = args.iterations
    job.skip_research = args.skip_research
    store.save(job)
    scheduler = Scheduler(project_root, events)
    scheduler.enqueue(ScheduledJob(
        job_id=job.job_id, goal_id=goal_record.goal_id, priority=goal_record.priority,
        budget={"iterations": args.iterations},
    ))
    if args.queue:
        events.emit(Event("outcome.queued", {"job_id": job.job_id, "goal_id": goal_record.goal_id}))
        print(f"Queued outcome job: {job.job_id}. Run `ghost worker --watch` to execute it.")
        return
    events.emit(Event("outcome.created", {"job_id": job.job_id, "goal_id": goal_record.goal_id}))
    events.emit(Event("model.routed", {"job_id": job.job_id, "research": research_model,
                                        "roles": coding_route, "complexity": coding_route["complexity"]}))
    print(f"Starting outcome job: {job.job_id}")

    result = OutcomeWorker(project_root).run_next(
        lambda scheduled: _execute_queued_outcome(project_root, scheduled), job_id=job.job_id
    )
    if result is None:
        print(f"Outcome remains queued: {job.job_id}. A worker is busy, recovery is pending, or admission was denied.")
        return
    job = store.load(job.job_id)
    print(f"Outcome {job.status}: {job.result or 'failed'}")
    print(f"Review artifacts: {store.path_for(job)}")


def _cmd_doctor() -> None:
    """Report local runtime health without changing state."""
    doctor = Doctor(Path.cwd())
    checks = doctor.check()
    tampered = ExtensionManager(Path.cwd()).verify_integrity()
    checks["extensions"] = f"{len(tampered)} tampered" if tampered else "ok"
    service_names = {"ollama", "docker"}
    storage = {k: v for k, v in checks.items() if k not in service_names}
    services = {k: v for k, v in checks.items() if k in service_names}
    print("STORAGE:")
    for name, status in storage.items():
        print(f"  {name}: {status}")
    print("SERVICES:")
    for name, status in services.items():
        print(f"  {name}: {status}")
        if name == "ollama" and status == "ok":
            models = doctor.available_models()
            if models:
                print(f"    models: {', '.join(models)}")


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Check, apply, or stamp .ghost data schema versions."""
    from . import migrations

    root = Path.cwd()
    if args.check:
        problems = migrations.check(root)
        if problems:
            for problem in problems:
                print(problem)
        else:
            print("schema: ok")
        return
    if args.apply:
        reports = migrations.apply(root, dry_run=args.dry_run)
        if reports:
            for report in reports:
                print(report)
        else:
            print("no pending migrations")
        return
    stamped = migrations.stamp(root)
    print(f"schema.json records: {', '.join(stamped) if stamped else 'none'}")


def _cmd_events(args: argparse.Namespace) -> None:
    """Tail the event log through a named cursor, or show the failed queue."""
    from .event_cursor import EventCursor

    root = Path.cwd()
    if args.action == "failed":
        failed = root / ".ghost" / "events" / "failed.jsonl"
        if failed.exists():
            print(failed.read_text(encoding="utf-8"), end="")
        return
    cursor = EventCursor(root, args.cursor)
    pairs = cursor.read_new(max_events=args.max)
    for end_offset, event in pairs:
        print(json.dumps(event.__dict__))
    if pairs and not args.no_commit:
        cursor.commit(pairs[-1][0])


def _cmd_emergency_stop(args: argparse.Namespace) -> None:
    """Engage, clear, or inspect the runtime emergency stop."""
    from .security import SecurityMonitor
    root = Path.cwd()
    stop = EmergencyStop(root)
    scheduler = Scheduler(root)
    monitor = SecurityMonitor(root)
    if args.action == "engage":
        stop.engage(args.reason)
        held = scheduler.hold_all(args.reason)
        print(f"Emergency stop engaged. {len(held)} queued job(s) paused by emergency stop.")
    elif args.action == "clear":
        released = scheduler.release_emergency_stop_holds()
        stop.clear()
        print(f"Emergency stop cleared. {len(released)} job(s) requeued.")
    else:
        if not stop.active():
            print("clear")
            return
        engaged = stop.engaged_at()
        print(json.dumps({"engaged": True,
                          "engaged_at": engaged,
                          "observed": [{"component": name, "latency_s": latency}
                                       for name, latency in _observed_components(root, monitor)],
                          }, indent=2, default=str))


def _observed_components(root: Path, monitor) -> list[tuple[str, object]]:
    """Report which components observed the stop and their recorded latencies."""
    latencies: dict[str, object] = {}
    for event in monitor.recent_events():
        if event.get("kind") == "emergency_stop.observed":
            detail = event.get("detail", {})
            component = detail.get("component")
            if component is not None:
                latencies[component] = detail.get("latency_s")
    return sorted(latencies.items())


def _cmd_extension(args: argparse.Namespace) -> None:
    """Create only reviewable extension scaffolds; enabling is a later approval step."""
    manager = ExtensionManager(Path.cwd())
    manager.verify_integrity()
    if args.action == "propose":
        manifest = manager.propose(args.name, args.goal)
    elif args.action == "scaffold":
        manifest = manager.scaffold(args.name, args.goal)
    elif args.action == "test":
        manifest = manager.mark_tested(args.name)
    elif args.action == "request-enable":
        approval_id = manager.request_enable(args.name)
        print(json.dumps({"extension": args.name, "approval_id": approval_id,
                          "next": f"ghost approval approve {approval_id}"}, indent=2))
        return
    elif args.action == "enable":
        if not args.approval_id:
            print("--approval-id is required to enable an extension")
            return
        manifest = manager.enable(args.name, args.approval_id)
    else:
        manifest = manager.load(args.name)
        if not manifest:
            print("Extension not found.")
            return
    print(json.dumps({"name": manifest.name, "status": manifest.status,
                      "path": str(manager.directory(manifest.name)),
                      "permissions": manifest.permissions}, indent=2))


def _cmd_approval(args: argparse.Namespace) -> None:
    """List or decide a previously previewed consequential action."""
    store = ApprovalStore(Path.cwd())
    if args.action == "list":
        print(json.dumps([request.__dict__ for request in store.pending()], indent=2))
        return
    if not args.approval_id:
        print("approval_id is required for approve/reject")
        return
    request = store.decide(args.approval_id, args.action == "approve", args.note)
    print(json.dumps(request.__dict__, indent=2))


def _cmd_policy(args: argparse.Namespace) -> None:
    """Show or set standing autonomy policy modes (owner authority)."""
    from .policy import PolicyError, PolicyStore
    store = PolicyStore(Path.cwd())
    try:
        if args.action == "show":
            print(json.dumps(store.load(), indent=2))
            return
        if not args.mode or not args.state:
            print("usage: ghost policy set <mode> on|off")
            return
        print(json.dumps(store.set(args.mode, args.state == "on"), indent=2))
    except PolicyError as exc:
        print(str(exc))


def _cmd_export(args: argparse.Namespace) -> None:
    """Write a versioned, secret-free export of personal state."""
    from .control_plane import GhostSDK
    from .utils.storage import atomic_write
    sdk = GhostSDK(Path.cwd())
    only = {name.strip() for name in args.only.split(",") if name.strip()}
    try:
        payload = sdk.export(only or None)
    except ValueError as exc:
        print(f"export refused: {exc}")
        return
    out = Path(args.out)
    if out.exists():
        print(f"export refused: {out} already exists")
        return
    atomic_write(out, json.dumps(payload, indent=2, default=str).encode("utf-8"))
    print(json.dumps({"written": str(out), "version": payload["version"],
                      "sections": sorted(payload["sections"])}, indent=2))


def _cmd_promote(args: argparse.Namespace) -> None:
    """Expose the explicit preview -> approval -> commit -> undo promotion flow."""
    service = PromotionService(Path.cwd())
    try:
        if args.action == "list":
            print(json.dumps(service.promotions.list(), indent=2))
            return
        if not args.value:
            raise ValueError("A sandbox path, transaction ID, or promotion ID is required")
        if args.action == "request":
            if not args.goal_id:
                print("--goal-id is required to request a promotion")
                return
            transaction_id, approval_id, files = service.request(args.goal_id, Path(args.value))
            print(json.dumps({"transaction_id": transaction_id, "approval_id": approval_id,
                              "files": files, "next": "ghost approval approve <approval_id>"}, indent=2))
        elif args.action == "commit":
            if not args.approval_id:
                print("--approval-id is required to commit a promotion")
                return
            promotion_id = service.commit(args.value, args.approval_id)
            print(json.dumps({"promotion_id": promotion_id, "status": "verified",
                              "verification": "approved file hashes; not functional tests",
                              "undo": f"ghost promote rollback {promotion_id}"}, indent=2))
        else:
            service.rollback(args.value)
            print(f"Rolled back {args.value}.")
    except (OSError, ValueError, PermissionError) as exc:
        print(f"Promotion refused: {exc}")


def _cmd_control_plane(args: argparse.Namespace | None = None) -> None:
    """Expose a structured local snapshot for future UI/API transports."""
    sdk = GhostSDK(Path.cwd())
    views = {
        "snapshot": lambda: json.loads(sdk.snapshot_json()),
        "goals": sdk.goal_tree,
        "opportunities": sdk.opportunity_pipeline,
        "resources": sdk.resource_usage,
        "integrations": sdk.integration_health,
        "notifications": sdk.notification_preferences,
    }
    result = views[getattr(args, "view", "snapshot")]()
    print(json.dumps(result, indent=2, default=str))


def _cmd_api(args: argparse.Namespace) -> None:
    """Run the deliberately loopback-only local API transport."""
    try:
        print(f"Ghost control-plane API listening on http://{args.host}:{args.port}/v1/snapshot")
        serve_control_plane(Path.cwd(), args.host, args.port)
    except (OSError, ValueError) as exc:
        print(f"API refused: {exc}")


def _cmd_integration(args: argparse.Namespace) -> None:
    """Plan or propose an integration without external account side effects."""
    planner = IntegrationPlanner(Path.cwd())
    try:
        blueprint = planner.blueprint(args.name) if args.action == "plan" else planner.propose(args.name)
        print(json.dumps(planner.as_dict(blueprint), indent=2))
        if args.name.strip().lower().replace("_", "-") == "telegram":
            print("\nLive qualification runbook: docs/engineering/telegram-live-qualification.md")
            print("Credential-aware status: ghost openjarvis telegram status")
            print("Authorized single-ping test: ghost openjarvis telegram live-check --chat-id <id>")
    except ValueError as exc:
        print(f"Integration unavailable: {exc}")


def _close_proactive_feedback(project_root: Path, job) -> None:
    """Close the proactive opportunity linked to a finished outcome job.

    Wired into _execute_queued_outcome so the real worker closes the loop. Uses
    the job's real terminal result; idempotent; never masks the job's primary
    result if feedback bookkeeping fails.
    """
    from .proactive import close_opportunity_feedback
    try:
        close_opportunity_feedback(project_root, job)
    except Exception as exc:  # feedback bookkeeping must not break the worker result
        from .runtime import EventBus, Event
        EventBus(project_root).emit(Event("proactive.feedback_error",
                                          {"job_id": job.job_id, "error": str(exc)},
                                          source="proactive"))
    # Capability-improver jobs close their gap on the same terminal result
    # (resolved on tests_passed, rolled back to queued on retryable failure).
    from .autonomy_improver import close_gap_feedback
    try:
        close_gap_feedback(project_root, job)
    except Exception as exc:
        from .runtime import EventBus, Event
        EventBus(project_root).emit(Event("capability.feedback_error",
                                          {"job_id": job.job_id, "error": str(exc)},
                                          source="capability_improver"))


def _execute_queued_outcome(project_root: Path, scheduled: ScheduledJob) -> WorkerResult:
    """Resume a durable queued outcome inside the worker-owned sandbox."""
    store = OutcomeStore(project_root)
    job = store.load(scheduled.job_id)
    if not job:
        # No outcome record -> no opportunity link exists, so there is nothing
        # to close. Return the terminal result; the missing artifact is already
        # durable in the scheduler. Nothing to reconcile (no job to inspect).
        return WorkerResult(scheduled.job_id, "failed", "outcome artifact is missing")
    if EmergencyStop(project_root).active():
        # Terminal early return: the job exists and carries an opportunity link.
        # Mark it terminal with a REAL result so feedback_outcome_for_job yields
        # a genuine verdict, then close the linked proactive opportunity so the
        # loop is durably closed instead of left queued/running forever.
        job.status = "failed"
        job.result = "cancelled"
        store.save(job)
        _close_proactive_feedback(project_root, job)
        return WorkerResult(job.job_id, "cancelled", "emergency stop is engaged")
    from .models.chat import route_identity
    expected_route = job.acceptance_contract.get("inference_route", {"backend": "legacy"})
    if expected_route != route_identity():
        # Terminal early return (route mismatch). Mark the job terminal with a
        # real result and close the linked opportunity; no provider contacted.
        job.status = "failed"
        job.result = "route_mismatch"
        store.save(job)
        _close_proactive_feedback(project_root, job)
        return WorkerResult(job.job_id, "failed", "Inference route differs from the queued job. Start the matching worker/environment; no provider was contacted.")
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))
    research = "Research intentionally skipped for this job."
    try:
        if not job.skip_research:
            job.status = "researching"
            store.save(job)
            research = _research_directly(job.goal, job.research_model, base_url, timeout,
                                          graph_root=project_root / ".ghost" / "research")
            store.write_research(job, research)
        if EmergencyStop(project_root).active():
            # Terminal early return after research. Mark the job terminal with a
            # real result and close the linked opportunity so it isn't left
            # queued/running forever.
            job.status = "failed"
            job.result = "cancelled"
            store.save(job)
            _close_proactive_feedback(project_root, job)
            return WorkerResult(job.job_id, "cancelled", "emergency stop engaged after research")
        payload = store.make_task_payload(job, research, job.iterations)
        payload["context"]["runtime_context"] = ContextAssembler(project_root).assemble(job.goal_id)
        task_path = store.path_for(job) / "task.json"
        task_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        task = Task(payload["id"], payload["title"], payload["goal"], payload["constraints"], payload["context"])
        job.status = "implementing"
        store.save(job)
        run_dir = store.path_for(job) / "run"
        orchestrator = Orchestrator(project_root, model=job.coding_model, base_url=base_url, timeout=timeout)
        if run_dir.exists():
            orchestrator.resume_task_with_roots(task, run_dir)
        else:
            orchestrator.run_task_with_roots(task, project_root, run_dir)
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        job.result = str(state.get("last_result", "unknown"))
        job.sandbox_path = str(run_dir / "sandbox")
        if job.result == "yielded_to_owner":
            # TO1: the owner is back; pause (resumable with `ghost jobs resume`)
            # instead of failing. Sandbox and checkpoint are preserved. Marking
            # the scheduler record paused lets OutcomeWorker observe the
            # control-state change instead of recording a terminal failure.
            job.status = "paused"
            job.errors.append("paused: yielded_to_owner")
            store.save(job)
            Scheduler(project_root).mark(scheduled.job_id, "paused", expected={"running"},
                                         paused_by="yield_to_owner")
            return WorkerResult(job.job_id, "paused", "yielded_to_owner")
        job.status = "completed" if job.result == "tests_passed" else "needs_review"
        store.save(job)
        if job.status == "completed":
            # Capture a verified (goal -> context -> passing code) training pair
            # for a future LoRA fine-tune. Durable, idempotent, never trains here.
            try:
                from .training_capture import TrainingPairStore, build_pair
                from .knowledge_base import OwnerKnowledgeBase
                kb = OwnerKnowledgeBase(project_root).retrieve_context(job.goal)
                pair = build_pair(job.job_id, job.goal, job.coding_model, job.result,
                                  context=kb, sandbox_path=Path(job.sandbox_path))
                TrainingPairStore(project_root).capture(pair)
            except Exception:
                # Capturing pairs is best-effort and must never fail the job.
                pass
        ModelReputationStore(project_root).record(job.coding_model, "coding", job.status == "completed",
                                                   verification_failure=job.status != "completed")
        GoalStore(project_root).set_status(job.goal_id, "completed" if job.status == "completed" else "blocked")
        if job.status != "completed":
            gap = CapabilityRegistry(project_root).record_gap(job.goal, job.result or job.status,
                                                               "Queued sandbox run did not reach verified completion.")
            ExtensionManager(project_root).propose(f"workaround-{gap['id']}", gap["followup_goal"])
        EventBus(project_root).emit(Event("outcome.completed", {"job_id": job.job_id,
                                       "result": job.result, "status": job.status}))
        _close_proactive_feedback(project_root, job)
        return WorkerResult(job.job_id, "completed" if job.status == "completed" else "failed", job.result)
    except Exception as exc:
        job.status = "failed"
        job.errors.append(f"worker: {exc}")
        store.save(job)
        if job.goal_id:
            GoalStore(project_root).set_status(job.goal_id, "blocked")
        gap = CapabilityRegistry(project_root).record_gap(job.goal, "worker_failed", str(exc))
        ExtensionManager(project_root).propose(f"workaround-{gap['id']}", gap["followup_goal"])
        _close_proactive_feedback(project_root, job)
        return WorkerResult(job.job_id, "failed", str(exc))


def _cmd_worker(args: argparse.Namespace) -> None:
    """Run one or continuously poll the durable queue; no live promotion occurs."""
    worker = OutcomeWorker(Path.cwd())
    while True:
        if args.watch:
            try:
                LocalCheckIns(Path.cwd()).tick()
            except (ValueError, OSError) as exc:
                print(f"Local check-in refused: {exc}")
        result = worker.run_next(lambda scheduled: _execute_queued_outcome(Path.cwd(), scheduled))
        if result:
            print(f"Worker {result.status}: {result.job_id} ({result.detail})")
        elif not args.watch:
            print("No eligible queued jobs.")
            return
        if not args.watch:
            return
        time.sleep(max(0.25, args.poll_seconds))


def _cmd_notifications(args: argparse.Namespace) -> None:
    sdk = GhostSDK(Path.cwd())
    attention = AttentionManager(Path.cwd())
    try:
        if args.subaction == "ack":
            note = attention.acknowledge(args.notification_id)
            print(json.dumps(note.__dict__, indent=2))
            return
        overview = sdk.notifications_overview()
        if not args.all:
            print(json.dumps(overview["deliverable"], indent=2))
            return
        print(json.dumps(overview["deliverable"] + overview["held"], indent=2))
    except (ValueError, OSError) as exc:
        print(f"Notifications refused: {exc}")


def _cmd_focus(args: argparse.Namespace) -> None:
    from .focus import FocusStore
    try:
        store = FocusStore(Path.cwd())
        if args.subaction == "start":
            interventions = ["hold_notifications"] if args.hold_notifications else []
            session = store.start(args.purpose, args.minutes, interventions)
        elif args.subaction == "stop":
            session = store.stop()
        else:
            session = store.active()
    except (ValueError, OSError) as exc:
        print(f"Focus refused: {exc}")
        return
    if session is None:
        print("No active focus session")
        return
    print(json.dumps(asdict(session), indent=2))


def _cmd_why(args: argparse.Namespace) -> None:
    from .control_plane import GhostSDK
    try:
        detail = GhostSDK(Path.cwd()).explain(args.item_id)
    except (ValueError, OSError) as exc:
        print(f"Explain refused: {exc}")
        return
    if detail is None:
        print(f"No job or opportunity with id {args.item_id}")
        return
    if not args.json:
        job = detail.get("job")
        opportunity = detail.get("opportunity")
        goal = detail.get("goal")
        if job:
            print(f"Job {job['job_id']}: {job['goal']}")
            print(f"  status: {job['status']}  result: {job['result'] or 'none'}")
            if goal:
                print(f"  goal: {goal['goal_id']} ({goal['status']}) {goal['title']}")
        elif opportunity:
            print(f"Opportunity {opportunity['opportunity_id']}: {opportunity['title']}")
            print(f"  status: {opportunity['status']}  confidence: {opportunity['confidence']}")
            print(f"  rationale: {opportunity['rationale']}")
        if detail.get("intent"):
            print(f"  intent: {detail['intent']['intent_id']} ({detail['intent']['status']})")
        if detail.get("events"):
            print(f"  recent events mentioning this id: {len(detail['events'])}")
    print(json.dumps(detail, indent=2))


def _cmd_check_in(args: argparse.Namespace) -> None:
    manager = LocalCheckIns(Path.cwd())
    try:
        if args.action == "enable":
            result = manager.configure(args.interval_seconds, args.message, tuple(args.quiet_hours))
        elif args.action == "disable":
            result = manager.disable()
        elif args.action == "tick":
            result = manager.tick()
        else:
            result = manager.status()
        print(json.dumps(result.__dict__ if result else None, indent=2))
    except (ValueError, OSError) as exc:
        print(f"Local check-in refused: {exc}")


def _cmd_jobs(args: argparse.Namespace) -> None:
    scheduler = Scheduler(Path.cwd())
    try:
        if args.action == "list":
            jobs = scheduler.list()
            now = time.time()
            payload = []
            for job in jobs:
                record = job.__dict__.copy()
                record["next_due"] = job.due_at if (job.recurrence and job.status in {"queued", "paused"}) else None
                payload.append(record)
            print(json.dumps(payload, indent=2))
            return
        if args.action == "add":
            recurrence = None
            due_at = None
            if args.recurrence:
                kind, _, raw_seconds = args.recurrence.partition(":")
                if kind != "interval" or not raw_seconds.isdigit():
                    raise ValueError("--recurrence must be interval:SECONDS with SECONDS a positive integer")
                seconds = int(raw_seconds)
                if seconds <= 0:
                    raise ValueError("--recurrence interval must be > 0 seconds")
                recurrence = {"kind": "interval", "interval_seconds": seconds}
                due_at = time.time()  # due immediately; requeues after each settle
            goal_id = args.goal_id or "scheduler-own"
            job = ScheduledJob(job_id=f"rec-{int(time.time())}-{os.getpid()}", goal_id=goal_id,
                               priority=args.priority, recurrence=recurrence, due_at=due_at,
                               budget={"iterations": args.iterations})
            scheduler.enqueue(job)
            print(json.dumps(job.__dict__, indent=2))
            return
        if args.action == "remove":
            job = scheduler.remove(args.job_id)
            print(json.dumps(job.__dict__, indent=2))
            return
        if not args.job_id:
            raise ValueError("A job ID is required")
        from .control_plane import apply_job_action
        if args.action in ("pause", "resume", "cancel"):
            job_record = apply_job_action(Path.cwd(), args.job_id, args.action)
            print(json.dumps(job_record, indent=2))
            return
        if args.action == "retry":
            job = scheduler.retry(args.job_id)
            goals = GoalStore(Path.cwd())
            goal = goals.get(job.goal_id)
            if goal and goal.status == "blocked":
                goals.set_status(job.goal_id, "active")
        else:
            job = scheduler.mark(args.job_id, "cancelled", expected={"queued", "paused", "failed"})
        print(json.dumps(job.__dict__, indent=2))
    except (OSError, ValueError) as exc:
        print(f"Job operation refused: {exc}. Running processes cannot yet be cancelled by this command.")
    except LookupError as exc:
        print(f"Job operation refused: {exc}")


def _cmd_self_repair(args: argparse.Namespace) -> None:
    """Expose reviewable self-repair state; it never applies a patch directly."""
    manager = SelfRepairManager(Path.cwd())
    try:
        if args.action == "propose":
            if not args.sandbox:
                print("--sandbox is required for a self-repair proposal")
                return
            proposal = manager.propose(args.value, Path(args.sandbox))
            print(json.dumps(proposal.__dict__, indent=2))
        elif args.action == "verify":
            # Verification is tied to a REAL evaluator run against the exact
            # candidate bytes. A user-supplied pass/fail flag is not accepted as
            # evidence (S08): a manual note cannot impersonate independent checks.
            def _evaluate(candidate: Path) -> dict:
                return _run_isolated_evaluator(candidate, args.command, args.isolation)

            proposal = manager.verify_from_evaluator(args.value, _evaluate)
            print(json.dumps(proposal.__dict__, indent=2))
        else:
            if not args.goal_id:
                print("--goal-id is required for request-promotion")
                return
            transaction, approval, files = manager.request_promotion(args.value, args.goal_id)
            print(json.dumps({"transaction_id": transaction, "approval_id": approval, "files": files}, indent=2))
    except (OSError, ValueError, PermissionError) as exc:
        print(f"Self-repair refused: {exc}")


def _run_isolated_evaluator(candidate: Path, command: str, isolation: str) -> dict:
    """Run the evaluator in container isolation when chosen/available (R07).

    isolation="container" demands the container runner and refuses without it;
    "auto" uses it when available and falls back to the bounded host evaluator;
    "host" always uses the bounded host evaluator. The returned dict records
    which runner actually ran via "runner" (and "isolation_unavailable" on the
    auto fallback) so stored evidence is honest about how it was produced.
    """
    from ghost_agent.openjarvis_bridge import isolated_execution

    if isolation == "host":
        result = _run_bounded_evaluator(candidate, command)
        result["runner"] = "host"
        return result
    timeout = float(os.environ.get("GHOST_EVALUATOR_TIMEOUT", "60"))
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []
    if not parts:
        return {"returncode": 1, "output": "empty evaluator command", "runner": "host"}
    outcome = isolated_execution.run_in_isolation(candidate, parts, timeout=timeout)
    if outcome.get("available"):
        return {
            "returncode": int(outcome.get("returncode", 1)),
            "output": str(outcome.get("output", ""))[-4000:],
            "runner": "container",
        }
    error_type = str(outcome.get("error_type", "unknown"))
    error = str(outcome.get("error", ""))
    if isolation == "container":
        raise ValueError(
            f"container isolation unavailable ({error_type}: {error})")
    result = _run_bounded_evaluator(candidate, command)
    result["runner"] = "host"
    result["isolation_unavailable"] = error_type
    return result


def _run_bounded_evaluator(candidate: Path, command: str) -> dict:
    """Run an evaluator command in the candidate, bounded, capturing output.

    NOTE: This is an OPERATOR-triggered LOCAL evaluator. It is NOT
    containerized and NOT host-isolated: the candidate runs as an ordinary
    subprocess on the operator's machine. It only bounds the run with a timeout
    and a sanitized environment so host credentials are not leaked and a
    hostile/long-running candidate cannot run unattended. Use a real container
    or sandbox if stronger isolation is required.

    The working directory is the candidate and the environment is BUILT from a
    minimal allowlist (PATH plus a few portable variables) rather than the full
    inherited os.environ, so host secrets/tokens in the caller's environment are
    not passed into the candidate process. Returns {returncode, output}.
    """
    import shlex
    import subprocess

    if not candidate.is_dir():
        return {"returncode": 1, "output": "candidate directory is missing"}
    deadline_s = float(os.environ.get("GHOST_EVALUATOR_TIMEOUT", "60"))
    parts = shlex.split(command)
    if not parts:
        return {"returncode": 1, "output": "empty evaluator command"}
    # Minimal sanitized environment: PATH so the command can find its binary,
    # plus a small portable allowlist. Does NOT inherit the full os.environ, so
    # host credentials / secrets are not leaked into the candidate process.
    env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    try:
        proc = subprocess.run(
            parts, cwd=str(candidate), capture_output=True, text=True,
            timeout=deadline_s, env=env,
        )
    except FileNotFoundError:
        return {"returncode": 1, "output": f"evaluator not found: {parts[0]}"}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 1, "output": f"evaluator timed out after {deadline_s}s"}
    output = (proc.stdout or "") + (proc.stderr or "")
    return {"returncode": int(proc.returncode), "output": output[-4000:]}


def _daily_inference_budget(root: Path, max_calls: int) -> object:
    """TD5 (S06): build the durable daily inference budget for unattended loops."""
    from datetime import date
    from .openjarvis_bridge.spend import DurableInferenceBudget
    return DurableInferenceBudget(
        root,
        max_calls=max_calls,
        path=root / ".ghost" / "inference" / f"{date.today():%Y-%m-%d}.json")


def _cmd_overnight(args: argparse.Namespace) -> None:
    """Run the unattended overnight loop (OJ11/S06) over existing subsystems."""
    from .overnight import DEFAULT_DIGEST_WINDOW, DEFAULT_QUIET_HOURS, OvernightRunner
    root = Path.cwd()
    quiet_hours = getattr(args, "quiet_hours", None)
    digest_window = getattr(args, "digest_window", None)
    if quiet_hours is not None and not all(0 <= h <= 23 for h in quiet_hours):
        print("hours must be 0-23")
        return
    if digest_window is not None and not all(0 <= h <= 23 for h in digest_window):
        print("hours must be 0-23")
        return
    quiet_hours = tuple(quiet_hours) if quiet_hours else DEFAULT_QUIET_HOURS
    digest_window = tuple(digest_window) if digest_window else DEFAULT_DIGEST_WINDOW
    # TD5 (S06): durable daily cap on proactive inference calls.
    max_calls = max(1, min(10000, int(getattr(args, "max_inference_calls", 48))))
    inference_budget = _daily_inference_budget(root, max_calls)
    if getattr(args, "overnight_action", None) == "until-idle-ends":
        runner = OvernightRunner(root, _resolve_model(getattr(args, "model", None)),
                                 idle_after=args.idle_after_seconds,
                                 quiet_hours=quiet_hours, digest_window=digest_window)
        result = runner.run_until_idle()
        print(json.dumps(result.__dict__, indent=2, ensure_ascii=False, default=str))
        return
    runner = OvernightRunner(root, _resolve_model(getattr(args, "model", None)),
                             interval=getattr(args, "interval", 300.0),
                             idle_after=getattr(args, "idle_after_seconds", 900.0),
                             quiet_hours=quiet_hours, digest_window=digest_window,
                             inference_budget=inference_budget)
    cycles = runner.run(max_cycles=getattr(args, "max_cycles", None))
    print(json.dumps({"cycles": cycles, "log": str(runner.log_path)}, indent=2))


def _cmd_trace_learning(args: argparse.Namespace) -> None:
    """Mine real outcome traces for recurring failure signatures (OJ15/S08)."""
    from .trace_learning import run_trace_learning
    root = Path.cwd()
    if getattr(args, "trace_action", "") == "report":
        report_path = root / ".ghost" / "trace_learning" / "last_report.json"
        if not report_path.exists():
            print("No trace-learning report yet. Run: ghost trace-learning run")
            return
        print(report_path.read_text(encoding="utf-8"))
        return
    min_count = max(1, int(getattr(args, "min_count", 2)))
    summary = run_trace_learning(root, min_count=min_count)
    analysis = summary["analysis"]
    print(f"Scanned {analysis['scanned']} outcomes, {analysis['failed']} failed.")
    print(f"Top failure signatures (min count {min_count}):")
    for sig in analysis["top_signatures"][:10]:
        print(f"  [{sig['count']}x] {sig['error_type']} / {sig['subsystem']}: "
              f"{sig['normalized_error'][:90]}")
    if summary["actions"]:
        print("Proposed actions (never auto-applied):")
        for action in summary["actions"]:
            print(f"  - {action['action']}: {action['signature']['error_type']} "
                  f"({action['signature']['count']}x)")
    else:
        print("No recurring signatures met the threshold.")


def _cmd_digest(args: argparse.Namespace) -> None:
    """Print the most recent overnight digest file, if any."""
    digest_dir = Path.cwd() / ".ghost" / "digest"
    if not digest_dir.is_dir():
        print("No digests yet.")
        return
    digests = sorted(digest_dir.glob("*.md"))
    if not digests:
        print("No digests yet.")
        return
    print(digests[-1].read_text(encoding="utf-8"))


def _cmd_memory(args: argparse.Namespace) -> None:
    """Retract, purge, list, edit, export memories; manage retention."""
    from .context_manager import ValidatedMemoryStore
    from dataclasses import asdict
    store = ValidatedMemoryStore(Path.cwd())
    try:
        action = getattr(args, "memory_subcommand", getattr(args, "action", None))
        if action == "retract":
            record = store.retract(args.memory_id, args.reason)
            print(json.dumps({"action": "retracted", "memory_id": record.memory_id,
                              "validation": record.validation}, indent=2))
        elif action == "purge":
            record = store.purge(args.memory_id, args.reason)
            print(json.dumps({"action": "purged", "memory_id": record.memory_id}, indent=2))
        elif action == "list":
            records = store.active()
            if args.kind is not None:
                if args.kind not in store.KINDS:
                    raise ValueError(f"Unknown memory kind: {args.kind}")
                records = [record for record in records if record.kind == args.kind]
            if not args.show_sensitive:
                records = [record for record in records
                           if record.sensitivity not in ("health", "secret")]
            payload = [asdict(record) for record in records] if args.json else [
                {"memory_id": record.memory_id, "kind": record.kind,
                 "sensitivity": record.sensitivity, "subject": record.subject,
                 "validation": record.validation, "content": record.content}
                for record in records]
            print(json.dumps(payload, indent=2))
        elif action == "edit":
            record = store.edit(args.memory_id, content=args.content,
                                subject=args.subject, sensitivity=args.sensitivity,
                                confirm_verified=args.verified)
            print(json.dumps({"action": "edited", "memory_id": record.memory_id,
                              "revisions": len(record.revisions),
                              "last_verified_at": record.last_verified_at}, indent=2))
        elif action == "export":
            out = Path(args.out)
            if out.exists():
                raise ValueError(f"Refusing to overwrite existing file: {out}")
            records = [asdict(record) for record in store.active()
                       if record.sensitivity != "secret"]
            out.write_text(json.dumps(records, indent=2), encoding="utf-8")
            print(json.dumps({"exported": len(records), "path": str(out)}, indent=2))
        elif action == "retention":
            if args.retention_action == "set":
                store.set_retention(args.kind, args.days)
                print(json.dumps({"kind": args.kind, "days": args.days}, indent=2))
            elif args.retention_action == "apply":
                purged = store.apply_retention()
                print(json.dumps({"action": "retention_applied", "purged": purged}, indent=2))
            else:
                print("retention requires 'set' or 'apply'")
    except ValueError as exc:
        print(f"Memory operation refused: {exc}")


def _cmd_device(args: argparse.Namespace) -> None:
    """Manage declared devices; registration does not grant control permissions."""
    registry = DeviceRegistry(Path.cwd())
    try:
        if args.action == "ingest":
            # TL4: telemetry is data, never instructions. Rejected string
            # content is never echoed — counts and reason categories only.
            from .telemetry import TelemetryError, ingest_samples
            if not args.device_id or not args.file:
                print("ingest requires a device_id and --file")
                return
            device = registry.get(args.device_id)
            if device is None:
                print(f"Unknown device: {args.device_id}")
                return
            try:
                raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"Cannot read samples file: {type(exc).__name__}")
                return
            try:
                result = ingest_samples(raw, device=device, registry=registry)
            except TelemetryError as exc:
                print(f"Telemetry refused: {exc}")
                return
            print(json.dumps(result, indent=2))
            return
        if args.action == "list":
            print(json.dumps([device.__dict__ for device in registry.list()], indent=2))
            return
        if args.action == "register":
            if not args.device_id or not args.name or not args.capability:
                print("register requires device_id, --name, and at least one --capability")
                return
            device = registry.register(args.device_id, args.name, args.kind, args.capability)
            transport = getattr(args, "transport", None)
            firmware = getattr(args, "firmware", None)
            if transport or firmware:
                fields: dict = {}
                if transport:
                    fields["transport"] = transport
                if firmware is not None:
                    fields["firmware_version"] = firmware
                device = registry.update(device.device_id, **fields)
            print(json.dumps(device.__dict__, indent=2))
            return
        if not args.device_id:
            print(f"{args.action} requires a device_id")
            return
        if args.action == "pair":
            device = registry.update(args.device_id, pairing_state="paired")
            print(json.dumps(device.__dict__, indent=2))
        elif args.action == "revoke":
            device = registry.update(args.device_id, pairing_state="revoked")
            print(json.dumps(device.__dict__, indent=2))
        elif args.action == "status":
            device = registry.get(args.device_id)
            if device is None:
                print(f"Unknown device: {args.device_id}")
                return
            print(json.dumps({
                "device": device.__dict__,
                "freshness": registry.freshness(args.device_id),
                "declared_capabilities": device.capabilities,
                "verified_capabilities": device.verified_capabilities,
            }, indent=2))
        elif args.action == "simulate":
            from .device_control import (CommandRefused, DeviceCommandGate,
                                         PanTiltCommand, SimulatedPanTilt,
                                         limits_from_constraints)
            device = registry.get(args.device_id)
            if device is None:
                print(f"Unknown device: {args.device_id}")
                return
            if args.pan is None or args.tilt is None:
                print("simulate requires --pan and --tilt")
                return
            limits = limits_from_constraints(device.constraints)
            sim = SimulatedPanTilt(limits)
            sim.connected = True
            gate = DeviceCommandGate(Path.cwd(), registry, args.device_id, limits)
            try:
                result = gate.send(sim, PanTiltCommand(args.pan, args.tilt), args.duration)
                if args.disconnect:
                    sim.disconnect()
                print(json.dumps(result, indent=2))
            except CommandRefused as exc:
                print(f"Command refused: {exc}")
    except ValueError as exc:
        print(f"Device operation refused: {exc}")


def _cmd_huawei(args: argparse.Namespace) -> None:
    """TF1/TF4: synthetic-only Huawei route CLI; no network calls, privacy-guarded."""
    from .huawei_route import HuaweiRoute, HuaweiRouteError
    route = HuaweiRoute(Path.cwd())
    action = getattr(args, "huawei_action", None)
    try:
        if action == "route":
            record = route.route()
            print(json.dumps(record.__dict__ if record else None, indent=2))
        elif action == "status":
            record = route.route()
            print(json.dumps({
                "provider": "huawei",
                "state": record.state if record else "not_configured",
                "note": ("synthetic/manual data only until Huawei credentials are "
                         "configured"),
            }, indent=2))
        elif action == "register-route":
            record = route.register_route(args.name, args.source, time.time(),
                                          args.prerequisite, args.limitation)
            print(json.dumps(record.__dict__, indent=2))
        elif action == "set-state":
            print(json.dumps(route.set_state(args.state).__dict__, indent=2))
        elif action == "ingest":
            raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
            obs = route.ingest(raw)
            # Privacy guard: counts and metrics only, never observation values.
            print(json.dumps({"ingested": len(obs),
                              "metrics": sorted({o.metric for o in obs})}, indent=2))
        elif action == "correct":
            correction = route.correct_context(args.note, args.effect)
            print(json.dumps(correction.__dict__, indent=2))
        elif action == "trend":
            print(json.dumps(route.trend_summary(time.time(), args.window_seconds,
                                                 args.metric), indent=2))
        elif action == "retention":
            if args.days is not None:
                route.set_retention_days(args.days)
            print(json.dumps({"retention_days": route.retention_days()}, indent=2))
        elif action == "purge":
            removed = route.purge_all() if args.purge_all else route.purge_expired()
            print(json.dumps({"removed": removed}, indent=2))
        else:
            print("huawei requires a sub-command (route, status, register-route, "
                  "set-state, ingest, correct, trend, retention, purge)")
    except (HuaweiRouteError, ValueError) as exc:
        print(f"Huawei route refused: {exc}")


def _cmd_mcp(args: argparse.Namespace) -> None:
    """Scaffold a disabled MCP package; review/approval remains required."""
    try:
        path = MCPAddonManager(Path.cwd()).scaffold(args.name, args.goal, args.tool)
        print(json.dumps({"path": str(path), "status": "scaffolded_not_executable"}, indent=2))
    except ValueError as exc:
        print(f"MCP scaffold refused: {exc}")


def _cmd_parallel(args: argparse.Namespace) -> None:
    """Decompose a complex task and run sub-agents in parallel."""
    task_path = Path(args.task_path)
    if not task_path.exists():
        print(f"Error: {task_path} not found")
        sys.exit(1)

    from .parallel import TaskDecomposer, ParallelRunner

    task_data = json.loads(task_path.read_text(encoding="utf-8"))
    goal = task_data.get("goal", "")
    task_id = task_data.get("id", task_path.stem)

    print(f"{_BOLD}Decomposing task:{_RESET} {goal}\n")

    decomposition = TaskDecomposer.decompose(goal)

    print(f"{_DIM}Found {len(decomposition.sub_tasks)} sub-tasks ({decomposition.merge_strategy} execution):{_RESET}\n")
    for st in decomposition.sub_tasks:
        deps = f" (depends on: {', '.join(st.depends_on)})" if st.depends_on else ""
        print(f"  {_BRIGHT_CYAN}{st.id}{_RESET}: {st.goal}{deps}")
    print()

    if args.dry_run:
        print(f"{_DIM}Dry run -- not executing.{_RESET}")
        return

    model = _resolve_model(args.model)
    if not model:
        base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
        model = _pick_model_interactive(base_url)
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout = int(os.getenv("GHOST_OLLAMA_TIMEOUT", "180"))
    project_root = Path.cwd()

    def make_orchestrator(workspace: Path) -> Orchestrator:
        return Orchestrator(
            repo_root=workspace,
            model=model,
            base_url=base_url,
            timeout=timeout,
            verbose=args.verbose,
        )

    runner = ParallelRunner(orchestrator_factory=make_orchestrator, max_workers=3)
    completed = runner.run_decomposition(decomposition, project_root)

    print(f"\n{runner.format_results(completed)}")


    # ============================================================================
    # AI-OS Daemon (OJ-Daemon)
    # Composes: proactive tick → worker → trace-learning → digest → capability
    # improver. One Ghost-owned process; upstream scheduler/authority untouched.
    # ============================================================================

def _cmd_daemon(args: argparse.Namespace) -> None:
    """Run the always-on daemon: one OvernightRunner cycle plus away-only learning hooks."""
    import json as _json

    root = Path.cwd()

    if getattr(args, "daemon_action", None) == "status":
        status_path = root / ".ghost" / "daemon_status.json"
        if status_path.exists():
            data = _json.loads(status_path.read_text(encoding="utf-8"))
            print(_json.dumps(data, indent=2))
        else:
            print("Daemon has not run yet.")
        return

    if getattr(args, "daemon_action", None) == "stop":
        from .runtime import EmergencyStop
        stop = EmergencyStop(root)
        stop.engage("user requested daemon stop")
        print("Emergency stop engaged. Daemon will stop after current cycle.")
        return

    model = _resolve_model(getattr(args, "model", None))
    if not model:
        print("A model is required: pass --model or configure one.")
        return
    from .daemon import GhostDaemon
    if getattr(args, "quiet_hours", None) is not None and not all(
            0 <= h <= 23 for h in args.quiet_hours):
        print("hours must be 0-23")
        return
    if getattr(args, "digest_window", None) is not None and not all(
            0 <= h <= 23 for h in args.digest_window):
        print("hours must be 0-23")
        return
    from .overnight import DEFAULT_DIGEST_WINDOW, DEFAULT_QUIET_HOURS, OvernightRunner
    # TD5 (S06): durable daily cap on proactive inference calls.
    max_calls = max(1, min(10000, int(getattr(args, "max_inference_calls", 48))))
    inference_budget = _daily_inference_budget(Path.cwd(), max_calls)
    runner = OvernightRunner(Path.cwd(), model,
                             interval=args.interval,
                             idle_after=args.idle_after_seconds,
                             quiet_hours=tuple(args.quiet_hours) if getattr(args, "quiet_hours", None) else DEFAULT_QUIET_HOURS,
                             digest_window=tuple(args.digest_window) if getattr(args, "digest_window", None) else DEFAULT_DIGEST_WINDOW,
                             inference_budget=inference_budget)
    daemon = GhostDaemon(Path.cwd(), model, interval=args.interval,
                         idle_after=args.idle_after_seconds, trace_every=args.trace_every,
                         quiet_hours=tuple(args.quiet_hours) if getattr(args, "quiet_hours", None) else None,
                         digest_window=tuple(args.digest_window) if getattr(args, "digest_window", None) else None,
                         runner=runner)
    cycles = daemon.run(max_cycles=args.max_cycles)
    print(f"AI-OS daemon stopped after {cycles} cycles.")


# ============================================================================
# Learner CLI (OJ12-S1)
# Wraps LearnerStore + oracles with a clean CLI surface.
# ============================================================================

def _cmd_learn(args: argparse.Namespace) -> None:
    """Personal learning: concepts, attempts, review, spaced repetition (OJ12)."""
    from .learner import LearnerStore, RustCompileOracle, Oracle

    root = Path.cwd()
    learner = LearnerStore(root)
    action = getattr(args, "learn_action", None) or "status"

    if action == "status":
        concepts = learner.concepts()
        due = learner.due_count()
        print(f"Concepts: {len(concepts)}")
        print(f"Due for review: {due}")
        for c in concepts:
            print(f"  [{c.concept_id}] {c.name}: mastery={c.mastery:.1%}, "
                  f"streak={c.streak}, attempts={c.attempts}")
        return

    if action == "concepts":
        for c in learner.concepts():
            print(f"  [{c.concept_id}] {c.name}: mastery={c.mastery:.1%}, "
                  f"due={learner.due_count(now=time.time()) > 0}")
        return

    if action == "add-concept":
        concept_id = args.concept_id
        name = args.name
        concept = learner.register_concept(concept_id, name)
        print(f"Registered: [{concept.concept_id}] {concept.name}")
        return

    if action == "record":
        concept_id = args.concept_id
        correct = args.correct.lower() in ("true", "1", "yes", "correct")
        submission = getattr(args, "submission", "") or ""
        concept = learner.record_attempt(concept_id, correct, submission=submission)
        print(f"Recorded: [{concept_id}] correct={correct}, "
              f"mastery={concept.mastery:.1%}, streak={concept.streak}")
        return

    if action == "review":
        concept_id = args.concept_id
        submission = args.submission
        oracle_name = getattr(args, "oracle", "rustc")

        if oracle_name == "rustc":
            oracle: Oracle = RustCompileOracle()
        else:
            raise ValueError(f"Unknown oracle: {oracle_name}")

        if not oracle.available():
            raise RuntimeError(f"Oracle '{oracle_name}' not available on this system. "
                               "Install rustc or use a different oracle.")

        # assess() calls oracle.check() and record_attempt() internally
        attempt = learner.assess(concept_id, submission, oracle)
        concept = learner.get_concept(concept_id)
        status = "✓" if attempt.correct else "✗"
        print(f"{status} [{concept_id}] {attempt.feedback}")
        if concept:
            print(f"   mastery: {concept.mastery:.1%}, streak: {concept.streak}")
        return

    if action == "path":
        from .learning_engine import LearningEngine
        engine = LearningEngine(root / ".ghost")
        targets = args.targets
        path = engine.generate_path(targets, name=args.name or "", description=args.description)
        print(f"Learning path: [{path.path_id}] {path.name}")
        print(f"  {len(path.concept_ids)} concept(s): {', '.join(path.concept_ids)}")
        return

    if action == "next":
        due = learner.next_activity()
        if due is None:
            print("Nothing due for review right now.")
        else:
            print(f"Next activity: [{due.concept_id}] {due.name}")
        return
    if action == "progress":
        prog = learner.progress()
        print(f"Overall mastery: {prog['overall_mastery']:.1%}")
        print(f"Due for review: {prog['due']}")
        for item in prog["concepts"]:
            print(f"  [{item['concept_id']}] mastery={item['mastery']:.1%}, "
                  f"attempts={item['attempts']}, correct={item['correct']}, streak={item['streak']}")
        return

    if action == "lessons":
        lessons = learner.lessons(args.concept_id)
        if lessons:
            print(f"Lessons for {args.concept_id}:")
            for lesson in lessons:
                print(f"  - {lesson}")
        else:
            print("No cited lessons for this concept.")
        return

    if action == "attempts":
        items = learner.attempts(args.concept_id, limit=getattr(args, "limit", 20))
        if not items:
            print("No attempts recorded.")
            return
        for a in items:
            outcome = "correct" if a.correct else "wrong"
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a.at))
            detail = f" | {a.submission[:60]}" if a.submission else ""
            print(f"  {outcome} [{a.concept_id}] at {when}{detail}")
        return

    if action == "exercises":
        from .study import ExerciseGenerator, StudyError
        model = _resolve_model(getattr(args, "model", None))
        if not model:
            print("A model is required: pass --model or configure one.")
            return
        try:
            generator = ExerciseGenerator(root, model)
            exercises = generator.generate(args.concept_id, count=args.count)
        except StudyError as exc:
            print(f"Exercise generation refused: {exc}")
            return
        if not exercises:
            print("No valid exercises generated.")
            return
        for exercise in exercises:
            print(f"[{exercise.exercise_id}] ({exercise.answer_kind}) "
                  f"sources={','.join(exercise.source_refs) or 'none'}")
            print(f"  {exercise.prompt}")
        return

    if action == "answer":
        from .study import ExerciseGenerator, StudyError
        generator = ExerciseGenerator(root, "cli-check")
        try:
            correct, feedback = generator.check(args.exercise_id, args.submission)
        except StudyError as exc:
            print(f"Answer refused: {exc}")
            return
        print(("✓ correct" if correct else "✗ incorrect") + f" — {feedback}")
        return

    if action == "hint":
        from .study import ExerciseGenerator, StudyError
        generator = ExerciseGenerator(root, "cli-hint")
        try:
            exercise = generator.get_exercise(args.exercise_id)
            if exercise is None:
                raise StudyError(f"Unknown exercise {args.exercise_id}")
            wrong = learner.attempts(exercise.concept_id, limit=200)
            wrong = sum(1 for a in wrong if not a.correct)
            print(generator.next_hint(args.exercise_id, wrong))
        except StudyError as exc:
            print(f"Hint refused: {exc}")
        return

    if action == "deadline":
        import datetime as _dt
        try:
            due_date = _dt.date.fromisoformat(args.date)
        except ValueError:
            raise ValueError(f"Invalid --date (expected YYYY-MM-DD): {args.date}")
        # End of the due day (local midnight of the next day) so the deadline
        # stays relevant through the whole of its date.
        due_at = (_dt.datetime.combine(due_date + _dt.timedelta(days=1),
                                       _dt.time.min)
                  - _dt.datetime(1970, 1, 1)).total_seconds()
        record = learner.set_deadline(args.concepts.split(","), due_at, args.label)
        print(f"Deadline '{record['label']}' set for {args.date} "
              f"({len(record['concept_ids'])} concept(s): "
              f"{','.join(record['concept_ids'])})")
        return

    if action == "formats":
        from .learner import FormatStats
        stats = FormatStats(root).stats()
        for fmt, s in stats.items():
            print(f"  {fmt}: generated={s['generated']}, "
                  f"attempted={s['attempted']}, correct={s['correct']}")
        rec = FormatStats(root).recommend_format()
        print(f"Recommended format: {rec['format']} ({rec['reason']})")
        if rec.get("unused"):
            print(f"Unused: {', '.join(rec['unused'])} — {rec['unused_reason']}")
        return

    if action == "challenge":
        from .challenges import ChallengeError, ChallengeStore
        store = ChallengeStore(root)
        caction = getattr(args, "challenge_action", None) or "list"
        if caction == "create":
            spec_path = Path(args.spec)
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"Spec unreadable: {exc}")
                return
            try:
                challenge_id = store.create(
                    spec.get("concept_ids", []), spec.get("prompt", ""),
                    spec.get("starter", {}), spec.get("oracle", {}),
                    spec.get("solution", {}))
            except ChallengeError as exc:
                print(f"Challenge refused: {exc}")
                return
            print(f"Created challenge [{challenge_id}]")
            return
        if caction == "list":
            challenges = store.list()
            if not challenges:
                print("No challenges.")
                return
            for item in challenges:
                print(f"[{item['challenge_id']}] {item['prompt'][:80]}")
            return
        if caction == "check":
            try:
                correct, feedback = store.check(
                    args.challenge_id, getattr(args, "concept", "") or "")
            except ChallengeError as exc:
                print(f"Check refused: {exc}")
                return
            print(("correct" if correct else "incorrect") + f" — {feedback[:300]}")
            return
        if caction == "reset":
            try:
                store.reset(args.challenge_id)
            except ChallengeError as exc:
                print(f"Reset refused: {exc}")
                return
            print(f"Reset starter for [{args.challenge_id}]")
            return

    print(f"Unknown learn action: {action}")
    print("Available: status, concepts, add-concept, record, review, path, next, progress, lessons, attempts, exercises, answer, hint, deadline, challenge, formats")


# ============================================================================
# SelfImprover CLI (OJ14/S08)
# ============================================================================

def _cmd_self_improve(args: argparse.Namespace) -> None:
    """Propose, sync, and decide on structural improvements."""
    from .self_improver import SelfImprover, SelfImproverStore
    from .trace_learning import run_trace_learning

    root = Path.cwd()
    store = SelfImproverStore(root)
    improver = SelfImprover(root)
    action = getattr(args, "improve_action", None) or "list"

    if action == "list":
        status = getattr(args, "status", None)
        candidates = store.list_candidates(status=status)
        if not candidates:
            print("No improvement candidates found.")
            return
        print(f"{len(candidates)} candidate(s):")
        for c in candidates:
            print(f"  [{c.candidate_id}] {c.status} | {c.hypothesis[:60]}...")
        return

    if action == "propose":
        model = _resolve_model(getattr(args, "model", None))
        if not model:
            print("A model is required: pass --model or configure one.")
            return
        summary = run_trace_learning(root, min_count=2)
        sigs = summary.get("analysis", {}).get("top_signatures", [])
        if not sigs:
            print("No recurring failure signatures found to base an improvement on.")
            return
        cand = improver.propose(sigs, model)
        if cand is None:
            print("No new recurring failure to work on.")
            return
        if cand.status == "failed":
            print(f"[{cand.candidate_id}] queued: FAILED — {cand.failure_reason}")
        else:
            print(f"[{cand.candidate_id}] {cand.status} | job={cand.job_id}")
        return

    if action == "sync":
        changed = improver.sync()
        if not changed:
            print("No queued candidates to sync.")
            return
        for c in changed:
            extra = ""
            if c.status == "handed_to_self_repair":
                extra = f" | repair_proposal_id={c.repair_proposal_id}"
                print("Next: ghost self-repair verify <repair_id> --command \"python -m pytest -q\"")
            elif c.failure_reason:
                extra = f" | {c.failure_reason}"
            print(f"[{c.candidate_id}] {c.status}{extra}")
        return

    if action == "decide":
        candidate_id = args.candidate_id
        decision = args.decision
        reason = args.reason
        if decision not in ("approved", "rejected", "rollback"):
            print("Decision must be: approved, rejected, or rollback")
            return
        store.record_decision(candidate_id, decision, reason)
        print(f"Decision recorded: {candidate_id} -> {decision}")
        return

    print(f"Unknown self-improve action: {action}")
    print("Available: list, propose, decide, sync")




# ============================================================================
# Perception CLI (OJ-Perception)
# ============================================================================

def _cmd_perception(args: argparse.Namespace) -> None:
    """Read-only local observation: filesystem, git, processes."""
    from .perception import FilesystemPerception, GitPerception, ProcessPerception

    root = Path.cwd()
    action = getattr(args, "perception_action", None) or "git"

    if action == "filesystem":
        paths = [Path(p).resolve() for p in args.paths]
        fs = FilesystemPerception(root)
        obs = fs.observe(paths)
        print(f"Observed {len(obs)} filesystem entity(ies):")
        for o in obs:
            state = o.state
            exists = state.get("exists", False)
            status = "✓" if exists else "✗"
            size = state.get("size", "")
            print(f"  {status} {o.entity_id}{f' ({size} bytes)' if size else ''}")
        return

    if action == "git":
        git = GitPerception(root)
        obs = git.observe()
        state = obs.state
        print(f"Git working tree:")
        print(f"  branch: {state.get('branch', '?')}")
        print(f"  dirty: {state.get('dirty', False)}")
        changes = state.get("changed_entries", [])
        if changes:
            print(f"  changed: {len(changes)} file(s)")
            for c in changes[:5]:
                print(f"    {c}")
        else:
            print("  (clean)")
        return

    if action == "process":
        pid = args.pid
        proc = ProcessPerception(root)
        obs = proc.observe(pid)
        state = obs.state
        alive = state.get("alive")
        if alive is True:
            print(f"Process {pid}: ALIVE")
        elif alive is False:
            print(f"Process {pid}: TERMINATED")
        else:
            print(f"Process {pid}: UNKNOWN ({state.get('error', 'access denied')})")
        return

    print(f"Unknown perception action: {action}")
    print("Available: filesystem, git, process")


# ============================================================================
# Context synthesizer CLI (OJ-Context)
# ============================================================================

def _cmd_context(args: argparse.Namespace) -> None:
    """Synthesize runtime context for model prompts; show conflicts."""
    from .context_synthesizer import ContextSynthesizer, SynthesizedContext

    root = Path.cwd()
    action = getattr(args, "ctx_action", None) or "assemble"
    synthesizer = ContextSynthesizer(root)

    if action == "assemble":
        assembled = synthesizer.synthesize(max_tokens=2000)
        print(f"Synthesized context ({len(assembled.items)} item(s), "
              f"{assembled.total_tokens} tokens, pruned={len(assembled.pruned_items)}):")
        for item in assembled.items[:10]:
            print(f"  [{item.relevance:.2f}] {item.source}: {item.content[:80]}...")
        if assembled.conflicts:
            print(f"\n  Conflicts ({len(assembled.conflicts)}):")
            for cf in assembled.conflicts:
                print(f"    {cf.topic}: {cf.summary[:80]}")
        return

    if action == "conflicts":
        assembled = synthesizer.synthesize(max_tokens=2000)
        conflicts = assembled.conflicts
        if not conflicts:
            print("No context conflicts detected.")
            return
        print(f"Context conflicts ({len(conflicts)}):")
        for cf in conflicts:
            print(f"  {cf.topic}: {cf.summary}")
            if cf.resolved_content:
                print(f"    resolved: {cf.resolved_content[:80]}")
        return

    print(f"Unknown context action: {action}")
    print("Available: assemble, conflicts")


# ============================================================================
# Bench CLI (T11)
# ============================================================================

def _cmd_bench(args: argparse.Namespace) -> None:
    """Run the fixed benchmark of 8 non-toy goals with hidden acceptance tests."""
    from .benchmark import format_report, latest_report, list_reports, run_benchmark
    from .benchmark_goals import BENCH_GOALS

    root = Path.cwd()
    action = getattr(args, "bench_action", None) or "list"

    if action == "list":
        for goal in BENCH_GOALS:
            print(f"{goal.goal_id:<14} {goal.module_file}")
        return

    if action == "run":
        model = _resolve_model(getattr(args, "model", None))
        if not model:
            print("A model is required: pass --model or configure one.")
            return
        goal_ids = getattr(args, "goals", None)
        iterations = getattr(args, "iterations", 10)
        report = run_benchmark(
            root,
            model,
            lambda run_root: (lambda scheduled: _execute_queued_outcome(run_root, scheduled)),
            goal_ids=goal_ids,
            iterations=iterations,
        )
        print(format_report(report))
        from .benchmark import latest_report as _lr
        bench_root = root / ".ghost" / "bench" / report["run_id"]
        print(f"Report: {bench_root / 'report.json'}")
        return

    if action == "report":
        report = latest_report(root)
        if report is None:
            print("No benchmark runs yet.")
        else:
            print(format_report(report))
        return

    if action == "history":
        reports = list_reports(root)
        if not reports:
            print("No benchmark runs yet.")
            return
        print(f"{'run_id':<24} {'model':<20} {'accepted':<9} {'ghost':<7} goals")
        for entry in reports:
            if "error" in entry:
                print(f"{entry['run_id']:<24} <unreadable report: {entry['error']}>")
                continue
            sm = entry.get("summary", {})
            print(
                f"{entry['run_id']:<24} {str(entry.get('model', '')):<20} "
                f"{sm.get('accepted_rate', 0.0):<9.3f} {sm.get('ghost_pass_rate', 0.0):<7.3f} "
                f"{sm.get('total', 0)}"
            )
        return

    if action == "compare":
        from .benchmark import compare as compare_reports

        reports = list_reports(root)
        by_id: dict[str, dict] = {}
        for entry in reports:
            if "error" not in entry:
                by_id[entry.get("run_id", "")] = entry

        def _resolve(token: str) -> Optional[dict]:
            if token in by_id:
                return by_id[token]
            if token == "latest":
                for entry in reports:
                    if "error" not in entry:
                        return entry
                return None
            if token == "previous":
                seen = 0
                for entry in reports:
                    if "error" not in entry:
                        seen += 1
                        if seen == 2:
                            return entry
                return None
            return None

        report_a = _resolve(args.run_a)
        report_b = _resolve(args.run_b)
        if report_a is None or report_b is None:
            print(f"Run not found: {args.run_a if report_a is None else args.run_b}")
            print("Available run ids: " + (", ".join(by_id) if by_id else "(none)"))
            return
        result = compare_reports(report_a, report_b)
        print(
            f"comparing {result['run_a']} ({result['model_a']}) -> "
            f"{result['run_b']} ({result['model_b']})"
        )
        if result["different_models"]:
            print(f"WARNING: different models ({result['model_a']} vs {result['model_b']})")
        print(f"{'goal_id':<14} {'a':<22} {'b':<22} change")
        for goal_id, entry in result["goals"].items():
            def _fmt(cell) -> str:
                if cell is None:
                    return "-"
                return f"tp={str(cell['tests_passed']).lower()}/acc={str(cell['accepted']).lower()}"
            print(f"{goal_id:<14} {_fmt(entry['a']):<22} {_fmt(entry['b']):<22} {entry['change']}")
        sm = result["summary"]
        print(
            f"accepted_rate {sm['a']['accepted_rate']:.3f} -> {sm['b']['accepted_rate']:.3f} "
            f"(delta {sm['delta_accepted_rate']:+.3f}); "
            f"ghost_pass_rate {sm['a']['ghost_pass_rate']:.3f} -> {sm['b']['ghost_pass_rate']:.3f} "
            f"(delta {sm['delta_ghost_pass_rate']:+.3f})"
        )
        print(
            f"improved={sm['improved']} regressed={sm['regressed']} same={sm['same']} "
            f"new={sm['new']} removed={sm['removed']}"
        )
        return

    print(f"Unknown bench action: {action}")
    print("Available: list, run, report, history, compare")


def _cmd_protocol_bench(args: argparse.Namespace) -> None:
    """Forward to the non-executing protocol benchmark's own argparse entry."""
    import sys as _sys
    from .protocol_benchmark import main as run_protocol_benchmark

    # protocol_benchmark.main() parses sys.argv itself; rebuild it from our
    # already-validated namespace so unknown top-level flags never reach it.
    forward: list[str] = []
    for key in ("output_root", "model", "base_url", "repetitions",
                "prompt_style", "dataset", "output_format"):
        forward.append(f"--{key.replace('_', '-')}={getattr(args, key)}")
    _sys.argv = ["ghost protocol-bench"] + forward
    run_protocol_benchmark()


def _cmd_model_profile(args: argparse.Namespace) -> None:
    """TH2: model profiles with explicit unknowns (unknown != free/zero)."""
    from .models.profiles import ModelProfileStore

    root = Path.cwd()
    store = ModelProfileStore(root)
    action = getattr(args, "model_profile_action", None) or "show"
    model = args.model
    profile = store.get(model)

    if action == "show":
        print(f"model: {profile.model}")
        print(f"provider: {profile.provider}")
        print(f"context_tokens: {profile.context_tokens}")
        print(f"max_output_tokens: {profile.max_output_tokens}")
        print(f"supports_tools: {profile.supports_tools}")
        print(f"modalities: {', '.join(profile.modalities)}")
        print(f"cost_per_1k_input: {profile.cost_per_1k_input if profile.cost_per_1k_input is not None else 'unknown'}")
        print(f"cost_per_1k_output: {profile.cost_per_1k_output if profile.cost_per_1k_output is not None else 'unknown'}")
        print(f"source: {profile.source}")
        return

    if action == "set":
        if getattr(args, "context", None) is not None:
            profile.context_tokens = args.context
        if getattr(args, "max_output", None) is not None:
            profile.max_output_tokens = args.max_output
        if getattr(args, "cost_in", None) is not None:
            profile.cost_per_1k_input = args.cost_in
        if getattr(args, "cost_out", None) is not None:
            profile.cost_per_1k_output = args.cost_out
        store.set(profile)
        print(f"Saved profile for {model} to {store.path}")
        return

    if action == "probe":
        base_url = getattr(args, "base_url", None) or os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        probed = store.probe_ollama(model, base_url)
        print(f"probed {model}: context_tokens={probed.context_tokens if probed.context_tokens is not None else 'unknown'} source={probed.source}")
        return

    print(f"Unknown model-profile action: {action}")
    print("Available: show, set, probe")


if __name__ == "__main__":
    main()
