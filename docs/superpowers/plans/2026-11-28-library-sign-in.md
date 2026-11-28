# Library Sign-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The network library gives nothing but a sign-in page to anyone not signed in; a cookie holding the existing device token lets a signed-in browser in.

**Architecture:** The gate lives in the shared sync modules: `library_sync.py` (Python: `signed_in`, `is_local`, `gate`, cookies in `respond`, `--serve --gate` for checks) and `library-sync.ps1` (PowerShell twin). The network launchers call it before anything else. The sign-in page is one self-contained file, `launchers/signin.html`, copied next to the catalog like the other launcher files and served inline (status 401) for any closed page.

**Tech Stack:** Python 3 `http.server`, PowerShell 5.1, plain browser JS; pytest; headless Chrome checks (`tests/reader/cdp.mjs`).

**Spec:** `docs/superpowers/specs/2026-11-28-library-sign-in-design.md`

## Global Constraints

- Cookie: name `libsess`, value = the 64-hex device token, `HttpOnly; SameSite=Lax; Path=/; Max-Age=34560000` (400 days); not `Secure`. Cleared with the same attributes and `Max-Age=0`.
- Local = client address 127.0.0.1 / ::1 / ::ffff:127.0.0.1 **and** `Host` (port stripped) is `localhost`, `127.0.0.1` or `[::1]`. Local requests are never gated.
- Open without sign-in: `GET/HEAD /signin.html`, `POST /__sync/login`, `POST /__sync/register`, `POST /__sync/session`, `POST /__sync/logout`. `/__library_root`: local only. Everything else closed.
- Closed answer: a navigation (`Sec-Fetch-Mode: navigate`, or `Accept` contains `text/html`, or method GET on a path ending `/` or `.html`) → 401 with the sign-in page as body (`text/html; charset=utf-8`, `Cache-Control: no-store`); otherwise → 401, empty body.
- `POST /__sync/session` with a valid Bearer → 204 + cookie; else 401.
- `/__sync/login` and `/__sync/register` 200 → also set the cookie (token from the JSON answer). `/__sync/logout` → always clears the cookie, whatever its status.
- Signed in = valid `libsess` cookie or valid `Authorization: Bearer` (same account-file lookup as `_auth`).
- Log (network windows): a closed request logs `⛔ <label> — not signed in` (Windows: `! <label> — not signed in`) once per device until it is let in; closed requests never count as a device "connected" or "opened a book".
- Page copy (English): heading = library title from the catalog (`document.title` of `/index.html` is not reachable — use "Library"); tabs "Sign in" / "Create account"; error texts = the catalog's `ACCT_ERR`. Not a secure context (and not localhost) → no form, only: "Open the library by its https:// address to sign in."
- No real book or person names in tests/docs ("Jane Doe - A Quiet Harbour" style).

## Review Focus

1. A request from a tunnel/router: 127.0.0.1 client but a foreign `Host` → must be closed (Task 1 test).
2. Logout with an already-revoked token → cookie still cleared (Task 1 test).
3. Offline app: a 401 on `.index/library.json` or a book must not delete cached books (existing catalog code treats non-OK as offline — Task 4 check reads it; no change expected).
4. A device signed in before the change (token in localStorage, no cookie) gets in without the form (Task 2 browser check).
5. The PowerShell twin answers the same as Python for each Global Constraint line (Task 3 static checks + checklist).

---

### Task 1: The gate in `library_sync.py`

**Files:** Modify `launchers/library_sync.py`; Test `tests/test_library_gate.py` (new).

**Interfaces — produces:**
- `SyncStore.signed_in(headers: dict) -> str | None` (login), cookie or Bearer; in-memory cache token→(login, expiry 60 s), dropped entirely on any logout.
- `SyncStore` route `/__sync/session` (POST).
- `is_local(handler) -> bool`.
- `gate(handler, store, page: Path) -> bool` — True: go on; False: the 401 answer is already written.
- `respond(handler, store)` sets/clears the cookie per Global Constraints.
- `serve(root, port, gate=False)`; CLI `--gate`.

