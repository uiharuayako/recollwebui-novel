/* Runs inside an opaque CHM frame. Never receives account credentials. */
(() => {
  'use strict';
  const script = document.currentScript;
  const channel = script?.dataset.chmChannel;
  const parentOrigin = script?.dataset.chmOrigin;
  let contentRoot;
  try { contentRoot = new URL(script?.dataset.chmRoot); } catch (_) { return; }
  if (!channel || !parentOrigin || window === window.top) return;
  const instance = typeof crypto.randomUUID === 'function' ? crypto.randomUUID()
    : [...crypto.getRandomValues(new Uint32Array(4))].map((part) => part.toString(16).padStart(8, '0')).join('');

  const NAME_PREFIX = 'recoll-chm:';
  const MAX_STORAGE_BYTES = 8192;
  const MAX_ENTRIES = 64;
  const FORBIDDEN_KEYS = new Set(['source_path', 'source_url', 'path', 'url', '__proto__', 'constructor', 'prototype']);
  const bytes = (value) => new TextEncoder().encode(value).length;
  const validKey = (key) => typeof key === 'string' && key.length <= 100 && !key.includes('\0') && !FORBIDDEN_KEYS.has(key);
  const emptyStorage = () => ({ version: 1, local: Object.create(null), session: Object.create(null), cookies: Object.create(null) });
  const sanitizeStorage = (value) => {
    const result = emptyStorage();
    if (!value || value.version !== 1) return result;
    for (const namespace of ['local', 'session', 'cookies']) {
      if (!value[namespace] || typeof value[namespace] !== 'object' || Array.isArray(value[namespace])) continue;
      for (const [key, item] of Object.entries(value[namespace]).slice(0, MAX_ENTRIES)) {
        if (!validKey(key) || typeof item !== 'string' || item.length > 2048 || item.includes('\0')) continue;
        result[namespace][key] = item;
        if (bytes(JSON.stringify(result)) > MAX_STORAGE_BYTES) delete result[namespace][key];
      }
    }
    return result;
  };
  let storage = emptyStorage();
  try {
    if (window.name.startsWith(NAME_PREFIX) && bytes(window.name) <= MAX_STORAGE_BYTES + NAME_PREFIX.length) {
      storage = sanitizeStorage(JSON.parse(window.name.slice(NAME_PREFIX.length)));
    }
  } catch (_) { /* An existing frame name is not a storage snapshot. */ }
  const saveStorage = () => {
    window.name = NAME_PREFIX + JSON.stringify(storage);
  };
  const setItem = (namespace, key, value) => {
    key = String(key); value = String(value);
    const entries = storage[namespace];
    if (!validKey(key) || value.length > 2048 || value.includes('\0') ||
        (!Object.hasOwn(entries, key) && Object.keys(entries).length >= MAX_ENTRIES)) {
      throw new DOMException('Book storage limit exceeded', 'QuotaExceededError');
    }
    const previous = entries[key];
    entries[key] = value;
    if (bytes(JSON.stringify(storage)) > MAX_STORAGE_BYTES) {
      if (previous === undefined) delete entries[key]; else entries[key] = previous;
      throw new DOMException('Book storage limit exceeded', 'QuotaExceededError');
    }
    saveStorage();
  };
  const removeItem = (namespace, key) => { delete storage[namespace][String(key)]; saveStorage(); };
  const makeStorage = (namespace) => {
    const api = {
      getItem: (key) => storage[namespace][String(key)] ?? null,
      setItem: (key, value) => setItem(namespace, key, value),
      removeItem: (key) => removeItem(namespace, key),
      clear: () => { storage[namespace] = Object.create(null); saveStorage(); },
      key: (index) => Object.keys(storage[namespace])[Number(index)] ?? null,
      get length() { return Object.keys(storage[namespace]).length; },
    };
    return new Proxy(api, {
      get: (target, key) => key in target ? target[key] : storage[namespace][key],
      set: (_target, key, value) => { setItem(namespace, key, value); return true; },
      deleteProperty: (_target, key) => { removeItem(namespace, key); return true; },
      ownKeys: () => Object.keys(storage[namespace]),
      getOwnPropertyDescriptor: (_target, key) => Object.hasOwn(storage[namespace], key)
        ? { configurable: true, enumerable: true, writable: true, value: storage[namespace][key] } : undefined,
    });
  };
  for (const [property, namespace] of [['localStorage', 'local'], ['sessionStorage', 'session']]) {
    Object.defineProperty(window, property, { value: makeStorage(namespace), configurable: false });
  }
  // Old CHM preference helpers use cookies. Keep them entirely inside this book.
  Object.defineProperty(document, 'cookie', {
    configurable: false,
    get: () => Object.entries(storage.cookies).map(([key, value]) => `${key}=${value}`).join('; '),
    set: (value) => {
      const parts = String(value).split(';');
      const entry = parts.shift();
      const separator = entry.indexOf('=');
      if (separator < 0) return;
      const key = entry.slice(0, separator).trim();
      const expires = parts.find((part) => /^\s*expires=/i.test(part));
      const maxAge = parts.find((part) => /^\s*max-age=/i.test(part));
      if ((maxAge && Number(maxAge.split('=')[1]) <= 0) ||
          (expires && Date.parse(expires.slice(expires.indexOf('=') + 1)) <= Date.now())) {
        removeItem('cookies', key);
      } else {
        try { setItem('cookies', key, entry.slice(separator + 1).trim()); } catch (_) { /* Cookie writes are best effort. */ }
      }
    },
  });

  const relativeHref = () => {
    const url = new URL(window.location.href);
    if (url.origin !== contentRoot.origin || !url.pathname.startsWith(contentRoot.pathname)) return '';
    let path = url.pathname.slice(contentRoot.pathname.length);
    try { path = decodeURIComponent(path); } catch (_) { /* Keep malformed escapes inert. */ }
    return path + url.search + url.hash;
  };
  const hasFrames = () => [...document.querySelectorAll('iframe,frame')].some((frame) => {
    const rect = frame.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
  const maxScroll = () => Math.max(0, (document.scrollingElement?.scrollHeight || 0) - window.innerHeight);
  const position = () => ({
    href: relativeHref(), scrollTop: window.scrollY, scrollLeft: window.scrollX,
    percentage: maxScroll() ? Math.min(1, Math.max(0, window.scrollY / maxScroll())) : 0,
    storage: sanitizeStorage(storage),
  });
  const send = (event, extra = {}) => {
    const current = position();
    if (!current.href) return;
    window.top.postMessage({ type: 'recoll-chm', channel, instance, event, position: current,
      title: document.title, hasFrames: hasFrames(), area: window.innerWidth * window.innerHeight, ...extra }, parentOrigin);
  };
  let loaded = false;
  let navigating = false;
  let scrollTimer;
  let layoutRevision = 0;
  let restoreRevision = 0;
  let restoring = false;
  const sendPosition = () => { if (loaded && !navigating) send('position'); };
  const schedulePosition = () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(sendPosition, 100);
  };
  window.addEventListener('scroll', schedulePosition, { passive: true });
  window.addEventListener('resize', schedulePosition, { passive: true });
  window.addEventListener('hashchange', sendPosition);
  const navigationStarted = () => {
    if (navigating) return;
    navigating = true;
    clearTimeout(scrollTimer);
    // Old CHM links update their chapter storage before assigning location.
    // Never report that new chapter alongside the departing page's scroll.
    send('navigate');
  };
  window.addEventListener('beforeunload', navigationStarted);
  window.addEventListener('pagehide', navigationStarted);
  document.addEventListener('visibilitychange', () => { if (document.hidden) sendPosition(); });

  const ready = () => {
    navigating = false;
    loaded = true;
    requestAnimationFrame(() => { if (!navigating) send('ready'); });
  };
  if (document.readyState === 'complete') ready();
  else window.addEventListener('load', ready, { once: true });
  window.addEventListener('pageshow', (event) => { if (event.persisted) ready(); });

  const applyStyle = (css) => {
    if (typeof css !== 'string' || css.length > 65536 || hasFrames()) return;
    const revision = ++layoutRevision;
    const ratio = position().percentage;
    const element = document.elementFromPoint(Math.min(window.innerWidth / 2, 120), 12);
    const anchor = element?.closest('p,li,h1,h2,h3,h4,h5,h6,pre,blockquote') || element;
    const oldTop = anchor?.getBoundingClientRect().top;
    let style = document.getElementById('recoll-chm-reader-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'recoll-chm-reader-style';
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = css;
    if (restoring) return;
    requestAnimationFrame(() => {
      if (revision !== layoutRevision) return;
      if (anchor?.isConnected && Number.isFinite(oldTop)) window.scrollBy(0, anchor.getBoundingClientRect().top - oldTop);
      else window.scrollTo(window.scrollX, maxScroll() * ratio);
      sendPosition();
    });
  };
  window.addEventListener('message', (event) => {
    const command = event.data;
    if (event.source !== window.top || event.origin !== parentOrigin || !command ||
        command.type !== 'recoll-chm-command' || command.channel !== channel) return;
    switch (command.command) {
      case 'style': applyStyle(command.css); break;
      case 'scrollBy':
        if (Number.isFinite(command.top)) window.scrollBy(0, command.top);
        sendPosition(); break;
      case 'scrollToRatio':
        if (Number.isFinite(command.ratio)) window.scrollTo(0, maxScroll() * Math.max(0, Math.min(1, command.ratio)));
        sendPosition(); break;
      case 'restore': {
        const restored = command.position;
        if (!restored || typeof restored !== 'object') break;
        layoutRevision += 1;
        const revision = ++restoreRevision;
        restoring = true;
        if (restored.storage) { storage = sanitizeStorage(restored.storage); saveStorage(); }
        requestAnimationFrame(() => {
          if (revision !== restoreRevision || navigating) return;
          window.scrollTo(Number.isFinite(restored.scrollLeft) ? restored.scrollLeft : 0,
            Number.isFinite(restored.scrollTop) ? restored.scrollTop : maxScroll() * (Number(restored.percentage) || 0));
          restoring = false;
          send('position', { restored: command.requestId });
        });
        break;
      }
      case 'find': {
        const query = typeof command.query === 'string' ? command.query.slice(0, 512) : '';
        const found = !!query && typeof window.find === 'function' && window.find(query, false, false, true, false, true, false);
        send('position', { found });
        break;
      }
      case 'snapshot': sendPosition(); break;
      default: break;
    }
  });
})();
