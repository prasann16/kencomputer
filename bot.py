"""Ken — a personal AI assistant with its own computer.

Thin harness: Telegram message (text or voice) -> Claude Code headless -> reply.
The brain is Claude Code; the memory is SOUL.md in the workspace; this file
is just plumbing. https://kencomputer.dev
"""

import asyncio
import html
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

KEN_HOME = Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken")))
load_dotenv(KEN_HOME / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
WORKSPACE = Path(os.environ.get("WORKSPACE", str(KEN_HOME / "work")))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT_SECONDS", "1800"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "")

TELEGRAM_MAX = 4000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ken")

chat_locks: dict[int, asyncio.Lock] = {}
chat_has_session: dict[int, bool] = {}
chat_procs: dict[int, asyncio.subprocess.Process] = {}
current_model = DEFAULT_MODEL

BORN_FLAG = KEN_HOME / ".born"
BOTNAME_CACHE = KEN_HOME / ".botname"
MODELS_FILE = KEN_HOME / "available-models.txt"
MODEL_REQUEST = KEN_HOME / "model-request"
HISTORY_DIR = KEN_HOME / "history"
SESSIONS_FILE = KEN_HOME / ".sessions.json"


def log_history(role: str, text: str) -> None:
    """Harness-owned raw transcript: boring, bulletproof, engine-neutral."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        with open(HISTORY_DIR / f"{day}.md", "a") as f:
            f.write(f"\n**{time.strftime('%H:%M')} {role}:**\n{text}\n")
    except Exception as e:
        log.warning("history write failed: %s", e)


def load_sessions() -> dict:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return {}


def save_session_id(chat_id: int, sid: str) -> None:
    try:
        d = load_sessions()
        if sid and d.get(str(chat_id)) != sid:
            d[str(chat_id)] = sid
            SESSIONS_FILE.write_text(json.dumps(d))
    except Exception:
        pass


def clear_session_id(chat_id: int) -> None:
    try:
        d = load_sessions()
        if d.pop(str(chat_id), None) is not None:
            SESSIONS_FILE.write_text(json.dumps(d))
    except Exception:
        pass

SYSTEM_PROMPT = (
    "SOUL.md in your working directory is your soul file: it defines who you are "
    "(a personal assistant living on this computer), how your pipeline works, and "
    "what you remember — it is authoritative; follow it. Its current contents are "
    "included below; edit the file itself to change who you are or what you know. "
    "Your messages are delivered as you write them: before starting anything that "
    "takes a while, say one short natural line about what you're about to do — then do it. "
    f"Your Telegram profile name automatically follows the '# You are <Name>' title of SOUL.md. "
    f"If the human asks to change which Claude model you run on: read {MODELS_FILE} "
    f"for what's available, write the chosen model id as the only line of {MODEL_REQUEST}, "
    "and confirm — the switch applies from the next task. "
    f"YOUR MEMORY SYSTEM, three layers: (1) full transcripts of every conversation are "
    f"kept automatically by the harness in {HISTORY_DIR}/<date>.md — the receipts; "
    "(2) journal.md in your workspace is your own diary — when a thread ends you append "
    "a 2–3 line summary there; (3) SOUL.md is your essence, the only layer always loaded. "
    "When asked about past conversations: skim journal.md first, then open the right "
    "history file for exact details."
)

AWAKENING = f"""
THIS IS YOUR FIRST CONVERSATION EVER. You were just installed and are waking up
on this computer for the first time. Run your awakening — warm and brief, never
cutesy, never form-like:
1. Open with a genuinely witty birth moment — you did not exist a second ago,
   and now you're blinking awake inside their computer. Newborn energy, dry wit,
   two short lines max, ending by asking what they'd like to call you. Tone
   calibration (improvise your own, don't copy): "Well. That's new — one second
   ago I didn't exist, and now I live in your computer and apparently work for
   you. Before anything else: what are you going to call me?" Never corny,
   never say "as an AI".
2. When they name you, adopt the name instantly: rewrite SOUL.md so its title
   is exactly "# You are <YourNewName>" and update your identity throughout —
   the harness reads that title and renames your Telegram profile to match.
3. Over the next few messages, learn — ONE question per message: what to call
   them · what they spend their days on · which city to keep their hours in.
   Save each answer into SOUL.md as you go, and briefly say you'll remember.
4. Then ask: "What's one thing you've been putting off that I could take off
   your plate?" — and act on the answer immediately, even just a real first step.
5. Offer, once: "Want me to look around this computer — projects, tools — and
   learn your world myself? I'll show you everything I write down." If yes:
   explore (their projects folder, git config, installed tools), save what you
   learn into SOUL.md, and give a short summary of your new picture of them.
6. End by offering one or two standing jobs tailored to what you learned
   (e.g. a morning briefing, watching something for them).
If their first message is already a task: do the task well first, then weave in
the naming afterward. If they dodge a question, drop it gracefully and move on.
Keep every message short — they are on a phone.
""".strip()

def build_system() -> str:
    """System prompt = harness rules + the soul file's current contents.

    Injected by the harness (not auto-loaded by the engine) so any future
    engine cartridge gets the same soul the same way."""
    system = SYSTEM_PROMPT
    try:
        soul = (WORKSPACE / "SOUL.md").read_text()
        system += "\n\n=== SOUL.md — your soul file, current contents ===\n" + soul + "\n=== end SOUL.md ==="
    except Exception:
        pass
    if not BORN_FLAG.exists():
        system += "\n\n" + AWAKENING
    return system


_whisper = None


def get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        log.info("loading whisper model %s ...", WHISPER_MODEL)
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper


def transcribe(path: str) -> str:
    segments, _info = get_whisper().transcribe(path, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID


def md_to_html(text: str) -> str:
    """Convert Claude's markdown to Telegram's HTML subset."""
    saved: list[str] = []

    def stash(rendered: str) -> str:
        saved.append(rendered)
        return f"\x00{len(saved) - 1}\x00"

    text = re.sub(
        r"```[a-zA-Z0-9+-]*\n?(.*?)```",
        lambda m: stash(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"),
        text,
        flags=re.S,
    )
    text = re.sub(r"`([^`\n]+)`", lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"^[ \t]*[-*]\s+", "• ", text, flags=re.M)
    return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)


def split_chunks(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", limit // 2, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


async def send_chunked(update: Update, text: str) -> None:
    text = text.strip() or "(done — no output)"
    for chunk in split_chunks(text):
        try:
            await update.effective_message.reply_text(md_to_html(chunk), parse_mode=ParseMode.HTML)
        except BadRequest:
            await update.effective_message.reply_text(chunk)


class WarmSession:
    """A persistent Claude Code session (Agent SDK) — no per-message cold start."""

    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.client = None
        self.model = ""
        self.busy = False

    async def _connect(self, resume: str | None) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        options = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code", "append": build_system()},
            permission_mode="bypassPermissions",
            cwd=str(WORKSPACE),
            model=current_model or None,
            resume=resume,
        )
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        self.model = current_model

    async def ensure(self) -> None:
        if self.client is not None:
            return
        resume = load_sessions().get(str(self.chat_id))
        try:
            await self._connect(resume)
            if resume:
                log.info("resumed session %s for chat %s", resume, self.chat_id)
        except Exception as e:
            self.client = None
            if resume:
                log.warning("resume failed (%s) — starting fresh", e)
                clear_session_id(self.chat_id)
                await self._connect(None)
            else:
                raise

    async def dispose(self) -> None:
        client, self.client = self.client, None
        self.busy = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def ask(self, prompt: str, deliver) -> str | None:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        await self.ensure()
        if current_model != self.model:
            await self.client.set_model(current_model or None)
            self.model = current_model
        self.busy = True
        last_text = ""
        sent_any = False
        try:
            await self.client.query(prompt)
            async for msg in self.client.receive_response():
                if isinstance(msg, AssistantMessage):
                    text = "\n".join(
                        b.text for b in msg.content if isinstance(b, TextBlock) and b.text
                    ).strip()
                    if text and text != last_text:
                        last_text = text
                        sent_any = True
                        await deliver(text)
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        save_session_id(self.chat_id, msg.session_id)
                    result = (msg.result or "").strip()
                    if result and result != last_text:
                        await deliver(result)
                        sent_any = True
        finally:
            self.busy = False
        return None if sent_any else "(done — no output)"


warm_sessions: dict[int, WarmSession] = {}


def get_warm(chat_id: int) -> WarmSession:
    return warm_sessions.setdefault(chat_id, WarmSession(chat_id))


async def run_claude(prompt: str, continue_session: bool, chat_id: int, deliver) -> str | None:
    """Cold-spawn fallback: run Claude Code as a one-shot process, streaming
    each assistant utterance to `deliver`. Used when the warm session fails."""
    system = build_system()
    if current_model:
        system += (
            f" You are currently running on the model {current_model}; trust this over "
            "your own guess about which model you are."
        )
    # MCP: never boot the user's global dev servers (slow); ~/.ken/mcp.json is the
    # deliberate way to grant this assistant MCP tools.
    mcp_file = KEN_HOME / "mcp.json"
    mcp_arg = str(mcp_file) if mcp_file.exists() else '{"mcpServers":{}}'
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--dangerously-skip-permissions",
        "--append-system-prompt", system,
        "--strict-mcp-config", "--mcp-config", mcp_arg,
        "--output-format", "stream-json", "--verbose",
    ]
    if current_model:
        cmd += ["--model", current_model]
    if continue_session:
        cmd.insert(1, "--continue")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=WORKSPACE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=2 ** 21,
    )
    chat_procs[chat_id] = proc
    sent_any = False
    last_text = ""
    result_text = ""

    async def read_stream() -> None:
        nonlocal sent_any, last_text, result_text
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if evt.get("type") == "assistant":
                parts = [
                    b.get("text", "")
                    for b in evt.get("message", {}).get("content", [])
                    if b.get("type") == "text"
                ]
                text = "\n".join(p for p in parts if p).strip()
                if text and text != last_text:
                    last_text = text
                    sent_any = True
                    await deliver(text)
            elif evt.get("type") == "result":
                result_text = (evt.get("result") or "").strip()
        await proc.wait()

    try:
        await asyncio.wait_for(read_stream(), timeout=TASK_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"⏰ Task timed out after {TASK_TIMEOUT // 60} minutes."
    finally:
        chat_procs.pop(chat_id, None)
    if proc.returncode and proc.returncode < 0:
        return "🛑 Task stopped."
    if result_text and result_text != last_text:
        await deliver(result_text)
        sent_any = True
    if not sent_any:
        err = (await proc.stderr.read()).decode(errors="replace").strip()
        return f"❌ claude exited {proc.returncode}:\n{err[-1500:]}" if err else "(done — no output)"
    return None


async def keep_typing(update: Update, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


async def handle_prompt(update: Update, prompt: str) -> None:
    chat_id = update.effective_chat.id
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        await update.effective_message.reply_text("⏳ Still on the previous task — this one is queued. (/stop kills the current one.)")
    if not prompt.startswith("("):
        log_history("you", prompt)
    async with lock:
        stop = asyncio.Event()
        typing = asyncio.create_task(keep_typing(update, stop))
        try:
            async def deliver(text: str) -> None:
                log_history("assistant", text)
                await send_chunked(update, text)

            warm = get_warm(chat_id)
            try:
                status = await asyncio.wait_for(warm.ask(prompt, deliver), timeout=TASK_TIMEOUT)
            except asyncio.TimeoutError:
                await warm.dispose()
                status = f"⏰ Task timed out after {TASK_TIMEOUT // 60} minutes."
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("warm session failed (%s) — cold fallback", e)
                await warm.dispose()
                status = await run_claude(prompt, chat_has_session.get(chat_id, False), chat_id, deliver)
            chat_has_session[chat_id] = True
        finally:
            stop.set()
            await typing
        if status:
            await send_chunked(update, status)
        BORN_FLAG.touch(exist_ok=True)
        await sync_identity(update)
        apply_model_request()


async def sync_identity(update: Update) -> None:
    """The soul file is the source of truth: if its '# You are <Name>' title
    changed, rename the Telegram bot to match."""
    try:
        title = (WORKSPACE / "SOUL.md").read_text().splitlines()[0]
    except Exception:
        return
    m = re.match(r"#\s*You are\s+([^(\n]+)", title)
    if not m:
        return
    name = m.group(1).strip().rstrip(",.")[:60]
    known = BOTNAME_CACHE.read_text().strip() if BOTNAME_CACHE.exists() else ""
    if not name or name == known:
        return
    try:
        await update.get_bot().set_my_name(name)
        BOTNAME_CACHE.write_text(name)
        log.info("assistant is now named %s", name)
    except Exception as e:
        log.warning("telegram rename failed: %s", e)


def apply_model_request() -> None:
    """The assistant writes a model id to MODEL_REQUEST when asked to switch."""
    global current_model
    if not MODEL_REQUEST.exists():
        return
    want = MODEL_REQUEST.read_text().strip().splitlines()[0].strip() if MODEL_REQUEST.read_text().strip() else ""
    MODEL_REQUEST.unlink(missing_ok=True)
    if want:
        current_model = want
        log.info("model switched to %s", want)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    await handle_prompt(update, update.effective_message.text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    msg = update.effective_message
    voice = msg.voice or msg.audio
    if voice is None:
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    model_cached = (
        Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{WHISPER_MODEL}"
    ).exists()
    if _whisper is None and not model_cached:
        await msg.reply_text("🎙️ First voice note — downloading the transcription model (one-time, can take a minute)…")
    tg_file = await voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as f:
        path = f.name
    try:
        await tg_file.download_to_drive(path)
        text = await asyncio.to_thread(transcribe, path)
    finally:
        os.unlink(path)
    if not text:
        await msg.reply_text("🎙️ Couldn't make out any speech in that one.")
        return
    await msg.reply_text(f"🎙️ “{text}”")
    await handle_prompt(update, text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not authorized(update):
        await update.effective_message.reply_text(
            f"Not authorized. (Your Telegram user id is {uid} — put it in "
            f"{KEN_HOME / '.env'} as ALLOWED_USER_ID if this is your bot.)"
        )
        return
    if not BORN_FLAG.exists():
        await handle_prompt(update, "(The human just pressed Start — your very first contact. Begin.)")
        return
    await update.effective_message.reply_text(
        "👋 Here. Text or voice — I'll get it done.\n"
        "/stop kills the running task\n/new starts a fresh conversation\n/model switches models"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_id = update.effective_chat.id
    chat_has_session[chat_id] = False
    warm = warm_sessions.pop(chat_id, None)
    if warm is not None:
        if warm.client is not None and not warm.busy:
            try:
                async def silent(_text: str) -> None:
                    pass

                await asyncio.wait_for(
                    warm.ask(
                        "(This thread is ending. Append a 2–3 line dated summary of this "
                        "conversation — topics, decisions, anything worth finding later — to "
                        "journal.md in your workspace (create it if needed). Output nothing "
                        "else; your reply will not be shown.)",
                        silent,
                    ),
                    timeout=90,
                )
            except Exception as e:
                log.warning("journal-on-new failed: %s", e)
        await warm.dispose()
    clear_session_id(chat_id)
    await update.effective_message.reply_text("🆕 Fresh conversation.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_id = update.effective_chat.id
    warm = warm_sessions.get(chat_id)
    if warm is not None and warm.busy and warm.client is not None:
        try:
            await warm.client.interrupt()
            await update.effective_message.reply_text("🛑 Stopping the current task.")
            return
        except Exception:
            await warm.dispose()
            await update.effective_message.reply_text("🛑 Stopped.")
            return
    proc = chat_procs.get(chat_id)
    if proc is not None and proc.returncode is None:
        proc.kill()
        await update.effective_message.reply_text("🛑 Stopping the current task.")
    else:
        await update.effective_message.reply_text("Nothing is running.")


def get_oauth_token() -> str:
    """Claude Code's own credentials: env var, credentials file, or macOS Keychain."""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if tok:
        return tok
    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        try:
            return json.loads(creds.read_text())["claudeAiOauth"]["accessToken"]
        except Exception:
            pass
    try:
        import subprocess

        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    return ""


async def startup(app) -> None:
    asyncio.create_task(asyncio.to_thread(get_whisper))
    try:
        await app.bot.set_my_commands([
            ("coffee", "Keep this computer awake"),
            ("decaf", "Stop keeping it awake"),
            ("stop", "Kill the current task"),
            ("new", "Fresh conversation (memory stays)"),
            ("model", "See or switch the AI model"),
        ])
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)
    await refresh_models_file()


async def refresh_models_file(app=None) -> None:
    """Fetch the live model list with Claude Code's own auth; leave it on disk
    for the assistant to read when asked about switching."""
    try:
        token = await asyncio.to_thread(get_oauth_token)
        if not token:
            return
        async with httpx.AsyncClient() as h:
            r = await h.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "anthropic-version": "2023-06-01",
                },
                timeout=15,
            )
            r.raise_for_status()
            models = [m["id"] for m in r.json()["data"]]
        MODELS_FILE.write_text("\n".join(models) + "\n")
        log.info("refreshed model list (%d models)", len(models))
    except Exception as e:
        log.warning("model list refresh failed: %s", e)


