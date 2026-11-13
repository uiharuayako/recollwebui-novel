import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../static/reader/pdf-adapter.js', import.meta.url), 'utf8');
const { PdfRendition } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function positioned() {
  const rendition = new PdfRendition({ numPages: 3 });
  const stage = { scrollTop: 1250, clientTop: 0, clientHeight: 500, scrollHeight: 3060,
    getBoundingClientRect: () => ({ top: 100 }) };
  const pages = [0, 1020, 2040].map((top) => ({ div: { clientHeight: 1000,
    getBoundingClientRect: () => ({ top: 100 + top - stage.scrollTop }) } }));
  rendition.stage = stage;
  rendition.viewer = { pagesCount: 3, currentScaleValue: 'page-width', pagesRotation: 0,
    getPageView: (index) => pages[index] };
  return { rendition, stage };
}

test('continuous PDF location follows the page at the viewport top, including page gaps', () => {
  const { rendition, stage } = positioned();
  assert.equal(rendition.getPosition().pageNumber, 2);
  assert.equal(rendition.getPosition().pageOffsetRatio, .23);
  assert.equal(Number(rendition.getPosition().percentage), 1.23 / 3);
  stage.scrollTop = 1010;
  assert.equal(rendition.getPosition().pageNumber, 1);
  assert.equal(rendition.getPosition().pageOffsetRatio, 1);
  stage.scrollTop = 2560;
  assert.equal(rendition.getPosition().percentage, '1');
});

test('a short final PDF page reaches complete without requiring positive scroll', () => {
  const rendition = new PdfRendition({ numPages: 1 });
  rendition.stage = { scrollTop: 0, clientHeight: 700, scrollHeight: 700, getBoundingClientRect: () => ({ top: 0 }) };
  rendition.viewer = { pagesCount: 1, currentScaleValue: 'page-width', pagesRotation: 0,
    getPageView: () => ({ div: { clientHeight: 400, getBoundingClientRect: () => ({ top: 0 }) } }) };
  assert.equal(rendition.getPosition().percentage, '1');
});

test('saved positions accept legacy page indices and constrain malformed offsets, scale, and rotation', () => {
  const rendition = new PdfRendition({ numPages: 3 });
  assert.deepEqual(rendition.normalizePosition({ chapterDocIndex: 1, pageOffsetRatio: .3 }), {
    pageNumber: 2, pageOffsetRatio: .3, scaleValue: 'page-width', rotation: 0,
  });
  assert.deepEqual(rendition.normalizePosition({ pageNumber: 99, pageOffsetRatio: -4, scaleValue: 25, rotation: -90 }), {
    pageNumber: 3, pageOffsetRatio: 0, scaleValue: 5, rotation: 270,
  });
});

test('outline resolves named and object destinations while preserving valid children of invalid parents', async () => {
  const rendition = new PdfRendition({ numPages: 4,
    getOutline: async () => [{ title: 'Part one', dest: 'intro', items: [
      { title: 'Broken', dest: 'missing', items: [{ title: 'Chapter', dest: [{ num: 20 }, { name: 'Fit' }] }] },
    ] }],
    getDestination: async (name) => name === 'intro' ? [0, { name: 'Fit' }] : null,
    getPageIndex: async () => 2,
  });
  const outline = await rendition.getOutline();
  assert.deepEqual(outline.map(({ label, pageNumber, depth }) => ({ label, pageNumber, depth })), [
    { label: 'Part one', pageNumber: 1, depth: 0 }, { label: 'Chapter', pageNumber: 3, depth: 2 },
  ]);
});

class EventBus {
  listeners = new Map();
  on(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(fn); }
  off(name, fn) { this.listeners.get(name)?.delete(fn); }
  dispatch(name, event) { for (const fn of this.listeners.get(name) || []) fn(event); }
}

test('new searches cancel old result promises and repeated pending queries share their results', async () => {
  const rendition = new PdfRendition({ numPages: 2 });
  rendition.eventBus = new EventBus();
  rendition.findController = { pageMatches: [[4], [8]], pageMatchesLength: [[5], [5]] };
  const first = rendition.search('first');
  const second = rendition.search('second');
  const repeated = rendition.search('second');
  assert.deepEqual(await first, []);
  rendition.searchExcerpts.set(0, ['context second context']);
  rendition.eventBus.dispatch('updatefindmatchescount', { source: rendition.findController });
  assert.deepEqual(await repeated, await second);
  assert.equal((await second)[0].query, 'second');
  assert.equal((await second)[0].excerpt, 'context second context');
  assert.equal((await second)[1].pageNumber, 2);
  assert.deepEqual(await rendition.search(''), []);
});

test('superseded navigation stops waiting for a canvas that has left the rendering queue', async () => {
  const { rendition, stage } = positioned();
  rendition.eventBus = new EventBus();
  rendition.components = { RenderingStates: { FINISHED: 3 } };
  rendition.viewer.update = () => {};
  rendition.ensurePage = async (number) => ({ renderingState: number === 3 ? 3 : 1,
    div: { clientHeight: 1000, getBoundingClientRect: () => ({ top: 100 + (number - 1) * 1020 - stage.scrollTop }) } });
  const first = rendition.showPage(2);
  await Promise.resolve();
  await Promise.resolve();
  const second = rendition.showPage(3);
  await Promise.all([first, second]);
  assert.equal(rendition.isRendering, false);
  assert.equal(rendition.viewer.currentPageNumber, 3);
});

test('closing a PDF cancels pending search and releases account document references', async () => {
  let destroyed = false;
  const rendition = new PdfRendition({ numPages: 2 }, { loadingTask: { destroy: async () => { destroyed = true; } } });
  rendition.eventBus = new EventBus();
  rendition.findController = { pageMatches: [], pageMatchesLength: [] };
  rendition.viewer = { setDocument(document) { assert.equal(document, null); } };
  rendition.linkService = { setDocument(document) { assert.equal(document, null); } };
  rendition.searchExcerpts.set(0, ['private text']);
  const pending = rendition.search('needle');
  rendition.removeContent();
  assert.deepEqual(await pending, []);
  await Promise.resolve();
  assert.equal(destroyed, true);
  for (const key of ['pdf', 'viewer', 'findController', 'loadingTask', 'eventBus', 'searchPromise']) {
    assert.equal(rendition[key], null);
  }
  assert.equal(rendition.searchExcerpts.size, 0);
  assert.deepEqual(rendition.searchResults, []);
});
