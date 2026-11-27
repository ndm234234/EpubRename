"""The catalog's book id equals the reader's: first dc:identifier, else first dc:title, else file name."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import make_server  # noqa: E402

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)


def book_id(metadata, rel="book.epub", package_attrs=""):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "lib"
        path = root / rel
        path.parent.mkdir(parents=True)
        opf = (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            f'version="2.0" {package_attrs}><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:opf="http://www.idpf.org/2007/opf">{metadata}</metadata></package>'
        )
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("META-INF/container.xml", CONTAINER)
            zf.writestr("content.opf", opf)
        covers = Path(tmp) / "covers"
        covers.mkdir()
        return make_server.read_book(path, root, covers, set())["id"]


class CatalogIdTest(unittest.TestCase):
    def test_identifier(self):
        self.assertEqual(
            book_id('<dc:identifier id="b">urn:uuid:1111</dc:identifier>'), "urn:uuid:1111")

    def test_identifier_untrimmed(self):
        self.assertEqual(
            book_id("<dc:identifier>\n  urn:x  \n</dc:identifier>"), "\n  urn:x  \n")

    def test_first_identifier_not_unique_identifier(self):
        md = '<dc:identifier id="first">A</dc:identifier><dc:identifier id="second">B</dc:identifier>'
        self.assertEqual(book_id(md, package_attrs='unique-identifier="second"'), "A")

    def test_empty_identifier_falls_to_title(self):
        md = "<dc:identifier/><dc:title> A Quiet Harbour </dc:title>"
        self.assertEqual(book_id(md), " A Quiet Harbour ")

    def test_identifier_starting_with_element(self):
        md = "<dc:identifier><span/>x</dc:identifier><dc:title>T</dc:title>"
        self.assertEqual(book_id(md), "T")

    def test_no_identifier_no_title(self):
        self.assertEqual(
            book_id("", rel="Jane Doe/Jane Doe - Second Tide.epub"),
            "Jane Doe - Second Tide.epub")

    def test_rename_keeps_id(self):
        md = "<dc:identifier>urn:uuid:2222</dc:identifier>"
        self.assertEqual(book_id(md, rel="a.epub"), book_id(md, rel="John Roe/b.epub"))

    def test_unreadable_book_gets_file_name(self):
        # A broken EPUB still gets an id (the reader's own fallback: the file name)
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Jane Doe").mkdir()
            with zipfile.ZipFile(root / "Jane Doe" / "Broken Tide.epub", "w") as zf:
                zf.writestr("mimetype", "application/epub+zip")   # no container.xml
            seen = {}
            real = make_server.build_page

            def spy(books, *a, **k):
                seen["books"] = books
                return real(books, *a, **k)

            with mock.patch.object(make_server, "build_page", spy), \
                    mock.patch("sys.stdout"):
                make_server.build_index(root)
            (book,) = seen["books"]
            self.assertIn("error", book)
            self.assertEqual(book["id"], "Broken Tide.epub")


if __name__ == "__main__":
    unittest.main()
