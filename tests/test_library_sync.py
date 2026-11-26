"""Tests for launchers/library_sync.py: accounts and one versioned sync document per account."""

import io
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launchers"))
from library_sync import SyncStore, is_private, respond  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return SyncStore(tmp_path)


def call(store, method, path, body=None, token=None, raw=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
    status, ctype, out = store.handle(method, path, headers, data)
    return status, (json.loads(out) if out else None)


def register(store, login="Ann", password="pw123456", code=None):
    return call(store, "POST", "/__sync/register",
                {"login": login, "password": password, "code": code or store.code()})


def new_token(store, login="Ann"):
    status, body = register(store, login)
    assert status == 200
    return body["token"]


def test_code_created_once_and_has_format(tmp_path):
    code = SyncStore(tmp_path).code()
    assert re.fullmatch(r"[a-z]+(-[a-z]+){3}-\d{2}", code)
    assert (tmp_path / ".sync" / "code.txt").read_text().strip() == code
    assert SyncStore(tmp_path).code() == code


def test_register_login_data_roundtrip(store):
    token = new_token(store)
    assert call(store, "GET", "/__sync/data", token=token) == (200, {"version": 0, "doc": None})
    assert call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1}}, token) == (200, {"version": 1})
    assert call(store, "GET", "/__sync/data", token=token) == (200, {"version": 1, "doc": {"v": 1}})
    status, body = call(store, "POST", "/__sync/login", {"login": "ANN", "password": "pw123456"})
    assert status == 200 and body["token"] != token
    assert call(store, "GET", "/__sync/data", token=body["token"]) == (200, {"version": 1, "doc": {"v": 1}})


def test_json_content_type(store):
    status, ctype, _ = store.handle("GET", "/__sync/data", {}, b"")
    assert status == 401 and ctype == "application/json; charset=utf-8"


def test_put_with_stale_version_is_409_with_current(store):
    token = new_token(store)
    call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1}}, token)
    assert call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 2}}, token) == \
        (409, {"version": 1, "doc": {"v": 1}})
    assert call(store, "GET", "/__sync/data", token=token)[1]["doc"] == {"v": 1}


def test_wrong_code_403_then_throttled_429(store):
    for _ in range(5):
        assert register(store, "bob", code="nope")[0] == 403
    status, body = register(store, "bob")
    assert status == 429 and body["wait"] > 0
    store._now = lambda t=store._now(): t + 61
    assert register(store, "bob")[0] == 200


def test_code_is_compared_trimmed_and_case_insensitive(store):
    assert register(store, "bob", code="  " + store.code().upper() + "\n")[0] == 200


def test_wrong_password_throttled_per_login(store):
    register(store, "ann")
    register(store, "cat")
    for _ in range(5):
        assert call(store, "POST", "/__sync/login", {"login": "ann", "password": "bad"})[0] == 401
    assert call(store, "POST", "/__sync/login", {"login": "ann", "password": "pw123456"})[0] == 429
    assert call(store, "POST", "/__sync/login", {"login": "cat", "password": "pw123456"})[0] == 200
    store._now = lambda t=store._now(): t + 61
    assert call(store, "POST", "/__sync/login", {"login": "ann", "password": "pw123456"})[0] == 200


def test_unknown_login_is_401(store):
    assert call(store, "POST", "/__sync/login", {"login": "nobody", "password": "x"})[0] == 401


def test_login_taken_409_case_insensitive(store):
    assert register(store, "Ann")[0] == 200
    assert register(store, "aNN")[0] == 409


@pytest.mark.parametrize("login", ["a", "x" * 33, "../x", "a/b", "a b", "", "con", "NUL", "aux", "prn", "com1", "lpt9", "con.x", "nul.txt.y"])
def test_bad_logins_400(store, login):
    assert register(store, login)[0] == 400


def test_malformed_json_400(store):
    assert call(store, "POST", "/__sync/register", raw=b"{nope")[0] == 400
    assert call(store, "POST", "/__sync/login", raw=b"[1]")[0] == 400


def test_logout_forgets_only_that_token(store):
    t1 = new_token(store)
    t2 = call(store, "POST", "/__sync/login", {"login": "ann", "password": "pw123456"})[1]["token"]
    status, ctype, body = store.handle("POST", "/__sync/logout", {"Authorization": f"Bearer {t1}"}, b"")
    assert status == 204 and body == b""
    assert call(store, "GET", "/__sync/data", token=t1)[0] == 401
    assert call(store, "GET", "/__sync/data", token=t2)[0] == 200


