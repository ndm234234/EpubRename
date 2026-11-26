#!/usr/bin/env python3
"""Make sure every EPUB has a cover that readers and catalogs can find.

Usage:
    python3 fix_cover.py [PATH ...] [--dry-run] [--yes] [--no-generate]

PATH is an .epub file or a folder (searched recursively); default: ./Out

For each book:
  ✅ the cover is marked and every listed file is there: the file is not touched;
  🔧 the cover picture is there but not marked (it is only shown on the first page,
     or the mark points to a file missing from the book): the mark is added;
  🎨 there is no cover picture at all: one is drawn from the title and author
     and added to the book (needs Pillow: pip install pillow);
  🧹 the book lists files it doesn't have: they are removed from the manifest, the
     reading order and the guide (readers otherwise show broken pictures or empty pages);
  🖼  the first page shows a picture the book doesn't have (e.g. file:///tmp/… left by
     the tool that made the book), so readers open on a broken image: it is pointed to
     the real picture (the unused one of the size the page gives, else the cover).
  📝 pages are plain HTML though the book lists them as XHTML (strict readers can't open
     them), or the mimetype file has a line break after its text: the pages are rewritten
     as XHTML with exactly the same words, the line break is removed.

Before changing a book the script shows what it found and asks: yes / all (yes to this
and every next book, no more questions) / no / cancel.
--yes fixes everything without asking, --dry-run only shows what would be done.
The .epub file is changed in place.

Only the package file (OPF), the first page (🖼) and the pages and mimetype file (📝) are
edited, so the rest of the book stays the same byte for byte (plus the new picture for 🎨). The new file is checked before it replaces
the old one, and it keeps the old file's date (the catalog sorts "recently added" by it).
"""

import argparse
import colorsys
import hashlib
import html
import io
import os
import posixpath
import re
import sys
import time
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from make_server import NS, locate_cover, text_of, zip_path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # only needed to draw covers for books that have no picture at all
    Image = None

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
COVER_SIZE = (1200, 1800)           # drawn covers, px (2:3, like printed books)
GENERATED_NAME = "cover-generated.jpg"

# Fonts with Cyrillic, bold first; the first one found is used
FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
FONT_REGULAR = {   # a lighter face for the author line, when there is one next to the bold font
    "georgiab.ttf": "georgia.ttf", "timesbd.ttf": "times.ttf", "arialbd.ttf": "arial.ttf",
    "segoeuib.ttf": "segoeui.ttf", "DejaVuSerif-Bold.ttf": "DejaVuSerif.ttf",
    "LiberationSerif-Bold.ttf": "LiberationSerif-Regular.ttf", "NotoSerif-Bold.ttf": "NotoSerif-Regular.ttf",
    "DejaVuSans-Bold.ttf": "DejaVuSans.ttf", "Georgia Bold.ttf": "Georgia.ttf", "Arial Bold.ttf": "Arial.ttf",
}


# ---------------------------------------------------------------- drawing a cover

def find_font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)


def font(path: str | None, size: int, regular: bool = False):
    if path is None:
        return ImageFont.load_default(size)   # no Cyrillic in it, but better than nothing
    if regular:
        p = Path(path)
        light = p.with_name(FONT_REGULAR.get(p.name, p.name))
        if light.is_file():
            path = str(light)
    return ImageFont.truetype(path, size)


def wrap(draw, text: str, fnt, width: int) -> list[str] | None:
    """Split text into lines no wider than width; None if a single word does not fit."""
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) <= width:
            line = test
        elif not line or draw.textlength(word, font=fnt) > width:
            return None
        else:
            lines.append(line)
            line = word
    return lines + [line] if line else lines


