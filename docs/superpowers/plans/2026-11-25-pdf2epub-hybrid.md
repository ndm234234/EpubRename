# pdf2epub Hybrid OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python3 pdf2epub.py` (no flags) re-recognises every PDF page with Marker in two modes, keeps the accurate mode's structure, repairs its text against the fast mode and the PDF text layer, and writes an EPUB into `In/`; it installs its engine on first run and resumes after interruption.

**Architecture:** `pdf2epub.py` stays the stdlib-only entry point and keeps the old Poppler pipeline as the "quick" fallback. New stdlib-only modules beside it: engine install/run (subprocess into a Python 3.12 venv in `~/.local/share/pdf2epub/`), page merging (text verification by voting), Marker-HTML → `Block` parsing, and the per-book hybrid orchestration with an on-disk work folder. The existing TOC/chapter/EPUB writer is reused, extended with list and table blocks.

**Tech Stack:** Python 3.10+ stdlib (`html.parser`, `difflib`, `subprocess`, `hashlib`, `unittest`); Poppler CLI; engine: `uv` 0.13.9 → Python 3.12 venv, `torch==2.15.0` (CPU index), `marker-pdf==2.2.0`, `surya-ocr==0.23.0`, llama.cpp release `b12681` (`llama-b12681-bin-ubuntu-x64.tar.gz`, SHA-256 `61f04060b691efc99708ff9ca59564a24de3073543f35b1c084e1b8cb73e6da0`).

**Spec:** `docs/superpowers/specs/2026-11-25-pdf2epub-hybrid-design.md`

## Global Constraints

- Plain `python3 pdf2epub.py` must do everything; flags (`--quick`, `--force`, `--keep-work`, `--yes`, `--title`, `--author`, `--lang`, `--out`, paths) are optional.
- `pdf2epub.py` and all new project modules import only the stdlib (heavy deps live only in the engine venv).
- Engine location: `~/.local/share/pdf2epub/` (never inside the project folder, never in temp dirs). Model cache: the default HuggingFace cache.
- Versions pinned exactly as in Tech Stack; the llama.cpp archive is verified by SHA-256 before use.
- Marker always runs with `--force_ocr --strip_existing_ocr --paginate_output --output_format html`.
- Work folder: `<out>/.pdf2epub-work/<safe_name(pdf.stem)>/`; deleted after a successful EPUB unless `--keep-work`.
- Never lose text silently: coverage is checked against the union of fast and the PDF text layer.
- Merge thresholds: retry accurate when coverage < 0.97 or a missing run ≥ 4 words; whole page from fast only when accurate coverage < 0.5.
- On a non-scanned page (no full-page image per `pdfimages -list`, as `FULL_PAGE` today) the text layer wins word votes.
- Checkbox list items render as `☐ ` text, never `<input>`.
- EPUB carries `<meta name="generator" content="pdf2epub hybrid"/>` or `"pdf2epub quick"`.
- fast and accurate never run at the same time; `llama-server` is always stopped when the program exits (normally, on error, on Ctrl+C).
- User-facing messages in English with emoji, like the other scripts; README documents the hybrid mode.
- Tests: `python3 -m unittest discover -s tests -v`; book text from `Pdf/` must never be committed (fixtures are hand-written synthetic HTML).

## Review Focus

- Ctrl+C or power loss in the middle of a page → next run resumes; no half-written cache file is trusted (all cache writes atomic: write `.part`, then `os.replace`).
- Marker/llama-server crashes or returns empty HTML for one page → that page falls back to fast (or quick text) and the book still finishes; reported.
- A `llama-server` left running from a crashed earlier run (or port busy) → it is stopped/reused, the run doesn't hang.
- File names with spaces, Cyrillic and quotes → engine commands and work folder work (argument lists, never shell strings).
- Digital (non-scanned) PDF → the text layer wins, so OCR typos never reach the book.

---

## File Structure

