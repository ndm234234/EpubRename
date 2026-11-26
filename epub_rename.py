#!/usr/bin/env python3
"""Copy EPUB files from the In folder to Out/<Authors>/<Authors> - <Title>.epub.

Author and title are taken from the book metadata (the OPF file inside the EPUB).
Source files in In are only read, never modified.
Uses only the Python standard library.
"""

import hashlib
import re
import shutil
import sys
import unicodedata
import zipfile
from collections import Counter
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent  # real location (symlinks resolved)
BASE_DIR = Path.cwd()                         # In/ and Out/ are taken from the current folder
IN_DIR = BASE_DIR / "In"
OUT_DIR = BASE_DIR / "Out"
UNKNOWN_AUTHOR = "Unknown Author"
AUTHOR_SEPARATOR = ", "   # between co-authors (" - " separates authors from the title)
MAX_AUTHORS_SHOWN = 2     # more authors than this -> first two + ET_AL
ET_AL = " и др."

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Characters forbidden on Windows (the strictest case; Linux forbids only "/")
FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_BYTES = 200  # safety margin below the 255-byte file name limit


def sanitize(name: str, fallback: str) -> str:
    """Make a string safe to use as a file or folder name."""
    name = unicodedata.normalize("NFC", name or "")
    name = FORBIDDEN_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")  # no leading/trailing dots or spaces
    if not name:
        name = fallback
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        name = "_" + name
    # Trim by byte length (Cyrillic takes 2 bytes per character in UTF-8)
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_NAME_BYTES:
        name = encoded[:MAX_NAME_BYTES].decode("utf-8", errors="ignore").rstrip(" .")
    return name


def read_metadata(epub_path: Path) -> tuple[list[str], str]:
    """Return ([authors], title) from the EPUB metadata."""
    with zipfile.ZipFile(epub_path, mode="r") as zf:  # read-only
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", NS)
        if rootfile is None:
            raise ValueError("rootfile not found in container.xml")
        opf = ET.fromstring(zf.read(rootfile.attrib["full-path"]))

    metadata = opf.find("opf:metadata", NS)
    if metadata is None:
        metadata = opf  # fallback for non-standard structure

    title_el = metadata.find(".//dc:title", NS)
    creators = metadata.findall(".//dc:creator", NS)

    title = (title_el.text or "").strip() if title_el is not None else ""
    # All authors, in the order they are listed in the book
    authors = [(c.text or "").strip() for c in creators]
    return [a for a in authors if a], title


def normalize_author(raw: str) -> str:
    """Bring an author name to the "First Last" form.

    "Толстой, Лев Николаевич" -> "Лев Николаевич Толстой"
    "ТОЛСТОЙ ЛЕВ"             -> "Толстой Лев"
    """
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw or "")).strip()
    # Exactly one comma means "Last, First". With two or more
    # (e.g. "Smith, John, Jr.") leave it alone to avoid breaking it.
    if name.count(",") == 1:
        last, first = (p.strip() for p in name.split(","))
        if last and first:
            name = f"{first} {last}"
    # All-caps name -> normal case
    if name.isupper():
        name = name.title()
    return name


def author_key(name: str) -> tuple[str, ...]:
    """Key for comparing authors ignoring word order, case, ё/е and punctuation.

    "Лев Толстой", "Толстой Лев", "толстой, лев" -> ('лев', 'толстой')
    """
    words = re.findall(r"\w+", name.lower().replace("ё", "е"))
    return tuple(sorted(words))


def build_author_names(raw_authors: list[str]) -> dict[tuple[str, ...], str]:
    """Pick a single spelling for each author, used as the folder name.

    Priority: an existing folder in Out, then the most frequent
    spelling among the books in the current run.
    """
    names: dict[tuple[str, ...], str] = {}
    if OUT_DIR.is_dir():
        for d in sorted(OUT_DIR.iterdir()):
            if d.is_dir():
                names.setdefault(author_key(d.name), d.name)

    counts = Counter(normalize_author(a) for a in raw_authors if a.strip())
    for name, _ in counts.most_common():
        names.setdefault(author_key(name), name)
    return names


