"""Checking one page of accurate OCR against two witnesses.

The accurate mode (a model that reads the whole page) gives the best structure but
can drop text or write a different word. The fast mode and the PDF's own text layer
are witnesses: a word two of the three agree on wins, and text that a witness has
and the accurate page lacks is put back in its place. Only text is changed; the
accurate page's markup (headings, bold, lists, tables) stays.
"""

import difflib
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

WORD = re.compile(r"\w+")
VOID = {"br", "img", "input", "hr", "meta", "link", "col", "wbr", "source", "area"}
BULLET = re.compile(r"^\s*([•◦▪■□☐©○●‣∙·*–—-]|О\s|O\s|o\s)\s*")
RETRY_COVERAGE = 0.97      # below this the page is recognised again
RETRY_MISSING_RUN = 4      # ... or when this many words in a row were missing
WHOLE_PAGE_FAST = 0.5      # below this the fast page is used instead
LAYER_MISSING_RUN = 8      # text only the PDF layer has is put back from this length


# ---------------------------------------------------------------- a tiny HTML tree

@dataclass
class Text:
    s: str


@dataclass
class Node:
    tag: str
    start: str = ""                     # the start tag as written
    children: list = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, self.get_starttag_text())
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, self.get_starttag_text()))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(Text(data))


def parse(html_text: str) -> Node:
    b = _TreeBuilder()
    b.feed(html_text)
    b.close()
    return b.root


def serialize(node) -> str:
    if isinstance(node, Text):
        return html.escape(node.s, quote=False)
    inner = "".join(serialize(c) for c in node.children)
    if node.tag == "#root":
        return inner
    return node.start + ("" if node.tag in VOID else inner + f"</{node.tag}>")


def plain(node) -> str:
    if isinstance(node, Text):
        return node.s
    return " ".join(plain(c) for c in node.children)


def norm(word: str) -> str:
    return word.lower().replace("ё", "е")


# ---------------------------------------------------------------- units and tokens

@dataclass
class Unit:
    """A piece of a page that can be missing as a whole: a block, a list item or a line."""
    nodes: list            # nodes it is made of (for a line inside a <p>: the nodes between <br>s)
    parent: Node           # where it sits
    is_item: bool


@dataclass
class Tok:
    word: str
    text: Text | None      # where the word is (None for the PDF layer)
    span: tuple
    unit: int


def units_of(root: Node) -> list[Unit]:
    units = []
    for child in root.children:
        if isinstance(child, Text):
            if child.s.strip():
                units.append(Unit([child], root, False))
        elif child.tag in ("ul", "ol"):
            for li in child.children:
                if isinstance(li, Node) and li.tag == "li":
                    units.append(Unit([li], child, True))
        elif child.tag == "p" and any(isinstance(c, Node) and c.tag == "br" for c in child.children):
            line: list = []
            for c in child.children + [Node("br")]:
                if isinstance(c, Node) and c.tag == "br":
                    if line and WORD.search(" ".join(plain(n) for n in line)):
                        units.append(Unit(line, child, bool(BULLET.match(" ".join(plain(n) for n in line)))))
                    line = []
                else:
                    line.append(c)
        else:
            units.append(Unit([child], root, False))
    return units


def tokens_of(units: list[Unit]) -> list[Tok]:
    toks = []

    def walk(node, unit):
        if isinstance(node, Text):
            for m in WORD.finditer(node.s):
                toks.append(Tok(m.group(), node, m.span(), unit))
        else:
            for c in node.children:
                walk(c, unit)

    for i, u in enumerate(units):
        for n in u.nodes:
            walk(n, i)
    return toks


def layer_tokens(text: str) -> list[Tok]:
    return [Tok(m.group(), None, m.span(), -1) for m in WORD.finditer(text or "")]


