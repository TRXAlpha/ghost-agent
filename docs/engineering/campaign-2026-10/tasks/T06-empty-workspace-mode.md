# T06: Empty-workspace mode for greenfield jobs

Size: M · Wave 3 (after T08, T09) · Important: two reviewers
Files: `ghost_agent/orchestrator.py`, `ghost_agent/autonomy.py`,
`ghost_agent/proactive.py`, `ghost_agent/autonomy_improver.py`,
`ghost_agent/cli.py`, new `tests/test_workspace_mode.py`

## Objective (owner-visible)

Today every job copies the **entire project directory** into its sandbox
(`Orchestrator._create_sandbox`, called from `run_task_with_roots`). When Ghost
runs from its own repo, a goal like "write hello.py with add(a, b) and a test"
gets a sandbox containing all of Ghost. The suite gate (`python -m pytest -q`)
then runs Ghost's ~1,000 tests, and the job fails or times out. This is the
main reason 16 of 18 real jobs failed.

After this task, a job can declare `workspace_mode = "empty"`. Its sandbox
starts as an empty directory, so the suite gate runs only the job's own tests.

- Proactive and Telegram jobs use `"empty"` (they are greenfield experiments).
- The capability improver keeps `"project"` (its work is about Ghost itself).
- `ghost accomplish` gets `--workspace {project,empty}`, default `project`
  (unchanged behaviour unless the owner asks).
- Jobs created before this change have no key and behave as `"project"`.

## Design (decided; do not change)

- The mode is stored in `OutcomeJob.acceptance_contract["workspace_mode"]`, so
  it is part of the immutable job contract (resume already refuses changed
  contracts).
- `make_task_payload` copies it into `task.constraints["workspace_mode"]`.
- The orchestrator reads `task.constraints.get("workspace_mode", "project")`.
  Any other value raises `ValueError("workspace_mode must be 'project' or 'empty'")`.
- Empty mode creates the sandbox directory with the **same target safety
  checks** as `_create_sandbox`, but copies nothing.
- Empty mode forces `_needs_existing_code_context` to return `False`, so no
  repository RAG, knowledge suggestions or memories about Ghost's code are
  injected.

## Steps

### A. `ghost_agent/orchestrator.py`

1. Read `_create_sandbox` (~line 368). Extract its first block, from
   `source = ...` through the `if target.exists(): raise FileExistsError(...)`
   check, into two pieces:
   - a new method `_validate_sandbox_target(self, target: Path) -> Path` that
     resolves `target`, performs the "escapes project" check and the "already
     exists" check, and returns the resolved target;
   - `_create_sandbox`, which keeps the source checks (`source == target`,
     `source.is_relative_to(target)`) and calls `_validate_sandbox_target`.

   Behaviour and error messages must stay byte-for-byte the same.
2. Add:
   ```python
   def _create_empty_sandbox(self, target: Path) -> Path:
       """Create a NEW empty workspace for greenfield work; nothing is copied."""
       target = self._validate_sandbox_target(target)
       target.mkdir(parents=True)
       return target
   ```
3. Add a module-level constant near the top of the class or module:
   ```python
   WORKSPACE_MODES = ("project", "empty")
   ```
   and a static method:
   ```python
   @staticmethod
   def _workspace_mode(task: Task) -> str:
       mode = task.constraints.get("workspace_mode", "project")
       if mode not in WORKSPACE_MODES:
           raise ValueError("workspace_mode must be 'project' or 'empty'")
       return mode
   ```
4. In `run_task_with_roots` (~line 237), replace
   `sandbox = self._create_sandbox(workspace_root, artifacts / "sandbox")` with:
   ```python
   if self._workspace_mode(task) == "empty":
       sandbox = self._create_empty_sandbox(artifacts / "sandbox")
   else:
       sandbox = self._create_sandbox(workspace_root, artifacts / "sandbox")
   ```
   Call `self._workspace_mode(task)` **before** `_init_artifacts_root`, so an
   invalid mode fails before any directory is created. Store it in a local
   variable and reuse it.
5. In `_needs_existing_code_context` (~line 765), add as the first line of the
   body:
   ```python
   if task.constraints.get("workspace_mode") == "empty":
       return False
   ```
6. Do **not** change `run_task`, `resume_task` or `resume_task_with_roots`.
   Resume reuses the preserved sandbox; an empty-mode sandbox simply has no
   project copy.

### B. `ghost_agent/autonomy.py`

7. In `OutcomeStore.make_task_payload`, add `"workspace_mode"` to the tuple of
   copied keys:
   `("required_files", "command_budget_seconds", "command_budget_calls", "command_timeout_seconds", "workspace_mode")`.

### C. `ghost_agent/proactive.py`

8. Add a keyword parameter to `queue_sandbox_outcome`:
   `workspace_mode: str = "empty"`. Validate it right after the existing
   `priority` check:
   ```python
   if workspace_mode not in {"project", "empty"}:
       raise ProactiveError("workspace_mode must be 'project' or 'empty'")
   ```
   Add `"workspace_mode": workspace_mode,` to the `job.acceptance_contract.update({...})`
   dict.
9. Do not change `ProactiveEngine._queue`. It relies on the default `"empty"`.

### D. `ghost_agent/autonomy_improver.py`

10. At the `queue_sandbox_outcome(` call (~line 84), add the keyword argument
    `workspace_mode="project"`. Do not change anything else.

### E. Telegram

