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

SENTENCES = ["Первая страница рассказывает о том, как дети растут и учатся новому каждый день.",
             "Вторая страница продолжает рассказ о воспитании и о том, что важно родителям.",
             "Третья страница подводит итог и завершает эту короткую тестовую книгу."]


def make_pdf(path: Path, pages=SENTENCES, y=700) -> Path:
    """A tiny text PDF, one Helvetica line per page (latin transliteration is enough for the layer)."""
    objs = []

    def add(o):
        objs.append(o)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = []
    for n, _ in enumerate(pages):
        data = f"BT /F1 12 Tf 72 {y} Td (Page {n + 1} text line for the layer) Tj ET".encode()
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

    def __init__(self, fail_accurate=(), interrupt_on=None, fail_fast=(), loop_accurate=()):
        self.calls, self.fail_accurate, self.interrupt_on = [], set(fail_accurate), interrupt_on
        self.fail_fast, self.loop_accurate = set(fail_fast), set(loop_accurate)

    def __call__(self, engine, pdf, mode, pages, out_dir):
        self.calls.append((mode, list(pages)))
        if mode == "accurate":
            if self.interrupt_on is not None and self.interrupt_on in pages:
                self.interrupt_on = None
                raise KeyboardInterrupt
            if set(pages) & self.fail_accurate:
                raise MarkerError("boom")
        elif set(pages) & self.fail_fast:
            raise MarkerError("boom")
        if mode == "accurate" and set(pages) & self.loop_accurate:
            return {i: f"<h1>Глава {i + 1}</h1><p>{SENTENCES[i]}</p><p>" + " ".join(["и так далее"] * 40) + "</p>"
                    for i in pages}
        tag = "<b>Внимание.</b> " if mode == "accurate" else ""
        return {i: f"<h1>Глава {i + 1}</h1><p>{tag}{SENTENCES[i]}</p>" for i in pages}

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

    def convert(self, fake, keep_work=False, pdf=None, out=None):
        return hy.convert_hybrid(pdf or self.pdf, out or self.out, "Book", "Author", "ru", self.engine, keep_work,
                                 self.lines.append, run=fake, stop=self.stops.append)

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

    def test_summary_names_pages_to_check(self):
        summary = self.convert(FakeEngine(fail_accurate={0}))
        self.assertIn("check: 1 (fast)", summary)

    def test_work_folder_removed(self):
        self.convert(FakeEngine())
        self.assertFalse(self.work().exists())

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
                  Block("h", "ПРИНЦИП 1. КЛЮЧИ 37", "ПРИНЦИП 1. КЛЮЧИ 37", 2, page=2),
                  Block("p", "Ключ 1. Оценка 42", "Ключ 1. Оценка 42", page=2),
                  Block("page", level=3, page=3), Block("p", "Текст главы.", "Текст главы.", page=3)]
        toc = Toc("СОДЕРЖАНИЕ", [TocEntry("ПРЕДИСЛОВИЕ", 15, 0, 1), TocEntry("ПРИНЦИП 1. КЛЮЧИ", 37, 0, 2),
                                 TocEntry("Ключ 1. Оценка", 42, 10, 2, level=2)], [1, 2])
        tocs = hy.place_tocs(blocks, [toc])
        self.assertEqual(tocs, [toc])
        self.assertEqual([b.kind for b in blocks], ["page", "h", "toc", "page", "page", "p"])

    def test_marker_contents_without_layer(self):
        from pdf2epub import Block
        blocks = [Block("h", "СОДЕРЖАНИЕ", "СОДЕРЖАНИЕ", 1, page=1),
                  Block("table", rows=[["<b>ПРЕДИСЛОВИЕ</b>", "<b>15</b>"], ["Как читать", "17"],
                                       ["<b>ВВЕДЕНИЕ</b>", "<b>19</b>"], ["Симптомы", "21"]], page=1)]
        tocs = hy.place_tocs(blocks, [])
        self.assertEqual([e.title for e in tocs[0].entries], ["ПРЕДИСЛОВИЕ", "Как читать", "ВВЕДЕНИЕ", "Симптомы"])


class TimeTest(unittest.TestCase):
    def test_estimate(self):
        import pdf2epub
        self.assertEqual(pdf2epub.estimate(9), "~18 min")
        self.assertEqual(pdf2epub.estimate(228), "~7.6 h")

    def test_time_left_counts_only_recognised_pages(self):
        self.assertEqual(hy.time_left(elapsed=240, recognised=2, remaining=3), 360)
        self.assertIsNone(hy.time_left(elapsed=0.1, recognised=0, remaining=5))


if __name__ == "__main__":
    unittest.main()
