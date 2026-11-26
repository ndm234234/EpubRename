// The catalog returns to where you were (local only, never synced), in headless Chrome on a phone.
//   node tests/reader/catalog-place.mjs [base]
// The check builds a library of its own (30 books copied from Out/ under made-up names, a catalog
// made by make_server.py, served by its own library_sync.py on a free port), so [base] is not
// needed and not used; it is accepted so the check can be called like the others.
//   1. installed app, "Recently opened": scroll to the 15th book, open the one at the top, read 3
//      pages, go back: the same book is at the same height (or the list is at its top with it first)
//   2. installed app: search, scroll, open a book; the reader in a fresh load, "Catalog": the
//      search text is back and the same book is at the top
//   3. browser tab: "Recently opened" is the default there too (the book read moves to the front);
//      a chosen sort wins; the back button keeps the scroll; the place is kept per kind of device
//   4. phone: the book page's cover and Read button open the reader on the first tap, also while
//      the cover is still flying in
import { mkdtempSync, mkdirSync, copyFileSync, readdirSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
const ROOT = ".";
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };

// ---- a library of its own
const epubs = dir => readdirSync(dir).flatMap(n => { const f = join(dir, n);
  return statSync(f).isDirectory() ? epubs(f) : n.endsWith(".epub") ? [f] : []; });
