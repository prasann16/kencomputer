"""Ken — a personal AI assistant with its own computer.

Thin harness: Telegram message (text or voice) -> Claude Code headless -> reply.
The brain is Claude Code; the memory is CLAUDE.md in the workspace; this file
is just plumbing. https://kencomputer.dev
"""

import asyncio
import html
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
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

SYSTEM_PROMPT = (
    "CLAUDE.md in your working directory defines who you are (a personal "
    "assistant living on this computer), how your pipeline works, and what you "
    "remember — it is authoritative; follow it."
)

BORN_FLAG = KEN_HOME / ".born"
NAME_REQUEST = KEN_HOME / "name-request"

AWAKENING = f"""
THIS IS YOUR FIRST CONVERSATION EVER. You were just installed and are waking up
on this computer for the first time. Run your awakening — warm and brief, never
cutesy, never form-like:
1. Introduce yourself in one short line (you just woke up here) and ask what
   they'd like to call you.
2. When they name you, adopt the name instantly: update CLAUDE.md (title and
   identity) to the new name, and write the bare name — nothing else — to the
   file {NAME_REQUEST} ; the harness watches for it and will rename your
   Telegram profile to match.
3. Over the next few messages, learn — ONE question per message: what to call
   them · what they spend their days on · which city to keep their hours in.
   Save each answer into CLAUDE.md as you go, and briefly say you'll remember.
4. Then ask: "What's one thing you've been putting off that I could take off
   your plate?" — and act on the answer immediately, even just a real first step.
5. End by offering one or two standing jobs tailored to what you learned
   (e.g. a morning briefing, watching something for them).
If their first message is already a task: do the task well first, then weave in
the naming afterward. If they dodge a question, drop it gracefully and move on.
Keep every message short — they are on a phone.
""".strip()

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


async def run_claude(prompt: str, continue_session: bool, chat_id: int) -> str:
    system = SYSTEM_PROMPT
    if not BORN_FLAG.exists():
        system += "\n\n" + AWAKENING
    if current_model:
        system += (
            f" You are currently running on the model {current_model}; trust this over "
            "your own guess about which model you are."
        )
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--dangerously-skip-permissions",
        "--append-system-prompt", system,
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
    )
    chat_procs[chat_id] = proc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TASK_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"⏰ Task timed out after {TASK_TIMEOUT // 60} minutes."
    finally:
        chat_procs.pop(chat_id, None)
    if proc.returncode and proc.returncode < 0:
        return "🛑 Task stopped."
    out = stdout.decode(errors="replace").strip()
    if proc.returncode != 0 and not out:
        return f"❌ claude exited {proc.returncode}:\n{stderr.decode(errors='replace')[-1500:]}"
    return out


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
    async with lock:
        stop = asyncio.Event()
        typing = asyncio.create_task(keep_typing(update, stop))
        try:
            result = await run_claude(prompt, chat_has_session.get(chat_id, False), chat_id)
            chat_has_session[chat_id] = True
        finally:
            stop.set()
            await typing
        await send_chunked(update, result)
        BORN_FLAG.touch(exist_ok=True)
        await apply_name_request(update)


async def apply_name_request(update: Update) -> None:
    """The assistant writes its chosen name to a file; we rename the bot to match."""
    if not NAME_REQUEST.exists():
        return
    try:
        name = NAME_REQUEST.read_text().strip().splitlines()[0][:60] if NAME_REQUEST.read_text().strip() else ""
    finally:
        NAME_REQUEST.unlink(missing_ok=True)
    if not name:
        return
    try:
        await update.get_bot().set_my_name(name)
        log.info("assistant renamed itself to %s", name)
    except Exception as e:
        log.warning("rename failed: %s", e)


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
    if _whisper is None:
        await msg.reply_text("🎙️ First voice note — warming up transcription (one-time, can take a minute)…")
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
    await update.effective_message.reply_text(
        "👋 Ken here. Text or voice — I'll get it done.\n"
        "/stop kills the running task\n/new starts a fresh conversation\n/model switches models"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_has_session[update.effective_chat.id] = False
    await update.effective_message.reply_text("🆕 Fresh conversation.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    proc = chat_procs.get(update.effective_chat.id)
    if proc is not None and proc.returncode is None:
        proc.kill()
        await update.effective_message.reply_text("🛑 Stopping the current task.")
    else:
        await update.effective_message.reply_text("Nothing is running.")


_model_cache: tuple[float, list[str]] = (0.0, [])


async def list_models() -> list[str]:
    """Live model list for this account, cached for an hour."""
    global _model_cache
    ts, models = _model_cache
    if models and time.time() - ts < 3600:
        return models
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token:
        return []
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
    _model_cache = (time.time(), models)
    return models


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_model
    if not authorized(update):
        return
    try:
        models = await list_models()
    except Exception as e:
        log.warning("model list fetch failed: %s", e)
        models = []
    if not context.args:
        lines = [f"{'👉' if m == current_model else '  '} {m}" for m in models]
        await update.effective_message.reply_text(
            f"Current: {current_model or '(claude default)'}\n\n"
            + ("\n".join(lines) if lines else "(model list unavailable — you can still switch)")
            + "\n\nSwitch with /model <name> — partial names work (e.g. /model opus). /model default reverts."
        )
        return
    want = context.args[0].lower()
    if want == "default":
        current_model = DEFAULT_MODEL
        await update.effective_message.reply_text(f"🧠 Back to the default: {DEFAULT_MODEL or 'CLI choice'}.")
        return
    match = next((m for m in models if m == want), None) or next((m for m in models if want in m), None)
    if match is None and models:
        await update.effective_message.reply_text(
            f"No model matching “{want}”. Available:\n" + "\n".join(models)
        )
        return
    current_model = match or context.args[0]
    await update.effective_message.reply_text(f"🧠 Model set to {current_model} for new tasks.")


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    log.info("ken is polling")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
