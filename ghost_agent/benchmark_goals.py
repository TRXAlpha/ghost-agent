"""Eight fixed benchmark goals with hidden acceptance tests for ghost bench."""
from __future__ import annotations

import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class BenchGoal:
    """One benchmark goal with its hidden acceptance test."""
    goal_id: str
    module_file: str
    goal: str
    hidden_test: str


BENCH_GOALS: tuple[BenchGoal, ...] = (
    BenchGoal(
        goal_id="roman",
        module_file="roman.py",
        goal="Create roman.py with to_roman(n: int) -> str for 1 <= n <= 3999 and from_roman(s: str) -> int using standard subtractive notation (IV, IX, XL, XC, CD, CM). Both raise ValueError on out-of-range or invalid input (including empty strings and characters other than IVXLCDM). Add pytest tests in test_roman.py.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from roman import to_roman, from_roman

            def test_known_values():
                assert to_roman(1994) == "MCMXCIV"
                assert to_roman(3999) == "MMMCMXCIX"
                assert to_roman(4) == "IV"
                assert from_roman("MCMXCIV") == 1994
                assert from_roman("XL") == 40

            def test_roundtrip():
                for n in range(1, 4000):
                    assert from_roman(to_roman(n)) == n

            @pytest.mark.parametrize("bad", [0, -1, 4000])
            def test_to_roman_range(bad):
                with pytest.raises(ValueError):
                    to_roman(bad)

            @pytest.mark.parametrize("bad", ["", "ABC", "12"])
            def test_from_roman_invalid(bad):
                with pytest.raises(ValueError):
                    from_roman(bad)
        """),
    ),
    BenchGoal(
        goal_id="wordfreq",
        module_file="wordfreq.py",
        goal="Create wordfreq.py with top_words(text: str, n: int) -> list[tuple[str, int]]. Lowercase the text; a word is a maximal run of letters a-z; return the n most frequent (word, count) pairs sorted by count descending, then word ascending; n <= 0 returns an empty list. Add pytest tests in test_wordfreq.py.",
        hidden_test=textwrap.dedent("""\
            from wordfreq import top_words

            def test_counts_and_order():
                assert top_words("b a B c a b", 2) == [("b", 3), ("a", 2)]

            def test_ties_alphabetical():
                assert top_words("y x", 5) == [("x", 1), ("y", 1)]

            def test_punctuation_and_case():
                assert top_words("Hello, hello! HELLO?", 1) == [("hello", 3)]

            def test_non_positive_n():
                assert top_words("a a", 0) == []
                assert top_words("a a", -3) == []

            def test_empty_text():
                assert top_words("", 3) == []
        """),
    ),
    BenchGoal(
        goal_id="rpn",
        module_file="rpn.py",
        goal="Create rpn.py with evaluate(expr: str) -> float that evaluates a reverse Polish notation expression with whitespace-separated numbers and the operators + - * /. Raise ValueError for malformed expressions (too few operands, leftover operands, unknown tokens, empty input) and let division by zero raise ZeroDivisionError. Add pytest tests in test_rpn.py.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from rpn import evaluate

            def test_basic():
                assert evaluate("3 4 + 2 *") == 14
                assert evaluate("5 1 2 + 4 * + 3 -") == 14
                assert evaluate("2 3 /") == pytest.approx(2 / 3)
                assert evaluate("-2 3 *") == -6

            @pytest.mark.parametrize("bad", ["", "1 +", "1 2", "1 x +", "+"])
            def test_malformed(bad):
                with pytest.raises(ValueError):
                    evaluate(bad)

            def test_division_by_zero():
                with pytest.raises(ZeroDivisionError):
                    evaluate("1 0 /")
        """),
    ),
    BenchGoal(
        goal_id="lru",
        module_file="lru.py",
        goal="Create lru.py with class LRUCache(capacity: int) providing get(key) (returns the value or None, and marks the key as most recently used) and put(key, value) (inserts or updates; when over capacity, evicts the least recently used key). capacity < 1 raises ValueError. Add pytest tests in test_lru.py.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from lru import LRUCache

            def test_eviction_order():
                c = LRUCache(2)
                c.put("a", 1)
                c.put("b", 2)
                assert c.get("a") == 1
                c.put("c", 3)
                assert c.get("b") is None
                assert c.get("a") == 1
                assert c.get("c") == 3

            def test_update_refreshes():
                c = LRUCache(2)
                c.put("a", 1)
                c.put("b", 2)
                c.put("a", 10)
                c.put("c", 3)
                assert c.get("a") == 10
                assert c.get("b") is None

            def test_missing_key():
                assert LRUCache(1).get("x") is None

            @pytest.mark.parametrize("bad", [0, -1])
            def test_bad_capacity(bad):
                with pytest.raises(ValueError):
                    LRUCache(bad)
        """),
    ),
    BenchGoal(
        goal_id="csvstats",
        module_file="csvstats.py",
        goal="Create csvstats.py with column_means(csv_text: str) -> dict[str, float]. The first row is the header. A column is numeric if every non-empty cell in it parses as float; empty cells are ignored; columns that are not numeric or have no non-empty cells are omitted. Use the csv module. Add pytest tests in test_csvstats.py.",
        hidden_test=textwrap.dedent("""\
            from csvstats import column_means

            def test_mixed_columns():
                text = "a,b,c\\n1,x,2\\n3,y,\\n"
                assert column_means(text) == {"a": 2.0, "c": 2.0}

            def test_all_empty_column_omitted():
                assert column_means("a,b\\n1,\\n2,\\n") == {"a": 1.5}

            def test_quoted_values():
                assert column_means('name,score\\n"Smith, J",4\\n"Lee",6\\n') == {"score": 5.0}

            def test_header_only():
                assert column_means("a,b\\n") == {}
        """),
    ),
    BenchGoal(
        goal_id="todo",
        module_file="todo.py",
        goal="Create todo.py with class TodoStore(path) that persists to a JSON file at path. Methods: add(title: str) -> int returns a new id starting at 1 and increasing; items() -> list[dict] returns dicts with keys id, title, done in id order; complete(item_id: int) marks it done and raises KeyError for unknown ids. A new TodoStore on the same path must see earlier data; a missing file means an empty store. Add pytest tests in test_todo.py using tmp_path.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from todo import TodoStore

            def test_add_and_persist(tmp_path):
                path = tmp_path / "todo.json"
                s = TodoStore(path)
                assert s.add("milk") == 1
                assert s.add("eggs") == 2
                s.complete(1)
                again = TodoStore(path)
                assert again.items() == [
                    {"id": 1, "title": "milk", "done": True},
                    {"id": 2, "title": "eggs", "done": False},
                ]
                assert again.add("bread") == 3

            def test_unknown_id(tmp_path):
                with pytest.raises(KeyError):
                    TodoStore(tmp_path / "t.json").complete(99)

            def test_missing_file_is_empty(tmp_path):
                assert TodoStore(tmp_path / "none.json").items() == []
        """),
    ),
    BenchGoal(
        goal_id="tictactoe",
        module_file="tictactoe.py",
        goal="Create tictactoe.py with winner(board: list[str]) -> str | None. board is exactly 3 strings of exactly 3 characters, each 'X', 'O' or '.'. Return 'X' or 'O' if that player has three in a row (row, column or diagonal), 'draw' if the board is full with no winner, otherwise None. Raise ValueError for any other shape or character. Add pytest tests in test_tictactoe.py.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from tictactoe import winner

            def test_row_col_diag():
                assert winner(["XXX", "OO.", "..."]) == "X"
                assert winner(["O..", "OX.", "O.X"]) == "O"
                assert winner(["X.O", ".XO", "..X"]) == "X"
                assert winner(["..O", ".O.", "O.X"]) == "O"

            def test_draw_and_ongoing():
                assert winner(["XOX", "XOO", "OXX"]) == "draw"
                assert winner(["X..", "...", "..."]) is None

            @pytest.mark.parametrize("bad", [["XXX", "OO."], ["XXXX", "...", "..."], ["XXA", "...", "..."]])
            def test_invalid(bad):
                with pytest.raises(ValueError):
                    winner(bad)
        """),
    ),
    BenchGoal(
        goal_id="intervals",
        module_file="intervals.py",
        goal="Create intervals.py with merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]] that returns the intervals sorted by start with overlapping or touching intervals merged ((1, 3) and (3, 5) become (1, 5)). Raise ValueError if any start > end. Do not modify the input list. Add pytest tests in test_intervals.py.",
        hidden_test=textwrap.dedent("""\
            import pytest
            from intervals import merge

            def test_merge():
                assert merge([(5, 6), (1, 3), (2, 4)]) == [(1, 4), (5, 6)]
                assert merge([(1, 3), (3, 5)]) == [(1, 5)]
                assert merge([]) == []
                assert merge([(1, 10), (2, 3)]) == [(1, 10)]

            def test_input_not_modified():
                data = [(3, 4), (1, 2)]
                merge(data)
                assert data == [(3, 4), (1, 2)]

            def test_invalid():
                with pytest.raises(ValueError):
                    merge([(5, 1)])
        """),
    ),
)