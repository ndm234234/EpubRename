// Which folders a book is in, shown in the catalog (docs/superpowers/specs/2026-11-28-book-folders-shown-design.md).
//   node tests/reader/book-folders.mjs [base]
// Like folders.mjs, the check builds a library of its own (6 books copied from Out/ under made-up
// names) and serves it on a free port; [base] is accepted and not used.
//   1. a book in no folder: no line on its card, no chips on its page
//   2. the card line: folders by name, two names then +N, the full list in its title; names are text
//   3. inside a folder: that folder is left out of the line; Unfiled: no lines
//   4. a long name on a phone: one line, nothing scrolls sideways
//   5. the book page: a chip per folder; a tick in the picker changes the chips and the card at once
//   6. a chip opens its folder: the page closes, one folder step, Back goes to All books
//   7. a computer screen: the line and the chips are there
import { mkdtempSync, mkdirSync, copyFileSync, readdirSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };

// ---- a library of its own
const epubs = dir => readdirSync(dir).flatMap(n => { const f = join(dir, n);
  return statSync(f).isDirectory() ? epubs(f) : n.endsWith(".epub") ? [f] : []; });
const LIB = mkdtempSync(join(tmpdir(), "epub-bookfolders-"));
const book = n => `Jane Doe - A Quiet Harbour ${String(n).padStart(2, "0")}.epub`;
let made = 0;
epubs("Out").slice(0, 6).forEach((src, i) => {
  mkdirSync(join(LIB, "Jane Doe"), { recursive: true }); copyFileSync(src, join(LIB, "Jane Doe", book(i + 1))); made++;
});
const server = spawn("python3", ["launchers/library_sync.py", "--serve", LIB, "--port", "0"], { stdio: ["ignore", "pipe", "ignore"] });
process.on("exit", () => { try { server.kill("SIGKILL"); } catch {} try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
if (made < 6) { console.log("FAIL: Out/ has too few books"); process.exit(1); }
ok(spawnSync("python3", ["make_server.py", LIB], { encoding: "utf8" }).status === 0, "catalog built for the check");
const PORT = await new Promise(res => {
  let out = "";
  server.stdout.on("data", d => { out += d; const m = /Library: http:\/\/127\.0\.0\.1:(\d+)\//.exec(out); if (m) res(+m[1]); });
  server.on("exit", () => res(0));
});
if (!PORT) { console.log("FAIL: library_sync.py did not start"); process.exit(1); }
const CAT = `http://127.0.0.1:${PORT}/index.html`;

// ---- helpers
const LISTED = "document.querySelectorAll('#list .book').length > 0 || !!document.querySelector('#list .empty')";
const catalog = async d => { await d.goto("about:blank"); await d.goto(CAT); await d.waitFor(LISTED, 20000); };
const seeded = async (d, map) => {
  const { identifier } = await d.send("Page.addScriptToEvaluateOnNewDocument",
    { source: `try { localStorage.setItem("epubLibrary:shelves", ${JSON.stringify(JSON.stringify(map))}); localStorage.removeItem("epubIndex:folder"); } catch {}` });
  await catalog(d);
  await d.send("Page.removeScriptToEvaluateOnNewDocument", { identifier });
};
const folder = (name, ids) => ({ name, named: 1, created: 1, books: Object.fromEntries(ids.map(id => [id, { time: 1 }])) });
const cardSel = n => `[...document.querySelectorAll('#list .book')].find(a => decodeURIComponent(a.dataset.key).endsWith(${JSON.stringify(book(n))}))`;
// the card's folder line: [text, title] or null
const line = (d, n) => d.eval(`(c => (e => e ? [e.textContent.trim(), e.title] : null)(c && c.querySelector('.bfolders')))(${cardSel(n)})`);
const tags = d => d.eval("(e => e ? [...e.querySelectorAll('.ftag')].map(t => t.textContent.trim()) : null)(document.querySelector('#detail .dfolders'))");
const spot = (d, sel) => d.eval(`(e => { if (!e) return null; e.scrollIntoView({ block: "center" });
  const r = e.getBoundingClientRect(); return r.width ? [r.left + r.width / 2, r.top + r.height / 2] : null; })(${sel})`);
const tapAt = async (d, sel, what) => { const at = await spot(d, sel); if (!at) { ok(false, `${what} is on the screen`); return; } await d.tap(...at); };
const openBook = async (d, n) => {
  await tapAt(d, `(a => a && a.querySelector('.cover'))(${cardSel(n)})`, `the cover of book ${n}`);
  try { await d.waitFor("!document.getElementById('detail').hidden", 3000); } catch {}
  await sleep(500);   // the opening flight
};
const back = async d => { await d.eval("setTimeout(() => history.back())"); await sleep(600); };
const count = d => d.eval("document.getElementById('count').textContent");

const d = await launch({ width: 390, height: 844, touch: true });
await catalog(d);
const ids = await d.eval(`Object.fromEntries(books.map(b => [b.file.split("/").pop(), b.id]))`);
const id = n => ids[book(n)];
const MAP = { f1: folder("To read", [id(1), id(2), id(3)]), f2: folder("Holiday", [id(1)]), f3: folder("Kids", [id(1)]),
              f4: folder("<b>x</b>", [id(2)]), gone: { deleted: 5 } };

// ======== 1. a book in no folder
await seeded(d, MAP);
ok(await line(d, 4) === null, "book 4 (no folder): no line on its card");
// ======== 2. the card line
{
  const l1 = await line(d, 1), l2 = await line(d, 2), l3 = await line(d, 3);
  ok(l1 && l1[0] === "Holiday · Kids +1", `book 1: "Holiday · Kids +1" (${l1 && l1[0]})`);
  ok(l1 && l1[1] === "Holiday, Kids, To read", `book 1: the title lists all (${l1 && l1[1]})`);
  ok(l2 && l2[0] === "<b>x</b> · To read", `book 2: the name is text, not markup (${l2 && l2[0]})`);
  ok(l3 && l3[0] === "To read", `book 3: "To read" (${l3 && l3[0]})`);
  ok(await d.eval(`!${cardSel(1)}.querySelector('.bfolders a, .bfolders button')`), "the line holds no link or button");
}
await d.shot("book-folders-cards.png");
// ======== 3. inside a folder; Unfiled
await d.eval("openFolder('f1')"); await sleep(200);
{
  const l1 = await line(d, 1);
  ok(l1 && l1[0] === "Holiday · Kids", `in To read, book 1: "Holiday · Kids" (${l1 && l1[0]})`);
  ok(l1 && l1[1] === "Holiday, Kids, To read", `in To read, book 1: the title still lists all (${l1 && l1[1]})`);
  ok(await line(d, 3) === null, "in To read, book 3 (only there): no line");
}
await d.eval("openFolder('~unfiled')"); await sleep(200);
ok(await d.eval("document.querySelectorAll('#list .book').length > 0 && !document.querySelector('#list .bfolders')"), "Unfiled: no lines");
await d.eval("openFolder(null)"); await sleep(200);
// ======== 4. a long name on a phone
{
  const long = "A folder with a very long name that goes on and on and on...";
  await seeded(d, { ...MAP, f5: folder(long, [id(5)]), f6: folder("Zz", [id(5)]) });
  const l5 = await line(d, 5);
  ok(l5 && l5[0] === `${long} · Zz`, `book 5: the long name and Zz (${l5 && l5[0]})`);
  const fit = await d.eval(`(c => { const e = c.querySelector('.bfolders'), r = e.getBoundingClientRect(), i = c.querySelector('.info').getBoundingClientRect();
    return { one: r.height < parseFloat(getComputedStyle(e).lineHeight) * 1.5 || r.height < 24, inside: r.right <= i.right + 1, page: document.documentElement.scrollWidth, win: innerWidth }; })(${cardSel(5)})`);
  ok(fit.one, "the long line stays on one line");
  ok(fit.inside, "the long line stays inside its card");
  ok(fit.page <= fit.win, `nothing scrolls sideways (${fit.page} <= ${fit.win})`);
}
// ======== 5. the book page
await seeded(d, MAP);
await openBook(d, 4);
ok(await tags(d) === null, "book 4's page: no folder chips");
await back(d);
await openBook(d, 1);
ok(JSON.stringify(await tags(d)) === JSON.stringify(["Holiday", "Kids", "To read"]), `book 1's page: Holiday, Kids, To read (${await tags(d)})`);
ok(await d.eval("[...document.querySelectorAll('#detail .ftag')].every(t => t.tagName === 'BUTTON' && t.dataset.folder)"), "the chips are buttons naming their folder");
ok(await d.eval("(m => !!m && m.nextElementSibling === document.querySelector('#detail .dfolders'))(document.querySelector('#detail .meta'))"), "the chips are under the meta line");
await d.shot("book-folders-page.png");
await tapAt(d, "document.getElementById('folderBtn')", "the Folders button");
try { await d.waitFor("!document.getElementById('folderPick').hidden", 2000); } catch {}
await tapAt(d, "[...document.querySelectorAll('#folderPick label')].find(l => l.textContent.trim() === 'Holiday')", "the row Holiday");
await sleep(200);
ok(JSON.stringify(await tags(d)) === JSON.stringify(["Kids", "To read"]), `Holiday unticked: the chips are Kids, To read (${await tags(d)})`);
ok((await line(d, 1) || [])[0] === "Kids · To read", `Holiday unticked: the card behind reads "Kids · To read" (${(await line(d, 1) || [])[0]})`);
await back(d);   // the sheet
await back(d);   // the page
// ======== 6. a chip opens its folder (from a book page opened inside another folder)
await seeded(d, MAP);
await d.eval("openFolder('f1')"); await sleep(200);
await openBook(d, 1);
await tapAt(d, "document.querySelector('#detail .ftag[data-folder=\"f3\"]')", "the chip Kids");
await sleep(600);
ok(await d.eval("document.getElementById('detail').hidden"), "chip Kids: the book page is closed");
ok(await count(d) === "1 book in Kids", `chip Kids: the folder Kids is open (${await count(d)})`);
ok(await d.eval("!!history.state && history.state.folder === 'f3' && !history.state.book"), `one folder step, Kids (${await d.eval("JSON.stringify(history.state)")})`);
await back(d);
ok(await count(d) === "6 books", `Back: All books (${await count(d)})`);
ok(await d.eval("location.href") === CAT, "Back: still the catalog");
// from All books too
await openBook(d, 2);
await tapAt(d, "document.querySelector('#detail .ftag[data-folder=\"f4\"]')", "the chip <b>x</b>");
await sleep(600);
ok(await count(d) === "1 book in <b>x</b>", `from All books, chip <b>x</b>: that folder is open (${await count(d)})`);
await back(d);
ok(await count(d) === "6 books", `Back: All books (${await count(d)})`);
d.close();
// ======== 7. a computer screen
{
  const w = await launch({ width: 1280, height: 800, touch: false });
  await seeded(w, MAP);
  ok((await line(w, 1) || [])[0] === "Holiday · Kids +1", "computer: the card line");
  await w.eval(`${cardSel(1)}.querySelector('.cover').click()`);
  try { await w.waitFor("!document.getElementById('detail').hidden", 3000); } catch {}
  await sleep(500);
  ok(JSON.stringify(await tags(w)) === JSON.stringify(["Holiday", "Kids", "To read"]), `computer: the chips (${await tags(w)})`);
  await w.shot("book-folders-page-wide.png");
  w.close();
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
