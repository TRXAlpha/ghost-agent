"""Schema registry and migration framework for `.ghost` stores.

Stores record their data version in `.ghost/schema.json`. A missing
schema.json means every store is at (current) version 1. Migration steps are
registered in MIGRATIONS as (store, from_version) -> callable(root).
"""

import json
import shutil
import time
from pathlib import Path
from typing import Callable

from .utils.storage import atomic_json

CURRENT_VERSIONS = {
    "scheduler": 1,
    "notifications": 1,
    "events": 2,
    "opportunities": 1,
    "goals": 1,
}

STORE_FILES = {
    "scheduler": "scheduler.json",
    "notifications": "notifications.json",
    "events": "events.jsonl",
    "opportunities": "opportunities.json",
    "goals": "goals.json",
}

MIGRATIONS: dict[tuple[str, int], Callable[[Path], None]] = {}


def _schema_path(root: Path) -> Path:
    return Path(root) / ".ghost" / "schema.json"


def _read_versions(root: Path) -> dict[str, int]:
    path = _schema_path(root)
    if not path.is_file():
        return {}
    data = json_load(path)
    stores = data.get("stores", {})
    return {name: int(version) for name, version in stores.items()}


def json_load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_versions(root: Path, versions: dict[str, int]) -> None:
    atomic_json(_schema_path(root), {"stores": versions, "updated_at": time.time()})


def check(root: Path) -> list[str]:
    """Report data versions newer than this Ghost can understand."""
    problems: list[str] = []
    for name, recorded in _read_versions(root).items():
        current = CURRENT_VERSIONS.get(name)
        if current is not None and recorded > current:
            problems.append(
                f"{name} data version {recorded} is newer than this Ghost ({current}); refusing"
            )
    return problems


def _backup(root: Path, store: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backup_dir = Path(root) / ".ghost" / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    source = Path(root) / ".ghost" / STORE_FILES[store]
    target = backup_dir / source.name
    shutil.copy2(source, target)
    return target


def apply(root: Path, dry_run: bool = False) -> list[str]:
    """Run registered migration steps for stores behind the current version."""
    root = Path(root)
    versions = dict(_read_versions(root))
    reports: list[str] = []
    for store in sorted(STORE_FILES):
        current = CURRENT_VERSIONS[store]
        recorded = versions.get(store, 1)
        if recorded >= current:
            continue
        missing = [v for v in range(recorded, current) if (store, v) not in MIGRATIONS]
        if missing:
            raise ValueError(
                f"no migration step registered for {store} version {missing[0]}"
            )
        reports.append(f"{store}: {recorded} -> {current}" + (" (dry run)" if dry_run else ""))
        if dry_run:
            continue
        _backup(root, store)
        for version in range(recorded, current):
            MIGRATIONS[(store, version)](root)
        versions[store] = current
        _write_versions(root, versions)
    return reports


def stamp(root: Path) -> list[str]:
    """Record current versions for store files present but not yet in schema.json."""
    root = Path(root)
    versions = _read_versions(root)
    changed = False
    for store, filename in STORE_FILES.items():
        if (root / ".ghost" / filename).is_file() and store not in versions:
            versions[store] = CURRENT_VERSIONS[store]
            changed = True
    if changed:
        _write_versions(root, versions)
    return sorted(versions)
