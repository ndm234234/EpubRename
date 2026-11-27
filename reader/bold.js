// Bold word starts: the first letters of every word in bold, to lead the eye along the line
// (the idea known as "bionic reading"). Each word's start is wrapped in <b class="bio"> in the
// chapter's own document; switching off unwraps them and joins the text back into whole nodes.
(function () {
  "use strict";
  var CLASS = "bio";
  // left as they are: code and the like, and the page's own scripts and styles
  var SKIP = "pre, code, kbd, samp, tt, script, style, b." + CLASS;
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
  }

  window.BoldStarts = { CLASS: CLASS, prefix: prefix, apply: apply };
})();
