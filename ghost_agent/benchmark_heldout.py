"""Held-out benchmark goals (human-written; never used to drive evolution).

These only gate strategy promotion in ``ghost evolve``. Three are bug-fix
tasks that start from existing, subtly broken code (``starter_files``), so
the benchmark also measures working in an existing project, not only
greenfield creation. tests/test_benchmark_heldout_integrity.py proves every
hidden test passes a reference solution and fails the buggy starter.
"""
from __future__ import annotations

import textwrap

from .benchmark_goals import BenchGoal

_D = textwrap.dedent

HELDOUT_GOALS: tuple = (
    BenchGoal(
        goal_id="slugify",
        module_file="slugify.py",
        split="heldout",
        goal=("Create slugify.py with slugify(text: str, max_length: int = 0) -> str. Lowercase the text, "
              "replace every run of characters that are not a-z or 0-9 with a single hyphen, strip leading and "
              "trailing hyphens. Accented Latin letters are transliterated to plain ASCII first (use "
              "unicodedata NFKD and drop combining marks). If max_length > 0, cut the result to at most "
              "max_length characters without leaving a trailing hyphen. Add pytest tests in test_slugify.py."),
        hidden_test=_D("""\
            from slugify import slugify

            def test_basic():
                assert slugify("Hello, World!") == "hello-world"
                assert slugify("  --Already--Slugged--  ") == "already-slugged"

            def test_accents_and_runs():
                assert slugify("Crème Brûlée & Café") == "creme-brulee-cafe"
                assert slugify("a___b...c") == "a-b-c"

            def test_max_length_no_trailing_hyphen():
                assert slugify("hello world again", max_length=6) == "hello"
                assert slugify("hello world", max_length=0) == "hello-world"

            def test_empty_and_symbols():
                assert slugify("") == ""
                assert slugify("!!!") == ""
        """),
    ),
    BenchGoal(
        goal_id="matrix",
        module_file="matrix.py",
        split="heldout",
        goal=("Create matrix.py with class Matrix(rows: list[list[float]]). Rows must be non-empty and all the "
              "same length, else ValueError. Provide: shape -> (rows, cols) property; transpose() -> Matrix; "
              "__matmul__ for matrix multiplication (ValueError on incompatible shapes); __add__ for element-wise "
              "addition (ValueError on shape mismatch); __eq__ comparing values; and identity(n) as a "
              "staticmethod. Do not mutate inputs. Add pytest tests in test_matrix.py."),
        hidden_test=_D("""\
            import pytest
            from matrix import Matrix

            def test_shape_and_transpose():
                m = Matrix([[1, 2, 3], [4, 5, 6]])
                assert m.shape == (2, 3)
                assert m.transpose() == Matrix([[1, 4], [2, 5], [3, 6]])

            def test_matmul_and_identity():
                a = Matrix([[1, 2], [3, 4]])
                b = Matrix([[5, 6], [7, 8]])
                assert a @ b == Matrix([[19, 22], [43, 50]])
                assert a @ Matrix.identity(2) == a

            def test_add_and_errors():
                a = Matrix([[1, 2], [3, 4]])
                assert a + a == Matrix([[2, 4], [6, 8]])
                with pytest.raises(ValueError):
                    a @ Matrix([[1, 2, 3]])
                with pytest.raises(ValueError):
                    a + Matrix([[1, 2]])
                with pytest.raises(ValueError):
                    Matrix([[1, 2], [3]])
                with pytest.raises(ValueError):
                    Matrix([])

            def test_inputs_not_mutated():
                rows = [[1, 2], [3, 4]]
                Matrix(rows).transpose()
                assert rows == [[1, 2], [3, 4]]
        """),
    ),
    BenchGoal(
        goal_id="inventory_fix",
        module_file="inventory.py",
        split="heldout",
        goal=("inventory.py already exists in the project and has bugs. Fix it so that: add(name, qty, price) "
              "adds quantity (qty must be > 0, price >= 0, else ValueError) and a later add with a different "
              "price updates the price; remove(name, qty) raises KeyError for unknown items and ValueError if "
              "qty exceeds stock, and deletes the item when its quantity reaches 0; total_value() returns the "
              "sum of qty * price over all items. Keep the existing class name Inventory and method names. "
              "Add pytest tests in test_inventory.py."),
        starter_files=(("inventory.py", _D("""\
            class Inventory:
                def __init__(self):
                    self.items = {}

                def add(self, name, qty, price):
                    if name in self.items:
                        old_qty, old_price = self.items[name]
                        self.items[name] = (old_qty + qty, old_price)
                    else:
                        self.items[name] = (qty, price)

                def remove(self, name, qty):
                    old_qty, price = self.items[name]
                    self.items[name] = (old_qty - qty, price)

                def total_value(self):
                    return sum(qty for qty, price in self.items.values())
        """)),),
        hidden_test=_D("""\
            import pytest
            from inventory import Inventory

            def test_add_remove_and_value():
                inv = Inventory()
                inv.add("apple", 3, 2.0)
                inv.add("pear", 1, 5.0)
                assert inv.total_value() == 11.0
                inv.remove("apple", 3)
                assert "apple" not in inv.items
                assert inv.total_value() == 5.0

            def test_price_update_on_add():
                inv = Inventory()
                inv.add("apple", 1, 2.0)
                inv.add("apple", 1, 3.0)
                assert inv.total_value() == 6.0

            def test_errors():
                inv = Inventory()
                inv.add("apple", 2, 1.0)
                with pytest.raises(ValueError):
                    inv.remove("apple", 5)
                with pytest.raises(KeyError):
                    inv.remove("kiwi", 1)
                with pytest.raises(ValueError):
                    inv.add("x", 0, 1.0)
                with pytest.raises(ValueError):
                    inv.add("x", 1, -1.0)
        """),
    ),
    BenchGoal(
        goal_id="dates_fix",
        module_file="dateutil_lite.py",
        split="heldout",
        goal=("dateutil_lite.py already exists in the project and has bugs. Fix is_leap(year) (Gregorian rules: "
              "divisible by 4, except centuries unless divisible by 400), days_in_month(year, month) (ValueError "
              "for months outside 1-12) and add_months(year, month, day, n) which returns a (year, month, day) "
              "tuple n months later (n may be negative), clamping the day to the last day of the target month. "
              "Do not use the datetime module. Keep the function names. Add pytest tests in test_dateutil_lite.py."),
        starter_files=(("dateutil_lite.py", _D("""\
            def is_leap(year):
                return year % 4 == 0

            def days_in_month(year, month):
                if month == 2:
                    return 29 if is_leap(year) else 28
                if month in (4, 6, 9, 11):
                    return 30
                return 31

            def add_months(year, month, day, n):
                month = month + n
                year = year + month // 12
                month = month % 12
                return (year, month, min(day, days_in_month(year, month)))
        """)),),
        hidden_test=_D("""\
            import pytest
            from dateutil_lite import add_months, days_in_month, is_leap

            def test_leap_rules():
                assert is_leap(2024) and is_leap(2000)
                assert not is_leap(1900) and not is_leap(2023)

            def test_days_in_month():
                assert days_in_month(2024, 2) == 29
                assert days_in_month(1900, 2) == 28
                assert days_in_month(2023, 4) == 30
                with pytest.raises(ValueError):
                    days_in_month(2023, 13)
                with pytest.raises(ValueError):
                    days_in_month(2023, 0)

            def test_add_months():
                assert add_months(2024, 1, 31, 1) == (2024, 2, 29)
                assert add_months(2023, 11, 15, 2) == (2024, 1, 15)
                assert add_months(2024, 12, 31, 12) == (2025, 12, 31)
                assert add_months(2024, 3, 31, -1) == (2024, 2, 29)
                assert add_months(2024, 1, 10, -13) == (2022, 12, 10)
        """),
    ),
    BenchGoal(
        goal_id="calc_expr",
        module_file="calc.py",
        split="heldout",
        goal=("Create calc.py with evaluate(expr: str) -> float that evaluates arithmetic expressions with + - * / "
              "and parentheses, standard precedence, left associativity, unary minus (e.g. '-3', '2*-4', '-(1+2)'), "
              "integer and decimal numbers and arbitrary whitespace. Raise ValueError for malformed input "
              "(empty, unbalanced parentheses, dangling operators, unknown characters) and let division by zero "
              "raise ZeroDivisionError. Do not use eval, exec or ast. Add pytest tests in test_calc.py."),
        hidden_test=_D("""\
            import pytest
            from calc import evaluate

            def test_precedence_and_assoc():
                assert evaluate("1 + 2 * 3") == 7
                assert evaluate("(1 + 2) * 3") == 9
                assert evaluate("8 - 3 - 2") == 3
                assert evaluate("8 / 4 / 2") == 1

            def test_unary_and_decimals():
                assert evaluate("-3 + 5") == 2
                assert evaluate("2*-4") == -8
                assert evaluate("-(1+2)") == -3
                assert evaluate("1.5 * 2") == pytest.approx(3.0)

            @pytest.mark.parametrize("bad", ["", "(1+2", "1+", "2 ** 3", "1 $ 2", "()", "3 4"])
            def test_malformed(bad):
                with pytest.raises(ValueError):
                    evaluate(bad)

            def test_div_zero():
                with pytest.raises(ZeroDivisionError):
                    evaluate("1/(2-2)")
        """),
    ),
    BenchGoal(
        goal_id="toposort",
        module_file="toposort.py",
        split="heldout",
        goal=("Create toposort.py with toposort(deps: dict[str, list[str]]) -> list[str], where deps maps each "
              "node to the nodes it depends on (dependencies must come first). Nodes that only appear as "
              "dependencies are included too. Among nodes that are ready at the same time, order them "
              "alphabetically so the result is deterministic. Raise ValueError naming the cycle when the graph "
              "has a cycle. Do not mutate the input. Add pytest tests in test_toposort.py."),
        hidden_test=_D("""\
            import pytest
            from toposort import toposort

            def test_order_and_determinism():
                deps = {"app": ["lib", "util"], "lib": ["util"], "util": []}
                assert toposort(deps) == ["util", "lib", "app"]
                assert toposort({"b": [], "a": [], "c": ["a"]}) == ["a", "b", "c"]

            def test_dependency_only_nodes_included():
                assert toposort({"x": ["y"]}) == ["y", "x"]

            def test_cycle():
                with pytest.raises(ValueError):
                    toposort({"a": ["b"], "b": ["a"]})
                with pytest.raises(ValueError):
                    toposort({"a": ["a"]})

            def test_input_not_mutated():
                deps = {"a": ["b"], "b": []}
                toposort(deps)
                assert deps == {"a": ["b"], "b": []}
        """),
    ),
    BenchGoal(
        goal_id="ledger",
        module_file="ledger.py",
        split="heldout",
        goal=("Create ledger.py with class InsufficientFunds(Exception) and class Ledger. Methods: open(account: str) "
              "(ValueError if it exists), deposit(account, amount), withdraw(account, amount), "
              "transfer(src, dst, amount), balance(account) -> int and history(account) -> list[tuple[str, int]] "
              "of (kind, amount) entries in order, where kind is 'deposit', 'withdraw', 'transfer_in' or "
              "'transfer_out'. Amounts are positive integers (ValueError otherwise); unknown accounts raise "
              "KeyError; overdrawing raises InsufficientFunds and changes nothing (a failed transfer leaves both "
              "accounts and histories unchanged). Add pytest tests in test_ledger.py."),
        hidden_test=_D("""\
            import pytest
            from ledger import InsufficientFunds, Ledger

            def test_flow_and_history():
                led = Ledger()
                led.open("a"); led.open("b")
                led.deposit("a", 100)
                led.transfer("a", "b", 30)
                led.withdraw("b", 10)
                assert led.balance("a") == 70 and led.balance("b") == 20
                assert led.history("a") == [("deposit", 100), ("transfer_out", 30)]
                assert led.history("b") == [("transfer_in", 30), ("withdraw", 10)]

            def test_failed_transfer_is_atomic():
                led = Ledger()
                led.open("a"); led.open("b")
                led.deposit("a", 5)
                with pytest.raises(InsufficientFunds):
                    led.transfer("a", "b", 10)
                assert led.balance("a") == 5 and led.balance("b") == 0
                assert led.history("b") == []

            def test_errors():
                led = Ledger()
                led.open("a")
                with pytest.raises(ValueError):
                    led.open("a")
                with pytest.raises(KeyError):
                    led.deposit("zz", 1)
                for bad in (0, -5, 1.5):
                    with pytest.raises(ValueError):
                        led.deposit("a", bad)
        """),
    ),
    BenchGoal(
        goal_id="textstats_fix",
        module_file="textstats.py",
        split="heldout",
        goal=("textstats.py already exists in the project and has bugs. Fix word_count(text) (words are maximal runs "
              "of letters, digits or apostrophes; punctuation and multiple spaces must not create empty words), "
              "sentence_count(text) (sentences end with ., ! or ?; repeated terminators like '?!' or '...' end one "
              "sentence; trailing text without a terminator still counts as a sentence if it contains a word; empty "
              "text has 0 sentences) and average_word_length(text) (0.0 for no words). Keep the function names. "
              "Add pytest tests in test_textstats.py."),
        starter_files=(("textstats.py", _D("""\
            def word_count(text):
                return len(text.split(" "))

            def sentence_count(text):
                return text.count(".") + text.count("!") + text.count("?")

            def average_word_length(text):
                words = text.split(" ")
                return sum(len(w) for w in words) / len(words)
        """)),),
        hidden_test=_D("""\
            import pytest
            from textstats import average_word_length, sentence_count, word_count

            def test_word_count():
                assert word_count("Hello,  world! It's 2024.") == 4
                assert word_count("") == 0
                assert word_count("   ...   ") == 0

            def test_sentence_count():
                assert sentence_count("Hi. How are you?! Fine...") == 3
                assert sentence_count("No terminator here") == 1
                assert sentence_count("") == 0
                assert sentence_count("Wait... what?") == 2

            def test_average_word_length():
                assert average_word_length("ab abcd") == pytest.approx(3.0)
                assert average_word_length("") == 0.0
        """),
    ),
)
