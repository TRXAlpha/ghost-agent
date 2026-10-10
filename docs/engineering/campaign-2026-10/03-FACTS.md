# Verified facts (checked 2026-10-06 at commit a602677)

Use these facts instead of guessing. If something here turns out to be wrong,
say so in your report.

## 1. Python interpreter and running tests

- Worktrees have **no `.venv` of their own**. Use the main checkout's
  interpreter by absolute path:
  ```bash
  PY="C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe"
  ```
- **Always run from your worktree root, and always use `"$PY" -m ...`.**
  `python -m` puts the current directory first on `sys.path`, so your
  worktree's `ghost_agent` package is imported instead of the main checkout's
  editable install. Check this once per session:
  ```bash
  "$PY" -c "import ghost_agent, os; print(os.path.dirname(ghost_agent.__file__))"
  ```
  The printed path must be inside your worktree. If it is not, stop and report.
- **Run a focused test file:**
  ```bash
  "$PY" -m pytest -q -p no:cacheprovider tests/test_proactive.py
  ```
- **Run the full suite** (no deselects: the old hanging overnight test was fixed by T02 and passes):
  ```bash
  "$PY" -m pytest -q -p no:cacheprovider
  ```
  Never use `--deselect` unless a task file explicitly says so. As of 2026-10-10 master runs ~1300
  tests in about 3 minutes. If the full suite takes more than 10 minutes it is hanging: stop it and
  report.
- `pytest-timeout` is **not installed**. Do not pass `--timeout`.
- `--timeout`-style protection comes from T02's `faulthandler_timeout` once
  merged. That setting only dumps stack traces; it does not kill the run.
- Tests in `tests/test_openjarvis_*.py` need the separate OpenJarvis venv
  (`workspaces/openjarvis-venv`). In the main venv they skip or pass without it.
  Do not try to install OpenJarvis.
- Some tests are Windows-specific (job objects, `GetLastInputInfo`). This
  machine is Windows 11, so they run.

## 2. Commands

- CLI entry point: `"$PY" -m ghost_agent.cli <command>` (the `ghost` script is
  the same thing).
- Syntax check: `"$PY" -m py_compile ghost_agent/<file>.py`.
- Import check for the whole package:
  `"$PY" -c "import ghost_agent.cli"`.
- Never run CLI commands that change state (`accomplish`, `worker`,
  `proactive`, `daemon run`, `overnight run`, `self-improve propose`, and so on)
  in the main checkout. Run them only inside a fresh temporary directory, and
  only when your task says to.

## 3. Repository map (only the parts you are likely to need)

| Path | What it is |
|---|---|
| `ghost_agent/cli.py` (~3800 lines) | All CLI commands. The parser is built in `main()` around lines 1560–1900. A dict near line 1880–1905 maps command names to `_cmd_*` handlers. Handlers are further down. |
| `ghost_agent/orchestrator.py` | The coding state machine: `Orchestrator._run_loop` (phases INGEST → PLAN → IMPLEMENT → VERIFY ↔ REPAIR → DONE). |
| `ghost_agent/autonomy.py` | `OutcomeJob` (persistent job record), `OutcomeStore` (`make_task_payload` builds the task given to the orchestrator), `CapabilityRegistry` (gap registry, file `.ghost/capabilities.json`). |
| `ghost_agent/worker.py` | `OutcomeWorker.run_next`: claims one scheduled job and runs it. |
| `ghost_agent/cli.py::_execute_queued_outcome` (~line 2925) | Real production path that runs a queued job: research → `Orchestrator.run_task_with_roots(task, project_root, run_dir)`. |
| `ghost_agent/proactive.py` | `ProactiveEngine.tick` (one model call → one opportunity), `queue_sandbox_outcome` (the only hand-off from proactive/Telegram/capability-improver into the worker), `idle_seconds()`. |
| `ghost_agent/overnight.py` | `OvernightRunner`: the existing idle-aware loop (proactive tick + worker + digest). This is **the** loop. Do not build another. |
| `ghost_agent/runtime.py` | `EmergencyStop`, `EventBus`/`Event`, `Scheduler`/`ScheduledJob`, `Doctor`, `AttentionManager`/`Notification` (the real notification store, `.ghost/notifications.json`). |
| `ghost_agent/trace_learning.py` | `run_trace_learning(root, min_count=...)`: mines failed outcomes into failure signatures and capability gaps. |
| `ghost_agent/autonomy_improver.py` | `CapabilityImprover`: queues sandbox work for gaps seen at least twice. |
| `ghost_agent/capability_acquisition.py` | The **real** capability lifecycle (used by `ghost capability`). |
| `ghost_agent/context_manager.py` | `ValidatedMemoryStore` (the **real** memory authority, with retract/purge) and `ContextAssembler`. |
| `ghost_agent/learner.py` | `LearnerStore` + `RustCompileOracle` (used by `ghost learn`). |
| `ghost_agent/self_improver.py` | `SelfImprover` (T18 rewrites its core). |
| `ghost_agent/tools/cmd_tools.py` | Command allowlist. `python` / `pytest` resolve to `sys.executable`. |
| `ghost_agent/utils/storage.py` | `atomic_json(path, data)`, `exclusive_lock(path)`. Use these for `.ghost` state. |
| `tests/conftest.py` | Shared fixtures. Read it before writing tests. |

