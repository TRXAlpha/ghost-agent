"""Extraction adapters for PDF and image documents (TP1).

PDF text comes from ``pdftotext`` (poppler-utils); image OCR comes from
``tesseract``. Neither tool is installed automatically — when missing we raise
:class:`ExtractionUnavailable` and the caller records a skip. We never
pip-install, download, or guess at an extraction path.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 2_000_000  # cap extracted text; over this, quality becomes "partial"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class Extraction:
    text: str
    pages: list[tuple[int, str]] = field(default_factory=list)  # (1-based page no, text)
    quality: str = "text"  # "text" | "ocr" | "partial"
    tool: str = ""


class ExtractionUnavailable(RuntimeError):
    """Raised when the external extraction tool for a file type is missing."""


def _run_tool(cmd: list[str], timeout: int) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed (exit {proc.returncode}): "
                           f"{proc.stderr.strip()[:200]}")
    return proc.stdout


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_CHARS:
        return text, False
    return text[:MAX_CHARS], True


def extract_pdf(path: Path, timeout: int = 60) -> Extraction:
    """Extract text from a PDF via pdftotext -layout, split on form feeds."""
    tool = "pdftotext"
    if shutil.which(tool) is None:
        raise ExtractionUnavailable(
            f"{tool} not installed; install poppler-utils to ingest PDFs")
    raw = _run_tool([tool, "-layout", "-enc", "UTF-8", str(path), "-"], timeout)
    raw, truncated = _truncate(raw)
    # pdftotext ends output with a trailing \f; the empty remainder is a
    # split artifact, not a real blank page.
    if raw.endswith("\f"):
        raw = raw[:-1]
    pages = [(i, page) for i, page in enumerate(raw.split("\f"), start=1)]
    quality = "partial" if (truncated or any(not p.strip() for _, p in pages)) else "text"
    return Extraction(text=raw, pages=pages, quality=quality, tool=tool)


def extract_image(path: Path, timeout: int = 120) -> Extraction:
    """OCR an image (png/jpg/tiff) via tesseract; single page, quality "ocr"."""
    tool = "tesseract"
    if shutil.which(tool) is None:
        raise ExtractionUnavailable(
            f"{tool} not installed; install tesseract-ocr to ingest images")
    raw = _run_tool([tool, str(path), "stdout"], timeout)
    raw, truncated = _truncate(raw)
    quality = "partial" if truncated else "ocr"
    return Extraction(text=raw, pages=[(1, raw)], quality=quality, tool=tool)


def extract_any(path: Path, timeout_pdf: int = 60, timeout_image: int = 120) -> Extraction:
    """Dispatch on file suffix: PDF -> extract_pdf, image -> extract_image."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path, timeout=timeout_pdf)
    if suffix in IMAGE_EXTS:
        return extract_image(path, timeout=timeout_image)
    raise ValueError(f"no extractor for file type: {suffix}")
