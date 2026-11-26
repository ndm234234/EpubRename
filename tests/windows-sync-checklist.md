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
3. The window logs `@ <ip> · <device> — registration ok (200)` and `sign-in ok (200)` lines and the
   device as connected; no password anywhere.
4. A phone in the same Wi-Fi still opens the catalog and a book (static files unchanged).
5. With both `.bat` files running on the same library, run the contract against each port in turn:
   both `all ok`.

## 3. Short-name check (only the `.bat` servers need it)

1. In `cmd`: `dir /x <library>` shows the short name of `.sync` (for example `SYNC~1`), if the
   drive keeps short names.
2. `http://localhost:<port>/SYNC~1/code.txt` (with the name shown) is a 404 on both launchers.

## 4. Cross-check with the Python store on the same folder

1. Register an account on the `.bat` (contract run or the catalog), e.g. login `jane.doe`.
2. Stop the `.bat`. In WSL (or Python for Windows), on the same folder:
   `python3 <library>/library_sync.py --serve <library> --port 8765`
3. Sign in to `jane.doe` there (`POST /__sync/login`) and `GET /__sync/data`: the same version and
   document as written through the `.bat`.
4. Write through Python (`PUT /__sync/data`), stop it, start the `.bat` again, sign in there:
   it sees Python's version and document. Both accept each other's `accounts\*.json` (same salt,
   hash, iterations and token hashes).

## 5. Old .NET (only if a machine has .NET Framework older than 4.7.2)

Register and sign in once: the first sign-in compiles a small PBKDF2 helper (a second or two),
after which it works as on newer machines.

Report the result (or the failing lines) to the owner; until then the Windows part of the sync
is untested.
