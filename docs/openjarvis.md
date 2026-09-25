# OpenJarvis-backed Ghost

Implemented 10 September 2026. This is the first qualified integration slice, not a claim that Ghost now autonomously builds arbitrary integrations or runs a personal AI OS.

## What is actually reused

| Ghost workflow | Upstream implementation used | Ghost responsibility retained |
| --- | --- | --- |
| `ghost-jarvis run/accomplish/research/worker` | Ollama and OpenAI-compatible inference engines | Existing coding/research procedure, queue, test feedback, tool policy and promotion controls |
| `openjarvis ask` | `OrchestratorAgent`, `SessionStore` | Explicit tool allowlist, bounded calls/context, emergency stop and minimal receipts |
| `index/search/forget`, `ask --memory` | `chunk_text`, Rust-backed `SQLiteMemory` / FTS5 | Explicit project imports, source hashes, correction/deletion and data scope |
| `openjarvis telegram serve` | `TelegramChannel`, python-telegram-bot polling/send/lifecycle | Local owner pairing, private-chat policy, revocation, duplicate receipts and single-bot lock |

There is one coding executor and one queue owner: existing Ghost. The upstream conversational agent is a separate read-only mode, not a second coding loop. No upstream default shell, browser, MCP tools, automatic provider discovery, scheduler, optimizer, full-system profile or startup daemon is enabled by this bridge.

## Install and launch

From this project in PowerShell:

```powershell
.\scripts\bootstrap-openjarvis.ps1
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis doctor
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis doctor --probe
```

The bootstrap uses the Windows Python installation manager to extract Python 3.13 under `workspaces/openjarvis-python`, creates `workspaces/openjarvis-venv`, checks out commit `a2eb7f0b0ca7b96882d25ac820228d5c43690321`, applies the maintained Telegram patch and installs the version-locked dependencies. Rust/cargo and Windows C++ build tools are needed for native retrieval. `-SkipNative` permits inference-only installation; memory commands then report the missing dependency instead of providing pretend results.

The existing `.venv` is untouched. No models are downloaded, no external account is configured, and no service is started. OpenJarvis requires Python below 3.14. Ghost's original Python 3.14 path continues working without OpenJarvis installed.

The bootstrap refuses another upstream revision or changes beyond the maintained patch. Its dependency snapshot is specific to Windows/Python 3.13; it is a version lock, not a hash-locked release or a Linux/macOS qualification. Build-tool dependencies also need a tighter release lock before deployment.

The qualified checkout is editable for integration development. Bootstrap verifies the exact diff; runtime checks package-version metadata. This is **not** tamper-proof activation or proof that every installed source byte remains unchanged. Controlled releases/integrity remain OJ15/OJ16 work.

## Coding and research

Start Ollama yourself, then select models actually installed on your machine:

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe accomplish "Build a calculator with tests" --model "<installed-model>" --research-model "<installed-model>" --require-file calculator.py
```

This uses OpenJarvis inference inside Ghost's existing research → build → test → repair workflow. `--skip-research` explicitly disables research for a task that does not need it. Queued work created through `ghost-jarvis` must use the same configured `ghost-jarvis worker`; a changed engine/endpoint/backend is refused, not silently substituted. Old jobs lacking routing metadata are legacy jobs.

Generated code stays in Ghost's copied workspace. **That workspace is not hostile-code containment:** candidate processes can still reach host resources under the existing execution model. Do not use this as an unattended untrusted-code runner. Container qualification and independent protected evaluation remain OJ06.

The legacy `ghost` command remains available; opt into upstream with the `ghost-jarvis` launcher. Programmatic callers can set `GHOST_INFERENCE_BACKEND=openjarvis` explicitly. The protocol benchmark uses that same selector.

## Other inference servers

```powershell
$env:GHOST_OPENJARVIS_ENGINE = 'openai-compat'
$env:GHOST_OPENJARVIS_URL = 'http://127.0.0.1:1234/v1'
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis ask --model "<served-model>" "Explain Rust borrowing"
```

LM Studio, llama.cpp, vLLM and similar services can use upstream's compatible engine; server-specific model behavior is not certified by this adapter. For a remote endpoint, explicitly set `GHOST_ALLOW_REMOTE_INFERENCE=1` and an HTTPS URL. A bearer credential may be supplied through `GHOST_OPENJARVIS_API_KEY`; never place it in source, chat, a task file, or the URL. Remote inference transmits the supplied task/context to that endpoint. No automatic cloud fallback is configured, and paid-account spending caps are not implemented yet.

Inference requests have output-token and character caps. Network timeout is an inactivity timeout, not a hard whole-job wall-clock limit. A read-only assistant run also has a bounded number of inference calls, including upstream continuation calls. JSON mode is supported; the pinned Ollama engine does not enforce JSON schemas, so the bridge refuses that stronger promise. Structured requests use non-streaming generation because this upstream streaming path drops the format constraint.

## Imported lessons and conversations

Copy a UTF-8 lesson into the project, for example `lessons/rust.md`, then:

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis index lessons/rust.md
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis search "borrowing"
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis ask --model "<installed-model>" --memory "Explain borrowing using my lesson and cite the source"
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis forget lessons/rust.md
```

