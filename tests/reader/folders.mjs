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
// Part 2, the book's page (Folders: tick to put the book in, untick to take it out, a new folder):
//   5. no folders: the list has only "＋ New folder"; a new one is made ticked; the strip appears
//   6. tick, untick, tick; a second folder: the book is in both; Back closes the sheet, not the page
//   7. Back order: folder, book page, Back (the folder), Back (All books); a reload keeps the folder
//   8. a folder naming a book not in the library: not counted, kept
//   9. a book renamed on disk stays in its folder; the reader keeps its place under the same id
//  10. a name already used (ignoring case), empty, too long: a short message, nothing made
//  11. a computer screen: a popover near the button, no backdrop; a click elsewhere or Esc closes it
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

// ======== 2b. Back always goes to All books: one folder step at most
{
  const open = async (id, n) => { await tapChip(d, id);
    try { await d.waitFor(`document.querySelectorAll('#list .book').length === ${n}`, 3000); } catch {} };
  const back = async () => { await d.eval("setTimeout(() => history.back())"); await sleep(600); };   // may leave the page
  const state = () => d.eval("JSON.stringify(history.state)");
  // (a) folder A, folder B, Back: All books
  await catalog(d);   // about:blank, then the catalog with All books
  const len = await d.eval("history.length");
  await open("f1", 2); await open("f2", 1);
  ok(await count(d) === "1 books in Holiday", `folder A, then B: B is open (${await count(d)})`);
  ok(await d.eval("history.length") === len + 1, "folder A, then B: one history step above All books");
  await back();
  ok(await cards(d) === 6 && await count(d) === "6 books", `A, B, Back: All books (${await count(d)})`);
  ok(await d.eval("location.href") === CAT, "A, B, Back: still the catalog");
  // (b) folder A, the All books chip, Back: leaves the catalog as before (does not reopen A)
  await catalog(d);
  await open("f1", 2);
  await open("", 6);
  await sleep(300);
  ok(await cards(d) === 6 && await count(d) === "6 books", `A, All books chip: All books (${await count(d)})`);
  ok(await state() === "null" || await state() === "{}", `A, All books chip: the folder step is left (state ${await state()})`);
  await back();
  ok(await d.eval("location.href") === "about:blank", `A, All books chip, Back: the page before the catalog (${await d.eval("location.href")})`);
  // (c) folder, a book's page, Back: the folder with its books; Back again: All books
  await catalog(d);
  await open("f1", 2);
  await d.eval("document.querySelector('#list .book .cover').click()");
  try { await d.waitFor("!document.getElementById('detail').hidden", 3000); } catch {}
  ok(await d.eval("!document.getElementById('detail').hidden"), "folder: a cover opens the book page");
  await back();
  ok(await d.eval("document.getElementById('detail').hidden"), "book page, Back: the book page is closed");
  ok(await cards(d) === 2 && await count(d) === "2 books in To read", `book page, Back: the folder with its books (${await count(d)})`);
  await back();
  ok(await cards(d) === 6 && await count(d) === "6 books", `then Back: All books (${await count(d)})`);
  ok(await d.eval("location.href") === CAT, "then Back: still the catalog");
}

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

// ================================================================ part 2: the book's page
const PICK = "!document.getElementById('folderPick').hidden";
const stored = d => d.eval("JSON.parse(localStorage.getItem('epubLibrary:shelves') || '{}')");
const liveIds = map => Object.keys(map).filter(k => !map[k].deleted);
const byName = (map, name) => liveIds(map).find(k => map[k].name === name);
// the centre of an element (scrolled into view), or null
const spot = (d, sel) => d.eval(`(e => { if (!e) return null; e.scrollIntoView({ block: "center" });
  const r = e.getBoundingClientRect(); return r.width ? [r.left + r.width / 2, r.top + r.height / 2] : null; })(${sel})`);
