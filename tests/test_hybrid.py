"""Tests for pdf2epub_parts.hybrid: the per-book pipeline with a fake recognition engine."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pdf2epub_parts.hybrid as hy  # noqa: E402
from pdf2epub import check_epub  # noqa: E402
from pdf2epub_parts.engine import Engine, MarkerError  # noqa: E402

SENTENCES = ["Первая страница рассказывает о том, как яблони растут и цветут каждую весну.",
             "Вторая страница продолжает рассказ об уходе за садом и о том, что важно садоводам.",
             "Третья страница подводит итог и завершает эту короткую тестовую книгу."]


def make_pdf(path: Path, pages=SENTENCES, y=700, blank=(), drawn=()) -> Path:
    """A tiny text PDF, one Helvetica line per page (latin transliteration is enough for the layer);
    pages (0-based) in blank have nothing on them, in drawn only a thick line (a drawing, no text)."""
    objs = []

    def add(o):
        objs.append(o)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = []
    for n, _ in enumerate(pages):
        data = f"BT /F1 12 Tf 72 {y} Td (Page {n + 1} text line for the layer) Tj ET".encode()
        if n in blank:
            data = b""
        elif n in drawn:
            data = b"0 0 0 RG 12 w 100 100 m 500 700 l S"
        contents.append(add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"))
    pages_id = len(objs) + len(pages) + 1
    kids = [add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 %d 0 R >> >>"
                b" /Contents %d 0 R >>" % (pages_id, font, c)) for c in contents]
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    cat = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, x)
    path.write_bytes(out)
    return path


class FakeEngine:
    """Stands in for run_marker: records calls, can fail or be interrupted."""

    def __init__(self, fail_accurate=(), interrupt_on=None, fail_fast=(), loop_accurate=(), fast_heads=False,
                 notes=False, endnotes=False, empty_accurate=(), empty_once=(), formula=False,
                 blank=(), short_accurate=()):
        self.formula, self.blank, self.short_accurate = formula, set(blank), set(short_accurate)
        self.fast_heads, self.notes, self.endnotes = fast_heads, notes, endnotes
        self.empty_accurate, self.empty_once = set(empty_accurate), set(empty_once)
        self.calls, self.fail_accurate, self.interrupt_on = [], set(fail_accurate), interrupt_on
        self.fail_fast, self.loop_accurate = set(fail_fast), set(loop_accurate)

    clock = None                  # [seconds]: a run of Marker takes a minute a page on it

    def __call__(self, engine, pdf, mode, pages, out_dir):
        self.calls.append((mode, list(pages)))
        if set(pages) & self.blank:
            raise MarkerError("TypeError: 'NoneType' object is not iterable")   # Marker on a blank page
        if mode == "accurate" and set(pages) & self.short_accurate:
            return {i: "<p>Глава</p>" if i in self.short_accurate else f"<p>{SENTENCES[i]}</p>" for i in pages}
        if self.clock is not None and mode == "accurate":
            self.clock[0] += 60 * len(pages)
        if mode == "accurate":
            if self.interrupt_on is not None and self.interrupt_on in pages:
                self.interrupt_on = None
                raise KeyboardInterrupt
            if set(pages) & self.fail_accurate:
                raise MarkerError("boom")
        elif set(pages) & self.fail_fast:
            raise MarkerError("boom")
        if mode == "accurate" and set(pages) & (self.empty_accurate | self.empty_once):
            blank = set(pages) & (self.empty_accurate | self.empty_once)
            self.empty_once -= blank
            return {i: "" if i in blank else f"<h1>Глава {i + 1}</h1><p>{SENTENCES[i]}</p>" for i in pages}
        if mode == "accurate" and set(pages) & self.loop_accurate:
            return {i: f"<h1>Глава {i + 1}</h1><p>{SENTENCES[i]}</p><p>" + " ".join(["и так далее"] * 40) + "</p>"
                    for i in pages}
        tag = "<b>Внимание.</b> " if mode == "accurate" else ""
        head = (lambda i: f"<h2>{i + 11} ТЕСТОВАЯ КНИГА</h2>") if mode == "fast" and self.fast_heads else (lambda i: "")
        note = (lambda i: f"<p>Ссылка на сноску<sup>{i + 1}</sup> в тексте.</p><p><sup>{i + 1}</sup> Текст сноски {i + 1}.</p>"
                if mode == "accurate" and self.notes else "")
        if mode == "accurate" and self.endnotes:
            end = {0: "<p>Ссылка на примечание<sup>[1]</sup> в тексте.</p>",
                   2: "<h2>Примечания</h2><p>Текст примечания.</p>"}
            note = lambda i: end.get(i, "")
        math = (lambda i: "<p>Пусть <math>x</math> растет.</p>" if mode == "accurate" and self.formula else "")
        return {i: f"{head(i)}<h1>Глава {i + 1}</h1><p>{tag}{SENTENCES[i]}</p>{note(i)}{math(i)}" for i in pages}

    def accurate_pages(self):
        return sorted(p for mode, pages in self.calls if mode == "accurate" for p in pages)


class HybridTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.pdf = make_pdf(self.dir / "book.pdf")
        self.out = self.dir / "In" / "book.epub"
        self.out.parent.mkdir()
        self.engine = Engine(self.dir / "py", self.dir / "marker", self.dir / "llama-server")
        self.stops = []
        self.lines = []

    def convert(self, fake, keep_work=False, pdf=None, out=None, status=None, **kw):
        return hy.convert_hybrid(pdf or self.pdf, out or self.out, "Book", "Author", "ru", self.engine, keep_work,
                                 self.lines.append, run=fake, stop=self.stops.append, status=status, **kw)

    def work(self, pdf=None):
        return self.out.parent / ".pdf2epub-work" / (pdf or self.pdf).stem

    def test_converts_with_fake_engine(self):
        summary = self.convert(FakeEngine())
        self.assertIn("chapter", summary)
        check_epub(self.out)
        with zipfile.ZipFile(self.out) as zf:
            self.assertIn("pdf2epub hybrid", zf.read("OEBPS/content.opf").decode())
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        for s in SENTENCES:
            self.assertIn(s, text)
        self.assertIn("<b>Внимание.</b>", text)
        self.assertTrue(any("page" in line for line in self.lines))

    def test_resume_skips_done_pages(self):
        with mock.patch.object(hy, "ACC_BATCH", 1):
            with self.assertRaises(KeyboardInterrupt):
                self.convert(FakeEngine(interrupt_on=1))
            self.assertFalse(list(self.work().rglob("*.part")))
            second = FakeEngine()
            self.convert(second)
        self.assertEqual(second.accurate_pages(), [1, 2])
        self.assertNotIn("fast", [m for m, _ in second.calls], "fast pages are cached")
        check_epub(self.out)

    def test_marker_error_falls_back(self):
        self.convert(FakeEngine(fail_accurate={0}), keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["1"]["source"], "fast")
        self.assertEqual(report["pages"]["2"]["source"], "accurate")
        check_epub(self.out)

    def test_footnotes_are_epub_footnotes(self):
        self.convert(FakeEngine(notes=True))
        check_epub(self.out)
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertIn('<a epub:type="noteref" href="#fn1" id="fnref1"><sup>1</sup></a>', text)
        self.assertIn('<aside epub:type="footnote" class="note" id="fn1"><p><a href="#fnref1"><sup>1</sup></a> '
                      'Текст сноски 1.</p></aside>', text)
        self.assertIn('id="fn3"', text)

    def test_endnotes_linked_across_files(self):
        self.convert(FakeEngine(endnotes=True))
        check_epub(self.out)
        with zipfile.ZipFile(self.out) as zf:
            files = {n: zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml")}
        ref = next(n for n, t in files.items() if "enref1" in t and "noteref" in t)
        note = next(n for n, t in files.items() if 'id="en1"' in t)
        self.assertNotEqual(ref, note)
        self.assertIn(f'href="{Path(note).name}#en1"', files[ref])
        self.assertIn(f'href="{Path(ref).name}#enref1"', files[note])
        self.assertIn('epub:type="endnote"', files[note])

    def test_running_heads_left_out(self):
        self.convert(FakeEngine(fast_heads=True, fail_accurate={0}))
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertNotIn("ТЕСТОВАЯ КНИГА", text)
        for s in SENTENCES:
            self.assertIn(s, text)

    def test_page_lines_say_what_the_check_did(self):
        self.convert(FakeEngine(fail_accurate={0}))
        pages = [line for line in self.lines if line.startswith("📄")]
        self.assertEqual(len(pages), 3)
        self.assertIn("⚠️ fast reading used (accurate failed)", pages[0])
        self.assertIn("page 2/3 · ok · ", pages[1])

    def test_live_line_while_marker_reads(self):
        status = []
        self.convert(FakeEngine(), status=status.append)
        self.assertTrue(any(s.startswith("⚡ fast reading of pages 1–3") for s in status), status)
        self.assertTrue(any(s.startswith("🔍 reading pages 1–3") for s in status), status)
        self.assertEqual(status[-1], "", "the live line is cleared at the end")

    def test_time_of_each_page(self):
        clock = [1000.0]
        fake = FakeEngine(empty_once={2})
        fake.clock = clock
        with mock.patch.object(hy.time, "monotonic", lambda: clock[0]):
            self.convert(fake, keep_work=True)
        pages = [line for line in self.lines if line.startswith("📄")]
        self.assertIn("page 1/3 · ok · ⏱ 1:00 · ", pages[0])
        self.assertIn("⏱ 2:00 · ", pages[2], "a page read twice took its share of the batch and the second reading")
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual([report["pages"][k]["seconds"] for k in "123"], [60, 60, 120])
        log = (self.work() / "log.txt").read_text()
        self.assertIn("page 1: accurate", log)
        self.assertRegex(log, r"page 3: accurate .* 120 s")

    def test_fast_failure_split_in_halves(self):
        fake = FakeEngine(fail_fast={1})
        self.convert(fake, keep_work=True)
        fast_calls = [p for m, p in fake.calls if m == "fast"]
        self.assertEqual(fast_calls, [[0, 1, 2], [0], [1, 2], [1], [2]])
        self.assertTrue((self.work() / "fast" / "page-0003.html").read_text().strip())
        self.assertFalse((self.work() / "fast" / "page-0002.html").exists(), "a failure is not cached")

    def test_accurate_keeps_failing_stops_the_book(self):
        with mock.patch.object(hy, "ACC_BATCH", 1), mock.patch.object(hy, "FAIL_LIMIT", 2):
            with self.assertRaises(RuntimeError) as err:
                self.convert(FakeEngine(fail_accurate={0, 1, 2}))
        self.assertIn("again", str(err.exception))
        self.assertFalse(self.out.exists())
        self.assertEqual(list((self.work() / "merged").iterdir()), [], "pages are not cached as fast-only")

    def test_loop_after_retry_uses_fast(self):
        self.convert(FakeEngine(loop_accurate={1}), keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["2"]["source"], "fast")
        self.assertNotIn("и так далее и так далее", (self.work() / "merged" / "page-0002.html").read_text())

    def test_other_pdf_same_name_starts_over(self):
        with mock.patch.object(hy, "ACC_BATCH", 1):
            with self.assertRaises(KeyboardInterrupt):
                self.convert(FakeEngine(interrupt_on=2))
        make_pdf(self.pdf, y=500)                   # another file under the same name
        second = FakeEngine()
        self.convert(second)
        self.assertEqual(second.accurate_pages(), [0, 1, 2])

    def test_empty_page_without_report_is_redone(self):
        self.convert(FakeEngine(), keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        del report["pages"]["2"]
        (self.work() / "report.json").write_text(json.dumps(report))
        (self.work() / "merged" / "page-0002.html").write_text("")
        self.out.unlink()
        second = FakeEngine()
        self.convert(second, keep_work=True)
        self.assertEqual(second.accurate_pages(), [], "rebuilt from the cached accurate reading")
        self.assertIn(SENTENCES[1], (self.work() / "merged" / "page-0002.html").read_text())

    def test_empty_accurate_page_read_again(self):
        fake = FakeEngine(empty_once={1})
        self.convert(fake, keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["2"]["source"], "accurate")
        self.assertEqual(fake.accurate_pages(), [0, 1, 1, 2])

    def test_empty_accurate_page_is_not_kept(self):
        with mock.patch.object(hy, "FRONT_PAGES", 0):        # not a title page made a picture
            self.convert(FakeEngine(empty_accurate={1}), keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["2"]["source"], "fast")
        self.assertEqual(report["pages"]["2"]["reason"], "accurate found nothing")
        self.assertFalse((self.work() / "acc" / "page-0002.html").exists(), "a blank reading is a failure, not a result")

    def test_blank_pages_are_not_given_to_marker(self):
        """Marker fails on a blank page; one in a batch made it read the whole book again in halves."""
        pdf = make_pdf(self.dir / "blank.pdf", pages=SENTENCES + ["", ""], blank={3}, drawn={4})
        fake = FakeEngine(blank={3})
        fake_pages = lambda: sorted({p for _, ps in fake.calls for p in ps})
        sentences = SENTENCES + ["", "Пятая страница — рисунок."]
        with mock.patch(f"{__name__}.SENTENCES", sentences):
            self.convert(fake, pdf=pdf, keep_work=True)
        self.assertNotIn(3, fake_pages(), "the blank page is not read")
        self.assertIn(4, fake_pages(), "a drawing without text is read")
        self.assertFalse(any(len(ps) > 1 and 3 in ps for _, ps in fake.calls))
        report = json.loads((self.work(pdf) / "report.json").read_text())
        self.assertEqual(report["pages"]["4"]["source"], "empty")
        self.assertNotIn("4 (empty)", hy.to_check(report), "blank on purpose: nothing to check")
        self.assertIn("4/5 · empty page", "\n".join(self.lines))

    def test_title_page_accurate_cannot_read_is_a_picture(self):
        """A title page set as a design (accurate reads too little, fast loses letters and the layout)
        goes in as a picture of the page."""
        self.convert(FakeEngine(short_accurate={1}), keep_work=True)
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            pictures = [n for n in zf.namelist() if n.startswith("OEBPS/images/img")]
        self.assertNotIn(SENTENCES[1], text)
        self.assertIn(SENTENCES[2], text)
        self.assertEqual(len(pictures), 1)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["2"]["source"], "picture")
        self.assertNotIn("check:", self.convert(FakeEngine(), keep_work=True), "nothing left to check")

    def test_first_page_like_that_is_the_cover_only(self):
        self.convert(FakeEngine(short_accurate={0}), keep_work=True)
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            pictures = [n for n in zf.namelist() if n.startswith("OEBPS/images/img")]
        self.assertIn("images/cover.jpg", " ".join(zipfile.ZipFile(self.out).namelist()))
        self.assertEqual(pictures, [], "not the cover a second time")
        self.assertNotIn(SENTENCES[0], text)
        self.out.unlink()
        self.convert(FakeEngine(), keep_work=True)                 # built again from the cache
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertNotIn(SENTENCES[0], text)

    def test_pages_further_in_or_well_read_stay_text(self):
        with mock.patch.object(hy, "FRONT_PAGES", 1):
            self.convert(FakeEngine(short_accurate={1}), keep_work=True)       # page 2 is past the front
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertIn(SENTENCES[1], text)

    def test_accurate_reading_too_little_is_named(self):
        with mock.patch.object(hy, "FRONT_PAGES", 0):
            self.convert(FakeEngine(short_accurate={1}), keep_work=True)
        report = json.loads((self.work() / "report.json").read_text())
        self.assertEqual(report["pages"]["2"]["source"], "fast")
        self.assertEqual(report["pages"]["2"]["reason"], "accurate read too little")
        self.assertTrue(any("page 2/3 · ⚠️ fast reading used (accurate read too little)" in l for l in self.lines))

    def test_summary_names_pages_to_check(self):
        summary = self.convert(FakeEngine(fail_accurate={0}))
        self.assertIn("check: 1 (fast)", summary)

    def test_work_folder_removed(self):
        self.convert(FakeEngine())
        self.assertFalse(self.work().exists())

    def test_trial_of_the_first_pages(self):
        trial = self.out.parent / "Preview" / "book.epub"
        trial.parent.mkdir()
        first = FakeEngine()
        self.convert(first, out=trial, only={1, 2}, work_root=self.out.parent)
        self.assertEqual(first.accurate_pages(), [0, 1])
        self.assertEqual(sorted(p for m, ps in first.calls if m == "fast" for p in ps), [0, 1])
        check_epub(trial)
        with zipfile.ZipFile(trial) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertIn(SENTENCES[1], text)
        self.assertNotIn(SENTENCES[2], text)
        self.assertTrue(self.work().exists(), "the trial's pages are kept for the whole book")
        self.assertFalse((self.work() / "done").exists())
        # the whole book then reads only the pages the trial did not
        whole = FakeEngine()
        self.convert(whole)
        self.assertEqual(whole.accurate_pages(), [2])
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
        self.assertIn(SENTENCES[2], text)
        self.assertFalse(self.work().exists())

    def test_trial_of_one_page(self):
        trial = self.out.parent / "Preview" / "book.epub"
        trial.parent.mkdir()
        fake = FakeEngine()
        self.convert(fake, out=trial, only={3}, work_root=self.out.parent)
        self.assertEqual(fake.accurate_pages(), [2])
        check_epub(trial)
        with zipfile.ZipFile(trial) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            self.assertNotIn("OEBPS/images/cover.jpg", zf.namelist(), "no page 1, no cover")
        self.assertIn(SENTENCES[2], text)
        self.assertNotIn(SENTENCES[0], text)
        # the summary speaks of the trial's pages, not of everything in the work folder
        summary = self.convert(FakeEngine(fail_accurate={0}), out=trial, only={3}, work_root=self.out.parent)
        self.assertIn("accurate 1", summary)
        summary = self.convert(FakeEngine(fail_accurate={0}), out=trial, only={1}, work_root=self.out.parent)
        self.assertIn("pages: fast 1;", summary)

    def test_book_with_formulas(self):
        fake = FakeEngine(formula=True)
        convert = lambda formulas: [f'<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>{f}</mi></math>'
                                    for f, _ in formulas]
        self.convert(fake, mathml=convert)
        with zipfile.ZipFile(self.out) as zf:
            text = "".join(zf.read(n).decode() for n in zf.namelist() if n.endswith(".xhtml"))
            opf = zf.read("OEBPS/content.opf").decode()
        self.assertIn('<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>', text)
        self.assertRegex(opf, r'<item id="c\d+" href="[^"]+" media-type="application/xhtml\+xml" properties="mathml"/>')
        check_epub(self.out)

    def test_keep_work(self):
        self.convert(FakeEngine(), keep_work=True)
        self.assertTrue((self.work() / "report.json").exists())

    def test_stops_server_on_error(self):
        with self.assertRaises(KeyboardInterrupt):
            self.convert(FakeEngine(interrupt_on=0))
        self.assertTrue(self.stops)

    def test_unicode_file_name(self):
        pdf = make_pdf(self.dir / 'Книга "тест" 1.pdf')
        out = self.out.parent / 'Книга "тест" 1.epub'
        self.convert(FakeEngine(), pdf=pdf, out=out)
        check_epub(out)


class BookPartsTest(unittest.TestCase):
    def test_text_page_is_not_a_cover(self):
        words = " ".join(["слово"] * 80)
        self.assertFalse(hy.first_page_is_cover(f"<h1>СОДЕРЖАНИЕ</h1><p>{words}</p>", big_picture=True))
        self.assertTrue(hy.first_page_is_cover("<h1>Тихие реки</h1><p>Мила Фенвик</p>", big_picture=True))
        self.assertFalse(hy.first_page_is_cover("<p>Мила Фенвик</p>", big_picture=False))

    def test_layer_contents_preferred(self):
        from pdf2epub import Block, Toc, TocEntry
        blocks = [Block("page", level=1, page=1), Block("h", "СОДЕРЖАНИЕ", "СОДЕРЖАНИЕ", 1, page=1),
                  Block("table", rows=[["<b>ПРЕДИСЛОВИЕ</b>", "<b>15</b>"]], page=1),
                  Block("page", level=2, page=2),
                  Block("h", "ШАГ 1. ПОЛИВ 37", "ШАГ 1. ПОЛИВ 37", 2, page=2),
                  Block("p", "Совет 1. Утро 42", "Совет 1. Утро 42", page=2),
                  Block("page", level=3, page=3), Block("p", "Текст главы.", "Текст главы.", page=3)]
        toc = Toc("СОДЕРЖАНИЕ", [TocEntry("ПРЕДИСЛОВИЕ", 15, 0, 1), TocEntry("ШАГ 1. ПОЛИВ", 37, 0, 2),
                                 TocEntry("Совет 1. Утро", 42, 10, 2, level=2)], [1, 2])
        tocs = hy.place_tocs(blocks, [toc])
        self.assertEqual(tocs, [toc])
        self.assertEqual([b.kind for b in blocks], ["page", "h", "toc", "page", "page", "p"])

    def test_formulas_in_contents_from_marker(self):
        """The PDF text layer has old OCR in place of formulas; Marker's reading has them."""
        from pdf2epub import Block, Toc, TocEntry
        blocks = [Block("page", level=1, page=1), Block("h", "Содержание", "Содержание", 1, page=1),
                  Block("table", rows=[["Оптимизация .....", "160"],
                                       ["Градиентный спуск <math>\\bar{\\omega}^{i+1}</math> .....", "164"],
                                       ["<b>Регуляризация</b>", "<b>179</b>"],
                                       ["<b>ГЛАВА 5. Компьютерное зрение .....</b>", "<b>189</b>"]], page=1),
                  Block("page", level=2, page=2), Block("p", "Текст.", "Текст.", page=2)]
        toc = Toc("Содержание", [TocEntry("Оптимизация", 160, 0, 1), TocEntry("Градиентный спуск ro;+i = fЬ;", 164, 0, 1),
                                 TocEntry("Регуляризация", 179, 0, 1),
                                 TocEntry("ГЛАВА 5. Компьютерн\ufffdе зрение", 189, 0, 1)], [1])
        hy.place_tocs(blocks, [toc])
        self.assertEqual([e.html for e in toc.entries],
                         ["", "Градиентный спуск <math>\\bar{\\omega}^{i+1}</math>", "", ""])
        self.assertEqual(toc.entries[3].title, "ГЛАВА 5. Компьютерное зрение",
                         "a letter the text layer lost (\ufffd) comes from Marker's reading")
        self.assertEqual(toc.entries[0].title, "Оптимизация")

    def test_formulas_become_mathml(self):
        from pdf2epub import Block, Toc, TocEntry
        p = Block("p", "Пусть <math>x^2</math> растет.", "Пусть x^2 растет.", page=1)
        h = Block("h", "Градиент <math>\\nabla L</math>", "Градиент \\nabla L", 2, page=1)
        bad = Block("list", items=["<math display=\"block\">\\frac{a}{</math>"], page=1)
        toc = Toc("Содержание", [TocEntry("Градиент", 5, 0, 1)], [1])
        toc.entries[0].html = "Градиент <math>\\nabla L</math>"
        asked = []

        def convert(formulas):
            asked.extend(formulas)
            return [None if f.startswith("\\frac") else f'<math xmlns="m" display="{"block" if d else "inline"}">'
                    f"<mi>{f[-1]}</mi></math>" for f, d in formulas]
        hy.apply_math([p, h, bad], [toc], convert)
        self.assertEqual(sorted(set(asked)), [("\\frac{a}{", True), ("\\nabla L", False), ("x^2", False)])
        self.assertEqual(p.html, 'Пусть <math xmlns="m" display="inline"><mi>2</mi></math> растет.')
        self.assertIn('<mi>L</mi>', h.html)
        self.assertEqual(h.text, "Градиент L", "the plain title (book navigation) has the formula's letters")
        self.assertIn('<mi>L</mi>', toc.entries[0].html)
        self.assertEqual(bad.items[0], '<span class="tex">\\frac{a}{</span>', "a formula that fails stays readable")

    def test_marker_contents_without_layer(self):
        from pdf2epub import Block
        blocks = [Block("h", "СОДЕРЖАНИЕ", "СОДЕРЖАНИЕ", 1, page=1),
                  Block("table", rows=[["<b>ПРЕДИСЛОВИЕ</b>", "<b>15</b>"], ["Как читать", "17"],
                                       ["<b>ВВЕДЕНИЕ</b>", "<b>19</b>"], ["Почва", "21"]], page=1)]
        tocs = hy.place_tocs(blocks, [])
        self.assertEqual([e.title for e in tocs[0].entries], ["ПРЕДИСЛОВИЕ", "Как читать", "ВВЕДЕНИЕ", "Почва"])


