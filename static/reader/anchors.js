// Text anchors survive font changes and iframe reflow; pixel/ratio positions
// remain useful fallbacks for old progress records and non-text documents.
function textNodes(doc) {
  return doc.createTreeWalker(doc.body, 4, { acceptNode(node) {
    return node.textContent.trim() && !node.parentElement?.closest('script,style,noscript,[hidden]') ? 1 : 2;
  } });
}

function nodePath(node, body) {
  const parts = [];
  for (let current = node; current && current !== body; current = current.parentNode) {
    parts.unshift(Array.prototype.indexOf.call(current.parentNode.childNodes, current));
  }
  return parts;
}

export function captureTextAnchor(stage, documents) {
  if (!stage) return null;
  const view = stage.getBoundingClientRect();
  for (const { document: doc, frame, index = 0 } of documents) {
    if (!doc?.body || !frame) continue;
    const bounds = frame.getBoundingClientRect();
    if (bounds.bottom <= view.top + 4 || bounds.top >= view.bottom) continue;
    const y = Math.max(2, view.top + 8 - bounds.top);
    const bodyBounds = doc.body.getBoundingClientRect();
    const x = Math.min(bounds.width - 5, Math.max(8, bodyBounds.left + 6));
    let node, offset = 0;
    try {
      const point = doc.caretPositionFromPoint?.(x, y);
      const range = point ? null : doc.caretRangeFromPoint?.(x, y);
      node = point?.offsetNode || range?.startContainer;
      offset = point?.offset ?? range?.startOffset ?? 0;
    } catch { /* Browsers may decline a point on padding. */ }
    if (node?.nodeType !== 3 || !node.textContent.trim()) {
      const walker = textNodes(doc);
      while ((node = walker.nextNode())) {
        const range = doc.createRange();
        range.selectNodeContents(node);
        if (range.getBoundingClientRect().bottom + bounds.top >= view.top + 4) break;
      }
      offset = 0;
    }
    if (!node) continue;
    offset = Math.min(offset, Math.max(0, node.textContent.length - 1));
    const range = doc.createRange();
    range.setStart(node, offset); range.setEnd(node, Math.min(node.textContent.length, offset + 1));
    const rect = range.getBoundingClientRect();
    const quoteStart = Math.max(0, offset - 16);
    const internalScroll = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight) > frame.clientHeight + 4
      && frame.clientHeight <= stage.clientHeight + 4;
    return { sectionIndex: index, nodePath: nodePath(node, doc.body), offset,
      quote: node.textContent.slice(quoteStart, offset + 80), quoteOffset: offset - quoteStart,
      scrollContainer: internalScroll ? 'document' : 'stage',
      viewportOffset: rect.top + bounds.top - view.top };
  }
  return null;
}

export function restoreTextAnchor(stage, documents, anchor) {
  if (!stage || !anchor) return false;
  const entry = documents.find(({ index = 0 }) => index === Number(anchor.sectionIndex));
  const doc = entry?.document, frame = entry?.frame;
  if (!doc?.body || !frame) return false;
  let node = doc.body;
  for (const index of anchor.nodePath || []) node = node?.childNodes[index];
  let offset = Number(anchor.offset) || 0;
  if (node?.nodeType !== 3 || (anchor.quote && !node.textContent.includes(anchor.quote))) {
    const walker = textNodes(doc); node = null;
    for (let candidate; (candidate = walker.nextNode());) {
      const start = anchor.quote ? candidate.textContent.indexOf(anchor.quote) : -1;
      if (start >= 0) { node = candidate; offset = start + (Number(anchor.quoteOffset) || 0); break; }
    }
  }
  if (!node || node.nodeType !== 3) return false;
  offset = Math.max(0, Math.min(offset, node.textContent.length - 1));
  const range = doc.createRange();
  range.setStart(node, offset); range.setEnd(node, Math.min(node.textContent.length, offset + 1));
  const rect = range.getBoundingClientRect();
  const delta = rect.top + frame.getBoundingClientRect().top - stage.getBoundingClientRect().top - (Number(anchor.viewportOffset) || 0);
  if (anchor.scrollContainer === 'document') doc.defaultView?.scrollBy(0, delta);
  else stage.scrollTop += delta;
  return true;
}
