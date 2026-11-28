"""One book through the hybrid recognition: Marker fast + accurate, checked page by page.

Everything a page needs is cached in a work folder next to the output, so a run that
is stopped (Ctrl+C, power off) goes on from the first unfinished page next time.
"""

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import pdf2epub as base
from pdf2epub import Block
from pdf2epub_parts.engine import Engine, MarkerError, latex_to_mathml, run_marker, stop_llama_server
from pdf2epub_parts.markup import (fix_drop_cap, join_pages, link_endnotes, merge_heading_runs, page_blocks,
                                   place_footnotes, text_of,
                                   running_head_offset, strip_running_heads, take_footnotes, tocs_from_blocks)
from pdf2epub_parts.merge import merge_page, needs_retry

ACC_BATCH = 4          # pages per accurate run (the model is loaded once per run)
FAIL_LIMIT = 8         # accurate failing on this many pages in a row: something is broken, stop the book
BLANK_SUSPECT = 8          # accurate empty while fast has this many words: the reading failed
CHECK_COVERAGE = 0.9   # pages checked less well than this are named in the summary


def _write(path: Path, text: str) -> None:
    """Write so that a file is either complete or absent (never half-written)."""
    part = path.with_name(path.name + ".part")
    with part.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def time_left(elapsed: float, recognised: int, remaining: int) -> float | None:
    """Seconds left, from the pages recognised in this run (pages from the cache took no time)."""
    return elapsed / recognised * remaining if recognised else None


COVER_WORDS = 40       # a first page with more recognised words is a page of the book, not a cover
FRONT_PAGES = 12       # title pages, imprint: a short page here that accurate can't read goes in as a picture


def first_page_is_cover(page_html: str, big_picture: bool) -> bool:
    """In a scan every page is a picture: the first one is a cover only if it has little text."""
    return big_picture and len(re.sub(r"<[^>]+>", " ", page_html).split()) <= COVER_WORDS


def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"\w+", text)]


def _marker_titles(blocks: list[Block], pages: set[int]) -> dict[int, list[str]]:
    """Contents lines as Marker read them: {page number: [title html]}."""
    found: dict[int, list[str]] = {}
    for b in blocks:
        if b.page not in pages:
            continue
        lines = [" ".join(r) for r in b.rows] if b.kind == "table" else [b.html] if b.kind in ("h", "p") else []
        for line in lines:
            m = re.fullmatch(r"(.*?)[\s.…·_]*(?:<b>)?\s*(\d{1,4})\s*(?:</b>)?\s*", line)
            if m:
                title = re.sub(r"</?b>", "", m.group(1)).strip()
                title = re.sub(r"\s*[.…·_]{3,}\s*$", "", title)
                found.setdefault(int(m.group(2)), []).append(title)
    return found


def _use_marker_titles(toc, found: dict[int, list[str]]) -> None:
    """Where the PDF text layer fails: a formula (old OCR in its place) or a letter it lost ("\ufffd"),
    the title is taken from Marker's reading."""
    for e in toc.entries:
        mine = set(_words(e.title))
        for title in found.get(e.number, []):
            if "<math" not in title and "\ufffd" not in e.title:
                continue
            words = _words(re.sub(r"<math[^>]*>.*?</math>", " ", title))
            if words and sum(1 for w in words if w in mine) >= 0.7 * len(words):
                if "<math" in title:
                    e.html = title
                else:
                    e.title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()
                break


def place_tocs(blocks: list[Block], layer_tocs: list) -> list:
    """The printed contents: from the PDF text layer when it has one (it sees every page of the
    contents, with nesting from the indent), else from the tables Marker made of it."""
    if not layer_tocs:
        return tocs_from_blocks(blocks)
    for toc in layer_tocs:
        _use_marker_titles(toc, _marker_titles(blocks, set(toc.pages)))
    for i, toc in enumerate(layer_tocs):
        on = set(toc.pages)
        kept = [b for b in blocks if not (b.page in on and b.kind != "page")]
        at = next((k for k, b in enumerate(kept) if b.kind == "page" and b.page == toc.pages[0]), None)
        if at is None:
            continue
        kept[at + 1:at + 1] = [Block("h", base.esc(toc.title), toc.title, 1, page=toc.pages[0]),
                               Block("toc", level=i, page=toc.pages[0])]
        blocks[:] = kept
    return layer_tocs


