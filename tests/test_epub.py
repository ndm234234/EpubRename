"""Tests for writing the EPUB: lists, tables, generator mark, headings made of parts."""

import contextlib
import io
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdf2epub  # noqa: E402
from pdf2epub import Block, check_epub, split_chapters, write_epub  # noqa: E402
from pdf2epub_parts.markup import merge_heading_runs  # noqa: E402


def write(blocks, generator="pdf2epub hybrid"):
    tmp = tempfile.TemporaryDirectory()
    out = Path(tmp.name) / "book.epub"
    chapters = split_chapters(blocks, "Book")
    write_epub(out, "Book", "Author", "ru", chapters, None, {}, [], None, generator=generator)
    with zipfile.ZipFile(out) as zf:
        pages = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        opf = zf.read("OEBPS/content.opf").decode()
    check_epub(out)
    tmp.cleanup()
    return pages, opf


class EpubTest(unittest.TestCase):
    def test_list_and_table_render(self):
        blocks = [Block("h", "Глава", "Глава", 1, page=1),
                  Block("list", items=["☐ Крепко прижать", "☐ <b>Улыбнуться</b>"], page=1),
                  Block("table", rows=[["Утро", "<b>Да</b>"], ["Вечер", "Нет"]], page=1)]
        pages, _ = write(blocks)
        self.assertIn('<ul class="list"><li>☐ Крепко прижать</li><li>☐ <b>Улыбнуться</b></li></ul>', pages)
        self.assertIn("<td>Утро</td><td><b>Да</b></td>", pages)

    def test_justified_text_is_hyphenated(self):
        import pdf2epub
        rule = re.search(r"\np \{([^}]*)\}", pdf2epub.CSS).group(1)
        self.assertIn("text-align: justify", rule)
        for prop in ("hyphens: auto", "-webkit-hyphens: auto", "adobe-hyphenate: auto"):
            self.assertIn(prop, rule, "even word spacing needs hyphenation")
        self.assertNotIn("hyphens", re.search(r"\nh1, h2, h3 \{([^}]*)\}", pdf2epub.CSS).group(1))
        self.assertIn("hyphens: manual", re.search(r"div.toc p \{([^}]*)\}", pdf2epub.CSS).group(1),
                      "contents lines are not hyphenated")

    def test_generator_meta(self):
        _, opf = write([Block("p", "Текст", "Текст", page=1)])
        self.assertIn('<meta name="generator" content="pdf2epub hybrid"/>', opf)
        _, opf = write([Block("p", "Текст", "Текст", page=1)], generator="pdf2epub quick")
        self.assertIn('content="pdf2epub quick"', opf)


class HeadingRunsTest(unittest.TestCase):
    def test_heading_runs(self):
        blocks = [Block("h", "ПРИНЦИП", "ПРИНЦИП", 1, page=38), Block("h", "1", "1", 2, page=38),
                  Block("h", "ИСПОЛЬЗУЙТЕ КЛЮЧИ К УСПЕХУ", "ИСПОЛЬЗУЙТЕ КЛЮЧИ К УСПЕХУ", 1, page=38),
                  Block("p", "Текст", "Текст", page=38)]
        out = merge_heading_runs(blocks)
        self.assertEqual([b.kind for b in out], ["h", "p"])
        self.assertEqual(out[0].text, "ПРИНЦИП 1. ИСПОЛЬЗУЙТЕ КЛЮЧИ К УСПЕХУ")
        self.assertEqual(out[0].html, "ПРИНЦИП 1<br/>ИСПОЛЬЗУЙТЕ КЛЮЧИ К УСПЕХУ")
        self.assertEqual(out[0].level, 1)

    def test_separate_headings_stay(self):
        blocks = [Block("h", "ПРЕДИСЛОВИЕ", "ПРЕДИСЛОВИЕ", 1, page=15),
                  Block("h", "Как пользоваться этой книгой", "Как пользоваться этой книгой", 2, page=15)]
        self.assertEqual(len(merge_heading_runs(blocks)), 2)


class MainTest(unittest.TestCase):
    def test_main_falls_back_to_quick(self):
        from test_hybrid import make_pdf
        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_pdf(Path(tmp) / "book.pdf", y=400)  # mid-page: not a running header
            out_dir = Path(tmp) / "In"
            stdout = io.StringIO()
            with mock.patch("pdf2epub_parts.engine.ensure_engine", return_value=None), \
                    mock.patch.object(sys, "argv", ["pdf2epub.py", str(pdf), "--out", str(out_dir), "--yes"]), \
                    contextlib.redirect_stdout(stdout):
                code = pdf2epub.main()
            self.assertEqual(code, 0, stdout.getvalue())
            self.assertIn("quick mode", stdout.getvalue())
            with zipfile.ZipFile(out_dir / "book.epub") as zf:
                self.assertIn('content="pdf2epub quick"', zf.read("OEBPS/content.opf").decode())

    def test_ctrl_c_explains_resume(self):
        from test_hybrid import make_pdf
        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_pdf(Path(tmp) / "book.pdf", y=400)
            stdout = io.StringIO()
            with mock.patch("pdf2epub_parts.engine.ensure_engine", return_value=None), \
                    mock.patch.object(pdf2epub, "convert", side_effect=KeyboardInterrupt), \
                    mock.patch.object(sys, "argv", ["pdf2epub.py", str(pdf), "--out", str(Path(tmp) / "In"), "--yes"]), \
                    contextlib.redirect_stdout(stdout):
                code = pdf2epub.main()
            self.assertEqual(code, 130)
            self.assertIn("run it again", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