- Create `pdf2epub_engine.py` — engine paths, pinned versions, install (`ensure_engine`), running Marker (`run_marker`), splitting paginated HTML, stopping `llama-server`.
- Create `pdf2epub_merge.py` — word tokenizing, alignment, coverage, three-witness voting, splicing missing fragments: `merge_page`.
- Create `pdf2epub_html.py` — Marker page HTML → `Block`s (headings, paragraphs, lists, tables, images), drop-cap repair, cross-page paragraph joining, printed-contents tables → `Toc`.
- Create `pdf2epub_hybrid.py` — per-book orchestration: work folder cache and resume, progress/ETA line, log, `report.json`, building blocks for `write_epub`.
- Modify `pdf2epub.py` — `Block` gains list/table kinds; `write_epub` renders them and the generator meta; `main` runs hybrid by default with automatic engine install and quick fallback; new flags `--quick`, `--keep-work`.
- Create `tests/` — `test_engine.py`, `test_merge.py`, `test_html.py`, `test_hybrid.py`, `test_epub.py`, `fixtures/` (synthetic Marker HTML).
- Modify `README.md` — pdf2epub section.

---

### Task 1: Engine install and location

**Files:**
- Create: `pdf2epub_engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Produces:
  - `ENGINE_DIR: Path` = `Path.home() / ".local/share/pdf2epub"`
  - `@dataclass Engine: python: Path; marker_single: Path; llama_server: Path`
  - `find_engine(root: Path = ENGINE_DIR) -> Engine | None` — returns an Engine only when `root/".complete"` exists and the three paths exist.
  - `ensure_engine(log: Callable[[str], None], root: Path = ENGINE_DIR, runner=subprocess.run, fetch=urllib.request.urlretrieve) -> Engine | None` — idempotent; resumes a partial install; returns None (after logging why) when installation fails.

- [ ] **Step 1: Write failing tests** in `tests/test_engine.py` (`unittest`, temp dirs, fake `runner`/`fetch` that record calls and create the expected files):
  - `test_find_engine_none_without_complete_marker` — empty root → `None`.
  - `test_ensure_engine_installs_pinned_versions` — recorded commands contain `"marker-pdf==2.2.0"`, `"surya-ocr==0.23.0"`, `"torch==2.15.0"`, `"https://download.pytorch.org/whl/cpu"`, `"--python", "3.12"`; fetch URL ends with `llama-b12681-bin-ubuntu-x64.tar.gz`; returns an Engine and `root/".complete"` exists.
  - `test_ensure_engine_rejects_bad_checksum` — fetch writes bytes with another hash → returns `None`, logs a message containing `"checksum"`, `.complete` absent.
  - `test_ensure_engine_is_idempotent` — second call with `.complete` present makes zero runner/fetch calls.
  - `test_failed_step_resumes` — runner fails on the marker install the first time; second call does not repeat the finished steps (uv venv) and completes.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_engine -v` → FAIL (module missing).
- [ ] **Step 3: Implement** in `pdf2epub_engine.py`. Steps, each leaving a stamp file `root/".step-<name>"` so a rerun skips finished ones: `bootstrap` (system `python3 -m venv root/bootstrap`, `root/bootstrap/bin/pip install uv==0.13.9`), `python` (`uv venv --python 3.12 root/venv` with `UV_PYTHON_INSTALL_DIR=root/python`, `UV_CACHE_DIR=root/uv-cache`), `torch` (`uv pip install --python root/venv/bin/python torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu`), `marker` (`uv pip install ... marker-pdf==2.2.0 surya-ocr==0.23.0`), `llama` (download to `root/llama.tar.gz.part`, verify SHA-256, extract into `root/llama/`, locate `llama-server`). Log one line per step (`⬇️  Installing the recognition engine (one time, ~3 GB)…`). Then write `.complete`.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_engine -v` → PASS.
- [ ] **Step 5: Real install check** (user-approved): `python3 -c "import pdf2epub_engine as e; print(e.ensure_engine(print))"` → prints an `Engine(...)`; `~/.local/share/pdf2epub/.complete` exists; running it again prints the Engine immediately.
- [ ] **Step 6: Commit** `git add pdf2epub_engine.py tests/test_engine.py && git commit -m "pdf2epub: install the recognition engine (pinned Marker, Surya, llama.cpp)"`

### Task 2: Running Marker and splitting pages

**Files:**
- Modify: `pdf2epub_engine.py`
- Create: `tests/fixtures/paginated.html`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Engine` (Task 1).
- Produces:
  - `split_pages(html: str) -> dict[int, str]` — 0-based `data-page-id` → inner HTML of that page div.
  - `run_marker(engine: Engine, pdf: Path, mode: Literal["fast", "accurate"], pages: list[int], out_dir: Path, runner=subprocess.run) -> dict[int, str]` — runs `marker_single` once for the given 0-based pages (`--page_range` comma list), env `LLAMA_CPP_BINARY=engine.llama_server`; returns `split_pages` of the produced HTML and copies Marker's extracted images into `out_dir/images/`, rewriting `src` to those files. Raises `MarkerError` on non-zero exit or missing output.
  - `stop_llama_server(engine: Engine) -> None` — terminates any `llama-server` process whose executable is `engine.llama_server` (via `/proc/*/exe`), no-op when none.