RIGHT_INDENT = 0.2     # a line that starts this far into the column and ends at its right edge is set right


def align_right(blocks: list[Block], pages: list, body: float) -> None:
    """Paragraphs the book prints at the right (a signature under a quote, a date under a letter):
    Marker gives them as ordinary paragraphs, the PDF text layer still knows where their lines are."""
    def words(text: str) -> list[str]:
        return [w.lower() for w in re.findall(r"\w+", text)]

    by_page = {p.number: p for p in pages}
    for number, page in by_page.items():
        lines = [l for l in page.lines if l.kind == "text" and words(l.text)]
        paras = [b for b in blocks if b.kind == "p" and b.page == number]
        if not lines or not paras:
            continue
        left, right = base.column_edges(page, (min(l.left for l in lines), max(l.right for l in lines)))
        width = right - left
        if width <= 0:
            continue
        sets = [set(words(b.text)) for b in paras]
        mine: dict[int, list] = {}
        for line in lines:
            lw = words(line.text)
            share = [sum(1 for w in lw if w in s) / len(lw) for s in sets]
            best = max(range(len(paras)), key=lambda i: share[i])
            if share[best] >= 0.6:
                mine.setdefault(best, []).append(line)
        for i, own in mine.items():
            covered = sum(len(words(l.text)) for l in own)
            if covered < 0.6 * len(words(paras[i].text)):
                continue                        # most of the paragraph is not found: don't guess
            if all(abs(l.right - right) <= body and l.left - left >= RIGHT_INDENT * width for l in own):
                paras[i].align = "right"


MATH = re.compile(r'<math( display="block")?>(.*?)</math>', re.S)


def apply_math(blocks: list[Block], tocs: list, convert: Callable[[list], list]) -> None:
    """Formulas (LaTeX from Marker) become MathML; one that can't be converted stays as LaTeX."""
    def fragments():
        for b in blocks:
            yield b, "html", None
            for k in range(len(b.items)):
                yield b, "items", k
            for r, row in enumerate(b.rows):
                for c in range(len(row)):
                    yield b, "rows", (r, c)
        for toc in tocs:
            for e in toc.entries:
                yield e, "html", None

    def get(obj, field, at):
        if field == "html":
            return obj.html
        return obj.items[at] if field == "items" else obj.rows[at[0]][at[1]]

    def put(obj, field, at, value):
        if field == "html":
            obj.html = value
        elif field == "items":
            obj.items[at] = value
        else:
            obj.rows[at[0]][at[1]] = value

    places = [p for p in fragments() if "<math" in get(*p)]
    formulas = sorted({(html.unescape(m.group(2)), bool(m.group(1))) for p in places for m in MATH.finditer(get(*p))})
    if not formulas:
        return
    made = dict(zip(formulas, convert(formulas)))

    def swap(m):
        latex = html.unescape(m.group(2))
        mathml = made.get((latex, bool(m.group(1))))
        return mathml or f'<span class="tex">{html.escape(latex, quote=False)}</span>'

    for obj, field, at in places:
        put(obj, field, at, MATH.sub(swap, get(obj, field, at)))
        if field == "html" and not (isinstance(obj, Block) and obj.kind != "h"):
            # the plain title of a heading or a contents line (the reader's navigation): the formula's characters
            plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", obj.html))).strip()
            if isinstance(obj, Block):
                obj.text = plain
            else:
                obj.title = plain


def _short(seconds: float) -> str:
    """0:57, 1:52, 1:02:05: the time one page took."""
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def page_note(source: str, info: dict, retried: bool) -> str:
    """What the check did on a page, for its progress line."""
    if source == "empty":
        return "empty page"
    if source == "picture":
        return "kept as a picture"
    if source == "layer":
        return "⚠️ only the PDF's own text"
    if source == "fast":
        return f"⚠️ fast reading used ({info.get('reason', 'accurate failed')})"
    parts = ["read twice"] if retried else []
    fixed, put_back = info.get("fixed_words", 0), info.get("inserted_blocks", 0)
    if fixed:
        parts.append(f"{fixed} word{'s' if fixed > 1 else ''} fixed")
    if put_back:
        parts.append(f"{put_back} missing piece{'s' if put_back > 1 else ''} put back")
    if info.get("coverage", 1.0) < CHECK_COVERAGE:
        parts.append(f"⚠️ {info['coverage']:.0%} read, check it")
    return ", ".join(parts) or "ok"