def draw_cover(title: str, authors: list[str]) -> bytes:
    """A cover in the catalog's placeholder style: colour from the title, title and author."""
    W, H = COVER_SIZE
    author = ", ".join(authors)
    hue = int(hashlib.md5((title + author).encode()).hexdigest(), 16) % 360 / 360

    def hsl(h, s, l):
        r, g, b = colorsys.hls_to_rgb(h % 1, l, s)
        return round(r * 255), round(g * 255), round(b * 255)

    # Smooth diagonal gradient: 2x2 corner colours stretched over the page
    corners = Image.new("RGB", (2, 2))
    corners.putdata([hsl(hue, .45, .45), hsl(hue + .06, .45, .36),
                     hsl(hue + .06, .45, .34), hsl(hue + .11, .45, .24)])
    img = corners.resize((W, H), Image.BICUBIC).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Spine shadow and a thin inner frame
    for x in range(60):
        d.line([(x, 0), (x, H)], fill=(0, 0, 0, int(70 * (1 - x / 60) ** 2)))
    m = 70
    d.rectangle([m, m, W - m, H - m], outline=(255, 255, 255, 70), width=3)

    path = find_font()
    inner = W - 2 * m - 120

    # Author: small capitals near the top
    if author:
        size = 52
        while size > 28 and d.textlength(author.upper(), font=font(path, size, True)) > inner:
            size -= 2
        fa = font(path, size, True)
        lines = wrap(d, author.upper(), fa, inner) or [author.upper()]
        y = m + 110
        for line in lines[:2]:
            d.text((W / 2, y), line, font=fa, fill=(255, 255, 255, 215), anchor="mt")
            y += size * 1.3

    # Title: as big as fits, centred a bit above the middle
    for size in range(124, 40, -4):
        ft = font(path, size)
        lines = wrap(d, title, ft, inner)
        if lines and len(lines) <= 7:
            break
    else:
        ft, lines = font(path, 40), [title[:60]]
    step = size * 1.18
    y = H * .46 - step * len(lines) / 2
    for line in lines:
        d.text((W / 2, y), line, font=ft, fill=(255, 255, 255, 255), anchor="mt")
        y += step
    d.line([(W / 2 - 60, y + 40), (W / 2 + 60, y + 40)], fill=(255, 255, 255, 150), width=4)

    img = Image.alpha_composite(img, layer).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------- editing the OPF (as text)

def attr_re(name: str) -> str:
    return rf"""\b{name}\s*=\s*(["'])(.*?)\1"""


def id_attr(item_id: str) -> str:
    return rf"""(?<![\w:.-])id\s*=\s*(["']){re.escape(item_id)}\1"""


def item_tag(text: str, item_id: str) -> re.Match | None:
    return re.search(rf"<(?:[\w.-]+:)?item\b[^>]*?{id_attr(item_id)}[^>]*>", text, re.S)


def set_cover_property(text: str, item_id: str) -> str:
    """EPUB 3: exactly this item has properties="cover-image"."""
    def drop(m):   # take the token off other items (e.g. one pointing to a missing file)
        tag = m.group(0)
        p = re.search(attr_re("properties"), tag)
        if re.search(id_attr(item_id), tag) or not p:
            return tag
        tokens = [t for t in p.group(2).split() if t != "cover-image"]
        start = p.start()
        while start and tag[start - 1].isspace():
            start -= 1
        kept = f' properties={p.group(1)}{" ".join(tokens)}{p.group(1)}' if tokens else ""
        return tag[:start] + kept + tag[p.end():]
    text = re.sub(r"<(?:[\w.-]+:)?item\b[^>]*\bcover-image\b[^>]*>", drop, text, flags=re.S)

    m = item_tag(text, item_id)
    tag = m.group(0)
    p = re.search(attr_re("properties"), tag)
    if p:
        if "cover-image" in p.group(2).split():
            return text
        q = p.group(1)
        new = tag[:p.start()] + f"properties={q}{(p.group(2) + ' cover-image').strip()}{q}" + tag[p.end():]
    else:
        new = re.sub(r"\s*(/?>)$", r' properties="cover-image"\1', tag)
    return text[:m.start()] + new + text[m.end():]


def set_cover_meta(text: str, item_id: str) -> str:
    """EPUB 2 (and read by EPUB 3 readers too): <meta name="cover" content="item-id"/>."""
    end = re.search(r"</([\w.-]+:)?metadata\s*>", text)
    if not end:
        raise ValueError("no </metadata> in the OPF")
    new = f'<{end.group(1) or ""}meta name="cover" content="{html.escape(item_id)}"/>'
    meta = re.compile(r"""<(?:[\w.-]+:)?meta\b[^>]*?\bname\s*=\s*(["'])cover\1[^>]*?/?>""", re.S)
    if meta.search(text):
        text = meta.sub(new, text, count=1)
        first = meta.search(text)   # remove other cover metas, keep the one just written
        return text[:first.end()] + meta.sub("", text[first.end():])
    indent = re.search(r"\n([ \t]*)[^\n]*$", text[:end.start()])
    pad = "\n" + (indent.group(1) + "  " if indent else "  ")
    return text[:end.start()].rstrip() + pad + new + "\n" + (indent.group(1) if indent else "") + text[end.start():]


def add_manifest_item(text: str, item_id: str, href: str, media_type: str) -> str:
    end = re.search(r"</([\w.-]+:)?manifest\s*>", text)
    if not end:
        raise ValueError("no </manifest> in the OPF")
    tag = f'<{end.group(1) or ""}item id="{html.escape(item_id)}" href="{html.escape(href)}" media-type="{media_type}"/>'
    indent = re.search(r"\n([ \t]*)[^\n]*$", text[:end.start()])
    pad = "\n" + (indent.group(1) + "  " if indent else "  ")
    return text[:end.start()].rstrip() + pad + tag + "\n" + (indent.group(1) if indent else "") + text[end.start():]