def test_deleted_account_file_keeps_data(store, tmp_path):
    token = new_token(store)
    call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1, "x": 7}}, token)
    (tmp_path / ".sync" / "accounts" / "ann.json").unlink()
    assert call(store, "GET", "/__sync/data", token=token)[0] == 401
    token2 = new_token(store)
    assert call(store, "GET", "/__sync/data", token=token2) == (200, {"version": 1, "doc": {"v": 1, "x": 7}})


def place(store, token, book, cfi, time):
    return store.handle("POST", "/__sync/place", {"Authorization": f"Bearer {token}"},
                        json.dumps({"book": book, "place": {"cfi": cfi, "time": time}}).encode())[0]


def test_place_endpoint_later_time_wins(store):
    token = new_token(store)
    doc = {"v": 1, "places": {"b1": {"cfi": "c1", "time": 10}}}
    call(store, "PUT", "/__sync/data", {"version": 0, "doc": doc}, token)
    assert place(store, token, "b1", "c2", 20) == 204
    got = call(store, "GET", "/__sync/data", token=token)[1]
    assert got["version"] == 2 and got["doc"]["places"]["b1"] == {"cfi": "c2", "time": 20}
    assert place(store, token, "b1", "c3", 5) == 204
    got = call(store, "GET", "/__sync/data", token=token)[1]
    assert got["version"] == 2 and got["doc"]["places"]["b1"]["cfi"] == "c2"


def test_place_with_null_doc_creates_one(store):
    token = new_token(store)
    assert place(store, token, "b1", "c1", 7) == 204
    assert call(store, "GET", "/__sync/data", token=token)[1] == \
        {"version": 1, "doc": {"v": 1, "places": {"b1": {"cfi": "c1", "time": 7}}}}


def test_place_time_must_be_a_number(store):
    token = new_token(store)
    assert place(store, token, "b1", "c1", "20") == 400
    assert place(store, token, "b1", "c1", True) == 400


def test_no_token_401(store):
    for method, path in [("GET", "/__sync/data"), ("PUT", "/__sync/data"),
                         ("POST", "/__sync/place"), ("POST", "/__sync/logout")]:
        assert call(store, method, path, {})[0] == 401
    assert call(store, "GET", "/__sync/data", token="0" * 64)[0] == 401


def test_routing_errors(store):
    assert call(store, "GET", "/__sync/nothing")[0] == 404
    assert call(store, "GET", "/__sync/register")[0] == 405
    assert call(store, "DELETE", "/__sync/data")[0] == 405


def test_body_limit_413_through_respond(store):
    class Fake:
        command, path = "POST", "/__sync/login"
        headers = {"Content-Length": str(8 * 1024 * 1024 + 1)}
        rfile = io.BytesIO(b"")
        wfile = io.BytesIO()
        sent = []

        def send_response(self, code): self.sent.append(code)
        def send_header(self, k, v): pass
        def end_headers(self): pass

    h = Fake()
    respond(h, store)
    assert h.sent == [413]


def test_respond_writes_answer(store):
    body = json.dumps({"login": "nobody", "password": "x"}).encode()

    class Fake:
        command, path = "POST", "/__sync/login"
        headers = {"Content-Length": str(len(body))}
        rfile = io.BytesIO(body)
        wfile = io.BytesIO()
        sent = []

        def send_response(self, code): self.sent.append(code)
        def send_header(self, k, v): pass
        def end_headers(self): pass

    h = Fake()
    respond(h, store)
    assert h.sent == [401] and "error" in json.loads(h.wfile.getvalue())


def test_is_private():
    assert is_private("/.sync/code.txt")
    assert is_private("/%2Esync/x")
    assert is_private("/a/.sync")
    assert is_private("/a%2F.sync/x")
    assert is_private("//.sync/x")
    assert is_private("/.sync?x=1")
    assert is_private("/.sync#a")
    assert not is_private("/.index/reader/sync.js")
    assert not is_private("/book.sync.txt")


