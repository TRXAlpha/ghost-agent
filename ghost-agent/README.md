# ghost-agent

Local Codex-like coding agent that plans, edits files, runs commands, and learns.

## Setup

- Python 3.11+
- Install dependencies:

```bash
pip install -e .
```

- Ensure Ollama is running:

```bash
ollama serve
```

## Usage

Run a task:

```bash
ghost run path\to\task.json
```

Override model or base URL:

```bash
set GHOST_MODEL=gemma3:270m
set GHOST_OLLAMA_BASE_URL=http://localhost:11434
set GHOST_OLLAMA_TIMEOUT=180
ghost run path\to\task.json
```

Resume a task:

```bash
ghost resume <task_id>
```

Interactive mode (auto-edits files in the project root, watches for changes):

```bash
ghost interactive --project-root .
```

## Windows quickstart

Run without installing entrypoints:

```powershell
python -m pytest -q
python -m ghost_agent.cli run smoke_task.json
```

Install entrypoints in a venv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install pytest
pytest -q
ghost run smoke_task.json
```

Example task:

```json
{
  "id": "mvp_smoketest_001",
  "title": "Implement fizzbuzz",
  "goal": "Create a Python script fizzbuzz.py that prints 1..100 with fizz/buzz rules. Add pytest tests.",
  "constraints": { "test_cmd": "pytest -q", "iteration_limit": 8 },
  "context": { "notes": "Keep it simple." }
}
```

## Task file format

See `task.json` in the prompt spec. The agent creates a workspace at:

```
workspaces/<task_id>/
```

Artifacts written per task:

- `task.json`
- `plan.md`
- `state.json`
- `actions.log`
- `llm.log`

Note: `ghost run` recreates the task workspace, clearing any previous contents
for that task id. Use `ghost resume` to keep existing workspace state.

## Security warnings

This agent can run commands. The tool layer enforces:

- Workspace-only file access
- Command allowlist
- Timeout and output limits
- No network commands unless explicitly enabled

Review tool policies in `ghost_agent/tools/cmd_tools.py` before use.

## Memory

Memories are stored in `memories/` with an `index.json` for retrieval. On completion,
the agent writes a short lesson note under `memories/lessons/`.

## Advanced

### How it works (end-to-end)

1) The CLI loads a task (or accepts a goal in interactive mode).
2) The orchestrator builds a workspace context and a system prompt that enforces
   strict JSON output.
3) The model emits actions (write/read/list/search/run).
4) The tool layer validates each action and executes it safely.
5) The orchestrator feeds tool results back to the model until success or
   iteration limit.
6) Artifacts and memories are persisted for review and reuse.

### State machine

Phases are deterministic:

- INGEST: load `task.json` and retrieve relevant memory notes
- PLAN: model writes `plan.md` with steps + verification
- IMPLEMENT: model edits files
- VERIFY: run tests/commands if configured
- REPAIR: fix failures and return to VERIFY
- DONE: write summary memory note and persist final `state.json`

If iteration limit is reached, the run ends with `last_result=iteration_limit`
and still writes a memory note.

### Workspace and artifacts layout

`ghost run` uses `workspaces/<task_id>/` as both the editable workspace and the
artifact folder.

`ghost interactive` edits your project root directly and stores artifacts in:

```
.ghost/
  workspaces/<run_id>/
    task.json
    plan.md
    state.json
    actions.log
    llm.log
    notes.md
