#!/bin/sh
# Opens the book catalog (index.html in this folder) for this computer AND your home network,
# so you can read on a phone or tablet connected to the same Wi-Fi.
# Only devices with home-network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) get an answer;
# files are only read, nothing can be changed. Anyone on your Wi-Fi who knows the address can open it.
# Stop it with Ctrl+C or by closing the terminal; it also stops by itself after
# 3 hours without use. Needs python3 (installed on most Linux systems).
cd "$(dirname "$0")" || exit 1
exec python3 - "$PWD" <<'PY'
import functools, http.server, ipaddress, os, posixpath, socket, subprocess, sys, threading, time, urllib.parse, urllib.request, webbrowser

ROOT, IDLE_HOURS = sys.argv[1], 3
sys.path.insert(0, ROOT)
import library_sync                       # accounts and reading-place sync, stored in .sync/
store = library_sync.SyncStore(ROOT)
def open_browser(url):
    if not os.environ.get("LIBRARY_NO_BROWSER"):
        webbrowser.open(url)
PORTS = range(48865, 48885)   # own range, so this and the computer-only launcher can run together
last_use = time.time()

# ---- who is using the library: shown in this window
# The catalog and the reader send a small "still here" every minute and "bye" when the page
# closes; a device that sends nothing for SILENT seconds is shown as gone.
SILENT, BYE_GRACE = 180, 10   # going from the catalog to a book closes one page and opens another
devices, refused, lock = {}, set(), threading.Lock()

def say(line):
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}  {line}", flush=True)

def device_kind(ua):
    ua = ua or ""
    system = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua else
              "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "")
    browser = ("Edge" if "Edg" in ua else "Yandex" if "YaBrowser" in ua else "Opera" if "OPR/" in ua else
               "Firefox" if "Firefox" in ua or "FxiOS" in ua else "Chrome" if "Chrome" in ua or "CriOS" in ua else
               "Safari" if "Safari" in ua else "")
    return " · ".join(x for x in (system, browser) if x) or "unknown browser"

def host_name(ip):
    """The device's name in the home network, if the router knows it (waits at most 0.7 s)."""
    found = []
    def look():
        try:
            found.append(socket.gethostbyaddr(ip)[0])
        except OSError:
            pass
    t = threading.Thread(target=look, daemon=True)
    t.start()
    t.join(0.7)
    return found[0] if found and found[0] != ip else ""

def plain_ip(addr):
    return addr[7:] if addr.startswith("::ffff:") else addr

def minutes(sec):
    return f"{round(sec / 60)} min" if sec >= 90 else f"{round(sec)} s"

def seen(ip, ua, path):
    """Note a request; print new devices and opened books."""
    kind, now = device_kind(ua), time.time()
    key = (ip, kind)
    with lock:
        d = devices.get(key)
        new = d is None
        if new:
            d = devices[key] = {"first": now, "label": f"{ip} · {kind}", "book": "", "bye": 0}
        d["last"] = now
        d["bye"] = 0
        count = len(devices)
    if new:
        name = "this computer" if ip in ("127.0.0.1", "::1") else host_name(ip)
        d["label"] = f"{ip}{f' ({name})' if name else ''} · {kind}"
        say(f"+ {d['label']} — connected    [devices: {count}]")
    if path.lower().endswith(".epub"):
        d["book"] = posixpath.basename(path)[:-5]
        say(f"📖 {d['label']} — «{d['book']}»")

def bye(ip, ua):
    with lock:
        d = devices.get((ip, device_kind(ua)))
        if d:
            d["bye"] = time.time()

def watch_devices():
    while True:
        time.sleep(2)
        now, gone = time.time(), []
        with lock:
            for key, d in list(devices.items()):
                if d["bye"] and now - d["bye"] > BYE_GRACE and d["last"] <= d["bye"]:
                    why = "closed the page"
                elif now - d["last"] > SILENT:
                    why = f"no signal for {minutes(SILENT)} (screen off, app in the background or out of Wi-Fi)"
                else:
                    continue
                del devices[key]
                gone.append((d, why, len(devices)))
        for d, why, count in gone:
            book = f", was reading «{d['book']}»" if d["book"] else ""
            say(f"− {d['label']} — {why}; was here {minutes(d['last'] - d['first'])}{book}    [devices: {count}]")

