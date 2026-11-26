#!/usr/bin/env python3
"""How long a library sync server takes with a big account (~1 MB of bookmarks with notes).

  python3 tests/sync_timing.py http://127.0.0.1:48765 [path/to/library/.sync/code.txt] [size in KB]

Creates a new account (a random email), PUTs a document of about the given size (default 1024 KB),
then times GET /__sync/data, PUT /__sync/data and POST /__sync/place three times each and prints
the slowest of each, and the test account's login and password (to sign in on a phone). Exits 1 if a request fails or the slowest one takes 1 s or more.
The code comes from the second argument (a code.txt) or from env LIBRARY_CODE.
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_contract import _call  # noqa: E402

LIMIT = 1.0
WORDS = "harbour lantern quiet tide gull rope sail morning salt pier keeper fog bell shore".split()


def big_doc(kb):
    """A sync document of about `kb` KB: one book's place, many bookmarks with notes in others."""
    doc = {"v": 1, "places": {"Jane Doe - A Quiet Harbour": {"cfi": "epubcfi(/6/4!/4/2/1:0)", "pct": 0.1, "time": 1}},
           "marks": {}, "opened": {}, "prefs": {}}
    i = 0
    while len(json.dumps(doc)) < kb * 1024:
        book = f"Jane Doe - Voyage {i // 200}"
        cfi = f"epubcfi(/6/{2 + 2 * (i % 40)}!/4/2/1:{i})"
        t = 1700000000000 + i * 1000
        note = " ".join(WORDS[(i * 7 + k * 3) % len(WORDS)] for k in range(25))
        doc["marks"].setdefault(book, {})[f"{t}|{cfi}"] = {
            "id": f"{t}|{cfi}", "cfi": cfi, "text": f"Seed page {i}", "name": "", "note": note, "time": t}
        i += 1
    return doc


def timed(fn):
    t = time.perf_counter()
    status = fn()
    return status, time.perf_counter() - t


def run(base, code, kb=1024, log=print):
    """[(what, status, slowest seconds)]."""
    base = base.rstrip("/")
    login, password = f"timing-{secrets.token_hex(4)}@example.com", "pw-" + secrets.token_hex(8)
    s, b = _call(base, "POST", "/__sync/register", {"login": login, "password": password, "code": code})
    if s != 200:
        log(f"FAIL register: {s} {b}")
        return [("register", s, 0.0)]
    token = b["token"]
    doc = big_doc(kb)
    log(f"account {login}, password {password} (a test account: try it on a phone, then delete it)")
    log(f"document: {len(json.dumps(doc)) // 1024} KB")
    s, b = _call(base, "PUT", "/__sync/data", {"version": 0, "doc": doc}, token)
    if s != 200:
        log(f"FAIL first PUT: {s}")
        return [("first PUT", s, 0.0)]
    version = b["version"]
    results = {"GET /__sync/data": [], "PUT /__sync/data": [], "POST /__sync/place": []}
    for n in range(3):
        s, dt = timed(lambda: _call(base, "GET", "/__sync/data", None, token)[0])
        results["GET /__sync/data"].append((s, dt))
        doc["places"]["Jane Doe - A Quiet Harbour"]["time"] = 10 + n

        def put():
            nonlocal version
            st, body = _call(base, "PUT", "/__sync/data", {"version": version, "doc": doc}, token)
            if st == 200:
                version = body["version"]
            return st
        results["PUT /__sync/data"].append(timed(put))
        place = {"book": "Jane Doe - A Quiet Harbour", "place": {"cfi": "epubcfi(/6/8)", "pct": 0.2, "time": 100 + n}}
        results["POST /__sync/place"].append(timed(lambda: _call(base, "POST", "/__sync/place", place, token)[0]))
        version += 1   # the place was newer: one more version
    out = []
    for what, runs in results.items():
        status = next((st for st, _ in runs if st not in (200, 204)), runs[0][0])
        slowest = max(dt for _, dt in runs)
        ok = status in (200, 204) and slowest < LIMIT
        log(f"{'ok  ' if ok else 'FAIL'} {what}: {status}, slowest of 3: {slowest:.2f} s")
        out.append((what, status, slowest))
    return out


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: sync_timing.py BASE_URL [path/to/.sync/code.txt] [size in KB]")
    code = os.environ.get("LIBRARY_CODE")
    if len(argv) > 2:
        code = open(argv[2], encoding="utf-8-sig").read().strip()
    kb = int(argv[3]) if len(argv) > 3 else 1024
    res = run(argv[1], code, kb)
    bad = [r for r in res if r[1] not in (200, 204) or r[2] >= LIMIT]
    print(f"{len(bad)} too slow or failed" if bad else "all under 1 s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
