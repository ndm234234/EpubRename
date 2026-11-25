"""Tests for fix_cover.py: a cover page whose picture points outside the book."""

import io
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fix_cover  # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None

CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">test</dc:identifier>
    <dc:title>The Salt Road</dc:title>
    <dc:creator>Oskar Brandt</dc:creator>
    <meta name="cover" content="thumb"/>
  </metadata>
  <manifest>
    <item id="thumb" href="cover.jpg" media-type="image/jpeg"/>
    <item id="big" href="big.jpeg" media-type="image/jpeg"/>
    <item id="fig" href="fig.png" media-type="image/png"/>
    <item id="p1" href="p1.html" media-type="application/xhtml+xml"/>
    <item id="p2" href="p2.html" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="p1"/><itemref idref="p2"/></spine>
</package>"""

COVER_PAGE = """<html><body><div>
  <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 60 90">
    <image width="60" height="90" xlink:href="{href}"></image>
  </svg>
</div></body></html>"""

TEXT_PAGE = '<html><body><p>Text</p><img src="fig.png"/></body></html>'


def picture(size, fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "navy").save(buf, fmt)
    return buf.getvalue()


def book(path: Path, href: str, big=(60, 90), thumb=(25, 30)) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/cover.jpg", picture(thumb))   # by default not in proportion: no size match
        z.writestr("OEBPS/big.jpeg", picture(big))
        z.writestr("OEBPS/fig.png", picture((60, 90), "PNG"))   # same size, but used on a page
        z.writestr("OEBPS/p1.html", COVER_PAGE.format(href=href))
        z.writestr("OEBPS/p2.html", TEXT_PAGE)


@unittest.skipIf(Image is None, "needs Pillow")
class BrokenCoverPageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "b.epub"

    def tearDown(self):
        self.dir.cleanup()

    def plan(self):
        with zipfile.ZipFile(self.path) as zf:
            return fix_cover.plan_fix(zf, generate=False)

    def test_picture_outside_the_book_is_pointed_at_the_unused_one_of_its_size(self):
        book(self.path, "file:///tmp/SvgXlinkProcessor123.png")
        plan = self.plan()
        self.assertTrue(plan.changes)
        page = plan.replace["OEBPS/p1.html"].decode()
        self.assertIn('xlink:href="big.jpeg"', page)
        self.assertNotIn("file://", page)
        fix_cover.rewrite_epub(self.path, plan)
        with zipfile.ZipFile(self.path) as zf:
            self.assertIn('xlink:href="big.jpeg"', zf.read("OEBPS/p1.html").decode())
            self.assertEqual(zf.read("OEBPS/p2.html").decode(), TEXT_PAGE)
        self.assertFalse(self.plan().changes)   # fixed for good

    def test_a_smaller_copy_in_proportion_counts_as_the_same_size(self):
        book(self.path, "file:///srv/tmp/x.png", big=(40, 60))
        self.assertIn('xlink:href="big.jpeg"', self.plan().replace["OEBPS/p1.html"].decode())

    def test_of_several_in_proportion_the_largest_is_shown(self):
        book(self.path, "file:///srv/tmp/x.png", big=(40, 60), thumb=(20, 30))
        self.assertIn('xlink:href="big.jpeg"', self.plan().replace["OEBPS/p1.html"].decode())

    def test_working_cover_page_is_left_alone(self):
        book(self.path, "big.jpeg")
        self.assertFalse(self.plan().changes)

    def test_without_a_size_match_the_cover_picture_is_used(self):
        book(self.path, "missing.png")
        with zipfile.ZipFile(self.path) as zin, zipfile.ZipFile(self.path.with_suffix(".x"), "w") as zout:
            for i in zin.infolist():
                data = zin.read(i)
                if i.filename == "OEBPS/p1.html":
                    data = data.replace(b' width="60" height="90"', b"")
                zout.writestr(i, data)
        self.path.with_suffix(".x").replace(self.path)
        page = self.plan().replace["OEBPS/p1.html"].decode()
        self.assertIn('xlink:href="cover.jpg"', page)

    def test_web_pictures_are_not_touched(self):
        book(self.path, "https://example.com/c.png")
        self.assertFalse(self.plan().changes)


HTML_PAGE = """<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8">
    <title>A &amp; B</title>
    <link href="s.css" rel="stylesheet" type="text/css">
    <style>p>b{color:red}</style>
  </head>
  <body>
    <p class=x>One&nbsp;two<br>three <input disabled></p>
    <svg xmlns="http://www.w3.org/2000/svg" viewbox="0 0 10 10" preserveaspectratio="none"><lineargradient id="g"/></svg>
    <p>&lt;tag&gt; &#169; <b><i>bold</b></i>
  </body>
</html>"""


class XhtmlTest(unittest.TestCase):
    def test_html_page_becomes_xml_with_the_same_text(self):
        out = fix_cover.to_xhtml(HTML_PAGE)
        root = ET.fromstring(out)
        x = "{http://www.w3.org/1999/xhtml}"
        self.assertEqual(root.tag, x + "html")
        self.assertEqual(root.find(f"{x}head/{x}title").text, "A & B")
        self.assertEqual(root.find(f"{x}body/{x}p").get("class"), "x")
        self.assertEqual(root.find(f".//{x}input").get("disabled"), "disabled")
        svg = root.find(".//{http://www.w3.org/2000/svg}svg")
        self.assertEqual(svg.get("viewBox"), "0 0 10 10")
        self.assertEqual(svg.get("preserveAspectRatio"), "none")
        self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}linearGradient"))
        self.assertIn("p>b{color:red}", root.find(f"{x}head/{x}style").text)
        text = "".join(root.itertext())
        self.assertIn("One\u00a0two", text)
        self.assertIn("<tag> \u00a9 bold", text)
        self.assertTrue(out.startswith("<!DOCTYPE html>"))

    def test_xml_page_is_not_touched(self):
        page = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>'
        self.assertIsNone(fix_cover.xhtml_fix(page.encode()))

    def test_html_page_gets_fixed(self):
        self.assertIsNotNone(fix_cover.xhtml_fix(HTML_PAGE.encode()))


class MimetypeTest(unittest.TestCase):
    def test_line_break_after_mimetype_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.epub"
            book(path, "big.jpeg") if Image else None
            if Image is None:
                self.skipTest("needs Pillow")
            with zipfile.ZipFile(path) as zin, zipfile.ZipFile(path.with_suffix(".x"), "w") as zout:
                for i in zin.infolist():
                    zout.writestr(i, b"application/epub+zip\r\n" if i.filename == "mimetype" else zin.read(i))
            path.with_suffix(".x").replace(path)
            with zipfile.ZipFile(path) as zf:
                plan = fix_cover.plan_fix(zf, generate=False)
            self.assertEqual(plan.replace["mimetype"], b"application/epub+zip")
            fix_cover.rewrite_epub(path, plan)
            with zipfile.ZipFile(path) as zf:
                self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
                self.assertEqual(zf.infolist()[0].compress_type, zipfile.ZIP_STORED)


if __name__ == "__main__":
    unittest.main()
