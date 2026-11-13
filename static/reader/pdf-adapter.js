let stylesReady;

function loadStyles() {
  if (!stylesReady) {
    stylesReady = Promise.all(['./vendor/pdfjs/6.5.438/pdf_viewer.css', './pdf.css'].map((path) => new Promise((resolve, reject) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = new URL(path, import.meta.url).href;
      link.onload = resolve;
      link.onerror = () => reject(new Error('PDF 阅读样式未能加载'));
      document.head.appendChild(link);
    }))).catch((error) => { stylesReady = null; throw error; });
  }
  return stylesReady;
}

export async function createPdfRendition(source, options = {}) {
  const pdfAssets = new URL('./vendor/pdfjs/6.5.438/', import.meta.url);
  const pdfjs = await import('./vendor/pdfjs/6.5.438/pdf.min.mjs');
  const components = await import('./vendor/pdfjs/6.5.438/pdf_viewer.mjs');
  await loadStyles();
  pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdf.worker.min.mjs', pdfAssets).href;
  let documentSource;
  if (typeof source === 'string') {
    const url = new URL(source, window.location.href);
    if (url.origin !== window.location.origin) throw new Error('PDF 必须从本站文档接口加载');
    documentSource = { url: url.href, withCredentials: true };
  } else {
    documentSource = { data: new Uint8Array(source) };
  }
  const loadingTask = pdfjs.getDocument({
    ...documentSource,
    cMapUrl: new URL('cmaps/', pdfAssets).href,
    cMapPacked: true,
    standardFontDataUrl: new URL('standard_fonts/', pdfAssets).href,
    wasmUrl: new URL('wasm/', pdfAssets).href,
    iccUrl: new URL('iccs/', pdfAssets).href,
    enableXfa: false,
    disableWorkerRendering: true,
  });
  options.onLoadingTask?.(loadingTask);
  loadingTask.onPassword = (updatePassword, reason) => {
    const password = window.prompt(reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD
      ? 'PDF 密码错误，请重新输入：' : '请输入 PDF 密码：');
    if (password == null) void loadingTask.destroy().catch(() => {});
    else updatePassword(password);
  };
  try {
    return new PdfRendition(await loadingTask.promise, {
      ...options, loadingTask, components, pdfjs, imageResourcesPath: new URL('images/', pdfAssets).href,
    });
  } catch (error) {
    await loadingTask.destroy().catch(() => {});
    throw new Error(`PDF 打开失败：${error.message}`);
  }
}

const clamp = (number, min, max) => Math.max(min, Math.min(max, number));

export class PdfRendition {
  constructor(pdf, { loadingTask, initialPosition = null, onError = () => {}, components, pdfjs, imageResourcesPath = '' } = {}) {
    this.pdf = pdf;
    this.loadingTask = loadingTask;
    this.components = components;
    this.pdfjs = pdfjs;
    this.imageResourcesPath = imageResourcesPath;
    this.format = 'PDF';
    this.readerMode = 'scroll';
    this.isNativePdf = true;
    this.isAllowScript = 'no';
    this.events = new Map();
    this.onError = onError;
    this.initialPosition = this.normalizePosition(initialPosition);
    this.destroyed = false;
    this.isRendering = false;
    this.abortController = new AbortController();
    this.navigationToken = 0;
    this.searchToken = 0;
    this.searchResults = [];
    this.searchQuery = '';
    this.searchExcerpts = new Map();
    this.handleScroll = () => { if (!this.isRendering) this.emit('position-changed'); };
  }

