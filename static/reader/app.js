const query = new URLSearchParams(window.location.search);
document.documentElement.classList.add('reader-page-root');
document.body.classList.add('reader-page');
const readerConfig = window.RECOLL_READER || {};
const mode = readerConfig.mode || query.get('mode') || 'book';
const resnum = query.get('resnum') || '0';
const rawQueryString = (readerConfig.queryString || window.location.search.replace(/^\?/, '')).split('&amp;').join('&');
const searchParams = new URLSearchParams(rawQueryString);
searchParams.set('resnum', resnum);
searchParams.set('mode', mode);
searchParams.delete('page');
const baseApi = '/api/reader';
const FONT_SIZE_STORAGE_KEY = 'recoll-reader:fontSize';
const DEFAULT_FONT_SIZE = 20;
const MIN_FONT_SIZE = 16;
const MAX_FONT_SIZE = 32;
const FONT_SIZE_STEP = 2;
const SCROLL_READER_FORMATS = new Set(['txt', 'md', 'html', 'htm', 'xhtml', 'xml', 'mhtml', 'epub']);
const EPUB_MOUNT_WINDOW_BEFORE = 1;
const EPUB_MOUNT_WINDOW_AFTER = 1;
const EPUB_PRELOAD_WINDOW_BEFORE = 2;
const EPUB_PRELOAD_WINDOW_AFTER = 2;
const readStoredFontSize = () => {
  try {
    const raw = localStorage.getItem(FONT_SIZE_STORAGE_KEY);
    const parsed = raw == null ? NaN : Number(raw);
    if (!Number.isFinite(parsed)) return DEFAULT_FONT_SIZE;
    return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(parsed)));
  } catch (err) {
    return DEFAULT_FONT_SIZE;
  }
};
const state = {
  items: [],
  currentIndex: 0,
  current: null,
  rendition: null,
  currentChmManifest: null,
  currentChmHref: '',
  currentChmRestore: null,
  folderMeta: null,
  bookMeta: null,
  currentBlobUrl: '',
  parserRegex: localStorage.getItem('recoll-reader:parserRegex') || '',
  fontSize: readStoredFontSize(),
  isOpening: false,
  layoutSyncTimer: 0,
  layoutSyncInFlight: false,
  layoutSyncQueued: false,
  lastStageWidth: 0,
  lastStageHeight: 0,
  scrollHeightTimer: 0,
  openToken: 0,
  sidebarInitialized: false,
  readerMode: '',
  mobileFlag: 'no',
  viewportSyncTimer: 0,
  pendingRestorePosition: null,
  epubWindow: {
    syncTimer: 0,
    syncToken: 0,
    currentIndex: -1,
    mountedRange: null,
    preloadRange: null,
    loaded: new Set(),
    loading: new Map(),
    mounted: new Map(),
    slots: [],
    slotHeights: new Map(),
    averageHeightPerSize: 0.34,
    restorePosition: null,
    progressBadge: null,
    bootstrapHost: null,
  },
};

window.Kookit = window.Kookit || {};

// Kookit emits mobile bridge messages in several interaction paths.
// On desktop browsers there is no native bridge, so provide a safe no-op.
if (!window.ReactNativeWebView || typeof window.ReactNativeWebView.postMessage !== 'function') {
  window.ReactNativeWebView = {
    postMessage() {},
  };
}

const el = (id) => document.getElementById(id);
const stage = el('page-area');
const booklist = el('reader-booklist');
const titleEl = el('reader-title');
const metaEl = el('reader-meta');
const sidebar = el('reader-sidebar');
const sidebarBackdrop = el('reader-sidebar-backdrop');
const readerApp = el('reader-app');
const toolbar = el('reader-toolbar');
const toolbarToggleButton = el('reader-toggle-toolbar');
const toolbarPeekButton = el('reader-show-toolbar');
const stageShell = el('reader-stage-shell');
const fontSizeLabel = el('reader-font-size');
const fontDecreaseButton = el('reader-font-decrease');
const fontIncreaseButton = el('reader-font-increase');
const parserInput = el('reader-parser-regex');
const prevPageButton = el('reader-prev-page');
const nextPageButton = el('reader-next-page');
const sidebarHeader = sidebar ? sidebar.querySelector('.reader-sidebar-header') : null;
parserInput.value = state.parserRegex;

const safeText = (value) => (value == null ? '' : String(value));
const escapeHtml = (value) => safeText(value)
  .split('&').join('&amp;')
  .split('<').join('&lt;')
  .split('>').join('&gt;')
  .split('"').join('&quot;');
const normalizeFormat = (format) => safeText(format).trim().toLowerCase();
const isChmFormat = (format) => normalizeFormat(format) === 'chm';
const isEpubFormat = (format) => normalizeFormat(format) === 'epub';
const isScrollReaderFormat = (format) => SCROLL_READER_FORMATS.has(normalizeFormat(format));
const getReaderModeForFormat = (format) => (isScrollReaderFormat(format) || isChmFormat(format) ? 'scroll' : 'single');
const isPagedReaderMode = (readerMode) => readerMode === 'single' || readerMode === 'double';
const hasSavedPositionData = (position) => !!(position && typeof position === 'object' && Object.keys(position).length > 0);
const isValidChmPosition = (position) => !!(position && typeof position.href === 'string' && position.href.length > 0);
const canRestoreSavedPosition = (item, position) => {
  if (!hasSavedPositionData(position)) return false;
  const format = normalizeFormat(item && item.format);
  if (isChmFormat(format)) return isValidChmPosition(position);
  if (format === 'txt') return isValidTxtPosition(position);
  if (isScrollReaderFormat(format)) return true;
  return true;
};

const isMobileViewport = () => window.matchMedia('(max-width: 700px)').matches;
const isTouchDevice = () => window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;
const getViewportReaderConfig = (item = state.current) => ({
  readerMode: getReaderModeForFormat(item && item.format),
  isMobile: isTouchDevice() ? 'yes' : 'no',
});
const getContentInset = () => {
  const safeLeft = window.CSS && typeof window.CSS.supports === 'function' && window.CSS.supports('padding-left: env(safe-area-inset-left)')
    ? 'env(safe-area-inset-left)'
    : '0px';
  const safeRight = window.CSS && typeof window.CSS.supports === 'function' && window.CSS.supports('padding-right: env(safe-area-inset-right)')
    ? 'env(safe-area-inset-right)'
    : '0px';
  return {
    left: `max(${isMobileViewport() ? 14 : 36}px, ${safeLeft})`,
    right: `max(${isMobileViewport() ? 14 : 36}px, ${safeRight})`,
  };
};
const isToolbarHidden = () => readerApp && readerApp.classList.contains('reader-toolbar-hidden');
const clampFontSize = (value) => Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(value)));
const syncReaderModeUi = () => {
  if (!readerApp) return;
  const paged = isPagedReaderMode(state.readerMode);
  readerApp.classList.toggle('reader-scroll-mode', !paged);
  readerApp.classList.toggle('reader-paged-mode', paged);
};

const syncScrollIframeHeight = (doc) => {
  if (!doc || !stage || !state.current || isChmFormat(state.current.format)) return;
  const iframe = getActiveContentFrame();
  if (!iframe || iframe.id !== 'kookit-iframe') return;
  const body = doc.body;
  const html = doc.documentElement;
  const contentHeight = Math.max(
    body ? body.scrollHeight : 0,
    body ? body.offsetHeight : 0,
    html ? html.scrollHeight : 0,
    html ? html.offsetHeight : 0,
  );
  if (!contentHeight) return;
  const nextHeight = Math.max(contentHeight + 96, stage.clientHeight);
  iframe.style.height = `${nextHeight}px`;
  iframe.style.minHeight = `${nextHeight}px`;
};

const resetEpubContextWindow = () => {
  window.clearTimeout(state.epubWindow.syncTimer);
  state.epubWindow.syncToken += 1;
  state.epubWindow.currentIndex = -1;
  state.epubWindow.mountedRange = null;
  state.epubWindow.preloadRange = null;
  state.epubWindow.loaded = new Set();
  state.epubWindow.loading = new Map();
  state.epubWindow.mounted = new Map();
  state.epubWindow.slots = [];
  state.epubWindow.slotHeights = new Map();
  state.epubWindow.restorePosition = null;
  state.epubWindow.progressBadge = null;
  if (state.epubWindow.bootstrapHost && state.epubWindow.bootstrapHost.parentNode) {
    state.epubWindow.bootstrapHost.parentNode.removeChild(state.epubWindow.bootstrapHost);
  }
  state.epubWindow.bootstrapHost = null;
};

const getEpubChapterDocList = () => {
  if (!state.rendition || !isEpubFormat(state.current && state.current.format)) return [];
  return Array.isArray(state.rendition.chapterDocList) ? state.rendition.chapterDocList : [];
};

const isEpubWindowedMode = () => !!(
  state.current
  && isEpubFormat(state.current.format)
  && state.readerMode === 'scroll'
  && state.epubWindow.slots.length
);

const decodeHtmlText = (value) => {
  const text = safeText(value);
  if (!text) return '';
  return new DOMParser().parseFromString(text, 'text/html').documentElement.textContent || text;
};

const flattenTocItems = (tocItems, output = []) => {
  if (!Array.isArray(tocItems)) return output;
  for (const item of tocItems) {
    if (!item) continue;
    output.push({
      label: decodeHtmlText(item.label) || '',
      href: safeText(item.href),
    });
    if (Array.isArray(item.subitems) && item.subitems.length) {
      flattenTocItems(item.subitems, output);
    }
  }
  return output;
};

const buildEpubChapterDocListFromBook = async (rendition) => {
  const book = rendition && rendition.book;
  const sections = Array.isArray(book && book.sections) ? book.sections : [];
  if (!sections.length) return [];
  const tocItems = flattenTocItems(Array.isArray(book && book.toc) ? book.toc : []);
  const tocIndexByHref = new Map();
  tocItems.forEach((item, index) => {
    if (item && item.href && !tocIndexByHref.has(item.href)) {
      tocIndexByHref.set(item.href, { ...item, index });
    }
  });
  const chapterDocList = sections.map((section, index) => {
    const tocItem = tocItems[index] || null;
    let label = decodeHtmlText(tocItem && tocItem.label);
    let href = safeText(tocItem && tocItem.href);
    if (!label && section && section.label) {
      label = decodeHtmlText(section.label);
    }
    if (!href && section && section.href) {
      href = safeText(section.href);
    }
    return {
      label: label || `第 ${index + 1} 章`,
      href: href || `title${index}`,
      text: section,
    };
  });
  chapterDocList.forEach((item, index) => {
    if (item.href && tocIndexByHref.has(item.href)) {
      const tocItem = tocIndexByHref.get(item.href);
      if (tocItem && tocItem.label) {
        item.label = decodeHtmlText(tocItem.label) || item.label;
      }
    }
    if (!item.label || !item.label.trim()) {
      item.label = `第 ${index + 1} 章`;
    }
  });
  return chapterDocList;
};

