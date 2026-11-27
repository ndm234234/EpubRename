# Folders: each person's own collections of books in the catalog

## Intent

A library shared by several people gets long. Each person wants to sort it their own way: put
books into folders they name themselves ("To read", "Holiday", "For the kids") and see just one
folder at a time. A folder is a **collection**, not a place on disk: one book may be in several
folders, and taking a book out of a folder or deleting a folder never touches the book.

Folders are **per person**: they live in the person's account (see
`2026-11-26-library-sync-design.md`) and follow them to all their devices. Others don't see them.
Putting books in folders and taking them out must be easy **on a phone**.

## Goals

- Create a folder with a name; rename it; delete it (the books stay in the library).
- Put a book into any number of folders and take it out again, from the phone in a couple of taps.
- Open a folder in the catalog: the grid shows only its books; search, sort and "Group by author"
  work inside it as they do on the whole library.
- Signed in: folders are synced across all devices of the account, the same on every kind of
  device (phone, tablet, computer). Changes made offline go when the server is back.
- Not signed in (or on a plain `http://` address where there is no sync): folders work on that
  device alone, in its local storage, and join the account's on the first sign-in (nothing lost on
  either side, as with bookmarks).
- A book stays in its folders when its file is renamed (`epub_rename.py`) or moved to another
  subfolder of the library.

## Non-goals

- Folders on disk; any change to the book files or the library's subfolders.
- Folders shared between people, or folders made by the library owner for everyone.
- Folders inside folders.
- Choosing several books at once (long press / multi-select). May come later.
- A hand-made order of books inside a folder (dragging). The catalog's sort is used.
- Folder controls in the reader. May come later.
- "Keep this folder on the device" in the offline panel. May come later.
- One device used by several people with different accounts (owner: everyone has their own
  device). Folders behave like bookmarks today: one set per device, kept on sign-out, merged into
  whichever account signs in. `sync.js` gets no per-account local storage.

## Anti-goals (a failure even if it "works")

- A book taken out of a folder, or a deleted folder, coming back from another device.
- One person seeing or changing another person's folders on their own devices or through the
  server.
- The catalog getting busier for people who never use folders.
- A folder silently losing books after a file is renamed.
- Any change to the sync servers' behaviour, or a second copy of server logic to keep in step.

## Approaches

