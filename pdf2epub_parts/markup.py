"""Turning Marker's HTML pages into pdf2epub's book blocks.

Marker gives one HTML fragment per page. Here it becomes the same blocks the rest of
pdf2epub writes into the EPUB (headings, paragraphs, pictures) plus lists and tables;
printed contents tables become the clickable contents; a first letter set apart
(a drop cap) and paragraphs cut by a page break are put back together.
"""

import html
import re
from collections import Counter

from pdf2epub import TERMINAL, Block, Toc, TocEntry
from pdf2epub_parts.merge import Node, Text, parse, plain, serialize

KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "sup": "sup", "sub": "sub"}
CHECKBOX = "☐ "
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")      # not allowed in XHTML
SPACED = {"p", "div", "li", "blockquote", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}  # block inside inline text
LOOKALIKE = {"A": "А", "B": "В", "C": "С", "D": "В", "E": "Е", "H": "Н", "K": "К", "M": "М",
             "O": "О", "P": "Р", "T": "Т", "X": "Х"}


def inline(node) -> str:
    """HTML of a node's content with only bold, italic, sup/sub and line breaks kept."""
    if isinstance(node, Text):
        return html.escape(CONTROL.sub("", node.s), quote=False)
    if node.tag == "br":
        return "<br/>"
    if node.tag == "input" and "checkbox" in node.start:
        return CHECKBOX
    if node.tag == "math":              # a formula in LaTeX: made MathML when the book is written
        block = ' display="block"' if re.search(r"""display=["']?block""", node.start) else ""
        return f"<math{block}>" + html.escape(CONTROL.sub("", _plain(node)), quote=False) + "</math>"
    inner = "".join(inline(c) for c in node.children)
    if node.tag in SPACED:
        inner = f" {inner} "
    tag = KEEP.get(node.tag)
    return f"<{tag}>{inner}</{tag}>" if tag and inner.strip() else inner


def _plain(node) -> str:
    return node.s if isinstance(node, Text) else "".join(_plain(c) for c in node.children)


def tidy(fragment: str) -> str:
    fragment = re.sub(r"\s+", " ", fragment).strip()
    fragment = re.sub(r"(<br/>\s*)+$", "", fragment)
    return re.sub(r"</(b|i)>(\s*)<\1>", r"\2", fragment)      # <b>a</b> <b>b</b> -> <b>a b</b>


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip() \
        .replace(" ,", ",").replace(" .", ".")


