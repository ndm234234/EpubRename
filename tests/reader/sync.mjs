// Sync, part 1: the reader's local data rules and the account button / panel.
//   node sync.mjs <base: http://127.0.0.1:PORT of `launchers/library_sync.py --serve ROOT`> <book under Out/> [ROOT]
// ROOT (default ".") is the folder that server serves: the library code is read from ROOT/.sync/code.txt.
// - the place's time changes only on deliberate moves (a page turn), not on opening, reopening,
//   resizing or a font change; a book never read before is saved with time 0
// - bookmarks get an id (time|cfi), edits set `edited`, deleting writes a tombstone (marksGone)
// - prefs keep a "changed at" time
// - the account button: shown on a secure page (http://127.0.0.1), hidden on plain http with a host
//   name (http://lib.test, mapped to 127.0.0.1); signed out the reader sends nothing to /__sync
// - the panel: a wrong library code shows an error in the panel; the right one signs in and syncs
// Part 2: the catalog (index.html made by make_server.py from two books copied under made-up names
// into a temp library, served by its own library_sync.py): the account button; a book opened on one
// device comes first under "Recently opened" on another (installed-app mode stubbed through
// matchMedia); catalog settings of the app and of a browser tab sync separately; a sync that hangs
// never holds the catalog back.
import { readFileSync, mkdtempSync, mkdirSync, copyFileSync, readdirSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
const [BASE, BOOK, ROOT = "."] = process.argv.slice(2);
const INSECURE = BASE.replace("127.0.0.1", "lib.test");
const W = 390, H = 844;
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const READY = "typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start";
const url = base => `${base}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`;
const settle = async p => { await sleep(150); await p.waitFor("!settling", 15000); await sleep(150); };
const open = async (p, base) => { await p.goto("about:blank"); await p.goto(url(base)); await p.waitFor(READY, 30000); await settle(p); };
const place = p => p.eval("JSON.stringify(JSON.parse(localStorage.getItem(bookKey)))").then(JSON.parse);
const id = p => p.eval("bookKey.slice('epubReader:book:'.length)");
const syncCalls = p => p.eval("performance.getEntriesByType('resource').filter(e => e.name.includes('/__sync')).length");
const SHOWN = sel => `(e => !!e && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden')(document.querySelector('${sel}'))`;
// the toolbar itself is out of sight on a phone until a tap in the middle: only display counts here
const BTN = "(e => !!e && getComputedStyle(e).display !== 'none')(document.getElementById('acctBtn'))";
const text = sel => p.eval(`(document.querySelector('${sel}') || {}).textContent || ''`);

const p = await launch({ width: W, height: H, touch: true, args: ["--host-resolver-rules=MAP lib.test 127.0.0.1"] });

// ---- the place's time
await open(p, BASE);
await sleep(3000);
let s = await place(p);
ok(s && s.cfi && s.time === 0, `opened a new book, waited 3 s: place saved with time 0 (${s && s.time})`);
const t0 = await p.eval("Date.now()");
await p.eval("rendition.next().then(() => 0)"); await settle(p);
s = await place(p);
ok(s && s.time >= t0, `one page turn: the time is new (${s && s.time} >= ${t0})`);
const T = s.time, C = s.cfi;
await open(p, BASE);
await sleep(3000);
s = await place(p);
ok(s && s.time === T && s.cfi === C, `reopened, waited 3 s: time and place unchanged (${s && s.time})`);
await p.send("Emulation.setDeviceMetricsOverride", { width: W, height: H - 56, deviceScaleFactor: 2, mobile: true });
await sleep(700); await settle(p);
await p.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 2, mobile: true });
await sleep(700); await settle(p);
s = await place(p);
ok(s && s.time === T, `window resized: time unchanged (${s && s.time})`);
const tp = await p.eval("Date.now()");
await p.eval("setFont(10); 0"); await settle(p);
s = await place(p);
ok(s && s.time === T, `font size changed: place time unchanged (${s && s.time})`);
const pt = Number(await p.eval("localStorage.getItem('epubReader:prefsTime')"));
ok(pt >= tp, `font size changed: prefsTime set (${pt})`);
await p.eval("setFont(-10); 0"); await settle(p);

