# epub-rename

Small Python scripts to tidy up an EPUB collection:

- **`epub_rename.py`** — copies books from `In/` into `Out/<Author>/<Author> - <Title>.epub`,
  using the author and title stored inside each book, and verifies every copy.
- **`make_index.py`** — builds `index.html`, an offline catalog of a book folder
  with covers, search, sorting and a built-in reader. It runs automatically at the end of `epub_rename.py`
  and can also be used on its own for any folder.
- **`fix_cover.py`** — makes sure every book has a cover that readers and catalogs can find:
  marks the cover picture when the book doesn't, or draws one from the title and author,
  and removes references to files the book doesn't contain. Asks before changing each book.

No dependencies besides Python. [Pillow](https://pypi.org/project/pillow/) is optional
(used to shrink covers for the catalog, and by `fix_cover.py` to draw missing covers).

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
- `FOLDER/Open Library.bat`, `FOLDER/Open Library.sh` and their `(network)` versions —
  launchers (from `launchers/`).

EPUB files are only read. Keep `.index/` next to `index.html` when moving the catalog.

### Catalog features

- Cover grid; books without a cover get a generated placeholder.
  The cover is found the way readers find it: the book's cover mark (EPUB 3 `cover-image`,
  EPUB 2 `<meta name="cover">`), the guide's cover page, a picture named "cover", or the
  picture on the first page (many converted books have only that). Marks that point to
  files missing from the book are skipped. Transparent PNG covers are put on white.
  Many books mark a tiny cover (e.g. 126×200) and keep the same picture full size elsewhere
  (usually on the first page): then the big copy is used, so covers stay sharp on the book's page.
- Title, authors, series, year, publisher, file size.
- Follows the system light / dark theme.
- Instant search across title, authors, series, description and file path
  (case-insensitive, `ё` = `е`, all words must match). `/` focuses search, `Esc` clears it.
- Click an author to see all their books.
- Sort by author, title, recently added or size; optional grouping by author.
  Settings are remembered.
- One rule for every book: the **cover** (or the **title**) shows the book's page, the **Read**
  button under it starts reading straight away (one tap on a phone). The download icon next to
  Read saves the EPUB file: Chrome/Edge (via the launcher) show a dialog to choose the folder;
  other browsers download it as usual.
- The book's page: the cover flies from the shelf to the left and the description slides in on
  the right (or "This book has no description."), with **Read** and **Save** buttons. `Esc`, the
  **Catalog** button or the browser's Back return to the shelf (the cover flies back); leaving the
  reader brings you back to the book's page. `Ctrl`+click / middle click on a cover opens the reader
  in a new tab. With "reduce motion" turned on in the system there is no animation.
- **Open file** opens any EPUB from disk in the reader.

### Reader

