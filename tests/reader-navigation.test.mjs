import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../static/reader/navigation.js', import.meta.url), 'utf8');
const { ReaderNavigation, findTextMatches, buildEpubOutline, extractTxtOutline, buildTextIndex } =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const textNode = (nodeValue) => ({ nodeType: 3, nodeValue });
const element = (tagName, ...childNodes) => ({ nodeType: 1, tagName, childNodes });
const doc = (...children) => ({ body: element('BODY', ...children) });
const defer = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test('literal matches retain source offsets across Unicode and punctuation', () => {
  const text = '😀中文 a+b? 中文 A+B?';
  assert.deepEqual(findTextMatches(text, 'a+b?').map((match) => [match.textOffset, match.matchLength]), [[5, 4], [13, 4]]);
  assert.equal(findTextMatches(text, '中文').length, 2);
  assert.deepEqual(findTextMatches(text, '  '), []);
});

test('text index excludes scripts/styles and spans inline markup with stable BR offsets', () => {
  const document = doc(element('STYLE', textNode('hidden target')), element('P', textNode('read '), element('EM', textNode('this')), textNode(' book')),
    element('P', textNode('next'), element('BR'), textNode('line')));
  const indexed = buildTextIndex(document);
  assert.equal(indexed.text, 'read this book\nnext\nline\n');
  assert.equal(findTextMatches(indexed.text, 'this book')[0].textOffset, 5);
  assert.equal(indexed.segments.at(-1).start, indexed.text.indexOf('line'));
});

test('EPUB TOC preserves nested fragment targets and skips unresolvable links', () => {
  const chapters = [{ href: 'Text/a.xhtml', label: 'A' }, { href: 'Text/b.xhtml', label: 'B' }];
  const outline = buildEpubOutline([{ label: 'Part', subitems: [{ label: 'Heading', href: 'Text/b.xhtml#%E7%AB%A0' }] },
    { label: 'Missing', href: 'missing.xhtml' }], chapters);
  assert.deepEqual(outline, [{ kind: 'epub', index: 1, href: 'Text/b.xhtml#%E7%AB%A0', fragment: '章', label: 'Heading', depth: 1 }]);
  assert.equal(buildEpubOutline([], chapters).length, 2);
});

test('TXT chapter detection supports Chinese/English and rejects heading words in prose', () => {
  const outline = extractTxtOutline('序言\n正文中的第一章不算标题。\n\n 第十二章 夜航\nChapter 12 — Home\n后记\n', 4);
  assert.deepEqual(outline.map((item) => item.label), ['序言', '第十二章 夜航', 'Chapter 12 — Home', '后记']);
  assert.equal(outline[1].index, 4);
  assert.equal(outline[1].textOffset, 18);
});

test('TXT search requests every source chunk including unmounted chunks', async () => {
  const requested = [], values = ['nothing here', 'second has needle', 'third NEEDLE'];
  const context = { format: 'txt', txt: { contentUrl: '/chunks', manifest: { chunks: [{}, {}, {}] } },
    fetchJson: async (url) => { requested.push(url); return { text: values[Number(url.split('/').at(-1))] }; } };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null });
  const results = await navigation.search('needle');
  assert.deepEqual(requested, ['/chunks/0', '/chunks/1', '/chunks/2']);
  assert.deepEqual(results.map((item) => [item.kind, item.index, item.textOffset]), [['txt', 1, 11], ['txt', 2, 6]]);
});

test('EPUB search fetches chapter URLs rather than depending on mounted chapter documents', async () => {
  const requested = [];
  const context = { format: 'epub', epub: { chapters: [{ url: '/first', label: 'One' }, { text: { load: async () => '/second' }, label: 'Two' }] },
    fetchText: async (url) => { requested.push(url); return url === '/first' ? 'unrelated' : 'target in chapter two'; } };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null, parseDocument: (text) => doc(textNode(text)) });
  const results = await navigation.search('target');
  assert.deepEqual(requested, ['/first', '/second']);
  assert.equal(results[0].index, 1);
  assert.equal(results[0].label, 'Two');
});

test('new searches supersede an in-flight search without publishing stale results', async () => {
  const first = defer(); let call = 0;
  const context = { format: 'txt', txt: { contentUrl: '/chunks', manifest: { chunks: [{}] } },
    fetchJson: async () => { call += 1; return call === 1 ? first.promise : { text: 'new term' }; } };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null });
  const stale = navigation.search('old');
  await navigation.search('new');
  first.resolve({ text: 'old term' }); await stale;
  assert.equal(navigation.results.length, 1);
  assert.equal(navigation.results[0].query, 'new');
});