- **A. Folders as a new part of the account's sync document — chosen.** The servers store each
  account's document as an opaque blob, so they don't change at all (both the PowerShell and the
  Python one). Merging lives in `sync-merge.js` next to bookmarks, which already solve the same
  problem (union by id, tombstones so deletions don't come back). Without an account it is plain
  local storage, like "recently opened".
- B. Real folders on disk (moving or copying files) — dropped: the library is shared, so one
  person's folders would be everyone's; copies waste space and a book in several folders becomes
  several books; the catalog and sync key books by file path in places, so moves break things.
- C. A separate store on the server (own endpoint and files) — dropped: the same logic written
  twice, in PowerShell and Python, for nothing that A doesn't already give.
- D. Tags written into the EPUB's own metadata — dropped: changes the books, and is shared by all.

The owner confirmed: nothing on disk changes; folders exist only as lists.

## How a book is identified: its id, not its path

Folders store books by the **same id the reader already uses** for places and bookmarks
(`reader.html`, `openBook`): the text of the book's first `dc:identifier`, else its `dc:title`,
else the file name. Today the catalog only knows the path (`href`), which changes when a file is
renamed or moved; the id survives that.

- `make_server.py` adds `id` to each book in the catalog's data, computed exactly as the reader
  computes it from the same file: epub.js takes the text of the **first `dc:identifier` element
  in the document** (not the one `unique-identifier` points to), untrimmed; the same for
  `dc:title`; the file name is the last part of the path, decoded. The Python side must match
  this byte for byte (a test opens real fixtures in both and compares), otherwise a folder made
  from the catalog and a place saved in the reader would name the same book differently. (Builder
  check: whether `text_of`'s whitespace folding differs from epub.js on any real file.)
- Two files with the same id (e.g. two copies of one edition) go into and out of folders together.
  Accepted: they are the same book to the reader as well (shared place and bookmarks).
- A folder may name a book that is not in the library (deleted, or not yet copied to this
  library). It is kept but not shown and not counted; it shows again if the book comes back.

## The data

Local storage: one key, `epubLibrary:shelves`, holding the same shape as the document's part.

In the sync document (`v` stays 1; a new top-level part. An older cached client keeps an unknown
part from the server as it is, and has none of its own to clash with, so it loses nothing):

```json
"shelves": {
  "<folder id>": { "name": "To read", "named": 1764150000000, "created": 1764150000000,
                   "books": { "<book id>": { "time": 1764150000000 },
                              "<book id>": { "time": 1764150100000, "out": true } } },
  "<folder id>": { "deleted": 1764150200000 }
}
```

- `<folder id>`: random, made when the folder is created (creation time + random part).
- `named`: when the name was last set. `books`: one entry per book ever put in; `out: true` once
  taken out (a tombstone, so it isn't brought back).
- Deleting a folder replaces it with `{deleted: time}`.

## Merging (in `sync-merge.js`, unit tested like bookmarks)

- Folders: union by folder id.
- **A deleted folder stays deleted**: if either side has `deleted`, the result is the tombstone
  (latest `deleted` time), whatever the other side did. Deletion asks for confirmation, so there is
  no undo to support.
- Name: the later `named` wins.
- Books: per (folder, book), the later `time` wins, in or out.
- Equal times: a fixed rule (as the existing merge does), so both sides end up identical.
- Tombstones (`out` entries and deleted folders) older than about a year may be dropped, by the
  existing `pruneTombstones`.
- Two devices that each create a folder with the same name before syncing end up with two folders
  of that name. Rare; accepted. The person can delete one.

`sync.js` collects `epubLibrary:shelves` into the document and writes it back, and reports
`shelves: true` to `onRemoteChange` listeners when it changed; the catalog then redraws (keeping
the open folder if it still exists, else showing all books).

## Interface (catalog only)

The catalog's words stay English like the rest of it; the UI word is **"Folders"** (my call).
The "Open file" chip already uses a folder icon; folders get a different one, e.g. a stack of
books or a tag (builder's call), so the two aren't confused.

**Folder strip.** A row of chips under the header: `All books` · `To read 12` · `Holiday 5` … ·
`＋`. On a phone it scrolls sideways with a finger and doesn't wrap; the chosen chip stays in view.
Folders are listed by name (the catalog's collator), the same order on every device. The strip is
**hidden while the person has no folders**, so the catalog looks as it does today for people who
don't use them; the first folder is made from a book's page (below).

**Opening a folder.** Tap its chip: the grid shows its books, the count reads e.g. `12 books in
To read` (`3 of 12 in To read` while searching). Search, sort and grouping apply inside it. The
phone's Back button goes back to All books (the folder is a history step, like a book's page).
An empty folder says: "No books here yet. Open a book and choose Folders to add it." Which folder
is open is remembered by that catalog on that device, like its scroll place, and not synced.

**Putting a book in / taking it out.** On the book's page (which a tap on a cover already opens),
next to Read and Save, a **Folders** button. It opens a list of the person's folders with a
check box each (ticked = the book is in it) and, at the end, **＋ New folder** (a name field; the
new folder is created ticked). Ticking and unticking takes effect at once, no Save button. On a
phone the list comes up from the bottom of the screen over the book page; on a computer it is a
popover like the Account and Offline ones. One place for adding, removing, and being in several
folders.

**Managing a folder.** When a folder is open, its chip shows `⋯`: **Rename** and **Delete
folder**. Delete asks first: "Delete the folder "To read"? The books stay in the library." The
strip's `＋` makes a new empty folder (name field) and opens it.

Names: trimmed, not empty, at most ~60 characters; the same name as another of the person's
folders (ignoring case) is refused locally with a short message.

## Left to the builder

- Exact layout, icon and spacing of the strip, the folder list and the `⋯` menu within the
  existing styles; the bottom-sheet look on phones.
- Whether the strip sits inside the sticky header or just below it.
- Folder id format; name length limit; how long tombstones are kept.
- Tests: merge unit tests next to `tests/sync/merge.test.mjs`; the client's collect/apply in
  `tests/sync/client.test.mjs`; the catalog in the browser tests (`tests/reader/`) on a phone-sized
  screen; a Python test that the catalog's `id` matches the reader's on the test fixtures.