def unique(base: str, taken: set[str]) -> str:
    name, n = base, 1
    while name in taken:
        n += 1
        stem, dot, ext = base.rpartition(".")
        name = f"{stem}-{n}.{ext}" if dot else f"{base}-{n}"
    return name


# ---------------------------------------------------------------- one book

def read_package(zf: zipfile.ZipFile):
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//c:rootfile", NS)
    if rootfile is None:
        raise ValueError("rootfile not found in container.xml")
    opf_path = rootfile.attrib["full-path"]
    raw = zf.read(opf_path)
    return opf_path, raw, ET.fromstring(raw)


REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)   # http:, https:, data: … — not inside the book


def drop_tag(text: str, m: re.Match) -> str:
    """Cut a tag out together with the indentation / line break before it."""
    start = m.start()
    while start and text[start - 1] in " \t":
        start -= 1
    if start and text[start - 1] == "\n":
        start -= 1
    return text[:start] + text[m.end():]


def tags(text: str, name: str):
    """<name ...> elements (any prefix), with an optional empty closing tag."""
    return re.finditer(rf"<(?:[\w.-]+:)?{name}\b[^>]*?(?:/>|>\s*</(?:[\w.-]+:)?{name}\s*>|>)", text, re.S)


def attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""(?<![\w:.-]){name}\s*=\s*(["'])(.*?)\1""", tag, re.S)
    return html.unescape(m.group(2)) if m else None


def name_key(name: str) -> str:
    """Loose form of a file name: case, %-escapes and slashes don't matter."""
    return urllib.parse.unquote(name).replace("\\", "/").casefold()


def similar_file(path: str, names: set[str]) -> str | None:
    """A file in the book that is probably the one meant by path, spelled a bit differently
    (letter case, %20, or a non-UTF-8 zip name). Such files are never unlisted: some readers find them."""
    want, base = name_key(path), name_key(posixpath.basename(path))
    for n in sorted(names):
        variants = {n}
        try:
            variants.add(n.encode("cp437").decode("utf-8"))   # zip names stored without the UTF-8 flag
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for v in variants:
            if name_key(v) == want or name_key(posixpath.basename(v)) == base:
                return n
    return None


def drop_missing(text: str, opf_dir: str, gone: set[str]) -> tuple[str, list[str], set[str]]:
    """Remove manifest items whose files are not in the book, and what points to them
    (reading order, guide, the NCX table of contents reference). Returns (text, paths, ids)."""
    gone_paths, gone_ids = [], set()
    for m in reversed(list(tags(text, "item"))):
        href = attr(m.group(0), "href")
        if not href or REMOTE.match(href):
            continue
        path = zip_path(opf_dir, href)
        if path not in gone:
            continue
        gone_paths.insert(0, path)
        if attr(m.group(0), "id"):
            gone_ids.add(attr(m.group(0), "id"))
        text = drop_tag(text, m)
    if not gone_paths:
        return text, [], set()
    for m in reversed(list(tags(text, "itemref"))):
        if attr(m.group(0), "idref") in gone_ids:
            text = drop_tag(text, m)
    for m in reversed(list(tags(text, "reference"))):
        href = attr(m.group(0), "href")
        if href and not REMOTE.match(href) and zip_path(opf_dir, href) in gone_paths:
            text = drop_tag(text, m)
    spine = re.search(r"<(?:[\w.-]+:)?spine\b[^>]*>", text)
    if spine and attr(spine.group(0), "toc") in gone_ids:
        tag = re.sub(r"\s+toc\s*=\s*([\"']).*?\1", "", spine.group(0))
        text = text[:spine.start()] + tag + text[spine.end():]
    return text, gone_paths, gone_ids


PAGE_IMAGE = re.compile(r"""(<(?:\w+:)?(?:img|image)\b[^>]*?\s(?:src|(?:xlink:)?href)\s*=\s*)(["'])([^"']+)\2""", re.I)
WEB = re.compile(r"^(?:https?|data|ftp):", re.I)   # pictures that live on the web, not in the book


def picture_size(zf: zipfile.ZipFile, path: str) -> tuple[int, int] | None:
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(zf.read(path))) as im:
            return im.size
    except Exception:   # not a picture Pillow can read: just no size
        return None


