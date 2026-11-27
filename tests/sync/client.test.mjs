import { test, mock, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const load = (f) => vm.runInThisContext(fs.readFileSync(path.join(here, '..', '..', 'reader', f), 'utf8'), { filename: f });
load('sync-merge.js');
const M = globalThis.SyncMerge;

const BOOK = 'Jane Doe - A Quiet Harbour';
const WEIRD = 'urn:isbn:978-0-00-000000-0 / "Tom: Sawyer"';
const GONE = Date.now() - 1000;
const tick = () => new Promise((r) => setImmediate(r));
const flush = async () => { for (let i = 0; i < 10; i++) await tick(); };

function fakeStorage() {
  const m = new Map();
  return {
    m,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    key: (i) => [...m.keys()][i] ?? null,
    get length() { return m.size; },
  };
}

// In-memory server with the real API's semantics (versions, 409, tokens).
function fakeServer() {
  const s = { version: 0, doc: M.emptyDoc(), calls: [], puts: 0, beforePut: null, down: false, hang: false, token: 'tok1', accounts: { 'ann@example.com': 'pw' }, code: 'river-paper-cold-moon-47' };
  const res = (status, body) => ({ status, ok: status < 400, json: async () => body });
  s.fetch = (url, opts = {}) => {
    s.calls.push({ url, opts });
    if (s.hang) return new Promise((_, rej) => opts.signal && opts.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' }))));
    if (s.down) return Promise.reject(new TypeError('Failed to fetch'));
    const body = opts.body ? JSON.parse(opts.body) : null;
    const auth = (opts.headers || {}).Authorization;
    const authed = auth === 'Bearer ' + s.token;
    if (s.broken && /register|login/.test(url)) return Promise.resolve(res(s.broken, {}));
    if (url === '/__sync/register') {
      body.login = body.login.trim().toLowerCase();   // as the servers do
      if (!/^[a-z0-9._+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(body.login)) return Promise.resolve(res(400, {}));
      if (body.code !== s.code) return Promise.resolve(res(403, {}));
      if (s.accounts[body.login]) return Promise.resolve(res(409, {}));
      s.accounts[body.login] = body.password;
      return Promise.resolve(res(200, { token: s.token }));
    }
    if (url === '/__sync/login') {
      if (body.login === 'slow@example.com') return Promise.resolve(res(429, { wait: 60 }));
      return Promise.resolve(s.accounts[body.login] === body.password ? res(200, { token: s.token }) : res(401, {}));
    }
    if (url === '/__sync/logout') return Promise.resolve(res(204, null));
    if (!authed) return Promise.resolve(res(401, {}));
    if (url === '/__sync/data' && (opts.method || 'GET') === 'GET') return Promise.resolve(res(200, { version: s.version, doc: JSON.parse(JSON.stringify(s.doc)) }));
    if (url === '/__sync/data') {
      s.puts++;
      if (s.beforePut) { const f = s.beforePut; s.beforePut = null; f(s); }
      if (body.version !== s.version) return Promise.resolve(res(409, { version: s.version, doc: s.doc }));
      s.version++; s.doc = body.doc;
      return Promise.resolve(res(200, { version: s.version }));
    }
    if (url === '/__sync/place') { s.place = body; return Promise.resolve(res(204, null)); }
    return Promise.resolve(res(404, {}));
  };
  return s;
}

let ls, srv, media;
function env({ kind = 'computer', signedIn = true } = {}) {
  ls = fakeStorage();
  srv = fakeServer();
  media = { '(pointer: coarse)': kind !== 'computer', '(hover: none)': kind !== 'computer' };
  globalThis.localStorage = ls;
  globalThis.fetch = srv.fetch;
  globalThis.isSecureContext = true;
  globalThis.matchMedia = (q) => ({ matches: !!media[q] });
  globalThis.screen = { width: kind === 'phone' ? 390 : 800, height: kind === 'phone' ? 800 : 1200 };
  delete globalThis.LibrarySync;
  load('sync.js');
  if (signedIn) ls.setItem('librarySync:account', JSON.stringify({ login: 'ann@example.com', token: 'tok1' }));
  return globalThis.LibrarySync;
}
const put = (k, v) => ls.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
const get = (k) => JSON.parse(ls.getItem(k));
const mark = (t, extra) => Object.assign({ cfi: 'epubcfi(/6/' + t + ')', text: 't' + t, name: '', note: '', time: t }, extra);

function seedLocal() {
  put('epubReader:book:' + BOOK, { cfi: 'epubcfi(/6/4)', pct: 0.2, title: 'A Quiet Harbour', time: 1000 });
  put('epubReader:book:' + WEIRD, { cfi: 'epubcfi(/6/8)', pct: 0.5, title: 'x', time: 2000 });
  put('epubReader:marks:' + BOOK, [mark(10), mark(20, { note: 'n', edited: 30 })]);
  put('epubReader:marksGone:' + BOOK, { '5|epubcfi(/6/5)': GONE });
  put('epubLibrary:opened', { 'a.epub': 5, 'b.epub': 7 });
  put('epubReader:prefs', { theme: 'dark', size: 18 });
  put('epubReader:prefsTime', '500');
  put('epubIndex', { sort: 'title' });
  put('epubIndex:time', '600');
  put('epubReader:loc:' + BOOK, 'cached-positions');
  put('epubIndex:place', { href: 'x.epub', offset: 5, q: 'PLACE-MARK', time: 1 });   // where the catalog was: stays on the device
  put('epubIndex:app:place', { href: 'y.epub', offset: 6, q: 'PLACE-MARK', time: 2 });
}

beforeEach(() => mock.timers.reset());

test('available and deviceKind', () => {
  let S = env();
  assert.equal(S.available(), true);
  assert.equal(S.deviceKind(), 'computer');
  assert.equal(env({ kind: 'phone' }).deviceKind(), 'phone');
  S = env({ kind: 'tablet' });
  assert.equal(S.deviceKind(), 'tablet');
  globalThis.isSecureContext = false;
  assert.equal(S.available(), false);
});

test('collect then apply leaves localStorage byte-identical; only synced keys uploaded', () => {
  const S = env();
  seedLocal();
  const before = JSON.stringify([...ls.m]);
  const doc = S.collectLocal();
  assert.equal(doc.v, 1);
  assert.equal(doc.places[BOOK].pct, 0.2);
  assert.equal(doc.places[WEIRD].time, 2000);
  assert.equal(Object.keys(doc.marks[BOOK]).length, 3);
  assert.deepEqual(doc.marks[BOOK]['5|epubcfi(/6/5)'], { deleted: GONE });
  assert.equal(doc.marks[BOOK]['20|epubcfi(/6/20)'].note, 'n');
  assert.deepEqual(doc.opened, { 'a.epub': 5, 'b.epub': 7 });
  assert.deepEqual(doc.prefs.computer.reader, { theme: 'dark', size: 18 });
  assert.equal(doc.prefs.computer.readerTime, 500);
  assert.deepEqual(doc.prefs.computer.catalogTab, { sort: 'title' });
  assert.equal(doc.prefs.computer.catalogTabTime, 600);
  assert.equal(doc.prefs.computer.catalogApp, undefined);
  assert.ok(!JSON.stringify(doc).includes('cached-positions'));
  assert.ok(!JSON.stringify(doc).includes('PLACE-MARK'), 'the catalog place is not synced');
  const rep = S.applyLocal(doc);
  assert.equal(JSON.stringify([...ls.m]), before);
  assert.deepEqual(rep, { places: [], marks: [], prefs: false, opened: false, shelves: false });
});

test('account: register / login / logout and error mapping', async () => {
  const S = env({ signedIn: false });
  assert.equal(S.account(), null);
  assert.deepEqual(await S.register('ann2@example.com', 'pw', 'wrong'), { ok: false, error: 'wrong-code' });
  assert.deepEqual(await S.register('not-an-email', 'pw', srv.code), { ok: false, error: 'bad-login' });
  assert.deepEqual(await S.register('ann@example.com', 'pw', srv.code), { ok: false, error: 'taken' });
  assert.deepEqual(await S.login('ann@example.com', 'nope'), { ok: false, error: 'wrong-password' });
  const w = await S.login('slow@example.com', 'x');
  assert.equal(w.error, 'wait');
  assert.equal(w.wait, 60);
  assert.deepEqual(await S.register(' Bob@Example.COM ', 'pw', srv.code), { ok: true });
  assert.deepEqual(S.account(), { login: 'bob@example.com' });   // stored as the servers store it
  assert.equal(get('librarySync:account').token, 'tok1');
  assert.deepEqual(await S.logout(), { ok: true });
  assert.equal(S.account(), null);
  assert.deepEqual(await S.login('ann@example.com', 'pw'), { ok: true });
  assert.equal(S.account().login, 'ann@example.com');
  srv.down = true;
  assert.deepEqual(await S.login('ann@example.com', 'pw'), { ok: false, error: 'offline' });
  // logout while offline still signs out locally
  assert.deepEqual(await S.logout(), { ok: true });
  assert.equal(S.account(), null);
});

test('sync merges both ways', async () => {
  const S = env();
  seedLocal();
  srv.version = 3;
  srv.doc = M.merge(M.emptyDoc(), { places: { [BOOK]: { cfi: 'epubcfi(/6/40)', pct: 0.7, title: 'A Quiet Harbour', time: 5000 } }, opened: { 'c.epub': 9 }, marks: { [BOOK]: { 'x1': mark(15, { id: 'x1' }) } } });
  assert.equal(await S.syncNow(), 'ok');
  assert.equal(get('epubReader:book:' + BOOK).pct, 0.7);
  assert.equal(get('epubLibrary:opened')['c.epub'], 9);
  // (no setMarkOrder: the device's own marks keep their order, the new one follows)
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [10, 20, 15]);
  assert.equal(srv.version, 4);
  assert.equal(srv.doc.places[WEIRD].time, 2000);
  assert.equal(srv.doc.opened['a.epub'], 5);
  assert.equal(srv.doc.marks[BOOK]['5|epubcfi(/6/5)'].deleted, GONE);
  assert.ok(Number(ls.getItem('librarySync:lastSync')) > 0);
  assert.equal(ls.getItem('librarySync:dirty'), null);
  assert.equal(srv.calls[0].opts.headers.Authorization, 'Bearer tok1');
});

test('tombstones: server delete removes mark locally and moves to marksGone; old tombstones pruned', async () => {
  const S = env();
  put('epubReader:marks:' + BOOK, [mark(10), mark(20)]);
  srv.doc = M.merge(M.emptyDoc(), { marks: { [BOOK]: {
    '10|epubcfi(/6/10)': { deleted: GONE },
    'old': { deleted: Date.now() - 400 * 24 * 3600 * 1000 },
  } } });
  assert.equal(await S.syncNow(), 'ok');
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [20]);
  assert.deepEqual(get('epubReader:marksGone:' + BOOK), { '10|epubcfi(/6/10)': GONE });
  assert.equal(srv.doc.marks[BOOK].old, undefined);
});

test('409 then success: result has both devices marks', async () => {
  const S = env();
  put('epubReader:marks:' + BOOK, [mark(10)]);
  srv.beforePut = (s) => { s.version++; s.doc = M.merge(s.doc, { marks: { [BOOK]: { y: mark(20, { id: 'y' }) } } }); };
  assert.equal(await S.syncNow(), 'ok');
  assert.equal(srv.puts, 2);
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [10, 20]);
  assert.equal(Object.keys(srv.doc.marks[BOOK]).length, 2);
});

