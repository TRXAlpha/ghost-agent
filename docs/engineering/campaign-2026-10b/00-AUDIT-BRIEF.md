# Campaign 2026-10b, phase A: requirement gap audit

You are a **read-only auditor** for the Ghost repo at
`C:\Users\chris\Documents\Ghost\ghost_agent` (branch `master`). The product
specification is `implementation.md`. Your card tells you which area (A1–A8)
you audit. Your job: find out, **with evidence**, what the code actually
does today for each requirement and scenario in your area, and what is
missing.

Your report is the input for the next build campaign. A wrong "implemented"
claim wastes days, so be strict.

## Hard rules

1. **Read-only.** Do not edit, create, delete, format, commit or merge
   anything, except your single report file (below). Do not run state-changing
   `ghost` commands. Do not touch `.ghost/`. Running tests and `--help` is fine.
2. **Evidence or it didn't happen.** Every "present" claim needs a
   `path:line` reference to the production code **and** to its caller on a real
   entry point (a CLI command in `ghost_agent/cli.py`, the worker,
   `OvernightRunner`, the API server, or the Telegram bridge). Code that only
   tests import is **test-only**, not present.
3. **Classify every item with exactly one status:**
   - `LIVE`: production code, reachable from a real entry point, with tests.
   - `PARTIAL`: some of the requirement exists and is wired; name exactly what is missing.
   - `TEST-ONLY`: code exists but no production caller.
   - `STUB`: placeholder/mock that says so (fine for external integrations).
   - `MISSING`: nothing.
   - `BLOCKED-EXTERNAL`: needs credentials, hardware, accounts or firmware the agents cannot obtain. Name the exact prerequisite.
4. Do not trust docs, comments, STATE.md, README or commit messages as evidence.
   They describe intentions. Only code and test runs count.
5. Do not propose rewrites for style. Gaps are things the spec requires that
   the code does not do.
6. **Output budget: each model response is capped at about 4,000 tokens,
   including thinking.** Never write a long file in one call. Write your report
   **one section at a time**: create the file with the header, then append one
   requirement section per call. Each append stays under about 60 lines. Keep
   tool outputs small: use `grep -n` and read line ranges, not whole large
   files.
7. You may use `delegate_task` for read-only sub-investigations (at most 3
   tasks per call). Give each subagent these same rules and a narrow question,
   and have it return a short answer (under 30 lines) for **you** to write into
   the report.

## Report file

Write exactly one file:
`docs/engineering/campaign-2026-10b/audit/<AREA>.md` (for example
`audit/A3.md`). Use this structure:

```markdown
# Audit <AREA>: <area title>
Auditor run: <date>, master <git rev-parse --short HEAD>

## <Rxx or Sxx id>: <short name>
| Spec point (short) | Status | Evidence (path:line; entry point) | Gap / what is missing |
|---|---|---|---|
| ... | LIVE/PARTIAL/... | ... | ... |

Proposed bounded tasks (only for PARTIAL/TEST-ONLY/MISSING items that agents can do without external access):
- <one line each: what to build, which files, which existing component to reuse>

## <next id> ...

## Summary
- Counts per status.
- Top 5 highest-value gaps for the owner, one line each.
- External blockers with their exact prerequisite.
```

Cover **every** bullet of every requirement and scenario in your area: read
those sections of `implementation.md` first. Bundle trivially related bullets
into one row where sensible, but don't skip any.

## How to check wiring quickly

- Find the definitions: `git grep -n "class Foo\|def foo" ghost_agent`.
- Find the callers: `git grep -n "foo(" ghost_agent | grep -v "def foo"`.
- Check whether `cli.py` exposes it: `git grep -n "foo" ghost_agent/cli.py`.
- Run the relevant tests:
  `C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>.py`.
- Check the CLI: `C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe -m ghost_agent.cli <cmd> --help`.

## Areas

| Area | Spec sections | Start with these modules (not exhaustive) |
|---|---|---|
| A1 | R01 durable state/events/recovery; R12 background operation and resources | runtime.py (Scheduler, EventBus, Doctor), worker.py, overnight.py, daemon.py, command_budget.py, utils/storage.py |
| A2 | R02 memory and correction; R10 learning/study; S04; S05 | context_manager.py, memory/store.py, knowledge.py, knowledge_base.py, learner.py, learning_engine.py, context_synthesizer.py, research.py, openjarvis_bridge/memory.py |
| A3 | R03 initiative/opportunities; S06 idle/overnight; S07 unfamiliar context | proactive.py, overnight.py, digest.py, daemon.py, autonomy.py, intent.py, kernel.py (GoalStore) |
| A4 | R04 permissions/owner control; R14 control plane | kernel.py, approvals.py, security.py, secrets.py, control_plane.py, api_server.py, dashboard.py, openjarvis_bridge/authority.py, runtime.py (EmergencyStop) |
| A5 | R05 models/inexpensive execution; R06 research/feasibility | models/*.py, openjarvis_bridge/runtime.py, openjarvis_bridge/assistant.py, openjarvis_bridge/spend.py, research.py, provenance.py, tools/web_tools.py, protocol_benchmark.py, benchmark.py |
| A6 | R07 isolated execution/acceptance; R13 self-improvement; S08 | orchestrator.py, openjarvis_bridge/isolated_execution.py, evaluation.py, test_discovery.py, self_repair.py, self_improver.py, promotion.py, trace_learning.py, tools/cmd_tools.py, tools/process_runner.py |
| A7 | R08 integration factory; R09 messaging/attention/focus; S01 Telegram | capability_acquisition.py, autonomy_improver.py, extensions.py, mcp_addons.py, integrations/*.py, openjarvis_bridge/telegram.py, checkins.py, runtime.py (AttentionManager) |
| A8 | R11 devices/health; S02 Huawei; S03 DeskSentinel | devices.py, huawei_route.py, integrations/huawei.py, integrations/desksentinel.py, perception.py, context_manager.py (health privacy) |

## Finish

When the report file is complete, check it has every section and the summary,
then call `kanban_complete`. The summary should be the report's Summary
section, under 25 lines. If you cannot finish, call `kanban_block` with what
is missing.