const tapAt = async (d, sel, what) => { const at = await spot(d, sel); if (!at) { ok(false, `${what} is on the screen`); return; } await d.tap(...at); };
const fileSel = n => `[...document.querySelectorAll('#list .book')].find(a => decodeURIComponent(a.dataset.key).endsWith(${JSON.stringify(n)}))`;
const openBook = async (d, file) => {
  await tapAt(d, `(a => a && a.querySelector('.cover'))(${fileSel(file)})`, `the cover of ${file}`);
  try { await d.waitFor("!document.getElementById('detail').hidden", 3000); } catch {}
  await sleep(500);   // the opening flight
};
const book = n => `Jane Doe - A Quiet Harbour ${String(n).padStart(2, "0")}.epub`;
const tapFolders = async d => {
  await tapAt(d, "document.getElementById('folderBtn')", "the Folders button");
  try { await d.waitFor(PICK, 2000); } catch {}
};
const rows = d => d.eval("[...document.querySelectorAll('#folderPick label')].map(l => [l.textContent.trim(), l.querySelector('input[type=checkbox]').checked])");
const tapRow = (d, name) => tapAt(d, `[...document.querySelectorAll('#folderPick label')].find(l => l.textContent.trim() === ${JSON.stringify(name)})`, `the row ${name}`);
const typeNew = async (d, name) => {
  await tapAt(d, "document.getElementById('folderNew')", "＋ New folder");
  ok(await d.eval("document.activeElement === document.getElementById('folderNewName')"), "＋ New folder: the name field has the focus");
  await d.eval(`(() => { const e = document.getElementById('folderNewName'); e.value = ${JSON.stringify(name)};
    e.dispatchEvent(new Event('input')); e.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
  await sleep(200);
};
const back = async d => { await d.eval("setTimeout(() => history.back())"); await sleep(600); };

// ======== 5. no folders: the first one is made from a book's page
await seeded(d, {});
await openBook(d, book(1));
await tapFolders(d);
ok(await d.eval(PICK), "Folders: the list is open");
ok(await d.eval("(e => e.getAttribute('role') === 'dialog' && e.getAttribute('aria-label') === 'Folders')(document.getElementById('folderPick'))"), "the list is a dialog named Folders");
ok((await rows(d)).length === 0 && await d.eval("(e => !!e && !e.hidden && e.textContent.trim() === '＋ New folder')(document.getElementById('folderNew'))"),
   "no folders: the list shows only ＋ New folder");
{
  const r = await d.eval("(r => [r.left, r.right, r.bottom].map(Math.round))(document.getElementById('folderPick').getBoundingClientRect())");
  ok(r[0] === 0 && r[1] === 390 && r[2] === 844, `phone: a sheet along the bottom of the screen (${r})`);
  ok(await d.eval("!document.getElementById('folderDim').hidden"), "phone: the book page is dimmed behind it");
  ok(await d.eval("!!(history.state && history.state.pick)"), "phone: the sheet is a history step");
}
await d.shot("folders-pick-empty.png");
await typeNew(d, "  To read ");
ok(JSON.stringify(await rows(d)) === JSON.stringify([["To read", true]]), `a new folder "To read", ticked (${JSON.stringify(await rows(d))})`);
{
  const m = await stored(d), f = byName(m, "To read");
  ok(liveIds(m).length === 1 && f && m[f].books[id(1)], "stored: one folder, holding book 1");
}
ok(await d.eval("document.getElementById('folderNew').hidden === false && document.getElementById('folderNewName').offsetParent === null"), "after Enter: ＋ New folder again");
await d.shot("folders-pick-one.png");
await d.tap(195, 120);   // the dimmed book page
ok(await d.eval("document.getElementById('folderPick').hidden && document.getElementById('folderDim').hidden"), "a tap on the backdrop closes the sheet");
ok(await d.eval("!document.getElementById('detail').hidden"), "the book page stays");
ok(await d.eval("!(history.state && history.state.pick) && !!(history.state && history.state.book)"), "the sheet's history step is gone");
await back(d);
ok(await d.eval("document.getElementById('detail').hidden"), "Back: the book page is closed");
ok(JSON.stringify(await chips(d)) === JSON.stringify(["All books", "To read 1"]), `the strip appears: To read 1 (${(await chips(d)).join(" | ")})`);

// ======== 6. tick, untick, tick; a second folder
await openBook(d, book(2));
await tapFolders(d);
ok(JSON.stringify(await rows(d)) === JSON.stringify([["To read", false]]), `book 2: To read, not ticked (${JSON.stringify(await rows(d))})`);
await tapRow(d, "To read");
ok((await chips(d)).includes("To read 2"), `ticked: To read 2 (${(await chips(d)).join(" | ")})`);
await tapRow(d, "To read");
ok((await chips(d)).includes("To read 1"), `unticked: To read 1 (${(await chips(d)).join(" | ")})`);
await tapRow(d, "To read");
ok((await chips(d)).includes("To read 2") && JSON.stringify(await rows(d)) === JSON.stringify([["To read", true]]), `ticked again: To read 2 (${(await chips(d)).join(" | ")})`);
await typeNew(d, "Holiday");
ok(JSON.stringify(await rows(d)) === JSON.stringify([["Holiday", true], ["To read", true]]), `a second folder, by name, both ticked (${JSON.stringify(await rows(d))})`);
ok(JSON.stringify(await chips(d)) === JSON.stringify(["All books", "Holiday 1", "To read 2"]), `book 2 in both (${(await chips(d)).join(" | ")})`);
await d.eval("document.getElementById('folderPick').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
await sleep(400);
ok(await d.eval("document.getElementById('folderPick').hidden && !document.getElementById('detail').hidden"), "Esc closes the sheet, not the book page");
await tapFolders(d);
await back(d);
ok(await d.eval("document.getElementById('folderPick').hidden"), "Back closes the sheet");
ok(await d.eval("!document.getElementById('detail').hidden"), "Back with the sheet open: the book page stays");
await back(d);
ok(await d.eval("document.getElementById('detail').hidden") && await d.eval("location.href") === CAT, "Back again: the catalog");

// ======== 7. Back order
{
  const toRead = byName(await stored(d), "To read");
  await tapChip(d, toRead);
  try { await d.waitFor("document.querySelectorAll('#list .book').length === 2", 3000); } catch {}
  await openBook(d, book(2));
  ok(await d.eval("!document.getElementById('detail').hidden"), "in To read: book 2's page");
  await back(d);
  ok(await d.eval("document.getElementById('detail').hidden") && await cards(d) === 2 && await count(d) === "2 books in To read",
     `Back: the folder with its 2 books (${await count(d)})`);
  await back(d);
  ok(await cards(d) === 6 && await count(d) === "6 books" && await d.eval("location.href") === CAT, `Back: All books (${await count(d)})`);
  await tapChip(d, toRead);
  try { await d.waitFor("document.querySelectorAll('#list .book').length === 2", 3000); } catch {}
  await catalog(d);
  ok(await count(d) === "2 books in To read", `a reload: still To read (${await count(d)})`);
}

// ======== 8. a folder naming a book not in the library
await seeded(d, { m1: folder("Mixed", [id(1), "urn:not-here"]) });
ok(JSON.stringify(await chips(d)) === JSON.stringify(["All books", "Mixed 1"]), `a book not here is not counted (${(await chips(d)).join(" | ")})`);
await openBook(d, book(4));
await tapFolders(d);
await tapRow(d, "Mixed");
{
  const m = await stored(d);
  ok(m.m1.books["urn:not-here"] && m.m1.books[id(4)] && m.m1.books[id(1)], "ticking another book keeps the one not here");
}
await back(d); await back(d);

// ======== 9. a book renamed on disk
{
  await seeded(d, { r1: folder("Renamed", [id(3)]) });
  const from = NAMES[2], to = "Jane Doe/Jane Doe - A Quiet Harbour 03 (second edition).epub";
  copyFileSync(join(LIB, from), join(LIB, to)); rmSync(join(LIB, from));
  const again = spawnSync("python3", [join(ROOT, "make_server.py"), LIB], { encoding: "utf8" });
  ok(again.status === 0, "renamed: catalog built again");
  await d.send("Network.enable"); await d.send("Network.setCacheDisabled", { cacheDisabled: true });   // the new page, not a kept copy
  await catalog(d);
  const b3 = await d.eval(`(b => b && { id: b.id, href: readerUrl(b) })(books.find(b => b.file.endsWith(${JSON.stringify(to.split("/").pop())})))`);
  ok(b3 && b3.id === id(3), `renamed: the same id (${b3 && b3.id})`);
  ok(await cards(d) === 6 && JSON.stringify(await chips(d)) === JSON.stringify(["All books", "Renamed 1"]), `renamed: still in its folder (${(await chips(d)).join(" | ")})`);
  await tapChip(d, "r1");
  try { await d.waitFor("document.querySelectorAll('#list .book').length === 1", 3000); } catch {}
  ok(await d.eval(`!!${fileSel(to.split("/").pop())}`), "renamed: the folder shows it under the new name");
  if (b3) {
    const key = "epubReader:book:" + b3.id;
    await d.eval(`localStorage.removeItem(${JSON.stringify(key)})`);
    await d.goto(new URL(b3.href, CAT).href);
    let saved = false;
    try { await d.waitFor(`localStorage.getItem(${JSON.stringify(key)}) !== null`, 30000); saved = true; } catch {}
    ok(saved, `the reader saves its place under the catalog's id (${key})`);
  }
  await catalog(d);
}

