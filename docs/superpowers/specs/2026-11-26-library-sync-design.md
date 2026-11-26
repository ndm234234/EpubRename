# Sync between devices through the library's own server, with accounts

## Intent

Reading should not be tied to one device. Two scenarios, both required:

1. **Continue elsewhere.** Open a book on another phone and it opens where it was left on the first.
2. **Lose nothing.** After a phone is replaced or its browser data cleared, signing in brings back
   every reading place, bookmark, bookmark name and note, and the "recently opened" list.

Several people use one library, each with their own places and bookmarks, so there are accounts
(login + password). Sync is **optional**: without signing in everything works exactly as today.

How the library is served today: `Open Library (network).bat` on a Windows computer runs a small
web server in PowerShell (no install) on the home network; the router publishes it over https
(the server sees the router's home-network address, so it lets the requests in). The library may
therefore be reachable from the internet. `Open Library.bat` serves the same folder to the
computer only; `.sh` twins of both do the same in Python on Linux/Mac. The launchers live in
`launchers/` and `make_server.py` copies them next to `index.html`.

## Goals

- Accounts: anyone who knows the **library code** can create an account (email, password) from the
  catalog or the reader; then sign in once per device, which stays signed in until "Sign out".
- Synced, shared by all devices of an account:
  - the reading place of every book (`epubReader:book:<id>`: cfi, pct, title, time);
  - bookmarks with their names and notes (`epubReader:marks:<id>`);
  - the catalog's "recently opened" list (`epubLibrary:opened`: {href: time}).
- Synced **per kind of device** (phone / tablet / computer): a phone's settings follow to another
  phone, not to the tablet or a desktop browser:
  - reader settings (`epubReader:prefs`: theme, font, face, layout, typoTab);
  - catalog grouping, kept apart for a browser tab (`epubIndex`) and the installed app
    (`epubIndex:app`), as today.
- Synced **the same on every device** (owner's call after the first build): the catalog's sort
  (`epubIndex:sort` + `epubIndex:sort:time`, `prefs.all.catalogSort` in the doc; the later choice
  wins). "Recently opened" is offered, and the default, in every catalog.
- Automatic, no chores. Works offline: changes wait on the device and go when the server is back.
- All four launchers serve sync; Windows still needs nothing installed.

## Non-goals

- The books themselves (offline copies stay per device); cached positions (`epubReader:loc:*`);
  offline library state (`epubLibrary:offline`).
- Live updates while two devices have the same book open.
- Closing the books themselves behind a login (stays as today; may come later on the same accounts).
- An admin page. E-mail, password recovery by mail.

## Anti-goals (a failure even if it "works")

- Someone without the library code creating an account; anyone reading or changing another
  account's data.
- Passwords stored readable.
- A deleted bookmark coming back from another device.
- A device with old data moving the reading place backwards on the others.
- Sync making reading slower or breaking it when the computer is off or unreachable.
- Anything installed on Windows; data leaving the computer.

## Approaches

Where accounts and data live:

- **A. The library's own server (the launchers) — chosen.** Data in files next to the library on
  the computer. Works wherever the library opens; when the computer is off, devices wait.
- B. Google Drive (sign-in with Google, the app's hidden folder) — dropped: the owner didn't want a
  Google Cloud project, and per-person accounts are simpler on the own server.

How the server part is built:

- **1. In the existing launchers — chosen:** PowerShell (`.bat`) and Python (`.sh`). There are
  three servers today (`Open Library.bat`: HttpListener; the network `.bat`: raw TcpListener; both
  `.sh`: Python http.server); the sync part is one shared PowerShell file and one shared Python
  file that the launchers load. Nothing new
  to install on Windows. The cost (the same server logic twice) is kept small by a dumb server
  (below) and closed by one set of tests run against both.
- 2. One Python server for every system — dropped: Windows would need Python installed.

Accounts: **self-registration with a library code — chosen** (no admin work per person).
Dropped: accounts created only by the owner with a command (manual per person); profiles without
passwords (anyone from the internet could act as anyone).

## The server: storage, not logic

The server only keeps accounts and stores each account's sync document as an opaque blob with a
version number. **All merging happens in the browser** (one implementation, in JavaScript, unit
tested); the two servers stay small and identical in behaviour.

Endpoints (JSON over the same port, `POST` unless noted; all answer `401` for a missing/bad token):

- `POST /__sync/register` `{login, password, code}` → `{token}`; `403` wrong code, `409` login taken.
- `POST /__sync/login` `{login, password}` → `{token}`; `401` wrong login or password.
- `POST /__sync/logout` (token) → `204`; forgets that device's token.
- `GET /__sync/data` (token) → `{version, doc}` (`version: 0, doc: null` before the first write).
- `PUT /__sync/data` (token) `{version, doc}` → `{version}` (the new one) or `409 {version, doc}` if
  the stored version is not the one given (another device wrote in between: merge and retry).
- `POST /__sync/place` (token) `{book, place}` → `204`: saves one book's reading place inside the
  stored doc if its `time` is later than the stored one (and bumps the version). For the send when
  the page is hidden: a `keepalive` request may carry only ~64 KB and its answer can't be acted on,
  so the whole doc can't go then. (Builder check; owner's call to keep it.)
- The token goes in a header (`Authorization: Bearer …`).

Files, in the library folder (`<library>/.sync/`, never served as static files):

- `code.txt` — the library code. Made on first start; shown in the launcher's window at every
  start; the owner may edit it. The library may be reachable from the internet, so the code must
  not be guessable: 4 random words from a built-in list of common short English words plus 2
  digits (e.g. `river-paper-cold-moon-47`), about 40+ bits. Wrong codes are throttled for the
  whole library (not per login: every guess may use a new login): after 5 wrong codes in a row,
  registration pauses for a minute, doubling up to an hour; a right code resets it. A flood of
  wrong codes can delay genuine registrations; accepted.
- `accounts/<login>.json` — `{login, salt, hash, iterations, tokens: [hash of each device token]}`.
  Passwords: PBKDF2-SHA256 with a random salt (both PowerShell/.NET and Python have it). Tokens:
  random 32 bytes, stored only as their hash.
- `data/<login>.json` — `{version, doc}`.
- Logins are the person's **email** (changed from a free login name): case-insensitive (trimmed,
  stored lower-case), at most 128 characters, `local@domain` with `a–z 0–9 . _ + -` before the `@`
  and dot-separated `a–z 0–9 -` labels after it (see `library_sync.py`) — they name files. The JSON
  field is still called `login`. No mail is ever sent (nothing is verified).
- **Password reset without an admin page:** the owner deletes `accounts/<login>.json` (that signs
  out all of that person's devices). The person registers again with the same login and the code
  and gets their data back (`data/<login>.json` is kept). Documented in README and in the window.
- Writes are atomic (temp file + rename). `Open Library.bat` and the network one may run at the same
  time on one folder: the version check on `PUT` keeps them safe; a lock file guards the
  read-check-write.
- Repeated wrong passwords for one login: a growing delay (all requests come from the router's
  address, so per-address limits don't work).
- Body size limit (a few MB). The PowerShell server reads the request body as bytes by
  `Content-Length` (it reads headers as text today).
- The window logs sign-ins and registrations like it logs devices today (never passwords).

## The sync document (`doc`, `v: 1`)

```json
{
  "v": 1,
  "places": { "<book id>": { "cfi": "...", "pct": 0.21, "title": "...", "time": 1764150000000 } },
  "marks":  { "<book id>": { "<mark id>": { "cfi": "...", "text": "...", "name": "...", "note": "...",
                                            "time": 1764150000000, "edited": 1764150100000 },
                             "<mark id>": { "deleted": 1764150200000 } } },
  "opened": { "<book href>": 1764150000000 },
  "prefs":  { "phone": { "reader": {...}, "readerTime": 0, "catalogTab": {...}, "catalogTabTime": 0,
                         "catalogApp": {...}, "catalogAppTime": 0 },
              "tablet": {...}, "computer": {...} }
}
```

- `<book id>` is the id the reader already uses (identifier, else title, else file name).
- A bookmark's id: its creation time plus cfi (existing bookmarks get one on the first sync).
  Name/note edits set `edited`; a delete leaves `{deleted: time}` so it isn't brought back.
  Tombstones older than about a year may be dropped.

## Merging (in the browser; local + server → result, saved on both)

- A book's place: the later `time` wins.
- **The place's `time` changes only when the reader moves on purpose** (page turns, contents,
  search, slider, bookmark), not when a book is merely opened, re-laid out or resized; otherwise
  a device with an old place would make it the newest by opening the book.
- Bookmarks: union by id; for one id the latest of `time`/`edited`/`deleted` wins.
- Opened: per href, the later time.
- Prefs: per device kind and group (reader, catalogTab, catalogApp), the later time wins. The
  local side starts keeping "changed at" times for these.
- Clocks of devices may differ by seconds; acceptable.

## When it syncs

- On opening the reader or the catalog (signed in): fetch + merge before the book's saved place is
  used, so a book opens at the newest place. If the server doesn't answer within ~2 s, open from
  the local place.
- While reading: after a change, at most every ~30 s, and at once when the page is hidden
  (switching apps, locking the phone, closing the tab — `fetch` with `keepalive`).
- When the page becomes visible again: fetch + merge. If the open book has a newer place from
  another device (further on or back), offer it — "Continue from 34% (another device)?" — instead
  of jumping. In %: page numbers differ between screens and fonts.
- Unreachable or failing: keep a "not sent" mark, retry on the next trigger and on `online`.

## Interface

- Reader: an account button. Catalog: the logo and library name at the top left are the account
  button; a green check on the logo while signed in (grey while the library computer does not
  answer), the email and the last sync in its tooltip.
  Signed out: "Sign in" (email, password) and "Create
  account" (email, password twice, library code). Signed in: the email, the time of the last sync
  (or "waiting for the library computer"), "Sync now", "Sign out".
- Signing out keeps the device's local data; nothing is deleted on the server.
- First sign-in on a device merges its local data with the account's (nothing lost on either side).
- Errors show in that area, never as popups while reading.
- Device kind: the reader's touch check plus screen width (phone below ~600px).
- Accounts and sync are offered only on secure pages (`isSecureContext`: the router's https
  address and `localhost`). On plain `http://192.168…` addresses there is no account button (the catalog's name is a plain title), so
  a password never crosses the Wi-Fi unencrypted; README says to use the https address. (Each
  address keeps its own local storage anyway.)
- The sync files live in `<library>/.sync/` (`make_server.py` wipes `.index/` on every run). Both
  servers refuse any path through `.sync` (404).
- `sw.js` already leaves `/__…` requests alone (never cached).

## Left to the builder

- File layout of the browser code (one shared script for the catalog and the reader is the
  natural shape), UI placement and wording within the existing styles.
- The library code's exact format, PBKDF2 iterations, delays, timeouts, retry intervals.
- Tests: the merge is pure JS with node unit tests; one HTTP test set run against both servers
  (PowerShell runs on Linux as `pwsh` if available; otherwise the PowerShell server is checked on
  Windows by hand with a written checklist); the end-to-end flow in the headless-Chrome checks
  (`tests/reader/`), two browser profiles as two devices.
- No real/Russian book names in docs, tests, README (fictional examples only).