const getCurrentEpubChapterIndex = () => {
  if (!state.rendition || !isEpubFormat(state.current && state.current.format)) return -1;
  const position = typeof state.rendition.getPosition === 'function' ? state.rendition.getPosition() : null;
  const index = Number(position && position.chapterDocIndex);
  if (Number.isFinite(index) && index >= 0) {
    return index;
  }
  const tempIndex = Number(state.rendition.tempLocation && state.rendition.tempLocation.chapterDocIndex);
  return Number.isFinite(tempIndex) && tempIndex >= 0 ? tempIndex : -1;
};

const getEpubWindowRange = (index, total, radius) => {
  if (!Number.isFinite(index) || index < 0 || !total) {
    return { start: 0, end: -1 };
  }
  const start = Math.max(0, index - radius.before);
  const end = Math.min(total - 1, index + radius.after);
  return { start, end };
};

const getEpubMountedRange = (index, total) => getEpubWindowRange(index, total, {
  before: EPUB_MOUNT_WINDOW_BEFORE,
  after: EPUB_MOUNT_WINDOW_AFTER,
});

const getEpubPreloadRange = (index, total) => getEpubWindowRange(index, total, {
  before: EPUB_PRELOAD_WINDOW_BEFORE,
  after: EPUB_PRELOAD_WINDOW_AFTER,
});

const getEpubAnchorFromPosition = (position) => {
  if (!position || !state.rendition || !getEpubChapterDocList().length) return null;
  const chapterIndex = Number(position.chapterDocIndex);
  if (!Number.isFinite(chapterIndex) || chapterIndex < 0) return null;
  return {
    chapterDocIndex: chapterIndex,
    offset: Math.max(0, Number(position.intraChapterOffset ?? position.scrollTop ?? 0) || 0),
    chapterProgressRatio: Number.isFinite(Number(position.chapterProgressRatio)) ? Number(position.chapterProgressRatio) : null,
  };
};

const waitForCondition = async (check, { timeout = 15000, interval = 50 } = {}) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const result = check();
    if (result) return result;
    await new Promise((resolve) => window.setTimeout(resolve, interval));
  }
  throw new Error('等待阅读器初始化超时');
};

const waitForEpubBootstrap = async (rendition) => await waitForCondition(() => {
  if (!rendition) return null;
  if (Array.isArray(rendition.chapterDocList) && rendition.chapterDocList.length) {
    return rendition.chapterDocList;
  }
  const sections = rendition.book && Array.isArray(rendition.book.sections) ? rendition.book.sections : null;
  if (sections && sections.length) {
    return sections;
  }
  return null;
}, { timeout: 60000, interval: 100 });

const loadEpubChapterDoc = async (chapterDoc, index) => {
  if (!chapterDoc || !chapterDoc.text || typeof chapterDoc.text.load !== 'function') return;
  if (state.epubWindow.loaded.has(index) || state.epubWindow.loading.has(index)) return;
  const loadPromise = Promise.resolve().then(() => chapterDoc.text.load());
  state.epubWindow.loading.set(index, loadPromise);
  try {
    await loadPromise;
    state.epubWindow.loaded.add(index);
  } catch (err) {
    console.warn('epub chapter preload failed', index, err);
  } finally {
    state.epubWindow.loading.delete(index);
  }
};

const unloadEpubChapterDoc = async (chapterDoc, index) => {
  if (!chapterDoc || !chapterDoc.text || typeof chapterDoc.text.unload !== 'function') return;
  if (!state.epubWindow.loaded.has(index)) return;
  try {
    await chapterDoc.text.unload();
  } catch (err) {
    console.warn('epub chapter unload failed', index, err);
  } finally {
    state.epubWindow.loaded.delete(index);
  }
};

const getEpubProgressBadge = () => {
  if (state.epubWindow.progressBadge && stage && stage.contains(state.epubWindow.progressBadge)) {
    return state.epubWindow.progressBadge;
  }
  if (!stage) return null;
  let badge = stage.querySelector('.reader-progress');
  if (!badge) {
    badge = document.createElement('div');
    badge.className = 'reader-progress';
    badge.textContent = '0%';
    stage.appendChild(badge);
  }
  state.epubWindow.progressBadge = badge;
  return badge;
};

const estimateEpubSlotHeight = (chapterDoc, index) => {
  const cached = state.epubWindow.slotHeights.get(index);
  if (cached) return cached;
  const metrics = getReadingStyleMetrics();
  const size = Number(chapterDoc && chapterDoc.text && chapterDoc.text.size) || 1200;
  const fontScale = Math.max(0.8, metrics.fontSize / DEFAULT_FONT_SIZE);
  const baseHeight = Math.max(480, Math.round(size * state.epubWindow.averageHeightPerSize * fontScale));
  return baseHeight;
};

const setEpubSlotPlaceholderHeight = (slot, chapterDoc, index) => {
  if (!slot) return;
  const height = estimateEpubSlotHeight(chapterDoc, index);
  slot.style.minHeight = `${height}px`;
};

const updateEpubAverageHeightPerSize = (index, chapterDoc, measuredHeight) => {
  const size = Number(chapterDoc && chapterDoc.text && chapterDoc.text.size) || 0;
  if (!size || !measuredHeight) return;
  const ratio = measuredHeight / size;
  if (!Number.isFinite(ratio) || ratio <= 0) return;
  state.epubWindow.averageHeightPerSize = state.epubWindow.averageHeightPerSize * 0.7 + ratio * 0.3;
  state.epubWindow.slotHeights.set(index, measuredHeight);
};

const getEpubChapterOffsetInViewport = (slot) => {
  if (!slot || !stage) return 0;
  return Math.max(0, stage.scrollTop - slot.offsetTop);
};

const getChapterSlot = (index) => state.epubWindow.slots[Number(index) || 0] || null;

const captureEpubRestorePosition = () => {
  if (!stage) return null;
  const slots = state.epubWindow.slots;
  if (!slots.length) return { top: stage.scrollTop, chapterDocIndex: state.epubWindow.currentIndex };
  const viewportTop = stage.scrollTop;
  let anchor = null;
  for (let i = 0; i < slots.length; i += 1) {
    const slot = slots[i];
    if (!slot) continue;
    const top = slot.offsetTop;
    const bottom = top + Math.max(slot.offsetHeight, 1);
    if (viewportTop >= top && viewportTop < bottom) {
      const slotHeight = Math.max(1, slot.offsetHeight);
      anchor = {
        chapterDocIndex: i,
        offset: viewportTop - top,
        chapterProgressRatio: Math.max(0, Math.min(1, (viewportTop - top) / slotHeight)),
      };
      break;
    }
  }
  return anchor || { top: viewportTop, chapterDocIndex: state.epubWindow.currentIndex };
};

const restoreEpubScrollPosition = (restore) => {
  if (!restore || !stage) return;
  if (typeof restore.top === 'number') {
    stage.scrollTop = restore.top;
    return;
  }
  const slot = state.epubWindow.slots[restore.chapterDocIndex];
  if (!slot) return;
  let offset = Number(restore.offset || 0);
  if ((!Number.isFinite(offset) || offset < 0) && Number.isFinite(Number(restore.chapterProgressRatio))) {
    offset = Math.max(0, slot.offsetHeight * Number(restore.chapterProgressRatio));
  }
  stage.scrollTop = Math.max(0, slot.offsetTop + (Number.isFinite(offset) ? offset : 0));
};

const cleanupEpubMountedChapter = (index) => {
  const mounted = state.epubWindow.mounted.get(index);
  if (!mounted) return;
  if (mounted.resizeObserver) {
    mounted.resizeObserver.disconnect();
  }
  if (mounted.iframe && mounted.iframe.parentNode) {
    mounted.iframe.parentNode.removeChild(mounted.iframe);
  }
  state.epubWindow.mounted.delete(index);
};

const detachEpubMountedChapter = (index) => {
  const mounted = state.epubWindow.mounted.get(index);
  if (!mounted) return;
  if (mounted.resizeObserver) {
    mounted.resizeObserver.disconnect();
  }
  if (mounted.iframe && mounted.iframe.parentNode) {
    mounted.iframe.parentNode.removeChild(mounted.iframe);
  }
  state.epubWindow.mounted.delete(index);
};

const buildEpubChapterUrl = async (chapterDoc) => {
  if (!chapterDoc || !chapterDoc.text || typeof chapterDoc.text.load !== 'function') return '';
  return await chapterDoc.text.load();
};

const buildEpubWindowStage = (chapterDocList) => {
  if (!stage) return;
  stage.innerHTML = '';
  const fragment = document.createDocumentFragment();
  const slots = [];
  for (let i = 0; i < chapterDocList.length; i += 1) {
    const slot = document.createElement('section');
    slot.className = 'reader-epub-slot';
    slot.dataset.chapterIndex = String(i);
    setEpubSlotPlaceholderHeight(slot, chapterDocList[i], i);
    fragment.appendChild(slot);
    slots.push(slot);
  }
  stage.appendChild(fragment);
  state.epubWindow.slots = slots;
  getEpubProgressBadge();
};

const buildEpubChapterDocListFromManifest = (manifest) => ((manifest && manifest.chapters) || []).map((chapter, index) => {
  const href = safeText(chapter && chapter.href) || `title${index}`;
  const url = safeText(chapter && chapter.url);
  const label = safeText(chapter && chapter.label) || `第 ${index + 1} 章`;
  const size = Math.max(0, Number(chapter && chapter.size) || 0);
  return {
    label,
    href,
    url,
    text: {
      size,
      load: async () => url,
      unload: async () => {},
    },
  };
});

const applyReaderStyleToSubDocument = (doc) => {
  if (!doc) return;
  applyDefaultIframeStyle(doc);
  applyScrollLayout(doc);
  if (doc.head) {
    const styleId = 'recoll-reader-epub-slot-style';
    let style = doc.getElementById(styleId);
    if (!style) {
      style = doc.createElement('style');
      style.id = styleId;
      doc.head.appendChild(style);
    }
    style.textContent = buildReaderStyle();
  }
};

