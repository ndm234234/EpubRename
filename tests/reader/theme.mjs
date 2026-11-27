// The app's one theme (Auto, light, sepia, dark) in the reader and the catalog, in headless Chrome.
//   node tests/reader/theme.mjs [base]
// The check builds a library of its own (2 books copied from Out/ under made-up names, a catalog
// made by make_server.py, served by its own library_sync.py on a free port), so [base] is not
// needed and not used; it is accepted so the check can be called like the others.
// Reader:
//   1. nothing stored, the system light: Auto, shown light
//   2. the system dark, reloaded: dark already when the page is parsed; the book's pages dark too
//   3. the system turns light with the book open on Auto: the bars and the book follow
//   4. the button: light, sepia, dark (the book too), then Auto; the other settings kept
//   5. a theme stored before (sepia, with a font size): kept
import { mkdtempSync, mkdirSync, copyFileSync, readdirSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { launch, sleep } from "./cdp.mjs";
const ROOT = ".";
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };

// ---- a library of its own
const epubs = dir => readdirSync(dir).flatMap(n => { const f = join(dir, n);
  return statSync(f).isDirectory() ? epubs(f) : n.endsWith(".epub") ? [f] : []; });
const LIB = mkdtempSync(join(tmpdir(), "epub-theme-"));
const NAMES = [];
epubs(join(ROOT, "Out")).slice(0, 2).forEach((src, i) => {
  const name = `Jane Doe/Jane Doe - A Quiet Harbour ${String(i + 1).padStart(2, "0")}.epub`;
  mkdirSync(join(LIB, "Jane Doe"), { recursive: true }); copyFileSync(src, join(LIB, name)); NAMES.push(name);
});
const server = spawn("python3", [join(ROOT, "launchers/library_sync.py"), "--serve", LIB, "--port", "0"], { stdio: ["ignore", "pipe", "ignore"] });
process.on("exit", () => { try { server.kill("SIGKILL"); } catch {} try { rmSync(LIB, { recursive: true, force: true }); } catch {} });
if (NAMES.length < 2) { console.log("FAIL: Out/ has too few books"); process.exit(1); }
const built = spawnSync("python3", [join(ROOT, "make_server.py"), LIB], { encoding: "utf8" });
ok(built.status === 0, "catalog built for the check");
const PORT = await new Promise(res => {
  let out = "";
  server.stdout.on("data", d => { out += d; const m = /Library: http:\/\/127\.0\.0\.1:(\d+)\//.exec(out); if (m) res(+m[1]); });
  server.on("exit", () => res(0));
});
if (!PORT) { console.log("FAIL: library_sync.py did not start"); process.exit(1); }
const BASE = `http://127.0.0.1:${PORT}`;
const CAT = `${BASE}/index.html`;
const READER = `${BASE}/.index/reader/reader.html#book=` + encodeURIComponent("../../" + encodeURI(NAMES[0]));

// ---- helpers
// what <html data-theme> held when the page was parsed (DOMContentLoaded), recorded on every load
const EARLY = `document.addEventListener("DOMContentLoaded", () => { window.__early = document.documentElement.dataset.theme; });`;
const system = (d, dark) => d.send("Emulation.setEmulatedMedia", { features: [
  { name: "prefers-color-scheme", value: dark ? "dark" : "light" }, { name: "hover", value: "none" }, { name: "pointer", value: "coarse" }] });
const theme = d => d.eval("document.documentElement.dataset.theme");
const stored = d => d.eval(`(() => { try { return JSON.parse(localStorage.getItem("epubReader:prefs")); } catch { return "bad"; } })()`);
const openBook = async d => {
  await d.goto("about:blank"); await d.goto(READER);
  await d.waitFor("typeof rendition !== 'undefined' && !!rendition && rendition.getContents().length > 0 && placeShown", 30000);
  await sleep(300);
};
// the book's own page (inside the iframe): its body's theme class and its background
const bookLook = d => d.eval(`(() => { const doc = rendition.getContents()[0].document;
  return { cls: doc.body.className, bg: getComputedStyle(doc.body).backgroundColor }; })()`);
const isDarkRgb = s => { const m = /(\d+), (\d+), (\d+)/.exec(s || ""); return !!m && +m[1] + +m[2] + +m[3] < 150; };
const btnTitle = d => d.eval(`(b => b ? b.title + " | " + b.getAttribute("aria-label") : "")(document.getElementById("themeBtn"))`);
const press = async d => { await d.eval(`document.getElementById("themeBtn").click()`); await sleep(250); };
const waitTheme = async (d, t, ms = 1000) => { try { await d.waitFor(`document.documentElement.dataset.theme === ${JSON.stringify(t)}`, ms); } catch {} };

const d = await launch({ width: 390, height: 844, touch: true });
await d.send("Page.addScriptToEvaluateOnNewDocument", { source: EARLY });

// ======== 1. nothing stored, the system light
await system(d, false);
await openBook(d);
ok(await theme(d) === "light", `reader, nothing stored, system light: shown light (${await theme(d)})`);
ok(await btnTitle(d) === "Theme: Auto (as the system) | Theme: Auto (as the system)", `reader: the button says Auto (${await btnTitle(d)})`);

// ======== 2. the system dark, reloaded
await system(d, true);
await openBook(d);
ok(await d.eval("window.__early") === "dark", `reader, system dark: dark already when parsed (${await d.eval("window.__early")})`);
{ const b = await bookLook(d);
  ok(/\bdark\b/.test(b.cls) && isDarkRgb(b.bg), `reader, system dark: the book's page is dark (${b.cls}, ${b.bg})`); }
ok(await d.eval(`document.getElementById("themeColor").content`) === "#161618", "reader, system dark: the status bar colour is the dark one");

// ======== 3. the system turns light with the book open
await system(d, false);
await waitTheme(d, "light");
ok(await theme(d) === "light", `reader, the system turns light: the bars follow (${await theme(d)})`);
{ const b = await bookLook(d);
  ok(/\blight\b/.test(b.cls) && !isDarkRgb(b.bg), `reader, the system turns light: the book follows (${b.cls}, ${b.bg})`); }

// ======== 4. the button cycles
await d.eval(`prefs.font = 120; savePrefs()`);   // another setting, to see it kept
for (const t of ["light", "sepia", "dark"]) {
  await press(d);
  ok(await theme(d) === t && (await btnTitle(d)).startsWith(`Theme: ${t} |`), `reader button → ${t} (${await theme(d)}, ${await btnTitle(d)})`);
  const b = await bookLook(d);
  ok(new RegExp(`\\b${t}\\b`).test(b.cls), `reader button → ${t}: the book too (${b.cls})`);
}
{ const p = await stored(d); ok(p && p.theme === "dark" && p.font === 120, `reader: stored dark, the font size kept (${JSON.stringify(p)})`); }
await press(d);
ok((await stored(d)).theme === "auto" && (await btnTitle(d)).startsWith("Theme: Auto"), "reader button → Auto again");

// ======== 5. a theme stored before
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "sepia", font: 110, face: "book", layout: "book" }))`);
await openBook(d);
ok(await theme(d) === "sepia" && (await stored(d)).theme === "sepia", `reader: a stored sepia is kept (${await theme(d)})`);

d.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