// ---- bookmarks
await p.eval("toggleMarkHere(); 0");
const m = JSON.parse(await p.eval("JSON.stringify(marks[marks.length - 1] || null)"));
ok(m && m.id === m.time + "|" + m.cfi, `new bookmark has id = time|cfi (${m && m.id})`);
const BID = await id(p);
const stored = JSON.parse(await p.eval(`localStorage.getItem(${JSON.stringify("epubReader:marks:")} + ${JSON.stringify(BID)})`));
ok(Array.isArray(stored) && stored.some(x => x.id === m.id), "the id is saved");
await p.eval(`(() => { renderMarks(); editNote(marks.find(x => x.id === ${JSON.stringify(m && m.id)}));
  const a = document.querySelector('#markList textarea'); a.value = 'a note'; a.dispatchEvent(new Event('input')); a.blur(); })(); 0`).catch(e => console.log("  (edit note: " + e.message + ")"));
const edited = await p.eval(`(marks.find(x => x.id === ${JSON.stringify(m && m.id)}) || {}).edited`);
ok(typeof edited === "number" && edited >= m.time, `editing the note sets edited (${edited})`);
await p.eval("renderMarks(); document.querySelector('#markList button[title=\"Delete bookmark\"]').click(); 0");
const gone = JSON.parse(await p.eval(`localStorage.getItem(${JSON.stringify("epubReader:marksGone:")} + ${JSON.stringify(BID)})`));
ok(gone && typeof gone[m.id] === "number" && gone[m.id] >= m.time, `deleting writes marksGone[${m.id}]`);
ok(await p.eval("marks.length") === 0, "the bookmark is gone");
// a bookmark toggled off with the bookmark button is a delete too
await p.eval("toggleMarkHere(); 0");
const m2 = await p.eval("marks[marks.length - 1].id");
await p.eval("toggleMarkHere(); 0");
const gone2 = JSON.parse(await p.eval(`localStorage.getItem(${JSON.stringify("epubReader:marksGone:")} + ${JSON.stringify(BID)})`));
ok(gone2 && typeof gone2[m2] === "number", "toggling the bookmark off writes marksGone too");

// ---- account button, secure page, signed out
ok(await p.eval("isSecureContext") === true, "http://127.0.0.1 is a secure context");
ok(await p.eval(BTN), "secure page: account button shown");
ok(await syncCalls(p) === 0, "signed out: nothing sent to /__sync");

// ---- the panel
await p.tap(W / 2, 300);   // the bars
await p.eval("document.getElementById('acctBtn').click(); 0").catch(() => {}); await sleep(400);
ok(await p.eval("!!document.getElementById('acct') && document.getElementById('acct').classList.contains('open')"), "the button opens the account panel");
const R = JSON.parse(await p.eval("JSON.stringify((document.getElementById('acct') || document.body).getBoundingClientRect())"));
ok(Math.abs(R.top - 52) <= 1 && R.left === 0 && R.width === W, `phone: a sheet under the toolbar, full width (${[R.top, R.left, R.width]})`);
ok(await p.eval(SHOWN("#acctIn")) && !(await p.eval(SHOWN("#acctOut"))), "signed out: the sign-in forms");
const LOGIN = "t" + Math.floor(Math.random() * 1e9);
const fill = (form, vals) => p.eval(`(() => { const f = document.getElementById('${form}');
  ${Object.entries(vals).map(([k, v]) => `f.elements['${k}'].value = ${JSON.stringify(v)};`).join(" ")}
  f.requestSubmit(); })(); 0`).catch(e => console.log("  (form: " + e.message + ")"));
