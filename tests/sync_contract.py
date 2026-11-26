#!/usr/bin/env python3
"""HTTP contract every library sync server must pass (Python launchers now, PowerShell later).

  python3 tests/sync_contract.py http://127.0.0.1:48765 [path/to/library/.sync/code.txt]

The code comes from the second argument (a code.txt) or from env LIBRARY_CODE. Prints ok/FAIL
lines, exits 0 when everything passed. Importable: run_contract(base_url, code=None) -> failures.
Safe on a long-lived server: logins are random per run and at most one wrong code is sent.
"""

import http.client
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request


def _call(base, method, path, body=None, token=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, out = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, out = e.code, e.read()
    try:
        return status, (json.loads(out) if out else None)
    except ValueError:
        return status, out


def _raw(base, method, path, headers=None, body=b""):
    """A request with the path sent exactly as given (no client-side normalisation): status."""
    u = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=30)
    try:
        conn.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        if "Content-Length" not in (headers or {}):
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


# A document with the shapes a server must store untouched (PowerShell's JSON is lossy).
TRICKY_DOC = {
    "v": 1, "empty": {}, "list": [], "one": [1], "nul": None, "yes": True, "no": False,
    "big": 12345678901234567890, "f": 0.1, "tiny": -2.5e-7, "zero": 0,
    "s": "\u00dcn\u00efc\u00f8de \"q\" \\ \n\t \u2603 \U0001d11e",
    "deep": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": [[[[{"z": []}]]]]}}}}}}}}}}}},
    "places": {"Jane Doe - A Quiet Harbour": {"time": 10, "cfi": "x"}, "Places": {"time": 3}},
}