test('persistent 409 gives up after 3 puts without throwing', async () => {
  const S = env();
  put('epubLibrary:opened', { 'a.epub': 1 });
  const orig = srv.fetch;
  globalThis.fetch = (u, o) => { if (u === '/__sync/data' && o.method === 'PUT') { srv.version++; srv.doc = M.merge(srv.doc, { opened: { ['z' + srv.version]: 1 } }); } return orig(u, o); };
  assert.equal(await S.syncNow(), 'offline');
  assert.equal(srv.puts, 3);
});

test('PUT skipped when nothing differs; second sync does not bump version', async () => {
  const S = env();
  seedLocal();
  assert.equal(await S.syncNow(), 'ok');
  const v = srv.version, p = srv.puts;
  assert.equal(await S.syncNow(), 'ok');
  assert.equal(srv.version, v);
  assert.equal(srv.puts, p);
});

test('offline: no throw, dirty kept; timeout also offline', async () => {
  const S = env();
  put('librarySync:dirty', '1');
  srv.down = true;
  assert.equal(await S.syncNow(), 'offline');
  assert.equal(ls.getItem('librarySync:dirty'), '1');
  assert.equal(ls.getItem('librarySync:lastSync'), null);
  srv.down = false; srv.hang = true;
  const t0 = Date.now();
  assert.equal(await S.syncNow({ timeoutMs: 60 }), 'offline');
  assert.ok(Date.now() - t0 < 1000);
  assert.equal(ls.getItem('librarySync:dirty'), '1');
});

