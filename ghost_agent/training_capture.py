"""Capture verified input/target pairs for a future LoRA fine-tune.

Ghost executes coding tasks against a local model. Every time a task reaches
``tests_passed`` we have, at minimum:

    goal  +  (retrieved reference context)  ->  verified code that passed

That is a genuinely useful training example for a code-model fine-tune *because
the target is machine-verified by real tests*, not model-authored wishful
thinking. This module durably appends those triples to a JSONL corpus under
``.ghost/lora/training_pairs.jsonl``.

IMPORTANT SCOPE: this only *captures* the corpus. It does not train anything.
Fine-tuning a LoRA is a separate, compute-heavy, data-gated step (requires
llama.cpp/axolotl-style tooling and a curated corpus). Ghost records the pairs
so that step becomes possible later; nothing here invokes training or writes
model weights.

Format is self-describing JSONL so any downstream trainer can consume it:
  {"job_id", "goal", "model", "context", "result", "sandbox_files",
   "tests_passed": true, "captured_at"}
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils.storage import atomic_json, exclusive_lock

MAX_CONTEXT_CHARS = 4000
MAX_RESULT_CHARS = 6000
MAX_SANDBOX_FILES = 8
MAX_FILE_CHARS = 4000
SAMPLE_EXTS = {".py", ".js", ".ts", ".rs", ".go", ".sh", ".json", ".yaml", ".toml", ".md"}


@dataclass
class TrainingPair:
    job_id: str
    goal: str
    model: str
    result: str
    context: str = ""
    sandbox_files: Dict[str, str] = field(default_factory=dict)
    tests_passed: bool = True
    captured_at: float = field(default_factory=time.time)

    def to_jsonl(self) -> str:
        return json.dumps(asdict_safe(self), ensure_ascii=False)


def asdict_safe(pair: TrainingPair) -> Dict[str, Any]:
    """JSON-safe dataclass dump (plain dataclasses.asdict is fine here)."""
    from dataclasses import asdict
    return asdict(pair)


class TrainingPairStore:
    """Append-only, deduplicated corpus of verified training pairs."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.dir = self.root / ".ghost" / "lora"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "training_pairs.jsonl"

    def _load_ids(self) -> set:
        ids: set = set()
        if not self.path.exists():
            return ids
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["job_id"])
            except (ValueError, KeyError, OSError):
                continue
        return ids

    def capture(self, pair: TrainingPair) -> bool:
        """Append ``pair`` unless a pair with its job_id already exists.

        Returns True when newly recorded, False when it was a duplicate.
        Idempotence matters: a job may be retried or re-resumed.
        """
        if not pair.job_id or not pair.goal.strip() or not pair.result.strip():
            return False
        with exclusive_lock(self.path.with_suffix(".lock")):
            if pair.job_id in self._load_ids():
                return False
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(pair.to_jsonl() + "\n")
        return True

    def count(self) -> int:
        return len(self._load_ids())

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except (ValueError, OSError):
                continue
        return out[-limit:]


def build_pair(job_id: str, goal: str, model: str, result: str, *, context: str = "",
               sandbox_path: Optional[Path] = None) -> TrainingPair:
    """Assemble a TrainingPair from a completed job, sampling sandbox files."""
    context = (context or "")[:MAX_CONTEXT_CHARS]
    result = (result or "")[:MAX_RESULT_CHARS]
    sandbox_files: Dict[str, str] = {}
    if sandbox_path is not None:
        base = Path(sandbox_path)
        if base.is_dir():
            candidates = sorted(
                p for p in base.rglob("*")
                if p.is_file() and p.suffix.lower() in SAMPLE_EXTS
            )
            for p in candidates[:MAX_SANDBOX_FILES]:
                try:
                    sandbox_files[str(p.relative_to(base))] = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
                except OSError:
                    continue
    return TrainingPair(job_id=job_id, goal=goal, model=model, result=result,
                        context=context, sandbox_files=sandbox_files, tests_passed=True)
