# Track TP: document ingest adapters, source preference, conflicting context (S05, R06, R02)

Wave 2 track, created 2026-10-10 when TK/TI finished. Audits: `audit/A2.md`
(S05: page references, OCR; R02: conflicts into context), `audit/A5.md` (R06:
official-docs preference, PDF sources). Branch `c2/TP`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TP`.
Files this track owns: `ghost_agent/knowledge_base.py`, new
`ghost_agent/extractors.py`, `ghost_agent/tools/web_tools.py`
(`source_credibility` and PDF handling only), `ghost_agent/context_manager.py`
(`ContextAssembler` only), and tests. **No new Python dependencies.** The
external tools are optional subprocesses: `pdftotext` and `tesseract` are
installed on this host, but the code must degrade honestly when they're
absent.

---

## TP1: PDF and image extraction adapters (review: 2)

**Decided design:** new module `ghost_agent/extractors.py`:

```python
@dataclass
class Extraction: text: str; pages: list[tuple[int, str]]; quality: str  # "text"|"ocr"|"partial"; tool: str
class ExtractionUnavailable(RuntimeError): ...
def extract_pdf(path: Path, timeout: int = 60) -> Extraction
def extract_image(path: Path, timeout: int = 120) -> Extraction
```

- `extract_pdf`: `shutil.which("pdftotext")`, then run
  `pdftotext -layout -enc UTF-8 <path> -` (60 s timeout). Pages split on form
  feed `\f`, 1-based. Quality is `text`, or `partial` if any page is
  empty/whitespace-only (likely a scanned page).
- `extract_image` (`.png .jpg .jpeg .tif .tiff`): `shutil.which("tesseract")`,
  then run `tesseract <path> stdout`. One page; quality `ocr`.
- A missing tool raises `ExtractionUnavailable("pdftotext not installed; install poppler-utils to ingest PDFs")`
  (or the tesseract equivalent). Never pip-install, download or guess.
- Output is capped at 2,000,000 chars, with truncation marked by quality
  `partial`.
- `OwnerKnowledgeBase._ingest_file`: route `.pdf` and image suffixes through
  the extractors. Each chunk gets `page` (from TG2's field) and
  `extraction_quality`. An unavailable tool skips the file and records it in
  the ingest stats as `skipped: [{"path", "reason"}]`; it doesn't crash. TG2
  added `page`/`extraction_quality` to DocChunk; use them.

**Tests:** patch `shutil.which` and subprocess. A PDF with 2 pages becomes
chunks with pages 1/2; an empty page gives quality partial; a missing tool is
skipped with its reason; an image goes through OCR quality. Plus one real
integration test that runs only if `pdftotext` exists (use
`pytest.mark.skipif`): create a tiny PDF in the test with raw PDF bytes (a
minimal valid one-page PDF string) and extract "Hello".

---

## TP2: official-documentation preference in research

**Decided design:** in `web_tools.source_credibility(url)`, find its existing
tiers. Add a top tier `official_docs` for hosts that match a fixed allowlist of
documentation domains: `docs.python.org`, `doc.rust-lang.org`,
`developer.mozilla.org`, `core.telegram.org`, `developer.huawei.com`,
`learn.microsoft.com`, `docs.github.com`, `ollama.com`, plus any host starting
with `docs.` or `developer.`. Keep the existing tiers' ordering below it.
Research ranking that uses credibility then prefers official docs
automatically. Verify where credibility is consumed (`git grep -n source_credibility`)
and report it. If a consumer treats tiers as a closed set, extend it; don't
break it.

**Tests:** each listed host maps to official_docs; a `docs.` prefix matches;
an ordinary site keeps its old tier; the consumer ordering puts official docs
first (fixture list).

---

## TP3: surface contradictions in assembled context (parallel-ok with TP2)

**Goal:** contradictions are preserved and shown to the model and the owner,
not silently blended (R02: "Preserve contradictions and corrections").

**Decided design:** in `ContextAssembler.assemble`, after selecting memories,
run `ContextSynthesizer(root).detect_conflicts(...)`. Read context_synthesizer.py
for its actual API name and inputs; if no such method exists, stop and
report. Add `"conflicts": [{"a": id, "b": id, "reason": str}]` (max 5) to the
assembled payload, and a short line in the rendered text:
`"Conflicting records: <a> vs <b> (<reason>); do not assume either is true."`
This is bounded by the existing `max_chars` logic: conflicts are optional
context and the first thing dropped.

**Tests:** two contradictory memories produce a conflicts entry and the
warning line; no conflicts → no key/line; max_chars pressure drops conflicts
before required content.
