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
// Catalog:
//   6. nothing stored, the system dark: dark already when parsed; the status bar colour is dark
//   7. its button (next to the account): Auto, light, sepia (the page in sepia colours), dark, Auto;
//      the reader's other settings kept, the settings' time moves on
//  7b. the button's picture (A, sun, warm sun, moon) changes with a movement, none for less motion
//   8. one setting: sepia chosen in the catalog shows in the reader; dark chosen in the reader shows
//      back in the catalog
//   9. two tabs: the reader's button repaints the catalog open in another tab, and the catalog's
//      button the book open in the other
//  10. the bar still fits a phone (nothing sideways), the button is a finger's size; on a computer
//      it sits on the bar's first row
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
// the button's picture: one drawing per choice (auto: an "A" in a circle, light: a sun, sepia: a
// warm sun, dark: a moon); which one shows once the change has played out, and its colour
const ICON = `[...document.querySelectorAll("#themeBtn .ti")]`;
const icon = async d => { await sleep(700); return d.eval(`(() => { const on = ${ICON}.filter(g => +getComputedStyle(g).opacity > 0.99);
  return on.length === 1 ? on[0].dataset.t : on.map(g => g.dataset.t).join("+") || "none"; })()`); };
const iconStroke = (d, t) => d.eval(`getComputedStyle(document.querySelector('#themeBtn .ti[data-t="${t}"]')).stroke`);
// a change plays out: the drawings are moving right after the press
const pressMoving = d => d.eval(`(() => { document.getElementById("themeBtn").click();
  return ${ICON}.some(g => g.getAnimations().length > 0); })()`);
// contrast of two of the page's colour variables (WCAG): the muted text must reach 4.5 on sepia
const contrast = (d, a, b) => d.eval(`(() => {
  const css = getComputedStyle(document.documentElement), lum = n => {
    const h = css.getPropertyValue(n).trim(), c = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
      .map(x => x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  const [x, y] = [lum(${JSON.stringify(a)}), lum(${JSON.stringify(b)})].sort((p, q) => p - q);
  return Math.round((y + 0.05) / (x + 0.05) * 100) / 100; })()`);
const press = async d => { await d.eval(`document.getElementById("themeBtn").click()`); await sleep(250); };
const waitTheme = async (d, t, ms = 1000) => { try { await d.waitFor(`document.documentElement.dataset.theme === ${JSON.stringify(t)}`, ms); } catch {} };

const d = await launch({ width: 390, height: 844, touch: true });
await d.send("Page.addScriptToEvaluateOnNewDocument", { source: EARLY });

// ======== 1. nothing stored, the system light
await system(d, false);
await openBook(d);
ok(await theme(d) === "light", `reader, nothing stored, system light: shown light (${await theme(d)})`);
ok(await btnTitle(d) === "Theme: Auto (as the system) | Theme: Auto (as the system)", `reader: the button says Auto (${await btnTitle(d)})`);
ok(await icon(d) === "auto", `reader, Auto: the "A" picture (${await icon(d)})`);

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
  ok(await icon(d) === t, `reader button → ${t}: its picture (${await icon(d)})`);
}
{ const p = await stored(d); ok(p && p.theme === "dark" && p.font === 120, `reader: stored dark, the font size kept (${JSON.stringify(p)})`); }
await press(d);
ok((await stored(d)).theme === "auto" && (await btnTitle(d)).startsWith("Theme: Auto"), "reader button → Auto again");

// ======== 5. a theme stored before
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "sepia", font: 110, face: "book", layout: "book" }))`);
await openBook(d);
ok(await theme(d) === "sepia" && (await stored(d)).theme === "sepia", `reader: a stored sepia is kept (${await theme(d)})`);
for (const bg of ["--bg", "--panel"]) {
  const c = await contrast(d, "--muted", bg);
  ok(c >= 4.5, `reader on sepia: the muted text on ${bg} is readable (${c} ≥ 4.5)`);
}

// ======== 6. the catalog: nothing stored, the system dark
const catalog = async d => { await d.goto("about:blank"); await d.goto(CAT);
  await d.waitFor("document.querySelectorAll('#list .book').length > 0", 20000); };
await d.eval(`localStorage.removeItem("epubReader:prefs")`);
await system(d, true);
await catalog(d);
ok(await d.eval("window.__early") === "dark", `catalog, system dark: dark already when parsed (${await d.eval("window.__early")})`);
ok(await d.eval(`document.getElementById("themeColor") && document.getElementById("themeColor").content`) === "#111113",
  `catalog, system dark: the status bar colour is the catalog's dark (${await d.eval(`(m => m && m.content)(document.getElementById("themeColor"))`)})`);
ok(await d.eval(`document.querySelectorAll('meta[name="theme-color"]').length`) === 1, "catalog: one theme-color meta");