- [ ] **Step 1: Write failing tests:** `test_split_pages` on `fixtures/paginated.html` (two `<div class='page' data-page-id='4'>`/`'5'` divs) → keys `{4, 5}`, each value contains only its own page's text; `test_run_marker_command` with a fake runner that writes an HTML file → recorded args contain `--mode`, `accurate`, `--force_ocr`, `--strip_existing_ocr`, `--paginate_output`, `--page_range`, `3,4`, and env has `LLAMA_CPP_BINARY`; `test_run_marker_error` → fake runner exit 1 raises `MarkerError`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_engine -v` → new tests FAIL.
- [ ] **Step 3: Implement** the three functions (`html.parser`-based div tracking for `split_pages`; argument lists only).
- [ ] **Step 4: Run** the tests → PASS.
- [ ] **Step 5: Real smoke check:** run `run_marker` for page 5 of the 9-page sample (`pdfseparate`/`pdfunite` of pages 7,8,26,38,39,101,119,133,135 of the book into the scratchpad) in `fast` → dict with key 5 containing the page's checklist heading. Afterwards `pgrep -f llama-server` prints nothing.
- [ ] **Step 6: Commit** `git commit -m "pdf2epub: run Marker per page range and split its pages"`

### Task 3: Text verification and merging

**Files:**
- Create: `pdf2epub_merge.py`
- Test: `tests/test_merge.py`

**Interfaces:**
- Produces:
  - `@dataclass MergeResult: html: str; source: Literal["accurate", "spliced", "fast"]; fixed_words: int; inserted_blocks: int; coverage: float`
  - `coverage(acc_html: str, witnesses: list[str]) -> float` — share of words of the union of witnesses (plain text or HTML) found, in order, in accurate.
  - `merge_page(acc_html: str, fast_html: str, layer_text: str, digital: bool) -> MergeResult`
  - `needs_retry(result: MergeResult) -> bool` — True when coverage < 0.97 or a missing run ≥ 4 words was spliced.

