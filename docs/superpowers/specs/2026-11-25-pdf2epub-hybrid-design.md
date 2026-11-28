# pdf2epub: hybrid recognition (Marker fast + accurate + text layer)

Date: 2026-11-25. Status: under review (after the implementer's check).

## Why

`pdf2epub.py` turns PDFs from `Pdf/` into EPUBs in `In/`, from where `epub_rename.py` sorts them.
Today it takes the **PDF text layer** (Poppler `pdftohtml`) and guesses the structure from fonts and
indents. On scanned books with a poor old OCR layer the user called the result nonsense:
recognition errors («Гпавное» for «Главное», "main thing"; «Пояив» for «Полив», "watering"), lost
list check marks, sidebars glued into the middle of sentences, tables turned to mush, lost bold.

Goal: an EPUB that looks as if a person who sees the page had carefully retyped the book:
paragraphs, joined hyphenations, bold/italic, headings, lists, tables, a table of contents as in the
book — **with nothing lost and no invented text**.

## Goals

- Every page is recognized afresh from its image (not from the old text layer).
- Structure and formatting as close to the original as possible.
- The text is complete and exact: not one line of the book is lost, no words are swapped.
- Works **free and offline** (internet is needed only once, for the install).
- One book may take the whole night (5–8 hours for 230 pages) — that is acceptable.
- An interrupted run (computer turned off, Ctrl+C) continues from where it stopped.
- **Run without flags** (user requirement): just `python3 pdf2epub.py` and that is it.
  The first run installs the engine itself and goes on with the conversion; a repeat run picks up
  the interrupted work by itself. Flags may exist for special cases, but the user does not need them.

## Non-goals

- Speed. The fast variant stays only as a fallback mode.
- Windows/macOS support in this iteration (the user is on Ubuntu 26.04 in VMware; install paths
  are kept portable, but only Linux is tested).
- Fixing errors that are in the book itself.
- Online services, paid APIs.

## Anti-goals (a failure, even if "it works")

- Silently lost text (a paragraph, a list item, part of a page) — worse than any layout error.
- Words invented or "retold" by the model in the final text.
- A run that crashes after 6 hours and loses everything.
- An install that breaks the system Python or the project.

## Constraints and facts (checked by a trial on 2026-11-25)

- Machine: 16 cores (i7-14700K in VMware), 30 GB RAM, **no GPU**, ~39 GB free, Ubuntu 26.04,
  system Python 3.14, `pip` not installed system-wide, `venv` available.
- `marker-pdf` (with `surya-ocr` 0.23) **does not install on Python 3.14** (an old Pillow without wheels).
  It works in a venv on **Python 3.12**, obtained through `uv` (`uv venv --python 3.12`), with a CPU build
  of PyTorch (`--index-url https://download.pytorch.org/whl/cpu`).
- Marker modes (`marker_single --mode`):
  - `fast` — light ONNX models. 9 pages in 43 s. The text is complete and noticeably cleaner than the
    old layer, sidebars are not glued in. Structure is middling: the table of contents sticks into one
    paragraph, list check marks → «©»/«О», bold is lost, the drop cap «В» → «D», occasional errors
    («Сорт» → «Copm», Latin lookalikes).
  - `accurate` — the full-page VLM Surya 2.5 (GGUF, ~1.2 GB + mmproj ~0.2 GB, cache
    `~/.cache/huggingface`) through **`llama-server` from llama.cpp** (variable `LLAMA_CPP_BINARY`;
    the official build `llama-bXXXXX-bin-ubuntu-x64.tar.gz` from the ggml-org/llama.cpp GitHub releases).
    ~1.5–2 min/page. Excellent structure: table of contents → a table with bold chapters, `<b>`/`<i>`,
    lists with checkboxes, multi-line headings. **But**: on one of 3 pages it cut its output short
    (5 list items and 3 paragraphs were missing), swapped a word («когда», "when" → «quando»), and wrote
    «Это» ("this") with a capital letter mid-sentence.
- Needed flags: `--force_ocr --strip_existing_ocr`; `--paginate_output --output_format html` gives
  `<div class='page' data-page-id='N'>` — page-by-page matching is possible; `--page_range`.
- Running the downloaded `llama-server` binary needs the user's permission (granted for the trial).
- The existing `pdf2epub.py` (stdlib + Poppler only) can already: read the text layer with positions,
  find running heads and printed page numbers, parse the printed table of contents (levels by indent,
  linking printed numbers to PDF pages), build EPUB 3 + NCX with a cover, check the result
  (`check_epub`); compatibility with `fix_cover.py`/`make_index.py` is confirmed.

## Approaches (considered)

1. **fast only + rule-based fixes.** Complete, exact text, 15–20 min/book, but structure and
   formatting are worse. Rejected as the main mode: it does not give "as a person sees it".
2. **accurate only.** The best structure, but a risk of lost/swapped text with no check. Rejected:
   it breaks the main anti-goal.
3. **Hybrid (chosen by the user).** Every page is recognized in both modes; structure and
   formatting come from accurate, and its text is checked against the witnesses (fast and the PDF
   text layer) and repaired/replaced where they disagree. 5–8 h/book, run overnight.

## Decision

### Architecture

- `pdf2epub.py` stays the entry point and **is itself still stdlib only** (+ Poppler). The heavy
  dependencies live in a separate environment that the script calls as a subprocess.
- The recognition environment (the "engine") is installed **automatically on the first run** of
  `python3 pdf2epub.py` (no flags): the script says it is downloading the engine (~2–3 GB, once),
  installs it and goes straight on with the conversion. The location is permanent, outside the
  project and temp folders: `~/.local/share/pdf2epub/` (a venv on Python 3.12 through `uv`, CPU PyTorch,
  `marker-pdf`, llama.cpp `llama-server`; models in the usual HuggingFace cache). The project lives on a
  VMware shared folder (`/mnt/hgfs`), so the venv does not go there. *(The implementer's/Claude's
  decision, open to change.)*
- If the engine cannot be installed (no internet, install error) — a clear message saying what
  exactly failed, and conversion in the old mode (Poppler + rules), so the night is not wasted.
  A half-installed engine is completed on the next run. The `--quick` flag (optional) forces the
  old mode.

### Per-page pipeline

For each PDF page:

1. **fast**: Marker `--mode fast --force_ocr --strip_existing_ocr --paginate_output`, HTML.
   Quick; the whole book can go through in one call at the start (gives an early result and a witness).
2. **accurate**: the same with `--mode accurate`, page by page or in small batches
   (`--page_range`, `--keep_server` so `llama-server` is not restarted every time).
3. **Witness 3**: the existing PDF text layer (already read by `read_text`), if there is one.
4. **Text check** of accurate against the witnesses:
   - normalize (case, ё/е, quotes, dashes, spaces, hyphenation) and align the word sequences
     (difflib or similar);
   - **coverage**: what share of fast's words is found in accurate. If accurate lost a piece
     (coverage below a threshold, e.g. < 97 %, or a continuous fragment of ≥ N words is missing) →
     rerun accurate for the page once; if still incomplete → take the page from fast
     (with its structure) and flag it in the report;
   - **word swaps**: where accurate and fast differ by a single word, a vote of the three
     witnesses (accurate, fast, PDF layer) decides: the word two of three agree on wins;
     on a tie — accurate (it has the best recognition), except for words not in the book's language
     (Latin in Russian text when the witnesses have Cyrillic — like «quando»);
   - fixes go only into the text inside accurate's HTML; the markup (`<b>`, `<i>`, lists,
     headings, tables) is kept.