// ======== 7. the catalog's button
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "auto", font: 130 }))`);
await catalog(d);
ok((await btnTitle(d)).startsWith("Theme: Auto (as the system) |"), `catalog: the button says Auto (${await btnTitle(d)})`);
const bodyBg = () => d.eval("getComputedStyle(document.body).backgroundColor");
let t0 = +(await d.eval(`localStorage.getItem("epubReader:prefsTime")`) || 0);
for (const t of ["light", "sepia", "dark", "auto"]) {
  await press(d);
  const shownT = t === "auto" ? "dark" : t;
  ok(await theme(d) === shownT && (await btnTitle(d)).startsWith(t === "auto" ? "Theme: Auto" : `Theme: ${t} |`),
    `catalog button → ${t} (${await theme(d)}, ${await btnTitle(d)})`);
  const p = await stored(d), time = +(await d.eval(`localStorage.getItem("epubReader:prefsTime")`));
  ok(p && p.theme === t && p.font === 130 && time >= t0 && time > 0, `catalog button → ${t}: stored, the font size kept, the time moved (${JSON.stringify(p)})`);
  t0 = time;
  ok(await icon(d) === t, `catalog button → ${t}: its picture (${await icon(d)})`);
  if (t === "sepia") ok(await bodyBg() === "rgb(244, 236, 216)", `catalog on sepia: the sepia background (${await bodyBg()})`);
  if (t === "sepia") for (const bg of ["--bg", "--surface", "--surface-2"]) {
    const c = await contrast(d, "--muted", bg);
    ok(c >= 4.5, `catalog on sepia: the muted text on ${bg} is readable (${c} ≥ 4.5)`);
  }
  await d.shot(`catalog-${t}.png`);
}

// ======== 7b. the picture changes with a movement (none when the system asks for less motion);
// the sepia sun is warm, not the plain sun's colour
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "auto" }))`);
await catalog(d);
ok(await pressMoving(d), "catalog: the picture moves when the theme changes");
await icon(d);
await pressMoving(d);   // light → sepia
await icon(d);
{ const warm = await iconStroke(d, "sepia"), plain = await iconStroke(d, "light");
  ok(warm !== plain && /^rgb/.test(warm), `catalog: the sepia sun is warm (${warm}, the plain one ${plain})`); }
await d.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }, { name: "prefers-color-scheme", value: "dark" }] });
ok(!(await pressMoving(d)), "catalog, less motion asked for: the picture just changes");
ok(await icon(d) === "dark", `catalog, less motion: the moon (${await icon(d)})`);
await system(d, true);

// ======== 8. one setting for the catalog and the reader
await system(d, false);
await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "sepia" }))`);
await catalog(d);
ok(await theme(d) === "sepia", "catalog: sepia");
await openBook(d);
ok(await theme(d) === "sepia" && /\bsepia\b/.test((await bookLook(d)).cls), `the reader shows the catalog's sepia (${await theme(d)})`);
await press(d);   // sepia → dark
await catalog(d);
ok(await theme(d) === "dark" && (await btnTitle(d)).startsWith("Theme: dark |"), `the catalog shows the reader's dark (${await theme(d)})`);

// ======== 9. two tabs
{
  await d.eval(`localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light" }))`);
  await catalog(d);
  // the reader in another tab of the same browser, opened from the catalog (same site, so the
  // catalog can press the reader's button itself)
  await d.send("Runtime.evaluate", { expression: `window.__tab = window.open(${JSON.stringify(READER)}, "_blank"); !!window.__tab`, userGesture: true });
  let ready = false;
  try { await d.waitFor("!!window.__tab && window.__tab.eval(\"typeof rendition !== 'undefined' && !!rendition && placeShown\")", 30000); ready = true; } catch {}
  ok(ready, "two tabs: the reader opened in the other tab");
  await d.eval(`window.__tab.document.getElementById("themeBtn").click()`);   // light → sepia
  await waitTheme(d, "sepia", 2000);
  ok(await theme(d) === "sepia" && (await btnTitle(d)).startsWith("Theme: sepia |"), `two tabs: the catalog follows the reader's button (${await theme(d)})`);
  // and back: the catalog's button changes the book open in the other tab
  await press(d);   // sepia → dark
  try { await d.waitFor(`window.__tab.document.documentElement.dataset.theme === "dark"`, 2000); } catch {}
  const other = await d.eval(`(t => ({ theme: t.document.documentElement.dataset.theme, title: t.document.getElementById("themeBtn").title,
    book: t.eval("rendition.getContents()[0].document.body.className") }))(window.__tab)`);
  ok(other.theme === "dark" && other.title === "Theme: dark" && /\bdark\b/.test(other.book),
    `two tabs: the open book follows the catalog's button (${JSON.stringify(other)})`);
  await d.eval("window.__tab.close()");
}

// ======== 10. the bar fits
await catalog(d);
{ const fit = await d.eval(`({ sw: document.documentElement.scrollWidth, w: innerWidth,
    r: (r => [r.width, r.height])(document.getElementById("themeBtn").getBoundingClientRect()) })`);
  ok(fit.sw <= fit.w, `catalog on a phone: nothing sideways (${fit.sw} ≤ ${fit.w})`);
  ok(fit.r[0] >= 36 && fit.r[1] >= 36, `catalog on a phone: the button is a finger's size (${fit.r.map(Math.round).join("×")})`);
}
await d.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
await catalog(d);
{ const at = await d.eval(`(b => ({ btn: b.getBoundingClientRect().top, bar: document.querySelector("header .bar").getBoundingClientRect().top,
    q: document.getElementById("q").getBoundingClientRect().top }))(document.getElementById("themeBtn"))`);
  await d.shot("catalog-computer.png");
  ok(at.btn - at.bar < 40 && Math.abs(at.btn - at.q) < 30, `catalog on a computer: the button on the bar's first row (${JSON.stringify(at)})`);
}

d.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
