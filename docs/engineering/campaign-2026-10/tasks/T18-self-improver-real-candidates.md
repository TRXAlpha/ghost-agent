# T18: Make `SelfImprover` produce real sandboxed candidates

Size: M · Wave 7 (after T06, T07, T16, T17) · Important: two reviewers
Files: `ghost_agent/self_improver.py`, `ghost_agent/proactive.py` (one line),
`ghost_agent/cli.py` (`self-improve` parser and handler),
`tests/test_ai_os_subsystems.py` (`TestSelfImprover` only), new
`tests/test_self_improver_pipeline.py`

## Objective

Today `SelfImprover` (`ghost_agent/self_improver.py`):

- `propose_from_signatures` fills a fixed sentence template; it never creates a
  change;
- `evaluate_candidate` runs `python -m pytest` **on the live project root**,
  not on any candidate;
- `review_and_decide` calls itself a "model review" but is an if/else that can
  return `"approved"` without any code having changed;
- the `model` argument is unused.

Meanwhile Ghost already has a strong, reviewed candidate pipeline:
`self_repair.SelfRepairManager` (`ghost self-repair propose/verify/request-promotion`).
It hashes candidate bytes, binds evaluator evidence to them, and promotes only
through owner-approved `ghost promote`.

Make `SelfImprover` the **front end** that turns recurring failures into real
work, and hand the results to `SelfRepairManager`. Do not build a second
evidence or promotion system.

## Pipeline (decided)

```text
trace-learning signature (count >= 2)
  -> SelfImprover.propose(...)            creates candidate, status "queued"
       -> queue_sandbox_outcome(root, goal, model, source="self_improver",
                                priority=30, workspace_mode="project")
  (worker runs the job normally: research/sandbox/tests/repair)
  -> SelfImprover.sync()                  for each "queued" candidate:
       job not terminal          -> unchanged
       job.result == tests_passed and sandbox exists
                                 -> copy sandbox to <root>/workspaces/self-improve/<candidate_id>/
                                 -> SelfRepairManager(root).propose(diagnosis=hypothesis,
                                        sandbox=<copy>, proposed_benefit=..., linked_failure=signature_key)
                                 -> candidate status "handed_to_self_repair", store repair_proposal_id
       otherwise                 -> candidate status "failed", failure_reason = job.result or job.status
  owner then uses the existing commands:
       ghost self-repair verify <repair_id> --command "python -m pytest -q"
       ghost self-repair request-promotion <repair_id> --goal-id ...
```

Why copy? `SelfRepairManager._assert_candidate_scope` refuses candidates inside
`.ghost/` (a protected root), and job sandboxes live under
`.ghost/outcomes/.../run/sandbox`. `workspaces/` is inside the project, is not
protected, and is already gitignored.

## Exact details

### `ImprovementCandidate` (dataclass)

Add three fields **at the end**, with defaults, so old records still load:

```python
job_id: str = ""
signature_key: str = ""
repair_proposal_id: str = ""
```

Update the `status` comment to:
`# queued | handed_to_self_repair | failed | (legacy: proposed | evaluating | shadow | active | rolled_back)`.

Also check how `SelfImproverStore` loads records (`ImprovementCandidate(**item)`
or similar). If it would fail on records **without** the new keys, stop and
report. Dataclass defaults normally handle this.

### `signature_key`

`f"{sig['error_type']}|{sig['subsystem']}|{sig.get('normalized_error', '')[:120]}"`.
The signature dicts come from `FailureSignature.to_dict()` in
`trace_learning.py` (verified at a602677). The keys are `error_type`,
`normalized_error`, `subsystem`, `count`, `sample_error` (already truncated to
200 characters) and `sample_job_id`. Use `sample_error` as the sample text.

### `SelfImprover.propose(self, signatures: list[dict], model: str) -> Optional[ImprovementCandidate]`

Replaces `propose_from_signatures`. Delete the old method and update its
callers (only `cli.py` and tests). Behaviour:

1. A blank `model` raises `ValueError("A model is required to propose improvements")`.
2. Consider signatures in the given order. Skip any with `count < 2`. Skip any
   whose `signature_key` already belongs to a candidate with status `queued` or
   `handed_to_self_repair` (no duplicate open work). Pick the **first**
   remaining one. At most **one** candidate is created per call. If none
   remain, return `None`.