class RightAlignedTest(unittest.TestCase):
    @staticmethod
    def page(rows):
        """A page of lines (left, right, text), 11 pt, one under another."""
        from pdf2epub import Line, Page, Piece
        page = Page(16, 480, 640)
        for i, (left, right, text) in enumerate(rows):
            page.lines.append(Line([Piece(60 + 14 * i, left, right - left, 11, 11.0, [(text, False, False)])], 16))
        return page

    def test_signature_under_a_quote_goes_right(self):
        from pdf2epub import Block
        page = self.page([(55, 440, "Рынки технологий подобны реке, где отдельные участки движутся быстрее"),
                          (40, 440, "других. Хала сделала математику увлекательной для широкого круга"),
                          (40, 300, "участников будущего!"),
                          (330, 440, "— Мила Фенвик,"),
                          (180, 440, "руководитель группы по аналитике рисков"),
                          (260, 440, "банк Тихие Реки"),
                          (55, 440, "Книги по искусственному интеллекту обычно представляют собой либо"),
                          (40, 250, "рукописи для специалистов.")])
        quote = Block("p", "Рынки ...", "Рынки технологий подобны реке, где отдельные участки движутся быстрее "
                      "других. Хала сделала математику увлекательной для широкого круга участников будущего!", page=16)
        sign = Block("p", "— <i>Мила Фенвик,<br/>руководитель...</i>",
                     "— Мила Фенвик, руководитель группы по аналитике рисков банк Тихие Реки", page=16)
        after = Block("p", "Книги ...", "Книги по искусственному интеллекту обычно представляют собой либо "
                      "рукописи для специалистов.", page=16)
        hy.align_right([Block("page", level=16, page=16), quote, sign, after], [page], 11.0)
        self.assertEqual([quote.align, sign.align, after.align], ["", "right", ""])

    def test_no_text_layer_changes_nothing(self):
        from pdf2epub import Block, Page
        b = Block("p", "Текст", "Текст", page=1)
        hy.align_right([b], [Page(1, 480, 640)], 11.0)
        self.assertEqual(b.align, "")