// ======== 10. a name already used, empty, too long
await seeded(d, { f1: folder("To read", [id(1)]) });
await openBook(d, book(2));
await tapFolders(d);
const err = () => d.eval("(e => e && e.offsetParent ? e.textContent.trim() : '')(document.getElementById('folderErr'))");
await typeNew(d, "to read");
ok(await err(d) === "You already have a folder with that name.", `"to read": a short message (${await err(d)})`);
ok(await d.eval("(e => e.getBoundingClientRect().top >= document.getElementById('folderNewName').getBoundingClientRect().bottom - 1)(document.getElementById('folderErr'))"),
   "the message is under the field");
ok(liveIds(await stored(d)).length === 1 && (await rows(d)).length === 1, "nothing created");
await d.eval(`(() => { const e = document.getElementById('folderNewName'); e.value = "   ";
  e.dispatchEvent(new Event('input')); e.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
ok(await err(d) === "Give the folder a name.", `empty: a short message (${await err(d)})`);
ok(await d.eval("document.getElementById('folderNewName').maxLength") === 60, "the field takes at most 60 characters");
await d.eval(`(() => { const e = document.getElementById('folderNewName'); e.value = "x".repeat(61);
  e.dispatchEvent(new Event('input')); e.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
ok(await err(d) === "At most 60 characters.", `61 characters: a short message (${await err(d)})`);
ok(liveIds(await stored(d)).length === 1, "still nothing created");
await d.shot("folders-pick-taken.png");
d.close();

// ======== 11. a computer screen: a popover
{
  const w = await launch({ width: 1280, height: 800, touch: false });
  await catalog(w);
  await w.eval("showDetail(books[0])");
  await sleep(700);
  const len = await w.eval("history.length");
  const btn = await spot(w, "document.getElementById('folderBtn')");
  ok(await w.eval("document.getElementById('folderBtn').textContent.trim()") === "Folders", "computer: the button says Folders");
  await w.click(...btn);
  ok(await w.eval(PICK), "computer: the list is open");
  const r = await w.eval("(p => { const b = document.getElementById('folderBtn').getBoundingClientRect(), r = p.getBoundingClientRect(); return { w: r.width, gap: Math.min(Math.abs(r.top - b.bottom), Math.abs(b.top - r.bottom)), left: r.left, bl: b.left }; })(document.getElementById('folderPick'))");
  ok(r.w <= 400 && r.gap <= 16 && Math.abs(r.left - r.bl) <= 40, `computer: a popover next to the button (${JSON.stringify(r)})`);
  ok(await w.eval("document.getElementById('folderDim').hidden"), "computer: no backdrop");
  ok(await w.eval("history.length") === len, "computer: no history step");
  await w.shot("folders-pick-wide.png");
  await w.click(1200, 700);
  ok(await w.eval("document.getElementById('folderPick').hidden && !document.getElementById('detail').hidden"), "computer: a click elsewhere closes it, the book page stays");
  await w.click(...btn);
  await w.eval("document.getElementById('folderNew').focus()");
  await w.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await w.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await sleep(300);
  ok(await w.eval("document.getElementById('folderPick').hidden && !document.getElementById('detail').hidden"), "computer: Esc closes it, the book page stays");
  ok(await w.eval("document.activeElement === document.getElementById('folderBtn')"), "computer: the focus goes back to the button");
  w.close();
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
