/* Shared account lifecycle and small enhancements for server-rendered pages. */
(() => {
  'use strict';
  const node = document.getElementById('recoll-account-data');
  let config = {};
  try { config = JSON.parse(node ? node.textContent : '{}'); } catch (_) { /* fail closed */ }
  const baseURL = String(config.baseURL || '').replace(/\/$/, '');
  const user = config.user || null;
  const sessionId = config.sessionId || (config.session && config.session.id) || null;
  const instance = encodeURIComponent(baseURL || '/');
  const channelName = 'recoll-accounts:' + instance;
  const signalKey = channelName + ':signal';
  const tabId = Math.random().toString(36).slice(2);
  let expired = false;
  let checking = null;
  let channel;
  try { if ('BroadcastChannel' in window) channel = new BroadcastChannel(channelName); } catch (_) { /* storage fallback */ }

  const url = (path) => baseURL + '/' + String(path || '').replace(/^\/+/, '');
  const clearPrivateCache = () => {
    if (!user) return;
    const prefixes = [`recoll-account:${instance}:${user.id}:`, `recoll-reader:account:${user.id}:`];
    for (const storageName of ['localStorage', 'sessionStorage']) {
      try {
        const storage = window[storageName];
        for (let i = storage.length - 1; i >= 0; i--) {
          const key = storage.key(i);
          if (key && prefixes.some(prefix => key.startsWith(prefix))) storage.removeItem(key);
        }
      } catch (_) { /* private browser modes may deny storage */ }
    }
  };
  const dispatch = (message) => window.dispatchEvent(new CustomEvent('recoll-account-change', {detail: message}));
  const broadcast = (type, extra = {}) => {
    const message = {...extra, type, userId: user && user.id, sessionId, tabId, at: Date.now()};
    if (type === 'logout' || type === 'history-cleared') clearPrivateCache();
    dispatch(message);
    if (channel) channel.postMessage(message);
    else {
      try { localStorage.setItem(signalKey, JSON.stringify(message)); localStorage.removeItem(signalKey); } catch (_) { /* no shared storage */ }
    }
  };
  const expire = (type = 'logout', announce = false) => {
    if (expired) return;
    expired = true;
    clearPrivateCache();
    if (announce) broadcast(type);
    else dispatch({type, userId: user && user.id, sessionId});
    document.querySelectorAll('form button, form input[type=submit]').forEach(button => { button.disabled = true; });
    // Dispatch first so readers can destroy frames, blobs and pending writes.
    if (user && !document.querySelector('[data-account-notify="logout"]')) {
      window.location.replace(url('/login') + '?next=' + encodeURIComponent(window.location.pathname + window.location.search));
    }
  };
  const receive = (message) => {
    if (!message || message.tabId === tabId || !user) return;
    if (message.type === 'session-started') {
      if (message.userId !== user.id || message.sessionId !== sessionId) expire('session-changed');
      return;
    }
    if (message.userId !== user.id || message.sessionId !== sessionId) return;
    if (message.type === 'logout') { expire('logout'); return; }
    if (message.type === 'history-cleared') clearPrivateCache();
    dispatch(message);
  };
  if (channel) channel.onmessage = event => receive(event.data);
  window.addEventListener('storage', event => {
    if (event.key !== signalKey || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch (_) { /* ignore unrelated storage */ }
  });

  const accountFetch = async (path, options = {}) => {
    if (expired) throw new Error('登录状态已变化，请重新打开页面。');
    const headers = new Headers(options.headers || {});
    const method = String(options.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', config.csrf || '');
    headers.set('Accept', 'application/json');
    const response = await fetch(url(path), {...options, headers, credentials: 'same-origin', cache: 'no-store'});
    if (response.status === 401) expire('logout', true);
    return response;
  };
  const validateSession = () => {
    if (!user || expired) return Promise.resolve(false);
    if (checking) return checking;
    checking = accountFetch('/api/account').then(async response => {
      if (!response.ok) {
        if (response.status === 403 || response.status === 401) {
          expire('logout');
          return false;
        }
        throw new Error('暂时无法确认会话，请稍后重试。');
      }
      const latest = await response.json();
      const latestSession = latest.sessionId || (latest.session && latest.session.id);
      if (!latest.user || latest.user.id !== user.id || latestSession !== sessionId) {
        expire('session-changed');
        return false;
      }
      // Existing reading handles retain their epochs. Never refresh them silently.
      return true;
    }).finally(() => { checking = null; });
    return checking;
  };
  window.RecollAccount = {user, sessionId, csrf: config.csrf || '', settings: config.settings || {},
    baseURL, url, fetch: accountFetch, validateSession, broadcast, clearPrivateCache};

  const ready = () => {
    document.querySelectorAll('time[data-utc]').forEach(element => {
      const raw = element.dataset.utc;
      const value = /^\d+(\.\d+)?$/.test(raw) ? Number(raw) * 1000 : raw;
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) {
        element.dateTime = date.toISOString();
        element.textContent = date.toLocaleString();
      }
    });
    document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    }));
    document.querySelectorAll('[data-account-notify]').forEach(element => {
      const type = element.dataset.accountNotify;
      broadcast(type, type === 'settings-changed' ? {settings: config.settings} : {});
      if (type === 'logout') expired = true;
    });
    const changed = new URLSearchParams(window.location.search).get('changed');
    if (changed && ['search', 'reading', 'progress', 'reading-all'].includes(changed)) {
      broadcast('history-cleared', {scope: changed});
      const cleaned = new URL(window.location.href);
      cleaned.searchParams.delete('changed');
      history.replaceState(history.state, '', cleaned.pathname + cleaned.search + cleaned.hash);
    }
    if (user && !expired) broadcast('session-started');

    document.querySelectorAll('button.shelf-toggle').forEach(button => button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      const container = button.closest('.search-result-links') || button.parentElement;
      let feedback = container.querySelector('.app-shelf-status');
      if (!feedback) {
        feedback = document.createElement('span');
        feedback.className = 'app-shelf-status';
        feedback.setAttribute('role', 'status');
        feedback.setAttribute('aria-live', 'polite');
        container.append(feedback);
      }
      feedback.dataset.error = 'false';
      feedback.textContent = '正在更新书架…';
      try {
        const removing = button.dataset.onShelf === '1';
        const body = removing ? {document_id: button.dataset.documentId} : {handle: button.dataset.handle};
        const response = await accountFetch('/api/bookshelf/' + (removing ? 'remove' : 'add'), {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || result.error || '书架保存失败，请重试。');
        button.dataset.onShelf = removing ? '0' : '1';
        button.textContent = removing ? '加入书架' : '移出书架';
        button.setAttribute('aria-pressed', removing ? 'false' : 'true');
        feedback.textContent = removing ? '已移出书架，阅读进度仍会保留。' : '已加入我的书架。';
        broadcast('bookshelf-changed', {documentId: button.dataset.documentId});
      } catch (error) {
        feedback.dataset.error = 'true';
        feedback.textContent = error.message || '书架保存失败，请重试。';
      }
      finally { if (!expired) button.disabled = false; }
    }));
    prepareLegacyImport();
  };

  const integerKeys = new Set(['context', 'stem', 'dirdepth', 'maxchars', 'maxresults', 'perpage', 'collapsedups', 'shortenpaths']);
  const cookieKeys = new Set([...integerKeys, 'timefmt', 'csvfields', 'title_link', 'defaultsort']);
  const getLegacy = () => {
    const positions = [], positionKeys = new Map(), preferences = {}, cookies = [];
    let fontKey = false;
    try {
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (!key || !key.startsWith('recoll-reader:position:') || positions.length >= 100) continue;
        const raw = localStorage.getItem(key);
        if (!raw || raw.length > 16384) continue;
        try {
          const position = JSON.parse(raw);
          if (position && typeof position === 'object' && !Array.isArray(position)) {
            const path = key.slice('recoll-reader:position:'.length);
            positions.push({path, position}); positionKeys.set(path, key);
          }
        } catch (_) { /* invalid old positions are not uploaded */ }
      }
      const font = localStorage.getItem('recoll-reader:fontSize');
      if (font !== null && Number.isInteger(Number(font)) && Number(font) >= 16 && Number(font) <= 32) {
        preferences.font_size = Number(font); fontKey = true;
      }
    } catch (_) { /* unavailable storage */ }
    document.cookie.split(';').forEach(entry => {
      const separator = entry.indexOf('=');
      if (separator < 0) return;
      const key = entry.slice(0, separator).trim();
      if (!cookieKeys.has(key)) return;
      try {
        const value = decodeURIComponent(entry.slice(separator + 1));
        if (value.length > 1000) return;
        if (integerKeys.has(key)) {
          if (!/^\d+$/.test(value)) return;
          preferences[key] = Number(value);
        } else preferences[key] = value;
        cookies.push(key);
      } catch (_) { /* invalid old cookie */ }
    });
    return {positions, positionKeys, preferences, cookies, fontKey};
  };
  const deleteCookie = (name) => {
    for (const path of new Set(['/', baseURL || '/', baseURL + '/'])) {
      document.cookie = `${name}=; Max-Age=0; Path=${path}; SameSite=Lax`;
    }
  };
  const prepareLegacyImport = () => {
    const section = document.getElementById('legacy-import');
    if (!section || !user) return;
    const legacy = getLegacy();
    if (!legacy.positions.length && !Object.keys(legacy.preferences).length) return;
    section.hidden = false;
    document.getElementById('legacy-summary').textContent = `发现 ${legacy.positions.length} 条可导入旧位置（每批最多 100 条），以及 ${Object.keys(legacy.preferences).length} 项旧偏好。`;
    const status = document.getElementById('legacy-import-status');
    document.getElementById('legacy-import-submit').addEventListener('click', async event => {
      const includePositions = document.getElementById('legacy-import-positions').checked;
      const includePreferences = document.getElementById('legacy-import-preferences').checked;
      if (!includePositions && !includePreferences) { status.textContent = '请先选择要导入的数据类别。'; return; }
      event.target.disabled = true;
      try {
        const response = await accountFetch('/api/account/import', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({positions: includePositions ? legacy.positions : [], preferences: includePreferences ? legacy.preferences : {},
            progress_epoch: config.settings && config.settings.progress_epoch, overwrite: false})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || result.error || '导入未完成，请稍后重试。');
        const imported = Array.isArray(result.imported) ? result.imported : [];
        for (const item of imported) {
          const path = typeof item === 'string' ? item : item.path;
          const key = legacy.positionKeys.get(path);
          if (key) { try { localStorage.removeItem(key); } catch (_) {} }
        }
        if (includePreferences && result.preferences_imported) {
          legacy.cookies.forEach(deleteCookie);
          if (legacy.fontKey) { try { localStorage.removeItem('recoll-reader:fontSize'); } catch (_) {} }
        }
        status.textContent = `导入已完成，成功导入 ${imported.length} 条位置。未导入或已存在的进度会保留；刷新页面可查看剩余旧数据。`;
        broadcast('settings-changed', {source: 'legacy-import'});
      } catch (error) { status.textContent = error.message || '导入未完成。'; }
      finally { if (!expired) event.target.disabled = false; }
    });
    document.getElementById('legacy-delete').addEventListener('click', () => {
      if (!window.confirm('删除此浏览器中所有旧阅读位置、旧字体及旧设置？云端账号数据不受影响。')) return;
      try {
        for (let index = localStorage.length - 1; index >= 0; index--) {
          const key = localStorage.key(index);
          if (key && (key.startsWith('recoll-reader:position:') || ['recoll-reader:fontSize', 'recoll-reader:folder', 'recoll-reader:parserRegex'].includes(key))) localStorage.removeItem(key);
        }
      } catch (_) { /* unavailable storage */ }
      legacy.cookies.forEach(deleteCookie);
      section.hidden = true;
    });
  };
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && !expired) void validateSession().catch(() => {}); });
  window.addEventListener('pageshow', event => { if (event.persisted && !expired) void validateSession().catch(() => {}); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
