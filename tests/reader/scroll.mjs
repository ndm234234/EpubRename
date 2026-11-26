// "Scroll text" (Aa → Layout): the book as one continuous ribbon, scrolled bottom to top.
// node scroll.mjs <base> <book under Out/> [search word]
// - the checkbox switches to scroll mode and back, keeping the first visible word (± one line)
// - continuous: past a chapter's end the next chapter follows in the same scroll
// - the reader's own scroll saves the place with a newer time; opening, switching, a font change
//   and a resize keep the time; reopening shows the same first visible word
// - contents, search hits and bookmarks land at the top; a bookmark stores the current place
// - keys scroll (arrow a little, PageDown / Space a screen); a tap shows / hides the bars and
//   never jumps; the setting is this device's own (epubReader:scroll, not synced): reader settings
//   that come from another device leave the mode as it is
// A phone (390x844 touch) and a desktop (1280x800).
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK, WORD = "что"] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const URL_ = `${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`;
const READY = "typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start";
const settle = async p => { await sleep(200); await p.waitFor("!settling", 15000); await sleep(250); };
const J = async (p, expr) => JSON.parse(await p.eval(`JSON.stringify(${expr}) || "null"`));

// In-page helpers of this check, separate from the reader's own code: words of a chapter in
// document order (k = index), the first word fully visible at the top of the scroll area, where a
// word is now (its top against the area's top, in px, with the line height), the word at a cfi.
const SCROLL_HELPERS = `
window.__views = () => { const m = rendition.manager; return (m.views._views || m.views.all()).filter(v => v.displayed && v.contents); };
window.__box = () => rendition.manager.container.getBoundingClientRect();
window.__ws = function (doc) {
  if (doc.__ws) return doc.__ws;
  const out = [], w = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode()); ) { const re = /\\S+/g; for (let m; (m = re.exec(n.nodeValue)); ) out.push({ n, o: m.index, l: m[0].length, t: m[0] }); }
  return doc.__ws = out;
};
window.__wr = function (doc, x) {   // the word's first box, or null if it has none (hidden)
  const r = doc.createRange(); r.setStart(x.n, x.o); r.setEnd(x.n, x.o + x.l);
  return [...r.getClientRects()].find(b => b.width > 0 && b.height > 0) || null;
};
window.__first = function () {
  const box = __box();
  for (const v of __views()) {
    const fr = v.iframe.getBoundingClientRect();
    if (fr.bottom <= box.top + 1 || fr.top >= box.bottom) continue;
    const doc = v.contents.document, ws = __ws(doc);
    for (let k = 0; k < ws.length; k++) {
      const b = __wr(doc, ws[k]);
      if (!b) continue;
      const top = b.top + fr.top - box.top;
      if (top >= -1) return top < box.height ? { href: v.section.href, idx: v.index, k, t: ws[k].t, top } : null;
    }
  }
  return null;
};
window.__locate = function (href, k) {
  const v = __views().find(v => v.section.href === href);
  if (!v) return null;
  const doc = v.contents.document, ws = __ws(doc), b = __wr(doc, ws[k]);
  if (!b) return null;
  let lineH = b.height * 1.5;
  for (let j = k + 1; j < ws.length && j < k + 80; j++) { const c = __wr(doc, ws[j]); if (c && c.top > b.top + 2) { lineH = c.top - b.top; break; } }
  return { top: b.top + v.iframe.getBoundingClientRect().top - __box().top, lineH, t: ws[k].t };
};
window.__wordAt = function (cfi) {
  const r = rendition.getRange(cfi); if (!r) return null;
  const doc = r.startContainer.ownerDocument, v = __views().find(v => v.contents.document === doc);
  const ws = __ws(doc);
  let a = 0, b = ws.length - 1;   // the first word that ends after the place
  while (a < b) { const m = (a + b) >> 1; if (r.comparePoint(ws[m].n, ws[m].o + ws[m].l) > 0) b = m; else a = m + 1; }
  return { href: v.section.href, k: a, t: ws[a].t };
};
window.__place = () => JSON.parse(localStorage.getItem(bookKey));
window.__inset = function () {   // the text's left and right margins on screen (px), visible lines only
  const box = __box(); let l = 1e9, r = -1e9;
  for (const v of __views()) {
    const fr = v.iframe.getBoundingClientRect(), doc = v.contents.document;
    for (const x of __ws(doc)) { const b = __wr(doc, x); if (!b) continue;
      const L = b.left + fr.left, R = b.right + fr.left, T = b.top + fr.top;
      if (L < box.left - 1 || R > box.right + 1 || T < box.top || T > box.bottom - b.height) continue;
      l = Math.min(l, L); r = Math.max(r, R); }
  }
  return { left: Math.round(l), right: Math.round(innerWidth - r) };
};
window.__flow = () => rendition.manager.isPaginated ? "paginated" : "scrolled";
0`;
// the word at the top (± one line): fully visible, its top within the first line and a half
const atTop = l => !!l && l.top >= -2 && l.top < 1.5 * l.lineH;
const fmt = l => l ? `"${l.t}" at ${Math.round(l.top)}px, line ${Math.round(l.lineH)}px` : "not shown";
const sameWord = async (p, w) => J(p, `__locate(${JSON.stringify(w.href)}, ${w.k})`);