@contextmanager
def _reading(status, label: str):
    """A live line with a clock while Marker reads (it says nothing for minutes)."""
    if status is None:
        yield
        return
    started, stop = time.time(), threading.Event()

    def tick():
        while not stop.wait(1):
            status(f"{label} · {_fmt(time.time() - started)}…")

    status(f"{label} · {_fmt(0)}…")
    clock = threading.Thread(target=tick, daemon=True)
    clock.start()
    try:
        yield
    finally:
        stop.set()
        clock.join()
        status("")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_check(report: dict) -> list[str]:
    """Pages worth a look: not from the accurate reading, or checked less well."""
    out = []
    for n, p in sorted(report["pages"].items(), key=lambda x: int(x[0])):
        if p.get("reason") in ("title page", "blank", "cover"):
            continue                            # on purpose: a picture, a blank page, the cover
        if p["source"] not in ("accurate", "spliced"):
            out.append(f"{n} ({p['source']})")
        elif p.get("coverage", 1) < CHECK_COVERAGE:
            out.append(f"{n} ({p['coverage']:.0%})")
    return out


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def convert_hybrid(pdf: Path, out: Path, title: str, author: str, lang: str | None, engine: Engine,
                   keep_work: bool, progress: Callable[[str], None],
                   run=run_marker, stop=stop_llama_server, status: Callable[[str], None] | None = None,
                   only: set[int] | None = None, work_root: Path | None = None,
                   mathml: Callable[[list], list] | None = None) -> str:
    """progress: lines that stay; status: the live line under them (None: no live line).
    mathml: [(LaTeX, block?)] -> [MathML or None] (default: the engine's converter).

    only: a trial of only these pages (1-based); the pages it reads stay in the work folder
    (under work_root, default: next to out), so the whole book goes on from them.
    """
    work = (work_root or out.parent) / ".pdf2epub-work" / base.safe_name(pdf.stem)
    for sub in ("fast", "acc", "merged", "images"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    log_file = (work / "log.txt").open("a", encoding="utf-8")

    def log(line: str) -> None:
        log_file.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        log_file.flush()

    try:
        return _convert(pdf, out, title, author, lang, engine, keep_work, progress, run, stop, work, log, status,
                        only, mathml or (lambda formulas: latex_to_mathml(engine, formulas)))
    finally:
        log_file.close()
        if not keep_work and not only and out.exists() and (work / "done").exists():
            shutil.rmtree(work, ignore_errors=True)
            parent = work.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()


def _convert(pdf, out, title, author, lang, engine, keep_work, progress, run, stop, work, log, status,
             only=None, mathml=None) -> str:
    log(f"start {pdf}" + (f" (pages {', '.join(map(str, sorted(only)))})" if only else ""))
    # the PDF's own text layer: a witness, printed page numbers, and "is this page a scan?"
    with tempfile.TemporaryDirectory(prefix="pdf2epub-") as tmp_name:
        tmp = Path(tmp_name)
        pages = base.read_text(pdf, tmp)
        # Marker fails on a blank page, and one in a batch costs the whole batch: blank pages are not read
        pictured = base.read_images(pdf)
        blank_pages = {p.number for p in pages if not p.lines and not pictured.get(p.number)
                       and (not only or p.number in only) and base.page_is_blank(pdf, p.number, tmp)}
    if not pages:
        raise RuntimeError("no pages found")
    sizes = base.Counter()
    for line in (l for p in pages for l in p.lines):
        sizes[line.size] += len(line.text)
    body = base.weighted_median(sizes) if sizes else 10.0
    base.drop_headers_footers(pages, body)
    layer = {p.number: " ".join(l.text for l in p.lines) for p in pages}
    pictures = base.read_images(pdf)
    total = len(pages)
    if only:
        pages[:] = [p for p in pages if p.number in only]    # headers and footers were found on the whole book
        if not pages:
            raise RuntimeError(f"the book has only {total} page(s)")

    def scanned(p) -> bool:
        return any(w >= base.FULL_PAGE * p.width and h >= base.FULL_PAGE * p.height for w, h in pictures.get(p.number, []))

    numbers = [p.number for p in pages]            # 1-based; Marker counts from 0
    fast_file = lambda n: work / "fast" / f"page-{n:04d}.html"
    acc_file = lambda n: work / "acc" / f"page-{n:04d}.html"
    merged_file = lambda n: work / "merged" / f"page-{n:04d}.html"
    report_path = work / "report.json"

    # the cache belongs to one PDF: another file under the same name starts over
    stamp = {"sha256": _sha256(pdf), "pages": total}
    stamp_path = work / "source.json"
    if stamp_path.exists() and json.loads(stamp_path.read_text()) != stamp:
        log("another PDF under the same name: starting over")
        for sub in ("fast", "acc", "merged", "images"):
            shutil.rmtree(work / sub, ignore_errors=True)
            (work / sub).mkdir()
        report_path.unlink(missing_ok=True)
    _write(stamp_path, json.dumps(stamp))
    report = json.loads(report_path.read_text()) if report_path.exists() else {"pages": {}}

    def recognise(mode: str, wanted: list[int], keep) -> None:
        """keep(page number, html) for every page recognised; a failing run is tried again in
        halves, so one bad page costs a few runs, and a page that fails alone is left out."""
        span = f"{wanted[0]}–{wanted[-1]}" if len(wanted) > 1 else f"{wanted[0]}"
        label = f"⚡ fast reading of pages {span}" if mode == "fast" else f"🔍 reading pages {span}"
        try:
            with _reading(status, label):
                got = run(engine, pdf, mode, [n - 1 for n in wanted], work)
        except MarkerError as e:
            log(f"{mode} {wanted[0]}-{wanted[-1]} failed: {e}")
            if len(wanted) > 1:
                half = len(wanted) // 2
                recognise(mode, wanted[:half], keep)
                recognise(mode, wanted[half:], keep)
            return
        for n in wanted:
            keep(n, got.get(n - 1, ""))

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def heads_offset() -> int | None:
        for files in (fast_file, acc_file):
            offset = running_head_offset({n: read(files(n)) for n in numbers if files(n).exists()})
            if offset is not None:
                return offset
        return None

    def done(n: int) -> bool:
        return merged_file(n).exists() and str(n) in report["pages"]

    try:
        # 1. fast for the whole book (minutes), cached per page
        missing = [n for n in numbers if not fast_file(n).exists() and n not in blank_pages]
        if missing:
            progress(f"⚡ fast recognition of {len(missing)} page(s)…")
            recognise("fast", missing, lambda n, h: _write(fast_file(n), h))

        # running heads ("22 INTRODUCTION") are not text: the page numbers in them tell them apart
        offset = heads_offset()

        # 2. accurate page by page (hours), checked against fast and the text layer
        todo = [n for n in numbers if not done(n)]
        started, done_now, recognised = time.time(), 0, 0
        failing: list[int] = []                    # pages in a row accurate failed on
        blank: set[int] = set()                    # pages accurate read as blank (not kept)
        spent: dict[int, float] = {}               # seconds Marker spent on each page

        def timed(pages: list[int], action) -> None:
            """Run action and share its time between the pages it read."""
            t = time.monotonic()
            action()
            for n in pages:
                spent[n] = spent.get(n, 0.0) + (time.monotonic() - t) / len(pages)

        for i in range(0, len(todo), ACC_BATCH):
            batch = todo[i:i + ACC_BATCH]
            need = [n for n in batch if not acc_file(n).exists() and n not in blank_pages]
            if need:
                timed(need, lambda: recognise("accurate", need, lambda n, h: _write(acc_file(n), h)))
                recognised += len(need)
                # a blank page where fast read text: the reading failed (e.g. its server died);
                # read it once more, and if it is still blank, don't keep it
                for n in need:
                    if acc_file(n).exists() and not read(acc_file(n)).strip() and \
                            len(re.findall(r"\w+", text_of(read(fast_file(n))))) >= BLANK_SUSPECT:
                        log(f"page {n}: accurate came back blank, reading it again")
                        timed([n], lambda: recognise("accurate", [n], lambda k, h: _write(acc_file(k), h)))
                        if not read(acc_file(n)).strip():
                            acc_file(n).unlink()
                            blank.add(n)
            for n in batch:
                if n in blank_pages:
                    report["pages"][str(n)] = {"source": "empty", "retried": False, "reason": "blank", "seconds": 0}
                    _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                    _write(merged_file(n), "")
                    log(f"page {n}: empty (blank, not read)")
                    done_now += 1
                    progress(f"📄 page {n}/{numbers[-1]} · {page_note('empty', {}, False)} · ⏱ {_short(0)}"
                             f" · {_fmt(time.time() - started)} elapsed")
                    continue
                fast_html = strip_running_heads(read(fast_file(n)), n, offset)
                acc_html = strip_running_heads(read(acc_file(n)), n, offset) if acc_file(n).exists() else None
                if acc_html is None:
                    failing.append(n)
                    if len(failing) >= FAIL_LIMIT:
                        for k in failing:
                            merged_file(k).unlink(missing_ok=True)
                            report["pages"].pop(str(k), None)
                        _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                        raise RuntimeError(f"accurate recognition failed on {len(failing)} pages in a row "
                                           f"(see {work / 'log.txt'}); the work so far is kept: run it again")
                else:
                    failing = []
                page = pages[numbers.index(n)]
                digital = bool(page.lines) and not scanned(page)
                retried, info = False, {}
                if acc_html and acc_html.strip():
                    result = merge_page(acc_html, fast_html, layer[n], digital)
                    if needs_retry(result):
                        retried = True
                        again: dict[int, str] = {}
                        timed([n], lambda: recognise("accurate", [n], again.__setitem__))
                        if again.get(n, "").strip():
                            second = merge_page(strip_running_heads(again[n], n, offset), fast_html, layer[n], digital)
                            if (second.looped, -second.coverage, second.extra_run) < \
                                    (result.looped, -result.coverage, result.extra_run):
                                result = second
                    merged, source = result.html, result.source
                    info = {"fixed_words": result.fixed_words, "inserted_blocks": result.inserted_blocks,
                            "coverage": round(result.coverage, 3)}
                    if source == "fast":
                        info["reason"] = "accurate read too little"     # less than half of what fast read
                    if result.looped and fast_html.strip():
                        merged, source, info["reason"] = fast_html, "fast", "accurate repeated itself"
                elif fast_html.strip():
                    merged, source = fast_html, "fast"
                    info["reason"] = "accurate failed" if acc_html is None and n not in blank else "accurate found nothing"
                elif layer[n].strip():
                    merged, source = f"<p>{_escape(layer[n])}</p>", "layer"
                else:
                    merged, source = "", "empty"
                if not merged.strip() and pictures.get(n):
                    picture = base.render_page(pdf, n, work / "images" / f"page-{n:04d}", base.PAGE_IMAGE_HEIGHT)
                    if picture:
                        merged, source = f'<p><img src="{_escape(str(picture))}" alt="page {n}"/></p>', "picture"
                seconds = round(spent.get(n, 0.0))
                report["pages"][str(n)] = {"source": source, "retried": retried, **info, "seconds": seconds}
                _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                _write(merged_file(n), merged)
                log(f"page {n}: {source} {info} {seconds} s")
                done_now += 1
                elapsed = time.time() - started
                left = time_left(elapsed, recognised, len(todo) - done_now)
                progress(f"📄 page {n}/{numbers[-1]} · {page_note(source, info, retried)} · ⏱ {_short(seconds)}"
                         f" · {_fmt(elapsed)} elapsed"
                         + (f" · ~{_fmt(left)} left" if left is not None else ""))
    finally:
        stop(engine)

    # 3. the book (pages merged by an earlier version may still have their running heads)
    offset = heads_offset()
    with tempfile.TemporaryDirectory(prefix="pdf2epub-") as tmp_name:
        tmp = Path(tmp_name)
        cover = base.render_page(pdf, 1, tmp / "cover", base.COVER_HEIGHT) if numbers[0] == 1 else None
        big = any(w * h >= 0.5 * pages[0].width * pages[0].height for w, h in pictures.get(1, []))
        skip_first = cover is not None and first_page_is_cover(merged_file(1).read_text(encoding="utf-8"), big)
        blocks: list[Block] = []
        for n in numbers:
            if skip_first and n == 1:
                if "1" in report["pages"] and report["pages"]["1"].get("reason") != "cover":
                    report["pages"]["1"]["reason"] = "cover"       # not in the text: nothing to check
                    _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                continue
            page_html = read(merged_file(n))
            entry = report["pages"].get(str(n), {})
            # a title page set as a design: accurate takes it for a picture (reads nothing or too little),
            # fast loses letters and the layout; not a page where Marker failed (its fast text is fine)
            title_page = n <= FRONT_PAGES and entry.get("source") == "fast" \
                and entry.get("reason") in ("accurate found nothing", "accurate read too little") \
                and len(text_of(page_html).split()) <= COVER_WORDS
            if n == 1 and cover is not None and (title_page or entry.get("reason") == "title page"):
                if title_page:
                    entry.update(source="picture", reason="title page")
                    _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                continue                        # it is the cover already
            blocks.append(Block("page", level=n, page=n))
            if title_page:
                picture = base.render_page(pdf, n, work / "images" / f"page-{n:04d}", base.PAGE_IMAGE_HEIGHT)
                if picture:
                    page_html = f'<p><img src="{_escape(str(picture))}" alt="page {n}"/></p>'
                    entry.update(source="picture", reason="title page")
                    _write(merged_file(n), page_html)
                    _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                    log(f"page {n}: title page kept as a picture")
            blocks += page_blocks(strip_running_heads(page_html, n, offset), n)
        align_right(blocks, pages, body)
        blocks, notes = take_footnotes(blocks)
        blocks = join_pages(blocks)
        place_footnotes(blocks, notes)
        link_endnotes(blocks)
        fix_drop_cap(blocks)
        blocks = merge_heading_runs(blocks)
        tocs = place_tocs(blocks, base.find_tocs(pages, body))
        main = max(tocs, key=lambda t: len(t.entries)) if tocs else None
        if tocs:
            base.anchor_tocs(blocks, tocs, base.page_offset(pages, tocs, blocks))
            if sum(1 for e in main.entries if e.anchor) < 0.5 * len(main.entries):
                main = None
        for b in blocks:
            if b.kind == "img" and not Path(b.src).is_absolute():
                b.src = str(work / "images" / b.src)
        img_names: dict[str, str] = {}
        for b in blocks:
            if b.kind == "img" and b.src not in img_names and Path(b.src).is_file():
                img_names[b.src] = f"images/img{len(img_names) + 1:04d}{Path(b.src).suffix.lower() or '.jpg'}"
        blocks = [b for b in blocks if b.kind != "img" or b.src in img_names]
        lang = lang or base.detect_lang(" ".join(b.text for b in blocks[:400]))
        apply_math(blocks, tocs, mathml)
        chapters = base.split_chapters(blocks, title, main)
        if not chapters:
            raise RuntimeError("no text or pictures found")
        base.write_epub(out, title, author, lang, chapters, cover, img_names, tocs, main,
                        generator="pdf2epub hybrid")
    if not only:
        (work / "done").write_text("")
    log(f"done {out}")

    report = {"pages": {k: v for k, v in report["pages"].items() if int(k) in numbers}}   # a trial: its pages
    sources = base.Counter(p["source"] for p in report["pages"].values())
    fixed = sum(p.get("fixed_words", 0) for p in report["pages"].values())
    words = sum(len(b.text.split()) for b in blocks if b.kind in ("p", "list"))
    parts = ", ".join(f"{k} {v}" for k, v in sorted(sources.items()))
    check = to_check(report)
    return (f"{len(chapters)} chapter(s), {words} words; pages: {parts}; {fixed} word(s) corrected"
            + (f"; check: {', '.join(check)}" if check else "") + f" — «{title}»"
            + (f", {author}" if author else ""))

