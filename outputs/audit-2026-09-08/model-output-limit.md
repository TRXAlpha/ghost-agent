# Local model output-limit check

Sent a non-executing request to local Ollama /api/chat using
qwen2.5-coder:1.5b, temperature=0, num_predict=16, stream=false.
Prompt: Explain binary search in 500 words.

Observed response: done=true, done_reason=length, eval_count=16,
total_duration=5826316800 ns. The installed backend honored this small
generation cap. No generated code was executed and no permissions changed.

This does not establish code quality, full-outcome reliability, transport-size
limits, streaming cancellation, or a cumulative job token budget. The production
orchestrator requests 2048 output tokens by default; truncation can produce
invalid JSON and must remain a failed response, not fabricated success.
