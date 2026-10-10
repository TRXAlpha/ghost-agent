"""TK2: reproducible, resettable practice challenges with protected solutions.

Learner-facing material lives under ``.ghost/learner/challenges/<id>/``
(prompt + starter files). Solutions and the test oracle live separately
under ``.ghost/learner/protected/<id>/`` and are never copied into the
learner directory. Oracle runs execute in a temp copy with a bounded
timeout; nothing runs inside the learner directory itself.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .learner import FormatStats, LearnerStore, RustCompileOracle
from .utils.storage import atomic_json

CHALLENGE_TIMEOUT = 60
ORACLE_KINDS = ("pytest", "rustc")


class ChallengeError(ValueError):
    """Invalid challenge spec, unknown challenge, or failed oracle setup."""


class ChallengeStore:
    """Stores code challenges with protected oracles and solutions."""

    def __init__(self, root: Path, plan_root: Optional[Path] = None) -> None:
        self.root = Path(root).resolve()
        self.learner_dir = self.root / ".ghost" / "learner" / "challenges"
        self.protected_dir = self.root / ".ghost" / "learner" / "protected"
        self.learner_dir.mkdir(parents=True, exist_ok=True)
        self.protected_dir.mkdir(parents=True, exist_ok=True)

    # -- paths --------------------------------------------------------------
    def _learner_path(self, challenge_id: str) -> Path:
        return self.learner_dir / challenge_id

    def _protected_path(self, challenge_id: str) -> Path:
        return self.protected_dir / challenge_id

    def list(self) -> list[dict[str, Any]]:
        """List challenges with their learner-facing metadata."""
        out = []
        if not self.learner_dir.exists():
            return out
        for path in sorted(self.learner_dir.iterdir()):
            meta = path / "README.md"
            if path.is_dir() and meta.exists():
                out.append({
                    "challenge_id": path.name,
                    "prompt": meta.read_text(encoding="utf-8"),
                })
        return out

    # -- oracle execution ----------------------------------------------------
    def _run_pytest(self, workdir: Path, tests: str,
                    files: dict[str, str]) -> tuple[bool, str]:
        for name, content in files.items():
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (workdir / "test_ghost_challenge.py").write_text(
            tests, encoding="utf-8")
        try:
            probe = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                capture_output=True, text=True, timeout=CHALLENGE_TIMEOUT,
                cwd=str(workdir))
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"pytest failed to run: {exc}"
        detail = (probe.stdout or probe.stderr or "").strip()
        return probe.returncode == 0, detail[-500:]

    def _run_rustc(self, workdir: Path, _tests: str,
                   files: dict[str, str]) -> tuple[bool, str]:
        for name, content in files.items():
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        main = files.get("main.rs") or next(
            (c for n, c in files.items() if n.endswith(".rs")), "")
        if not main.strip():
            return False, "no Rust source found in challenge files"
        return RustCompileOracle(plan_root=None).check(main)

    def _run_oracle(self, kind: str, tests: str,
                    files: dict[str, str]) -> tuple[bool, str]:
        workdir = Path(tempfile.mkdtemp(prefix="ghost-challenge-"))
        try:
            if kind == "pytest":
                return self._run_pytest(workdir, tests, files)
            if kind == "rustc":
                return self._run_rustc(workdir, tests, files)
            raise ChallengeError(f"Unknown oracle kind: {kind}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # -- challenge lifecycle --------------------------------------------------
    def create(self, concept_ids: list[str], prompt: str,
               starter: dict[str, str], oracle: dict,
               solution: dict[str, str]) -> str:
        """Create a challenge after verifying the oracle is solvable."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ChallengeError("prompt must be non-empty text")
        if not isinstance(starter, dict) or not starter:
            raise ChallengeError("starter files required")
        if not isinstance(solution, dict) or not solution:
            raise ChallengeError("solution files required")
        kind = oracle.get("kind") if isinstance(oracle, dict) else None
        tests = oracle.get("tests") if isinstance(oracle, dict) else None
        if kind not in ORACLE_KINDS or not isinstance(tests, str) or not tests:
            raise ChallengeError(
                "oracle must be {'kind': 'pytest'|'rustc', 'tests': str}")
        challenge_id = "chl_" + hashlib.sha1(
            f"{concept_ids}|{prompt.strip()}|{time.time()}".encode()
        ).hexdigest()[:12]
        # negative control: the starter must not already pass the oracle
        ok, feedback = self._run_oracle(kind, tests, starter)
        if ok:
            raise ChallengeError(
                "refusing to create: starter already passes the oracle")
        # solvability: the oracle must pass on the reference solution
        ok, feedback = self._run_oracle(kind, tests, solution)
        if not ok:
            raise ChallengeError(
                f"refusing to create: oracle fails on the solution: "
                f"{feedback}")
        # learner-facing material: prompt + starter only
        learner = self._learner_path(challenge_id)
        (learner / "starter").mkdir(parents=True, exist_ok=True)
        (learner / "README.md").write_text(prompt, encoding="utf-8")
        for name, content in starter.items():
            target = learner / "starter" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        # protected: oracle, solution, and the original starter for reset
        protected = self._protected_path(challenge_id)
        protected.mkdir(parents=True, exist_ok=True)
        atomic_json(protected / "oracle.json", oracle)
        for name, content in solution.items():
            target = protected / "solution" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for name, content in starter.items():
            target = protected / "starter_original" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        atomic_json(protected / "meta.json", {
            "concept_ids": list(concept_ids), "oracle_kind": kind,
            "created_at": time.time()})
        FormatStats(self.root).record("challenge", generated=1)
        return challenge_id

    def reset(self, challenge_id: str) -> None:
        """Restore the learner starter from the protected original copy."""
        learner = self._learner_path(challenge_id)
        original = self._protected_path(challenge_id) / "starter_original"
        if not learner.exists() or not original.exists():
            raise ChallengeError(f"Unknown challenge {challenge_id}")
        shutil.rmtree(learner / "starter", ignore_errors=True)
        (learner / "starter").mkdir(parents=True, exist_ok=True)
        for path in original.rglob("*"):
            if path.is_file():
                target = learner / "starter" / path.relative_to(original)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    def check(self, challenge_id: str, concept_id: str) -> tuple[bool, str]:
        """Run the oracle on the learner's current starter in a temp copy."""
        learner = self._learner_path(challenge_id)
        protected = self._protected_path(challenge_id)
        if not learner.exists() or not protected.exists():
            raise ChallengeError(f"Unknown challenge {challenge_id}")
        oracle_path = protected / "oracle.json"
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        files = {str(p.relative_to(learner / "starter")): p.read_text(
            encoding="utf-8")
            for p in (learner / "starter").rglob("*") if p.is_file()}
        correct, feedback = self._run_oracle(
            oracle.get("kind", ""), oracle.get("tests", ""), files)
        for cid in ([concept_id] if concept_id else
                    json.loads((protected / "meta.json").read_text(
                        encoding="utf-8")).get("concept_ids", [])):
            LearnerStore(self.root).record_attempt(
                cid, correct, feedback=feedback)
        FormatStats(self.root).record(
            "challenge", attempted=1, correct=1 if correct else 0)
        return correct, feedback