await p.eval("document.querySelector('#acct [data-tab=create]').click(); 0").catch(() => {});
await fill("acctCreate", { login: LOGIN, password: "secret-pw", password2: "other-pw", code: "x" });
await sleep(300);
ok(/match/i.test(await text("#acctErr")), `passwords that differ: error in the panel ("${await text("#acctErr")}")`);
await fill("acctCreate", { login: LOGIN, password: "secret-pw", password2: "secret-pw", code: "wrong-code-words-00" });
await p.waitFor("/code/i.test(document.getElementById('acctErr').textContent)", 10000).catch(() => {});
ok(/code/i.test(await text("#acctErr")), `wrong library code: error in the panel ("${await text("#acctErr")}")`);
let code = "";
try { code = readFileSync(join(ROOT, ".sync", "code.txt"), "utf8").trim(); } catch {}
await fill("acctCreate", { login: LOGIN, password: "secret-pw", password2: "secret-pw", code });
await p.waitFor("/Last synced/.test(document.getElementById('acctStatus').textContent)", 15000).catch(() => {});
ok(await p.eval(SHOWN("#acctOut")) && (await text("#acctLogin")).includes(LOGIN), `account created: signed in as ${LOGIN}`);
ok(/Last synced/.test(await text("#acctStatus")), `synced ("${await text("#acctStatus")}")`);
ok(await syncCalls(p) > 0, "signed in: synced through /__sync");
// signed in: reopening waits for the sync, then opens at the place
await open(p, BASE);
s = await place(p);
ok(s && s.time === T && s.cfi === C, "signed in, reopened: same place, same time");

// ---- a second device (another browser profile) on the same account
{
  // a bookmark the second device will delete
  await p.eval("toggleMarkHere(); 0");
  const MK = await p.eval("marks[marks.length - 1].id");
  await p.eval("LibrarySync.syncNow()");
  const q = await launch({ width: W, height: H, touch: true });
  await q.goto(`${BASE}/reader/reader.html`);
  const r = JSON.parse(await q.eval(`LibrarySync.login(${JSON.stringify(LOGIN)}, "secret-pw").then(JSON.stringify)`));
  ok(r.ok, "second device: signed in");
  await open(q, BASE);
  const sq = await place(q);
  ok(sq && sq.cfi === C && sq.time === T, "second device: opens at the first one's place (the opening sync)");
  ok(await q.eval(`marks.some(m => m.id === ${JSON.stringify(MK)})`), "second device: has the first one's bookmark");
  await q.eval(`(() => { renderMarks(); const i = marks.findIndex(m => m.id === ${JSON.stringify(MK)});
    document.querySelectorAll('#markList button[title="Delete bookmark"]')[i].click(); })(); 0`);
  ok(await q.eval("LibrarySync.syncNow()") === "ok", "second device: deleted it and synced");
  // the first device syncs, but its upload fails after the merge was written locally: the open book
  // must still drop the bookmark (else a later edit would bring it back)
  await p.eval(`window.__fetch = fetch; window.fetch = (u, o) => o && o.method === "PUT"
    ? Promise.resolve(new Response("{}", { status: 500 })) : __fetch(u, o); 0`);
  await p.eval("setFont(10); 0"); await settle(p);   // something of its own to upload
  ok(await p.eval("LibrarySync.syncNow()") === "offline", "first device: upload fails");
  await sleep(300);
  ok(await p.eval(`!marks.some(m => m.id === ${JSON.stringify(MK)})`), "first device: the deleted bookmark is gone from the open book");
  ok(await p.eval(`renderMarks(), document.querySelectorAll('#markList .mark').length`) === 0, "first device: and from the list");
  await p.eval("window.fetch = __fetch; setFont(-10); 0"); await settle(p);
  for (let i = 0; i < 3; i++) { await q.eval("rendition.next().then(() => 0)"); await settle(q); }
  const moved = await place(q);
  await q.eval("sendPlace(); 0"); await sleep(800);   // as on leaving the page
  q.close();
  // the first device comes back to the front: the place is offered, not jumped to
  const before = await p.eval("anchor");
  await p.eval("document.dispatchEvent(new Event('visibilitychange')); 0");
  await p.waitFor("document.getElementById('toast').classList.contains('show')", 10000).catch(() => {});
  const msg = await text("#toastText");
  ok(/^Continue from \d+% \(another device\)\?$/.test(msg), `back to the front: "${msg}"`);
  ok(await p.eval("anchor") === before, "not moved by itself");
  await p.eval("document.getElementById('toastAct').click(); 0"); await settle(p); await sleep(300); await settle(p);
  s = await place(p);
  ok(await p.eval("anchor") === moved.cfi && s.cfi === moved.cfi && s.time === moved.time,
     `"Continue": at the other device's place, with its time (${s && s.time} / ${moved.time})`);
  // an offer left on the screen while the reader goes on is dropped, and can't be taken any more
  await p.eval(`localStorage.setItem(bookKey, JSON.stringify({ cfi: ${JSON.stringify(C)}, pct: 0.01, title: "x", time: Date.now() }));
    offerPlace(); window.__act = toastAction; 0`);
  ok(await p.eval("document.getElementById('toast').classList.contains('show')"), "another offer shown");
  await p.eval("rendition.next().then(() => 0)"); await settle(p);
  ok(!(await p.eval("document.getElementById('toast').classList.contains('show')")), "a page turn hides it");
  const mine = await place(p);
  await p.eval("__act && __act(); 0"); await settle(p); await sleep(300); await settle(p);
  s = await place(p);
  ok(s.cfi === mine.cfi && s.time === mine.time && await p.eval("anchor") === mine.cfi, "the old offer taken late: nothing happens");
}
await p.eval("document.getElementById('acctBtn').click(); 0").catch(() => {}); await sleep(300);
await p.eval("document.getElementById('acctSignOut').click(); 0").catch(() => {});
await p.waitFor("getComputedStyle(document.getElementById('acctIn')).display !== 'none'", 10000).catch(() => {});
ok(await p.eval(SHOWN("#acctIn")), "signed out again: the sign-in forms");

