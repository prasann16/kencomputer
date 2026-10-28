"""Open loops: the pinned Telegram list read from memory/open-loops.md."""
import os
import tempfile
import time

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import loops  # noqa: E402

FILE = """Open loops with Swap: numbered items. Close by number.

## Waiting on Swap
1. Counsellor: book a session. Oct 28: "will see, not now".
2. Zynnex AI: founder email draft ready in Gmail;
   he reads and sends.
4. (closed: Valerie asks 6 yrs)

## Waiting on others
7. PR #83 (scheduler): Prasann reviewing.

## Parked until after Nov 10
8. Voice AI client test.

## Closed Oct 28
- Pin code saved.
"""
DAY = time.mktime(time.strptime("2026-10-28 12:00", "%Y-%m-%d %H:%M"))


def test_reads_waiting_items_and_skips_closed_ones():
    items = loops.open_items(FILE)
    assert [i["num"] for i in items] == [1, 2]
    assert items[1]["end"] - items[1]["line"] == 2  # the wrapped line belongs to item 2


def test_tolerates_format_drift():
    drift = """### waiting on you
- 3) Pick a date
**5.** Read the deck
- [ ] #6 Answer Mira
- a loose bullet
- 10 high intent drafts a day
## Waiting for Prasann and others
- 9. review
"""
    items = loops.open_items(drift)
    assert [i["num"] for i in items] == [3, 5, 6, None, None]
    assert loops.parse(drift)[1]["kind"] == "others"


def test_section_kinds():
    assert loops.kind("Waiting on Swap") == "you"
    assert loops.kind("Waiting on others") == "others"
    assert loops.kind("Waiting on Ken") == "ken"
    assert loops.kind("To read") == "read"
    assert loops.kind("Closed Oct 28") == "closed"
    assert loops.kind("Parked until after Nov 10") == "other"


def test_render_lists_items_with_buttons():
    text, rows = loops.render(FILE)
    assert text.startswith("📌 <b>Open loops</b>")
    assert "1. Counsellor" in text and "2. Zynnex" in text
    assert "Voice AI" not in text and "Valerie" not in text
    assert "1 waiting on others" in text
    assert rows == [[("✅ 1", "loop:1"), ("✅ 2", "loop:2")]]


def test_render_caps_buttons_four_a_row():
    many = "## Waiting on you\n" + "".join(f"{n}. thing {n}\n" for n in range(1, 12))
    _, rows = loops.render(many)
    assert [len(r) for r in rows] == [4, 4]


def test_render_when_nothing_is_waiting():
    text, rows = loops.render("## Waiting on others\n1. x\n")
    assert "Nothing waiting on you." in text and rows == []


def test_closing_moves_the_item_under_closed():
    new, said = loops.close(FILE, 2, when=DAY)
    assert said.startswith("Zynnex")
    assert [i["num"] for i in loops.open_items(new)] == [1]
    assert "he reads and sends" not in new  # its wrapped line went with it
    closed = next(s for s in loops.parse(new) if s["kind"] == "closed")
    assert [i["text"] for i in closed["items"]] == ["Pin code saved.", "Zynnex AI: founder email draft ready in Gmail;"]


def test_closing_starts_a_new_closed_section_on_a_new_day():
    new, _ = loops.close(FILE, 1, when=DAY + 86400)
    assert "## Closed Oct 29\n- Counsellor" in new


def test_closing_an_unknown_number():
    assert loops.close(FILE, 4) is None and loops.close(FILE, 99) is None


def test_add_read_and_close_it_by_file():
    new, num = loops.add_read(FILE, "Voice AI market scan", "/home/s/.ken/work/research/scan.md")
    assert num == 9
    assert "## To read\n9. Voice AI market scan: /home/s/.ken/work/research/scan.md" in new
    assert new.index("## To read") < new.index("## Parked")
    again, num2 = loops.add_read(new, "Second", "/x/b.md")
    assert num2 == 10 and "9. Voice AI market scan: /home/s/.ken/work/research/scan.md\n10. Second: /x/b.md" in again
    text, _ = loops.render(again)
    assert "To read" in text and "10. Second" in text
    done, _ = loops.close_matching(again, "/x/b.md", when=DAY)
    assert [i["num"] for i in loops.open_items(done)] == [1, 2, 9]


