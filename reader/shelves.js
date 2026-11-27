// Pure edits of the folders map (see docs/superpowers/specs/2026-11-26-library-sync-design.md):
// shelves[id] = {name, named, created, books: {bookId: {time} | {time, out: true}}} or {deleted: time}.
// No DOM, no storage. Functions never mutate their input; they return a new map.
// Keys named __proto__ are skipped.
(function () {
  'use strict';

  var MAX_NAME = 60;

  function isObj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function isLive(f) { return isObj(f) && typeof f.deleted !== 'number'; }

  function clone(x) {
    if (Array.isArray(x)) return x.map(clone);
    if (isObj(x)) {
      var o = {};
      Object.keys(x).forEach(function (k) { if (k !== '__proto__') o[k] = clone(x[k]); });
      return o;
    }
    return x;
  }

  function liveIds(map) {
    return Object.keys(isObj(map) ? map : {}).filter(function (id) { return id !== '__proto__' && isLive(map[id]); });
  }

  function booksOf(f) { return isObj(f.books) ? f.books : {}; }

  function list(map, haveBook, compare) {
    return liveIds(map).map(function (id) {
      var f = map[id], b = booksOf(f);
      return {
        id: id,
        name: String(f.name === undefined ? '' : f.name),
        books: Object.keys(b).filter(function (bk) {
          return bk !== '__proto__' && isObj(b[bk]) && !b[bk].out && haveBook(bk);
        })
      };
    }).sort(function (x, y) { return compare(x.name, y.name); });
  }

  function has_(map, folderId, bookId) {
    if (!isObj(map) || folderId === '__proto__' || bookId === '__proto__') return false;
    if (!has(map, folderId) || !isLive(map[folderId])) return false;
    var b = booksOf(map[folderId]);
    return has(b, bookId) && isObj(b[bookId]) && !b[bookId].out;
  }

  function checkName(map, name, compare, exceptId) {
    var n = String(name === undefined || name === null ? '' : name).trim();
    if (!n) return { error: 'empty' };
    if (n.length > MAX_NAME) return { error: 'long' };
    var taken = liveIds(map).some(function (id) {
      return id !== exceptId && compare(String(map[id].name), n) === 0;
    });
    return taken ? { error: 'taken' } : { name: n };
  }

  function newId(now) {
    var s = '';
    while (s.length < 6) s += Math.floor(Math.random() * 36).toString(36);
    return now.toString(36) + '-' + s;
  }

  function create(map, name, now, compare) {
    var c = checkName(map, name, compare);
    if (c.error) return { error: c.error };
    var out = clone(isObj(map) ? map : {});
    var id = newId(now);
    while (has(out, id)) id = newId(now);
    out[id] = { name: c.name, named: now, created: now, books: {} };
    return { map: out, id: id };
  }

  function rename(map, id, name, now, compare) {
    if (!isObj(map) || id === '__proto__' || !has(map, id) || !isLive(map[id])) return { error: 'missing' };
    var c = checkName(map, name, compare, id);
    if (c.error) return { error: c.error };
    var out = clone(map);
    out[id].name = c.name;
    out[id].named = now;
    return { map: out };
  }

  function remove(map, id, now) {
    var out = clone(isObj(map) ? map : {});
    if (id === '__proto__') return out;
    out[id] = { deleted: now };
    return out;
  }

  function setBook(map, id, bookId, inside, now) {
    if (!isObj(map) || id === '__proto__' || bookId === '__proto__' || !has(map, id) || !isLive(map[id])) return map;
    var out = clone(map);
    if (!isObj(out[id].books)) out[id].books = {};
    out[id].books[bookId] = inside ? { time: now } : { time: now, out: true };
    return out;
  }

  globalThis.LibraryShelves = {
    list: list, has: has_, checkName: checkName, create: create,
    rename: rename, remove: remove, setBook: setBook
  };
})();