def is_home(addr):
    """This computer or a home-network address; nothing from the internet."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.version == 4:
        return ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
    return ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("fc00::/7")

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.unquote(self.path.split("?")[0])
        if path != "/__library_root":
            seen(plain_ip(self.client_address[0]), self.headers.get("User-Agent"), path)
        if library_sync.is_private(path):
            self.send_error(404)
            return
        if self.path.startswith("/__sync/"):
            self.sync()
            return
        if path == "/__alive":                # "still here" from the catalog / reader
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/__library_root":   # lets a launcher recognise this library
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

    sync_status = 0

    def send_response(self, code, message=None):
        self.sync_status = code
        super().send_response(code, message)

    def sync(self):
        """Sync routes; sign-ins and registrations are shown in the window (never passwords)."""
        path = self.path.split("?")[0]
        library_sync.respond(self, store)
        if path in ("/__sync/register", "/__sync/login"):
            ip = plain_ip(self.client_address[0])
            what = "registration" if path.endswith("register") else "sign-in"
            ok = self.sync_status == 200
            say(f"🔑 {ip} · {device_kind(self.headers.get('User-Agent'))} — {what} {'ok' if ok else 'refused'} ({self.sync_status})")

    def list_directory(self, path):          # no folder listings on the network
        self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/__sync/"):
            self.sync()
        elif self.path.split("?")[0] == "/__bye":   # the page is closing
            bye(plain_ip(self.client_address[0]), self.headers.get("User-Agent"))
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(405)

    def do_PUT(self):
        if self.path.split("?")[0] == "/__sync/data":
            self.sync()
        else:
            self.send_error(405)

    def do_DELETE(self):
        self.send_error(405)

    def log_message(self, *args):
        global last_use
        last_use = time.time()

class Server(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:   # IPv4 and IPv6 on one socket
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()

    def verify_request(self, request, client_address):
        if is_home(client_address[0]):
            return True
        ip = plain_ip(client_address[0])
        if ip not in refused:   # say it once per address
            refused.add(ip)
            say(f"⛔ refused {ip} — not a home-network address")
        return False

def is_this_library(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__library_root", timeout=3) as r:
            return r.read().decode() == ROOT
    except Exception:
        return False

def home_addresses():
    """Addresses of this computer in the home network: what to type on the phone."""
    found = []
    try:
        found += subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split()
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 9))   # nothing is sent; only picks the outgoing address
            found.append(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for a in found:
        if ":" not in a and is_home(a) and not a.startswith(("127.", "169.254.")) and a not in out:
            out.append(a)
    return out

server = None
for port in PORTS:
    if is_this_library(port):             # already running: just open the catalog
        open_browser(f"http://localhost:{port}/index.html")
        sys.exit(0)
    try:
        server = Server(("::", port), functools.partial(Handler, directory=ROOT))
        break
    except OSError:                       # port used by another program
        continue
if server is None:
    sys.exit("Could not start: ports 48865-48884 are busy.")

def stop_when_idle():
    while time.time() - last_use < IDLE_HOURS * 3600:
        time.sleep(60)
    server.shutdown()

addresses = home_addresses()
print("\n  Library is open on your home network.\n")
if addresses:
    print("  On a phone or tablet in the same Wi-Fi open:")
    for a in addresses:
        print(f"      http://{a}:{port}/")
    if len(addresses) > 1:
        print("  (several addresses: this computer has several networks; try the 192.168... one first)")
else:
    print("  This computer doesn't seem to be in a home network (no 192.168.x.x / 10.x.x.x address).")
print(f"\n  On this computer:  http://localhost:{port}/\n  Folder:            {ROOT}\n")
print("  The phone can't connect? Check that a firewall (ufw, firewalld) allows port", port)
print("  Anyone on this Wi-Fi who knows the address can open the library; only reading places are written")
print("  (to the .sync folder), and only by signed-in devices.")
print(f"  Library code (to create an account on a new device): {store.code()}")
print("  Press Ctrl+C or close this window to stop.", flush=True)
print("\n  Devices using the library:", flush=True)
threading.Thread(target=stop_when_idle, daemon=True).start()
threading.Thread(target=watch_devices, daemon=True).start()
threading.Timer(0.3, open_browser, [f"http://localhost:{port}/index.html"]).start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
PY