def repair_cover_page(zf: zipfile.ZipFile, opf, opf_dir: str, names: set[str]) -> tuple[str, bytes, str, str] | None:
    """The first page shows a picture that is not in the book (often file:///tmp/… left by the
    tool that made the book), so readers show a broken image instead of the cover. Point it to the
    picture meant: the image no page shows whose size is the one the page gives, else the cover.
    Returns (page path, new page, old href, new picture path) or None."""
    items = opf.findall("opf:manifest/opf:item", NS)
    by_id = {i.get("id"): i for i in items}
    pages = [zip_path(opf_dir, i.get("href", "")) for i in items if "html" in i.get("media-type", "")]
    first = next((by_id.get(r.get("idref")) for r in opf.findall("opf:spine/opf:itemref", NS)
                  if r.get("linear", "yes") != "no"), None)
    if first is None or "html" not in first.get("media-type", ""):
        return None
    page = zip_path(opf_dir, first.get("href", ""))
    if page not in names:
        return None
    try:
        text = zf.read(page).decode("utf-8")
    except UnicodeDecodeError:
        return None
    page_dir = posixpath.dirname(page)
    broken = next((m for m in PAGE_IMAGE.finditer(text) if not WEB.match(m.group(3))
                   and (REMOTE.match(m.group(3)) or zip_path(page_dir, m.group(3)) not in names)
                   and similar_file(zip_path(page_dir, m.group(3)), names) is None), None)
    if broken is None:
        return None

    shown = set()
    for p in pages:
        if p in names:
            d = posixpath.dirname(p)
            shown |= {zip_path(d, m.group(3)) for m in PAGE_IMAGE.finditer(zf.read(p).decode("utf-8", "replace"))}
    tag = text[broken.start():text.find(">", broken.end()) + 1]
    w, h = attr(tag, "width"), attr(tag, "height")
    target = None
    if w and h and w.isdigit() and h.isdigit():
        unused = [p for i in items if i.get("media-type", "").startswith("image/")
                  and (p := zip_path(opf_dir, i.get("href", ""))) in names and p not in shown]
        sizes = {p: picture_size(zf, p) for p in unused}
        want = (int(w), int(h))
        same = [p for p, s in sizes.items() if s == want]
        if not same and want[1]:   # the picture may be stored smaller than the page says, in proportion
            same = [p for p, s in sizes.items() if s and s[1] and abs(s[0] / s[1] - want[0] / want[1]) < 0.01]
        if same:   # several (e.g. a small cover thumbnail in the same proportion): the sharpest
            target = max(same, key=lambda p: sizes[p][0] * sizes[p][1])
    if target is None:
        target = locate_cover(zf, opf, opf_dir)[0]
    if target is None:
        return None
    href = html.escape(posixpath.relpath(target, page_dir or "."), quote=True)
    new = text[:broken.start(3)] + href + text[broken.end(3):]
    return page, new.encode("utf-8"), broken.group(3), target


# ---------------------------------------------------------------- HTML pages declared as XHTML

XHTML_NS = "http://www.w3.org/1999/xhtml"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
NOT_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
PREFIXES = {"xlink": "http://www.w3.org/1999/xlink", "epub": "http://www.idpf.org/2007/ops"}
# HTML parsing lowercases names; inside <svg> they have to be in their own case again
SVG_NAMES = {n.lower(): n for n in (
    "viewBox preserveAspectRatio gradientUnits gradientTransform patternUnits patternContentUnits "
    "patternTransform clipPathUnits maskUnits maskContentUnits filterUnits primitiveUnits textLength "
    "lengthAdjust markerWidth markerHeight markerUnits refX refY stdDeviation baseProfile startOffset "
    "spreadMethod pathLength numOctaves baseFrequency kernelMatrix tableValues xChannelSelector "
    "yChannelSelector diffuseConstant specularExponent specularConstant surfaceScale "
    "linearGradient radialGradient clipPath foreignObject textPath feGaussianBlur feOffset feBlend "
    "feColorMatrix feComposite feFlood feMerge feMergeNode feImage feMorphology feTurbulence "
    "feDisplacementMap feTile feComponentTransfer feFuncR feFuncG feFuncB feFuncA feDropShadow "
    "feConvolveMatrix feDiffuseLighting feSpecularLighting fePointLight feSpotLight feDistantLight").split()}


