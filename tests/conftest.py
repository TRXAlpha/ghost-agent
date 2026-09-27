"""Skip OpenJarvis bridge tests on interpreters the pinned upstream rejects.

The OpenJarvis bridge hard-fails on Python outside 3.11-3.13
(`require_upstream` raises IntegrationError). On Python 3.14 these seven
bridge test files always fail at runtime regardless of code correctness,
which would block every sandbox suite-verification gate. Skipping them on
unsupported interpreters is documented, version-conditional, and preserves
the tests themselves: they run normally under 3.11-3.13.
"""
from __future__ import annotations

import sys

import pytest

collect_ignore_glob = []

if not (3, 11) <= sys.version_info[:2] < (3, 14):
    collect_ignore_glob = [
        "test_openjarvis_*.py",
    ]

pytest_collect_ignore = list(collect_ignore_glob)
