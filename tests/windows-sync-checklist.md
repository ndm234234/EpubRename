# Windows sync checklist (the `.bat` launchers)

The build machine has no PowerShell, so `launchers/library-sync.ps1` and the two `.bat` servers
are checked by hand on Windows (Windows PowerShell 5.1, nothing installed). Only
`tests/test_windows_launchers.py` runs automatically: it checks syntax, the word list and
the hooks, and runs no PowerShell.

Use a test library (for example a folder with one book, `Jane Doe - A Quiet Harbour.epub`, built
with `python3 make_server.py <folder>`), so `library-sync.ps1` sits next to the `.bat` files.

## 1. Computer-only launcher

1. Double-click `Open Library.bat`. The window shows `Library:`, `Folder:` and
   `Library code: <four words>-<two digits>`; the catalog opens in the browser.
2. `<library>\.sync\code.txt` exists and holds the same code (UTF-8, no BOM).
3. In the browser, `http://localhost:<port>/.sync/code.txt` is a 404.
4. From the same computer (WSL or Python for Windows):
   `python3 tests/sync_contract.py http://127.0.0.1:<port> <library>/.sync/code.txt`
   Expected: every line `ok`, last line `all ok`.
5. The window shows `registration ok (200)` and `sign-in ok (200)` lines, never a password.
6. Close the window, start it again: the same code is printed.

## 2. Network launcher

1. Double-click `Open Library (network).bat`. Allow PowerShell for Private networks if Windows asks.
   The window shows the addresses and `Library code (to create an account on a new device): ...`.
2. From another computer in the home network:
   `python3 tests/sync_contract.py http://<windows-ip>:<port> <copy of code.txt>`
   (or `LIBRARY_CODE=<code> python3 tests/sync_contract.py http://<windows-ip>:<port>`).
   Expected: `all ok`.
3. The window logs `@ <ip> · <device> — registration ok (200): <login>` and `sign-in ok (200): <login>`
   lines and the device as connected; no password or device key anywhere.
4. Sign in on a phone (catalog → account): `sign-in ok (200): <login>`; opening a book then shows
   `> <ip> · Android · Chrome · <login> — «...»`. Close the window, start it again, open the catalog on
   the phone: one `* <ip> · Android · Chrome — <login>` line on its first sync, not one per sync.
   Sign out on the phone: `* <ip> · Android · Chrome · <login> — signed out`; a wrong password shows
   `sign-in refused (401): <login>`.
5. A phone in the same Wi-Fi still opens the catalog and a book (static files unchanged).
6. With both `.bat` files running on the same library, run the contract against each port in turn:
   both `all ok`.

## 3. Short-name check (only the `.bat` servers need it)

1. In `cmd`: `dir /x <library>` shows the short name of `.sync` (for example `SYNC~1`), if the
   drive keeps short names.
2. `http://localhost:<port>/SYNC~1/code.txt` (with the name shown) is a 404 on both launchers.

## 4. Cross-check with the Python store on the same folder

1. Register an account on the `.bat` (contract run or the catalog), e.g. `jane.doe@example.com`; also `con@example.com` (a
   device name before the `@` is fine: the file `con@example.com.json` must appear and work).
2. Stop the `.bat`. In WSL (or Python for Windows), on the same folder:
   `python3 <library>/library_sync.py --serve <library> --port 8765`
3. Sign in as `jane.doe@example.com` there (`POST /__sync/login`) and `GET /__sync/data`: the same version and
   document as written through the `.bat`.
4. Write through Python (`PUT /__sync/data`), stop it, start the `.bat` again, sign in there:
   it sees Python's version and document. Both accept each other's `accounts\*.json` (same salt,
   hash, iterations and token hashes).

## 5. Speed with a big account

On the library computer (WSL or Python for Windows), with each `.bat` running in turn:

    python3 tests/sync_timing.py http://127.0.0.1:<port> <library>/.sync/code.txt

It creates an account (a random `timing-...@example.com` email), uploads a document of about 1 MB (thousands
of bookmarks with notes, made-up books), then times `GET /__sync/data`, `PUT /__sync/data` and
`POST /__sync/place` three times each. Expected on a normal PC: each `ok`, the slowest of each well
under 1 s, last line `all under 1 s`. (A third argument sets another size in KB, e.g. `4096`.)
Then sign in on a phone (over https) with the email and password the script printed and open a
book: it still opens within about 2 s; turn a page, lock the phone, unlock it: no error shown.
Afterwards delete `.sync/accounts/timing-*@example.com.json` and `.sync/data/timing-*@example.com.json`.

## 6. Old .NET (only if a machine has .NET Framework older than 4.7.2)

Register and sign in once: the first sign-in compiles a small PBKDF2 helper (a second or two),
after which it works as on newer machines.

Report the result (or the failing lines) to the owner; until then the Windows part of the sync
is untested.
