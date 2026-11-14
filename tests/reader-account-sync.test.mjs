import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../static/reader/account-sync.js', import.meta.url), 'utf8');
const { ReaderAccountSync, sectionReadingProgress } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const settings = { progress_sync_enabled: true, progress_epoch: 3, reading_epoch: 4 };
const account = { userId: 'account-a', sessionId: 'session-a', csrf: 'csrf-a', settings };
const item = { documentId: 'book-a', openHandle: 'bound-handle', contentVersion: 'v1', format: 'txt' };
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const row = (revision = 7, position = { chunkIndex: 4, chunkProgressRatio: 0.3 }) => ({ position, revision, settings, content_version: 'v1' });

function fixture(handler) {
  const calls = [], statuses = [], timers = [];
  let expired = 0;
  const sync = new ReaderAccountSync({ account: { ...account }, apiBase: '/prefix/api/reader',
    onStatus: (status) => statuses.push(status), onExpired: () => { expired += 1; },
    setTimer: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timer.cleared = true; },
    fetchImpl: async (url, options) => {
      const call = { url, ...options, data: options.body ? JSON.parse(options.body) : undefined };
      calls.push(call);
      if (handler) return handler(call, calls);
      if (options.method === 'GET') return response(row());
      if (url.endsWith('/open')) return response({ progress: row(), settings });
      return response(row(call.data.revision + 1, call.data.position));
    },
  });
  return { sync, calls, statuses, timers, expired: () => expired };
}

test('section progress includes position within a single chunk or chapter', () => {
  assert.equal(sectionReadingProgress(0, 0, 1), 0);
  assert.equal(sectionReadingProgress(0, 0.5, 1), 0.5);
  assert.equal(sectionReadingProgress(0, 1, 1), 1);
  assert.equal(sectionReadingProgress(1, 0, 2), 0.5);
  assert.equal(sectionReadingProgress(1, 0.4, 2), 0.7);
  assert.equal(sectionReadingProgress(2, 0, 3), 2 / 3);
  assert.equal(sectionReadingProgress(2, 0.7, 3, true), 1);
  assert.equal(sectionReadingProgress(0, 0, 0), 0);
});

test('loads account progress before restoration and never sends initial 0% events', async () => {
  const { sync, calls, timers } = fixture();
  assert.deepEqual(await sync.prepare(item), row().position);
  sync.capture(item, { chunkIndex: 0 });
  await sync.flush(item);
  assert.equal(calls.length, 1);
  assert.equal(timers.length, 0);
  await sync.rendered(item, 'stable-folder');
  assert.equal(calls[1].data.folderKey, 'stable-folder');
  sync.capture(item, { chunkIndex: 5, chunkProgressRatio: 0.1 });
  sync.capture(item, { chunkIndex: 6, chunkProgressRatio: 0.2 });
  assert.equal(timers.length, 1, 'changes are coalesced into one six-second write');
  assert.equal(timers[0].delay, 6000);
  await sync.flush(item);
  assert.equal(calls[2].data.position.chunkIndex, 6);
  assert.equal(calls[2].data.revision, 7);
  assert.equal(calls[2].data.epoch, 3);
  assert.equal(calls[2].headers['X-CSRF-Token'], account.csrf);
  assert.equal(calls[2].credentials, 'same-origin');
});

test('metadata preparation never records an open; explicit reopen updates folder receipt', async () => {
  const { sync, calls } = fixture();
  await sync.prepare(item);
  assert.equal(calls.filter((call) => call.url.endsWith('/open')).length, 0);
  await sync.rendered(item, 'folder-1');
  await sync.prepare(item);
  await sync.rendered(item, 'folder-1');
  assert.equal(calls.filter((call) => call.url.endsWith('/open')).length, 2);
});

test('a conflict pauses writes until the user explicitly takes over the latest revision', async () => {
  let writes = 0, reads = 0;
  const { sync, calls, statuses } = fixture((call) => {
    if (call.method === 'GET') return response(row(++reads === 1 ? 7 : 9));
    if (call.url.endsWith('/open')) return response({ progress: row(), settings });
    if (++writes === 1) return response({ code: 'conflict', current: row(9), error: 'changed' }, 409);
    return response(row(call.data.revision + 1, call.data.position));
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 8 });
  await sync.flush(item);
  assert.equal(statuses.at(-1).type, 'conflict');
  sync.capture(item, { chunkIndex: 2 }); // Reading backwards is a valid choice.
  await sync.flush(item);
  assert.equal(writes, 1);
  await sync.resolve(true);
  assert.equal(writes, 2);
  assert.equal(calls.at(-1).data.revision, 9);
  assert.equal(calls.at(-1).data.position.chunkIndex, 2);
});

