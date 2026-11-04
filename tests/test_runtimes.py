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


async def test_failed_download_shows_and_try_again_works(tmp_path, monkeypatch):
    pins = tmp_path / "runtimes.json"
    pins.write_text(json.dumps({"claude": {"version": "2.1.300", "url": "", "sha256": ""}}))
    monkeypatch.setattr(runtimes, "PINS", pins)
    monkeypatch.setattr(runtimes, "_pip_has", lambda name: False)
    monkeypatch.setattr(runtimes, "_chosen", {})
    monkeypatch.setattr(runtimes, "_state", {})
    monkeypatch.setattr(runtimes, "_on_this_computer", lambda name, version: None)
    monkeypatch.setenv("KEN_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def offline(name, pin):
        raise OSError("no internet")
    monkeypatch.setattr(runtimes, "_download", offline)
    await runtimes.update(["claude"])  # signed in: get it at start, quietly
    assert runtimes.status() == {"claude": {"error": "Couldn’t download Claude. Check the internet and try again."}}

    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\n")
    monkeypatch.setattr(runtimes, "_download", lambda name, pin: str(claude))
    await runtimes.ensure("claude")  # Try again
    assert runtimes.status() == {} and not runtimes.missing("claude")


def test_old_claude_code_on_the_mac_is_not_used(tmp_path, monkeypatch):
    # v0.1.37 compared Claude Code 2.0.28 with the SDK's 0.2.167, used it, and got a retired model (404).
    pins = tmp_path / "runtimes.json"
    pins.write_text(json.dumps({"claude": {"version": "0.2.167", "runs": "2.1.300", "url": "", "sha256": ""}}))
    monkeypatch.setattr(runtimes, "PINS", pins)
    monkeypatch.setattr(runtimes, "_pip_has", lambda name: False)
    monkeypatch.setattr(runtimes, "_chosen", {})
    monkeypatch.setattr(runtimes, "PLACES", [])
    monkeypatch.setenv("KEN_HOME", str(tmp_path))
    old = tmp_path / "bin" / "claude"
    old.parent.mkdir()
    old.write_text("#!/bin/sh\necho '2.0.28 (Claude Code)'\n")
    old.chmod(0o755)
    monkeypatch.setenv("PATH", f"{old.parent}:/usr/bin:/bin")
    (tmp_path / "runtimes").mkdir()
    (tmp_path / "runtimes" / "claude.path").write_text(f"{old}\n0.2.167")  # what v0.1.37 remembered
    runtimes.setup()
    assert runtimes.missing("claude")
    assert runtimes._on_this_computer("claude", runtimes._runs(runtimes._pins()["claude"])) is None
    old.write_text("#!/bin/sh\necho '2.1.301 (Claude Code)'\n")
    assert runtimes._on_this_computer("claude", "2.1.300") == str(old)