- [ ] **Step 1: Write failing tests** (all inputs short synthetic strings):
  - `test_vote_fixes_foreign_word` — acc `<p>польете грядки позже, quando солнце сядет</p>`, fast and layer with `когда` → html contains `когда`, not `quando`; `fixed_words == 1`.
  - `test_vote_keeps_accurate_on_tie` — acc `Супер!`, fast `Cnep!`, layer empty → `Супер!` kept.
  - `test_case_vote` — acc `обычно Это делаете`, fast and layer `это` → `это`.
  - `test_digital_layer_wins` — acc `Главнов`, fast `Гпавное`, layer `Глaвное`(other), `digital=True` with layer `Главное правило` → layer text wins every disagreement.
  - `test_markup_preserved` — acc `<p><b>СОВЕТ.</b> Заранее трудно</p>` with a fixed word inside → `<b>СОВЕТ.</b>` still present.
  - `test_missing_list_items_spliced` — acc has 3 `<li>` items, fast has 8 → result has all 8 item texts in fast's order, `source == "spliced"`, `inserted_blocks >= 1`.
  - `test_whole_page_from_fast` — acc covers < 50 % → `source == "fast"`, html == fast_html.
  - `test_coverage_uses_layer_too` — fast lacks a sentence that the layer has and acc lacks → coverage < 1.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_merge -v` → FAIL.
- [ ] **Step 3: Implement.** Tokens = words `\w+` normalised (lowercase, ё→е) with their character spans in accurate's text nodes; align accurate↔fast and accurate↔layer with `difflib.SequenceMatcher(autojunk=False)` on token lists. For each single-token `replace` opcode decide by majority of the three originals (case-sensitive); `digital=True` → layer wins; tie → accurate unless accurate's token is Latin while both witnesses' are Cyrillic. Edit only text nodes (rebuild via `html.parser` events). Missing runs: fast blocks (top-level elements, `li` inside lists) whose words are absent from accurate are inserted after the accurate block that contains the preceding matched word (or at the start).
- [ ] **Step 4: Run** the tests → PASS.
- [ ] **Step 5: Commit** `git commit -m "pdf2epub: verify accurate OCR text against fast OCR and the PDF text layer"`

### Task 4: Marker HTML to blocks

**Files:**
- Create: `pdf2epub_html.py`
- Modify: `pdf2epub.py` (`Block`: add kinds `"list"` and `"table"`; fields `items: list[str]` and `rows: list[list[str]]`, default empty)
- Create: `tests/fixtures/toc_page.html`, `tests/fixtures/chapter_page.html`
- Test: `tests/test_html.py`

**Interfaces:**
- Consumes: `Block`, `Toc`, `TocEntry` from `pdf2epub.py`.
- Produces:
  - `page_blocks(html: str, page: int) -> list[Block]` — `h1–h6` → `Block("h", level=min(n, 3))`; `p` → `"p"`; `ul/ol` → `Block("list", items=[li inner html])` with `<input type="checkbox"/>` → `☐ `; `table` → `Block("table", rows=...)`; `img` → `Block("img", src=…)`; `b/i/em/strong/br` kept inside html; everything else unwrapped; `page` set.
  - `fix_drop_cap(blocks: list[Block]) -> None` — first paragraph after a level-1 heading starting `^[A-ZА-ЯЁ] [а-яё]` → letters joined; a lone Latin capital that looks like Cyrillic (`D→В`, `B→В`, `C→С`, `O→О`, `P→Р`, `H→Н`, `K→К`, `M→М`, `T→Т`, `A→А`, `E→Е`, `X→Х`) is replaced when the word continues in Cyrillic.
  - `join_pages(blocks: list[Block]) -> list[Block]` — a `p` ending without `TERMINAL` followed (across a `page` marker) by a `p` starting lowercase becomes one block; hyphenated end `-` + lowercase → glued without hyphen.
  - `tocs_from_blocks(blocks: list[Block], body_levels) -> list[Toc]` — a `table` whose rows are `[title, number]` with ≥ 4 rows and mostly rising numbers (same rule as `find_tocs`) → `Toc(title=<preceding heading text>, entries=..., pages=[...])`; entry level 1 when the title cell is all `<b>`, otherwise 2; the table block becomes `Block("toc", level=<toc index>)`.

- [ ] **Step 1: Write failing tests:** `test_headings_paragraphs_bold` (chapter_page fixture: `<h1>ШАГ</h1><h2>1</h2><h1>ПОЛИВАЙТЕ…</h1><p><b>СОВЕТ.</b> …</p>` → kinds `h,h,h,p`, bold kept); `test_checkbox_list` → `Block("list")` with items starting `☐ Крепкие`; `test_drop_cap` (`В се садовники`, `Dсе садовники`) → `Все садовники`; `test_join_pages` (`…чтобы их` / page / `яблони выросли.` → one p; `Каж-` / page / `дый` → `Каждый`); `test_toc_table` (toc_page fixture: bold rows `ОТ СОСТАВИТЕЛЯ | 13`, plain `Как читать этот справочник | 17`, …) → one Toc titled `СОДЕРЖАНИЕ` with levels `[1, 1, 1, 2, …]` and numbers `[13, 14, 15, 17, …]`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_html -v` → FAIL.
- [ ] **Step 3: Implement** with `html.parser`.
- [ ] **Step 4: Run** → PASS; also `python3 -m unittest discover -s tests -v` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "pdf2epub: turn Marker HTML into book blocks (lists, tables, contents)"`

### Task 5: EPUB output for lists, tables and the generator mark

**Files:**
- Modify: `pdf2epub.py` (`write_epub`, `CSS`, chapter-heading two-line merge reused for Marker headings)
- Test: `tests/test_epub.py`

**Interfaces:**
- Consumes: `Block` kinds from Task 4.
- Produces: `write_epub(..., tocs, main, generator: str = "pdf2epub quick")` — renders `list` as `<ul class="list"><li>…</li></ul>`, `table` as `<table><tr><td>…</td></tr></table>`, and puts `<meta name="generator" content="{generator}"/>` in the OPF metadata.
- Also: `merge_heading_runs(blocks) -> list[Block]` in `pdf2epub_html.py` — consecutive heading blocks on one page (`ШАГ` / `1` / `ПОЛИВАЙТЕ…`) become one heading with `<br/>`, text `ШАГ 1. ПОЛИВАЙТЕ…` (same rule `close_heading` uses today), level = smallest.

- [ ] **Step 1: Write failing tests:** `test_list_and_table_render` — build chapters with one list and one table, write to a temp file → `check_epub` passes, XHTML contains `<ul class="list">`, `☐`, `<td>`; `test_generator_meta` → OPF contains `content="pdf2epub hybrid"`; `test_heading_runs` → one heading `ШАГ 1. ПОЛИВАЙТЕ САД ПО УТРАМ`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_epub -v` → FAIL.
- [ ] **Step 3: Implement**; CSS: `ul.list { list-style: none; padding-left: 1.2em; }`, `table { border-collapse: collapse; margin: 1em auto; } td { border: 1px solid #999; padding: .2em .4em; }`.
- [ ] **Step 4: Run** all tests → PASS; quick mode on the synthetic PDFs (`python3 pdf2epub.py --quick <scratchpad>/tp --out <scratchpad>/out2 --yes --force`) still converts both with ✅.
- [ ] **Step 5: Commit** `git commit -m "pdf2epub: lists, tables and generator mark in the EPUB"`

