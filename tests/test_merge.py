"""Tests for pdf2epub_parts.merge: checking accurate OCR against fast OCR and the PDF text layer."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf2epub_parts.merge import coverage, merge_page, needs_retry  # noqa: E402


def text(html_text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text)).strip()


ITEMS = ["Крепкие ветки держат тяжелые плоды", "Листья блестят после дождя", "Пчелы летают над цветами",
         "Кора гладкая и без трещин", "Цветение", "Почва рыхлая",
         "Яблоки наливаются соком до осени", "Плодоношение"]


def ul(items):
    return "<ul>" + "".join(f"<li>☐ {i}</li>" for i in items) + "</ul>"


class VoteTest(unittest.TestCase):
    def test_vote_fixes_foreign_word(self):
        acc = "<p>Обещайте соседу, что польете грядки позже, quando солнце сядет за лес.</p>"
        fast = "<p>Обещайте соседу, что польете грядки позже, когда солнце сядет за лес.</p>"
        layer = "Обещайте соседу, что польете грядки позже, когда солнце сядет за лес."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("когда", r.html)
        self.assertNotIn("quando", r.html)
        self.assertEqual(r.fixed_words, 1)

    def test_foreign_word_fixed_without_layer(self):
        acc = "<p>что польете с ним грядки позже, quando солнце сядет за лес.</p>"
        fast = "<p>что польете с ним грядки позже, когда солнце сядет за лес.</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("когда", r.html)

    def test_vote_keeps_accurate_on_tie(self):
        acc = "<ul><li>☐ Крупные яблоки!</li><li>☐ Супер!</li><li>☐ Фантастика!</li></ul>"
        fast = "<p>◦ Крупные яблоки! ◦ Cnep! ◦ Фантастика!</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("Супер!", r.html)
        self.assertNotIn("Cnep", r.html)

    def test_case_vote(self):
        acc = "<p>поливайте куст так, как вы обычно Это делаете.</p>"
        fast = "<p>поливайте куст так, как вы обычно это делаете.</p>"
        layer = "поливайте куст так, как вы обычно это делаете."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("обычно это делаете", r.html)

    def test_digital_layer_wins(self):
        acc = "<h3>Главнов правило</h3><p>Текст после заголовка идет дальше.</p>"
        fast = "<h3>Гпавное правило</h3><p>Текст после заголовка идет дальше.</p>"
        layer = "Главное правило Текст после заголовка идет дальше."
        r = merge_page(acc, fast, layer, digital=True)
        self.assertIn("Главное правило", r.html)

    def test_unrelated_layer_changes_nothing(self):
        acc = "<h1>Глава 1</h1><p>Первая страница рассказывает о том, как яблони растут.</p>"
        layer = "Page 1 text line for the layer"
        r = merge_page(acc, acc, layer, digital=True)
        self.assertIn("Глава 1", r.html)
        self.assertEqual(r.fixed_words, 0)

    def test_dissimilar_word_not_replaced(self):
        acc = "<p>Садовники часто читают яблоням стихи перед сном.</p>"
        fast = "<p>Садовники часто поют яблоням стихи перед сном.</p>"
        layer = "Садовники часто поют яблоням стихи перед сном."
        r = merge_page(acc, fast, layer, digital=False)
        self.assertIn("поют", r.html, "two witnesses agree on a whole different word: they win")

    def test_markup_preserved(self):
        acc = "<p><b>СОВЕТ.</b> Заранее трудно предсказать quando урожай</p>"
        fast = "<p>СОВЕТ. Заранее трудно предсказать когда урожай</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn("<b>СОВЕТ.</b>", r.html)
        self.assertIn("предсказать когда урожай", r.html)


    def test_latin_word_kept_when_fast_agrees(self):
        acc = "<p>Сорт MELBA описал Whitmore в своей книге подробно.</p>"
        layer = "Сорт МЕLВА описал Whitmоrе в своей книге подробно."
        r = merge_page(acc, acc, layer, digital=False)
        self.assertIn("MELBA", r.html)
        self.assertIn("Whitmore", r.html)


class SpliceTest(unittest.TestCase):
    def test_missing_list_items_spliced(self):
        acc = "<p>Опыт садоводов говорит, что урожай связан с:</p>" + ul(ITEMS[:3])
        fast = "<p>Опыт садоводов говорит, что урожай связан с:</p>" + \
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
        p1 = "Первый абзац рассказывает о том, как яблони растут и цветут."
        p2 = "Второй абзац пропал у точного режима целиком, хотя он важен."
        p3 = "Третий абзац завершает страницу и подводит итог сказанному."
        r = merge_page(f"<p>{p1}</p><p>{p3}</p>", f"<p>{p1}</p><p>{p2}</p><p>{p3}</p>", "", digital=False)
        body = text(r.html)
        self.assertTrue(body.find(p1) < body.find(p2) < body.find(p3), body)
        self.assertTrue(needs_retry(r))

    def test_whole_page_from_fast(self):
        fast = "<p>" + " ".join(ITEMS * 3) + "</p>"
        acc = "<p>Крепкие ветки держат</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertEqual(r.source, "fast")
        self.assertEqual(r.html, fast)

    def test_complete_page_not_retried(self):
        acc = "<p>Все садовники мечтают, чтобы их яблони выросли и дали урожай.</p>"
        r = merge_page(acc, acc, "", digital=False)
        self.assertEqual(r.source, "accurate")
        self.assertFalse(needs_retry(r))

    def test_misread_line_not_spliced_as_missing(self):
        acc = ("<ul><li>☐ Крупные яблоки!</li><li>☐ Сочные груши!</li><li>☐ Спелые сливы!</li>"
               "<li>☐ Супер!</li><li>☐ Фантастика!</li></ul>")
        fast = "<p>◦ Крупные яблоки!<br/>◦ Сочные груши!<br/>◦ Спелые сливы!<br/>• Cnep!</p><h2>О Фантастика!</h2>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertNotIn("Cnep", r.html)
        self.assertEqual(r.inserted_blocks, 0)

    def test_truncated_paragraph_restored(self):
        words = ("Последний абзац страницы рассказывает о том, как важно поливать саженцы сразу, "
                 "а не через час, потому что молодые корни плохо переносят сухую жаркую почву, "
                 "если между поливами прошло много времени и земля успела высохнуть.")
        cut = " ".join(words.split()[:16])
        acc = f"<p>Первый абзац на месте.</p><p>{cut}</p>"
        fast = f"<p>Первый абзац на месте.</p><p>{words}</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertIn(words, text(r.html))
        self.assertEqual(r.html.count("<p>"), 2, "the words go back into the same paragraph")

    def test_moved_block_not_duplicated(self):
        a = "Первый абзац рассказывает о том, как яблони растут и цветут каждую весну."
        b = "Врезка: садоводам стоит помнить, что урожай зависит от погоды и от почвы."
        c = "Третий абзац завершает страницу и подводит итог всему сказанному выше."
        r = merge_page(f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"<p>{a}</p><p>{b}</p><p>{c}</p>", "", digital=False)
        self.assertEqual([text(r.html).count(w) for w in ("Первый", "Врезка", "Третий")], [1, 1, 1], r.html)
        r = merge_page(f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"<p>{a}</p><p>{c}</p><p>{b}</p>", f"{a} {b} {c}",
                       digital=False)
        self.assertEqual([text(r.html).count(w) for w in ("Первый", "Врезка", "Третий")], [1, 1, 1], r.html)

    def test_extra_text_flagged(self):
        page = "<p>Обычный текст страницы об уходе за садом и о работе в теплице и на грядках.</p>"
        loop = "<p>" + " ".join(["и так далее"] * 40) + "</p>"
        r = merge_page(page + loop, page, "", digital=False)
        self.assertGreaterEqual(r.extra_run, 20)
        self.assertTrue(needs_retry(r))
        r = merge_page(page, page, "", digital=False)
        self.assertEqual(r.extra_run, 0)

    def test_junk_not_spliced(self):
        acc = "<p>Хороший садовник должен быть в саду и наслаждаться им.</p>"
        fast = "<p>Хороший садовник должен быть в саду и наслаждаться им.</p><p>Ш8Ш</p>"
        r = merge_page(acc, fast, "", digital=False)
        self.assertNotIn("Ш8Ш", r.html)

    def test_coverage_uses_layer_too(self):
        acc = "<p>Первое предложение страницы стоит здесь.</p>"
        fast = "<p>Первое предложение страницы стоит здесь.</p>"
        layer = ("Первое предложение страницы стоит здесь. Второе предложение есть только "
                 "в текстовом слое этой страницы и больше нигде.")
        self.assertLess(coverage(acc, [fast, layer]), 1.0)
        r = merge_page(acc, fast, layer, digital=True)
        self.assertIn("только в текстовом слое", r.html)

    def test_scan_layer_text_not_spliced(self):
        acc = ("<p>Это относится не только к попыткам садовника помочь яблоне, но также к подкормке и методам "
               "специальной обрезки — избегайте тех, кто сосредоточен исключительно на обрезке веток. "
               "Вместо этого измените время полива, чтобы показать дереву, что оно растет.</p>")
        layer = ("Это относится не только кспециальнойнапопыткамобрезке веток и корнейсадовника помочьобрезки "
                 "— избегайте тех)яблоне. Вместо этого,, кто сосредоточен исключительноно такжеизмените время "
                 "полик подкормке и методам- ва, чтобы показать дереву, что оно растет.")
        r = merge_page(acc, acc, layer, digital=False)
        self.assertNotIn("полик", r.html, "the text layer of a scan is old OCR: its order can't be trusted")
        self.assertEqual(r.inserted_blocks, 0)


if __name__ == "__main__":
    unittest.main()
