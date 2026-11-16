// Live view (liveview.py): shows a tab of Ken's browser and sends the person's clicks, scrolling and typing to it.
(() => {
  const canvas = document.getElementById("screen"), ctx = canvas.getContext("2d");
  const status = document.getElementById("status"), text = document.getElementById("text");
  const say = (s) => { status.textContent = s; };
  const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${location.pathname.replace(/\/$/, "")}/ws`;
  let ws = null, over = false, tries = 0;

  const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };
  const at = (e) => { const r = canvas.getBoundingClientRect(); return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }; };

  function connect() {
    ws = new WebSocket(wsUrl);
    ws.binaryType = "blob";
    ws.onopen = () => {
      tries = 0;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      // The space the picture gets (CSS pixels): the tab is sized to it. w/h: the most picture worth sending.
      const top = canvas.getBoundingClientRect().top, below = document.body.scrollHeight - canvas.getBoundingClientRect().bottom;
      const cw = innerWidth, ch = Math.max(320, innerHeight - top - below);
      send({ type: "hello", cw, ch, w: Math.round(Math.min(1600, cw * dpr)), h: Math.round(Math.min(1600, ch * dpr)) });
    };
    ws.onmessage = async (ev) => {
      if (typeof ev.data !== "string") {
        const img = await createImageBitmap(ev.data);
        if (canvas.width !== img.width || canvas.height !== img.height) { canvas.width = img.width; canvas.height = img.height; }
        ctx.drawImage(img, 0, 0);
        img.close();
        return;
      }
      const m = JSON.parse(ev.data);
      if (m.type === "ready") say(`Live. This link works for ${m.minutes_left} more minutes. Press Done when you're through.`);
      else if (m.type === "ended") finish({ done: "Done. Ken is carrying on.", cancelled: "Cancelled. Ken knows.", expired: "This link has expired.", ended: "Ken ended this view." }[m.outcome] || "Ended.");
      else if (m.type === "error") finish(m.text);
    };
    ws.onclose = () => {
      if (over) return;
      if (tries++ < 3) { say("Reconnecting…"); setTimeout(connect, 1500); } else say("Lost the connection. Reload the page to try again.");
    };
  }

  function finish(message) {
    over = true;
    say(message);
    document.querySelectorAll("button, input").forEach((b) => { b.disabled = true; });
    canvas.style.opacity = "0.4";
  }

  // Mouse: pressed, moved, released, as they happen. Touch: a tap is a click, a swipe scrolls the page.
  let touch = null, lastMove = 0;
  canvas.addEventListener("pointerdown", (e) => {
    canvas.focus();
    canvas.setPointerCapture(e.pointerId);
    if (e.pointerType === "mouse") { if (e.button === 0) send({ type: "mouse", action: "down", ...at(e) }); }
    else touch = { start: at(e), x: e.clientX, y: e.clientY, scrolling: false };
    e.preventDefault();
  });
  canvas.addEventListener("pointermove", (e) => {
    if (touch) {
      const dx = e.clientX - touch.x, dy = e.clientY - touch.y;
      if (!touch.scrolling && Math.hypot(dx, dy) > 8) touch.scrolling = true;
      if (touch.scrolling) {
        const k = canvas.width / canvas.getBoundingClientRect().width;
        send({ type: "wheel", ...touch.start, dx: -dx * k, dy: -dy * k });
        touch.x = e.clientX; touch.y = e.clientY;
      }
      return;
    }
    const now = performance.now();
    if (e.pointerType === "mouse" && now - lastMove > 33) { lastMove = now; send({ type: "mouse", action: "move", ...at(e) }); }
  });
  canvas.addEventListener("pointerup", (e) => {
    if (touch) {
      if (!touch.scrolling) { send({ type: "mouse", action: "move", ...touch.start }); send({ type: "mouse", action: "down", ...touch.start }); send({ type: "mouse", action: "up", ...touch.start }); }
      touch = null;
    } else if (e.button === 0) send({ type: "mouse", action: "up", ...at(e) });
  });
  canvas.addEventListener("pointercancel", () => { touch = null; });
  canvas.addEventListener("wheel", (e) => { send({ type: "wheel", ...at(e), dx: e.deltaX, dy: e.deltaY }); e.preventDefault(); }, { passive: false });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());

  // Typing straight into the picture (a computer), or the box below it (a phone's keyboard).
  const KEYS = new Set(["Enter", "Backspace", "Tab", "Escape", "Delete", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"]);
  canvas.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return; // paste comes through the paste event
    if (KEYS.has(e.key)) send({ type: "key", key: e.key });
    else if (e.key.length === 1) send({ type: "text", text: e.key });
    else return;
    e.preventDefault();
  });
  document.addEventListener("paste", (e) => {
    if (document.activeElement !== canvas) return;
    const t = e.clipboardData.getData("text");
    if (t) send({ type: "text", text: t });
    e.preventDefault();
  });
  const sendText = () => { if (text.value) { send({ type: "text", text: text.value }); text.value = ""; } };
  document.getElementById("send").onclick = sendText;
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") { sendText(); e.preventDefault(); } });
  document.querySelectorAll("[data-key]").forEach((b) => { b.onclick = () => send({ type: "key", key: b.dataset.key }); });

  document.getElementById("done").onclick = () => send({ type: "done" });
  document.getElementById("cancel").onclick = () => send({ type: "cancel" });
  connect();
})();