// ---- plain http with a host name: not a secure context, no account button
await open(p, INSECURE);
ok(await p.eval("isSecureContext") === false, "http://lib.test is not a secure context");
ok(!(await p.eval(BTN)), "insecure page: account button hidden");
ok(await syncCalls(p) === 0, "insecure page: nothing sent to /__sync");

p.close();

// ======== part 2: the catalog
const epubs = dir => readdirSync(dir).flatMap(n => { const f = join(dir, n);
  return statSync(f).isDirectory() ? epubs(f) : n.endsWith(".epub") ? [f] : []; });
const mine = join(ROOT, "Out", BOOK);
const two = [mine, epubs(join(ROOT, "Out")).find(f => f !== mine)];
const LIB = mkdtempSync(join(tmpdir(), "epub-catalog-sync-"));
const NAMES = ["Jane Doe/Jane Doe - A Quiet Harbour.epub", "John Roe/John Roe - Second Tide.epub"];
two.forEach((src, i) => { mkdirSync(join(LIB, NAMES[i].split("/")[0]), { recursive: true }); copyFileSync(src, join(LIB, NAMES[i])); });
const built = spawnSync("python3", [join(ROOT, "make_server.py"), LIB], { encoding: "utf8" });
ok(built.status === 0, "catalog built for the check");
const PORT2 = 9800 + Math.floor(Math.random() * 150);
const srv = spawn("python3", [join(ROOT, "launchers/library_sync.py"), "--serve", LIB, "--port", String(PORT2)], { stdio: "ignore" });
process.on("exit", () => { try { srv.kill(); rmSync(LIB, { recursive: true, force: true }); } catch {} });
await sleep(1500);
const B2 = `http://127.0.0.1:${PORT2}`, CAT = B2 + "/index.html";
const CODE2 = readFileSync(join(LIB, ".sync", "code.txt"), "utf8").trim();
const APP = `(() => { const mm = matchMedia.bind(window);
  window.matchMedia = q => /display-mode/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : mm(q); })()`;
