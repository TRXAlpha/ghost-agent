"""Read-only knowledge graph over the owner's Obsidian vault (Ghost/vault/wiki).

The vault is the owner's curated project graph (projects, hosts, entities,
sessions) kept by claude-obsidian. Ghost uses it as context, not as a second
memory authority: it never writes the vault (writes go through the owner's
reviewed vault transactions). Retrieval is graph-first:

1. lexical seed: score pages by query-word hits in title (x3), tags/type (x2), body (x1);
2. expansion: add the seeds' [[wikilink]] neighbours (out- and back-links), ranked by
   how many seeds link to them;
3. excerpt: frontmatter-free text, secrets redacted, bounded size.
No embeddings, no network, no model.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .security import SECRET_PATTERNS
from .utils.storage import atomic_json

DEFAULT_VAULT = Path.home() / "Documents" / "Ghost" / "vault"
_LINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_WORD = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
_STOP = frozenset("the and for with that this from into are was not you your can will about have has its "
                  "what when which how why who use using new all any one two get set run".split())


def vault_root() -> Optional[Path]:
    """GHOST_VAULT, else ~/Documents/Ghost/vault; None when no wiki/ exists."""
    root = Path(os.environ.get("GHOST_VAULT") or DEFAULT_VAULT)
    return root if (root / "wiki").is_dir() else None


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.casefold()) if w not in _STOP]


def _parse(rel: str, text: str) -> dict[str, Any]:
    meta: dict[str, str] = {}
    body = text
    match = _FRONT.match(text.replace("\r\n", "\n"))
    if match:
        body = text.replace("\r\n", "\n")[match.end():]
        for line in match.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip("'\"")
        tags = re.findall(r"^\s+-\s+(.+)$", match.group(1), re.MULTILINE)
    else:
        tags = []
    links = sorted({m.strip().removesuffix(".md") for m in _LINK.findall(body)})
    title = meta.get("title") or rel.rsplit("/", 1)[-1]
    return {"id": rel, "title": title, "type": meta.get("type", ""), "status": meta.get("status", ""),
            "tags": tags, "links": links, "body": body.strip()}


def build_graph(root: Optional[Path] = None, cache: Optional[Path] = None) -> dict[str, Any]:
    """Pages and links of wiki/**/*.md, cached by file mtimes."""
    root = root or vault_root()
    if root is None:
        return {"root": None, "pages": {}}
    files = sorted((root / "wiki").rglob("*.md"))
    signature = [f"{p.relative_to(root / 'wiki').as_posix()}:{p.stat().st_mtime_ns}" for p in files]
    if cache is not None and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("signature") == signature:
                return cached
        except (OSError, ValueError):
            pass
    pages = {}
    for path in files:
        rel = path.relative_to(root / "wiki").as_posix().removesuffix(".md")
        try:
            pages[rel] = _parse(rel, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    graph = {"root": str(root), "signature": signature, "built_at": time.time(), "pages": pages}
    if cache is not None:
        atomic_json(cache, graph)
    return graph


def _redact(text: str) -> str:
    for pattern in SECRET_PATTERNS.values():
        text = pattern.sub("[redacted]", text)
    return text


def query(graph: dict[str, Any], text: str, *, k: int = 4, neighbours: int = 4,
          excerpt_chars: int = 700) -> list[dict[str, Any]]:
    """Graph-first retrieval: lexical seeds, then their most-linked neighbours."""
    pages = graph.get("pages", {})
    terms = Counter(_words(text))
    if not pages or not terms:
        return []
    backlinks: dict[str, set[str]] = {}
    for pid, page in pages.items():
        for target in page["links"]:
            backlinks.setdefault(target, set()).add(pid)
    scores: dict[str, float] = {}
    for pid, page in pages.items():
        if page["type"] in {"meta", "map"}:
            continue  # index/log/hot/catalog maps are navigation, not knowledge
        title, tags, body = (Counter(_words(page["title"] + " " + pid)), Counter(_words(" ".join(page["tags"]))),
                             Counter(_words(page["body"])))
        score = sum(3 * title[t] + 2 * tags[t] + min(body[t], 5) for t in terms)
        if score:
            scores[pid] = score
    seeds = [pid for pid, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]
    near: Counter = Counter()
    for pid in seeds:
        for other in set(pages[pid]["links"]) | backlinks.get(pid, set()):
            if other in pages and other not in seeds and pages[other]["type"] not in {"meta", "map"}:
                near[other] += 1
    picked = [(pid, "seed") for pid in seeds] + [(pid, "linked") for pid, _ in near.most_common(neighbours)]
    out = []
    for pid, why in picked:
        page = pages[pid]
        out.append({"page": pid, "title": page["title"], "type": page["type"], "via": why,
                    "links": page["links"][:12], "excerpt": _redact(page["body"][:excerpt_chars])})
    return out


def context_for(project_root: Path, text: str, **kwargs) -> list[dict[str, Any]]:
    """Vault pages relevant to `text`, using the cache under .ghost/; [] when there is no vault."""
    try:
        graph = build_graph(cache=Path(project_root).resolve() / ".ghost" / "vault-graph.json")
        return query(graph, text, **kwargs)
    except Exception:
        return []  # the vault is optional context, never a failure


def stats(graph: dict[str, Any]) -> dict[str, Any]:
    pages = graph.get("pages", {})
    return {"root": graph.get("root"), "pages": len(pages),
            "links": sum(len(p["links"]) for p in pages.values()),
            "types": dict(Counter(p["type"] or "untyped" for p in pages.values()))}
