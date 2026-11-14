/* Server-rendered sharing management: copying never needs a page mutation. */
(() => {
  'use strict';
  document.querySelectorAll('[data-share-card]').forEach(card => {
    const input = card.querySelector('[data-share-url]');
    const button = card.querySelector('[data-share-copy]');
    const status = card.querySelector('[data-share-copy-status]');
    if (!input || !button || !status) return;
    try { input.value = new URL(input.value, window.location.href).href; } catch (_) { return; }
    button.hidden = false;
    input.addEventListener('click', () => input.select());
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(input.value);
        status.textContent = '链接已复制，可以分享给他人。';
      } catch (_) {
        input.focus();
        input.select();
        input.setSelectionRange(0, input.value.length);
        status.textContent = '已选中链接，请长按或按 Ctrl+C / ⌘C 复制。';
      } finally {
        button.disabled = false;
      }
    });
  });
})();