def file_hash(path: Path) -> str:
    """SHA-256 of the file contents (read in chunks, fine for large files)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Result groups in the order they are printed: status -> (icon, header)
GROUPS = {
    "new": ("✅", "New"),
    "overwritten": ("🔄", "Overwritten — existing file was different"),
    "same": ("🟰", "Identical — already in Out, skipped"),
    "error": ("❌", "Errors"),
}


def process_book(book: Path, book_meta, author_names) -> tuple[str, str, str]:
    """Copy one book and verify it. Returns (status, sort key, report line)."""
    if isinstance(book_meta, Exception):
        return "error", book.name, f"{book.name}  —  can't read metadata: {book_meta}"
    authors, title = book_meta

    # Normalize each author and pick its unified spelling; drop duplicates
    names: list[str] = []
    for a in authors:
        normalized = normalize_author(a)
        name = author_names.get(author_key(normalized), normalized)
        if name not in names:
            names.append(name)
    if len(names) > MAX_AUTHORS_SHOWN:
        display_author = AUTHOR_SEPARATOR.join(names[:MAX_AUTHORS_SHOWN]) + ET_AL
    else:
        display_author = AUTHOR_SEPARATOR.join(names)
    safe_author = sanitize(display_author, UNKNOWN_AUTHOR)
    safe_title = sanitize(title, book.stem)

    author_dir = OUT_DIR / safe_author
    author_dir.mkdir(exist_ok=True)

    # The file name keeps the full author string (e.g. trailing "и др."), which
    # is not allowed at the end of a folder name
    file_name = sanitize(f"{display_author or safe_author} - {safe_title}", book.stem) + ".epub"
    target = author_dir / file_name
    rel = str(target.relative_to(OUT_DIR))
    line = f"{book.name}  →  {rel}"
    exists = target.exists()

    src_hash = file_hash(book)  # hash of the original BEFORE copying

    # Already in Out and identical byte for byte -> nothing to do
    if exists and target.stat().st_size == book.stat().st_size \
            and file_hash(target) == src_hash:
        return "same", rel, line

    shutil.copy2(book, target)  # source is only read; the file in Out is overwritten

    # Check 1: the original in In has not changed
    if file_hash(book) != src_hash:
        return "error", book.name, f"{book.name}  —  source file changed during processing!"

    # Check 2: the copy matches the original byte for byte
    if file_hash(target) != src_hash or book.stat().st_size != target.stat().st_size:
        target.unlink(missing_ok=True)
        return "error", book.name, f"{book.name}  —  copy doesn't match the original, removed"

    return ("overwritten" if exists else "new"), rel, line


def main() -> int:
    # Print each line immediately, even when output goes to a pipe or a file
    sys.stdout.reconfigure(line_buffering=True)
    if not IN_DIR.is_dir():
        print(f"❌ Folder not found: {IN_DIR}")
        print("   Run the script from a folder that contains In/ with your .epub files.")
        return 1
    # Protect sources: Out must not be the same as In or nested inside it
    in_real, out_real = IN_DIR.resolve(), OUT_DIR.resolve()
    if out_real == in_real or in_real in out_real.parents or out_real in in_real.parents:
        print(f"❌ In and Out folders must not be the same or nested in each other:\n"
              f"  In:  {in_real}\n  Out: {out_real}")
        return 1
    OUT_DIR.mkdir(exist_ok=True)

    books = sorted(p for p in IN_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".epub")
    if not books:
        print(f"📭 No epub files found in {IN_DIR}")
        return 0

    print(f"📚 Found {len(books)} epub file(s) in {IN_DIR.name}/\n")
    # Pass 1: read metadata of all books to unify author names
    meta: dict[Path, tuple[list[str], str] | Exception] = {}
    for book in books:
        try:
            meta[book] = read_metadata(book)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as e:
            meta[book] = e
    author_names = build_author_names(
        [a for m in meta.values() if not isinstance(m, Exception) for a in m[0]]
    )

    # Pass 2: copy. Results are collected and printed grouped at the end;
    # meanwhile a single progress line is shown (only in an interactive terminal).
    show_progress = sys.stdout.isatty()
    results: dict[str, list[tuple[str, str]]] = {k: [] for k in GROUPS}
    for n, book in enumerate(books, 1):
        if show_progress:
            print(f"\r\033[K⏳ [{n}/{len(books)}] {book.name}", end="", flush=True)
        status, sort_key, text = process_book(book, meta[book], author_names)
        results[status].append((sort_key, text))
    if show_progress:
        print("\r\033[K", end="")

    for status, (icon, header) in GROUPS.items():
        items = sorted(results[status])
        if not items:
            continue
        print(f"{icon} {header} ({len(items)})")
        width = len(str(len(items)))
        for i, (_, text) in enumerate(items, 1):
            print(f"  [{i:>{width}}/{len(items)}] {text}")
        print()

    count = {k: len(v) for k, v in results.items()}
    errors = count["error"]
    print(f"✨ Done:  ✅ new {count['new']}  ·  🔄 overwritten {count['overwritten']}"
          f"  ·  🟰 identical {count['same']}  ·  ❌ errors {errors}")
    if count["new"] or count["overwritten"]:
        print("🔒 All copies verified by SHA-256, source files untouched")

    # Rebuild the catalog Out/index.html (make_server.py lives next to this script)
    print("\n🗂  Building catalog...")
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from make_server import build_index
    except ImportError as e:
        print(f"⚠️  Catalog skipped: make_server.py not found next to this script ({e})")
    else:
        build_index(OUT_DIR)
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
