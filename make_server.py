#!/usr/bin/env python3
"""Build index.html — a browsable catalog of all EPUB books in a folder.

Usage:
    python3 make_server.py [FOLDER]        (default: ./Out)

The folder is scanned recursively. The catalog shows covers, authors, titles,
descriptions, has search, sorting and grouping by author; clicking a title
opens the book in the built-in reader (epub.js, offline). Works offline; no internet or server needed.

Written files:  FOLDER/index.html, small cover copies in  FOLDER/.index/covers/
the reader in  FOLDER/.index/reader/, the "Open Library" launchers (with library_sync.py and
library-sync.ps1, the account/sync store) and sw.js (offline app) in  FOLDER/
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
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # Pillow is optional
    Image = None

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
THUMB_SIZE = (600, 900)   # max cover size in the catalog, px (sharp on the book page and HiDPI screens)
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


RASTER = re.compile(r"\.(jpe?g|png|gif|webp|bmp)$", re.I)
SAME_PICTURE = 12   # mean difference (0-255) of 16x24 grey copies below which two pictures are the same


def sharper_copy(zf: zipfile.ZipFile, path: str, raw: bytes) -> bytes:
    """Many books mark a small cover (e.g. 126x200) and show the same picture in full size on the
    first page. If the book has a bigger copy of the cover, return that one instead."""
    if Image is None:
        return raw
    try:
        small = Image.open(io.BytesIO(raw))
        w, h = small.size
        if h >= THUMB_SIZE[1] or w >= THUMB_SIZE[0]:
            return raw   # already sharp enough for the catalog
        ratio, sig = w / h, None
        best, best_area = raw, w * h
        for info in zf.infolist():
            if info.filename == path or not RASTER.search(info.filename) or info.file_size > 30_000_000:
                continue
            try:
                data = zf.read(info.filename)
                img = Image.open(io.BytesIO(data))
                cw, ch = img.size   # only the header is read so far
                if cw * ch <= best_area or abs(cw / ch - ratio) > ratio * .06:
                    continue
                if sig is None:
                    sig = small.convert("L").resize((16, 24))
                diff = ImageChops.difference(sig, img.convert("L").resize((16, 24)))
                if ImageStat.Stat(diff).mean[0] < SAME_PICTURE:
                    best, best_area = data, cw * ch
            except Exception:
                continue
        return best
    except Exception:
        return raw


def find_cover(zf: zipfile.ZipFile, opf, opf_dir: str) -> bytes | None:
    """Bytes of the cover picture (its biggest copy in the book), or None if the book has none."""
    path, _ = locate_cover(zf, opf, opf_dir)
    return sharper_copy(zf, path, zf.read(path)) if path else None


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
        "file": rel, "id": "",
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
    # the reader's book id (reader.html openBook): first identifier, else first title, raw, else file name
    def raw(el):
        return (el.text or "") if el is not None else ""
    book["id"] = raw(md.find(".//dc:identifier", NS)) or raw(md.find(".//dc:title", NS)) or path.name
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
<link rel="icon" href=".index/reader/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href=".index/reader/icon.png">
<link rel="manifest" href=".index/manifest.webmanifest">
<meta name="theme-color" content="#f7f7f5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111113" media="(prefers-color-scheme: dark)">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
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
  .brand { position: relative; display: flex; align-items: center; gap: 10px; min-width: 0; margin-right: 6px; }
  .brand .names { display: grid; min-width: 0; }
  /* With sync: the whole logo + name block is the account button (a button laid over it, so the
     h1 stays a heading); a green check on the logo while signed in (grey while the library
     computer doesn't answer), the email and the last sync in its tooltip */
  #acctBrand { position: absolute; inset: -4px -6px; z-index: 1; padding: 0; border: 0; border-radius: 12px;
               background: none; cursor: pointer; }
  #acctBrand:hover, #acctBrand[aria-expanded=true] { background: var(--accent-soft); }
  .logoBox { position: relative; display: flex; flex: none; }
  #acctMark { position: absolute; right: -5px; bottom: -5px; width: 17px; height: 17px; padding: 2.5px;
              border-radius: 50%; background: #22a55a; color: #fff; stroke-width: 3.5;
              box-shadow: 0 0 0 2px var(--bg); pointer-events: none; }
  #acctMark.wait { background: #9aa1ad; }
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
  /* Folders: one row of chips under the bar, scrolled sideways (never wrapped); hidden while there are none */
  .folders { max-width: 1400px; margin: 10px auto 0; display: flex; gap: 8px; overflow-x: auto;
             overscroll-behavior-x: contain; scrollbar-width: none; }
  .folders::-webkit-scrollbar { display: none; }
  .fchip { flex: none; height: 34px; padding: 0 12px; gap: 6px; }
  .fchip .count { font-weight: 600; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .fchip[aria-pressed=true] .count { color: inherit; opacity: .75; }
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
  .shelf { position: relative; transition: transform .2s ease; }
  /* Lifted under the mouse or when reached with the keyboard; not when focus just comes back
     to it after a book's page is closed by a tap or click (that would look like an animation) */
  .book:hover .shelf, .shelf:has(:focus-visible) { transform: translateY(-4px); }
  .book:hover .cover, .shelf:has(:focus-visible) .cover { box-shadow: var(--shadow-hover); }
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
  .actions { display: flex; align-items: center; gap: 4px; margin: auto 0 0; padding-top: 8px; }   /* bottom of the card: Read buttons line up */
  .act { display: inline-flex; align-items: center; gap: 5px; height: 30px; padding: 0 8px; border: 0;
         border-radius: 8px; background: none; font: inherit; font-size: 13px; font-weight: 500;
         color: var(--muted); cursor: pointer; text-decoration: none; }
  .act .ic { width: 16px; height: 16px; }
  .act:hover { background: var(--surface-2); color: var(--text); }
  .act.save { margin-left: auto; }
  /* Read: always visible under the cover, so a tap on the phone goes straight into the book */
  .act.go { padding: 0 12px 0 10px; color: var(--accent); background: var(--accent-soft); font-weight: 600; }
  .act.go:hover { color: #fff; background: var(--accent); }
  @media (max-width: 640px) {
    .act { height: 34px; }
    .act.go { flex: 1; justify-content: center; }
  }
  .err { color: var(--danger); font-size: 12px; }

  /* Book page: the cover flies from the shelf to the left, the description slides in on the right */
  .detail { position: fixed; inset: 0; z-index: 20; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; }
  .dbg { position: fixed; inset: 0; background: var(--bg); }
  .dwrap { position: relative; max-width: 1240px; margin: 0 auto; min-height: 100%; display: grid;
           grid-template-columns: clamp(200px, 38%, 460px) 1fr; gap: clamp(28px, 5vw, 64px); align-items: start;
           padding: 88px clamp(20px, 4vw, 40px) 56px; }
  .dcover { position: sticky; top: 88px; display: block; aspect-ratio: 2 / 3; justify-self: end;
            width: min(100%, calc((100dvh - 136px) * 2 / 3));   /* whole cover fits in the window */ border-radius: 6px 12px 12px 6px;
            overflow: hidden; background: var(--surface-2); color: #fff; text-decoration: none;
            box-shadow: var(--shadow-hover); }
  .dcover img, .ghost img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .dcover .ph { font-size: 20px; }
  .dcover::before, .ghost::before { content: ""; position: absolute; inset: 0; z-index: 1; pointer-events: none;
            background: linear-gradient(90deg, rgba(0,0,0,.22) 0, rgba(255,255,255,.18) 2.5%,
                        rgba(0,0,0,.08) 4.5%, transparent 9%); }
  .ghost { position: fixed; z-index: 30; margin: 0; overflow: hidden; pointer-events: none; color: #fff;
           border-radius: 6px 12px 12px 6px; background: var(--surface-2); box-shadow: var(--shadow-hover); }
  .dbody { min-width: 0; padding-top: 8px; }
  .dbody h3 { margin: 0 0 8px; font-size: 32px; line-height: 1.15; letter-spacing: -.02em; font-weight: 750; }
  /* The title is a button that copies it: it looks like the heading it is */
  .dtitle { all: unset; cursor: pointer; border-radius: 6px; overflow-wrap: anywhere; }
  .dtitle:hover { text-decoration: underline dotted; text-decoration-thickness: 2px; text-underline-offset: 5px; }
  .dtitle:focus-visible { outline: 2px solid currentColor; outline-offset: 3px; }
  .dbody .authors { font-size: 17px; white-space: normal; }
  .dbody .meta { margin-top: 6px; font-size: 13px; }
  .dact .short { display: none; }
  @media (max-width: 640px) { .dact .short { display: inline; } }
  .dact { display: flex; gap: 10px; align-items: center; margin: 24px 0 0; flex-wrap: wrap; }
  .dtext.none { font-style: italic; color: var(--muted); }
  .dtext { margin: 28px 0 0; padding-top: 24px; border-top: 1px solid var(--line); max-width: 68ch;
           white-space: pre-line; font-size: 16px; line-height: 1.65; color: var(--text); opacity: .9; }
  .btn { height: 46px; display: inline-flex; align-items: center; gap: 8px; padding: 0 24px; border-radius: 12px;
         font: inherit; font-weight: 600; text-decoration: none; cursor: pointer; border: 1px solid var(--line);
         background: var(--surface); color: var(--text); transition: background .15s, transform .15s, box-shadow .15s; }
  .btn:hover { background: var(--surface-2); }
  .btn:disabled { opacity: .65; cursor: default; background: var(--surface); }
  .btn.primary { border: 0; color: #fff; background: linear-gradient(135deg, #5b8cff, #3563e9);
         box-shadow: 0 8px 20px -8px rgba(53,99,233,.7); }
  .btn.primary:hover { transform: translateY(-1px); box-shadow: 0 12px 24px -8px rgba(53,99,233,.8); }
  .dback { position: fixed; top: 20px; left: 24px; z-index: 2; height: 40px; display: inline-flex; align-items: center;
           gap: 6px; padding: 0 14px 0 10px; border: 1px solid var(--line); border-radius: 12px; cursor: pointer;
           font: inherit; font-size: 14px; font-weight: 500; color: var(--text); background: var(--header);
           -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px); }
  .dback:hover { background: var(--surface-2); }
  body.detail-open { overflow: hidden; }
  @media (max-width: 640px) {
    .dwrap { grid-template-columns: 1fr; gap: 24px; padding: 72px 16px 96px; }
    .dcover { position: relative; top: 0; width: min(240px, 62vw); justify-self: center; }
    .dbody { padding-top: 0; }
    .dbody h3 { font-size: 24px; }
    .dback { top: 14px; left: 16px; }
    .dact { position: fixed; left: 0; right: 0; bottom: 0; margin: 0; padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
            background: var(--surface); border-top: 1px solid var(--line); flex-wrap: nowrap; z-index: 2; }
    .dact .btn.primary { flex: 1; justify-content: center; }
    .dact .btn { padding: 0 18px; }
    .dact .long { display: none; }
    .dtext { margin-top: 0; border-top: 0; padding-top: 0; }
  }

  .note { max-width: 1400px; margin: 16px auto 0; padding: 12px 16px; border-radius: 12px; display: flex;
          gap: 10px; align-items: flex-start; border: 1px solid var(--line); background: var(--accent-soft);
          color: var(--text); font-size: 14px; }
  .note .ic { color: var(--accent); margin-top: 1px; }
  .note code { background: var(--surface); padding: 1px 6px; border-radius: 6px; font-size: 13px; }
  @media (max-width: 1456px) { .note { margin: 16px 28px 0; } }
  .empty { color: var(--muted); text-align: center; padding: 80px 0; display: flex; flex-direction: column;
           align-items: center; gap: 12px; }
  .empty .ic { width: 40px; height: 40px; stroke-width: 1.5; opacity: .6; }
  /* Installed app: books kept on the device */
  .pop { position: fixed; z-index: 15; top: 64px; right: max(16px, calc((100vw - 1400px) / 2 + 28px)); width: min(360px, calc(100vw - 32px));
         padding: 16px; display: flex; flex-direction: column; gap: 12px; border-radius: 14px; font-size: 14px;
         background: var(--surface); border: 1px solid var(--line); box-shadow: 0 18px 40px -12px rgba(0,0,0,.35); }
  .pophead { font-weight: 700; font-size: 15px; }
  .popstat { color: var(--muted); font-variant-numeric: tabular-nums; }
  .popbar { height: 6px; border-radius: 3px; background: var(--surface-2); overflow: hidden; }
  .popbar div { height: 100%; width: 0; background: var(--accent); transition: width .3s; }
  .popseg { display: flex; flex-direction: column; gap: 6px; }
  .popseg label { display: flex; gap: 8px; align-items: center; cursor: pointer; }
  .popinfo { color: var(--muted); font-size: 13px; }
  .popinfo:empty { display: none; }
  .pop .btn { height: 40px; justify-content: center; font-size: 14px; }
  .book.away .shelf, .book.away .info { opacity: .4; }
  #offBtn .count { font-weight: 600; font-size: 12px; padding: 1px 7px; border-radius: 999px; color: var(--muted);
                   background: var(--surface-2); font-variant-numeric: tabular-nums; }
  #offBtn.all .count { color: #fff; background: #22a35a; }
  #offBtn.busy { color: var(--accent); border-color: var(--accent); }
  #offBtn.busy .count { color: #fff; background: var(--accent); }
  /* On the cover: is the book on this device (check) or only in the library (cloud) */
  .where { position: absolute; z-index: 2; right: 7px; bottom: 7px; width: 28px; height: 28px; border-radius: 50%;
           display: grid; place-items: center; padding: 0; border: 0; cursor: pointer; color: #fff;
           background: rgba(20,20,24,.55); -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
           box-shadow: 0 0 0 1.5px rgba(255,255,255,.75), 0 2px 6px rgba(0,0,0,.35); }
  .where .ic { width: 17px; height: 17px; stroke-width: 2.1; }
  .where::after { content: ""; position: absolute; inset: -10px; }   /* easier to hit with a finger */
  .where:hover { transform: scale(1.1); }
  .where .ic:last-child { display: none; }
  .book.here .where { background: #22a35a; }
  .book.here .where .ic:first-child { display: none; }
  .book.here .where .ic:last-child { display: block; }
  .book.busy .where { animation: busy 1s ease-in-out infinite alternate; }
  @keyframes busy { to { background: var(--accent); transform: scale(.85); } }
  .toast { position: fixed; z-index: 40; left: 50%; bottom: calc(24px + env(safe-area-inset-bottom)); transform: translateX(-50%);
           max-width: calc(100vw - 32px); padding: 12px 18px; border-radius: 12px; font-size: 14px;
           background: var(--text); color: var(--bg); box-shadow: 0 12px 30px -10px rgba(0,0,0,.5); }
  footer { color: var(--muted); font-size: 12px; text-align: center; padding: 0 20px 32px; opacity: .8; }

  /* Account (sync between devices): same panel as in the reader */
  #acctPop { gap: 8px; right: auto; left: max(16px, calc((100vw - 1400px) / 2 + 28px)); }   /* under the logo */
  #acctPop .tabs { display: flex; gap: 2px; padding: 2px; border-radius: 10px; background: var(--surface-2); }
  #acctPop .tabs button { flex: 1; height: 32px; padding: 0 8px; border: 0; border-radius: 8px; font: inherit; font-size: 14px;
                          color: var(--muted); background: none; cursor: pointer; }
  #acctPop .tabs button.on { background: var(--surface); color: var(--text); font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,.15); }
  #acctPop [data-pane] { display: none; }
  #acctPop [data-pane].on, #acctOut { display: grid; gap: 8px; margin-top: 10px; }
  #acctPop input { height: 36px; min-width: 0; font: inherit; color: var(--text); background: var(--bg);
                   border: 1px solid var(--line); border-radius: 10px; padding: 0 12px; }
  #acctPop input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  #acctPop .go { height: 36px; border: 0; border-radius: 10px; font: inherit; font-weight: 600; color: #fff;
                 background: var(--accent); cursor: pointer; }
  #acctPop .row { display: flex; gap: 8px; }
  #acctPop .row button { flex: 1; height: 36px; border: 0; border-radius: 10px; font: inherit; color: var(--text);
                         background: var(--surface-2); cursor: pointer; }
  #acctPop .row button.go { color: #fff; background: var(--accent); }
  #acctPop p { margin: 0 2px; font-size: 12px; color: var(--muted); line-height: 1.35; }
  #acctErr { font-size: 13px !important; color: var(--danger) !important; }
  #acctErr:empty { display: none; }
  @media (max-width: 640px) { #acctPop input { font-size: 16px; } }   /* iPhone zooms in on smaller text fields */

  @media (max-width: 640px) {
    header { padding: 10px 16px; }
    main { padding: 20px 16px 48px; }
    .note { margin: 12px 16px 0; }
    .pop { right: 16px; }
    #acctPop { right: auto; left: 16px; }
    .search { order: 3; flex-basis: 100%; }
    .brand { flex: 1; }
    .chip span { display: none; }
    #offBtn { padding: 0 8px 0 10px; gap: 5px; }
    .chip { padding: 0 11px; }
    .folders { margin: 10px -16px 0; padding: 0 16px; scroll-padding: 0 16px; }   /* scrolls to the screen's edges */
    /* sort becomes an icon button; the phone's own picker still lists the options */
    .select select { width: 40px; padding: 0; color: transparent; background-image: none; }
    .select select option { color: var(--text); }
    .select .ic { left: 11px; color: var(--text); }
    .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 26px 16px; }
  }
  @media (max-width: 420px) { .brand .badge { display: none; } }   /* the library name needs the room */
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-library" viewBox="0 0 24 24"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
  <symbol id="i-sort" viewBox="0 0 24 24"><path d="m3 16 4 4 4-4M7 20V4M21 8l-4-4-4 4M17 4v16"/></symbol>
  <symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></symbol>
  <symbol id="i-shelf" viewBox="0 0 24 24"><path d="M9 3h9a1 1 0 0 1 1 1v14"/><path d="M5 8a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v13l-5-3.5L5 21Z"/></symbol>
  <symbol id="i-read" viewBox="0 0 24 24"><path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></symbol>
  <symbol id="i-back" viewBox="0 0 24 24"><path d="M19 12H5M12 19l-7-7 7-7"/></symbol>
  <symbol id="i-device" viewBox="0 0 24 24"><rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/></symbol>
  <symbol id="i-offline" viewBox="0 0 24 24"><path d="M2 8.8a15 15 0 0 1 4.2-2.6M10.7 5.1A15 15 0 0 1 22 8.8M5 12.9a10 10 0 0 1 5.2-2.7M16.8 11.2a10 10 0 0 1 2.2 1.7M8.5 16.4a5 5 0 0 1 7 0M12 20h.01M2 2l20 20"/></symbol>
  <symbol id="i-cloud" viewBox="0 0 24 24"><path d="M12 13v8M8 17l4 4 4-4M4 14.9A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.5 8.24"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></symbol>
  <symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></symbol>
</svg>
<header><div class="bar">
  <div class="brand">
    <span class="logoBox"><svg class="ic logo" aria-hidden="true"><use href="#i-library"/></svg><svg id="acctMark" class="ic" aria-hidden="true" hidden><use href="#i-check"/></svg></span>
    <div class="names"><h1>__TITLE__</h1></div>
    <span id="count" class="badge"></span>
    <button id="acctBrand" type="button" aria-expanded="false" aria-controls="acctPop" aria-label="Account" hidden
      title="Account: your last-opened books and settings on all your devices"></button>
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
        <option value="opened">Recently opened</option>
        <option value="author">Author</option>
        <option value="title">Title</option>
        <option value="added">Recently added</option>
        <option value="size">File size</option>
      </select>
    </div>
    <button id="group" class="chip" aria-pressed="false" title="Group books by author">
      <svg class="ic" aria-hidden="true"><use href="#i-user"/></svg><span>Group by author</span></button>
    <button id="offBtn" class="chip" type="button" aria-expanded="false" aria-controls="offPanel" hidden
      title="Books kept on this device"><svg class="ic" aria-hidden="true"><use href="#i-cloud"/></svg><span>Offline</span><b id="offLabel" class="count"></b></button>
    <a id="openFile" class="chip" href="__READER__" title="Open any EPUB file in the reader">
      <svg class="ic" aria-hidden="true"><use href="#i-folder"/></svg><span>Open file</span></a>
  </div>
</div>
<nav id="folders" class="folders" aria-label="Folders" hidden></nav>
</header>
<div id="fileNote" class="note" hidden><svg class="ic" aria-hidden="true"><use href="#i-info"/></svg>
  <div>Opened directly from disk: books ask you to choose the file. For one-click reading open
  the catalog with <b>Open Library</b> (<code>Open Library.bat</code> on Windows,
  <code>Open Library.sh</code> on Linux) in this folder.</div></div>
<div id="offPanel" class="pop" role="dialog" aria-label="Books on this device" hidden>
  <div class="pophead">On this device</div>
  <div id="offStat" class="popstat"></div>
  <div class="popbar" id="offBarWrap" hidden><div id="offBar"></div></div>
  <div class="popseg" role="radiogroup" aria-label="Keep on this device">
    <label><input type="radio" name="offMode" value="all"> All books (the whole library offline)</label>
    <label><input type="radio" name="offMode" value="opened"> Only books I open or keep</label>
  </div>
  <div id="offInfo" class="popinfo"></div>
  <button id="offClear" class="btn" type="button">Remove all books from this device</button>
</div>
<div id="acctPop" class="pop" role="dialog" aria-label="Account" hidden>
  <div id="acctIn">
    <div class="tabs" role="tablist">
      <button type="button" role="tab" data-tab="signin">Sign in</button>
      <button type="button" role="tab" data-tab="create">Create account</button>
    </div>
    <form id="acctSignIn" novalidate data-pane="signin" role="tabpanel">
      <input name="login" type="email" inputmode="email" placeholder="Email" autocomplete="email" autocapitalize="none" spellcheck="false" aria-label="Email">
      <input name="password" type="password" placeholder="Password" autocomplete="current-password" aria-label="Password">
      <button class="go" type="submit">Sign in</button>
    </form>
    <form id="acctCreate" novalidate data-pane="create" role="tabpanel">
      <input name="login" type="email" inputmode="email" placeholder="Email" autocomplete="email" autocapitalize="none" spellcheck="false" aria-label="Email">
      <input name="password" type="password" placeholder="Password" autocomplete="new-password" aria-label="Password">
      <input name="password2" type="password" placeholder="Password again" autocomplete="new-password" aria-label="Password again">
      <input name="code" placeholder="Library code" autocomplete="off" autocapitalize="none" spellcheck="false" aria-label="Library code">
      <button class="go" type="submit">Create account</button>
    </form>
  </div>
  <div id="acctOut">
    <div id="acctWho">Signed in as <b id="acctLogin"></b></div>
    <p id="acctStatus"></p>
    <div class="row"><button type="button" id="acctSync" class="go">Sync now</button><button type="button" id="acctSignOut">Sign out</button></div>
  </div>
  <p id="acctErr" role="alert"></p>
  <p>Reading places, bookmarks and settings follow you to your other devices.
    The library code is shown in the library computer's launcher window.</p>
</div>
<div id="netNote" class="note" hidden><svg class="ic" aria-hidden="true"><use href="#i-offline"/></svg>
  <div>No connection to the library. Books on this device can be read; the others are dimmed.</div></div>
<div id="newNote" class="note" hidden><svg class="ic" aria-hidden="true"><use href="#i-info"/></svg>
  <div>The library has changed. <a href="" id="reloadLink">Show the new catalog</a></div></div>
<div id="toast" class="toast" role="status" hidden></div>
<main id="list"></main>
<section id="detail" class="detail" role="region" aria-label="About the book" hidden></section>
<footer>Generated __DATE__ · cover or title: about the book · Read: start reading</footer>

<script id="data" type="application/json">__DATA__</script>
<script src=".index/reader/sync-merge.js"></script>
<script src=".index/reader/sync.js"></script>
<script src=".index/reader/shelves.js"></script>
<script>
"use strict";
const books = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const coll = new Intl.Collator("ru", { sensitivity: "base", numeric: true });
const norm = s => s.toLowerCase().replace(/ё/g, "е");
const authorLine = b => b.authors.length ? b.authors.join(", ") : "Unknown Author";
const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " KB";
books.forEach(b => b._search = norm([b.title, authorLine(b), b.series, b.description, b.file].join(" ")));

// Installed as an app (started from its icon): the library can be kept on the device (see
// "Offline library" below) and the settings are its own. In a browser tab neither.
const inApp = navigator.standalone === true ||
  matchMedia("(display-mode: standalone), (display-mode: fullscreen), (display-mode: minimal-ui)").matches;
// When each book was last opened in the reader: {href: time}, written by reader.html
const openedTimes = () => { try { return JSON.parse(localStorage.getItem("epubLibrary:opened")) || {}; } catch { return {}; } };
let openedAt = openedTimes();

// Settings survive page reloads. The view (sort and grouping) is one for all devices and both
// catalogs (browser tab and app), synced; each catalog's own copy is the fallback.
const PREFS = inApp ? "epubIndex:app" : "epubIndex", VIEW = "epubIndex:view";
function applyPrefs() {
  const read = k => { try { return JSON.parse(localStorage.getItem(k)) || {}; } catch { return {}; } };
  const saved = read(PREFS), view = read(VIEW);
  // chosen last on any device, else this catalog's own, else the books read last first, ungrouped
  $("sort").value = view.sort || saved.sort || "opened";
  if (!$("sort").value) $("sort").value = "opened";
  $("group").setAttribute("aria-pressed", ("group" in view ? view.group : saved.group) === true);
}
applyPrefs();
const save = () => {
  try {
    const v = JSON.stringify({ sort: $("sort").value, group: $("group").getAttribute("aria-pressed") === "true" });
    localStorage.setItem(PREFS, v);
    localStorage.setItem(VIEW, v);
    localStorage.setItem(VIEW + ":time", String(Date.now()));
    localStorage.setItem(PREFS + ":time", String(Date.now()));
  } catch {}
  Sync.changed();
};
// Sync between devices (sync.js); a stand-in that does nothing if it isn't there
const Sync = window.LibrarySync || { available: () => false, signedIn: () => false, account: () => null,
  lastSync: () => null, changed() {}, onRemoteChange() {},
  syncNow: async () => "signed-out", logout: async () => ({ ok: true }),
  login: async () => ({ ok: false, error: "offline" }), register: async () => ({ ok: false, error: "offline" }) };

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

function coverArt(b) {
  return b.cover ? el("img", { src: b.cover, alt: "" }) : placeholder(b);
}

function authorLinks(b) {
  const authors = el("div", { class: "authors" });
  (b.authors.length ? b.authors : ["Unknown Author"]).forEach((a, i) => {
    if (i) authors.append(", ");
    authors.append(el("a", { title: "Show all books by this author",
                             on: () => { if (current) requestClose(); $("q").value = a; render(); } }, a));
  });
  return authors;
}

const metaLine = b => [b.series && `${b.series}${b.seriesIndex ? " #" + b.seriesIndex : ""}`,
                       b.year, b.publisher, fmtSize(b.size)].filter(Boolean).join(" · ");

function saveLink(b, props, ...children) {
  const fileName = b.file.split("/").pop();
  const a = el("a", { href: b.href, download: fileName, title: "Save the EPUB file: " + fileName, ...props }, ...children);
  a.addEventListener("click", e => saveAs(e, b.href, fileName));
  return a;
}

// Book page: shown first for books with a description; "Read" then opens the reader.
// The cover flies from its place on the shelf to the left, the text slides in from the right;
// closing plays it backwards. Each open book page is a history entry: Back closes it, and
// leaving the reader with Esc comes back to it.
const detail = $("detail");
const motion = matchMedia("(prefers-reduced-motion: no-preference)");
const DUR = 520, EASE = "cubic-bezier(.22,.8,.24,1)";
let current = null;

const shelfCover = b => document.querySelector(`.book[data-key="${CSS.escape(b.href)}"] .shelf`);
const onScreen = r => r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
const box = r => ({ left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });

// The running opening transition: its animations, the flying cover copy, the
// covers hidden meanwhile and what to do at the end. Only one at a time; all clean-up is here,
// so an interrupted transition never leaves a stray copy or a hidden cover behind.
let flight = null;

function endFlight(f) {
  if (flight !== f) return;
  flight = null;
  f.anims.forEach(a => a.cancel());
  if (f.ghost) f.ghost.remove();
  f.hidden.forEach(e => { e.style.visibility = ""; e.style.opacity = ""; });
  if (f.end) f.end();
}

function startFlight(phase, anims, ghost, hidden, end) {
  const f = { phase, anims, ghost, hidden, end };
  flight = f;
  // the book page's own cover is only made transparent: it stays a link that can be tapped while the
  // copy flies to it (hidden, a tap would fall through to the page and do nothing); the shelf's
  // cover is behind the book page anyway
  hidden.forEach(e => { if (e.closest("#detail")) e.style.opacity = "0"; else e.style.visibility = "hidden"; });
  Promise.all(anims.map(a => a.finished)).then(() => { if (f.phase === phase) endFlight(f); }, () => {});
  return f;
}

// A copy of the cover moving between two places (null if either is off screen)
function fly(from, to, b, o) {
  if (!from || !to) return null;
  const a = from.getBoundingClientRect(), z = to.getBoundingClientRect();
  if (!onScreen(a) || !onScreen(z)) return null;
  const ghost = el("div", { class: "ghost" }, coverArt(b));
  if (!b.cover) ghost.firstChild.style.fontSize = getComputedStyle(to.querySelector(".ph") || to).fontSize;
  document.body.append(ghost);
  return { ghost, anim: ghost.animate([box(a), box(z)], o) };
}

// Text lines slide in from the right one after another; the phone's pinned Read bar rises
// from the bottom instead (moving its parent would drag it along)
function slideParts(body, o) {
  return [...body.children].map((part, i) => {
    const pinned = getComputedStyle(part).position === "fixed";
    const away = { opacity: 0, transform: pinned ? "translateY(100%)" : "translateX(80px)" };
    return part.animate([away, { opacity: 1, transform: "none" }], { ...o, delay: DUR * .22 + i * 45 });
  });
}

function setCatalogInert(on) {
  for (const e of [document.querySelector("header"), $("fileNote"), $("list"), document.querySelector("footer")])
    if (e) e.inert = on;
  document.body.classList.toggle("detail-open", on);
}

function hideDetail() { detail.hidden = true; detail.replaceChildren(); detail.style.pointerEvents = ""; }

function openDetail(b, animate = true) {
  if (flight) endFlight(flight);   // e.g. a click on another book while one is still opening
  current = b;
  const read = el("a", { class: "btn primary", href: readerUrl(b) }, icon("read"), "Read");
  const body = el("div", { class: "dbody" },
    el("h3", {}, el("button", { class: "dtitle", type: "button", title: "Copy the title", on: () => copyTitle(b.title) }, b.title)),
    authorLinks(b),
    el("div", { class: "meta" }, metaLine(b)),
    el("div", { class: "dact" }, read, ...(offline ? [offline.bookButton(b), saveLink(b, { class: "btn", "aria-label": "Save the file" }, icon("download"))]
                                           : [saveLink(b, { class: "btn" }, icon("download"), "Save")])),
    b.description ? el("div", { class: "dtext" }, b.description)
                  : el("div", { class: "dtext none" }, "This book has no description."));
  const cover = el("a", { class: "dcover", href: readerUrl(b), title: "Read in the browser" }, coverArt(b));
  detail.replaceChildren(el("div", { class: "dbg" }),
    el("button", { class: "dback", type: "button", title: "Back to the catalog (Esc)", on: requestClose }, icon("back"), "Catalog"),
    el("div", { class: "dwrap" }, cover, body));
  detail.hidden = false;
  detail.style.pointerEvents = "";
  detail.scrollTop = 0;
  setCatalogInert(true);
  read.focus({ preventScroll: true });
  if (!animate || !motion.matches) return;
  const o = { duration: DUR, easing: EASE, fill: "both" };
  const shelf = shelfCover(b), trip = fly(shelf, cover, b, o);
  startFlight("open", [
    detail.querySelector(".dbg").animate([{ opacity: 0 }, { opacity: 1 }], { ...o, duration: DUR * .8 }),
    detail.querySelector(".dback").animate([{ opacity: 0 }, { opacity: 1 }], { ...o, delay: DUR * .4 }),
    ...slideParts(body, o),
    ...(trip ? [trip.anim] : [])], trip && trip.ghost, trip ? [shelf, cover] : [], null);
}

function closeDetail() {
  if (!current) return;
  const b = current;
  current = null;
  setCatalogInert(false);
  const shelf = shelfCover(b);
  const after = () => { hideDetail(); if (shelf && !current) shelf.querySelector(".cover").focus({ preventScroll: true }); };

  // No animation on the way back: the shelf is there at once (only opening is animated).
  // A book page still opening stops where it is.
  if (flight) endFlight(flight);
  after();
}

function showDetail(b) {
  history.pushState({ book: b.href }, "");
  openDetail(b);
}
function requestClose() {
  const pop = history.state && history.state.book;
  closeDetail();                // right away, not after the browser gets to popstate
  if (pop) history.back();      // drop the book page from history (popstate then has nothing to do)
}
addEventListener("popstate", e => {
  const key = e.state && e.state.book, b = key && books.find(x => x.href === key);
  if (!key) { const f = (e.state && e.state.folder) || null; if (f !== folderId) openFolder(f, false); }
  if (b) { if (!current || current.href !== key) openDetail(b); }
  else closeDetail();
});

// One rule for every book: the cover (or title) shows the book's page, the Read button reads.
// Ctrl / middle click on the cover still opens the reader in a new tab.
function bookClick(b) {
  return e => {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    showDetail(b);
  };
}

function card(b) {
  const cover = el("a", { class: "cover", href: readerUrl(b), title: "About the book" },
                   b.cover ? el("img", { src: b.cover, alt: "", loading: "lazy" }) : placeholder(b));
  cover.addEventListener("click", bookClick(b));
  const shelf = el("div", { class: "shelf" }, cover);
  if (offline) shelf.append(el("button", { class: "where", type: "button",
    on: e => { e.preventDefault(); e.stopPropagation(); offline.toggleBook(b); } }, icon("cloud"), icon("device")));
  const title = el("a", { class: "title", href: readerUrl(b), title: b.title }, b.title);
  title.addEventListener("click", bookClick(b));
  const actions = el("div", { class: "actions" },
    el("a", { class: "act go", href: readerUrl(b), title: "Read in the browser" }, icon("read"), "Read"),
    saveLink(b, { class: "act save", "aria-label": "Save as…" }, icon("download")));
  return el("article", { class: "book" + (offline ? offline.marks(b) : ""), "data-key": b.href }, shelf,
    el("div", { class: "info" }, title, authorLinks(b),
      el("div", { class: "meta" }, metaLine(b)),
      b.error ? el("div", { class: "err" }, "⚠ " + b.error) : null, actions));
}

const sorters = {
  opened: (a, b) => (openedAt[b.href] || 0) - (openedAt[a.href] || 0) || sorters.author(a, b),
  author: (a, b) => coll.compare(authorLine(a), authorLine(b)) || coll.compare(a.title, b.title),
  title:  (a, b) => coll.compare(a.title, b.title),
  added:  (a, b) => b.added - a.added,
  size:   (a, b) => b.size - a.size,
};

// keep: the book row at the top of the screen stays at the same height (the list was re-made while
// the person was reading it: other order, other books); if that book is gone, the scroll stays
function render(keep) {
  const anchor = keep ? topRow() : null;
  renderList();
  if (anchor) placeRow(anchor);
}
function renderList() {
  $("searchBox").classList.toggle("filled", !!$("q").value);   // hides the "/" hint
  const words = norm($("q").value).split(/\s+/).filter(Boolean);
  openedAt = openedTimes();
  const map = shelves(), open = renderFolders(map);
  const inside = open ? books.filter(b => Shelves.has(map, open.id, b.id)) : books;
  const shown = inside.filter(b => words.every(w => b._search.includes(w))).sort(sorters[$("sort").value]);
  $("count").textContent = open
    ? (shown.length === inside.length ? `${inside.length} books in ${open.name}` : `${shown.length} of ${inside.length} in ${open.name}`)
    : (shown.length === books.length ? `${books.length} books` : `${shown.length} of ${books.length} books`);
  const list = $("list"); list.replaceChildren();
  if (open && !inside.length) {
    list.append(el("div", { class: "empty" }, icon("shelf"), "No books here yet. Open a book and choose Folders to add it."));
    return;
  }
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

// ------------------------------------------------------------ Folders
// epubLibrary:shelves (one set per device, synced with the account; edits in shelves.js): the strip
// under the bar lists them by name, All books first; it is hidden while there are none. The open
// folder is a history step (Back goes to All books) and is remembered by this catalog on this
// device (<PREFS>:folder, "" for All books), never synced.
const Shelves = window.LibraryShelves;
const SHELVES = "epubLibrary:shelves", FOLDER = PREFS + ":folder";
const bookIds = new Set(books.map(b => b.id));
let folderId = null;
function shelves() {
  try { const m = JSON.parse(localStorage.getItem(SHELVES)); return m && typeof m === "object" && !Array.isArray(m) ? m : {}; }
  catch { return {}; }
}
function saveShelves(map) {
  try { localStorage.setItem(SHELVES, JSON.stringify(map)); } catch {}
  Sync.changed();
  render(true);
}
const liveFolders = map => Shelves ? Shelves.list(map, id => bookIds.has(id), coll.compare) : [];
// the strip for this map; returns the open folder ({id, name}) or null (a folder gone meanwhile: All books)
function renderFolders(map) {
  const live = liveFolders(map);
  let open = live.find(f => f.id === folderId) || null;
  if (folderId !== null && !open) { folderId = null; try { localStorage.setItem(FOLDER, ""); } catch {} }
  const strip = $("folders");
  strip.hidden = !live.length;
  const chip = (id, ...label) => el("button", { class: "chip fchip", type: "button", "data-folder": id,
    "aria-pressed": String((folderId || "") === id), on: () => { if ((folderId || "") !== id) openFolder(id || null); } }, ...label);
  strip.replaceChildren(chip("", "All books"), ...live.map(f =>
    chip(f.id, icon("shelf"), f.name, el("b", { class: "count" }, books.filter(b => Shelves.has(map, f.id, b.id)).length))));
  return open;
}
const showOpenChip = () => {
  const c = $("folders").querySelector(".fchip[aria-pressed=true]");
  if (c && !$("folders").hidden) c.scrollIntoView({ inline: "nearest", block: "nearest" });
};
function openFolder(id, push = true) {
  folderId = id || null;
  try { localStorage.setItem(FOLDER, folderId || ""); } catch {}
  if (push) history.pushState({ folder: folderId }, "");
  render();
  scrollTo(0, 0);
  showOpenChip();
}
// At start: the folder of this history step, else the one open last here (then Back leads to All books)
{
  const st = history.state || {};
  if (st.folder !== undefined) folderId = st.folder || null;
  else {
    let saved = null; try { saved = localStorage.getItem(FOLDER) || null; } catch {}
    if (saved && liveFolders(shelves()).some(f => f.id === saved)) {
      folderId = saved;
      if (!st.book) { history.replaceState({}, ""); history.pushState({ folder: saved }, ""); }
    }
  }
}

// ------------------------------------------------------------ Where you were (this device only)
// <PREFS>:place = {href, offset, q, time}: the book row at the top of the screen (its key in the
// library), its distance from the top of the window, the search text. Kept for the catalog to come
// back to it (after the reader, after a restart on a book); never part of what is synced.
const PLACE = PREFS + ":place";
const topRow = () => {
  const h = document.querySelector("header").getBoundingClientRect().bottom;   // the header sticks
  for (const a of document.querySelectorAll("#list .book")) {
    const r = a.getBoundingClientRect();
    if (r.bottom > h + 1) return { href: a.dataset.key, offset: Math.round(r.top) };
  }
  return null;
};
function placeRow(p) {
  const a = p && p.href && [...document.querySelectorAll("#list .book")].find(x => x.dataset.key === p.href);
  if (a) scrollTo(0, Math.max(0, a.getBoundingClientRect().top + scrollY - p.offset));
}
let placeTimer = 0;
function savePlace() {
  clearTimeout(placeTimer); placeTimer = 0;
  const r = topRow();
  try { localStorage.setItem(PLACE, JSON.stringify({ href: r ? r.href : "", offset: r ? r.offset : 0, q: $("q").value, time: Date.now() })); } catch {}
}
addEventListener("scroll", () => { if (!placeTimer) placeTimer = setTimeout(savePlace, 300); }, { passive: true });
addEventListener("pagehide", savePlace);

// ------------------------------------------------------------ Offline library (installed app)
// The app keeps the catalog, the reader and the books in the site's own storage on the device
// (Cache Storage; not the browser's HTTP cache, which it clears as it likes), so they open
// without a connection; sw.js serves them from there. Here we decide what is kept: at start,
// when the connection comes back and when the app comes to the front, .index/library.json is
// compared with what is stored. Changed files are fetched again; books changed or removed in the
// library are deleted from the device; with "All books" new ones are downloaded one by one.
const offline = (() => {
  if (!inApp || !("serviceWorker" in navigator) || !("caches" in window) || !isSecureContext ||
      !/^https?:$/.test(location.protocol)) return null;
  const SHELL = "epublib-shell", BOOKS = "epublib-books", STATE = "epubLibrary:offline";
  const root = new URL(".", location.href);
  const SYNC = { cache: "no-store", headers: { "x-lib-sync": "1" } };   // sw.js lets these through
  // The same address however its letters are encoded (the same rule as in sw.js)
  const canon = url => {
    const u = new URL(url, root);
    return u.origin + u.pathname.split("/").map(p => {
      try { return encodeURIComponent(decodeURIComponent(p)); } catch { return p; }
    }).join("/");
  };
  const bookUrl = b => b._url || (b._url = canon(b.href));
  // mode: "all" | "opened"; shell: {url: hash}; books: {url: "size:mtime" of the stored copy};
  // skip: {url: true} books removed by hand that "All books" leaves alone
  const st = { mode: "all", shell: {}, books: {}, skip: {} };
  try { Object.assign(st, JSON.parse(localStorage.getItem(STATE))); } catch {}
  const keep = () => { try { localStorage.setItem(STATE, JSON.stringify(st)); } catch {} };
  let onDevice = new Set(), online = true, busy = false, again = false, lastSync = 0;
  let progress = null, noRoom = false;

  navigator.serviceWorker.register("sw.js").catch(() => {});
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});

  async function scan() {
    onDevice = new Set((await (await caches.open(BOOKS)).keys()).map(r => r.url));
  }

  function setOnline(v) { online = v; $("netNote").hidden = v; }

  async function sync() {
    if (busy) { again = true; return; }
    busy = true;
    try {
      let lib;
      try {
        const r = await fetch(canon(".index/library.json"), SYNC);
        if (!r.ok) throw new Error(r.status);
        lib = await r.json();
      } catch { setOnline(false); return; }
      setOnline(true);
      lastSync = Date.now();
      if (lib.build !== "__BUILD__") $("newNote").hidden = false;   // this page is an older copy
      await syncShell(lib.shell);
      await syncBooks(lib.books);
    } finally {
      busy = false; progress = null;
      await scan().catch(() => {}); show();
      if (again) { again = false; sync(); }
    }
  }

  async function syncShell(list) {
    const cache = await caches.open(SHELL);
    const want = new Map(list.map(f => [canon(f.url), f.hash]));
    for (const req of await cache.keys())
      if (!want.has(req.url) && !req.url.endsWith("/")) await cache.delete(req);
    const had = st.shell;
    st.shell = {};
    for (const [url, hash] of want) {
      if (had[url] === hash && await cache.match(url)) { st.shell[url] = hash; continue; }
      try {
        const r = await fetch(url, SYNC);
        if (r.ok) { await cache.put(url, r); st.shell[url] = hash; }
      } catch { setOnline(false); break; }
    }
    keep();
  }

  async function syncBooks(list) {
    const cache = await caches.open(BOOKS);
    const want = new Map(list.map(b => [canon(b.href), b.size + ":" + b.mtime]));
    // Removed from the library, or changed there: the copy on the device goes
    for (const req of await cache.keys()) {
      const stamp = want.get(req.url), had = st.books[req.url];
      if (!stamp || (had && had !== stamp)) { await cache.delete(req); delete st.books[req.url]; }
      else st.books[req.url] = stamp;    // stored by the reader since the last check
    }
    for (const url of Object.keys(st.books)) if (!want.has(url)) delete st.books[url];
    keep();
    await scan();
    show();
    if (st.mode !== "all") return;
    for (const url of Object.keys(st.skip)) if (!want.has(url) || onDevice.has(url)) delete st.skip[url];
    const missing = [...want].filter(([url]) => !onDevice.has(url) && !st.skip[url]);
    noRoom = false;
    progress = { done: 0, total: missing.length };
    for (const [url, stamp] of missing) {
      if (st.mode !== "all") break;
      progress.url = url;
      show();
      try {
        const r = await fetch(url, SYNC);
        if (r.ok) {
          await cache.put(url, r);
          st.books[url] = stamp; keep();
          onDevice.add(url);
        }
      } catch (e) {
        if (e.name === "QuotaExceededError") noRoom = true; else setOnline(false);
        break;
      }
      progress.done++;
    }
  }

  function show() {
    const here = books.filter(b => onDevice.has(bookUrl(b)));
    $("offStat").textContent = `${here.length} of ${books.length} books · ` +
      fmtSize(here.reduce((n, b) => n + b.size, 0));
    $("offLabel").textContent = progress && progress.total
      ? `${progress.done} / ${progress.total}` : `${here.length} / ${books.length}`;
    $("offBtn").title = `Offline: books on this device ${here.length} of ${books.length}`;
    $("offBtn").classList.toggle("busy", !!(progress && progress.total));
    $("offBtn").classList.toggle("all", here.length === books.length);
    $("offBarWrap").hidden = !(progress && progress.total);
    if (progress && progress.total) $("offBar").style.width = (100 * progress.done / progress.total) + "%";
    $("offInfo").textContent =
      progress && progress.total ? "Downloading books… The app can be used meanwhile." :
      !online ? "No connection to the library: changes will be checked when it is back." :
      noRoom ? "The device has no room for more books." :
      st.mode === "opened" ? "A book is kept after it is opened. Books removed or changed in the library are deleted here." :
      here.length === books.length ? "All books are on this device. New ones are downloaded, removed ones deleted." : "";
    for (const r of document.getElementsByName("offMode")) r.checked = r.value === st.mode;
    $("offClear").hidden = !here.length;
    pageButtons.forEach(b => b.isConnected ? b._label() : pageButtons.delete(b));
    for (const c of document.querySelectorAll(".book[data-key]")) {
      const b = byHref.get(c.dataset.key);
      if (!b) continue;
      const url = bookUrl(b);
      c.classList.toggle("away", away(b));
      c.classList.toggle("here", onDevice.has(url));
      c.classList.toggle("busy", (!!progress && progress.url === url) || loading.has(url));
      const mark = c.querySelector(".where");
      if (mark) mark.title = mark.ariaLabel = onDevice.has(url) ? "On this device · tap to remove" : "Not on this device · tap to download";
      c.querySelector(".cover").title = onDevice.has(url) ? "About the book · on this device"
                                                          : "About the book · not on this device";
    }
  }

  const byHref = new Map(books.map(b => [b.href, b]));
  const away = b => !online && !onDevice.has(bookUrl(b));

  // A book that is not here can't be read without the library: say so instead of a broken page
  document.addEventListener("click", e => {
    const a = e.target.closest("a[href*='#book=']");
    if (!a || e.defaultPrevented) return;
    const src = new URLSearchParams(new URL(a.href).hash.slice(1)).get("book") || "";
    const b = byHref.get(src.replace(/^(\.\.\/)+/, ""));
    if (b && away(b)) { e.preventDefault(); e.stopImmediatePropagation(); toast("This book is not on this device. Connect to the library to read it."); }
  }, true);

  for (const r of document.getElementsByName("offMode"))
    r.addEventListener("change", () => { st.mode = r.value; st.skip = {}; keep(); show(); if (st.mode === "all") sync(); });
  // All of them: also stops "All books", or they would come right back
  $("offClear").addEventListener("click", async () => {
    if (!confirm("Remove all books from this device?\nThey stay in the library. New books will not be downloaded by themselves any more.")) return;
    st.mode = "opened";
    await caches.delete(BOOKS);
    st.books = {}; st.skip = {}; keep();
    await scan(); show();
  });
  const panel = $("offPanel"), btn = $("offBtn");
  const openPanel = on => {
    if (on) panel.style.top = btn.getBoundingClientRect().bottom + 8 + "px";   // under the button, however the header wraps
    panel.hidden = !on; btn.setAttribute("aria-expanded", on);
  };
  btn.addEventListener("click", () => openPanel(panel.hidden));
  document.addEventListener("pointerdown", e => {
    if (!panel.hidden && !panel.contains(e.target) && !btn.contains(e.target)) openPanel(false);
  });
  $("reloadLink").addEventListener("click", e => { e.preventDefault(); location.reload(); });
  addEventListener("online", () => sync());
  addEventListener("offline", () => { setOnline(false); show(); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - lastSync > 30000) sync();
  });
  btn.hidden = false;
  scan().then(show, () => {});
  sync();
  // One book: download it to the device, or remove it from there. With "All books" a book removed
  // by hand is remembered (st.skip) and not downloaded again, until it is kept again or "All books"
  // is chosen anew.
  const loading = new Set();
  async function keepBook(b) {
    const url = bookUrl(b);
    if (onDevice.has(url) || loading.has(url)) return;
    loading.add(url); show();
    try {
      const r = await fetch(url, SYNC);
      if (!r.ok) throw new Error(r.status + " " + r.statusText);
      await (await caches.open(BOOKS)).put(url, r);
      st.books[url] = b.size + ":" + b.added; delete st.skip[url]; onDevice.add(url);
    } catch (e) {
      toast(e.name === "QuotaExceededError" ? "The device has no room for this book."
                                            : "Can't download the book: no connection to the library.");
    } finally { loading.delete(url); keep(); show(); }
  }
  async function dropBook(b) {
    const url = bookUrl(b);
    await (await caches.open(BOOKS)).delete(url);
    delete st.books[url]; onDevice.delete(url);
    if (st.mode === "all") st.skip[url] = true;
    keep(); show();
  }
  // The mark on the cover is a button too: the cloud downloads the book, the phone removes it
  // (after asking). The cover itself still opens the book.
  function toggleBook(b) {
    if (loading.has(bookUrl(b))) return;
    if (!onDevice.has(bookUrl(b))) return keepBook(b);
    if (confirm(`Remove “${b.title}” from this device?\nIt stays in the library and can be downloaded again.`)) dropBook(b);
  }

  // The book page's button: the same, said in words
  const pageButtons = new Set();
  function bookButton(b) {
    const url = bookUrl(b), btn = el("button", { class: "btn", type: "button" });
    btn._label = () => {
      const here = onDevice.has(url), busy = loading.has(url) || (progress && progress.url === url);
      const [long, short] = busy ? ["Downloading…", "Downloading…"]
        : here ? ["Remove from this device", "Remove"] : ["Keep on this device", "Download"];
      btn.replaceChildren(icon(here ? "trash" : "cloud"), el("span", { class: "long" }, long), el("span", { class: "short" }, short));
      btn.title = here ? "The book stays in the library" : "Download the book to read it without a connection";
      btn.disabled = !!busy;
    };
    btn.addEventListener("click", () => onDevice.has(url) ? dropBook(b) : keepBook(b));
    pageButtons.add(btn);
    btn._label();
    return btn;
  }

  const marks = b => (away(b) ? " away" : "") + (onDevice.has(bookUrl(b)) ? " here" : "");
  return { away, marks, panel: openPanel, bookButton, toggleBook };
})();

// Copy a book's title. The clipboard API needs a secure page (https, localhost or a file);
// over plain http on the local network the old way, a selected text field, still works.
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  const t = el("textarea", { readonly: "", style: "position: fixed; top: 0; left: -9999px; opacity: 0" });
  t.value = text;
  document.body.append(t);
  t.select();
  let done = false;
  try { done = document.execCommand("copy"); } catch {}
  t.remove();
  return done;
}
function copyTitle(title) {
  copyText(title).then(done => toast(done ? "Title copied" : "Couldn't copy the title"));
}

let toastTimer = 0;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").hidden = true, 3500);
}

// Back from the reader (app or browser tab): the book just read moves to the front
{
  const fresh = () => { if (JSON.stringify(openedTimes()) !== JSON.stringify(openedAt) && $("sort").value === "opened") render(true); };
  addEventListener("pageshow", e => { if (e.persisted) fresh(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) fresh(); });
}

if (location.protocol === "file:") $("fileNote").hidden = false;
$("q").addEventListener("input", () => { render(); savePlace(); });
$("sort").addEventListener("change", () => { save(); render(); });
$("group").addEventListener("click", e => {
  const b = e.currentTarget; b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
  save(); render();
});
document.addEventListener("keydown", e => {
  if (offline && e.key === "Escape" && !$("offPanel").hidden) { offline.panel(false); return; }
  if (current) { if (e.key === "Escape") { e.preventDefault(); requestClose(); } return; }
  if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); }
  if (e.key === "Escape") { $("q").value = ""; render(); }
});
// Opened again (not by Back/Forward): the search is back; and the book that was at the top is there
// again, whatever the browser would scroll to (the order may have changed meanwhile)
{
  let p = null; try { p = JSON.parse(localStorage.getItem(PLACE)); } catch {}
  const nav = performance.getEntriesByType("navigation")[0];
  if (p && typeof p.q === "string" && !(nav && nav.type === "back_forward")) $("q").value = p.q;
  try { history.scrollRestoration = "manual"; } catch {}
  render();
  placeRow(p);
  showOpenChip();
}
// Can be installed as an app (browser menu → "Install app" / "Add to Home screen"), but the browser
// is not to suggest it by itself
addEventListener("beforeinstallprompt", e => e.preventDefault());

// Let the library server know this page is open: the network launcher's window lists the devices
// using the library. A "still here" every minute and "bye" on closing; nothing from file://.
(() => {
  if (!/^https?:$/.test(location.protocol)) return;
  const ping = () => fetch("/__alive", { cache: "no-store" }).catch(() => {});
  ping();
  setInterval(ping, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
  addEventListener("pagehide", () => { try { navigator.sendBeacon("/__bye"); } catch {} });
})();

// Account: sign in / create one; signed in: last sync, "Sync now", "Sign out". Errors show here only.
let acctTab = "signin", syncResult = "", acctBusy = false;
const ACCT_ERR = {
  "wrong-code": "Wrong library code. It is shown in the library computer's launcher window.",
  taken: "There is already an account with this email. Sign in instead.",
  "bad-login": "Enter a valid email address.",
  "wrong-password": "Wrong email or password.",
  wait: "Too many tries. Try again later.",
  offline: "Can't reach the library computer.",
  "server-error": "The library computer had a problem (see its window / the .sync folder).",
};
const acctError = text => { $("acctErr").textContent = text || ""; };
function syncedAt(t) {
  const d = new Date(t), now = new Date();
  if (now - d < 60000) return "just now";
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? "at " + time : d.toLocaleDateString() + " " + time;
}
// Under the library name: the account and its last sync (one line), or "Sign in to sync"
setInterval(() => { if (!document.hidden) showBrand(); }, 60000);   // "synced 14:05" stays true while the page stays open
function showBrand() {
  if (!Sync.available()) return;
  const a = Sync.signedIn() && Sync.account(), t = Sync.lastSync();
  const waiting = syncResult === "offline" || (!t && syncResult !== "busy");
  const line = !a ? "Sign in to sync" : a.login + " · " + (syncResult === "busy" ? "syncing…"
    : waiting ? "waiting for the library computer" : "synced " + syncedAt(t));
  $("acctBrand").title = line;
  $("acctMark").toggleAttribute("hidden", !a);   // an <svg>: no .hidden property
  $("acctMark").classList.toggle("wait", !!a && waiting);
  $("acctBrand").setAttribute("aria-label", a ? "Account: " + a.login : "Account: sign in to sync");
}
function showAcct() {
  showBrand();
  const on = Sync.signedIn(), a = Sync.account();
  $("acctIn").hidden = on;
  $("acctOut").hidden = !on;
  for (const e of $("acctPop").querySelectorAll("[data-tab], [data-pane]")) {
    const sel = (e.dataset.tab || e.dataset.pane) === acctTab;
    e.classList.toggle("on", sel);
    if (e.dataset.tab) e.setAttribute("aria-selected", sel);
  }
  if (!on) {
    for (const f of [$("acctSignIn"), $("acctCreate")]) if (a && !f.elements.login.value) f.elements.login.value = a.login;
    return;
  }
  $("acctLogin").textContent = a ? a.login : "";
  const t = Sync.lastSync();
  $("acctStatus").textContent = syncResult === "busy" ? "Syncing…"
    : syncResult === "offline" || !t ? "Waiting for the library computer" + (t ? ` · last synced ${syncedAt(t)}` : "")
    : "Last synced " + syncedAt(t);
}
function toggleAcct(open = $("acctPop").hidden) {
  $("acctPop").hidden = !open;
  $("acctBrand").setAttribute("aria-expanded", open);
  if (open) { acctError(""); showAcct(); }
}
async function syncFromPanel() {
  syncResult = "busy"; showAcct();
  syncResult = await Sync.syncNow();
  if (syncResult === "signed-out") acctError("Signed out by the library computer. Sign in again.");
  showAcct();
}
async function acctAuth(form, call) {
  if (acctBusy) return;
  acctBusy = true;
  acctError("");
  for (const b of form.querySelectorAll("button")) b.disabled = true;
  try {
    const r = await call();
    if (!r.ok) {
      acctError(r.error === "wait" && r.wait ? `Too many tries. Try again in ${Math.ceil(r.wait / 60)} min.` : ACCT_ERR[r.error] || ACCT_ERR.offline);
      return;
    }
    for (const i of form.querySelectorAll("input[type=password]")) i.value = "";
    await syncFromPanel();   // the first sync merges this device's data with the account's: nothing is lost
  } finally {
    acctBusy = false;
    for (const b of form.querySelectorAll("button")) b.disabled = false;
  }
}
$("acctSignIn").onsubmit = e => {
  e.preventDefault();
  const f = e.target, login = f.elements.login.value.trim(), pw = f.elements.password.value;
  if (!login || !pw) return acctError("Enter your email and password.");
  acctAuth(f, () => Sync.login(login, pw));
};
$("acctCreate").onsubmit = e => {
  e.preventDefault();
  const f = e.target, login = f.elements.login.value.trim(), pw = f.elements.password.value, code = f.elements.code.value.trim();
  if (!login || !pw) return acctError("Enter an email and a password.");
  if (pw !== f.elements.password2.value) return acctError("The passwords don't match.");
  if (!code) return acctError("Enter the library code: it is shown in the library computer's launcher window.");
  acctAuth(f, () => Sync.register(login, pw, code));
};
$("acctPop").querySelector(".tabs").addEventListener("click", e => {
  const b = e.target.closest("[data-tab]");
  if (b) { acctTab = b.dataset.tab; acctError(""); showAcct(); }
});
$("acctPop").addEventListener("keydown", e => {
  e.stopPropagation();   // typing here never reaches the catalog's shortcuts
  if (e.key === "Escape") { e.preventDefault(); toggleAcct(false); $("acctBrand").focus(); }
});
$("acctSync").onclick = () => { acctError(""); syncFromPanel(); };
$("acctSignOut").onclick = async () => { await Sync.logout(); syncResult = ""; acctError(""); showAcct(); };
$("acctBrand").onclick = e => { e.stopPropagation(); toggleAcct(); };
document.addEventListener("click", e => {   // a click elsewhere closes the panel
  if (!$("acctPop").hidden && !$("acctPop").contains(e.target) && !$("acctBrand").contains(e.target)) toggleAcct(false);
});
if (Sync.available()) {   // secure pages only: a password never crosses the Wi-Fi in the clear
  $("acctBrand").hidden = false;
  showBrand();
}

// Signed in: what other devices opened and chose comes in at start (never waiting longer than 2 s
// for the library computer), when the page comes back to the front, and whenever a sync brings news.
{
  const refresh = () => {
    openedAt = openedTimes();
    applyPrefs();
    render(true);
    showBrand();
    if (!$("acctPop").hidden) showAcct();
  };
  Sync.onRemoteChange(r => { if (r.opened || r.prefs || r.shelves) refresh(); });
  if (Sync.signedIn()) Sync.syncNow({ timeoutMs: 2000 }).then(r => { syncResult = r; refresh(); });
  // always attached: signed in from the panel later, coming back syncs too
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Sync.signedIn()) Sync.syncNow().then(r => { syncResult = r; refresh(); });
  });
}

// Came back to a book page (reload, or the reader's Back): show it without the flight
{ const key = history.state && history.state.book, b = key && books.find(x => x.href === key);
  if (b) openDetail(b, false); }
</script>
</body>
</html>
"""