test('timeout holds even if fetch ignores abort; late result changes nothing', async () => {
  const S = env();
  let release;
  globalThis.fetch = () => new Promise((r) => { release = () => r({ status: 200, json: async () => ({ version: 0, doc: M.merge(M.emptyDoc(), { opened: { 'late.epub': 1 } }) }) }); });
  assert.equal(await S.syncNow({ timeoutMs: 30 }), 'offline');
  release(); await flush();
  assert.equal(ls.getItem('epubLibrary:opened'), null);
});

test('signed out: no fetch at all', async () => {
  const S = env({ signedIn: false });
  assert.equal(await S.syncNow(), 'signed-out');
  S.sendPlaceNow(BOOK, { cfi: 'c' });
  assert.equal(srv.calls.length, 0);
});

test('401 drops the token, keeps login and local data', async () => {
  const S = env();
  seedLocal();
  srv.token = 'other';
  const before = ls.getItem('epubReader:book:' + BOOK);
  assert.equal(await S.syncNow(), 'signed-out');
  assert.deepEqual(get('librarySync:account'), { login: 'ann@example.com' });
  assert.deepEqual(S.account().login, 'ann@example.com');
  assert.equal(ls.getItem('epubReader:book:' + BOOK), before);
  const n = srv.calls.length;
  assert.equal(await S.syncNow(), 'signed-out');
  assert.equal(srv.calls.length, n);
});