test('TXT refresh returns before background outline download and retains a usable initial outline', async () => {
  const pending = defer();
  const context = { format: 'txt', txt: { contentUrl: '/chunks', manifest: { chunks: [{}, {}] } }, fetchJson: () => pending.promise };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null });
  const outline = await navigation.refresh();
  assert.deepEqual(outline.map((item) => item.label), ['文本区段 1', '文本区段 2']);
  navigation.generation += 1;
  pending.resolve({ text: '第一章\ncontent' });
});

test('temporary search navigation and keep/return callbacks remain distinct from ordinary TOC navigation', async () => {
  const calls = [];
  const navigation = new ReaderNavigation({ document: null, onNavigate: (target, options) => calls.push(options),
    onReturn: () => calls.push('return'), onKeep: () => calls.push('keep') });
  await navigation.navigate({ index: 1 }); assert.equal(navigation.canReturn, false);
  await navigation.navigate({ index: 2 }, { temporary: true }); assert.equal(navigation.canReturn, true);
  await navigation.returnToReading(); assert.equal(navigation.canReturn, false);
  await navigation.navigate({ index: 3 }, { temporary: true }); await navigation.keepReadingHere();
  assert.deepEqual(calls, [{ temporary: false }, { temporary: true }, 'return', { temporary: true }, 'keep']);
});

test('reset aborts source requests and prevents late results or account data from reappearing', async () => {
  let signal;
  const context = { format: 'txt', txt: { contentUrl: '/chunks', manifest: { chunks: [{}] } },
    fetchJson: async (url, options) => { signal = options.signal; return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }); } };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null });
  navigation.canReturn = true; navigation.textCache.set('private', 'account content');
  const pending = navigation.search('secret');
  navigation.reset(); await pending;
  assert.equal(signal.aborted, true);
  assert.deepEqual(navigation.results, []); assert.deepEqual(navigation.outline, []);
  assert.equal(navigation.textCache.size, 0); assert.equal(navigation.canReturn, false);
  assert.deepEqual(await navigation.search('secret'), []);
});

test('manifest TXT outline, including an empty outline, never downloads source chunks on open', async () => {
  let requests = 0;
  const context = { format: 'txt', txt: { contentUrl: '/chunks', manifest: { chunks: [{}, {}],
    toc: [{ index: 1, label: '第二章', textOffset: 12, query: '第二章', matchLength: 3 }] } },
    fetchJson: async () => { requests += 1; return { text: 'ignored' }; } };
  const navigation = new ReaderNavigation({ getContext: () => context, document: null });
  assert.deepEqual((await navigation.refresh()).map(item => [item.kind, item.label, item.index]), [['txt', '第二章', 1]]);
  context.txt.manifest.toc = [];
  assert.equal((await navigation.refresh()).length, 2);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(requests, 0);
});

test('TXT paragraphs preserve leading and consecutive empty lines and UTF16 search offsets', () => {
  const source = '\n甲😀\n\n\nneedle\n';
  const lines = source.split('\n').map((value, index, values) => {
    const line = element('P', ...(value ? [textNode(value)] : []));
    line.hasAttribute = (name) => name === 'data-reader-txt-line';
    line.getAttribute = (name) => name === 'data-reader-line-break' && index < values.length - 1 ? 'yes' : null;
    return line;
  });
  const indexed = buildTextIndex(doc(...lines));
  assert.equal(indexed.text, source);
  assert.equal(findTextMatches(indexed.text, 'needle')[0].textOffset, source.indexOf('needle'));
  assert.equal(findTextMatches(indexed.text, '😀')[0].matchLength, 2);
});

test('a slower earlier navigation cannot overwrite the current section or temporary state', async () => {
  let rejectFirst;
  const first = new Promise((resolve, reject) => { rejectFirst = reject; });
  const navigation = new ReaderNavigation({ document: null, onNavigate: (target) => target.index === 1 ? first : undefined });
  const stale = navigation.navigate({ index: 1 }, { temporary: true });
  await navigation.navigate({ index: 2 }, { temporary: true });
  rejectFirst(new Error('obsolete request failed')); await stale;
  assert.equal(navigation.activeIndex, 2);
  assert.equal(navigation.canReturn, true);
});

test('a delayed return cannot clear a newer temporary jump and reset cancels late navigation', async () => {
  const returning = defer(), navigating = defer();
  const navigation = new ReaderNavigation({ document: null, onReturn: () => returning.promise,
    onNavigate: (target) => target.index === 9 ? navigating.promise : undefined });
  const staleReturn = navigation.returnToReading();
  await navigation.navigate({ index: 2 }, { temporary: true });
  returning.resolve(); await staleReturn;
  assert.equal(navigation.canReturn, true);
  const staleNavigation = navigation.navigate({ index: 9 }, { temporary: true });
  navigation.reset(); navigating.resolve(); await staleNavigation;
  assert.equal(navigation.activeIndex, -1);
  assert.equal(navigation.canReturn, false);
});