def test_research_deliverables_from_a_run_report():
    report = ("Done. Wrote the scan to `/home/s/.ken/work/research/voice-ai.md` and notes in "
              "~/.ken/work/notes/raw.md. Updated /home/s/.ken/memory/job-search.md and SOUL at /home/s/.ken/work/SOUL.md. "
              "Also made /home/s/.ken/work/chart.png.")
    assert loops.deliverables(report) == ["/home/s/.ken/work/research/voice-ai.md", "~/.ken/work/notes/raw.md"]
    assert loops.deliverables("No files.") == []
    paths = loops.deliverables(report)
    assert loops.mentioned(paths, "Your scan is ready: voice-ai.md") == paths[0]
    assert loops.mentioned(paths, "Here's what I found.") is None


def test_keeps_at_most_five_read_pins():
    pins = [{"msg": n} for n in range(1, 8)]
    kept, dropped = loops.cap(pins)
    assert [p["msg"] for p in kept] == [3, 4, 5, 6, 7]
    assert [p["msg"] for p in dropped] == [1, 2]
    assert loops.cap(pins[:3]) == (pins[:3], [])


class FakeBot:
    """Records what Ken asks Telegram to do; editing fails as if the pin was deleted."""

    def __init__(self, gone=False):
        self.calls, self.gone = [], gone

    async def edit_message_text(self, text, **kw):
        from telegram.error import BadRequest
        self.calls.append(("edit", kw["message_id"]))
        if self.gone:
            raise BadRequest("Message to edit not found")

    async def send_message(self, chat, text, **kw):
        from types import SimpleNamespace
        self.calls.append(("send", kw.get("disable_notification")))
        return SimpleNamespace(message_id=42)

    async def pin_chat_message(self, chat, mid, disable_notification=False):
        self.calls.append(("pin", mid, disable_notification))

    async def unpin_chat_message(self, chat, message_id=None):
        self.calls.append(("unpin", message_id))


def test_pin_is_edited_in_place_and_replaced_when_gone():
    import asyncio
    import bot
    bot.ALLOWED_USER_ID = 7
    bot.write_loops(FILE)
    first = FakeBot()
    asyncio.run(bot.refresh_pin(first))
    assert first.calls == [("send", True), ("pin", 42, True)]
    asyncio.run(bot.refresh_pin(first))  # nothing changed: nothing sent
    assert len(first.calls) == 2
    bot.write_loops(loops.close(FILE, 1)[0])
    asyncio.run(bot.refresh_pin(first))
    assert first.calls[-1] == ("edit", 42)
    bot.write_loops(FILE)
    gone = FakeBot(gone=True)
    asyncio.run(bot.refresh_pin(gone))
    assert gone.calls == [("edit", 42), ("send", True), ("pin", 42, True)]


def test_research_result_is_pinned_then_read():
    import asyncio
    from types import SimpleNamespace
    import bot
    bot.ALLOWED_USER_ID = 7
    bot.write_loops(FILE)
    bot.ENGINE.kv_set("loops:reads", "[]")
    bot.remember_deliverables({"run": "nope", "title": "Market scan", "summary": "Saved /w/research/scan.md"})
    fake = FakeBot()
    asyncio.run(bot.pin_deliverable(fake, "Your scan is in scan.md", SimpleNamespace(message_id=99)))
    assert ("pin", 99, True) in fake.calls
    assert "Market scan: /w/research/scan.md" in bot.loops_text()
    assert asyncio.run(bot.read_pin_answered(fake, 99))
    assert ("unpin", 99) in fake.calls
    assert "scan.md" not in loops.render(bot.loops_text())[0]


def test_pending_in_the_app_and_the_prompt_rule():
    import bot
    bot.write_loops(FILE)
    reply = bot.pending_plain()["reply"]
    assert "1. Counsellor" in reply and "<b>" not in reply
    assert "open-loops.md" in bot.SYSTEM_PROMPT and "pending" in bot.COMMANDS
