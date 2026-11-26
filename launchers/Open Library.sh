#!/bin/sh
# Opens the book catalog (index.html in this folder) in the browser.
# A tiny web server runs on this computer only (127.0.0.1), so books open with one click.
# Stop it with Ctrl+C or by closing the terminal; it also stops by itself after
# 3 hours without use. Starting it again while it runs just opens the browser.
# Needs python3 (installed on most Linux systems).
cd "$(dirname "$0")" || exit 1
exec python3 - "$PWD" <<'PY'
import functools, http.server, os, sys, threading, time, urllib.request, webbrowser

ROOT, IDLE_HOURS = sys.argv[1], 3
sys.path.insert(0, ROOT)
import library_sync                       # accounts and reading-place sync, stored in .sync/
store = library_sync.SyncStore(ROOT)
def open_browser(url):
    if not os.environ.get("LIBRARY_NO_BROWSER"):
        webbrowser.open(url)
# Ports are tried in order and the first one is normally used: reading places are
# stored per address, so the same port keeps them. Ports used by other programs are skipped.
PORTS = range(48765, 48785)
last_use = time.time()

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/__sync/"):
            library_sync.respond(self, store)
        elif library_sync.is_private(self.path):
            self.send_error(404)
        elif self.path == "/__library_root":   # lets a launcher recognise this library
            body = ROOT.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_HEAD(self):
        if library_sync.is_private(self.path):
            self.send_error(404)
        else:
            super().do_HEAD()

    def do_POST(self):
        if self.path.startswith("/__sync/"):
            library_sync.respond(self, store)
        else:
            self.send_error(405)

    def do_PUT(self):
        if self.path.split("?")[0] == "/__sync/data":
            library_sync.respond(self, store)
        else:
            self.send_error(405)

    def log_message(self, *args):
        global last_use
        last_use = time.time()

def is_this_library(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__library_root", timeout=3) as r:
            return r.read().decode() == ROOT
    except Exception:
        return False

server = None
for port in PORTS:
    url = f"http://127.0.0.1:{port}/index.html"
    if is_this_library(port):             # already running: just open the catalog
        open_browser(url)
        sys.exit(0)
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                                 functools.partial(Handler, directory=ROOT))
        break
    except OSError:                       # port used by another program
        continue
if server is None:
    sys.exit("Could not start: ports 48765-48784 are busy.")

def stop_when_idle():
    while time.time() - last_use < IDLE_HOURS * 3600:
        time.sleep(60)
    server.shutdown()

threading.Thread(target=stop_when_idle, daemon=True).start()
threading.Timer(0.3, open_browser, [url]).start()
print(f"Library: {url}\nFolder:  {ROOT}\nLibrary code: {store.code()}\nPress Ctrl+C or close this window to stop.", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
PY
