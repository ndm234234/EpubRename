"""Starts each Python server (the two .sh launchers, library_sync --serve) and runs the HTTP contract."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
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


def test_timing_script_against_the_python_server(tmp_path):
    # tests/sync_timing.py (the timing step of windows-sync-checklist.md) works, and the Python
    # server stays well under its limit with a big account
    from sync_timing import run
    lib = _library(tmp_path)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(lib / "library_sync.py"), "--serve", str(lib), "--port", str(port)],
                            cwd=lib, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _find(lib, [port], proc)
        code = (lib / ".sync" / "code.txt").read_text(encoding="utf-8").strip()
        res = run(f"http://127.0.0.1:{port}", code, kb=1024, log=lambda *_: None)
        assert [r[0] for r in res] == ["GET /__sync/data", "PUT /__sync/data", "POST /__sync/place"]
        assert all(r[1] in (200, 204) and r[2] < 1.0 for r in res), res
        data = next((lib / ".sync" / "data").glob("*.json")).read_text(encoding="utf-8")
        assert data.startswith('{"version": 7, "doc": {')   # 1 + 3 PUTs + 3 newer places
    finally:
        proc.terminate()
        proc.wait(timeout=10)


ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _ask(base, method, path, ua, body=None, token=None):
    req = urllib.request.Request(base + path, method=method, headers={"User-Agent": ua},
                                 data=None if body is None else json.dumps(body).encode())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = r.read()
            return r.status, (json.loads(out) if out and r.headers.get_content_type() == "application/json" else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def test_network_window_names_who_is_signed_in(tmp_path):
    """The window shows each device's account: on sign-in, on its first sync, gone on sign-out."""
    lib = _library(tmp_path)
    (lib / "Jane Doe - A Quiet Harbour.epub").write_bytes(b"PK")
    env = dict(os.environ, LIBRARY_NO_BROWSER="1")
    proc = subprocess.Popen(["sh", str(lib / "Open Library (network).sh")], cwd=lib, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    jane, pw = "jane.doe@example.com", "pw-" + "q" * 10
    try:
        base = f"http://127.0.0.1:{_find(lib, range(48865, 48885), proc)}"
        code = (lib / ".sync" / "code.txt").read_text(encoding="utf-8").strip()
        s, out = _ask(base, "POST", "/__sync/register", ANDROID, {"login": jane, "password": pw, "code": code})
        assert s == 200
        token = out["token"]
        assert _ask(base, "GET", "/Jane%20Doe%20-%20A%20Quiet%20Harbour.epub", ANDROID)[0] == 200
        # another device already signed in (before the window was started): known by its first sync
        assert _ask(base, "GET", "/__sync/data", WINDOWS, token=token)[0] == 200
        assert _ask(base, "GET", "/__sync/data", WINDOWS, token=token)[0] == 200
        assert _ask(base, "POST", "/__sync/login", WINDOWS, {"login": jane, "password": "pw-wrong"})[0] != 200
        assert _ask(base, "POST", "/__sync/logout", ANDROID, {}, token=token)[0] == 204
        assert _ask(base, "GET", "/Jane%20Doe%20-%20A%20Quiet%20Harbour.epub", ANDROID)[0] == 200
        time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            log, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            log, _ = proc.communicate()
    lines = log.splitlines()
    def one(*parts):
        found = [ln for ln in lines if all(p in ln for p in parts)]
        assert found, f"no line with {parts} in:\n{log}"
        return found
    one("Android · Chrome", "registration ok (200)", jane)
    one("Android · Chrome", jane, "«Jane Doe - A Quiet Harbour»")
    assert len(one("👤", "Windows · Chrome", jane)) == 1   # once, not on every sync
    one("Windows · Chrome", "sign-in refused", jane)
    one("👤", "Android · Chrome", "signed out")
    assert any("«Jane Doe - A Quiet Harbour»" in ln and "Android" in ln and jane not in ln for ln in lines), log
    assert "pw-" not in log and token not in log   # never a password or a device key