11. `ghost_agent/openjarvis_bridge/telegram.py` line ~221 calls
    `queue_sandbox_outcome` and relies on the default (`"empty"`). **Do not
    edit this file.** Mention it in your report.

### F. `ghost_agent/cli.py` (`ghost accomplish`)

12. Next to the existing `--require-file` argument (~line 1673), add:
    ```python
    accomplish_parser.add_argument("--workspace", choices=("project", "empty"), default="project",
                                   help="Sandbox start: copy of this project (default) or an empty directory for greenfield work")
    ```
13. In the accomplish handler, find
    `job.acceptance_contract["required_files"] = list(required_files)`
    (~line 2730) and add on the next line:
    `job.acceptance_contract["workspace_mode"] = getattr(args, "workspace", "project")`
14. In the same handler's dry-run preview dict (the one containing
    `"required_files": required_files,` around line 2700), add:
    `"workspace_mode": getattr(args, "workspace", "project"),`
15. Find every other place that builds an `argparse.Namespace` for accomplish
    with explicit fields (`git grep -n "skip_research=False, dry_run=False"`;
    two around lines 2304 and 2334). Add `workspace="project"` to each so those
    code paths keep working.
16. Run `"$PY" -m py_compile ghost_agent/cli.py`.

## Tests to add: `tests/test_workspace_mode.py`

Use the scripted-model pattern from `03-FACTS.md` §6. For
`run_task_with_roots`, construct the task with
`Task(task_id, title, goal, constraints, context)` (read the dataclass at
`orchestrator.py` ~line 99 for the field order) and call
`agent.run_task_with_roots(task, workspace_root=project, artifacts_root=project / ".ghost" / "outcomes" / "job1")`.
Check `tests/test_resume_validation.py` for how existing tests choose
`artifacts_root` under the project root.

1. `test_empty_mode_sandbox_has_no_project_files`: create
   `project/existing_module.py` and `project/tests/test_existing.py` (a test that
   always **fails**, `assert False`). Run an empty-mode task whose scripted model
   writes `app.py` and `test_app.py` (passing). Assert that the sandbox does not
   contain `existing_module.py` or `tests/`, and that
   `state["last_result"] == "tests_passed"`. This proves the suite gate did not
   run the project's failing test.
2. `test_project_mode_still_copies_project`: same setup, `workspace_mode`
   absent. Assert that the sandbox contains `existing_module.py` and that the
   result is **not** `tests_passed` (the project's failing test runs). This
   proves the default is unchanged.
3. `test_invalid_workspace_mode_rejected_before_creating_dirs`:
   `workspace_mode="bogus"` raises `ValueError`, and the artifacts directory
   does not exist afterwards.
4. `test_empty_mode_refuses_existing_target`: pre-create the target sandbox
   directory, then call `_create_empty_sandbox` and expect `FileExistsError`.
5. `test_empty_mode_refuses_escape`: call `_create_empty_sandbox` with a target
   outside `repo_root` (`tmp_path.parent / "x"`) and expect `ValueError`.
6. `test_empty_mode_disables_existing_code_context`: a Task whose goal contains
   "fix" and has `workspace_mode="empty"` →
   `Orchestrator._needs_existing_code_context(task) is False`.
7. `test_make_task_payload_copies_workspace_mode`: an `OutcomeJob` with
   `acceptance_contract={"workspace_mode": "empty"}` →
   `payload["constraints"]["workspace_mode"] == "empty"`. Read
   `tests/test_autonomy_jobs.py` for how it builds payloads.
8. `test_proactive_queue_defaults_to_empty`: call
   `queue_sandbox_outcome(tmp_path, "build a small calculator", "scripted-model")`.
   Load the job (`OutcomeStore(tmp_path)`; find the right load method in
   `autonomy.py`) and assert `acceptance_contract["workspace_mode"] == "empty"`.
9. `test_queue_rejects_bad_workspace_mode`: `workspace_mode="x"` raises
   `ProactiveError`.
10. `test_capability_improver_queues_project_mode`: in
    `tests/test_autonomy_improver.py`, read how `try_build` is tested and copy
    that setup. Assert that the queued job's contract has
    `workspace_mode == "project"`.

## Must NOT

- Change the default for `ghost accomplish` (it stays `project`).
- Remove or loosen any check in `_create_sandbox`.
- Edit `openjarvis_bridge/telegram.py`.
- Change resume behaviour.

## Verification

```bash
"$PY" -m py_compile ghost_agent/orchestrator.py ghost_agent/cli.py
"$PY" -m pytest -q -p no:cacheprovider tests/test_workspace_mode.py tests/test_autonomy.py tests/test_autonomy_jobs.py tests/test_autonomy_improver.py tests/test_proactive.py tests/test_resume_validation.py tests/test_deliverable_contracts.py
"$PY" -m pytest -q -p no:cacheprovider
"$PY" -m ghost_agent.cli accomplish --help
```

## Acceptance criteria

- AC1: An empty-mode job's sandbox contains only what the model wrote, and its
  suite gate does not run project tests (test 1).
- AC2: The default `project` behaviour is unchanged (test 2 and existing
  tests).
- AC3: Proactive and Telegram jobs are empty-mode; capability-improver jobs are
  project-mode.
- AC4: `ghost accomplish --workspace empty` is available, and appears in the
  dry-run preview.
- AC5: An invalid mode fails closed before any directory is created.
- AC6: The full suite passes.
