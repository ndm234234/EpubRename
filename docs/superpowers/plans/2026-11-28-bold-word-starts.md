# Bold Word Starts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-device "Bold word starts" switch in the reader that sets the first letters of each word in bold, with every stored address (reading place, bookmarks, notes, search, highlights) the same as without it.

**Architecture:** A new `reader/bold.js` (plain script like `theme.js`, global `BoldStarts`) wraps word starts in `<b class="bio">` and patches epub.js's `EpubCFI` once: addresses from ranges ignore `b.bio` (epub.js's own ignore class), addresses to ranges resolve on a plain copy of the chapter and are carried over by character position. `reader.html` calls it from its content hook and adds the checkbox.

**Tech Stack:** plain browser JS, epub.js (bundled `reader/epub.min.js`), node:test + vm for unit tests, headless Chrome checks in `tests/reader/`.

**Spec:** `docs/superpowers/specs/2026-11-28-bold-word-starts-design.md`

## Global Constraints

- Per device: stored as `epubReader:bold` (`true`/absent) through the reader's `store`, like `epubReader:scroll`; never in `epubReader:prefs` (synced).
- Bold letters: `n <= 3` → 1, `n === 4` → 2, otherwise `Math.ceil(n * 0.45)`; `n` = length of the word match.
- Word = `/[\p{L}\p{M}\p{N}­]+/gu` (letters, combining marks, digits, soft hyphen: a hyphenation point never splits a word).
- Left alone: text inside `pre, code, kbd, samp, tt, script, style`.
- Wrapper element: `<b class="bio">`; ignore class name `"bio"`.
- UI copy: checkbox label `Bold word starts`, note `The first letters of every word in bold, to lead the eye along the line. On this device only.` — on the **Font** tab, under the font buttons.
- Code, comments and docs in English; no real book titles or author names in tests or docs (use "Jane Doe - A Quiet Harbour" style).

## Review Focus

1. A bookmark or note made with the mode on, opened with it off (or the reverse): must land on the same word — Task 2 resolution tests + Task 4 cross-mode check.
2. Address pointing at an element, not text (an image, a footnote `<sup>`, a chapter start `/4/2`): must resolve to the same element with the mode on — Task 2 `element` test.
3. Switching the mode on/off while reading: the page must stay at the reading place — Task 4 check.
4. Big chapters (thousands of words) and many search hits on one chapter: address conversion must not walk the whole chapter per call — Task 2 caches per document (test: cache reused, rebuilt after `apply`).
5. Words with soft hyphens or combining marks (`й` decomposed): one word, one bold start — Task 1 test.

---

### Task 1: `reader/bold.js` — wrapping and unwrapping

**Files:**
- Create: `reader/bold.js`
- Test: `tests/reader/bold-unit.mjs` (headless Chrome, like the other `tests/reader/` checks: the code needs a real DOM and the project has no jsdom)

**Interfaces:**
- Produces (global `window.BoldStarts`):
  - `prefix(n: number) -> number` — bold letter count.
  - `apply(doc: Document, on: boolean) -> void` — wraps (idempotent: no-op if `doc` already has `b.bio`) or unwraps (`replaceWith(...childNodes)` then `body.normalize()`); bumps the document's address cache (Task 2).
  - `CLASS = "bio"`.

- [ ] **Step 1: Write the failing check `tests/reader/bold-unit.mjs`**

Opens `${BASE}/reader/reader.html` (no book), then evaluates in the page on `document.implementation.createHTMLDocument("")` documents. Assertions:

```js
// prefix
[[1,1],[2,1],[3,1],[4,2],[5,3],[7,4],[10,5]].forEach(([n,k]) => ok(BoldStarts.prefix(n) === k, `prefix(${n}) = ${k}`));
// body "<p>Съешь же этих <code>longword</code> мягких</p><pre>keep me</pre>"
// after apply(doc, true): p.innerHTML ===
//   '<b class="bio">Съе</b>шь <b class="bio">ж</b>е <b class="bio">эт</b>их <code>longword</code> <b class="bio">мяг</b>ких'
// pre unchanged; apply(doc, true) again → same innerHTML (idempotent)
// "пере­ход" → one <b class="bio"> (prefix(9)=5 → "пере­"), the rest "ход"
// apply(doc, false) → body.innerHTML equal to the original, and p.childNodes.length === 1 (normalized)
```