class PageNoteTest(unittest.TestCase):
    def test_notes(self):
        self.assertEqual(hy.page_note("accurate", {"fixed_words": 0, "inserted_blocks": 0, "coverage": 1.0}, False), "ok")
        self.assertEqual(hy.page_note("spliced", {"fixed_words": 2, "inserted_blocks": 1, "coverage": 0.98}, True),
                         "read twice, 2 words fixed, 1 missing piece put back")
        self.assertEqual(hy.page_note("accurate", {"fixed_words": 1, "inserted_blocks": 0, "coverage": 0.79}, False),
                         "1 word fixed, ⚠️ 79% read, check it")
        self.assertEqual(hy.page_note("fast", {"reason": "accurate repeated itself"}, True),
                         "⚠️ fast reading used (accurate repeated itself)")
        self.assertEqual(hy.page_note("empty", {}, False), "empty page")

    def test_short_time(self):
        self.assertEqual([hy._short(s) for s in (0, 7.4, 59.6, 61, 3725)], ["0:00", "0:07", "1:00", "1:01", "1:02:05"])


class TimeTest(unittest.TestCase):
    def test_estimate(self):
        import pdf2epub
        self.assertEqual(pdf2epub.estimate(9), "~9 min")
        self.assertEqual(pdf2epub.estimate(228), "~3.8 h")

    def test_time_left_counts_only_recognised_pages(self):
        self.assertEqual(hy.time_left(elapsed=240, recognised=2, remaining=3), 360)
        self.assertIsNone(hy.time_left(elapsed=0.1, recognised=0, remaining=5))


if __name__ == "__main__":
    unittest.main()
