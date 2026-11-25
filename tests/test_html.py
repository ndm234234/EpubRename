"""Tests for pdf2epub_parts.markup: Marker HTML -> book blocks."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf2epub import Block  # noqa: E402
from pdf2epub_parts.markup import (fix_drop_cap, join_pages, link_endnotes, page_blocks, place_footnotes,  # noqa: E402
                                   running_head_offset, strip_running_heads, take_footnotes, tocs_from_blocks)

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



class RunningHeadTest(unittest.TestCase):
    PAGES = {23: "<h2>22 ВВЕДЕНИЕ</h2><p>ства и только время от времени.</p>",
             24: "<p>текст страницы продолжается.</p><p>ВВЕДЕНИЕ 23</p>",
             33: "<h2>32 ВВЕДЕНИЕ</h2><p>дения или развлечения.</p>",
             53: "<h2>52 ГЛАВА 1</h2><p>шения. Ежедневно.</p>",
             8: "<h2>ГЛАВА 1. КЛЮЧИ К УСПЕХУ 37</h2><p>Содержание</p>",
             27: "<p>Текст.</p><p>1 Иногда их называют иначе. — Примеч. перев.</p>",
             40: "<h1>ГЛАВА 3</h1><p>Начало главы.</p>",
             60: "<p>Обычная страница без колонтитула.</p><p>59</p>"}

    def test_offset_from_page_numbers(self):
        self.assertEqual(running_head_offset(self.PAGES), -1)

    def test_no_offset_without_enough_heads(self):
        self.assertIsNone(running_head_offset({23: self.PAGES[23], 8: self.PAGES[8], 40: self.PAGES[40]}))

    def test_heads_removed_text_kept(self):
        strip = lambda n: strip_running_heads(self.PAGES[n], n, -1)
        self.assertEqual(strip(23), "<p>ства и только время от времени.</p>")
        self.assertEqual(strip(24), "<p>текст страницы продолжается.</p>")
        self.assertEqual(strip(60), "<p>Обычная страница без колонтитула.</p>")
        for n in (8, 27, 40):
            self.assertEqual(strip(n), self.PAGES[n], "contents line, footnote and chapter heading stay")
        self.assertEqual(strip_running_heads(self.PAGES[23], 23, None), self.PAGES[23])

    def test_chapter_heading_and_footnote_are_not_heads(self):
        pages = {n: f"<h1>Глава {n}</h1><p>Текст главы.</p><p><sup>{n}</sup> Сноска.</p>" for n in (1, 2, 3, 4)}
        self.assertIsNone(running_head_offset(pages))
        self.assertEqual(strip_running_heads(pages[2], 2, 0), pages[2])


class FootnoteTest(unittest.TestCase):
    def page(self, n, html):
        return [Block("page", level=n, page=n)] + page_blocks(html, n)

    def book(self):
        return (self.page(154, "<p>и лечение эффективны в <i>момент исполнения.</i><sup>9</sup></p>"
                               "<p>Составляйте списки. Когда обычно пишете список</p><p></p>"
                               "<p><sup>9</sup> Иногда это называют “правилом нужного момента”. — <i>Примеч. перев</i>.</p>")
                + self.page(155, "<p>“дел”, вы именно так и поступаете.</p>"))

    def test_note_leaves_the_text_and_follows_its_paragraph(self):
        blocks, notes = take_footnotes(self.book())
        self.assertEqual(len(notes), 1)
        blocks = join_pages(blocks)
        place_footnotes(blocks, notes)
        kinds = [b.kind for b in blocks if b.kind != "page"]
        self.assertEqual(kinds, ["p", "note", "p"])
        body = [b for b in blocks if b.kind != "page"]
        self.assertIn('<a epub:type="noteref" href="#fn1" id="fnref1"><sup>9</sup></a>', body[0].html)
        self.assertEqual(body[1].anchor, "fn1")
        self.assertIn("правилом нужного момента", body[1].html)
        self.assertIn('href="#fnref1"', body[1].html)
        self.assertEqual(body[2].text, "Составляйте списки. Когда обычно пишете список “дел”, вы именно так и поступаете.")

    def test_sup_without_a_reference_is_not_a_note(self):
        blocks = self.page(10, "<p>Площадь 5 м<sup>2</sup> хватит.</p><p><sup>2</sup> м — квадратные метры.</p>")
        blocks2, notes = take_footnotes(blocks)
        self.assertEqual(len(notes), 1, "a reference elsewhere on the page makes it a note")
        blocks = self.page(10, "<p>Обычный текст страницы.</p><p><sup>3</sup> Строка с числом.</p>")
        blocks2, notes = take_footnotes(blocks)
        self.assertEqual(notes, [])
        self.assertEqual(blocks2, blocks)


class EndnoteTest(unittest.TestCase):
    def blocks(self, pages):
        out = []
        for n, html in pages:
            out += [Block("page", level=n, page=n)] + page_blocks(html, n)
        return out

    def notes(self, blocks):
        return [b for b in blocks if b.kind == "endnote"]

    def test_numbers_lost_by_recognition_come_back_in_order(self):
        blocks = self.blocks([
            (5, "<p>Текст со ссылкой<sup>[1]</sup> и еще одной<sup>[2]</sup>.</p>"),
            (6, "<p>Дальше<sup>[3]</sup>, потом<sup>[4]</sup> и<sup>[5]</sup>.</p>"),
            (9, "<h2>notes</h2><h2>Примечания</h2><p>Первое примечание.</p><p>Второе примечание.</p>"
                "<h3>3</h3><p>Третье примечание.</p><p>4 Четвертое примечание.</p><p>Пятое примечание.</p>")])
        self.assertEqual(link_endnotes(blocks), 5)
        notes = self.notes(blocks)
        self.assertEqual([b.anchor for b in notes], ["en1", "en2", "en3", "en4", "en5"])
        self.assertEqual(notes[0].html, '<a href="#enref1">1.</a> Первое примечание.')
        self.assertEqual(notes[2].html, '<a href="#enref3">3.</a> Третье примечание.')
        self.assertEqual(notes[3].html, '<a href="#enref4">4.</a> Четвертое примечание.')
        self.assertIn('Текст со ссылкой<a epub:type="noteref" href="#en1" id="enref1"><sup>[1]</sup></a>', blocks[1].html)
        headings = [b.text for b in blocks if b.kind == "h"]
        self.assertEqual(headings, ["Примечания"], "the label and the lone numbers are not headings")

    def test_notes_after_each_chapter(self):
        blocks = self.blocks([
            (1, "<h1>Глава 1</h1><p>Раз<sup>1</sup> и два<sup>2</sup>.</p>"),
            (2, "<h2>Примечания</h2><p>1 Первая глава, первое.</p><p>2 Первая глава, второе.</p>"),
            (3, "<h1>Глава 2</h1><p>Снова раз<sup>1</sup>.</p>"),
            (4, "<h2>Примечания</h2><p>1 Вторая глава, первое.</p>"),
            (5, "<h1>Об авторе</h1><p>1990 год рождения.</p>")])
        self.assertEqual(link_endnotes(blocks), 3)
        notes = self.notes(blocks)
        self.assertEqual([b.text for b in notes],
                         ["Первая глава, первое.", "Первая глава, второе.", "Вторая глава, первое."])
        text = " ".join(b.html for b in blocks if b.kind == "p")
        self.assertIn('Снова раз<a epub:type="noteref" href="#en3" id="enref3"><sup>1</sup></a>', text)
        self.assertIn("1990 год рождения.", text, "the notes end at the next chapter")

    def test_no_notes_section_changes_nothing(self):
        blocks = self.blocks([(5, "<p>Площадь 5 м<sup>2</sup> и ссылка<sup>[1]</sup>.</p>")])
        before = [b.html for b in blocks]
        self.assertEqual(link_endnotes(blocks), 0)
        self.assertEqual([b.html for b in blocks], before)

if __name__ == "__main__":
    unittest.main()
