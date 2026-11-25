"""Tests for pdf2epub_parts.engine: installing and running the recognition engine."""

import hashlib
import io
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pdf2epub_parts.engine as eng  # noqa: E402


def fake_tarball() -> bytes:
    """A llama.cpp-like archive with a llama-server inside."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho llama\n"
        info = tarfile.TarInfo("llama-b12681/llama-server")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeRunner:
    """Records commands and creates the files a real install would leave behind."""

    def __init__(self, root: Path, fail_on: str | None = None):
        self.root, self.fail_on, self.calls = root, fail_on, []

    def __call__(self, args, **kw):
        args = [str(a) for a in args]
        self.calls.append((args, kw.get("env")))
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            self.fail_on = None                       # fails only once
            return subprocess.CompletedProcess(args, 1, b"", b"network error")
        if "-m venv" in joined:
            (self.root / "bootstrap/bin").mkdir(parents=True, exist_ok=True)
            (self.root / "bootstrap/bin/uv").write_text("")
        if " venv " in joined and "--python" in args:
            (self.root / "venv/bin").mkdir(parents=True, exist_ok=True)
            (self.root / "venv/bin/python").write_text("")
        if "marker-pdf==" in joined:
            (self.root / "venv/bin/marker_single").write_text("")
        return subprocess.CompletedProcess(args, 0, b"", b"")


class FakeFetch:
    def __init__(self, data: bytes):
        self.data, self.urls = data, []

    def __call__(self, url, filename):
        self.urls.append(url)
        Path(filename).write_bytes(self.data)
        return filename, None


class EngineInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "engine"
        self.tar = fake_tarball()
        patcher = mock.patch.object(eng, "LLAMA_SHA256", hashlib.sha256(self.tar).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.log = []

    def install(self, runner=None, fetch=None):
        runner = runner or FakeRunner(self.root)
        fetch = fetch or FakeFetch(self.tar)
        return eng.ensure_engine(self.log.append, root=self.root, runner=runner, fetch=fetch), runner, fetch

    def test_find_engine_none_without_complete_marker(self):
        self.assertIsNone(eng.find_engine(self.root))

    def test_ensure_engine_installs_pinned_versions(self):
        engine, runner, fetch = self.install()
        commands = " ".join(" ".join(args) for args, _ in runner.calls)
        for pinned in ("marker-pdf==2.2.0", "surya-ocr==0.23.0", "torch==2.15.0",
                       "https://download.pytorch.org/whl/cpu", "--python 3.12", "uv==0.13.9"):
            self.assertIn(pinned, commands)
        self.assertTrue(fetch.urls[0].endswith("llama-b12681-bin-ubuntu-x64.tar.gz"))
        self.assertIsInstance(engine, eng.Engine)
        self.assertTrue((self.root / ".complete").exists())
        self.assertTrue(engine.llama_server.is_file())
        self.assertEqual(eng.find_engine(self.root), engine)

    def test_ensure_engine_rejects_bad_checksum(self):
        engine, _, _ = self.install(fetch=FakeFetch(b"not the archive"))
        self.assertIsNone(engine)
        self.assertTrue(any("checksum" in line for line in self.log))
        self.assertFalse((self.root / ".complete").exists())

    def test_ensure_engine_is_idempotent(self):
        self.install()
        runner, fetch = FakeRunner(self.root), FakeFetch(self.tar)
        engine, _, _ = self.install(runner, fetch)
        self.assertIsNotNone(engine)
        self.assertEqual(runner.calls, [])
        self.assertEqual(fetch.urls, [])

    def test_failed_venv_suggests_package(self):
        engine, _, _ = self.install(runner=FakeRunner(self.root, fail_on="-m venv"))
        self.assertIsNone(engine)
        self.assertIn("python3-venv", "\n".join(self.log))

    def test_other_system_uses_quick_mode(self):
        runner = FakeRunner(self.root)
        with mock.patch("platform.system", return_value="Windows"):
            engine, _, _ = self.install(runner=runner)
        self.assertIsNone(engine)
        self.assertEqual(runner.calls, [], "nothing is installed")
        self.assertIn("Linux", "\n".join(self.log))

    def test_failed_step_resumes(self):
        engine, runner, _ = self.install(runner=FakeRunner(self.root, fail_on="marker-pdf=="))
        self.assertIsNone(engine)
        runner2 = FakeRunner(self.root)
        engine, _, _ = self.install(runner=runner2)
        self.assertIsNotNone(engine)
        joined = [" ".join(a) for a, _ in runner2.calls]
        self.assertFalse(any("-m venv" in c for c in joined), "bootstrap must not repeat")
        self.assertFalse(any("torch==" in c for c in joined), "torch must not repeat")
        self.assertTrue(any("marker-pdf==" in c for c in joined))


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class MarkerRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.engine = eng.Engine(self.dir / "py", self.dir / "marker_single", self.dir / "llama-server")
        self.pdf = self.dir / "Книга \"тест\" 1.pdf"
        self.pdf.write_bytes(b"%PDF-1.4")

    def fake_marker(self, code=0):
        calls = []

        def runner(args, **kw):
            args = [str(a) for a in args]
            calls.append((args, kw.get("env")))
            if code == 0:
                out = Path(args[args.index("--output_dir") + 1]) / self.pdf.stem
                out.mkdir(parents=True, exist_ok=True)
                (out / (self.pdf.stem + ".html")).write_text((FIXTURES / "paginated.html").read_text())
                (out / "_page_5_Figure_1.jpeg").write_bytes(b"\xff\xd8jpeg")
            return subprocess.CompletedProcess(args, code, b"", b"boom")
        return runner, calls

    def test_split_pages(self):
        pages = eng.split_pages((FIXTURES / "paginated.html").read_text())
        self.assertEqual(set(pages), {4, 5})
        self.assertIn("Крепко прижать", pages[4])
        self.assertIn("Вложенный блок", pages[4])
        self.assertNotIn("пятой", pages[4])
        self.assertIn("<b>жирным</b>", pages[5])
        self.assertNotIn("Крепко", pages[5])

    def test_run_marker_command(self):
        runner, calls = self.fake_marker()
        pages = eng.run_marker(self.engine, self.pdf, "accurate", [3, 4], self.dir / "work", runner=runner)
        args, env = calls[0]
        for flag in ("--mode", "accurate", "--force_ocr", "--strip_existing_ocr", "--paginate_output",
                     "--page_range", "3,4", str(self.pdf)):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--output_format") + 1], "html")
        self.assertEqual(env["LLAMA_CPP_BINARY"], str(self.engine.llama_server))
        self.assertEqual(set(pages), {4, 5})
        img = re.search(r'src="([^"]+)"', pages[5]).group(1)
        self.assertTrue(Path(img).is_file(), img)
        self.assertEqual(Path(img).parent, self.dir / "work" / "images")
        self.assertTrue(Path(img).name.startswith("accurate-"), "fast and accurate pictures must not mix")

    def test_run_marker_timeout(self):
        def runner(args, **kw):
            self.assertGreater(kw.get("timeout") or 0, 0)
            raise subprocess.TimeoutExpired(args, kw["timeout"])
        with self.assertRaises(eng.MarkerError):
            eng.run_marker(self.engine, self.pdf, "accurate", [0], self.dir / "work", runner=runner)

    def test_run_marker_error(self):
        runner, _ = self.fake_marker(code=1)
        with self.assertRaises(eng.MarkerError):
            eng.run_marker(self.engine, self.pdf, "fast", [0], self.dir / "work", runner=runner)


if __name__ == "__main__":
    unittest.main()
