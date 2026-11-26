// In-page helpers, evaluated in the reader page: where each word is (page = column of the
// chapter's document), the state of the page shown, and the page that holds a place (cfi).
export const HELPERS = `
window.__words = function () {
  const m = rendition.manager, v = m.views.first(), doc = v.contents.document, delta = m.layout.delta;
  const key = doc.documentElement.scrollWidth + ":" + delta + ":" + doc.body.style.cssText + ":" + prefs.face + prefs.font;
  if (doc.__wm && doc.__wm.key === key) return doc.__wm;
  const words = [], w = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT), r = doc.createRange();
  for (let n; (n = w.nextNode()); ) {
    const s = n.nodeValue, re = /\\S+/g;
    for (let mt; (mt = re.exec(s)); ) {
      r.setStart(n, mt.index); r.setEnd(n, mt.index + mt[0].length);
      const rects = [...r.getClientRects()].filter(x => x.width > 0 && x.height > 0);
      if (!rects.length) continue;   // hidden text
      for (const b of rects) words.push({ page: Math.floor((b.left + b.width / 2) / delta), top: b.top, bottom: b.bottom, t: mt[0], node: n, off: mt.index });
    }
  }
  doc.__wm = { key, words };
  return doc.__wm;
};
window.__state = function () {
  const m = rendition.manager, c = m.container, v = m.views.first(), l = rendition.location, delta = m.layout.delta;
  const doc = v.contents.document, cols = Math.round(doc.documentElement.scrollWidth / delta);
  const page = (c.scrollLeft - v.element.offsetLeft) / delta;
  const { words } = __words();
  const onPage = words.filter(x => x.page === Math.round(page));
  const fr = v.iframe.getBoundingClientRect(), foot = document.querySelector("footer").getBoundingClientRect();
  const visBottom = Math.min(c.getBoundingClientRect().bottom, TOUCH_UI ? foot.top : 1e9) - fr.top;
  const clipped = onPage.filter(x => x.bottom > visBottom + 0.5 || x.top < -0.5).map(x => x.t + "@" + Math.round(x.bottom));
  const beyond = words.filter(x => x.page >= cols || x.page < 0).length;
  // which pages have a word, to tell a blank page
  const pagesWithWords = new Set(words.map(x => x.page));
  return { div: m.layout.divisor || 1, idx: v.index, href: v.section.href, page, cols, total: l && l.start.displayed.total, dpage: l && l.start.displayed.page,
    first: onPage.length ? onPage[0].t : "", last: onPage.length ? onPage[onPage.length - 1].t : "", n: onPage.length,
    firstI: onPage.length ? words.indexOf(onPage[0]) : -1, lastI: onPage.length ? words.indexOf(onPage[onPage.length - 1]) : -1,
    clipped, beyond, blank: !onPage.length && !doc.images.length && !doc.querySelector("svg"),
    anchorAt: __pageOf(anchor), atEnd: !!(l && l.atEnd), atStart: !!(l && l.atStart), settling, anchor };
};
// page (column) that holds a cfi, in the current view; null if not in this chapter
window.__pageOf = function (cfi) {
  try {
    const r = rendition.getRange(cfi); if (!r) return null;
    const delta = rendition.manager.layout.delta;
    if (r.collapsed) { const n = r.startContainer;
      if (n.nodeType === 3 && r.startOffset < n.length) { let i = r.startOffset;
        while (i < n.length - 1 && /\\s/.test(n.nodeValue[i])) i++;   // the first letter at or after the place
        r.setStart(n, i); r.setEnd(n, i + 1); }
      else if (n.nodeType === 1 && n.childNodes[r.startOffset]) r.selectNode(n.childNodes[r.startOffset]); }
    const rr = [...r.getClientRects()].filter(x => x.width > 0 || x.height > 0);
    const b = rr[0] || r.getBoundingClientRect();
    return Math.floor((b.left + Math.max(b.width, 1) / 2) / delta);
  } catch (e) { return null; }
};
window.__vis = function (cfi) { const s = __state(); return { at: __pageOf(cfi), page: Math.round(s.page), idx: s.idx }; };
0`;
