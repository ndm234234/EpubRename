# Library Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional accounts (login + password, self-registration with a library code) and automatic sync of reading places, bookmarks/notes, "recently opened" and per-device-kind settings between devices, stored by the library's own launcher servers.

**Architecture:** The servers (Python for the `.sh` launchers, PowerShell for the `.bat` ones) are a dumb store: accounts, a token per device, and one versioned JSON document per account, plus one "save this book's place" endpoint. All merging is in the browser, in one pure JS module shared by the reader and the catalog; a client module collects local data from `localStorage`, merges with the server's document, writes both ways.

**Tech Stack:** Python 3 stdlib (`http.server`, `hashlib.pbkdf2_hmac`, `secrets`), Windows PowerShell 5.1 / .NET (`Rfc2898DeriveBytes`, `RandomNumberGenerator`), browser JS without build step, Node 22 (`node:test`) for JS unit tests, pytest, the existing headless-Chrome checks in `tests/reader/`.

**Spec:** `docs/superpowers/specs/2026-11-26-library-sync-design.md` (read it first; this plan does not repeat its reasoning).

## Global Constraints

- Nothing new installed on Windows: PowerShell 5.1 only (no `pwsh`-only syntax: no `??`, no ternary, no `-Parallel`).
- No npm packages, no build step; Python stdlib only.
- Sync data lives in `<library>/.sync/` (never in `.index/`, which `make_server.py` wipes). Every server answers 404 to any path with a `.sync` segment.
- Passwords: PBKDF2-SHA256, random 16-byte salt, 200000 iterations, stored as hex; device tokens: 32 random bytes (hex), stored only as SHA-256 hex.
- Logins: `^[a-z0-9._-]{2,32}$` after lower-casing; anything else → 400.
- Library code: 4 words from a built-in list + 2 digits, `-`-joined (e.g. `river-paper-cold-moon-47`); created on first start into `.sync/code.txt`; printed in the launcher window at every start; compared trimmed and case-insensitive.
- Wrong library codes throttled library-wide: after 5 wrong in a row, registration answers 429 for 60 s, doubling per further wrong code up to 3600 s; a right code resets. Wrong passwords: per login, the same scheme.
- Request body limit 8 MB → 413.
- Accounts UI only when `isSecureContext` (https address, localhost); hidden on plain `http://192.168…`.
- Sync is optional: signed out, everything behaves exactly as today.
- No real/Russian book names in docs, tests, README (fictional examples only, e.g. "Jane Doe - A Quiet Harbour").
- Commit messages end with the session's attribution lines.

## Review Focus

- Two devices writing at the same moment (one gets 409): the loser merges and retries; nothing from either is lost — tested in Task 5 (client) and Task 2 (server version check).
- The library computer off or unreachable while reading: no delay over ~2 s on opening, no errors shown while reading, changes go out later — tested in Task 8.
- Existing data from before sync (bookmarks without `id`, places with old `time`): first sync creates no duplicates and moves no place backwards — tested in Task 4.
- Hostile logins and paths: `../x`, `a/b`, `CON`, 1 char, 33 chars, uppercase duplicates of an existing login; `/.sync/accounts/x.json` requested directly — tested in Task 1 and Task 2.
- A big account (doc > 64 KB) hidden right after a page turn: the place still reaches the server through `/__sync/place` — tested in Task 8.

---

### Task 0: Rename `make_index.py` to `make_server.py`

The script builds not only the catalog but the whole served library (reader, launchers, server code), so the owner renamed it. Plain rename, no behaviour change; old docs under `docs/superpowers/` dated before 2026-11-26's sync work keep the old name (history).

**Files:**
- Rename: `make_index.py` → `make_server.py` (`git mv`)
- Modify: `fix_cover.py:50` (`from make_server import …`) and `:911` (message), `epub_rename.py:259-265` (import and messages), `reader/sw.js:2` (comment), `README.md` (every `make_index.py`, the `## make_index.py` heading), the module docstring/usage line in `make_server.py`; any test importing `make_index`.

