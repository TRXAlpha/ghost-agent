"""Bounded Ghost self-repair proposals; no unrestricted self-modification.

Evidence boundaries (S08):

- A candidate is a copied workspace whose real bytes are snapshotted and hashed.
- verify_from_evaluator is the ONLY producer of trusted evaluator evidence: it
  hashes the candidate BEFORE and AFTER the evaluator run, rejects the run if
  the bytes changed during evaluation, and binds the PRE-run digest to the
  recorded evidence so the evidence provably describes the bytes that ran.
- record_verification (public) accepts persisted evidence but NEVER counts
  non-evaluator evidence, or evidence whose run_digest does not match the
  CURRENT candidate digest, as verification. A manually recorded note cannot
  impersonate independent evidence, and any candidate modification invalidates
  previously verified evidence (the digest no longer matches).
- mark_applied / mark_rollback only transition state while bound to a REAL
  promotion journal record (PromotionManager.list()); a bare state change or a
  bare string is not proof that application/restoration occurred.
- _assert_candidate_scope validates the candidate against explicit project and
  protected roots (the authority/evidence area), not directory-name guesses, so
  legitimate copied workspaces are preserved.
"""
from __future__ import annotations

import hashlib
import json
import platform
import secrets
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .promotion import PromotionManager, PromotionService, load_update_policy
from .utils.storage import atomic_json, exclusive_lock

# S08/R13: self-repair promotion of owner-controlled kernel code must go
# through the owner's explicit update process, never the automated path.
PROTECTED_PATH_MESSAGE = "protected path requires the owner-controlled update process: {path}"


class SelfRepairError(ValueError):
    """Invalid self-repair call, evidence, or state."""


# Ephemeral/cache trees whose content must not change the digest of the
# evaluated source bytes, and which are also excluded from promotion previews.
# Kept in lockstep with PromotionManager.preview (promotion.py) so the verified
# candidate digest and the promotion snapshot describe the same meaningful set.
EXCLUDED_DIGEST_PARTS = {".git", ".ghost", ".venv", "__pycache__", ".pytest_cache"}


def _dir_digest(path: Path, algorithm: str = "sha256") -> str:
    """Content digest of a directory (files + relpaths); stable across runs.

    Ephemeral/cache trees are excluded so a real evaluator run (which writes
    ``.pytest_cache`` / ``__pycache__`` under the candidate) does not change the
    digest of the source bytes it evaluated. This mirrors PromotionManager's
    preview convention (promotion.py) so a verified promotion snapshot and the
    verified candidate digest agree on the same meaningful file set.
    """
    digest = hashlib.new(algorithm)
    files = sorted(
        (p.relative_to(path).as_posix(), p.read_bytes())
        for p in path.rglob("*")
        if p.is_file() and not any(part.lower() in EXCLUDED_DIGEST_PARTS for part in p.relative_to(path).parts)
    )
    for rel, data in files:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(data)
        digest.update(b"\x00")
    return digest.hexdigest()


def environment_identity(candidate: Path, command: str) -> dict[str, Any]:
    """Describe which environment produced a verification result (R07, R13).

    Records the interpreter, platform, the evaluator command, the supervisor
    revision, and digests of dependency manifests in the candidate root. A later
    verification whose dependency digests differ from a stored record therefore
    counts as a different environment. Pass/fail semantics are unchanged; this
    is evidence only.
    """
    supervisor_rev = "unknown"
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parent),
            capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            supervisor_rev = proc.stdout.strip()
    except Exception:
        supervisor_rev = "unknown"
    dependency_digests: dict[str, str] = {}
    patterns = ("pyproject.toml", "requirements*.txt", "*.lock",
                "package.json", "package-lock.json")
    seen: set[str] = set()
    root = Path(candidate)
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file() or path.name in seen:
                continue
            seen.add(path.name)
            dependency_digests[path.name] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "evaluator_command": command,
        "supervisor_rev": supervisor_rev,
        "dependency_digests": dependency_digests,
    }


