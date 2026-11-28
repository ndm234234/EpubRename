# Folders Shown on a Book Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The catalog shows which folders a book is in: a plain line on its card, tappable chips on its page.

**Architecture:** All in the catalog page written by `make_server.py`. One helper gives a book's live
folders by name from the stored map; `card()` adds a line, `openDetail()` adds a chip row, and
`render()` refreshes the open book page's row so a tick in the picker shows at once.

**Tech Stack:** plain JS/CSS inside `make_server.py`'s HTML template; browser checks over CDP (`tests/reader/cdp.mjs`).

**Spec:** `docs/superpowers/specs/2026-11-28-book-folders-shown-design.md`

## Global Constraints

- UI words English; nothing shown for a book in no folder; nothing shown if `shelves.js` didn't load.
- Folder order: `liveFolders(map)` (the catalog's collator), the same as the strip.
- Card line: the open folder left out; at most two names, then `+N`; one line with ellipsis; `title` = all names joined by `, `; not a link.
- Not the `.chip` class for the new elements (on a phone `.chip span { display: none }`).
- No commit until the owner has looked (the owner's instruction for this work).

## Review Focus

- A very long folder name on a narrow phone card: the line stays one line inside the card (no sideways page scroll).
- A chip tapped on a book page opened from inside another folder: the target folder opens, and Back goes to All books (one folder step).
- A folder renamed or deleted while the book page is open: the chips follow on the next `render()`.
- The Unfiled view: no card line.
- Folder names with HTML-like text (`<b>`): shown as text (`el()` sets text, never HTML).

---

### Task 1: Folders on the card and the book page

**Files:**
- Modify: `make_server.py` (CSS near `.meta`/`.dbody .meta`; `card()`; `openDetail()`; `render()`; new helpers next to `liveFolders`)
- Create: `tests/reader/book-folders.mjs` (own library, like `folders.mjs`)
- Modify: `tests/reader/run.sh` (run it after `folders.mjs`)

**Interfaces:**
- Produces: `foldersOf(map, b) -> [{id, name}]` (live folders holding `b.id`, by name);
  `cardFolders(map, b) -> Element|null` (`div.bfolders`); `pageFolders(map, b) -> Element|null`
  (`div.dfolders` of `button.ftag[data-folder]`); `goFolder(id)` (closes the book page, then opens the folder).

- [ ] **Step 1: Write `tests/reader/book-folders.mjs`** — phone 390×844 touch, seeded map
  `{ f1: "To read" [1,2,3], f2: "Holiday" [1], f3: "Kids" [1], f4: "<b>x</b>" [2] }`:
  - book 4 (no folder): no `.bfolders` on its card; its page has no `.dfolders`.
  - book 1 card text `Holiday · Kids +1`, title `Holiday, Kids, To read`; book 2 `<b>x</b> · To read`.
  - `openFolder('f1')`: book 1 card `Holiday · Kids`, book 3 has no line; Unfiled: no `.bfolders` anywhere.
  - no card's line is taller than one line, page `scrollWidth <= innerWidth` (a 60-char name added).
  - book 1 page chips `Holiday | Kids | To read`; tick Holiday off in the picker → chips `Kids | To read` and the card behind changes.
  - from folder f1, book 1 page, tap chip Kids → page closed, count `"1 book in Kids"`, `history.state.folder === f3`; Back → All books.
  - computer 1280×800: chips shown, the card line present.

- [ ] **Step 2: Run it, expect FAIL** — `node tests/reader/book-folders.mjs` (needs `Out/` books, google-chrome).

- [ ] **Step 3: Implement** the helpers above; `render()` replaces the open page's `.dfolders` (or inserts it after `.meta`) when `current` is set.
  `goFolder(id)`: steps as in `requestClose`; with steps, a one-off `popstate` listener calls `openFolder(id)` after `history.go(-steps)`; without, `openFolder(id)` at once.

- [ ] **Step 4: Run it, expect ALL OK**; also `node tests/reader/folders.mjs` stays ALL OK.

- [ ] **Step 5: No commit** — the owner looks first.
