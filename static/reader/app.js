import { ReaderAccountSync, sectionReadingProgress } from './account-sync.js';
import { ReaderSettings } from './settings.js';
import { captureTextAnchor, restoreTextAnchor } from './anchors.js';
import { ReaderNavigation, scrollToTextMatch } from './navigation.js';
import { ReaderExperience } from './experience.js';
import { ChmSandboxReader } from './chm-adapter.js';

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
const baseApi = readerConfig.apiBase || '/api/reader';
const account = readerConfig.account || null;
const applicationBase = ((account && (account.baseUrl || account.baseURL)) || baseApi.replace(/\/api\/reader\/?$/, '')).replace(/\/$/, '');
const DEFAULT_FONT_SIZE = 20;
const MIN_FONT_SIZE = 16;
const MAX_FONT_SIZE = 32;
const FONT_SIZE_STEP = 2;
const SCROLL_READER_FORMATS = new Set(['txt', 'md', 'html', 'htm', 'xhtml', 'xml', 'mhtml', 'epub', 'pdf']);
const TXT_MOUNT_WINDOW_BEFORE = 2;
const TXT_MOUNT_WINDOW_AFTER = 2;
const TXT_PRELOAD_WINDOW_BEFORE = 4;
const TXT_PRELOAD_WINDOW_AFTER = 4;
const EPUB_MOUNT_WINDOW_BEFORE = 1;
const EPUB_MOUNT_WINDOW_AFTER = 1;
const EPUB_PRELOAD_WINDOW_BEFORE = 2;
const EPUB_PRELOAD_WINDOW_AFTER = 2;
const DOCUMENT_CSP = "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; media-src 'self' blob:; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'self'; form-action 'none'";
const readStoredFontSize = () => {
  const parsed = Number(account && account.settings && account.settings.preferences && account.settings.preferences.font_size);
  return Number.isFinite(parsed) && parsed >= MIN_FONT_SIZE
    ? Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(parsed))) : DEFAULT_FONT_SIZE;
};
const state = {
  items: [],
  currentIndex: 0,
  current: null,
  rendition: null,
  currentChmManifest: null,
  currentChmHref: '',
  currentChmRestore: null,
  chmSandbox: null,
  folderMeta: null,
  bookMeta: null,
  currentBlobUrl: '',
  pdfLoadingTask: null,
  parserRegex: '',
  fontSize: readStoredFontSize(),
  isOpening: false,
  layoutSyncTimer: 0,
  layoutSyncInFlight: false,
  layoutSyncQueued: false,
  lastStageWidth: 0,
  lastStageHeight: 0,
  scrollHeightTimer: 0,
  genericRecordTimer: 0,
  openToken: 0,
  sidebarInitialized: false,
  readerMode: '',
  mobileFlag: 'no',
  viewportSyncTimer: 0,
  pendingRestorePosition: null,
  openRestorePosition: null,
  temporaryPosition: null,
  lastTextAnchor: null,
  layoutRestoreAnchor: null,
  layoutPending: false,
  reflowing: false,
  initialSearchHandled: false,
  navigationToken: 0,
  chmNavigationToken: 0,
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
  txtWindow: {
    syncTimer: 0,
    progressTimer: 0,
    syncToken: 0,
    documentToken: 0,
    currentIndex: -1,
    mountedRange: null,
    preloadRange: null,
    loaded: new Set(),
    loading: new Map(),
    mounted: new Map(),
    chunkData: new Map(),
    slots: [],
    slotHeights: new Map(),
    averageHeightPerByte: 0.026,
    restorePosition: null,
    progressBadge: null,
    manifest: null,
    contentUrl: '',
    charset: 'utf-8',
    chunkCount: 0,
    chunkWindowMounted: TXT_MOUNT_WINDOW_BEFORE,
    chunkWindowPrefetch: TXT_PRELOAD_WINDOW_BEFORE,
  },
};

let readerSettings;
let readerNavigation;
let readerExperience;

window.Kookit = window.Kookit || {};
const ensureKookitRuntime = async () => {
  if (!window.BookHelper) {
    const mod = await import('./kookit.bundle.js');
    const exported = mod?.default || mod;
    window.Kookit = Object.keys(window.Kookit).length ? window.Kookit : exported;
    window.BookHelper = exported?.BookHelper;
    window.StyleHelper = exported?.StyleHelper;
  }
  if (!window.BookHelper) throw new Error('Koodo reader runtime failed to initialize');
};

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
const sidebarHeader = sidebar ? sidebar.querySelector('.reader-sidebar-header') : null;
parserInput.value = state.parserRegex;

const syncStatus = document.createElement('div');
syncStatus.className = 'reader-meta';
syncStatus.setAttribute('role', 'status');
syncStatus.setAttribute('aria-live', 'polite');
const syncMessage = document.createElement('span');
if (readerConfig.shared) syncMessage.textContent = '分享阅读 · 无需登录';
syncStatus.appendChild(syncMessage);
const adoptSavedButton = document.createElement('button');
adoptSavedButton.type = 'button';
adoptSavedButton.className = 'reader-button';
adoptSavedButton.textContent = '采用已保存位置';
const continueHereButton = document.createElement('button');
continueHereButton.type = 'button';
continueHereButton.className = 'reader-button';
continueHereButton.textContent = '从当前继续';
syncStatus.append(adoptSavedButton, continueHereButton);
adoptSavedButton.hidden = continueHereButton.hidden = true;
if (metaEl && metaEl.parentNode) metaEl.parentNode.appendChild(syncStatus);
const showReaderError = (message) => {
  if (accountSync.stopped) return;
  syncStatus.dataset.status = 'error'; syncMessage.textContent = message;
};

const shelfButton = document.createElement('button');
shelfButton.type = 'button';
shelfButton.className = 'reader-button reader-shelf-button';
shelfButton.textContent = mode === 'folder' ? '加入文件夹' : '加入书架';
shelfButton.disabled = true;
const shareButton = document.createElement('a');
shareButton.className = 'reader-button reader-share-button';
shareButton.textContent = mode === 'folder' ? '分享文件夹' : '分享此书';
shareButton.title = shareButton.textContent;
shareButton.setAttribute('aria-label', shareButton.textContent);
shareButton.hidden = true;
if (account && !readerConfig.shared) toolbar.querySelector('.reader-toolbar-right')?.prepend(shelfButton, shareButton);
const pendingShelfUpdates = new Set();
const getShelfTarget = () => mode === 'folder' ? state.folderMeta?.shelf : state.current;
const updateShelfButton = () => {
  const target = getShelfTarget();
  const isFolder = mode === 'folder';
  shelfButton.textContent = isFolder
    ? (target?.onShelf ? '移出文件夹' : '加入文件夹')
    : (target?.onShelf ? '移出书架' : '加入书架');
  shelfButton.title = isFolder ? (target?.onShelf ? '从书架移出此文件夹' : '将此文件夹加入书架') : shelfButton.textContent;
  shelfButton.setAttribute('aria-label', shelfButton.title);
  shelfButton.disabled = !target?.documentId || !target?.openHandle || !account || accountSync.stopped
    || pendingShelfUpdates.has(target.documentId);
  shareButton.hidden = !account || !!readerConfig.shared || !target?.documentId || accountSync.stopped;
  if (!shareButton.hidden) shareButton.href = `${applicationBase}/shares/new?document_id=${encodeURIComponent(target.documentId)}`;
  else shareButton.removeAttribute('href');
};

const clearAccountReader = (message = '登录状态已改变，请重新登录后打开文档。') => {
  accountSync.stop();
  state.openToken += 1;
  state.pendingRestorePosition = null;
  state.temporaryPosition = null;
  state.lastTextAnchor = null;
  state.openRestorePosition = null;
  state.layoutRestoreAnchor = null;
  state.layoutPending = false;
  readerSettings?.setOpen(false);
  readerNavigation?.reset?.();
  document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
  state.isOpening = false;
  cleanupRendition();
  state.items = [];
  state.current = null;
  state.folderMeta = null;
  window.rendition = null;
  booklist.replaceChildren();
  titleEl.textContent = '阅读已暂停';
  metaEl.textContent = '';
  syncMessage.textContent = message;
  adoptSavedButton.hidden = continueHereButton.hidden = true;
  shelfButton.disabled = true;
  shareButton.hidden = true;
  shareButton.removeAttribute('href');
  updateNavigationControls();
  setError(message);
};

const accountSync = new ReaderAccountSync({ account, apiBase: baseApi,
  onExpired: () => clearAccountReader(),
  onStatus: ({ type, message }) => {
    syncStatus.dataset.status = type;
    syncMessage.textContent = message;
    adoptSavedButton.hidden = type !== 'conflict';
    continueHereButton.hidden = type !== 'conflict' && type !== 'version-changed';
  },
  onSettings: (settings) => { if (account) account.settings = settings; },
});

adoptSavedButton.addEventListener('click', async () => {
  const current = state.current;
  const position = await accountSync.resolve(false);
  if (!current || current !== state.current || accountSync.stopped) return;
  if (position) state.pendingRestorePosition = { path: current.path, position };
  await openIndex(state.currentIndex);
});
continueHereButton.addEventListener('click', async () => {
  persistCurrentPosition();
  await accountSync.resolve(true);
});
shelfButton.addEventListener('click', async () => {
  const item = getShelfTarget();
  if (!item || shelfButton.disabled) return;
  pendingShelfUpdates.add(item.documentId);
  updateShelfButton();
  const onShelf = !!item.onShelf;
  try {
    const data = await accountSync.request(`${applicationBase}/api/bookshelf/${onShelf ? 'remove' : 'add'}`, {
      body: onShelf ? { document_id: item.documentId } : { handle: item.openHandle },
    });
    item.onShelf = !!data.onShelf;
    if (getShelfTarget() === item) {
      const subject = mode === 'folder' ? '文件夹' : '';
      syncMessage.textContent = item.onShelf ? `${subject}已加入书架` : `${subject}已移出书架`;
    }
    window.RecollAccount?.broadcast?.('bookshelf-changed');
  } catch (error) {
    if (onShelf && error.status === 404) {
      // Another tab may already have removed this entry from the bookshelf.
      item.onShelf = false;
      if (getShelfTarget() === item) syncMessage.textContent = mode === 'folder' ? '文件夹已移出书架' : '已移出书架';
      window.RecollAccount?.broadcast?.('bookshelf-changed');
    } else if (getShelfTarget() === item) {
      showReaderError(`书架未更新：${error.message}`);
    }
  } finally {
    pendingShelfUpdates.delete(item.documentId);
    updateShelfButton();
  }
});

const safeText = (value) => (value == null ? '' : String(value));
const escapeHtml = (value) => safeText(value)
  .split('&').join('&amp;')
  .split('<').join('&lt;')
  .split('>').join('&gt;')
  .split('"').join('&quot;');
const normalizeFormat = (format) => safeText(format).trim().toLowerCase();
const isChmFormat = (format) => normalizeFormat(format) === 'chm';
const isEpubFormat = (format) => normalizeFormat(format) === 'epub';
const isTxtFormat = (format) => normalizeFormat(format) === 'txt';
const getReadingFraction = (index, ratio, count) => sectionReadingProgress(index, ratio, count,
  !!stage && stage.scrollTop > 0 && stage.scrollTop + stage.clientHeight >= stage.scrollHeight - 2);
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
const syncViewportHeight = () => {
  const viewport = window.visualViewport;
  // Pinch zoom changes the visible area without changing the reader layout.
  if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
  const height = Math.floor((viewport && viewport.height) || window.innerHeight);
  if (height <= 0) return;
  const value = `${height}px`;
  const root = document.documentElement;
  if (root.style.getPropertyValue('--reader-viewport-height') !== value) {
    root.style.setProperty('--reader-viewport-height', value);
  }
};
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

