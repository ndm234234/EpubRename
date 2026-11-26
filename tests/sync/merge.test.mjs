import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, '..', '..', 'reader', 'sync-merge.js'), 'utf8');
vm.runInThisContext(src, { filename: 'sync-merge.js' });
const M = globalThis.SyncMerge;

const BOOK = 'Jane Doe - A Quiet Harbour';
const doc = (o) => Object.assign({ v: 1, places: {}, marks: {}, opened: {}, prefs: {} }, o);
const clone = (x) => JSON.parse(JSON.stringify(x));

test('emptyDoc and null handling', () => {
  assert.deepEqual(M.emptyDoc(), { v: 1, places: {}, marks: {}, opened: {}, prefs: {} });
  const x = doc({ opened: { 'a.epub': 5 } });
  assert.deepEqual(M.merge(null, x), x);
  assert.deepEqual(M.merge(x, null), x);
  assert.deepEqual(M.merge(null, null), M.emptyDoc());
  assert.deepEqual(M.merge({}, { v: 1 }), M.emptyDoc());
});

test('markId', () => {
  assert.equal(M.markId({ id: 'x', time: 1, cfi: 'c' }), 'x');
  assert.equal(M.markId({ time: 12, cfi: 'epubcfi(/6/2)' }), '12|epubcfi(/6/2)');
});

test('place: later time wins, missing time is oldest', () => {
  const a = doc({ places: { [BOOK]: { cfi: 'a', pct: 0.1, time: 100 } } });
  const b = doc({ places: { [BOOK]: { cfi: 'b', pct: 0.5, time: 200 } } });
  assert.equal(M.merge(a, b).places[BOOK].cfi, 'b');
  assert.equal(M.merge(b, a).places[BOOK].cfi, 'b');
  const old = doc({ places: { [BOOK]: { cfi: 'old' } } });
  assert.equal(M.merge(old, a).places[BOOK].cfi, 'a');
  assert.equal(M.merge(a, old).places[BOOK].cfi, 'a');
  const old2 = doc({ places: { [BOOK]: { cfi: 'old', time: 'x' } } });
  assert.equal(M.merge(old2, old).places[BOOK].cfi, M.merge(old, old2).places[BOOK].cfi);
});

test('place: equal time is deterministic', () => {
  const a = doc({ places: { [BOOK]: { cfi: 'a', time: 5 } } });
  const b = doc({ places: { [BOOK]: { cfi: 'b', time: 5 } } });
  assert.deepEqual(M.merge(a, b), M.merge(b, a));
});

test('place: books from both sides are kept', () => {
  const a = doc({ places: { A: { cfi: '1', time: 1 } } });
  const b = doc({ places: { B: { cfi: '2', time: 1 } } });
  assert.deepEqual(Object.keys(M.merge(a, b).places).sort(), ['A', 'B']);
});

test('bookmarks: union by id', () => {
  const a = doc({ marks: { [BOOK]: { m1: { cfi: 'c1', time: 1 } } } });
  const b = doc({ marks: { [BOOK]: { m2: { cfi: 'c2', time: 2 } } } });
  assert.deepEqual(Object.keys(M.merge(a, b).marks[BOOK]).sort(), ['m1', 'm2']);
});

test('bookmarks: later edit wins; tombstone vs edit', () => {
  const live = { cfi: 'c', name: 'old', time: 10 };
  const edited = { cfi: 'c', name: 'new', time: 10, edited: 50 };
  const a = doc({ marks: { [BOOK]: { m: live } } });
  const b = doc({ marks: { [BOOK]: { m: edited } } });
  assert.equal(M.merge(a, b).marks[BOOK].m.name, 'new');

  const tomb = (t) => doc({ marks: { [BOOK]: { m: { deleted: t } } } });
  // tombstone newer than edit removes
  assert.deepEqual(M.merge(b, tomb(60)).marks[BOOK].m, { deleted: 60 });
  assert.deepEqual(M.merge(tomb(60), b).marks[BOOK].m, { deleted: 60 });
  // edit newer than tombstone keeps
  assert.equal(M.merge(b, tomb(40)).marks[BOOK].m.name, 'new');
  assert.equal(M.merge(tomb(40), b).marks[BOOK].m.name, 'new');
  // tie: tombstone wins, both orders
  assert.deepEqual(M.merge(b, tomb(50)).marks[BOOK].m, { deleted: 50 });
  assert.deepEqual(M.merge(tomb(50), b).marks[BOOK].m, { deleted: 50 });
  // two tombstones: later deleted
  assert.deepEqual(M.merge(tomb(5), tomb(9)).marks[BOOK].m, { deleted: 9 });
});

test('bookmarks: same old bookmark without id on both sides becomes one', () => {
  const m = { cfi: 'epubcfi(/6/4)', text: 't', time: 77 };
  const id = M.markId(m);
  const a = doc({ marks: { [BOOK]: { [id]: clone(m) } } });
  const b = doc({ marks: { [BOOK]: { [id]: clone(m) } } });
  assert.equal(Object.keys(M.merge(a, b).marks[BOOK]).length, 1);
});

test('opened: per href max', () => {
  const a = doc({ opened: { 'a.epub': 5, 'b.epub': 9 } });
  const b = doc({ opened: { 'a.epub': 7, 'c.epub': 1 } });
  assert.deepEqual(M.merge(a, b).opened, { 'a.epub': 7, 'b.epub': 9, 'c.epub': 1 });
  const bad = doc({ opened: { 'a.epub': 'zz' } });
  assert.equal(M.merge(bad, a).opened['a.epub'], 5);
  assert.equal(M.merge(a, bad).opened['a.epub'], 5);
});

