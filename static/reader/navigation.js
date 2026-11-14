// Navigation indexes the source document, including chapters outside the mounted
// reading window. It deliberately owns no reading-position persistence.
const MAX_RESULTS = 500;
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
const BLOCK_TAGS = new Set(['P', 'DIV', 'ARTICLE', 'SECTION', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'PRE', 'TR']);
const string = (value) => value == null ? '' : String(value);
const decode = (value) => { try { return decodeURIComponent(value); } catch { return value; } };
const hrefPath = (href) => decode(string(href).split('#')[0]).replace(/^\.\//, '');
const literalPattern = (query) => new RegExp(string(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu');

export function findTextMatches(text, query, limit = MAX_RESULTS) {
  if (!query || !query.trim()) return [];
  const matches = [];
  for (const match of string(text).matchAll(literalPattern(query))) {
    const start = Math.max(0, match.index - 48), end = Math.min(text.length, match.index + match[0].length + 80);
    matches.push({ textOffset: match.index, matchLength: match[0].length, query: match[0],
      excerpt: `${start ? '…' : ''}${text.slice(start, end).replace(/\s+/g, ' ')}${end < text.length ? '…' : ''}` });
    if (matches.length >= limit) break;
  }
  return matches;
}

export function buildTextIndex(doc) {
  let text = '';
  const segments = [];
  const newline = () => { if (text && !text.endsWith('\n')) text += '\n'; };
  const walk = (node) => {
    if (node.nodeType === 3) {
      const value = node.nodeValue || '';
      segments.push({ node, start: text.length, end: text.length + value.length });
      text += value;
      return;
    }
    if (node.nodeType !== 1 || SKIP_TAGS.has(node.tagName)) return;
    if (node.tagName === 'BR') { text += '\n'; return; }
    if (node.hasAttribute?.('data-reader-txt-line')) {
      for (const child of node.childNodes) walk(child);
      if (node.getAttribute('data-reader-line-break') === 'yes') text += '\n';
      return;
    }
    const block = BLOCK_TAGS.has(node.tagName);
    if (block) newline();
    for (const child of node.childNodes) walk(child);
    if (block) newline();
  };
  if (doc?.body) walk(doc.body);
  return { text, segments };
}

export function buildEpubOutline(toc, chapters) {
  const result = [];
  const visit = (items, depth = 0) => {
    for (const item of items || []) {
      const href = string(item.href), index = chapters.findIndex((chapter) => hrefPath(chapter.href) === hrefPath(href));
      if (index >= 0) result.push({ kind: 'epub', index, href, fragment: decode(href.split('#').slice(1).join('#')),
        label: string(item.label || item.title || chapters[index].label || `第 ${index + 1} 章`), depth: Number(item.depth) || depth });
      visit(item.subitems || item.children || item.items, depth + 1);
    }
  };
  visit(toc);
  return result.length ? result : chapters.map((chapter, index) => ({ kind: 'epub', index, href: chapter.href,
    label: chapter.label || `第 ${index + 1} 章`, depth: 0 }));
}

export function extractTxtOutline(text, index = 0) {
  // A bounded line expression avoids treating prose containing “第…章” as a heading.
  const pattern = /^(?:[ \t\u3000]*)(第[〇零一二三四五六七八九十百千万两\d]+[章节卷部回篇][^\n]{0,70}|(?:chapter|part|book)\s+[\divxlcdm]+[^\n]{0,70}|序章|序言|前言|楔子|尾声|后记|附录)(?:[ \t\r]*)$/gim;
  return [...string(text).matchAll(pattern)].map((match) => ({ kind: 'txt', index,
    label: match[1].trim(), depth: 0, textOffset: match.index + match[0].indexOf(match[1]), query: match[1].trim(), matchLength: match[1].trim().length }));
}

export function clearSearchHighlight(doc) {
  doc?.defaultView?.CSS?.highlights?.delete('reader-search');
  doc?.querySelectorAll?.('mark[data-reader-search]').forEach((mark) => {
    const parent = mark.parentNode;
    mark.replaceWith(...mark.childNodes);
    parent?.normalize();
  });
}

export function scrollToTextMatch(doc, target, stage, frame) {
  if (!doc?.body) return false;
  clearSearchHighlight(doc);
  let rect;
  if (target.fragment) {
    const node = doc.getElementById(decode(target.fragment)) || [...doc.getElementsByName(decode(target.fragment))][0];
    if (node) rect = node.getBoundingClientRect();
  }
  if (!rect) {
    const indexed = buildTextIndex(doc);
    let offset = Math.max(0, Number(target.textOffset) || 0), length = Number(target.matchLength) || string(target.query).length;
    if (target.query && indexed.text.slice(offset, offset + length).toLocaleLowerCase() !== target.query.toLocaleLowerCase()) {
      const found = findTextMatches(indexed.text, target.query, MAX_RESULTS);
      if (found.length) {
        const nearest = found.reduce((a, b) => Math.abs(a.textOffset - offset) <= Math.abs(b.textOffset - offset) ? a : b);
        offset = nearest.textOffset; length = nearest.matchLength;
      }
    }
    const first = indexed.segments.find((segment) => segment.end > offset);
    const last = [...indexed.segments].reverse().find((segment) => segment.start < offset + Math.max(1, length));
    if (!first || !last) return false;
    const range = doc.createRange();
    range.setStart(first.node, Math.max(0, offset - first.start));
    range.setEnd(last.node, Math.min(last.node.length, Math.max(0, offset + Math.max(1, length) - last.start)));
    rect = range.getBoundingClientRect();
    if (length > 0 && target.query) {
      let style = doc.getElementById('reader-search-highlight-style');
      if (!style) { style = doc.createElement('style'); style.id = 'reader-search-highlight-style'; doc.head?.append(style); }
      style.textContent = '::highlight(reader-search){background:#ffd778;color:#201b13}mark[data-reader-search]{background:#ffd778!important;color:#201b13!important}';
      const win = doc.defaultView;
      if (win?.CSS?.highlights && win.Highlight) {
        win.CSS.highlights.set('reader-search', new win.Highlight(range));
      } else {
        // Mark each text node separately, preserving links and inline formatting.
        for (const segment of [...indexed.segments].reverse()) {
          if (segment.end <= offset || segment.start >= offset + length) continue;
          const part = doc.createRange();
          part.setStart(segment.node, Math.max(0, offset - segment.start));
          part.setEnd(segment.node, Math.min(segment.node.length, offset + length - segment.start));
          const mark = doc.createElement('mark'); mark.dataset.readerSearch = 'yes';
          part.surroundContents(mark);
        }
      }
    }
  }
  if (stage && frame) {
    const frameTop = frame.getBoundingClientRect().top - stage.getBoundingClientRect().top + stage.scrollTop;
    stage.scrollTop = Math.max(0, frameTop + rect.top + (doc.defaultView?.scrollY || 0) - 48);
  } else doc.defaultView?.scrollTo({ top: Math.max(0, rect.top + (doc.defaultView.scrollY || 0) - 48), behavior: 'auto' });
  return true;
}

export class ReaderNavigation {
  constructor({ getContext, onNavigate, onReturn, onKeep, onState, document: uiDocument = globalThis.document,
    fetchImpl = globalThis.fetch?.bind(globalThis), parseDocument } = {}) {
    this.getContext = getContext || (() => ({}));
    this.onNavigate = onNavigate || (() => {}); this.onReturn = onReturn || (() => {});
    this.onKeep = onKeep || (() => {}); this.onState = onState || (() => {});
    this.document = uiDocument; this.fetch = fetchImpl;
    this.parseDocument = parseDocument || ((text) => new DOMParser().parseFromString(text, 'text/html'));
    this.outline = []; this.results = []; this.activeIndex = -1; this.activeOutlineIndex = -1; this.canReturn = false;
    this.generation = 0; this.searchGeneration = 0; this.outlineGeneration = 0; this.navigationGeneration = 0; this.textCache = new Map(); this.tab = 'toc';
    this.requests = new Set(); this.stopped = false; this.navigationPosition = null;
    this.attach();
  }
  el(id) { return this.document?.getElementById(id); }
  attach() {
    const tabs = ['toc', 'books', 'search'];
    for (const name of tabs) {
      const button = this.el(`reader-tab-${name}`);
      button?.addEventListener('click', () => this.selectTab(name));
      button?.addEventListener('keydown', (event) => {
        let next;
        if (event.key === 'ArrowRight') next = (tabs.indexOf(name) + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (tabs.indexOf(name) + tabs.length - 1) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault(); this.selectTab(tabs[next]); this.el(`reader-tab-${tabs[next]}`)?.focus();
      });
    }
    const submit = () => void this.search(this.el('reader-search-input')?.value || '');
    this.el('reader-search-submit')?.addEventListener('click', submit);
    this.el('reader-search-input')?.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } });
    this.el('reader-return-position')?.addEventListener('click', () => void this.returnToReading());
    this.el('reader-keep-position')?.addEventListener('click', () => void this.keepReadingHere());
    this.selectTab('toc');
  }
  selectTab(name) {
    this.tab = name;
    for (const tab of ['toc', 'books', 'search']) {
      const selected = name === tab, button = this.el(`reader-tab-${tab}`), panel = this.el(`reader-panel-${tab}`);
      if (button) { button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; button.classList.toggle('is-active', selected); }
      if (panel) panel.hidden = !selected;
    }
    if (name === 'search') this.el('reader-search-input')?.focus();
  }
  emit(extra = {}) {
    for (const id of ['reader-return-position', 'reader-keep-position', 'reader-return-banner']) {
      const element = this.el(id); if (element) element.hidden = !this.canReturn;
    }
    this.onState({ outline: this.outline, results: this.results, activeIndex: this.activeIndex,
      activeOutlineIndex: this.activeOutlineIndex, canReturn: this.canReturn, ...extra });
  }
  status(message) { const node = this.el('reader-search-status'); if (node) node.textContent = message; }
  context() { const context = this.getContext() || {}; return { ...context, format: string(context.format || context.current?.format).toLowerCase() }; }
  documents(context = this.context()) { return context.getDocuments?.() || context.documents || []; }
  reset() {
    this.stopped = true;
    this.generation += 1; this.searchGeneration += 1; this.outlineGeneration += 1; this.navigationGeneration += 1;
    for (const controller of this.requests) controller.abort();
    this.requests.clear(); this.clearHighlights(); this.textCache.clear();
    this.outline = []; this.results = []; this.activeIndex = -1; this.activeOutlineIndex = -1; this.canReturn = false;
    this.navigationPosition = null;
    if (this.boundStage && this.scrollListener) this.boundStage.removeEventListener('scroll', this.scrollListener);
    this.boundStage = null; this.scrollListener = null;
    this.el('reader-toc-list')?.replaceChildren(); this.el('reader-search-results')?.replaceChildren();
    if (this.el('reader-search-input')) this.el('reader-search-input').value = '';
    this.status(''); this.emit({ loading: false, query: '' });
  }
  async refresh() {
    this.navigationGeneration += 1;
    this.stopped = false;
    for (const controller of this.requests) controller.abort();
    this.requests.clear();
    const token = ++this.generation;
    this.searchGeneration += 1; this.outlineGeneration += 1; this.textCache.clear(); this.results = []; this.outline = [];
    this.activeOutlineIndex = -1; this.canReturn = false; this.navigationPosition = null;
    this.renderResults(); this.status(''); this.emit();
    const context = this.context();
    try {
      if (context.format === 'epub') this.outline = buildEpubOutline(context.epub?.toc || context.rendition?.book?.toc, context.epub?.chapters || context.rendition?.chapterDocList || []);
      else if (context.format === 'pdf') {
        let outline = await context.rendition?.getOutline?.() || [];
        if (!outline.length) {
          const progress = await context.rendition?.getProgress?.();
          outline = Array.from({ length: Number(progress?.pageCount) || 0 }, (_, index) => ({ label: `第 ${index + 1} 页`, pageNumber: index + 1, depth: 0 }));
        }
        if (token !== this.generation) return [];
        this.outline = outline.map((item) => ({ ...item, kind: 'pdf', index: Math.max(0, Number(item.pageNumber || 1) - 1) }));
      } else if (context.format === 'chm') this.outline = (context.chm?.toc || []).map((item, index) => ({ ...item, kind: 'chm', index,
        fragment: item.fragment || decode(string(item.href).split('#').slice(1).join('#')) }));
      else if (context.format === 'txt') {
        const chunks = context.txt?.manifest?.chunks || [];
        const manifestToc = context.txt?.manifest?.toc;
        this.outline = Array.isArray(manifestToc) && manifestToc.length ? manifestToc.map((item) => ({ ...item, kind: 'txt' }))
          : chunks.map((chunk, index) => ({ kind: 'txt', index, label: chunk.label || `文本区段 ${index + 1}`, depth: 0 }));
        // Opening a large book must not await an entire-book download. Improve
        // this usable initial outline incrementally, with just one request at a time.
        if (!Array.isArray(manifestToc)) void this.buildTxtOutline(context, token, this.outlineGeneration);
      } else {
        for (const { document: doc, index = 0 } of this.documents(context)) {
          const indexed = buildTextIndex(doc);
          for (const heading of doc?.querySelectorAll('h1,h2,h3,h4,h5,h6') || []) {
            const label = heading.textContent.trim(); if (!label) continue;
            const segment = indexed.segments.find((entry) => heading.contains(entry.node));
            this.outline.push({ kind: 'html', index, label, depth: Number(heading.tagName.slice(1)) - 1,
              fragment: heading.id || '', textOffset: segment?.start || 0, query: label, matchLength: label.length, element: heading });
          }
        }
      }
      if (token !== this.generation) return [];
      if (context.stage && this.boundStage !== context.stage) {
        if (this.boundStage && this.scrollListener) this.boundStage.removeEventListener('scroll', this.scrollListener);
        this.scrollListener = () => this.updateActiveHeading();
        context.stage.addEventListener('scroll', this.scrollListener, { passive: true }); this.boundStage = context.stage;
      }
      this.renderOutline(); this.emit(); return this.outline;
    } catch (error) {
      if (token === this.generation) { this.renderOutline(`目录未加载：${error.message}`); this.emit({ error: error.message }); }
      return [];
    }
  }
  async buildTxtOutline(context, generation, token) {
    const chunks = context.txt?.manifest?.chunks || [], headings = [];
    try {
      for (let index = 0; index < chunks.length; index += 1) {
        if (generation !== this.generation || token !== this.outlineGeneration) return;
        const text = await this.loadText(context, index);
        if (generation !== this.generation || token !== this.outlineGeneration) return;
        headings.push(...extractTxtOutline(text, index));
        if (headings.length) {
          this.outline = [...headings, ...chunks.slice(index + 1).map((chunk, offset) => ({ kind: 'txt', index: index + offset + 1,
            label: chunk.label || `文本区段 ${index + offset + 2}`, depth: 0 }))];
          this.renderOutline();
        }
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      this.emit();
    } catch (error) {
      if (generation === this.generation && token === this.outlineGeneration) this.emit({ error: `章节识别未完成：${error.message}` });
    }
  }
  async loadText(context, index) {
    const generation = this.generation;
    const key = `${context.format}:${index}`;
    if (this.textCache.has(key)) return this.textCache.get(key);
    let text = '';
    if (context.format === 'txt') {
      const url = `${context.txt.contentUrl || context.txt.manifest.contentUrl}/${index}`;
      const data = await this.requestSource(context, url, 'json');
      text = string(data.text).replace(/\r\n?/g, '\n');
    } else if (context.format === 'epub' || context.format === 'chm') {
      const chapter = context.format === 'chm' ? this.chmSections(context)[index] : (context.epub?.chapters || context.rendition?.chapterDocList || [])[index];
      let source = chapter?.url || await chapter?.text?.load?.();
      if (generation !== this.generation || this.stopped) throw new DOMException('阅读文档已关闭', 'AbortError');
      if (source?.body) text = buildTextIndex(source).text;
      else if (source) {
        if (!string(source).trim().startsWith('<')) {
          source = await this.requestSource(context, source, 'text');
        }
        text = buildTextIndex(this.parseDocument(source)).text;
      }
    } else text = buildTextIndex(this.documents(context).find((entry) => entry.index === index)?.document || this.documents(context)[0]?.document).text;
    if (generation === this.generation) this.textCache.set(key, text);
    if (this.textCache.size > 8) this.textCache.delete(this.textCache.keys().next().value);
    return text;
  }
  async requestSource(context, url, type) {
    const controller = new AbortController(); this.requests.add(controller);
    const options = { signal: controller.signal, credentials: 'same-origin' };
    try {
      if (type === 'json' && context.fetchJson) return await context.fetchJson(url, options);
      if (context.fetchText) {
        const text = await context.fetchText(url, options);
        return type === 'json' ? JSON.parse(text) : text;
      }
      const response = await this.fetch(url, options);
      if (!response.ok) throw new Error(`内容加载失败（${response.status}）`);
      return await response[type]();
    } finally { this.requests.delete(controller); }
  }
  async search(query) {
    if (this.stopped) return [];
    query = string(query).trim().slice(0, 200);
    const token = ++this.searchGeneration, generation = this.generation;
    this.outlineGeneration += 1;
    const context = this.context();
    this.results = []; this.renderResults(); this.selectTab('search');
    if (this.el('reader-search-input')) this.el('reader-search-input').value = query;
    if (!query) { this.status('输入要在书中查找的文字'); return []; }
    this.status('正在搜索全书…'); this.emit({ loading: true, query });
    try {
      if (context.format === 'pdf' && context.rendition?.search) {
        const results = await context.rendition.search(query);
        if (token !== this.searchGeneration || generation !== this.generation) return [];
        this.results = (Array.isArray(results) ? results : results?.results || []).slice(0, MAX_RESULTS).map((result) => ({ ...result, kind: 'pdf', query: result.query || query,
          label: result.label || `第 ${result.pageNumber} 页`, index: Number(result.pageNumber || 1) - 1 }));
      } else {
        const chapters = context.format === 'chm' ? this.chmSections(context) : context.epub?.chapters || context.rendition?.chapterDocList || [];
        const count = context.format === 'txt' ? (context.txt?.manifest?.chunks || []).length : ['epub', 'chm'].includes(context.format) ? chapters.length : Math.max(1, this.documents(context).length);
        for (let index = 0; index < count; index += 1) {
          if (token !== this.searchGeneration || generation !== this.generation) return [];
          const text = await this.loadText(context, index);
          if (token !== this.searchGeneration || generation !== this.generation) return [];
          const label = ['epub', 'chm'].includes(context.format) ? chapters[index]?.label : context.format === 'txt' ? `文本区段 ${index + 1}` : context.current?.title || '正文';
          const kind = ['txt', 'epub', 'chm'].includes(context.format) ? context.format : 'html';
          this.results.push(...findTextMatches(text, query, MAX_RESULTS - this.results.length).map((match) => ({ ...match, kind, index, label, ...(kind === 'chm' ? { href: chapters[index]?.href } : {}) })));
          this.status(`已检索 ${index + 1} / ${count} 个区段 · ${this.results.length} 处命中`);
          if (this.results.length >= MAX_RESULTS) break;
          if (index % 4 === 0) { this.renderResults(); await new Promise((resolve) => setTimeout(resolve, 0)); }
        }
      }
      this.renderResults(); this.status(this.results.length >= MAX_RESULTS ? `显示前 ${MAX_RESULTS} 处命中，请缩小查找范围` : this.results.length ? `找到 ${this.results.length} 处命中` : '全文未找到这段文字，可尝试更短的词语');
      this.emit({ loading: false, query }); return this.results;
    } catch (error) {
      if (token === this.searchGeneration && generation === this.generation) { this.renderResults(); this.status(`搜索未完成：${error.message}`); this.emit({ loading: false, error: error.message }); }
      return this.results;
    }
  }
  async navigate(target, { temporary = false } = {}) {
    if (this.stopped) return;
    const token = ++this.navigationGeneration;
    const previous = this.canReturn;
    if (temporary) this.canReturn = true;
    try {
      await this.onNavigate(target, { temporary });
      if (token !== this.navigationGeneration || this.stopped) return;
      this.setActive(target.index);
      const ordinal = this.outline.indexOf(target);
      if (ordinal >= 0) {
        this.setActiveOutline(ordinal);
        this.navigationPosition = { target, key: this.readingPositionKey(this.context()) };
      }
      this.emit();
    } catch (error) {
      if (token !== this.navigationGeneration || this.stopped) return;
      this.canReturn = previous; this.status(`跳转失败：${error.message}`); this.emit({ error: error.message });
    }
  }
  async navigateAdjacent(direction) {
    if (this.stopped || !this.outline.length) return;
    this.updateActiveHeading();
    const ordinal = this.activeOutlineIndex + direction;
    if (ordinal >= 0 && ordinal < this.outline.length) await this.navigate(this.outline[ordinal]);
  }
  async returnToReading() {
    if (this.stopped) return;
    const token = ++this.navigationGeneration;
    try {
      await this.onReturn();
      if (token !== this.navigationGeneration || this.stopped) return;
      this.canReturn = false; this.clearHighlights(); this.emit();
    } catch (error) { if (token === this.navigationGeneration && !this.stopped) this.status(`返回失败：${error.message}`); }
  }
  async keepReadingHere() {
    if (this.stopped) return;
    const token = ++this.navigationGeneration;
    try {
      await this.onKeep();
      if (token !== this.navigationGeneration || this.stopped) return;
      this.canReturn = false; this.clearHighlights(); this.emit({ keepReading: true });
    } catch (error) { if (token === this.navigationGeneration && !this.stopped) this.status(`阅读位置未更新：${error.message}`); }
  }
  clearHighlights() { for (const entry of this.documents()) clearSearchHighlight(entry.document); }
  chmSections(context) {
    const unique = new Map();
    for (const item of context.chm?.toc || []) {
      const href = hrefPath(item.href); if (href && !unique.has(href)) unique.set(href, { ...item, href, url: string(item.url).split('#')[0] });
    }
    return [...unique.values()];
  }
  readingPositionKey(context) {
    const position = context.position || {};
    return JSON.stringify([context.stage?.scrollTop, position.chunkIndex, position.chapterDocIndex,
      position.pageNumber, position.pageOffsetRatio, position.href, position.scrollTop, position.scrollLeft]);
  }
  updateActiveHeading() {
    const context = this.context(), position = context.position || {}, documents = this.documents(context);
    // A target near the document end may be unable to reach the viewport top.
    // Keep the chosen entry until the actual reading position changes.
    if (this.navigationPosition?.key === this.readingPositionKey(context)) {
      const ordinal = this.outline.indexOf(this.navigationPosition.target);
      if (ordinal >= 0) { this.setActiveOutline(ordinal); return; }
    }
    this.navigationPosition = null;
    const viewportTop = context.stage ? context.stage.getBoundingClientRect().top + 80 : null;
    let index = context.format === 'txt' ? Number(position.chunkIndex) : context.format === 'pdf'
      ? Number(position.pageNumber) - 1 : Number(position.chapterDocIndex);
    if (!Number.isFinite(index)) index = this.activeIndex;
    if (viewportTop != null && context.format !== 'chm') {
      for (const entry of documents) {
        if (entry.frame?.getBoundingClientRect().top <= viewportTop) index = entry.index;
      }
    }
    let selected = -1;
    if (context.format === 'chm') {
      const href = decode(string(position.href)).replace(/^\.\//, '');
      selected = this.outline.findIndex((item) => decode(string(item.href)).replace(/^\.\//, '') === href);
      if (selected < 0) selected = this.outline.findIndex((item) => hrefPath(item.href) === hrefPath(href));
    } else {
      for (let ordinal = 0; ordinal < this.outline.length; ordinal += 1) {
        const item = this.outline[ordinal];
        if (item.index < index || (context.format === 'pdf' && item.index === index)) selected = ordinal;
      }
      const current = this.outline.findIndex((item) => item.index === index);
      if (context.format !== 'pdf' && current >= 0) {
        selected = this.outline[this.activeOutlineIndex]?.index === index ? this.activeOutlineIndex : current;
      }
    }
    // A source file can contain several directory entries. Resolve their actual
    // positions in mounted documents rather than treating its index as a TOC index.
    const indexes = new Map();
    let measured = false, before = -1;
    if (viewportTop != null && context.format !== 'pdf') this.outline.forEach((item, ordinal) => {
      const entry = context.format === 'chm' ? (hrefPath(item.href) === hrefPath(position.href) ? documents[0] : null)
        : documents.find((candidate) => candidate.index === item.index);
      const doc = entry?.document; if (!doc?.body || !entry.frame) return;
      let node = item.element?.isConnected ? item.element : null;
      const fragment = item.fragment || (context.format === 'chm' ? string(item.href).split('#')[1] : '');
      if (!node && fragment) node = doc.getElementById(decode(fragment)) || doc.getElementsByName(decode(fragment))[0];
      let rect = node?.getBoundingClientRect();
      if (!rect) {
        if (item.textOffset == null && !item.query) return;
        if (!indexes.has(doc)) indexes.set(doc, buildTextIndex(doc));
        const offset = Number(item.textOffset) || 0;
        const segment = indexes.get(doc).segments.find((part) => part.end > offset);
        if (!segment) return;
        const range = doc.createRange(), start = Math.max(0, offset - segment.start);
        range.setStart(segment.node, start); range.setEnd(segment.node, Math.min(segment.node.length, start + 1));
        rect = range.getBoundingClientRect();
      }
      const top = rect.top + entry.frame.getBoundingClientRect().top;
      measured = true;
      if (top <= viewportTop) before = ordinal;
    });
    if (measured) {
      if (before >= 0) selected = before;
      else selected = this.outline.findLastIndex((item) => item.index < index);
    }
    this.setActiveOutline(selected);
  }
  setActive(index) {
    this.activeIndex = Number(index);
    this.updateActiveHeading();
  }
  setActiveOutline(ordinal) {
    const changed = ordinal !== this.activeOutlineIndex;
    this.activeOutlineIndex = ordinal;
    this.el('reader-toc-list')?.querySelectorAll('[data-section-index]').forEach((node, index) => {
      const active = index === ordinal;
      node.classList.toggle('is-active', active);
      if (active) node.setAttribute('aria-current', 'location'); else node.removeAttribute('aria-current');
    });
    if (changed) this.emit();
  }
  renderOutline(emptyMessage = '当前文档没有章节标题') {
    const parent = this.el('reader-toc-list'); if (!parent) { this.updateActiveHeading(); return; }
    parent.replaceChildren();
    for (const item of this.outline) {
      const button = this.document.createElement('button'); button.type = 'button'; button.className = 'reader-book-item reader-toc-item';
      button.dataset.sectionIndex = String(item.index); button.style.paddingLeft = `${14 + Math.min(8, Math.max(0, item.depth || 0)) * 14}px`;
      button.textContent = item.label || item.title || item.href || '章节';
      button.addEventListener('click', () => void this.navigate(item)); parent.append(button);
    }
    if (!this.outline.length) { const message = this.document.createElement('p'); message.className = 'reader-navigation-empty'; message.textContent = emptyMessage; parent.append(message); }
    this.setActive(this.activeIndex);
  }
  renderResults() {
    const parent = this.el('reader-search-results'); if (!parent) return;
    parent.replaceChildren();
    for (const result of this.results) {
      const button = this.document.createElement('button'); button.type = 'button'; button.className = 'reader-book-item reader-search-result';
      const title = this.document.createElement('strong'); title.textContent = result.label || '正文';
      const excerpt = this.document.createElement('small'); excerpt.textContent = result.excerpt || result.snippet || result.query;
      button.append(title, excerpt); button.addEventListener('click', () => void this.navigate(result, { temporary: true })); parent.append(button);
    }
  }
}
