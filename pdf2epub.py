#!/usr/bin/env python3
"""Turn PDF books into EPUB.

Usage:
    python3 pdf2epub.py [PDF or FOLDER ...] [--out DIR] [--title T] [--author A]
                        [--lang CODE] [--force] [--yes] [--quick] [--pages N] [--keep-work]

By default every .pdf in ./Pdf is converted into ./In/<same name>.epub, so that
epub_rename.py can then sort the new books into Out/ like any other EPUB.
The PDF files are only read, never changed.

Every page is recognised from its picture by Marker (free, offline), read twice (fast and
accurate) and checked against the PDF text layer (pdf2epub_parts/). The first run
installs the engine into ~/.local/share/pdf2epub (about 3 GB, one time); a stopped run goes
on from the same page. --quick, or a missing engine, uses only the PDF text layer.

What ends up in the book:
  - the text of every page (also the hidden text layer of scanned and recognised
    books), joined into paragraphs: line breaks and word hyphenation of the printed
    page are removed, running headers, footers and page numbers are dropped;
  - bold and italic;
  - the table of contents as printed in the book (entries, nesting, page numbers), linked
    to the places in the text; the printed contents pages become clickable lists;
  - headings, found by their larger font or as bold lines standing apart; without printed
    contents, the biggest ones start new chapters and make the table of contents;
  - pictures that sit among the text; a page that has no text at all (a scan
    without recognised text, a drawing, a table as a picture) is put in as a picture
    of the whole page;
  - the first page as the cover;
  - title and author from the PDF properties or from a file name like
    "Author - Title.pdf"; when the author is unknown the script asks (or use
    --title / --author, --yes to never ask).

A book that already exists in the output folder is skipped (--force overwrites it).

--pages N is a trial: only the first N pages, into <out>/Preview/ (out of the way of
epub_rename.py and of the whole book). The pages it recognises are kept, so the whole book
later goes on from them.

Needs Poppler (the pdftohtml, pdftoppm, pdfinfo and pdfimages programs):
    Linux:   sudo apt install poppler-utils   (or dnf/pacman install poppler)
    macOS:   brew install poppler
    Windows: download poppler (https://github.com/oschwartz10612/poppler-windows/releases),
             unpack and add its Library/bin folder to PATH
"""

import argparse
import difflib
import html
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path.cwd()                  # Pdf/ and In/ are taken from the current folder
PDF_DIR = BASE_DIR / "Pdf"
OUT_DIR = BASE_DIR / "In"
TOOLS = ("pdftohtml", "pdftoppm", "pdfinfo", "pdfimages")

HEADING_RATIO = 1.15      # a line this much bigger than the body text is a heading
CHAPTER_RATIO = 1.45      # ... and this much bigger starts a new chapter
HEADER_BAND = 0.10        # running headers/footers live in the top/bottom 10% of the page
FULL_PAGE = 0.8           # a picture covering 80% of the page is a page scan, not a figure
PAGES_PER_PART = 20       # books without any headings are cut into parts of this many pages
COVER_HEIGHT = 1600       # px
PAGE_IMAGE_HEIGHT = 1600  # px, pages put in as pictures

TERMINAL = tuple(".!?…:;»\"”)")
BULLET_RE = re.compile(r"^\s*([•●▪■□◦○‣∙·–—-]|\d{1,3}[.)]\s|[а-яa-z][)]\s)", re.I)


# ---------------------------------------------------------------- small helpers

def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, check=False, **kw)


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def clean_xml_text(data: bytes) -> str:
    """pdftohtml sometimes writes characters XML doesn't allow; drop them."""
    text = data.decode("utf-8", errors="replace")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "", text)


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return name[:150] or "book"


# ---------------------------------------------------------------- reading the PDF

@dataclass
class Piece:
    """One run of text from pdftohtml, with its place on the page."""
    top: float
    left: float
    width: float
    height: float
    size: float
    runs: list            # [(text, bold, italic)]

    @property
    def text(self) -> str:
        return "".join(t for t, _, _ in self.runs)

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def right(self) -> float:
        return self.left + self.width


