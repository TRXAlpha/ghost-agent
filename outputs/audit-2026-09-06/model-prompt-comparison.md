# Qwen prompt comparison — 6 September 2026

The protocol runner now supports `--prompt-style production|compact` and `--dataset baseline|validation`. The experimental compact prompt and six validation cases were written before their first live evaluation. Expected actions were not changed after observing results. Production Ghost still uses its original system prompt.

| Prompt | Baseline exact actions | Validation exact actions | Strict JSON |
| --- | --- | --- | --- |
| Production | 4/8 | 8/12 | 0/20 |
| Compact experiment | 4/8 | 8/12 | 0/20 |

All responses were recoverable by Ghost's action parser. Repeats use temperature zero and the same seed, so they are not independent statistical samples. These small fixed fixtures are diagnostic, not a broad model ranking.

Both prompts failed directory listing and exact-path cases. In the production validation run, a request to list `docs` produced a web search for FastAPI documentation; a request to read `docs/setup notes.md` instead targeted `docs/setup_notes.md`. This makes task-specific action scope enforcement important even for syntactically valid tool calls. No proposed action was executed during evaluation.

No prompt was promoted: the compact experiment showed no action-accuracy or JSON-compliance improvement. Future work should test constrained output and investigate request/template behavior, then use additional unseen cases before changing production behavior. Do not infer reliable execution or prompt-injection resistance from these action-list checks.

## Raw evidence

- [Production baseline](../benchmarks/protocol-e1472bec969c460d8ab53d439d6476b0/report.json)
- [Compact baseline](../benchmarks/protocol-300e0cff61954bd3b0d2f9a6ae06dd25/report.json)
- [Production validation](../benchmarks/protocol-823a019da8454d5fb1fd05b51d1b933b/report.json)
- [Compact validation](../benchmarks/protocol-eeba8f5c45504a969ce6a9b7fb1f473c/report.json)

The local API reported Ollama version `0.31.1`. `/api/show` for the installed Qwen model returned its Qwen assistant system text and a ChatML-style message template. This inspection alone does not establish whether the server honored every supplied generation option or system instruction.

Reproduce a validation run:

```powershell
.\.venv\Scripts\python.exe -m ghost_agent.protocol_benchmark --prompt-style compact --dataset validation --repetitions 2
```

Verification: **353 tests passed in 9.82 seconds**; compileall succeeded. Added tests cover validation-case oracles, prompt selection, persisted comparison metadata, and refusal of unknown configurations. Existing tests continue to check that benchmark proposals are not executed and do not update routing reputation.

The subsequent [constrained-output comparison](model-format-comparison.md) improved JSON formatting but regressed baseline action accuracy. Production orchestration therefore remains unchanged.