test('onRemoteChange reports books whose place got newer, prefs and opened', async () => {
  const S = env();
  seedLocal();
  srv.doc = M.merge(M.emptyDoc(), {
    places: { [BOOK]: { cfi: 'epubcfi(/6/40)', pct: 0.7, title: 't', time: 5000 }, 'Other Book': { cfi: 'c', pct: 0.1, title: 'o', time: 1 } },
    opened: { 'new.epub': 3 },
    prefs: { computer: { reader: { theme: 'light' }, readerTime: 900 } },
  });
  const seen = [];
  S.onRemoteChange((r) => seen.push(r));
  await S.syncNow();
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].places.sort(), [BOOK, 'Other Book'].sort());
  assert.equal(seen[0].prefs, true);
  assert.equal(seen[0].opened, true);
  assert.deepEqual(get('epubReader:prefs'), { theme: 'light' });
  assert.equal(ls.getItem('epubReader:prefsTime'), '900');
  await S.syncNow();
  assert.equal(seen.length, 1); // nothing changed the second time
});

test('prefs of another device kind: not applied locally, kept in the doc', async () => {
  const S = env({ kind: 'computer' });
  put('epubReader:prefs', { theme: 'dark' }); put('epubReader:prefsTime', '100');
  srv.doc = M.merge(M.emptyDoc(), { prefs: { phone: { reader: { theme: 'sepia' }, readerTime: 9999 } } });
  await S.syncNow();
  assert.deepEqual(get('epubReader:prefs'), { theme: 'dark' });
  assert.equal(srv.doc.prefs.phone.reader.theme, 'sepia');
  assert.equal(srv.doc.prefs.computer.reader.theme, 'dark');
  const d = S.collectLocal();
  assert.equal(d.prefs.phone, undefined);
});

