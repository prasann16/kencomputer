"""The Mac app gets Claude Code and Codex from this computer or a one-time download (runtimes.py)."""
import json

import runtimes


def test_terminal_install_needs_nothing(tmp_path, monkeypatch):
    # pip ships both, so nothing is ever found or downloaded.
    monkeypatch.setattr(runtimes, "PINS", tmp_path / "none.json")
    assert not runtimes.missing("claude") and not runtimes.missing("codex")
    assert runtimes.codex_bin() is None


def test_version_rules():
    assert runtimes._version("2.1.300 (Claude Code)") == (2, 1, 300)
    assert runtimes._version("codex-cli 0.163.0") == (0, 163, 0)
    # Claude Code: the user's own copy if it's at least as new.
    assert runtimes._good_enough("claude", (2, 1, 310), (2, 1, 300))
    assert not runtimes._good_enough("claude", (2, 1, 200), (2, 1, 300))
    # Codex: the same minor version only.
    assert runtimes._good_enough("codex", (0, 163, 2), (0, 163, 0))
    assert not runtimes._good_enough("codex", (0, 164, 0), (0, 163, 0))


def test_remembers_a_copy_only_for_the_same_version(tmp_path, monkeypatch):
    pins = tmp_path / "runtimes.json"
    pins.write_text(json.dumps({"codex": {"version": "0.163.0", "url": "", "sha256": ""}}))
    monkeypatch.setattr(runtimes, "PINS", pins)
    monkeypatch.setattr(runtimes, "_pip_has", lambda name: False)
    monkeypatch.setattr(runtimes, "_chosen", {})
    monkeypatch.setenv("KEN_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("CODEX_BIN", raising=False)
    codex = tmp_path / "bin" / "codex"
    codex.parent.mkdir()
    codex.write_text("#!/bin/sh\n")
    (tmp_path / "runtimes").mkdir()
    (tmp_path / "runtimes" / "codex.path").write_text(f"{codex}\n0.162.0")
    runtimes.setup()
    assert runtimes.missing("codex")  # checked against an older Ken's Codex: look again
    (tmp_path / "runtimes" / "codex.path").write_text(f"{codex}\n0.163.0")
    runtimes.setup()
    assert not runtimes.missing("codex") and runtimes.codex_bin() == str(codex)
