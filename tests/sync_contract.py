#!/usr/bin/env python3
"""HTTP contract every library sync server must pass (Python launchers now, PowerShell later).

  python3 tests/sync_contract.py http://127.0.0.1:48765 [path/to/library/.sync/code.txt]

The code comes from the second argument (a code.txt) or from env LIBRARY_CODE. Prints ok/FAIL
lines, exits 0 when everything passed. Importable: run_contract(base_url, code=None) -> failures.
Safe on a long-lived server: logins are random per run and at most one wrong code is sent.
"""

import json
import os
import secrets
import sys
import urllib.error
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

    s, b = reg(other, code)
    t3 = b.get("token") if s == 200 and isinstance(b, dict) else None
    if t3:
        s, b = _call(base, "GET", "/__sync/data", token=t3)
        check("another account starts empty", s == 200 and isinstance(b, dict) and b.get("version") == 0)
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
