// Bold word starts: the first letters of every word in bold, to lead the eye along the line
// (the idea known as "bionic reading"). Each word's start is wrapped in <b class="epubreader-bio"> in the
// chapter's own document; switching off unwraps them and joins the text back into whole nodes.
(function () {
  "use strict";
  var CLASS = "epubreader-bio";   // a name no book uses (a book's own "bio" is an author's biography)
  // left as they are: code and the like, the page's own scripts and styles, and the text of
  // pictures (SVG) and formulas (MathML), where an HTML <b> isn't shown at all
  var SKIP = "pre, code, kbd, samp, tt, script, style, svg, math, b." + CLASS;
  // a word: letters, combining marks, digits; a soft hyphen (a hyphenation point) doesn't split it
  var WORD = /[\p{L}\p{M}\p{N}­]+/gu, MARK = /\p{M}/u;

  // how many letters of a word of n get bold
  function prefix(n) { return n <= 3 ? 1 : n === 4 ? 2 : Math.ceil(n * 0.45); }

  function wrap(doc) {
    var texts = [], w = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode()))
      if (n.nodeValue.trim() && !(n.parentElement && n.parentElement.closest(SKIP))) texts.push(n);
    for (var i = 0; i < texts.length; i++) {
      var s = texts[i].nodeValue, f = doc.createDocumentFragment(), last = 0, m;
      WORD.lastIndex = 0;
      while ((m = WORD.exec(s))) {
        var k = prefix(m[0].length);
        while (k < m[0].length && MARK.test(m[0][k])) k++;   // a mark stays with its letter
        if (m.index > last) f.append(s.slice(last, m.index));
        var b = doc.createElement("b");
        b.className = CLASS;
        b.textContent = m[0].slice(0, k);
        f.append(b);
        last = m.index + k;
      }
      if (!last) continue;
      if (last < s.length) f.append(s.slice(last));
      texts[i].replaceWith(f);
    }
  }
  function unwrap(doc) {
    var bs = doc.querySelectorAll("b." + CLASS);
    for (var i = 0; i < bs.length; i++) bs[i].replaceWith.apply(bs[i], Array.prototype.slice.call(bs[i].childNodes));
    doc.body.normalize();
  }
  // on: wrap (once: a document already wrapped stays as it is); off: unwrap
  function apply(doc, on) {
    if (!doc || !doc.body) return;
    var has = !!doc.querySelector("b." + CLASS);
    if (on && !has) wrap(doc);
    else if (!on && has) unwrap(doc);
    if (on !== has) { caches.delete(doc); stamps.set(doc, stamp(doc) + 1); }
  }
  // how many times the bold changed a document's text nodes (lists of them made before are stale)
  var stamps = new WeakMap();
  function stamp(doc) { return stamps.get(doc) || 0; }

  // ---------------------------------------------------------------- addresses (CFI)
  // The reading place, bookmarks, notes, search hits are kept as addresses into the chapter's
  // document (EpubCFI), and the bold cuts its text nodes. They must not differ with the bold:
  // a bookmark made with it is opened on a device without it. The bold only wraps text, so a
  // character's position in the chapter's text, and the order of its other elements, stay the same.
  // - range → address: epub.js's own "ignore class" leaves the wrapped pieces out (exact).
  // - address → range: epub.js's resolver with that class misses now and then (at paragraph
  //   starts), so the address is resolved on a plain copy of the chapter (the bold unwrapped)
  //   and carried over to the real one by character position (and element order).
  var caches = new WeakMap();
  var api = { CLASS: CLASS, prefix: prefix, apply: apply, stamp: stamp, patchCfi: patchCfi, isPiece: isPiece, _cacheBuilds: 0 };
  // a text node that is the bold start of a word (the rest of the word follows it)
  function isPiece(n) { var e = n && n.parentElement; return !!e && e.localName === "b" && e.classList.contains(CLASS); }

  function textNodes(doc) {
    var a = [], w = doc.createTreeWalker(doc.documentElement, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) a.push(n);
    return a;
  }
  function plainOf(doc) {   // the plain copy of a document with the bold, and how to cross over
    var c = caches.get(doc);
    if (c) return c;
    api._cacheBuilds++;
    var plain = doc.cloneNode(true);
    unwrap(plain);
    var pStart = new Map(), g = 0, i, n;
    var pt = textNodes(plain);
    for (i = 0; i < pt.length; i++) { pStart.set(pt[i], g); g += pt[i].length; }
    var rt = textNodes(doc), rEnds = [];
    for (g = 0, i = 0; i < rt.length; i++) { g += rt[i].length; rEnds.push(g); }
    var pEls = plain.getElementsByTagName("*"), pIndex = new Map();
    for (i = 0; i < pEls.length; i++) pIndex.set(pEls[i], i);
    var rEls = Array.prototype.filter.call(doc.getElementsByTagName("*"),
      function (e) { return !(e.localName === "b" && e.classList.contains(CLASS)); });
    c = { plain: plain, pStart: pStart, rt: rt, rEnds: rEnds, pIndex: pIndex, rEls: rEls };
    caches.set(doc, c);
    return c;
  }
  // the real text node and offset at character position g; an end boundary keeps to the text
  // before it, a start to the text after it
  function realAt(c, g, isEnd) {
    var lo = 0, hi = c.rEnds.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (isEnd ? c.rEnds[mid] >= g : c.rEnds[mid] > g) hi = mid; else lo = mid + 1; }
    if (lo >= c.rt.length) { var t = c.rt[c.rt.length - 1]; return t ? [t, t.length] : null; }
    return [c.rt[lo], g - (c.rEnds[lo] - c.rt[lo].length)];
  }
  function crossOver(c, node, off, isEnd) {
    if (node.nodeType === 3) {
      var g = c.pStart.get(node);
      return g === undefined ? null : realAt(c, g + off, isEnd);
    }
    var i = c.pIndex.get(node);
    if (i === undefined) return null;
    var real = c.rEls[i], kids = node.childNodes;
    if (off === 0) return [real, 0];
    if (off >= kids.length) return [real, real.childNodes.length];
    var child = kids[off], rc = null;
    if (child.nodeType === 1) rc = c.rEls[c.pIndex.get(child)];
    else if (child.nodeType === 3) { var t = realAt(c, c.pStart.get(child), false); rc = t && t[0]; }
    while (rc && rc.parentNode !== real) rc = rc.parentNode;
    return [real, rc ? Array.prototype.indexOf.call(real.childNodes, rc) : Math.min(off, real.childNodes.length)];
  }
  // once, with epub.js's EpubCFI (ePub.CFI), before the book is shown
  function patchCfi(CFI) {
    var P = CFI && CFI.prototype;
    if (!P || P.__bold) return;
    P.__bold = true;
    var fromRange = P.fromRange, fromNode = P.fromNode, toRange = P.toRange;
    P.fromRange = function (range, base, ignoreClass) { return fromRange.call(this, range, base, ignoreClass || CLASS); };
    P.fromNode = function (node, base, ignoreClass) { return fromNode.call(this, node, base, ignoreClass || CLASS); };
    P.toRange = function (doc, ignoreClass) {
      if (!doc || !doc.querySelector || !doc.querySelector("b." + CLASS)) return toRange.call(this, doc, ignoreClass);
      var c = plainOf(doc), r = toRange.call(this, c.plain);
      if (!r) return r;
      var s = crossOver(c, r.startContainer, r.startOffset, false);
      var e = r.collapsed ? s : crossOver(c, r.endContainer, r.endOffset, true);
      if (!s || !e) return null;
      var out = doc.createRange();
      out.setStart(s[0], s[1]);
      out.setEnd(e[0], e[1]);
      return out;
    };
  }

  window.BoldStarts = api;
})();