test('the catalog view (sort and grouping) is the same on every kind of device', async () => {
  const S = env({ kind: 'computer' });
  put('epubIndex:view', { sort: 'author', group: false }); put('epubIndex:view:time', '100');
  srv.doc = M.merge(M.emptyDoc(), { prefs: { all: { catalogView: { sort: 'opened', group: true }, catalogViewTime: 200 } } });
  await S.syncNow();
  assert.deepEqual(get('epubIndex:view'), { sort: 'opened', group: true });   // a phone's newer choice reaches the computer
  put('epubIndex:view', { sort: 'title', group: false }); put('epubIndex:view:time', '300');
  await S.syncNow();
  assert.deepEqual(srv.doc.prefs.all.catalogView, { sort: 'title', group: false });   // and the computer's goes back to all
  assert.equal(srv.doc.prefs.all.catalogViewTime, 300);
  assert.equal(srv.doc.prefs.computer && srv.doc.prefs.computer.catalogView, undefined);
});

test('non-synced keys are untouched and never uploaded', async () => {
  const S = env();
  seedLocal(); put('epubReader:other', 'zzz');
  await S.syncNow();
  assert.equal(ls.getItem('epubReader:loc:' + BOOK), 'cached-positions');
  assert.ok(!JSON.stringify(srv.doc).includes('cached-positions'));
  assert.ok(!JSON.stringify(srv.doc).includes('zzz'));
});

test('setMarkOrder controls the order marks are written in', async () => {
  const S = env();
  S.setMarkOrder((a, b) => b.time - a.time);
  put('epubReader:marks:' + BOOK, [mark(10)]);
  srv.doc = M.merge(M.emptyDoc(), { marks: { [BOOK]: { y: mark(20, { id: 'y' }), z: mark(30, { id: 'z' }) } } });
  await S.syncNow();
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [30, 20, 10]);
});

test('default mark order: marks already on the device keep their order, new ones follow by time', async () => {
  const S = env();
  // the reader keeps marks in book order, which need not be creation order
  put('epubReader:marks:' + BOOK, [mark(30), mark(10), mark(50)]);
  srv.doc = M.merge(M.emptyDoc(), { marks: { [BOOK]: { y: mark(40, { id: 'y' }), z: mark(20, { id: 'z' }) } } });
  await S.syncNow();
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [30, 10, 50, 20, 40]);
  // none on the device yet: by time
  srv.doc = M.merge(srv.doc, { marks: { [WEIRD]: { a: mark(9, { id: 'a' }), b: mark(3, { id: 'b' }) } } });
  await S.syncNow();
  assert.deepEqual(get('epubReader:marks:' + WEIRD).map((m) => m.time), [3, 9]);
});

test('a library computer error (5xx) on sign-in or create is server-error, not offline', async () => {
  const S = env({ signedIn: false });
  srv.broken = 500;
  assert.deepEqual(await S.login('ann@example.com', 'pw'), { ok: false, error: 'server-error' });
  assert.deepEqual(await S.register('bob@example.com', 'pw', srv.code), { ok: false, error: 'server-error' });
  srv.broken = 503;
  assert.deepEqual(await S.login('ann@example.com', 'pw'), { ok: false, error: 'server-error' });
});

