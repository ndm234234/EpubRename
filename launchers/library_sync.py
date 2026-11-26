#!/usr/bin/env python3
"""Sync store for the library's own server: accounts and one versioned JSON document per account.

The server is a dumb store: all merging happens in the browser. Files, in <library>/.sync/
(never served; every server answers 404 to a path with a `.sync` segment):

  code.txt              the library code: 4 words + 2 digits, "-"-joined; the owner may edit it
  accounts/<login>.json {login, salt, hash, iterations, tokens: [sha256 hex, ...]}
  data/<login>.json     {version, doc}
  lock                  exclusive lock file for every read-check-write of accounts/ and data/

On-disk format, shared with the PowerShell twin:
  * login: lower-case, ^[a-z0-9._-]{2,32}$ (it names the files).
  * password: PBKDF2-HMAC-SHA256 over the password's UTF-8 bytes, 200000 iterations, random
    16-byte salt; `salt` and `hash` (32 bytes) are lower-case hex strings.
  * device token: 32 random bytes, sent to the device as a 64-char lower-case hex string. The
    stored token hash is SHA-256 (lower-case hex) of that hex string's ASCII bytes (the 64
    characters themselves, not the 32 raw bytes). The token travels as `Authorization: Bearer <hex>`.
  * every file is UTF-8 without BOM; writes go to a temp file in the same folder, then os.replace.
    Readers tolerate a BOM (PowerShell 5.1 writes one). A missing file means "absent"; an
    unparsable data/account file is an error (500) and is never overwritten.

Throttle state (wrong codes, wrong passwords) lives in memory only.
Dev server: python3 launchers/library_sync.py --serve ROOT --port N  (127.0.0.1, no browser; N=0: a free
port, printed in the "Library:" line).
"""

import contextlib
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

ITERATIONS = 200000
BODY_LIMIT = 8 * 1024 * 1024
LOGIN_RE = re.compile(r"[a-z0-9._-]{2,32}")
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
TOKEN_RE = re.compile(r"[0-9a-f]{64}")
JSON_TYPE = "application/json; charset=utf-8"
FREE_WRONG = 5          # wrong tries in a row before the pause starts
FIRST_PAUSE = 60        # seconds; doubles per further wrong try
MAX_PAUSE = 3600

WORDS = """
able acid aged also arch area army atom aunt auto away baby back bake ball band bank barn base bath
bead beam bean bear beat bell belt bend bird bite blue boat body bold bolt bone book boot born bowl
brave bread brick bridge brook broom brown bulb bush cabin cake calm camp candy cape card care cart
cave chain chair chalk charm chess chief city clay clean clear cliff clock cloud coal coast coat
coin cold cone cook cool copper coral cord corn cove craft crane crisp crown cube curl dark dawn
deep deer desk dish dock door dove draw dream drum dune dust eagle earth east easy edge elm ember
end fair fall farm fast fawn fern field fig fine fire fish flag flame flat fleet flint flour foam
fog fold forest fork fox frame frost fruit gate gift glad glass glen glow goat gold grain grape
grass gray green grove gull hall hand harbor hare hat hawk hay heart hedge hill honey hood hope horn
house hush ice inch ink iron isle ivory jade jar jazz jolly joy keen kettle key kind king kite knot
lake lamp lane lark leaf lemon lily lime linen lion lock log loom lull lunar maple marsh mast meadow
melon mill mint mist moon moss moth mouse mud nest net night noble north nut oak oat ocean olive
orange otter owl oyster paint pale palm paper path pearl pebble pine plain plum pond pony pool port
quail quartz queen quick quiet quilt rain raven reed ribbon ridge ring river road robin rock roof
root rope rose round ruby sage sail salt sand seal shade shell shore silk silver sky slate snow soft
song south spark spice spoon spring star steam stone storm straw stream sugar summer sun swan swift
tale tea thorn tide tiger tile timber tower trail tree tulip twig vale velvet violet wagon warm wave
wheat willow wind winter wolf wood wren yard yarn yellow zinc acorn amber angle apple apron arrow
aster badge basil basket beach beacon berry birch blade blank blaze bloom blossom bonnet border
branch breeze bright bronze bubble bucket budget butter button cactus camel canal candle canyon
carpet castle cedar cellar cherry cinder circle clover cobble comet cotton cradle crest crystal
cypress daisy delta desert diary dinner dolphin dragon drift echo elder engine fabric falcon feather
ferry fiddle finch fjord flower forge fossil fountain garden garlic ginger glider granite gravel
guitar hammer harvest hazel helmet heron hollow hornet island jacket jasmine jungle kernel kitten
ladder lagoon lantern laurel lilac linden lizard lotus magnet mango marble meteor mirror monkey
mosaic nectar needle nickel noodle novel nutmeg orchid oxygen paddle palace panda parrot pasture
pepper petal piano pillow planet pocket poppy prairie pumpkin puzzle rabbit radish rafter ranch
rapids reef rocket saddle saffron salmon sapphire satin scarlet shadow sheep shield signal sketch
sparrow spider spruce square stable statue sunset table tassel temple thistle thunder ticket toast
tomato trumpet tunnel turnip turtle umbrella valley vanilla village walnut walrus wander weasel
whale whisper window wizard wonder yacht zebra zephyr anchor arctic autumn badger banjo barrel
beetle bishop bobcat bottle bounty buckle cabbage cannon carrot ceramic chapel cheese chimney cicada
citrus cobweb cricket cuckoo currant dahlia dapple dingo dusk ermine fennel ferret firefly flute
gazelle gecko glacier goblet gopher gourd hammock heather hickory icicle jackal jigsaw kayak koala
lavender lichen locket lullaby mallow maroon meringue minnow mulberry muffin nutria oatmeal oriole
osprey paprika pelican penguin pigeon pistol plover pretzel quince radar raisin rhubarb sequoia
shuttle skiff sorrel starling sundial tangerine teapot thimble toffee vessel violin wafer whistle
""".split()

