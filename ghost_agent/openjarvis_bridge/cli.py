"""Entry points for upstream-backed Ghost and qualification commands."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .runtime import IntegrationError, doctor, initialize_process, project_root, require_upstream


def main():
    """Dedicated launcher: existing ghost remains a legacy compatibility route."""
    initialize_process(Path.cwd())
    os.environ["GHOST_INFERENCE_BACKEND"] = "openjarvis"
    from ..cli import main as ghost_main
    try:
        ghost_main()
    except IntegrationError as exc:
        raise SystemExit(str(exc)) from exc


def add_parser(sub):
    parser = sub.add_parser("openjarvis", help="Qualify and use the pinned upstream integration")
    actions = parser.add_subparsers(dest="oj_action", required=True)
    health = actions.add_parser("doctor", help="Report dependency/engine status without provider discovery")
    health.add_argument("--probe", action="store_true", help="Contact only the configured inference endpoint")
    ask_parser = actions.add_parser("ask", help="Persistent read-only upstream assistant")
    ask_parser.add_argument("prompt", nargs="+")
    ask_parser.add_argument("--model", required=True)
    ask_parser.add_argument("--memory", action="store_true", help="Allow search of explicitly imported documents")
    ask_parser.add_argument("--max-calls", type=int, default=6)
    for action in ("index", "forget"):
        item = actions.add_parser(action, help=f"{action.capitalize()} one project document in upstream memory")
        item.add_argument("path")
    search = actions.add_parser("search", help="Search explicitly imported documents")
    search.add_argument("query")
    telegram = actions.add_parser("telegram", help="Pair/revoke/serve Telegram or check credentials")
    telegram.add_argument("action", choices=["pair", "revoke", "status", "serve", "relay",
                                              "rotate-token-check", "live-check", "receipts"])
    telegram.add_argument("--user-id", default="")
    telegram.add_argument("--chat-id", default="")
    telegram.add_argument("--model", default="")
    telegram.add_argument("--memory", action="store_true", help="Permit delivery of imported document excerpts to your paired Telegram chat")
    telegram.add_argument("--allow-sandbox-builds", action="store_true",
                          help="Queue explicit build/create/implement requests for Ghost's sandbox worker")
    telegram.add_argument("--run-worker", action="store_true",
                          help="Run Ghost's existing bounded worker alongside the Telegram transport")
    telegram.add_argument("--status", default="", help="[receipts] filter receipts by status")
    telegram.add_argument("--host", default="ghostpi", help="[relay] SSH host running ghost-bot")
    return parser


def dispatch(args):
    root = project_root()
    initialize_process(root)
    if args.oj_action == "doctor":
        result = doctor(root, probe=args.probe)
        print(json.dumps(result, indent=2))
        if "error" in result or (args.probe and not result.get("healthy")):
            raise SystemExit(1)
        return
    require_upstream()
    if args.oj_action == "telegram":
        from .telegram import (live_check, list_receipts, pair, pairing, revoke, serve,
                               token_health, check_token_rotation,
                               record_token_fingerprint)
        from ..integrations.telegram import token_value
        if args.action == "pair":
            result = pair(root, args.user_id, args.chat_id)
        elif args.action == "revoke":
            result = revoke(root)
        elif args.action == "status":
            result = {**pairing(root), **token_health(root)}
        elif args.action == "rotate-token-check":
            token = token_value()
            if not token:
                raise IntegrationError("Set the NEW TELEGRAM_BOT_TOKEN in the environment, then rerun rotate-token-check.")
            result = check_token_rotation(token, root)
            if result.get("well_formed") and result.get("changed"):
                result["recorded"] = record_token_fingerprint(token, root)["fingerprint"]
        elif args.action == "live-check":
            if not args.chat_id:
                raise IntegrationError("live-check requires --chat-id (the paired owner chat).")
            result = live_check(root, args.chat_id)
        elif args.action == "receipts":
            result = list_receipts(root, status=args.status)
        elif args.action == "relay":
            if not args.model:
                raise IntegrationError("Telegram relay requires --model.")
            from .telegram import relay_serve
            relay_serve(root, args.model, host=args.host, memory=args.memory,
                        allow_sandbox_builds=args.allow_sandbox_builds, run_worker=args.run_worker)
            return
        else:
            if not args.model:
                raise IntegrationError("Telegram serve requires --model.")
            serve(root, args.model, memory=args.memory,
                  allow_sandbox_builds=args.allow_sandbox_builds,
                  run_worker=args.run_worker)
            return
    elif args.oj_action == "ask":
        from .assistant import ask
        result = ask(root, " ".join(args.prompt), args.model, memory=args.memory, max_calls=args.max_calls)
    else:
        from .memory import ProjectMemory
        with ProjectMemory(root) as memory:
            if args.oj_action == "index":
                result = memory.index(args.path)
            elif args.oj_action == "forget":
                result = memory.forget(args.path)
            else:
                result = memory.search(args.query)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