class _ToXhtml(HTMLParser):
    """Writes an HTML page back out as well-formed XHTML: empty elements closed, attributes
    quoted, named entities as characters, unclosed elements closed, the XHTML namespace on <html>."""
    def __init__(self, prefixes: set[str]):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.prefixes, self.svg = [], [], prefixes, 0

    def esc(self, s: str, quote=False) -> str:
        s = NOT_XML_CHARS.sub("", s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return s.replace('"', "&quot;") if quote else s

    def open(self, tag, attrs, empty):
        svg = self.svg or tag == "svg"
        name = SVG_NAMES.get(tag, tag) if svg else tag
        seen, parts = set(), []
        for k, v in attrs:
            k = SVG_NAMES.get(k, k) if svg else k
            if k in seen or not re.fullmatch(r"[A-Za-z_][\w.:-]*", k):
                continue   # XML allows each attribute once, and only proper names
            seen.add(k)
            parts.append(f' {k}="{self.esc(k if v is None else v, True)}"')
        if tag == "html":
            if "xmlns" not in seen:
                parts.insert(0, f' xmlns="{XHTML_NS}"')
            parts += [f' xmlns:{p}="{PREFIXES[p]}"' for p in sorted(self.prefixes) if f"xmlns:{p}" not in seen]
        self.out.append(f"<{name}{''.join(parts)}{' /' if empty else ''}>")
        if not empty:
            self.stack.append(name)
            self.svg += tag == "svg"

    def handle_starttag(self, tag, attrs):
        self.open(tag, attrs, tag in VOID)

    def handle_startendtag(self, tag, attrs):
        self.open(tag, attrs, True)

    def handle_endtag(self, tag):
        name = SVG_NAMES.get(tag, tag) if self.svg else tag
        if name not in self.stack:
            return   # a closing tag with nothing to close (e.g. </br>, or one closed already)
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            self.svg -= top == "svg"
            if top == name:
                break

    def handle_data(self, data):
        self.out.append(self.esc(data))

    def handle_comment(self, data):
        self.out.append("<!--" + NOT_XML_CHARS.sub("", data).replace("--", "- -").rstrip("-") + "-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        if data.startswith("xml ") and not "".join(self.out).strip():
            self.out.append(f"<?{data}>" if data.endswith("?") else f"<?{data}?>")

    def unknown_decl(self, data):
        if data.startswith("CDATA["):
            self.out.append(self.esc(data[6:]))

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def to_xhtml(text: str) -> str:
    used = {p for p in PREFIXES if re.search(rf"\s{p}:[\w-]+\s*=", text)}
    parser = _ToXhtml(used)
    parser.feed(text)
    return parser.result()


def page_text(text: str) -> str:
    """The words of a page as a browser reads them (no comments), spaces squeezed."""
    class Words(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.words = []
        def handle_data(self, data):
            self.words.append(data)
    p = Words()
    p.feed(text)
    p.close()
    return " ".join(NOT_XML_CHARS.sub("", "".join(p.words)).split())


def xhtml_fix(raw: bytes) -> bytes | None:
    """A page listed as XHTML that is really HTML (so strict readers can't open it) → the
    same page as XHTML. None when it already is XML. The words must come out the same."""
    try:
        ET.fromstring(raw)
        return None
    except ET.ParseError:
        pass
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("a page is not UTF-8, not touching it")
    new = to_xhtml(text)
    root = ET.fromstring(new.encode("utf-8"))   # raises if it still isn't XML
    if root.tag != f"{{{XHTML_NS}}}html":
        raise ValueError("a page has no <html> element, not touching it")
    if " ".join("".join(root.itertext()).split()) != page_text(text):
        raise ValueError("a page would lose or change words when rewritten, not touching it")
    return new.encode("utf-8")


MIMETYPE = b"application/epub+zip"


COVER_FOUND = {"guide": "the guide's cover page shows it", "named": "a picture named \"cover\"",
               "first page": "the picture on the first page"}


class Plan:
    """What fix_cover would do with one book."""
    def __init__(self):
        self.cover = "ok"          # ok / marked / generated / skipped
        self.cleaned = False       # dangling manifest entries removed
        self.page_fixed = False    # the first page's broken picture pointed to the real one
        self.xhtml_pages = 0       # HTML pages listed as XHTML, rewritten as XHTML
        self.mimetype_fixed = False   # "mimetype" had extra bytes (a line break)
        self.steps: list[str] = []   # human-readable list of changes
        self.notes: list[str] = []   # problems that are only reported
        self.replace: dict[str, bytes] = {}
        self.removed_ids: set[str] = set()   # manifest items taken out
        self.added_ids: set[str] = set()     # manifest items put in
        self.add: dict[str, bytes] = {}

    @property
    def changes(self) -> bool:
        return bool(self.replace)

    @property
    def icon(self) -> str:
        return {"generated": "🎨", "marked": "🔧"}.get(self.cover, "🧹" if self.cleaned else
                                                     "🖼 " if self.page_fixed else
                                                     "📝" if self.xhtml_pages or self.mimetype_fixed else
                                                     "⏭ " if self.cover == "skipped" else "✅")


def plan_fix(zf: zipfile.ZipFile, generate: bool) -> Plan:
    plan = Plan()
    opf_path, raw, opf = read_package(zf)
    opf_dir = posixpath.dirname(opf_path)
    all_names = zf.namelist()
    names = set(all_names)
    if len(all_names) != len(names):
        raise ValueError("the book has files stored twice under one name, not touching it")
    if any(i.flag_bits & 1 for i in zf.infolist()):
        raise ValueError("the book is password-protected, not touching it")
    items = opf.findall("opf:manifest/opf:item", NS)
    missing, gone = [], set()
    for i in items:
        href = i.get("href")
        if not href or REMOTE.match(href) or zip_path(opf_dir, href) in names:
            continue
        near = similar_file(zip_path(opf_dir, href), names)
        if near:
            plan.notes.append(f"lists {zip_path(opf_dir, href)}, the book has {near}: names differ a little, left as is")
        else:
            missing.append(i)
            gone.add(zip_path(opf_dir, href))
    repaired = repair_cover_page(zf, opf, opf_dir, names)
    if repaired:
        page, new_page, old_href, target = repaired
        plan.replace[page] = new_page
        plan.page_fixed = True
        plan.steps.append(f"the first page shows a picture the book doesn't have ({old_href})"
                          f" → show {target} there")
    mimetype = zf.read("mimetype") if "mimetype" in names else MIMETYPE
    if mimetype != MIMETYPE:
        if mimetype.strip() != MIMETYPE:
            raise ValueError("the book's mimetype is not application/epub+zip, not touching it")
        plan.replace["mimetype"] = MIMETYPE
        plan.mimetype_fixed = True
        plan.steps.append("the mimetype file has a line break after application/epub+zip → remove it")
    html_pages = []
    for i in items:
        p = zip_path(opf_dir, i.get("href", ""))
        if i.get("media-type") == "application/xhtml+xml" and p in names:
            fixed = xhtml_fix(plan.replace.get(p) or zf.read(p))
            if fixed is not None:
                plan.replace[p] = fixed
                html_pages.append(p)
    if html_pages:
        plan.xhtml_pages = len(html_pages)
        plan.steps.append(f"{len(html_pages)} page(s) are HTML, not the XHTML the book says (strict readers"
                          " can't open them) → rewrite them as XHTML, same text")
    path, how = locate_cover(zf, opf, opf_dir)
    if how == "declared" and not missing:
        return plan

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("the OPF is not UTF-8, not touching it")
    epub3 = opf.get("version", "2.0").startswith("3")
    ids = {i.get("id") for i in items}
    item_id = None

    if how == "declared":
        item_id = next((i.get("id") for i in items if zip_path(opf_dir, i.get("href", "")) == path), None)
    elif path:
        # Mark the picture that is already there; add it to the manifest if it is missing there
        item = next((i for i in items if zip_path(opf_dir, i.get("href", "")) == path), None)
        if item is None:
            item_id = unique("cover-image", ids)
            href = posixpath.relpath(path, opf_dir or ".")
            media = MEDIA_TYPES.get(posixpath.splitext(path)[1].lower(), "image/jpeg")
            text = add_manifest_item(text, item_id, href, media)
            plan.added_ids.add(item_id)
        else:
            item_id = item.get("id")
        plan.cover = "marked"
        plan.steps.append(f"cover not marked, {COVER_FOUND.get(how, how)}: {path} → mark it as the cover")
    elif not generate:
        plan.cover = "skipped"
        plan.notes.append("no cover picture in the book (--no-generate: not drawing one)")
    else:
        if Image is None:
            raise RuntimeError("no picture in the book, and Pillow is needed to draw one (pip install pillow)")
        md = opf.find("opf:metadata", NS)
        md = md if md is not None else opf
        title = text_of(md.find(".//dc:title", NS)) or "Untitled"
        authors = [a for a in (text_of(c) for c in md.findall(".//dc:creator", NS)) if a]
        name = unique(posixpath.join(opf_dir, GENERATED_NAME) if opf_dir else GENERATED_NAME, names)
        plan.add[name] = draw_cover(title, authors)
        item_id = unique("cover-generated", ids)
        text = add_manifest_item(text, item_id, posixpath.relpath(name, opf_dir or "."), "image/jpeg")
        plan.added_ids.add(item_id)
        plan.cover = "generated"
        plan.steps.append(f"no cover picture in the book → draw one from the title and author: {name}")

    if plan.cover in ("marked", "generated"):
        text = set_cover_meta(text, item_id)
        if epub3:
            text = set_cover_property(text, item_id)

    if missing:
        text, gone, gone_ids = drop_missing(text, opf_dir, gone)
        plan.cleaned = bool(gone)
        plan.removed_ids = gone_ids
        pages = {i.get("id") for i in items if i.get("id") in gone_ids} & {
            r.get("idref") for r in opf.findall("opf:spine/opf:itemref", NS)}
        plan.steps.append("the book lists files it doesn't have: " + ", ".join(gone)
                          + " → remove them from the manifest"
                          + (" and the reading order" if pages else ""))
        if pages:
            plan.notes.append(f"{len(pages)} page(s) are missing from the book itself: their text can't be restored")
        meta = re.search(r"""<(?:[\w.-]+:)?meta\b[^>]*?\bname\s*=\s*(["'])cover\1[^>]*?/?>""", text, re.S)
        if meta and attr(meta.group(0), "content") in gone_ids:   # a cover mark left pointing nowhere
            text = set_cover_meta(text, item_id) if item_id else drop_tag(text, meta)

    new_opf = text.encode("utf-8")
    ET.fromstring(new_opf)   # still well-formed XML
    plan.replace[opf_path] = new_opf
    return plan


def opf_outline(opf) -> dict:
    """The parts of the OPF the fix may touch, to compare the old and new file."""
    md = opf.find("opf:metadata", NS)
    return {
        "manifest": {i.get("id"): (i.get("href"), i.get("media-type")) for i in opf.findall("opf:manifest/opf:item", NS)},
        "spine": [r.get("idref") for r in opf.findall("opf:spine/opf:itemref", NS)],
        "metadata": [(e.tag, (e.text or "").strip(), sorted((k, v) for k, v in e.attrib.items()
                                                            if not (e.tag.endswith("meta") and e.get("name") == "cover")))
                     for e in (md if md is not None else [])
                     if not (e.tag.endswith("meta") and e.get("name") == "cover")],
    }


def check_outline(old: dict, new: dict, plan: "Plan") -> None:
    """Only what the plan says may change: listed items removed / added, their pages out of
    the reading order, the cover mark. Titles, authors and everything else must stay."""
    om, nm = old["manifest"], new["manifest"]
    for item_id, value in om.items():
        if item_id not in plan.removed_ids and nm.get(item_id) != value:
            raise RuntimeError(f"manifest item {item_id!r} changed unexpectedly")
    if set(nm) - set(om) != plan.added_ids:
        raise RuntimeError("unexpected items in the new manifest")
    if new["spine"] != [r for r in old["spine"] if r not in plan.removed_ids]:
        raise RuntimeError("the reading order changed unexpectedly")
    if new["metadata"] != old["metadata"]:
        raise RuntimeError("title / author / other book data changed unexpectedly")


def rewrite_epub(src: Path, plan: "Plan") -> None:
    """Write a fixed copy next to src, check it thoroughly, then put it in place keeping src's date.
    The original is only replaced (atomically) after every check passed; on any problem it stays as it was."""
    tmp = src.with_name(src.name + ".fixcover.tmp")
    st = src.stat()
    try:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
            infos = sorted(zin.infolist(), key=lambda i: i.filename != "mimetype")   # mimetype first
            for info in infos:
                data = plan.replace.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)   # checks the CRC: a damaged original stops here
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.external_attr = info.external_attr
                zi.compress_type = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zi, data)
            for name, data in plan.add.items():
                zi = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
                zi.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zi, data)
        with open(tmp, "rb+") as fh:   # really on the disk before it replaces the original
            os.fsync(fh.fileno())

        with zipfile.ZipFile(tmp) as zt, zipfile.ZipFile(src) as zs:
            # 1. The new zip reads back without errors
            if zt.testzip() is not None:
                raise RuntimeError("the new file is damaged")
            # 2. Every old file is there with the same content (CRC and size), except the edited OPF
            new_infos = {i.filename: i for i in zt.infolist()}
            if len(new_infos) != len(zt.infolist()):
                raise RuntimeError("duplicate names in the new file")
            for old in zs.infolist():
                new = new_infos.get(old.filename)
                if new is None:
                    raise RuntimeError(f"{old.filename} went missing in the new file")
                if old.filename not in plan.replace and (new.CRC, new.file_size) != (old.CRC, old.file_size):
                    raise RuntimeError(f"{old.filename} changed in the new file")
            if set(new_infos) - set(zs.namelist()) != set(plan.add):
                raise RuntimeError("unexpected files in the new file")
            for name, data in {**plan.replace, **plan.add}.items():
                if zt.read(name) != data:
                    raise RuntimeError(f"{name} was not written correctly")
            # 3. The OPF changed only where intended
            opf_path, _, opf = read_package(zt)
            _, _, old_opf = read_package(zs)
            check_outline(opf_outline(old_opf), opf_outline(opf), plan)
            # 4. Everything listed is in the book, and the cover is found
            opf_dir = posixpath.dirname(opf_path)
            names = set(new_infos)
            if any(i.get("href") and not REMOTE.match(i.get("href")) and zip_path(opf_dir, i.get("href")) not in names
                   and similar_file(zip_path(opf_dir, i.get("href")), names) is None
                   for i in opf.findall("opf:manifest/opf:item", NS)):
                raise RuntimeError("the new file still lists missing files")
            if plan.cover != "skipped" and locate_cover(zt, opf, opf_dir)[1] != "declared":
                raise RuntimeError("the cover is still not found in the new file")
        os.replace(tmp, src)
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- main

