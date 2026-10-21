"""Held-out benchmark integrity: every hidden test is solvable and discriminating.

A reference solution must pass each hidden test; the buggy starter (bug-fix
goals) or an empty module (greenfield goals) must fail it. This protects the
evolution engine's promotion gate from broken or toothless tests.
"""
import subprocess
import sys
import textwrap

import pytest

from ghost_agent.benchmark_heldout import HELDOUT_GOALS

_D = textwrap.dedent

REFERENCES = {
    "slugify": _D("""\
        import re, unicodedata
        def slugify(text, max_length=0):
            text = unicodedata.normalize("NFKD", text)
            text = "".join(c for c in text if not unicodedata.combining(c)).lower()
            slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
            if max_length > 0:
                slug = slug[:max_length].rstrip("-")
            return slug
    """),
    "matrix": _D("""\
        class Matrix:
            def __init__(self, rows):
                if not rows or not rows[0] or any(len(r) != len(rows[0]) for r in rows):
                    raise ValueError("bad rows")
                self.rows = [list(r) for r in rows]
            @property
            def shape(self):
                return (len(self.rows), len(self.rows[0]))
            def transpose(self):
                return Matrix([list(c) for c in zip(*self.rows)])
            def __matmul__(self, other):
                if self.shape[1] != other.shape[0]:
                    raise ValueError("shape")
                cols = list(zip(*other.rows))
                return Matrix([[sum(a * b for a, b in zip(r, c)) for c in cols] for r in self.rows])
            def __add__(self, other):
                if self.shape != other.shape:
                    raise ValueError("shape")
                return Matrix([[a + b for a, b in zip(r1, r2)] for r1, r2 in zip(self.rows, other.rows)])
            def __eq__(self, other):
                return isinstance(other, Matrix) and self.rows == other.rows
            @staticmethod
            def identity(n):
                return Matrix([[1 if i == j else 0 for j in range(n)] for i in range(n)])
    """),
    "inventory_fix": _D("""\
        class Inventory:
            def __init__(self):
                self.items = {}
            def add(self, name, qty, price):
                if qty <= 0 or price < 0:
                    raise ValueError("bad")
                old = self.items.get(name, (0, price))[0]
                self.items[name] = (old + qty, price)
            def remove(self, name, qty):
                old, price = self.items[name]
                if qty > old:
                    raise ValueError("too many")
                if old - qty == 0:
                    del self.items[name]
                else:
                    self.items[name] = (old - qty, price)
            def total_value(self):
                return sum(q * p for q, p in self.items.values())
    """),
    "dates_fix": _D("""\
        def is_leap(year):
            return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        def days_in_month(year, month):
            if not 1 <= month <= 12:
                raise ValueError("month")
            if month == 2:
                return 29 if is_leap(year) else 28
            return 30 if month in (4, 6, 9, 11) else 31
        def add_months(year, month, day, n):
            index = year * 12 + (month - 1) + n
            year, month = divmod(index, 12)
            month += 1
            return (year, month, min(day, days_in_month(year, month)))
    """),
    "calc_expr": _D("""\
        import re
        def evaluate(expr):
            tokens = re.findall(r"\\d+\\.?\\d*|[-+*/()]|\\S", expr)
            if not tokens:
                raise ValueError("empty")
            pos = 0
            def peek():
                return tokens[pos] if pos < len(tokens) else None
            def take():
                nonlocal pos
                tok = peek(); pos += 1; return tok
            def expr_():
                value = term()
                while peek() in ("+", "-"):
                    op = take(); rhs = term()
                    value = value + rhs if op == "+" else value - rhs
                return value
            def term():
                value = factor()
                while peek() in ("*", "/"):
                    op = take(); rhs = factor()
                    value = value * rhs if op == "*" else value / rhs
                return value
            def factor():
                tok = take()
                if tok == "-":
                    return -factor()
                if tok == "(":
                    value = expr_()
                    if take() != ")":
                        raise ValueError("paren")
                    return value
                if tok is not None and re.fullmatch(r"\\d+\\.?\\d*", tok):
                    return float(tok)
                raise ValueError(f"unexpected {tok!r}")
            result = expr_()
            if pos != len(tokens):
                raise ValueError("trailing")
            return result
    """),
    "toposort": _D("""\
        import heapq
        def toposort(deps):
            nodes = set(deps) | {d for ds in deps.values() for d in ds}
            indeg = {n: 0 for n in nodes}
            users = {n: [] for n in nodes}
            for node, ds in deps.items():
                for d in ds:
                    indeg[node] += 1
                    users[d].append(node)
            ready = [n for n in nodes if indeg[n] == 0]
            heapq.heapify(ready)
            out = []
            while ready:
                n = heapq.heappop(ready); out.append(n)
                for u in users[n]:
                    indeg[u] -= 1
                    if indeg[u] == 0:
                        heapq.heappush(ready, u)
            if len(out) != len(nodes):
                raise ValueError("cycle among " + ", ".join(sorted(n for n in nodes if indeg[n] > 0)))
            return out
    """),
    "ledger": _D("""\
        class InsufficientFunds(Exception):
            pass
        class Ledger:
            def __init__(self):
                self._bal, self._hist = {}, {}
            def open(self, account):
                if account in self._bal:
                    raise ValueError("exists")
                self._bal[account] = 0; self._hist[account] = []
            def _check(self, account, amount):
                if account not in self._bal:
                    raise KeyError(account)
                if type(amount) is not int or amount <= 0:
                    raise ValueError("amount")
            def deposit(self, account, amount):
                self._check(account, amount)
                self._bal[account] += amount; self._hist[account].append(("deposit", amount))
            def withdraw(self, account, amount):
                self._check(account, amount)
                if self._bal[account] < amount:
                    raise InsufficientFunds(account)
                self._bal[account] -= amount; self._hist[account].append(("withdraw", amount))
            def transfer(self, src, dst, amount):
                self._check(src, amount); self._check(dst, amount)
                if self._bal[src] < amount:
                    raise InsufficientFunds(src)
                self._bal[src] -= amount; self._bal[dst] += amount
                self._hist[src].append(("transfer_out", amount)); self._hist[dst].append(("transfer_in", amount))
            def balance(self, account):
                return self._bal[account]
            def history(self, account):
                return list(self._hist[account])
    """),
    "textstats_fix": _D("""\
        import re
        def _words(text):
            return re.findall(r"[A-Za-z0-9']+", text)
        def word_count(text):
            return len(_words(text))
        def sentence_count(text):
            parts = re.split(r"[.!?]+", text)
            return sum(1 for part in parts if _words(part))
        def average_word_length(text):
            words = _words(text)
            return sum(len(w) for w in words) / len(words) if words else 0.0
    """),
}


def _run(tmp_path, module_file, module_source, hidden_test):
    (tmp_path / module_file).write_text(module_source, encoding="utf-8")
    (tmp_path / "test_hidden_check.py").write_text(hidden_test, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden_check.py"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)


def test_every_heldout_goal_has_a_reference():
    assert {g.goal_id for g in HELDOUT_GOALS} == set(REFERENCES)
    assert all(g.split == "heldout" for g in HELDOUT_GOALS)


@pytest.mark.parametrize("goal", HELDOUT_GOALS, ids=lambda g: g.goal_id)
def test_reference_passes_hidden_test(goal, tmp_path):
    result = _run(tmp_path, goal.module_file, REFERENCES[goal.goal_id], goal.hidden_test)
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize("goal", HELDOUT_GOALS, ids=lambda g: g.goal_id)
def test_starter_or_empty_module_fails_hidden_test(goal, tmp_path):
    starter = dict(goal.starter_files).get(goal.module_file, "")
    result = _run(tmp_path, goal.module_file, starter, goal.hidden_test)
    assert result.returncode != 0