const applyReaderStyleToMountedEpubSlots = () => {
  for (const mounted of state.epubWindow.mounted.values()) {
    if (!mounted || !mounted.iframe) continue;
    try {
      applyReaderStyleToSubDocument(mounted.iframe.contentDocument);
    } catch (err) {
      console.warn('apply epub slot style failed', err);
    }
  }
};

const mountEpubChapterIntoSlot = async (slot, chapterDoc, index, token) => {
  if (!slot || !chapterDoc) return;
  const existing = state.epubWindow.mounted.get(index);
  if (existing && existing.slot === slot) {
    return;
  }
  cleanupEpubMountedChapter(index);
  slot.innerHTML = '<div class="reader-loading">正在加载章节…</div>';
  setEpubSlotPlaceholderHeight(slot, chapterDoc, index);
  const url = await buildEpubChapterUrl(chapterDoc);
  if (token !== state.epubWindow.syncToken) return;
  slot.innerHTML = '';
  const iframe = document.createElement('iframe');
  iframe.className = 'reader-epub-slot-frame';
  iframe.setAttribute('scrolling', 'no');
  iframe.setAttribute('sandbox', 'allow-same-origin allow-scripts');
  iframe.setAttribute('title', safeText(chapterDoc.label || chapterDoc.href || `Chapter ${index + 1}`));
  iframe.src = url;
  slot.appendChild(iframe);
  const mounted = { slot, iframe, resizeObserver: null };
  state.epubWindow.mounted.set(index, mounted);
  iframe.addEventListener('load', () => {
    if (token !== state.epubWindow.syncToken) return;
    const doc = iframe.contentDocument;
    applyReaderStyleToSubDocument(doc);
    bindIframeInteractions(doc, (event) => {
      if (!shouldHandleStageKey(event)) return;
      if (event.key === 'PageUp' || (event.key === ' ' && event.shiftKey)) {
        event.preventDefault();
        scrollReadingViewport(-1);
      } else if (event.key === 'PageDown' || event.key === ' ') {
        event.preventDefault();
        scrollReadingViewport(1);
      }
    });
    const syncHeight = () => {
      if (!doc || !doc.body) return;
      const htmlEl = doc.documentElement;
      const contentHeight = Math.max(
        doc.body.scrollHeight || 0,
        doc.body.offsetHeight || 0,
        htmlEl ? htmlEl.scrollHeight : 0,
        htmlEl ? htmlEl.offsetHeight : 0,
      );
      const nextHeight = Math.max(240, contentHeight + 16);
      iframe.style.height = `${nextHeight}px`;
      iframe.style.minHeight = `${nextHeight}px`;
      slot.style.minHeight = `${nextHeight}px`;
      updateEpubAverageHeightPerSize(index, chapterDoc, nextHeight);
    };
    syncHeight();
    if (typeof ResizeObserver === 'function' && doc.body) {
      const resizeObserver = new ResizeObserver(() => {
        syncHeight();
      });
      resizeObserver.observe(doc.body);
      if (doc.documentElement) {
        resizeObserver.observe(doc.documentElement);
      }
      mounted.resizeObserver = resizeObserver;
    }
    state.epubWindow.loaded.add(index);
    doc.addEventListener('load', (event) => {
      if (event && event.target && event.target.tagName === 'IMG') {
        syncHeight();
      }
    }, true);
  }, { once: true });
};

const unmountEpubChapterSlot = async (slot, chapterDoc, index) => {
  cleanupEpubMountedChapter(index);
  if (slot) {
    slot.innerHTML = '';
    setEpubSlotPlaceholderHeight(slot, chapterDoc, index);
  }
  await unloadEpubChapterDoc(chapterDoc, index);
};

const disposeRenderedEpubBootstrap = () => {
  try {
    if (state.rendition && typeof state.rendition.removeContent === 'function') {
      state.rendition.removeContent();
    }
  } catch (err) {
    console.warn('dispose rendered epub bootstrap failed', err);
  }
};

const updateEpubProgress = async () => {
  if (!state.current || !isEpubFormat(state.current.format)) return;
  const chapterDocList = getEpubChapterDocList();
  if (!chapterDocList.length) return;
  const currentIndex = Math.max(0, getCurrentEpubChapterIndex());
  const badge = getEpubProgressBadge();
  const slot = state.epubWindow.slots[currentIndex];
  const intraChapterOffset = slot ? Math.max(0, Math.round(getEpubChapterOffsetInViewport(slot))) : 0;
  const chapterProgressRatio = slot && slot.offsetHeight > 0
    ? Math.max(0, Math.min(1, intraChapterOffset / slot.offsetHeight))
    : 0;
  if (badge) {
    const percentage = chapterDocList.length > 1 ? Math.round((currentIndex / (chapterDocList.length - 1)) * 100) : 100;
    badge.textContent = `${percentage}%`;
  }
  const position = {
    chapterDocIndex: currentIndex,
    chapterHref: safeText((chapterDocList[currentIndex] || {}).href),
    chapterTitle: safeText((chapterDocList[currentIndex] || {}).label),
    percentage: chapterDocList.length > 1 ? String(currentIndex / (chapterDocList.length - 1)) : '1',
    intraChapterOffset,
    chapterProgressRatio,
    scrollTop: stage ? Math.max(0, Math.round(stage.scrollTop)) : 0,
  };
  savePosition(state.current, position);
  if (state.folderMeta) {
    saveFolderCursor(state.folderMeta);
  }
};

const updateCurrentEpubChapterByScroll = () => {
  if (!stage || !state.epubWindow.slots.length) return;
  const viewportLine = stage.scrollTop + Math.max(80, stage.clientHeight * 0.25);
  let currentIndex = state.epubWindow.currentIndex >= 0 ? state.epubWindow.currentIndex : 0;
  for (let i = 0; i < state.epubWindow.slots.length; i += 1) {
    const slot = state.epubWindow.slots[i];
    if (!slot) continue;
    if (viewportLine >= slot.offsetTop) {
      currentIndex = i;
    } else {
      break;
    }
  }
  if (currentIndex !== state.epubWindow.currentIndex) {
    state.epubWindow.currentIndex = currentIndex;
    if (state.rendition && state.rendition.tempLocation) {
      state.rendition.tempLocation.chapterDocIndex = String(currentIndex);
      state.rendition.tempLocation.chapterHref = safeText((getEpubChapterDocList()[currentIndex] || {}).href);
      state.rendition.tempLocation.chapterTitle = safeText((getEpubChapterDocList()[currentIndex] || {}).label);
    }
    void updateEpubProgress();
    scheduleEpubContextWindowSync();
  }
};

const bindEpubStageScroll = () => {
  if (!stage || stage.__recollEpubScrollBound) return;
  let timer = 0;
  stage.addEventListener('scroll', () => {
    updateCurrentEpubChapterByScroll();
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      void updateEpubProgress();
      scheduleEpubContextWindowSync();
    }, 120);
  }, { passive: true });
  stage.__recollEpubScrollBound = 'yes';
};

const syncEpubSlotsStyle = () => {
  const chapterDocList = getEpubChapterDocList();
  if (!chapterDocList.length) return;
  state.epubWindow.slots.forEach((slot, index) => {
    const chapterDoc = chapterDocList[index];
    if (!slot || !chapterDoc) return;
    setEpubSlotPlaceholderHeight(slot, chapterDoc, index);
  });
  applyReaderStyleToMountedEpubSlots();
};

const syncEpubMountedWindow = async (force = false) => {
  if (!isEpubWindowedMode()) return;
  const chapterDocList = getEpubChapterDocList();
  if (!chapterDocList.length) return;
  syncEpubSlotsStyle();
  await syncEpubContextWindow(force);
};

const openEpubScrollDocument = async (item, viewportConfig) => {
  if (!item || !item.manifestUrl) {
    throw new Error('EPUB 清单地址缺失');
  }
  setLoading(`正在解析 ${item.name || item.title || 'EPUB'} 章节列表…`);
  const manifest = await fetchJson(item.manifestUrl);
  const chapterDocList = buildEpubChapterDocListFromManifest(manifest);
  if (!chapterDocList.length) {
    throw new Error('EPUB 章节列表为空');
  }
  const rendition = {
    format: 'EPUB',
    readerMode: viewportConfig.readerMode,
    chapterDocList,
    book: {
      title: safeText(manifest && manifest.title),
      toc: Array.isArray(manifest && manifest.toc) ? manifest.toc : [],
      sections: chapterDocList.map((chapter) => ({
        href: chapter.href,
        label: chapter.label,
        size: chapter.text && chapter.text.size,
      })),
    },
    tempLocation: {},
    on() {},
    setStyle() {},
    removeContent() {
      if (stage) stage.innerHTML = '';
    },
  };
  state.rendition = rendition;
  window.rendition = rendition;
  setLoading(`正在准备 ${item.name || item.title || 'EPUB'} 阅读窗口…`);
  buildEpubWindowStage(chapterDocList);
  bindStageNavigation();
  bindEpubStageScroll();
  const initialIndex = getInitialChapterDocIndex(rendition);
  const savedPosition = loadPosition(item);
  const pendingRestore = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
    ? state.pendingRestorePosition.position
    : null;
  const restorePosition = pendingRestore || savedPosition;
  const restoredIndex = restorePosition && hasSavedPositionData(restorePosition)
    ? Math.max(0, Math.min(chapterDocList.length - 1, Number(restorePosition.chapterDocIndex) || initialIndex))
    : initialIndex;
  state.epubWindow.currentIndex = restoredIndex;
  if (restorePosition && hasSavedPositionData(restorePosition)) {
    state.epubWindow.restorePosition = getEpubAnchorFromPosition(restorePosition);
  }
  state.pendingRestorePosition = null;
  rendition.tempLocation.chapterDocIndex = String(restoredIndex);
  rendition.tempLocation.chapterHref = safeText((chapterDocList[restoredIndex] || {}).href);
  rendition.tempLocation.chapterTitle = safeText((chapterDocList[restoredIndex] || {}).label);
  rendition.getPosition = () => ({
    chapterDocIndex: state.epubWindow.currentIndex,
    chapterHref: safeText((chapterDocList[state.epubWindow.currentIndex] || {}).href),
    chapterTitle: safeText((chapterDocList[state.epubWindow.currentIndex] || {}).label),
    percentage: chapterDocList.length > 1 ? String(state.epubWindow.currentIndex / (chapterDocList.length - 1)) : '1',
    intraChapterOffset: (() => {
      const currentSlot = state.epubWindow.slots[state.epubWindow.currentIndex];
      return currentSlot ? Math.max(0, Math.round(getEpubChapterOffsetInViewport(currentSlot))) : 0;
    })(),
    scrollTop: stage ? Math.max(0, Math.round(stage.scrollTop)) : 0,
  });
  rendition.getProgress = async () => ({
    percentage: chapterDocList.length > 1 ? state.epubWindow.currentIndex / (chapterDocList.length - 1) : 1,
  });
  rendition.goToPosition = async (rawPosition) => {
    const position = typeof rawPosition === 'string' ? JSON.parse(rawPosition) : rawPosition;
    const nextIndex = Math.max(0, Math.min(chapterDocList.length - 1, Number(position && position.chapterDocIndex) || 0));
    state.epubWindow.currentIndex = nextIndex;
    rendition.tempLocation.chapterDocIndex = String(nextIndex);
    rendition.tempLocation.chapterHref = safeText((chapterDocList[nextIndex] || {}).href);
    rendition.tempLocation.chapterTitle = safeText((chapterDocList[nextIndex] || {}).label);
    state.epubWindow.restorePosition = getEpubAnchorFromPosition(position);
    await syncEpubContextWindow(true);
  };
  rendition.goToChapterDocIndex = async (index) => {
    await rendition.goToPosition({ chapterDocIndex: index, intraChapterOffset: 0 });
  };
  await syncEpubContextWindow(true);
  if (state.epubWindow.restorePosition) {
    restoreEpubScrollPosition(state.epubWindow.restorePosition);
    state.epubWindow.restorePosition = null;
  } else {
    const slot = getChapterSlot(restoredIndex);
    if (slot && stage) {
      stage.scrollTop = Math.max(0, slot.offsetTop);
    }
  }
  await updateEpubProgress();
  return rendition;
};

