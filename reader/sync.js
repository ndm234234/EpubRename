// Browser sync client shared by the reader and the catalog (see
// docs/superpowers/specs/2026-11-26-library-sync-design.md). Needs SyncMerge (sync-merge.js).
// Collects local data from localStorage into a sync document, merges it with the server's, writes
// both ways. Reads localStorage, fetch, isSecureContext, matchMedia, screen at call time.
(function () {
  'use strict';

  var P_BOOK = 'epubReader:book:', P_MARKS = 'epubReader:marks:', P_GONE = 'epubReader:marksGone:';
  var K_OPENED = 'epubLibrary:opened';
  var PREF_KEYS = [
    { group: 'reader', key: 'epubReader:prefs', time: 'epubReader:prefsTime' },
    { group: 'catalogTab', key: 'epubIndex', time: 'epubIndex:time' },
    { group: 'catalogApp', key: 'epubIndex:app', time: 'epubIndex:app:time' }
  ];
  var K_ACCOUNT = 'librarySync:account', K_DIRTY = 'librarySync:dirty', K_LAST = 'librarySync:lastSync';
  var THROTTLE_MS = 30000, DEFAULT_TIMEOUT = 8000, AUTH_TIMEOUT = 10000, MAX_PUTS = 3;

  var markOrder = function (a, b) { return (a.time || 0) - (b.time || 0); };
  var listeners = [];
  var inflight = null, changeSeq = 0, lastRun = -Infinity, timer = null, onlineHooked = false;

  function isObj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function canon(x) {
    if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
    if (isObj(x)) return '{' + Object.keys(x).sort().map(function (k) { return JSON.stringify(k) + ':' + canon(x[k]); }).join(',') + '}';
    var s = JSON.stringify(x);
    return s === undefined ? 'null' : s;
  }

  function lsGet(k) { try { return globalThis.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { globalThis.localStorage.setItem(k, v); return true; } catch (e) { return false; } }
  function lsDel(k) { try { globalThis.localStorage.removeItem(k); } catch (e) { /* ignore */ } }
  function lsJson(k) {
    var s = lsGet(k);
    if (s === null) return undefined;
    try { return JSON.parse(s); } catch (e) { return undefined; }
  }
  function lsKeys(prefix) {
    var out = [], ls = globalThis.localStorage;
    try {
      for (var i = 0; i < ls.length; i++) {
        var k = ls.key(i);
        if (k && k.indexOf(prefix) === 0) out.push(k.slice(prefix.length));
      }
    } catch (e) { /* ignore */ }
    return out;
  }
  // Writes only when the stored value differs (compared as JSON, not as text).
  function writeJson(k, val) {
    if (canon(lsJson(k)) === canon(val) && lsGet(k) !== null) return false;
    return lsSet(k, JSON.stringify(val));
  }

  // Secure pages only (https, localhost); and only from a server: file:// pages count as secure too
  function available() {
    var loc = globalThis.location;
    if (loc && !/^https?:$/.test(loc.protocol)) return false;
    return !!globalThis.isSecureContext;
  }

  function deviceKind() {
    try {
      var mm = globalThis.matchMedia;
      if (typeof mm === 'function' && mm('(pointer: coarse)').matches && mm('(hover: none)').matches) {
        var s = globalThis.screen || {};
        return Math.min(s.width, s.height) < 600 ? 'phone' : 'tablet';
      }
    } catch (e) { /* fall through */ }
    return 'computer';
  }

  // ---- account ----
  function readAccount() {
    var a = lsJson(K_ACCOUNT);
    return isObj(a) && typeof a.login === 'string' ? a : null;
  }
  function account() {
    var a = readAccount();
    return a ? { login: a.login } : null;
  }
  function signedIn() { var a = readAccount(); return !!(a && a.token); }
  // When the last sync finished (ms), or null
  function lastSync() { var t = Number(lsGet(K_LAST)); return lsGet(K_LAST) !== null && isFinite(t) && t > 0 ? t : null; }

  function call(method, url, token, body, signal) {
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;
    var opts = { method: method, headers: headers, signal: signal };
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    return globalThis.fetch(url, opts).then(function (r) {
      return Promise.resolve(r.status === 204 ? null : r.json()).catch(function () { return null; })
        .then(function (b) { return { status: r.status, body: b }; });
    });
  }
  // Wraps call() with a deadline so a hung request cannot hold the UI.
  function limited(ms, fn) {
    var ctl = typeof AbortController === 'function' ? new AbortController() : null, t;
    var dl = new Promise(function (res, rej) {
      t = setTimeout(function () { try { if (ctl) ctl.abort(); } catch (e) { /* ignore */ } rej(new Error('timeout')); }, ms);
    });
    var work;
    try { work = fn(ctl ? ctl.signal : undefined); } catch (e) { work = Promise.reject(e); }
    return Promise.race([work, dl]).then(function (v) { clearTimeout(t); return v; }, function (e) { clearTimeout(t); throw e; });
  }

  var ERR = {
    register: { 400: 'bad-login', 403: 'wrong-code', 409: 'taken', 429: 'wait' },
    login: { 400: 'bad-login', 401: 'wrong-password', 429: 'wait' }
  };
  function auth(kind, login, body) {
    return limited(AUTH_TIMEOUT, function (signal) { return call('POST', '/__sync/' + kind, null, body, signal); }).then(function (r) {
      if (r.status === 200 && isObj(r.body) && typeof r.body.token === 'string') {
        lsSet(K_ACCOUNT, JSON.stringify({ login: String(login).toLowerCase().trim(), token: r.body.token }));
        return { ok: true };
      }
      var out = { ok: false, error: ERR[kind][r.status] || 'offline' };
      if (out.error === 'wait' && isObj(r.body) && isNum(r.body.wait)) out.wait = r.body.wait;
      return out;
    }, function () { return { ok: false, error: 'offline' }; });
  }
  function register(login, password, code) { return auth('register', login, { login: login, password: password, code: code }); }
  function login(l, password) { return auth('login', l, { login: l, password: password }); }
  function logout() {
    var a = readAccount();
    lsDel(K_ACCOUNT);
    if (!a || !a.token) return Promise.resolve({ ok: true });
    return limited(AUTH_TIMEOUT, function (signal) { return call('POST', '/__sync/logout', a.token, null, signal); })
      .then(function () { return { ok: true }; }, function () { return { ok: true }; });
  }

  // ---- local <-> doc ----
  function setMarkOrder(fn) { if (typeof fn === 'function') markOrder = fn; }

  function collectLocal() {
    var doc = SyncMerge.emptyDoc(), kind = deviceKind();
    lsKeys(P_BOOK).forEach(function (id) {
      var p = lsJson(P_BOOK + id);
      if (isObj(p)) doc.places[id] = p;
    });
    var bookMarks = {};
    lsKeys(P_MARKS).forEach(function (id) {
      var arr = lsJson(P_MARKS + id);
      if (!Array.isArray(arr)) return;
      var m = bookMarks[id] = {};
      arr.forEach(function (x) { if (isObj(x)) m[SyncMerge.markId(x)] = x; });
    });
    lsKeys(P_GONE).forEach(function (id) {
      var g = lsJson(P_GONE + id);
      if (!isObj(g)) return;
      var m = bookMarks[id] = bookMarks[id] || {};
      Object.keys(g).forEach(function (mid) { if (isNum(g[mid])) m[mid] = { deleted: g[mid] }; });
    });
    Object.keys(bookMarks).forEach(function (id) { if (Object.keys(bookMarks[id]).length) doc.marks[id] = bookMarks[id]; });
    var op = lsJson(K_OPENED);
    if (isObj(op)) doc.opened = op;
    var prefs = {};
    PREF_KEYS.forEach(function (d) {
      var v = lsJson(d.key);
      if (!isObj(v)) return;
      prefs[d.group] = v;
      var t = Number(lsGet(d.time));
      if (lsGet(d.time) !== null && isFinite(t)) prefs[d.group + 'Time'] = t;
    });
    if (Object.keys(prefs).length) doc.prefs[kind] = prefs;
    return doc;
  }

  function applyLocal(doc) {
    var rep = { places: [], marks: [], prefs: false, opened: false };
    if (!isObj(doc)) return rep;
    var places = isObj(doc.places) ? doc.places : {};
    Object.keys(places).forEach(function (id) {
      if (isObj(places[id]) && writeJson(P_BOOK + id, places[id])) rep.places.push(id);
    });
    var marks = isObj(doc.marks) ? doc.marks : {};
    Object.keys(marks).forEach(function (id) {
      var live = [], gone = {};
      var entries = isObj(marks[id]) ? marks[id] : {};
      Object.keys(entries).forEach(function (mid) {
        var m = entries[mid];
        if (!isObj(m)) return;
        if (isNum(m.deleted)) gone[mid] = m.deleted; else live.push(m);
      });
      var oldLive = lsJson(P_MARKS + id), changed = false;
      var asSet = function (a) { return Array.isArray(a) ? a.map(canon).sort().join('|') : ''; };
      if (asSet(oldLive) !== asSet(live) || (oldLive === undefined && live.length)) {
        if (live.length || oldLive !== undefined) { live.sort(markOrder); changed = lsSet(P_MARKS + id, JSON.stringify(live)) || changed; }
      }
      var oldGone = lsJson(P_GONE + id);
      if (canon(isObj(oldGone) ? oldGone : {}) !== canon(gone)) {
        if (Object.keys(gone).length) lsSet(P_GONE + id, JSON.stringify(gone)); else lsDel(P_GONE + id);
        changed = true;
      }
      if (changed) rep.marks.push(id);
    });
    if (isObj(doc.opened) && writeJson(K_OPENED, doc.opened)) rep.opened = true;
    var mine = isObj(doc.prefs) && isObj(doc.prefs[deviceKind()]) ? doc.prefs[deviceKind()] : {};
    PREF_KEYS.forEach(function (d) {
      if (isObj(mine[d.group]) && writeJson(d.key, mine[d.group])) rep.prefs = true;
      var t = mine[d.group + 'Time'];
      if (isNum(t) && lsGet(d.time) !== String(t)) { if (lsSet(d.time, String(t))) rep.prefs = true; }
    });
    return rep;
  }

  // ---- syncing ----
  function dropToken() {
    var a = readAccount();
    if (a) lsSet(K_ACCOUNT, JSON.stringify({ login: a.login }));
    return 'signed-out';
  }

  function attempt(signal, isDead) {
    var a = readAccount();
    if (!a || !a.token) return Promise.resolve('signed-out');
    var seq = changeSeq, report = { places: [], marks: [], prefs: false, opened: false };
    function absorb(r) {
      r.places.forEach(function (id) { if (report.places.indexOf(id) < 0) report.places.push(id); });
      r.marks.forEach(function (id) { if (report.marks.indexOf(id) < 0) report.marks.push(id); });
      report.prefs = report.prefs || r.prefs;
      report.opened = report.opened || r.opened;
    }
    // What applyLocal changed is announced however the attempt ends (ok, offline, a failed PUT, the
    // deadline): localStorage already holds it, and the next sync would find nothing new to report.
    function announce() {
      if (report.places.length || report.marks.length || report.prefs || report.opened) {
        listeners.slice().forEach(function (fn) { try { fn(report); } catch (e) { /* ignore */ } });
      }
    }
    function finish() {
      lsSet(K_LAST, String(Date.now()));
      if (seq === changeSeq) lsDel(K_DIRTY);
      return 'ok';
    }
    function round(server, version, puts) {
      if (isDead()) return 'offline';
      var merged = SyncMerge.pruneTombstones(SyncMerge.merge(server, collectLocal()), Date.now());
      absorb(applyLocal(merged));
      if (canon(merged) === canon(server)) return finish();
      if (puts >= MAX_PUTS) return 'offline';
      return call('PUT', '/__sync/data', a.token, { version: version, doc: merged }, signal).then(function (r) {
        if (isDead()) return 'offline';
        if (r.status === 200) return finish();
        if (r.status === 401) return dropToken();
        if (r.status === 409 && isObj(r.body)) return round(isObj(r.body.doc) ? r.body.doc : {}, r.body.version, puts + 1);
        return 'offline';
      });
    }
    return call('GET', '/__sync/data', a.token, null, signal).then(function (r) {
      if (r.status === 401) return dropToken();
      if (r.status !== 200 || !isObj(r.body)) return 'offline';
      return round(isObj(r.body.doc) ? r.body.doc : {}, r.body.version, 0);
    }).then(function (res) { announce(); return res; }, function (e) { announce(); throw e; });
  }

  function syncNow(opts) {
    if (inflight) return inflight;
    var ms = opts && isNum(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_TIMEOUT;
    lastRun = Date.now();
    var dead = false, t, ctl = null;
    try { ctl = typeof AbortController === 'function' ? new AbortController() : null; } catch (e) { ctl = null; }
    var deadline = new Promise(function (res) {
      t = setTimeout(function () { dead = true; try { if (ctl) ctl.abort(); } catch (e) { /* ignore */ } res('offline'); }, ms);
    });
    var work;
    try { work = attempt(ctl ? ctl.signal : undefined, function () { return dead; }).catch(function () { return 'offline'; }); } catch (e) { work = Promise.resolve('offline'); }
    var p = Promise.race([work, deadline]).then(function (r) { clearTimeout(t); dead = true; inflight = null; return r; });
    inflight = p;
    return p;
  }

  function hookOnline() {
    if (onlineHooked || typeof globalThis.addEventListener !== 'function') return;
    onlineHooked = true;
    globalThis.addEventListener('online', function () { if (lsGet(K_DIRTY) && signedIn()) syncNow(); });
  }

  function changed() {
    changeSeq++;
    lsSet(K_DIRTY, '1');
    hookOnline();
    if (!signedIn() || timer !== null) return;
    var wait = Math.max(0, THROTTLE_MS - (Date.now() - lastRun));
    if (wait === 0) { syncNow(); return; }
    timer = setTimeout(function () { timer = null; syncNow(); }, wait);
  }

  function sendPlaceNow(bookId, place) {
    try {
      var a = readAccount();
      if (!a || !a.token) return;
      var p = globalThis.fetch('/__sync/place', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + a.token },
        body: JSON.stringify({ book: bookId, place: place })
      });
      if (p && typeof p.catch === 'function') p.catch(function () { /* fire and forget */ });
    } catch (e) { /* never throws */ }
  }

  function onRemoteChange(fn) { if (typeof fn === 'function') listeners.push(fn); }

  globalThis.LibrarySync = {
    available: available, deviceKind: deviceKind, account: account, signedIn: signedIn, lastSync: lastSync,
    register: register, login: login, logout: logout,
    collectLocal: collectLocal, applyLocal: applyLocal, setMarkOrder: setMarkOrder,
    syncNow: syncNow, changed: changed, sendPlaceNow: sendPlaceNow, onRemoteChange: onRemoteChange
  };
})();
