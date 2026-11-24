"""Tests for pdf2epub_hybrid: the per-book pipeline with a fake recognition engine."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pdf2epub_hybrid as hy  # noqa: E402
from pdf2epub import check_epub  # noqa: E402
from pdf2epub_engine import Engine, MarkerError  # noqa: E402

SENTENCES = ["Первая страница рассказывает о том, как дети растут и учатся новому каждый день.",
             "Вторая страница продолжает рассказ о воспитании и о том, что важно родителям.",
             "Третья страница подводит итог и завершает эту короткую тестовую книгу."]


def make_pdf(path: Path, pages=SENTENCES) -> Path:
    """A tiny text PDF, one Helvetica line per page (latin transliteration is enough for the layer)."""
    objs = []

    def add(o):
        objs.append(o)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = []
    for n, _ in enumerate(pages):
        data = f"BT /F1 12 Tf 72 700 Td (Page {n + 1} text line for the layer) Tj ET".encode()
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

    def __init__(self, fail_accurate=(), interrupt_on=None):
        self.calls, self.fail_accurate, self.interrupt_on = [], set(fail_accurate), interrupt_on

    def __call__(self, engine, pdf, mode, pages, out_dir):
        self.calls.append((mode, list(pages)))
        if mode == "accurate":
            if self.interrupt_on is not None and self.interrupt_on in pages:
                self.interrupt_on = None
                raise KeyboardInterrupt
            if set(pages) & self.fail_accurate:
                raise MarkerError("boom")
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


if __name__ == "__main__":
    unittest.main()