test('concurrent syncNow share one in-flight run', async () => {
  const S = env();
  const a = S.syncNow(), b = S.syncNow();
  assert.equal(a, b);
  await a;
  assert.equal(srv.calls.filter((c) => c.url === '/__sync/data' && c.opts.method === 'GET').length, 1);
});

test('changed(): marks dirty, at most one sync per 30 s with a trailing call', async () => {
  mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 });
  const S = env();
  const gets = () => srv.calls.filter((c) => c.url === '/__sync/data' && c.opts.method === 'GET').length;
  S.changed(); await flush();
  assert.equal(gets(), 1);
  assert.equal(ls.getItem('librarySync:dirty'), null);
  S.changed(); S.changed(); await flush();
  assert.equal(ls.getItem('librarySync:dirty'), '1');
  assert.equal(gets(), 1);
  mock.timers.tick(29000); await flush();
  assert.equal(gets(), 1);
  mock.timers.tick(1000); await flush();
  assert.equal(gets(), 2);
  assert.equal(ls.getItem('librarySync:dirty'), null);
  mock.timers.reset();
});

test('online event retries a dirty sync', async () => {
  const handlers = {};
  globalThis.addEventListener = (n, f) => { (handlers[n] = handlers[n] || []).push(f); };
  const S = env();
  srv.down = true;
  S.changed(); await flush();
  assert.equal(ls.getItem('librarySync:dirty'), '1');
  assert.equal(handlers.online.length, 1);
  srv.down = false;
  handlers.online[0](); await flush();
  assert.equal(ls.getItem('librarySync:dirty'), null);
  delete globalThis.addEventListener;
});

test('sendPlaceNow: keepalive POST, never throws', async () => {
  const S = env();
  S.sendPlaceNow(BOOK, { cfi: 'c', pct: 0.3, time: 5 });
  assert.equal(srv.calls.length, 1);
  const c = srv.calls[0];
  assert.equal(c.url, '/__sync/place');
  assert.equal(c.opts.method, 'POST');
  assert.equal(c.opts.keepalive, true);
  assert.deepEqual(JSON.parse(c.opts.body), { book: BOOK, place: { cfi: 'c', pct: 0.3, time: 5 } });
  globalThis.fetch = () => { throw new Error('sync boom'); };
  S.sendPlaceNow(BOOK, {});
  globalThis.fetch = () => Promise.reject(new Error('async boom'));
  S.sendPlaceNow(BOOK, {});
  await flush();
});

test('available: secure http(s) pages only, never file://', () => {
  const S = env();
  globalThis.location = { protocol: 'https:' };
  assert.equal(S.available(), true);
  globalThis.location = { protocol: 'http:' };
  assert.equal(S.available(), true);
  globalThis.isSecureContext = false;
  assert.equal(S.available(), false);
  globalThis.isSecureContext = true;
  globalThis.location = { protocol: 'file:' };
  assert.equal(S.available(), false);
  delete globalThis.location;
});

test('signedIn and lastSync', async () => {
  let S = env({ signedIn: false });
  assert.equal(S.signedIn(), false);
  assert.equal(S.lastSync(), null);
  S = env();
  assert.equal(S.signedIn(), true);
  const before = Date.now();
  assert.equal(await S.syncNow(), 'ok');
  assert.ok(S.lastSync() >= before);
  srv.token = 'other';   // the server forgot this device: 401
  assert.equal(await S.syncNow(), 'signed-out');
  assert.equal(S.signedIn(), false);
  assert.deepEqual(S.account(), { login: 'ann@example.com' });
});

