# Reader: text-only reading on touch devices — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On phones/tablets the book takes the whole screen; the toolbar and slider float in on a tap in the middle. Plus: "As in book" names the book's font, and the catalog copies a title on click.

**Architecture:** All in plain HTML/CSS/JS, no build step. A `touchui` class on `<body>` (set from `matchMedia("(hover: none) and (pointer: coarse)")`) turns `header` and `#seek` into absolutely positioned overlays; a `bars` class shows them. The reading area never changes size. The existing in-iframe tap handler (`touchEnd`) gets the three zones.

**Tech Stack:** `reader/reader.html` (epub.js), `make_index.py` (catalog template). Checked in headless Chrome over the DevTools protocol (Node 22, no packages) with touch emulation; a script in the session scratchpad, not in the repo (the repo's tests are Python only and need no browser).

**Spec:** `docs/superpowers/specs/2026-11-26-reader-immersive-touch-design.md`

## Global Constraints

- No new dependencies; the reader stays a single self-contained page.
- Nothing changes for mouse devices, narrow desktop windows included.
- The size of `#stage` / `#viewer` must be identical with bars shown and hidden.
- UI copy in English, like the rest of the reader and catalog.

## Review Focus

- Rotating the phone with bars open: the bars stay overlays, the place is kept.
- Tapping a link or selecting text in the middle third: the link works / selection is not cancelled, bars don't toggle.
- Opening Aa / contents / search / bookmarks from the bars: panels open above the bars; a tap on the page closes the panel first.
- A book whose CSS names a font that is embedded (`@font-face` in the book) vs. only a system name vs. nothing.
- Catalog opened over http on the LAN (no clipboard API): copying still works.

---

### Task 1: Touch layout and tap zones (`reader/reader.html`)

- [ ] Browser check (scratchpad `check_reader.mjs`): emulate a 390×844 touch device, open a book; assert `header` and `#seek` are not visible, `#where`/`#pct` are; `#viewer` size recorded.
- [ ] Tap the middle third → `body.bars`, header and seek visible, `#viewer` size unchanged, page (cfi) unchanged. Tap again → hidden.
- [ ] Tap right third in the upper half → next page (cfi changes), bars hide. Left third → previous.
- [ ] Bars open, drag the slider / pick a contents entry → bars hide after the move.
- [ ] Desktop (1280×800, mouse): header and seek visible as before, no `touchui` class.
- [ ] Implement: CSS for `body.touchui` (header `position:absolute; top:0`, `#seek` absolute above the footer, hidden with opacity/transform + `visibility` unless `body.bars`); `setBars(on: boolean)`; `touchEnd` zones by thirds of `#stage` width; hide bars on `relocated` caused by a move.
- [ ] Run the check, `python3 -m pytest -q tests`, commit.

### Task 2: Book's font under "As in book"

- [ ] Browser check: open Aa; the "As in book" button has a `<small>` with a non-empty name; with a book that embeds a font, the text ends with "in the book".
- [ ] Implement `bookFontName(doc: Document): string` — disable `#readerTypo`, read `getComputedStyle` of the most common `font-family` among `p` (fallback `body`), re-enable; first family, quotes stripped; generic → "default serif"/"default sans"; embedded if `doc.fonts` has a face with that family. Update on each `rendered` section and on opening Aa.
- [ ] Run the check, commit.

### Task 3: Copy the title in the catalog (`make_index.py`)

- [ ] Python test in `tests/test_index.py`: the built `index.html` contains the title copy handler (`copyTitle`) and the `.dtitle` class on the detail title.
- [ ] Implement in the template: `h3.dtitle` with `title="Click to copy"`, `cursor:pointer`; `copyTitle(text: string)` uses `navigator.clipboard.writeText`, falling back to a hidden textarea + `document.execCommand("copy")`; then `toast("Title copied")`.
- [ ] Browser check: open the catalog, open a book's page, click the title → clipboard (granted via CDP) holds the title, toast shown.
- [ ] Rebuild `Out/index.html`, run tests, update README's reader/catalog notes, commit and push.
