#!/bin/sh
# Opens the book catalog (index.html in this folder) for this computer AND your home network,
# so you can read on a phone or tablet connected to the same Wi-Fi.
# Only devices with home-network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) get an answer;
# files are only read, nothing can be changed. Anyone on your Wi-Fi who knows the address can open it.
# Stop it with Ctrl+C or by closing the terminal; it also stops by itself after
# 3 hours without use. Needs python3 (installed on most Linux systems).
cd "$(dirname "$0")" || exit 1
exec python3 - "$PWD" <<'PY'
import functools, http.server, ipaddress, socket, subprocess, sys, threading, time, urllib.request, webbrowser

ROOT, IDLE_HOURS = sys.argv[1], 3
PORTS = range(48865, 48885)   # own range, so this and the computer-only launcher can run together
last_use = time.time()

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
        if self.path == "/__library_root":   # lets a launcher recognise this library
            body = ROOT.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def list_directory(self, path):          # no folder listings on the network
        self.send_error(404)

    def do_PUT(self):
        self.send_error(405)
    do_POST = do_DELETE = do_PUT

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
        return is_home(client_address[0])

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
        webbrowser.open(f"http://localhost:{port}/index.html")
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
print("  Anyone on this Wi-Fi who knows the address can open the library (read only).")
print("  Press Ctrl+C or close this window to stop.", flush=True)
threading.Thread(target=stop_when_idle, daemon=True).start()
threading.Timer(0.3, webbrowser.open, [f"http://localhost:{port}/index.html"]).start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
PY