const syncEpubContextWindow = async (force = false) => {
  if (!state.rendition || !isEpubFormat(state.current && state.current.format) || state.readerMode !== 'scroll') return;
  const chapterDocList = getEpubChapterDocList();
  if (!chapterDocList.length) return;
  const currentIndex = getCurrentEpubChapterIndex();
  if (currentIndex < 0) return;
  const token = ++state.epubWindow.syncToken;
  state.epubWindow.currentIndex = currentIndex;
  const mountedRange = getEpubMountedRange(currentIndex, chapterDocList.length);
  const preloadRange = getEpubPreloadRange(currentIndex, chapterDocList.length);
  state.epubWindow.mountedRange = mountedRange;
  state.epubWindow.preloadRange = preloadRange;
  const restore = state.epubWindow.restorePosition || captureEpubRestorePosition();
  state.epubWindow.restorePosition = null;

  const preloadTasks = [];
  for (let i = preloadRange.start; i <= preloadRange.end; i += 1) {
    preloadTasks.push(loadEpubChapterDoc(chapterDocList[i], i));
  }

  await Promise.allSettled(preloadTasks);
  if (token !== state.epubWindow.syncToken) return;

  const mountTasks = [];
  for (let i = mountedRange.start; i <= mountedRange.end; i += 1) {
    mountTasks.push(mountEpubChapterIntoSlot(state.epubWindow.slots[i], chapterDocList[i], i, token));
  }
  await Promise.allSettled(mountTasks);
  if (token !== state.epubWindow.syncToken) return;

  const unmountTasks = [];
  for (let i = 0; i < chapterDocList.length; i += 1) {
    if (i >= preloadRange.start && i <= preloadRange.end) continue;
    unmountTasks.push(unmountEpubChapterSlot(state.epubWindow.slots[i], chapterDocList[i], i));
  }
  for (let i = preloadRange.start; i <= preloadRange.end; i += 1) {
    if (i >= mountedRange.start && i <= mountedRange.end) continue;
    unmountTasks.push((async () => {
      const chapterDoc = chapterDocList[i];
      const slot = state.epubWindow.slots[i];
      detachEpubMountedChapter(i);
      if (slot) {
        slot.innerHTML = '';
        setEpubSlotPlaceholderHeight(slot, chapterDoc, i);
      }
    })());
  }
  await Promise.allSettled(unmountTasks);
  if (token !== state.epubWindow.syncToken) return;

  restoreEpubScrollPosition(restore);
  void updateEpubProgress();
};

const scheduleEpubContextWindowSync = (force = false) => {
  if (!state.rendition || !isEpubFormat(state.current && state.current.format) || state.readerMode !== 'scroll') return;
  window.clearTimeout(state.epubWindow.syncTimer);
  state.epubWindow.syncTimer = window.setTimeout(() => {
    void syncEpubContextWindow(force);
  }, force ? 0 : 120);
};

const scheduleScrollIframeHeightSync = (doc, delay = 60) => {
  window.clearTimeout(state.scrollHeightTimer);
  state.scrollHeightTimer = window.setTimeout(() => {
    syncScrollIframeHeight(doc);
  }, delay);
};

const bindScrollContentAutosize = (doc) => {
  if (!doc || !doc.body || doc.__recollReaderScrollAutoSizeBound || !state.current || isChmFormat(state.current.format)) return;
  const schedule = () => scheduleScrollIframeHeightSync(doc);
  if (typeof ResizeObserver === 'function') {
    const resizeObserver = new ResizeObserver(schedule);
    resizeObserver.observe(doc.body);
    if (doc.documentElement) {
      resizeObserver.observe(doc.documentElement);
    }
    doc.__recollReaderScrollResizeObserver = resizeObserver;
  }
  doc.addEventListener('load', (event) => {
    if (event && event.target && event.target.tagName === 'IMG') {
      schedule();
    }
  }, true);
  doc.__recollReaderScrollAutoSizeBound = 'yes';
};

const closeSidebar = () => {
  sidebar.classList.add('is-hidden');
  document.body.classList.remove('reader-sidebar-open');
};

const openSidebar = () => {
  sidebar.classList.remove('is-hidden');
  if (isMobileViewport()) {
    document.body.classList.add('reader-sidebar-open');
  }
};

const syncSidebarState = () => {
  if (!isMobileViewport()) {
    sidebar.classList.remove('is-hidden');
    document.body.classList.remove('reader-sidebar-open');
    state.sidebarInitialized = true;
    return;
  }
  if (!state.sidebarInitialized) {
    closeSidebar();
    state.sidebarInitialized = true;
    return;
  }
  if (sidebar.classList.contains('is-hidden')) {
    document.body.classList.remove('reader-sidebar-open');
  } else {
    document.body.classList.add('reader-sidebar-open');
  }
};

const syncToolbarToggleLabel = () => {
  if (!toolbarToggleButton) return;
  toolbarToggleButton.textContent = isToolbarHidden() ? '展开顶栏' : '收起顶栏';
};

const setToolbarHidden = (hidden) => {
  if (!readerApp) return;
  readerApp.classList.toggle('reader-toolbar-hidden', !!hidden);
  syncToolbarToggleLabel();
  window.requestAnimationFrame(() => {
    scheduleLayoutSync(true);
  });
};

const toggleToolbar = () => {
  setToolbarHidden(!isToolbarHidden());
};

const updateFontSizeUi = () => {
  if (fontSizeLabel) {
    fontSizeLabel.textContent = `${state.fontSize}px`;
  }
  if (fontDecreaseButton) {
    fontDecreaseButton.disabled = state.fontSize <= MIN_FONT_SIZE;
  }
  if (fontIncreaseButton) {
    fontIncreaseButton.disabled = state.fontSize >= MAX_FONT_SIZE;
  }
};

const persistFontSize = () => {
  try {
    localStorage.setItem(FONT_SIZE_STORAGE_KEY, String(state.fontSize));
  } catch (err) {
    console.warn('save font size failed', err);
  }
};

const savePosition = (item, position) => {
  if (!item || !item.path) return;
  try {
    localStorage.setItem(`recoll-reader:position:${item.path}`, JSON.stringify(position || {}));
  } catch (err) {
    console.warn('savePosition failed', err);
  }
};

