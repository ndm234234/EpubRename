// Sync, part 1: the reader's local data rules and the account button / panel.
//   node sync.mjs <base: http://127.0.0.1:PORT of `launchers/library_sync.py --serve ROOT`> <book under Out/> [ROOT]
// ROOT (default ".") is the folder that server serves: the library code is read from ROOT/.sync/code.txt.
// - the place's time changes only on deliberate moves (a page turn), not on opening, reopening,
//   resizing or a font change; a book never read before is saved with time 0
// - bookmarks get an id (time|cfi), edits set `edited`, deleting writes a tombstone (marksGone)
// - bookmarks stored out of book order are put in book order on opening and on a sync; Previous
//   bookmark goes to the nearest one
// - prefs keep a "changed at" time
// - the account button: shown on a secure page (http://127.0.0.1), hidden on plain http with a host
//   name (http://lib.test, mapped to 127.0.0.1); signed out the reader sends nothing to /__sync
// - the panel: not an email, a 500 from the library computer and a wrong library code each show
//   their own error in the panel; the right code signs in and syncs
// Part 2: the catalog (index.html made by make_server.py from two books copied under made-up names
// into a temp library, served by its own library_sync.py): the account button; a book opened on one
// device comes first under "Recently opened" on another (installed-app mode stubbed through
// matchMedia); catalog settings of the app and of a browser tab sync separately; signed in from the
// catalog's panel (a 500 and not an email told apart first), coming back to the front syncs
// without a reload; a sync that hangs never holds the catalog back.
// Part 3: two phones and a tablet on one account (a temp library with the book under a made-up name,
// its own server, stopped and started again): A's place, bookmark with note and font reach B; a
// bookmark deleted on B stays deleted; the tablet keeps its own fonts; a newer place pending on A
// survives A's resize and font change; two uploads at once (409) keep both changes; a tab closed at
// once sends its place (/__sync/place) with an account over 64 KB; the server stopped: no error, a
// book opens in < 3 s, and after the restart one sync each brings the place over; back in front, B
// is offered A's place and a tap takes it; a sync that never answers holds the opening < 3 s.
// Ports: the servers take free ones (--port 0), the browsers too (cdp.mjs).
import { readFileSync, mkdtempSync, mkdirSync, copyFileSync, readdirSync, statSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK, ROOT = "."] = process.argv.slice(2);
// A library_sync.py of its own: port 0 = a free one, read from the "Library:" line it prints once
// it listens (so no fixed waits and no port collisions between checks run side by side). On a
// restart the same port is asked for again: the devices' data is kept per address.
const servers = new Set();
process.on("exit", () => { for (const s of servers) try { s.kill("SIGKILL"); } catch {} });
async function startServer(root, port = 0) {
  for (let i = 0; i < 40; i++) {
    const proc = spawn("python3", [join(ROOT, "launchers/library_sync.py"), "--serve", root, "--port", String(port)], { stdio: ["ignore", "pipe", "ignore"] });
    servers.add(proc);
    const got = await new Promise(res => {
      let out = "";
      proc.stdout.on("data", d => { out += d; const m = /Library: http:\/\/127\.0\.0\.1:(\d+)\//.exec(out); if (m) res(+m[1]); });
      proc.on("exit", () => res(0));
    });
    if (got) return { proc, port: got, url: `http://127.0.0.1:${got}` };
    servers.delete(proc);
    await sleep(250);   // the port is not free yet (a restart)
  }
  throw new Error("library_sync.py did not start on port " + port);
}
async function stopServer(s) {
  if (s.proc.exitCode === null && s.proc.signalCode === null) { const gone = new Promise(r => s.proc.once("exit", r)); s.proc.kill(); await gone; }
  servers.delete(s.proc);
}
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
// bookmarks stored out of book order (a sync from the catalog writes them oldest first): the reader
// puts them in book order on opening and when a sync brings them in; Previous goes to the nearest
{
  const saved = await p.eval("localStorage.getItem(bookKey)"), cfis = [];
  for (let i = 0; i < 3; i++) {
    if (i) { await p.eval("rendition.next().then(() => 0)"); await settle(p); await p.eval("rendition.next().then(() => 0)"); await settle(p); }
    await p.eval("toggleMarkHere(); hideToast(); 0");
    cfis.push(await p.eval("here().start.cfi"));
  }
  const MKEY = JSON.stringify("epubReader:marks:" + BID);
  const reverse = `localStorage.setItem(${MKEY}, JSON.stringify(JSON.parse(localStorage.getItem(${MKEY}))
    .sort((a, b) => ${JSON.stringify(cfis)}.indexOf(b.cfi) - ${JSON.stringify(cfis)}.indexOf(a.cfi)))); 0`;
  await p.eval(reverse);
  await open(p, BASE);   // at the third bookmark's page
  ok(JSON.stringify(await p.eval("marks.map(m => m.cfi)")) === JSON.stringify(cfis), "bookmarks stored out of order: opened in book order");
  await p.eval("window.__d = displayAt; window.__to = ''; displayAt = c => { __to = c; }; stepMark(-1); displayAt = __d; 0");
  ok(await p.eval("__to") === cfis[1], "Previous bookmark: the nearest one, not the first in storage");
  await p.eval(reverse);
  await p.eval("reloadMarks(); 0");
  ok(JSON.stringify(await p.eval("marks.map(m => m.cfi)")) === JSON.stringify(cfis), "a sync wrote them out of order: reloaded in book order");
  ok(await p.eval("renderMarks(), document.querySelectorAll('#markList .mark').length") === 3, "the list shows all three");
  // as before this check: no bookmarks, the place it had
  await p.eval(`marks = []; localStorage.removeItem(${MKEY}); localStorage.setItem(bookKey, ${JSON.stringify(saved)}); renderMarks(); 0`);
}

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
const LOGIN = "t" + Math.floor(Math.random() * 1e9) + "@example.com";
ok(await p.eval("[...document.querySelectorAll('#acct [name=login]')].every(i => i.type === 'email' && i.autocomplete === 'email' && i.placeholder === 'Email')"), "the panel asks for an email");
const fill = (form, vals) => p.eval(`(() => { const f = document.getElementById('${form}');
  ${Object.entries(vals).map(([k, v]) => `f.elements['${k}'].value = ${JSON.stringify(v)};`).join(" ")}
  f.requestSubmit(); })(); 0`).catch(e => console.log("  (form: " + e.message + ")"));
await p.eval("document.querySelector('#acct [data-tab=create]').click(); 0").catch(() => {});
await fill("acctCreate", { login: LOGIN, password: "secret-pw", password2: "other-pw", code: "x" });
await sleep(300);
ok(/match/i.test(await text("#acctErr")), `passwords that differ: error in the panel ("${await text("#acctErr")}")`);
await fill("acctCreate", { login: "con", password: "secret-pw", password2: "secret-pw", code: "x" });
await p.waitFor("document.getElementById('acctErr').textContent.includes('valid email')", 10000).catch(() => {});
ok(/valid email/.test(await text("#acctErr")), `not an email (con): the error says so ("${await text("#acctErr")}")`);
await p.eval(`window.__f = fetch; window.fetch = (u, o) => String(u).includes('/__sync/') ? Promise.resolve(new Response("{}", { status: 500 })) : __f(u, o); 0`);
await fill("acctCreate", { login: LOGIN, password: "secret-pw", password2: "secret-pw", code: "x" });
await p.waitFor("/had a problem/.test(document.getElementById('acctErr').textContent)", 10000).catch(() => {});
ok(/had a problem/.test(await text("#acctErr")), `the library computer answers 500: not "can't reach" ("${await text("#acctErr")}")`);
await p.eval("window.fetch = __f; 0");
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
const srv = await startServer(LIB);
process.on("exit", () => { try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
const B2 = srv.url, CAT = B2 + "/index.html";
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
const BRAND_ON = "(e => !!e && e.tagName === 'BUTTON' && getComputedStyle(e).display !== 'none' && e.getAttribute('aria-controls') === 'acctPop')(document.getElementById('acctBrand'))";
const MARK_SHOWN = "(e => !!e && getComputedStyle(e).display !== 'none')(document.getElementById('acctMark'))";
{
  // signed out: as before
  const t = await device(false, ["--host-resolver-rules=MAP lib.test 127.0.0.1"]);
  await catalog(t);
  ok(await t.eval("document.getElementById('acctBtn') === null"), "catalog: no separate account chip");
  ok(await t.eval(BRAND_ON), "catalog, secure page: the logo and library name are the account button");
  ok(await t.eval("document.getElementById('acctLine') === null"), "catalog: no caption line under the library name");
  ok(!(await t.eval(MARK_SHOWN)), "catalog, signed out: no check mark on the logo");
  ok(await t.eval("document.getElementById('acctBrand').getAttribute('aria-label')") === "Account: sign in to sync", "catalog, signed out: the button's label");
  ok(await t.eval(`(r => (e => !!e && !!e.closest('#acctBrand'))(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)))(document.querySelector('.brand h1').getBoundingClientRect())`),
     "catalog: a tap on the library name hits the account button");
  ok(await t.eval("document.querySelector('header').getBoundingClientRect().height") <= 111, `catalog, 390 px: the header is no taller than before (${await t.eval("document.querySelector('header').getBoundingClientRect().height")} px, was 111)`);
  ok(await calls2(t) === 0, "catalog, signed out: nothing sent to /__sync");
  await catalog(t, CAT.replace("127.0.0.1", "lib.test"));
  ok(!(await t.eval(BRAND_ON)) && !(await t.eval(MARK_SHOWN)), "catalog, plain http with a host name: the name is a plain title, no check mark");
  ok(await t.eval(`(r => (e => !!e && !e.closest('button'))(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)))(document.querySelector('.brand h1').getBoundingClientRect())`),
     "catalog, plain http: the library name is not a button");
  ok(await calls2(t) === 0, "catalog, insecure page: nothing sent to /__sync");
  t.close();
}
const A = await device(false);
await A.goto(CAT);
const reg = JSON.parse(await A.eval(`LibrarySync.register("cat" + Math.floor(Math.random() * 1e9) + "@example.com", "secret-pw", ${JSON.stringify(CODE2)}).then(r => JSON.stringify(Object.assign(r, { login: LibrarySync.account() && LibrarySync.account().login })))`));
ok(reg.ok, "device A: account created");
const LOGIN2 = reg.login;
await catalog(A);
await A.waitFor(MARK_SHOWN + " && !document.getElementById('acctMark').classList.contains('wait')", 5000).catch(() => {});
ok(await A.eval(MARK_SHOWN + " && !document.getElementById('acctMark').classList.contains('wait')"), "catalog, signed in and synced: a green check mark on the logo");
ok((await A.eval("document.getElementById('acctBrand').title")).startsWith(LOGIN2 + " · "),
   `catalog, signed in: the email and the sync in the logo's tooltip ("${await A.eval("document.getElementById('acctBrand').title")}")`);
ok(await A.eval("document.getElementById('acctBrand').getAttribute('aria-label')") === "Account: " + LOGIN2, "catalog, signed in: the button's label has the email");
ok(await A.eval("document.getElementById('acctBrand').getAttribute('aria-expanded')") === "false", "catalog: aria-expanded false while closed");
await A.eval("document.getElementById('acctBrand').click(); 0");
await sleep(300);
ok(await A.eval("!document.getElementById('acctPop').hidden"), "catalog: the button opens the panel");
ok(await A.eval("document.getElementById('acctBrand').getAttribute('aria-expanded')") === "true", "catalog: aria-expanded true while open");
ok(await A.eval(`(p => p.left < innerWidth / 2 && p.top >= document.querySelector('.brand').getBoundingClientRect().bottom - 1)(document.getElementById('acctPop').getBoundingClientRect())`),
   "catalog: the panel opens on the left, under the library name");
ok(await A.eval(SHOWN("#acctOut")) && (await A.eval("document.getElementById('acctLogin').textContent")) === LOGIN2, "catalog panel: signed in as the account");
await A.eval("document.getElementById('acctPop').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0");
ok(await A.eval("document.getElementById('acctPop').hidden && document.activeElement === document.getElementById('acctBrand')"), "catalog: Esc closes the panel, focus back on the account button");
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
  // D: another fresh app gets B's settings; so does a browser tab (A)
  const D = await device(true);
  await D.goto(CAT);
  await login(D, LOGIN2);
  await catalog(D);
  await D.waitFor("document.getElementById('sort').value === 'title'", 10000).catch(() => {});
  ok(await D.eval("document.getElementById('sort').value") === "title", "device D (app): B's sort comes along");
  ok(await D.eval("document.getElementById('group').getAttribute('aria-pressed')") === "true", "device D (app): and B's grouping");
  D.close();
  await catalog(A);
  await A.waitFor("document.getElementById('sort').value === 'title'", 10000).catch(() => {});
  ok(await A.eval("document.getElementById('sort').value") === "title", "browser tab on a computer: the sort chosen on the phone's app comes along (one view for all devices)");
  ok(await A.eval("document.getElementById('group').getAttribute('aria-pressed')") === "true",
     "browser tab on a computer: and the grouping chosen on the phone's app");
  // E signs in from the catalog's own panel (no reload): back in front, it syncs and gets news
  const E = await device(false);
  await catalog(E);
  await E.eval(`window.__f = fetch; window.fetch = (u, o) => String(u).includes('/__sync/') ? Promise.resolve(new Response("{}", { status: 500 })) : __f(u, o);
    (() => { document.getElementById('acctBrand').click(); const f = document.getElementById('acctSignIn');
    f.elements.login.value = "con"; f.elements.password.value = "x"; f.requestSubmit(); })(); 0`);
  await E.waitFor("/had a problem/.test(document.getElementById('acctErr').textContent)", 10000).catch(() => {});
  ok(/had a problem/.test(await E.eval("document.getElementById('acctErr').textContent")), "catalog: a 500 from the library computer is told apart from \"can't reach\"");
  await E.eval("window.fetch = __f; 0");
  await E.eval(`(() => { const f = document.getElementById('acctSignIn'); f.elements.login.value = "con"; f.elements.password.value = "x"; f.requestSubmit(); })(); 0`);
  await E.waitFor("document.getElementById('acctErr').textContent.includes('valid email')", 10000).catch(() => {});
  ok(/valid email/.test(await E.eval("document.getElementById('acctErr').textContent")), "catalog: not an email (con): the error says so");
  ok(await E.eval("[...document.querySelectorAll('#acctPop [name=login]')].every(i => i.type === 'email' && i.autocomplete === 'email' && i.placeholder === 'Email')"), "catalog: the panel asks for an email");
  await E.eval(`(() => { const f = document.getElementById('acctSignIn');
    f.elements.login.value = ${JSON.stringify(LOGIN2)}; f.elements.password.value = "secret-pw"; f.requestSubmit(); })(); 0`);
  await E.waitFor("/Last synced/.test(document.getElementById('acctStatus').textContent)", 10000).catch(() => {});
  ok(await E.eval("LibrarySync.signedIn()"), "device E: signed in from the catalog panel");
  ok(await E.eval(MARK_SHOWN), "device E: the check mark on the logo after signing in from the panel");
  await A.eval(`(() => { const o = JSON.parse(localStorage.getItem('epubLibrary:opened') || '{}');
    o['made-up/later.epub'] = Date.now(); localStorage.setItem('epubLibrary:opened', JSON.stringify(o)); })(); 0`);
  ok(await A.eval("LibrarySync.syncNow()") === "ok", "device A: something new synced");
  await E.eval("document.dispatchEvent(new Event('visibilitychange')); 0");
  await E.waitFor("'made-up/later.epub' in JSON.parse(localStorage.getItem('epubLibrary:opened') || '{}')", 5000).catch(() => {});
  ok(await E.eval("'made-up/later.epub' in JSON.parse(localStorage.getItem('epubLibrary:opened') || '{}')"),
     "device E, back in front (no reload since signing in): synced and has it");
  E.close();
  // a sync that never answers does not hold the catalog back
  await A.send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const f = fetch.bind(window);
    window.fetch = (u, o) => String(u).includes('/__sync') ? new Promise(() => {}) : f(u, o); })()` });
  const t0 = Date.now();
  await A.goto("about:blank"); await A.goto(CAT);
  await A.waitFor("document.querySelectorAll('#list .title').length > 0", 5000);
  ok(Date.now() - t0 < 4000, `a hanging sync: the catalog is listed without waiting (${Date.now() - t0} ms)`);
  A.close();
}
await stopServer(srv);

// ======== part 3
// Two phones (A, B) and a tablet (T), each its own browser profile, on one account of a library of
// its own (the book copied in under a made-up name; this server is stopped and started again).
{
  const LIB3 = mkdtempSync(join(tmpdir(), "epub-reader-sync-"));
  process.on("exit", () => { try { rmSync(LIB3, { recursive: true, force: true }); } catch {} });
  symlinkSync(resolve(ROOT, "reader"), join(LIB3, "reader"));
  const FILE3 = "Out/Jane Doe/Jane Doe - A Quiet Harbour.epub";
  mkdirSync(join(LIB3, "Out/Jane Doe"), { recursive: true });
  copyFileSync(join(ROOT, "Out", BOOK), join(LIB3, FILE3));
  let S = await startServer(LIB3);
  const PORT3 = S.port, B3 = S.url;
  const CODE3 = readFileSync(join(LIB3, ".sync", "code.txt"), "utf8").trim();
  const LOGIN3 = "e2e" + Math.floor(Math.random() * 1e9) + "@example.com";
  const dataFile = () => join(LIB3, ".sync", "data", LOGIN3 + ".json");
  const serverDoc = () => { try { return JSON.parse(readFileSync(dataFile(), "utf8")).doc || {}; } catch { return {}; } };
  const URL3 = `${B3}/reader/reader.html#book=${encodeURIComponent("../" + FILE3)}`;
  const errors = new Map();   // uncaught page errors per device
  const dev = async (name, w = W, h = H) => {
    const d = await launch({ width: w, height: h, touch: true });
    errors.set(d, 0);
    d.on(m => { if (m.method === "Runtime.exceptionThrown") errors.set(d, errors.get(d) + 1); });
    d.name = name;
    return d;
  };
  const helpers = d => d.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches"));
  const open3 = async d => { await d.goto("about:blank"); await d.goto(URL3); await d.waitFor(READY, 30000); await settle(d); await helpers(d); };
  const st = d => d.eval("JSON.stringify(__state())").then(JSON.parse);
  const turn = async (d, n) => { for (let i = 0; i < n; i++) { await d.eval("rendition.next().then(() => 0)"); await settle(d); } };
  const sync = d => d.eval("LibrarySync.syncNow()");
  const toast = d => d.eval("document.getElementById('toast').classList.contains('show') ? document.getElementById('toastText').textContent : ''");
  // what a reader would see as trouble: the start screen (a book not open, an error) or a message
  const trouble = d => d.eval(`[document.getElementById('start').classList.contains('hidden') ? '' : 'start screen: ' + document.getElementById('start').textContent.trim().replace(/\\s+/g, ' ').slice(0, 80),
    document.getElementById('toast').classList.contains('show') ? 'message: ' + document.getElementById('toastText').textContent : ''].filter(Boolean).join('; ')`);
  const visible = (d, hidden) => d.eval(`(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => ${hidden} });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => ${hidden} ? 'hidden' : 'visible' });
    document.dispatchEvent(new Event('visibilitychange')); })(); 0`);
  const markIds = d => d.eval("JSON.stringify(marks.map(m => m.id))").then(JSON.parse);

  // ---- accounts: A creates one (a large one: 300 bookmarks with notes in another book), B signs in
  const A = await dev("A");
  await A.goto(`${B3}/reader/reader.html`);
  await A.eval(`(() => { const words = "harbour lantern quiet tide gull rope sail morning salt pier keeper fog bell shore".split(" ");
    const seed = [];
    for (let i = 0; i < 300; i++) {
      const note = Array.from({ length: 40 }, (_, k) => words[(i * 7 + k * 3) % words.length]).join(" ");
      const cfi = "epubcfi(/6/" + (2 + 2 * (i % 40)) + "!/4/2/1:" + i + ")", time = 1700000000000 + i * 1000;
      seed.push({ id: time + "|" + cfi, cfi, text: "Seed page " + i, name: "", note: "Note " + i + ": " + note, time });
    }
    localStorage.setItem("epubReader:marks:seed:jane-doe-a-long-voyage", JSON.stringify(seed)); })(); 0`);
  const reg3 = JSON.parse(await A.eval(`LibrarySync.register(${JSON.stringify(LOGIN3)}, "secret-pw", ${JSON.stringify(CODE3)}).then(JSON.stringify)`));
  ok(reg3.ok, `part 3, A: account ${LOGIN3} created`);

  // ---- A reads 10 pages, adds a bookmark with a note, a bigger font; B opens the book there
  await open3(A);
  await turn(A, 10);
  await A.eval("toggleMarkHere(); 0");
  const MA = await A.eval("marks[marks.length - 1].id");
  await A.eval(`(() => { renderMarks(); editNote(marks.find(x => x.id === ${JSON.stringify(MA)}));
    const a = document.querySelector('#markList textarea'); a.value = 'remember the lighthouse'; a.dispatchEvent(new Event('input')); a.blur(); })(); 0`);
  await A.eval("toggleMarks(false); setFont(10); 0"); await settle(A);
  const BOOKID = await id(A);
  const a1 = await st(A), pa1 = await place(A);
  ok(await sync(A) === "ok", `A: 10 pages on (at "${a1.first}"), a bookmark with a note, font ${await A.eval("prefs.font")}%: synced`);
  const big = statSync(dataFile()).size;
  ok(big > 64 * 1024, `the account's data is over 64 KB (${big} bytes): too big for a keepalive upload`);

  const B = await dev("B");
  await B.goto(`${B3}/reader/reader.html`);
  ok(JSON.parse(await B.eval(`LibrarySync.login(${JSON.stringify(LOGIN3)}, "secret-pw").then(JSON.stringify)`)).ok, "B: signed in");
  await open3(B);
  const b1 = await st(B), pb1 = await place(B);
  ok(pb1.cfi === pa1.cfi && b1.first === a1.first, `B opens the book at A's place: "${b1.first}" (A: "${a1.first}")`);
  ok(await B.eval(`(marks.find(m => m.id === ${JSON.stringify(MA)}) || {}).note`) === "remember the lighthouse", "B: A's bookmark with its note");
  ok(await B.eval("prefs.font") === 110 && await B.eval("LibrarySync.deviceKind()") === "phone", `B (phone): A's font (${await B.eval("prefs.font")}%)`);

  // ---- B deletes the bookmark; A, back in front, loses it and does not bring it back
  await B.eval(`(() => { renderMarks(); const i = marks.findIndex(m => m.id === ${JSON.stringify(MA)});
    document.querySelectorAll('#markList button[title="Delete bookmark"]')[i].click(); toggleMarks(false); })(); 0`);
  ok(await sync(B) === "ok", "B: deleted the bookmark and synced");
  await visible(A, false);
  await A.waitFor(`!marks.some(m => m.id === ${JSON.stringify(MA)})`, 10000).catch(() => {});
  ok(!(await markIds(A)).includes(MA), "A back in front (visibilitychange): the bookmark is gone");
  await A.eval("setFont(10); 0"); await settle(A);   // something of A's own to upload
  ok(await sync(A) === "ok" && await sync(A) === "ok", "A: synced again (twice)");
  ok(!(await markIds(A)).includes(MA), "A: still gone after its own syncs");
  const g = (serverDoc().marks || {})[BOOKID] || {};
  ok(g[MA] && typeof g[MA].deleted === "number", "the library keeps it deleted (a tombstone)");
  ok(await sync(B) === "ok" && !(await markIds(B)).includes(MA), "B: synced, not resurrected");
  await A.eval("setFont(-10); 0"); await settle(A);
  ok(await sync(A) === "ok", "A: back to 110%, synced");

  // ---- a tablet keeps its own fonts
  const T = await dev("T", 820, 1180);
  await T.goto(`${B3}/reader/reader.html`);
  await T.eval(`LibrarySync.login(${JSON.stringify(LOGIN3)}, "secret-pw").then(r => r.ok)`);
  await open3(T);
  ok(await T.eval("LibrarySync.deviceKind()") === "tablet" && await T.eval("prefs.font") === 100,
     `tablet (820x1180): fonts not taken from the phones (${await T.eval("prefs.font")}%)`);
  ok((await place(T)).cfi === pa1.cfi, "tablet: opens at the phones' place");
  await T.eval("setFont(-10); 0"); await settle(T);
  ok(await sync(T) === "ok", "tablet: 90%, synced");
  await sync(A); await sync(B);
  ok(await A.eval("prefs.font") === 110 && await B.eval("prefs.font") === 110, "phones keep 110% (the tablet's font doesn't reach them)");

  // ---- a newer place from B waits on A (not accepted) while A resizes and changes the font: A's
  // old place must not become the newest
  await turn(B, 5);
  const pb2 = await place(B);
  ok(await sync(B) === "ok", "B: 5 pages on, synced");
  await visible(A, false);
  await A.waitFor("document.getElementById('toast').classList.contains('show')", 10000).catch(() => {});
  ok(/^Continue from \d+% \(another device\)\?$/.test(await toast(A)), `A: offered B's place ("${await toast(A)}"), not taken`);
  await A.send("Emulation.setDeviceMetricsOverride", { width: W, height: H - 56, deviceScaleFactor: 2, mobile: true });
  await sleep(700); await settle(A);
  await A.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 2, mobile: true });
  await sleep(700); await settle(A);
  await A.eval("setFont(10); 0"); await settle(A);
  await A.eval("setFont(-10); 0"); await settle(A);
  let pa = await place(A);
  ok(pa.cfi === pb2.cfi && pa.time === pb2.time, `A resized and changed the font: B's place still pending in storage (time ${pa.time} / ${pb2.time})`);
  ok(await A.eval("anchor") === pa1.cfi, "A: still shows its own page");
  ok(await sync(A) === "ok", "A: synced");
  await A.eval("sendPlace(); 0"); await sleep(500);   // as on leaving: sends what is stored
  const sp = (serverDoc().places || {})[BOOKID] || {};
  ok(sp.cfi === pb2.cfi && sp.time === pb2.time, "the library still has B's place as the newest");
  await open3(B);
  ok((await place(B)).cfi === pb2.cfi && await B.eval("anchor") === pb2.cfi, "B reopens: at B's place");
  await open3(T);
  ok(await T.eval("anchor") === pb2.cfi, "the tablet reopens: at B's place too");

  // ---- A and B write at the same moment (different things): the later one gets 409, merges, and
  // both changes are kept
  await B.eval(`(() => { const f = window.__f0 = window.__f0 || fetch.bind(window);
    window.__puts = []; window.__got = false; window.__go = false;
    window.fetch = async (u, o) => {
      const m = (o && o.method) || "GET", data = String(u).includes("/__sync/data");
      if (data && m === "GET") { const r = await f(u, o); window.__got = true; return r; }
      if (data && m === "PUT") { while (!window.__go) await new Promise(r => setTimeout(r, 20));
        const r = await f(u, o); window.__puts.push(r.status); return r; }
      return f(u, o); }; })(); 0`);
  await B.eval("toggleMarkHere(); window.__res = LibrarySync.syncNow(); 0");
  const MB = await B.eval("marks[marks.length - 1].id");
  await B.waitFor("window.__got", 5000).catch(() => {});
  await A.eval("toggleMarkHere(); 0");
  const MX = await A.eval("marks[marks.length - 1].id");
  ok(await sync(A) === "ok", "A: added a bookmark and synced while B's upload was on its way");
  await B.eval("window.__go = true; 0");
  ok(await B.eval("window.__res") === "ok", "B: its upload finished too");
  const puts = await B.eval("window.__puts");
  ok(puts[0] === 409 && puts[puts.length - 1] === 200, `B: the library answered 409, B merged and sent again (${puts})`);
  await B.eval("window.fetch = window.__f0; 0");
  await sync(A);
  const both = (serverDoc().marks || {})[BOOKID] || {};
  ok(both[MB] && !both[MB].deleted && both[MX] && !both[MX].deleted, "the library has both bookmarks");
  ok((await markIds(A)).includes(MB) && (await markIds(B)).includes(MX), "A has B's bookmark, B has A's");
  // and both at once, without holding either
  await A.eval("toggleMarkHere(); 0"); await B.eval("toggleMarkHere(); 0");   // both off again
  const [ra, rb] = await Promise.all([sync(A), sync(B)]);
  await Promise.all([sync(A), sync(B)]);
  const off = (serverDoc().marks || {})[BOOKID] || {};
  ok(ra === "ok" && rb === "ok" && off[MB] && off[MB].deleted && off[MX] && off[MX].deleted && !(await markIds(A)).length && !(await markIds(B)).length,
     `both deleted at once, synced at once (${ra}, ${rb}): both deletes kept on both`);

  // ---- A turns pages and is hidden at once (the tab closed): the place goes by /__sync/place
  await open3(A);
  ok(await A.eval("anchor") === pb2.cfi, "A reopens: at the newest place (B's)");
  // uploads of the whole account fail from here on (as a keepalive upload of > 64 KB would)
  await A.eval(`window.__f0 = fetch.bind(window); window.fetch = (u, o) => o && o.method === "PUT"
    ? Promise.reject(new TypeError("too large for keepalive")) : __f0(u, o); 0`);
  await turn(A, 3);
  const pa3 = await place(A), a3 = await st(A);
  await A.goto("about:blank");   // visibilitychange (hidden) + pagehide: the place only, keepalive
  let sp3 = {};
  for (let i = 0; i < 25 && sp3.cfi !== pa3.cfi; i++) { await sleep(200); sp3 = (serverDoc().places || {})[BOOKID] || {}; }
  ok(sp3.cfi === pa3.cfi && sp3.time === pa3.time, "A turned 3 pages and was closed at once: the library has the new place");
  await open3(B);
  const b3 = await st(B);
  ok(await B.eval("anchor") === pa3.cfi && b3.first === a3.first, `B opens at it: "${b3.first}" (A: "${a3.first}")`);

  // ---- the library computer is off
  await open3(A);
  await stopServer(S);
  const e0 = errors.get(A);
  await turn(A, 3);
  ok(await A.eval("LibrarySync.syncNow()") === "offline", "server stopped: A's sync is offline");
  ok(await trouble(A) === "" && errors.get(A) === e0, `server stopped, A turned 3 pages: nothing shown (${await trouble(A) || "-"})`);
  const pa4 = await place(A);
  // opening a book (picked by hand: nothing can be loaded from the computer now)
  await A.eval("window.__old = rendition; 0");
  const { root } = await A.send("DOM.getDocument");
  const { nodeId } = await A.send("DOM.querySelector", { nodeId: root.nodeId, selector: "#file" });
  const t0 = Date.now();
  await A.send("DOM.setFileInputFiles", { nodeId, files: [join(LIB3, FILE3)] });
  await A.waitFor("rendition !== __old && rendition.location && rendition.location.start && placeShown", 15000);
  const took = Date.now() - t0;
  await settle(A); await helpers(A);
  ok(took < 3000, `server stopped: opening a book took ${took} ms (< 3 s)`);
  ok(await trouble(A) === "" && errors.get(A) === e0, `and nothing is shown (${await trouble(A) || "-"})`);
  ok(await A.eval("anchor") === pa4.cfi, "at A's place");
  const a4 = await st(A);
  // the computer is back: A's next sync sends it, B's next sync has it
  S = await startServer(LIB3, PORT3);
  ok(S.port === PORT3, "server started again on the same port");
  const last = await A.eval("LibrarySync.lastSync()");
  await A.eval("dispatchEvent(new Event('online')); 0");
  await A.waitFor(`LibrarySync.lastSync() > ${last}`, 10000).catch(() => {});
  ok(await A.eval(`LibrarySync.lastSync() > ${last}`), "A: synced on 'online'");
  ok(await sync(B) === "ok" && (await place(B)).cfi === pa4.cfi, "B: one sync, and it has A's place");
  await B.waitFor("document.getElementById('toast').classList.contains('show')", 5000).catch(() => {});
  ok(/^Continue from \d+% \(another device\)\?$/.test(await toast(B)), `B (book open): offered ("${await toast(B)}")`);
  await B.eval("hideToast(); 0");

  // ---- B in front with the book open, A reads on: back to B, the place is offered; a tap takes it
  await turn(A, 2);
  const a5 = await st(A);
  ok(await sync(A) === "ok", `A: 2 pages on ("${a5.first}"), synced`);
  await visible(B, true);
  await visible(B, false);
  await B.waitFor("document.getElementById('toast').classList.contains('show')", 10000).catch(() => {});
  ok(/^Continue from \d+% \(another device\)\?$/.test(await toast(B)), `B back in front: "${await toast(B)}"`);
  const r = JSON.parse(await B.eval("JSON.stringify(document.getElementById('toastAct').getBoundingClientRect())"));
  await B.tap(r.left + r.width / 2, r.top + r.height / 2);
  await settle(B); await sleep(300); await settle(B);
  const b5 = await st(B);
  ok(await B.eval("anchor") === a5.anchor && b5.first === a5.first, `tapped "Continue": B is at A's page ("${b5.first}")`);

  // ---- a library that never answers holds the opening back at most ~2 s
  await T.send("Fetch.enable", { patterns: [{ urlPattern: "*/__sync/*" }] });   // sync requests never answered
  await T.goto("about:blank"); await T.goto(URL3);
  await T.waitFor(READY + " && placeShown", 15000);
  const took2 = Math.round(await T.eval("performance.now()"));   // since the page began loading
  ok(took2 < 3000, `sync never answering: the tablet opened the book in ${took2} ms (< 3 s)`);
  ok(await trouble(T) === "", `and nothing is shown (${await trouble(T) || "-"})`);
  ok([...errors.values()].every(n => n === 0), `no page errors (${[...errors].map(([d, n]) => d.name + ":" + n)})`);
  A.close(); B.close(); T.close();
  await stopServer(S);
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