async function openBook(p) {
  await p.goto("about:blank"); await p.goto(URL_);
  await p.waitFor(READY, 30000);
  await p.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches")); await p.eval(SCROLL_HELPERS);
  await settle(p);
}
const scrollBox = (p, on) => p.eval(`(b => { if (b && b.checked !== ${on}) b.click(); })(document.getElementById('scrollText')); 0`);

for (const phone of [true, false]) {
  const W = phone ? 390 : 1280, H = phone ? 844 : 800;
  const p = await launch({ width: W, height: H, touch: phone });
  p.on(m => { if (m.method === "Runtime.exceptionThrown") { fails++; console.log("  FAIL page error (above)"); } });
  await p.send("Page.addScriptToEvaluateOnNewDocument", { source:
    `try { if (!sessionStorage.x) { sessionStorage.x = 1; localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light", font: 100, face: "book", layout: "book" })); } } catch {}` });
  console.log(phone ? "phone" : "desktop");
  await openBook(p);
  await p.waitFor("locationsReady", 120000);
  // a few pages into the book (past the title pages), paged as before
  for (let i = 0; i < 12; i++) { await p.eval("rendition.next().then(() => 0)"); await settle(p); }
  ok(await p.eval("__flow()") === "paginated", "paged by default");
  ok(await p.eval("!!document.querySelector('#typo [data-pane=layout] #scrollText')"), "a \"Scroll text\" checkbox in Aa → Layout");
  ok(/Scroll text/.test(await p.eval("(e => e && e.closest('label') ? e.closest('label').textContent : '')(document.getElementById('scrollText'))")), "labelled \"Scroll text\"");
  ok(await p.eval("(e => !!e && e.checked === false)(document.getElementById('scrollText'))"), "unchecked by default");
  const in0 = await J(p, "__inset()");
  const A0 = await p.eval("anchor"), w0 = await J(p, "__wordAt(anchor)"), t0 = (await J(p, "__place()")).time;

  // 1. paged → scroll: the first word of the page is at the top
  await p.eval("document.getElementById('typoBtn').click(); document.querySelector('#typo [data-tab=layout]').click(); 0");
  await scrollBox(p, true); await settle(p);
  await p.eval("document.getElementById('typoBtn').click(); 0"); await sleep(300);
  ok(await p.eval("__flow()") === "scrolled", "the checkbox: scroll mode");
  ok(await p.eval("localStorage.getItem('epubReader:scroll')") === "true", "kept as epubReader:scroll, this device's own");
  ok(!("scroll" in (await J(p, "JSON.parse(localStorage.getItem('epubReader:prefs'))"))), "not in epubReader:prefs (the reader settings that sync)");
  { const l = await sameWord(p, w0); ok(atTop(l), `scroll mode: the page's first word at the top (${fmt(l)})` + (l ? "" : await p.eval(`JSON.stringify({ w0: ${JSON.stringify(w0)}, anchor, settling, at: placeBox(anchor), views: shownViews().map(v => v.section.href + ":" + Math.round(v.iframe.getBoundingClientRect().top) + "/" + Math.round(v.iframe.getBoundingClientRect().height)) })`))); }
  if (phone) { const in1 = await J(p, "__inset()");
    ok(Math.abs(in1.left - in0.left) <= 3 && Math.abs(in1.right - in0.right) <= 12, `phone: the same side margins as on pages (pages ${in0.left}/${in0.right}px, scroll ${in1.left}/${in1.right}px)`); }
  ok((await J(p, "__place()")).time === t0, "switching keeps the place's time");
  ok(await p.eval("rendition.manager.container.scrollHeight > rendition.manager.container.clientHeight + 100"), "the text scrolls");
  { const where = await p.eval("document.getElementById('where').textContent"), pct = await p.eval("document.getElementById('pct').textContent");
    ok(!/\d+\/\d+|p\. /.test(where + " " + pct) && /^\d+%$/.test(pct.trim()), `status: % only, no page numbers ("${where}" | "${pct}")`); }
  await p.shot(`scroll-${phone ? "phone" : "desktop"}.png`);

  // 2. scroll back to paged at once: same word on the page shown
  await scrollBox(p, false); await settle(p);
  { const v = await J(p, `__vis(${JSON.stringify(A0)})`);
    ok(await p.eval("__flow()") === "paginated" && v.at === v.page, `back to pages: the word on the page shown (page ${v.page}, word on ${v.at})`); }
  const hooks0 = await p.eval("book.spine.hooks.content.list().length");
  await scrollBox(p, true); await settle(p);
  await scrollBox(p, false); await settle(p); await scrollBox(p, true); await settle(p);
  ok(await p.eval("book.spine.hooks.content.list().length") === hooks0, "switching modes leaves no extra content hooks");

  // 3. the reader scrolls (finger / wheel): the place follows, with a newer time
  const C = JSON.parse(await p.eval("JSON.stringify(__box())"));
  const cx = C.left + C.width / 2, cy = C.top + C.height / 2;
  const readerScroll = async dy => {
    if (phone) {   // a finger drags the text up (held still at the end: no fling)
      for (let left = dy; left > 0; left -= H * 0.6) {
        const d = Math.min(left, H * 0.6), y0 = cy + d / 2, steps = Math.ceil(d / 20);
        await p.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: cx, y: y0 }] });
        for (let i = 1; i <= steps; i++) { await p.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: cx, y: y0 - d * i / steps }] }); await sleep(16); }
        await sleep(150);
        await p.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await sleep(100);
      }
    }
    else for (let left = dy; Math.abs(left) > 0; ) { const d = Math.sign(left) * Math.min(Math.abs(left), 200); left -= d;
      await p.send("Input.dispatchMouseEvent", { type: "mouseWheel", x: cx, y: cy, deltaX: 0, deltaY: d }); await sleep(30); }
    await sleep(1200); await settle(p);
  };
  // how far the text moved up since f was taken (by where f's word is now: scrollTop alone also
  // changes when epub.js adds or drops a chapter above)
  const travel = async f => { const l = await sameWord(p, f); return l ? Math.round(f.top - l.top) : NaN; };
  {
    const f0 = await J(p, "__first()");
    await readerScroll(H * 0.8);
    const pl = await J(p, "__place()"), f = await J(p, "__first()"), d = await travel(f0);
    ok(d > H * 0.3, `scrolled by the reader (the text moved up ${d}px)`);
    ok(pl.time > t0, `the place saved with a newer time (${t0} → ${pl.time})`);
    const w = await J(p, `__wordAt(${JSON.stringify(pl.cfi)})`);
    ok(f && w && w.href === f.href && Math.abs(w.k - f.k) <= 20, `saved place = the first visible word (saved "${w && w.t}", shown "${f && f.t}")`);
    ok(pl.cfi === await p.eval("anchor"), "the reading place is the one saved");
    ok(/^\d+%$/.test((await p.eval("document.getElementById('pct').textContent")).trim()), "% still shown after scrolling");
  }

  // 4. keys: arrow down a little, PageDown / Space a screen (less a line), PageUp back
  {
    const key = async k => { await p.eval(`document.dispatchEvent(new KeyboardEvent('keydown', { key: ${JSON.stringify(k)}, bubbles: true, cancelable: true })); 0`); await sleep(700); };
    const ch = await p.eval("rendition.manager.container.clientHeight");
    const step = async act => { const f = await J(p, "__first()"); await act(); return travel(f); };
    let d = await step(() => key("ArrowDown"));
    ok(d > 0 && d < ch / 3, `ArrowDown: a little (${d}px of ${ch})`);
    d = await step(() => key("PageDown"));
    ok(d > ch * 0.75 && d < ch, `PageDown: about a screen (${d}px of ${ch})`);
    d = await step(() => key(" "));
    ok(d > ch * 0.75 && d < ch, `Space: about a screen (${d}px)`);
    d = await step(() => key("PageUp"));
    ok(-d > ch * 0.75 && -d < ch, `PageUp: a screen back (${-d}px)`);
    if (!phone) {
      d = await step(async () => { await p.eval("document.getElementById('next').click(); 0"); await sleep(700); });
      ok(d > ch * 0.75 && d < ch, `the next button scrolls a screen (${d}px)`);
      d = await step(async () => { await p.eval("document.getElementById('prev').click(); 0"); await sleep(700); });
      ok(-d > ch * 0.75 && -d < ch, `the previous button scrolls a screen back (${-d}px)`);
    }
    await sleep(800); await settle(p);
  }

  // 5. reopen: the same first visible word, the time unchanged
  {
    const f = await J(p, "__first()"), pl = await J(p, "__place()");
    await openBook(p);
    ok(await p.eval("__flow()") === "scrolled", "reopened in scroll mode");
    const l = await sameWord(p, f);
    ok(atTop(l), `reopened at the same first visible word (${fmt(l)}, was "${f.t}")`);
    ok((await J(p, "__place()")).time === pl.time, "opening keeps the place's time");
  }

  // 6. continuous: scroll past the chapter's end, the next chapter follows in the same scroll
  {
    const idx = await p.eval("__first().idx");
    // to a screen before the chapter's end (as if the reader dragged the scrollbar there)
    await p.eval(`readerInput(); (v => { const c = rendition.manager.container; c.scrollTop += v.iframe.getBoundingClientRect().bottom - __box().bottom - 50; })(__views().find(v => v.index === ${idx})); 0`);
    await sleep(800); await settle(p);
    await readerScroll(H * 0.5);
    const shown = await J(p, `__views().filter(v => { const r = v.iframe.getBoundingClientRect(), b = __box(); return r.bottom > b.top && r.top < b.bottom; }).map(v => v.index)`);
    ok(shown.includes(idx) && shown.some(i => i > idx), `chapter ${idx}'s end and the next one's start on one screen (${shown})`);
    ok(await p.eval(`__views().some(v => v.index > ${idx})`), "the next chapter is in the same scroll");
    const before = await p.eval("__first().idx");
    await readerScroll(H * 0.9);
    const after = await J(p, "__first()");
    ok(after && after.idx > idx, `scrolled on into the next chapter (${before} → ${after && after.idx} "${after && after.t}")`);
  }

  // 7. font size: the place kept
  {
    const f = await J(p, "__first()"), t = (await J(p, "__place()")).time;
    for (const d of [20, -30, 10]) {
      await p.eval(`setFont(${d}); 0`); await settle(p); await sleep(300);
      const l = await sameWord(p, f);
      ok(atTop(l), `size ${await p.eval("prefs.font")}%: place kept (${fmt(l)}, was "${f.t}")`);
    }
    await p.eval(`document.querySelector('#faceSeg [data-v=literata]').click(); 0`); await settle(p);
    { const l = await sameWord(p, f); ok(atTop(l), `font Literata: place kept (${fmt(l)})`); }
    await p.eval(`document.querySelector('#faceSeg [data-v=book]').click(); 0`); await settle(p);
    ok((await J(p, "__place()")).time === t, "font changes keep the place's time");
  }

  // 8. a resize (the phone turns, the window changes): the place kept
  {
    const f = await J(p, "__first()");
    for (const [w, h] of [[H, W], [W, H]]) {
      await p.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: phone });
      await sleep(900); await settle(p); await sleep(300); await settle(p);
      const l = await sameWord(p, f);
      ok(atTop(l), `window ${w}x${h}: place kept (${fmt(l)})`);
    }
  }

  // 9. a tap shows / hides the bars and never jumps (also on the side thirds)
  {
    const f = await J(p, "__first()"), a = await p.eval("anchor");
    if (phone) {
      for (const x of [W / 2, W * 0.85, W * 0.15]) {
        await p.tap(x, H * 0.4);
        ok(await p.eval("document.body.classList.contains('bars')"), `tap at x=${Math.round(x)}: the bars shown`);
        await p.tap(x, H * 0.4);
        ok(!(await p.eval("document.body.classList.contains('bars')")), `tap again: the bars hidden`);
      }
    } else { await p.click(W * 0.8, H * 0.6); await p.click(W * 0.2, H * 0.6); }
    await sleep(800);
    const d = await travel(f);
    ok(d === 0 && await p.eval("anchor") === a, `taps: no jump (${d}px)`);
  }

  // 9b. phone: real taps in Aa (bigger, smaller, a face, back) are not the reader's scroll:
  // the place keeps its time and stays at the top
  if (phone) {
    const f = await J(p, "__first()"), t = (await J(p, "__place()")).time, n0 = await p.eval("readerScrolls");
    const tapOn = async sel => { const c = await J(p, `(r => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 }))(document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect())`);
      await p.tap(c.x, c.y); await sleep(150); await settle(p); };
    await p.tap(W / 2, H * 0.4); await sleep(300);
    await tapOn("#typoBtn"); await sleep(300);
    for (const sel of ["#bigger", "#smaller", "#faceSeg button:nth-child(2)", "#faceSeg button:nth-child(1)"]) await tapOn(sel);
    await sleep(1200); await settle(p);
    ok(await p.eval("readerScrolls") === n0, `taps in Aa: not counted as the reader's scroll (${n0} → ${await p.eval("readerScrolls")})`);
    ok((await J(p, "__place()")).time === t, "taps in Aa: the place keeps its time");
    { const l = await sameWord(p, f); ok(atTop(l), `taps in Aa: place kept (${fmt(l)})`); }
    await p.eval("toggleTypo(false); setBars(false); 0"); await settle(p);
  }

  // 10. contents: the chapter's start at the top
  {
    const t = (await J(p, "__place()")).time;
    const entries = await J(p, "tocItems.map((t, i) => ({ i, href: t.href }))");
    const e = entries[Math.min(3, entries.length - 1)];
    await p.eval(`tocItems[${e.i}].a.click(); 0`); await sleep(500); await settle(p);
    const f = await J(p, "__first()");
    // the entry's own target: the element its #id names, else the chapter's start
    const target = await J(p, `(() => { const it = book.navigation.toc.flatMap(function f(x) { return [x, ...(x.subitems || []).flatMap(f)]; })[${e.i}];   // tocItems' order
      const [file, id] = it.href.split('#'); const v = __views().find(v => v.section.href.endsWith(file) || file.endsWith(v.section.href)); if (!v) return null;
      const doc = v.contents.document, el = id ? doc.getElementById(id) : doc.body, ws = __ws(doc);
      const k = ws.findIndex(w => el.contains(w.n) || (el.compareDocumentPosition(w.n) & Node.DOCUMENT_POSITION_FOLLOWING));
      return { href: v.section.href, k, t: ws[k] && ws[k].t }; })()`);
    const l = target && await sameWord(p, target);
    ok(atTop(l), `contents "${await p.eval(`tocItems[${e.i}].a.textContent`)}": its start at the top (${fmt(l)}; first shown "${f && f.t}")`);
    ok((await J(p, "__place()")).time > t, "a contents jump is a move of the reader's (newer time)");
  }

  // 11. search hits at the top
  {
    await p.eval(`runSearch(${JSON.stringify(WORD)}); 0`);
    await p.waitFor("/Done|Showing|Nothing/.test(document.getElementById('searchStatus').textContent)", 60000);
    await settle(p);
    const n = await p.eval("hits.length");
    for (const i of [...new Set([1, Math.floor(n / 2), n - 1])].filter(i => i >= 0 && i < n)) {
      await p.eval(`goToHit(${i}); 0`); await sleep(300); await settle(p);
      const w = await J(p, `__wordAt(hits[${i}].cfi)`), l = w && await sameWord(p, w);
      // the book's last hits may sit on its last screen, which can't scroll further
      const end = await p.eval("!rendition.manager.isPaginated && (c => c.scrollTop + c.clientHeight >= c.scrollHeight - 2)(rendition.manager.container)");
      ok(atTop(l) || (end && l && l.top >= 0), `hit ${i} of ${n}: at the top (${fmt(l)}${end ? ", end of the book" : ""})`);
    }
    await p.eval("clearSearch(); 0");
  }

  // 12. bookmarks: on the current place; away and back (in the middle of the book, by the slider)
  {
    await p.eval("rendition.display(book.locations.cfiFromPercentage(0.5)).then(() => 0)"); await settle(p);
    await readerScroll(H * 0.6);
    const a = await p.eval("anchor");
    await p.eval("toggleMarkHere(); 0"); await sleep(200);
    const m = await J(p, "marks[marks.length - 1] && marks.find(m => m.cfi === anchor)");
    ok(!!m, `bookmark added on the current place (${m && m.cfi === a})`);
    ok(await p.eval("document.getElementById('markBtn').classList.contains('on')"), "the bookmark icon shows it" +
      (await p.eval("document.getElementById('markBtn').classList.contains('on')") ? "" : " " + await p.eval("JSON.stringify({ anchor, first: firstShown(), at: placeBox(anchor), box: __box().top })")));
    const w = await J(p, `__wordAt(${JSON.stringify(a)})`);
    for (let i = 0; i < 3; i++) await readerScroll(H * 0.9);
    ok(!(await p.eval("document.getElementById('markBtn').classList.contains('on')")), "three screens on: not on the bookmark");
    await p.eval(`goToMark(marks.find(m => m.cfi === ${JSON.stringify(a)})); 0`); await sleep(300); await settle(p);
    const l = await sameWord(p, w);
    ok(atTop(l), `went to the bookmark (${fmt(l)})`);
    ok(await p.eval("document.getElementById('markBtn').classList.contains('on')"), "the bookmark icon shows it again");
    for (let i = 0; i < 2; i++) await readerScroll(H * 0.9);
    ok(!(await p.eval("document.getElementById('markPrev').disabled")), "Previous bookmark enabled below it");
    await p.eval(`stepMark(-1); 0`); await sleep(300); await settle(p);
    { const l = await sameWord(p, w); ok(atTop(l) && await p.eval("document.getElementById('markBtn').classList.contains('on')"), `Previous bookmark: back on it (${fmt(l)})`); }
    await p.eval("toggleMarkHere(); 0");
    ok(!(await p.eval(`marks.some(m => m.cfi === ${JSON.stringify(a)})`)), "pressed again: the bookmark removed");
  }

  // 13. reader settings synced from another device (with an old "scroll" in them) leave the mode
  {
    const f = await J(p, "__first()");
    await p.eval(`localStorage.setItem('epubReader:prefs', JSON.stringify(Object.assign({}, prefs, { scroll: false, font: 110 }))); reloadPrefs(); 0`);
    await sleep(300); await settle(p);
    ok(await p.eval("prefs.font") === 110, "synced settings applied (font 110%)");
    ok(await p.eval("__flow()") === "scrolled" && await p.eval("document.getElementById('scrollText').checked"), "synced settings: still scroll mode (the setting is this device's)");
    { const l = await sameWord(p, f); ok(atTop(l), `the place kept (${fmt(l)})`); }
    await p.eval("savePrefs(); 0");
    ok(!("scroll" in (await J(p, "JSON.parse(localStorage.getItem('epubReader:prefs'))"))), "saving again drops the old \"scroll\" from the synced settings");
  }
  p.close();
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