test('prefs: per kind and group, value and time move together', () => {
  const a = doc({ prefs: { phone: { reader: { size: 1 }, readerTime: 10, catalogTab: { t: 'x' }, catalogTabTime: 50 } } });
  const b = doc({ prefs: { phone: { reader: { size: 2 }, readerTime: 20, catalogTab: { t: 'y' }, catalogTabTime: 5 },
                           tablet: { catalogApp: { z: 1 }, catalogAppTime: 3 } } });
  const r = M.merge(a, b);
  assert.deepEqual(r.prefs.phone.reader, { size: 2 });
  assert.equal(r.prefs.phone.readerTime, 20);
  assert.deepEqual(r.prefs.phone.catalogTab, { t: 'x' });
  assert.equal(r.prefs.phone.catalogTabTime, 50);
  assert.deepEqual(r.prefs.tablet, { catalogApp: { z: 1 }, catalogAppTime: 3 });
  assert.deepEqual(M.merge(b, a), r);
});

test('prefs: unknown groups and extra keys', () => {
  const a = doc({ prefs: { phone: { fancy: 1, fancyTime: 9, extra: 'k' } } });
  const b = doc({ prefs: { phone: { fancy: 2, fancyTime: 4, other: 'o' } } });
  const r = M.merge(a, b);
  assert.equal(r.prefs.phone.fancy, 1);
  assert.equal(r.prefs.phone.fancyTime, 9);
  assert.equal(r.prefs.phone.extra, 'k');
  assert.equal(r.prefs.phone.other, 'o');
  assert.deepEqual(M.merge(b, a), r);
});

test('unknown keys survive; inputs not mutated; v and maps always present', () => {
  const a = doc({ future: { x: 1 }, places: { [BOOK]: { cfi: 'a', time: 9, extraKey: 'keep' } } });
  const b = doc({ other: [1, 2] });
  const ac = clone(a), bc = clone(b);
  const r = M.merge(a, b);
  assert.deepEqual(r.future, { x: 1 });
  assert.deepEqual(r.other, [1, 2]);
  assert.equal(r.places[BOOK].extraKey, 'keep');
  assert.deepEqual(a, ac);
  assert.deepEqual(b, bc);
  r.places[BOOK].cfi = 'mut';
  assert.equal(a.places[BOOK].cfi, 'a');
  const e = M.merge({ v: 7 }, null);
  assert.equal(e.v, 1);
  for (const k of ['places', 'marks', 'opened', 'prefs']) assert.deepEqual(e[k], {});
});

test('pruneTombstones drops old tombstones only', () => {
  const now = 1e13, year = 365 * 24 * 3600 * 1000;
  const d = doc({ marks: {
    A: { old: { deleted: now - year - 1 }, fresh: { deleted: now - 1000 }, live: { cfi: 'c', time: 1 } },
    B: { old: { deleted: 1 } } } });
  const c = clone(d);
  const r = M.pruneTombstones(d, now);
  assert.deepEqual(Object.keys(r.marks.A).sort(), ['fresh', 'live']);
  assert.equal(r.marks.B, undefined);
  assert.deepEqual(d, c);
  assert.equal(M.pruneTombstones(d, now, 10).marks.A.fresh, undefined);
});

// ---- randomized properties ----
function prng(seed) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function randDoc(r) {
  const pick = (a) => a[Math.floor(r() * a.length)];
  const t = () => pick([undefined, 'bad', 1, 2, 3, 4, 5]);
  const d = { v: 1, places: {}, marks: {}, opened: {}, prefs: {} };
  for (const b of ['A', 'B']) {
    if (r() < 0.6) d.places[b] = { cfi: pick(['x', 'y', 'z']), pct: pick([0.1, 0.2]), time: t() };
    if (r() < 0.7) {
      d.marks[b] = {};
      for (const id of ['m1', 'm2', 'm3']) {
        const x = r();
        if (x < 0.3) continue;
        d.marks[b][id] = x < 0.55 ? { deleted: pick([1, 2, 3, 4, 5]), ...(r() < 0.3 ? { z: pick([1, 2]) } : {}) }
          : { cfi: pick(['p', 'q']), name: pick(['n1', 'n2']), time: pick([1, 2, 3]), edited: pick([undefined, 2, 3, 4, 5]) };
      }
    }
    if (r() < 0.5) d.opened[b] = t();
  }
  for (const k of ['phone', 'tablet']) {
    if (r() < 0.6) {
      const p = {};
      for (const g of ['reader', 'catalogTab', 'catalogApp', 'fancy']) {
        if (r() < 0.6) { p[g] = { v: pick([1, 2, 3]) }; if (r() < 0.9) p[g + 'Time'] = t(); }
      }
      if (r() < 0.3) p.stray = pick([1, 2]);
      d.prefs[k] = p;
    }
  }
  if (r() < 0.3) d.future = pick([1, 2]);
  return JSON.parse(JSON.stringify(d));
}

test('property: commutative, idempotent, associative (seeded)', () => {
  const r = prng(20261126);
  for (let i = 0; i < 400; i++) {
    const a = randDoc(r), b = randDoc(r), c = randDoc(r);
    const ab = M.merge(a, b);
    assert.deepEqual(ab, M.merge(b, a), 'commutative case ' + i);
    assert.deepEqual(M.merge(a, ab), ab, 'idempotent case ' + i);
    assert.deepEqual(M.merge(ab, ab), ab, 'self-merge case ' + i);
    assert.deepEqual(M.merge(M.merge(a, b), c), M.merge(a, M.merge(b, c)), 'associative case ' + i);
  }
});