const getGenericContentHeight = (doc) => {
  if (!doc?.body) return 0;
  const range = doc.createRange();
  range.selectNodeContents(doc.body);
  // scrollHeight is at least the iframe viewport height. Including it while
  // adding a bottom inset would repeatedly enlarge an already full-height
  // iframe; measure the actual content boxes instead.
  return Math.max(0, range.getBoundingClientRect().bottom - doc.body.getBoundingClientRect().top);
};

const syncScrollIframeHeight = (doc) => {
  if (!doc || !stage || !state.current || isChmFormat(state.current.format)) return;
  const iframe = getActiveContentFrame();
  if (!iframe || iframe.id !== 'kookit-iframe') return;
  const contentHeight = getGenericContentHeight(doc);
  if (!contentHeight) return;
  const nextHeight = Math.max(contentHeight + 96, stage.clientHeight);
  iframe.style.height = `${nextHeight}px`;
  iframe.style.minHeight = `${nextHeight}px`;
};

const installGenericScrollPosition = (rendition) => {
  if (rendition.readerMode !== 'scroll' || typeof rendition.getPosition !== 'function') return;
  const nativePosition = rendition.getPosition.bind(rendition);
  const nativeProgress = rendition.getProgress?.bind(rendition);
  rendition.getPosition = () => {
    const position = nativePosition() || {};
    // Kookit's cached paragraph is affected by its previous native location.
    // Derive the text from the visible DOM so reopening the same viewport
    // produces the same semantic position as scrolling there.
    const anchor = captureTextAnchor(stage, getReadingDocuments());
    let textNode = rendition.getDocument?.()?.body;
    for (const index of anchor?.nodePath || []) textNode = textNode?.childNodes[index];
    const text = anchor && textNode?.nodeType === 3 ? textNode.textContent.trim().slice(0, 1024) : position.text;
    const height = Math.max(1, getGenericContentHeight(rendition.getDocument?.()));
    const offset = Math.max(0, stage.scrollTop);
    const ratio = Math.min(1, offset / height);
    return { ...position, text, ...(anchor ? { textAnchor: anchor } : {}), intraChapterOffset: offset, chapterProgressRatio: ratio, scrollTop: offset,
      percentage: String(getReadingFraction(Number(position.chapterDocIndex) || 0, ratio, rendition.chapterDocList?.length || 1)) };
  };
  rendition.getProgress = async () => ({ ...(await nativeProgress?.()), percentage: Number(rendition.getPosition().percentage) });
};

