// Folders in the catalog (this device's own set, synced with the account), in headless Chrome on a phone.
//   node tests/reader/folders.mjs [base]
// The check builds a library of its own (6 books copied from Out/ under made-up names, a catalog
// made by make_server.py, served by its own library_sync.py on a free port), so [base] is not
// needed and not used; it is accepted so the check can be called like the others.
// Folders are seeded into localStorage (epubLibrary:shelves) before the catalog loads.
//   1. no folders: the strip is hidden, the catalog looks as before
//   2. two folders: the strip lists All books and the folders by name with their counts; a tap
//      opens one (only its books, the count says so), search works inside it; Back goes to All
//      books; a reload comes back to the open folder
//   3. a phone screen: the strip scrolls sideways, the page does not
//   4. an empty folder says how to add books
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
const LIB = mkdtempSync(join(tmpdir(), "epub-folders-"));
const NAMES = [];
epubs(join(ROOT, "Out")).slice(0, 6).forEach((src, i) => {
  const name = `Jane Doe/Jane Doe - A Quiet Harbour ${String(i + 1).padStart(2, "0")}.epub`;
  mkdirSync(join(LIB, "Jane Doe"), { recursive: true }); copyFileSync(src, join(LIB, name)); NAMES.push(name);
});
const server = spawn("python3", [join(ROOT, "launchers/library_sync.py"), "--serve", LIB, "--port", "0"], { stdio: ["ignore", "pipe", "ignore"] });
process.on("exit", () => { try { server.kill("SIGKILL"); } catch {} try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
if (NAMES.length < 6) { console.log("FAIL: Out/ has too few books"); process.exit(1); }
const built = spawnSync("python3", [join(ROOT, "make_server.py"), LIB], { encoding: "utf8" });
ok(built.status === 0, "catalog built for the check");
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
const cards = d => d.eval("document.querySelectorAll('#list .book').length");
const count = d => d.eval("document.getElementById('count').textContent");
// the catalog loads with these folders stored (and nothing else changed); later loads keep what the page stores
const seeded = async (d, map) => {
  const { identifier } = await d.send("Page.addScriptToEvaluateOnNewDocument",
    { source: `try { localStorage.setItem("epubLibrary:shelves", ${JSON.stringify(JSON.stringify(map))}); } catch {}` });
  await catalog(d);
  await d.send("Page.removeScriptToEvaluateOnNewDocument", { identifier });
};
const folder = (name, ids) => ({ name, named: 1, created: 1, books: Object.fromEntries(ids.map(id => [id, { time: 1 }])) });
const chips = d => d.eval("[...document.querySelectorAll('#folders button.fchip')].map(b => [...b.childNodes].map(n => n.textContent.trim()).filter(Boolean).join(' '))");
const tapChip = async (d, id) => {
  const at = await d.eval(`(r => r && r.width ? [r.left + r.width / 2, r.top + r.height / 2] : null)(
    (e => e && e.getBoundingClientRect())(document.querySelector('#folders button.fchip[data-folder="${id}"]')))`);
  if (!at) { ok(false, `chip ${JSON.stringify(id)} is on the screen`); return; }
  await d.tap(...at);
};
const search = (d, q) => d.eval(`(() => { const e = document.getElementById('q'); e.value = ${JSON.stringify(q)}; e.dispatchEvent(new Event('input')); })()`);

const d = await launch({ width: 390, height: 844, touch: true });

// ======== 1. no folders
await catalog(d);
ok(await d.eval("localStorage.getItem('epubLibrary:shelves')") === null, "no folders stored");
ok(await d.eval("!!document.getElementById('folders') && document.getElementById('folders').hidden"), "no folders: the strip is hidden");
ok(await cards(d) === 6, "no folders: all 6 books shown");
ok(await count(d) === "6 books", `no folders: the count reads "6 books" (${await count(d)})`);
// the reader's ids of the books, by file
const ids = await d.eval(`Object.fromEntries(books.map(b => [b.file.split("/").pop(), b.id]))`);
const id = n => ids[`Jane Doe - A Quiet Harbour ${String(n).padStart(2, "0")}.epub`];
ok([1, 2, 3, 4, 5, 6].every(n => id(n)) && new Set(Object.values(ids)).size === 6, "each book has its own id");

// ======== 2. two folders
await seeded(d, { f1: folder("To read", [id(1), id(2)]), f2: folder("Holiday", [id(3)]), gone: { deleted: 5 } });
ok(await d.eval("!document.getElementById('folders').hidden"), "folders: the strip is shown");
const shown = await chips(d);
ok(JSON.stringify(shown) === JSON.stringify(["All books", "Holiday 1", "To read 2"]), `strip: All books, then by name with counts (${shown.join(" | ")})`);
ok(await d.eval("document.querySelector('#folders .fchip[data-folder=\"\"]').getAttribute('aria-pressed')") === "true", "All books is the open one");
ok(await cards(d) === 6, "All books: 6 books");
await tapChip(d, "f1");
try { await d.waitFor("document.querySelectorAll('#list .book').length === 2", 3000); } catch {}
ok(await cards(d) === 2, `a tap on "To read": 2 books (${await cards(d)})`);
await d.shot("folders-open.png");
ok(await count(d) === "2 books in To read", `count: "2 books in To read" (${await count(d)})`);
ok(await d.eval("document.querySelector('#folders .fchip[data-folder=\"f1\"]').getAttribute('aria-pressed')") === "true", "To read is the open one");
const keys = await d.eval("[...document.querySelectorAll('#list .book')].map(a => decodeURIComponent(a.dataset.key).split('/').pop()).sort().join()");
ok(keys === "Jane Doe - A Quiet Harbour 01.epub,Jane Doe - A Quiet Harbour 02.epub", `its books are books 1 and 2 (${keys})`);
await search(d, "Harbour 01");
ok(await cards(d) === 1 && await count(d) === "1 of 2 in To read", `search inside: "1 of 2 in To read" (${await count(d)})`);
await search(d, "");
ok(await cards(d) === 2, "search cleared: 2 books again");
ok(await d.eval("localStorage.getItem('epubIndex:folder')") === "f1", "the open folder is remembered on this device");
// a reload comes back to it; Back then goes to All books
await catalog(d);
ok(await cards(d) === 2 && await count(d) === "2 books in To read", `reload: "To read" is open again (${await count(d)})`);
await d.eval("history.back()");
try { await d.waitFor("document.querySelectorAll('#list .book').length === 6", 3000); } catch {}
ok(await cards(d) === 6 && await count(d) === "6 books", `Back: All books (${await count(d)})`);
ok(await d.eval("location.href") === CAT, "Back stays on the catalog");
ok(await d.eval("localStorage.getItem('epubIndex:folder')") === "", "All books is remembered");

// ======== 3. the strip on a phone screen: it scrolls sideways, the page does not
{
  const map = {};
  for (let i = 1; i <= 8; i++) map["g" + i] = folder(`Folder number ${i}`, [id(1 + i % 6)]);
  await seeded(d, map);
  const w = await d.eval("({ page: document.documentElement.scrollWidth, win: innerWidth, sw: document.getElementById('folders').scrollWidth, cw: document.getElementById('folders').clientWidth })");
  await d.shot("folders-many.png");
  ok(w.page <= w.win, `8 folders: no sideways page scroll (${w.page} <= ${w.win})`);
  ok(w.sw > w.cw, `8 folders: the strip scrolls sideways (${w.sw} > ${w.cw})`);
  ok((await chips(d)).length === 9, "8 folders: 9 chips");
  // the open one is scrolled into view
  await d.eval("openFolder('g8')");
  await sleep(300);
  const r = await d.eval("(r => [Math.round(r.left), Math.round(r.right)])(document.querySelector('#folders .fchip[data-folder=\"g8\"]').getBoundingClientRect())");
  ok(r[0] >= 0 && r[1] <= 390 + 1, `the last folder opened: its chip is in view (${r})`);
  await d.eval("openFolder(null)");
}

// ======== 4. an empty folder
await seeded(d, { e1: folder("Empty one", []), f1: folder("To read", [id(1)]) });
await tapChip(d, "e1");
try { await d.waitFor("!!document.querySelector('#list .empty')", 3000); } catch {}
const empty = await d.eval("(e => e ? e.textContent.trim() : null)(document.querySelector('#list .empty'))");
ok(empty === "No books here yet. Open a book and choose Folders to add it.", `empty folder: says how to add books (${empty})`);
ok(await count(d) === "0 books in Empty one", `empty folder: count (${await count(d)})`);

// a folder deleted while open (another device): back to All books
await seeded(d, { e1: { deleted: 9 }, f1: folder("To read", [id(1)]) });
ok(await cards(d) === 6 && await count(d) === "6 books", `the open folder was deleted: All books (${await count(d)})`);

d.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
