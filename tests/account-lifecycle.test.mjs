import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../static/accounts.js', import.meta.url), 'utf8');
const config = { user: { id: 'account-a' }, sessionId: 'session-a', csrf: 'csrf-a', baseURL: '/library' };
const matchingAccount = { user: { id: 'account-a' }, sessionId: 'session-a' };
const privateKey = 'recoll-account:%2Flibrary:account-a:pending';
const otherAccountKey = 'recoll-account:%2Flibrary:account-b:pending';
const response = (data = matchingAccount, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => data,
});

function storage() {
  const entries = new Map([[privateKey, 'private position'], [otherAccountKey, 'other account']]);
  return {
    get length() { return entries.size; },
    key: index => [...entries.keys()][index] ?? null,
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, String(value)),
    removeItem: key => entries.delete(key),
  };
}

function fixture(fetchImpl = async () => response()) {
  const events = [], redirects = [], notifications = [], calls = [];
  const buttons = [{ disabled: false }];
  const localStorage = storage(), sessionStorage = storage();
  class MockBroadcastChannel {
    postMessage(message) { notifications.push(message); }
  }
  const window = {
    BroadcastChannel: MockBroadcastChannel, localStorage, sessionStorage,
    addEventListener() {},
    dispatchEvent(event) { events.push(event); },
    location: {
      pathname: '/library/reader', search: '?document_id=book-a',
      replace(target) { redirects.push(target); },
    },
  };
  const document = {
    // The lifecycle API is installed before DOMContentLoaded; page enhancement
    // callbacks are unrelated to these identity and transient-failure checks.
    readyState: 'loading',
    getElementById: id => id === 'recoll-account-data' ? { textContent: JSON.stringify(config) } : null,
    addEventListener() {},
    querySelector: () => null,
    querySelectorAll: selector => selector === 'form button, form input[type=submit]' ? buttons : [],
  };
  vm.runInNewContext(source, {
    window, document, Headers, localStorage, sessionStorage,
    BroadcastChannel: MockBroadcastChannel,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    fetch: (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  }, { filename: 'static/accounts.js' });
  return { account: window.RecollAccount, calls, events, redirects, notifications, buttons, localStorage, sessionStorage };
}

function assertStillActive(f) {
  assert.equal(f.events.length, 0);
  assert.equal(f.redirects.length, 0);
  assert.equal(f.notifications.length, 0);
  assert.equal(f.buttons[0].disabled, false);
  assert.equal(f.localStorage.getItem(privateKey), 'private position');
  assert.equal(f.sessionStorage.getItem(privateKey), 'private position');
}

function assertExpired(f, type) {
  assert.equal(f.events.length, 1);
  assert.equal(f.events[0].type, 'recoll-account-change');
  assert.equal(f.events[0].detail.type, type);
  assert.equal(f.redirects.length, 1);
  assert.equal(f.redirects[0], '/library/login?next=%2Flibrary%2Freader%3Fdocument_id%3Dbook-a');
  assert.equal(f.buttons[0].disabled, true);
  assert.equal(f.localStorage.getItem(privateKey), null);
  assert.equal(f.sessionStorage.getItem(privateKey), null);
  assert.equal(f.localStorage.getItem(otherAccountKey), 'other account');
}

test('matching user and session validate without expiring private state', async () => {
  const f = fixture();
  assert.equal(await f.account.validateSession(), true);
  assert.equal(f.calls[0].url, '/library/api/account');
  assert.equal(f.calls[0].options.credentials, 'same-origin');
  assert.equal(f.calls[0].options.cache, 'no-store');
  assertStillActive(f);
});

for (const kind of ['network failure', 'HTTP 503']) {
  test(`${kind} rejects without expiring the reader and permits a later check`, async () => {
    let attempts = 0;
    const f = fixture(async () => {
      if (++attempts > 1) return response();
      if (kind === 'network failure') throw new Error('offline');
      return response({}, 503);
    });
    await assert.rejects(f.account.validateSession(), kind === 'network failure' ? /offline/ : /暂时无法确认会话/);
    assertStillActive(f);
    assert.equal(await f.account.validateSession(), true);
    assert.equal(f.calls.length, 2);
    assertStillActive(f);
  });
}

for (const status of [401, 403]) {
  test(`HTTP ${status} expires the session, notifies readers and prevents later requests`, async () => {
    const f = fixture(async () => response({}, status));
    assert.equal(await f.account.validateSession(), false);
    assertExpired(f, 'logout');
    if (status === 401) {
      assert.equal(f.notifications.length, 1);
      assert.equal(f.notifications[0].type, 'logout');
    }
    assert.equal(await f.account.validateSession(), false);
    assert.equal(f.calls.length, 1);
    assert.equal(f.events.length, 1);
  });
}

for (const [name, latest] of [
  ['different user', { user: { id: 'account-b' }, sessionId: 'session-a' }],
  ['different session', { user: { id: 'account-a' }, sessionId: 'session-b' }],
]) {
  test(`${name} expires cached identity and notifies the reader`, async () => {
    const f = fixture(async () => response(latest));
    assert.equal(await f.account.validateSession(), false);
    assertExpired(f, 'session-changed');
    assert.equal(await f.account.validateSession(), false);
    assert.equal(f.calls.length, 1);
  });
}

test('concurrent checks share one pending request and later checks are fresh', async () => {
  let release, attempts = 0;
  const f = fixture(() => ++attempts === 1
    ? new Promise(resolve => { release = resolve; })
    : Promise.resolve(response()));
  const first = f.account.validateSession();
  const second = f.account.validateSession();
  assert.equal(first, second);
  assert.equal(f.calls.length, 1);
  release(response());
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(await f.account.validateSession(), true);
  assert.equal(f.calls.length, 2);
  assertStillActive(f);
});
