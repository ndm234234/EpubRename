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
                  Block("list", items=["☐ Крепкие ветки", "☐ <b>Цветение</b>"], page=1),
                  Block("table", rows=[["Утро", "<b>Да</b>"], ["Вечер", "Нет"]], page=1)]
        pages, _ = write(blocks)
        self.assertIn('<ul class="list"><li>☐ Крепкие ветки</li><li>☐ <b>Цветение</b></li></ul>', pages)
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

    def test_right_aligned_paragraph(self):
        pages, _ = write([Block("h", "Отзывы", "Отзывы", 1), Block("p", "Текст отзыва.", "Текст отзыва."),
                          Block("p", "— <i>Мила Фенвик</i>", "— Мила Фенвик", align="right")])
        self.assertIn('<p class="right">— <i>Мила Фенвик</i></p>', pages)
        self.assertIn("<p>Текст отзыва.</p>", pages)

    def test_generator_meta(self):
        _, opf = write([Block("p", "Текст", "Текст", page=1)])
        self.assertIn('<meta name="generator" content="pdf2epub hybrid"/>', opf)
        _, opf = write([Block("p", "Текст", "Текст", page=1)], generator="pdf2epub quick")
        self.assertIn('content="pdf2epub quick"', opf)


class HeadingRunsTest(unittest.TestCase):
    def test_heading_runs(self):
        blocks = [Block("h", "ШАГ", "ШАГ", 1, page=38), Block("h", "1", "1", 2, page=38),
                  Block("h", "ПОЛИВАЙТЕ САД ПО УТРАМ", "ПОЛИВАЙТЕ САД ПО УТРАМ", 1, page=38),
                  Block("p", "Текст", "Текст", page=38)]
        out = merge_heading_runs(blocks)
        self.assertEqual([b.kind for b in out], ["h", "p"])
        self.assertEqual(out[0].text, "ШАГ 1. ПОЛИВАЙТЕ САД ПО УТРАМ")
        self.assertEqual(out[0].html, "ШАГ 1<br/>ПОЛИВАЙТЕ САД ПО УТРАМ")
        self.assertEqual(out[0].level, 1)

    def test_separate_headings_stay(self):
        blocks = [Block("h", "ПРЕДИСЛОВИЕ", "ПРЕДИСЛОВИЕ", 1, page=15),
                  Block("h", "Как читать этот справочник", "Как читать этот справочник", 2, page=15)]
        self.assertEqual(len(merge_heading_runs(blocks)), 2)


class ProgressTest(unittest.TestCase):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    def test_live_line_on_a_terminal(self):
        out = self.Tty()
        progress = pdf2epub.Progress(out)
        progress.status("🔍 reading pages 1–4 · 0:00:05…")
        progress("📄 page 1/4 · ok")
        progress.status("")
        self.assertEqual(out.getvalue(), "   🔍 reading pages 1–4 · 0:00:05…\r\033[K   📄 page 1/4 · ok\n")

    def test_no_live_line_in_a_log_file(self):
        out = io.StringIO()
        progress = pdf2epub.Progress(out)
        progress.status("🔍 reading pages 1–4 · 0:00:05…")
        progress("📄 page 1/4 · ok")
        self.assertEqual(out.getvalue(), "   📄 page 1/4 · ok\n")


class LayerContentsTest(unittest.TestCase):
    @staticmethod
    def page(number, rows):
        """A contents page whose every line is one piece of text, dot leaders and page number included."""
        page = pdf2epub.Page(number, 480, 640)
        for i, (left, text, *size) in enumerate(rows):
            size = size[0] if size else 11.0
            piece = pdf2epub.Piece(40 + 14 * i, left, 400 - left, 11, size, [(text, False, False)])
            page.lines.append(pdf2epub.Line([piece], number))
        return page

    def test_one_piece_lines_with_dot_leaders(self):
        page = self.page(6, [(200, "Содержание", 28.0), (34, "https://example.org/watermark", 7.0),
                             (41, "Отзывы о книге .............................. 15"),
                             (41, "Введение ......................................... 17"),
                             (55, "Почему я написала эту книгу ...................... 17"),
                             (55, "Кому предназначена книга ......................... 19"),
                             (41, "ГЛАВА 1. Почему так важно изучать математику"),
                             (41, "искусственного интеллекта? ....................... 33"),
                             (55, "Вперед, к математике ............................. 35"),
                             (55, "Случайные леса ................................... 13 7"),
                             (55, "Кластеризация ..................................... 138"),
                             (55, "Случайные величины ........................., 140")])
        tocs = pdf2epub.find_tocs([page], 11.0)
        self.assertEqual(len(tocs), 1)
        self.assertEqual(tocs[0].title, "Содержание", "the big line above, not the small one")
        entries = tocs[0].entries
        self.assertEqual([e.title for e in entries],
                         ["Отзывы о книге", "Введение", "Почему я написала эту книгу", "Кому предназначена книга",
                          "ГЛАВА 1. Почему так важно изучать математику искусственного интеллекта?",
                          "Вперед, к математике", "Случайные леса", "Кластеризация", "Случайные величины"])
        self.assertEqual([e.number for e in entries], [15, 17, 17, 19, 33, 35, 137, 138, 140], "13 7 is 137")
        self.assertEqual([e.level for e in entries], [1, 1, 2, 2, 1, 2, 2, 2, 2])

    def test_ordinary_text_with_numbers_is_not_contents(self):
        page = self.page(6, [(41, "В 1990 году сад занимал 12"), (41, "гектаров, а в 2000 уже 15"),
                             (41, "и рос дальше еще лет 20"), (41, "пока не достиг 30"), (41, "а потом 45")])
        self.assertEqual(pdf2epub.find_tocs([page], 11.0), [])


