"""One book through the hybrid recognition: Marker fast + accurate, checked page by page.

Everything a page needs is cached in a work folder next to the output, so a run that
is stopped (Ctrl+C, power off) goes on from the first unfinished page next time.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

import pdf2epub as base
from pdf2epub import Block
from pdf2epub_engine import Engine, MarkerError, run_marker, stop_llama_server
from pdf2epub_html import fix_drop_cap, join_pages, merge_heading_runs, page_blocks, tocs_from_blocks
from pdf2epub_merge import merge_page, needs_retry

ACC_BATCH = 4          # pages per accurate run (the model is loaded once per run)
FAIL_LIMIT = 8         # accurate failing on this many pages in a row: something is broken, stop the book
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


def first_page_is_cover(page_html: str, big_picture: bool) -> bool:
    """In a scan every page is a picture: the first one is a cover only if it has little text."""
    return big_picture and len(re.sub(r"<[^>]+>", " ", page_html).split()) <= COVER_WORDS


def place_tocs(blocks: list[Block], layer_tocs: list) -> list:
    """The printed contents: from the PDF text layer when it has one (it sees every page of the
    contents, with nesting from the indent), else from the tables Marker made of it."""
    if not layer_tocs:
        return tocs_from_blocks(blocks)
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
        if p["source"] not in ("accurate", "spliced"):
            out.append(f"{n} ({p['source']})")
        elif p.get("coverage", 1) < CHECK_COVERAGE:
            out.append(f"{n} ({p['coverage']:.0%})")
    return out


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def convert_hybrid(pdf: Path, out: Path, title: str, author: str, lang: str | None, engine: Engine,
                   keep_work: bool, progress: Callable[[str], None],
                   run=run_marker, stop=stop_llama_server) -> str:
    work = out.parent / ".pdf2epub-work" / base.safe_name(pdf.stem)
    for sub in ("fast", "acc", "merged", "images"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    log_file = (work / "log.txt").open("a", encoding="utf-8")

    def log(line: str) -> None:
        log_file.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        log_file.flush()

    try:
        return _convert(pdf, out, title, author, lang, engine, keep_work, progress, run, stop, work, log)
    finally:
        log_file.close()
        if not keep_work and out.exists() and (work / "done").exists():
            shutil.rmtree(work, ignore_errors=True)
            parent = work.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()


def _convert(pdf, out, title, author, lang, engine, keep_work, progress, run, stop, work, log) -> str:
    log(f"start {pdf}")
    # the PDF's own text layer: a witness, printed page numbers, and "is this page a scan?"
    with tempfile.TemporaryDirectory(prefix="pdf2epub-") as tmp_name:
        tmp = Path(tmp_name)
        pages = base.read_text(pdf, tmp)
    if not pages:
        raise RuntimeError("no pages found")
    sizes = base.Counter()
    for line in (l for p in pages for l in p.lines):
        sizes[line.size] += len(line.text)
    body = base.weighted_median(sizes) if sizes else 10.0
    base.drop_headers_footers(pages, body)
    layer = {p.number: " ".join(l.text for l in p.lines) for p in pages}
    pictures = base.read_images(pdf)

    def scanned(p) -> bool:
        return any(w >= base.FULL_PAGE * p.width and h >= base.FULL_PAGE * p.height for w, h in pictures.get(p.number, []))

    numbers = [p.number for p in pages]            # 1-based; Marker counts from 0
    fast_file = lambda n: work / "fast" / f"page-{n:04d}.html"
    acc_file = lambda n: work / "acc" / f"page-{n:04d}.html"
    merged_file = lambda n: work / "merged" / f"page-{n:04d}.html"
    report_path = work / "report.json"

    # the cache belongs to one PDF: another file under the same name starts over
    stamp = {"sha256": _sha256(pdf), "pages": len(numbers)}
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
        try:
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

    def done(n: int) -> bool:
        return merged_file(n).exists() and str(n) in report["pages"]

    try:
        # 1. fast for the whole book (minutes), cached per page
        missing = [n for n in numbers if not fast_file(n).exists()]
        if missing:
            progress(f"⚡ fast recognition of {len(missing)} page(s)…")
            recognise("fast", missing, lambda n, h: _write(fast_file(n), h))

        # 2. accurate page by page (hours), checked against fast and the text layer
        todo = [n for n in numbers if not done(n)]
        started, done_now, recognised = time.time(), 0, 0
        failing: list[int] = []                    # pages in a row accurate failed on
        for i in range(0, len(todo), ACC_BATCH):
            batch = todo[i:i + ACC_BATCH]
            need = [n for n in batch if not acc_file(n).exists()]
            if need:
                recognise("accurate", need, lambda n, h: _write(acc_file(n), h))
                recognised += len(need)
            for n in batch:
                fast_html = fast_file(n).read_text(encoding="utf-8") if fast_file(n).exists() else ""
                acc_html = acc_file(n).read_text(encoding="utf-8") if acc_file(n).exists() else None
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
                        recognise("accurate", [n], again.__setitem__)
                        if again.get(n, "").strip():
                            second = merge_page(again[n], fast_html, layer[n], digital)
                            if (second.looped, -second.coverage, second.extra_run) < \
                                    (result.looped, -result.coverage, result.extra_run):
                                result = second
                    merged, source = result.html, result.source
                    info = {"fixed_words": result.fixed_words, "inserted_blocks": result.inserted_blocks,
                            "coverage": round(result.coverage, 3)}
                    if result.looped and fast_html.strip():
                        merged, source, info["reason"] = fast_html, "fast", "accurate repeated itself"
                elif fast_html.strip():
                    merged, source = fast_html, "fast"
                    info["reason"] = "accurate failed" if acc_html is None else "accurate found nothing"
                elif layer[n].strip():
                    merged, source = f"<p>{_escape(layer[n])}</p>", "layer"
                else:
                    merged, source = "", "empty"
                if not merged.strip() and pictures.get(n):
                    picture = base.render_page(pdf, n, work / "images" / f"page-{n:04d}", base.PAGE_IMAGE_HEIGHT)
                    if picture:
                        merged, source = f'<p><img src="{_escape(str(picture))}" alt="page {n}"/></p>', "picture"
                report["pages"][str(n)] = {"source": source, "retried": retried, **info}
                _write(report_path, json.dumps(report, ensure_ascii=False, indent=1))
                _write(merged_file(n), merged)
                log(f"page {n}: {source} {info}")
                done_now += 1
                elapsed = time.time() - started
                left = time_left(elapsed, recognised, len(todo) - done_now)
                progress(f"📄 page {n}/{numbers[-1]} · accurate · {_fmt(elapsed)} elapsed"
                         + (f" · ~{_fmt(left)} left" if left is not None else ""))
    finally:
        stop(engine)

    # 3. the book
    with tempfile.TemporaryDirectory(prefix="pdf2epub-") as tmp_name:
        tmp = Path(tmp_name)
        cover = base.render_page(pdf, 1, tmp / "cover", base.COVER_HEIGHT)
        big = any(w * h >= 0.5 * pages[0].width * pages[0].height for w, h in pictures.get(1, []))
        skip_first = cover is not None and first_page_is_cover(merged_file(1).read_text(encoding="utf-8"), big)
        blocks: list[Block] = []
        for n in numbers:
            if skip_first and n == 1:
                continue
            blocks.append(Block("page", level=n, page=n))
            blocks += page_blocks(merged_file(n).read_text(encoding="utf-8"), n)
        blocks = join_pages(blocks)
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
        chapters = base.split_chapters(blocks, title, main)
        if not chapters:
            raise RuntimeError("no text or pictures found")
        base.write_epub(out, title, author, lang, chapters, cover, img_names, tocs, main,
                        generator="pdf2epub hybrid")
    (work / "done").write_text("")
    log(f"done {out}")

    sources = base.Counter(p["source"] for p in report["pages"].values())
    fixed = sum(p.get("fixed_words", 0) for p in report["pages"].values())
    words = sum(len(b.text.split()) for b in blocks if b.kind in ("p", "list"))
    parts = ", ".join(f"{k} {v}" for k, v in sorted(sources.items()))
    check = to_check(report)
    return (f"{len(chapters)} chapter(s), {words} words; pages: {parts}; {fixed} word(s) corrected"
            + (f"; check: {', '.join(check)}" if check else "") + f" — «{title}»"
            + (f", {author}" if author else ""))