### Task 6: Hybrid orchestration with resume

**Files:**
- Create: `pdf2epub_hybrid.py`
- Test: `tests/test_hybrid.py`

**Interfaces:**
- Consumes: `Engine`, `run_marker`, `stop_llama_server`, `MarkerError` (Tasks 1–2); `merge_page`, `needs_retry` (Task 3); `page_blocks`, `fix_drop_cap`, `join_pages`, `merge_heading_runs`, `tocs_from_blocks` (Tasks 4–5); from `pdf2epub.py`: `read_text`, `read_images`, `drop_headers_footers`, `render_page`, `anchor_tocs`, `page_offset`, `split_chapters`, `write_epub`, `detect_lang`, `FULL_PAGE`, `weighted_median`.
- Produces: `convert_hybrid(pdf: Path, out: Path, title: str, author: str, lang: str | None, engine: Engine, keep_work: bool, progress: Callable[[str], None], run=run_marker) -> str` (summary line, same style as `convert`).

Work folder layout (`<out.parent>/.pdf2epub-work/<safe_name(pdf.stem)>/`): `fast/page-NNNN.html`, `acc/page-NNNN.html`, `merged/page-NNNN.html`, `images/`, `log.txt`, `report.json` (`{"pages": {N: {"source", "fixed_words", "inserted_blocks", "coverage", "retried"}}}`). Every file written via `.part` + `os.replace`. A page is done when `merged/page-NNNN.html` exists.