3. Goal text (exact template):
   ```text
   Improve Ghost to reduce the recurring failure "{error_type}" in {subsystem} (seen {count} times). Example: {sample}. Make the smallest code change under ghost_agent/ that addresses it, add a focused pytest regression test under tests/, and keep the existing tests passing.
   ```
   `sample` is `sig.get("sample_error") or sig.get("normalized_error", "")`,
   truncated to 200 characters and with newlines replaced by spaces. If the full goal is
   longer than 600 characters (the `queue_sandbox_outcome` limit), truncate
   `sample` further until it fits.
4. Create the candidate (`store.create_candidate`) with the hypothesis set to
   the goal's first sentence, `components=[subsystem]`,
   `baseline={"failure_count": count}`,
   `plan="Sandbox job via worker; verification via self-repair evaluator"`,
   `budget_hours=1.0`,
   `rollback="ghost promote rollback of the promotion, if ever applied"`.
5. Call `queue_sandbox_outcome(self.root, goal, model, source="self_improver", priority=30, workspace_mode="project")`.
   - On `ProactiveError` (for example a blocked marker in the error text),
     call `store.fail(candidate_id, f"queue refused: {exc}")` and return the
     failed candidate.
   - On success, `store.update_candidate(candidate_id, {"status": "queued", "job_id": job.job_id, "signature_key": key})`.
6. Return the candidate.

### `SelfImprover.sync(self) -> list[ImprovementCandidate]`

For each candidate with status `queued`:

- Load the job with `OutcomeStore(self.root).load(job_id)`. If it is missing,
  fail the candidate with `"job record missing"`.
- Terminal means `job.status` is in `{"completed", "failed", "needs_review", "cancelled"}`.
  This is the set `feedback_outcome_for_job` in `proactive.py` uses
  (`completed`, `failed`, `needs_review`), plus `cancelled` as a defensive
  extra (emergency-stop paths in `_execute_queued_outcome` set
  `job.result = "cancelled"`). Define it as a module constant
  `_TERMINAL_JOB_STATUSES` in `self_improver.py`.
- If the job is not terminal, leave the candidate unchanged.
- If `job.result == "tests_passed"` and `Path(job.sandbox_path).is_dir()`:
  copy the sandbox to `self.root / "workspaces" / "self-improve" / candidate_id`
  with
  `shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))`.
  If `dst` already exists, fail the candidate with
  `"handoff directory already exists"` (never overwrite). Then call
  `SelfRepairManager(self.root).propose(diagnosis=candidate.hypothesis, sandbox=dst, proposed_benefit=f"Targets {count} recurrences of {error_type}", linked_failure=candidate.signature_key)`.
  On `ValueError` (which includes `SelfRepairError`), fail the candidate with
  `f"self-repair refused: {exc}"`. On success, update the candidate to
  `{"status": "handed_to_self_repair", "repair_proposal_id": proposal.proposal_id}`.
- Otherwise, fail the candidate with
  `f"job ended {job.result or job.status}"`.

Return the list of candidates whose status changed.

### Delete

- `SelfImprover.evaluate_candidate` and `SelfImprover.review_and_decide`
  (fake evaluation and fake model review). Check their callers first
  (`git grep -n "evaluate_candidate\|review_and_decide"`); only tests should
  use them.
- The `import subprocess` in `self_improver.py`, if it is no longer used.
- Keep `SelfImproverStore` methods as they are (they are storage).

### `proactive.py`

Add `"self_improver"` to the allowed-source set in `queue_sandbox_outcome`. If
T11 is merged, the set is
`{"proactive", "telegram", "capability_improver", "benchmark", "self_improver"}`.

### `cli.py`

- `self-improve propose`: add `--model` (default `None`, resolved with
  `_resolve_model`; a missing model prints
  `"A model is required: pass --model or configure one."`). Run
  `run_trace_learning(root, min_count=2)`, take
  `summary["analysis"]["top_signatures"]`, and call
  `SelfImprover(root).propose(sigs, model)`. Print the candidate id, status and
  job id, or `"No new recurring failure to work on."`.
- Add a `self-improve sync` sub-command: call `SelfImprover(root).sync()` and
  print each changed candidate's id, new status, `repair_proposal_id` and
  failure reason. If one was handed off, also print
  `"Next: ghost self-repair verify <repair_id> --command \"python -m pytest -q\""`.
- `self-improve list` and `decide`: leave them as they are.
- `SelfImprover.__init__`: remove the default model (`"qwen2.5-coder:1.5b"`)
  and the `model` parameter, if T18's API no longer needs them (the model is
  passed to `propose`). Update the CLI accordingly.