def run_contract(base_url, code=None, log=None):
    base = base_url.rstrip("/")
    code = code or os.environ.get("LIBRARY_CODE", "")
    failures = []

    def check(name, ok):
        if log:
            log(("ok   " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    login, other = "c" + secrets.token_hex(6), "c" + secrets.token_hex(6)
    pw = "pw-" + secrets.token_hex(6)
    reg = lambda lg, c, p=pw: _call(base, "POST", "/__sync/register", {"login": lg, "password": p, "code": c})

    s, _ = _call(base, "GET", "/index.html")
    check("normal file still served (/index.html 200)", s == 200)
    s, _ = _call(base, "GET", "/.sync/code.txt")
    check("/.sync/code.txt is 404", s == 404)
    s, _ = _call(base, "GET", "/%2esync/code.txt")
    check("/%2esync/code.txt is 404", s == 404)
    for p in ("/.SYNC/code.txt", "/%2E%73ync/code.txt", "/x/../.sync/code.txt", "/.sync./code.txt",
              "/%5C.sync%5Ccode.txt", "//.sync/code.txt", "/.sync/code.txt?x=1"):
        check(f"GET {p} (sent raw) is 404", _raw(base, "GET", p) == 404)
    check("HEAD /.sync/code.txt is 404", _raw(base, "HEAD", "/.sync/code.txt") == 404)
    check("HEAD /x/../.SYNC/code.txt is 404", _raw(base, "HEAD", "/x/../.SYNC/code.txt") == 404)
    check("PUT /index.html is 405", _raw(base, "PUT", "/index.html", body=b"{}") == 405)
    check("PUT /__sync/login is 405", _raw(base, "PUT", "/__sync/login", body=b"{}") == 405)
    check("POST /index.html is 405", _raw(base, "POST", "/index.html", body=b"{}") == 405)
    s, _ = _call(base, "GET", "/__sync/nothing")
    check("unknown /__sync/ route is 404", s == 404)
    s, _ = _call(base, "POST", "/__sync/login", raw=b"not json")
    check("login with a non-JSON body is 400", s == 400)
    s, _ = _call(base, "POST", "/__sync/login", raw=json.dumps(["a", "b"]).encode())
    check("login with a JSON non-object body is 400", s == 400)
    check("a body over 8 MB is 413 (headers only sent)",
          _raw(base, "POST", "/__sync/login", {"Content-Length": str(8 * 1024 * 1024 + 1)}) == 413)

    s, _ = _call(base, "GET", "/__sync/data")
    check("data without token is 401", s == 401)
    s, _ = _call(base, "GET", "/__sync/data", token="0" * 64)
    check("data with unknown token is 401", s == 401)
    s, _ = _call(base, "POST", "/__sync/place", {"book": "b", "place": {"time": 1}})
    check("place without token is 401", s == 401)

    s, _ = reg(login, "definitely-not-the-code-00")
    check("register with wrong code is 403", s == 403)
    s, b = reg(login, code)
    token = b.get("token") if s == 200 and isinstance(b, dict) else None
    check("register with right code is 200 + token", s == 200 and bool(token))
    s, _ = reg(login.upper(), code)
    check("register of a taken login is 409", s == 409)
    s, b = _call(base, "POST", "/__sync/login", {"login": login.upper(), "password": pw})
    token2 = b.get("token") if s == 200 and isinstance(b, dict) else None
    check("login (any case) is 200 + token", s == 200 and bool(token2))
    s, _ = _call(base, "POST", "/__sync/login", {"login": login, "password": pw + "x"})
    check("login with wrong password is 401", s == 401)
    s, _ = _call(base, "POST", "/__sync/register", {"login": "A", "password": pw, "code": code})
    check("register with a bad login is 400", s == 400)

    if token:
        s, b = _call(base, "GET", "/__sync/data", token=token)
        check("data starts empty at version 0", s == 200 and isinstance(b, dict) and b.get("version") == 0 and b.get("doc") is None)
        s, b = _call(base, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1, "n": "one"}}, token)
        check("PUT at the current version is 200 {version: 1}", s == 200 and isinstance(b, dict) and b.get("version") == 1)
        s, b = _call(base, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1, "n": "stale"}}, token)
        check("PUT at a stale version is 409 with the stored data",
              s == 409 and isinstance(b, dict) and b.get("version") == 1 and b.get("doc") == {"v": 1, "n": "one"})
        s, b = _call(base, "GET", "/__sync/data", token=token)
        check("GET returns what was stored", s == 200 and isinstance(b, dict) and b == {"version": 1, "doc": {"v": 1, "n": "one"}})
        s, _ = _call(base, "POST", "/__sync/place", {"book": "Jane Doe - A Quiet Harbour", "place": {"time": 5, "pos": "x"}}, token)
        check("place is 204", s == 204)
        s, b = _call(base, "GET", "/__sync/data", token=token)
        doc = b.get("doc") if isinstance(b, dict) else None
        check("place landed in the document and bumped the version",
              isinstance(doc, dict) and doc.get("places", {}).get("Jane Doe - A Quiet Harbour", {}).get("time") == 5
              and b.get("version") == 2)
        s, b = _call(base, "GET", "/__sync/data", token=token2)
        check("a second device of the same account sees the same data", s == 200 and isinstance(b, dict) and b.get("version") == 2)
        s, _ = _call(base, "POST", "/__sync/logout", token=token)
        check("logout is 204", s == 204)
        s, _ = _call(base, "GET", "/__sync/data", token=token)
        check("a logged-out token is 401", s == 401)
        s, _ = _call(base, "GET", "/__sync/data", token=token2)
        check("other device's token still works", s == 200)

    s, b = reg(other, "  " + code.upper() + " ")
    t3 = b.get("token") if s == 200 and isinstance(b, dict) else None
    check("the code is compared trimmed and case-insensitive", bool(t3))
    if t3:
        s, b = _call(base, "GET", "/__sync/data", token=t3.upper())
        check("another account starts empty (token in any case)", s == 200 and isinstance(b, dict) and b.get("version") == 0)
        raw = json.dumps({"version": 0, "doc": TRICKY_DOC}, ensure_ascii=False).encode("utf-8")
        s, b = _call(base, "PUT", "/__sync/data", token=t3, raw=raw)
        check("PUT of a tricky document is 200", s == 200 and isinstance(b, dict) and b.get("version") == 1)
        s, b = _call(base, "GET", "/__sync/data", token=t3)
        check("the tricky document comes back exactly", s == 200 and b == {"version": 1, "doc": TRICKY_DOC})
        s, _ = _call(base, "POST", "/__sync/place", {"book": "Jane Doe - A Quiet Harbour", "place": {"time": 9}}, t3)
        s2, b = _call(base, "GET", "/__sync/data", token=t3)
        check("an older place is ignored (204, nothing changes)",
              s == 204 and s2 == 200 and b == {"version": 1, "doc": TRICKY_DOC})
        place = {"time": 11.5, "cfi": "\u00fc", "list": [], "o": {}}
        s, _ = _call(base, "POST", "/__sync/place", {"book": "places", "place": place}, t3)
        want = json.loads(json.dumps(TRICKY_DOC))
        want["places"]["places"] = place
        s2, b = _call(base, "GET", "/__sync/data", token=t3)
        check("a newer place changes only that book (keys are case-sensitive)",
              s == 204 and s2 == 200 and b == {"version": 2, "doc": want})
        s, b = _call(base, "PUT", "/__sync/data", {"version": True, "doc": {}}, t3)
        check("PUT with a non-integer version is 400", s == 400)
        s, b = _call(base, "PUT", "/__sync/data", {"version": 2, "doc": None}, t3)
        check("PUT with a non-object doc is 400", s == 400)
    else:
        check("second account registers", False)
    return failures


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: sync_contract.py BASE_URL [path/to/.sync/code.txt]")
    code = None
    if len(argv) > 2:
        code = open(argv[2], encoding="utf-8-sig").read().strip()
    failures = run_contract(argv[1], code, log=print)
    print(f"{len(failures)} failure(s)" if failures else "all ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
