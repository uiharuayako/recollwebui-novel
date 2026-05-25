const query = new URLSearchParams(window.location.search);
const mode = window.RECOLL_READER?.mode || query.get('mode') || 'book';
const resnum = query.get('resnum') || '0';
const rawQueryString = (window.RECOLL_READER?.queryString || window.location.search.replace(/^\?/, '')).replaceAll('&amp;', '&');
const searchParams = new URLSearchParams(rawQueryString);
searchParams.set('resnum', resnum);
searchParams.set('mode', mode);
const baseApi = '/api/reader';
const state = {
  items: [],
  currentIndex: 0,
  current: null,
  rendition: null,
  folderMeta: null,
  bookMeta: null,
  currentBlobUrl: '',
  parserRegex: localStorage.getItem('recoll-reader:parserRegex') || '',
};

window.Kookit ||= {};

const el = (id) => document.getElementById(id);
const stage = el('page-area');
const booklist = el('reader-booklist');
const titleEl = el('reader-title');
const metaEl = el('reader-meta');
const sidebar = el('reader-sidebar');
const parserInput = el('reader-parser-regex');
parserInput.value = state.parserRegex;

const safeText = (value) => (value == null ? '' : String(value));
const escapeHtml = (value) => safeText(value)
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

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
  if (!state.items.length) {
    booklist.innerHTML = '<div class="reader-loading">当前文件夹没有可阅读文件</div>';
    return;
  }
  booklist.innerHTML = state.items.map((item, idx) => `
    <button class="reader-book-item ${idx === state.currentIndex ? 'is-active' : ''}" data-index="${idx}">
      ${escapeHtml(item.title || item.name || 'Untitled')}
      <small>${escapeHtml(item.format || '')} ${item.path ? escapeHtml(item.path.replace(item.root || '', '')) : ''}</small>
    </button>`).join('');
  booklist.querySelectorAll('[data-index]').forEach((btn) => {
    btn.addEventListener('click', () => openIndex(Number(btn.dataset.index)));
  });
};

const setLoading = (text) => {
  stage.innerHTML = `<div class="reader-loading">${escapeHtml(text || '加载中…')}</div>`;
};

const setError = (text) => {
  stage.innerHTML = `<div class="reader-error">${escapeHtml(text || '加载失败')}</div>`;
};

const cleanupRendition = () => {
  if (state.rendition && typeof state.rendition.removeContent === 'function') {
    try { state.rendition.removeContent(); } catch (err) {}
  }
  state.rendition = null;
  if (state.currentBlobUrl) {
    URL.revokeObjectURL(state.currentBlobUrl);
    state.currentBlobUrl = '';
  }
};

const fetchJson = async (url) => {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return await res.json();
};

const openIndex = async (index) => {
  if (!state.items.length) return;
  const nextIndex = Math.max(0, Math.min(index, state.items.length - 1));
  const item = state.items[nextIndex];
  state.currentIndex = nextIndex;
  state.current = item;
  updateMeta();
  renderSidebar();
  cleanupRendition();
  setLoading(`正在打开 ${item.name || item.title || ''}`);
  try {
    const response = await fetch(item.url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`读取文件失败: ${response.status}`);
    const buffer = await response.arrayBuffer();
    const Kookit = window.Kookit;
    const options = {
      format: item.format.toUpperCase(),
      readerMode: 'double',
      charset: item.format === 'txt' ? (item.charset || 'utf-8') : undefined,
      animation: '',
      convertChinese: 'no',
      fullTranslationMode: 'no',
      textOrientation: 'horizontal',
      parserRegex: item.format === 'txt' ? (state.parserRegex || '') : '',
      isDarkMode: 'no',
      isMobile: 'no',
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
      isMobile: 'no',
    };
    const rendition = window.BookHelper.getRendition(buffer, options, Kookit);
    state.rendition = rendition;
    window.rendition = rendition;
    const savedPosition = item.format === 'txt' ? loadPosition(item) : null;
    if (savedPosition) {
      await rendition.renderTo(stage, savedPosition);
    } else {
      await rendition.renderTo(stage);
      if (item.format === 'txt') {
        await rendition.goToPosition(JSON.stringify({
          text: '',
          chapterTitle: '',
          chapterDocIndex: 0,
          chapterHref: item.href || 'title0',
          count: '',
          page: '',
          percentage: '0',
        }));
      }
    }
    const progressBadge = document.createElement('div');
    progressBadge.className = 'reader-progress';
    progressBadge.textContent = '0%';
    stage.appendChild(progressBadge);
    const updateProgress = async () => {
      try {
        const progress = await rendition.getProgress();
        const position = rendition.getPosition ? rendition.getPosition() : {};
        progressBadge.textContent = progress && typeof progress.percentage !== 'undefined' ? `${Math.round(progress.percentage * 100)}%` : '阅读中';
        savePosition(item, { ...position, progress });
        if (state.folderMeta) {
          saveFolderCursor(state.folderMeta);
        }
      } catch (err) {}
    };
    rendition.on('rendered', updateProgress);
    rendition.on('page-changed', updateProgress);
    rendition.on('chapter-changed', updateProgress);
    await updateProgress();
  } catch (err) {
    console.error(err);
    setError(err?.message || '打开阅读器失败');
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
  state.currentIndex = typeof saved?.currentIndex === 'number' ? Math.min(saved.currentIndex, Math.max(0, state.items.length - 1)) : (data.currentIndex || 0);
  state.current = state.items[state.currentIndex] || state.items[0] || null;
  renderSidebar();
  updateMeta();
  if (data.truncated) {
    metaEl.textContent += ` · 已截断至 ${state.items.length} 本`;
  }
  await openIndex(state.currentIndex);
};

el('reader-toggle-toc').addEventListener('click', () => {
  sidebar.classList.toggle('is-hidden');
});

el('reader-prev-book').addEventListener('click', () => {
  if (state.items.length > 1) openIndex(state.currentIndex - 1);
});

el('reader-next-book').addEventListener('click', () => {
  if (state.items.length > 1) openIndex(state.currentIndex + 1);
});

el('reader-save-regex').addEventListener('click', () => {
  state.parserRegex = parserInput.value.trim();
  localStorage.setItem('recoll-reader:parserRegex', state.parserRegex);
  if (state.current && state.current.format === 'txt') {
    openIndex(state.currentIndex);
  }
});

window.addEventListener('beforeunload', () => {
  if (state.current) {
    const position = state.rendition?.getPosition ? state.rendition.getPosition() : {};
    savePosition(state.current, position);
  }
  if (state.folderMeta) saveFolderCursor(state.folderMeta);
});

(async () => {
  try {
    if (!window.BookHelper) {
      const mod = await import('/static/reader/kookit.bundle.js');
      const exported = mod?.default || mod;
      window.Kookit = window.Kookit && Object.keys(window.Kookit).length ? window.Kookit : exported;
      window.BookHelper = window.BookHelper || exported?.BookHelper;
      window.StyleHelper = window.StyleHelper || exported?.StyleHelper;
    }
    if (!window.BookHelper) {
      throw new Error('Koodo reader runtime failed to initialize');
    }
    if (mode === 'folder') {
      await loadFolderMode();
    } else {
      await loadBookMode();
    }
  } catch (err) {
    console.error(err);
    setError(err?.message || '初始化阅读器失败');
  }
})();