- Run `"$PY" -m py_compile ghost_agent/cli.py ghost_agent/self_improver.py`.

## Tests

### Update `tests/test_ai_os_subsystems.py::TestSelfImprover`

Tests in this class that exercise `propose_from_signatures`,
`evaluate_candidate` or `review_and_decide` test **deleted, fake behaviour**.
Delete exactly those tests. Keep the tests of `SelfImproverStore`. List every
deleted test by name in your report. **This is the only permitted test
deletion in this task.**

### New: `tests/test_self_improver_pipeline.py`

Use `tmp_path` as the project root. Build signatures as plain dicts with the
key names you confirmed in `trace_learning.py`.

1. `test_propose_queues_project_mode_job`: one signature with count 3 →
   candidate status `queued`, a non-empty `job_id`, and the job's contract has
   `workspace_mode == "project"` and `source == "self_improver"`. The scheduler
   has the job.
2. `test_propose_skips_low_count_and_duplicates`: count 1 → `None`. The same
   signature twice → the second call returns `None` (open candidate exists).
3. `test_propose_requires_model`: `model=" "` → `ValueError`.
4. `test_propose_blocked_marker_fails_candidate`: the sample text contains
   `"deploy"` → candidate status `failed`, with `failure_reason` containing
   `"queue refused"`.
5. `test_goal_text_within_limit`: a 5,000-character sample → the queued goal
   is ≤ 600 characters.
6. `test_sync_hands_passing_job_to_self_repair`: after `propose`, simulate the
   worker by loading the job, creating a sandbox dir under
   `tmp_path/.ghost/outcomes/<job>/run/sandbox` with a file in it, and setting
   `status`/`result="tests_passed"`/`sandbox_path`, then saving it. Run `sync()`
   → status `handed_to_self_repair`, the copy exists under
   `workspaces/self-improve/<candidate_id>/` with the file,
   `SelfRepairManager(tmp_path).get(repair_proposal_id)` exists, and its
   `sandbox` is the copy (not the `.ghost` path).
7. `test_sync_failed_job_marks_failed`: `result="iteration_limit"` →
   `failed`, with `"iteration_limit"` in `failure_reason`. There is no
   self-repair record.
8. `test_sync_leaves_running_job`: a non-terminal status → unchanged.
9. `test_sync_refuses_existing_handoff_dir`: pre-create the handoff dir →
   `failed`, with `"already exists"`.
10. `test_old_candidate_records_still_load`: write a candidates JSON in the old
    format (no new keys) to the store's file path (read `SelfImproverStore` for
    the path), and assert `list_candidates()` works.
11. `test_no_live_tree_modification`: snapshot the names of files under
    `tmp_path` excluding `.ghost` and `workspaces` before propose+sync, and
    assert they are unchanged afterwards.

## Must NOT

- Run pytest or any command on the live project from `SelfImprover`.
- Add a new evidence, approval or promotion mechanism. Use `SelfRepairManager`
  as is, and do not edit `self_repair.py`.
- Auto-verify or auto-promote anything.
- Create more than one candidate per `propose` call.

## Verification

```bash
"$PY" -m py_compile ghost_agent/cli.py ghost_agent/self_improver.py ghost_agent/proactive.py
"$PY" -m pytest -q -p no:cacheprovider tests/test_self_improver_pipeline.py tests/test_ai_os_subsystems.py tests/test_self_repair.py tests/test_self_repair_oj15.py tests/test_proactive.py
"$PY" -m pytest -q -p no:cacheprovider
"$PY" -m ghost_agent.cli self-improve --help
```

## Acceptance criteria

- AC1: `propose` queues a real project-mode sandbox job through
  `queue_sandbox_outcome`, at most one per call, without duplicates.
- AC2: `sync` hands passing jobs to `SelfRepairManager` via a copy outside
  `.ghost`, and fails the others with reasons.
- AC3: The fake evaluation and fake model review are gone, and nothing runs
  tests on the live tree.
- AC4: Old candidate records load.
- AC5: The full suite passes.

## Owner live step (not the worker)

With Ollama running, in the real project:

```bash
ghost trace-learning run
ghost self-improve propose --model gemma4:26b
ghost worker --watch
ghost self-improve sync
ghost self-repair verify <repair_id> --command "python -m pytest -q"
```