const LIB = mkdtempSync(join(tmpdir(), "epub-catalog-place-"));
const NAMES = [];
epubs(join(ROOT, "Out")).slice(0, 30).forEach((src, i) => {
  const n = String(i + 1).padStart(2, "0"), who = i % 3 === 2 ? "John Roe" : "Jane Doe";
  const name = `${who}/${who} - ${i % 3 === 2 ? "Second Tide" : "A Quiet Harbour"} ${n}.epub`;
  mkdirSync(join(LIB, who), { recursive: true }); copyFileSync(src, join(LIB, name)); NAMES.push(name);
});
const server = spawn("python3", [join(ROOT, "launchers/library_sync.py"), "--serve", LIB, "--port", "0"], { stdio: ["ignore", "pipe", "ignore"] });
process.on("exit", () => { try { server.kill("SIGKILL"); } catch {} try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
if (NAMES.length < 25) { console.log("FAIL: Out/ has too few books"); process.exit(1); }
const built = spawnSync("python3", [join(ROOT, "make_server.py"), LIB], { encoding: "utf8" });
ok(built.status === 0, "catalog built for the check");
// started after the catalog is built (the server sees the files either way)
const PORT = await new Promise(res => {
  let out = "";
  server.stdout.on("data", d => { out += d; const m = /Library: http:\/\/127\.0\.0\.1:(\d+)\//.exec(out); if (m) res(+m[1]); });
  server.on("exit", () => res(0));
});
if (!PORT) { console.log("FAIL: library_sync.py did not start"); process.exit(1); }
const CAT = `http://127.0.0.1:${PORT}/index.html`;

// ---- helpers
const APP = `(() => { const mm = matchMedia.bind(window);
  window.matchMedia = q => /display-mode/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : mm(q); })()`;
const device = async app => {
  const d = await launch({ width: 390, height: 844, touch: true });
  if (app) await d.send("Page.addScriptToEvaluateOnNewDocument", { source: APP });
  return d;
};
const LISTED = "document.querySelectorAll('#list .book').length > 0";
const catalog = async d => { await d.goto("about:blank"); await d.goto(CAT); await d.waitFor(LISTED, 20000); };
const HEAD = "document.querySelector('header').getBoundingClientRect().bottom";
// the first book whose card shows below the header: its key, its distance from the window top, the scroll
const top = d => d.eval(`(() => { const h = ${HEAD};
  for (const a of document.querySelectorAll('#list .book')) { const r = a.getBoundingClientRect();
    if (r.bottom > h + 1) return { key: a.dataset.key, top: Math.round(r.top), y: Math.round(scrollY) }; } })()`);
const scrollToBook = (d, n) => d.eval(`(() => { const a = document.querySelectorAll('#list .book')[${n}];
  scrollTo(0, a.getBoundingClientRect().top + scrollY - ${HEAD} - 20); })()`);
// the stored place has caught up with the screen
const saved = async (d, key) => {
  const k = await d.eval("inApp ? 'epubIndex:app:place' : 'epubIndex:place'");
  try { await d.waitFor(`(p => !!p && p.href === ${JSON.stringify(key)})(JSON.parse(localStorage.getItem(${JSON.stringify(k)})))`, 3000); }
  catch { ok(false, `the place is stored under ${k}`); }
  return JSON.parse(await d.eval(`localStorage.getItem(${JSON.stringify(k)})`));
};
const READY = "typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start";
const openBook = async (d, key) => {   // from the catalog, as a link does
  await d.eval(`(() => { const a = [...document.querySelectorAll('#list .book')].find(x => x.dataset.key === ${JSON.stringify(key)});
    location.href = a.querySelector('a.title').href; })()`);
  await d.waitFor(READY, 30000);
  await d.waitFor("!settling", 15000);
};
const rowTop = (d, key) => d.eval(`(a => a ? Math.round(a.getBoundingClientRect().top) : null)(
  [...document.querySelectorAll('#list .book')].find(x => x.dataset.key === ${JSON.stringify(key)}))`);
const settled = async (d, want, what) => {   // the wanted book is where it was on the screen
  let now = null;
  try { await d.waitFor(`(a => !!a && Math.abs(a.getBoundingClientRect().top - ${want.top}) <= 4)(
    [...document.querySelectorAll('#list .book')].find(x => x.dataset.key === ${JSON.stringify(want.key)}))`, 6000); } catch {}
  now = await rowTop(d, want.key);
  ok(now !== null && Math.abs(now - want.top) <= 4, `${what}: the same book at the same height (${want.top} -> ${now})`);
};

// ======== 1. installed app, "Recently opened"
{
  const d = await device(true);
  await catalog(d);
  ok(await d.eval("document.getElementById('sort').value") === "opened", "app: sorted by recently opened");
  await scrollToBook(d, 14);
  const before = await top(d);
  ok(before.y > 500, `scrolled down to the 15th book (${before.y} px)`);
  await saved(d, before.key);
  const first = await d.eval("document.querySelector('#list .book').dataset.key");
  await openBook(d, before.key);
  for (let i = 0; i < 3; i++) { await d.eval("rendition.next().then(() => 0)"); await d.waitFor("!settling", 15000); }
  await d.eval("history.back()");
  await d.waitFor(LISTED, 20000);
  const moved = await d.eval(`document.querySelector('#list .book').dataset.key === ${JSON.stringify(before.key)}`);
  ok(moved && first !== before.key, "the book just read moved to the front");
  // that book was the one at the top: the list is at its top with it first
  let now;
  try { await d.waitFor("scrollY < 4", 6000); } catch {}
  now = await top(d);
  ok(now.key === before.key && now.y < 4, `back from the reader: the list is at its top with the book just read first (y ${now.y})`);
  // another book at the top: it keeps its height although the order changed
  await scrollToBook(d, 14);
  const mid = await top(d);
  await saved(d, mid.key);
  // not the one at the top: open another one shown on the screen
  const shownKey = await d.eval(`(() => { const h = ${HEAD}; const l = [...document.querySelectorAll('#list .book')];
    const a = l.find(x => x.dataset.key !== ${JSON.stringify(mid.key)} && x.getBoundingClientRect().top > h + 100 && x.getBoundingClientRect().top < innerHeight - 100); return a && a.dataset.key; })()`);
  if (shownKey) {
    await openBook(d, shownKey);
    await d.eval("history.back()");
    await d.waitFor(LISTED, 20000);
    await settled(d, mid, "another book read (it moved to the front, the one above stays)");
  } else ok(false, "a second book on screen to open");
  d.close();
}

// ======== 2. installed app: search, scroll, the reader in a fresh load, "Catalog"
{
  const d = await device(true);
  await catalog(d);
  await d.eval("(() => { const q = document.getElementById('q'); q.value = 'harbour'; q.dispatchEvent(new Event('input')); })()");
  await d.waitFor("document.getElementById('count').textContent.includes(' of ')", 5000);
  const n = await d.eval("document.querySelectorAll('#list .book').length");
  ok(n >= 15 && n < NAMES.length, `searched: ${n} books shown`);
  await scrollToBook(d, 9);
  const before = await top(d);
  ok(before.y > 300, `scrolled down (${before.y} px)`);
  await saved(d, before.key);
  const q = await d.eval("JSON.parse(localStorage.getItem('epubIndex:app:place')).q");
  ok(q === "harbour", "the place keeps the search text");
  await openBook(d, before.key);
  // the app started again on the book: no catalog before it in this tab
  const readerUrl = await d.eval("location.href");
  await d.goto("about:blank"); await d.goto(readerUrl);
  await d.waitFor(READY, 30000); await d.waitFor("!settling", 15000);
  await d.eval("document.getElementById('back').click()");
  await d.waitFor(LISTED, 20000);
  ok(await d.eval("document.getElementById('q').value") === "harbour", "Catalog from a fresh reader: the search text is back");
  ok(await d.eval("document.querySelectorAll('#list .book').length") === n, "and the list is filtered by it");
  await settled(d, before, "Catalog from a fresh reader");
  d.close();
}

// ======== 3. browser tab: "Recently opened" by default too; the back button; its own place
{
  const d = await device(false);
  await catalog(d);
  ok(await d.eval("[...document.getElementById('sort').options].map(o => o.value).join()") === "opened,author,title,added,size",
     "browser tab: \"Recently opened\" is offered, first");
  ok(await d.eval("document.getElementById('sort').value") === "opened", "browser tab: sorted by recently opened by default");
  ok(await d.eval("localStorage.getItem('epubIndex:app')") === null, "browser tab: the app's settings are not touched");
  await scrollToBook(d, 14);
  const before = await top(d);
  await saved(d, before.key);
  ok(await d.eval("localStorage.getItem('epubIndex:app:place')") === null, "browser tab: the app's place is not touched");
  const first = await d.eval("document.querySelector('#list .book').dataset.key");
  await openBook(d, before.key);
  await d.eval("history.back()");
  await d.waitFor(LISTED, 20000);
  try { await d.waitFor(`document.querySelector('#list .book').dataset.key === ${JSON.stringify(before.key)}`, 6000); } catch {}
  ok(first !== before.key && await d.eval(`document.querySelector('#list .book').dataset.key === ${JSON.stringify(before.key)}`),
     "browser tab, back button: the book just read moved to the front");
  // an explicitly chosen sort wins over the default, and the back button keeps the scroll
  await d.eval("(() => { const s = document.getElementById('sort'); s.value = 'author'; s.dispatchEvent(new Event('change')); })()");
  await catalog(d);
  ok(await d.eval("document.getElementById('sort').value") === "author", "browser tab: a chosen sort (Author) stays after a reload");
  await scrollToBook(d, 14);
  const mid = await top(d);
  await saved(d, mid.key);
  await openBook(d, mid.key);
  await d.eval("history.back()");
  await d.waitFor(LISTED, 20000);
  await settled(d, mid, "browser tab, Author, back button");
  d.close();
}

// ======== 4. phone: the book page's cover and Read button work while the cover is still flying in
{
  const d = await device(false);
  const touch = async (x, y) => {   // a quick tap, nothing waited for afterwards
    await d.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await sleep(40);
    await d.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };
  const centre = sel => d.eval(`(r => r && r.width ? [r.left + r.width / 2, r.top + r.height / 2] : null)(
    (e => e && e.getBoundingClientRect())(document.querySelector(${JSON.stringify(sel)})))`);
  for (const [what, sel, after] of [["the cover", "#detail .dcover", 150], ["the Read button", "#detail .dact .btn.primary", 400]]) {
    await catalog(d);
    await d.eval("scrollTo(0, 0)");
    const shelf = await centre("#list .book .cover");
    await touch(...shelf);
    try { await d.waitFor("!document.getElementById('detail').hidden", 2000); } catch {}
    await sleep(after);
    const flying = await d.eval("flight !== null");
    const at = await centre(sel);
    if (!at) { ok(false, `book page: ${what} is there`); continue; }
    await touch(...at);
    let opened = false;
    try { opened = await d.waitFor("location.href.includes('reader.html')", 4000); } catch {}
    ok(flying && opened, `phone: ${what} tapped ${after} ms into the opening (cover still flying: ${flying}) opens the reader at once`);
  }
  d.close();
}

console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