  normalizePosition(position) {
    const number = Number(position?.pageNumber ?? (Number(position?.chapterDocIndex ?? 0) + 1));
    const ratio = Number(position?.pageOffsetRatio ?? 0);
    const scale = position?.scaleValue;
    const rotation = Number(position?.rotation);
    return {
      pageNumber: Number.isFinite(number) ? clamp(Math.floor(number), 1, this.pdf.numPages) : 1,
      pageOffsetRatio: Number.isFinite(ratio) ? clamp(ratio, 0, 1) : 0,
      scaleValue: Number.isFinite(Number(scale)) && Number(scale) > 0 ? clamp(Number(scale), 0.25, 5) : 'page-width',
      rotation: Number.isFinite(rotation) && rotation % 90 === 0 ? ((rotation % 360) + 360) % 360 : 0,
    };
  }

  on(name, callback) {
    if (!this.events.has(name)) this.events.set(name, new Set());
    this.events.get(name).add(callback);
    return () => this.events.get(name)?.delete(callback);
  }

  emit(name) {
    if (this.destroyed) return;
    for (const callback of this.events.get(name) || []) callback();
  }

  waitForEvent(name, predicate = () => true, navigationSignal = null) {
    return new Promise((resolve, reject) => {
      const signal = navigationSignal
        ? AbortSignal.any([this.abortController.signal, navigationSignal]) : this.abortController.signal;
      const finish = (error, event) => {
        clearTimeout(timer);
        this.eventBus.off(name, handler);
        signal.removeEventListener('abort', abort);
        if (error) reject(error); else resolve(event);
      };
      const handler = (event) => { if (predicate(event)) finish(event.error || null, event); };
      const abort = () => finish(new DOMException('阅读已关闭', 'AbortError'));
      const timer = setTimeout(() => finish(new Error('PDF 页面加载超时')), 30000);
      this.eventBus.on(name, handler);
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    });
  }

  async renderTo(stage) {
    this.stage = stage;
    this.wrapper = document.createElement('div');
    this.wrapper.className = 'pdfViewer reader-pdf-pages';
    this.wrapper.setAttribute('aria-label', 'PDF 阅读正文');
    stage.classList.add('pdf-reader-stage');
    stage.parentElement?.classList.add('pdf-reader-shell');
    stage.replaceChildren(this.wrapper);
    stage.addEventListener('scroll', this.handleScroll, { passive: true });
    const { EventBus, PDFViewer, PDFLinkService, PDFFindController, LinkTarget } = this.components;
    const rendition = this;
    // Search fills the result list without moving the reader away from their place.
    class ReaderLinkService extends PDFLinkService {
      get page() { return super.page; }
      set page(value) { if (!rendition.collectingSearch) super.page = value; }
      goToDestination(dest) { return rendition.goToDestination(dest); }
    }
    class ReaderFindController extends PDFFindController {
      match(query, content, index) {
        const matches = super.match(query, content, index) || [];
        rendition.searchExcerpts.set(index, matches.map(({ index: offset, length }) =>
          content.slice(Math.max(0, offset - 45), offset + length + 65).replace(/\s+/g, ' ').trim()));
        return matches;
      }
      // Result selection is explicit and handled below after its text layer exists.
      scrollMatchIntoView() {}
    }
    this.eventBus = new EventBus();
    this.linkService = new ReaderLinkService({
      eventBus: this.eventBus, externalLinkTarget: LinkTarget.BLANK,
      externalLinkRel: 'noopener noreferrer nofollow', ignoreDestinationZoom: true,
    });
    this.findController = new ReaderFindController({
      eventBus: this.eventBus, linkService: this.linkService, delay: 0, updateMatchesCountOnProgress: false,
    });
    this.viewer = new PDFViewer({
      container: stage, viewer: this.wrapper, eventBus: this.eventBus,
      linkService: this.linkService, findController: this.findController,
      textLayerMode: 1, annotationMode: this.pdfjs.AnnotationMode.ENABLE,
      annotationEditorMode: this.pdfjs.AnnotationEditorType.DISABLE,
      imageResourcesPath: this.imageResourcesPath, maxCanvasPixels: 16000000,
      enableAutoLinking: true, abortSignal: this.abortController.signal,
    });
    this.linkService.setViewer(this.viewer);
    this.linkService.setDocument(this.pdf);
    this.eventBus.on('pagerendered', (event) => {
      if (event.error) this.onError(event.error);
      else this.emit('rendered');
    });
    this.eventBus.on('pagechanging', () => this.emit('page-changed'));
    this.eventBus.on('scalechanging', () => this.emit('scale-changed'));
    const initialized = this.waitForEvent('pagesinit');
    this.viewer.setDocument(this.pdf);
    // Upstream switches documents above 10,000 pages to PAGE mode before
    // creating page views. Keep this reader's continuous-scroll contract.
    if (this.viewer.scrollMode !== this.components.ScrollMode.VERTICAL) {
      this.viewer._scrollMode = this.components.ScrollMode.VERTICAL;
    }
    // A malformed document can reject before emitting pagesinit.
    await Promise.race([initialized, this.viewer.pagesPromise.then(() => initialized)]);
    this.viewer.pagesRotation = this.initialPosition.rotation;
    this.viewer.currentScaleValue = this.initialPosition.scaleValue;
    await this.goToPosition(this.initialPosition);
    // Loading page dimensions can change the offsets above the restored page.
    // PDFViewer loads metadata here, not canvases for every page.
    await this.viewer.pagesPromise;
    if (!this.destroyed) await this.goToPosition(this.initialPosition);
  }