- [ ] **Step 1:** `git mv make_index.py make_server.py`; update the references above (`grep -rn make_index --exclude-dir=Out --exclude-dir=.git .` must list only old docs under `docs/superpowers/`).
- [ ] **Step 2: Run** `python3 -m pytest -q tests` — all pass; `python3 make_server.py Out` builds the catalog; `python3 epub_rename.py --help` and `python3 fix_cover.py --help` start without import errors.
- [ ] **Step 3: Commit** `rename make_index.py to make_server.py`.

### Task 1: Python sync store (`launchers/library_sync.py`)

**Files:**
- Create: `launchers/library_sync.py`
- Test: `tests/test_library_sync.py`

**Interfaces:**
- Produces:
  - `class SyncStore: __init__(self, library_root: Path)`; files under `library_root/".sync"`.
  - `SyncStore.code() -> str` (creates `code.txt` if missing).
  - `SyncStore.handle(method: str, path: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str, bytes]` (status, content type, body). Paths: `/__sync/register`, `/__sync/login`, `/__sync/logout`, `/__sync/data` (GET, PUT), `/__sync/place`; JSON shapes exactly as in the spec's "Endpoints".
  - `is_private(url_path: str) -> bool` — True if any decoded segment is `.sync`.
  - `respond(handler: http.server.BaseHTTPRequestHandler, store: SyncStore) -> None` — reads the body by `Content-Length` (413 over the limit), calls `handle`, writes the answer.
  - `python3 launchers/library_sync.py --serve ROOT --port N` — a test/dev server: static files of ROOT + sync, 127.0.0.1 only, no browser. Used by tests and by `tests/reader/run.sh`.
  - Login state: `accounts/<login>.json` `{login, salt, hash, iterations, tokens: [sha256 hex]}`; data: `data/<login>.json` `{version, doc}`; throttle state in memory.
  - Writes: temp file + `os.replace`; the read-check-write of `data/` and `accounts/` under an exclusive lock file `.sync/lock` (`fcntl.flock` on POSIX, `msvcrt.locking` fallback), so two processes on one folder are safe.

- [ ] **Step 1: Write the failing tests** in `tests/test_library_sync.py` (each on a `tmp_path` library):
  - `test_code_created_once_and_has_format`: `re.fullmatch(r"[a-z]+(-[a-z]+){3}-\d{2}", code)`; second `SyncStore` on the same folder returns the same code.
  - `test_register_login_data_roundtrip`: register `{"login":"Ann","password":"pw123456","code":code}` → 200 with `token`; `GET /__sync/data` with `Authorization: Bearer <token>` → `{"version":0,"doc":null}`; `PUT` `{version:0, doc:{"v":1}}` → `{"version":1}`; `GET` → `{"version":1,"doc":{"v":1}}`; login `ANN` with the password → new token that also reads the data.
  - `test_put_with_stale_version_is_409_with_current`: after version 1, `PUT {version:0,...}` → 409 and body `{version:1, doc:...}`.
  - `test_wrong_code_403_then_throttled_429`: 5 wrong codes → 403 each; 6th (even with the right code) → 429; after `store._now` is advanced 61 s the right code → 200.
  - `test_wrong_password_throttled_per_login`: same scheme for login, other logins unaffected.
  - `test_login_taken_409_case_insensitive`, `test_bad_logins_400` for `["a", "x"*33, "../x", "a/b", "a b", ""]`.
  - `test_logout_forgets_only_that_token`; `test_deleted_account_file_keeps_data`: delete `accounts/ann.json`, register `ann` again → `GET` returns the old doc.
  - `test_place_endpoint_later_time_wins`: `POST /__sync/place {book:"b1", place:{cfi:"c2",time:20}}` over a doc with `places.b1.time=10` → stored; with `time:5` → unchanged; version bumps only when stored; works when `doc` is null (creates `{"v":1,"places":{...}}`).
  - `test_no_token_401`, `test_body_limit_413` (via `respond` with a fake handler), `test_is_private` for `/.sync/code.txt`, `/%2Esync/x`, `/a/.sync`, and False for `/.index/reader/sync.js`.
  - `test_passwords_and_tokens_not_stored_plain`: the account file contains neither the password nor the token.