def test_passwords_and_tokens_not_stored_plain(store, tmp_path):
    token = new_token(store)
    text = (tmp_path / ".sync" / "accounts" / "ann.json").read_text()
    assert "pw123456" not in text and token not in text
    acc = json.loads(text)
    assert acc["login"] == "ann" and acc["iterations"] == 200000
    assert len(bytes.fromhex(acc["salt"])) == 16 and len(acc["tokens"]) == 1


def test_word_list_is_big_and_distinct():
    from library_sync import WORDS
    assert len(WORDS) >= 400 and len(set(WORDS)) == len(WORDS)
    assert all(re.fullmatch(r"[a-z]+", w) for w in WORDS)


def test_concurrent_wrong_logins_cannot_beat_the_throttle(store):
    import threading
    register(store, "ann")
    statuses = []

    def go():
        statuses.append(call(store, "POST", "/__sync/login", {"login": "ann", "password": "bad"})[0])

    threads = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert statuses.count(401) <= 5 and statuses.count(401) + statuses.count(429) == 20


def test_concurrent_wrong_codes_cannot_beat_the_throttle(store):
    import threading
    store.code()
    statuses = []

    def go():
        statuses.append(register(store, "bob", code="nope")[0])

    threads = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert statuses.count(403) <= 5 and statuses.count(403) + statuses.count(429) == 20


def test_bom_files_are_read(store, tmp_path):
    token = new_token(store)
    d = tmp_path / ".sync"
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "ann.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"version": 3, "doc": {"v": 1}}).encode())
    assert call(store, "GET", "/__sync/data", token=token) == (200, {"version": 3, "doc": {"v": 1}})
    acc = d / "accounts" / "ann.json"
    acc.write_bytes(b"\xef\xbb\xbf" + acc.read_bytes())
    assert call(store, "POST", "/__sync/login", {"login": "ann", "password": "pw123456"})[0] == 200
    (d / "code.txt").write_bytes(b"\xef\xbb\xbfriver-paper-cold-moon-47\r\n")
    assert store.code() == "river-paper-cold-moon-47"
    assert register(store, "bob", code="river-paper-cold-moon-47")[0] == 200


def test_corrupt_data_file_is_500_and_never_overwritten(store, tmp_path):
    token = new_token(store)
    f = tmp_path / ".sync" / "data" / "ann.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text("{broken", encoding="utf-8")
    assert call(store, "GET", "/__sync/data", token=token)[0] == 500
    assert call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1}}, token)[0] == 500
    assert place(store, token, "b1", "c1", 5) == 500
    assert f.read_text(encoding="utf-8") == "{broken"


def test_corrupt_account_file_is_500_not_a_crash(store, tmp_path):
    new_token(store)
    acc = tmp_path / ".sync" / "accounts" / "ann.json"
    acc.write_text(json.dumps({"login": "ann", "salt": "zz", "hash": "\u00e9", "iterations": 1, "tokens": []}))
    assert call(store, "POST", "/__sync/login", {"login": "ann", "password": "pw123456"})[0] == 500


def test_respond_turns_exceptions_into_500(store):
    class Boom:
        def handle(self, *a):
            raise RuntimeError("x")

        _err = staticmethod(SyncStore._err.__get__(store))

    class Fake:
        command, path = "GET", "/__sync/data"
        headers = {}
        rfile = io.BytesIO(b"")
        wfile = io.BytesIO()
        sent = []

        def send_response(self, code): self.sent.append(code)
        def send_header(self, k, v): pass
        def end_headers(self): pass

    h = Fake()
    respond(h, Boom())
    assert h.sent == [500]


def test_invalid_utf8_files_are_corrupt_not_crashes(store, tmp_path):
    token = new_token(store)
    d = tmp_path / ".sync"
    (d / "accounts" / "aaa.json").write_bytes(b'{"x":"\xff"}')
    assert call(store, "GET", "/__sync/data", token=token)[0] == 200
    f = d / "data" / "ann.json"
    f.parent.mkdir(exist_ok=True)
    f.write_bytes("{}".encode("utf-16"))
    assert call(store, "GET", "/__sync/data", token=token)[0] == 500
    assert call(store, "PUT", "/__sync/data", {"version": 0, "doc": {"v": 1}}, token)[0] == 500
    assert f.read_bytes() == "{}".encode("utf-16")
