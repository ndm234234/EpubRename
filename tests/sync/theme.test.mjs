// reader/theme.js: the app's one theme (Auto, light, sepia, dark), shared by the catalog and the reader.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(here, '..', '..', 'reader', 'theme.js'), 'utf8');
const BG = { light: '#fdfcf8', sepia: '#f4ecd8', dark: '#161618' };

// a page: storage, the system's light/dark, <html>, the theme-color meta, window events
function page({ prefs, dark = false, throwing = false } = {}) {
  const m = new Map();
  if (prefs !== undefined) m.set('epubReader:prefs', typeof prefs === 'string' ? prefs : JSON.stringify(prefs));
  const localStorage = {
    getItem: (k) => { if (throwing) throw new Error('blocked'); return m.has(k) ? m.get(k) : null; },
    setItem: (k, v) => { if (throwing) throw new Error('blocked'); m.set(k, String(v)); },
  };
  const mq = { matches: dark, listeners: [], addEventListener(t, f) { this.listeners.push(f); } };
  const meta = { content: '' };
  const html = { dataset: {} };
  const winListeners = {};
  const ctx = {
    localStorage,
    matchMedia: (q) => { assert.equal(q, '(prefers-color-scheme: dark)'); return mq; },
    document: { documentElement: html, getElementById: (id) => (id === 'themeColor' ? meta : null) },
    getComputedStyle: (el) => ({ getPropertyValue: (n) => (n === '--bg' ? ' ' + (BG[el.dataset.theme] || '') : '') }),
    addEventListener: (t, f) => { (winListeners[t] ||= []).push(f); },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx, { filename: 'theme.js' });
  const system = (d) => { mq.matches = d; mq.listeners.forEach((f) => f({ matches: d })); };
  const storageEvent = (key) => (winListeners.storage || []).forEach((f) => f({ key }));
  return { T: ctx.AppTheme, m, html, meta, system, storageEvent };
}

test('stored: a known theme, else Auto', () => {
  assert.equal(page().T.stored(), 'auto');
  assert.equal(page({ prefs: { theme: 'sepia' } }).T.stored(), 'sepia');
  assert.equal(page({ prefs: { theme: 'blue' } }).T.stored(), 'auto');
  assert.equal(page({ prefs: 'not json' }).T.stored(), 'auto');
  assert.equal(page({ prefs: 'null' }).T.stored(), 'auto');
  assert.equal(page({ prefs: { theme: 'dark' }, throwing: true }).T.stored(), 'auto');
});

test('shown: Auto follows the system, the others are themselves', () => {
  const p = page();
  assert.equal(p.T.shown('auto'), 'light');
  p.system(true);
  assert.equal(p.T.shown('auto'), 'dark');
  assert.equal(p.T.shown('sepia'), 'sepia');
  assert.equal(p.T.shown('blue'), 'dark');   // unknown = Auto
});

test('next: auto, light, sepia, dark, auto; an unknown one goes to light', () => {
  const { T } = page();
  assert.deepEqual([...T.THEMES], ['auto', 'light', 'sepia', 'dark']);
  assert.equal(T.next('auto'), 'light');
  assert.equal(T.next('light'), 'sepia');
  assert.equal(T.next('sepia'), 'dark');
  assert.equal(T.next('dark'), 'auto');
  assert.equal(T.next('blue'), 'light');
});

test('label: the button title', () => {
  const { T } = page();
  assert.equal(T.label('auto'), 'Theme: Auto (as the system)');
  assert.equal(T.label('light'), 'Theme: light');
  assert.equal(T.label('sepia'), 'Theme: sepia');
  assert.equal(T.label('dark'), 'Theme: dark');
});

test('loading paints the shown theme and the status bar colour at once', () => {
  const p = page({ dark: true });
  assert.equal(p.html.dataset.theme, 'dark');
  assert.equal(p.meta.content, BG.dark);
  const s = page({ prefs: { theme: 'sepia' }, dark: true });
  assert.equal(s.html.dataset.theme, 'sepia');
  assert.equal(s.meta.content, BG.sepia);
});

test('loading with storage blocked still paints (Auto), no exception', () => {
  const p = page({ throwing: true, dark: true });
  assert.equal(p.html.dataset.theme, 'dark');
});

test('save keeps the other prefs, stamps the time and paints', () => {
  const p = page({ prefs: { theme: 'light', font: 120 } });
  const before = Date.now();
  p.T.save('dark');
  assert.deepEqual(JSON.parse(p.m.get('epubReader:prefs')), { theme: 'dark', font: 120 });
  assert.ok(+p.m.get('epubReader:prefsTime') >= before);
  assert.equal(p.html.dataset.theme, 'dark');
  assert.equal(p.T.stored(), 'dark');
});

test('save over missing or broken prefs makes a fresh object', () => {
  const p = page({ prefs: 'not json' });
  p.T.save('sepia');
  assert.deepEqual(JSON.parse(p.m.get('epubReader:prefs')), { theme: 'sepia' });
  const q = page({ prefs: '[1,2]' });
  q.T.save('sepia');
  assert.deepEqual(JSON.parse(q.m.get('epubReader:prefs')), { theme: 'sepia' });
});

test('watch: a system change and another tab changing the prefs call back; other keys do not', () => {
  const p = page();
  let n = 0;
  p.T.watch(() => n++);
  p.system(true);
  assert.equal(n, 1);
  p.storageEvent('epubReader:prefs');
  assert.equal(n, 2);
  p.storageEvent('epubLibrary:opened');
  assert.equal(n, 2);
});
