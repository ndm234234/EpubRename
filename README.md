# epub-rename

Two small Python scripts to tidy up an EPUB collection:

- **`epub_rename.py`** — copies books from `In/` into `Out/<Author>/<Author> - <Title>.epub`,
  using the author and title stored inside each book, and verifies every copy.
- **`make_index.py`** — builds `index.html`, an offline catalog of a book folder
  with covers, search, sorting and a built-in reader. It runs automatically at the end of `epub_rename.py`
  and can also be used on its own for any folder.

No dependencies besides Python. [Pillow](https://pypi.org/project/pillow/) is optional
(used to shrink covers for the catalog).

## Quick start

```bash
git clone https://github.com/<your-username>/epub-rename.git
cd epub-rename
mkdir In                      # put your .epub files here
python3 epub_rename.py        # sorts books into Out/ and builds Out/index.html
```

Then open `Out/index.html` in a browser.

## Requirements

- Python 3.10 or newer
- Optional: Pillow — `pip install pillow` (without it covers are copied at full size)

Works on Linux, macOS and Windows.

## epub_rename.py

```bash
python3 epub_rename.py
```

`In/` and `Out/` are taken from the **current folder**: run the script where your `In/`
folder is. `Out/` is created automatically.

```
In/                                  Out/
├── a8f3k2.epub          ──►         ├── Clara Whitmore/
├── x91bd0.epub                      │   ├── Clara Whitmore - The Lantern Keeper.epub
└── q7mm41.epub                      │   └── Clara Whitmore - Paper Harbors.epub
                                     ├── Oskar Brandt, Mila Fenwick/
                                     │   └── Oskar Brandt, Mila Fenwick - The Clockwork Orchard.epub
                                     ├── index.html
                                     └── .index/covers/
```

### What it does

- **Reads metadata** (`dc:creator`, `dc:title`) from each EPUB.
- **Safe file names** — characters forbidden on Windows/Linux (`< > : " / \ | ? *`, control
  characters) are removed, as well as trailing dots/spaces and reserved names (`CON`, `NUL`, …).
  Long names are trimmed to fit file system limits.
- **Unifies author names**:
  - `Whitmore, Clara` → `Clara Whitmore` (exactly one comma means "Last, First");
  - `WHITMORE CLARA` → `Whitmore Clara` (all caps → normal case);
  - the same author written with a different word order, case or `ё`/`е`
    goes into one folder; an existing folder in `Out/` wins.
- **Co-authors** — all authors are used, separated by commas; with more than two
  authors the first two are kept followed by `и др.` ("et al.").
- **Source files are never modified** — books in `In/` are only read; their SHA-256 is
  checked before and after copying. The script refuses to run if `In` and `Out` overlap.
- **Every copy is verified** byte for byte (SHA-256 and size); a bad copy is removed.
- **Re-running is safe**:
  - identical file already in `Out/` → skipped, not rewritten;
  - different file with the same name → overwritten.

### Output

While working, a single progress line is shown. At the end the results are grouped:

```
📚 Found 5 epub file(s) in In/

✅ New (1)
  [1/1] d.epub  →  Theo Lindqvist/Theo Lindqvist - Notes from the Quiet Hour.epub

🔄 Overwritten — existing file was different (1)
  [1/1] b.epub  →  Clara Whitmore/Clara Whitmore - Paper Harbors.epub

🟰 Identical — already in Out, skipped (2)
  [1/2] a.epub  →  Clara Whitmore/Clara Whitmore - The Lantern Keeper.epub
  [2/2] c.epub  →  Ada Merriweather/Ada Merriweather - The Salt Road.epub

❌ Errors (1)
  [1/1] zz.epub  —  can't read metadata: File is not a zip file

✨ Done:  ✅ new 1  ·  🔄 overwritten 1  ·  🟰 identical 2  ·  ❌ errors 1
🔒 All copies verified by SHA-256, source files untouched
```

Exit code: `0` — no errors, `1` — bad folders, `2` — some books failed.

### Settings

Constants at the top of `epub_rename.py`:

| Constant            | Default            | Meaning                                   |
|---------------------|--------------------|-------------------------------------------|
| `IN_DIR`, `OUT_DIR` | `In`, `Out`        | source and destination folders            |
| `UNKNOWN_AUTHOR`    | `Unknown Author`   | folder for books without an author        |
| `AUTHOR_SEPARATOR`  | `", "`             | between co-authors                        |
| `MAX_AUTHORS_SHOWN` | `2`                | more authors than this → shortened        |
| `ET_AL`             | `" и др."`         | suffix for shortened author lists         |

## make_index.py

```bash
python3 make_index.py [FOLDER] [--title "My Library"]
```

Scans `FOLDER` (default: `Out` in the current folder) recursively and writes:

- `FOLDER/index.html` — the catalog page;
- `FOLDER/.index/covers/` — small cover copies (reused on the next run; covers of
  removed books are cleaned up);
- `FOLDER/.index/reader/` — the reader (copied from `reader/` next to the script);
- `FOLDER/Open Library.bat`, `FOLDER/Open Library.sh` — launchers (from `launchers/`).

EPUB files are only read. Keep `.index/` next to `index.html` when moving the catalog.

### Catalog features

- Cover grid; books without a cover get a generated placeholder.
- Title, authors, series, year, publisher, file size; expandable description.
- Instant search across title, authors, series, description and file path
  (case-insensitive, `ё` = `е`, all words must match). `/` focuses search, `Esc` clears it.
- Click an author to see all their books.
- Sort by author, title, recently added or size; optional grouping by author.
  Settings are remembered.
- Click a **cover** or a **title** to read the book in the browser.
- **⬇ Save as…** under the cover saves the EPUB file: Chrome/Edge (via the launcher) show
  a dialog to choose the folder; other browsers download it as usual.
- **📂 Open file…** opens any EPUB from disk in the reader.

### Reader

A single offline page (`reader/reader.html`) built on [epub.js](https://github.com/futurepress/epub.js)
and [JSZip](https://stuk.github.io/jszip/) (both bundled, no internet needed). Nothing is uploaded.

- Paging: `←` / `→`, `PageUp` / `PageDown`, `Space`, side arrows, swipe on touch screens.
- Table of contents (`T`), current chapter, page in chapter and % of the book.
- Progress slider at the bottom: shows how much is read; drag it to jump anywhere
  (while dragging it shows the % and chapter you'll land on).
- Remembers where you stopped in each book (in the browser's `localStorage`),
  also when the same book is opened by picking the file by hand.
- Search in the book (🔍, `Ctrl+F` or `/`): lists every match grouped by chapter, with the
  % of the book and the text around it; click a match to go there. All matches are highlighted
  on the page, the current one in orange. `Enter` / `F3` — next match, `Shift+Enter` /
  `Shift+F3` — previous; while the panel is closed a small `‹ 3 / 42 ›` bar stays at the bottom.
  Search ignores case, treats ё as е and finds phrases split by formatting (e.g. half in italics).
- Font size (`+` / `−`) and light / sepia / dark theme, remembered.
- Drag an EPUB onto the page to open it.

### Open Library launchers

Browsers don't let a page opened straight from disk (`file://`) read other files, so
`index.html` opened by double-click can only ask you to pick each book file.
For one-click reading, open the catalog with the launcher placed next to `index.html`:

- **Windows:** double-click `Open Library.bat` (uses the built-in PowerShell, nothing to install).
- **Linux:** run `Open Library.sh` (needs `python3`; in a file manager: right-click →
  *Run as a Program*, or `sh "Open Library.sh"` in a terminal).

The launcher starts a tiny web server on this computer only (`localhost:48765`; if another
program uses that port, the next free one up to 48784) and opens the catalog in the browser. Close the window (or `Ctrl+C`) to stop it; the Linux one
also stops after 3 hours without use. Running it again while it works just opens the browser.
Reading places are stored per address, so keep using the launcher to keep them.

## Notes and limitations

- Only the metadata stored in the book is used. If a book says `Whitmore Clara`
  (last name first, no comma), the script can't tell which part is the first name.
- Genres are not used: many EPUBs don't contain them.
- Please respect copyright — this repository contains only the scripts; don't commit
  your books (`In/`, `Out/` and `*.epub` are in `.gitignore`).

## License

[MIT](LICENSE)
