"""Tests for pdf2epub_merge: checking accurate OCR against fast OCR and the PDF text layer."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf2epub_merge import coverage, merge_page, needs_retry  # noqa: E402


def text(html_text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text)).strip()


ITEMS = ["Крепко прижать к себе ребенка", "Погладить по голове или плечу", "Ласково погладить по волосам",
         "Обнять ребенка обеими руками", "Улыбнуться", "Легко поцеловать",
         "Использовать знак большой палец вверх", "Подмигнуть"]


def ul(items):
    return "<ul>" + "".join(f"<li>☐ {i}</li>" for i in items) + "</ul>"


class VoteTest(unittest.TestCase):
    def test_vote_fixes_foreign_word(self):
        acc = "<p>Скажите ребенку, что поиграете позже, quando он будет вести себя хорошо.</p>"
        fast = "<p>Скажите ребенку, что поиграете позже, когда он будет вести себя хорошо.</p>"
        layer = "Скажите ребенку, что поиграете позже, когда он будет вести себя хорошо."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("когда", r.html)
        self.assertNotIn("quando", r.html)
        self.assertEqual(r.fixed_words, 1)

    def test_foreign_word_fixed_without_layer(self):
        acc = "<p>что поиграете с ним позже, quando он будет вести себя хорошо.</p>"
        fast = "<p>что поиграете с ним позже, когда он будет вести себя хорошо.</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("когда", r.html)

    def test_vote_keeps_accurate_on_tie(self):
        acc = "<ul><li>☐ Отличная работа!</li><li>☐ Супер!</li><li>☐ Фантастика!</li></ul>"
        fast = "<p>◦ Отличная работа! ◦ Cnep! ◦ Фантастика!</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("Супер!", r.html)
        self.assertNotIn("Cnep", r.html)

    def test_case_vote(self):
        acc = "<p>наказывайте его так, как вы обычно Это делаете.</p>"
        fast = "<p>наказывайте его так, как вы обычно это делаете.</p>"
        layer = "наказывайте его так, как вы обычно это делаете."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("обычно это делаете", r.html)

    def test_digital_layer_wins(self):
        acc = "<h3>Главнвя мысль</h3><p>Текст после заголовка идет дальше.</p>"
        fast = "<h3>Гпавная мысль</h3><p>Текст после заголовка идет дальше.</p>"
        layer = "Главная мысль Текст после заголовка идет дальше."
        r = merge_page(acc, fast, layer, digital=True)
        self.assertIn("Главная мысль", r.html)

    def test_unrelated_layer_changes_nothing(self):
        acc = "<h1>Глава 1</h1><p>Первая страница рассказывает о том, как дети растут.</p>"
        layer = "Page 1 text line for the layer"
        r = merge_page(acc, acc, layer, digital=True)
        self.assertIn("Глава 1", r.html)
        self.assertEqual(r.fixed_words, 0)

    def test_dissimilar_word_not_replaced(self):
        acc = "<p>Родители часто читают детям сказки перед сном.</p>"
        fast = "<p>Родители часто поют детям сказки перед сном.</p>"
        layer = "Родители часто поют детям сказки перед сном."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("поют", r.html, "two witnesses agree on a whole different word: they win")

    def test_markup_preserved(self):
        acc = "<p><b>ПРОБЛЕМА.</b> Очень трудно предсказать quando результаты</p>"
        fast = "<p>ПРОБЛЕМА. Очень трудно предсказать когда результаты</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("<b>ПРОБЛЕМА.</b>", r.html)
        self.assertIn("предсказать когда результаты", r.html)


    def test_latin_word_kept_when_fast_agrees(self):
        acc = "<p>Синдром ADHD описал Whitmore в своей книге подробно.</p>"
        layer = "Синдром АDНD описал Whitmоrе в своей книге подробно."
        r = merge_page(acc, acc, layer, digital=False)
        self.assertIn("ADHD", r.html)
        self.assertIn("Whitmore", r.html)


class SpliceTest(unittest.TestCase):
    def test_missing_list_items_spliced(self):
        acc = "<p>Исследования показывают, что результаты связаны с:</p>" + ul(ITEMS[:3])
        fast = "<p>Исследования показывают, что результаты связаны с:</p>" + \
            "<p>" + "<br/>".join("• " + i for i in ITEMS) + "</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertEqual(r.source, "spliced")
        self.assertGreaterEqual(r.inserted_blocks, 1)
        body = text(r.html)
        positions = [body.find(i) for i in ITEMS]
        self.assertTrue(all(p >= 0 for p in positions), body)
        self.assertEqual(positions, sorted(positions), "items must keep fast's order")

    def test_missing_item_in_the_middle_goes_in_place(self):
        acc = ul(ITEMS[:3] + ITEMS[4:])
        fast = ul(ITEMS)
        r = merge_page(acc, fast, "", digital=False)
        body = text(r.html)
        self.assertLess(body.find(ITEMS[2]), body.find(ITEMS[3]))
        self.assertLess(body.find(ITEMS[3]), body.find(ITEMS[4]))
        self.assertEqual(r.html.count("<ul"), 1, "a missing item joins the same list")

    def test_missing_paragraph_spliced_after_previous(self):
        p1 = "Первый абзац рассказывает о том, как дети растут и учатся."
        p2 = "Второй абзац пропал у точного режима целиком, хотя он важен."
        p3 = "Третий абзац завершает страницу и подводит итог сказанному."
        r = merge_page(f"<p>{p1}</p><p>{p3}</p>", f"<p>{p1}</p><p>{p2}</p><p>{p3}</p>", "", digital=False)
        body = text(r.html)
        self.assertTrue(body.find(p1) < body.find(p2) < body.find(p3), body)
        self.assertTrue(needs_retry(r))

    def test_whole_page_from_fast(self):
        fast = "<p>" + " ".join(ITEMS * 3) + "</p>"
        acc = "<p>Крепко прижать к себе</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertEqual(r.source, "fast")
        self.assertEqual(r.html, fast)

    def test_complete_page_not_retried(self):
        acc = "<p>Все родители мечтают, чтобы их дети выросли и стали успешными.</p>"
        r = merge_page(acc, acc, "", digital=False)
        self.assertEqual(r.source, "accurate")
        self.assertFalse(needs_retry(r))

    def test_misread_line_not_spliced_as_missing(self):
        acc = ("<ul><li>☐ Отличная работа!</li><li>☐ Здорово!</li><li>☐ Потрясающе!</li>"
               "<li>☐ Супер!</li><li>☐ Фантастика!</li></ul>")
        fast = "<p>◦ Отличная работа!<br/>◦ Здорово!<br/>◦ Потрясающе!<br/>• Cnep!</p><h2>О Фантастика!</h2>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertNotIn("Cnep", r.html)
        self.assertEqual(r.inserted_blocks, 0)

    def test_truncated_paragraph_restored(self):
        words = ("Последний абзац страницы рассказывает о том, как важно хвалить ребенка сразу, "
                 "а не через час, потому что дети с СДВГ плохо связывают похвалу с поступком, "
                 "если между ними прошло много времени и случилось что-то еще.")
        cut = " ".join(words.split()[:16])
        acc = f"<p>Первый абзац на месте.</p><p>{cut}</p>"
        fast = f"<p>Первый абзац на месте.</p><p>{words}</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn(words, text(r.html))
        self.assertEqual(r.html.count("<p>"), 2, "the words go back into the same paragraph")

    def test_moved_block_not_duplicated(self):
        a = "Первый абзац рассказывает о том, как дети растут и учатся каждый день."
        b = "Врезка: родителям стоит помнить, что поведение зависит от ситуации и усталости."
        c = "Третий абзац завершает страницу и подводит итог всему сказанному выше."
        r = merge_page(f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"<p>{a}</p><p>{b}</p><p>{c}</p>", "", digital=False)
        self.assertEqual([text(r.html).count(w) for w in ("Первый", "Врезка", "Третий")], [1, 1, 1], r.html)
        r = merge_page(f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"{a} {b} {c}",
                       digital=False)
        self.assertEqual([text(r.html).count(w) for w in ("Первый", "Врезка", "Третий")], [1, 1, 1], r.html)

    def test_extra_text_flagged(self):
        page = "<p>Обычный текст страницы о воспитании детей и их поведении дома и в школе.</p>"
        loop = "<p>" + " ".join(["и так далее"] * 40) + "</p>"
        r = merge_page(page + loop, page, "", digital=False)
        self.assertGreaterEqual(r.extra_run, 20)
        self.assertTrue(needs_retry(r))
        r = merge_page(page, page, "", digital=False)
        self.assertEqual(r.extra_run, 0)

    def test_junk_not_spliced(self):
        acc = "<p>Хороший родитель должен быть с ребенком и наслаждаться им.</p>"
        fast = "<p>Хороший родитель должен быть с ребенком и наслаждаться им.</p><p>Ш8Ш</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertNotIn("Ш8Ш", r.html)

    def test_coverage_uses_layer_too(self):
        acc = "<p>Первое предложение страницы стоит здесь.</p>"
        fast = "<p>Первое предложение страницы стоит здесь.</p>"
        layer = ("Первое предложение страницы стоит здесь. Второе предложение есть только "
                 "в текстовом слое этой страницы и больше нигде.")
        self.assertLess(coverage(acc, [fast, layer]), 1.0)
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("только в текстовом слое", r.html)


if __name__ == "__main__":
    unittest.main()
