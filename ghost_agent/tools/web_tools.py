"""Web search tool — DuckDuckGo HTML lite, zero dependencies beyond requests."""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import ipaddress
import os
import re
import socket
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse, urldefrag, urljoin

import requests

_DDG_URL = "https://html.duckduckgo.com/html/"
_OPENALEX_URL = "https://api.openalex.org/works"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GhostAgent/1.0)"}


def _is_forbidden_destination(host: str) -> bool:
    """True if host resolves to any loopback/private/link-local/reserved address.

    Research fetches must never reach localhost, private networks, link-local
    or metadata endpoints (e.g. 169.254.169.254) unless the owner explicitly
    scopes them with GHOST_ALLOW_PRIVATE_FETCH=1. Resolution failure is
    forbidden too (fail closed).
    """
    if os.environ.get("GHOST_ALLOW_PRIVATE_FETCH") == "1":
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError, UnicodeError):
        return True
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_loopback or address.is_private or address.is_link_local
                or address.is_multicast or address.is_unspecified
                or address.is_reserved):
            return True
    return False


class _PageTextExtractor(HTMLParser):
    """Extract readable text without adding an HTML parsing dependency."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS = {"p", "br", "li", "h1", "h2", "h3", "h4", "article", "section", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if not self._skip_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if not self._skip_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _clean_page_text(html: str) -> str:
    parser = _PageTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    text = unescape(" ".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()


def source_credibility(url: str) -> Dict[str, Any]:
    """Return a transparent, deliberately conservative source-quality score."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".").removeprefix("www.")
        if parsed.scheme not in {"https", "http"} or parsed.username is not None or parsed.password is not None:
            host = ""
        parsed.port  # Reject invalid port syntax rather than misclassifying it.
    except (ValueError, UnicodeError):
        host = ""
    if not host:
        return {"score": 0.0, "tier": "unknown", "domain": ""}

    def belongs_to(domains: tuple[str, ...]) -> bool:
        return any(host == domain or host.endswith("." + domain) for domain in domains)

    if belongs_to(("wikipedia.org", "medium.com", "substack.com", "youtube.com")):
        return {"score": 0.15, "tier": "tertiary_or_social", "domain": host}

    if host.endswith(".gov") or host.endswith(".edu") or host in {
        "who.int", "esa.int", "nasa.gov", "nih.gov", "cdc.gov",
    }:
        return {"score": 0.90, "tier": "public_or_academic_institution", "domain": host}
    if belongs_to((
        "nature.com", "science.org", "cell.com", "thelancet.com", "nejm.org",
        "pnas.org", "ieee.org", "acm.org", "aps.org", "aip.org", "wiley.com",
        "springer.com", "sciencedirect.com", "oup.com",
    )):
        return {"score": 0.85, "tier": "scholarly_publisher", "domain": host}
    if belongs_to(("ncbi.nlm.nih.gov", "crossref.org", "semanticscholar.org", "openalex.org")):
        return {"score": 0.80, "tier": "scholarly_index", "domain": host}
    if belongs_to(("arxiv.org", "biorxiv.org", "medrxiv.org", "osf.io")):
        return {"score": 0.65, "tier": "preprint_or_repository", "domain": host}
    if host.endswith(".org"):
        return {"score": 0.50, "tier": "organization", "domain": host}
    return {"score": 0.35, "tier": "unclassified_web", "domain": host}


def evidence_identity(source: Dict[str, Any]) -> str:
    """Deduplicate a scholarly work across its DOI/index/hosting aliases."""
    doi = str(source.get("doi") or "").strip()
    url = str(source.get("url") or "").strip()
    parsed = urlparse(url)
    if not doi and parsed.hostname in {"doi.org", "dx.doi.org"}:
        doi = unquote(parsed.path).lstrip("/")
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        return "doi:" + doi.casefold()
    if source.get("work_id"):
        return "work:" + str(source["work_id"]).casefold()
    return urldefrag(url)[0]


