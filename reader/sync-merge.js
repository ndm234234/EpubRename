// Pure merge of two sync documents (see docs/superpowers/specs/2026-11-26-library-sync-design.md).
// No DOM, no storage. Keys named __proto__ are dropped; non-object entries are ignored.
  // For well-formed or JSON-parsed input every winner is picked by a total order, so merge is
// commutative, idempotent and associative.
(function () {
  'use strict';

  var YEAR_MS = 365 * 24 * 3600 * 1000;
  var KNOWN_GROUPS = ['reader', 'catalogTab', 'catalogApp'];

  function isObj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function get(o, k) { return has(o, k) ? o[k] : undefined; }
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function time(x) { return isNum(x) ? x : -Infinity; }

  function norm(x) {
    if (Array.isArray(x)) return x.map(norm);
    if (isObj(x)) {
      var o = {};
      Object.keys(x).sort().forEach(function (k) { if (k !== '__proto__' && x[k] !== undefined) o[k] = norm(x[k]); });
      return o;
    }
    return x;
  }
  function ser(x) { var s = JSON.stringify(norm(x)); return s === undefined ? '' : s; }
  function clone(x) { return x === undefined ? x : JSON.parse(JSON.stringify(x)); }

  // Total order on candidates {t, k, s}: time, then kind (tombstone beats live at a tie), then text.
  function cmp(x, y) {
    if (x.t !== y.t) return x.t < y.t ? -1 : 1;
    if (x.k !== y.k) return x.k < y.k ? -1 : 1;
    if (x.s !== y.s) return x.s < y.s ? -1 : 1;
    return 0;
  }
  function pickMax(items, rank) {
    var best = null, bestRank = null;
    items.forEach(function (it) {
      var r = rank(it);
      if (best === null || cmp(r, bestRank) > 0) { best = it; bestRank = r; }
    });
    return best;
  }

  function emptyDoc() { return { v: 1, places: {}, marks: {}, opened: {}, prefs: {}, shelves: {} }; }

  function markId(m) { return m.id || (m.time + '|' + m.cfi); }

  function isTomb(m) { return isObj(m) && isNum(m.deleted); }
  function markRank(m) {
    if (isTomb(m)) return { t: m.deleted, k: 1, s: ser(m) };
    return { t: Math.max(time(m.time), time(m.edited)), k: 0, s: ser(m) };
  }

  function keysOf(a, b) {
    var seen = Object.create(null), out = [];
    [a, b].forEach(function (o) {
      Object.keys(o).forEach(function (k) { if (k !== '__proto__' && !has(seen, k)) { seen[k] = 1; out.push(k); } });
    });
    return out;
  }
  function mapOf(d, k) { return d && isObj(get(d, k)) ? d[k] : {}; }

  function mergeMapOfEntries(a, b, rank) {
    var out = {};
    keysOf(a, b).forEach(function (k) {
      var items = [get(a, k), get(b, k)].filter(isObj);
      if (items.length) out[k] = clone(pickMax(items, rank));
    });
    return out;
  }

  function mergePlaces(a, b) {
    return mergeMapOfEntries(a, b, function (p) { return { t: time(p.time), k: 0, s: ser(p) }; });
  }

  function mergeMarks(a, b) {
    var out = {};
    keysOf(a, b).forEach(function (book) {
      var m = mergeMapOfEntries(isObj(get(a, book)) ? a[book] : {}, isObj(get(b, book)) ? b[book] : {}, markRank);
      if (isObj(get(a, book)) || isObj(get(b, book))) out[book] = m;
    });
    return out;
  }

  function mergeOpened(a, b) {
    var out = {};
    keysOf(a, b).forEach(function (k) {
      var items = [get(a, k), get(b, k)].filter(function (x) { return x !== undefined; });
      var best = pickMax(items, function (x) { return { t: time(x), k: 0, s: ser(x) }; });
      out[k] = clone(best);
    });
    return out;
  }

  function mergeKind(a, b) {
    var out = {}, keys = keysOf(a, b), groups = {};
    keys.forEach(function (k) {
      if (KNOWN_GROUPS.indexOf(k) >= 0) groups[k] = 1;
      else if (k.length > 4 && k.slice(-4) === 'Time') groups[k.slice(0, -4)] = 1;
    });
    keys.forEach(function (k) {
      if (!has(groups, k) && !(k.length > 4 && k.slice(-4) === 'Time' && has(groups, k.slice(0, -4)))) {
        var items = [a, b].filter(function (o) { return has(o, k); }).map(function (o) { return o[k]; });
        out[k] = clone(pickMax(items, function (x) { return { t: 0, k: 0, s: ser(x) }; }));
      }
    });
    Object.keys(groups).forEach(function (g) {
      var tk = g + 'Time';
      var cands = [a, b].filter(function (o) { return has(o, g) || has(o, tk); }).map(function (o) {
        var c = {};
        if (has(o, g)) c[g] = o[g];
        if (has(o, tk)) c[tk] = o[tk];
        return c;
      });
      var win = pickMax(cands, function (c) { return { t: time(c[tk]), k: 0, s: ser(c) }; });
      if (win) {
        if (has(win, g)) out[g] = clone(win[g]);
        if (has(win, tk)) out[tk] = clone(win[tk]);
      }
    });
    return out;
  }

  function mergePrefs(a, b) {
    var out = {};
    keysOf(a, b).forEach(function (kind) {
      var x = get(a, kind), y = get(b, kind);
      if (isObj(x) && isObj(y)) out[kind] = mergeKind(x, y);
      else if (isObj(x) || isObj(y)) out[kind] = mergeKind(isObj(x) ? x : {}, isObj(y) ? y : {});
    });
    return out;
  }

  function isGone(f) { return isObj(f) && isNum(f.deleted); }

  // A deleted folder stays deleted. Otherwise the later name, the earlier creation, and per book
  // the later entry (at a tie the one taken out) win.
  function mergeFolder(items) {
    var gone = items.filter(isGone);
    if (gone.length) {
      return { deleted: Math.max.apply(null, gone.map(function (f) { return f.deleted; })) };
    }
    var out = {};
    var namer = pickMax(items, function (f) { return { t: time(f.named), k: 0, s: ser(f.name) }; });
    if (has(namer, 'name')) out.name = clone(namer.name);
    if (has(namer, 'named')) out.named = clone(namer.named);
    var made = items.map(function (f) { return f.created; }).filter(isNum);
    if (made.length) out.created = Math.min.apply(null, made);
    var a = mapOf(items[0], 'books'), b = items.length > 1 ? mapOf(items[1], 'books') : {};
    out.books = mergeMapOfEntries(a, b, function (e) { return { t: time(e.time), k: e.out ? 1 : 0, s: ser(e) }; });
    return out;
  }

  function mergeShelves(a, b) {
    var out = {};
    keysOf(a, b).forEach(function (id) {
      var items = [get(a, id), get(b, id)].filter(isObj);
      if (items.length) out[id] = mergeFolder(items);
    });
    return out;
  }

  var KNOWN_TOP = { v: 1, places: 1, marks: 1, opened: 1, prefs: 1, shelves: 1 };

  function merge(a, b) {
    a = isObj(a) ? a : {};
    b = isObj(b) ? b : {};
    var out = {};
    keysOf(a, b).forEach(function (k) {
      if (has(KNOWN_TOP, k)) return;
      var items = [a, b].filter(function (o) { return has(o, k); }).map(function (o) { return o[k]; });
      out[k] = clone(pickMax(items, function (x) { return { t: 0, k: 0, s: ser(x) }; }));
    });
    out.v = 1;
    out.places = mergePlaces(mapOf(a, 'places'), mapOf(b, 'places'));
    out.marks = mergeMarks(mapOf(a, 'marks'), mapOf(b, 'marks'));
    out.opened = mergeOpened(mapOf(a, 'opened'), mapOf(b, 'opened'));
    out.prefs = mergePrefs(mapOf(a, 'prefs'), mapOf(b, 'prefs'));
    out.shelves = mergeShelves(mapOf(a, 'shelves'), mapOf(b, 'shelves'));
    return out;
  }

  function pruneTombstones(doc, now, maxAgeMs) {
    var limit = now - (maxAgeMs === undefined ? YEAR_MS : maxAgeMs);
    var out = clone(isObj(doc) ? doc : emptyDoc());
    var marks = isObj(out.marks) ? out.marks : {};
    Object.keys(marks).forEach(function (book) {
      var bm = marks[book];
      if (!isObj(bm)) return;
      Object.keys(bm).forEach(function (id) {
        if (isTomb(bm[id]) && bm[id].deleted < limit) delete bm[id];
      });
      if (!Object.keys(bm).length) delete marks[book];
    });
    out.marks = marks;
    var shelves = isObj(out.shelves) ? out.shelves : {};
    Object.keys(shelves).forEach(function (id) {
      var f = shelves[id];
      if (isGone(f)) {
        if (f.deleted < limit) delete shelves[id];
      } else if (isObj(f) && isObj(f.books)) {
        Object.keys(f.books).forEach(function (bk) {
          var e = f.books[bk];
          if (isObj(e) && e.out && time(e.time) < limit) delete f.books[bk];
        });
      }
    });
    out.shelves = shelves;
    return out;
  }

  globalThis.SyncMerge = { emptyDoc: emptyDoc, merge: merge, markId: markId, pruneTombstones: pruneTombstones };
})();
