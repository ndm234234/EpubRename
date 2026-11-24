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
from pdf2epub_merge import Node, Text, parse

KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "sup": "sup", "sub": "sub"}
CHECKBOX = "☐ "
LOOKALIKE = {"A": "А", "B": "В", "C": "С", "D": "В", "E": "Е", "H": "Н", "K": "К", "M": "М",
             "O": "О", "P": "Р", "T": "Т", "X": "Х"}


def inline(node) -> str:
    """HTML of a node's content with only bold, italic, sup/sub and line breaks kept."""
    if isinstance(node, Text):
        return html.escape(node.s, quote=False)
    if node.tag == "br":
        return "<br/>"
    if node.tag == "input" and "checkbox" in node.start:
        return CHECKBOX
    inner = "".join(inline(c) for c in node.children)
    tag = KEEP.get(node.tag)
    return f"<{tag}>{inner}</{tag}>" if tag and inner.strip() else inner


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
    found = [m.group(1) for m in [re.search(r'src="([^"]+)"', node.start)] if node.tag == "img" and m]
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
    """'В се родители' / 'Dсе родители' after a chapter heading -> 'Все родители'.

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
                and not last.text.rstrip().endswith(TERMINAL) and b.text[:1].islower()):
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


# ---------------------------------------------------------------- headings in parts

LABEL_NUMBER = re.compile(r"\d{1,3}|[IVXLC]{1,6}")


def merge_heading_runs(blocks: list[Block]) -> list[Block]:
    """'ПРИНЦИП' + '1' + 'ИСПОЛЬЗУЙТЕ КЛЮЧИ' (headings in a row on one page) -> one heading
    'ПРИНЦИП 1. ИСПОЛЬЗУЙТЕ КЛЮЧИ'. Only a word followed by a number, and such a label
    followed by its title, are joined; other headings in a row stay apart."""
    out: list[Block] = []
    for b in blocks:
        prev = out[-1] if out else None
        if b.kind == "h" and prev is not None and prev.kind == "h" and prev.page == b.page:
            words = prev.text.split()
            if len(words) == 1 and LABEL_NUMBER.fullmatch(b.text):          # "ПРИНЦИП" + "1"
                prev.text, prev.html = f"{prev.text} {b.text}", f"{prev.html} {b.html}"
                prev.level = min(prev.level, b.level)
                continue
            if len(words) == 2 and LABEL_NUMBER.fullmatch(words[1]) and b.level <= 2 \
                    and not LABEL_NUMBER.fullmatch(b.text):                  # "ПРИНЦИП 1" + title
                prev.text, prev.html = f"{prev.text}. {b.text}", f"{prev.html}<br/>{b.html}"
                prev.level = min(prev.level, b.level)
                continue
        out.append(b)
    return out
