// Run before the stylesheets so a saved appearance is applied before first paint.
(() => {
  const key = 'ken-theme';
  const normalize = (value) => ['light', 'dark'].includes(value) ? value : 'system';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try { preference = normalize(localStorage.getItem(key)); } catch {}

  const apply = () => {
    const theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#181818' : '#ffffff');
    window.dispatchEvent(new Event('ken-theme-change'));
  };
  const syncDesktop = () => window.kenDesktop?.setTheme?.(preference).catch(() => {});
  window.kenTheme = Object.freeze({
    get preference() { return preference; },
    setPreference(value) {
      preference = normalize(value);
      try { localStorage.setItem(key, preference); } catch {}
      apply();
      syncDesktop();
    },
  });
  system.addEventListener('change', apply);
  window.addEventListener('storage', (event) => {
    if (event.storageArea !== localStorage || (event.key !== key && event.key !== null)) return;
    preference = normalize(event.newValue);
    apply();
    syncDesktop();
  });
  apply();
  syncDesktop();
})();