@dataclass
class SelfRepairProposal:
    """Pure record of a self-repair proposal. No manager logic / manager state."""

    proposal_id: str
    diagnosis: str
    sandbox: str
    proposed_benefit: str = ""
    linked_failure: str = ""
    regression_results: list[dict[str, Any]] = field(default_factory=list)
    benchmark_results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "diagnosed"
    created_at: float = field(default_factory=time.time)
    verified_snapshot: dict[str, dict[str, str | None]] = field(default_factory=dict)
    run_digest: str = ""
    run_nonce: str = ""
    promotion_id: str = ""
    rollback_promotion_id: str = ""


class SelfRepairManager:
    """Persist diagnose -> sandbox -> verify -> promotion-request progression.

    Owns the on-disk state at ``root/.ghost/self-repair.json`` and every
    transition that mutates it. The pure :class:`SelfRepairProposal` dataclass
    only carries data.
    """

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.path = self.root / ".ghost" / "self-repair.json"
        # Authority / evidence / source areas a candidate must never be able to
        # write through. Path-relationship based scope guard uses these.
        self.protected_roots = {self.root / ".ghost", self.root / "ghost_agent"}
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
            if not isinstance(items, list):
                raise ValueError("Expected a list of self-repair proposals")
            identifiers = set()
            for item in items:
                proposal = SelfRepairProposal(**item)
                if (not isinstance(proposal.proposal_id, str) or not proposal.proposal_id
                        or proposal.proposal_id in identifiers
                        or not isinstance(proposal.sandbox, str)
                        or not isinstance(proposal.diagnosis, str)
                        or not isinstance(proposal.regression_results, list)
                        or not isinstance(proposal.benchmark_results, list)
                        or not isinstance(proposal.verified_snapshot, dict)
                        or not isinstance(proposal.proposed_benefit, str)
                        or not isinstance(proposal.linked_failure, str)
                        or not isinstance(proposal.run_digest, str)
                        or not isinstance(proposal.run_nonce, str)
                        or not isinstance(proposal.promotion_id, str)
                        or not isinstance(proposal.rollback_promotion_id, str)
                        or proposal.status not in {"diagnosed", "patched", "verified",
                                                   "rejected", "applied", "rolled_back"}):
                    raise ValueError("Invalid or duplicate self-repair record")
                identifiers.add(proposal.proposal_id)
            return items
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Self-repair records are unreadable or malformed; preserve them for review") from exc

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.path, items)

    def propose(self, diagnosis: str, sandbox: Path, proposed_benefit: str = "",
                linked_failure: str = "") -> SelfRepairProposal:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._propose_locked(diagnosis, sandbox, proposed_benefit, linked_failure)

    def _propose_locked(self, diagnosis: str, sandbox: Path, proposed_benefit: str = "",
                        linked_failure: str = "") -> SelfRepairProposal:
        candidate = Path(sandbox).resolve()
        if not candidate.is_dir():
            raise SelfRepairError("Self-repair candidate must be an existing sandbox directory")
        self._assert_candidate_scope(candidate)
        diagnosis = diagnosis.strip()
        if not diagnosis:
            raise SelfRepairError("Diagnosis must be nonempty")
        if any(item.get("diagnosis") == diagnosis
               and item.get("status") in {"rejected", "rolled_back"}
               for item in self._load()):
            raise SelfRepairError(
                "Identical diagnosis was already rejected; refusing an endless repair loop")
        digest = hashlib.sha1(f"{diagnosis}|{time.time_ns()}".encode()).hexdigest()[:10]
        proposal = SelfRepairProposal(
            f"repair_{digest}", diagnosis, str(candidate),
            proposed_benefit=proposed_benefit.strip(),
            linked_failure=linked_failure.strip(), status="patched")
        data = self._load()
        data.append(asdict(proposal))
        self._save(data)
        return proposal

    def _assert_candidate_scope(self, candidate: Path) -> None:
        """Validate a candidate path against explicit project and protected roots.

        A candidate must be (a) inside the project root, (b) not equal to the
        project root, and (c) not inside any protected root (Ghost authority /
        evidence / source area). This uses path relationships, not name
        matching, so legitimate copied workspaces anywhere under the project are
        preserved while authority/evidence directories stay out of reach.
        """
        candidate = candidate.resolve()
        if not candidate.is_absolute():
            raise SelfRepairError("Candidate must be an absolute path")
        if candidate == self.root:
            raise SelfRepairError("Candidate cannot be the project root itself")
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise SelfRepairError("Candidate must be inside the project root") from None
        for protected in self.protected_roots:
            try:
                rel = candidate.relative_to(protected)
            except ValueError:
                continue
            raise SelfRepairError(
                f"Self-repair candidate cannot be inside a protected root: {protected} ({rel})")

    def get(self, proposal_id: str) -> Optional[SelfRepairProposal]:
        return next((SelfRepairProposal(**item) for item in self._load()
                     if item.get("proposal_id") == proposal_id), None)

    def list(self) -> list[SelfRepairProposal]:
        return [SelfRepairProposal(**item) for item in self._load()]

    # The manager's proof of an actual internal evaluator run is a (run_nonce,
    # witnessed_digest) pair. verify_from_evaluator mints a random nonce per run
    # and records the PRE-run digest it witnessed. Public record_verification
    # accepts evidence only if it carries the proposal's stored run_nonce AND a
    # run_digest that equals BOTH the stored witnessed digest (the bytes the
    # manager actually saw) AND the current candidate digest (bytes unchanged
    # since witness). Because the witnessed digest is immutable until the next
    # witnessed run, a caller who merely recomputes _dir_digest cannot replay a
    # nonce against different candidate bytes.
    @staticmethod
    def _is_evaluator_evidence(result: Any, witnessed_digest: str,
                               current_digest: str, expected_nonce: str) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("source") != "evaluator":
            return False
        if result.get("run_digest") != witnessed_digest:  # exactly the bytes witnessed
            return False
        if result.get("run_digest") != current_digest:    # candidate unchanged since witness
            return False
        if result.get("run_nonce") != expected_nonce:
            return False
        return type(result.get("returncode")) is int

    def record_verification(self, proposal_id: str, regression_results: list[dict[str, Any]],
                            benchmark_results: list[dict[str, Any]]) -> SelfRepairProposal:
        """Persist caller-supplied evidence and decide verified/rejected.

        This public method stores whatever evidence it is given, but it never
        counts as independent verification unless EVERY result is a genuine
        evaluator record bound to the manager's last witnessed run: source ==
        "evaluator", run_digest == the stored witnessed digest == the current
        candidate digest, and run_nonce == the stored run_nonce minted for that
        run. A hand-entered note, a caller-computed digest, or a replayed nonce
        against different bytes cannot impersonate an independent evaluation and
        is rejected. It never rewrites the witnessed digest or nonce.
        """
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._record_verification_locked(
                proposal_id, regression_results, benchmark_results,
                expected_nonce=None, witnessed_digest=None)

    def _record_verification_locked(self, proposal_id: str,
                                    regression_results: list[dict[str, Any]],
                                    benchmark_results: list[dict[str, Any]],
                                    expected_nonce: Optional[str] = None,
                                    witnessed_digest: Optional[str] = None) -> SelfRepairProposal:
        if not isinstance(regression_results, list) or not isinstance(benchmark_results, list):
            raise SelfRepairError("Regression and benchmark results must be lists")
        data = self._load()
        for item in data:
            if item.get("proposal_id") != proposal_id:
                continue
            candidate = Path(item["sandbox"])
            current_digest = _dir_digest(candidate) if candidate.is_dir() else ""
            fresh_run = witnessed_digest is not None  # called from verify_from_evaluator
            # Ground truth of what the manager actually witnessed: for a fresh
            # run it is the just-witnessed pre-run digest set by _run_verified_locked;
            # for the public path it is the immutable stored witnessed digest.
            if fresh_run:
                witnessed = witnessed_digest
            else:
                expected_nonce = item.get("run_nonce", "")
                witnessed = item.get("run_digest", "")
            combined = (regression_results or []) + (benchmark_results or [])
            # A proposal that has never had a manager-minted nonce-minted run
            # (run_nonce still empty) can NEVER be verified by any path: the
            # empty-string default must not be accepted as a real run's stamp.
            nonce_ok = bool(expected_nonce) and bool(witnessed)
            # Every gate must be real, current, nonce-stamped evidence explicitly
            # matching the bytes the manager witnessed.
            passed = nonce_ok and bool(combined) and all(
                self._is_evaluator_evidence(r, witnessed, current_digest, expected_nonce)
                and r["returncode"] == 0
                for r in combined
            )
            # The witnessed digest and nonce are only ever replaced by a fresh
            # witnessed run (verify_from_evaluator) — never by caller evidence.
            if fresh_run:
                item["run_nonce"] = expected_nonce
                item["run_digest"] = witnessed
            item["regression_results"] = regression_results
            item["benchmark_results"] = benchmark_results
            snapshot = PromotionManager(self.root).snapshot(candidate) if passed else {}
            item["verified_snapshot"] = snapshot
            passed = passed and bool(snapshot)
            item["status"] = "verified" if passed else "rejected"
            self._save(data)
            return SelfRepairProposal(**item)
        raise SelfRepairError("Self-repair proposal not found")

    def verify_from_evaluator(self, proposal_id: str,
                              evaluator: Callable[[Path], dict[str, Any]]) -> SelfRepairProposal:
        """Run a real evaluator against the candidate and record its evidence.

        The evaluator is invoked with the candidate directory and returns a dict
        with at least ``returncode`` (and usually ``output``). The candidate is
        hashed BEFORE the run (pre_digest) and AGAIN after it (post_digest). If
        the two digests differ, the candidate mutated during evaluation and the
        run is rejected. On success, the PRE-run digest is bound to the recorded
        evidence, so the evidence provably describes the exact bytes that ran.

        A fresh private run_nonce is minted and stored on the proposal; it is the
        only token that lets later public ``record_verification`` calls treat
        evidence as genuine evaluator evidence.

        Verified only when every returncode == 0 (counted exactly, no bools or
        floats) AND a real promotion snapshot of the candidate exists.
        """
        proposal = self.get(proposal_id)
        if not proposal:
            raise SelfRepairError("Self-repair proposal not found")
        candidate = Path(proposal.sandbox).resolve()
        if not candidate.is_dir():
            raise SelfRepairError("Candidate directory is missing")
        pre_digest = _dir_digest(candidate)
        result = evaluator(candidate)
        if not isinstance(result, dict) or type(result.get("returncode")) is not int:
            raise SelfRepairError("Evaluator must return {returncode: int, ...}")
        post_digest = _dir_digest(candidate)
        if post_digest != pre_digest:
            raise SelfRepairError("candidate changed during evaluation")
        # Environment identity is evidence only (R07, R13): it records which
        # environment produced the run without changing pass/fail semantics.
        environment = environment_identity(
            candidate, str(result.get("command", "")))
        run_nonce = secrets.token_hex(16)
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._run_verified_locked(
                proposal_id, run_nonce, pre_digest, result, environment)

    def _run_verified_locked(self, proposal_id: str, run_nonce: str, pre_digest: str,
                             result: dict[str, Any],
                             environment: Optional[dict[str, Any]] = None) -> SelfRepairProposal:
        data = self._load()
        for item in data:
            if item.get("proposal_id") != proposal_id:
                continue
            evidence = [{
                "source": "evaluator",
                "run_digest": pre_digest,
                "run_nonce": run_nonce,
                "returncode": int(result["returncode"]),
                "output": str(result.get("output", ""))[:4000],
                "staged": result.get("staged", True),
            }]
            # Persist honest runner provenance from the evaluator result (R07):
            # "runner" records container vs host; "isolation_unavailable" is set
            # only when auto fell back to the host runner.
            for key in ("runner", "isolation_unavailable"):
                if key in result:
                    evidence[0][key] = result[key]
            if environment is not None:
                evidence[0]["environment"] = environment
            # fresh_run: the manager just witnessed `pre_digest`; stamp that
            # nonce/digest pair as the new ground truth.
            return self._record_verification_locked(
                proposal_id, evidence, list(evidence),
                expected_nonce=run_nonce, witnessed_digest=pre_digest)
        raise SelfRepairError("Self-repair proposal not found")

    def _promotion_status(self, promotion_id: str) -> Optional[str]:
        """Return the journal status of a real promotion record, else None.

        ``PromotionManager.list()`` resolves each promotion directory under
        ``root/.ghost/promotions`` to {promotion_id, status} (verified,
        rolled_back, legacy_manual_review, corrupt_manual_review, ...). A bare
        string that does not correspond to a real promotion directory therefore
        returns None and cannot be used as apply/rollback evidence.
        """
        if not isinstance(promotion_id, str) or not promotion_id.strip():
            return None
        wanted = promotion_id.strip()
        for record in PromotionManager(self.root).list():
            if record.get("promotion_id") == wanted:
                return record.get("status")
        return None

    def mark_applied(self, proposal_id: str, promotion_id: str) -> SelfRepairProposal:
        """Mark a verified proposal applied, bound to a REAL verified promotion.

        The promotion_id must reference an actual promotion journal record whose
        journal status is "verified" — i.e. a promotion that really completed. A
        bare string not matching a real record is rejected. Only a currently
        verified proposal may be applied (invalid transition -> error).
        """
        required = "verified"
        if self._promotion_status(promotion_id) != required:
            raise SelfRepairError(
                f"Applied state requires a real promotion record with status '{required}'")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._mark_state_locked(proposal_id, "applied",
                                           {"promotion_id": promotion_id.strip()})

    def mark_rollback(self, proposal_id: str, promotion_id: str, reason: str) -> SelfRepairProposal:
        """Mark an applied proposal rolled back, bound to a REAL restoration.

        The promotion_id must reference an actual promotion journal record whose
        journal status is "rolled_back" — i.e. a restoration that really
        completed. A bare string not matching a real record is rejected. Only a
        currently applied proposal may be rolled back.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise SelfRepairError("Rollback needs a reason")
        required = "rolled_back"
        if self._promotion_status(promotion_id) != required:
            raise SelfRepairError(
                f"Rolled-back state requires a real promotion record with status '{required}'")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._mark_state_locked(proposal_id, "rolled_back",
                                           {"rollback_promotion_id": promotion_id.strip()})

    def _mark_state_locked(self, proposal_id: str, target: str,
                           updates: dict[str, Any]) -> SelfRepairProposal:
        data = self._load()
        for item in data:
            if item.get("proposal_id") == proposal_id:
                current = item["status"]
                allowed = {"verified": {"applied"}, "applied": {"rolled_back"}}
                if target not in allowed.get(current, set()):
                    raise SelfRepairError(
                        f"Invalid self-repair state transition: {current} -> {target}")
                for key, value in updates.items():
                    item[key] = value
                item["status"] = target
                self._save(data)
                return SelfRepairProposal(**item)
        raise SelfRepairError("Self-repair proposal not found")

    def request_promotion(self, proposal_id: str, goal_id: str) -> tuple[str, str, list[str]]:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._request_promotion_locked(proposal_id, goal_id)

    def _request_promotion_locked(self, proposal_id: str, goal_id: str) -> tuple[str, str, list[str]]:
        proposal = self.get(proposal_id)
        if not proposal or proposal.status != "verified" or not proposal.verified_snapshot:
            raise PermissionError("Only verified self-repair proposals can request promotion")
        policy = load_update_policy(self.root)
        for name in proposal.verified_snapshot:
            if name.replace("\\", "/") in policy["protected_paths"]:
                raise SelfRepairError(PROTECTED_PATH_MESSAGE.format(path=name))
        return PromotionService(self.root).request(
            goal_id, Path(proposal.sandbox), expected_snapshot=proposal.verified_snapshot)