@dataclass
class Line:
    pieces: list
    page: int
    kind: str = "text"    # text | heading
    level: int = 0

    @property
    def top(self): return min(p.top for p in self.pieces)
    @property
    def base(self):
        """Where most of the line sits (a big first letter hanging above doesn't count)."""
        return statistics.median(p.bottom for p in self.pieces)
    @property
    def bottom(self): return max(p.bottom for p in self.pieces)
    @property
    def left(self): return min(p.left for p in self.pieces)
    @property
    def right(self): return max(p.right for p in self.pieces)
    @property
    def height(self): return self.bottom - self.top

    @property
    def size(self) -> float:
        """Font size of most of the letters (a big first letter doesn't count)."""
        sizes = Counter()
        for p in self.pieces:
            sizes[p.size] += max(1, len(p.text.strip()))
        return weighted_median(sizes)

    def runs(self) -> list:
        """Text runs with spaces put back between pieces that stand apart."""
        out, prev = [], None
        for p in sorted(self.pieces, key=lambda p: p.left):
            text = p.text
            if prev is not None and text:
                gap = p.left - prev.right
                if gap > 0.08 * min(p.height, prev.height) and not re.match(r"[,.;:!?)\]»”]", text) \
                        and not re.search(r"[(\[«“]$", prev.text):
                    out.append((" ", False, False))
            out.extend(p.runs)
            prev = p
        return out

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(t for t, _, _ in self.runs())).strip()

    @property
    def all_bold(self) -> bool:
        """Bold, apart from a footnote number or a stray comma."""
        bold = plain = 0
        for t, b, _ in self.runs():
            n = len(re.findall(r"\w", t))
            if b:
                bold += n
            else:
                plain += n
        return bold > 0 and plain <= max(1, bold // 15)


def weighted_median(counts: Counter) -> float:
    total, acc = sum(counts.values()), 0
    for value in sorted(counts):
        acc += counts[value]
        if acc * 2 >= total:
            return value
    return 0


def element_runs(el, bold=False, italic=False) -> list:
    """<text> content with <b>/<i> inside it -> [(text, bold, italic)]."""
    bold = bold or el.tag == "b"
    italic = italic or el.tag == "i"
    runs = [(el.text, bold, italic)] if el.text else []
    for child in el:
        runs += element_runs(child, bold, italic)
        if child.tail:
            runs.append((child.tail, bold, italic))
    return runs


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list = field(default_factory=list)
    figures: list = field(default_factory=list)   # [(top, path)]
    as_picture: str | None = None                  # the whole page as a picture
    printed: int | None = None                     # page number printed on the page
    toc: int | None = None                         # index of the printed contents on this page


def read_text(pdf: Path, tmp: Path) -> list[Page]:
    """Text of every page with positions, via `pdftohtml -xml`."""
    base = tmp / "text"
    res = run(["pdftohtml", "-xml", "-i", "-hidden", "-zoom", "1", "-q", "-nodrm", "-enc", "UTF-8", str(pdf), str(base)])
    xml_file = base.with_suffix(".xml")
    if not xml_file.exists():
        raise RuntimeError("pdftohtml failed: " + res.stderr.decode(errors="replace").strip())
    root = ET.fromstring(clean_xml_text(xml_file.read_bytes()))
    sizes, pages = {}, []
    for page_el in root.iter("page"):
        for fs in page_el.iter("fontspec"):
            sizes[fs.get("id")] = float(fs.get("size", "0"))
        page = Page(int(page_el.get("number")), float(page_el.get("width")), float(page_el.get("height")))
        pieces = []
        for t in page_el.iter("text"):
            runs = [(s.replace("\xa0", " "), b, i) for s, b, i in element_runs(t)]
            if not "".join(s for s, _, _ in runs).strip():
                continue
            pieces.append(Piece(float(t.get("top")), float(t.get("left")), float(t.get("width")),
                                float(t.get("height")), sizes.get(t.get("font"), 0.0), runs))
        page.lines = group_lines(pieces, page.number)
        pages.append(page)
    return pages


def group_lines(pieces: list[Piece], page: int) -> list[Line]:
    """Pieces that share most of their height make one line."""
    lines: list[Line] = []
    for p in sorted(pieces, key=lambda p: (p.top + p.bottom) / 2):
        best, best_overlap = None, 0.5
        for line in lines[-4:]:
            top, bottom = line.top, line.bottom
            overlap = (min(bottom, p.bottom) - max(top, p.top)) / max(1, min(p.height, bottom - top))
            if overlap > best_overlap:
                best, best_overlap = line, overlap
        if best:
            best.pieces.append(p)
        else:
            lines.append(Line([p], page))
    return sorted(lines, key=lambda l: (l.top, l.left))


def read_images(pdf: Path) -> dict[int, list[tuple[float, float]]]:
    """Size in points of every picture on every page, via `pdfimages -list` (nothing is extracted)."""
    res = run(["pdfimages", "-list", str(pdf)])
    found: dict[int, list] = {}
    for row in res.stdout.decode(errors="replace").splitlines()[2:]:
        cols = row.split()
        if len(cols) < 14 or cols[2] != "image":
            continue
        try:
            page, w, h, xppi, yppi = int(cols[0]), int(cols[3]), int(cols[4]), float(cols[12]), float(cols[13])
        except ValueError:
            continue
        if w < 32 or h < 32 or xppi <= 0 or yppi <= 0:
            continue
        found.setdefault(page, []).append((w / xppi * 72, h / yppi * 72))
    return found


def page_figures(pdf: Path, page: Page, tmp: Path) -> list[tuple[float, str]]:
    """Pictures of one page that are not the page scan itself: [(top, file)]."""
    base = tmp / f"fig{page.number}"
    run(["pdftohtml", "-xml", "-hidden", "-zoom", "1", "-q", "-nodrm", "-enc", "UTF-8", "-f", str(page.number), "-l", str(page.number),
         str(pdf), str(base)])
    xml_file = base.with_suffix(".xml")
    if not xml_file.exists():
        return []
    root = ET.fromstring(clean_xml_text(xml_file.read_bytes()))
    out = []
    for img in root.iter("image"):
        w, h = float(img.get("width", 0)), float(img.get("height", 0))
        if w >= FULL_PAGE * page.width and h >= FULL_PAGE * page.height:
            continue                    # the scan under recognised text
        if w < 24 or h < 24:
            continue                    # bullets, rules and other specks
        src = Path(img.get("src", ""))
        src = src if src.is_absolute() else xml_file.parent / src
        if src.is_file():
            out.append((float(img.get("top", 0)), str(src)))
    return out


def render_page(pdf: Path, number: int, out: Path, height: int) -> Path | None:
    """The page as a JPEG picture, via `pdftoppm`."""
    res = run(["pdftoppm", "-jpeg", "-jpegopt", "quality=85", "-scale-to-y", str(height), "-scale-to-x", "-1",
               "-f", str(number), "-l", str(number), "-singlefile", str(pdf), str(out)])
    path = out.with_suffix(".jpg")
    return path if res.returncode == 0 and path.is_file() else None


def read_info(pdf: Path) -> dict[str, str]:
    res = run(["pdfinfo", "-enc", "UTF-8", str(pdf)])
    info = {}
    for row in res.stdout.decode("utf-8", errors="replace").splitlines():
        key, sep, value = row.partition(":")
        if sep:
            info[key.strip()] = value.strip()
    return info


# ---------------------------------------------------------------- page analysis

def drop_headers_footers(pages: list[Page], body: float) -> None:
    """Remove running headers, footers and page numbers: small lines at the very top or
    bottom whose text (without digits) is just a number or repeats on several pages."""
    def key(line: Line) -> str:
        return re.sub(r"[\d\W_]+", " ", line.text.lower()).strip()

    candidates = []
    for page in pages:
        if not page.lines:
            continue
        for line in (page.lines[0], page.lines[-1]):
            in_band = line.bottom < page.height * HEADER_BAND * 1.4 or line.top > page.height * (1 - HEADER_BAND)
            if in_band and line.size <= body * 1.1 and len(line.text) < 120:
                candidates.append((page, line))
    counts = Counter(key(l) for _, l in candidates)
    for page, line in candidates:
        k = key(line)
        if (not k or counts[k] >= 3 or re.fullmatch(r"[ivxlcdm]+", k)) and line in page.lines:
            page.lines.remove(line)
            m = re.match(r"^\D{0,40}?\b(\d{1,4})\b\D*$", line.text)
            if m and page.printed is None:
                page.printed = int(m.group(1))


def find_headings(pages: list[Page], body: float) -> None:
    lines = [l for p in pages for l in p.lines]
    for line in lines:
        text = line.text
        if not re.search(r"\w", text) or len(text.split()) > 20 or text.endswith((",", ";")) \
                or text[:1].islower():      # a heading doesn't start in the middle of a sentence
            continue
        if line.size >= body * HEADING_RATIO:
            line.kind = "heading"
            line.level = 1 if line.size >= body * CHAPTER_RATIO else 2
    # Several heading lines in a row are one heading; it is a chapter if any of its lines is
    for page in pages:
        group: list[Line] = []
        for line in page.lines + [None]:
            if line is not None and line.kind == "heading" and \
                    (not group or line.top - group[-1].bottom < 3 * group[-1].height):
                group.append(line)
                continue
            if group:
                level = min(l.level for l in group)
                for l in group:
                    l.level = level
            group = [line] if line is not None and line.kind == "heading" else []

    # Bold lines of body size standing on their own (1-3 lines, no full stop at the end,
    # plain text right after them) are subheadings
    for page in pages:
        lines = page.lines
        i = 0
        while i < len(lines):
            if lines[i].kind != "text" or not lines[i].all_bold:
                i += 1
                continue
            j = i
            while j + 1 < len(lines) and lines[j + 1].kind == "text" and lines[j + 1].all_bold \
                    and lines[j + 1].top - lines[j].bottom < 2 * lines[j].height:
                j += 1
            group = lines[i:j + 1]
            text = " ".join(l.text for l in group)
            after = lines[j + 1] if j + 1 < len(lines) else None
            goes_on = after is not None and (after.all_bold or after.text[:1].islower()
                                             or after.text[:1] in ",;:)»”")
            if len(group) <= 3 and len(text.split()) <= 20 and text[:1].isupper() \
                    and not re.search(r"[.,;:…\-\u00ad–—]\s*\d{0,3}$", text) and not goes_on:
                for l in group:
                    l.kind, l.level = "heading", 3
            i = j + 1


# ---------------------------------------------------------------- printed table of contents

@dataclass
class TocEntry:
    title: str
    number: int         # page number printed in the contents
    left: float         # indent of its first line
    page: int           # the PDF page the entry is printed on
    level: int = 1
    anchor: str = ""    # id of the place in the text it leads to


@dataclass
class Toc:
    title: str          # "Contents", as printed above the list
    entries: list
    pages: list         # PDF pages it takes
    anchor: str = ""


def toc_lines(page: Page, body: float) -> list[tuple[Line, str, int | None]]:
    """Lines of a page as (line, text, page number at its right end or None)."""
    rows = []
    for line in page.lines:
        pieces = sorted(line.pieces, key=lambda p: p.left)
        m = re.fullmatch(r"(.*?\w.*?)[\s.…·_]*?(\d{1,4})", line.text)
        sure = False
        if m and len(pieces) > 1 and re.fullmatch(r"[.…·_\s]*\d{1,4}", pieces[-1].text.strip()):
            gap = pieces[-1].left - max(p.right for p in pieces[:-1])
            sure = gap > 1.5 * body or bool(re.search(r"[.…·_]{3,}\s*\d+$", line.text))
        rows.append((line, m, sure))
    # numbers stand in one column at the right; a number at that edge is a page number
    # even when it sits close to the text
    edges = [l.right for l, _, sure in rows if sure]
    edge = statistics.median(edges) if len(edges) >= 3 else None
    out = []
    for line, m, sure in rows:
        if m and (sure or (edge is not None and abs(line.right - edge) < 1.5 * body
                           and re.search(r"\s\d{1,4}$", line.text))):
            out.append((line, m.group(1).strip(), int(m.group(2))))
        else:
            out.append((line, line.text, None))
    return out


def find_tocs(pages: list[Page], body: float) -> list[Toc]:
    """Printed tables of contents: pages where most lines end in a page number at the right edge."""
    def is_toc_page(page):
        rows = [r for r in toc_lines(page, body) if re.search(r"\w", r[1])]
        numbered = [r for r in rows if r[2] is not None]
        return len(numbered) >= 4 and len(numbered) >= 0.4 * len(rows)

    groups: list[list[Page]] = []
    for page in pages[: max(30, len(pages) // 5)] + pages[-max(15, len(pages) // 10):]:
        if not is_toc_page(page):
            continue
        if groups and groups[-1][-1].number == page.number - 1:
            groups[-1].append(page)
        elif not groups or page.number > groups[-1][-1].number:
            groups.append([page])

    def clean(text):
        text = re.sub(r"\s+([,.:;!?])", r"\1", re.sub(r"\s+", " ", text))
        text = re.sub(r"([,:;!?])(?=[^\W\d_])|(\.)(?=[^\W\d_]{2})", r"\1\2 ", text)  # "9.ТЕКСТ" -> "9. ТЕКСТ"
        return text.strip(" .")

    tocs: list[Toc] = []
    for group in groups:
        current = Toc("", [], [])
        found = [current]
        pending: list[Line] = []     # lines of a title that goes on to the next line
        loose: list[str] = []        # lines standing apart: the heading of a new contents
        for page in group:
            pending = []
            for line, text, number in toc_lines(page, body):
                if not re.search(r"\w", text):
                    continue                                # dot leaders, stray marks
                near = pending[-1] if pending else None
                if near is not None and line.top - near.bottom > 1.2 * near.height:
                    loose = [l.text for l in pending]       # not part of the next entry
                    pending = []
                if number is None:
                    pending.append(line)
                    continue
                if not current.entries and loose and not current.title:
                    current.title = " ".join(loose)
                if current.entries and number < current.entries[-1].number - 10:
                    current = Toc(" ".join(loose), [], [])  # page numbers start again: another contents
                    found.append(current)
                loose = []
                first = pending[0] if pending else line
                full = clean(" ".join([l.text for l in pending] + [text]))
                current.entries.append(TocEntry(full, number, first.left, page.number))
                if page.number not in current.pages:
                    current.pages.append(page.number)
                pending = []
            if not current.entries and pending:
                loose = [l.text for l in pending]
        for toc in found:
            numbers = [e.number for e in toc.entries]
            rising = sum(1 for a, b in zip(numbers, numbers[1:]) if b >= a)
            if len(toc.entries) < 4 or rising < 0.7 * (len(toc.entries) - 1):
                continue
            set_levels(toc.entries, body)
            toc.title = clean(toc.title) or "Contents"
            tocs.append(toc)
    return tocs


def set_levels(entries: list[TocEntry], body: float) -> None:
    """Nesting from the indent; odd and even pages may have different margins."""
    base = {}
    for e in entries:
        base[e.page % 2] = min(base.get(e.page % 2, e.left), e.left)
    shifts = sorted({round(e.left - base[e.page % 2], 1) for e in entries})
    steps = [shifts[0]] if shifts else []
    for s in shifts[1:]:
        if s - steps[-1] > 0.6 * body:
            steps.append(s)
    for e in entries:
        shift = e.left - base[e.page % 2]
        e.level = min(3, 1 + sum(1 for s in steps[1:] if shift >= s - 0.3 * body))


def page_offset(pages: list[Page], tocs: list[Toc], blocks: list) -> int:
    """PDF page = printed page + offset; from the page numbers printed in headers and footers,
    or else from where the contents entries are found as headings."""
    found = Counter(p.number - p.printed for p in pages if p.printed is not None)
    if found and found.most_common(1)[0][1] >= 3:
        return found.most_common(1)[0][0]
    guesses = Counter()
    for toc in tocs:
        for e in toc.entries:
            for b in blocks:
                if b.kind == "h" and similarity(e.title, b.text) >= 0.85:
                    guesses[b.page - e.number] += 1
    return guesses.most_common(1)[0][0] if guesses else 0


def norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).strip()


def similarity(title: str, text: str) -> float:
    """How well a contents entry matches the beginning of a heading or paragraph."""
    a, b = norm(title), norm(text)
    if len(b) < min(len(a), 8) or not a:
        return 0.0
    if len(b) < len(a):
        a = a[:len(b)]          # a heading may be shorter than the entry ("ВВЕДЕНИЕ")
    return difflib.SequenceMatcher(None, a, b[:len(a)]).ratio()


def anchor_tocs(blocks: list, tocs: list[Toc], offset: int) -> None:
    """Find the place in the text every contents entry leads to and give it an id."""
    count = 0

    def anchor_of(block) -> str:
        nonlocal count
        if not block.anchor:
            count += 1
            block.anchor = f"a{count}"
        return block.anchor

    for toc in tocs:
        for i, b in enumerate(blocks):
            if b.kind == "toc" and b.level == tocs.index(toc):
                title = blocks[i - 1] if i and blocks[i - 1].kind == "h" else b   # "Contents" above the list
                toc.anchor = anchor_of(title)
        toc_pages = set(toc.pages)
        start = 0
        for e in toc.entries:
            target = e.number + offset
            best, best_score = None, 0.0
            for i in range(start, len(blocks)):
                b = blocks[i]
                if b.page > target + 1:
                    break
                if b.kind not in ("h", "p") or b.page < target - 1 or b.page in toc_pages:
                    continue
                score = similarity(e.title, b.text) + (0.08 if b.kind == "h" else 0) - 0.04 * abs(b.page - target)
                if score > best_score:
                    best, best_score = i, score
            if best is None or best_score < 0.7:
                # not found as text: the start of the page it is on
                best = next((i for i in range(start, len(blocks))
                             if blocks[i].page >= target and blocks[i].kind in ("h", "p", "img")), None)
            if best is not None:
                b = blocks[best]
                e.anchor = anchor_of(b)
                start = best
                if b.kind == "p" and best_score >= 0.7 and len(norm(b.text)) <= 1.3 * len(norm(e.title)) + 5:
                    b.kind = "h"        # a section title the contents lists: show it as a heading
                    b.level = 2 if e.level <= 2 else 3


# ---------------------------------------------------------------- building the text

@dataclass
class Block:
    kind: str           # p | h | img | page | toc | list | table | note (a footnote) | endnote
    html: str = ""
    text: str = ""
    level: int = 0
    src: str = ""
    page: int = 0       # PDF page it starts on
    anchor: str = ""    # id, when the contents link here
    items: list = field(default_factory=list)   # list: html of each item
    rows: list = field(default_factory=list)    # table: html of each cell, row by row


def runs_html(runs: list) -> str:
    out, b_open, i_open = [], False, False
    for text, b, i in runs:
        if not text:
            continue
        if text.strip():   # spaces don't switch styles
            if i_open and not i:
                out.append("</i>"); i_open = False
            if b_open and not b:
                if i_open:
                    out.append("</i>"); i_open = False
                out.append("</b>"); b_open = False
            if b and not b_open:
                if i_open:
                    out.append("</i>"); i_open = False
                out.append("<b>"); b_open = True
            if i and not i_open:
                out.append("<i>"); i_open = True
        out.append(esc(text))
    if i_open:
        out.append("</i>")
    if b_open:
        out.append("</b>")
    return re.sub(r"\s+", " ", "".join(out)).strip()


class ParagraphBuilder:
    def __init__(self):
        self.blocks: list[Block] = []
        self.runs: list = []
        self.last: Line | None = None
        self.page = 0

    def flush(self):
        if self.runs:
            html_text = runs_html(self.runs)
            if html_text:
                text = re.sub(r"\s+", " ", "".join(t for t, _, _ in self.runs)).strip()
                self.blocks.append(Block("p", html_text, text, page=self.page))
        self.runs, self.last = [], None

    def add_line(self, line: Line):
        runs = line.runs()
        if not self.runs:
            self.page = line.page
        if self.runs:
            prev_text = "".join(t for t, _, _ in self.runs).rstrip()
            m = re.search(r"(\w)\s?[-­]$", prev_text)
            next_text = line.text
            if m and next_text[:1].islower():
                # word hyphenated at the end of the line: glue the halves together
                self.strip_hyphen()
            elif prev_text.endswith(("—", "–")) or not prev_text.endswith(("-", "­")):
                self.runs.append((" ", False, False))
        self.runs.extend(runs)
        self.last = line

    def strip_hyphen(self):
        while self.runs:
            text, b, i = self.runs[-1]
            stripped = text.rstrip()
            if not stripped:
                self.runs.pop()
                continue
            stripped = re.sub(r"\s?[-­]$", "", stripped)
            self.runs[-1] = (stripped, b, i)
            return

    def ended(self) -> bool:
        text = "".join(t for t, _, _ in self.runs).rstrip()
        return not text or text.endswith(TERMINAL)


def column_edges(page: Page, fallback: tuple[float, float]) -> tuple[float, float]:
    """Left and right edge of the text column: where full (justified) lines start and end."""
    lines = [l for l in page.lines if l.kind == "text"]
    if not lines:
        return fallback
    span = max(l.right for l in lines) - min(l.left for l in lines)
    full = [l for l in lines if l.right - l.left >= 0.75 * span]
    if len(full) < 3:
        return fallback
    left = Counter(round(l.left / 3) * 3 for l in full).most_common(1)[0][0]
    right = statistics.median(l.right for l in full)
    return left, right


def build_blocks(pages: list[Page], body: float, skip_page: int | None, tocs: list) -> list[Block]:
    text_lines = [l for p in pages for l in p.lines if l.kind == "text"]
    steps = [b.base - a.base for a, b in zip(text_lines, text_lines[1:])
             if a.page == b.page and 0 < b.base - a.base < 4 * body]
    lead = statistics.median(steps) if steps else body * 1.3
    all_full = [column_edges(p, (0, 0)) for p in pages]
    known = [e for e in all_full if e != (0, 0)]
    fallback = (statistics.median(e[0] for e in known), statistics.median(e[1] for e in known)) if known \
        else (0, max((p.width for p in pages), default=600))

    pb = ParagraphBuilder()
    for page in pages:
        if page.number == skip_page:
            continue
        pb.blocks.append(Block("page", level=page.number, page=page.number))
        if page.as_picture:
            pb.flush()
            pb.blocks.append(Block("img", src=page.as_picture, text=f"page {page.number}", level=1, page=page.number))
            continue
        if page.toc is not None:
            # printed contents: replaced by a list of links, made when the book is written
            pb.flush()
            toc = tocs[page.toc]
            if page.number == toc.pages[0]:
                pb.blocks.append(Block("h", esc(toc.title), toc.title, 1, page=page.number))
                pb.blocks.append(Block("toc", level=page.toc, page=page.number))
            continue
        col_left, col_right = column_edges(page, fallback)
        items = [(l.top, l) for l in page.lines] + [(top, src) for top, src in page.figures]
        items.sort(key=lambda x: x[0])
        prev: Line | None = None
        first = True
        heading: list[Line] = []

        def close_heading():
            if heading:
                parts = []
                for l in heading:
                    if parts and re.fullmatch(r"\d{1,3}|[IVXLC]{1,6}", l.text):
                        parts[-1] += " " + l.text
                    else:
                        parts.append(l.text)
                text = " ".join(parts)
                text = re.sub(r"(?<=[^\W\d_]|[?!)»”])\d{1,2}$", "", text)    # footnote mark
                if len(parts) > 1 and re.fullmatch(r"\S+\s+(\d+|[IVXLC]+)", parts[0]):
                    text = parts[0] + ". " + " ".join(parts[1:])     # "ШАГ 1" + "TITLE"
                rows = []
                for l in heading:
                    if rows and re.fullmatch(r"\d{1,3}|[IVXLC]{1,6}", l.text):
                        rows[-1] += " " + runs_html(l.runs())
                    else:
                        rows.append(runs_html(l.runs()))
                html_text = "<br/>".join(rows).replace("</b> <b>", " ")
                pb.blocks.append(Block("h", html_text, text, heading[0].level, page=heading[0].page))
                heading.clear()

        for _, item in items:
            if isinstance(item, str):                       # a picture among the text
                close_heading()
                pb.flush()
                pb.blocks.append(Block("img", src=item, page=page.number))
                prev = None
                continue
            line = item
            if line.kind == "heading":
                if heading and heading[-1].level != line.level:
                    close_heading()
                if not heading:
                    pb.flush()
                heading.append(line)
                prev = None
                first = False
                continue
            close_heading()
            size = line.size or body
            indented = line.left > col_left + 0.8 * size
            starts_upper = line.text[:1].isupper() or line.text[:1].isdigit()
            if not pb.runs:
                new = True
            elif first:
                # first line of the page: continues the last paragraph unless that one was finished
                new = indented or (pb.ended() and starts_upper)
            elif prev is None:
                new = True
            else:
                gap = line.base - prev.base
                prev_short = prev.right < col_right - 3 * size
                new = (gap > 1.6 * lead
                       or (indented and line.left > prev.left + 0.8 * size)
                       or (prev_short and (pb.ended() or starts_upper)))
                if line.text[:1].islower() and not (prev_short and pb.ended()):
                    new = False     # a sentence going on
                new = new or bool(BULLET_RE.match(line.text))
            if new:
                pb.flush()
            pb.add_line(line)
            prev = line
            first = False
        close_heading()
        if pb.blocks and pb.blocks[-1].kind == "h":
            pb.flush()
    pb.flush()
    # paragraphs were flushed lazily: reorder so that each paragraph stays where it started
    return fix_order(pb.blocks)


def fix_order(blocks: list[Block]) -> list[Block]:
    """A paragraph that runs over a page break is added after the next page marker;
    move page markers that ended up inside such paragraphs behind them."""
    out: list[Block] = []
    pending: list[Block] = []
    for b in blocks:
        if b.kind == "page":
            pending.append(b)
        elif b.kind == "p" and pending and out and out[-1].kind == "p":
            out.append(b)
            out.extend(pending)
            pending = []
        else:
            out.extend(pending)
            pending = []
            out.append(b)
    return out + pending


# ---------------------------------------------------------------- writing the EPUB

CSS = """\
body { margin: 0 4%; line-height: 1.45; }
p { margin: 0; text-indent: 1.3em; text-align: justify;
    -webkit-hyphens: auto; hyphens: auto; adobe-hyphenate: auto; }
h1, h2, h3 { text-align: center; line-height: 1.25; margin: 1.6em 0 0.9em; page-break-after: avoid; }
h1 { font-size: 1.5em; page-break-before: always; }
h2 { font-size: 1.2em; }
div.figure { text-align: center; margin: 1em 0; page-break-inside: avoid; }
div.figure img { max-width: 100%; max-height: 95vh; }
div.page { text-align: center; page-break-before: always; page-break-after: always; margin: 0; }
div.page img { max-width: 100%; max-height: 98vh; }
div.cover { text-align: center; margin: 0; padding: 0; }
div.cover img { max-width: 100%; max-height: 100vh; }
div.toc p { text-indent: 0; text-align: left; margin: 0.3em 0;
    -webkit-hyphens: manual; hyphens: manual; adobe-hyphenate: none; }
div.toc p.toc1 { margin-top: 0.8em; font-weight: bold; }
div.toc p.toc2 { padding-left: 1.2em; }
div.toc p.toc3 { padding-left: 2.4em; }
div.toc a { text-decoration: none; color: inherit; }
div.toc span.pg { float: right; padding-left: 1em; font-weight: normal; }
ul.list { list-style: none; padding-left: 1.2em; margin: 0.5em 0; }
ul.list li { margin: 0.2em 0; text-indent: -1.2em; padding-left: 1.2em; }
table { border-collapse: collapse; margin: 1em auto; }
td { border: 1px solid #999; padding: 0.2em 0.4em; vertical-align: top; }
aside.note { font-size: 0.85em; margin: 0.4em 0 0.6em; }
aside.note p { text-indent: 0; }
div.endnote p { text-indent: 0; font-size: 0.9em; margin: 0.3em 0; }
a[href^="#fn"], aside.note a { text-decoration: none; }
"""


def xhtml(title: str, body: str, lang: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n<title>{esc(title)}</title>\n'
            f'<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n<body>\n{body}\n</body>\n</html>\n')


@dataclass
class Chapter:
    title: str
    blocks: list
    file: str = ""


def split_chapters(blocks: list[Block], book_title: str, main: "Toc | None" = None) -> list[Chapter]:
    levels = sorted({b.level for b in blocks if b.kind == "h"})
    if main is not None:
        # chapters as in the printed contents: a new file at every top-level entry
        top = min(e.level for e in main.entries)
        starts = {e.anchor: e.title for e in main.entries if e.level == top and e.anchor}
        if main.anchor:
            starts.setdefault(main.anchor, main.title)
        chapters = [Chapter(book_title, [])]
        for b in blocks:
            if b.anchor in starts and any(x.kind != "page" for x in chapters[-1].blocks):
                chapters.append(Chapter(starts[b.anchor], []))
            elif b.anchor in starts:
                chapters[-1].title = starts[b.anchor]
            chapters[-1].blocks.append(b)
    elif levels:
        top = levels[0]
        chapters = [Chapter(book_title, [])]
        for b in blocks:
            if b.kind == "h" and b.level == top:
                chapters.append(Chapter(b.text, []))
            chapters[-1].blocks.append(b)
    else:   # no headings at all: parts of PAGES_PER_PART pages
        chapters, start = [], None
        for b in blocks:
            if b.kind == "page" and (start is None or b.level - start >= PAGES_PER_PART):
                start = b.level
                chapters.append(Chapter(f"{start}", []))
            if chapters:
                chapters[-1].blocks.append(b)
        for c in chapters:
            pages = [b.level for b in c.blocks if b.kind == "page"]
            c.title = f"{pages[0]}–{pages[-1]}" if pages and pages[0] != pages[-1] else str(pages[0] if pages else "")
    chapters = [c for c in chapters if any(b.kind in ("p", "h", "img") for b in c.blocks)]
    for n, c in enumerate(chapters, 1):
        c.file = f"text{n:03d}.xhtml"
    return chapters


def detect_lang(text: str) -> str:
    cyr = len(re.findall(r"[а-яёіїєґ]", text, re.I))
    lat = len(re.findall(r"[a-z]", text, re.I))
    if cyr > lat:
        uk = len(re.findall(r"[іїєґ]", text, re.I))
        return "uk" if uk > cyr * 0.01 else "ru"
    return "en"


def write_epub(out: Path, title: str, author: str, lang: str, chapters: list[Chapter],
               cover: Path | None, images: dict[str, str], tocs: list, main,
               generator: str = "pdf2epub quick") -> None:
    """images: source file -> name inside the book (images/...)."""
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest, spine, files = [], [], {}

    files["style.css"] = CSS.encode()
    manifest.append('<item id="css" href="style.css" media-type="text/css"/>')
    if cover:
        manifest.append('<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
        files["cover.xhtml"] = xhtml(title, '<div class="cover"><img src="images/cover.jpg" alt="cover"/></div>',
                                     lang).encode()
        manifest.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover"/>')

    where = {b.anchor: ch.file for ch in chapters for b in ch.blocks if b.anchor}
    for ch in chapters:                    # places inside the text (the references to notes)
        for b in ch.blocks:
            for anchor in re.findall(r'\bid="([\w-]+)"', b.html + "".join(b.items)):
                where.setdefault(anchor, ch.file)

    def href(anchor: str) -> str | None:
        return f"{where[anchor]}#{anchor}" if anchor in where else None

    def contents_list(toc: Toc, id_attr: str) -> str:
        rows = []
        for e in toc.entries:
            link = href(e.anchor)
            name = f'<a href="{link}">{esc(e.title)}</a>' if link else esc(e.title)
            rows.append(f'<p class="toc{e.level}"><span class="pg">{e.number}</span>{name}</p>')
        return f'<div class="toc"{id_attr}>\n' + "\n".join(rows) + "\n</div>"

    toc = []   # (level, title, href) made from headings, when there is no printed contents
    hid = 0
    for n, ch in enumerate(chapters, 1):
        parts = []
        for b in ch.blocks:
            if b.kind == "h" and not b.anchor:
                hid += 1
                b.anchor = f"h{hid}"
            id_attr = f' id="{b.anchor}"' if b.anchor else ""
            if b.kind == "p":
                parts.append(f"<p{id_attr}>{b.html}</p>")
            elif b.kind == "endnote":
                parts.append(f'<div class="endnote" epub:type="endnote"{id_attr}><p>{b.html}</p></div>')
            elif b.kind == "note":
                parts.append(f'<aside epub:type="footnote" class="note"{id_attr}><p>{b.html}</p></aside>')
            elif b.kind == "h":
                tag = "h1" if b.level == 1 else "h2" if b.level == 2 else "h3"
                parts.append(f"<{tag}{id_attr}>{b.html}</{tag}>")
                toc.append((b.level, b.text, f"{ch.file}#{b.anchor}"))
            elif b.kind == "img":
                cls = "page" if b.level == 1 else "figure"
                parts.append(f'<div class="{cls}"{id_attr}><img src="{images[b.src]}" alt="{esc(b.text)}"/></div>')
            elif b.kind == "toc":
                parts.append(contents_list(tocs[b.level], id_attr))
            elif b.kind == "list":
                parts.append(f'<ul class="list"{id_attr}>' + "".join(f"<li>{i}</li>" for i in b.items) + "</ul>")
            elif b.kind == "table":
                rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in b.rows)
                parts.append(f"<table{id_attr}>{rows}</table>")
        if not any(t[2].startswith(ch.file + "#") for t in toc):
            toc.append((1, ch.title, ch.file))
        body = re.sub(r'href="#([\w-]+)"', lambda m: f'href="{href(m.group(1))}"' if where.get(m.group(1), ch.file)
                      != ch.file else m.group(0), "\n".join(parts))      # a note in another file
        files[ch.file] = xhtml(ch.title, body, lang).encode()
        manifest.append(f'<item id="c{n}" href="{ch.file}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{n}"/>')
    for n, (src, name) in enumerate(images.items(), 1):
        ext = Path(name).suffix.lower()
        mt = {".png": "image/png", ".gif": "image/gif"}.get(ext, "image/jpeg")
        manifest.append(f'<item id="img{n}" href="{name}" media-type="{mt}"/>')

    # table of contents of the reader: the printed one, or else the headings
    # (without the small bold ones, when there are bigger ones)
    if main is not None:
        top = min(e.level for e in main.entries)
        items = [(top, main.title, href(main.anchor))] if href(main.anchor) else []
        items += [(e.level, e.title, href(e.anchor)) for e in main.entries if href(e.anchor)]
    else:
        items = toc
        if any(level < 3 for level, _, _ in items):
            items = [t for t in items if t[0] < 3]
    nested, stack = [], []
    for level, text, link in items:       # one step deeper at most
        while stack and stack[-1] >= level:
            stack.pop()
        stack.append(level)
        nested.append((len(stack), text, link))

    nav, depth = [], 0
    for d, text, link in nested:
        nav.append("<ol>" if d > depth else "</li>" + "</ol></li>" * (depth - d))
        nav.append(f'<li><a href="{link}">{esc(text)}</a>')
        depth = d
    if depth:
        nav.append("</li>" + "</ol></li>" * (depth - 1) + "</ol>")
    files["nav.xhtml"] = xhtml(title, f'<nav epub:type="toc" id="toc"><h1>{esc(title)}</h1>{"".join(nav)}</nav>',
                               lang).encode()
    manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

    points, depth = [], 0
    for order, (d, text, link) in enumerate(nested, 1):
        if d <= depth:
            points.append("</navPoint>" * (depth - d + 1))
        points.append(f'<navPoint id="p{order}" playOrder="{order}"><navLabel><text>{esc(text)}</text>'
                      f'</navLabel><content src="{link}"/>')
        depth = d
    points.append("</navPoint>" * depth)
    files["toc.ncx"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n<docTitle><text>{esc(title)}</text></docTitle>\n'
        f'<navMap>{"".join(points)}</navMap>\n</ncx>\n').encode()
    manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

    creator = f"<dc:creator>{esc(author)}</dc:creator>\n" if author else ""
    cover_meta = ('<meta name="cover" content="cover-image"/>\n' if cover else "") + \
        f'<meta name="generator" content="{esc(generator)}"/>\n'
    guide = '<guide><reference type="cover" title="Cover" href="cover.xhtml"/></guide>\n' if cover else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n<dc:title>{esc(title)}</dc:title>\n{creator}'
        f'<dc:language>{lang}</dc:language>\n<meta property="dcterms:modified">{modified}</meta>\n{cover_meta}'
        '</metadata>\n'
        f'<manifest>\n{chr(10).join(manifest)}\n</manifest>\n'
        f'<spine toc="ncx">\n{chr(10).join(spine)}\n</spine>\n{guide}</package>\n')

    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n<rootfiles><rootfile '
                    'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
                    '</container>\n', compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            zf.writestr("OEBPS/" + name, data, compress_type=zipfile.ZIP_DEFLATED)
        if cover:
            zf.write(cover, "OEBPS/images/cover.jpg", compress_type=zipfile.ZIP_STORED)
        for src, name in images.items():
            zf.write(src, "OEBPS/" + name, compress_type=zipfile.ZIP_STORED)
    check_epub(tmp)
    os.replace(tmp, out)


def check_epub(path: Path) -> None:
    """Every page must be well-formed XML and every listed file present."""
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("the new file is damaged")
        names = set(zf.namelist())
        opf = ET.fromstring(zf.read("OEBPS/content.opf"))
        for item in opf.iter("{http://www.idpf.org/2007/opf}item"):
            name = "OEBPS/" + item.get("href")
            if name not in names:
                raise RuntimeError(f"missing {name}")
            if name.endswith((".xhtml", ".ncx")):
                ET.fromstring(zf.read(name))


# ---------------------------------------------------------------- one book

def guess_meta(pdf: Path, info: dict[str, str], args) -> tuple[str, str]:
    title, author = info.get("Title", ""), info.get("Author", "")
    junk = re.compile(r"^(untitled|unknown|microsoft|document|\S+\.(docx?|pdf|indd|tex|rtf))|^\W*$", re.I)
    if junk.search(title):
        title = ""
    if junk.search(author) or author.lower() in ("admin", "user", "owner", "пользователь"):
        author = ""
    stem = pdf.stem.replace("_", " ").strip()
    if title.replace("_", " ").strip() == stem:
        title = ""                      # just the file name again
    if not title:
        if " - " in stem and not author:
            author, title = (s.strip() for s in stem.split(" - ", 1))
        else:
            title = stem
    return args.title or title, args.author if args.author is not None else author


class Progress:
    """Lines that stay, and on a terminal one live line under them (rewritten in place)."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.live = self.stream.isatty()
        self.shown = False
        self.lock = threading.Lock()

    def __call__(self, line: str) -> None:
        with self.lock:
            self._clear()
            print("   " + line, file=self.stream, flush=True)

    def status(self, text: str) -> None:
        if not self.live:
            return
        with self.lock:
            self._clear()
            if text:
                self.stream.write("   " + text)
                self.stream.flush()
                self.shown = True

    def _clear(self) -> None:
        if self.shown:
            self.stream.write("\r\033[K")
            self.shown = False


def estimate(pages: int) -> str:
    """Time of the whole recognition: about a minute a page on a CPU."""
    minutes = pages
    return f"~{minutes} min" if minutes < 60 else f"~{minutes / 60:.1f} h"


def ask(prompt: str, default: str) -> str:
    try:
        answer = input(f"   {prompt} [{default or '—'}]: ").strip()
    except EOFError:
        return default
    return answer or default


def book_meta(pdf: Path, args) -> tuple[str, str, int]:
    """Title, author (asked when unknown) and the number of pages."""
    info = read_info(pdf)
    title, author = guess_meta(pdf, info, args)
    if not author and not args.yes and sys.stdin.isatty():
        print(f"\n📄 {pdf.name}: the author is not in the file; press Enter to keep a suggestion")
        title = ask("Title ", title)
        author = ask("Author", author)
    try:
        pages = int(info.get("Pages", "0"))
    except ValueError:
        pages = 0
    return title, author, pages


def convert(pdf: Path, out: Path, args, meta: tuple[str, str] | None = None) -> str:
    title, author = meta or book_meta(pdf, args)[:2]

    with tempfile.TemporaryDirectory(prefix="pdf2epub-") as tmp_name:
        tmp = Path(tmp_name)
        pages = read_text(pdf, tmp)
        if not pages:
            raise RuntimeError("no pages found")
        sizes = Counter()
        for line in (l for p in pages for l in p.lines):
            sizes[line.size] += len(line.text)
        body = weighted_median(sizes) if sizes else 10.0

        drop_headers_footers(pages, body)
        if args.pages:
            del pages[args.pages:]       # headers and footers were found on the whole book
        tocs = find_tocs(pages, body)
        by_number = {p.number: p for p in pages}
        for n, toc in enumerate(tocs):
            for number in toc.pages:
                by_number[number].toc = n
        find_headings(pages, body)

        # pictures: page scans are left out, other pictures are taken; empty pages become pictures
        images = read_images(pdf)
        for page in pages:
            pics = images.get(page.number, [])
            small = [p for p in pics if not (p[0] >= FULL_PAGE * page.width and p[1] >= FULL_PAGE * page.height)]
            has_text = sum(len(l.text) for l in page.lines) > 0
            if not has_text and pics:
                page.as_picture = "render"
            elif small:
                page.figures = page_figures(pdf, page, tmp)

        # the cover: the first page as a picture; it is left out of the text when it is a picture itself
        cover = render_page(pdf, 1, tmp / "cover", COVER_HEIGHT)
        first_pics = images.get(1, [])
        skip_first = cover is not None and any(w * h >= 0.5 * pages[0].width * pages[0].height for w, h in first_pics)

        img_names: dict[str, str] = {}
        for page in pages:
            if page.as_picture == "render" and page.number != (1 if skip_first else None):
                path = render_page(pdf, page.number, tmp / f"page{page.number}", PAGE_IMAGE_HEIGHT)
                page.as_picture = str(path) if path else None
        blocks = build_blocks(pages, body, 1 if skip_first else None, tocs)
        main = max(tocs, key=lambda t: len(t.entries)) if tocs else None
        if tocs:
            anchor_tocs(blocks, tocs, page_offset(pages, tocs, blocks))
            if sum(1 for e in main.entries if e.anchor) < 0.5 * len(main.entries):
                main = None         # the contents doesn't match the text: use the headings
        for b in blocks:
            if b.kind == "img" and b.src not in img_names:
                ext = Path(b.src).suffix.lower() or ".jpg"
                img_names[b.src] = f"images/img{len(img_names) + 1:04d}{ext}"

        text_sample = " ".join(b.text or b.html for b in blocks[:400])
        lang = args.lang or detect_lang(text_sample)
        chapters = split_chapters(blocks, title, main)
        if not chapters:
            raise RuntimeError("no text or pictures found")
        write_epub(out, title, author, lang, chapters, cover, img_names, tocs, main)

    words = sum(len(b.html.split()) for b in blocks if b.kind == "p")
    pics = sum(1 for b in blocks if b.kind == "img")
    return f"{len(chapters)} chapter(s), {words} words, {pics} picture(s) — «{title}»" + (f", {author}" if author else "")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Turn PDF books into EPUB (Pdf/*.pdf -> In/*.epub).")
    ap.add_argument("paths", nargs="*", type=Path, help="PDF files or folders (default: ./Pdf)")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output folder (default: ./In)")
    ap.add_argument("--title", help="book title (for a single PDF)")
    ap.add_argument("--author", help="book author (for a single PDF)")
    ap.add_argument("--lang", help="language code, e.g. ru, uk, en (default: guessed from the text)")
    ap.add_argument("--force", action="store_true", help="overwrite books that already exist in the output folder")
    ap.add_argument("--yes", action="store_true", help="never ask questions")
    ap.add_argument("--quick", action="store_true",
                    help="quick mode: only the PDF text layer, no recognition (seconds instead of hours)")
    ap.add_argument("--pages", type=int, metavar="N",
                    help="a trial: only the first N pages, into <out>/Preview/ (made again every time); "
                         "the pages it recognises are kept, so the whole book goes on from them")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the recognition work folder (In/.pdf2epub-work) after the book is done")
    args = ap.parse_args()
    if args.pages is not None and args.pages < 1:
        ap.error("--pages must be 1 or more")

    missing = [t for t in TOOLS if not shutil.which(t)]
    if missing:
        print(f"❌ Poppler is needed ({', '.join(missing)} not found). Install it:\n"
              "   Linux:   sudo apt install poppler-utils\n   macOS:   brew install poppler\n"
              "   Windows: https://github.com/oschwartz10612/poppler-windows/releases (add Library/bin to PATH)")
        return 1

    sources = args.paths or [PDF_DIR]
    pdfs = []
    for src in sources:
        if src.is_dir():
            pdfs += sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
        elif src.is_file() and src.suffix.lower() == ".pdf":
            pdfs.append(src)
        else:
            print(f"❌ Not a PDF file or folder: {src}")
            return 1
    if not pdfs:
        print(f"📭 No PDF files found in {', '.join(str(s) for s in sources)}")
        return 0
    if (args.title or args.author) and len(pdfs) > 1:
        print("❌ --title and --author can only be used with one PDF")
        return 1

    out_dir = args.out / "Preview" if args.pages else args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📚 Found {len(pdfs)} PDF file(s)" + (f", a trial of the first {args.pages} page(s)" if args.pages else "")
          + "\n")
    done = skipped = failed = 0
    # all questions first, so that the long recognition can then run unattended (overnight)
    jobs = []
    for pdf in pdfs:
        out = out_dir / (safe_name(pdf.stem) + ".epub")
        if out.exists() and not args.force and not args.pages:
            print(f"⏭️  {pdf.name}: {out.name} already exists (--force to overwrite)")
            skipped += 1
            continue
        try:
            jobs.append((pdf, out, *book_meta(pdf, args)))
        except Exception as e:
            print(f"❌ {pdf.name}: {e}")
            failed += 1

    engine = None
    if jobs and not args.quick:
        from pdf2epub_parts.engine import ensure_engine
        engine = ensure_engine(print)
        if engine is None:
            print("⚠️  The recognition engine is not available: using quick mode (the PDF text layer only)")
        else:
            pages = sum(min(job[4], args.pages or job[4]) for job in jobs)
            print(f"⏱️  {estimate(pages)} for {pages} pages "
                  "(it can be stopped with Ctrl+C and goes on from the same page next time)\n")

    for pdf, out, title, author, _ in jobs:
        print(f"⏳ {pdf.name} ...", flush=True)
        started = time.time()
        try:
            if engine is not None:
                from pdf2epub_parts.hybrid import convert_hybrid
                progress = Progress()
                try:
                    summary = convert_hybrid(pdf, out, title, author, args.lang, engine, args.keep_work,
                                             progress, status=progress.status,
                                             first_pages=args.pages, work_root=args.out)
                finally:
                    progress.status("")
            else:
                summary = convert(pdf, out, args, (title, author))
        except KeyboardInterrupt:
            print(f"\n⏸️  Stopped. Everything recognised so far is kept: run it again to go on "
                  f"from the same page.")
            return 130
        except Exception as e:        # one bad PDF must not stop the rest
            print(f"❌ {pdf.name}: {e}")
            failed += 1
            continue
        print(f"✅ {out.name}: {summary} ({time.time() - started:.0f} s)")
        done += 1

    print(f"\nDone: {done} converted, {skipped} skipped, {failed} failed. Books are in {out_dir}/")
    if done and args.pages:
        print("Have a look; the whole book: the same command without --pages"
              + (f" (the {args.pages} page(s) recognised now are not read again)" if engine else ""))
    elif done:
        print("Next: python3 epub_rename.py  (sorts them into Out/ and updates the catalog)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