def _text(fragment: str) -> str:
    """Plain text of an inline fragment: tags dropped without adding spaces."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<br/>", " ", re.sub(r"<(?!br/)[^>]+>", "", fragment)))).strip()


def _images(node) -> list[str]:
    if isinstance(node, Text):
        return []
    found = [html.unescape(m.group(2)) for m in [re.search(r"""src=(['"])(.*?)\1""", node.start)]
             if node.tag == "img" and m]
    for c in node.children:
        found += _images(c)
    return found


def page_blocks(page_html: str, page: int) -> list[Block]:
    blocks: list[Block] = []

    def add_text(kind, fragment, level=0):
        fragment = tidy(fragment)
        if _text(fragment):
            blocks.append(Block(kind, fragment, _text(fragment), level, page=page))

    def walk(node):
        for child in node.children:
            if isinstance(child, Text):
                add_text("p", inline(child))
                continue
            tag = child.tag
            if re.fullmatch(r"h[1-6]", tag):
                add_text("h", inline(child), min(int(tag[1]), 3))
            elif tag in ("p", "blockquote", "figcaption", "caption"):
                for src in _images(child):
                    blocks.append(Block("img", src=src, page=page))
                add_text("p", inline(child))
            elif tag in ("ul", "ol"):
                items = [tidy(inline(li)) for li in child.children if isinstance(li, Node) and li.tag == "li"]
                items = [i for i in items if _text(i)]
                if items:
                    blocks.append(Block("list", items=items, text=" ".join(_text(i) for i in items), page=page))
            elif tag == "table":
                rows = []
                for tr in _find(child, "tr"):
                    cells = [tidy(inline(td)) for td in tr.children if isinstance(td, Node) and td.tag in ("td", "th")]
                    if any(_text(c) for c in cells):
                        rows.append(cells)
                if rows:
                    blocks.append(Block("table", rows=rows, text=" ".join(_text(c) for r in rows for c in r), page=page))
            elif tag == "img":
                for src in _images(child):
                    blocks.append(Block("img", src=src, page=page))
            elif tag in ("br", "hr", "input"):
                continue
            else:
                walk(child)                       # div, span, section, figure, a ...: look inside

    walk(parse(page_html))
    # inline-only content that ended up as separate pieces (e.g. a span at top level) stays as paragraphs
    return blocks


def _find(node, tag) -> list:
    out = []
    for c in getattr(node, "children", []):
        if isinstance(c, Node):
            if c.tag == tag:
                out.append(c)
            else:
                out += _find(c, tag)
    return out


# ---------------------------------------------------------------- repairs

ONE_LETTER_WORDS = set("аиовкуся") | set("aio")


def fix_drop_cap(blocks: list[Block]) -> None:
    """'В се садовники' / 'Dсе садовники' after a chapter heading -> 'Все садовники'.

    'В' or 'А' can be words themselves, so a space is only closed up when the joined word
    is used elsewhere in the book and the cut-off rest is not a word of the book.
    """
    vocab = Counter(w.lower() for b in blocks if b.kind in ("p", "h", "list") for w in re.findall(r"\w+", b.text))
    for prev, b in zip(blocks, blocks[1:]):
        if b.kind != "p" or prev.kind != "h" or prev.level != 1:
            continue
        m = re.match(r"([A-ZА-ЯЁ]) ([а-яё]+)", b.text)
        join = bool(m) and (m.group(1).lower() not in ONE_LETTER_WORDS
                            or (vocab[(m.group(1) + m.group(2)).lower()] > 0 and vocab[m.group(2)] <= 1))
        for attr in ("html", "text"):
            value = getattr(b, attr)
            if join and re.match(r"[A-ZА-ЯЁ] [а-яё]", value):
                value = value[0] + value[2:]
            m = re.match(r"([A-Z])([а-яё])", value)
            if m and m.group(1) in LOOKALIKE:
                value = LOOKALIKE[m.group(1)] + value[1:]
            setattr(b, attr, value)


OPENING = "“„«\"'‘(["               # a quote or bracket before the first word


def join_pages(blocks: list[Block]) -> list[Block]:
    """A paragraph cut by a page break goes on in the first paragraph of the next page."""
    out: list[Block] = []
    pending: list[Block] = []          # page markers between the two halves
    for b in blocks:
        if b.kind == "page":
            pending.append(b)
            continue
        last = next((x for x in reversed(out) if x.kind != "page"), None)
        if (b.kind == "p" and pending and last is not None and last.kind == "p"
                and not last.text.rstrip().endswith(TERMINAL) and b.text.lstrip(OPENING)[:1].islower()):
            if re.search(r"\w[-­]$", last.text):
                last.html = re.sub(r"[-­](</[bi]>)?$", r"\1", last.html) + b.html
                last.text = last.text[:-1] + b.text
            else:
                last.html += " " + b.html
                last.text += " " + b.text
            out.extend(pending)
            pending = []
            continue
        out.extend(pending)
        pending = []
        out.append(b)
    return out + pending


# ---------------------------------------------------------------- printed contents

def _toc_rows(b: Block) -> list[tuple[str, int, bool]] | None:
    """(title, page number, bold) of a table that looks like contents, else None."""
    rows = []
    for row in b.rows:
        cells = [c for c in row if _text(c)]
        if len(cells) < 2 or not re.fullmatch(r"\d{1,4}", _text(cells[-1])):
            return None
        title = " ".join(_text(c) for c in cells[:-1])
        bold = all(re.fullmatch(r"<b>.*</b>", c) for c in cells[:-1])
        rows.append((title, int(_text(cells[-1])), bold))
    return rows


def tocs_from_blocks(blocks: list[Block]) -> list[Toc]:
    """Tables that are printed contents (title | page number, numbers rising) become a Toc;
    the first table becomes Block("toc"), tables continuing it on the next pages go away."""
    tocs: list[Toc] = []
    current = None                 # (toc, index of its block) while only page markers follow
    i = 0
    while i < len(blocks):
        b = blocks[i]
        rows = _toc_rows(b) if b.kind == "table" else None
        if rows is not None:
            numbers = [r[1] for r in rows]
            rising = sum(1 for x, y in zip(numbers, numbers[1:]) if y >= x)
            continues = current is not None and numbers[0] >= current.entries[-1].number
            if continues:
                current.entries += [TocEntry(t, n, 0.0, b.page, 1 if bold else 2) for t, n, bold in rows]
                if b.page not in current.pages:
                    current.pages.append(b.page)
                del blocks[i]
                continue
            if len(rows) >= 4 and rising >= 0.7 * (len(rows) - 1):
                prev = blocks[i - 1] if i and blocks[i - 1].kind == "h" else None
                current = Toc(prev.text if prev else "Contents",
                              [TocEntry(t, n, 0.0, b.page, 1 if bold else 2) for t, n, bold in rows], [b.page])
                tocs.append(current)
                blocks[i] = Block("toc", level=len(tocs) - 1, page=b.page)
                i += 1
                continue
        if b.kind not in ("page",) and not (b.kind == "h" and current is not None and _is_running_title(b, current)):
            current = None
        i += 1
    return tocs


def _is_running_title(b: Block, toc: Toc) -> bool:
    """The contents' own title repeated at the top of its next page."""
    return re.sub(r"\W|\d", "", b.text.lower()) == re.sub(r"\W|\d", "", toc.title.lower())


# ---------------------------------------------------------------- footnotes

NOTE_START = re.compile(r"^\s*<sup>(\d{1,3})</sup>\s*")


def _has_ref(b: Block, number: str) -> bool:
    mark = f"<sup>{number}</sup>"
    return mark in b.html or any(mark in i for i in b.items)


def take_footnotes(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    """Footnotes ('<sup>9</sup> Sometimes called…' at the foot of a page whose text has
    '<sup>9</sup>') leave the flow, so the paragraph a page break cut joins up again."""
    notes, keep = [], []
    for b in blocks:
        m = NOTE_START.match(b.html) if b.kind == "p" else None
        if m and any(x is not b and x.page in (b.page, b.page - 1) and _has_ref(x, m.group(1))
                     and not NOTE_START.match(x.html) for x in blocks):
            notes.append(b)
        else:
            keep.append(b)
    return keep, notes


def place_footnotes(blocks: list[Block], notes: list[Block]) -> None:
    """Each footnote goes right after the block that refers to it, as an EPUB footnote:
    the number in the text links to it (readers show it in a pop-up) and it links back."""
    for k, note in enumerate(notes, 1):
        number = NOTE_START.match(note.html).group(1)
        mark = f"<sup>{number}</sup>"
        refs = [i for i, b in enumerate(blocks) if b.kind != "note" and b.page in (note.page, note.page - 1)
                and _has_ref(b, number)]
        link = f'<a epub:type="noteref" href="#fn{k}" id="fnref{k}">{mark}</a>'
        if refs:
            i = refs[-1]
            ref = blocks[i]
            if mark in ref.html:
                ref.html = ref.html.replace(mark, link, 1)
            else:
                j = next(j for j, x in enumerate(ref.items) if mark in x)
                ref.items[j] = ref.items[j].replace(mark, link, 1)
        else:
            i = max((j for j, b in enumerate(blocks) if b.page <= note.page), default=len(blocks) - 1)
        body = NOTE_START.sub("", note.html, count=1)
        back = f'<a href="#fnref{k}">{mark}</a> ' if refs else f"{mark} "
        blocks.insert(i + 1, Block("note", html=back + body, text=note.text, page=note.page, anchor=f"fn{k}"))


# ---------------------------------------------------------------- endnotes

NOTES_TITLE = re.compile(r"(примечани[яе]|комментари[ийя]|сноски|примітки|коментарі|notes?|endnotes)\.?", re.I)
NOTE_REF = re.compile(r"<sup>(\[?)(\d{1,3})\]?</sup>")
NUMBER_ONLY = re.compile(r"\[?(\d{1,3})[\].]?")
NUMBER_LEAD = re.compile(r"^\s*(?:<sup>)?\[?(\d{1,3})[\].]?(?:</sup>)?\s+(?=\S)")


def _notes_sections(blocks: list[Block]) -> list[tuple[int, int, int]]:
    """(label heading, first entry, end) of every 'Notes' section: its entries run until a
    heading that is not just a number, or anything that is not a paragraph."""
    sections, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "h" and NOTES_TITLE.fullmatch(b.text.strip()):
            j = i + 1
            while j < len(blocks) and (blocks[j].kind == "page" or (
                    blocks[j].kind == "h" and NOTES_TITLE.fullmatch(blocks[j].text.strip()))):
                j += 1
            k = j
            while k < len(blocks) and (blocks[k].kind in ("p", "page") or (
                    blocks[k].kind == "h" and NUMBER_ONLY.fullmatch(blocks[k].text.strip()))):
                k += 1
            sections.append((i, j, k))
            i = k
        else:
            i += 1
    return sections


def _runs(items: list, number) -> list[list]:
    """Split where the numbering starts again (notes numbered chapter by chapter)."""
    runs: list[list] = []
    last = None
    for x in items:
        n = number(x)
        if n is not None and last is not None and n < last:
            runs.append([])
        if not runs:
            runs.append([])
        runs[-1].append(x)
        last = n if n is not None else last
    return runs


def _number_entries(entries: list[list]) -> None:
    """entries: [number or None, block, ...]. Numbers the recognition lost come back from the
    order: the paragraphs between notes 4 and 13 are notes 5 to 12."""
    i, prev = 0, 0
    while i < len(entries):
        if entries[i][0] is not None:
            prev = entries[i][0]
            i += 1
            continue
        j = i
        while j < len(entries) and entries[j][0] is None:
            j += 1
        nxt = entries[j][0] if j < len(entries) else None
        gap = (nxt - prev - 1) if nxt is not None else j - i
        for k in range(i, j):
            if k - i < gap:
                entries[k][0] = prev + 1 + (k - i)
            else:                                   # more paragraphs than numbers: one note goes on
                entries[k][0] = -1
        prev = prev + min(gap, j - i)
        i = j


def link_endnotes(blocks: list[Block]) -> int:
    """Notes collected under a 'Notes' heading (at the end of the book or of each chapter) are
    linked with their references in the text (<sup>[5]</sup> or <sup>5</sup>), both ways.
    Returns the number of notes."""
    sections = _notes_sections(blocks)
    if not sections:
        return 0
    count, drop, start = 0, set(), 0
    for label, first, end in sections:
        # references between the previous notes and these: [n] always, a bare n in sequence
        refs, last = [], 0
        for bi in range(start, label):
            b = blocks[bi]
            if b.kind == "endnote":
                continue
            for m in NOTE_REF.finditer(b.html):
                n = int(m.group(2))
                if m.group(1) or n in (last, last + 1, 1):
                    refs.append((bi, m.start(), m.end(), n))
                    last = n
        start = end
        # the entries: "5 Text", or a lone "5" before its text, or text alone
        entries, pending, lone_numbers = [], None, []
        for bi in range(first, end):
            b = blocks[bi]
            if b.kind == "page":
                continue
            lone = NUMBER_ONLY.fullmatch(b.text.strip())
            if lone:
                pending = int(lone.group(1))
                lone_numbers.append(bi)
                continue
            lead = NUMBER_LEAD.match(b.html)
            if pending is not None:
                entries.append([pending, bi, b.html])
            elif lead:
                entries.append([int(lead.group(1)), bi, b.html[lead.end():]])
            else:
                entries.append([None, bi, b.html])
            pending = None
        if not refs or not entries:
            continue
        drop.update(lone_numbers)
        for group, run in zip(_runs(entries, lambda e: e[0]), _runs(refs, lambda r: r[3])):
            _number_entries(group)
            ids: dict[int, int] = {}
            for e in group:
                n, bi, body = e
                if n == -1:                           # a further paragraph of the note before
                    blocks[bi] = Block("endnote", html=body, text=_text(body), page=blocks[bi].page)
                    continue
                count += 1
                ids[n] = count
                linked = any(r[3] == n for r in run)
                mark = f'<a href="#enref{count}">{n}.</a> ' if linked else f"{n}. "
                blocks[bi] = Block("endnote", html=mark + body, text=_text(body), page=blocks[bi].page,
                                   anchor=f"en{count}")
            first_ref = {}
            for r in run:                             # the first reference gets the way back
                first_ref.setdefault(r[3], r)
            for r in sorted(run, key=lambda r: (r[0], -r[1])):
                bi, s, t, n = r
                if n not in ids:
                    continue
                c = ids[n]
                id_attr = f' id="enref{c}"' if first_ref[n] is r else ""
                b = blocks[bi]
                b.html = b.html[:s] + f'<a epub:type="noteref" href="#en{c}"{id_attr}>{b.html[s:t]}</a>' + b.html[t:]
        labels = [i for i in range(label, first) if blocks[i].kind == "h"]
        drop.update(labels[:-1])                      # 'notes' + 'Примечания': one title is enough
    if drop:
        blocks[:] = [b for i, b in enumerate(blocks) if i not in drop or b.kind == "page"]
    return count


# ---------------------------------------------------------------- running heads

HEAD_NUMBER = re.compile(r"^\s*(\d{1,4})\b|\b(\d{1,4})\s*$")
HEAD_WORDS = 10
HEAD_VOTES = 3             # pages whose running head must agree before any is removed


def _edges(page_html: str) -> tuple[Node, list]:
    root = parse(page_html)
    kids = [c for c in root.children if isinstance(c, Node)]
    return root, (kids[:1] + kids[-1:] if len(kids) > 1 else kids)


def _head_number(node: Node) -> int | None:
    """The page number of a short line with a number at its start or end ('22 INTRODUCTION').
    A chapter heading (h1, the biggest type) or a footnote (starts with <sup>) is never one."""
    if node.tag == "h1" or NOTE_START.match(serialize(node)[len(node.start):]):
        return None
    t = " ".join(plain(node).split())
    if not t or len(t.split()) > HEAD_WORDS or len(t) > 120:
        return None
    m = HEAD_NUMBER.search(t)
    return int(m.group(1) or m.group(2)) if m else None


def running_head_offset(pages: dict[int, str]) -> int | None:
    """printed page number - PDF page number, from the running heads and page numbers at the
    top or bottom of the pages; None unless at least HEAD_VOTES pages agree on it."""
    votes = Counter()
    for n, page_html in pages.items():
        for node in _edges(page_html)[1]:
            number = _head_number(node)
            if number is not None:
                votes[number - n] += 1
    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count >= HEAD_VOTES else None


def strip_running_heads(page_html: str, page: int, offset: int | None) -> str:
    """Leave out the running head ('22 INTRODUCTION', 'INTRODUCTION 23') and the bare page
    number at the top or bottom of a page: the first or last line whose number is this page's."""
    if offset is None or not page_html.strip():
        return page_html
    root, edges = _edges(page_html)
    drop = [e for e in edges if _head_number(e) == page + offset]
    if not drop:
        return page_html
    root.children = [c for c in root.children if not any(c is d for d in drop)]
    return serialize(root).strip()


# ---------------------------------------------------------------- headings in parts

LABEL_NUMBER = re.compile(r"\d{1,3}|[IVXLC]{1,6}")


def merge_heading_runs(blocks: list[Block]) -> list[Block]:
    """'ШАГ' + '1' + 'ПОЛИВАЙТЕ САД' (headings in a row on one page) -> one heading
    'ШАГ 1. ПОЛИВАЙТЕ САД'. Only a word followed by a number, and such a label
    followed by its title, are joined; other headings in a row stay apart."""
    out: list[Block] = []
    for b in blocks:
        prev = out[-1] if out else None
        if b.kind == "h" and prev is not None and prev.kind == "h" and prev.page == b.page:
            words = prev.text.split()
            if len(words) == 1 and LABEL_NUMBER.fullmatch(b.text):          # "ШАГ" + "1"
                prev.text, prev.html = f"{prev.text} {b.text}", f"{prev.html} {b.html}"
                prev.level = min(prev.level, b.level)
                continue
            if len(words) == 2 and LABEL_NUMBER.fullmatch(words[1]) and b.level <= 2 \
                    and not LABEL_NUMBER.fullmatch(b.text):                  # "ШАГ 1" + title
                prev.text, prev.html = f"{prev.text}. {b.text}", f"{prev.html}<br/>{b.html}"
                prev.level = min(prev.level, b.level)
                continue
        out.append(b)
    return out