## 4. How a queued job actually runs (production path)

```text
queue_sandbox_outcome(root, goal, model, source=...)       proactive.py:220
  -> OutcomeStore.create + acceptance_contract              autonomy.py
  -> Scheduler.enqueue(ScheduledJob(...))                    runtime.py
OutcomeWorker.run_next(executor)                             worker.py
  -> _execute_queued_outcome(project_root, scheduled)       cli.py ~2925
       research (unless skip_research)
       payload = OutcomeStore.make_task_payload(job, research, job.iterations)
       Orchestrator(project_root, model=...).run_task_with_roots(task, project_root, run_dir)
            -> _create_sandbox(project_root, run_dir/"sandbox")   # copies the WHOLE project today
            -> _run_loop(...)
```

Facts about this path:

- `make_task_payload` copies only these acceptance-contract keys into the task
  constraints: `required_files`, `command_budget_seconds`,
  `command_budget_calls` and `command_timeout_seconds` (`autonomy.py` ~line 138).
- `Task.iteration_limit()` defaults to 8. `ghost accomplish` defaults to
  `--iterations 12`. Proactive jobs are hard-coded to 4 (`proactive.py` ~line
  252) and `ghost jobs add` defaults to 3.
- Today every phase transition increments `state["iteration"]`, including PLAN
  and VERIFY, which are not repair attempts. VERIFY does not call the model.
- VERIFY runs focused gates (touched test files), then the suite gate
  `python -m pytest -q` in the sandbox root. Because the sandbox is a copy of
  the Ghost repo, the suite gate currently runs Ghost's own ~1,000 tests.

## 5. Real failure evidence (`.ghost/outcomes/`, 18 jobs)

- 14 ended with `iteration_limit`, 2 `failed`, 2 `tests_passed`.
- Observed model behaviour: it wrote a placeholder file body `# Updated content`;
  it returned an empty response; it ran `web_search` for a trivial task; it read
  Ghost's own `schema.py` for a `hello.py` task.
- Observed verification output shows `[  8%]` progress lines, which means the
  suite gate was running the whole Ghost test suite.

## 6. Scripted model pattern (copy this in tests)

From `tests/test_deliverable_contracts.py`:

```python
import json
import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator

def test_example(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "example", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 3}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        actions = ([{"tool": "write_file", "path": "plan.md", "content": "Implement"}]
                   if planning else
                   [{"tool": "write_file", "path": "app.py", "content": "answer = 42\n"},
                    {"tool": "write_file", "path": "test_app.py",
                     "content": "from app import answer\ndef test_answer():\n    assert answer == 42\n"}])
        return json.dumps({"thought": "implement", "actions": actions})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "example" / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
```

`run_task` uses `tmp_path` as the project root and writes state to
`tmp_path/workspaces/<id>/state.json`. To exercise `run_task_with_roots`, see
`tests/test_autonomy.py` and `tests/test_resume_validation.py`.

## 7. Invariants (from AGENTS.md; never break them)

- Ghost owns goals, policy, approvals, verification, evidence and promotion.
- **Do not create competing schedulers, loops, authority systems, memory
  authorities or control planes.** There is one job queue (`Scheduler`), one
  worker (`OutcomeWorker`), one idle loop (`OvernightRunner`), one memory
  authority (`ValidatedMemoryStore`), one notification store
  (`AttentionManager`), and one capability lifecycle (`CapabilityAcquisition`
  plus the `CapabilityRegistry` gap list).
- Sandbox work never changes the live project. Live changes only happen through
  `ghost promote` with owner approval.
- Telegram, Huawei and DeskSentinel stay placeholders. Never fake a live
  integration.

## 8. Git

- The main checkout has one owner-dirty file: `AGENTS.md` (owner's; never
  touch it). The owner's uncommitted `cli.py` edit was moved to
  `C:/Users/chris/Documents/Ghost/owner-cli-uncommitted-2026-10-06.patch`
  (T01 applies it).
- Hermes subagents run in `<repo>/.worktrees/subagent-<id>` on branch
  `hermes-subagent/subagent-<id>`, branched from master's HEAD at spawn time.
- Existing worktrees `../ghost_oj07`, `../ghost_oj08`, `../ghost_oj10` and
  `../ghost_oj12` hold old OJ branches. Do not edit them. `oj07-recurring` and
  `oj10-acquisition` are already in master (same patches). `oj08-retry` has one
  unmerged commit, `7c1a1f0` (T04).