const loadPosition = (item) => {
  if (!item || !item.path) return null;
  try {
    const raw = localStorage.getItem(`recoll-reader:position:${item.path}`);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
};

const isValidTxtPosition = (position) => {
  if (!position || typeof position !== 'object') return false;
  const chapterDocIndex = position.chapterDocIndex;
  return typeof chapterDocIndex === 'number' || typeof chapterDocIndex === 'string';
};

const getActiveContentFrame = () => stage ? stage.querySelector('iframe') : null;

const hasMeaningfulChapterDoc = (chapter) => {
  if (!chapter || typeof chapter !== 'object') return false;
  return !!(safeText(chapter.href).trim() || safeText(chapter.label).trim());
};

const getInitialChapterDocIndex = (rendition) => {
  const chapterDocList = rendition && Array.isArray(rendition.chapterDocList) ? rendition.chapterDocList : [];
  if (!chapterDocList.length) return 0;
  const firstContentIndex = chapterDocList.findIndex((chapter) => hasMeaningfulChapterDoc(chapter));
  return firstContentIndex >= 0 ? firstContentIndex : 0;
};

const isBlankInitialPagedPosition = (rendition, position) => {
  if (!position || typeof position !== 'object') return false;
  const chapterDocList = rendition && Array.isArray(rendition.chapterDocList) ? rendition.chapterDocList : [];
  if (!chapterDocList.length || hasMeaningfulChapterDoc(chapterDocList[0])) return false;
  const chapterDocIndex = Number(position.chapterDocIndex);
  return chapterDocIndex === 0
    && !safeText(position.chapterHref).trim()
    && !safeText(position.chapterTitle).trim()
    && !safeText(position.text).trim();
};

const buildDefaultPagedPosition = (rendition) => {
  const chapterDocList = rendition && Array.isArray(rendition.chapterDocList) ? rendition.chapterDocList : [];
  if (!chapterDocList.length) return null;
  const chapterDocIndex = getInitialChapterDocIndex(rendition);
  const chapter = chapterDocList[chapterDocIndex] || {};
  return {
    text: '',
    chapterTitle: safeText(chapter.label),
    chapterDocIndex,
    chapterHref: safeText(chapter.href),
    count: '',
    page: '',
    percentage: chapterDocList.length > 1 ? String(chapterDocIndex / (chapterDocList.length - 1)) : '0',
  };
};

const resolveInitialPagedPosition = (rendition, position) => {
  if (!position || typeof position !== 'object' || !hasMeaningfulChapterDoc((rendition && rendition.chapterDocList && rendition.chapterDocList[Number(position.chapterDocIndex)]) || null)) {
    return buildDefaultPagedPosition(rendition);
  }
  return isBlankInitialPagedPosition(rendition, position) ? buildDefaultPagedPosition(rendition) : position;
};

const syncPagedStageViewport = () => {
  if (!stage || !stageShell || !isPagedReaderMode(state.readerMode)) {
    if (stage) {
      stage.style.height = '';
    }
    return;
  }
  const shellRect = stageShell.getBoundingClientRect();
  const shellStyle = window.getComputedStyle(stageShell);
  const viewportHeight = Math.max(
    0,
    Math.floor(
      shellRect.height
      - parseFloat(shellStyle.paddingTop || '0')
      - parseFloat(shellStyle.paddingBottom || '0'),
    ),
  );
  if (viewportHeight > 0) {
    stage.style.height = `${viewportHeight}px`;
  }
};

const getChmCurrentPosition = () => {
  const iframe = getActiveContentFrame();
  if (!iframe || !state.currentChmManifest) return null;
  try {
    const { contentWindow } = iframe;
    if (!contentWindow || !contentWindow.location) return null;
    return {
      href: state.currentChmHref || state.currentChmManifest.startPath || '',
      scrollLeft: Math.max(0, Math.round(contentWindow.scrollX || 0)),
      scrollTop: Math.max(0, Math.round(contentWindow.scrollY || 0)),
    };
  } catch (err) {
    return state.currentChmHref ? { href: state.currentChmHref } : null;
  }
};

const persistCurrentPosition = () => {
  if (!state.current) return;
  if (isChmFormat(state.current.format)) {
    const position = getChmCurrentPosition();
    if (position) savePosition(state.current, position);
    return;
  }
  const position = state.rendition && typeof state.rendition.getPosition === 'function'
    ? state.rendition.getPosition()
    : {};
  savePosition(state.current, position);
};

const saveFolderCursor = (meta) => {
  if (!meta) return;
  localStorage.setItem('recoll-reader:folder', JSON.stringify({ currentIndex: meta.currentIndex, root: meta.root }));
};

const updateMeta = () => {
  if (!state.current) return;
  const parts = [state.current.format ? state.current.format.toUpperCase() : '', state.current.author || '', state.current.name || ''].filter(Boolean);
  metaEl.textContent = parts.join(' · ');
  titleEl.textContent = state.current.title || state.current.name || 'Reader';
};

const renderSidebar = () => {
  const chmMode = !!(state.current && isChmFormat(state.current.format) && state.currentChmManifest);
  if (sidebarHeader) {
    sidebarHeader.textContent = chmMode ? '目录' : '书单';
  }
  if (!state.items.length && !chmMode) {
    booklist.innerHTML = '<div class="reader-loading">当前文件夹没有可阅读文件</div>';
    return;
  }
  const libraryMarkup = state.items.map((item, idx) => `
    <button class="reader-book-item ${idx === state.currentIndex ? 'is-active' : ''}" data-index="${idx}">
      ${escapeHtml(item.title || item.name || 'Untitled')}
      <small>${escapeHtml(item.format || '')} ${item.path ? escapeHtml(item.path.replace(item.root || '', '')) : ''}</small>
    </button>`).join('');
  let markup = libraryMarkup;
  if (chmMode) {
    const tocItems = (state.currentChmManifest.toc || []).map((item) => {
      const active = state.currentChmHref && normalizeChmHref(item.href) === normalizeChmHref(state.currentChmHref);
      const indent = 14 + (Math.max(0, Number(item.depth) || 0) * 16);
      return `
        <button class="reader-book-item ${active ? 'is-active' : ''}" data-chm-href="${escapeHtml(item.href || '')}" style="padding-left:${indent}px">
          ${escapeHtml(item.label || item.href || 'Untitled')}
          <small>${escapeHtml(item.href || '')}</small>
        </button>`;
    }).join('');
    const hasLibrary = state.items.length > 1;
    markup = `
      ${hasLibrary ? '<div class="reader-sidebar-section-title">书单</div>' : ''}
      ${hasLibrary ? libraryMarkup : ''}
      ${tocItems ? '<div class="reader-sidebar-section-title">目录</div>' : ''}
      ${tocItems || '<div class="reader-loading">当前 CHM 没有可解析目录</div>'}
    `;
  }
  booklist.innerHTML = markup;
  booklist.querySelectorAll('[data-index]').forEach((btn) => {
    btn.addEventListener('click', () => {
      void openIndex(Number(btn.dataset.index));
      if (isMobileViewport()) {
        closeSidebar();
      }
    });
  });
  booklist.querySelectorAll('[data-chm-href]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openChmHref(btn.dataset.chmHref || '');
      if (isMobileViewport()) {
        closeSidebar();
      }
    });
  });
};

const setLoading = (text) => {
  stage.innerHTML = `<div class="reader-loading">${escapeHtml(text || '加载中…')}</div>`;
};

const setError = (text) => {
  stage.innerHTML = `<div class="reader-error">${escapeHtml(text || '加载失败')}</div>`;
};

const cleanupRendition = () => {
  window.clearTimeout(state.scrollHeightTimer);
  window.clearTimeout(state.epubWindow.syncTimer);
  state.currentChmManifest = null;
  state.currentChmHref = '';
  state.currentChmRestore = null;
  resetEpubContextWindow();
  if (state.rendition && typeof state.rendition.removeContent === 'function') {
    try { state.rendition.removeContent(); } catch (err) {}
  }
  state.rendition = null;
  if (state.currentBlobUrl) {
    URL.revokeObjectURL(state.currentBlobUrl);
    state.currentBlobUrl = '';
  }
};

const updatePageButtons = () => {
  const showPageButtons = isPagedReaderMode(state.readerMode);
  syncReaderModeUi();
  const canPage = !isChmFormat(state.current && state.current.format)
    && !!(state.rendition && typeof state.rendition.prev === 'function' && typeof state.rendition.next === 'function');
  prevPageButton.hidden = !showPageButtons;
  nextPageButton.hidden = !showPageButtons;
  prevPageButton.disabled = !showPageButtons || !canPage;
  nextPageButton.disabled = !showPageButtons || !canPage;
};

const turnPage = async (direction) => {
  if (!isPagedReaderMode(state.readerMode)) return;
  const rendition = state.rendition;
  if (!rendition) return;
  const method = direction < 0 ? rendition.prev : rendition.next;
  if (typeof method !== 'function') return;
  try {
    await method.call(rendition);
  } catch (err) {
    console.error(err);
  }
};

const scrollReadingViewport = (direction) => {
  if (isPagedReaderMode(state.readerMode) || !stage) return;
  const amount = Math.max(120, stage.clientHeight - 72) * direction;
  if (state.current && isChmFormat(state.current.format)) {
    const iframe = getActiveContentFrame();
    try {
      const win = iframe && iframe.contentWindow;
      if (win && typeof win.scrollBy === 'function') {
        win.scrollBy({
          left: 0,
          top: amount,
          behavior: 'smooth',
        });
        return;
      }
    } catch (err) {
      console.warn('scroll chm iframe failed', err);
    }
  }
  stage.scrollBy({
    left: 0,
    top: amount,
    behavior: 'smooth',
  });
};

const shouldHandleStageKey = (event) => {
  const target = event.target;
  if (!target) return true;
  const tagName = target.tagName ? target.tagName.toLowerCase() : '';
  return !/^(input|textarea|select|button)$/.test(tagName);
};

const isInteractiveTouchTarget = (target) => {
  if (!target || typeof target.closest !== 'function') return false;
  return !!target.closest('a, button, input, textarea, select, label, summary, [role="button"], .kookit-note, .kookit-note-icon, .kookit-word-def, .kookit-word-tooltip');
};

const bindTouchNavigationTarget = (target, widthProvider) => {
  if (!target || target.__recollReaderTouchBound) return;
  let touchState = null;
  const resetTouchState = () => {
    touchState = null;
  };
  target.addEventListener('touchstart', (event) => {
    if (!event.touches || event.touches.length !== 1) {
      resetTouchState();
      return;
    }
    const touch = event.touches[0];
    touchState = {
      x: touch.clientX,
      y: touch.clientY,
      time: Date.now(),
      interactive: isInteractiveTouchTarget(event.target),
    };
  }, { passive: true });
  target.addEventListener('touchcancel', resetTouchState, { passive: true });
  target.addEventListener('touchend', (event) => {
    if (!touchState || !event.changedTouches || event.changedTouches.length !== 1) {
      resetTouchState();
      return;
    }
    const touch = event.changedTouches[0];
    const width = Math.max(1, widthProvider());
    const dx = touch.clientX - touchState.x;
    const dy = touch.clientY - touchState.y;
    const duration = Date.now() - touchState.time;
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);
    const swipeThreshold = Math.max(48, width * 0.12);
    const canPage = isPagedReaderMode(state.readerMode);

    if (canPage && absDx >= swipeThreshold && absDx > absDy * 1.3) {
      resetTouchState();
      void turnPage(dx < 0 ? 1 : -1);
      return;
    }

    if (!touchState.interactive && duration <= 280 && absDx < 12 && absDy < 12) {
      if (canPage && touch.clientX <= width * 0.28) {
        resetTouchState();
        void turnPage(-1);
        return;
      }
      if (canPage && touch.clientX >= width * 0.72) {
        resetTouchState();
        void turnPage(1);
        return;
      }
      if (touch.clientX > width * 0.38 && touch.clientX < width * 0.62) {
        resetTouchState();
        toggleToolbar();
        return;
      }
    }

    resetTouchState();
  }, { passive: true });
  target.__recollReaderTouchBound = 'yes';
};

const bindIframeInteractions = (doc, handleKeydown) => {
  if (!doc) return;
  if (!doc.__recollReaderKeybound) {
    doc.addEventListener('keydown', handleKeydown);
    doc.__recollReaderKeybound = true;
  }
  bindTouchNavigationTarget(doc, () => {
    if (doc.documentElement && doc.documentElement.clientWidth) {
      return doc.documentElement.clientWidth;
    }
    return stage ? stage.clientWidth : window.innerWidth;
  });
};

const bindStageNavigation = () => {
  bindTouchNavigationTarget(stage, () => stage ? stage.clientWidth : window.innerWidth);
};

