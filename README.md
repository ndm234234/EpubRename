# epub-rename

Small Python scripts to tidy up an EPUB collection:

- **`epub_rename.py`** — copies books from `In/` into `Out/<Author>/<Author> - <Title>.epub`,
  using the author and title stored inside each book, and verifies every copy.
- **`make_server.py`** — builds `index.html`, an offline catalog of a book folder
  with covers, search, sorting and a built-in reader. It runs automatically at the end of `epub_rename.py`
  and can also be used on its own for any folder.
- **`fix_cover.py`** — makes sure every book has a cover that readers and catalogs can find:
  marks the cover picture when the book doesn't, or draws one from the title and author,
  removes references to files the book doesn't contain, and repairs a first page that shows a
  missing picture instead of the cover and pages that strict readers can't open. Asks before changing each book.
- **`pdf2epub.py`** — turns PDF books from `Pdf/` into EPUB files in `In/`, ready for
  `epub_rename.py`: real paragraphs instead of printed lines, headings and table of contents,
  lists, tables, pictures, the first page as the cover. Reads every page with a free offline
  recognition engine (installed by the script on first run), or just the PDF text layer in quick mode.

No dependencies besides Python (`pdf2epub.py` also needs the free
[Poppler](https://poppler.freedesktop.org/) tools). [Pillow](https://pypi.org/project/pillow/) is optional
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
- For `pdf2epub.py` only: Poppler — `sudo apt install poppler-utils` (Linux),
  `brew install poppler` (macOS), or on Windows
  [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases) with its
  `Library/bin` folder added to `PATH`
- For the recognition in `pdf2epub.py` (its default mode): **Linux** (x86-64) and Python's
  `venv` module — on Debian/Ubuntu `sudo apt install python3-venv`. Everything else
  (Marker, its own Python, the models) the script installs by itself on the first run.

Works on Linux, macOS and Windows; the recognition in `pdf2epub.py` is Linux-only for now
(on macOS and Windows it uses quick mode).

### On a new Linux machine

```bash
sudo apt install python3 python3-venv poppler-utils
git clone https://github.com/ndm234234/EpubRename.git
cd EpubRename
mkdir Pdf                     # put your .pdf files here
python3 pdf2epub.py           # first run: installs the engine (~3 GB, needs internet once)
```

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

## make_server.py

```bash
python3 make_server.py [FOLDER] [--title "My Library"]
```

Scans `FOLDER` (default: `Out` in the current folder) recursively and writes:

- `FOLDER/index.html` — the catalog page;
- `FOLDER/.index/covers/` — small cover copies (reused on the next run; covers of
  removed books are cleaned up);
- `FOLDER/.index/reader/` — the reader (copied from `reader/` next to the script);
- `FOLDER/Open Library.bat`, `FOLDER/Open Library.sh` and their `(network)` versions —
  launchers (from `launchers/`), with `library_sync.py` and `library-sync.ps1`, the code
  behind accounts and sync (below).

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
- Sort by recently opened (the default, in the app and in a browser tab alike: the book read last
  comes first, then books not opened yet; with an account, on every device), author, title,
  recently added or size; optional grouping by author. A sort you choose is kept.
  Settings are remembered.
- One rule for every book: the **cover** (or the **title**) shows the book's page, the **Read**
  button under it starts reading straight away (one tap on a phone). The download icon next to
  Read saves the EPUB file: Chrome/Edge (via the launcher) show a dialog to choose the folder;
  other browsers download it as usual.
- The book's page: the cover flies from the shelf to the left and the description slides in on
  the right (or "This book has no description."), with **Read** and **Save** buttons. A click on
  the title copies it. `Esc`, the
  **Catalog** button or the browser's Back return to the shelf at once, without animation; leaving the
  reader brings you back to the book's page. `Ctrl`+click / middle click on a cover opens the reader
  in a new tab. With "reduce motion" turned on in the system there is no animation.
- **Open file** opens any EPUB from disk in the reader.
- The catalog returns to where you were: after reading it shows the same place in the shelf, and
  after the browser or app is restarted on a book's page it opens that page again. This is kept
  on the device only, it is not synced.

### Reader

A single offline page (`reader/reader.html`) built on [epub.js](https://github.com/futurepress/epub.js)
and [JSZip](https://stuk.github.io/jszip/) (both bundled, no internet needed). Nothing is uploaded.

- Paging: `←` / `→`, `PageUp` / `PageDown`, `Space`, side arrows, mouse wheel / touchpad (one page per notch or swipe).
- **Scroll text** (*Aa* → *Layout*): instead of pages, the whole book scrolls up as one ribbon, each
  chapter right after the one before (finger, wheel, scrollbar; `↓` / `↑` a little, `PageDown` /
  `Space` / the side arrows a screen, `Home` / `End` the chapter's start / end). The place is the line at the top; the status line shows the %.
  This device only: it is not synced to your other devices.
- **Phones and tablets** read with the whole screen for the text: only a thin line at the bottom
  stays, numbers only — `4/24` (page in the chapter) on the left, `120/560 21%` (page in the
  book, % read) on the right — and the text has the same small margin
  above and below it. The phone's own status bar (clock, battery) stays: hiding it only while reading would
  take the browser's fullscreen mode, and Chrome then always shows a "swipe to exit" hint.
  A tap in the **middle third** of the page brings the toolbar and the slider over the text (the
  page doesn't move); another tap hides them. A tap in
  the **left / right third** or a swipe turns the page. The bars hide again after any move in the
  book (page turn, slider, contents, search, bookmark). Taps on links or while selecting text don't
  count. A short tap belongs to the reader alone, so Chrome's "Touch to Search" sheet doesn't pop
  up over a page turn; a long press still selects a word, with the browser's menu and search.
  With a mouse the bars are always there and taps in the lower half of the page turn pages.
- Table of contents (`T`), current chapter, page in chapter, page in the book and % of the book.
  Pages in the whole book are an estimate for this screen and text size (laying out every
  chapter would take long): the book's length over the text a page holds in the chapters read so
  far. It settles after the first chapters — within about 1% on a 500-page book.
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
- Light / sepia / dark theme, remembered.
- **Print** (computer; also `Ctrl+P` or the browser's Print menu) prints exactly what is on the
  screen, like a picture of it: the open pages, without the bars and buttons, scaled to fit an
  A4 sheet (landscape for a wide window, portrait for a tall one), black on white whatever the theme.
- **Aa** — text settings, remembered for all books, on three tabs (*Size*, *Font*, *Layout*; it
  opens on the tab used last). On a phone or tablet it slides down from under the toolbar and takes
  at most half the screen, so the page below shows what a change does:
  - size (also `+` / `−`);
  - font: *As in book* (under it, the font the open chapter is set in: e.g. *Times New Roman*,
    *Liberation Serif · in the book* for a font the book carries, or *default serif*), or one of
    the reading fonts that come with the reader — Literata, PT Serif, Lora, Merriweather,
    PT Sans, Inter, OpenDyslexic, and free twins of well-known fonts that can't be shipped:
    *Palatino* (P052), *Helvetica* (Nimbus Sans), *Futura* (Jost), *Caecilia* (Bitter); the
    real font is named when you hover the button. Books come with their own fonts (embedded,
    or named like Times New Roman / Arial), and books that name none get the device's
    default, so they all look different. A chosen font shows every book the same way, on
    every device and without internet; bold, italics and monospaced code are kept.
    The fonts are in `reader/fonts/` (Cyrillic and Latin, 3.9 MB; SIL Open Font License,
    P052 and Nimbus Sans GNU AGPL 3 with a font exception — see `reader/fonts/LICENSE.txt`);
    a browser downloads only the font in use;
  - layout: *Same in all books* gives plain paragraphs the same line spacing, indent and
    justified text with hyphenation. Titles, epigraphs and other styled parts keep the book's
    look.
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
Reading places are stored per address, so keep using the launcher to keep them (or use an
account to have them on every device, below). The window shows the library code for accounts.

The Windows launchers share `launchers/library-sync.ps1`, the Linux ones `launchers/library_sync.py`;
both keep the same files in `.sync/`, so a `.bat` and a `.sh` can serve one library. To try the
sync store on its own: `python3 launchers/library_sync.py --serve FOLDER --port 8000` (this
computer only, no browser). The manual check for Windows is `tests/windows-sync-checklist.md`.

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
- The window also shows the **library code** (see Accounts, below).
- It uses its own ports (48865–48884), so both launchers can run at the same time. Reading places
  and bookmarks are kept separately on each device, unless you use an account (below).
- The window logs who uses the library: each device (address, computer name when the router
  knows it, system and browser), the book it opens, and when it leaves, with the number of
  connected devices:

  ```
  2026-11-24 15:57:09  + 192.168.1.64 (laptop) · Linux · Chrome — connected    [devices: 1]
  2026-11-24 15:57:11  📖 192.168.1.64 (laptop) · Linux · Chrome — «Book title»
  2026-11-24 15:57:17  − 192.168.1.64 (laptop) · Linux · Chrome — closed the page; was here 2 s, …    [devices: 0]
  ```

  Open pages report in once a minute; a device that goes quiet for 3 minutes (screen off,
  browser in the background, out of Wi-Fi) is shown as gone, and comes back as connected
  when it reports in again. Refused outside addresses are logged once each.

#### Accounts: the same place on every device

Optional. Without an account everything works as before, each device on its own. With one, your
phone, tablet and computer share what you read. The account lives in the library itself, on the
computer that runs the launcher; nothing goes to the internet.

- **An account is an email and a password.** The email only names the account (it is not case
  sensitive); the library never sends mail, so nothing is verified and there is no "reset by mail".
  How to add and remove accounts: below.
- **What is synced:** reading places, bookmarks (with their names and notes) and the recently
  opened books are the same on all devices, and so is the catalog's view: its sort and grouping
  (choose "Recently opened" on the phone and the computer shows it too). The reader's settings
  (font, theme, layout) are shared only between devices of the same kind (phone, tablet,
  computer), since what suits one screen doesn't suit another.
- **What is not synced:** the books themselves, the offline copies on a device, and *Scroll text*:
  each device keeps its own (scroll on the phone, turn pages on the computer).
- **Offline is fine:** changes are kept on the device and sent when the library is reachable again.
  The reader syncs when a book is opened, when you come back to the page and when you leave it;
  while you read, changes go out at most once every 30 seconds. Changes made on two devices are
  merged, and a deleted bookmark stays deleted.
- **Use the https address.** On a plain `http://192.168.…` address there is no account (the
  catalog's logo is just a title; the reader has no account button):
  browsers allow it only on https (or on `localhost`, the same computer). Put the library behind
  https as for installing the app, above.
- **Wrong codes and passwords are slowed down:** after 5 wrong in a row the library answers
  "try later" for a minute, longer with each further mistake.
- **Forgotten password:** the owner deletes `.sync/accounts/<email>.json` in the library folder
  (and keeps `.sync/data/<email>.json`). The person registers again with the same email and the
  library code, and gets their data back.
- **Shared devices:** signing in merges the places and bookmarks already on that device into the
  account being signed in, and signing out leaves them on the device — so on a family tablet, after
  one person signs out, the next person's sign-in takes the previous person's places and bookmarks
  into their own account.
- **Signing out while the library computer is unreachable** signs the device out, but its token
  stays valid on the library computer until the account file is reset (the owner deletes
  `.sync/accounts/<email>.json`, as for a forgotten password).
- **Back up `.sync/`** with the library: it holds the code, the accounts and everyone's data. It is
  never served to the network, `make_server.py` never touches it, and it is in `.gitignore`.

##### Add an account

The person whose account it is makes it, on their own device (the owner can't make one for
someone else):

1. Open the catalog or the reader on the library's **https** address and open the account
   panel: in the catalog tap the **blue book logo and library name** at the top left (a green
   check on it means you are signed in); in the reader, the account button in the toolbar.
2. **Create account**: their email, a password twice, and the **library code**. The code (four
   words and two digits, like `river-paper-cold-moon-47`) is printed in the launcher window at
   every start and kept in `.sync/code.txt` in the library folder.
3. On their other devices: **Sign in** with the same email and password (no code needed).

Giving someone the code is how you let them in: it stops strangers who reach the address from
making accounts. To stop new accounts with a code you gave out, edit `.sync/code.txt` (or delete it,
and a new random code is made); it applies at once, existing accounts keep working, and the
launcher window shows the new code from its next start.

##### Remove an account

No need to stop the launcher: the servers read the account files on every request. In the
library folder, for the account `jane.doe@example.com`:

- delete `.sync/accounts/jane.doe@example.com.json`: the password no longer works and all their
  devices are signed out (on their next sync). Their devices keep their own copy of the places and
  bookmarks;
- also delete `.sync/data/jane.doe@example.com.json` to delete what they synced. Keep it, and
  whoever registers again with that email (and the library code) gets the data back — that is how
  a forgotten password is reset.

#### Installing as an app

The library can be installed as an app on a phone, tablet or computer: its own icon, full screen,
no address bar. The browser doesn't push it; it's in the browser's menu — Chrome: ⋮ → *Install app*
(or *Add to Home screen*), Safari on iPhone: Share → *Add to Home Screen*.

- A full install needs the library on an **https** address (or `localhost` on the same computer).
  On a plain `http://192.168.…` address Chrome only offers a home-screen shortcut that opens in the
  browser; Safari on iPhone opens it full screen either way.
- The app opens the library at the address it was installed from: keep the computer's address (and
  port) the same, e.g. a fixed address for it in the router. The server is needed for the first start
  and for updates; after that the app also works without it (below).
- `make_server.py` writes the app description to `.index/manifest.webmanifest`, named after the library.

#### Offline: the library on the device

The installed app (only the app, on https or `localhost`) keeps the library in its own storage on the
device: the site's storage, not the browser's cache, so the browser doesn't clean it up by itself.
Without a connection to the library the catalog and the reader open as usual, and so do the books
that are on the device. The others are dimmed, and *Read* says the book isn't on the device.

Each cover shows where the book is: a green phone — on this device, a cloud with an arrow — only in
the library (it pulses while the book is being downloaded). The mark is a button:

- tap the **cloud** to download the book (it is not opened);
- tap the **phone** to remove the book from the device (after asking; it stays in the library);
- tap the cover itself to open the book as usual. A book opened in the reader is kept on the device
  by itself.

The book's page has the same as a button: *Keep on this device* / *Remove from this device*.

The **Offline** button in the header (a cloud with an arrow and `books here / all books`; green when
the whole library is on the device, blue while downloading) opens the settings:

- **All books** (default): the whole library is downloaded, one book after another, with progress
  on the button and in the panel. The app can be used meanwhile. A book removed by hand is not
  downloaded again (until it is kept again, or *All books* is chosen anew).
- **Only books I open or keep**: only the books opened in the reader or downloaded by hand.
- *Remove all books from this device* removes them all and switches to the second mode, so they
  don't come back by themselves.

The app checks the library when it starts, when it comes back to the screen and when the connection
returns. `make_server.py` writes the list for this to `.index/library.json`, so run it after changing
the books, as always:

- a new book is downloaded (in *All books* mode);
- a changed book (another size or date) is deleted from the device and downloaded again;
- a book removed from the library is deleted from the device too;
- an updated reader, cover or catalog page is fetched again. If the catalog on screen is older than
  the library, a note offers to show the new one.

The storage is the browser's site data for the library's address. Clearing it (e.g. *Clear data* in
the site settings, or removing the app) removes the books too. The service worker `sw.js` next to
`index.html` is written by `make_server.py`.

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
  order and the guide. A missing page can't be brought back; the script says so;
- 🖼 the first page shows a picture the book doesn't have (often `file:///tmp/…`, left by
  the tool that made the book), so readers open on a broken image — points it to the real
  picture: the one no page shows whose size (or proportions) the page gives, else the cover;
- 📝 pages are plain HTML although the book lists them as XHTML (common in books exported
  from online libraries: `<meta charset="UTF-8">` without `/>`), so strict readers can't open
  them — rewrites them as XHTML; the words are checked to stay exactly the same. A line break
  after the text of the `mimetype` file is removed too.

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
again changes nothing. Run `make_server.py` afterwards to see the covers in the catalog.

## pdf2epub.py

```bash
python3 pdf2epub.py                 # every Pdf/*.pdf -> In/*.epub
python3 epub_rename.py              # then sort them into Out/ as usual
```

Its helper modules live in `pdf2epub_parts/`; run only `pdf2epub.py`.

Like `epub_rename.py`, it takes `Pdf/` and `In/` from the **current folder**. Other options:

```bash
python3 pdf2epub.py book.pdf --title "Title" --author "Author"   # one file, set the names
python3 pdf2epub.py some/folder --out other/folder
python3 pdf2epub.py --force         # convert again books that are already in In/
python3 pdf2epub.py --yes           # never ask questions
python3 pdf2epub.py --lang uk       # language of the book (normally guessed from the text)
python3 pdf2epub.py --quick         # only the PDF text layer: seconds instead of hours
python3 pdf2epub.py --keep-work     # keep In/.pdf2epub-work (recognised pages, report.json)
```

### Recognition (the default)

Every page is read from its picture, the way a person reads it, by
[Marker](https://github.com/datalab-to/marker) with the Surya models — free, and
**offline** once installed:

1. **First run** installs the engine by itself (one time, about 3 GB, needs internet):
   Python 3.12 and Marker go to `~/.local/share/pdf2epub/`, the models to
   `~/.cache/huggingface/`. Nothing is installed system-wide.
2. Each book is read twice: a fast pass over the whole book (minutes), then a careful pass
   page by page (about **a minute a page** on a CPU, no graphics card is used — a 230-page
   book takes about 4 hours).
   The careful reading is checked word by word against the fast one and the PDF's own text
   layer: foreign or misread words are outvoted, and lines, list items or paragraphs that
   the careful pass dropped are put back in place.
3. All questions (author, title) are asked before the long part starts, so it can run
   unattended. It can be stopped any time (Ctrl+C, power off): the next run goes on from
   the same page. The work lives in `In/.pdf2epub-work/` and is removed when the book is done.

```
📚 Found 1 PDF file(s)

⏱️  ~3.8 h for 228 pages (it can be stopped with Ctrl+C and goes on from the same page next time)

⏳ Mila Fenwick - Quiet Rivers.pdf ...
   ⚡ fast recognition of 228 page(s)…
   📄 page 1/228 · ok · ⏱ 0:54 · 0:03:35 elapsed · ~4:05:30 left
   📄 page 2/228 · 2 words fixed, 1 missing piece put back · ⏱ 0:54 · 0:03:35 elapsed · ~4:04:20 left
   📄 page 3/228 · ⚠️ 79% read, check it · ⏱ 0:54 · 0:03:35 elapsed · ~4:03:10 left
   ...
   🔍 reading pages 5–8 · 0:01:42…
```

Each page line says what the check did on it (`ok`, words fixed by the vote, missing pieces
put back from the fast reading, a page read twice, the fast reading used instead). `⏱` is the time
the page took: Marker reads pages four at a time, so each gets its share of the batch, plus
any second reading of its own. The same time is in `log.txt` and in `report.json`. The last
line is live: it shows which pages Marker is reading and for how long.

If the engine can't be installed (no internet on the first run, unsupported system), the
script says so and uses quick mode. To remove the engine:

```bash
rm -rf ~/.local/share/pdf2epub ~/.cache/huggingface/hub/models--datalab-to--*
```

### What it does

- **Text** of every page, including the hidden text layer of scanned books that were
  recognised (OCR). Printed lines are joined into paragraphs, words hyphenated at the end
  of a line are glued back, running headers, footers and page numbers are removed.
  Bold and italic are kept.
- **Table of contents** is taken from the book's own printed contents, when it has one:
  the entries, their nesting (from the indent) and page numbers. Each entry is found in the
  text (printed page numbers are matched to PDF pages by the numbers in headers/footers),
  so the reader's table of contents looks like the printed one and leads to the right place.
  The printed contents pages become a list of clickable links with page numbers; chapters
  are split at its top-level entries; sections it lists are shown as headings.
- **Headings** are also found by their larger font, or as short bold lines standing on their own.
  Without printed contents, the biggest headings start new chapters and the table of
  contents is made of them. A book without any headings is cut into parts of 20 pages.
- **Pictures** among the text are kept; the page scans under recognised text are not.
  A page without any text (a scan that wasn't recognised, a diagram) is added as a
  picture of the whole page.
- **Cover** — the first page as a picture. If that page is a picture itself, it is not
  repeated in the text.
- **Title and author** come from the PDF properties, or from a file name like
  `Author - Title.pdf`. If the author is still unknown, the script asks for the title and
  author (Enter keeps the suggestion); `--title`/`--author` set them, `--yes` skips the question.
- **Language** is guessed from the text (Russian, Ukrainian or English); `--lang` sets it.
- PDF files are only read. A book already in `In/` is skipped unless `--force`;
  a new book is written to a temporary file and checked (every page is valid, every
  listed file is there) before it gets its name.

In quick mode (`--quick`, or when the engine is not available) the result depends on the
PDF's text layer: page layout is turned into flowing text by guessing, so
tables, multi-column pages and side notes can come out in the wrong order, and a badly
recognised scan gives badly recognised text. Formulas and drawings made of lines (not
pictures) are lost on pages that also have text.

## Reader checks (tests/reader)

End-to-end checks of the reader in headless Chrome, a touch phone by default (Node 22+ and
`google-chrome`, no packages). They open real books from `Out/` and look where the text really
is on the screen:

```bash
tests/reader/run.sh "Jane Doe/Jane Doe - A Quiet Harbour.epub" "John Roe/John Roe - Paper Lanterns.epub"
FULL=1 tests/reader/run.sh "Jane Doe/Jane Doe - A Quiet Harbour.epub"   # every font at 80/100/150/200%
```

- `nav.mjs` — every page forward, then back: no page skipped or shown twice, the status line
  matches the real page count, no clipped lines, the reading place on the page shown;
- `place.mjs` — the place kept through every font and size, quick changes, reopening, window
  resizes; bookmarks and search matches land on the page shown, also after a font change;
- `hits.mjs` — every search match (or an even spread of them) is on the page shown;
- `touch.mjs`, `status.mjs`, `typo.mjs` — taps and bars, the status line, the Aa panel;
- `scroll.mjs` — *Scroll text*: switching keeps the first visible word, chapters follow one
  another, the place saved on the reader's own scroll and kept on reopening, font changes and
  resizes; contents, search, bookmarks and taps in scroll mode, on a phone and a desktop.

Several books run side by side; the output keeps only failures and each check's verdict.
`W`/`H` set the screen, `TOUCH=0` a desktop, `SHOTS=dir` saves screenshots.

## Sync checks

```bash
python3 -m pytest -q tests                     # servers, merge, launchers
node --test 'tests/sync/*.test.mjs'            # the browser side of sync
python3 tests/sync_contract.py http://host:port FOLDER/.sync/code.txt   # a running server, any launcher
```

The reader checks above (`tests/reader/run.sh "<book>"`) cover the reader itself.

## Notes and limitations

- Only the metadata stored in the book is used. If a book says `Whitmore Clara`
  (last name first, no comma), the script can't tell which part is the first name.
- Genres are not used: many EPUBs don't contain them.
- Please respect copyright — this repository contains only the scripts; don't commit
  your books (`In/`, `Out/`, `Pdf/`, `*.epub` and `*.pdf` are in `.gitignore`).

## License

[MIT](LICENSE)
