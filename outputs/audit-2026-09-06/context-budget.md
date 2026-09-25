# Runtime context budget — 6 September 2026

`ContextAssembler.assemble` now enforces its character budget against `json.dumps(payload, ensure_ascii=False)`. Previously it shortened two lists once and could still return an oversized payload.

The full user profile and selected goal are retained, including policies, prohibitions, preferences, and goal constraints. If those required fields cannot fit, assembly raises an explicit error instead of silently dropping rules. A requested goal that cannot be loaded is also refused. The selected goal is not duplicated in the active-goal list.

Optional records are removed whole, with counts in `omitted_for_budget`. Trimming removes events, other active goals, prior failures, world observations, then validated memories. Event/failure lists discard oldest entries first; active goals retain their priority order. No quote or record is cut mid-string.

The outcome workflow already includes this assembler's output in its task runtime context. This change bounds that component only: it does not enforce a model-token budget or bound the complete assembled prompt, research dossier, tool outputs, storage reads, or in-memory source data. Those remaining limits still matter for Qwen 1.5B and lightweight operation. Existing per-category record-count limits remain in place; omission counts describe budget trimming after those initial selections.

Regression cases cover oversized memories/events, preservation of user and goal constraints, explicit failure for oversized required content, invalid budgets, and missing requested goals. Full suite: **452 passed in 11.32 seconds**; compileall succeeded.

## Complete coding-message boundary

The orchestrator now checks the serialized message list immediately before its Ollama call. `max_input_chars` defaults to 24,000 and is configurable on `Orchestrator`. Optional retrieval/history seed messages are removed whole, newest-added first. The original system/task messages, any additional system messages, the current phase prompt, and latest feedback are retained intact. If these required inputs still exceed the limit, the call is refused with an explicit budget error. Original caller messages are not mutated.

The model log records measured input characters, configured limit, and omitted message count. The check applies before both streaming and non-streaming calls, including their compatibility fallback. No production protocol prompt or source-evidence gate was weakened.

This is a limit on `json.dumps(messages, ensure_ascii=False)`, not exact tokens, HTTP payload bytes, model output size, or total process memory. RAG/source data may already have been assembled in memory before this check. Large required research/task context can now cause explicit refusal and needs structured retrieval or a deliberately larger budget; it is not silently truncated. Model-context-window calibration remains necessary.

Boundary tests capture the actual client arguments and cover optional-message removal, preservation of policies/contracts/feedback, refusal of oversized required components, and invalid limits. Latest full suite: **462 passed in 21.33 seconds**; compileall succeeded.
