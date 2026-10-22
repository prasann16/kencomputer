"""Cloudflare setup uses local evidence; an installed binary is never proof of a route."""
import asyncio
import json
import plistlib
import sys

import pytest

import cloudflare
from cloudflare import CloudflareSetup, scan


def test_detects_matching_routes_and_custom_service_config_without_exposing_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudflare, "executable", lambda _: None)
    configs, services = tmp_path / "configs", tmp_path / "services"
    configs.mkdir(); services.mkdir()
    (configs / "config.yml").write_text("""
tunnel: private-tunnel-id
token: private-tunnel-token
ingress:
  - hostname: ken.example.com
    service: http://localhost:7777
  - hostname: another.example.com
    service: http://127.0.0.1:9999
  - hostname: '*.example.com'
    service: http://localhost:7777
  - hostname: partial.example.com
    path: /api/identity
    service: http://localhost:7777
  - service: http_status:404
""")
    custom = tmp_path / "custom.yml"
    custom.write_text("ingress:\n - hostname: other-ken.example.com\n   service: http://[::1]:7777\n")
    (services / "com.cloudflare.cloudflared.plist").write_bytes(plistlib.dumps({"ProgramArguments": [
        "/custom/cloudflared", "tunnel", "--config", str(custom), "run", "--token", "never-show-this-token"]}))
    result = scan(7777, config_dirs=[configs], service_dirs=[services])
    assert result["routes"] == ["https://ken.example.com", "https://other-ken.example.com"]
    assert result["service_installed"] and result["config_found"]
    assert not result["config_unreadable"]
    assert "token" not in json.dumps(result)
    assert "private-tunnel-id" not in json.dumps(result)
    assert scan(8888, config_dirs=[configs], service_dirs=[services])["routes"] == []


def test_missing_broken_and_large_configs_are_distinguished(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudflare, "executable", lambda _: None)
    result = scan(7777, config_dirs=[tmp_path], service_dirs=[])
    assert not result["config_found"] and not result["config_unreadable"]
    (tmp_path / "config.yml").write_text("ingress: [broken")
    result = scan(7777, config_dirs=[tmp_path], service_dirs=[])
    assert result["config_found"] and result["config_unreadable"] and not result["routes"]
    (tmp_path / "config.yml").write_bytes(b"x" * (256 * 1024 + 1))
    assert scan(7777, config_dirs=[tmp_path], service_dirs=[])["config_unreadable"]


async def test_status_does_not_inspect_process_tokens_or_claim_connected(monkeypatch):
    monkeypatch.setattr(cloudflare, "scan", lambda port: {"binary": "/opt/homebrew/bin/cloudflared", "routes": []})
    calls = []
    async def run(*args, **kwargs):
        calls.append(args)
        return (0, "cloudflared version 2026.10.0") if args[-1] == "--version" else (0, "/opt/homebrew/bin/cloudflared\n/bin/zsh\n")
    monkeypatch.setattr(cloudflare, "command", run)
    status = await CloudflareSetup().status(7777)
    assert status["installed"] and status["running"] and status["version"] == "2026.10.0"
    assert "connected" not in status and status["routes"] == []
    assert ("/bin/ps", "-axo", "comm=") in calls


async def test_install_is_explicit_deduplicated_and_uses_only_fixed_argv(monkeypatch):
    installed = False
    monkeypatch.setattr(cloudflare.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cloudflare, "executable", lambda name: "/brew" if name == "brew" else "/cloudflared" if installed else None)
    proceed = asyncio.Event()
    calls = []
    async def run(*args, **kwargs):
        nonlocal installed
        calls.append((args, kwargs))
        await proceed.wait()
        installed = True
        return 0, ""
    monkeypatch.setattr(cloudflare, "command", run)
    setup = CloudflareSetup()
    assert setup.install_state == {"state": "idle"} and not calls
    assert setup.install() == {"state": "running"}
    first = setup.install_task
    assert setup.install() == {"state": "running"} and setup.install_task is first
    proceed.set()
    await first
    assert setup.install_state == {"state": "done"}
    assert calls[0][0] == ("/brew", "install", "cloudflared") and len(calls) == 1
    assert calls[0][1]["env"]["NONINTERACTIVE"] == "1"
    assert setup.install() == {"state": "done"} and len(calls) == 1


async def test_failed_install_and_shutdown_are_recoverable(monkeypatch):
    monkeypatch.setattr(cloudflare.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cloudflare, "executable", lambda name: "/brew" if name == "brew" else None)
    async def fail(*args, **kwargs):
        return 1, ""
    monkeypatch.setattr(cloudflare, "command", fail)
    setup = CloudflareSetup()
    setup.install()
    await setup.install_task
    assert setup.install_state["state"] == "error"
    started = asyncio.Event()
    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(cloudflare, "command", wait)
    setup.install()
    await started.wait()
    await setup.close()
    assert setup.install_task.done() and setup.install_state["state"] == "error"
    monkeypatch.setattr(cloudflare, "executable", lambda _: None)
    with pytest.raises(ValueError, match="Homebrew"):
        setup.install()


@pytest.mark.parametrize("without_killpg", [False, True])
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
async def test_command_stops_child(monkeypatch, without_killpg, cancel):
    if without_killpg:
        monkeypatch.delattr(cloudflare.os, "killpg", raising=False)
    create = asyncio.create_subprocess_exec
    started = asyncio.Event()
    process = None

    async def spawn(*args, **kwargs):
        nonlocal process
        process = await create(*args, **kwargs)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(cloudflare.command(
        sys.executable, "-c", "import time; time.sleep(60)", timeout=60 if cancel else 0.05))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
            await task
        assert process.returncode is not None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
