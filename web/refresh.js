// Ken in the cloud: when a new version of Ken's screens is deployed, an open tab picks it up by itself. The site that
// serves the screens stamps the page (<meta name="ken-ui-version">) and serves the current stamp at /version.json.
// Every few minutes, and when the tab comes back into view, the page asks; if it changed, it reloads (same address),
// but only when nothing would be lost: no message being typed, no upload or recording, no popup open. Otherwise it
// waits and tries again. Off in the Mac app (it updates with releases) and wherever the page has no stamp.
(() => {
  const CHECK_MS = 5 * 60_000, RETRY_MS = 30_000;

  // Whether reloading now loses nothing they're in the middle of.
  function safeToReload(doc) {
    const typed = [...doc.querySelectorAll('textarea, input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]):not([type=time])')]
      .some((el) => String(el.value || '').trim());
    const busy = doc.querySelector('.recording-input, .upload-status, .modal, .scrim, .talk, .doc-viewer, .drawer');
    return !typed && !busy;
  }

  function start(win) {
    const doc = win.document;
    const mine = doc.querySelector('meta[name="ken-ui-version"]')?.getAttribute('content');
    if (!mine || win.kenDesktop) return null;
    let newer = false, retry = 0;
    const tryReload = () => {
      clearTimeout(retry);
      if (safeToReload(doc)) win.location.reload();
      else retry = setTimeout(tryReload, RETRY_MS);
    };
    const check = async () => {
      if (newer) return tryReload();
      try {
        const r = await win.fetch('/version.json', { cache: 'no-store' });
        const current = r.ok ? (await r.json()).version : null;
        if (current && current !== mine) { newer = true; tryReload(); }
      } catch {}  // offline or asleep: the next check asks again
    };
    const timer = setInterval(check, CHECK_MS);
    doc.addEventListener('visibilitychange', () => { if (doc.visibilityState === 'visible') check(); });
    return { check, stop: () => { clearInterval(timer); clearTimeout(retry); } };
  }

  if (typeof window !== 'undefined') window.kenRefresh = { safeToReload, start, running: start(window) };
})();
