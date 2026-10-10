"""rustup installs rustc in ~/.cargo/bin, often absent from PATH for background processes."""
from pathlib import Path

import ghost_agent.learner as learner_mod
from ghost_agent.learner import RustCompileOracle


def test_falls_back_to_cargo_bin_when_not_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / ".cargo" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "rustc.exe"
    exe.write_text("")
    monkeypatch.setattr(learner_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(learner_mod.shutil, "which", lambda name: str(name) if Path(str(name)).is_file() else None)
    oracle = RustCompileOracle()
    assert oracle.rustc == str(exe)
    assert oracle.available()


def test_path_rustc_and_explicit_paths_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(learner_mod.shutil, "which", lambda name: "C:/x/rustc.exe" if name == "rustc" else None)
    assert RustCompileOracle().rustc == "rustc"
    assert RustCompileOracle(rustc="D:/tools/rustc.exe").rustc == "D:/tools/rustc.exe"


def test_missing_everywhere_stays_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(learner_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(learner_mod.shutil, "which", lambda name: None)
    assert RustCompileOracle().available() is False
