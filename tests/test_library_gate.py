"""The sign-in gate (library_sync --serve --gate): nothing but the sign-in page without a sign-in."""

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

LAUNCHERS = Path(__file__).resolve().parent.parent / "launchers"
FOREIGN = "library.example.com"   # what a router or tunnel sends: not this computer
BOOK = "/Jane%20Doe%20-%20A%20Quiet%20Harbour.epub"
PAGE = "text/html,application/xhtml+xml"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    lib = tmp_path_factory.mktemp("lib")
    for f in ("library_sync.py", "signin.html"):
        shutil.copy(LAUNCHERS / f, lib / f)
    (lib / "index.html").write_text("<html>the catalog</html>", encoding="utf-8")
    (lib / ".index").mkdir()
    (lib / ".index" / "library.json").write_text("{}", encoding="utf-8")
    (lib / "Jane Doe - A Quiet Harbour.epub").write_bytes(b"PK-book")
    proc = subprocess.Popen([sys.executable, str(lib / "library_sync.py"), "--serve", str(lib), "--port", "0", "--gate"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    line = proc.stdout.readline()
    m = re.search(r"127\.0\.0\.1:(\d+)/", line)
    assert m, line
    base = f"http://127.0.0.1:{m.group(1)}"
    code = (lib / ".sync" / "code.txt").read_text(encoding="utf-8").strip()
    yield base, code
    proc.terminate()
    proc.wait(10)


def ask(base, method, path, host=FOREIGN, accept=None, body=None, token=None, cookie=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    if host:
        req.add_header("Host", host)
    if accept:
        req.add_header("Accept", accept)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if cookie:
        req.add_header("Cookie", f"libsess={cookie}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def cookie_of(headers):
    for v in headers.get_all("Set-Cookie") or []:
        if v.startswith("libsess="):
            return v
    return None


def test_this_computer_is_let_in(server):
    base, _ = server
    for host in (None, "127.0.0.1", "localhost"):
        s, out, _ = ask(base, "GET", "/index.html", host=host or f"127.0.0.1:{base.rsplit(':', 1)[1]}")
        assert s == 200 and b"the catalog" in out, host


def test_nothing_without_a_sign_in(server):
    base, _ = server
    s, out, h = ask(base, "GET", "/", accept=PAGE)
    assert s == 401 and b'id="signin"' in out and h.get("Content-Type", "").startswith("text/html")
    assert "no-store" in h.get("Cache-Control", "")
    s, out, _ = ask(base, "GET", "/index.html", accept=PAGE)
    assert s == 401 and b"<html>the catalog</html>" not in out
    for path in ("/.index/library.json", BOOK, "/__sync/data", "/__alive"):
        s, out, _ = ask(base, "GET", path)
        assert s == 401 and out == b"", path
    s, _, _ = ask(base, "GET", "/__library_root")
    assert s in (401, 404)
    s, out, _ = ask(base, "GET", "/signin.html")
    assert s == 200 and b'id="signin"' in out


def test_sign_in_gives_the_cookie_and_it_opens_the_library(server):
    base, code = server
    s, out, h = ask(base, "POST", "/__sync/register", body={"login": "jane.doe@example.com", "password": "pw-quiet", "code": code})
    assert s == 200
    token = json.loads(out)["token"]
    c = cookie_of(h)
    assert c and c.startswith(f"libsess={token};")
    for part in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=34560000", "Secure"):
        assert part in c   # Secure: never sent over plain http (the browser speaks https to the router)
    s, out, _ = ask(base, "GET", "/index.html", accept=PAGE, cookie=token)
    assert s == 200 and b"the catalog" in out
    s, out, _ = ask(base, "GET", BOOK, cookie=token)
    assert s == 200 and out == b"PK-book"
    s, out, h = ask(base, "POST", "/__sync/login", body={"login": "jane.doe@example.com", "password": "pw-quiet"})
    assert s == 200 and cookie_of(h).startswith("libsess=" + json.loads(out)["token"])
    s, _, h = ask(base, "POST", "/__sync/login", body={"login": "jane.doe@example.com", "password": "pw-wrong"})
    assert s == 401 and cookie_of(h) is None


def test_a_device_signed_in_before_gets_the_cookie(server):
    base, _ = server
    s, out, _ = ask(base, "POST", "/__sync/login", body={"login": "jane.doe@example.com", "password": "pw-quiet"})
    token = json.loads(out)["token"]
    s, _, h = ask(base, "POST", "/__sync/session", body={}, token=token)
    assert s == 204 and cookie_of(h).startswith(f"libsess={token};")
    s, _, h = ask(base, "POST", "/__sync/session", body={}, token="0" * 64)
    assert s == 401 and cookie_of(h) is None
    s, _, h = ask(base, "POST", "/__sync/session", body={})
    assert s == 401


def test_sign_out_closes_the_library(server):
    base, _ = server
    s, out, _ = ask(base, "POST", "/__sync/login", body={"login": "jane.doe@example.com", "password": "pw-quiet"})
    token = json.loads(out)["token"]
    assert ask(base, "GET", BOOK, cookie=token)[0] == 200
    s, _, h = ask(base, "POST", "/__sync/logout", body={}, token=token, cookie=token)
    assert s == 204 and "Max-Age=0" in cookie_of(h)
    assert ask(base, "GET", BOOK, cookie=token)[0] == 401
    # an already dead key: the cookie is still cleared (the page can't clear an HttpOnly cookie)
    s, _, h = ask(base, "POST", "/__sync/logout", body={}, cookie=token)
    assert s == 401 and "Max-Age=0" in cookie_of(h)


def test_only_this_computer_s_own_names_are_local(server):
    base, _ = server
    port = base.rsplit(":", 1)[1]
    for host in ("[::1].evil.com", "[::1]evil", "localhost.evil.com", "127.0.0.1.nip.io", f"[::1]:{port}x"):
        assert ask(base, "GET", "/index.html", host=host)[0] == 401, host
    for host in ("[::1]", f"[::1]:{port}", f"localhost:{port}"):
        assert ask(base, "GET", "/index.html", host=host)[0] == 200, host
