# The network library only for people signed in

## Intent

The network library (`Open Library (network).sh` / `.bat`) is reached from the internet: the
owner's router forwards a port and does the HTTPS itself, then passes plain HTTP on to this
computer. Every such request arrives from the router's home address, so the launchers' "home
network only" filter lets the whole internet through: anyone who finds the address (scanners,
crawlers) can list the catalog and download every book. It is a private library.

After this change nothing of the library is given to anyone who is not signed in, except a
sign-in page. People sign in with the accounts that already exist for sync; new accounts still
need the library code, which the owner gives out.

## Goals

- Without a valid sign-in, every path answers with the sign-in page (or 401 for non-page
  requests): the catalog, `.index/library.json`, covers, the reader, fonts, books, `/__sync/data`.
- One sign-in per device, kept for a long time; the browser carries it by itself on every request
  (images, the EPUB download, the service worker's fetches), so the catalog, reader and offline
  app work as now once signed in.
- Devices already signed in for sync get in without typing the password again.
- Signing out (the existing "Sign out" in the catalog/reader) also closes the library to that
  device.
- This computer (requests from 127.0.0.1 / ::1) needs no sign-in, as before.
- Both launchers: the Python one (`.sh`) and the PowerShell one (`.bat`), same behaviour.

## Non-goals

- Throwing out people who already signed in (revoking all devices, per-person bans). Changing the
  library code only stops new accounts; existing accounts and signed-in devices stay. Possible
  later.
- The computer-only launchers (`Open Library.sh` / `.bat`): they serve 127.0.0.1 only, unchanged.
- HTTPS: the router provides it.

## Anti-goals

- Any book, cover or catalog listing given to a request without a valid sign-in.
- The owner's signed-in phones losing their downloaded books, reading places or bookmarks because
  of the switch (an offline app must not take a 401 as "the library is empty").
- A password or device key written to the window's log or into a URL.

## Approaches

1. **The server asks for a sign-in, a cookie carries it** (picked). The server gives a cookie on
   sign-in, holding the device token the sync already issues; every request without a valid one
   gets the sign-in page. Works for every kind of request, ties into the existing accounts and
   sign-out. Cost: both servers change; the PowerShell one can only be checked by hand here.
2. **A password on the router.** One shared password not tied to accounts; the browser's own
   login box, which installed web apps and offline mode handle badly; depends on the router model.
3. **HTTP Basic auth with the accounts.** No real sign-out, a browser box that phones and installed
   apps handle badly.

## How it works (decided)

- **The cookie** holds the same device token the sync already uses (`Authorization: Bearer`):
  sign-in (`/__sync/login`) and registration (`/__sync/register`) answers set it as well as
  returning the token as now. `HttpOnly`, `SameSite=Lax`, `Path=/`, long-lived (400 days, the
  browsers' cap). Not `Secure`: the server itself only sees plain HTTP (the router does HTTPS).
- **HTTPS only for passwords (the owner's choice):** at home too the library is opened by the
  router's `https://` address. Opened over plain `http://` (e.g. `http://192.168...`, not
  localhost), the sign-in page shows no form, only "Open the library by its https:// address" —
  a password never crosses the Wi-Fi in the clear (the catalog's account panel already hides on
  plain http for the same reason). The server can't tell the two apart (both arrive as plain
  HTTP from the router or a home address), so this is the page's check (`isSecureContext`), as in
  the catalog today.
- **Check:** a request is let in if it comes from 127.0.0.1/::1 *and* is addressed to
  `localhost`/`127.0.0.1`/`[::1]` (the `Host` header; my call: a tunnel on this computer that
  forwards the internet to 127.0.0.1 would keep its own host name and so stay closed), or carries a cookie (or a Bearer
  header) whose token belongs to an account. Token lookup is the existing one (account files);
  the servers may keep a short in-memory cache.
- **Open without sign-in:** the sign-in page and what it needs, `POST /__sync/login`,
  `POST /__sync/register`, the new cookie route below, and `/__library_root` (launchers use it to
  recognise a running library; it holds only the folder path — see open question in "Left to the
  builder"). Everything else is closed.
- **Closed answer:** a page navigation (HTML / `Accept: text/html` / `/`) gets the sign-in page
  with status 401; anything else gets a bare 401. The service worker already never caches a non-OK
  answer and falls back to its cache when the network fails; the offline catalog treats a non-OK
  `library.json` as "offline" and keeps its books (checked in the code: `sync()` in the catalog).
- **Already signed-in devices:** their token is in `localStorage` (`librarySync:account`). The
  sign-in page, on load, looks there; if a token is found it calls a new route
  `POST /__sync/session` with it as Bearer; a valid token gets the cookie (204) and the page
  reloads into the library. An invalid one is dropped and the form is shown.
- **Sign-out:** `/__sync/logout` (existing) also clears the cookie (`Max-Age=0`), always, even
  when the token it is given is no longer valid (my call: the cookie is `HttpOnly`, the page can't
  clear it). What the device already downloaded (the offline app's books, catalog, reader) stays
  on it: sign-out closes the server, not the device's own copies (my call: the anti-goal about
  downloaded books wins).
- **The sign-in page:** one small self-contained HTML page served by the server itself (inline CSS
  and JS, no other files), in the app's look (light/dark by the system), English like the rest of
  the UI: library title, "Sign in" (email, password) and "Create account" (email, password, password
  again, library code) — the same fields and error texts as the catalog's account panel. On
  success it stores the account in `localStorage` (`librarySync:account`, same shape as sync.js) so
  sync works at once, then loads the page that was asked for. It says nothing about the books.
- **The log** (network window): a closed request is logged once per device
  (`⛔ <ip> · <device> — not signed in`), not per file.
- **Throttling** of wrong passwords and codes stays as it is (it already exists for both routes).

## Left to the builder

- Where the gate sits in each server (shared helper in `library_sync.py` / `library-sync.ps1`, or
  in each network launcher), and the page's file location (a template next to the sync code, or
  generated by `make_server.py` into `.index/`; it must be served without a sign-in either way).
- `/__library_root`: keep it open only to 127.0.0.1 if the launchers only ask it from this
  computer (they do: `IsThisLibrary` / `is_this_library` call 127.0.0.1) — the builder checks and
  closes it to others if so.
- Tests: the Python server through the existing launcher tests (closed answers for each kind of
  path, sign-in → cookie → open, Bearer → session cookie, logout closes, 127.0.0.1 open,
  no token in the log); the PowerShell one by static checks plus steps in
  `tests/windows-sync-checklist.md`; the reader/catalog suites keep running against
  `library_sync.py --serve`, which is 127.0.0.1 and so open.
- README: the network section says the library now needs a sign-in, and that changing the code
  keeps existing accounts.