const device = async (app, extra = []) => {
  const d = await launch({ width: W, height: H, touch: true, args: extra });
  if (app) await d.send("Page.addScriptToEvaluateOnNewDocument", { source: APP });
  return d;
};
const first = d => d.eval(`(document.querySelector('#list .title') || {}).textContent || ''`);
const catalog = async (d, url = CAT) => { await d.goto("about:blank"); await d.goto(url); await d.waitFor("document.querySelectorAll('#list .title').length > 0", 20000); };
const calls2 = d => d.eval("performance.getEntriesByType('resource').filter(e => e.name.includes('/__sync')).length");
const login = (d, who) => d.eval(`LibrarySync.login(${JSON.stringify(who)}, "secret-pw").then(r => r.ok)`);
{
  // signed out: as before
  const t = await device(false, ["--host-resolver-rules=MAP lib.test 127.0.0.1"]);
  await catalog(t);
  ok(await t.eval("getComputedStyle(document.getElementById('acctBtn')).display !== 'none'"), "catalog, secure page: account button shown");
  ok(await calls2(t) === 0, "catalog, signed out: nothing sent to /__sync");
  await catalog(t, CAT.replace("127.0.0.1", "lib.test"));
  ok(await t.eval("getComputedStyle(document.getElementById('acctBtn')).display === 'none'"), "catalog, plain http with a host name: account button hidden");
  ok(await calls2(t) === 0, "catalog, insecure page: nothing sent to /__sync");
  t.close();
}
const A = await device(false);
await A.goto(CAT);
const reg = JSON.parse(await A.eval(`LibrarySync.register("cat" + Math.floor(Math.random() * 1e9), "secret-pw", ${JSON.stringify(CODE2)}).then(r => JSON.stringify(Object.assign(r, { login: LibrarySync.account() && LibrarySync.account().login })))`));
ok(reg.ok, "device A: account created");
const LOGIN2 = reg.login;
await catalog(A);
await A.eval("document.getElementById('acctBtn').click(); 0");
await sleep(300);
ok(await A.eval("!document.getElementById('acctPop').hidden"), "catalog: the button opens the panel");
ok(await A.eval(SHOWN("#acctOut")) && (await A.eval("document.getElementById('acctLogin').textContent")) === LOGIN2, "catalog panel: signed in as the account");
await A.eval("document.getElementById('acctBtn').click(); 0");
// A reads the second book in the reader
await A.goto("about:blank");
await A.goto(`${B2}/.index/reader/reader.html#book=${encodeURIComponent("../../" + NAMES[1].split("/").map(encodeURIComponent).join("/"))}`);
await A.waitFor(READY, 30000); await settle(A);
ok(await A.eval("LibrarySync.syncNow()") === "ok", "device A: opened the second book and synced");
{
  // B: a fresh installed app, signed in
  const Bd = await device(true);
  await Bd.goto(CAT);
  ok(await login(Bd, LOGIN2), "device B: signed in");
  await catalog(Bd);
  const want = await Bd.eval(`books.find(b => /Second Tide/.test(b.file)).title`);
  await Bd.waitFor(`document.querySelector('#list .title').textContent === ${JSON.stringify(want)}`, 10000).catch(() => {});
  ok(await Bd.eval("document.getElementById('sort').value") === "opened", "device B (app): sorted by recently opened");
  ok(await first(Bd) === want, `device B (app): the book A opened is first`);
  await Bd.eval(`(() => { const s = document.getElementById('sort'); s.value = 'title'; s.dispatchEvent(new Event('change')); document.getElementById('group').click(); })(); 0`);
  ok(await Bd.eval("+localStorage.getItem('epubIndex:app:time') > 0"), "device B: the app's settings have a time");
  ok(await Bd.eval("LibrarySync.syncNow()") === "ok", "device B: synced its settings");
  Bd.close();
}
{
  // D: another fresh app gets B's settings; a browser tab (A) none of them
  const D = await device(true);
  await D.goto(CAT);
  await login(D, LOGIN2);
  await catalog(D);
  await D.waitFor("document.getElementById('sort').value === 'title'", 10000).catch(() => {});
  ok(await D.eval("document.getElementById('sort').value") === "title", "device D (app): B's sort comes along");
  ok(await D.eval("document.getElementById('group').getAttribute('aria-pressed')") === "true", "device D (app): and B's grouping");
  D.close();
  await catalog(A);
  await sleep(1500);
  ok(await A.eval("document.getElementById('sort').value") === "author" && await A.eval("document.getElementById('group').getAttribute('aria-pressed')") === "false",
     "browser tab: keeps its own settings (the app's don't reach it)");
  // a sync that never answers does not hold the catalog back
  await A.send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const f = fetch.bind(window);
    window.fetch = (u, o) => String(u).includes('/__sync') ? new Promise(() => {}) : f(u, o); })()` });
  const t0 = Date.now();
  await A.goto("about:blank"); await A.goto(CAT);
  await A.waitFor("document.querySelectorAll('#list .title').length > 0", 5000);
  ok(Date.now() - t0 < 4000, `a hanging sync: the catalog is listed without waiting (${Date.now() - t0} ms)`);
  A.close();
}
srv.kill();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