- [ ] Step 1: tests (server = `library_sync.py --serve ROOT --port 0 --gate` subprocess on a tmp library with `index.html`, `signin.html`, `.index/library.json`, `Jane Doe - A Quiet Harbour.epub`; code from `.sync/code.txt`): local Host open; `Host: library.example.com` → `/` with `Accept: text/html` 401 + body contains `signin` marker, `/.index/library.json` 401 empty, book 401, `/__sync/data` 401, `/__library_root` 401/404, `/signin.html` 200; register → `Set-Cookie: libsess=` → cookie opens index + book; Bearer → `/__sync/session` 204 + cookie; logout → `Max-Age=0` and the old cookie closed; logout with junk token → still `Max-Age=0`.
- [ ] Step 2: run, expect FAIL (`--gate` unknown).
- [ ] Step 3: implement.
- [ ] Step 4: run Task 1 tests + `pytest -q` all green.
- [ ] Step 5: commit `library_sync: sign-in gate (cookie), --serve --gate`.

### Task 2: The sign-in page

**Files:** Create `launchers/signin.html`; Test `tests/reader/signin.mjs` (new); Modify `tests/reader/run.sh` (a second server with `--gate`, run `signin.mjs`).

- [ ] Step 1: `signin.mjs <gatedBase>`: Chrome with `--host-resolver-rules=MAP library.test 127.0.0.1`; (a) `http://library.test:<port>/` → hint, no password field; (b) with `--unsafely-treat-insecure-origin-as-secure=http://library.test:<port>`: form; wrong password → "Wrong email or password."; register with the code → lands on the catalog (`index.html` content), `localStorage['librarySync:account']` has the token; (c) a fresh browser with only that localStorage account set (init script) → lands on the catalog without the form; (d) `LibrarySync.logout()` then reload → the sign-in page.
- [ ] Step 2: run, expect FAIL.
- [ ] Step 3: implement the page (inline CSS/JS, light/dark by `prefers-color-scheme`, `credentials: "same-origin"` fetches).
- [ ] Step 4: run, ALL OK.
- [ ] Step 5: commit `Sign-in page for the network library`.

### Task 3: Network launchers

**Files:** Modify `launchers/Open Library (network).sh`, `launchers/library-sync.ps1`, `launchers/Open Library (network).bat`, `tests/test_sync_servers.py`, `tests/test_windows_launchers.py`, `tests/windows-sync-checklist.md`.

- [ ] Step 1: test in `test_sync_servers.py`: the network `.sh` with `Host: library.example.com` → closed answers as Task 1; `⛔`… `not signed in` once per device; after sign-in via cookie the book is served and logged `📖`; no token in the log. Static test: the `.bat` calls the PowerShell gate before serving files, and `library-sync.ps1` knows `/__sync/session`, `libsess`, `Max-Age=0`.
- [ ] Step 2: run, expect FAIL.
- [ ] Step 3: implement `.sh` (use `library_sync.gate`); PowerShell: `Test-SyncSignedIn($store, $headers)`, session route, answers carry an optional `Cookie` header line, the `.bat` gate + 401 page from `$root\signin.html`, `/__library_root` local only, log.
- [ ] Step 4: `pytest -q` green.
- [ ] Step 5: commit `Network launchers: only signed-in devices`.

### Task 4: Sign-out reload, README

**Files:** Modify `make_server.py` (catalog sign-out → `location.reload()`), `reader/reader.html` (same), `README.md` (network section: sign-in needed, https at home, code change keeps accounts; files list: `signin.html`).

- [ ] Step 1: extend `signin.mjs` (d) to sign out through the catalog's button → sign-in page shown.
- [ ] Step 2: FAIL; Step 3: implement; Step 4: `tests/reader/run.sh "<book>"` exit 0, `pytest -q` green.
- [ ] Step 5: commit `Sign-out shows the sign-in page; README`.