const restoreGenericScrollPosition = async (rendition, position, openToken) => {
  if (rendition.readerMode !== 'scroll') return;
  await new Promise((resolve) => requestAnimationFrame(resolve));
  if (openToken !== state.openToken || accountSync.stopped) return;
  const doc = rendition.getDocument?.();
  if (!doc?.body) return;
  syncScrollIframeHeight(doc);
  let offset = 0;
  if (Number.isFinite(Number(position?.chapterProgressRatio))) {
    offset = Number(position.chapterProgressRatio) * getGenericContentHeight(doc);
  } else if (Number.isFinite(Number(position?.intraChapterOffset ?? position?.scrollTop))) {
    offset = Number(position.intraChapterOffset ?? position.scrollTop);
  } else if (position?.xpath || position?.text) {
    // Older Kookit positions contain a native paragraph path. Resolve it only
    // after the iframe has its full height, rather than scrolling its initial
    // small internal viewport and losing that scroll during expansion.
    const path = String(position.xpath || '').replace(/^\/body\/DocFragment\[\d+\]/, '');
    let node = null;
    try {
      if (path) node = doc.evaluate(`/html${path}`, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    } catch (error) { /* An obsolete native path falls back to the chapter start. */ }
    if (!node && position.text) {
      node = [...doc.body.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,div,blockquote,pre')]
        .find((element) => element.textContent.trim().startsWith(String(position.text).trim()));
    }
    if (node?.getBoundingClientRect) offset = node.getBoundingClientRect().top + (doc.defaultView?.scrollY || 0);
  }
  doc.defaultView?.scrollTo(0, 0);
  stage.scrollTop = Math.max(0, offset);
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

// Slots that could still hold content from the previous window. Releasing every slot of
// the book instead costs one forced layout per chapter (clearing a slot re-reads the
// browser metrics), which freezes the page for tens of seconds on books with thousands
// of chapters.
const collectActiveSlotIndexes = (ranges, mountedIndexes) => {
  const indexes = new Set();
  for (const range of ranges) {
    if (!range || !Number.isFinite(range.start) || !Number.isFinite(range.end)) continue;
    for (let i = Math.max(0, range.start); i <= range.end; i += 1) {
      indexes.add(i);
    }
  }
  for (const index of mountedIndexes) {
    indexes.add(Number(index));
  }
  return indexes;
};

const getEpubAnchorFromPosition = (position) => {
  if (!position || !state.rendition || !getEpubChapterDocList().length) return null;
  const chapterIndex = Number(position.chapterDocIndex);
  if (!Number.isFinite(chapterIndex) || chapterIndex < 0) return null;
  return {
    chapterDocIndex: chapterIndex,
    offset: Number(position.intraChapterOffset ?? position.scrollTop ?? -1),
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
  await fetchReaderResponse(url, { method: 'HEAD' });
  if (token !== state.epubWindow.syncToken) return;
  slot.innerHTML = '';
  const iframe = document.createElement('iframe');
  iframe.className = 'reader-epub-slot-frame';
  iframe.setAttribute('scrolling', 'no');
  iframe.setAttribute('sandbox', 'allow-same-origin');
  iframe.setAttribute('title', safeText(chapterDoc.label || chapterDoc.href || `Chapter ${index + 1}`));
  iframe.src = url;
  slot.appendChild(iframe);
  const mounted = { slot, iframe, resizeObserver: null };
  state.epubWindow.mounted.set(index, mounted);
  iframe.addEventListener('load', () => {
    if (state.epubWindow.mounted.get(index) !== mounted) return;
    const doc = iframe.contentDocument;
    if (!doc || !doc.body || doc.contentType === 'application/json') return;
    iframe.dataset.readerContentLoaded = 'yes';
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
      if (!doc?.body || state.epubWindow.mounted.get(index) !== mounted) return;
      const contentHeight = getGenericContentHeight(doc);
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
    const percentage = Math.round(getReadingFraction(currentIndex, chapterProgressRatio, chapterDocList.length) * 100);
    badge.textContent = `${percentage}%`;
  }
  const position = {
    chapterDocIndex: currentIndex,
    chapterHref: safeText((chapterDocList[currentIndex] || {}).href),
    chapterTitle: safeText((chapterDocList[currentIndex] || {}).label),
    percentage: String(getReadingFraction(currentIndex, chapterProgressRatio, chapterDocList.length)),
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

const openEpubScrollDocument = async (item, viewportConfig, openToken) => {
  if (!item || !item.manifestUrl) {
    throw new Error('EPUB 清单地址缺失');
  }
  setLoading(`正在解析 ${item.name || item.title || 'EPUB'} 章节列表…`);
  const manifest = await fetchJson(item.manifestUrl);
  if (openToken !== state.openToken) return null;
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
  rendition.getPosition = () => {
    const index = state.epubWindow.currentIndex;
    const slot = state.epubWindow.slots[index];
    const offset = slot ? Math.max(0, Math.round(getEpubChapterOffsetInViewport(slot))) : 0;
    const ratio = slot && slot.offsetHeight > 0 ? Math.min(1, offset / slot.offsetHeight) : 0;
    return {
      chapterDocIndex: index,
      chapterHref: safeText((chapterDocList[index] || {}).href),
      chapterTitle: safeText((chapterDocList[index] || {}).label),
      percentage: String(getReadingFraction(index, ratio, chapterDocList.length)),
      intraChapterOffset: offset,
      chapterProgressRatio: ratio,
      scrollTop: stage ? Math.max(0, Math.round(stage.scrollTop)) : 0,
    };
  };
  rendition.getProgress = async () => ({
    percentage: Number(rendition.getPosition().percentage),
  });
  rendition.goToPosition = async (rawPosition) => {
    const position = typeof rawPosition === 'string' ? JSON.parse(rawPosition) : rawPosition;
    const nextIndex = Math.max(0, Math.min(chapterDocList.length - 1, Number(position && position.chapterDocIndex) || 0));
    state.epubWindow.currentIndex = nextIndex;
    rendition.tempLocation.chapterDocIndex = String(nextIndex);
    rendition.tempLocation.chapterHref = safeText((chapterDocList[nextIndex] || {}).href);
    rendition.tempLocation.chapterTitle = safeText((chapterDocList[nextIndex] || {}).label);
    state.epubWindow.restorePosition = getEpubAnchorFromPosition(position);
    window.clearTimeout(state.epubWindow.syncTimer);
    state.epubWindow.navigating = true;
    try { await syncEpubContextWindow(true); }
    finally { state.epubWindow.navigating = false; }
  };
  rendition.goToChapterDocIndex = async (index) => {
    await rendition.goToPosition({ chapterDocIndex: index, intraChapterOffset: 0 });
  };
  await syncEpubContextWindow(true);
  if (openToken !== state.openToken) return null;
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
  const requestedIndex = Number(state.epubWindow.restorePosition?.chapterDocIndex);
  const currentIndex = Number.isInteger(requestedIndex) && requestedIndex >= 0
    ? Math.min(chapterDocList.length - 1, requestedIndex) : getCurrentEpubChapterIndex();
  if (currentIndex < 0) return;
  const token = ++state.epubWindow.syncToken;
  state.epubWindow.currentIndex = currentIndex;
  const previousMountedRange = state.epubWindow.mountedRange;
  const previousPreloadRange = state.epubWindow.preloadRange;
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
  const activeIndexes = collectActiveSlotIndexes(
    [previousMountedRange, previousPreloadRange],
    state.epubWindow.mounted.keys(),
  );
  for (const i of activeIndexes) {
    if (i < 0 || i >= chapterDocList.length) continue;
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
  if (state.isOpening || state.layoutPending || state.epubWindow.navigating || !state.rendition || !isEpubFormat(state.current && state.current.format) || state.readerMode !== 'scroll') return;
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
  el('reader-toggle-toc')?.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('reader-sidebar-open');
  readerExperience?.syncPanels();
};

const openSidebar = () => {
  sidebar.classList.remove('is-hidden');
  el('reader-toggle-toc')?.setAttribute('aria-expanded', 'true');
  if (isMobileViewport()) {
    document.body.classList.add('reader-sidebar-open');
  }
  readerExperience?.syncPanels();
};

const syncSidebarState = () => {
  if (!isMobileViewport()) {
    if (!state.sidebarInitialized) sidebar.classList.remove('is-hidden');
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
  toolbarToggleButton.setAttribute('aria-label', isToolbarHidden() ? '展开顶栏' : '收起顶栏');
  toolbarToggleButton.setAttribute('aria-expanded', String(!isToolbarHidden()));
  toolbarToggleButton.title = isToolbarHidden() ? '展开顶栏' : '收起顶栏';
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

let fontPreferenceSave = Promise.resolve();
const persistFontSize = () => {
  if (!account || accountSync.stopped) return;
  const fontSize = state.fontSize;
  fontPreferenceSave = fontPreferenceSave.then(() => {
    if (accountSync.stopped) return;
    return accountSync.request(`${applicationBase}/api/account/preferences`, {
      body: { preferences: { font_size: fontSize } },
    });
  }).then(() => {
    if (accountSync.stopped || state.fontSize !== fontSize) return;
    window.RecollAccount?.broadcast?.('settings-changed', { preference: 'font_size' });
  }).catch((error) => {
    showReaderError(`字号尚未保存：${error.message}`);
  });
};

const savePosition = (item, position) => {
  if (state.isOpening || state.reflowing || state.layoutPending || state.temporaryPosition || item !== state.current) return;
  const anchor = captureTextAnchor(stage, getReadingDocuments());
  if (anchor) { state.lastTextAnchor = anchor; position = { ...position, textAnchor: anchor }; }
  updateReaderProgress(position);
  if (isTxtFormat(item?.format) && position && getTxtChunkDocList().length) {
    const count = getTxtChunkDocList().length;
    accountSync.capture(item, { ...position, percentage: String(getReadingFraction(position.chunkIndex, position.chunkProgressRatio, count)) });
  } else {
    accountSync.capture(item, position);
  }
};

const loadPosition = (item) => accountSync.position(item);

const isValidTxtPosition = (position) => {
  if (!position || typeof position !== 'object') return false;
  const chunkIndex = position.chunkIndex ?? position.chapterDocIndex;
  return typeof chunkIndex === 'number' || typeof chunkIndex === 'string';
};

const getActiveContentFrame = () => stage ? stage.querySelector('iframe') : null;

const resetTxtContextWindow = () => {
  window.clearTimeout(state.txtWindow.syncTimer);
  window.clearTimeout(state.txtWindow.progressTimer);
  state.txtWindow.syncToken += 1;
  state.txtWindow.documentToken += 1;
  state.txtWindow.currentIndex = -1;
  state.txtWindow.mountedRange = null;
  state.txtWindow.preloadRange = null;
  state.txtWindow.loaded = new Set();
  state.txtWindow.loading = new Map();
  state.txtWindow.mounted = new Map();
  state.txtWindow.chunkData = new Map();
  state.txtWindow.slots = [];
  state.txtWindow.slotHeights = new Map();
  state.txtWindow.restorePosition = null;
  state.txtWindow.progressBadge = null;
  state.txtWindow.manifest = null;
  state.txtWindow.contentUrl = '';
  state.txtWindow.charset = 'utf-8';
  state.txtWindow.chunkCount = 0;
  if (state.txtWindow.bootstrapHost && state.txtWindow.bootstrapHost.parentNode) {
    state.txtWindow.bootstrapHost.parentNode.removeChild(state.txtWindow.bootstrapHost);
  }
  state.txtWindow.bootstrapHost = null;
};

const getTxtChunkDocList = () => {
  if (!state.txtWindow.manifest) return [];
  return Array.isArray(state.txtWindow.manifest.chunks) ? state.txtWindow.manifest.chunks : [];
};

const isTxtWindowedMode = () => !!(
  state.current
  && isTxtFormat(state.current.format)
  && state.readerMode === 'scroll'
  && state.txtWindow.slots.length
);

const getTxtMountedRange = (index, length) => ({
  start: Math.max(0, index - TXT_MOUNT_WINDOW_BEFORE),
  end: Math.min(length - 1, index + TXT_MOUNT_WINDOW_AFTER),
});

const getTxtPreloadRange = (index, length) => ({
  start: Math.max(0, index - TXT_PRELOAD_WINDOW_BEFORE),
  end: Math.min(length - 1, index + TXT_PRELOAD_WINDOW_AFTER),
});

const getTxtChapterSlot = (index) => state.txtWindow.slots[Number(index) || 0] || null;

const fetchTxtJson = async (url) => fetchJson(url);

const loadTxtChunk = async (chunkDoc, index) => {
  if (!chunkDoc || !state.txtWindow.contentUrl) return;
  if (state.txtWindow.loaded.has(index)) return state.txtWindow.chunkData.get(index);
  if (state.txtWindow.loading.has(index)) return state.txtWindow.loading.get(index);
  const documentToken = state.txtWindow.documentToken;
  const contentUrl = state.txtWindow.contentUrl;
  const loadPromise = Promise.resolve().then(async () => {
    const data = await fetchTxtJson(`${contentUrl}/${index}`);
    if (documentToken !== state.txtWindow.documentToken || contentUrl !== state.txtWindow.contentUrl) {
      return null;
    }
    state.txtWindow.loaded.add(index);
    state.txtWindow.chunkData.set(index, data);
    return data;
  });
  state.txtWindow.loading.set(index, loadPromise);
  try {
    return await loadPromise;
  } finally {
    if (state.txtWindow.loading.get(index) === loadPromise) {
      state.txtWindow.loading.delete(index);
    }
  }
};

const unloadTxtChunk = async (index) => {
  if (!state.txtWindow.loaded.has(index)) return;
  state.txtWindow.loaded.delete(index);
  state.txtWindow.chunkData.delete(index);
};

const estimateTxtSlotHeight = (chunkDoc, index) => {
  const cached = state.txtWindow.slotHeights.get(index);
  if (cached && cached > 0) return cached;
  const byteLength = Math.max(1, Number(chunkDoc && chunkDoc.byteLength) || 0);
  const fontScale = Math.max(0.7, Math.min(1.8, state.fontSize / DEFAULT_FONT_SIZE));
  return Math.max(320, Math.round(byteLength * state.txtWindow.averageHeightPerByte * fontScale));
};

const setTxtSlotPlaceholderHeight = (slot, chunkDoc, index) => {
  if (!slot) return;
  const height = estimateTxtSlotHeight(chunkDoc, index);
  slot.style.minHeight = `${height}px`;
};

const updateTxtAverageHeightPerByte = (index, chunkDoc, measuredHeight) => {
  const byteLength = Math.max(1, Number(chunkDoc && chunkDoc.byteLength) || 0);
  const ratio = measuredHeight / byteLength;
  state.txtWindow.averageHeightPerByte = state.txtWindow.averageHeightPerByte * 0.7 + ratio * 0.3;
  state.txtWindow.slotHeights.set(index, measuredHeight);
};

const buildTxtWindowStage = (chunkDocList) => {
  if (!stage) return;
  stage.innerHTML = '';
  const slots = [];
  for (let i = 0; i < chunkDocList.length; i += 1) {
    const slot = document.createElement('section');
    slot.className = 'reader-txt-slot';
    slot.dataset.chunkIndex = String(i);
    setTxtSlotPlaceholderHeight(slot, chunkDocList[i], i);
    stage.appendChild(slot);
    slots.push(slot);
  }
  state.txtWindow.slots = slots;
};

const applyReaderStyleToMountedTxtSlots = () => {
  const styleText = buildReaderStyle();
  for (const mounted of state.txtWindow.mounted.values()) {
    if (!mounted || !mounted.iframe) continue;
    const doc = mounted.iframe.contentDocument;
    if (!doc || !doc.head) continue;
    let style = doc.getElementById('recoll-reader-txt-style');
    if (!style) {
      style = doc.createElement('style');
      style.id = 'recoll-reader-txt-style';
      doc.head.appendChild(style);
    }
    style.textContent = styleText;
  }
};

const detachTxtMountedChunk = (index) => {
  const mounted = state.txtWindow.mounted.get(index);
  if (!mounted) return;
  if (mounted.resizeObserver) {
    mounted.resizeObserver.disconnect();
  }
  if (mounted.iframe && mounted.iframe.parentNode) {
    mounted.iframe.parentNode.removeChild(mounted.iframe);
  }
  state.txtWindow.mounted.delete(index);
};

const normalizeTxtPosition = (position) => {
  if (!position || typeof position !== 'object') return null;
  const chunkIndex = Number(position.chunkIndex ?? position.chapterDocIndex ?? 0);
  if (!Number.isFinite(chunkIndex) || chunkIndex < 0) return null;
  return {
    chunkIndex,
    chunkProgressRatio: Number.isFinite(Number(position.chunkProgressRatio)) ? Number(position.chunkProgressRatio) : 0,
    scrollTop: Number.isFinite(Number(position.scrollTop)) ? Number(position.scrollTop) : 0,
  };
};

const getTxtAnchorFromPosition = (position) => normalizeTxtPosition(position);

const isTxtLegacyPosition = (position) => {
  if (!position || typeof position !== 'object') return false;
  return Object.prototype.hasOwnProperty.call(position, 'chapterDocIndex')
    || Object.prototype.hasOwnProperty.call(position, 'chapterHref')
    || Object.prototype.hasOwnProperty.call(position, 'chapterTitle')
    || Object.prototype.hasOwnProperty.call(position, 'text');
};

const captureTxtRestorePosition = () => {
  const chunkDocList = getTxtChunkDocList();
  if (!chunkDocList.length) return null;
  const currentIndex = Math.max(0, state.txtWindow.currentIndex >= 0 ? state.txtWindow.currentIndex : 0);
  const slot = getTxtChapterSlot(currentIndex);
  const ratio = slot && slot.offsetHeight > 0
    ? Math.max(0, Math.min(1, stage ? (stage.scrollTop - slot.offsetTop) / Math.max(1, slot.offsetHeight) : 0))
    : 0;
  return {
    chunkIndex: currentIndex,
    chunkProgressRatio: ratio,
    scrollTop: stage ? Math.max(0, Math.round(stage.scrollTop)) : 0,
  };
};

const restoreTxtScrollPosition = (restore) => {
  if (!restore || !stage || !state.txtWindow.slots.length) return;
  const slot = state.txtWindow.slots[Math.max(0, Math.min(state.txtWindow.slots.length - 1, Number(restore.chunkIndex) || 0))];
  if (!slot) return;
  const ratio = Number.isFinite(Number(restore.chunkProgressRatio))
    ? Math.max(0, Math.min(1, Number(restore.chunkProgressRatio)))
    : 0;
  // Callers record progress immediately after restoration. Finish the scroll
  // here so they cannot capture the previously inspected chunk.
  stage.scrollTop = Math.max(0, Math.round(slot.offsetTop + slot.offsetHeight * ratio));
};

const mountTxtChapterIntoSlot = async (slot, chunkDoc, index, token) => {
  if (!slot || !chunkDoc) return;
  const existing = state.txtWindow.mounted.get(index);
  if (existing && existing.slot === slot) return;
  setTxtSlotPlaceholderHeight(slot, chunkDoc, index);
  const data = await loadTxtChunk(chunkDoc, index);
  if (token !== state.txtWindow.syncToken) return;
  slot.innerHTML = '';
  const iframe = document.createElement('iframe');
  iframe.className = 'reader-txt-iframe';
  iframe.setAttribute('sandbox', 'allow-same-origin');
  iframe.setAttribute('loading', 'eager');
  iframe.setAttribute('referrerpolicy', 'same-origin');
  iframe.setAttribute('title', chunkDoc.label || `Chunk ${index + 1}`);
  slot.appendChild(iframe);
  const doc = iframe.contentDocument || iframe.contentWindow && iframe.contentWindow.document;
  if (!doc) return;
  const text = safeText(data && data.text);
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const paragraphs = lines.map((line, lineIndex) => `<p data-reader-txt-line${lineIndex < lines.length - 1 ? ' data-reader-line-break="yes"' : ''}>${escapeHtml(line)}</p>`).join('');
  doc.open();
  doc.write(`<!doctype html><html><head><meta charset="utf-8"></head><body>${paragraphs}</body></html>`);
  doc.close();
  applyDefaultIframeStyle(doc);
  applyScrollLayout(doc);
  bindIframeInteractions(doc, handleReadingKey);
  if (doc.body) {
    doc.body.style.whiteSpace = 'pre-wrap';
    doc.body.style.wordBreak = 'break-word';
    doc.body.style.overflowWrap = 'break-word';
    doc.body.style.lineHeight = String((readerSettings?.values.reader_line_height || 180) / 100);
    doc.body.style.fontSize = `${state.fontSize}px`;
  }
  const updateTxtIframeHeight = () => {
    if (state.txtWindow.mounted.get(index) !== mounted) return;
    const contentHeight = Math.max(getGenericContentHeight(doc), 240);
    const nextHeight = Math.max(320, contentHeight + 16);
    iframe.style.height = `${nextHeight}px`;
    iframe.style.minHeight = `${nextHeight}px`;
    slot.style.minHeight = `${nextHeight}px`;
    updateTxtAverageHeightPerByte(index, chunkDoc, nextHeight);
  };
  const mounted = { slot, iframe, resizeObserver: null };
  state.txtWindow.mounted.set(index, mounted);
  if (typeof ResizeObserver === 'function' && doc.body) {
    const resizeObserver = new ResizeObserver(() => {
      updateTxtIframeHeight();
    });
    resizeObserver.observe(doc.body);
    if (doc.documentElement) {
      resizeObserver.observe(doc.documentElement);
    }
    mounted.resizeObserver = resizeObserver;
  }
  doc.addEventListener('load', (event) => {
    if (event && event.target && event.target.tagName === 'IMG') {
      updateTxtIframeHeight();
    }
  }, true);
  requestAnimationFrame(() => {
    updateTxtIframeHeight();
  });
  applyReaderStyleToMountedTxtSlots();
  updateTxtIframeHeight();
};

const unmountTxtChapterSlot = async (slot, chunkDoc, index) => {
  if (!slot || !chunkDoc) return;
  const mounted = state.txtWindow.mounted.get(index);
  if (mounted && mounted.resizeObserver) {
    mounted.resizeObserver.disconnect();
  }
  if (mounted && mounted.iframe && mounted.iframe.contentDocument) {
    try {
      const measuredHeight = mounted.iframe.getBoundingClientRect().height;
      if (measuredHeight > 0) {
        updateTxtAverageHeightPerByte(index, chunkDoc, measuredHeight);
      }
    } catch (err) {}
  }
  detachTxtMountedChunk(index);
  slot.innerHTML = '';
  setTxtSlotPlaceholderHeight(slot, chunkDoc, index);
  await unloadTxtChunk(index);
};

const getCurrentTxtChunkIndex = () => {
  const chunkDocList = getTxtChunkDocList();
  if (!chunkDocList.length) return -1;
  const viewportTop = stage ? stage.scrollTop : 0;
  const viewportLine = viewportTop + Math.max(80, (stage ? stage.clientHeight : 0) * 0.25);
  let currentIndex = state.txtWindow.currentIndex >= 0 ? state.txtWindow.currentIndex : 0;
  for (let i = 0; i < state.txtWindow.slots.length; i += 1) {
    const slot = state.txtWindow.slots[i];
    if (!slot) continue;
    if (viewportLine >= slot.offsetTop) {
      currentIndex = i;
    } else {
      break;
    }
  }
  return currentIndex;
};

const updateTxtProgress = async () => {
  const chunkDocList = getTxtChunkDocList();
  if (!chunkDocList.length) return;
  const currentIndex = Math.max(0, getCurrentTxtChunkIndex());
  const slot = state.txtWindow.slots[currentIndex];
  const chunkProgressRatio = slot && slot.offsetHeight > 0
    ? Math.max(0, Math.min(1, (stage.scrollTop - slot.offsetTop) / slot.offsetHeight))
    : 0;
  const badge = state.txtWindow.progressBadge;
  if (badge) {
    const percentage = Math.round(getReadingFraction(currentIndex, chunkProgressRatio, chunkDocList.length) * 100);
    badge.textContent = `${percentage}%`;
  }
  state.txtWindow.currentIndex = currentIndex;
  savePosition(state.current, {
    chunkIndex: currentIndex,
    chunkProgressRatio,
    scrollTop: stage ? Math.max(0, Math.round(stage.scrollTop)) : 0,
  });
  if (state.folderMeta) {
    saveFolderCursor(state.folderMeta);
  }
};

const syncTxtContextWindow = async (force = false) => {
  if (!isTxtWindowedMode()) return;
  const chunkDocList = getTxtChunkDocList();
  if (!chunkDocList.length) return;
  const requestedIndex = Number(state.txtWindow.restorePosition?.chunkIndex);
  const currentIndex = Number.isInteger(requestedIndex) && requestedIndex >= 0
    ? Math.min(chunkDocList.length - 1, requestedIndex) : getCurrentTxtChunkIndex();
  if (currentIndex < 0) return;
  const token = ++state.txtWindow.syncToken;
  state.txtWindow.currentIndex = currentIndex;
  const previousMountedRange = state.txtWindow.mountedRange;
  const previousPreloadRange = state.txtWindow.preloadRange;
  const mountedRange = getTxtMountedRange(currentIndex, chunkDocList.length);
  const preloadRange = getTxtPreloadRange(currentIndex, chunkDocList.length);
  state.txtWindow.mountedRange = mountedRange;
  state.txtWindow.preloadRange = preloadRange;
  const restore = state.txtWindow.restorePosition || captureTxtRestorePosition();
  state.txtWindow.restorePosition = null;

  const preloadTasks = [];
  for (let i = preloadRange.start; i <= preloadRange.end; i += 1) {
    preloadTasks.push(loadTxtChunk(chunkDocList[i], i));
  }
  await Promise.allSettled(preloadTasks);
  if (token !== state.txtWindow.syncToken) return;

  const mountTasks = [];
  for (let i = mountedRange.start; i <= mountedRange.end; i += 1) {
    mountTasks.push(mountTxtChapterIntoSlot(state.txtWindow.slots[i], chunkDocList[i], i, token));
  }
  await Promise.allSettled(mountTasks);
  if (token !== state.txtWindow.syncToken) return;

  const unmountTasks = [];
  const activeIndexes = collectActiveSlotIndexes(
    [previousMountedRange, previousPreloadRange],
    state.txtWindow.mounted.keys(),
  );
  for (const i of activeIndexes) {
    if (i < 0 || i >= chunkDocList.length) continue;
    if (i >= preloadRange.start && i <= preloadRange.end) continue;
    unmountTasks.push(unmountTxtChapterSlot(state.txtWindow.slots[i], chunkDocList[i], i));
  }
  await Promise.allSettled(unmountTasks);
  if (token !== state.txtWindow.syncToken) return;

  if (restore) {
    restoreTxtScrollPosition(restore);
  }
  void updateTxtProgress();
};

const scheduleTxtContextWindowSync = (force = false) => {
  if (state.isOpening || state.layoutPending || state.txtWindow.navigating || !isTxtWindowedMode()) return;
  window.clearTimeout(state.txtWindow.syncTimer);
  state.txtWindow.syncTimer = window.setTimeout(() => {
    void syncTxtContextWindow(force);
  }, force ? 0 : 120);
};

const scheduleTxtProgressSync = () => {
  window.clearTimeout(state.txtWindow.progressTimer);
  state.txtWindow.progressTimer = window.setTimeout(() => {
    void updateTxtProgress();
  }, 120);
};

const bindTxtStageScroll = () => {
  if (!stage || stage.__recollTxtScrollBound) return;
  stage.addEventListener('scroll', () => {
    scheduleTxtContextWindowSync();
    scheduleTxtProgressSync();
  }, { passive: true });
  stage.__recollTxtScrollBound = 'yes';
};

const openTxtScrollDocument = async (item, openToken) => {
  if (!item || !item.manifestUrl) {
    throw new Error('TXT 清单地址缺失');
  }
  setLoading(`正在解析 ${item.name || item.title || 'TXT'} 分块索引…`);
  const manifest = await fetchTxtJson(item.manifestUrl);
  if (openToken !== state.openToken) return null;
  const chunkDocList = Array.isArray(manifest && manifest.chunks) ? manifest.chunks : [];
  if (!chunkDocList.length) {
    throw new Error('TXT 分块索引为空');
  }
  state.txtWindow.manifest = manifest;
  state.txtWindow.contentUrl = safeText(manifest.contentUrl);
  state.txtWindow.charset = safeText(manifest.charset || 'utf-8');
  state.txtWindow.chunkCount = Math.max(0, Number(manifest.chunkCount) || chunkDocList.length);
  const rendition = {
    format: 'TXT',
    readerMode: 'scroll',
    chunkDocList,
    tempLocation: {},
    on() {},
    setStyle() {},
    removeContent() {
      if (stage) stage.innerHTML = '';
    },
    getPosition() {
      return captureTxtRestorePosition() || { chunkIndex: 0, chunkProgressRatio: 0, scrollTop: 0 };
    },
    async getProgress() {
      const position = captureTxtRestorePosition();
      return { percentage: getReadingFraction(position?.chunkIndex, position?.chunkProgressRatio, chunkDocList.length) };
    },
    async goToPosition(rawPosition) {
      const position = typeof rawPosition === 'string' ? JSON.parse(rawPosition) : rawPosition;
      const normalized = normalizeTxtPosition(position) || { chunkIndex: 0, chunkProgressRatio: 0, scrollTop: 0 };
      state.txtWindow.currentIndex = Math.max(0, Math.min(chunkDocList.length - 1, normalized.chunkIndex));
      state.txtWindow.restorePosition = normalized;
      window.clearTimeout(state.txtWindow.syncTimer);
      state.txtWindow.navigating = true;
      try { await syncTxtContextWindow(true); }
      finally { state.txtWindow.navigating = false; }
    },
  };
  state.rendition = rendition;
  window.rendition = rendition;
  setLoading(`正在准备 ${item.name || item.title || 'TXT'} 阅读窗口…`);
  buildTxtWindowStage(chunkDocList);
  bindStageNavigation();
  bindTxtStageScroll();
  const savedPosition = loadPosition(item);
  const pendingRestore = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
    ? state.pendingRestorePosition.position
    : null;
  const restoreSource = pendingRestore || savedPosition;
  const restorePosition = isTxtLegacyPosition(restoreSource)
    ? null
    : (normalizeTxtPosition(restoreSource) || { chunkIndex: 0, chunkProgressRatio: 0, scrollTop: 0 });
  state.pendingRestorePosition = null;
  state.txtWindow.currentIndex = Math.max(0, Math.min(chunkDocList.length - 1, restorePosition.chunkIndex));
  state.txtWindow.restorePosition = restorePosition;
  const progressBadge = document.createElement('div');
  progressBadge.className = 'reader-progress';
  progressBadge.textContent = '0%';
  stage.appendChild(progressBadge);
  state.txtWindow.progressBadge = progressBadge;
  await syncTxtContextWindow(true);
  if (openToken !== state.openToken) return null;
  await updateTxtProgress();
  return rendition;
};

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
  if (state.chmSandbox) return state.chmSandbox.getPosition();
  const iframe = getActiveContentFrame();
  if (!iframe?.dataset.readerContentLoaded || !state.currentChmManifest) return null;
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
  if (isTxtFormat(state.current.format)) {
    const position = captureTxtRestorePosition();
    if (position) savePosition(state.current, position);
    return;
  }
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
  // The open receipt records the document identity, independent of list order.
  meta.currentIndex = state.currentIndex;
};

const documentMetaMarkup = (item) => `
  <span class="reader-format-tag">${escapeHtml(item.format ? item.format.toUpperCase() : '文档')}</span>
  ${item.folder_name ? `<span class="reader-folder-name" title="${escapeHtml(item.folder_name)}">${escapeHtml(item.folder_name)}</span>` : ''}`;

const updateMeta = (includeName = true) => {
  if (!state.current) return;
  const parts = [state.current.author || '', includeName ? state.current.name || '' : ''].filter(Boolean);
  metaEl.innerHTML = documentMetaMarkup(state.current) + (parts.length ? `<span class="reader-document-info">${escapeHtml(parts.join(' · '))}</span>` : '');
  titleEl.textContent = state.current.title || state.current.name || 'Reader';
};

const renderSidebar = () => {
  const chmMode = !!(state.current && isChmFormat(state.current.format) && state.currentChmManifest);
  if (sidebarHeader) {
    sidebarHeader.textContent = '阅读导航';
  }
  if (!state.items.length && !chmMode) {
    booklist.innerHTML = '<div class="reader-loading">当前文件夹没有可阅读文件</div>';
    return;
  }
  const libraryMarkup = state.items.map((item, idx) => `
    <button class="reader-book-item ${idx === state.currentIndex ? 'is-active' : ''}" data-index="${idx}">
      <span class="reader-document-meta">${documentMetaMarkup(item)}</span>
      ${escapeHtml(item.title || item.name || 'Untitled')}
      ${item.path ? `<small>${escapeHtml(item.path.replace(item.root || '', ''))}</small>` : ''}
    </button>`).join('');
  booklist.innerHTML = libraryMarkup;
  booklist.querySelectorAll('[data-index]').forEach((btn) => {
    btn.addEventListener('click', () => {
      void openIndex(Number(btn.dataset.index));
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
  window.clearTimeout(state.layoutSyncTimer);
  state.layoutRestoreAnchor = null;
  state.layoutPending = false;
  state.reflowing = false;
  if (state.pdfLoadingTask) {
    void state.pdfLoadingTask.destroy().catch(() => {});
    state.pdfLoadingTask = null;
  }
  window.clearTimeout(state.scrollHeightTimer);
  window.clearTimeout(state.genericRecordTimer);
  window.clearTimeout(state.epubWindow.syncTimer);
  window.clearTimeout(state.txtWindow.syncTimer);
  state.chmSandbox?.destroy();
  state.chmSandbox = null;
  state.currentChmManifest = null;
  state.currentChmHref = '';
  state.currentChmRestore = null;
  resetEpubContextWindow();
  resetTxtContextWindow();
  if (state.rendition && typeof state.rendition.removeContent === 'function') {
    try { state.rendition.removeContent(); } catch (err) {}
  }
  state.rendition = null;
  if (state.currentBlobUrl) {
    URL.revokeObjectURL(state.currentBlobUrl);
    state.currentBlobUrl = '';
  }
};

const updateNavigationControls = ({ outline = readerNavigation?.outline || [],
  activeOutlineIndex = readerNavigation?.activeOutlineIndex ?? -1 } = {}) => {
  syncReaderModeUi();
  const ready = !!state.current && !state.isOpening && !accountSync.stopped;
  for (const id of ['reader-prev-book', 'reader-progress-prev-book']) {
    const button = el(id); if (button) button.disabled = !ready || state.currentIndex <= 0;
  }
  for (const id of ['reader-next-book', 'reader-progress-next-book']) {
    const button = el(id); if (button) button.disabled = !ready || state.currentIndex >= state.items.length - 1;
  }
  const canNavigate = ready && !!(state.rendition || state.currentChmManifest)
    && !readerNavigation?.stopped && outline.length > 0;
  if (el('reader-prev-section')) el('reader-prev-section').disabled = !canNavigate || activeOutlineIndex <= 0;
  if (el('reader-next-section')) el('reader-next-section').disabled = !canNavigate || activeOutlineIndex >= outline.length - 1;
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
  if (state.chmSandbox) { state.chmSandbox.scrollBy(amount); return; }
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
  if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey
      || document.querySelector('dialog[open]') || el('reader-settings-panel')?.hidden === false
      || (isMobileViewport() && !sidebar.classList.contains('is-hidden'))) return false;
  const target = event.target;
  if (!target) return true;
  return !target.closest?.('input,textarea,select,button,a[href],[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="slider"]');
};

const isInteractiveTouchTarget = (target) => {
  if (!target || typeof target.closest !== 'function') return false;
  return !!target.closest('a, img, button, input, textarea, select, label, summary, [role="button"], .kookit-note, .kookit-note-icon, .kookit-word-def, .kookit-word-tooltip');
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
  bindReadingExtras(doc);
  bindTouchNavigationTarget(doc, () => {
    if (doc.documentElement && doc.documentElement.clientWidth) {
      return doc.documentElement.clientWidth;
    }
    return stage ? stage.clientWidth : window.innerWidth;
  });
};

const bindStageNavigation = () => {
  bindTouchNavigationTarget(stage, () => stage ? stage.clientWidth : window.innerWidth);
  if (stage && !stage.__recollGenericScrollBound) {
    stage.addEventListener('scroll', () => {
      const rendition = state.rendition;
      if (state.isOpening || state.readerMode !== 'scroll' || typeof rendition?.record !== 'function') return;
      window.clearTimeout(state.genericRecordTimer);
      state.genericRecordTimer = window.setTimeout(() => {
        if (!state.isOpening && rendition === state.rendition) void rendition.record().catch(() => {});
      }, 120);
    }, { passive: true });
    stage.__recollGenericScrollBound = true;
  }
};

const bindIframeNavigation = () => {
  if (state.chmSandbox) return;
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

const fetchReaderResponse = async (url, options = {}) => {
  const res = await fetch(url, { ...options, credentials: 'same-origin' });
  if (res.status === 401) {
    const message = readerConfig.shared ? '分享已失效，请联系分享者。' : '登录已失效，请重新登录';
    clearAccountReader(message);
    throw new Error(message);
  }
  if (!res.ok) {
    throw new Error(`读取文档失败 (${res.status})`);
  }
  return res;
};

const fetchJson = async (url) => (await fetchReaderResponse(url)).json();

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
  const currentEntry = findChmTocEntry(state.currentChmHref);
  const label = currentEntry && currentEntry.label ? currentEntry.label : (state.current.title || state.current.name || 'Reader');
  updateMeta(false);
  titleEl.textContent = label;
};

const openChmHref = async (href, restore = null) => {
  if (!state.currentChmManifest) return;
  const url = resolveChmContentUrl(state.currentChmManifest.contentBaseUrl, href || state.currentChmManifest.startPath);
  const iframe = getActiveContentFrame();
  if (!iframe || !url) return;
  const openToken = state.openToken;
  const navigationToken = ++state.chmNavigationToken;
  await fetchReaderResponse(url, { method: 'HEAD' });
  if (openToken !== state.openToken || navigationToken !== state.chmNavigationToken || accountSync.stopped) return;
  delete iframe.dataset.readerContentLoaded;
  state.currentChmHref = normalizeChmHref(href || state.currentChmManifest.startPath);
  state.currentChmRestore = restore && typeof restore === 'object' ? restore : null;
  updateChmMeta();
  renderSidebar();
  if (state.chmSandbox) {
    await state.chmSandbox.open(state.currentChmHref, restore);
    return;
  }
  iframe.src = url;
};

const handleChmFrameLoad = (iframe) => {
  if (state.chmSandbox) { state.chmSandbox.setStyle(buildReaderStyle()); return; }
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
  if (doc.contentType === 'application/json' || win.location.href === 'about:blank') return;
  iframe.dataset.readerContentLoaded = 'yes';
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
      const base = new URL(contentBaseUrl, window.location.origin);
      const target = new URL(value, window.location.origin);
      if (target.origin === base.origin && target.pathname.startsWith(base.pathname)) return;
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
          void openChmHref(`${rel}${target.search}${target.hash}`).catch((error) => {
            showReaderError(error.message);
          });
        } else if (/^\/[^/]/.test(href)) {
          event.preventDefault();
          const rel = href.replace(/^\/+/, '');
          void openChmHref(`${rel}${target.search}${target.hash}`).catch((error) => {
            showReaderError(error.message);
          });
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
  if (!win.__recollChmProgressBound) {
    let timer;
    win.addEventListener('scroll', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (state.isOpening || state.reflowing || accountSync.stopped) return;
        state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
        updateReaderProgress(getChmCurrentPosition()); persistCurrentPosition();
      }, 120);
    }, { passive: true });
    win.__recollChmProgressBound = true;
  }
};

const openChmDocument = async (item, openToken) => {
  const manifestUrl = item.manifestUrl || '';
  if (!manifestUrl) {
    throw new Error('CHM 清单地址缺失');
  }
  const manifest = await fetchJson(manifestUrl);
  if (openToken !== state.openToken) return null;
  if (!manifest || !manifest.startUrl) {
    throw new Error('CHM 入口页解析失败');
  }
  state.currentChmManifest = manifest;
  state.currentChmHref = normalizeChmHref(manifest.startPath || '');
  stage.innerHTML = '<iframe id="reader-chm-iframe" class="reader-chm-iframe" title="CHM Reader" loading="eager" referrerpolicy="no-referrer"></iframe>';
  const iframe = getActiveContentFrame();
  if (!iframe) {
    throw new Error('CHM 阅读框初始化失败');
  }
  iframe.setAttribute('sandbox', manifest.renderBaseUrl ? 'allow-scripts' : 'allow-same-origin');
  if (manifest.renderBaseUrl) {
    state.chmSandbox = new ChmSandboxReader(iframe, manifest, {
      onPosition: (position) => {
        if (openToken !== state.openToken || accountSync.stopped || !position) return;
        const changed = state.currentChmHref !== position.href;
        state.currentChmHref = position.href;
        if (changed) updateChmMeta();
        updateReaderProgress(position);
        if (!state.isOpening && !state.reflowing) persistCurrentPosition();
      },
      onSearch: () => { openSidebar(); readerNavigation?.selectTab('search'); },
    });
    state.chmSandbox.setStyle(buildReaderStyle());
  }
  const savedPosition = loadPosition(item);
  const restore = isValidChmPosition(state.currentChmRestore)
    ? state.currentChmRestore
    : (isValidChmPosition(savedPosition) ? savedPosition : null);
  bindIframeNavigation();
  await openChmHref((restore && restore.href) || manifest.startPath, restore);
  await waitForCondition(() => openToken !== state.openToken || !!getActiveContentFrame()?.dataset.readerContentLoaded);
  if (openToken === state.openToken && !getActiveContentFrame()?.dataset.readerContentLoaded) {
    throw new Error('CHM 正文尚未成功加载，原阅读进度已保留');
  }
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
  const desiredContentWidth = Math.min(stageWidth - (mobile ? 28 : 40), readerSettings?.values.reader_content_width || 760);
  const contentWidth = Math.max(280, Math.min(stageWidth - 32, desiredContentWidth));
  return {
    fontSize: state.fontSize,
    lineHeight: (readerSettings?.values.reader_line_height || 180) / 100,
    paragraphGap: readerSettings?.values.reader_paragraph_spacing ?? 12,
    headingGap: Math.max(18, Math.round(state.fontSize * 0.75)),
    contentWidth: isComicFormat ? Math.max(280, stageWidth - 40) : contentWidth,
    contentPadding: mobile ? 0 : 4,
    isComicFormat,
  };
};

const buildReaderStyle = () => readerSettings?.contentStyle(state.fontSize) || '';

const applyDefaultIframeStyle = (doc) => {
  if (!doc || !doc.head) return;
  // Converted books are written into about:blank. Install their resource
  // policy before inserting the first chapter; the source response's CSP
  // does not follow an ArrayBuffer into this document.
  if (!doc.head.querySelector('meta[data-recoll-reader-csp]')) {
    const policy = doc.createElement('meta');
    policy.httpEquiv = 'Content-Security-Policy';
    policy.content = DOCUMENT_CSP;
    policy.dataset.recollReaderCsp = 'yes';
    doc.head.prepend(policy);
  }
  const styleId = 'recoll-reader-base-style';
  let style = doc.getElementById(styleId);
  if (!style) {
    style = doc.createElement('style');
    style.id = styleId;
    doc.head.appendChild(style);
  }
  const contentInset = getContentInset();
  style.textContent = `html,body{background:transparent !important;overflow-x:hidden;}html,body,body *,table,img,div,pre,code,td,th,tr,tbody,thead,tfoot,table[width],td[width],th[width]{max-width:100% !important;box-sizing:border-box !important;word-wrap:break-word;overflow-wrap:break-word;}pre,pre code{white-space:pre;word-wrap:normal;overflow-wrap:normal;}p,empty-line{display: inherit;margin-block-start: inherit;margin-block-end: inherit;margin-inline-start: inherit;margin-inline-end: inherit;}body{margin:0px;padding-left:${contentInset.left};padding-right:${contentInset.right};}`;
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

const syncRenditionLayoutContent = async (force = false) => {
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
  if (state.current && isTxtFormat(state.current.format)) {
    applyReaderStyleToMountedTxtSlots();
    state.txtWindow.slots.forEach((slot, index) => {
      const chunkDoc = getTxtChunkDocList()[index];
      if (!slot || !chunkDoc) return;
      setTxtSlotPlaceholderHeight(slot, chunkDoc, index);
    });
    rememberStageSize();
    await syncTxtContextWindow(force);
    return;
  }
  if (state.current && isEpubFormat(state.current.format)) {
    applyReaderStyleToMountedEpubSlots();
    syncEpubSlotsStyle();
    await syncEpubContextWindow(force);
    rememberStageSize();
    return;
  }
  if (!state.rendition) return;
  if (state.rendition.isNativePdf) {
    try { await state.rendition.resize(); }
    catch (error) { showReaderError(`PDF 重绘失败：${error.message}`); }
    rememberStageSize();
    return;
  }

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

const syncRenditionLayout = async (force = false) => {
  const openToken = state.openToken, navigationToken = state.navigationToken;
  const preserveText = !state.isOpening && !state.reflowing && !state.rendition?.isNativePdf
    && !isChmFormat(state.current?.format);
  const anchor = preserveText ? state.layoutRestoreAnchor || state.lastTextAnchor || captureTextAnchor(stage, getReadingDocuments()) : null;
  if (preserveText) state.reflowing = true;
  try {
    await syncRenditionLayoutContent(force);
    if (anchor) {
      await settleReaderLayout();
      // A later document or explicit navigation supersedes this reflow's
      // captured location, even when its asynchronous layout finishes last.
      if (openToken === state.openToken && navigationToken === state.navigationToken && !accountSync.stopped) {
        restoreTextAnchor(stage, getReadingDocuments(), anchor);
        state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
      }
    }
  } finally {
    if (openToken === state.openToken) {
      if (preserveText) state.reflowing = false;
      state.layoutRestoreAnchor = null; state.layoutPending = false;
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
  // Freeze the last text location as soon as the viewport changes. Waiting
  // for the debounce lets scroll/resize callbacks capture the reflowed text.
  if (!state.isOpening && !state.reflowing && !state.layoutPending) {
    state.layoutRestoreAnchor = state.lastTextAnchor || captureTextAnchor(stage, getReadingDocuments());
    state.layoutPending = true;
  }
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
  const anchor = captureTextAnchor(stage, getReadingDocuments());
  state.reflowing = true;
  state.fontSize = fontSize;
  persistFontSize();
  updateFontSizeUi();
  try {
    if (state.current && isChmFormat(state.current.format)) {
      const iframe = getActiveContentFrame();
      if (iframe) handleChmFrameLoad(iframe);
    } else {
      await syncRenditionLayout(true);
      if (isEpubWindowedMode()) await syncEpubMountedWindow(true);
    }
    await settleReaderLayout();
    restoreTextAnchor(stage, getReadingDocuments(), anchor);
  } finally { state.reflowing = false; }
  persistCurrentPosition();
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

const openPdfDocument = async (item, openToken) => {
  // Verify the session before PDF.js starts authenticated streaming and range
  // requests. The viewer can then paint without waiting for the entire file.
  await fetchReaderResponse(item.url, { method: 'HEAD' });
  const { createPdfRendition } = await import('./pdf-adapter.js');
  if (openToken !== state.openToken || accountSync.stopped) return;
  const pendingRestore = state.pendingRestorePosition?.path === item.path ? state.pendingRestorePosition.position : null;
  const rendition = await createPdfRendition(new URL(item.url, window.location.href).href, {
    initialPosition: pendingRestore || loadPosition(item),
    onLoadingTask: (task) => {
      if (openToken !== state.openToken || accountSync.stopped) void task.destroy().catch(() => {});
      else state.pdfLoadingTask = task;
    },
    onError: (error) => { showReaderError(`PDF 页面未能加载：${error.message}`); },
  });
  if (openToken !== state.openToken || accountSync.stopped) {
    rendition.removeContent();
    return;
  }
  state.pdfLoadingTask = null;
  state.pendingRestorePosition = null;
  state.rendition = rendition;
  window.rendition = rendition;
  state.readerMode = 'scroll';
  syncReaderModeUi();
  await rendition.renderTo(stage);
  if (openToken !== state.openToken || accountSync.stopped) return;
  const badge = document.createElement('div');
  badge.className = 'reader-progress';
  stage.appendChild(badge);
  const updateProgress = async () => {
    const progress = await rendition.getProgress();
    if (state.current !== item || accountSync.stopped) return;
    badge.textContent = `第 ${progress.pageNumber} / ${progress.pageCount} 页 · ${Math.round(progress.percentage * 100)}%`;
    if (el('reader-pdf-zoom-value')) el('reader-pdf-zoom-value').textContent = `${Math.round((progress.scale || 1) * 100)}%`;
    if (!rendition.isRendering) savePosition(item, rendition.getPosition());
  };
  rendition.on('rendered', () => { void updateProgress(); });
  rendition.on('position-changed', () => { void updateProgress(); });
  await updateProgress();
  updateNavigationControls();
  bindStageNavigation();
  await finishDocumentOpen(item, openToken);
};

const finishDocumentOpen = async (item, openToken) => {
  // Scroll restoration and iframe layout may finish on the next animation
  // frame. Initial 0% render events must never overwrite a saved position.
  await new Promise((resolve) => window.requestAnimationFrame(resolve));
  await new Promise((resolve) => window.requestAnimationFrame(resolve));
  if (openToken !== state.openToken || accountSync.stopped) return;
  const saved = state.openRestorePosition;
  if (saved?.textAnchor) {
    await settleReaderLayout();
    restoreTextAnchor(stage, getReadingDocuments(), saved.textAnchor);
  }
  if (openToken !== state.openToken || accountSync.stopped) return;
  state.isOpening = false;
  await accountSync.rendered(item, state.folderMeta?.folderKey || state.folderMeta?.folder_key);
  if (openToken !== state.openToken || accountSync.stopped) return;
  state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
  await readerNavigation?.refresh();
  if (openToken !== state.openToken || accountSync.stopped) return;
  updateDocumentControls();
  persistCurrentPosition();
  void handleInitialSearch();
};

const openIndex = async (index) => {
  state.navigationToken += 1;
  if (!state.items.length || accountSync.stopped) return;
  const previousItem = state.current;
  persistCurrentPosition();
  state.temporaryPosition = null;
  state.lastTextAnchor = null;
  const openToken = ++state.openToken;
  await accountSync.flush(previousItem);
  if (openToken !== state.openToken || accountSync.stopped) return;
  const nextIndex = Math.max(0, Math.min(index, state.items.length - 1));
  const item = state.items[nextIndex];
  const viewportConfig = getViewportReaderConfig(item);
  state.currentIndex = nextIndex;
  state.current = item;
  state.isOpening = true;
  state.readerMode = viewportConfig.readerMode;
  state.mobileFlag = viewportConfig.isMobile;
  syncReaderModeUi();
  updateMeta();
  updateShelfButton();
  cleanupRendition();
  renderSidebar();
  updateNavigationControls();
  setLoading(`正在打开 ${item.name || item.title || ''}`);
  try {
    await accountSync.prepare(item);
    if (openToken !== state.openToken || accountSync.stopped) return;
    state.openRestorePosition = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
      ? state.pendingRestorePosition.position : loadPosition(item);
    if (normalizeFormat(item.format) === 'pdf') {
      await openPdfDocument(item, openToken);
      return;
    }
    if (isTxtFormat(item.format)) {
      const rendition = await openTxtScrollDocument(item, openToken);
      if (openToken !== state.openToken) return;
      state.rendition = rendition;
      window.rendition = rendition;
      state.readerMode = 'scroll';
      updateNavigationControls();
      bindStageNavigation();
      rememberStageSize();
      const mounted = state.txtWindow.mounted.get(state.txtWindow.currentIndex);
      if (!mounted?.iframe?.contentDocument?.body) throw new Error('TXT 正文尚未成功加载，原阅读进度已保留');
      await finishDocumentOpen(item, openToken);
      return;
    }
    if (isChmFormat(item.format)) {
      const pendingRestore = state.pendingRestorePosition && state.pendingRestorePosition.path === item.path
        ? state.pendingRestorePosition.position
        : null;
      if (pendingRestore && isValidChmPosition(pendingRestore)) {
        state.currentChmRestore = pendingRestore;
        state.pendingRestorePosition = null;
      }
      await openChmDocument(item, openToken);
      if (openToken !== state.openToken) return;
      bindStageNavigation();
      bindIframeNavigation();
      rememberStageSize();
      await finishDocumentOpen(item, openToken);
      return;
    }
    if (isEpubFormat(item.format)) {
      const rendition = await openEpubScrollDocument(item, viewportConfig, openToken);
      if (openToken !== state.openToken) return;
      state.rendition = rendition;
      window.rendition = rendition;
      updateNavigationControls();
      bindStageNavigation();
      bindIframeNavigation();
      rememberStageSize();
      const initialIndex = state.epubWindow.currentIndex;
      await waitForCondition(() => openToken !== state.openToken || !!state.epubWindow.mounted.get(initialIndex)?.iframe?.dataset.readerContentLoaded);
      if (openToken !== state.openToken) return;
      if (!state.epubWindow.mounted.get(initialIndex)?.iframe?.dataset.readerContentLoaded) throw new Error('EPUB 正文尚未成功加载，原阅读进度已保留');
      const savedAnchor = getEpubAnchorFromPosition(state.openRestorePosition);
      if (savedAnchor) restoreEpubScrollPosition(savedAnchor);
      await finishDocumentOpen(item, openToken);
      return;
    }
    const response = await fetchReaderResponse(item.url);
    const buffer = await response.arrayBuffer();
    if (!['html', 'htm', 'xhtml'].includes(normalizeFormat(item.format))) await ensureKookitRuntime();
    const Kookit = window.Kookit;
    const options = {
      format: item.format.toUpperCase(),
      readerMode: viewportConfig.readerMode,
      charset: undefined,
      animation: '',
      convertChinese: 'no',
      fullTranslationMode: 'no',
      textOrientation: 'horizontal',
      parserRegex: '',
      isDarkMode: 'no',
      isMobile: viewportConfig.isMobile,
      password: '',
      isScannedPDF: 'no',
      backgroundColor: '',
      isConvertPDF: 'no',
      ocrLang: '',
      ocrEngine: 'paddle',
      isAllowScript: 'no',
      isBionic: 'no',
      isIndent: 'no',
      isHyphenation: 'no',
      isStartFromEven: 'no',
      scale: 1,
    };
    let rendition;
    if (['html', 'htm', 'xhtml'].includes(normalizeFormat(item.format))) {
      const { createHtmlRendition } = await import('./html-adapter.js');
      rendition = createHtmlRendition(buffer, { url: item.url, title: item.title || item.name,
        policy: DOCUMENT_CSP, prepareDocument: applyDefaultIframeStyle });
    } else rendition = window.BookHelper.getRendition(buffer, options, Kookit);
    // The bundled reader enables scripts for touch devices and PDFs by
    // default, overriding its constructor options. Set the instance itself.
    rendition.isAllowScript = 'no';
    if (openToken !== state.openToken) return;
    state.rendition = rendition;
    window.rendition = rendition;
    installGenericScrollPosition(rendition);
    updateNavigationControls();
    const savedPosition = loadPosition(item);
    await rendition.renderTo(stage);
    stage.querySelectorAll('iframe').forEach((frame) => frame.setAttribute('sandbox', 'allow-same-origin'));
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
    } else if (typeof rendition.goToPosition === 'function') {
      // Kookit renderTo only creates an empty iframe. Scroll readers need
      // the same initial chapter load as paged readers.
      const defaultPagedPosition = buildDefaultPagedPosition(rendition);
      if (defaultPagedPosition) {
        await rendition.goToPosition(JSON.stringify(defaultPagedPosition));
      }
    }
    if (openToken !== state.openToken || accountSync.stopped) return;
    const contentDoc = rendition.getDocument?.();
    if (!contentDoc?.body || !(contentDoc.body.innerText.trim()
      || contentDoc.body.querySelector('img,svg,canvas,video,audio,math'))) {
      throw new Error('正文尚未成功加载，原阅读进度已保留');
    }
    applyReaderStyle();
    await restoreGenericScrollPosition(rendition, pendingRestore || savedPosition, openToken);
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
    await finishDocumentOpen(item, openToken);
  } catch (err) {
    console.error(err);
    if (openToken === state.openToken && !accountSync.stopped) setError((err && err.message) || '打开阅读器失败');
  } finally {
    if (openToken === state.openToken) {
      state.isOpening = false;
      updateNavigationControls();
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
  if (data.resumeWarning) {
    const warning = document.createElement('div');
    warning.className = 'reader-meta';
    warning.setAttribute('role', 'alert');
    warning.textContent = data.resumeWarning;
    metaEl.parentNode?.appendChild(warning);
  }
  state.folderMeta = data;
  state.items = data.items || [];
  state.currentIndex = Math.max(0, Math.min(
    Number(data.currentIndex) || 0,
    Math.max(0, state.items.length - 1),
  ));
  state.current = state.items[state.currentIndex] || state.items[0] || null;
  renderSidebar();
  updateMeta();
  if (data.truncated) {
    metaEl.appendChild(document.createTextNode(` · 已截断至 ${state.items.length} 本`));
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

for (const [id, direction] of [['reader-prev-book', -1], ['reader-progress-prev-book', -1],
  ['reader-next-book', 1], ['reader-progress-next-book', 1]]) {
  el(id)?.addEventListener('click', () => { void openIndex(state.currentIndex + direction); });
}
for (const [id, direction] of [['reader-prev-section', -1], ['reader-next-section', 1]]) {
  el(id)?.addEventListener('click', () => { void readerNavigation.navigateAdjacent(direction); });
}

el('reader-save-regex').addEventListener('click', () => {
  state.parserRegex = parserInput.value.trim();
});

window.addEventListener('beforeunload', () => {
  void readerSettings?.flush({ keepalive: true });
  persistCurrentPosition();
  void accountSync.flush(state.current, { keepalive: true });
});

let sessionCheckInFlight = false;
const validateReaderSession = async () => {
  if (!account || accountSync.stopped || sessionCheckInFlight || document.hidden) return;
  sessionCheckInFlight = true;
  accountSync.paused = true;
  stage.style.visibility = 'hidden';
  try {
    let valid;
    if (window.RecollAccount?.validateSession) {
      valid = await window.RecollAccount.validateSession();
    } else {
      const data = await accountSync.request(`${applicationBase}/api/account`);
      valid = String(data.user?.id) === String(account.userId)
        && String(data.sessionId ?? data.session?.id) === String(account.sessionId);
    }
    if (!valid) clearAccountReader();
  } catch (error) {
    showReaderError('暂时无法确认会话，已暂停进度同步；联网后切回页面重试。');
    return;
  } finally {
    sessionCheckInFlight = false;
    stage.style.visibility = '';
  }
  if (!accountSync.stopped) accountSync.paused = false;
};
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    void readerSettings?.flush({ keepalive: true });
    persistCurrentPosition();
    void accountSync.flush(state.current, { keepalive: true });
  } else { void validateReaderSession(); }
});
window.addEventListener('pageshow', (event) => {
  if (event.persisted) void validateReaderSession();
});
window.addEventListener('recoll-account-change', (event) => {
  if (!account) return;
  const detail = event.detail || {};
  if (['logout', 'session-changed', 'history-cleared'].includes(detail.type)) {
    clearAccountReader(detail.type === 'history-cleared'
      ? '阅读记录已清理，请重新打开文档后继续。' : undefined);
  } else if (detail.type === 'settings-changed' || detail.type === 'reader-settings-changed') {
    if (detail.preference === 'font_size' || detail.type === 'reader-settings-changed') {
      void accountSync.request(`${applicationBase}/api/account`).then(async (data) => {
        if (!accountSync.stopped && data.settings?.preferences?.font_size != null) {
          const anchor = captureTextAnchor(stage, getReadingDocuments());
          account.settings = data.settings;
          state.reflowing = true;
          try {
            state.fontSize = readStoredFontSize();
            readerSettings?.replacePreferences(data.settings.preferences);
            updateFontSizeUi();
            await syncRenditionLayout(true); await settleReaderLayout();
            restoreTextAnchor(stage, getReadingDocuments(), anchor);
            state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
          } finally { state.reflowing = false; }
        }
      }).catch(() => {});
    } else {
      clearAccountReader('记录设置已改变，请重新打开文档后继续。');
    }
  }
});
window.setInterval(() => {
  if (!document.hidden && !state.isOpening && !accountSync.stopped) persistCurrentPosition();
}, 6000);

window.addEventListener('keydown', (event) => {
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

const handleViewportResize = () => {
  syncViewportHeight();
  syncSidebarState();
  scheduleLayoutSync();
  scheduleViewportModeSync();
  scheduleEpubContextWindowSync();
};

window.addEventListener('resize', handleViewportResize);
if (window.visualViewport) {
  // Mobile browser chrome can resize only the visual viewport.
  window.visualViewport.addEventListener('resize', handleViewportResize);
}

window.addEventListener('orientationchange', () => {
  syncViewportHeight();
  syncSidebarState();
  scheduleLayoutSync(true);
  scheduleViewportModeSync();
  scheduleEpubContextWindowSync(true);
});

const settleReaderLayout = async () => {
  await new Promise((resolve) => requestAnimationFrame(resolve));
  await new Promise((resolve) => requestAnimationFrame(resolve));
};

const getReadingDocuments = () => {
  if (!stage || state.rendition?.isNativePdf || state.chmSandbox) return [];
  const mounted = isEpubFormat(state.current?.format) ? state.epubWindow.mounted
    : isTxtFormat(state.current?.format) ? state.txtWindow.mounted : null;
  if (mounted) return [...mounted].map(([index, item]) => ({ index, frame: item.iframe, document: item.iframe?.contentDocument }))
    .filter((item) => item.document?.body).sort((a, b) => a.index - b.index);
  const frame = getActiveContentFrame();
  return frame?.contentDocument?.body ? [{ index: Number(state.rendition?.tempLocation?.chapterDocIndex) || 0,
    frame, document: frame.contentDocument }] : [];
};

const handleReadingKey = (event) => {
  if (!shouldHandleStageKey(event)) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
    event.preventDefault(); openSidebar(); readerNavigation?.selectTab('search'); return;
  }
  if (event.key === 'PageUp' || (event.key === ' ' && event.shiftKey)) {
    event.preventDefault(); scrollReadingViewport(-1);
  } else if (event.key === 'PageDown' || event.key === ' ') {
    event.preventDefault(); scrollReadingViewport(1);
  }
};

const updateReaderProgress = (position = state.rendition?.getPosition?.() || (isChmFormat(state.current?.format) ? getChmCurrentPosition() : null)) => {
  if (!position || !state.current) return;
  let fraction = Number(position.percentage);
  const format = normalizeFormat(state.current.format);
  if (format === 'txt') fraction = getReadingFraction(position.chunkIndex, position.chunkProgressRatio, getTxtChunkDocList().length);
  if (format === 'chm' && !state.chmSandbox) {
    const win = getActiveContentFrame()?.contentWindow, doc = win?.document;
    const height = Math.max(doc?.body?.scrollHeight || 0, doc?.documentElement?.scrollHeight || 0);
    fraction = (win?.scrollY || 0) / Math.max(1, height - (win?.innerHeight || 0));
  }
  if (!Number.isFinite(fraction)) fraction = stage.scrollTop / Math.max(1, stage.scrollHeight - stage.clientHeight);
  const value = Math.max(0, Math.min(1, fraction));
  const slider = el('reader-progress-range');
  if (slider && document.activeElement !== slider) slider.value = String(Math.round(value * 1000));
  slider?.setAttribute('aria-valuetext', `${Math.round(value * 100)}%`);
  const label = el('reader-progress-label');
  if (label) label.textContent = `${position.pageNumber ? `第 ${position.pageNumber} 页 · ` : ''}${Math.round(value * 100)}%`;
  const index = format === 'txt' ? Number(position.chunkIndex) : format === 'pdf' ? Number(position.pageNumber) - 1 : Number(position.chapterDocIndex);
  if (['html', 'htm', 'xhtml', 'md', 'xml', 'mhtml'].includes(format)) readerNavigation?.updateActiveHeading();
  else if (Number.isFinite(index)) readerNavigation?.setActive(index);
};

const updateDocumentControls = () => {
  const pdf = !!state.rendition?.isNativePdf;
  const controls = el('reader-pdf-controls'); if (controls) controls.hidden = !pdf;
  if (el('reader-progress-range')) el('reader-progress-range').disabled = false;
  document.querySelectorAll('[data-reader-text-setting],.reader-font-controls').forEach((node) => { node.hidden = pdf; });
  el('reader-prev-book').hidden = el('reader-next-book').hidden = state.items.length < 2;
  updateNavigationControls();
  updateReaderProgress();
};

const navigateReaderTarget = async (target, { temporary = false } = {}) => {
  if (!state.current || state.isOpening || (!state.rendition && !isChmFormat(state.current.format))) return;
  if (temporary && !state.temporaryPosition) {
    persistCurrentPosition();
    state.temporaryPosition = { item: state.current, position: structuredClone(state.rendition?.getPosition?.() || getChmCurrentPosition() || {}),
      anchor: captureTextAnchor(stage, getReadingDocuments()) };
  }
  const token = state.openToken, navigationToken = ++state.navigationToken, rendition = state.rendition;
  if (target.kind === 'pdf') {
    if (target.query && rendition.goToSearchResult) await rendition.goToSearchResult(target);
    else if (target.dest && rendition.goToDestination) await rendition.goToDestination(target.dest);
    else await rendition.goToPosition({ pageNumber: target.pageNumber || target.index + 1, pageOffsetRatio: 0 });
  } else if (target.kind === 'epub') {
    await rendition.goToChapterDocIndex(target.index);
    await waitForCondition(() => state.epubWindow.mounted.get(target.index)?.iframe?.dataset.readerContentLoaded === 'yes');
  } else if (target.kind === 'txt') {
    await rendition.goToPosition({ chunkIndex: target.index, chunkProgressRatio: 0 });
  } else if (target.kind === 'chm') {
    await openChmHref(target.href);
    await waitForCondition(() => getActiveContentFrame()?.dataset.readerContentLoaded === 'yes');
  }
  if (token !== state.openToken || navigationToken !== state.navigationToken || accountSync.stopped) return;
  await settleReaderLayout();
  if (token !== state.openToken || navigationToken !== state.navigationToken || accountSync.stopped) return;
  if (target.kind !== 'pdf') {
    if (state.chmSandbox && target.query) state.chmSandbox.find(target.query);
    const entry = getReadingDocuments().find((item) => item.index === target.index) || getReadingDocuments()[0];
    if (entry) scrollToTextMatch(entry.document, target, isChmFormat(state.current.format) ? null : stage, entry.frame);
  }
  if (isMobileViewport()) closeSidebar();
  state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
  // A sidebar resize may still be debounced when navigation finishes. The
  // explicit destination becomes the anchor for that pending layout pass.
  if (state.layoutPending) state.layoutRestoreAnchor = state.lastTextAnchor;
  updateReaderProgress();
  if (!temporary) persistCurrentPosition();
};

const returnToReading = async () => {
  const restore = state.temporaryPosition;
  if (!restore || restore.item !== state.current) return;
  const token = state.openToken, navigationToken = ++state.navigationToken;
  if (isChmFormat(state.current.format)) {
    await openChmHref(restore.position.href, restore.position);
    await waitForCondition(() => token !== state.openToken || !!getActiveContentFrame()?.dataset.readerContentLoaded);
  }
  else await state.rendition?.goToPosition?.(JSON.stringify(restore.position));
  await settleReaderLayout();
  if (token !== state.openToken || navigationToken !== state.navigationToken || accountSync.stopped) return;
  restoreTextAnchor(stage, getReadingDocuments(), restore.anchor);
  state.temporaryPosition = null;
  state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments());
  if (state.layoutPending) state.layoutRestoreAnchor = state.lastTextAnchor;
  persistCurrentPosition();
};

const handleInitialSearch = async () => {
  const initial = readerConfig.initialSearch || {
    query: query.get('reader_search') || '', terms: query.getAll('reader_term'), pageNumber: Number(query.get('reader_page')) || null,
  };
  if (state.initialSearchHandled || (!initial.query && !initial.pageNumber)) return;
  state.initialSearchHandled = true;
  openSidebar();
  const terms = [...new Set([initial.query, ...(initial.terms || [])].filter(Boolean))];
  let results = [];
  for (const term of terms) { results = await readerNavigation.search(term); if (results.length) break; }
  const target = results.find((result) => result.pageNumber === Number(initial.pageNumber)) || results[0];
  if (target) await readerNavigation.navigate(target, { temporary: true });
  else if (initial.pageNumber && state.rendition?.isNativePdf) {
    await readerNavigation.navigate({ kind: 'pdf', index: initial.pageNumber - 1, pageNumber: initial.pageNumber }, { temporary: true });
  }
};

const bindReadingExtras = (doc) => {
  if (!doc?.body || doc.__recollExtrasBound) return;
  doc.__recollExtrasBound = true;
  readerExperience?.bindDocument(doc);
  doc.addEventListener('click', (event) => {
    if (doc.getSelection()?.toString()) return;
    const image = event.target.closest?.('img');
    const dialog = el('reader-image-dialog');
    if (image && dialog) {
      event.preventDefault(); event.stopPropagation();
      const preview = el('reader-image-preview'); preview.src = image.currentSrc || image.src; preview.alt = image.alt || '文档插图';
      readerExperience.showDialog(dialog, image); return;
    }
    const link = event.target.closest?.('a[href]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const isNote = link.getAttribute('role') === 'doc-noteref' || /noteref/.test(link.getAttribute('epub:type') || '')
      || /footnote|noteref|fnref/.test(`${link.className} ${link.id}`);
    if (!isNote || !href.includes('#')) return;
    const fragment = href.slice(href.indexOf('#') + 1);
    let note; try { note = doc.getElementById(decodeURIComponent(fragment)); } catch { return; }
    const noteDialog = el('reader-footnote-dialog'), content = el('reader-footnote-content');
    if (!note || !noteDialog || !content) return;
    event.preventDefault(); event.stopPropagation();
    // Notes cross the sandbox boundary. Rebuild only inert text formatting;
    // copying book markup into the account page would grant it our origin.
    const allowed = new Set(['P', 'DIV', 'SPAN', 'EM', 'STRONG', 'B', 'I', 'S', 'U', 'SMALL', 'SUP', 'SUB', 'BR', 'OL', 'UL', 'LI', 'BLOCKQUOTE', 'CODE', 'PRE']);
    const copyNote = (node) => {
      if (node.nodeType === 3) return document.createTextNode(node.textContent);
      const output = document.createDocumentFragment();
      if (node.nodeType !== 1 || /^(SCRIPT|STYLE|IFRAME|OBJECT|EMBED|FORM|SVG|MATH)$/.test(node.tagName)) return output;
      const safe = allowed.has(node.tagName) ? document.createElement(node.tagName.toLowerCase()) : output;
      for (const child of node.childNodes) safe.append(copyNote(child));
      return safe;
    };
    content.replaceChildren(copyNote(note)); readerExperience.showDialog(noteDialog, link);
  }, true);
};

const initializeReadingTools = () => {
  if (!document.querySelector('link[href*="reader-ui.css"]')) {
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = new URL('./reader-ui.css', import.meta.url).href; document.head.appendChild(link);
  }
  let settingsAnchor;
  readerSettings = new ReaderSettings({ root: readerApp, preferences: account?.settings?.preferences,
    onBeforeChange: () => { settingsAnchor = captureTextAnchor(stage, getReadingDocuments()); },
    onChange: async () => {
      const anchor = settingsAnchor || state.lastTextAnchor;
      state.reflowing = true;
      try {
        applyReaderStyle(); applyReaderStyleToMountedEpubSlots(); applyReaderStyleToMountedTxtSlots();
        await syncRenditionLayout(true); await settleReaderLayout();
        restoreTextAnchor(stage, getReadingDocuments(), anchor);
      } catch (error) {
        showReaderError(`排版更新失败：${error.message}`);
      } finally { state.reflowing = false; settingsAnchor = null; }
      state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments()); persistCurrentPosition();
    },
    onSave: async (preferences, options = {}) => {
      if (!account || accountSync.stopped) return;
      try {
        const data = await accountSync.request(`${applicationBase}/api/account/preferences`, { body: { preferences }, ...options });
        if (data.settings) account.settings = data.settings;
        window.RecollAccount?.broadcast?.('reader-settings-changed');
      } catch (error) { showReaderError(`阅读设置尚未保存：${error.message}`); }
    },
  });
  readerNavigation = new ReaderNavigation({
    getContext: () => ({ current: state.current, format: state.current?.format, rendition: state.rendition, stage,
      position: state.rendition?.getPosition?.() || getChmCurrentPosition(),
      getDocuments: getReadingDocuments, epub: { toc: state.rendition?.book?.toc, chapters: getEpubChapterDocList() },
      txt: { manifest: state.txtWindow.manifest, contentUrl: state.txtWindow.contentUrl }, chm: { toc: state.currentChmManifest?.toc },
      fetchText: async (url, options) => (await fetchReaderResponse(url, options)).text(),
      fetchJson: async (url, options) => (await fetchReaderResponse(url, options)).json(),
    }), onNavigate: navigateReaderTarget, onReturn: returnToReading, onState: updateNavigationControls,
    onKeep: async () => {
      const token = state.openToken, navigationToken = state.navigationToken;
      await waitForCondition(() => !state.layoutPending && !state.reflowing || accountSync.stopped || token !== state.openToken);
      if (accountSync.stopped || token !== state.openToken || navigationToken !== state.navigationToken) return;
      state.temporaryPosition = null;
      persistCurrentPosition();
    },
  });
  el('reader-close-sidebar')?.addEventListener('click', closeSidebar);
  for (const [id, action] of [
    ['reader-pdf-zoom-out', async () => { const p = await state.rendition?.getProgress?.(); await state.rendition?.setScale?.(Math.max(.25, (p?.scale || 1) / 1.2)); }],
    ['reader-pdf-zoom-in', async () => { const p = await state.rendition?.getProgress?.(); await state.rendition?.setScale?.(Math.min(4, (p?.scale || 1) * 1.2)); }],
    ['reader-pdf-fit', () => state.rendition?.setScale?.('page-width')],
    ['reader-pdf-rotate', () => state.rendition?.rotate?.(90)],
  ]) el(id)?.addEventListener('click', () => Promise.resolve(action()).then(async () => {
    const info = await state.rendition?.getProgress?.();
    if (el('reader-pdf-zoom-value')) el('reader-pdf-zoom-value').textContent = `${Math.round((info?.scale || 1) * 100)}%`;
  }).catch((error) => { showReaderError(`PDF 显示未更新：${error.message}`); }));
  el('reader-progress-range')?.addEventListener('change', async (event) => {
    const ratio = Number(event.target.value) / 1000, format = normalizeFormat(state.current?.format);
    if (format === 'pdf') {
      const count = (await state.rendition.getProgress()).pageCount, value = Math.min(count - .0001, ratio * count);
      await state.rendition.goToPosition({ pageNumber: Math.floor(value) + 1, pageOffsetRatio: value % 1 });
    } else if (format === 'epub' || format === 'txt') {
      const count = format === 'epub' ? getEpubChapterDocList().length : getTxtChunkDocList().length;
      const value = Math.min(count - .0001, ratio * count), index = Math.floor(value);
      await state.rendition.goToPosition(format === 'epub' ? { chapterDocIndex: index, chapterProgressRatio: value % 1, intraChapterOffset: -1 }
        : { chunkIndex: index, chunkProgressRatio: value % 1 });
    } else if (format === 'chm') {
      if (state.chmSandbox) state.chmSandbox.scrollToRatio(ratio);
      else {
        const win = getActiveContentFrame()?.contentWindow, doc = win?.document;
        const height = Math.max(doc?.body?.scrollHeight || 0, doc?.documentElement?.scrollHeight || 0);
        win?.scrollTo(0, ratio * Math.max(0, height - win.innerHeight));
      }
    } else stage.scrollTop = ratio * Math.max(0, stage.scrollHeight - stage.clientHeight);
    persistCurrentPosition();
  });
  for (const [dialogId, closeId] of [['reader-image-dialog', 'reader-close-image'], ['reader-footnote-dialog', 'reader-close-footnote']]) {
    const dialog = el(dialogId);
    el(closeId)?.addEventListener('click', () => dialog?.close());
    dialog?.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  }
  let scrollTimer;
  stage.addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      if (state.isOpening || state.reflowing || state.layoutPending) return;
      state.lastTextAnchor = captureTextAnchor(stage, getReadingDocuments()); updateReaderProgress();
    }, 120);
  }, { passive: true });
  readerExperience = new ReaderExperience({
    root: readerApp, stage,
    openSidebar: () => { openSidebar(); scheduleLayoutSync(true); },
    closeSidebar: () => { closeSidebar(); scheduleLayoutSync(true); },
    toggleToolbar, showToolbar: () => setToolbarHidden(false),
    openSearch: () => { setToolbarHidden(false); openSidebar(); readerNavigation.selectTab('search'); scheduleLayoutSync(true); },
    closeSettings: () => readerSettings.setOpen(false),
    openSettings: () => readerSettings.setOpen(true),
  });
};

initializeReadingTools();


syncViewportHeight();

(async () => {
  try {
    updateFontSizeUi();
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
updateNavigationControls();