5. **Cache**: the result of every step for every page is saved to disk in the book's work folder
   (e.g. `In/.pdf2epub-work/<book name>/`: `fast.html`, `acc-0042.html`, `merged-0042.html`,
   `report.json`). A repeat run skips finished pages — this is how continuing after an
   interruption works. After the EPUB is built successfully the work folder is deleted (the
   `--keep-work` flag keeps it).

### Building the EPUB from Marker HTML

- Marker blocks → blocks of the current build (`Block`): `h1–h6` → headings, `p` → paragraphs, `ul/ol/li`
  → lists (a new block kind; checkboxes → «☐» in the text, not `<input>`), `table` → tables (a new
  block kind), `img` → images (Marker extracts images), `b/i` are kept.
- A paragraph broken by a page boundary is joined (the last paragraph of a page without final
  punctuation + the first paragraph of the next one starting with a lowercase letter).
- Running heads and page numbers are removed (Marker marks `PageHeader`/`PageFooter`; in HTML they
  are cut by default; printed page numbers for the table of contents are taken, as now, from the
  PDF layer or from the running heads).
- Drop cap: «В се садовники» / «D се садовники» ("All gardeners", split) at the start of a chapter →
  join the first letter to the word, «Все садовники» (rule: a single capital letter + space +
  lowercase continuation in the first paragraph after a chapter heading; for «D», Latin instead of
  Cyrillic, the check against the witnesses decides).
