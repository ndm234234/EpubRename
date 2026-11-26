# Reader: text-only reading on phones and tablets

## Intent

On a phone or tablet the open book should take the whole screen, so reading is as
comfortable as possible. The toolbar and the progress slider are only needed for
navigation, so they stay hidden until asked for.

## What the reader sees (touch devices)

- When the book opens: the page text, and under it only the thin status line
  "page N of M in chapter" with the percentage read. No toolbar at the top, no slider.
- Tap in the middle third of the screen: the toolbar (catalog, contents, Aa,
  bookmarks, search, theme…) appears at the top and the slider at the bottom,
  **over the text**. The text is not squeezed or re-laid out. Another tap in the middle
  hides them.
- Tap in the left or right third (full height): previous / next page. This replaces
  today's rule where only the lower half of the page turns pages.
- The bars hide by themselves after any move in the book: a page turn (tap or swipe),
  the slider, the contents, search or a bookmark.
- While Aa, contents, search or bookmarks is open, a tap on the page only closes it,
  as today.

## Goals, non-goals

- Goal: maximum text area while reading; navigation one tap away.
- Non-goal: any change on computers (mouse), including a narrow browser window.
- Anti-goal: the page jumping or the reading place moving when the bars show or hide.

## Constraints

- The size of the reading area must never change when bars show or hide: epub.js
  re-paginates on resize and the place can shift (see the footer-height comment in
  `reader/reader.html`).
- Links, text selection and long presses on the page keep working.

## Approaches

- **A (picked): the bars float over the book.** The status line stays in the normal
  layout and is always visible; the toolbar and the slider become overlays that slide in
  and out. The reading area keeps one size. The existing in-page tap handler is extended
  with the middle zone.
- B: everything, the status line too, over the text. Gains ~20px but the status line
  would cover the last line of text unless room is reserved in the book's margins, which
  differ from book to book. Rejected.

## Decided

- Touch devices are detected as `(hover: none) and (pointer: coarse)` (phones and
  tablets). *My call*, agreed in conversation that tablets are included.
- The auto-hide rules and the tap zones above (the user's choice for the thirds).

## Left to the builder

- Animation, shadows and the exact look of the floating bars (match the current theme
  tokens, readable in light / sepia / dark).
- How the slider overlay sits relative to the status line (above it is expected).
- Taps on the page margins outside the book iframe behave the same as taps on the text.

## Also in this change

### Reader: which font "As in book" is

In Aa → Font, the "As in book" button gets a second line with the font the book itself
uses for its main text (as the other font buttons show a sample line), so the reader
knows what they would get.

- The name is taken from the book's own styles for ordinary paragraphs of the open
  chapter (the reader's own font override is switched off while measuring), and updates
  when the chapter changes.
- *My calls:* the first font of the book's list is shown (e.g. "Charis SIL"), with
  "in the book" when the book carries that font file itself; if the book names only a
  generic family or nothing, "default serif" / "default sans" is shown. Whether a system
  font named by the book really exists on the device can't be checked by a browser, so
  it is shown as the book names it.

### Catalog: copy a book's title

In the catalog, on the page that opens when a book is clicked (cover, title,
description), a click / tap on the title copies the title to the clipboard and a short
"Title copied" message appears.

- *My calls:* only the title is copied (not the author). The title shows it can be
  clicked (pointer cursor, a hint on hover). Works when the catalog is opened from a
  file and from the local network (falls back to the old copy method where the
  clipboard API isn't allowed).
