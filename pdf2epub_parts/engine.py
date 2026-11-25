"""The recognition engine of pdf2epub: Marker (with Surya) and llama.cpp.

They need Python 3.12, PyTorch and a few GB of models, so they live in their own
folder (ENGINE_DIR), never in the project and never in the system Python. This
module installs them on first use and runs them; it uses only the standard library.
"""

import hashlib
import html
import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ENGINE_DIR = Path.home() / ".local" / "share" / "pdf2epub"

# Exact versions: a newer Marker may change the HTML the rest of pdf2epub reads
UV_VERSION = "0.13.9"
PYTHON_VERSION = "3.12"
TORCH = "torch==2.15.0"
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
MARKER = ("marker-pdf==2.2.0", "surya-ocr==0.23.0")
LLAMA_RELEASE = "b12681"
LLAMA_URL = (f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_RELEASE}/"
             f"llama-{LLAMA_RELEASE}-bin-ubuntu-x64.tar.gz")
LLAMA_SHA256 = "61f04060b691efc99708ff9ca59564a24de3073543f35b1c084e1b8cb73e6da0"


@dataclass(frozen=True)
class Engine:
    python: Path
    marker_single: Path
    llama_server: Path


def _paths(root: Path) -> Engine | None:
    server = next((p for p in sorted((root / "llama").glob("**/llama-server")) if p.is_file()), None)
    if server is None:
        return None
    return Engine(root / "venv" / "bin" / "python", root / "venv" / "bin" / "marker_single", server)


def find_engine(root: Path = ENGINE_DIR) -> Engine | None:
    """The installed engine, or None when it is missing or not fully installed."""
    if not (root / ".complete").exists():
        return None
    engine = _paths(root)
    if engine is None or not all(p.exists() for p in (engine.python, engine.marker_single, engine.llama_server)):
        return None
    return engine


class _StepFailed(Exception):
    pass


def ensure_engine(log: Callable[[str], None], root: Path = ENGINE_DIR,
                  runner=subprocess.run, fetch=urllib.request.urlretrieve) -> Engine | None:
    """Install the engine if needed (one time, ~3 GB) and return it; None if that fails.

    Every finished step leaves a stamp, so an interrupted or failed install continues
    where it stopped the next time.
    """
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        log(f"   The recognition engine works on Linux (x86-64) only for now, not on "
            f"{platform.system()} {platform.machine()}")
        return None
    engine = find_engine(root)
    if engine is not None:
        return engine
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_CACHE_DIR=str(root / "uv-cache"))
    uv = root / "bootstrap" / "bin" / "uv"
    python = root / "venv" / "bin" / "python"

    def run(args):
        res = runner([str(a) for a in args], env=env, capture_output=True, check=False)
        if res.returncode != 0:
            tail = (res.stderr or b"").decode(errors="replace").strip().splitlines()[-3:]
            raise _StepFailed(" ".join(tail) or f"exit code {res.returncode}")

    def get_llama():
        part = root / "llama.tar.gz.part"
        fetch(LLAMA_URL, str(part))
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        if digest != LLAMA_SHA256:
            part.unlink(missing_ok=True)
            raise _StepFailed(f"llama.cpp download has a wrong checksum ({digest[:12]}…)")
        target = root / "llama"
        shutil.rmtree(target, ignore_errors=True)
        with tarfile.open(part) as tf:
            tf.extractall(target, filter="data")
        part.unlink()
        if _paths(root) is None:
            raise _StepFailed("llama-server not found in the llama.cpp archive")

    steps = [
        ("bootstrap", "a helper for installing Python", lambda: (
            run([sys.executable, "-m", "venv", root / "bootstrap"]),
            run([root / "bootstrap" / "bin" / "pip", "install", "-q", f"uv=={UV_VERSION}"]))),
        ("python", f"Python {PYTHON_VERSION}", lambda: run(
            [uv, "venv", "-q", "--python", PYTHON_VERSION, root / "venv"])),
        ("torch", "PyTorch (CPU)", lambda: run(
            [uv, "pip", "install", "-q", "--python", python, TORCH, "--index-url", TORCH_INDEX])),
        ("marker", "Marker and Surya", lambda: run(
            [uv, "pip", "install", "-q", "--python", python, *MARKER])),
        ("llama", f"llama.cpp {LLAMA_RELEASE}", get_llama),
    ]
    todo = [s for s in steps if not (root / f".step-{s[0]}").exists()]
    if todo:
        log("⬇️  Installing the recognition engine (one time, ~3 GB; models are downloaded on first use)")
    for name, what, action in todo:
        log(f"   • {what} …")
        try:
            action()
        except (_StepFailed, OSError, tarfile.TarError) as e:
            log(f"❌ Could not install {what}: {e}")
            if name == "bootstrap":
                log("   Python can't make virtual environments here: on Debian/Ubuntu run "
                    "`sudo apt install python3-venv`, then start again")
            return None
        (root / f".step-{name}").write_text("")
    engine = _paths(root)
    if engine is None:
        log("❌ The engine is incomplete after installing")
        return None
    (root / ".complete").write_text("")
    return find_engine(root)