class GuessMetaTest(unittest.TestCase):
    ARGS = type("Args", (), {"title": None, "author": None})()

    def guess(self, name, text, info=None):
        return pdf2epub.guess_meta(Path(name), info or {}, self.ARGS, text)

    def test_author_at_the_end_of_the_file_name(self):
        text = "ТИХИЕ РЕКИ\n12 ПРИНЦИПОВ\n  М И Л А  Ф. Ф Е Н В И К\nИздательство"
        self.assertEqual(self.guess("Тихие реки 12 принципов Мила Ф Фенвик.pdf", text),
                         ("Тихие реки 12 принципов", "Мила Ф Фенвик"))

    def test_author_at_the_start_of_the_file_name(self):
        self.assertEqual(self.guess("Mila_Fenwick_Quiet_rivers.pdf", "Quiet Rivers\nMila Fenwick\n"),
                         ("Quiet rivers", "Mila Fenwick"))

    def test_capital_words_not_on_the_title_page_are_the_title(self):
        self.assertEqual(self.guess("Python Crash Course.pdf", "Python Crash Course\nby Mila Fenwick"),
                         ("Python Crash Course", ""))
        self.assertEqual(self.guess("Тихие реки Мила Фенвик.pdf", ""), ("Тихие реки Мила Фенвик", ""),
                         "no text to check by (a scan): don't guess")

    def test_dash_and_properties_still_win(self):
        self.assertEqual(self.guess("Мила Фенвик - Тихие реки.pdf", "Мила Фенвик"), ("Тихие реки", "Мила Фенвик"))
        self.assertEqual(self.guess("x.pdf", "Ян Тук", {"Title": "Тихие реки", "Author": "Мила Фенвик"}),
                         ("Тихие реки", "Мила Фенвик"))


class AskTest(unittest.TestCase):
    def test_tab_puts_the_suggestion_in_to_edit(self):
        complete = pdf2epub.tab_completer("Воспитание ребенка с СДВГ")
        self.assertEqual(complete("", 0), "Воспитание ребенка с СДВГ")
        self.assertEqual(complete("Воспит", 0), "Воспитание ребенка с СДВГ")
        self.assertIsNone(complete("", 1), "one suggestion only")
        self.assertIsNone(complete("Другое", 0))

    def test_enter_keeps_the_suggestion(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(pdf2epub.ask("Title ", "Тихие реки"), "Тихие реки")
        with mock.patch("builtins.input", return_value=" Тихие реки, том 2 "):
            self.assertEqual(pdf2epub.ask("Title ", "Тихие реки"), "Тихие реки, том 2")


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

    def test_first_pages_go_to_preview(self):
        from test_hybrid import make_pdf
        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_pdf(Path(tmp) / "book.pdf", y=400)
            out_dir = Path(tmp) / "In"
            out_dir.mkdir()
            (out_dir / "book.epub").write_bytes(b"the whole book, done before")
            stdout = io.StringIO()
            for _ in range(2):          # a trial is always made again
                with mock.patch("pdf2epub_parts.engine.ensure_engine", return_value=None), \
                        mock.patch.object(sys, "argv", ["pdf2epub.py", str(pdf), "--out", str(out_dir), "--yes",
                                                        "--pages", "2"]), \
                        contextlib.redirect_stdout(stdout):
                    code = pdf2epub.main()
                self.assertEqual(code, 0, stdout.getvalue())
            self.assertEqual((out_dir / "book.epub").read_bytes(), b"the whole book, done before")
            trial = out_dir / "Preview" / "book.epub"
            check_epub(trial)
            with zipfile.ZipFile(trial) as zf:
                text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            self.assertIn("Page 2 text", text)
            self.assertNotIn("Page 3 text", text)

    def test_chosen_pages_go_to_preview(self):
        from test_hybrid import make_pdf
        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_pdf(Path(tmp) / "book.pdf", y=400)
            out_dir = Path(tmp) / "In"
            stdout = io.StringIO()
            with mock.patch("pdf2epub_parts.engine.ensure_engine", return_value=None), \
                    mock.patch.object(sys, "argv", ["pdf2epub.py", str(pdf), "--out", str(out_dir), "--yes",
                                                    "--page", "2-3"]), \
                    contextlib.redirect_stdout(stdout):
                code = pdf2epub.main()
            self.assertEqual(code, 0, stdout.getvalue())
            with zipfile.ZipFile(out_dir / "Preview" / "book.epub") as zf:
                text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            self.assertIn("Page 2 text", text)
            self.assertIn("Page 3 text", text)
            self.assertNotIn("Page 1 text", text)

    def test_page_list(self):
        self.assertEqual(pdf2epub.page_list("23"), {23})
        self.assertEqual(pdf2epub.page_list("20-22, 40"), {20, 21, 22, 40})
        for bad in ("", "abc", "0", "5-3", "1-"):
            with self.assertRaises(ValueError, msg=bad):
                pdf2epub.page_list(bad)

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