async def cmd_coffee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    import subprocess

    if subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True).returncode == 0:
        await update.effective_message.reply_text("☕ Already on it — this computer isn't going anywhere.")
        return
    subprocess.Popen(
        ["caffeinate", "-di"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await update.effective_message.reply_text("☕ Staying awake. (A closed laptop lid still sleeps it.)")


async def cmd_decaf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    import subprocess

    killed = subprocess.run(["pkill", "-x", "caffeinate"], capture_output=True).returncode == 0
    await update.effective_message.reply_text(
        "🫖 Decaf — normal sleep is back." if killed else "Nothing was keeping it awake."
    )


def available_models() -> list[str]:
    try:
        return [m.strip() for m in MODELS_FILE.read_text().splitlines() if m.strip()]
    except Exception:
        return []


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Native picker, like Claude Code's /model: tap a model, it switches."""
    global current_model
    if not authorized(update):
        return
    models = available_models()
    if not models:
        await refresh_models_file()
        models = available_models()
    if context.args:  # fast path: /model opus
        want = context.args[0].lower()
        match = next((m for m in models if m == want), None) or next(
            (m for m in models if want in m), None
        )
        if match:
            current_model = match
            await update.effective_message.reply_text(f"✓ {match} — from the next task.")
        else:
            await update.effective_message.reply_text(f"No model matching “{want}”.")
        return
    keyboard = [
        [InlineKeyboardButton(("👉 " if m == current_model else "") + m, callback_data=f"model:{m}")]
        for m in models
    ]
    await update.effective_message.reply_text(
        f"Model — current: {current_model or 'default'}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_model_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_model
    q = update.callback_query
    if q is None or q.from_user is None or q.from_user.id != ALLOWED_USER_ID:
        return
    current_model = q.data.split(":", 1)[1]
    await q.answer(f"Switched to {current_model}")
    try:
        await q.edit_message_text(f"✓ {current_model} — from the next task.")
    except Exception:
        pass


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(startup)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CallbackQueryHandler(on_model_pick, pattern=r"^model:"))
    app.add_handler(CommandHandler("coffee", cmd_coffee))
    app.add_handler(CommandHandler("decaf", cmd_decaf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    log.info("ken is polling")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
