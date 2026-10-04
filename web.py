"""The web app's server: serves web/ and a small JSON + WebSocket API over the engine.

Local by default (127.0.0.1:7777). Access needs the token in ~/.ken/web-token —
`ken open` logs you in by visiting /login?token=… which sets a cookie. The UI is
plain files served from the repo, so a `git pull` + restart is a UI update; open
pages see the new revision on reconnect and reload themselves.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Callable

from aiohttp import WSMsgType, web

from engine import HOME, Engine

log = logging.getLogger("ken")

WEB_DIR = Path(__file__).parent / "web"
COOKIE = "ken_token"
MAX_UPLOAD = 200 * 1024 * 1024


def load_token(home: Path) -> str:
    f = home / "web-token"
    try:
        tok = f.read_text().strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(24)
    f.write_text(tok + "\n")
    f.chmod(0o600)
    return tok


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str))


class WebApp:
    def __init__(self, engine: Engine, home: Path, rev: str = "", transcribe: Callable[[str], str] | None = None, commands: dict[str, Callable] | None = None, setup=None) -> None:
        self.engine = engine
        self.token = load_token(home)
        self.rev = rev
        self.transcribe = transcribe
        self.commands = commands or {}
        self.setup = setup
        self.runner: web.AppRunner | None = None
        self.bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ auth

    def _authed(self, request: web.Request) -> bool:
        given = request.cookies.get(COOKIE, "")
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            given = auth[7:]
        return bool(given) and hmac.compare_digest(given, self.token)

    @web.middleware
    async def auth_mw(self, request: web.Request, handler):
        path = request.path
        if path in ("/login", "/manifest.webmanifest", "/icon.svg") or self._authed(request):
            return await handler(request)
        if path.startswith("/api/"):
            return _json({"error": "not signed in"}, 401)
        return web.Response(
            text=(
                "<!doctype html><meta name=viewport content='width=device-width'>"
                "<body style='font:16px system-ui;padding:40px;background:#F6F4EF;color:#1B1A17'>"
                "<h2>Ken is locked</h2><p>Open it with the link from <code>ken open</code> on the Ken computer.</p>"
            ),
            content_type="text/html",
            status=401,
        )

    async def login(self, request: web.Request) -> web.Response:
        given = request.query.get("token", "")
        if not given or not hmac.compare_digest(given, self.token):
            return web.Response(text="Wrong or expired link. Run `ken open` again.", status=403)
        resp = web.HTTPFound("/")
        resp.set_cookie(COOKIE, self.token, max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
        raise resp

    # ---------------------------------------------------------------- helpers

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.bg.add(t)
        t.add_done_callback(self.bg.discard)

    async def _body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _pid(self, request: web.Request) -> str:
        pid = request.match_info["pid"]
        if pid != HOME:
            try:
                self.engine.get_project(pid)
            except KeyError:
                raise web.HTTPNotFound(text="no such chat")
        return pid

    # ------------------------------------------------------------------ pages

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "index.html", headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; object-src 'none'; frame-src 'none'; base-uri 'none'",
        })

    async def static_file(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        path = (WEB_DIR / name).resolve()
        if WEB_DIR.resolve() not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    # -------------------------------------------------------------------- api

    async def meta(self, request):
        return _json({"rev": self.rev, "voice": self.transcribe is not None, "commands": sorted(self.commands),
                      # busy: don't restart the engine (e.g. to update) now
                      "busy": self.engine.busy() or bool(self.setup and self.setup.active)})

    async def setup_status(self, request):
        return _json(self.setup.status() if self.setup else {"claude": True, "voice": {"state": "ready"}})

    async def setup_action(self, request):
        """First-run setup: {"action": "start"} opens Claude sign-in, {"code": ...} finishes it."""
        b = await self._body(request)
        try:
            if b.get("action") == "start":
                return _json(await self.setup.start())
            if b.get("code"):
                return _json(await self.setup.submit(str(b["code"])))
        except RuntimeError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            log.exception("Connect Claude failed")
            return _json({"error": "Something went wrong connecting Claude. Click Connect Claude to try again."}, 500)
        return _json({"error": "Nothing to do."}, 400)

    async def command(self, request):
        """Run a slash command owned by the host (e.g. bot.py's /model, /coffee)."""
        b = await self._body(request)
        name, _, arg = str(b.get("text", "")).strip().lstrip("/").partition(" ")
        run = self.commands.get(name.lower())
        if run is None:
            return _json({"error": f"Unknown command /{name}"}, 404)
        result = run(arg)
        if asyncio.iscoroutine(result):
            result = await result
        return _json(result if isinstance(result, dict) else {"reply": str(result)})

    async def chats(self, request):
        return _json(self.engine.chats_list())

    async def library(self, request):
        items = []
        for pid, name in [(HOME, "Ken"), *[(p["id"], p["name"]) for p in self.engine.list_projects()]]:
            items.extend({**f, "project": pid, "project_name": name} for f in self.engine.list_files(pid))
        return _json(sorted(items, key=lambda f: -f["mtime"])[:100])

    async def preview(self, request):
        try:
            path = self.engine.file_path(self._pid(request), request.match_info["name"])
        except KeyError:
            raise web.HTTPNotFound()
        if path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".css", ".html"}:
            return _json({"error": "This file can be downloaded to open."}, 400)
        with path.open("r", errors="replace") as f:
            text = f.read(100001)
        return _json({"text": text[:100000], "truncated": len(text) > 100000})

    async def new_chat(self, request):
        b = await self._body(request)
        try:
            p = self.engine.create_project(
                str(b.get("name", "")), str(b.get("path", "")), str(b.get("context", "")), str(b.get("blurb", ""))
            )
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        return _json(p)

    async def chat(self, request):
        pid = self._pid(request)
        self.engine.welcome(pid)
        return _json(self.engine.chat_view(pid))

    async def open_chat(self, request):
        self._spawn(self.engine.on_open(self._pid(request)))
        return _json({"ok": True})

    async def warm_chat(self, request):
        # Start the existing CLI session without generating a greeting or a turn.
        self._spawn(self.engine.warm(self._pid(request)))
        return _json({"ok": True})

    async def post_message(self, request):
        pid = self._pid(request)
        b = await self._body(request)
        text = str(b.get("text", "")).strip()
        files = [str(f) for f in b.get("files", []) if isinstance(f, str)]
        if not text and not files:
            return _json({"error": "empty message"}, 400)
        client_id = b.get("client_id", "")
        if not isinstance(client_id, str) or len(client_id) > 100:
            return _json({"error": "invalid message identifier"}, 400)
        # A retry after a lost response must not start the same work twice.
        if client_id:
            row = self.engine.db.execute(
                "SELECT id, ts, data FROM events WHERE project=? AND type='message' AND json_extract(data, '$.client_id')=? LIMIT 1",
                (pid, client_id),
            ).fetchone()
            if row:
                return _json({"ok": True, "message": {"id": row["id"], "ts": row["ts"], "project": pid, **json.loads(row["data"])}})
        voice = b.get("voice")
        extra = {"client_id": client_id} if client_id else {}
        if voice is not None:
            if not text:
                return _json({"error": "No words were detected. Your recording has not been sent to Ken."}, 422)
            import math
            if not isinstance(voice, dict) or not isinstance(voice.get("peaks"), list) or len(voice["peaks"]) > 360:
                return _json({"error": "invalid voice note"}, 400)
            seconds = voice.get("seconds", 0)
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 86400:
                return _json({"error": "invalid voice duration"}, 400)
            if any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in voice["peaks"]):
                return _json({"error": "invalid voice waveform"}, 400)
            extra["voice"] = {"seconds": seconds, "peaks": voice["peaks"]}
        message = self.engine.log_message(pid, "you", text, files=files, **extra)
        self._spawn(self.engine.send_message(pid, text, [] if voice is not None else files, log_user=False))
        return _json({"ok": True, "message": message})

    async def stop(self, request):
        return _json({"stopped": await self.engine.stop_chat(self._pid(request))})

    async def upload(self, request):
        """Dropped files land in the chat's folder and Ken looks at them right away."""
        pid = self._pid(request)
        reader = await request.multipart()
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if not part.filename:
                continue
            dest = self.engine.new_file_path(pid, part.filename)
            size = 0
            try:
                with open(dest, "wb") as f:
                    while chunk := await part.read_chunk(1 << 16):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                        f.write(chunk)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            saved.append(dest.name)
        if saved and request.query.get("attach") != "1":
            self._spawn(self.engine.files_dropped(pid, saved))  # dropped: Ken looks right away
        return _json({"saved": saved})

    async def download(self, request):
        pid = self._pid(request)
        try:
            path = self.engine.file_path(pid, request.match_info["name"])
        except KeyError:
            raise web.HTTPNotFound()
        from urllib.parse import quote
        # Active documents must not execute under Ken's authenticated origin.
        inline = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".pdf"}
        return web.FileResponse(path, headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(path.name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        })

    async def reset(self, request):
        pid = self._pid(request)
        try:
            await self.engine.reset_chat(pid, clear=True)
        except ValueError as exc:
            return _json({"error": str(exc)}, 409)
        return _json(self.engine.chat_view(pid))

    def _project(self, request) -> str:
        pid = request.match_info["pid"]
        try:
            self.engine.get_project(pid)
        except KeyError:
            raise web.HTTPNotFound(text="no such project")
        return pid

    async def today(self, request):
        return _json(self.engine.today())

    async def project(self, request):
        return _json(self.engine.project_view(self._project(request)))

    async def update_project(self, request):
        b = await self._body(request)
        return _json(self.engine.update_project(self._project(request), **{k: b.get(k) for k in ("name", "blurb", "path")}))

    async def put_context(self, request):
        pid = self._project(request)
        self.engine.set_context(pid, str((await self._body(request)).get("text", "")))
        return _json({"ok": True})

    async def put_autonomy(self, request):
        pid = self._project(request)
        b = await self._body(request)
        self.engine.set_autonomy(pid, b.get("alone", []), b.get("ask", []))
        return _json(self.engine.get_autonomy(pid))

    async def start_run(self, request):
        b = await self._body(request)
        pid = self.engine.find_project(str(b.get("project", "")))
        prompt = str(b.get("prompt", "")).strip()
        if not pid or not prompt:
            return _json({"error": "need a project and what to do"}, 400)
        return _json(self.engine.start_run(pid, str(b.get("title") or prompt.splitlines()[0])[:80], prompt))

    async def get_run(self, request):
        try:
            return _json(self.engine.get_run(request.match_info["rid"]))
        except KeyError:
            raise web.HTTPNotFound()

    async def stop_run(self, request):
        try:
            await self.engine.stop_run(request.match_info["rid"])
        except KeyError:
            raise web.HTTPNotFound()
        return _json({"ok": True})

    async def resolve(self, request):
        b = await self._body(request)
        if b.get("decision") not in ("approve", "skip"):
            return _json({"error": "decision must be approve or skip"}, 400)
        body = b.get("body")
        try:
            a = await self.engine.resolve_approval(request.match_info["aid"], b["decision"], body if isinstance(body, str) else None)
        except KeyError:
            raise web.HTTPNotFound()
        return _json(a)

    async def voice(self, request):
        if self.transcribe is None:
            return _json({"error": "voice isn't available on this Ken"}, 400)
        data = await request.read()
        if not data:
            return _json({"error": "no audio"}, 400)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            text = await asyncio.to_thread(self.transcribe, path)
        except (TimeoutError, RuntimeError) as exc:
            log.warning("Voice failed: %s", exc)
            return _json({"error": str(exc)}, 503)
        except Exception:
            log.exception("Voice transcription failed")
            return _json({"error": "Could not transcribe this recording. Try again."}, 500)
        finally:
            os.unlink(path)
        if not text or not text.strip():
            return _json({"error": "Couldn’t make out the words. Your recording is saved; try again."}, 422)
        return _json({"text": text.strip()})

    async def events(self, request):
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        q = self.engine.subscribe()
        await ws.send_json({"type": "hello", "rev": self.rev})

        async def pump():
            while True:
                event = await q.get()
                await ws.send_str(json.dumps(event, default=str))

        pumping = asyncio.create_task(pump())
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            pumping.cancel()
            self.engine.unsubscribe(q)
        return ws

    # -------------------------------------------------------------- lifecycle

    def build(self) -> web.Application:
        app = web.Application(middlewares=[self.auth_mw], client_max_size=MAX_UPLOAD)
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/login", self.login)
        r.add_get("/api/meta", self.meta)
        r.add_get("/api/chats", self.chats)
        r.add_get("/api/library", self.library)
        r.add_post("/api/chats", self.new_chat)
        r.add_get("/api/chats/{pid}", self.chat)
        r.add_post("/api/chats/{pid}/open", self.open_chat)
        r.add_post("/api/chats/{pid}/warm", self.warm_chat)
        r.add_post("/api/chats/{pid}/messages", self.post_message)
        r.add_post("/api/chats/{pid}/stop", self.stop)
        r.add_post("/api/chats/{pid}/files", self.upload)
        r.add_get("/api/chats/{pid}/files/{name}", self.download)
        r.add_get("/api/chats/{pid}/preview/{name}", self.preview)
        r.add_post("/api/chats/{pid}/reset", self.reset)
        r.add_get("/api/today", self.today)
        r.add_get("/api/projects/{pid}", self.project)
        r.add_patch("/api/projects/{pid}", self.update_project)
        r.add_put("/api/projects/{pid}/context", self.put_context)
        r.add_put("/api/projects/{pid}/autonomy", self.put_autonomy)
        r.add_post("/api/runs", self.start_run)
        r.add_get("/api/runs/{rid}", self.get_run)
        r.add_post("/api/runs/{rid}/stop", self.stop_run)
        r.add_post("/api/approvals/{aid}", self.resolve)
        r.add_post("/api/voice", self.voice)
        r.add_post("/api/command", self.command)
        r.add_get("/api/setup", self.setup_status)
        r.add_post("/api/setup", self.setup_action)
        r.add_get("/api/events", self.events)
        r.add_get("/{name:.+}", self.static_file)
        return app

    async def start(self, host: str, port: int) -> None:
        self.runner = web.AppRunner(self.build(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()
        log.info("app on http://%s:%d (run `ken open` to sign in)", host, port)