ANSWERS = {"y": "yes", "yes": "yes", "д": "yes", "да": "yes",
           "a": "all", "all": "all", "в": "all", "все": "all", "всё": "all",
           "n": "no", "no": "no", "н": "no", "нет": "no",
           "c": "cancel", "cancel": "cancel", "q": "cancel", "quit": "cancel", "о": "cancel", "отмена": "cancel"}


def ask() -> str:
    """yes / all (yes to this and every next book) / no / cancel; Ctrl+C or end of input cancel."""
    while True:
        try:
            answer = input("   Fix it? The .epub file will be changed. [y]es / [a]ll (yes, don't ask again) / [n]o / [c]ancel: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "cancel"
        if answer in ANSWERS:
            return ANSWERS[answer]
        print("   Please answer y, a, n or c")


def collect(paths: list[str]) -> list[Path]:
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() == ".epub"
                            and ".index" not in f.relative_to(p).parts)
        elif p.is_file():
            files.append(p)
        else:
            print(f"❌ Not found: {p}")
    return files


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Make sure every EPUB has a cover readers can find.")
    parser.add_argument("paths", nargs="*", default=["Out"], help="EPUB files or folders (default: Out)")
    parser.add_argument("--dry-run", action="store_true", help="only show what would be done")
    parser.add_argument("-y", "--yes", action="store_true", help="fix every book without asking")
    parser.add_argument("--no-generate", action="store_true",
                        help="do not draw covers for books that have no picture at all")
    args = parser.parse_args()

    if not (args.dry_run or args.yes or sys.stdin.isatty()):
        print("❌ Not a terminal, so I can't ask about each book: add --yes to fix all, or --dry-run")
        return 1
    files = collect(args.paths)
    if not files:
        print("📭 No epub files found")
        return 0
    print(f"📚 Checking {len(files)} epub file(s)" + ("  (dry run: nothing is changed)" if args.dry_run
          else "" if args.yes else "  (asking before each change)"))
    if not args.dry_run:
        print("⚠️  WARNING: fixing a book changes its .epub file in place (the original is not kept).\n"
              "    Make a backup first if you may need the books unchanged; --dry-run only shows the changes.")
    if Image is None and not args.no_generate:
        print("ℹ️  Pillow not installed: books without any picture can't get a drawn cover (pip install pillow)")
    elif Image is not None and not args.no_generate and find_font() is None:
        print("⚠️  No font with Cyrillic found: drawn covers may show boxes instead of letters")
    print()

    ask_each = not (args.dry_run or args.yes)
    count = dict.fromkeys(["ok", "marked", "generated", "cleaned", "page", "xhtml", "skipped", "declined", "error"], 0)
    cancelled = 0
    for n, f in enumerate(files):
        try:
            with zipfile.ZipFile(f) as zf:
                plan = plan_fix(zf, not args.no_generate)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, RuntimeError, OSError) as e:
            count["error"] += 1
            print(f"❌ {f.name}\n     {e}")
            continue
        if not plan.changes and not plan.notes:
            count["ok"] += 1
            continue
        print(f"{plan.icon} {f.name}")
        for line in plan.steps:
            print(f"     • {line}")
        for line in plan.notes:
            print(f"     ⚠️  {line}")
        if not plan.changes:
            count["skipped"] += 1
            continue
        if ask_each:
            answer = ask()
            if answer == "all":
                ask_each = False
                print("   👍 Fixing this and all the remaining books without asking")
            if answer == "cancel":
                cancelled = len(files) - n
                print("   🛑 Cancelled: this and the remaining books are not changed")
                break
            if answer == "no":
                count["declined"] += 1
                print("   ⏭  Left as it is\n")
                continue
        if not args.dry_run:
            try:
                rewrite_epub(f, plan)
            except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, RuntimeError, OSError) as e:
                count["error"] += 1
                print(f"   ❌ {e}\n")
                continue
            print("   ✔️  Fixed\n")
        if plan.cover in ("marked", "generated"):
            count[plan.cover] += 1
        if plan.cleaned:
            count["cleaned"] += 1
        if plan.page_fixed:
            count["page"] += 1
        if plan.xhtml_pages or plan.mimetype_fixed:
            count["xhtml"] += 1

    verb = "would be " if args.dry_run else ""
    parts = [f"✅ fine {count['ok']}", f"🔧 cover {verb}marked {count['marked']}",
             f"🎨 cover {verb}drawn {count['generated']}", f"🧹 missing files {verb}unlisted {count['cleaned']}"]
    if count["xhtml"]:
        parts.append(f"📝 pages / mimetype {verb}made valid {count['xhtml']}")
    if count["page"]:
        parts.append(f"🖼  broken cover page {verb}fixed {count['page']}")
    if count["skipped"]:
        parts.append(f"⏭  only reported {count['skipped']}")
    if count["declined"]:
        parts.append(f"🙅 declined {count['declined']}")
    if cancelled:
        parts.append(f"🛑 not checked {cancelled}")
    parts.append(f"❌ errors {count['error']}")
    print("\n✨ Done:  " + "  ·  ".join(parts))
    if (count["marked"] or count["generated"]) and not args.dry_run:
        print("📖 Run make_server.py to see the new covers in the catalog")
    return 2 if count["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
