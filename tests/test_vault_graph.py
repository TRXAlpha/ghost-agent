"""Ghost reads the owner's Obsidian vault as a graph (read-only, graph-first retrieval)."""
import json

from ghost_agent import vault

PAGES = {
    "projects/ghost-hub-bot.md": "---\ntype: project\ntitle: 'ghost-hub bot'\ntags:\n  - ghostpi\n---\n"
                                 "# ghost-hub bot\nTelegram front end on [[hosts/ghostpi|ghostpi]]. Uses [[entities/telegram]].\n",
    "hosts/ghostpi.md": "---\ntype: host\ntitle: ghostpi\n---\nRaspberry Pi hub. Runs AdGuard and SearXNG.\n",
    "entities/telegram.md": "---\ntype: entity\ntitle: Telegram\n---\nMessaging. token 123456789:" + "A" * 30 + "\n",
    "projects/ignis.md": "---\ntype: project\ntitle: ignis\n---\nNASA fire model.\n",
    "maps/Projects.md": "---\ntype: map\ntitle: Project catalog\n---\n[[projects/ignis]] [[projects/ghost-hub-bot]]\n",
    "index.md": "---\ntype: meta\ntitle: Wiki Index\n---\n[[projects/ignis]] telegram telegram telegram\n",
}


def make_vault(tmp_path, monkeypatch):
    for rel, text in PAGES.items():
        path = tmp_path / "vault" / "wiki" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("GHOST_VAULT", str(tmp_path / "vault"))


def test_graph_seed_then_neighbours_and_redaction(tmp_path, monkeypatch):
    make_vault(tmp_path, monkeypatch)
    graph = vault.build_graph()
    assert vault.stats(graph)["pages"] == 6
    hits = vault.query(graph, "where does the telegram bot run")
    pages = [(h["page"], h["via"]) for h in hits]
    assert {("projects/ghost-hub-bot", "seed"), ("entities/telegram", "seed")} <= set(pages)
    assert ("hosts/ghostpi", "linked") in pages  # reached through the wikilink, not by words
    assert all(p not in {"index", "maps/Projects"} for p, _ in pages)  # navigation pages excluded
    telegram = next(h for h in hits if h["page"] == "entities/telegram")
    assert "[redacted]" in telegram["excerpt"] and "AAAA" not in telegram["excerpt"]


def test_cache_and_missing_vault(tmp_path, monkeypatch):
    make_vault(tmp_path, monkeypatch)
    cache = tmp_path / "cache.json"
    first = vault.build_graph(cache=cache)
    assert json.loads(cache.read_text())["signature"] == first["signature"]
    assert vault.build_graph(cache=cache)["built_at"] == first["built_at"]  # served from cache
    monkeypatch.setenv("GHOST_VAULT", str(tmp_path / "none"))
    assert vault.context_for(tmp_path, "telegram") == []


def test_proactive_tick_sees_vault_pages(tmp_path, monkeypatch):
    from ghost_agent import owner
    from ghost_agent.proactive import ProactiveEngine
    make_vault(tmp_path, monkeypatch)
    owner.tell(tmp_path, "I got a new telegram bot phone")
    seen = []
    ProactiveEngine(tmp_path, "tiny", chat_fn=lambda m, msgs, **k: seen.append(json.loads(msgs[1]["content"])) or json.dumps({
        "title": "t", "goal": "research the telegram phone", "rationale": "r", "kind": "research",
        "confidence": 0.5, "requires_user_input": False})).tick(auto_queue=False)
    assert any(p["page"] == "entities/telegram" for p in seen[0]["vault"])