```

### Tools and safety model

All filesystem access is constrained to the workspace root. Command execution is
allowlisted and uses timeouts and output limits. See:

- `ghost_agent/tools/fs_tools.py` for path sandboxing
- `ghost_agent/tools/cmd_tools.py` for command allowlist/blocklist and timeouts

### LLM adapter

`ghost_agent/models/ollama.py` calls Ollama via `/api/chat`, with a fallback to
`/api/generate`. It normalizes the base URL and surfaces clearer errors when the
model is missing.

### Schema and JSON enforcement

`ghost_agent/schema.py` defines the strict `ActionResponse` schema (Pydantic).
Any non-JSON or invalid tool payload results in a parse error and a repair step.

### Memory system

`ghost_agent/memory/store.py` maintains:

- `memories/index.json` (simple token index)
- `memories/lessons/*.md` (run summaries and learned notes)

Retrieval is keyword-based and injected into the prompt during INGEST.

### File-by-file overview

Core:

- `ghost_agent/cli.py`: CLI entrypoint and interactive mode
- `ghost_agent/orchestrator.py`: state machine, tool execution, logging
- `ghost_agent/schema.py`: JSON action schema validation
- `ghost_agent/__init__.py`: package exports and version

Tools:

- `ghost_agent/tools/fs_tools.py`: safe read/write/list/search
- `ghost_agent/tools/cmd_tools.py`: sandboxed command runner
- `ghost_agent/tools/git_tools.py`: optional (not required by default)
- `ghost_agent/tools/__init__.py`: tool package export

Models:

- `ghost_agent/models/ollama.py`: Ollama HTTP adapter
- `ghost_agent/models/__init__.py`: models package export

Memory:

- `ghost_agent/memory/store.py`: memory notes + index
- `ghost_agent/memory/__init__.py`: memory package export

Utils:

- `ghost_agent/utils/logging.py`: JSONL logging helpers
- `ghost_agent/utils/watch.py`: file watcher for interactive auto-runs
- `ghost_agent/utils/__init__.py`: utils package export

Tests:

- `tests/test_fs_tools.py`: workspace path sandboxing
- `tests/test_cmd_tools.py`: command allowlist blocking
- `tests/test_schema.py`: strict JSON parsing and schema validation
- `tests/__init__.py`: (not required)

Project:

- `pyproject.toml`: package metadata, entrypoints, build config
- `README.md`: usage and architecture notes
- `memories/`: long-lived learning notes and index
- `workspaces/`: per-task artifacts when using `ghost run`
- `.ghost/`: per-run artifacts when using `ghost interactive`

### Detailed file breakdown

`ghost_agent/cli.py`

- Parses CLI args and environment variables.
- Creates the `Orchestrator` and runs either `run`, `resume`, or `interactive`.
- Interactive mode creates `.ghost/workspaces/<run_id>/` and runs tasks directly
  against the project root.
- Launches a polling file watcher to auto-run tasks when you edit files.

`ghost_agent/orchestrator.py`

- Loads `task.json` into a `Task` dataclass.
- Initializes workspace or artifact roots and seeds `notes.md`.
- Implements the deterministic loop: INGEST → PLAN → IMPLEMENT → VERIFY → REPAIR → DONE.
- Constructs prompts, sends them to the model, parses JSON actions, and executes them.
- Writes `state.json`, `plan.md`, `actions.log`, and `llm.log`.
- Writes a lesson note into `memories/lessons/` when finished or on iteration limit.

`ghost_agent/schema.py`

- Defines the strict action schema with Pydantic (no extra keys allowed).
- Rejects any non-JSON or malformed actions before tool execution.

`ghost_agent/tools/fs_tools.py`

- Implements `safe_path` to prevent path escape outside the workspace root.
- Provides `read_file`, `write_file`, `list_dir`, and `search_in_files`.
- `search_in_files` is a simple Python-based scan (no external deps).

`ghost_agent/tools/cmd_tools.py`

- Enforces the allowlist (`python`, `pytest`, `git`, `pip`, `ruff`).
- Blocks dangerous tokens (e.g., `sudo`, `rm -rf`, `curl`, `wget`).
- Resolves and validates `cwd` to ensure it stays within the workspace.
- Truncates command output and applies a timeout.

`ghost_agent/models/ollama.py`

- Calls `/api/chat` with a standard chat payload.
- Falls back to `/api/generate` when `/api/chat` is unavailable.
- Normalizes the base URL and emits a clear error when the model is missing.

`ghost_agent/memory/store.py`

- Creates memory folders and `index.json` on first run.
- Stores notes as Markdown with a YAML-like header block.
- Tokenizes content and updates the index for keyword retrieval.

`ghost_agent/utils/logging.py`

- Writes JSONL logs for tool calls and model responses.
- Keeps logs append-only for easy review and debugging.

`ghost_agent/utils/watch.py`

- Polling file watcher used by interactive mode.
- Ignores `.ghost`, `.git`, `.venv`, `__pycache__`, `node_modules`, etc.
- Emits a summarized change list to prompt a focused auto-run.

`tests/*`

- Unit tests validate the sandbox and schema behavior.
- `pytest -q` runs in seconds and catches common regressions.

### Artifacts explained

`task.json`
- The task contract: id/title/goal/constraints/context.

`plan.md`
- Written by the model during PLAN, includes steps and verification plan.

`state.json`
- A resumable state snapshot of the loop.

`actions.log`
- JSONL file with each tool call and its result.

`llm.log`
- JSONL file with prompts and raw model responses.

`notes.md`
- Scratch pad for human notes if you want to annotate runs.

### Action schema

Expected model output is strict JSON:

```json
{
  "thought": "short reasoning",
  "actions": [
    { "tool": "write_file", "path": "main.py", "content": "..." },
    { "tool": "run_cmd", "cmd": "pytest -q", "cwd": "." }
  ]
}
```

If the model emits prose or markdown fences, parsing fails and the REPAIR phase
asks it to try again.

### Interactive mode details

`ghost interactive` is designed for live work:

- watches the project root for edits
- auto-runs a short task to review and improve changed files
- accepts direct goals via the prompt

To disable auto-runs, use `/watch off`.