- [ ] **Step 2: Run** `python3 -m pytest -q tests/test_library_sync.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `launchers/library_sync.py` with the interfaces above; `_now` is an attribute (default `time.time`) so tests can move time; the word list is ~256 common short English words in the module.
- [ ] **Step 4: Run** the tests — Expected: all pass; also `python3 -m pytest -q tests` stays green.
- [ ] **Step 5: Commit** `launchers/library_sync.py`, `tests/test_library_sync.py`.

### Task 2: Sync in the `.sh` launchers + an HTTP contract test

**Files:**
- Modify: `launchers/Open Library.sh`, `launchers/Open Library (network).sh`
- Create: `tests/sync_contract.py` (HTTP checks against any running server: `python3 tests/sync_contract.py http://host:port` exits 0/1, prints ok/FAIL lines; also importable: `run_contract(base_url) -> list[str]` failures)
- Test: `tests/test_sync_servers.py`

**Interfaces:**
- Consumes: `SyncStore`, `respond`, `is_private` (Task 1). The launchers `sys.path.insert(0, ROOT)` and `import library_sync` (the file sits next to them in the library; `make_server.py`'s `install_launchers` already copies every file from `launchers/`).
- Produces: both `.sh` servers answer `/__sync/*` (GET/POST/PUT), 404 for `.sync` paths, print `Library code: <code>` in the start banner; env `LIBRARY_NO_BROWSER=1` skips opening the browser (for tests).
- `run_contract` covers: register/login/data/409/place/logout, 401 without token, 404 for `/.sync/code.txt`, and that a normal file (`/index.html`) is still served. It reads the code from `<library>/.sync/code.txt` given as a second argument, or from env `LIBRARY_CODE`.

- [ ] **Step 1: Write the failing test** `tests/test_sync_servers.py::test_python_launchers_pass_contract`: for each `.sh` launcher, copy `launchers/*` and a tiny `index.html` into `tmp_path`, start it with `LIBRARY_NO_BROWSER=1`, find its port by probing its range for `/__library_root` == tmp path (ranges 48765–48784 and 48865–48884), `assert run_contract(url) == []`, terminate it.
- [ ] **Step 2: Run** `python3 -m pytest -q tests/test_sync_servers.py` — Expected: FAIL.
- [ ] **Step 3: Implement**: in both `.sh` handlers route `/__sync/` to `library_sync.respond`, refuse `is_private` paths in `do_GET`/`do_HEAD` with 404, allow `do_PUT` for `/__sync/data` only, print the code, honour `LIBRARY_NO_BROWSER`. Also make `library_sync.py --serve` pass `run_contract` (same test, third case).
- [ ] **Step 4: Run** — Expected: PASS (3 cases), full `pytest -q tests` green.
- [ ] **Step 5: Commit**.

### Task 3: Sync in the `.bat` launchers (PowerShell 5.1)

**Files:**
- Create: `launchers/library-sync.ps1`
- Modify: `launchers/Open Library.bat` (HttpListener), `launchers/Open Library (network).bat` (raw TcpListener)
- Create: `tests/windows-sync-checklist.md`

**Interfaces:**
- Produces: `library-sync.ps1` defines `New-SyncStore([string]$root)` and `Invoke-SyncRequest($store, [string]$method, [string]$path, [hashtable]$headers, [byte[]]$body)` → `@{ Status = [int]; Type = [string]; Body = [byte[]] }`, `Test-SyncPrivate([string]$path) -> bool`, `Get-SyncCode($store) -> string`. Same files, formats, hashes, limits and throttles as Task 1 — a `.bat` and a `.sh` must be able to work on the same `.sync/` folder (same PBKDF2 parameters and hex encoding, same JSON keys).
- The `.bat` files dot-source `library-sync.ps1` from the library root. The network `.bat` reads the request body as bytes by `Content-Length` (today it reads headers with an ASCII `StreamReader`: read headers byte-wise or from the same buffered stream so no body bytes are lost). Lock: `[IO.File]::Open(".sync\lock", OpenOrCreate, ReadWrite, None)` with retry.

- [ ] **Step 1:** If `pwsh` is available on the build machine, extend `tests/test_sync_servers.py` with a case that starts the `.bat`'s PowerShell part under `pwsh` and runs `run_contract`; mark it `skipif(shutil.which("pwsh") is None)`.
- [ ] **Step 2: Implement** `library-sync.ps1` and hook both `.bat` servers (route `/__sync/`, 404 for private paths, print the code in the banner).
- [ ] **Step 3: Write** `tests/windows-sync-checklist.md`: on Windows run each `.bat`; from another computer in the home network run `python3 tests/sync_contract.py http://<windows-ip>:<port> <library>/.sync/code.txt` (or `LIBRARY_CODE=…`) — expected `ALL OK`; then the cross-check: register on the `.bat`, sign in to the same folder through `library_sync.py --serve` on Linux/WSL → same data.
- [ ] **Step 4: Run** the pwsh case if available; otherwise state in the commit message that the Windows checklist is pending and ask the owner to run it.
- [ ] **Step 5: Commit**.

### Task 4: Merge core in the browser (`reader/sync-merge.js`)

**Files:**
- Create: `reader/sync-merge.js` (classic script: sets `globalThis.SyncMerge`; no DOM, no storage)
- Test: `tests/sync/merge.test.mjs` (`node --test tests/sync`; loads the script with `createRequire`/`vm` and reads `globalThis.SyncMerge`)

**Interfaces:**
- Produces `SyncMerge`:
  - `emptyDoc() -> {v:1, places:{}, marks:{}, opened:{}, prefs:{}}`
  - `merge(a: Doc|null, b: Doc|null) -> Doc` — commutative and idempotent; rules from the spec's "Merging".
  - `markId(m) -> string` = `m.id || (m.time + "|" + m.cfi)`.
  - `pruneTombstones(doc, now, maxAgeMs = 365*24*3600*1000) -> Doc`.

- [ ] **Step 1: Write the failing tests**: `merge(a,b)` deep-equals `merge(b,a)` and `merge(a, merge(a,b))` for random-ish fixtures; place with later time wins; bookmark union; tombstone newer than `edited` removes, edit newer than tombstone keeps; same bookmark without `id` on both sides (old data) → one bookmark; opened per href max; prefs per kind and group by `*Time`; `merge(null, x)` = x; unknown extra keys survive.
- [ ] **Step 2: Run** `node --test tests/sync` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**.

### Task 5: Sync client (`reader/sync.js`)

**Files:**
- Create: `reader/sync.js` (classic script, after `sync-merge.js`; sets `globalThis.LibrarySync`)
- Test: `tests/sync/client.test.mjs` (node, with a fake `localStorage` and a fake `fetch`)

**Interfaces:**
- Consumes: `SyncMerge` (Task 4); the `/__sync/*` API (Task 1).
- Produces `LibrarySync`:
  - `available() -> boolean` (`isSecureContext`).
  - `deviceKind() -> "phone"|"tablet"|"computer"`: coarse pointer + no hover → phone if `Math.min(screen.width, screen.height) < 600`, else tablet; otherwise computer.
  - `account() -> {login} | null`; `register(login, password, code)`, `login(login, password)`, `logout()` → `Promise<{ok, error?}>` (error strings: `"wrong-code"`, `"taken"`, `"bad-login"`, `"wrong-password"`, `"wait"`, `"offline"`). Token and login kept in `localStorage["librarySync:account"]`.
  - `collectLocal() -> Doc` from localStorage: `epubReader:book:<id>` → places; `epubReader:marks:<id>` + `epubReader:marksGone:<id>` (`{markId: deletedTime}`) → marks; `epubLibrary:opened` → opened; `epubReader:prefs` + `epubReader:prefsTime`, `epubIndex` + `epubIndex:time`, `epubIndex:app` + `epubIndex:app:time` → `prefs[deviceKind()]` (`reader`, `catalogTab`, `catalogApp`).
  - `applyLocal(doc)` — the reverse; only this device kind's prefs are applied; bookmarks written back sorted as the reader sorts them (by cfi).
  - `syncNow({timeoutMs}) -> Promise<"ok"|"offline"|"signed-out">`: GET, merge with local, apply local, PUT with the server's version; on 409 merge the returned doc and retry (max 3). Remembers `librarySync:lastSync` and a `librarySync:dirty` flag.
  - `changed()` — marks dirty and schedules `syncNow` at most every 30 s; retries on `online`.
  - `sendPlaceNow(bookId, place)` — `fetch("/__sync/place", {method:"POST", keepalive:true, …})`, for `pagehide` / `visibilitychange: hidden`.
  - `onRemoteChange(fn)` — called after a sync that changed something local: `fn({places: [bookId…], prefs: bool, opened: bool})`.

- [ ] **Step 1: Write the failing tests**: collect/apply round-trip leaves localStorage unchanged; sync with a server doc merges both ways; 409 then success → merged result has both devices' marks; offline fetch → `"offline"`, dirty kept, no throw; signed out → no fetch at all; `onRemoteChange` reports a book whose place got newer.
- [ ] **Step 2: Run** `node --test tests/sync` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit**.

### Task 6: Reader: local data for sync, wiring, account panel

**Files:**
- Modify: `reader/reader.html`
- Test: `tests/reader/sync.mjs` (started in Task 8; this task adds its first part) — or extend `tests/reader/place.mjs` for the `time` rule.

**Interfaces:**
- Consumes: `LibrarySync` (Task 5).
- Changes in `reader.html`:
  - Load `sync-merge.js` and `sync.js` before the main script.
  - `savePlace(deliberate)`: `time` is set to `Date.now()` only when `deliberate` (calls from `byReader` and bookmark/hit/slider moves); a re-layout or opening keeps the stored `time`. Then `LibrarySync.changed()`.
  - Bookmarks: new ones get `id` (= `markId` at creation); name/note edits set `edited`; deleting writes `epubReader:marksGone:<bookId>[markId] = Date.now()`; `saveMarks()` calls `LibrarySync.changed()`.
  - `savePrefs()` also writes `epubReader:prefsTime = Date.now()` and calls `changed()`. The "opened" update (where `epubLibrary:opened` is written) calls `changed()`.
  - Before the saved place is read on opening: `await LibrarySync.syncNow({timeoutMs: 2000})` if signed in.
  - `visibilitychange` visible → `syncNow()`; if `onRemoteChange` reports the open book with a newer place: `showToast("Continue from 34% (another device)?", () => displayAt(cfi))` (the % from `pct`). Hidden / `pagehide` → `sendPlaceNow(bookId, place)`.
  - Account: a button in the toolbar next to the others (`#acctBtn`, hidden unless `LibrarySync.available()`), opening a small panel in the style of `#typo`: signed out — "Sign in" (login, password) and "Create account" (login, password ×2, library code), errors inline; signed in — login, "Last synced …" / "Waiting for the library computer", "Sync now", "Sign out". On phones it slides from the top like the Aa sheet.

- [ ] **Step 1: Write the failing check** (headless Chrome, `tests/reader/sync.mjs` part 1): after opening a book and waiting 3 s without turning, `epubReader:book:<id>.time` is unchanged; after one page turn it is newer; deleting a bookmark writes `marksGone`; on `http://` (non-secure) `#acctBtn` is hidden, on `http://127.0.0.1` (secure) it is shown.
- [ ] **Step 2: Run** `node tests/reader/sync.mjs <base> <book>` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it and the existing checks (`tests/reader/run.sh <book>`) — all `ALL OK`.
- [ ] **Step 5: Commit**.

### Task 7: Catalog wiring (`make_server.py`)

**Files:**
- Modify: `make_server.py` (catalog template and `install_reader` unchanged — `sync*.js` are copied with the reader)
- Test: `tests/reader/sync.mjs` part 2

**Interfaces:**
- Consumes: `LibrarySync` (Task 5), loaded from `.index/reader/sync-merge.js` and `.index/reader/sync.js`.
- Changes: `save()` of catalog prefs also writes `<PREFS>:time` and calls `changed()`; on load, signed in: `syncNow({timeoutMs: 2000})` then re-read `openedTimes()` and prefs and re-render; the same account button and panel as the reader (shared markup/CSS can be duplicated in the template; the logic lives in `sync.js`).

- [ ] **Step 1: Write the failing check**: catalog shows the account button on a secure origin; after device A opens a book, device B's catalog (installed-app mode stubbed as in earlier checks) lists it first under "Recently opened" after reload.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 -m pytest -q tests`, `node --test tests/sync`, the check — all pass.
- [ ] **Step 5: Commit**.

### Task 8: Two-device end-to-end checks + `run.sh`

**Files:**
- Modify: `tests/reader/sync.mjs` (part 3), `tests/reader/run.sh` (serve with `python3 launchers/library_sync.py --serve . --port $PORT` so the checks have sync; add `sync.mjs` to the list)

- [ ] **Step 1: Write the checks** with two browser profiles A and B (two `launch()` calls) on `http://127.0.0.1:<port>`:
  - register on A (code read from `<root>/.sync/code.txt`), sign in on B;
  - A: turn 10 pages, add a bookmark with a note, change the font (phone); B (phone): open the same book → at A's place (same first word), bookmark and note present, font as on A;
  - B: delete the bookmark; A: back to foreground (`visibilitychange`) → bookmark gone, not resurrected after A syncs again;
  - B as tablet (`W=820 H=1180`): fonts not taken from the phones;
  - A: turn pages, then hide the page immediately with a large account (seed 300 bookmarks with notes so the doc is > 64 KB) → B opens at the new place (the `/__sync/place` path);
  - stop the server; A turns pages: no error shown, opening a book takes < 3 s; start the server; within one sync B sees A's place;
  - B in foreground with the book open while A moves → toast "Continue from N% (another device)?" appears, tapping it moves B.
- [ ] **Step 2: Run** `node tests/reader/sync.mjs http://127.0.0.1:<port> "<book>"` — fix until `ALL OK`.
- [ ] **Step 3: Run** the whole set: `tests/reader/run.sh "<book>" "<book2>"`, `python3 -m pytest -q tests`, `node --test tests/sync`.
- [ ] **Step 4: Commit**.

### Task 9: Docs and rebuild

**Files:**
- Modify: `README.md` (reader section: accounts and sync; launchers: the library code in the window, password reset by deleting `.sync/accounts/<login>.json`, use the https address, back up `.sync/`), `make_server.py` docstring/help if it lists launcher files, `.gitignore` (`.sync/`)

- [ ] **Step 1:** Write the README parts (fictional names only).
- [ ] **Step 2:** `python3 make_server.py Out` — the library gets `library_sync.py`, `library-sync.ps1` and the new launchers.
- [ ] **Step 3:** Ask the owner to run `tests/windows-sync-checklist.md` on Windows if Task 3 had no `pwsh`.
- [ ] **Step 4: Commit and push.**
