/** Keep native shell framing across local page navigation in this WebView session only. */
(() => {
  let native = new URLSearchParams(location.search).get('desktop') === '1';
  try {
    if (native) sessionStorage.setItem('atlas-native-shell', '1');
    native ||= sessionStorage.getItem('atlas-native-shell') === '1';
  } catch { /* The explicit URL still identifies native mode if storage is unavailable. */ }
  if (native) document.documentElement.classList.add('native-shell');
})();
/** Restore only bounded appearance IDs before paint; source routes and graph state stay untouched. */
(() => {
  let choice = { id: 'light', base: 'light' };
  try {
    const saved = JSON.parse(localStorage.getItem('atlas-color-scheme') || 'null');
    if (saved && /^(light|dark|mod:[a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47})$/.test(saved.id)
        && ['light', 'dark'].includes(saved.base)) choice = saved;
  } catch { /* Storage restrictions or invalid data use the readable light base. */ }
  document.documentElement.setAttribute('data-color-scheme', choice.id);
  document.documentElement.setAttribute('data-color-mode', choice.base);
})();