# ---------------------------------------------------------------- running Marker

TIMEOUT_START = 1800    # s: loading (on the first run also downloading) the models
TIMEOUT_PAGE = 600      # s per page; accurate normally needs about 120


class MarkerError(Exception):
    """Marker failed or produced nothing."""


def split_pages(html_text: str) -> dict[int, str]:
    """Marker's paginated HTML -> {page index (0-based): inner HTML of that page}."""
    pages: dict[int, str] = {}
    opener = re.compile(r"<div\s+class=['\"]page['\"]\s+data-page-id=['\"](\d+)['\"]\s*>")
    tag = re.compile(r"<(/?)div\b[^>]*>", re.I)
    pos = 0
    while (m := opener.search(html_text, pos)):
        depth, i = 1, m.end()
        while depth and (t := tag.search(html_text, i)):
            depth += -1 if t.group(1) else 1
            i = t.end()
        end = t.start() if depth == 0 else len(html_text)
        pages[int(m.group(1))] = html_text[m.end():end].strip()
        pos = i
    return pages


def run_marker(engine: Engine, pdf: Path, mode: str, pages: list[int], out_dir: Path,
               runner=subprocess.run) -> dict[int, str]:
    """Recognise the given pages (0-based) of the PDF in "fast" or "accurate" mode.

    Pictures Marker cuts out are moved to out_dir/images and the pages point to them.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"marker-{mode}-", dir=out_dir))
    try:
        args = [engine.marker_single, pdf, "--mode", mode, "--force_ocr", "--strip_existing_ocr",
                "--paginate_output", "--output_format", "html",
                "--page_range", ",".join(str(p) for p in pages), "--output_dir", tmp]
        env = dict(os.environ, LLAMA_CPP_BINARY=str(engine.llama_server))
        try:
            res = runner([str(a) for a in args], env=env, capture_output=True, check=False,
                         timeout=TIMEOUT_START + TIMEOUT_PAGE * len(pages))
        except subprocess.TimeoutExpired:
            raise MarkerError(f"Marker {mode} took too long on pages {pages}") from None
        found = sorted(tmp.glob("*/*.html"))
        if res.returncode != 0 or not found:
            tail = (res.stderr or b"").decode(errors="replace").strip().splitlines()[-3:]
            raise MarkerError(f"Marker {mode} failed on pages {pages}: {' '.join(tail) or res.returncode}")
        html_text = found[0].read_text(encoding="utf-8")
        images = out_dir / "images"
        for f in found[0].parent.iterdir():
            if f.suffix.lower() in (".jpeg", ".jpg", ".png", ".gif", ".webp"):
                images.mkdir(exist_ok=True)
                target = images / f"{mode}-{f.name}"
                shutil.move(str(f), target)
                html_text = re.sub(r"""src=(['"])""" + re.escape(f.name) + r"\1",
                                   lambda _: f'src="{html.escape(str(target))}"', html_text)
        return split_pages(html_text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stop_llama_server(engine: Engine) -> None:
    """Stop every llama-server started from this engine (Marker may leave one running)."""
    target = str(engine.llama_server.resolve()) if engine.llama_server.exists() else str(engine.llama_server)
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if os.readlink(proc / "exe") != target:
                continue
            pid = int(proc.name)
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            continue
        for _ in range(50):
            if not Path(f"/proc/{pid}").exists():
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