Flow: fast for all missing pages in one `run` call (cached per page); then accurate per batch of 4 missing pages (`stop_llama_server` in `finally`); per page `merge_page` (layer text = that page's lines from `read_text`; `digital` = no full-page image); `needs_retry` → one accurate rerun of that page alone, merge again; `MarkerError` or empty accurate → use fast html, `source="fast"`; page with no text in any witness but an image → rendered page picture as today. Progress line: `📄 page 37/228 · accurate · 1:12:05 elapsed · ~4:40:00 left` (ETA from mean seconds per accurate page so far). Then blocks → `join_pages` → `fix_drop_cap` → `merge_heading_runs` → `tocs_from_blocks` (fallback `find_tocs` on the layer) → `anchor_tocs` → `split_chapters` → `write_epub(..., generator="pdf2epub hybrid")`; cover as today. On success remove the work folder unless `keep_work`.

- [ ] **Step 1: Write failing tests** with a fake `run` returning fixture HTML per page and a 3-page synthetic PDF (built like the earlier `text_with_figure.pdf`):
  - `test_converts_with_fake_engine` → EPUB exists, `check_epub` passes, OPF has `pdf2epub hybrid`.
  - `test_resume_skips_done_pages` — first run's fake raises `KeyboardInterrupt` on page 2's accurate call; second run → fake receives accurate calls only for pages not yet merged; no `.part` files remain.
  - `test_marker_error_falls_back` — fake raises `MarkerError` for accurate page 1 → EPUB built, `report.json` page 1 `source == "fast"`.
  - `test_work_folder_removed` / `test_keep_work` — folder absent after success; present with `keep_work=True`.
  - `test_stops_server_on_error` — `stop_llama_server` called even when the fake raises.
  - `test_unicode_file_name` — PDF named `Книга "тест" 1.pdf` converts.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_hybrid -v` → FAIL.
- [ ] **Step 3: Implement** `convert_hybrid`.
- [ ] **Step 4: Run** all tests → PASS.
- [ ] **Step 5: Commit** `git commit -m "pdf2epub: hybrid conversion with per-page cache and resume"`

### Task 7: Wire into `main`, docs, real run

**Files:**
- Modify: `pdf2epub.py` (`main`, docstring), `README.md`
- Test: `tests/test_epub.py` (CLI test)

**Interfaces:**
- Consumes: `ensure_engine` (Task 1), `convert_hybrid` (Task 6), existing `convert` (quick).

- [ ] **Step 1: Write failing test** `test_main_falls_back_to_quick` — patch `ensure_engine` to return `None`, run `main()` with argv `[pdf, --out, tmp, --yes]` → exit 0, EPUB generator `pdf2epub quick`, stdout mentions `quick mode`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** without `--quick`, call `ensure_engine(print)` once before the book loop; Engine → `convert_hybrid` per book, else quick `convert` with a one-line explanation. Before the loop print the estimate `⏱️  ~N h for M pages` (2 min/page accurate). Add `--quick` and `--keep-work`. Author/title question stays before the long work starts (all questions for all books asked first, then the night run). README: describe plain run, first-run install (~3 GB, one time), resume, quick fallback, engine location, how to remove it (`rm -rf ~/.local/share/pdf2epub ~/.cache/huggingface/hub/models--datalab-to--*`).
- [ ] **Step 4: Run** all tests → PASS.
- [ ] **Step 5: Real check on the 9-page sample:** `python3 pdf2epub.py <scratchpad>/sample/sample.pdf --out <scratchpad>/hyb --yes --keep-work` → ✅; inspect: no `quando`; the checklist page has all 8 items with `☐`; the chapter page has all 8 list items and all its paragraphs; the bold lead-in word stays bold; contents is a linked list with levels; `fix_cover.py --dry-run` → fine. Interrupt once with Ctrl+C mid-run and rerun → finishes without redoing merged pages. Tune the thresholds if a check fails, rerun tests.
- [ ] **Step 6: Commit** `git add pdf2epub.py README.md tests docs && git commit -m "pdf2epub: hybrid recognition by default, quick mode as fallback"`
