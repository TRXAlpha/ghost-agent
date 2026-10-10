"""Tests for `ghost protocol-bench` CLI forwarding (TH4)."""
import argparse
import sys

import pytest

import ghost_agent.cli as cli
from ghost_agent.cli import _cmd_protocol_bench


def test_protocol_bench_help_exits_zero(monkeypatch, capsys) -> None:
    """`ghost protocol-bench --help` works through the main CLI parser."""
    monkeypatch.setattr(sys, "argv", ["ghost", "protocol-bench", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--output-format" in out
    assert "protocol-bench" in out


def test_protocol_bench_forwards_to_module_main(monkeypatch, capsys) -> None:
    """The handler rebuilds argv and calls protocol_benchmark.main() (main patched)."""
    calls: list[list[str]] = []

    def fake_main() -> None:
        calls.append(list(sys.argv))

    monkeypatch.setattr("ghost_agent.protocol_benchmark.main", fake_main)
    args = argparse.Namespace(
        output_root="out/x",
        model="m:1b",
        base_url="http://h:1",
        repetitions=3,
        prompt_style="compact",
        dataset="validation",
        output_format="json",
    )
    _cmd_protocol_bench(args)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "ghost protocol-bench"
    assert "--output-root=out/x" in argv
    assert "--model=m:1b" in argv
    assert "--repetitions=3" in argv
    assert "--prompt-style=compact" in argv
    assert "--dataset=validation" in argv
    assert "--output-format=json" in argv
    assert capsys.readouterr().out == ""