test('a failed PUT still announces what applyLocal changed (else nobody ever would)', async () => {
  const S = env();
  put('epubReader:marks:' + BOOK, [mark(10), mark(20)]);
  srv.doc = M.merge(M.emptyDoc(), { marks: { [BOOK]: { '10|epubcfi(/6/10)': { deleted: GONE } } }, prefs: { computer: { reader: { theme: 'dark' }, readerTime: 900 } } });
  const realFetch = srv.fetch;
  globalThis.fetch = (url, opts = {}) => (opts.method === 'PUT' ? Promise.resolve({ status: 500, json: async () => ({}) }) : realFetch(url, opts));
  const seen = [];
  S.onRemoteChange((r) => seen.push(r));
  assert.equal(await S.syncNow(), 'offline');
  await flush();
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].marks, [BOOK]);
  assert.equal(seen[0].prefs, true);
  assert.deepEqual(get('epubReader:marks:' + BOOK).map((m) => m.time), [20]);
});

test('the deadline during the PUT still announces what applyLocal changed', async () => {
  const S = env();
  put('epubReader:marks:' + BOOK, [mark(10), mark(20)]);
  srv.doc = M.merge(M.emptyDoc(), { marks: { [BOOK]: { '10|epubcfi(/6/10)': { deleted: GONE } } } });
  const realFetch = srv.fetch;
  globalThis.fetch = (url, opts = {}) => (opts.method === 'PUT'
    ? new Promise((_, rej) => opts.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' }))))
    : realFetch(url, opts));
  const seen = [];
  S.onRemoteChange((r) => seen.push(r));
  assert.equal(await S.syncNow({ timeoutMs: 50 }), 'offline');
  await flush();
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].marks, [BOOK]);
});

test('collectLocal reads epubLibrary:shelves', () => {
  const S = env();
  const sh = { f1: { name: 'To read', named: 1, created: 1, books: { [BOOK]: { time: 2 } } } };
  put('epubLibrary:shelves', sh);
  assert.deepEqual(S.collectLocal().shelves, sh);
  put('epubLibrary:shelves', '[1]');
  assert.deepEqual(S.collectLocal().shelves, {});
  ls.setItem('epubLibrary:shelves', '{bad json');
  assert.deepEqual(S.collectLocal().shelves, {});
});

test('applyLocal writes shelves and reports it', () => {
  const S = env();
  const sh = { f1: { name: 'To read', named: 1, created: 1, books: { [BOOK]: { time: 2 } } } };
  const doc = Object.assign(M.emptyDoc(), { shelves: sh });
  assert.equal(S.applyLocal(doc).shelves, true);
  assert.deepEqual(get('epubLibrary:shelves'), sh);
  assert.equal(S.applyLocal(doc).shelves, false);
});

test("a sync brings another device's folder and announces shelves", async () => {
  const S = env();
  const f1 = { name: 'To read', named: 1, created: 1, books: { [BOOK]: { time: 2 } } };
  const f2 = { name: 'Favourites', named: 1, created: 3, books: {} };
  srv.doc = M.merge(M.emptyDoc(), { shelves: { f1 } });
  put('epubLibrary:shelves', { f2 });
  const seen = [];
  S.onRemoteChange((r) => seen.push(r));
  await S.syncNow();
  assert.deepEqual(Object.keys(get('epubLibrary:shelves')).sort(), ['f1', 'f2']);
  assert.deepEqual(Object.keys(srv.doc.shelves).sort(), ['f1', 'f2']);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].shelves, true);
});

test('a folder taken out offline is not brought back', async () => {
  const S = env();
  const NOW = Date.now() - 1000;
  const mk = (b) => ({ f1: { name: 'To read', named: 1, created: 1, books: { [BOOK]: b } } });
  srv.doc = M.merge(M.emptyDoc(), { shelves: mk({ time: NOW - 100 }) });
  put('epubLibrary:shelves', mk({ time: NOW, out: true }));
  await S.syncNow();
  assert.deepEqual(get('epubLibrary:shelves').f1.books[BOOK], { time: NOW, out: true });
  assert.deepEqual(srv.doc.shelves.f1.books[BOOK], { time: NOW, out: true });
});