test('adopting saved progress keeps capture blocked until restoration completes', async () => {
  let reads = 0;
  const { sync, calls } = fixture((call) => {
    if (call.method === 'GET') return response(row(++reads === 1 ? 7 : 12, { chunkIndex: 10 }));
    if (call.url.endsWith('/open')) return response({ progress: row(8), settings });
    throw new Error('Must not write while restoring');
  });
  await sync.prepare(item);
  await sync.rendered(item);
  assert.deepEqual(await sync.resolve(false), { chunkIndex: 10 });
  sync.capture(item, { chunkIndex: 0 });
  await sync.flush(item);
  assert.equal(calls.filter((call) => call.url.includes('/progress/') && call.method === 'POST').length, 0);
});

test('cleared epochs stop old writes and cannot be silently refreshed', async () => {
  const { sync, calls, statuses } = fixture((call) => {
    if (call.method === 'GET') return response(row());
    if (call.url.endsWith('/open')) return response({ settings, progress: row() });
    return response({ code: 'stale_epoch', error: 'cleared' }, 409);
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 9 });
  await sync.flush(item);
  assert.equal(statuses.at(-1).type, 'stale');
  const count = calls.length;
  await sync.resolve(true);
  sync.capture(item, { chunkIndex: 11 });
  await sync.flush(item);
  assert.equal(calls.length, count);
  assert.equal(sync.active.latest, null);
});

test('a content change during reading requires new metadata and cannot take over the old handle', async () => {
  const { sync, calls, statuses } = fixture((call) => {
    if (call.method === 'GET') return response(row());
    if (call.url.endsWith('/open')) return response({ progress: row(), settings });
    return response({ code: 'content_changed', error: 'changed' }, 409);
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 8 });
  await sync.flush(item);
  assert.equal(statuses.at(-1).type, 'stale');
  assert.match(statuses.at(-1).message, /文档内容已变化/);
  const count = calls.length;
  await sync.resolve(true);
  sync.capture(item, { chunkIndex: 9 });
  await sync.flush(item);
  assert.equal(calls.length, count);
});

test('explicit reopen refreshes server progress but cannot refresh a deleted epoch', async () => {
  let reads = 0;
  const { sync, calls, statuses } = fixture((call) => {
    if (call.method === 'GET') {
      reads += 1;
      return response({ ...row(reads === 1 ? 7 : 8, { chunkIndex: reads + 3 }),
        settings: reads < 3 ? settings : { ...settings, progress_epoch: 4 } });
    }
    return response({ settings, progress: row() });
  });
  await sync.prepare(item);
  assert.deepEqual(await sync.prepare(item), { chunkIndex: 5 });
  assert.equal(sync.active.revision, 8);
  assert.equal(await sync.prepare(item), null);
  assert.equal(statuses.at(-1).type, 'stale');
  assert.equal(sync.active.epoch, 3);
  const count = calls.length;
  await sync.prepare(item);
  assert.equal(calls.length, count);
});

test('invalid or oversized position responses stop automatic retries', async () => {
  const { sync, calls, statuses } = fixture((call) => {
    if (call.method === 'GET') return response(row());
    if (call.url.endsWith('/open')) return response({ settings, progress: row() });
    return response({ error: 'Too large' }, 413);
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 2 });
  await sync.flush(item);
  assert.match(statuses.at(-1).message, /过大/);
  const count = calls.length;
  await sync.flush(item);
  assert.equal(calls.length, count);
});

test('failed restoration fetch allows reading but cannot overwrite existing progress', async () => {
  const { sync, calls } = fixture((call) => call.method === 'GET'
    ? response({ error: 'temporarily unavailable' }, 503) : response({ settings, progress: row() }));
  assert.equal(await sync.prepare(item), null);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 0 });
  await sync.flush(item);
  assert.equal(calls.filter((call) => call.method === 'POST' && !call.url.endsWith('/open')).length, 0);
});

test('changed document versions preserve saved progress until an explicit decision', async () => {
  const { sync, calls, statuses } = fixture();
  const changedItem = { ...item, contentVersion: 'v2' };
  assert.equal(await sync.prepare(changedItem), null);
  assert.equal(sync.position(changedItem), null);
  assert.equal(statuses.at(-1).type, 'version-changed');
  await sync.rendered(changedItem);
  sync.capture(changedItem, { chunkIndex: 0 });
  await sync.flush(changedItem);
  assert.equal(calls.filter((call) => call.url.includes('/progress/') && call.method === 'POST').length, 0);
});

test('HTTP 401 discards account state and prevents every later write', async () => {
  const { sync, calls, expired } = fixture(() => response({ error: 'expired' }, 401));
  await sync.prepare(item);
  assert.equal(expired(), 1);
  assert.equal(sync.stopped, true);
  assert.equal(sync.contexts.size, 0);
  sync.capture(item, { chunkIndex: 1 });
  await sync.flush(item);
  assert.equal(calls.length, 1);
});