- **Table of contents**: the printed table of contents (in accurate it is a "title | number" table) →
  the same mechanism as now: levels (bold lines = level 1, the rest by indent/boldness),
  links to places in the text, a clickable list in the book, the reader's nav/NCX, splitting into
  chapters by top-level entries. Reuse the existing `find_tocs`/`anchor_tocs`/`split_chapters`/`write_epub`
  code, adapting its input.
- Cover, metadata, the author question, the `check_epub` check, writing through a temp file — as now.

### Observability (overnight run)

- A progress line: page N/M, stage, elapsed, estimated time left.
- A log in the work folder.
- A final report: how many pages from accurate, how many words fixed, which pages were taken from
  fast (and why), which pages have no text (inserted as an image).

### Resources

- `llama-server` uses all cores; fast and accurate are not run at the same time.
- Disk: models ~2–3 GB once; the work folder is tens of MB per book.

## Decided by the user

- Free and offline (internet only for the install).
- Approach: hybrid; a book may take the whole night.
- Marker as the base (the accurate mode is the Surya model through llama.cpp).
- Running the official `llama-server` build is allowed.
- The script runs without flags; the engine is installed automatically on the first run.

## The implementer's (Claude's) decisions — open to change

- Engine install location `~/.local/share/pdf2epub/`.
- Check thresholds (97 % coverage, length of a missing fragment) — tuned on trial pages.
- The work folder inside `In/.pdf2epub-work/` and its removal after success.
- The old mode stays as the fallback (`--quick` and when there is no engine).
- List checkboxes shown as the «☐» character, not as a form element.

## Answers to the review questions (Claude's decisions, open to change)

1. **A PDF with a good text layer** (not a scan: the page has no full-page scan image,
   detected as now through `pdfimages -list`). The text of such a page is exact, so in the word
   vote **the PDF layer wins** (rather than being one of three equals). Marker is still run —
   for the structure. On scans the layer is an ordinary third witness.
2. **A normal run** `python3 pdf2epub.py` — hybrid for all books in `Pdf/`, one after another:
   a whole book, then the next. At the start a time estimate is printed ("~6 h for 228 pages"),
   with no questions. There is no page limit.
3. **Engine install** — automatic on the first run, with no confirmations (the user
   has already allowed `llama-server` and wants a run without flags). Versions are **pinned**: specific
   versions of `marker-pdf`/`surya-ocr`/`torch` and a specific llama.cpp release (with a SHA-256 check
   of the archive); the models are those the pinned surya version downloads. `uv` is installed inside
   a service venv through `pip` (the system `pip` is not needed); `~/.local/bin/uv` is not relied on.
4. **Text lost in accurate**: first a rerun of the page; if still incomplete — **insert the
   missing fragments** from fast into the accurate page in their place (by block alignment:
   after the last matched block), not a replacement of the whole page. Replacing the whole page with
   fast — only if accurate covered less than half.
5. **Checking during development** (decided by the user on 2026-11-25): the implementer may
   install the engine into `~/.local/share/pdf2epub/` and run `llama-server`/Marker on trial
   pages as much as needed. The build tests also rely on saved Marker HTML
   (fixtures), so quick checks do not wait for recognition.
6. **Completeness check** — against the union of the witnesses: fast **and** the PDF text layer (if
   there is one). A fragment that at least one witness has and accurate lacks counts as
   lost. When two witnesses disagree on a whole fragment, the version of the witness the third
   agrees with is taken; without a third — accurate.
7. **Books already in `In/`** are skipped, as now (`--force` redoes them). The EPUB gets
   `<meta name="generator" content="pdf2epub hybrid|quick">`, so it is clear what made
   the book.

## Left to the implementer

- The exact word alignment and voting algorithm, thresholds.
- Batch size for accurate and the use of `--keep_server`.
- How exactly Marker returns images in HTML and how to bring them into the EPUB.
- Tests: on the 9-page sample (pages 7, 8, 26, 38, 39, 101, 119, 133, 135 of the test
  book) — text completeness against fast, no Latin swaps, table of contents structure;
  plus synthetic PDFs (a text PDF with an image, a scan with no text).
