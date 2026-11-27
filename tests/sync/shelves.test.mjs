import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, '..', '..', 'reader', 'shelves.js'), 'utf8');
vm.runInThisContext(src, { filename: 'shelves.js' });
const S = globalThis.LibraryShelves;

const BOOK = 'Jane Doe - A Quiet Harbour';
const compare = new Intl.Collator('en', { sensitivity: 'base' }).compare;
const all = () => true;
const deepFreeze = (x) => {
  if (x && typeof x === 'object') { Object.values(x).forEach(deepFreeze); Object.freeze(x); }
  return x;
};
const names = (m) => S.list(m, all, compare).map((f) => f.name);

test('create and list', () => {
  let m = S.create({}, 'To read', 10, compare).map;
  m = S.create(m, 'Holiday', 11, compare).map;
  assert.deepEqual(names(m), ['Holiday', 'To read']);
  const r = S.create({}, ' Trips ', 1000, compare);
  assert.match(r.id, /^[0-9a-z]+-[0-9a-z]{6}$/);
  assert.ok(r.id.startsWith((1000).toString(36) + '-'));
  assert.deepEqual(r.map[r.id], { name: 'Trips', named: 1000, created: 1000, books: {} });
});

test('checkName', () => {
  const r = S.create({}, 'To read', 1, compare);
  assert.deepEqual(S.checkName({}, '  ', compare), { error: 'empty' });
  assert.deepEqual(S.checkName({}, 'x'.repeat(61), compare), { error: 'long' });
  assert.deepEqual(S.checkName({}, 'x'.repeat(60), compare), { name: 'x'.repeat(60) });
  assert.deepEqual(S.checkName(r.map, 'to READ', compare), { error: 'taken' });
  assert.deepEqual(S.checkName(r.map, ' to read ', compare, r.id), { name: 'to read' });
  assert.deepEqual(S.create(r.map, 'to read', 2, compare), { error: 'taken' });
  const q = S.rename(r.map, r.id, 'to read', 5, compare);
  assert.equal(q.map[r.id].name, 'to read');
  assert.equal(q.map[r.id].named, 5);
  assert.equal(q.map[r.id].created, 1);
  assert.deepEqual(S.rename(r.map, r.id, '', 5, compare), { error: 'empty' });
});

test('setBook in, out, in', () => {
  const r = S.create({}, 'Holiday', 1, compare);
  let m = S.setBook(r.map, r.id, BOOK, true, 2);
  assert.equal(S.has(m, r.id, BOOK), true);
  assert.deepEqual(m[r.id].books[BOOK], { time: 2 });
  m = S.setBook(m, r.id, BOOK, false, 3);
  assert.equal(S.has(m, r.id, BOOK), false);
  assert.deepEqual(m[r.id].books[BOOK], { time: 3, out: true });
  m = S.setBook(m, r.id, BOOK, true, 4);
  assert.equal(S.has(m, r.id, BOOK), true);
  assert.deepEqual(m[r.id].books[BOOK], { time: 4 });
});

test('list counts only books in the library', () => {
  const r = S.create({}, 'Holiday', 1, compare);
  let m = S.setBook(r.map, r.id, BOOK, true, 2);
  m = S.setBook(m, r.id, 'gone', true, 3);
  m = S.setBook(m, r.id, 'left', false, 4);
  const l = S.list(m, (id) => id === BOOK || id === 'left', compare);
  assert.deepEqual(l, [{ id: r.id, name: 'Holiday', books: [BOOK] }]);
  assert.ok('gone' in m[r.id].books);
});

test('remove', () => {
  const r = S.create({}, 'Holiday', 1, compare);
  const m = S.remove(r.map, r.id, 5);
  assert.deepEqual(m[r.id], { deleted: 5 });
  assert.deepEqual(names(m), []);
  assert.equal(S.has(m, r.id, BOOK), false);
  assert.deepEqual(S.setBook(m, r.id, BOOK, true, 6), m);
  assert.deepEqual(S.setBook(m, 'nope', BOOK, true, 6), m);
  assert.ok(S.create(m, 'Holiday', 7, compare).id);
});

test('inputs are not mutated', () => {
  const r = S.create({}, 'Holiday', 1, compare);
  const m = deepFreeze(S.setBook(r.map, r.id, BOOK, true, 2));
  assert.equal(S.list(m, all, compare).length, 1);
  assert.equal(S.has(m, r.id, BOOK), true);
  assert.deepEqual(S.checkName(m, 'Other', compare), { name: 'Other' });
  assert.ok(S.create(m, 'Other', 3, compare).id);
  assert.equal(S.rename(m, r.id, 'Trips', 3, compare).map[r.id].name, 'Trips');
  assert.deepEqual(S.remove(m, r.id, 4)[r.id], { deleted: 4 });
  assert.equal(S.setBook(m, r.id, BOOK, false, 5)[r.id].books[BOOK].out, true);
});
