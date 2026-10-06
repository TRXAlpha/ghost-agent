# T11: `ghost bench`, a fixed benchmark of non-toy goals with hidden acceptance tests

Size: M · Wave 5 (after T06–T09) · Important: two reviewers
Files: new `ghost_agent/benchmark.py`, new `ghost_agent/benchmark_goals.py`,
`ghost_agent/proactive.py` (one line), `ghost_agent/cli.py`, new
`tests/test_benchmark.py`

## Objective (owner-visible)

Today there is no honest measure of whether Ghost can complete real work. Its
only successes are "write add(a, b)". A job's own passing tests also prove
little, because the model writes both the code and the tests.

Add `ghost bench`. It runs 8 fixed, small but real goals through the **real
production job path** (queue → worker → orchestrator), then runs a **hidden
acceptance test** that the model never saw against each sandbox. It reports two
numbers per run:

- **ghost_pass_rate**: jobs that ended `tests_passed` (Ghost's own gate);
- **accepted_rate**: jobs whose sandbox passes the hidden test (independent
  acceptance).

The live run needs Ollama and is done by the owner. **Workers must never claim
live numbers.** Unit tests use a fake executor.

## Design (decided)

- **Isolated state:** each run uses its own project root,
  `<project>/.ghost/bench/<run_id>/`, where
  `run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]`. All jobs,
  scheduler, goals and outcomes for the run live under
  `<run_root>/.ghost/...`. The owner's real queue is never touched.
- **Production path:** for each goal:
  1. `job = queue_sandbox_outcome(run_root, goal_text, model, source="benchmark", priority=50, workspace_mode="empty")`
  2. Reload the job with `OutcomeStore(run_root).load(job.job_id)`, then set
     `job.skip_research = True`,
     `job.acceptance_contract["required_files"] = [goal.module_file]` and
     `job.iterations = iterations` (CLI argument, default 10). Save it with
     `OutcomeStore(run_root).save(job)`.

     **Check first:** read `cli.py::_execute_queued_outcome` and confirm that it
     uses `job.iterations` and `job.skip_research` from the stored job. If it
     reads the iteration budget from the `ScheduledJob.budget` instead, stop and
     report.
  3. `result = OutcomeWorker(run_root).run_next(executor, job_id=job.job_id)`.
     `executor` is injected (see the CLI section).
  4. Reload the job and read `job.status`, `job.result` and `job.sandbox_path`.
  5. **Hidden acceptance:** if `sandbox_path` is set and exists, copy the
     sandbox to `<run_root>/acceptance/<goal_id>/` with
     `shutil.copytree(..., ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))`.
     Write the goal's hidden test to `test_hidden_acceptance.py` in that copy,
     and run
     `[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden_acceptance.py"]`
     with `cwd` = the copy, `capture_output=True`, `text=True` and `timeout=120`.
     Accepted means `returncode == 0`. On `subprocess.TimeoutExpired`, the
     result is "not accepted", with detail `"hidden test timeout"`.
  6. Before each goal, check `EmergencyStop(project_root).active()` on the
     **owner's** project root (not `run_root`). If it is active, stop the run
     and mark the remaining goals `"skipped_emergency_stop"`.
- **Report:** write `<run_root>/report.json` with `atomic_json`:
  ```json
  {"run_id": "...", "model": "...", "iterations": 10, "started_at": 0.0, "finished_at": 0.0,
   "goals": [{"goal_id": "...", "job_id": "...", "status": "...", "result": "...",
              "tests_passed": true, "accepted": false, "acceptance_detail": "last 2000 chars of pytest output",
              "duration_seconds": 0.0}],
   "summary": {"total": 8, "tests_passed": 0, "accepted": 0,
               "ghost_pass_rate": 0.0, "accepted_rate": 0.0}}
  ```
  `tests_passed` is `job.result == "tests_passed"`. Rates are
  `count / total`, rounded to 3 places; `total` counts only goals that were
  attempted (not skipped). If total is 0, rates are 0.0.
- **The `benchmark` source:** in `proactive.py::queue_sandbox_outcome`, the
  allowed-source set
  `{"proactive", "telegram", "capability_improver"}` becomes
  `{"proactive", "telegram", "capability_improver", "benchmark"}`. That is the
  only change to `proactive.py`.
- **Security note** (put it in the module docstring): hidden tests execute
  model-generated code on the host as the current user, exactly like Ghost's
  normal verification. This is not containment.
- **Emergency stop limitation** (also in the docstring): emergency stop is
  checked between goals. Inside a job, the orchestrator polls `run_root`'s stop
  file, not the owner's.

## `ghost_agent/benchmark_goals.py` (copy exactly)

Define:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BenchGoal:
    goal_id: str
    module_file: str
    goal: str
    hidden_test: str
```

and `BENCH_GOALS: tuple[BenchGoal, ...]` containing these 8 goals, in this
order. Use the goal text and hidden test **exactly as written** (as Python
triple-quoted strings; use `textwrap.dedent` on the hidden tests).

**1. `roman`** — module_file `roman.py`

Goal: "Create roman.py with to_roman(n: int) -> str for 1 <= n <= 3999 and from_roman(s: str) -> int using standard subtractive notation (IV, IX, XL, XC, CD, CM). Both raise ValueError on out-of-range or invalid input (including empty strings and characters other than IVXLCDM). Add pytest tests in test_roman.py."

Hidden test:
```python
import pytest
from roman import to_roman, from_roman

def test_known_values():
    assert to_roman(1994) == "MCMXCIV"
    assert to_roman(3999) == "MMMCMXCIX"
    assert to_roman(4) == "IV"
    assert from_roman("MCMXCIV") == 1994
    assert from_roman("XL") == 40

def test_roundtrip():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n

@pytest.mark.parametrize("bad", [0, -1, 4000])
def test_to_roman_range(bad):
    with pytest.raises(ValueError):
        to_roman(bad)

@pytest.mark.parametrize("bad", ["", "ABC", "12"])
def test_from_roman_invalid(bad):
    with pytest.raises(ValueError):
        from_roman(bad)
```

**2. `wordfreq`** — module_file `wordfreq.py`

Goal: "Create wordfreq.py with top_words(text: str, n: int) -> list[tuple[str, int]]. Lowercase the text; a word is a maximal run of letters a-z; return the n most frequent (word, count) pairs sorted by count descending, then word ascending; n <= 0 returns an empty list. Add pytest tests in test_wordfreq.py."

Hidden test:
```python
from wordfreq import top_words

def test_counts_and_order():
    assert top_words("b a B c a b", 2) == [("b", 3), ("a", 2)]

def test_ties_alphabetical():
    assert top_words("y x", 5) == [("x", 1), ("y", 1)]

def test_punctuation_and_case():
    assert top_words("Hello, hello! HELLO?", 1) == [("hello", 3)]

def test_non_positive_n():
    assert top_words("a a", 0) == []
    assert top_words("a a", -3) == []

def test_empty_text():
    assert top_words("", 3) == []
```

**3. `rpn`** — module_file `rpn.py`

Goal: "Create rpn.py with evaluate(expr: str) -> float that evaluates a reverse Polish notation expression with whitespace-separated numbers and the operators + - * /. Raise ValueError for malformed expressions (too few operands, leftover operands, unknown tokens, empty input) and let division by zero raise ZeroDivisionError. Add pytest tests in test_rpn.py."

Hidden test:
```python
import pytest
from rpn import evaluate

def test_basic():
    assert evaluate("3 4 + 2 *") == 14
    assert evaluate("5 1 2 + 4 * + 3 -") == 14
    assert evaluate("2 3 /") == pytest.approx(2 / 3)
    assert evaluate("-2 3 *") == -6

@pytest.mark.parametrize("bad", ["", "1 +", "1 2", "1 x +", "+"])
def test_malformed(bad):
    with pytest.raises(ValueError):
        evaluate(bad)

def test_division_by_zero():
    with pytest.raises(ZeroDivisionError):
        evaluate("1 0 /")
```

**4. `lru`** — module_file `lru.py`

Goal: "Create lru.py with class LRUCache(capacity: int) providing get(key) (returns the value or None, and marks the key as most recently used) and put(key, value) (inserts or updates; when over capacity, evicts the least recently used key). capacity < 1 raises ValueError. Add pytest tests in test_lru.py."

Hidden test:
```python
import pytest
from lru import LRUCache

def test_eviction_order():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3

def test_update_refreshes():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    c.put("c", 3)
    assert c.get("a") == 10
    assert c.get("b") is None

def test_missing_key():
    assert LRUCache(1).get("x") is None

@pytest.mark.parametrize("bad", [0, -1])
def test_bad_capacity(bad):
    with pytest.raises(ValueError):
        LRUCache(bad)
```

**5. `csvstats`** — module_file `csvstats.py`

Goal: "Create csvstats.py with column_means(csv_text: str) -> dict[str, float]. The first row is the header. A column is numeric if every non-empty cell in it parses as float; empty cells are ignored; columns that are not numeric or have no non-empty cells are omitted. Use the csv module. Add pytest tests in test_csvstats.py."

Hidden test:
```python
from csvstats import column_means

def test_mixed_columns():
    text = "a,b,c\n1,x,2\n3,y,\n"
    assert column_means(text) == {"a": 2.0, "c": 2.0}

def test_all_empty_column_omitted():
    assert column_means("a,b\n1,\n2,\n") == {"a": 1.5}

def test_quoted_values():
    assert column_means('name,score\n"Smith, J",4\n"Lee",6\n') == {"score": 5.0}

def test_header_only():
    assert column_means("a,b\n") == {}
```

**6. `todo`** — module_file `todo.py`

Goal: "Create todo.py with class TodoStore(path) that persists to a JSON file at path. Methods: add(title: str) -> int returns a new id starting at 1 and increasing; items() -> list[dict] returns dicts with keys id, title, done in id order; complete(item_id: int) marks it done and raises KeyError for unknown ids. A new TodoStore on the same path must see earlier data; a missing file means an empty store. Add pytest tests in test_todo.py using tmp_path."

Hidden test:
```python
import pytest
from todo import TodoStore

def test_add_and_persist(tmp_path):
    path = tmp_path / "todo.json"
    s = TodoStore(path)
    assert s.add("milk") == 1
    assert s.add("eggs") == 2
    s.complete(1)
    again = TodoStore(path)
    assert again.items() == [
        {"id": 1, "title": "milk", "done": True},
        {"id": 2, "title": "eggs", "done": False},
    ]
    assert again.add("bread") == 3

def test_unknown_id(tmp_path):
    with pytest.raises(KeyError):
        TodoStore(tmp_path / "t.json").complete(99)

def test_missing_file_is_empty(tmp_path):
    assert TodoStore(tmp_path / "none.json").items() == []
```

**7. `tictactoe`** — module_file `tictactoe.py`

Goal: "Create tictactoe.py with winner(board: list[str]) -> str | None. board is exactly 3 strings of exactly 3 characters, each 'X', 'O' or '.'. Return 'X' or 'O' if that player has three in a row (row, column or diagonal), 'draw' if the board is full with no winner, otherwise None. Raise ValueError for any other shape or character. Add pytest tests in test_tictactoe.py."

Hidden test:
```python
import pytest
from tictactoe import winner

def test_row_col_diag():
    assert winner(["XXX", "OO.", "..."]) == "X"
    assert winner(["O..", "OX.", "O.X"]) == "O"
    assert winner(["X.O", ".XO", "..X"]) == "X"
    assert winner(["..O", ".O.", "O.X"]) == "O"

def test_draw_and_ongoing():
    assert winner(["XOX", "XOO", "OXX"]) == "draw"
    assert winner(["X..", "...", "..."]) is None

@pytest.mark.parametrize("bad", [["XXX", "OO."], ["XXXX", "...", "..."], ["XXA", "...", "..."]])
def test_invalid(bad):
    with pytest.raises(ValueError):
        winner(bad)
```

**8. `intervals`** — module_file `intervals.py`

Goal: "Create intervals.py with merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]] that returns the intervals sorted by start with overlapping or touching intervals merged ((1, 3) and (3, 5) become (1, 5)). Raise ValueError if any start > end. Do not modify the input list. Add pytest tests in test_intervals.py."

Hidden test:
```python
import pytest
from intervals import merge

def test_merge():
    assert merge([(5, 6), (1, 3), (2, 4)]) == [(1, 4), (5, 6)]
    assert merge([(1, 3), (3, 5)]) == [(1, 5)]
    assert merge([]) == []
    assert merge([(1, 10), (2, 3)]) == [(1, 10)]

def test_input_not_modified():
    data = [(3, 4), (1, 2)]
    merge(data)
    assert data == [(3, 4), (1, 2)]

def test_invalid():
    with pytest.raises(ValueError):
        merge([(5, 1)])
```

Before you finish, **prove every hidden test is solvable**. In a scratch
directory outside the repo, write a correct reference implementation for each
goal, run its hidden test, and confirm it passes. Report "8/8 hidden tests pass
against reference implementations". **Do not** commit the reference
implementations.

## `ghost_agent/benchmark.py`

Public API:

```python
def run_benchmark(project_root: Path, model: str, executor_factory: Callable[[Path], Callable[[ScheduledJob], WorkerResult]],
                  *, goal_ids: Optional[list[str]] = None, iterations: int = 10) -> dict: ...
def latest_report(project_root: Path) -> Optional[dict]: ...
def format_report(report: dict) -> str: ...
```

- `executor_factory(run_root)` returns the handler passed to `run_next`. This
  keeps `benchmark.py` independent of `cli.py`.
- Unknown `goal_ids` raise `ValueError` listing the valid ids. `iterations` must
  be an `int` from 1 to 50, otherwise `ValueError`. An empty or blank `model`
  raises `ValueError`.
- `latest_report` returns the `report.json` of the newest directory under
  `.ghost/bench/` that has one (sorted by directory name), or `None`.
- `format_report` returns a plain-text table:
  `goal_id  result  tests_passed  accepted  seconds`, then a summary line
  `ghost_pass_rate=0.375 accepted_rate=0.250 (8 goals, model=...)`.

## CLI (`ghost_agent/cli.py`)

Add a `bench` command with sub-actions, following how other multi-action
commands are built (`self-improve` is a good model: find its parser and its
entry in the command dict):

- `ghost bench list`: print each goal id and module file.
- `ghost bench run --model MODEL [--goal ID ...] [--iterations N]`: `--model`
  defaults to `_resolve_model(None)`; if that is empty, print
  `"A model is required: pass --model or configure one."` and return. Call
  `run_benchmark(Path.cwd(), model, lambda run_root: (lambda s: _execute_queued_outcome(run_root, s)), goal_ids=..., iterations=...)`,
  then print `format_report(report)` and the report path.
- `ghost bench report`: print `format_report(latest_report(Path.cwd()))`, or
  `"No benchmark runs yet."`.

Then run `"$PY" -m py_compile ghost_agent/cli.py`.

## Tests to add: `tests/test_benchmark.py`

Use a fake `executor_factory`. The fake handler receives a `ScheduledJob`,
loads the job from `OutcomeStore(run_root)`, creates a sandbox directory under
`run_root/.ghost/outcomes/...` (any path under `run_root`), writes files into
it, sets `job.sandbox_path`, `job.status` and `job.result`, saves the job, and
returns `WorkerResult(job.job_id, "completed")`. Read `worker.py` to see which
statuses `run_next` expects.

1. `test_goals_well_formed`: 8 goals, unique ids, every `module_file` ends with
   `.py`, every hidden test imports from its module, and every goal text
   mentions the module file.
2. `test_run_isolated_from_owner_queue`: run with the fake executor for goal
   `intervals` only. Assert that the owner root's `.ghost/scheduler.json` does
   not exist (or is unchanged if you pre-create it), and that the run root
   exists under `.ghost/bench/`.
3. `test_accepted_when_hidden_test_passes`: the fake writes a correct
   `intervals.py`. Assert `accepted is True` and `accepted_rate == 1.0`.
4. `test_not_accepted_when_hidden_test_fails`: the fake writes a wrong
   `intervals.py` but sets `result="tests_passed"`. Assert `tests_passed is True`
   and `accepted is False`. This proves independent acceptance.
5. `test_missing_sandbox_not_accepted`: the fake sets no `sandbox_path`.
   Assert `accepted is False`, and that the run does not crash.
6. `test_emergency_stop_skips_remaining`: engage `EmergencyStop(owner_root)`
   before the run (read `runtime.py` for `engage`), and run two goals. Assert
   both are `"skipped_emergency_stop"`, the fake was never called, and
   total == 0.
7. `test_invalid_arguments`: an unknown goal id, `iterations=0`, `iterations=51`
   and `model=" "` each raise `ValueError`.
8. `test_job_contract_set`: inside the fake, assert that the loaded job has
   `skip_research is True`, `acceptance_contract["workspace_mode"] == "empty"`,
   `acceptance_contract["required_files"] == ["intervals.py"]` and the
   requested `iterations`.
9. `test_latest_report_and_format`: after a run, `latest_report` returns it, and
   `format_report` contains `accepted_rate=`.
10. `test_queue_accepts_benchmark_source` (in `tests/test_proactive.py`):
    `queue_sandbox_outcome(tmp_path, "x task", "m", source="benchmark")`
    succeeds.

## Must NOT

- Touch the owner's queue or `.ghost` outside `.ghost/bench/`.
- Call a real model in tests, or claim live results.
- Weaken any check in `queue_sandbox_outcome` other than adding the source.

## Verification

```bash
"$PY" -m py_compile ghost_agent/cli.py ghost_agent/benchmark.py ghost_agent/benchmark_goals.py
"$PY" -m pytest -q -p no:cacheprovider tests/test_benchmark.py tests/test_proactive.py
"$PY" -m pytest -q -p no:cacheprovider
"$PY" -m ghost_agent.cli bench list
```

## Acceptance criteria

- AC1: The 8 goals and hidden tests exist verbatim, and each hidden test passes
  against a reference implementation (reported, not committed).
- AC2: Runs are fully isolated under `.ghost/bench/<run_id>/` and go through
  `queue_sandbox_outcome` → `OutcomeWorker.run_next` → injected executor.
- AC3: The report separates `tests_passed` from `accepted`.
- AC4: Emergency stop is respected between goals.
- AC5: `ghost bench list/run/report` exist.
- AC6: The full suite passes.

## Owner live step (not the worker)

```bash
ghost bench run --model gemma4:26b
```

Record the summary line in STATE.md. This is the first honest reliability
number for Ghost.