_ZERO_ACCOUNT_SALT = b"\0" * 16


def is_private(url_path):
    """True if any (decoded) segment of the URL path is `.sync`."""
    path = unquote(re.split(r"[?#]", url_path, maxsplit=1)[0])
    return any(seg.lower().rstrip(". ") == ".sync" for seg in re.split(r"[/\\]", path))


def _hash_password(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class Corrupt(Exception):
    """A stored file exists but can't be used; it is reported (500) and never overwritten."""


class _Throttle:
    """Counts wrong tries per key; after FREE_WRONG in a row, pauses with a doubling delay.

    begin() reserves an attempt atomically (so parallel requests can't all slip in before the
    first one is judged); each begun attempt must end with right(), wrong() or release().
    """

    def __init__(self):
        self.state = {}      # key -> [wrong count, locked until, attempts in flight]
        self.mutex = threading.Lock()

    def begin(self, key, now):
        """0 if the attempt may go on (and is reserved), else the seconds to wait."""
        with self.mutex:
            s = self.state.setdefault(key, [0, 0.0, 0])
            if s[1] > now:
                return s[1] - now
            slots = FREE_WRONG - s[0] if s[0] < FREE_WRONG else 1
            if s[2] >= slots:
                return 1.0
            s[2] += 1
            return 0

    def _end(self, key, wrong, now=0):
        with self.mutex:
            s = self.state.setdefault(key, [0, 0.0, 0])
            s[2] = max(0, s[2] - 1)
            if wrong is None:
                pass
            elif wrong:
                s[0] += 1
                if s[0] >= FREE_WRONG:
                    s[1] = now + min(FIRST_PAUSE * 2 ** (s[0] - FREE_WRONG), MAX_PAUSE)
            else:
                s[0], s[1] = 0, 0.0
            if s[0] == 0 and s[2] == 0:
                del self.state[key]
            elif len(self.state) > 10000:   # forget keys whose pause is over and that never reached one
                for k in [k for k, v in self.state.items()
                          if k != key and v[2] == 0 and v[1] < now and v[0] < FREE_WRONG]:
                    del self.state[k]

    def wrong(self, key, now):
        self._end(key, True, now)

    def right(self, key):
        self._end(key, False)

    def release(self, key):
        self._end(key, None)


class SyncStore:
    def __init__(self, library_root):
        self.dir = Path(library_root) / ".sync"
        self._now = time.time
        self._code_throttle = _Throttle()
        self._pw_throttle = _Throttle()

    # ---- files ----
    def code(self):
        path = self.dir / "code.txt"
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        except OSError:
            pass
        with self._locked():
            try:
                text = path.read_text(encoding="utf-8-sig").strip()
                if text:
                    return text
            except OSError:
                pass
            words = [secrets.choice(WORDS) for _ in range(4)]
            text = "-".join(words + [f"{secrets.randbelow(100):02d}"])
            self._write(path, text + "\n", raw=True)
            return text

    @contextlib.contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "lock", "a+b") as f:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                while True:
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        continue
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _write(self, path, content, raw=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(content if raw else json.dumps(content, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _read_json(self, path, default=None):
        """Parsed file; `default` only if it does not exist; a file that can't be parsed raises Corrupt."""
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return default
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except ValueError:   # includes UnicodeDecodeError
            raise Corrupt(path.name) from None

    def _read_account(self, login):
        """The account dict, None if absent; Corrupt if the file is unusable."""
        acc = self._read_json(self._account_path(login))
        if acc is None:
            return None
        if not (isinstance(acc, dict) and isinstance(acc.get("tokens"), list)
                and isinstance(acc.get("iterations"), int) and not isinstance(acc.get("iterations"), bool)
                and acc["iterations"] > 0 and isinstance(acc.get("hash"), str) and acc["hash"].isascii()
                and isinstance(acc.get("salt"), str)):
            raise Corrupt(f"{login}.json")
        try:
            bytes.fromhex(acc["salt"])
        except ValueError:
            raise Corrupt(f"{login}.json") from None
        return acc

    def _account_path(self, login):
        return self.dir / "accounts" / f"{login}.json"

    def _read_data(self, login):
        d = self._read_json(self.dir / "data" / f"{login}.json")
        if d is None:
            return {"version": 0, "doc": None}
        if isinstance(d, dict) and isinstance(d.get("version"), int) and not isinstance(d["version"], bool):
            return d
        raise Corrupt(f"{login}.json")

    def _write_data(self, login, data):
        self._write(self.dir / "data" / f"{login}.json", data)

    def _auth(self, headers):
        """(login, token) of the account holding the bearer token, or (None, None)."""
        auth = ""
        for k, v in headers.items():
            if k.lower() == "authorization":
                auth = v.strip()
        parts = auth.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None, None
        token = parts[1].strip().lower()
        if not TOKEN_RE.fullmatch(token):
            return None, None
        want = _hash_token(token)
        for path in sorted((self.dir / "accounts").glob("*.json")):
            try:
                acc = self._read_json(path)
            except Corrupt:
                continue   # someone else's broken file must not lock everybody out
            if isinstance(acc, dict) and isinstance(acc.get("tokens"), list) \
                    and any(isinstance(t, str) and t.isascii() and hmac.compare_digest(want, t)
                            for t in acc["tokens"]):
                return path.stem, token
        return None, None

    # ---- answers ----
    @staticmethod
    def _json(status, obj):
        return status, JSON_TYPE, json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _err(self, status, name, **extra):
        return self._json(status, {"error": name, **extra})

    def _throttled(self, wait):
        return self._err(429, "throttled", wait=int(wait) + 1)

    @staticmethod
    def _body_object(body):
        try:
            obj = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return obj if isinstance(obj, dict) else None

    def handle(self, method, path, headers, body):
        path = re.split(r"[?#]", path, maxsplit=1)[0]
        routes = {"/__sync/register": "POST", "/__sync/login": "POST", "/__sync/logout": "POST",
                  "/__sync/place": "POST", "/__sync/data": ("GET", "PUT")}
        if path not in routes:
            return self._err(404, "not found")
        allowed = routes[path]
        if method not in (allowed if isinstance(allowed, tuple) else (allowed,)):
            return self._err(405, "method not allowed")
        if len(body) > BODY_LIMIT:
            return self._err(413, "too large")
        name = path.rsplit("/", 1)[1]
        try:
            return getattr(self, "_" + name)(method, headers, body)
        except Corrupt as e:
            return self._err(500, "corrupt file", file=str(e))
        except Exception:
            return self._err(500, "server error")

    def _new_token(self, acc):
        token = secrets.token_hex(32)
        acc["tokens"].append(_hash_token(token))
        return token

    def _credentials(self, body):
        """Parsed (obj, login, password) or None for a 400."""
        obj = self._body_object(body)
        if obj is None:
            return None
        login, password = obj.get("login"), obj.get("password")
        if not isinstance(login, str) or not LOGIN_RE.fullmatch(login.lower()) \
                or login.lower().split(".")[0] in RESERVED:   # reserved device names on Windows
            return None
        if not isinstance(password, str) or not 0 < len(password) <= 1024:
            return None
        return obj, login.lower(), password

    def _register(self, method, headers, body):
        cred = self._credentials(body)
        if cred is None:
            return self._err(400, "bad request")
        obj, login, password = cred
        now = self._now()
        wait = self._code_throttle.begin("code", now)
        if wait > 0:
            return self._throttled(wait)
        try:
            given = obj.get("code")
            want = self.code().strip().lower()
            good = isinstance(given, str) and hmac.compare_digest(
                given.strip().lower().encode("utf-8"), want.encode("utf-8"))
        except BaseException:
            self._code_throttle.release("code")
            raise
        if not good:
            self._code_throttle.wrong("code", now)
            return self._err(403, "wrong code")
        self._code_throttle.right("code")
        salt = secrets.token_bytes(16)
        acc = {"login": login, "salt": salt.hex(), "hash": _hash_password(password, salt, ITERATIONS),
               "iterations": ITERATIONS, "tokens": []}
        with self._locked():
            if self._account_path(login).exists():
                return self._err(409, "login taken")
            token = self._new_token(acc)
            self._write(self._account_path(login), acc)
        return self._json(200, {"token": token})

    def _login(self, method, headers, body):
        cred = self._credentials(body)
        if cred is None:
            return self._err(400, "bad request")
        _, login, password = cred
        now = self._now()
        wait = self._pw_throttle.begin(login, now)
        if wait > 0:
            return self._throttled(wait)
        try:
            acc = self._read_account(login)
            if acc is not None:
                ok = hmac.compare_digest(
                    _hash_password(password, bytes.fromhex(acc["salt"]), acc["iterations"]), acc["hash"])
            else:
                _hash_password(password, _ZERO_ACCOUNT_SALT, ITERATIONS)   # same cost for unknown logins
                ok = False
        except BaseException:
            self._pw_throttle.release(login)
            raise
        if not ok:
            self._pw_throttle.wrong(login, now)
            return self._err(401, "unauthorized")
        self._pw_throttle.right(login)
        with self._locked():
            acc = self._read_account(login)
            if acc is None:
                return self._err(401, "unauthorized")
            token = self._new_token(acc)
            self._write(self._account_path(login), acc)
        return self._json(200, {"token": token})

    def _logout(self, method, headers, body):
        with self._locked():
            login, token = self._auth(headers)
            if login is None:
                return self._err(401, "unauthorized")
            acc = self._read_json(self._account_path(login))
            acc["tokens"] = [t for t in acc["tokens"] if t != _hash_token(token)]
            self._write(self._account_path(login), acc)
        return 204, JSON_TYPE, b""

    def _data(self, method, headers, body):
        if method == "GET":
            login, _ = self._auth(headers)
            if login is None:
                return self._err(401, "unauthorized")
            return self._json(200, self._read_data(login))
        with self._locked():
            login, _ = self._auth(headers)
            if login is None:
                return self._err(401, "unauthorized")
            obj = self._body_object(body)
            if obj is None or not isinstance(obj.get("version"), int) or isinstance(obj.get("version"), bool) \
                    or not isinstance(obj.get("doc"), dict):
                return self._err(400, "bad request")
            current = self._read_data(login)
            if obj["version"] != current["version"]:
                return self._json(409, current)
            self._write_data(login, {"version": current["version"] + 1, "doc": obj["doc"]})
            return self._json(200, {"version": current["version"] + 1})

    def _place(self, method, headers, body):
        with self._locked():
            login, _ = self._auth(headers)
            if login is None:
                return self._err(401, "unauthorized")
            obj = self._body_object(body)
            book, place = (obj or {}).get("book"), (obj or {}).get("place")
            if not isinstance(book, str) or not book or not isinstance(place, dict) \
                    or not _is_number(place.get("time")):
                return self._err(400, "bad request")
            current = self._read_data(login)
            doc = current["doc"]
            if doc is None:
                doc = {"v": 1}
            if not isinstance(doc, dict):
                return self._err(400, "bad request")
            places = doc.get("places")
            if not isinstance(places, dict):
                places = doc["places"] = {}
            stored = places.get(book)
            old = stored.get("time") if isinstance(stored, dict) else None
            if not _is_number(old) or place["time"] > old:
                places[book] = place
                self._write_data(login, {"version": current["version"] + 1, "doc": doc})
        return 204, JSON_TYPE, b""


def respond(handler, store):
    """Read the request body by Content-Length (413 over the limit), ask the store, write the answer."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = -1
    if length < 0:
        status, ctype, out = store._err(400, "bad request")
    elif length > BODY_LIMIT:
        status, ctype, out = store._err(413, "too large")
        handler.close_connection = True   # the unread body would corrupt a kept-alive connection
    else:
        body = handler.rfile.read(length) if length else b""
        try:
            status, ctype, out = store.handle(handler.command, handler.path, dict(handler.headers.items()), body)
        except Exception:
            status, ctype, out = store._err(500, "server error")
    handler.send_response(status)
    if status != 204:
        handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(out)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if out:
        handler.wfile.write(out)


def serve(root, port):
    import functools
    root = str(Path(root).resolve())
    store = SyncStore(root)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def _sync(self):
            if self.path.startswith("/__sync/"):
                respond(self, store)
                return True
            return False

        def do_GET(self):
            if self._sync():
                return
            if self.path == "/__library_root":
                body = root.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif is_private(self.path):
                self.send_error(404)
            else:
                super().do_GET()

        def do_HEAD(self):
            if is_private(self.path):
                self.send_error(404)
            else:
                super().do_HEAD()

        def do_POST(self):
            if not self._sync():
                self.send_error(405)

        do_PUT = do_DELETE = do_POST

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=root))
    port = server.server_address[1]   # --port 0: the free port the system gave
    print(f"Library: http://127.0.0.1:{port}/index.html\nCode:    {store.code()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Dev server: static files of ROOT plus the sync routes.")
    ap.add_argument("--serve", required=True, metavar="ROOT")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    serve(args.serve, args.port)
