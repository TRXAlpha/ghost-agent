# T09: Reject placeholder file content from the model

Size: S · Wave 1 · Files: `ghost_agent/orchestrator.py`, new
`tests/test_placeholder_guard.py`

## Objective

Real evidence: in job `outcome_20260928_193317_...` the model wrote
`hello.py` with the content `# Updated content`. The write succeeded, which
wasted the iteration. `Orchestrator._validate_action_inputs` (~line 1277)
rejects only empty or `...` **paths/queries**; it never checks file content
except for tests.

Reject obviously-placeholder content so the model gets immediate, specific
feedback in the same turn's results (validation errors become REPAIR feedback
through the existing path).

## Decided rules (only for `write_file`)

Let `path = action.path` and `content = action.content or ""`.

**Rule 1: Python source with no code.** This applies when `path` ends with
`.py`, its file name is not `__init__.py`, and it is not a Python test file
(`self._is_python_test(lower_path)` is False). Reject if there is no
*meaningful line*. A line is meaningful when, after `.strip()`, it is non-empty,
does not start with `#`, and is not one of `pass`, `...`, `"""`, `'''`.

Error message:
`f"write_file content for {action.path} has no code (only comments/pass/...); write the real implementation"`

**Rule 2: known placeholder phrases (any file except `plan.md` and `*.md`).**
Normalise: `normalized = content.strip().lstrip("#/ ").strip().lower().rstrip(".")`.
Reject if `normalized` is in:

```python
_PLACEHOLDER_CONTENT = frozenset({
    "updated content", "todo", "your code here", "implementation here",
    "code here", "placeholder", "content here", "insert code here",
    "add code here", "rest of the code", "same as before",
})
```

Error message:
`f"write_file content for {action.path} is a placeholder ('{content.strip()[:40]}'); write the complete file content"`

Rule 1 runs before Rule 2 for `.py` files. Empty content for non-`.py` files
(for example `.gitkeep`) remains allowed.

## Steps

1. Read `_validate_action_inputs` and `_is_python_test`. Find where
   `_validate_action_inputs` is called (`git grep -n "_validate_action_inputs"`)
   and confirm in your report that a returned string becomes an `{"error": ...}`
   result that leads to REPAIR.
2. Add `_PLACEHOLDER_CONTENT` as a module-level constant in `orchestrator.py`,
   near the other constants at the top.
3. Add a static method:
   ```python
   @staticmethod
   def _placeholder_content_error(path: str, content: str) -> Optional[str]:
       ...
   ```
   implementing Rules 1 and 2 exactly as specified. `path` is the raw
   `action.path`. Compute `lower_path = path.replace("\\", "/").lower()` and the
   file name with `Path(lower_path).name`.
4. In `_validate_action_inputs`, inside `if action.tool == "write_file":` and
   **after** all existing checks in that branch (after the test-file checks),
   add:
   ```python
   placeholder = self._placeholder_content_error(action.path, action.content or "")
   if placeholder:
       return placeholder
   ```
   It must be placed so the existing checks keep their order and messages.
   Note: the existing test-file checks `return` early only on failure, so the
   new check also runs for valid test files. Rule 1 excludes tests and Rule 2
   applies to them; that is intended.
5. Do not change any other validation.

## Tests to add: `tests/test_placeholder_guard.py`

Test `Orchestrator._placeholder_content_error` directly (it is static), plus
one end-to-end test.

Rejected (each returns a string):
1. `("hello.py", "# Updated content")`
2. `("hello.py", "pass")`
3. `("hello.py", "# TODO\n\n...\n")`
4. `("notes.txt", "TODO")`
5. `("app.js", "// your code here")`
6. `("hello.py", "# Updated content.")` (trailing period)
7. `("test_app.py", "# your code here")`: Rule 2 applies to test files too.
   (The existing "must include def test_" check would reject this first when
   going through `_validate_action_inputs`; here you call
   `_placeholder_content_error` directly.)

Known limitation, asserted as accepted so it is documented:
- `("pkg/mod.py", '"""\nDocstring only\n"""')` returns `None`, because the line
  `Docstring only` counts as meaningful under Rule 1. Add it as a separate test,
  `test_docstring_only_module_is_a_known_gap`, and list it under LIMITATIONS in
  your report.

Accepted (each returns `None`):
8. `("hello.py", "def add(a, b):\n    return a + b\n")`
9. `("pkg/__init__.py", "")`
10. `("README.md", "TODO")` (Markdown is exempt)
11. `("plan.md", "placeholder")`
12. `(".gitkeep", "")`
13. `("config.json", "{}")`

End-to-end:
14. `test_placeholder_write_triggers_repair_feedback`: use the scripted-model
    pattern (`03-FACTS.md` §6) with `iteration_limit` 4. Keep a call counter in
    the fake:
    - the plan call writes `plan.md`;
    - the first non-plan call writes `app.py` = `"# Updated content"` and a
      valid `test_app.py`;
    - every later call writes a real `app.py` (`answer = 42`) **and** the same
      valid `test_app.py` (write both, because a rejected action may stop the
      rest of that turn's actions).

    Capture every `messages` argument. Assert that some later call's messages
    contain the substring `"has no code"`, and that the final result is
    `tests_passed`.

## Must NOT

- Change existing validation messages or their order.
- Apply the rules to `read_file`, `run_cmd` or other tools.

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_placeholder_guard.py tests/test_regressions.py tests/test_deliverable_contracts.py tests/test_model_orchestration.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: Rules 1 and 2 behave exactly as specified (tests 1–13).
- AC2: Rejection feeds the REPAIR prompt through the existing path (test 14).
- AC3: There are no new failures in the full suite. If an existing test writes
  placeholder content as a fixture and now fails, stop and report it; do not
  edit it.