def align(a: list[Tok], b: list[Tok]) -> dict[int, int]:
    """Index in a -> index in b for words that match or were read differently (same place)."""
    pairs = {}
    sm = difflib.SequenceMatcher(None, [norm(t.word) for t in a], [norm(t.word) for t in b], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                pairs[i1 + k] = j1 + k
    return pairs


def _cyrillic(w: str) -> bool:
    return bool(re.search(r"[а-яё]", w, re.I))


def _similar(a: str, b: str) -> bool:
    """The same word read slightly differently (not just any word that happens to sit there)."""
    a, b = norm(a), norm(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def _latin_only(w: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]+", w))


# ---------------------------------------------------------------- the result

@dataclass
class MergeResult:
    html: str
    source: str             # accurate | spliced | fast
    fixed_words: int
    inserted_blocks: int
    coverage: float
    max_missing_run: int = 0


def _covered(witness: list[Tok], acc: list[Tok]) -> set[int]:
    """Witness words the accurate page has, maybe read differently. Words in a stretch where
    the accurate page has at least half as many words of its own count as read differently,
    not missing (a misread line plus a stray letter must not come back as a copy)."""
    covered = set()
    sm = difflib.SequenceMatcher(None, [norm(t.word) for t in witness], [norm(t.word) for t in acc], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and 2 * (j2 - j1) >= i2 - i1):
            covered.update(range(i1, i2))
    return covered


def coverage(acc_html: str, witnesses: list[str]) -> float:
    """Share of the witnesses' words the accurate page has (the worst witness counts)."""
    acc = tokens_of(units_of(parse(acc_html)))
    worst = 1.0
    for w in witnesses:
        wt = tokens_of(units_of(parse(w)))
        if wt:
            worst = min(worst, len(_covered(wt, acc)) / len(wt))
    return worst


def _runs(n: int, covered: set[int]) -> list[tuple[int, int]]:
    """Runs [start, end) of indexes 0..n-1 not in covered."""
    runs, start = [], None
    for i in range(n + 1):
        if i < n and i not in covered:
            start = i if start is None else start
        elif start is not None:
            runs.append((start, i))
            start = None
    return runs


def merge_page(acc_html: str, fast_html: str, layer_text: str, digital: bool) -> MergeResult:
    acc_root = parse(acc_html)
    acc_units = units_of(acc_root)
    acc = tokens_of(acc_units)
    fast_root = parse(fast_html or "")
    fast_units = units_of(fast_root)
    fast = tokens_of(fast_units)
    layer = layer_tokens(layer_text)

    fast_cov = _covered(fast, acc)
    layer_cov = _covered(layer, acc)
    base = fast if fast else layer
    cov = (len(fast_cov) / len(fast) if fast else len(layer_cov) / len(layer)) if base else 1.0
    if fast and cov < WHOLE_PAGE_FAST:
        return MergeResult(fast_html, "fast", 0, 0, cov, len(fast))

    # 1. words: two witnesses out of three win; a digital PDF's own text always wins
    to_fast, to_layer = align(acc, fast), align(acc, layer)
    edits: dict[int, str] = {}
    for i, t in enumerate(acc):
        a = t.word
        f = fast[to_fast[i]].word if i in to_fast else None
        l = layer[to_layer[i]].word if i in to_layer else None
        choice = a
        if digital and l is not None and _similar(a, l):
            choice = l
        elif f is not None and l is not None and f == l:
            choice = f
        elif _latin_only(a):
            witness = next((w for w in (f, l) if w and _cyrillic(w)), None)
            if witness:
                choice = witness
        if choice != a:
            edits[i] = choice
    by_text: dict[int, list] = {}
    for i, new in edits.items():
        by_text.setdefault(id(acc[i].text), []).append((acc[i], new))
    for items in by_text.values():
        node = items[0][0].text
        for tok, new in sorted(items, key=lambda x: -x[0].span[0]):
            node.s = node.s[:tok.span[0]] + new + node.s[tok.span[1]:]

    # 2. text a witness has and the accurate page lacks goes back where it was
    to_acc_fast = align(fast, acc)
    inserted = 0
    max_run = 0
    cursor: dict[int, tuple] = {}      # acc unit -> (container, index) of the last insertion after it

    def place_after(acc_unit: int | None, new_node: Node, is_item: bool):
        nonlocal inserted
        inserted += 1
        if acc_unit is None:
            key = -1
            container, index = cursor.get(key, (acc_root, -1))
            if is_item:
                new_node = Node("ul", "<ul>", [new_node])
            container.children.insert(index + 1, new_node)
            cursor[key] = (container, index + 1)
            return
        unit = acc_units[acc_unit]
        if acc_unit in cursor:
            container, index = cursor[acc_unit]
        elif unit.is_item and unit.parent.tag in ("ul", "ol"):
            container, index = unit.parent, unit.parent.children.index(unit.nodes[-1])
        else:
            top = unit.parent if unit.parent is not acc_root and unit.parent in acc_root.children else unit.nodes[-1]
            container, index = acc_root, acc_root.children.index(top)
        if is_item and container.tag not in ("ul", "ol"):
            new_node = Node("ul", "<ul>", [new_node])
        elif not is_item and container.tag in ("ul", "ol"):
            outer = acc_root.children.index(container)
            container, index = acc_root, outer
        container.children.insert(index + 1, new_node)
        cursor[acc_unit] = (container, index + 1)

    def acc_unit_before(witness_index: int, mapping: dict[int, int]) -> int | None:
        for j in range(witness_index - 1, -1, -1):
            if j in mapping:
                return acc[mapping[j]].unit
        return None

    item_marker = next((plain(u.nodes[0]).strip()[:1] for u in acc_units
                        if u.is_item and plain(u.nodes[0]).strip()[:1] in "☐■•"), "")
    for ui, unit in enumerate(fast_units):
        idx = [k for k, t in enumerate(fast) if t.unit == ui]
        if not idx:
            continue
        missing = [k for k in idx if k not in fast_cov]
        max_run = max([max_run] + [e - s for s, e in _runs(len(fast), fast_cov) if s in idx])
        if len(missing) < 0.6 * len(idx) or (len(idx) < 2 and not unit.is_item):
            continue
        content = "".join(serialize(n) for n in unit.nodes).strip()
        if unit.is_item:
            body = BULLET.sub("", re.sub(r"^<li[^>]*>|</li>$", "", content))
            new = parse(f"<li>{item_marker + ' ' if item_marker else ''}{body}</li>").children[0]
        elif len(unit.nodes) == 1 and isinstance(unit.nodes[0], Node):
            new = unit.nodes[0]
        else:
            new = parse(f"<p>{content}</p>").children[0]
        place_after(acc_unit_before(idx[0], to_acc_fast), new, unit.is_item)

    # 3. longer runs only the PDF layer has (not in fast either)
    if layer:
        to_acc_layer = align(layer, acc)
        in_fast = set(align(layer, fast)) if fast else set()
        for s, e in _runs(len(layer), layer_cov):
            run = range(s, e)
            if e - s < LAYER_MISSING_RUN or sum(1 for k in run if k in in_fast) > 0.5 * (e - s):
                continue
            max_run = max(max_run, e - s)
            chunk = layer_text[layer[s].span[0]:layer[e - 1].span[1]]
            end = layer_text[layer[e - 1].span[1]:layer[e - 1].span[1] + 1]
            if end in ".!?…":
                chunk += end
            place_after(acc_unit_before(s, to_acc_layer), parse(f"<p>{html.escape(chunk, quote=False)}</p>").children[0],
                        False)

    source = "spliced" if inserted else "accurate"
    return MergeResult(serialize(acc_root), source, len(edits), inserted, cov, max_run)


def needs_retry(result: MergeResult) -> bool:
    return result.source != "fast" and (result.coverage < RETRY_COVERAGE or result.max_missing_run >= RETRY_MISSING_RUN)
