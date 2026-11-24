#!/usr/bin/env python3
"""Build index.html — a browsable catalog of all EPUB books in a folder.

Usage:
    python3 make_index.py [FOLDER]        (default: ./Out)

The folder is scanned recursively. The catalog shows covers, authors, titles,
descriptions, has search, sorting and grouping by author; clicking a title
opens the book in the built-in reader (epub.js, offline). Works offline; no internet or server needed.

Written files:  FOLDER/index.html, small cover copies in  FOLDER/.index/covers/
the reader in  FOLDER/.index/reader/  and the "Open Library" launchers in  FOLDER/
(they open the catalog through a tiny local web server, so books open with a click).
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
import shutil
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
READER_DIR = "reader"     # reader page + epub.js, copied from next to this script
READER_SRC = Path(__file__).resolve().parent / "reader"
LAUNCHERS_SRC = Path(__file__).resolve().parent / "launchers"   # "Open Library" .bat / .sh
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


IMG_TAG = re.compile(r"""<(?:\w+:)?(?:img|image)\b[^>]*?\s(?:src|(?:xlink:)?href)\s*=\s*["']([^"']+)["']""", re.I)


def zip_path(base_dir: str, href: str) -> str:
    """Path inside the zip of an href written in a file that lives in base_dir."""
    href = urllib.parse.unquote(href.split("#")[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)


def first_image_in_page(zf: zipfile.ZipFile, page_path: str) -> str | None:
    """Zip path of the first picture shown on an (X)HTML page that exists in the zip."""
    try:
        text = zf.read(page_path).decode("utf-8", "replace")
    except KeyError:
        return None
    names = set(zf.namelist())
    for m in IMG_TAG.finditer(text):
        path = zip_path(posixpath.dirname(page_path), m.group(1))
        if path in names:
            return path
    return None


def locate_cover(zf: zipfile.ZipFile, opf, opf_dir: str) -> tuple[str | None, str]:
    """Find the cover picture the way readers do. Returns (zip path, how it was found).

    "declared" means the book itself marks it as the cover (EPUB 3 cover-image or
    EPUB 2 <meta name="cover">); other ways are guesses that work for books whose
    cover is not marked: the guide's cover page, a file named "cover", or the
    picture on the first page. Marks pointing to files missing from the zip are skipped.
    """
    names = set(zf.namelist())
    manifest = opf.find("opf:manifest", NS)
    items = manifest.findall("opf:item", NS) if manifest is not None else []
    by_id = {i.get("id"): i for i in items}
    is_image = lambda i: i.get("media-type", "").startswith("image/")
    is_page = lambda i: "html" in i.get("media-type", "")

    def item_image(item):
        """The picture an item stands for: the image itself, or the first one on a page."""
        if item is None:
            return None
        path = zip_path(opf_dir, item.get("href", ""))
        if is_image(item):
            return path if path in names else None
        return first_image_in_page(zf, path) if is_page(item) else None

    # EPUB 3: <item properties="cover-image">
    for i in items:
        if "cover-image" in i.get("properties", "").split() and (path := item_image(i)):
            return path, "declared"
    # EPUB 2: <meta name="cover" content="item-id">
    for m in opf.iter("{%s}meta" % NS["opf"]):
        if m.get("name") == "cover" and (path := item_image(by_id.get(m.get("content")))):
            return path, "declared"
    # EPUB 2 guide: <reference type="cover" href="cover.xhtml or cover.jpg">
    for r in opf.iter("{%s}reference" % NS["opf"]):
        if r.get("type", "").lower() in ("cover", "coverimage"):
            path = zip_path(opf_dir, r.get("href", ""))
            if path in names:
                found = path if re.search(r"\.(jpe?g|png|gif|webp)$", path, re.I) else first_image_in_page(zf, path)
                if found:
                    return found, "guide"
    # An image whose id or file name says "cover"
    for i in items:
        if is_image(i) and "cover" in (i.get("id", "") + i.get("href", "")).lower() and (path := item_image(i)):
            return path, "named"
    # The picture on the first page of the book (converted books often have only this)
    spine = opf.find("opf:spine", NS)
    if spine is not None:
        for ref in spine.findall("opf:itemref", NS):
            if ref.get("linear", "yes") != "no":
                if path := item_image(by_id.get(ref.get("idref"))):
                    return path, "first page"
                break
    return None, ""


def find_cover(zf: zipfile.ZipFile, opf, opf_dir: str) -> bytes | None:
    """Bytes of the cover picture, or None if the book has none."""
    path, _ = locate_cover(zf, opf, opf_dir)
    return zf.read(path) if path else None


def save_cover(raw: bytes | None, covers_dir: Path, used: set[str]) -> str:
    """Save a small copy of the cover into covers_dir; return its file name ("" if none).

    The name is a hash of the original image, so books sharing a cover
    share one copy.
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
                if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
                    # Transparent PNG (some covers are dark text on a transparent page):
                    # put it on white, a plain convert would show garbage in the clear parts
                    rgba = img.convert("RGBA")
                    img = Image.new("RGB", rgba.size, "white")
                    img.paste(rgba, mask=rgba.getchannel("A"))
                elif img.mode not in ("RGB", "L"):
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
    --bg: #f7f7f5; --surface: #ffffff; --surface-2: #efeeeb; --text: #18181b; --muted: #6b6b74;
    --line: rgba(0,0,0,.09); --accent: #3563e9; --accent-soft: rgba(53,99,233,.1); --ring: rgba(53,99,233,.35);
    --header: rgba(247,247,245,.78); --danger: #d93636;
    --shadow-cover: 0 1px 2px rgba(0,0,0,.08), 0 8px 18px -8px rgba(0,0,0,.28);
    --shadow-hover: 0 2px 4px rgba(0,0,0,.08), 0 18px 34px -12px rgba(0,0,0,.4);
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #111113; --surface: #1b1b1e; --surface-2: #26262a; --text: #f2f2f4; --muted: #a0a0a8;
            --line: rgba(255,255,255,.09); --accent: #7ea2ff; --accent-soft: rgba(126,162,255,.15);
            --ring: rgba(126,162,255,.4); --header: rgba(17,17,19,.75); --danger: #ff6b6b;
            --shadow-cover: 0 1px 2px rgba(0,0,0,.5), 0 8px 18px -8px rgba(0,0,0,.8);
            --shadow-hover: 0 2px 4px rgba(0,0,0,.5), 0 18px 34px -12px rgba(0,0,0,.9);
            color-scheme: dark; }
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased; }
  .ic { width: 18px; height: 18px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8;
        stroke-linecap: round; stroke-linejoin: round; }
  :focus-visible { outline: 3px solid var(--ring); outline-offset: 2px; }

  /* Header: translucent, stays on top */
  header { position: sticky; top: 0; z-index: 5; background: var(--header);
           -webkit-backdrop-filter: saturate(1.6) blur(16px); backdrop-filter: saturate(1.6) blur(16px);
           border-bottom: 1px solid var(--line); padding: 12px 28px; }
  .bar { max-width: 1400px; margin: 0 auto; display: flex; gap: 10px 14px; flex-wrap: wrap; align-items: center; }
  .brand { display: flex; align-items: center; gap: 10px; min-width: 0; margin-right: 6px; }
  .logo { width: 34px; height: 34px; padding: 7px; border-radius: 10px; color: #fff; stroke-width: 2;
          background: linear-gradient(135deg, #5b8cff, #3563e9); box-shadow: 0 4px 12px -4px rgba(53,99,233,.6); }
  h1 { font-size: 19px; font-weight: 700; letter-spacing: -.01em; margin: 0; white-space: nowrap;
       overflow: hidden; text-overflow: ellipsis; }
  .badge { font-size: 12px; font-weight: 600; color: var(--muted); background: var(--surface-2);
           padding: 2px 8px; border-radius: 999px; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .search { flex: 1 1 280px; position: relative; display: flex; align-items: center; min-width: 200px; }
  .search .ic { position: absolute; left: 12px; color: var(--muted); pointer-events: none; }
  .search input { width: 100%; height: 40px; padding: 0 40px 0 38px; font: inherit; color: var(--text);
                  background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
                  transition: border-color .15s, box-shadow .15s; }
  .search input::placeholder { color: var(--muted); opacity: .8; }
  .search input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 4px var(--accent-soft); }
  .search kbd { position: absolute; right: 10px; font: 12px/1 ui-monospace, monospace; color: var(--muted);
                border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 6px; padding: 3px 6px;
                pointer-events: none; transition: opacity .15s; }
  .search:focus-within kbd, .search.filled kbd { opacity: 0; }
  .tools { display: flex; gap: 8px; align-items: center; }
  .chip, .select select { height: 40px; display: inline-flex; align-items: center; gap: 7px; padding: 0 14px;
          font: inherit; font-size: 14px; font-weight: 500; color: var(--text); background: var(--surface);
          border: 1px solid var(--line); border-radius: 12px; cursor: pointer; text-decoration: none;
          white-space: nowrap; transition: background .15s, color .15s, border-color .15s; }
  .chip:hover, .select select:hover { background: var(--surface-2); }
  .chip[aria-pressed=true] { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
  .select { position: relative; display: flex; align-items: center; }
  .select .ic { position: absolute; left: 12px; color: var(--muted); pointer-events: none; }
  .select select { -webkit-appearance: none; appearance: none; padding: 0 34px 0 36px;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238a8a93' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
          background-repeat: no-repeat; background-position: right 10px center; background-size: 16px; }

  main { max-width: 1400px; margin: 0 auto; padding: 28px 28px 64px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 34px 26px; }

  /* Group by author: heading with a count and a rule */
  h2 { display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 700; letter-spacing: -.01em;
       margin: 40px 0 18px; }
  h2:first-child { margin-top: 4px; }
  h2::after { content: ""; flex: 1; height: 1px; background: var(--line); }

  /* A book: cover on a shelf, text below */
  .book { display: flex; flex-direction: column; min-width: 0; }
  .cover { position: relative; display: block; aspect-ratio: 2 / 3; border-radius: 6px 10px 10px 6px;
           overflow: hidden; background: var(--surface-2); box-shadow: var(--shadow-cover);
           color: #fff; text-decoration: none; transition: transform .2s ease, box-shadow .2s ease; }
  .cover::before { content: ""; position: absolute; inset: 0; z-index: 1; pointer-events: none;   /* spine */
           background: linear-gradient(90deg, rgba(0,0,0,.22) 0, rgba(255,255,255,.18) 2.5%,
                       rgba(0,0,0,.08) 4.5%, transparent 9%); }
  .cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .book:hover .cover, .cover:focus-visible { transform: translateY(-4px); box-shadow: var(--shadow-hover); }
  .read { position: absolute; z-index: 2; left: 50%; bottom: 12px; transform: translate(-50%, 6px);
          display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius: 999px;
          font-size: 13px; font-weight: 600; color: #fff; background: rgba(20,20,24,.72);
          -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); opacity: 0;
          transition: opacity .18s, transform .18s; white-space: nowrap; }
  .read .ic { width: 16px; height: 16px; }
  .book:hover .read, .cover:focus-visible .read { opacity: 1; transform: translate(-50%, 0); }
  @media (hover: none) and (pointer: coarse) { .read { display: none; } }   /* touch screens: tap opens anyway */
  .ph { width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center;
        padding: 16px 14px 16px 20px; font-weight: 700; text-align: center; gap: 10px; line-height: 1.25;
        text-shadow: 0 1px 2px rgba(0,0,0,.25); }
  .ph .a { font-weight: 500; font-size: 13px; opacity: .85; }
  .info { padding: 12px 2px 0; display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
  .title { font-weight: 650; font-size: 15px; line-height: 1.3; color: var(--text); text-decoration: none;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .title:hover { color: var(--accent); }
  .authors { color: var(--muted); font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .authors a { color: inherit; text-decoration: none; cursor: pointer; }
  .authors a:hover { color: var(--accent); }
  .meta { color: var(--muted); font-size: 12px; opacity: .85; }
  .actions { display: flex; align-items: center; gap: 2px; margin: 6px 0 0 -8px; }
  .act { display: inline-flex; align-items: center; gap: 5px; height: 30px; padding: 0 8px; border: 0;
         border-radius: 8px; background: none; font: inherit; font-size: 13px; font-weight: 500;
         color: var(--muted); cursor: pointer; text-decoration: none; }
  .act .ic { width: 16px; height: 16px; }
  .act:hover { background: var(--surface-2); color: var(--text); }
  .act[aria-expanded=true] { color: var(--accent); }
  .act[aria-expanded=true] .ic { transform: rotate(180deg); }
  .act.save { margin-left: auto; }
  .desc { margin: 4px 0 0; font-size: 13px; color: var(--muted); white-space: pre-line; }
  .err { color: var(--danger); font-size: 12px; }

  .note { max-width: 1400px; margin: 16px auto 0; padding: 12px 16px; border-radius: 12px; display: flex;
          gap: 10px; align-items: flex-start; border: 1px solid var(--line); background: var(--accent-soft);
          color: var(--text); font-size: 14px; }
  .note .ic { color: var(--accent); margin-top: 1px; }
  .note code { background: var(--surface); padding: 1px 6px; border-radius: 6px; font-size: 13px; }
  @media (max-width: 1456px) { .note { margin: 16px 28px 0; } }
  .empty { color: var(--muted); text-align: center; padding: 80px 0; display: flex; flex-direction: column;
           align-items: center; gap: 12px; }
  .empty .ic { width: 40px; height: 40px; stroke-width: 1.5; opacity: .6; }
  footer { color: var(--muted); font-size: 12px; text-align: center; padding: 0 20px 32px; opacity: .8; }

  @media (max-width: 640px) {
    header { padding: 10px 16px; }
    main { padding: 20px 16px 48px; }
    .note { margin: 12px 16px 0; }
    .search { order: 3; flex-basis: 100%; }
    .brand { flex: 1; }
    .chip span { display: none; }
    .chip { padding: 0 11px; }
    /* sort becomes an icon button; the phone's own picker still lists the options */
    .select select { width: 40px; padding: 0; color: transparent; background-image: none; }
    .select select option { color: var(--text); }
    .select .ic { left: 11px; color: var(--text); }
    .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 26px 16px; }
  }
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-library" viewBox="0 0 24 24"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
  <symbol id="i-sort" viewBox="0 0 24 24"><path d="m3 16 4 4 4-4M7 20V4M21 8l-4-4-4 4M17 4v16"/></symbol>
  <symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></symbol>
  <symbol id="i-read" viewBox="0 0 24 24"><path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></symbol>
  <symbol id="i-chevron" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>
  <symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></symbol>
</svg>
<header><div class="bar">
  <div class="brand">
    <svg class="ic logo" aria-hidden="true"><use href="#i-library"/></svg>
    <h1>__TITLE__</h1>
    <span id="count" class="badge"></span>
  </div>
  <label class="search" id="searchBox">
    <svg class="ic" aria-hidden="true"><use href="#i-search"/></svg>
    <input id="q" type="search" placeholder="Search by title, author, description…" aria-label="Search books" autofocus>
    <kbd>/</kbd>
  </label>
  <div class="tools">
    <div class="select" title="Sort">
      <svg class="ic" aria-hidden="true"><use href="#i-sort"/></svg>
      <select id="sort" aria-label="Sort">
        <option value="author">Author</option>
        <option value="title">Title</option>
        <option value="added">Recently added</option>
        <option value="size">File size</option>
      </select>
    </div>
    <button id="group" class="chip" aria-pressed="false" title="Group books by author">
      <svg class="ic" aria-hidden="true"><use href="#i-user"/></svg><span>Group by author</span></button>
    <a id="openFile" class="chip" href="__READER__" title="Open any EPUB file in the reader">
      <svg class="ic" aria-hidden="true"><use href="#i-folder"/></svg><span>Open file</span></a>
  </div>
</div></header>
<div id="fileNote" class="note" hidden><svg class="ic" aria-hidden="true"><use href="#i-info"/></svg>
  <div>Opened directly from disk: books ask you to choose the file. For one-click reading open
  the catalog with <b>Open Library</b> (<code>Open Library.bat</code> on Windows,
  <code>Open Library.sh</code> on Linux) in this folder.</div></div>
<main id="list"></main>
<footer>Generated __DATE__ · click a cover or a title to read the book</footer>

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

const NS = "http://www.w3.org/2000/svg";
function icon(name) {
  const svg = document.createElementNS(NS, "svg"), use = document.createElementNS(NS, "use");
  svg.setAttribute("class", "ic");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

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

// Reader lives in .index/reader/, so book paths are given relative to it
const readerUrl = b => "__READER__#book=" + encodeURIComponent("../../" + b.href);

// "Save as": Chrome/Edge show a save dialog to choose the folder; other browsers
// (and pages opened from disk) fall back to a normal download of the link.
async function saveAs(e, href, fileName) {
  if (!window.showSaveFilePicker || location.protocol === "file:") return;
  e.preventDefault();
  let handle;
  try {
    handle = await showSaveFilePicker({ suggestedName: fileName,
      types: [{ description: "EPUB book", accept: { "application/epub+zip": [".epub"] } }] });
  } catch (err) {
    // Dialog not available here: plain download instead
    if (err.name !== "AbortError") Object.assign(document.createElement("a"), { href, download: fileName }).click();
    return;
  }
  try {
    const r = await fetch(href);
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    const w = await handle.createWritable();
    await r.body.pipeTo(w);
  } catch (err) { alert("Can't save " + fileName + ": " + err.message); }
}

function card(b) {
  // Cover opens the book in the reader, like the title
  const cover = el("a", { class: "cover", href: readerUrl(b), title: "Read in the browser" },
                   b.cover ? el("img", { src: b.cover, alt: "", loading: "lazy" }) : placeholder(b),
                   el("span", { class: "read" }, icon("read"), "Read"));
  const authors = el("div", { class: "authors" });
  (b.authors.length ? b.authors : ["Unknown Author"]).forEach((a, i) => {
    if (i) authors.append(", ");
    authors.append(el("a", { title: "Show all books by this author",
                             on: () => { $("q").value = a; render(); } }, a));
  });
  const meta = [b.series && `${b.series}${b.seriesIndex ? " #" + b.seriesIndex : ""}`,
                b.year, b.publisher, fmtSize(b.size)].filter(Boolean).join(" · ");
  const actions = el("div", { class: "actions" });
  let desc = null;
  if (b.description) {
    desc = el("p", { class: "desc", hidden: "" }, b.description);
    const about = el("button", { class: "act", type: "button", "aria-expanded": "false" }, "About", icon("chevron"));
    about.addEventListener("click", () => {
      desc.hidden = !desc.hidden;
      about.setAttribute("aria-expanded", String(!desc.hidden));
    });
    actions.append(about);
  }
  const fileName = b.file.split("/").pop();
  const save = el("a", { class: "act save", href: b.href, download: fileName, "aria-label": "Save as…",
                         title: "Save the EPUB file: " + fileName }, icon("download"));
  save.addEventListener("click", e => saveAs(e, b.href, fileName));
  actions.append(save);
  return el("article", { class: "book" }, cover,
    el("div", { class: "info" },
      el("a", { class: "title", href: readerUrl(b), title: b.title }, b.title), authors,
      el("div", { class: "meta" }, meta),
      b.error ? el("div", { class: "err" }, "⚠ " + b.error) : null, actions, desc));
}

const sorters = {
  author: (a, b) => coll.compare(authorLine(a), authorLine(b)) || coll.compare(a.title, b.title),
  title:  (a, b) => coll.compare(a.title, b.title),
  added:  (a, b) => b.added - a.added,
  size:   (a, b) => b.size - a.size,
};

function render() {
  $("searchBox").classList.toggle("filled", !!$("q").value);   // hides the "/" hint
  const words = norm($("q").value).split(/\s+/).filter(Boolean);
  const shown = books.filter(b => words.every(w => b._search.includes(w))).sort(sorters[$("sort").value]);
  $("count").textContent = shown.length === books.length
    ? `${books.length} books` : `${shown.length} of ${books.length} books`;
  const list = $("list"); list.replaceChildren();
  if (!shown.length) { list.append(el("div", { class: "empty" }, icon("search"), "Nothing found")); return; }

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
      list.append(el("h2", {}, k, el("span", { class: "badge" }, g.length)),
                  el("div", { class: "grid" }, ...g.map(card)));
    }
  } else {
    list.append(el("div", { class: "grid" }, ...shown.map(card)));
  }
}

if (location.protocol === "file:") $("fileNote").hidden = false;
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
                .replace("__READER__", f"{INDEX_DIR}/{READER_DIR}/reader.html")
                .replace("__DATA__", data))


# ---------------------------------------------------------------- main

def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Create index.html catalog for EPUB books in a folder.")
    parser.add_argument("folder", nargs="?", default="Out", help="folder with books (default: Out in the current folder)")
    parser.add_argument("--title", help="catalog title (default: Books)")
    args = parser.parse_args()
    return build_index(Path(args.folder), args.title)


def install_reader(dest: Path) -> None:
    """Copy the reader (reader.html + epub.js + JSZip) next to the catalog."""
    if not READER_SRC.is_dir():
        print(f"⚠️  Reader not found in {READER_SRC}: books will open as plain files")
        return
    dest.mkdir(parents=True, exist_ok=True)
    names = {f.name for f in READER_SRC.iterdir() if f.is_file()}
    for name in names:
        shutil.copy2(READER_SRC / name, dest / name)
    for f in dest.iterdir():
        if f.is_file() and f.name not in names:
            f.unlink()


def install_launchers(root: Path) -> None:
    """Put "Open Library.bat" (Windows) and "Open Library.sh" (Linux) next to index.html."""
    if not LAUNCHERS_SRC.is_dir():
        return
    for f in LAUNCHERS_SRC.iterdir():
        if f.is_file():
            shutil.copy2(f, root / f.name)   # copy2 keeps the executable bit of .sh


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

    # Start from a clean .index so no leftovers from earlier runs survive
    index_dir = root / INDEX_DIR
    if index_dir.is_symlink() or index_dir.is_file():
        index_dir.unlink()
    elif index_dir.exists():
        shutil.rmtree(index_dir)
    covers_dir = index_dir / COVERS_DIR
    covers_dir.mkdir(parents=True)
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

    install_reader(root / INDEX_DIR / READER_DIR)
    install_launchers(root)

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
    print(f"🖼  {covers_dir}  ({len(used_covers)} covers, {covers_kb:.0f} KB)")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
