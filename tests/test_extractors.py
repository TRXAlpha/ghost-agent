"""TP1 tests: PDF/image extraction adapters and knowledge-base routing."""
from __future__ import annotations

import shutil
import subprocess

import pytest

import ghost_agent.extractors as ex
from ghost_agent.extractors import (
    ExtractionUnavailable,
    extract_image,
    extract_pdf,
)
from ghost_agent.knowledge_base import OwnerKnowledgeBase

# Minimal valid one-page PDF containing "Hello".
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 24 Tf 100 700 Td (Hello) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def test_extract_pdf_missing_tool_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: None)
    with pytest.raises(ExtractionUnavailable, match="poppler-utils"):
        extract_pdf(tmp_path / "x.pdf")


def test_extract_image_missing_tool_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: None)
    with pytest.raises(ExtractionUnavailable, match="tesseract"):
        extract_image(tmp_path / "x.png")


def test_extract_pdf_splits_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/pdftotext")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class P:
            returncode = 0
            stdout = "page one\fpage two\n"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    result = extract_pdf(tmp_path / "doc.pdf")
    assert [p for p, _ in result.pages] == [1, 2]
    assert result.pages[0][1] == "page one"
    assert result.quality == "text"
    assert result.tool == "pdftotext"
    assert captured["cmd"][:3] == ["pdftotext", "-layout", "-enc"]
    assert captured["cmd"][-1] == "-"


def test_extract_pdf_empty_page_is_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/pdftotext")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "scanned page follows\f\n"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    result = extract_pdf(tmp_path / "doc.pdf")
    assert result.quality == "partial"


def test_extract_image_ocr_quality(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/tesseract")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "recognized words"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    result = extract_image(tmp_path / "scan.png")
    assert result.quality == "ocr"
    assert result.pages == [(1, "recognized words")]
    assert result.text == "recognized words"


def test_extract_output_truncated_marks_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/pdftotext")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "x" * (ex.MAX_CHARS + 1)
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    result = extract_pdf(tmp_path / "doc.pdf")
    assert len(result.text) == ex.MAX_CHARS
    assert result.quality == "partial"


# ── Knowledge base routing ─────────────────────────────────────────────────


def test_ingest_routes_pdf_through_extractor(monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/pdftotext")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "alpha content\fbravo content\n"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    kb = OwnerKnowledgeBase(tmp_path)
    stats = kb.ingest(tmp_path)
    pdf_chunks = sorted(
        (c for c in kb.chunks if c.source == "doc.pdf" and not c.superseded),
        key=lambda c: c.chunk_id)
    assert len(pdf_chunks) == 2
    assert [c.page for c in pdf_chunks] == [1, 2]
    assert all(c.extraction_quality == "text" for c in pdf_chunks)
    assert "alpha content" in pdf_chunks[0].content
    assert stats.skipped == []


def test_ingest_empty_page_quality_partial_and_blank_skipped(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/pdftotext")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "real text\f\fmore text\n"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    kb = OwnerKnowledgeBase(tmp_path)
    kb.ingest(tmp_path)
    pages = sorted(c.page for c in kb.chunks if c.source == "scan.pdf")
    assert pages == [1, 3]  # blank page 2 carries no chunk
    assert all(c.extraction_quality == "partial" for c in kb.chunks
               if c.source == "scan.pdf")


def test_ingest_missing_tool_skips_with_reason(monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(ex.shutil, "which", lambda name: None)
    kb = OwnerKnowledgeBase(tmp_path)
    stats = kb.ingest(tmp_path)  # must not raise
    assert not any(c.source == "doc.pdf" for c in kb.chunks)
    assert stats.skipped == [{"path": "doc.pdf",
                              "reason": "pdftotext not installed; "
                                        "install poppler-utils to ingest PDFs"}]


def test_ingest_image_goes_through_ocr(monkeypatch, tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8fakejpeg")
    monkeypatch.setattr(ex.shutil, "which", lambda name: "/usr/bin/tesseract")

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "ocr recognized text"
            stderr = ""
        return P()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    kb = OwnerKnowledgeBase(tmp_path)
    kb.ingest(tmp_path)
    chunks = [c for c in kb.chunks if c.source == "photo.jpg"]
    assert len(chunks) == 1
    assert chunks[0].page == 1
    assert chunks[0].extraction_quality == "ocr"
    assert "ocr recognized text" in chunks[0].content


@pytest.mark.skipif(shutil.which("pdftotext") is None,
                    reason="pdftotext not installed")
def test_extract_pdf_real_integration(tmp_path):
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    result = extract_pdf(pdf)
    assert "Hello" in result.text
    assert result.tool == "pdftotext"
