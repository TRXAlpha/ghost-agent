# T03: Remove duplicated `OpportunityStore` methods

Size: XS · Wave 1 · Files: `ghost_agent/proactive.py`

## Objective

`class OpportunityStore` in `ghost_agent/proactive.py` defines `find_any` and
`find_suppressed` **twice** (around lines 88–105 and again around 111–128).
Python silently uses the second definition. The bodies are identical (only one
docstring differs by a dash), so delete the second copy.

## Steps

1. Open `ghost_agent/proactive.py` and find `class OpportunityStore`. Confirm
   the order of methods: `list`, `list_all`, `find_any`, `find_suppressed`,
   `find_active`, `find_any`, `find_suppressed`, `save`.
2. Compare the two `find_any` bodies and the two `find_suppressed` bodies
   character by character, ignoring docstrings. If the **code** differs in any
   way, stop and report both versions. Do not choose one yourself.
3. Delete the **second** `find_any` and the **second** `find_suppressed` (the
   ones after `find_active`). Keep the first copies and `find_active`.
4. Check that each method name now appears once:
   ```bash
   git grep -n "def find_any\|def find_suppressed\|def find_active" ghost_agent/proactive.py
   ```
   Expected: exactly 3 lines.

## Tests to add: append to `tests/test_proactive.py`

- `test_opportunity_store_methods_defined_once`: use `inspect.getsource` on
  the module (`ghost_agent.proactive`). Count the occurrences of
  `"    def find_any("` and `"    def find_suppressed("`, and assert each count
  is 1. (The source search is the honest check here: a duplicate definition
  cannot be seen at runtime.)

## Must NOT

- Change any behaviour or any other method.

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_proactive.py tests/test_proactive_feedback.py
```

## Acceptance criteria

- AC1: Each of the three methods is defined once.
- AC2: The proactive and proactive_feedback tests all pass (16 + 20 at baseline,
  plus your new test).
