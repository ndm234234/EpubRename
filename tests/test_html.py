"""Tests for pdf2epub_html: Marker HTML -> book blocks."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf2epub import Block  # noqa: E402
from pdf2epub_html import fix_drop_cap, join_pages, page_blocks, tocs_from_blocks  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class PageBlocksTest(unittest.TestCase):
    def setUp(self):
        self.blocks = page_blocks((FIXTURES / "chapter_page.html").read_text(), page=38)

    def test_headings_paragraphs_bold(self):
        kinds = [b.kind for b in self.blocks]
        self.assertEqual(kinds[:5], ["h", "h", "h", "p", "p"])
        self.assertEqual([b.level for b in self.blocks[:3]], [1, 2, 1])
        self.assertEqual(self.blocks[4].html, "<b>ПРОБЛЕМА.</b> Очень трудно предсказать результаты для детей с СДВГ.")
        self.assertTrue(all(b.page == 38 for b in self.blocks))

    def test_checkbox_list(self):
        lists = [b for b in self.blocks if b.kind == "list"]
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[0].items, ["■ высоким интеллектом;", "■ высоким уровнем образования;"])
        self.assertTrue(lists[1].items[0].startswith("☐ Крепко"), lists[1].items)
        self.assertNotIn("input", " ".join(lists[1].items))

    def test_table(self):
        table = next(b for b in self.blocks if b.kind == "table")
        self.assertEqual(table.rows, [["Утро", "<b>Да</b>"], ["Вечер", "Нет"]])

    def test_unknown_tags_unwrapped(self):
        last = self.blocks[-1]
        self.assertEqual(last.html, "Обычный текст со <i>словом</i> и ссылкой тут.")
        self.assertEqual(last.text, "Обычный текст со словом и ссылкой тут.")

    def test_italic_heading(self):
        h = next(b for b in self.blocks if b.kind == "h" and "Невербальные" in b.text)
        self.assertEqual(h.level, 3)
        self.assertEqual(h.html, "<i>Невербальные знаки одобрения</i>")


class ImageTest(unittest.TestCase):
    def test_single_quoted_src(self):
        blocks = page_blocks("<p>Рис. 7.1</p><p><img src='/w/images/fast-_page_7_Diagram_1.jpeg' alt=''/></p>", page=8)
        self.assertEqual([(b.kind, b.src) for b in blocks if b.kind == "img"],
                         [("img", "/w/images/fast-_page_7_Diagram_1.jpeg")])

    def test_nested_paragraphs_keep_a_space(self):
        blocks = page_blocks("<blockquote><p>Конец цитаты.</p><p>Автор</p></blockquote>", page=1)
        self.assertIn("Конец цитаты. Автор", " ".join(b.text for b in blocks))

    def test_control_characters_removed(self):
        blocks = page_blocks("<p>Текст\x1b с мусором\x07.</p>", page=1)
        self.assertEqual(blocks[0].text, "Текст с мусором.")


class DropCapTest(unittest.TestCase):
    def check(self, start):
        blocks = [Block("h", "ПРИНЦИП 1", "ПРИНЦИП 1", 1), Block("p", start + " родители мечтают", start + " родители мечтают"),
                  Block("p", "Не все дети растут быстро.", "Не все дети растут быстро.")]
        fix_drop_cap(blocks)
        self.assertEqual(blocks[1].html, "Все родители мечтают")
        self.assertEqual(blocks[1].text, "Все родители мечтают")

    def test_space_after_capital(self):
        self.check("В се")

    def test_latin_lookalike(self):
        self.check("Dсе")

    def test_ordinary_sentence_untouched(self):
        blocks = [Block("h", "Т", "Т", 1), Block("p", "А потом пришла зима", "А потом пришла зима")]
        fix_drop_cap(blocks)
        self.assertEqual(blocks[1].text, "А потом пришла зима")


class JoinPagesTest(unittest.TestCase):
    def test_sentence_across_pages(self):
        blocks = [Block("p", "Все родители мечтают, чтобы их", "Все родители мечтают, чтобы их", page=1),
                  Block("page", level=2, page=2),
                  Block("p", "дети выросли.", "дети выросли.", page=2)]
        out = join_pages(blocks)
        paras = [b for b in out if b.kind == "p"]
        self.assertEqual([p.text for p in paras], ["Все родители мечтают, чтобы их дети выросли."])
        self.assertEqual([b.kind for b in out], ["p", "page"])

    def test_hyphen_across_pages(self):
        blocks = [Block("p", "Каж-", "Каж-", page=1), Block("page", level=2, page=2),
                  Block("p", "дый из нас", "дый из нас", page=2)]
        self.assertEqual(join_pages(blocks)[0].html, "Каждый из нас")

    def test_finished_paragraph_not_joined(self):
        blocks = [Block("p", "Конец мысли.", "Конец мысли.", page=1), Block("page", level=2, page=2),
                  Block("p", "Новая мысль.", "Новая мысль.", page=2)]
        self.assertEqual(len([b for b in join_pages(blocks) if b.kind == "p"]), 2)


class TocTest(unittest.TestCase):
    def test_toc_table(self):
        blocks = page_blocks((FIXTURES / "toc_page.html").read_text(), page=7)
        tocs = tocs_from_blocks(blocks)
        self.assertEqual(len(tocs), 1)
        toc = tocs[0]
        self.assertEqual(toc.title, "СОДЕРЖАНИЕ")
        self.assertEqual([e.level for e in toc.entries], [1, 1, 1, 2, 1, 2])
        self.assertEqual([e.number for e in toc.entries], [13, 14, 15, 17, 19, 19])
        self.assertEqual(toc.entries[0].title, "ПРИМЕЧАНИЕ АВТОРА")
        self.assertEqual([b.kind for b in blocks], ["h", "toc"])
        self.assertEqual(toc.pages, [7])

    def test_toc_continues_on_next_page(self):
        first = page_blocks((FIXTURES / "toc_page.html").read_text(), page=7)
        second = page_blocks("<table>" + "".join(
            f"<tr><td><b>ПРИНЦИП {n}</b></td><td><b>{30 + n * 10}</b></td></tr>" for n in range(1, 6)) + "</table>", page=8)
        blocks = first + [Block("page", level=8, page=8)] + second
        tocs = tocs_from_blocks(blocks)
        self.assertEqual(len(tocs), 1)
        self.assertEqual(len(tocs[0].entries), 11)
        self.assertEqual(tocs[0].pages, [7, 8])
        self.assertEqual([b.kind for b in blocks], ["h", "toc", "page"])

    def test_ordinary_table_is_not_toc(self):
        blocks = page_blocks((FIXTURES / "chapter_page.html").read_text(), page=38)
        self.assertEqual(tocs_from_blocks(blocks), [])


if __name__ == "__main__":
    unittest.main()
