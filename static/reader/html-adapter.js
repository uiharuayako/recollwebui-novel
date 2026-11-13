// Preserve the complete HTML article. Splitting on headings makes continuous
// reading, the document outline, and full-document search lose later sections.
export function createHtmlRendition(buffer, { url, title = '', policy, prepareDocument = () => {} } = {}) {
  const bytes = new Uint8Array(buffer);
  const header = new TextDecoder('ascii').decode(bytes.slice(0, 4096));
  const encoding = bytes[0] === 255 && bytes[1] === 254 ? 'utf-16le'
    : bytes[0] === 254 && bytes[1] === 255 ? 'utf-16be'
    : header.match(/(?:charset|encoding)\s*=\s*["']?\s*([\w-]+)/i)?.[1] || 'utf-8';
  let source;
  try { source = new TextDecoder(encoding).decode(bytes); }
  catch { source = new TextDecoder().decode(bytes); }
  const parsed = new DOMParser().parseFromString(source, 'text/html');
  parsed.querySelectorAll('script,iframe,object,embed,form,base,meta[http-equiv]').forEach((node) => node.remove());
  for (const element of parsed.querySelectorAll('*')) {
    for (const attribute of [...element.attributes]) {
      if (/^on/i.test(attribute.name) || ['srcdoc', 'autofocus', 'formaction'].includes(attribute.name)
        || (/^(href|src|xlink:href)$/i.test(attribute.name) && /^\s*javascript:/i.test(attribute.value))) element.removeAttribute(attribute.name);
    }
  }
  const listeners = new Map();
  let frame, stage;
  const emit = (event) => { for (const listener of listeners.get(event) || []) listener(); };
  const rendition = {
    format: 'HTML', readerMode: 'scroll', chapterDocList: [{ label: title, href: url }],
    tempLocation: { chapterDocIndex: '0', chapterTitle: title, chapterHref: url },
    on(event, listener) { if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event).add(listener); },
    getDocument() { return frame?.contentDocument; },
    getPosition() { return { ...this.tempLocation }; },
    getProgress() { return { percentage: 0 }; },
    setStyle(css) {
      const doc = this.getDocument();
      if (!doc?.head) return;
      let style = doc.getElementById('recoll-reader-content-style');
      if (!style) { style = doc.createElement('style'); style.id = 'recoll-reader-content-style'; doc.head.append(style); }
      style.textContent = css;
    },
    async renderTo(target) {
      stage = target;
      frame = document.createElement('iframe');
      frame.id = 'kookit-iframe'; frame.title = title || 'HTML 正文';
      frame.setAttribute('sandbox', 'allow-same-origin'); frame.setAttribute('scrolling', 'no');
      frame.referrerPolicy = 'same-origin'; frame.style.width = '100%'; frame.style.border = '0';
      target.replaceChildren(frame);
      const doc = this.getDocument();
      doc.open(); doc.write('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>'); doc.close();
      // Install the resource policy before importing any book markup.
      const csp = doc.createElement('meta'); csp.httpEquiv = 'Content-Security-Policy';
      csp.content = policy; csp.dataset.recollReaderCsp = 'yes'; doc.head.prepend(csp);
      const base = doc.createElement('base'); base.href = new URL(url, window.location.href).href; doc.head.append(base);
      prepareDocument(doc);
      for (const node of parsed.head.querySelectorAll('style,link[rel="stylesheet"]')) doc.head.append(doc.importNode(node, true));
      for (const attribute of parsed.body.attributes) {
        if (['class', 'lang', 'dir'].includes(attribute.name)) doc.body.setAttribute(attribute.name, attribute.value);
      }
      const fragment = doc.createDocumentFragment();
      for (const node of parsed.body.childNodes) fragment.append(doc.importNode(node, true));
      doc.body.replaceChildren(fragment);
      emit('rendered');
    },
    async goToPosition(raw) {
      const position = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (stage) stage.scrollTop = Math.max(0, Number(position?.intraChapterOffset ?? position?.scrollTop) || 0);
      emit('rendered');
    },
    async record() { emit('page-changed'); },
    removeContent() { listeners.clear(); frame?.remove(); frame = null; stage = null; },
  };
  return rendition;
}