const bindIframeNavigation = () => {
  const iframe = getActiveContentFrame();
  if (!iframe || iframe.dataset.readerBound === 'yes') return;
  const handleKeydown = (event) => {
    if (!shouldHandleStageKey(event)) return;
    if (isPagedReaderMode(state.readerMode) && (event.key === 'ArrowLeft' || event.key === 'PageUp')) {
      event.preventDefault();
      void turnPage(-1);
    } else if (isPagedReaderMode(state.readerMode) && (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ')) {
      event.preventDefault();
      void turnPage(1);
    } else if (!isPagedReaderMode(state.readerMode) && (event.key === 'PageUp' || (event.key === ' ' && event.shiftKey))) {
      event.preventDefault();
      scrollReadingViewport(-1);
    } else if (!isPagedReaderMode(state.readerMode) && (event.key === 'PageDown' || event.key === ' ')) {
      event.preventDefault();
      scrollReadingViewport(1);
    }
  };
  iframe.addEventListener('load', () => {
    bindIframeInteractions(iframe.contentDocument, handleKeydown);
    if (state.current && isChmFormat(state.current.format)) {
      handleChmFrameLoad(iframe);
    }
  });
  bindIframeInteractions(iframe.contentDocument, handleKeydown);
  iframe.dataset.readerBound = 'yes';
};

const fetchJson = async (url) => {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return await res.json();
};

const normalizeChmHref = (href) => {
  const value = safeText(href).trim();
  if (!value) return '';
  try {
    const url = new URL(value, 'https://reader.invalid/');
    const pathname = url.pathname.replace(/^\/+/, '');
    return `${pathname}${url.search}${url.hash}`;
  } catch (err) {
    return value.replace(/^\/+/, '');
  }
};

const resolveChmContentUrl = (baseUrl, href) => {
  if (!baseUrl) return '';
  if (!href) return baseUrl;
  try {
    let absoluteBase = baseUrl;
    if (!/^https?:\/\//i.test(baseUrl)) {
      absoluteBase = window.location.origin + (baseUrl.startsWith('/') ? '' : '/') + baseUrl;
    }
    return new URL(href, absoluteBase).toString();
  } catch (err) {
    return href;
  }
};

const findChmTocEntry = (href) => {
  const normalized = normalizeChmHref(href);
  return ((state.currentChmManifest && state.currentChmManifest.toc) || []).find((item) => normalizeChmHref(item.href) === normalized) || null;
};

const updateChmMeta = () => {
  if (!state.current || !state.currentChmManifest) return;
  const parts = [state.current.format ? state.current.format.toUpperCase() : '', state.current.author || ''].filter(Boolean);
  const currentEntry = findChmTocEntry(state.currentChmHref);
  const label = currentEntry && currentEntry.label ? currentEntry.label : (state.current.title || state.current.name || 'Reader');
  metaEl.textContent = parts.join(' · ');
  titleEl.textContent = label;
};

const openChmHref = (href, restore = null) => {
  if (!state.currentChmManifest) return;
  const url = resolveChmContentUrl(state.currentChmManifest.contentBaseUrl, href || state.currentChmManifest.startPath);
  const iframe = getActiveContentFrame();
  if (!iframe || !url) return;
  state.currentChmHref = normalizeChmHref(href || state.currentChmManifest.startPath);
  state.currentChmRestore = restore && typeof restore === 'object' ? restore : null;
  updateChmMeta();
  renderSidebar();
  iframe.src = url;
};

const handleChmFrameLoad = (iframe) => {
  if (!iframe || !state.currentChmManifest) return;
  let doc;
  let win;
  try {
    doc = iframe.contentDocument;
    win = iframe.contentWindow;
  } catch (err) {
    console.warn('access chm iframe failed', err);
    return;
  }
  if (!doc || !win) return;
  applyDefaultIframeStyle(doc);
  applyScrollLayout(doc);
  if (doc.head) {
    const styleId = 'recoll-reader-chm-style';
    let style = doc.getElementById(styleId);
    if (!style) {
      style = doc.createElement('style');
      style.id = styleId;
      doc.head.appendChild(style);
    }
    style.textContent = buildReaderStyle();
  }
  try {
    const currentUrl = new URL(win.location.href);
    const baseUrl = new URL(state.currentChmManifest.contentBaseUrl, window.location.origin);
    let rel = decodeURIComponent(currentUrl.pathname.replace(baseUrl.pathname, '').replace(/^\/+/, ''));
    rel = `${rel}${currentUrl.search}${currentUrl.hash}`;
    const normalized = normalizeChmHref(rel || state.currentChmManifest.startPath);
    if (normalized) {
      state.currentChmHref = normalized;
    }
  } catch (err) {
    if (!state.currentChmHref) {
      state.currentChmHref = normalizeChmHref(state.currentChmManifest.startPath);
    }
  }

  const rewriteAbsoluteUrl = (element, attr) => {
    const value = element.getAttribute(attr);
    if (!value || /^(https?:|javascript:|data:|mailto:|file:|#|tel:)/i.test(value)) return;
    if (/^\/[^/]/.test(value)) {
      const rel = value.replace(/^\/+/, '');
      element.setAttribute(attr, resolveChmContentUrl(contentBaseUrl, rel));
    }
  };
  const contentBaseUrl = state.currentChmManifest.contentBaseUrl;
  doc.querySelectorAll('[href]').forEach((el) => rewriteAbsoluteUrl(el, 'href'));
  doc.querySelectorAll('[src]').forEach((el) => rewriteAbsoluteUrl(el, 'src'));
  doc.querySelectorAll('[action]').forEach((el) => rewriteAbsoluteUrl(el, 'action'));

  if (!doc.__recollChmLinksBound) {
    doc.querySelectorAll('a[href]').forEach((link) => {
      const href = link.getAttribute('href');
      if (!href || /^javascript:/i.test(href)) return;
      link.addEventListener('click', (event) => {
        const resolved = resolveChmContentUrl(win.location.href, href);
        if (!resolved) return;
        let target;
        try {
          target = new URL(resolved, window.location.origin);
        } catch (err) {
          return;
        }
        const manifestBase = new URL(state.currentChmManifest.contentBaseUrl, window.location.origin);
        if (target.origin !== manifestBase.origin) {
          return;
        }
        if (target.pathname.startsWith(manifestBase.pathname)) {
          event.preventDefault();
          const rel = decodeURIComponent(target.pathname.replace(manifestBase.pathname, '').replace(/^\/+/, ''));
          openChmHref(`${rel}${target.search}${target.hash}`);
        } else if (/^\/[^/]/.test(href)) {
          event.preventDefault();
          const rel = href.replace(/^\/+/, '');
          openChmHref(`${rel}${target.search}${target.hash}`);
        }
      });
    });
    doc.__recollChmLinksBound = true;
  }

  if (state.currentChmRestore && normalizeChmHref(state.currentChmRestore.href) === state.currentChmHref) {
    const restore = state.currentChmRestore;
    state.currentChmRestore = null;
    win.requestAnimationFrame(() => {
      win.scrollTo(restore.scrollLeft || 0, restore.scrollTop || 0);
    });
  } else {
    state.currentChmRestore = null;
  }

  updateChmMeta();
  renderSidebar();
};

const openChmDocument = async (item) => {
  const manifestUrl = item.manifestUrl || '';
  if (!manifestUrl) {
    throw new Error('CHM 清单地址缺失');
  }
  const manifest = await fetchJson(manifestUrl);
  if (!manifest || !manifest.startUrl) {
    throw new Error('CHM 入口页解析失败');
  }
  state.currentChmManifest = manifest;
  state.currentChmHref = normalizeChmHref(manifest.startPath || '');
  stage.innerHTML = '<iframe id="reader-chm-iframe" class="reader-chm-iframe" title="CHM Reader" loading="eager" referrerpolicy="same-origin"></iframe>';
  const iframe = getActiveContentFrame();
  if (!iframe) {
    throw new Error('CHM 阅读框初始化失败');
  }
  const savedPosition = loadPosition(item);
  const restore = isValidChmPosition(state.currentChmRestore)
    ? state.currentChmRestore
    : (isValidChmPosition(savedPosition) ? savedPosition : null);
  openChmHref((restore && restore.href) || manifest.startPath, restore);
};

const getStageSize = () => ({
  width: stage ? stage.clientWidth : 0,
  height: stage ? stage.clientHeight : 0,
});

const rememberStageSize = () => {
  const { width, height } = getStageSize();
  state.lastStageWidth = width;
  state.lastStageHeight = height;
};

const getReadingStyleMetrics = () => {
  const stageWidth = stage ? stage.clientWidth : window.innerWidth;
  const mobile = isMobileViewport();
  const format = safeText(state.rendition && state.rendition.format).toUpperCase();
  const isComicFormat = /^(CBR|CBT|CBZ|CB7)$/.test(format);
  const desiredContentWidth = mobile
    ? Math.max(280, Math.min(stageWidth - 28, 640))
    : Math.max(520, Math.min(Math.round(stageWidth * 0.7), 820));
  const contentWidth = Math.max(280, Math.min(stageWidth - 32, desiredContentWidth));
  return {
    fontSize: state.fontSize,
    lineHeight: state.fontSize >= 26 ? 2.0 : state.fontSize >= 22 ? 1.92 : 1.85,
    paragraphGap: Math.max(10, Math.round(state.fontSize * 0.42)),
    headingGap: Math.max(18, Math.round(state.fontSize * 0.75)),
    contentWidth: isComicFormat ? Math.max(280, stageWidth - 40) : contentWidth,
    contentPadding: mobile ? 0 : 4,
    isComicFormat,
  };
};

const buildReaderStyle = () => {
  const metrics = getReadingStyleMetrics();
  const fontSize = `${metrics.fontSize}px`;
  const lineHeight = metrics.lineHeight.toFixed(2);
  const headingLineHeight = Math.max(1.35, metrics.lineHeight - 0.35).toFixed(2);
  const contentWidth = `${metrics.contentWidth}px`;
  const paragraphGap = `${metrics.paragraphGap}px`;
  const headingGap = `${metrics.headingGap}px`;
  const textSelectors = [
    'article', 'blockquote', 'dd', 'div', 'dt', 'li', 'p', 'pre', 'td', 'th',
    'section', 'aside', 'main', 'ul', 'ol', 'dl', 'figcaption',
  ].join(', ');
  const headingSelectors = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].join(', ');
  const mediaSelectors = ['img', 'svg', 'video', 'canvas', 'audio', 'embed', 'object', 'figure'].join(', ');
  return `
    html, body {
      background: transparent !important;
      color: #2f2419 !important;
      overflow-x: hidden;
    }
    body {
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
      hanging-punctuation: first last;
    }
    a, article, cite, div, li, p, span:not(.kookit-note):not(.kookit-note-icon):not(.kookit-note-tooltip):not(.kookit-word-def):not(.kookit-word-tooltip), pre, dt, dd, table, bold, font, blockquote {
      font-size: ${fontSize} !important;
      line-height: ${lineHeight} !important;
      letter-spacing: 0.01em !important;
      color: #2f2419 !important;
      max-width: 100% !important;
      overflow: visible !important;
      word-wrap: break-word !important;
    }
    p, li, dd, blockquote {
      margin-top: 0 !important;
      margin-bottom: ${paragraphGap} !important;
      text-align: justify !important;
      text-justify: inter-ideograph !important;
    }
    ${headingSelectors} {
      margin: 0 0 ${headingGap} !important;
      line-height: ${headingLineHeight} !important;
      color: #241b13 !important;
    }
    ${mediaSelectors} {
      max-width: min(100%, ${contentWidth}) !important;
      height: auto !important;
    }
    pre, code {
      white-space: pre-wrap !important;
      word-break: break-word !important;
    }
    body > img,
    body > svg,
    body > video,
    body > canvas,
    body > figure {
      display: block !important;
    }
    .singlePage { padding-bottom: ${Math.max(28, state.fontSize + 8)}px !important; }
  `;
};

const applyDefaultIframeStyle = (doc) => {
  if (!doc || !doc.head) return;
  const styleId = 'recoll-reader-base-style';
  let style = doc.getElementById(styleId);
  if (!style) {
    style = doc.createElement('style');
    style.id = styleId;
    doc.head.appendChild(style);
  }
  const contentInset = getContentInset();
  style.textContent = `html,body{background:transparent !important;overflow-x:hidden;}html,body,body *,table,img,div,pre,code,td,th,tr,tbody,thead,tfoot,table[width],td[width],th[width]{max-width:100% !important;box-sizing:border-box !important;word-wrap:break-word;overflow-wrap:break-word;}p,empty-line{display: inherit;margin-block-start: inherit;margin-block-end: inherit;margin-inline-start: inherit;margin-inline-end: inherit;}body{margin:0px;padding-left:${contentInset.left};padding-right:${contentInset.right};}`;
};

const applyScrollLayout = (doc) => {
  if (!doc || !doc.body) return;
  const contentInset = getContentInset();
  doc.body.setAttribute('style', `width: 100%;height: auto;overflow-x: hidden;overflow-y: visible;padding-left:${contentInset.left};padding-right:${contentInset.right};padding-top: 0px;padding-bottom: 0px;margin: 0px;box-sizing: border-box;max-width: 100%;`);
  bindScrollContentAutosize(doc);
  scheduleScrollIframeHeightSync(doc, 0);
};

const applyPagedLayout = (rendition, doc) => {
  if (!rendition || !doc || !doc.body || rendition.readerMode === 'scroll') return;
  const contentInset = getContentInset();
  const isVertical = typeof rendition.isVertical === 'function'
    ? rendition.isVertical()
    : (rendition.textOrientation === 'vertical' && rendition.readerMode !== 'scroll');
  if (isVertical) {
    const rawGap = Math.floor(stage.clientHeight / 12);
    const gap = Math.max(18, rawGap % 2 === 0 ? rawGap : rawGap - 1);
    doc.body.setAttribute('style', `writing-mode: vertical-rl; text-orientation: mixed;height: ${stage.clientHeight}px;overflow-y: hidden;overflow-x: hidden;padding-left:${contentInset.left};padding-right:${contentInset.right};margin: 0px;box-sizing: border-box;touch-action:none; overscroll-behavior: none;max-width: inherit;column-fill: auto;column-gap: ${gap}px;column-width: ${Math.max(260, stage.clientHeight - gap)}px;`);
    return;
  }
  const rawGap = Math.floor(stage.clientWidth / 12);
  const gap = Math.max(24, rawGap % 2 === 0 ? rawGap : rawGap - 1);
  doc.body.setAttribute('style', `height: 100%;overflow-y: hidden;overflow-x: hidden;padding-left:${contentInset.left};padding-right:${contentInset.right};margin: 0px;box-sizing: border-box;touch-action:none; overscroll-behavior: none;max-width: inherit;column-fill: auto;column-gap: ${gap}px;column-width: ${Math.max(320, stage.clientWidth - gap)}px;`);
};

const applyPdfLayout = (rendition, doc) => {
  if (!rendition || !doc || !doc.body || rendition.readerMode === 'scroll') return;
  const contentInset = getContentInset();
  const rawGap = Math.floor(stage.clientWidth / 12);
  const gap = Math.max(24, rawGap % 2 === 0 ? rawGap : rawGap - 1);
  doc.body.setAttribute('style', `height: 100%;overflow-y: hidden;overflow-x: hidden;padding-left:${contentInset.left};padding-right:${contentInset.right};margin: 0px;box-sizing: border-box;touch-action: manipulation; overscroll-behavior: none;max-width: inherit;column-fill: auto;column-gap: ${gap}px;column-width: ${Math.max(320, stage.clientWidth - gap)}px;`);
};

const applyReaderStyle = () => {
  if (!state.rendition || typeof state.rendition.setStyle !== 'function') return;
  state.rendition.setStyle(buildReaderStyle());
};

const syncRenditionLayout = async (force = false) => {
  if (state.layoutSyncInFlight) {
    state.layoutSyncQueued = true;
    return;
  }
  if (state.isOpening) return;
  const { width, height } = getStageSize();
  if (!width || !height) return;
  if (!force && width === state.lastStageWidth && height === state.lastStageHeight) return;
  if (state.current && isChmFormat(state.current.format)) {
    const iframe = getActiveContentFrame();
    if (iframe) {
      handleChmFrameLoad(iframe);
    }
    rememberStageSize();
    return;
  }
  if (!state.rendition) return;

  state.layoutSyncInFlight = true;
  try {
    const rendition = state.rendition;
    const doc = typeof rendition.getDocument === 'function' ? rendition.getDocument() : null;
    if (!doc) {
      rememberStageSize();
      return;
    }

    applyDefaultIframeStyle(doc);
    if (rendition.readerMode === 'scroll') {
      stage.style.height = '';
      applyScrollLayout(doc);
    } else if (safeText(rendition.format).toUpperCase() === 'PDF') {
      syncPagedStageViewport();
      rendition.pdfScale = 0;
      applyPdfLayout(rendition, doc);
    } else {
      syncPagedStageViewport();
      applyPagedLayout(rendition, doc);
    }
    applyReaderStyle();
    bindIframeNavigation();
    rememberStageSize();
    if (isEpubWindowedMode()) {
      await syncEpubMountedWindow(force);
    }
  } catch (err) {
    console.error('reader layout sync failed', err);
  } finally {
    state.layoutSyncInFlight = false;
    if (state.layoutSyncQueued) {
      state.layoutSyncQueued = false;
      window.clearTimeout(state.layoutSyncTimer);
      state.layoutSyncTimer = window.setTimeout(() => {
        void syncRenditionLayout(force);
      }, 80);
    }
  }
};

const scheduleLayoutSync = (force = false) => {
  if (!stage) return;
  if (isPagedReaderMode(state.readerMode)) {
    syncPagedStageViewport();
  }
  const { width, height } = getStageSize();
  if (!force && width === state.lastStageWidth && height === state.lastStageHeight) return;
  window.clearTimeout(state.layoutSyncTimer);
  state.layoutSyncTimer = window.setTimeout(() => {
    void syncRenditionLayout(force);
  }, 80);
};

const applyFontSizeChange = async (nextFontSize) => {
  const fontSize = clampFontSize(nextFontSize);
  if (fontSize === state.fontSize) {
    updateFontSizeUi();
    return;
  }
  state.fontSize = fontSize;
  persistFontSize();
  updateFontSizeUi();
  if (state.current && isChmFormat(state.current.format)) {
    const iframe = getActiveContentFrame();
    if (iframe) {
      handleChmFrameLoad(iframe);
    }
    return;
  }
  if (!state.rendition) return;
  await syncRenditionLayout(true);
  if (isEpubWindowedMode()) {
    await syncEpubMountedWindow(true);
  }
};

const scheduleViewportModeSync = () => {
  window.clearTimeout(state.viewportSyncTimer);
  state.viewportSyncTimer = window.setTimeout(() => {
    if (!state.current || state.isOpening) return;
    const viewportConfig = getViewportReaderConfig();
    if (viewportConfig.readerMode === state.readerMode && viewportConfig.isMobile === state.mobileFlag) {
      return;
    }
    const position = isChmFormat(state.current.format)
      ? getChmCurrentPosition()
      : (state.rendition && typeof state.rendition.getPosition === 'function'
        ? state.rendition.getPosition()
        : null);
    if (position && state.current && state.current.path) {
      state.pendingRestorePosition = { path: state.current.path, position };
    }
    void openIndex(state.currentIndex);
  }, 140);
};

const openIndex = async (index) => {
  if (!state.items.length) return;
  persistCurrentPosition();
  const openToken = ++state.openToken;
  const nextIndex = Math.max(0, Math.min(index, state.items.length - 1));
  const item = state.items[nextIndex];
  const viewportConfig = getViewportReaderConfig();
  state.currentIndex = nextIndex;
  state.current = item;
  state.isOpening = true;
  state.readerMode = viewportConfig.readerMode;
  state.mobileFlag = viewportConfig.isMobile;
  syncReaderModeUi();
  updateMeta();
  cleanupRendition();
  renderSidebar();
  updatePageButtons();
  setLoading(`正在打开 ${item.name || item.title || ''}`);
  try {
    if (isChmFormat(item.format)) {
      const pendingRestore = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
        ? state.pendingRestorePosition.position
        : null;
      if (pendingRestore && isValidChmPosition(pendingRestore)) {
        state.currentChmRestore = pendingRestore;
        state.pendingRestorePosition = null;
      }
      await openChmDocument(item);
      if (openToken !== state.openToken) return;
      bindStageNavigation();
      bindIframeNavigation();
      rememberStageSize();
      return;
    }
    const response = await fetch(item.url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`读取文件失败: ${response.status}`);
    if (isEpubFormat(item.format)) {
      const rendition = await openEpubScrollDocument(item, viewportConfig);
      if (openToken !== state.openToken) return;
      state.rendition = rendition;
      window.rendition = rendition;
      updatePageButtons();
      bindStageNavigation();
      bindIframeNavigation();
      rememberStageSize();
      state.isOpening = false;
      return;
    }
    const Kookit = window.Kookit;
    const options = {
      format: item.format.toUpperCase(),
      readerMode: viewportConfig.readerMode,
      charset: item.format === 'txt' ? (item.charset || 'utf-8') : undefined,
      animation: '',
      convertChinese: 'no',
      fullTranslationMode: 'no',
      textOrientation: 'horizontal',
      parserRegex: item.format === 'txt' ? (state.parserRegex || '') : '',
      isDarkMode: 'no',
      isMobile: viewportConfig.isMobile,
      password: '',
      isScannedPDF: 'no',
      backgroundColor: '',
      isConvertPDF: 'no',
      ocrLang: '',
      ocrEngine: 'paddle',
      isAllowScript: 'yes',
      isBionic: 'no',
      isIndent: 'no',
      isHyphenation: 'no',
      isStartFromEven: 'no',
      scale: 1,
    };
    const rendition = window.BookHelper.getRendition(buffer, options, Kookit);
    if (openToken !== state.openToken) return;
    state.rendition = rendition;
    window.rendition = rendition;
    updatePageButtons();
    const savedPosition = loadPosition(item);
    await rendition.renderTo(stage);
    if (openToken !== state.openToken) return;
    const renderedDoc = typeof rendition.getDocument === 'function' ? rendition.getDocument() : null;
    if (renderedDoc) {
      applyDefaultIframeStyle(renderedDoc);
      if (rendition.readerMode === 'scroll') {
        stage.style.height = '';
        applyScrollLayout(renderedDoc);
      } else if (safeText(rendition.format).toUpperCase() === 'PDF') {
        syncPagedStageViewport();
        rendition.pdfScale = 0;
        applyPdfLayout(rendition, renderedDoc);
      } else {
        syncPagedStageViewport();
        applyPagedLayout(rendition, renderedDoc);
      }
    }
    applyReaderStyle();
    bindStageNavigation();
    bindIframeNavigation();
    if (isEpubFormat(item.format)) {
      await syncEpubMountedWindow(true);
    }
    const pendingRestore = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
      ? state.pendingRestorePosition.position
      : null;
    if (pendingRestore && typeof rendition.goToPosition === 'function' && !isEpubFormat(item.format)) {
      state.pendingRestorePosition = null;
      await rendition.goToPosition(JSON.stringify(resolveInitialPagedPosition(rendition, pendingRestore)));
    } else if (canRestoreSavedPosition(item, savedPosition) && typeof rendition.goToPosition === 'function' && !isEpubFormat(item.format)) {
      await rendition.goToPosition(JSON.stringify(resolveInitialPagedPosition(rendition, savedPosition)));
    } else if (normalizeFormat(item.format) === 'txt' && typeof rendition.goToPosition === 'function') {
      await rendition.goToPosition(JSON.stringify({
        text: '',
        chapterTitle: '',
        chapterDocIndex: 0,
        chapterHref: item.href || 'title0',
        count: '',
        page: '',
        percentage: '0',
      }));
    } else if (isPagedReaderMode(rendition.readerMode) && typeof rendition.goToPosition === 'function') {
      const defaultPagedPosition = buildDefaultPagedPosition(rendition);
      if (defaultPagedPosition) {
        await rendition.goToPosition(JSON.stringify(defaultPagedPosition));
      }
    }
    if (!isEpubFormat(item.format)) {
      const progressBadge = document.createElement('div');
      progressBadge.className = 'reader-progress';
      progressBadge.textContent = '0%';
      stage.appendChild(progressBadge);
      const updateProgress = async () => {
        try {
          const progress = await rendition.getProgress();
          const position = rendition.getPosition ? rendition.getPosition() : {};
          progressBadge.textContent = progress && typeof progress.percentage !== 'undefined' ? `${Math.round(progress.percentage * 100)}%` : '阅读中';
          savePosition(item, Object.assign({}, position || {}, { progress }));
          if (state.folderMeta) {
            saveFolderCursor(state.folderMeta);
          }
        } catch (err) {}
      };
      const syncWindowAndProgress = () => {
        void updateProgress();
      };
      rendition.on('rendered', syncWindowAndProgress);
      rendition.on('page-changed', syncWindowAndProgress);
      rendition.on('chapter-changed', syncWindowAndProgress);
      await updateProgress();
    }
    rememberStageSize();
  } catch (err) {
    console.error(err);
    setError((err && err.message) || '打开阅读器失败');
  } finally {
    if (openToken === state.openToken) {
      state.isOpening = false;
      rememberStageSize();
    }
  }
};

const loadBookMode = async () => {
  const data = await fetchJson(`${baseApi}/book?${searchParams.toString()}`);
  const book = data.book;
  state.items = [book];
  state.currentIndex = 0;
  state.current = book;
  renderSidebar();
  updateMeta();
  await openIndex(0);
};

const loadFolderMode = async () => {
  const data = await fetchJson(`${baseApi}/folder?${searchParams.toString()}`);
  state.folderMeta = data;
  state.items = data.items || [];
  const saved = JSON.parse(localStorage.getItem('recoll-reader:folder') || 'null');
  state.currentIndex = saved && typeof saved.currentIndex === 'number'
    ? Math.min(saved.currentIndex, Math.max(0, state.items.length - 1))
    : (data.currentIndex || 0);
  state.current = state.items[state.currentIndex] || state.items[0] || null;
  renderSidebar();
  updateMeta();
  if (data.truncated) {
    metaEl.textContent += ` · 已截断至 ${state.items.length} 本`;
  }
  await openIndex(state.currentIndex);
};

el('reader-toggle-toc').addEventListener('click', () => {
  if (sidebar.classList.contains('is-hidden')) {
    openSidebar();
  } else {
    closeSidebar();
  }
  window.requestAnimationFrame(() => {
    syncSidebarState();
    scheduleLayoutSync(true);
  });
});

if (sidebarBackdrop) {
  sidebarBackdrop.addEventListener('click', () => {
    closeSidebar();
    window.requestAnimationFrame(() => {
      scheduleLayoutSync(true);
    });
  });
}

if (toolbarToggleButton) {
  toolbarToggleButton.addEventListener('click', () => {
    toggleToolbar();
  });
}

if (toolbarPeekButton) {
  toolbarPeekButton.addEventListener('click', () => {
    setToolbarHidden(false);
  });
}

if (fontDecreaseButton) {
  fontDecreaseButton.addEventListener('click', () => {
    void applyFontSizeChange(state.fontSize - FONT_SIZE_STEP);
  });
}

if (fontIncreaseButton) {
  fontIncreaseButton.addEventListener('click', () => {
    void applyFontSizeChange(state.fontSize + FONT_SIZE_STEP);
  });
}

el('reader-prev-book').addEventListener('click', () => {
  if (state.items.length > 1) openIndex(state.currentIndex - 1);
});

el('reader-next-book').addEventListener('click', () => {
  if (state.items.length > 1) openIndex(state.currentIndex + 1);
});

prevPageButton.addEventListener('click', () => {
  void turnPage(-1);
});

nextPageButton.addEventListener('click', () => {
  void turnPage(1);
});

el('reader-save-regex').addEventListener('click', () => {
  state.parserRegex = parserInput.value.trim();
  localStorage.setItem('recoll-reader:parserRegex', state.parserRegex);
  if (state.current && state.current.format === 'txt') {
    void openIndex(state.currentIndex);
  }
});

window.addEventListener('beforeunload', () => {
  persistCurrentPosition();
  if (state.folderMeta) saveFolderCursor(state.folderMeta);
});

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && isMobileViewport() && !sidebar.classList.contains('is-hidden')) {
    closeSidebar();
    scheduleLayoutSync(true);
    return;
  }
  if (event.key.toLowerCase() === 'h' && shouldHandleStageKey(event)) {
    event.preventDefault();
    toggleToolbar();
    return;
  }
  if (!shouldHandleStageKey(event)) return;
  if (isPagedReaderMode(state.readerMode) && (event.key === 'ArrowLeft' || event.key === 'PageUp')) {
    event.preventDefault();
    void turnPage(-1);
  } else if (isPagedReaderMode(state.readerMode) && (event.key === 'ArrowRight' || event.key === 'PageDown')) {
    event.preventDefault();
    void turnPage(1);
  } else if (!isPagedReaderMode(state.readerMode) && (event.key === 'PageUp' || (event.key === ' ' && event.shiftKey))) {
    event.preventDefault();
    scrollReadingViewport(-1);
  } else if (!isPagedReaderMode(state.readerMode) && (event.key === 'PageDown' || event.key === ' ')) {
    event.preventDefault();
    scrollReadingViewport(1);
  }
});