A single offline page (`reader/reader.html`) built on [epub.js](https://github.com/futurepress/epub.js)
and [JSZip](https://stuk.github.io/jszip/) (both bundled, no internet needed). Nothing is uploaded.

- Paging: `←` / `→`, `PageUp` / `PageDown`, `Space`, side arrows. On touch screens: swipe, or tap
  the lower half of the page (from the middle down to the slider) — right side for the next page,
  left side for the previous one. Taps in the upper half, on links or while selecting text don't turn pages.
- Table of contents (`T`), current chapter, page in chapter and % of the book.
- `Esc` closes an open panel (contents, search, bookmarks), then clears search highlights,
  then closes the book and returns to the catalog, scrolled to where you left it
  (the **Catalog** button does the same).
- Progress slider at the bottom: shows how much is read; drag it to jump anywhere
  (while dragging it shows the % and chapter you'll land on).
  To place it, the reader first counts pages across the whole book: a big book (hundreds of
  chapters) can take a minute or more the first time, shown as *Preparing navigation… N%*.
  The result is cached in `localStorage` (about 2 MB for all books, oldest dropped first),
  so the next time the book opens the slider is ready at once.
- Remembers where you stopped in each book (in the browser's `localStorage`),
  also when the same book is opened by picking the file by hand.
- Search in the book (search icon, `Ctrl+F` or `/`): lists every match grouped by chapter, with the
  % of the book and the text around it; click a match to go there. All matches are highlighted
  on the page, the current one in orange. `Enter` / `F3` — next match, `Shift+Enter` /
  `Shift+F3` — previous; while the panel is closed a small `‹ 3 / 42 ›` bar stays at the bottom.
  Search ignores case, treats ё as е and finds phrases split by formatting (e.g. half in italics).
- Bookmarks, separate for each book: the bookmark icon (or `M`) bookmarks the page you are reading
  (filled — the page already has one; press again to remove it). The bookmarks icon next to it
  (or `B`, available once the book has a bookmark) lists the book's bookmarks in reading order
  with the %, chapter and first words of the page; rename (pencil), delete (bin) or click to go
  there. The up / down arrows in the list or `[` / `]` jump to the previous / next bookmark. Kept in the browser's
  `localStorage`, like the reading place.
- Notes: any bookmark can have a note of your own — free text, several lines. Press the note icon
  next to the bookmark (or **Add note** in the "Bookmark added" message right after bookmarking).
  It is saved as you type; `Esc`, `Ctrl+Enter` or **Done** finish, an empty note is removed. Notes are
  shown under their bookmarks; click one to edit it. On phones the list fills the screen, the buttons
  are finger-sized and the note being edited stays above the on-screen keyboard.
- Finding your bookmarks and notes: the field at the top of the bookmarks list filters it as you
  type (by name, the page's first words, chapter and note; all words must match, case and ё/е
  don't matter; `Esc` clears it). The book search (`Ctrl+F`) also lists matching bookmarks and notes,
  in a separate group above the matches in the text.
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

#### Reading on a phone or tablet (home Wi-Fi)

`Open Library (network).bat` (Windows) and `Open Library (network).sh` (Linux) do the same, but
the library can also be opened from other devices in your home network. The window shows the
address to type on the phone, e.g. `http://192.168.1.64:48865/`.

- Only home-network addresses (`192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`, link-local) get an
  answer, so even a port forwarded on the router doesn't put the library on the internet.
  For reading away from home use a VPN to your home network instead.
- Files are only read; folder listings and uploads are refused.
- Anyone on the same Wi-Fi who knows the address can open the library.
- Windows asks once whether PowerShell may use the network: allow it for **Private** networks
  (and check the Wi-Fi is set as a Private network). No administrator rights needed.
  On Linux a firewall (`ufw`, `firewalld`) may need to allow the port.
- It uses its own ports (48865–48884), so both launchers can run at the same time. Reading places
  and bookmarks are kept separately on each device.

## fix_cover.py

```bash
python3 fix_cover.py [PATH ...] [--dry-run] [--yes] [--no-generate]   # default PATH: Out
```

Some books have a cover but don't say so: it's only shown on the first page, the cover
mark points to a file that is not in the book, or the mark uses the file name instead of
the manifest id. Readers and libraries then show no cover. Some books also list files
they don't contain, which makes readers show broken pictures or skip to empty pages.
For each book the script:

- ✅ the cover is properly marked and every listed file is there — the file is not touched;
- 🔧 a cover picture is there but not marked — adds the mark (`<meta name="cover">`, and
  `properties="cover-image"` for EPUB 3);
- 🎨 there is no cover picture at all — draws one (title and author on a coloured
  background, like the catalog placeholders) and adds it to the book. Needs Pillow;
  `--no-generate` skips these books;
- 🧹 the book lists files it doesn't have — removes them from the manifest, the reading
  order and the guide. A missing page can't be brought back; the script says so.

**The `.epub` files are changed in place**, so the script asks before each book:

```
🧹 Oskar Brandt - The Salt Road.epub
     • the book lists files it doesn't have: OEBPS/cover.jpg, OEBPS/UusgmGFo.html → remove them from the manifest and the reading order
     ⚠️  1 page(s) are missing from the book itself: their text can't be restored
   Fix it? The .epub file will be changed. [y]es / [a]ll (yes, don't ask again) / [n]o / [c]ancel:
```

`y` fixes the book, `a` fixes it and all the next books without asking again, `n` leaves it as it is, `c` (or `Ctrl+C`) stops and leaves the remaining
books untouched. `--yes` fixes all books without asking (needed when the input is not a
terminal), `--dry-run` only prints what would be done.

Only the package file (OPF) is edited, as text, so everything else in the book stays
byte for byte; the fixed file is checked (opens, nothing lost, every listed file present,
cover found) before it replaces the old one, and keeps the old file's date. Running it
again changes nothing. Run `make_index.py` afterwards to see the covers in the catalog.

## Notes and limitations

- Only the metadata stored in the book is used. If a book says `Whitmore Clara`
  (last name first, no comma), the script can't tell which part is the first name.
- Genres are not used: many EPUBs don't contain them.
- Please respect copyright — this repository contains only the scripts; don't commit
  your books (`In/`, `Out/` and `*.epub` are in `.gitignore`).

## License

[MIT](LICENSE)
