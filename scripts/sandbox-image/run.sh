#!/bin/sh
# Read one JSON payload from stdin, execute _command (no shell) in _cwd,
# emit sentinel-wrapped JSON result.
set -u

payload="$(cat)"

# Extract fields with python (always present in this image).
result="$(python - "$payload" <<'PYEOF'
import json, subprocess, sys

payload = json.loads(sys.argv[1])
argv = payload.get("_command") or []
if isinstance(argv, str):
    argv = [argv]
cwd = payload.get("_cwd") or payload.get("_workspace") or "/"
try:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=payload.get("_timeout_seconds") or None,
        shell=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    print(json.dumps({
        "returncode": proc.returncode,
        "output": output,
        "error": proc.returncode != 0,
    }))
except FileNotFoundError as exc:
    print(json.dumps({"returncode": 127, "output": str(exc), "error": True}))
except subprocess.TimeoutExpired:
    print(json.dumps({"returncode": 124, "output": "command timed out", "error": True}))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"returncode": 1, "output": repr(exc), "error": True}))
PYEOF
)"

printf '%s\n' "---OPENJARVIS_OUTPUT_START---"
printf '%s\n' "$result"
printf '%s\n' "---OPENJARVIS_OUTPUT_END---"