Only explicitly imported project text is searched. Hidden/runtime directories and out-of-project paths are refused. Reimport atomically replaces the old chunks for that source. Forget removes indexed chunks, not the original file. Source path/hash/chunk metadata accompany retrieval; model citations are requested, not independently verified answer-level citations.

## First proactive MVP

The narrow initiative loop is now available:

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe proactive tick --model "<installed-model>" --context-file desksentinel.md
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe proactive status
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe proactive watch --model "<installed-model>" --context-file lessons/rust.md --idle-after-seconds 900 --run-worker
```

It reads active goals, declared devices, recent events, existing outcome state, and explicitly named project-relative Markdown/text files. It asks the model for one JSON proposal, records it, deduplicates by goal fingerprint, and automatically queues only a high-confidence sandbox outcome. The normal Ghost worker remains the only executor; live promotion still requires the existing review flow. `--no-queue` makes a planning-only tick.

The watch process is an explicit foreground process. It sleeps between ticks, runs only when Windows reports verified keyboard/mouse idle time above the threshold, and defers when idle state is unavailable. With `--run-worker`, it hands queued work to Ghost's existing worker and waits for its bounded research/build/test/repair result before the next tick. It does not claim that the owner is distracted, asleep, sick, or safe to interrupt. Stop it with Ctrl+C or engage `ghost emergency-stop`.

The MVP blocks proposals involving diagnosis, account access, sending messages, money, deployment, physical device motion, or live deletion. Integration ideas such as Huawei Health Kit, Telegram improvements, DeskSentinel, Rust games, or study tooling can be proposed as research/feasibility or sandbox-prototype outcomes when their context is supplied. They still stop at missing credentials, developer enrollment, firmware details, permissions, or live-provider qualification.

**Forget is currently index deletion, not comprehensive personal-data erasure.** Existing conversation messages may still quote previously retrieved material, including old/corrected information. Until session-aware correction/erasure is implemented, do not use this slice for sensitive medical or other highly private records. Recognized-secret rejection/redaction is best effort, not general PII detection.

Data lives in the current project's `.ghost/openjarvis/`: upstream `knowledge.db`, upstream `sessions.db`, assistant receipts and Telegram pairing/delivery receipts. Existing Ghost memories/jobs are not migrated or silently copied. Agent receipts contain counts/status/tool names rather than prompt bodies; conversation content is stored in the session database. No external analytics client is constructed, and full upstream config/recipes are not loaded.

## Telegram

Create your bot and supply its token in the launching process environment as `GHOST_TELEGRAM_BOT_TOKEN`. Then pair your own Telegram user ID and matching private-chat ID locally:

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis telegram pair --user-id "123456789" --chat-id "123456789"
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis telegram serve --model "<installed-model>"
```

Replace the example ID. Groups and other senders are refused. Starting the transport is an explicit command, not an install side effect. `--memory` on `serve` explicitly permits imported project document excerpts to be delivered to that paired Telegram chat. Telegram conversation history is kept separate from local chat history for now; cross-channel sharing is not implicitly authorized by pairing.

The first MVP can optionally turn explicit build-shaped Telegram requests into normal Ghost sandbox jobs:

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis telegram serve --model "<installed-model>" --allow-sandbox-builds --run-worker
```

With `--allow-sandbox-builds`, a paired message beginning with `/build`, `create`, `implement`, `prototype`, `fix`, or `accomplish` is acknowledged and queued for Ghost's existing research/build/test/repair worker. `--run-worker` starts that same worker in the transport process so the queued job progresses without a second manual command. It does not edit the live project, send arbitrary commands, control devices, or auto-promote results. Ordinary Telegram messages remain read-only conversations; blocked side effects such as medical advice, account access, messaging people, money, deployment, and physical control are refused.

```powershell
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis telegram status
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis telegram revoke
```

Revocation is rechecked before each message and before delivery. Ctrl+C stops admission and asks upstream to disconnect. Emergency stop suppresses inference/delivery. A bot-token-derived OS lock prevents two Ghost projects from polling the same bot. It cannot prevent a separate non-Ghost client from polling that bot.

Receipts suppress duplicate message execution, including after restarts. Interrupted/failed/ambiguous messages are **not** automatically retried. A crash around sending cannot establish exactly-once delivery; inspect receipts and send a new message if appropriate. There is no background completion notification, remote coding, approval-by-chat, pairing challenge flow, automatic reconnection policy, or account provisioning yet.

`patches/openjarvis-telegram.patch` changes the pinned transport's callback to async, moves synchronous handlers off its event-loop thread, and preserves pending updates on startup. It is a maintained local patch; nothing was submitted upstream. Shutdown of an in-flight model request is bounded by its request timeout, not guaranteed immediate cancellation.

## Verification and next steps

Run:

```powershell
.\workspaces\openjarvis-venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q
```

See the [integration ledger](../outputs/openjarvis-integration/2026-09-10.md) for actual evidence and roadmap status. The native engine, agent, retrieval and channel boundaries are exercised locally. Live-model quality and live Telegram delivery need separately configured services; mock/provider fixtures must not be reported as those qualifications.