if (typeof ResizeObserver === 'function') {
  const resizeObserver = new ResizeObserver(() => {
    syncSidebarState();
    scheduleLayoutSync();
    scheduleViewportModeSync();
    scheduleEpubContextWindowSync();
  });
  if (stage) {
    resizeObserver.observe(stage);
  }
}

window.addEventListener('resize', () => {
  syncSidebarState();
  scheduleLayoutSync();
  scheduleViewportModeSync();
  scheduleEpubContextWindowSync();
});

window.addEventListener('orientationchange', () => {
  syncSidebarState();
  scheduleLayoutSync(true);
  scheduleViewportModeSync();
  scheduleEpubContextWindowSync(true);
});

(async () => {
  try {
    updateFontSizeUi();
    if (!window.BookHelper) {
      const mod = await import('/static/reader/kookit.bundle.js');
      const exported = mod && mod.default ? mod.default : mod;
      window.Kookit = window.Kookit && Object.keys(window.Kookit).length ? window.Kookit : exported;
      window.BookHelper = window.BookHelper || (exported && exported.BookHelper);
      window.StyleHelper = window.StyleHelper || (exported && exported.StyleHelper);
    }
    if (!window.BookHelper) {
      throw new Error('Koodo reader runtime failed to initialize');
    }
    if (mode === 'folder') {
      await loadFolderMode();
    } else {
      await loadBookMode();
    }
    syncReaderModeUi();
    syncSidebarState();
    bindStageNavigation();
    syncToolbarToggleLabel();
  } catch (err) {
    console.error(err);
    setError((err && err.message) || '初始化阅读器失败');
  }
})();

updateFontSizeUi();
syncReaderModeUi();
updatePageButtons();