def build_page(books: list[dict], title: str, build: str) -> str:
    # JSON inside <script>: escape "<" so no "</script>" can break out
    data = json.dumps(books, ensure_ascii=False).replace("<", "\\u003c")
    return (PAGE.replace("__TITLE__", html.escape(title))
                .replace("__BUILD__", build)
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
    """Copy the reader (reader.html + epub.js + JSZip + fonts/) next to the catalog."""
    if not READER_SRC.is_dir():
        print(f"⚠️  Reader not found in {READER_SRC}: books will open as plain files")
        return
    dest.mkdir(parents=True, exist_ok=True)
    names = {f.relative_to(READER_SRC) for f in READER_SRC.rglob("*")
             if f.is_file() and f.name != "sw.js"}   # sw.js goes next to index.html
    for name in names:
        target = dest / name
        src = READER_SRC / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.is_file() and target.stat().st_size == src.stat().st_size
                and target.stat().st_mtime >= src.stat().st_mtime):
            shutil.copy2(src, target)
    for f in sorted(dest.rglob("*"), reverse=True):   # files left from an older reader; then empty folders
        if f.is_file() and f.relative_to(dest) not in names:
            f.unlink()
        elif f.is_dir() and not any(f.iterdir()):
            f.rmdir()


def write_manifest(index_dir: Path, title: str) -> None:
    """The web app manifest: the library can be installed as an app on a phone or computer
    (from the browser's menu; it needs an https address, or localhost)."""
    icons = "reader/icon"
    manifest = {
        "name": title,
        "short_name": title,
        "description": "EPUB library and reader",
        "start_url": "../index.html",
        "scope": "../",
        # Not "fullscreen": the phone's status bar (clock, battery) stays, and a fullscreen app
        # leaves a black band where the camera cutout is.
        "display": "standalone",
        "background_color": "#f7f7f5",
        "theme_color": "#f7f7f5",
        "icons": [
            {"src": f"{icons}-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icons}-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icons}-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icons}.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }
    (index_dir / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_library_list(root: Path, books: list[dict], build: str) -> None:
    """.index/library.json: what an installed app keeps on the device. The catalog page compares it
    with what it has: files with another hash are fetched again, books that changed (size, time)
    or are gone are dropped, new ones downloaded."""
    shell = [root / "index.html", root / INDEX_DIR / "manifest.webmanifest"]
    shell += sorted(f for d in (READER_DIR, COVERS_DIR) for f in (root / INDEX_DIR / d).rglob("*")
                    if f.is_file() and f.name != "sw.js")
    library = {
        "build": build,
        "shell": [{"url": "/".join(urllib.parse.quote(p) for p in f.relative_to(root).parts),
                   "hash": hashlib.sha1(f.read_bytes()).hexdigest()[:16]} for f in shell],
        "books": [{"href": b["href"], "size": b["size"], "mtime": b["added"]} for b in books],
    }
    (root / INDEX_DIR / "library.json").write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")


def install_service_worker(root: Path) -> None:
    """sw.js next to index.html (a service worker only serves the folder it is in and below)."""
    src = READER_SRC / "sw.js"
    if src.is_file():
        shutil.copy2(src, root / "sw.js")


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
    write_manifest(index_dir, title or DEFAULT_TITLE)
    install_launchers(root)
    install_service_worker(root)

    build = datetime.now().strftime("%Y%m%d%H%M%S%f")
    out = root / "index.html"
    out.write_text(build_page(books, title or DEFAULT_TITLE, build), encoding="utf-8")
    write_library_list(root, books, build)

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