def source_group(source: Dict[str, Any]) -> str:
    """Retrieval diversity proxy, not proof of independent experiments."""
    for key in ("publisher_id", "venue_id"):
        if source.get(key):
            return key + ":" + str(source[key])
    return "host:" + str(source.get("domain") or source_credibility(source.get("url", ""))["domain"])


def evidence_aliases(source: Dict[str, Any]) -> set[str]:
    aliases = {evidence_identity(source)}
    if source.get("work_id"):
        aliases.add("work:" + str(source["work_id"]).casefold())
    if source.get("url"):
        aliases.add(urldefrag(str(source["url"]))[0])
    return aliases - {""}


def fetch_source_text(url: str, timeout: int = 15, max_chars: int = 24_000,
                      max_bytes: int = 2 * 1024 * 1024) -> Dict[str, Any]:
    """Fetch a source page and return normalized evidence text.

    This intentionally declines non-HTML pages (including PDFs) rather than
    pretending a search-result snippet is primary evidence.

    The check refuses hosts that resolve to private/loopback/link-local or
    reserved addresses (or that fail to resolve) unless GHOST_ALLOW_PRIVATE_FETCH=1.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= 8 * 1024 * 1024:
        raise ValueError("max_bytes must be an integer from 1 to 8388608")
    if type(max_chars) is not int or not 1 <= max_chars <= 8 * 1024 * 1024:
        raise ValueError("max_chars must be an integer from 1 to 8388608")
    # Request identity encoding and refuse compression to avoid decompression
    # expansion before the byte gate. Follow redirects without reading bodies.
    current = url
    try:
        for redirect in range(6):
            if not source_credibility(current)["domain"]:
                return {}
            current_host = urlparse(current).hostname
            if current_host is None or _is_forbidden_destination(current_host):
                raise ValueError("refusing to fetch private, local or unresolvable host: " + repr(current_host))
            with requests.get(current, headers={**_HEADERS, "Accept-Encoding": "identity"},
                              timeout=timeout, stream=True, allow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location or redirect == 5:
                        return {}
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type.split(";", 1)[0].strip() not in {"text/html", "application/xhtml+xml"}:
                    return {}
                if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
                    return {}
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isascii() or not length.isdigit()
                        or len(length) > 10 or int(length) > max_bytes):
                    return {}
                body = bytearray()
                for chunk in response.iter_content(chunk_size=min(65536, max_bytes + 1)):
                    if len(body) + len(chunk) > max_bytes:
                        return {}  # Never treat a cut-off document as evidence.
                    body.extend(chunk)
                encoding = response.encoding or "utf-8"
                try:
                    html = body.decode(encoding, errors="replace")
                except LookupError:
                    html = body.decode("utf-8", errors="replace")
                resolved_url = response.url
                break
        else:
            return {}
    except (requests.RequestException, OSError, ValueError):
        return {}

    text = _clean_page_text(html)
    if len(text) < 200:
        return {}

    return {
        "url": resolved_url,
        "evidence_text": text[:max_chars],
        "content_type": content_type,
        "source_kind": "full_text",
        "download_bytes": len(body),
        "evidence_truncated": len(text) > max_chars,
        **source_credibility(resolved_url),
    }


def _abstract_from_inverted_index(index: Any) -> str:
    """Reconstruct an OpenAlex abstract from its compact word-position index."""
    if not isinstance(index, dict):
        return ""
    positions: Dict[int, str] = {}
    for word, offsets in index.items():
        if not isinstance(word, str) or not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
                positions[offset] = word
    return " ".join(positions[position] for position in sorted(positions))


def academic_search(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Discover scholarly works through OpenAlex when general web search is weak.

    The evidence text is the indexed article abstract, explicitly labelled as
    such. It is useful for source-grounded triage, but never masquerades as a
    full paper.
    """
    try:
        if _is_forbidden_destination(urlparse(_OPENALEX_URL).hostname or ""):
            return []
        response = requests.get(
            _OPENALEX_URL,
            params={"search": query, "per-page": max_results},
            headers=_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        works = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(works, list):
            return []
    except (requests.RequestException, ValueError, OSError):
        return []

    results: List[Dict[str, Any]] = []
    for work in works:
        if not isinstance(work, dict) or work.get("is_retracted") is True:
            continue
        abstract = _abstract_from_inverted_index(work.get("abstract_inverted_index"))
        title = str(work.get("title") or "").strip()
        if len(title) < 5:
            continue
        location = work.get("primary_location") or {}
        if not isinstance(location, dict):
            continue
        venue = location.get("source") or {}
        if not isinstance(venue, dict):
            venue = {}
        landing_url = location.get("landing_page_url") or work.get("doi") or work.get("id")
        if not isinstance(landing_url, str) or not source_credibility(landing_url)["domain"]:
            continue
        landing_host = urlparse(landing_url).hostname
        if landing_host is None or _is_forbidden_destination(landing_host):
            continue
        work_type = str(work.get("type") or "")
        score = 0.75 if work_type == "article" else 0.65
        result = {
            "title": title,
            "url": str(landing_url),
            "snippet": abstract[:500] if len(abstract) >= 200 else "",
            "tier": "scholarly_abstract_metadata" if len(abstract) >= 200 else "scholarly_discovery_metadata",
            "score": score,
            "domain": source_credibility(landing_url)["domain"],
            "retrieved_via": "openalex.org",
            "work_id": work.get("id"),
            "doi": work.get("doi"),
            "publisher_id": venue.get("host_organization"),
            "venue_id": venue.get("id"),
            "venue_name": venue.get("display_name"),
            "publication_year": work.get("publication_year"),
            "cited_by_count": work.get("cited_by_count", 0),
            "source_kind": "abstract" if len(abstract) >= 200 else "metadata_only",
        }
        if len(abstract) >= 200:
            result.update(evidence_text=abstract[:24_000], evidence_excerpt=abstract[:5_000])
        results.append(result)
    return results


def web_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search the web via DuckDuckGo and return structured results.

    Returns a list of dicts, each with:
      - title: str
      - url: str
      - snippet: str

    Returns an empty list on failure.
    """
    try:
        if _is_forbidden_destination(urlparse(_DDG_URL).hostname or ""):
            return []
        resp = requests.get(
            _DDG_URL,
            params={"q": query},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
    except (requests.RequestException, OSError):
        return []

    html = resp.text
    results: List[Dict[str, Any]] = []

    # Extract titles + URLs from <a class="result__a" href="...">title</a>
    link_pattern = re.compile(
        r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    # Extract snippets from <a class="result__snippet" ...>snippet</a>
    snippet_pattern = re.compile(
        r'<a\s+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (raw_url, raw_title) in enumerate(links):
        if i >= max_results:
            break

        # DuckDuckGo wraps URLs through a redirect; extract the real URL
        url = _extract_real_url(raw_url)
        title = _strip_html(raw_title).strip()
        snippet = _strip_html(snippets[i]).strip() if i < len(snippets) else ""

        if url and title:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


def _extract_real_url(raw_url: str) -> str:
    """Extract the actual URL from a DuckDuckGo redirect URL."""
    # DDG format: //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&rut=...
    if "uddg=" in raw_url:
        m = re.search(r"uddg=([^&]+)", raw_url)
        if m:
            return unquote(m.group(1))
    # Direct URL
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


def format_search_results(results: List[Dict[str, Any]], query: str) -> str:
    """Format search results into a readable string for the LLM."""
    if not results:
        return f"No web results found for: {query}"

    parts = [f"Web search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r['title']}")
        parts.append(f"   URL: {r['url']}")
        if r["snippet"]:
            parts.append(f"   {r['snippet']}")
        parts.append("")

    return "\n".join(parts)
