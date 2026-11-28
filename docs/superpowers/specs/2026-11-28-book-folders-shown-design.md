# Folders shown on a book: which folders it is in, at a glance

Follows `2026-11-27-folders-design.md`.

## Intent

Today a person can only find out which folders a book is in by opening its page, pressing
**Folders** and looking at the ticks. A book may be in several folders, and that should be plain to
see without any of that: on the book's card in the catalog's grid, and on the book's page.

## Goals

- **Card in the grid:** a short line under the author with the names of the book's folders, e.g.
  `To read · Holiday +1`.
- **Book page:** every folder the book is in, as a chip under the meta line; a tap on a chip opens
  that folder in the catalog.
- Both update at once when the book is ticked into or out of a folder, renamed, or a folder is
  deleted (here or from another device through sync).

## Non-goals

- Search matching folder names.
- Changing which folders a book is in from the card or the chips (that stays the Folders button).
- Any change to the server, the sync document or `shelves.js`'s data.

## Anti-goals

- The catalog getting busier for people who don't use folders: a book in no folder shows nothing,
  on the card and on the page.
- On a phone, a tap meant for the book landing on a folder instead.

## Approaches

The only sensible way: the catalog already has the person's folders (`shelves()`,
`liveFolders`) and each book's `id`; while drawing, it works out each book's folders and writes
them on the card and the page. The folders can't be written into the catalog by `make_server.py`
(they belong to each person), and keeping a list of folders on each book would be a second copy
to keep in step.

The choice was whether the names can be tapped:

- **A. Plain text on the card, chips you can tap on the book page — chosen by the owner.** The
  whole card still opens the book page; only the page jumps to a folder.
- B. Tappable everywhere — dropped: small targets next to the cover and title on a phone.
- C. Tappable nowhere — dropped: the page could not take you to a folder.

## How it works

- **Order:** folders by name with the catalog's collator, the same as the strip.
- **Card line:** shown only when the book is in at least one folder. While a folder is open, that
  folder is left out of the line (every card there is in it); in *Unfiled* no card has a line.
  Up to two names, then `+N` for the rest; one line, the end cut off with an ellipsis if it is too
  long; the full list (all folders, including the open one) in its `title`. Muted small text with
  the folders' icon (`i-shelf`), not a link: a tap on it does nothing, like a tap on the meta line
  today (the cover and the title open the book page).
- **Book page:** under the meta line, one chip per folder (all of them, including the open one),
  wrapping onto more rows as needed. A tap closes the book page and opens that folder (the same as
  tapping it in the strip, with the same history step). Nothing shown when the book is in no
  folder; the Folders button stays as it is (*my call*: no count on it, the chips already say it).
- When the picker on the book page changes a tick, the chips under the meta line and the card
  behind change at once.
- If `shelves.js` didn't load, neither appears (as with the Folders button today).

## Left to the builder

- Exact look of the card line and the chips within the existing styles (the strip's chips are a
  starting point, smaller).
- Tests in the browser tests (`tests/reader/`) on a phone-sized and a computer-sized screen: a
  book in 0, 1, 3 folders; the open folder left out of the card; a chip on the page opens its
  folder; ticking in the picker updates both places.
