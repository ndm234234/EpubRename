"""Starts each Python server (the two .sh launchers, library_sync --serve) and runs the HTTP contract."""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_contract import run_contract  # noqa: E402

LAUNCHERS = Path(__file__).resolve().parent.parent / "launchers"


def _library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for f in LAUNCHERS.iterdir():
        if f.is_file():
            shutil.copy(f, lib / f.name)
    (lib / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    return lib


def _find(lib, ports, proc):
    deadline = time.time() + 20
    while time.time() < deadline:
        assert proc.poll() is None, "server exited early"
        for p in ports:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{p}/__library_root", timeout=2) as r:
                    if r.read().decode() == str(lib):
                        return p
            except Exception:
                pass
        time.sleep(0.2)
    raise AssertionError("server did not come up")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("kind", ["Open Library.sh", "Open Library (network).sh", "serve"])
def test_python_launchers_pass_contract(tmp_path, kind):
    lib = _library(tmp_path)
    env = dict(os.environ, LIBRARY_NO_BROWSER="1")
    if kind == "serve":
        port = _free_port()
        cmd, ports = [sys.executable, str(lib / "library_sync.py"), "--serve", str(lib), "--port", str(port)], [port]
    else:
        ports = range(48765, 48785) if kind == "Open Library.sh" else range(48865, 48885)
        cmd = ["sh", str(lib / kind)]
    code = None
    proc = subprocess.Popen(cmd, cwd=lib, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        port = _find(lib, ports, proc)
        code = (lib / ".sync" / "code.txt").read_text(encoding="utf-8").strip()
        assert run_contract(f"http://127.0.0.1:{port}", code) == []
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    if kind != "serve":
        assert "Library code" in out and code in out
    if "network" in kind:
        assert "registration ok" in out and "sign-in ok" in out and "refused" in out
        assert "pw-" not in out   # passwords are never logged