test('serialized writes advance revisions and retain a newer in-flight position', async () => {
  let release;
  const { sync, calls } = fixture(async (call) => {
    if (call.method === 'GET') return response(row());
    if (call.url.endsWith('/open')) return response({ settings, progress: row() });
    if (call.data.revision === 7) await new Promise((resolve) => { release = resolve; });
    return response(row(call.data.revision + 1, call.data.position));
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 5 });
  const first = sync.flush(item);
  await Promise.resolve();
  sync.capture(item, { chunkIndex: 6 });
  release();
  await first;
  await sync.flush(item);
  const writes = calls.filter((call) => call.method === 'POST' && call.url.includes('/progress/'));
  assert.deepEqual(writes.map((call) => call.data.revision), [7, 8]);
  assert.deepEqual(writes.map((call) => call.data.writer_seq), [1, 2]);
  assert.equal(writes[1].data.position.chunkIndex, 6);
});

test('a lost committed response retries the identical write before sending a newer position', async () => {
  let writes = 0, committed = row(), firstBody;
  const { sync, calls } = fixture((call) => {
    if (call.method === 'GET') return response(committed);
    if (call.url.endsWith('/open')) return response({ settings, progress: committed });
    writes += 1;
    if (writes === 1) {
      firstBody = call.data;
      committed = { ...row(8, call.data.position), writer_id: call.data.writer_id, writer_seq: call.data.writer_seq };
      throw new TypeError('The server committed, but the response connection was lost');
    }
    if (writes === 2) {
      assert.deepEqual(call.data, firstBody, 'the server can match its idempotent replay branch');
      return response(committed);
    }
    assert.equal(call.data.revision, committed.revision);
    committed = { ...row(committed.revision + 1, call.data.position), writer_id: call.data.writer_id, writer_seq: call.data.writer_seq };
    return response(committed);
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 5 });
  await sync.flush(item);
  assert.equal(sync.active.sequence, 0, 'an unacknowledged sequence is not committed locally');
  sync.capture(item, { chunkIndex: 6 });
  await sync.flush(item);
  const attempts = calls.filter((call) => call.method === 'POST' && call.url.includes('/progress/'));
  assert.deepEqual(attempts.map((call) => call.data.writer_seq), [1, 1, 2]);
  assert.deepEqual(attempts.map((call) => call.data.position.chunkIndex), [5, 5, 6]);
  assert.equal(sync.active.sequence, 2);
  assert.equal(sync.active.revision, 9);
  assert.equal(sync.active.dirty, false);
});

test('returning to the old saved position still acknowledges a lost write and then saves the return', async () => {
  let writes = 0;
  const original = row().position;
  const { sync, calls } = fixture((call) => {
    if (call.method === 'GET') return response(row());
    if (call.url.endsWith('/open')) return response({ settings, progress: row() });
    writes += 1;
    if (writes === 1) throw new TypeError('lost response after commit');
    return response(row(writes === 2 ? 8 : 9, call.data.position));
  });
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chunkIndex: 8 });
  await sync.flush(item);
  sync.capture(item, original);
  assert.equal(sync.active.dirty, false);
  await sync.flush(item);
  const attempts = calls.filter((call) => call.method === 'POST' && call.url.includes('/progress/'));
  assert.equal(attempts.length, 3);
  assert.deepEqual(attempts[2].data.position, original);
  assert.equal(attempts[2].data.revision, 8);
});

test('native EPUB/Kookit percentages are converted to the server percent scale', async () => {
  const { sync, calls } = fixture();
  await sync.prepare(item);
  await sync.rendered(item);
  sync.capture(item, { chapterDocIndex: 2, percentage: '0.35', intraChapterOffset: 125 });
  await sync.flush(item);
  assert.equal(calls.at(-1).data.progress, 35);
  assert.equal(calls.at(-1).data.position.intraChapterOffset, 125);
});

test('reader overrides vendor mobile script defaults before rendering and ignores legacy global storage', async () => {
  const app = await readFile(new URL('../static/reader/app.js', import.meta.url), 'utf8');
  const create = app.indexOf('window.BookHelper.getRendition(buffer');
  const disable = app.indexOf("rendition.isAllowScript = 'no'", create);
  const render = app.indexOf('await rendition.renderTo(stage)', create);
  assert.ok(create >= 0 && disable > create && render > disable);
  assert.equal(app.includes('localStorage'), false);
  // Native CHM scripts use an opaque frame; converted vendor documents still
  // keep scripts disabled and no frame combines scripts with same-origin.
  assert.match(app, /setAttribute\('sandbox', manifest\.renderBaseUrl \? 'allow-scripts' : 'allow-same-origin'\)/);
  assert.doesNotMatch(app, /allow-scripts allow-same-origin|allow-same-origin allow-scripts/);
  assert.match(app, /import\('\.\/kookit\.bundle\.js'\)/);
});