  pageTop(page) {
    return page.div.getBoundingClientRect().top - this.stage.getBoundingClientRect().top
      + this.stage.scrollTop - (this.stage.clientTop || 0);
  }

  getPosition() {
    if (!this.viewer?.pagesCount || !this.stage) return { version: 1, ...this.initialPosition, percentage: '0' };
    const top = this.stage.scrollTop;
    let low = 0, high = this.viewer.pagesCount - 1;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (this.pageTop(this.viewer.getPageView(mid)) <= top + 1) low = mid;
      else high = mid - 1;
    }
    const page = this.viewer.getPageView(low);
    const ratio = clamp((top - this.pageTop(page)) / Math.max(1, page.div.clientHeight), 0, 1);
    const atEnd = low === this.pdf.numPages - 1
      && top + this.stage.clientHeight >= this.stage.scrollHeight - 2;
    return {
      version: 1, pageNumber: low + 1, pageOffsetRatio: ratio,
      percentage: String(atEnd ? 1 : (low + ratio) / this.pdf.numPages),
      scaleValue: this.viewer.currentScaleValue, rotation: this.viewer.pagesRotation,
    };
  }

  async getProgress() {
    const position = this.getPosition();
    return { percentage: Number(position.percentage), pageNumber: position.pageNumber,
      pageCount: this.pdf.numPages, scale: this.viewer?.currentScale || 1,
      scaleValue: this.viewer?.currentScaleValue || 'page-width', rotation: this.viewer?.pagesRotation || 0 };
  }

  async ensurePage(pageNumber) {
    const page = this.viewer.getPageView(pageNumber - 1);
    if (!page.pdfPage) {
      const document = this.pdf;
      const pdfPage = await document.getPage(pageNumber);
      if (this.destroyed || this.pdf !== document) throw new DOMException('阅读已关闭', 'AbortError');
      page.setPdfPage(pdfPage);
    }
    return page;
  }

  async goToPosition(rawPosition) {
    if (this.destroyed || !this.viewer) return;
    const position = this.normalizePosition(typeof rawPosition === 'string' ? JSON.parse(rawPosition) : rawPosition);
    const token = ++this.navigationToken;
    this.navigationAbort?.abort();
    this.navigationAbort = new AbortController();
    const signal = this.navigationAbort.signal;
    this.isRendering = true;
    try {
      const page = await this.ensurePage(position.pageNumber);
      if (this.destroyed || token !== this.navigationToken) return;
      this.viewer.currentPageNumber = position.pageNumber;
      this.stage.scrollTop = this.pageTop(page) + position.pageOffsetRatio * page.div.clientHeight;
      this.viewer.update();
      if (page.renderingState !== this.components.RenderingStates.FINISHED) {
        await this.waitForEvent('pagerendered', (event) => event.pageNumber === position.pageNumber && !event.cssTransform, signal);
      }
      if (this.destroyed || token !== this.navigationToken) return;
      this.emit('page-changed');
    } catch (error) {
      if (error.name !== 'AbortError' || (!this.destroyed && token === this.navigationToken)) throw error;
    } finally {
      if (token === this.navigationToken) {
        this.isRendering = false;
        this.emit('position-changed');
      }
    }
  }

  showPage(pageNumber, pageOffsetRatio = 0) { return this.goToPosition({ pageNumber, pageOffsetRatio }); }
  next() { return this.showPage(Math.min(this.pdf.numPages, this.getPosition().pageNumber + 1)); }
  prev() { return this.showPage(Math.max(1, this.getPosition().pageNumber - 1)); }

  async setScale(value) {
    if (this.destroyed || !this.viewer) return;
    const scale = value === 'page-width' ? value : clamp(Number(value), 0.25, 5);
    if (scale !== 'page-width' && !Number.isFinite(scale)) return;
    const position = this.getPosition();
    this.viewer.currentScaleValue = scale;
    await this.goToPosition(position);
  }

  async rotate(angle = 90) {
    if (this.destroyed || !this.viewer || !Number.isFinite(angle) || angle % 90 !== 0) return;
    const position = this.getPosition();
    this.viewer.pagesRotation = ((this.viewer.pagesRotation + angle) % 360 + 360) % 360;
    if (this.viewer.currentScaleValue === 'page-width') this.viewer.currentScaleValue = 'page-width';
    await this.goToPosition(position);
    this.emit('scale-changed');
  }

  async resize() {
    if (!this.viewer || this.destroyed) return;
    const position = this.getPosition();
    if (this.viewer.currentScaleValue === 'page-width') this.viewer.currentScaleValue = 'page-width';
    await this.goToPosition(position);
  }

  async getOutline() {
    const outline = await this.pdf.getOutline();
    const flattened = [];
    const walk = async (items, depth = 0) => {
      for (const item of items || []) {
        let dest = item.dest, pageNumber = null;
        try {
          if (typeof dest === 'string') dest = await this.pdf.getDestination(dest);
          if (Array.isArray(dest)) {
            pageNumber = typeof dest[0] === 'object' ? await this.pdf.getPageIndex(dest[0]) + 1 : dest[0] + 1;
          }
        } catch { /* An invalid optional outline entry must not block reading. */ }
        if (pageNumber >= 1 && pageNumber <= this.pdf.numPages) {
          flattened.push({ label: item.title || `第 ${pageNumber} 页`, pageNumber, depth, dest });
        }
        await walk(item.items, depth + 1);
      }
    };
    await walk(outline);
    return flattened;
  }

  async goToDestination(dest) {
    if (this.destroyed) return;
    this.navigationToken += 1;
    this.navigationAbort?.abort();
    this.isRendering = false;
    const destination = typeof dest === 'string' ? await this.pdf.getDestination(dest) : await dest;
    if (!Array.isArray(destination)) return;
    const reference = destination[0];
    const pageNumber = reference && typeof reference === 'object'
      ? await this.pdf.getPageIndex(reference) + 1 : Number.isInteger(reference) ? reference + 1 : null;
    if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > this.pdf.numPages || this.destroyed) return;
    await this.ensurePage(pageNumber);
    if (this.destroyed) return;
    // PDFLinkService's delayed text-layer focus can pull a reader back after
    // they have already scrolled away. Keep navigation immediate and focusless.
    this.viewer.scrollPageIntoView({ pageNumber, destArray: destination, ignoreDestinationZoom: true });
    this.viewer.update();
    this.emit('position-changed');
  }

  async search(rawQuery) {
    const query = String(rawQuery || '').trim();
    if (this.destroyed || !this.findController) return [];
    if (query && query === this.searchQuery) return this.collectingSearch ? this.searchPromise : this.searchResults;
    const token = ++this.searchToken;
    this.cancelSearch?.();
    this.searchQuery = query;
    this.searchResults = [];
    this.searchExcerpts.clear();
    if (!query) {
      this.collectingSearch = false;
      this.eventBus.dispatch('find', { source: this, type: '', query: '', highlightAll: false });
      this.eventBus.dispatch('findbarclose', { source: this });
      return [];
    }
    this.collectingSearch = true;
    this.searchPromise = new Promise((resolve) => {
      const finish = () => {
        this.eventBus.off('updatefindmatchescount', complete);
        this.cancelSearch = null;
        if (token !== this.searchToken || this.destroyed) { resolve([]); return; }
        this.collectingSearch = false;
        const results = [];
        this.findController.pageMatches.forEach((matches, pageIndex) => matches?.forEach((textOffset, matchIndex) => {
          results.push({ pageNumber: pageIndex + 1, matchIndex, textOffset,
            matchLength: this.findController.pageMatchesLength[pageIndex][matchIndex], query,
            excerpt: this.searchExcerpts.get(pageIndex)?.[matchIndex] || query });
        }));
        this.searchResults = results;
        resolve(results);
      };
      const complete = (event) => { if (event.source === this.findController) finish(); };
      this.cancelSearch = () => { this.eventBus.off('updatefindmatchescount', complete); resolve([]); };
      this.eventBus.on('updatefindmatchescount', complete);
      this.eventBus.dispatch('find', { source: this, type: '', query, caseSensitive: false,
        entireWord: false, highlightAll: true, findPrevious: false, matchDiacritics: false });
    });
    return this.searchPromise;
  }

  async goToSearchResult(result) {
    if (this.destroyed || !result) return;
    if (result.query && result.query !== this.searchQuery) await this.search(result.query);
    await this.showPage(Number(result.pageNumber));
    if (this.destroyed) return;
    const pageIndex = Number(result.pageNumber) - 1;
    const page = this.viewer.getPageView(pageIndex);
    if (!page.textLayer?.div.querySelector('.endOfContent')) {
      await this.waitForEvent('textlayerrendered', (event) => event.pageNumber === pageIndex + 1);
    }
    // PDF.js exposes the selected match as a mutable record (6.5.438).
    // Updating it and notifying the text layers selects an arbitrary sidebar hit.
    Object.assign(this.findController.selected, { pageIdx: pageIndex, matchIdx: Number(result.matchIndex) || 0 });
    this.eventBus.dispatch('updatetextlayermatches', { source: this, pageIndex: -1 });
    page.div.querySelector('.highlight.selected')?.scrollIntoView({ block: 'center', inline: 'nearest' });
    this.emit('position-changed');
  }

  setStyle() {}

  removeContent() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.navigationToken += 1;
    this.searchToken += 1;
    this.cancelSearch?.();
    this.abortController.abort();
    this.stage?.removeEventListener('scroll', this.handleScroll);
    this.viewer?.setDocument(null);
    this.linkService?.setDocument(null);
    this.wrapper?.replaceChildren();
    this.stage?.classList.remove('pdf-reader-stage');
    this.stage?.parentElement?.classList.remove('pdf-reader-shell');
    this.events.clear();
    this.searchResults = [];
    this.searchExcerpts.clear();
    this.searchPromise = this.cancelSearch = null;
    this.searchQuery = '';
    void this.loadingTask?.destroy().catch(() => {});
    this.loadingTask = null;
    this.pdf = this.stage = this.wrapper = this.viewer = this.findController = this.linkService = this.eventBus = null;
  }
}