- [ ] **Step 2: Run it, expect FAIL** — `node tests/reader/bold-unit.mjs http://127.0.0.1:$PORT` (server as in `tests/reader/run.sh`): `BoldStarts is not defined`.
- [ ] **Step 3: Implement `reader/bold.js`** (IIFE, `"use strict"`, header comment in the project's style) and add `<script src="bold.js"></script>` next to `theme.js` in `reader/reader.html`. Text nodes collected with a TreeWalker first, then replaced (no mutation while walking).
- [ ] **Step 4: Run it, expect `ALL OK`.**
- [ ] **Step 5: Commit** `reader: bold word starts, the wrapping (bold.js)`.

### Task 2: Addresses unchanged by the bold

**Files:**
- Modify: `reader/bold.js`
- Test: `tests/reader/bold-unit.mjs` (extend; now opens a book: `Jane Doe` style fixture or the book passed as argv[3])

**Interfaces:**
- Consumes: `apply` from Task 1.
- Produces: `BoldStarts.patchCfi(CFI: Function) -> void` — called once with `ePub.CFI` before the first rendition (idempotent). After it:
  - `CFI.prototype.fromRange(range, base, ignoreClass)` and `fromNode(node, base, ignoreClass)` default `ignoreClass` to `"bio"`.
  - `CFI.prototype.toRange(doc, ignoreClass)`: if `doc` has no `b.bio` → original. Else resolve with the original `toRange` on the document's plain copy, then map each boundary to `doc`.

Mapping (the part the signature doesn't determine):

```
plain copy: doc.implementation.createHTMLDocument(""), documentElement replaced by importNode(doc.documentElement, true),
            then every b.bio unwrapped and body.normalize().
Per document cache (WeakMap doc → {plain, plainTexts, plainStart: Map<Text,number>, realTexts, realStarts: number[],
            plainEls: Element[], realEls: Element[]}), built lazily, dropped by apply().
            plainEls / realEls: all elements in document order; realEls without b.bio → same length, index i ↔ i.
boundary (container, offset) in plain → real:
  container is Text            → g = plainStart(container) + offset → real text node by binary search over realStarts
                                  (at a node's end prefer that node, so a span's end stays in its word)
  container is Element, child = container.childNodes[offset]:
     child is Element          → real = realEls[plainEls.indexOf(child)]; boundary (real.parentNode, index of real in it)
     child is Text             → as Text with offset 0
     no child (offset = end)   → real container = realEls[plainEls.indexOf(container)], offset = its childNodes.length
```

- [ ] **Step 1: Extend the check** (book open, `prefs`-free, in 3 chapters with ≥ 3 pages; ~150 words each, every k-th word as in the spike):
  - `same address`: for each word, `new ePub.CFI(range, base).toString()` with the mode off equals the one with it on, for the collapsed start and the whole word.
  - `resolves`: each address saved with the mode off, `rendition.getRange(cfi)` with it on gives the same character position (start) and `toString() === word` (span). Must be 100%, not "most".
  - `element`: for every `img`, `sup` and the first `p` of the chapter: `new ePub.CFI(el, base).toString()` same both ways, and resolving it with the mode on gives a range whose `startContainer.childNodes[startOffset]` is the corresponding element (or the range selects it).
  - `cache`: two `getRange` calls in a row reuse the cache (expose `BoldStarts._cacheBuilds` counter: +1 only once); after `apply(doc,false); apply(doc,true)` the next call rebuilds.
- [ ] **Step 2: Run, expect FAIL** (`patchCfi` missing).
- [ ] **Step 3: Implement `patchCfi`**, call `BoldStarts.patchCfi(ePub.CFI)` in `reader.html` right where the book is opened (before `makeRendition`).
- [ ] **Step 4: Run, expect `ALL OK`.**
- [ ] **Step 5: Commit** `reader: bold word starts keep every address (EpubCFI patched)`.

### Task 3: The switch

**Files:**
- Modify: `reader/reader.html` — Font pane markup (after `#faceSeg`), `applyTypo` (call `BoldStarts.apply(doc, boldPref)` last), a `boldPref` next to `scrollPref` (`store.get("epubReader:bold") === true`), `showTypo` (checkbox state), change handler.
- Test: `tests/reader/bold.mjs` (new, phone size, as `status.mjs`)

**Interfaces:**
- Consumes: `BoldStarts.apply`, `patchCfi` (Tasks 1–2).
- Produces: `#boldStarts` checkbox; `boldPref: boolean`.

Change handler: set `boldPref`, `store.set("epubReader:bold", boldPref)` (or remove when false — follow `scrollText`'s way), `apply` on every `rendition.getContents()` document, then `relayout()`.

- [ ] **Step 1: Write `tests/reader/bold.mjs`** — checks:
  - the checkbox sits in the Font pane (`#typo [data-pane=font] #boldStarts`);
  - tap it on → `document.querySelectorAll` in the chapter has `b.bio`; `localStorage["epubReader:bold"]` is `true`; `JSON.parse(localStorage["epubReader:prefs"])` has no bold key;
  - **place kept:** on page 3 of a chapter, the reading place (`anchor`) is on the shown page (`__pageOf(anchor) === shown`, helpers from `helpers.mjs`) after switching on, and again after switching off;
  - **cross-mode:** add a bookmark with the mode on, switch off, open it (`goToMark`): the shown page holds its word; the reverse;
  - reopening the reader with `epubReader:bold` set shows the bold at once.
- [ ] **Step 2: Run, expect FAIL** (no `#boldStarts`).
- [ ] **Step 3: Implement** (label/note copy from Global Constraints).
- [ ] **Step 4: Run, expect `ALL OK`.**
- [ ] **Step 5: Commit** `reader: "Bold word starts" switch, per device`.

### Task 4: The suite with the mode on, docs

**Files:**
- Modify: `tests/reader/nav.mjs` (env `BOLD=1` → sets `epubReader:bold` before load, like the prefs), `tests/reader/scroll.mjs` (same env), `tests/reader/run.sh` (run `bold-unit.mjs`, `bold.mjs`, and `BOLD=1 FACES=literata SIZES=100 nav.mjs` + `BOLD=1 scroll.mjs` per book), `README.md` (feature next to *Scroll text* ~line 211; "What is not synced" ~line 358; test list ~line 632).

- [ ] **Step 1:** Add the `BOLD` env to `nav.mjs` and `scroll.mjs`; run both with `BOLD=1` on a book: expect `ALL OK` (page turns without skips, place kept on font changes, search hits on the shown page).
- [ ] **Step 2:** Wire into `run.sh`; full run on one book: `tests/reader/run.sh "<book>"` → exit 0.
- [ ] **Step 3:** README: the switch (where, what, per device), the not-synced line, the new test files.
- [ ] **Step 4: Commit** `reader: the checks with bold word starts; README`.
