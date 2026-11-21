#!/usr/bin/env python3
"""Build index.html — a browsable catalog of all EPUB books in a folder.

Usage:
    python3 make_index.py [FOLDER]        (default: ./Out)

The folder is scanned recursively. The catalog shows covers, authors, titles,
descriptions, has search, sorting and grouping by author; clicking a book opens
the file. Works offline; no internet or server needed.

Written files:  FOLDER/index.html  and small cover copies in  FOLDER/.index/covers/
EPUB files are only read, never modified.
Pillow (optional) is used to shrink covers; without it covers are copied as is.
"""

import argparse
import hashlib
import html
import io
import json
import posixpath
import re
import sys
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Pillow is optional
    Image = None

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
THUMB_SIZE = (300, 450)   # max cover size in the catalog, px
THUMB_QUALITY = 82        # JPEG quality of cover copies
INDEX_DIR = ".index"      # subfolder for catalog files (covers)
COVERS_DIR = "covers"
DEFAULT_TITLE = "Books"    # catalog title shown on the page


# ---------------------------------------------------------------- reading EPUB

def text_of(el) -> str:
    return re.sub(r"\s+", " ", (el.text or "")).strip() if el is not None else ""


def html_to_text(s: str) -> str:
    """Descriptions are often HTML: turn them into plain text."""
    s = re.sub(r"<\s*(br|/p|/div|/li)\b[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def find_cover(zf: zipfile.ZipFile, opf, opf_dir: str) -> bytes | None:
    """Locate the cover image using EPUB 3, EPUB 2 and common-name conventions."""
    manifest = opf.find("opf:manifest", NS)
    if manifest is None:
        return None
    items = manifest.findall("opf:item", NS)
    images = [i for i in items if i.get("media-type", "").startswith("image/")]

    candidate = None
    # EPUB 3: <item properties="cover-image">
    candidate = next((i for i in images if "cover-image" in i.get("properties", "").split()), None)
    # EPUB 2: <meta name="cover" content="item-id">
    if candidate is None:
        meta = next((m for m in opf.iter("{%s}meta" % NS["opf"]) if m.get("name") == "cover"), None)
        if meta is not None:
            candidate = next((i for i in images if i.get("id") == meta.get("content")), None)
    # Fallback: an image whose id or file name contains "cover"
    if candidate is None:
        candidate = next((i for i in images
                          if "cover" in (i.get("id", "") + i.get("href", "")).lower()), None)
    if candidate is None:
        return None

    href = urllib.parse.unquote(candidate.get("href", ""))
    path = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
    try:
        return zf.read(path)
    except KeyError:
        return None


def save_cover(raw: bytes | None, covers_dir: Path, used: set[str]) -> str:
    """Save a small copy of the cover into covers_dir; return its file name ("" if none).

    The name is a hash of the original image, so an existing copy is reused
    on the next run instead of being generated again.
    """
    if not raw:
        return ""
    digest = hashlib.sha256(raw).hexdigest()[:20]
    if Image is not None:
        name = digest + ".jpg"
        target = covers_dir / name
        if not target.exists():
            try:
                img = Image.open(io.BytesIO(raw))
                img.thumbnail(THUMB_SIZE)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(target, "JPEG", quality=THUMB_QUALITY, optimize=True)
            except Exception:
                target.unlink(missing_ok=True)
                return ""
    else:
        ext = (".png" if raw[:8] == b"\x89PNG\r\n\x1a\n"
               else ".gif" if raw[:4] == b"GIF8" else ".jpg")
        name = digest + ext
        target = covers_dir / name
        if not target.exists():
            target.write_bytes(raw)
    used.add(name)
    return f"{INDEX_DIR}/{COVERS_DIR}/{name}"


def read_book(path: Path, root: Path, covers_dir: Path, used: set[str]) -> dict:
    """Collect everything the catalog needs about one book."""
    st = path.stat()
    rel = path.relative_to(root).as_posix()
    book = {
        "file": rel,
        "href": "/".join(urllib.parse.quote(part) for part in rel.split("/")),
        "size": st.st_size,
        "added": int(st.st_mtime),
        "title": path.stem, "authors": [], "series": "", "seriesIndex": "",
        "year": "", "publisher": "", "language": "", "description": "",
        "cover": "", "error": "",
    }
    with zipfile.ZipFile(path, mode="r") as zf:  # read-only
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", NS)
        if rootfile is None:
            raise ValueError("rootfile not found in container.xml")
        opf_path = rootfile.attrib["full-path"]
        opf = ET.fromstring(zf.read(opf_path))
        cover_raw = find_cover(zf, opf, posixpath.dirname(opf_path))

    md = opf.find("opf:metadata", NS)
    if md is None:
        md = opf
    book["title"] = text_of(md.find(".//dc:title", NS)) or path.stem
    book["authors"] = [a for a in (text_of(c) for c in md.findall(".//dc:creator", NS)) if a]
    book["publisher"] = text_of(md.find(".//dc:publisher", NS))
    book["language"] = text_of(md.find(".//dc:language", NS))
    book["description"] = html_to_text(text_of(md.find(".//dc:description", NS)))
    date = text_of(md.find(".//dc:date", NS))
    # Calibre writes "0101-01-01" when the date is unknown: ignore such years
    if (m := re.match(r"\d{4}", date)) and int(m.group()) >= 1000:
        book["year"] = m.group()
    # Calibre series: <meta name="calibre:series" content="...">
    for m in md.iter("{%s}meta" % NS["opf"]):
        if m.get("name") == "calibre:series":
            book["series"] = m.get("content", "")
        elif m.get("name") == "calibre:series_index":
            book["seriesIndex"] = m.get("content", "").removesuffix(".0")
    book["cover"] = save_cover(cover_raw, covers_dir, used)
    return book


# ---------------------------------------------------------------- HTML page

PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #f6f5f2; --card: #ffffff; --text: #1d1d1f; --muted: #6e6e73;
    --line: #e3e1dc; --accent: #2f6fde; --chip: #eeece7; --shadow: 0 1px 3px rgba(0,0,0,.08);
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161618; --card: #222225; --text: #ececee; --muted: #9a9aa0;
            --line: #333338; --accent: #6ea0ff; --chip: #2d2d31; --shadow: none; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--text); }
  header { position: sticky; top: 0; z-index: 5; background: var(--bg);
           border-bottom: 1px solid var(--line); padding: 14px 24px; }
  .bar { max-width: 1400px; margin: 0 auto; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  h1 { font-size: 20px; margin: 0 12px 0 0; white-space: nowrap; }
  #count { color: var(--muted); font-size: 14px; white-space: nowrap; }
  input[type=search] { flex: 1 1 260px; min-width: 200px; padding: 9px 12px; font: inherit;
         border: 1px solid var(--line); border-radius: 8px; background: var(--card); color: var(--text); }
  select, button { font: inherit; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
         background: var(--card); color: var(--text); cursor: pointer; }
  button[aria-pressed=true] { border-color: var(--accent); color: var(--accent); }
  main { max-width: 1400px; margin: 0 auto; padding: 20px 24px 60px; }
  h2 { font-size: 17px; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
  h2 small { color: var(--muted); font-weight: normal; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 20px; }
  .book { background: var(--card); border-radius: 10px; box-shadow: var(--shadow);
          border: 1px solid var(--line); overflow: hidden; display: flex; flex-direction: column; }
  .cover { display: block; aspect-ratio: 2 / 3; background: var(--chip); overflow: hidden; }
  .cover { position: relative; }
  .cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cover::after { content: "📁"; position: absolute; right: 8px; bottom: 8px; font-size: 20px;
                  padding: 4px 6px; border-radius: 8px; background: rgba(0,0,0,.55);
                  opacity: 0; transition: opacity .15s; }
  .cover:hover::after { opacity: 1; }
  .ph { width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center;
        padding: 14px; color: #fff; font-weight: 600; text-align: center; gap: 8px; }
  .ph .a { font-weight: 400; font-size: 13px; opacity: .9; }
  .info { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 3px; flex: 1; }
  .title { font-weight: 600; color: var(--text); text-decoration: none; }
  .title:hover { color: var(--accent); }
  .authors { color: var(--muted); font-size: 14px; }
  .authors a { color: inherit; text-decoration: none; cursor: pointer; }
  .authors a:hover { color: var(--accent); text-decoration: underline; }
  .meta { color: var(--muted); font-size: 12px; }
  .err { color: #d33; font-size: 12px; }
  details { margin-top: 4px; font-size: 13px; }
  summary { cursor: pointer; color: var(--accent); list-style: none; }
  summary::-webkit-details-marker { display: none; }
  details p { white-space: pre-line; margin: 6px 0 0; }
  .empty { color: var(--muted); text-align: center; padding: 60px 0; }
  footer { color: var(--muted); font-size: 12px; text-align: center; padding-bottom: 30px; }
</style>
</head>
<body>
<header><div class="bar">
  <h1>📚 __TITLE__</h1>
  <span id="count"></span>
  <input id="q" type="search" placeholder="Search by title, author, description…" autofocus>
  <select id="sort" title="Sort">
    <option value="author">Sort: author</option>
    <option value="title">Sort: title</option>
    <option value="added">Sort: recently added</option>
    <option value="size">Sort: file size</option>
  </select>
  <button id="group" aria-pressed="false" title="Group books by author">👤 Group by author</button>
</div></header>
<main id="list"></main>
<footer>Generated __DATE__ · click a cover to open its folder, a title to open the book</footer>

<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const books = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const coll = new Intl.Collator("ru", { sensitivity: "base", numeric: true });
const norm = s => s.toLowerCase().replace(/ё/g, "е");
const authorLine = b => b.authors.length ? b.authors.join(", ") : "Unknown Author";
const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " KB";
books.forEach(b => b._search = norm([b.title, authorLine(b), b.series, b.description, b.file].join(" ")));

// Settings survive page reloads
const saved = JSON.parse(localStorage.getItem("epubIndex") || "{}");
if (saved.sort) $("sort").value = saved.sort;
if (saved.group === true) $("group").setAttribute("aria-pressed", "true");
const save = () => localStorage.setItem("epubIndex", JSON.stringify(
  { sort: $("sort").value, group: $("group").getAttribute("aria-pressed") === "true" }));

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v; else if (k === "on") e.addEventListener("click", v);
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

function placeholder(b) {
  let h = 0; for (const ch of b.title + authorLine(b)) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const ph = el("div", { class: "ph", style: `background: linear-gradient(160deg, hsl(${h} 45% 42%), hsl(${(h + 40) % 360} 45% 28%))` },
                b.title, el("span", { class: "a" }, authorLine(b)));
  return ph;
}

function card(b) {
  // Cover opens the book's folder (as a file list in a new browser tab)
  const slash = b.href.lastIndexOf("/");
  const folder = slash >= 0 ? b.href.slice(0, slash + 1) : "./";
  const folderName = slash >= 0 ? decodeURIComponent(folder) : "(catalog folder)";
  const cover = el("a", { class: "cover", href: folder, target: "_blank",
                          title: "📁 Open folder: " + folderName },
                   b.cover ? el("img", { src: b.cover, alt: "", loading: "lazy" }) : placeholder(b));
  const authors = el("div", { class: "authors" });
  (b.authors.length ? b.authors : ["Unknown Author"]).forEach((a, i) => {
    if (i) authors.append(", ");
    authors.append(el("a", { title: "Show all books by this author",
                             on: () => { $("q").value = a; render(); } }, a));
  });
  const meta = [b.series && `${b.series}${b.seriesIndex ? " #" + b.seriesIndex : ""}`,
                b.year, b.publisher, fmtSize(b.size)].filter(Boolean).join(" · ");
  let desc = null;
  if (b.description) {
    desc = el("details", {}, el("summary", {}, "Description"), el("p", {}, b.description));
  }
  return el("article", { class: "book" }, cover,
    el("div", { class: "info" },
      el("a", { class: "title", href: b.href }, b.title), authors,
      el("div", { class: "meta" }, meta),
      b.error ? el("div", { class: "err" }, "⚠ " + b.error) : null, desc));
}

const sorters = {
  author: (a, b) => coll.compare(authorLine(a), authorLine(b)) || coll.compare(a.title, b.title),
  title:  (a, b) => coll.compare(a.title, b.title),
  added:  (a, b) => b.added - a.added,
  size:   (a, b) => b.size - a.size,
};

function render() {
  const words = norm($("q").value).split(/\s+/).filter(Boolean);
  const shown = books.filter(b => words.every(w => b._search.includes(w))).sort(sorters[$("sort").value]);
  $("count").textContent = shown.length === books.length
    ? `${books.length} books` : `${shown.length} of ${books.length} books`;
  const list = $("list"); list.replaceChildren();
  if (!shown.length) { list.append(el("div", { class: "empty" }, "Nothing found")); return; }

  if ($("group").getAttribute("aria-pressed") === "true") {
    const groups = new Map();
    for (const b of shown) {
      const k = authorLine(b);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(b);
    }
    const keys = [...groups.keys()];
    if ($("sort").value === "author") keys.sort(coll.compare);
    for (const k of keys) {
      const g = groups.get(k);
      list.append(el("h2", {}, k + " ", el("small", {}, `(${g.length})`)),
                  el("div", { class: "grid" }, ...g.map(card)));
    }
  } else {
    list.append(el("div", { class: "grid" }, ...shown.map(card)));
  }
}

$("q").addEventListener("input", render);
$("sort").addEventListener("change", () => { save(); render(); });
$("group").addEventListener("click", e => {
  const b = e.currentTarget; b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
  save(); render();
});
document.addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); }
  if (e.key === "Escape") { $("q").value = ""; render(); }
});
render();
</script>
</body>
</html>
"""


def build_page(books: list[dict], title: str) -> str:
    # JSON inside <script>: escape "<" so no "</script>" can break out
    data = json.dumps(books, ensure_ascii=False).replace("<", "\\u003c")
    return (PAGE.replace("__TITLE__", html.escape(title))
                .replace("__DATE__", datetime.now().strftime("%Y-%m-%d %H:%M"))
                .replace("__DATA__", data))


# ---------------------------------------------------------------- main

def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Create index.html catalog for EPUB books in a folder.")
    parser.add_argument("folder", nargs="?", default="Out", help="folder with books (default: Out in the current folder)")
    parser.add_argument("--title", help="catalog title (default: Books)")
    args = parser.parse_args()
    return build_index(Path(args.folder), args.title)


def build_index(folder: Path, title: str | None = None) -> int:
    """Create FOLDER/index.html (+ FOLDER/.index/covers). Returns exit code."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        print(f"❌ Folder not found: {root}")
        return 1

    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".epub")
    if not files:
        print(f"📭 No epub files found in {root}")
        return 0
    print(f"📚 Found {len(files)} epub file(s) in {root}")
    if Image is None:
        print("ℹ️  Pillow not installed: covers are copied at full size (pip install pillow)")

    covers_dir = root / INDEX_DIR / COVERS_DIR
    covers_dir.mkdir(parents=True, exist_ok=True)
    used_covers: set[str] = set()

    books, errors, no_cover = [], [], 0
    show_progress = sys.stdout.isatty()
    for n, path in enumerate(files, 1):
        if show_progress:
            print(f"\r\033[K⏳ [{n}/{len(files)}] {path.name}", end="", flush=True)
        try:
            book = read_book(path, root, covers_dir, used_covers)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError) as e:
            # Still list the file, so nothing silently disappears from the catalog
            rel = path.relative_to(root).as_posix()
            book = {"file": rel, "href": "/".join(urllib.parse.quote(p) for p in rel.split("/")),
                    "size": path.stat().st_size, "added": int(path.stat().st_mtime),
                    "title": path.stem, "authors": [], "series": "", "seriesIndex": "",
                    "year": "", "publisher": "", "language": "", "description": "",
                    "cover": "", "error": f"can't read metadata: {e}"}
            errors.append(f"{rel}  —  {e}")
        if not book["cover"]:
            no_cover += 1
        books.append(book)
    if show_progress:
        print("\r\033[K", end="")

    # Remove cover copies of books that are no longer in the folder
    # (only our own files inside .index/covers are touched)
    removed = 0
    for f in covers_dir.iterdir():
        if f.is_file() and f.name not in used_covers:
            f.unlink()
            removed += 1

    out = root / "index.html"
    out.write_text(build_page(books, title or DEFAULT_TITLE), encoding="utf-8")

    if errors:
        print(f"\n❌ Errors ({len(errors)}) — listed in the catalog without metadata:")
        for i, e in enumerate(errors, 1):
            print(f"  [{i}/{len(errors)}] {e}")
    print(f"\n✨ Done:  📖 books {len(books)}  ·  🖼  with cover {len(books) - no_cover}"
          f"  ·  ❌ errors {len(errors)}")
    covers_kb = sum(f.stat().st_size for f in covers_dir.iterdir()) / 1024
    print(f"📄 {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"🖼  {covers_dir}  ({len(used_covers)} covers, {covers_kb:.0f} KB"
          + (f", {removed} old removed" if removed else "") + ")")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
