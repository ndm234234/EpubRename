// Pictures in sepia and dark, in headless Chrome.
//   node tests/reader/pictures.mjs [base]
// The check makes a book of its own (pdf2epub's writer, three pictures drawn here: a page of black
// text on white, a colour photo, a grey photo full of middle greys) and serves it with its own
// library_sync.py on a free port, so [base] is not needed and not used.
//   1. the page of ink on paper is marked "ink"; the colour photo and the grey photo are not
//   2. light: no picture is recoloured
//   3. sepia: the ink page is blended into the page colour (its white turns sepia), the photos not
//   4. dark: the ink page is inverted (dark paper, light ink), the photos only dimmed
//   5. what the eye sees: the ink page's paper is the page's own colour in sepia and dark
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };

// ---- the book: three PNGs (drawn with the standard library) in an EPUB made by pdf2epub's writer
const LIB = mkdtempSync(join(tmpdir(), "epub-pictures-"));
const MAKE = `
import sys, zlib, struct, random
from pathlib import Path
sys.path.insert(0, ".")
import pdf2epub
lib = Path(sys.argv[1])
def png(path, w, h, pixel):
    rows = b"".join(b"\\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return str(path)
random.seed(1)
ink = png(lib / "ink.png", 200, 260, lambda x, y: (20, 20, 20) if 30 < x < 170 and y % 14 < 2 and y > 40 else (255, 255, 255))
photo = png(lib / "photo.png", 200, 150, lambda x, y: (40 + x, 90 + y // 2, 200 - x // 2))
grey = png(lib / "grey.png", 200, 150, lambda x, y: (v := 60 + (x + y) % 140, v, v))
B = pdf2epub.Block
blocks = [B("h", "Глава", "Глава", 1), B("p", "Текст до картинок.", "Текст до картинок."),
          B("img", src=ink), B("img", src=photo), B("img", src=grey), B("p", "Текст после.", "Текст после.")]
names = {ink: "images/img0001.png", photo: "images/img0002.png", grey: "images/img0003.png"}
(lib / "Jane Doe").mkdir()
pdf2epub.write_epub(lib / "Jane Doe" / "Jane Doe - Pictures.epub", "Pictures", "Jane Doe", "ru",
                    pdf2epub.split_chapters(blocks, "Pictures"), None, names, [], None)
`;
const made = spawnSync("python3", ["-c", MAKE, LIB], { encoding: "utf8" });
ok(made.status === 0, "book made for the check" + (made.status ? ": " + made.stderr.slice(-300) : ""));
const server = spawn("python3", ["launchers/library_sync.py", "--serve", LIB, "--port", "0"], { stdio: ["ignore", "pipe", "ignore"] });
process.on("exit", () => { try { server.kill("SIGKILL"); } catch {} try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
ok(spawnSync("python3", ["make_server.py", LIB], { encoding: "utf8" }).status === 0, "catalog built for the check");
const PORT = await new Promise(res => {
  let out = "";
  server.stdout.on("data", d => { out += d; const m = /Library: http:\/\/127\.0\.0\.1:(\d+)\//.exec(out); if (m) res(+m[1]); });
  server.on("exit", () => res(0));
});
if (!PORT) { console.log("FAIL: library_sync.py did not start"); process.exit(1); }
const READER = `http://127.0.0.1:${PORT}/.index/reader/reader.html#book=` + encodeURIComponent("../../" + encodeURI("Jane Doe/Jane Doe - Pictures.epub"));

// a tall window scrolling the text: the three pictures are on the screen at once
const d = await launch({ width: 1200, height: 2400, touch: false });
await d.goto(READER);
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light" })); localStorage.setItem("epubReader:scroll", "true")`);
await d.goto("about:blank"); await d.goto(READER);
await d.waitFor("typeof rendition !== 'undefined' && !!rendition && rendition.getContents().length > 0 && placeShown", 30000);
// the chapter with the pictures, and its pictures loaded
await d.eval(`rendition.display(book.spine.get(0).href)`);
await d.waitFor(`(() => { const c = rendition.getContents().find(c => c.document.images.length === 3);
  return !!c && [...c.document.images].every(i => i.complete && i.naturalWidth); })()`, 20000);
await sleep(300);

const look = () => d.eval(`(() => { const c = rendition.getContents().find(c => c.document.images.length === 3);
  return [...c.document.images].map(i => { const s = c.window.getComputedStyle(i);
    return { src: i.getAttribute("src").split("/").pop(), ink: i.classList.contains("ink"), blend: s.mixBlendMode, filter: s.filter }; }); })()`);
const setTheme = async t => { await d.eval(`prefs.theme = ${JSON.stringify(t)}; applyTheme()`); await sleep(400); };
// the colour on the screen at a point of the ink picture's paper (its top left corner, which is white in the file)
const paperAt = () => d.eval(`(() => { const c = rendition.getContents().find(c => c.document.images.length === 3);
  const img = c.document.images[0], r = img.getBoundingClientRect(), f = c.window.frameElement.getBoundingClientRect();
  return { x: Math.round(f.left + r.left + 6), y: Math.round(f.top + r.top + 6) }; })()`);
const pixel = async () => {
  const p = await paperAt();
  const { data } = await d.send("Page.captureScreenshot", { format: "png", clip: { x: p.x, y: p.y, width: 1, height: 1, scale: 1 } });
  writeFileSync(join(LIB, "px.png"), Buffer.from(data, "base64"));
  const r = spawnSync("python3", ["-c", `
import sys, zlib, struct
b = open(sys.argv[1], "rb").read(); i = 8; idat = b""
while i < len(b):
    n = struct.unpack(">I", b[i:i+4])[0]; t = b[i+4:i+8]
    if t == b"IHDR": ct = b[i+8+9]
    if t == b"IDAT": idat += b[i+8:i+8+n]
    i += 12 + n
raw = zlib.decompress(idat); print(*raw[1:4])`, join(LIB, "px.png")], { encoding: "utf8" });
  return r.stdout.trim().split(" ").map(Number);
};
const near = (a, b, tol = 14) => a.length === 3 && a.every((v, i) => Math.abs(v - b[i]) <= tol);

// ======== 1. which pictures are ink
{ const l = await look();
  ok(l[0].ink && !l[1].ink && !l[2].ink, `the ink page is marked, the photos not (${l.map(x => x.src + ":" + x.ink).join(", ")})`); }

// ======== 2. light
{ const l = await look();
  ok(l.every(x => x.blend === "normal" && x.filter === "none"), `light: nothing recoloured (${JSON.stringify(l.map(x => [x.blend, x.filter]))})`);
  const px = await pixel(); ok(near(px, [255, 255, 255]), `light: the ink page's paper is white (${px})`); }

// ======== 3. sepia
await setTheme("sepia");
{ const l = await look();
  ok(l[0].blend === "multiply" && l[1].blend === "normal" && l[2].blend === "normal",
    `sepia: the ink page blends into the page, the photos not (${l.map(x => x.blend).join(", ")})`);
  const px = await pixel(); ok(near(px, [244, 236, 216]), `sepia: the ink page's paper is the sepia page colour (${px})`); }

// ======== 4. dark
await setTheme("dark");
{ const l = await look();
  ok(/invert\(1\)/.test(l[0].filter) && l[0].blend === "screen", `dark: the ink page is inverted (${l[0].filter}, ${l[0].blend})`);
  ok(l.slice(1).every(x => !/invert/.test(x.filter) && /brightness/.test(x.filter)), `dark: the photos only dimmed (${l.slice(1).map(x => x.filter).join(", ")})`);
  const px = await pixel(); ok(near(px, [22, 22, 24]), `dark: the ink page's paper is the dark page colour (${px})`); }

// ======== back to light: nothing left over
await setTheme("light");
{ const l = await look(); ok(l.every(x => x.blend === "normal" && x.filter === "none"), "light again: nothing recoloured"); }

console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
