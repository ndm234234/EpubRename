# One theme for the whole app (catalog and reader)

A short design: a small change.

## Intent

In the evening, lying in bed, the screen shouldn't glow. Today only the reader has themes (light,
sepia, dark); the catalog just follows the system's light/dark. The owner wants the theme to be the
app's: chosen once, the catalog and the reader both wear it, and an **Auto** option follows the
system (so a phone that goes dark on a schedule takes the app with it).

## Goals

- Four themes everywhere: **Auto** · light · sepia · dark. Auto = the system's light or dark
  (`prefers-color-scheme`), and switches live when the system does, also inside an open book.
- The catalog gets a theme button in its top bar, next to the account button (owner), the same
  icon as the reader's; each press goes Auto → light → sepia → dark → Auto. The reader's button
  cycles the same four.
- One setting: changing it in the catalog changes the reader and the other way round, on that
  device and, signed in, on the account's devices of the same kind (as the reader's theme syncs
  today: phones share one, computers another).
- New users start on Auto (in both the catalog and the reader).

## Non-goals

- A theme by clock time chosen in the app (Auto leaves that to the system's own schedule).
- A brightness or warmth slider; more themes.
- Changing how the book's own page is printed (stays black on white).

## Anti-goals

- A white flash when a page opens at night: the theme must be set before the first paint, in both
  the catalog and the reader.
- The catalog and the reader showing different themes on the same device.
- Anything changed in the sync servers or the sync merge.

## Approaches

- **A. The catalog uses the reader's setting — chosen (owner).** The theme stays in
  `epubReader:prefs.theme` (with the font size and face), already synced per kind of device. The
  catalog reads and writes it there (it is the same site, so the same local storage) and redraws when
  sync reports `prefs` changed. A new value `"auto"` is added. An older cached reader that doesn't
  know `"auto"` shows light instead; nothing breaks.
- B. A separate app-wide setting outside the reader's prefs — dropped: needs moving the old value
  and teaching sync a new part, for nothing A doesn't give.

## Decided

- Values: `"auto" | "light" | "sepia" | "dark"`; the default (`PREFS.theme`) becomes `"auto"`.
  *My call:* a stored theme is kept as it is, so people who already have `"light"` saved stay on
  light until they press the button (there's no telling a chosen light from the old default).
- The catalog keeps its own light and dark palettes and gets a sepia one in the reader's sepia
  colours (`#f4ecd8` background, `#3b2f1e` text, sienna accent); its `theme-color` metas follow the
  shown theme, as the reader's does. *My call.*
- Changing the theme in the catalog counts as a prefs change (sets `epubReader:prefsTime`, tells
  `Sync.changed()`), exactly as the reader's button does.

## Left to the builder

- How the button shows which theme is on (at least its title and accessible name, e.g. "Theme:
  Auto"); whether Auto gets its own icon.
- How the reader applies the resolved theme to the book's pages under Auto (epub.js theme select on
  a `matchMedia` change) and to its own `theme-color`.
- Service-worker cache versions so installed apps pick up the change.
- Tests: the browser tests (`tests/reader/`) for the catalog button cycling and storing the value,
  the reader picking it up, and Auto following an emulated `prefers-color-scheme` change.
