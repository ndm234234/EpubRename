"""epub_rename.py sorts books into Out/Books/<Author>/ and builds the catalog in Out/."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "epub_rename.py"
CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)


def make_epub(path, author, title):
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>"
        f"<dc:identifier>id-{title}</dc:identifier></metadata></package>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("content.opf", opf)


def run(cwd):
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True)


class RenameLayoutTest(unittest.TestCase):
    def test_books_go_under_books_catalog_stays_in_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            make_epub(base / "In" / "a.epub", "Mira Holt", "Glass River")
            r = run(base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((base / "Out/Books/Mira Holt/Mira Holt - Glass River.epub").is_file())
            self.assertFalse((base / "Out/Mira Holt").exists())
            self.assertTrue((base / "Out/index.html").is_file())
            self.assertIn("Books/Mira%20Holt/", (base / "Out/index.html").read_text(encoding="utf-8"))

    def test_existing_author_folder_in_books_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Out/Books/Holt Mira").mkdir(parents=True)
            make_epub(base / "In" / "a.epub", "Mira Holt", "Glass River")
            r = run(base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((base / "Out/Books/Holt Mira/Holt Mira - Glass River.epub").is_file())

    def test_books_left_in_old_layout_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            make_epub(base / "Out/Old Author/Old Author - Tale.epub", "Old Author", "Tale")
            make_epub(base / "In" / "a.epub", "Mira Holt", "Glass River")
            r = run(base)
            self.assertIn("outside Out/Books", r.stdout)
            self.assertIn("Old Author", r.stdout)


if __name__ == "__main__":
    unittest.main()
