// CHM scripts run in an opaque sandbox. Only bounded reader state crosses
// this bridge; the account page never grants the book access to its DOM.
const forbiddenKeys = new Set(['source_path', 'source_url', 'path', 'url', '__proto__', 'constructor', 'prototype']);

function bookStorage(value) {
  if (!value || typeof value !== 'object') return null;
  const result = { version: 1, local: {}, session: {}, cookies: {} };
  for (const namespace of ['local', 'session', 'cookies']) {
    const entries = value[namespace];
    if (!entries || typeof entries !== 'object' || Array.isArray(entries)) continue;
    for (const [key, content] of Object.entries(entries).slice(0, 64)) {
      if (forbiddenKeys.has(key) || key.length > 100 || key.includes('\0')
          || typeof content !== 'string' || content.length > 2048 || content.includes('\0')) continue;
      result[namespace][key] = content;
    }
  }
  return new TextEncoder().encode(JSON.stringify(result)).length <= 8192 ? result : null;
}

export class ChmSandboxReader {
  constructor(frame, manifest, { onPosition, onError, onSearch } = {}) {
    this.frame = frame;
    this.base = new URL(manifest.renderBaseUrl, location.href);
    if (this.base.origin !== location.origin || !this.base.pathname.endsWith('/')) throw new Error('CHM 正文地址无效');
    this.channel = manifest.renderChannel;
    this.onPosition = onPosition;
    this.onError = onError;
    this.onSearch = onSearch;
    this.position = null;
    this.active = null;
    this.area = 0;
    this.css = '';
    this.pending = null;
    this.navigating = false;
    this.instance = null;
    this.sequence = 0;
    this.onMessage = this.receive.bind(this);
    window.addEventListener('message', this.onMessage);
  }

  owns(source) {
    try {
      for (let depth = 0; source && depth < 16; depth += 1) {
        if (source === this.frame.contentWindow) return true;
        if (source === window || source.parent === source) return false;
        source = source.parent;
      }
    } catch { /* A detached or unrelated frame cannot send reader state. */ }
    return false;
  }

  contentHref(value) {
    if (typeof value !== 'string' || value.length > 4096) return null;
    try {
      const url = new URL(value, this.base);
      if (url.origin !== this.base.origin || !url.pathname.startsWith(this.base.pathname)) return null;
      return url.pathname.slice(this.base.pathname.length) + url.search + url.hash;
    } catch { return null; }
  }

  receive(event) {
    const data = event.data;
    if (event.origin !== 'null' || !data || data.type !== 'recoll-chm'
        || data.channel !== this.channel || !this.owns(event.source)) return;
    if (typeof data.instance !== 'string' || data.instance.length > 128) return;
    if (data.event === 'search') { this.onSearch?.(); return; }
    if (data.event === 'navigate') {
      if (event.source === this.active && data.instance === this.instance) {
        this.navigating = true;
        delete this.frame.dataset.readerContentLoaded;
      }
      return;
    }
    if (!['ready', 'position'].includes(data.event) || data.hasFrames) return;
    if (data.event === 'position' && this.navigating) return;
    if (data.event === 'position' && data.instance !== this.instance) return;
    if (data.event === 'ready' && this.navigating && data.instance === this.instance) return;
    if (this.pending?.restoring && data.restored !== this.pending.restoreId) return;
    const href = this.contentHref(data.position?.href);
    if (href === null) return;
    const area = Math.max(0, Math.min(1e8, Number(data.area) || 0));
    if (this.active && this.owns(this.active) && event.source !== this.active && area < this.area) return;
    if (data.event !== 'ready' && event.source !== this.active) return;
    this.active = event.source;
    this.area = area;
    if (data.event === 'ready') { this.navigating = false; this.instance = data.instance; }
    const number = (value, max) => Math.max(0, Math.min(max, Number(value) || 0));
    const storage = bookStorage(data.position.storage);
    this.position = {
      href, scrollTop: number(data.position.scrollTop, 1e9), scrollLeft: number(data.position.scrollLeft, 1e9),
      percentage: number(data.position.percentage, 1), ...(storage ? { storage } : {}),
    };
    if (data.event === 'ready') {
      this.command('style', { css: this.css });
      if (this.pending?.restore) {
        const restore = this.pending.restore;
        this.pending.restore = null;
        this.pending.restoring = true;
        this.pending.restoreId = String(++this.sequence);
        this.command('restore', { position: restore, requestId: this.pending.restoreId });
        return;
      }
    }
    this.frame.dataset.readerContentLoaded = 'yes';
    this.onPosition?.(this.getPosition());
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.resolve();
      this.pending = null;
    }
  }

  open(href, restore = null) {
    const target = this.contentHref(href);
    if (target === null) return Promise.reject(new Error('CHM 章节地址无效'));
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.resolve();
      this.pending = null;
    }
    delete this.frame.dataset.readerContentLoaded;
    this.active = null;
    this.area = 0;
    this.navigating = true;
    this.instance = null;
    const storage = bookStorage(restore?.storage || this.position?.storage);
    // A name attribute only seeds window.name when the browsing context is
    // created. Recreate the frame so restored book state exists before its
    // first script, including an initial account-page -> opaque navigation.
    const nextFrame = this.frame.cloneNode(false);
    nextFrame.removeAttribute('src');
    nextFrame.name = storage ? 'recoll-chm:' + JSON.stringify(storage) : '';
    this.frame.replaceWith(nextFrame);
    this.frame = nextFrame;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending = null;
        reject(new Error('CHM 正文尚未成功加载，原阅读进度已保留'));
      }, 20000);
      this.pending = { resolve, reject, timer, restore };
      this.frame.src = new URL(target, this.base).href;
    });
  }

  command(command, values = {}) {
    this.active?.postMessage({ type: 'recoll-chm-command', channel: this.channel, command, ...values }, '*');
  }

  setStyle(css) { this.css = css; this.command('style', { css }); }
  scrollBy(top) { this.command('scrollBy', { top }); }
  scrollToRatio(ratio) { this.command('scrollToRatio', { ratio }); }
  find(query) { this.command('find', { query }); }
  getPosition() { return this.frame.dataset.readerContentLoaded && this.position ? structuredClone(this.position) : null; }

  destroy() {
    window.removeEventListener('message', this.onMessage);
    if (this.pending) { clearTimeout(this.pending.timer); this.pending.resolve(); }
    this.pending = null;
    this.active = null;
    this.frame.remove();
  }
}
