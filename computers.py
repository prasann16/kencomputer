"""Personal computers: local connection management and an authenticated API gateway.

The page and native bridge always come from the local engine. Remote engines only
supply API data. Each request names its computer, so switching views cannot move
an in-flight action to another machine. No remote request can traverse the gateway.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import secrets
import socket
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web, WSMsgType
from yarl import URL

from cloudflare import CloudflareSetup

PROTOCOL = 1
SELECTION_COOKIE = "ken_computer"


def response(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


def local_request(request):
    """A tunnel terminates on loopback too; verify the host and forwarding headers."""
    try:
        host = URL("http://" + request.host).host
        return (request.remote in {"127.0.0.1", "::1"}
                and host in {"127.0.0.1", "localhost", "::1"}
                and not any(h in request.headers for h in
                            ("Forwarded", "X-Forwarded-For", "CF-Connecting-IP", "X-Ken-Remote")))
    except ValueError:
        return False


def loopback_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def server_url(value):
    try:
        url = URL(str(value).strip())
        if (not url.host or url.user is not None or url.password is not None
                or url.query_string or url.fragment or url.path not in {"", "/"}
                or (url.scheme != "https" and not (url.scheme == "http" and loopback_host(url.host)))):
            raise ValueError
        _ = url.port
        return str(url.origin())
    except (ValueError, TypeError):
        raise ValueError("Enter an HTTPS hostname, such as https://ken.example.com, without a path.") from None


class Computers:
    def __init__(self, home: Path, rev=""):
        self.path = Path(home) / "computers.json"
        self.rev = rev
        self.data = {"server": {"enabled": False, "name": socket.gethostname().removesuffix(".local")}, "computers": []}
        if self.path.exists():
            # Fail closed on a damaged file; never silently overwrite saved connections.
            self.data = json.loads(self.path.read_text())
        if "id" not in self.data["server"]:
            self.data["server"]["id"] = secrets.token_hex(16)
            if self.path.exists():
                self.save()
        self.http = None
        self.sockets = {}
        self.remote_sockets = set()
        self.cloudflare = CloudflareSetup()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".computers-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)  # mkstemp preserves mode 0600 on POSIX, including on replacement
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def authenticates(self, request):
        key = self.data["server"].get("key", "")
        given = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return bool(self.data["server"]["enabled"] and key and hmac.compare_digest(key.encode(), given.encode()))

    def get(self, cid):
        return next((c for c in self.data["computers"] if c["id"] == cid), None)

    @staticmethod
    def public(c):
        return {k: c[k] for k in ("id", "name", "url")}

    @staticmethod
    def headers(c):
        headers = {"Authorization": "Bearer " + c["key"], "X-Ken-Remote": "1"}
        if c.get("access_id"):
            headers.update({"CF-Access-Client-Id": c["access_id"], "CF-Access-Client-Secret": c["access_secret"]})
        return headers

    async def lifecycle(self, app):
        trace = aiohttp.TraceConfig()

        async def no_redirect(*args):
            # ws_connect follows redirects internally. Never send credentials to a redirect target.
            raise aiohttp.ClientError("Redirects are not allowed")

        trace.on_request_redirect.append(no_redirect)
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
                                         timeout=aiohttp.ClientTimeout(total=700, sock_connect=10),
                                         trace_configs=[trace]) as self.http:
            yield

    async def shutdown(self, app):
        await self.cloudflare.close()
        for ws in list(set(self.sockets) | self.remote_sockets):
            await ws.close(code=1001, message=b"Ken connection closed")

    def install(self, app):
        app.cleanup_ctx.append(self.lifecycle)
        app.on_shutdown.append(self.shutdown)
        app.router.add_get("/api/computers", self.list)
        app.router.add_post("/api/computers", self.add)
        app.router.add_post("/api/computers/select", self.select)
        app.router.add_post("/api/computers/{cid}/check", self.check)
        app.router.add_delete("/api/computers/{cid}", self.remove)
        app.router.add_get("/api/computers/server", self.server)
        app.router.add_put("/api/computers/server", self.server)
        app.router.add_get("/api/computers/cloudflare", self.cloudflare_status)
        app.router.add_post("/api/computers/cloudflare/install", self.cloudflare_install)
        app.router.add_post("/api/computers/cloudflare/verify", self.cloudflare_verify)
        app.router.add_get("/api/identity", self.identity)
        app.router.add_route("*", "/remote/{cid}/api/{tail:.*}", self.proxy)

    async def list(self, request):
        return response({"local_name": self.data["server"]["name"],
                         "computers": [self.public(c) for c in self.data["computers"]]})

    async def identity(self, request):
        return response({"product": "ken", "protocol": PROTOCOL, "name": self.data["server"]["name"],
                         "server_id": self.data["server"]["id"], "rev": self.rev})

    async def body(self, request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Expected a JSON object.") from None

    @staticmethod
    def connection_error(status):
        if status in {401, 403}:
            return "Connection refused. Check the server key and any Cloudflare Access service token."
        if 300 <= status < 400:
            return "Cloudflare redirected the connection. Use a Service Auth policy and enter its service token."
        return "Couldn’t reach Ken. Check that the server and your Cloudflare Tunnel are running."

    async def probe(self, c):
        try:
            async with self.http.get(c["url"] + "/api/identity", headers=self.headers(c),
                                     allow_redirects=False, timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status != 200:
                    raise ValueError(self.connection_error(r.status))
                data = await r.json()
                if data.get("product") != "ken" or data.get("protocol") != PROTOCOL:
                    raise ValueError("This server needs a compatible Ken update before it can connect.")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ValueError("Couldn’t reach Ken. Check the address, server key, and Cloudflare Tunnel.") from None
        except (TypeError, AttributeError, json.JSONDecodeError):
            raise ValueError("This address didn’t respond as a Ken server. Check your tunnel’s service address.") from None

    async def add(self, request):
        b = await self.body(request)
        try:
            c = {"url": server_url(b.get("url", ""))}
            for key in ("key", "access_id", "access_secret"):
                value = b.get(key, "")
                if not isinstance(value, str) or len(value) > 4096 or any(ord(ch) < 32 for ch in value):
                    raise ValueError("Enter valid connection credentials.")
                c[key] = value.strip()
            if not c["key"]:
                raise ValueError("Enter the server key from the other Mac’s Ken settings.")
            if bool(c["access_id"]) != bool(c["access_secret"]):
                raise ValueError("Cloudflare Access needs both a client ID and a client secret.")
            identity = await self.probe(c)
            old = next((x for x in self.data["computers"] if x["url"] == c["url"]), None)
            c["id"] = old["id"] if old else secrets.token_hex(8)
            c["name"] = str(b.get("name") or identity["name"])[:80]
            if old:
                self.data["computers"].remove(old)
            self.data["computers"].append(c)
            self.save()
            return response(self.public(c))
        except ValueError as e:
            return response({"error": str(e)}, 400)

    async def check(self, request):
        c = self.get(request.match_info["cid"])
        if not c:
            return response({"error": "This computer was removed."}, 404)
        try:
            return response({"online": True, **await self.probe(c)})
        except ValueError as e:
            return response({"online": False, "error": str(e)})

    async def select(self, request):
        cid = (await self.body(request)).get("id", "local")
        if cid != "local" and not self.get(cid):
            return response({"error": "This computer was removed."}, 404)
        r = response({"path": "/" if cid == "local" else "/computer/" + cid})
        r.set_cookie(SELECTION_COOKIE, cid, httponly=True, samesite="Strict", max_age=365 * 86400)
        return r

    async def remove(self, request):
        cid = request.match_info["cid"]
        self.data["computers"] = [c for c in self.data["computers"] if c["id"] != cid]
        self.save()
        for ws, computer in list(self.sockets.items()):
            if computer == cid:
                await ws.close(code=1008, message=b"Computer removed")
        return response({"ok": True})

    async def server(self, request):
        server = self.data["server"]
        if request.method == "PUT":
            b = await self.body(request)
            if "enabled" in b:
                if not isinstance(b["enabled"], bool):
                    return response({"error": "Choose whether to enable server access."}, 400)
                server["enabled"] = b["enabled"]
            if "name" in b:
                server["name"] = str(b["name"]).strip()[:80] or socket.gethostname()
            if server["enabled"] and (not server.get("key") or b.get("rotate")):
                server["key"] = secrets.token_urlsafe(32)
            if not server["enabled"]:
                server.pop("key", None)
            self.save()
            if not server["enabled"] or b.get("rotate"):
                for ws in list(self.remote_sockets):
                    await ws.close(code=1008, message=b"Server access revoked")
        return response({**server, "service": "http://127.0.0.1:" + str(request.url.port or 7777)})

    async def cloudflare_status(self, request):
        return response(await self.cloudflare.status(request.url.port or 7777))

    async def cloudflare_install(self, request):
        if not self.data["server"]["enabled"]:
            return response({"error": "Enable server access on this Mac first."}, 400)
        try:
            return response(self.cloudflare.install(), 202)
        except ValueError as e:
            return response({"error": str(e)}, 400)

    async def cloudflare_verify(self, request):
        server = self.data["server"]
        if not server["enabled"]:
            return response({"error": "Enable server access on this Mac first."}, 400)
        b = await self.body(request)
        try:
            c = {"url": server_url(b.get("url", "")), "key": server["key"]}
            for key in ("access_id", "access_secret"):
                value = b.get(key, "")
                if not isinstance(value, str) or len(value) > 4096 or any(ord(ch) < 32 for ch in value):
                    raise ValueError("Enter valid Cloudflare Access credentials.")
                c[key] = value.strip()
            if bool(c["access_id"]) != bool(c["access_secret"]):
                raise ValueError("Cloudflare Access needs both a client ID and a client secret.")

            async def verify():
                identity = await self.probe(c)
                if identity.get("server_id") != server["id"]:
                    raise ValueError("That address reaches a different Ken. Point the tunnel at the service address shown here.")
                # A working HTTP route alone isn't enough for the live client.
                async with self.http.ws_connect(c["url"] + "/api/events", headers=self.headers(c),
                                                max_msg_size=65536) as ws:
                    hello = await ws.receive_json(timeout=4)
                    if not isinstance(hello, dict) or hello.get("type") != "hello":
                        raise ValueError("The tunnel didn’t open Ken’s live connection. Check that WebSockets are allowed.")

            await asyncio.wait_for(verify(), 12)
            if not server["enabled"] or server.get("key") != c["key"]:
                raise ValueError("Server access changed during the check. Try again.")
            local_only = loopback_host(URL(c["url"]).host)
            if not local_only:
                server["url"] = c["url"]
                self.save()  # Access credentials are used once, never saved by this check.
            return response({"ok": True, "url": c["url"], "local_only": local_only})
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError):
            return response({"error": "Ken couldn’t open a live connection through that address. Check your tunnel, WebSockets, and any Cloudflare Access policy."}, 400)
        except ValueError as e:
            return response({"error": str(e)}, 400)

    async def proxy(self, request):
        c = self.get(request.match_info["cid"])
        if not c:
            return response({"error": "This computer was removed. Choose another computer."}, 404)
        # Use the encoded path (including encoded file names) and never forward cookies.
        prefix = "/remote/" + c["id"]
        if not request.raw_path.startswith(prefix + "/api/"):
            return response({"error": "Invalid computer path."}, 400)
        url = URL(c["url"] + request.raw_path[len(prefix):], encoded=True)
        headers = self.headers(c)
        out = None
        for name in ("Content-Type", "Range", "If-Range"):
            if name in request.headers:
                headers[name] = request.headers[name]
        try:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return await self.websocket(request, url, headers)
            body = await request.read() if request.can_read_body else None
            async with self.http.request(request.method, url, headers=headers, data=body,
                                         allow_redirects=False) as r:
                if r.status in {401, 403} or 300 <= r.status < 400:
                    return response({"error": self.connection_error(r.status)}, 502)
                out = web.StreamResponse(status=r.status, headers={
                    k: v for k, v in r.headers.items() if k.lower() in
                    {"content-type", "content-disposition", "content-range", "accept-ranges"}})
                out.headers["Cache-Control"] = "no-store"
                out.headers["X-Content-Type-Options"] = "nosniff"
                out.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
                await out.prepare(request)
                async for chunk in r.content.iter_chunked(65536):
                    await out.write(chunk)
                await out.write_eof()
                return out
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionResetError):
            if out is not None and out.prepared:
                # Never turn a truncated download into a successful partial file or
                # append a JSON error to a response whose headers are already sent.
                if request.transport is not None:
                    request.transport.close()
                return out
            return response({"error": "This computer is offline. Ken will reconnect when it’s available. Work already accepted stays on that computer."}, 502)

    async def websocket(self, request, url, headers):
        async with self.http.ws_connect(url, headers=headers, heartbeat=25, max_msg_size=8 * 1024 * 1024) as remote:
            local = web.WebSocketResponse(heartbeat=25, max_msg_size=8 * 1024 * 1024)
            await local.prepare(request)
            self.sockets[local] = request.match_info["cid"]

            async def relay(source, target):
                async for msg in source:
                    if msg.type == WSMsgType.TEXT:
                        await target.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await target.send_bytes(msg.data)
                    elif msg.type == WSMsgType.ERROR:
                        break

            tasks = [asyncio.create_task(relay(local, remote)), asyncio.create_task(relay(remote, local))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.sockets.pop(local, None)
                await local.close()
            return local
