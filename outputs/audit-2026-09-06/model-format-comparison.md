# Qwen constrained-output comparison — 6 September 2026

Ghost's Ollama client now accepts optional `response_format="json"` or a JSON-schema dictionary, using the documented [Ollama format field](https://docs.ollama.com/api/chat). The constraint survives non-streaming generate fallback and streaming connection fallback. Unsupported format requests are not silently retried without the constraint. Omission preserves the existing request shape.

The protocol runner exposes `--output-format unconstrained|json|schema` and records the actual constraint in its report. Schema mode uses Ghost's existing `ActionResponse` schema, not case-specific expected answers. Neither mode grants permission or replaces parsing and kernel authorization. Production orchestration remains unconstrained because of the regression below.

## Live measurement

Same installed Qwen digest, production prompt, options, cases, and deterministic two repetitions as the previous baseline:

| Output mode | Baseline exact actions | Validation exact actions | Strict JSON across both |
| --- | --- | --- | --- |
| Unconstrained (previous runs) | 4/8 | 8/12 | 0/20 |
| JSON | 2/8 | 8/12 | 20/20 |
| Action schema | 2/8 | 8/12 | 20/20 |

Both constrained modes fixed formatting in these cases but regressed the baseline read action: the model described reading the file in `thought` while returning an empty action list. They did not fix directory listing or the path containing spaces. Consequently neither was enabled by default. A valid JSON/schema response is not evidence of a correct plan.

Raw reports:

- [JSON baseline](../benchmarks/protocol-d7cb309858764f3b86731c9101137007/report.json)
- [JSON validation](../benchmarks/protocol-4cc47f401d184aaf8bdfeda4c6bd89c6/report.json)
- [Schema baseline](../benchmarks/protocol-ef594640ffbe490e8aa4b8d662d48484/report.json)
- [Schema validation](../benchmarks/protocol-4b36c9671dda4f888959a377a77e7308/report.json)

No actions were executed or reputation scores updated. These results concern tiny fixed protocol cases; deterministic repeats are not independent samples, and correctness of model thoughts is not scored. Actual coding outcomes still need execution isolation and independent acceptance tests.

Reproduce:

```powershell
.\.venv\Scripts\python.exe -m ghost_agent.protocol_benchmark --output-format schema --dataset validation --repetitions 2
```

Verification: **370 tests passed in 10.18 seconds**; compileall succeeded. New tests cover request formatting, unchanged messages, streaming and generate fallbacks, unsupported/invalid formats, and benchmark constraint persistence.
