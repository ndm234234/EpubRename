// The reading place across font / size changes, reopening, bookmarks and search hits.
// node place.mjs <base> <book under Out/> <turns before the test> <search word>
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK, TURNS = "20", WORD = "что"] = process.argv.slice(2);
const W = +(process.env.W || 390), H = +(process.env.H || 844), TOUCH = process.env.TOUCH !== "0";
const FACES = "literata,ptserif,lora,merriweather,ptsans,inter,palatino,caecilia,helvetica,futura,opendyslexic,book".split(",");
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const settle = async p => { await sleep(150); await p.waitFor("!settling", 15000); await sleep(150); };
const st = async p => JSON.parse(await p.eval("JSON.stringify(__state())"));
const vis = async (p, cfiExpr) => JSON.parse(await p.eval(`JSON.stringify(__vis(${cfiExpr}))`));

const p = await launch({ width: W, height: H, touch: TOUCH });
await p.send("Page.addScriptToEvaluateOnNewDocument", { source:
  `if (!sessionStorage.x) { sessionStorage.x = 1; localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light", font: 100, face: "book", layout: "book" })); }` });
const URL_ = `${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`;
const openBook = async () => {
  await p.goto(URL_);
  await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
  await p.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches")); await settle(p);
};
await openBook();
for (let i = 0; i < +TURNS; i++) { await p.eval("rendition.next().then(() => 0)"); await settle(p); }
const s0 = await st(p);
const A = s0.anchor;
console.log(`at ${s0.idx}:${s0.page}/${s0.cols} "${s0.first} … ${s0.last}"`);
ok((await vis(p, "anchor")).at === Math.round(s0.page), "reading place is on the page shown");

// 1. every font, then sizes up and down: the reading place stays on the page shown and does not move
const face = v => p.eval(`document.querySelector('#faceSeg [data-v=${v}]').click(); 0`);
for (const f of FACES) {
  await face(f); await settle(p);
  const s = await st(p), v = await vis(p, "anchor");
  ok(s.anchor === A && v.at === v.page && v.idx === s0.idx, `${f}: place kept (${v.idx}:${v.page}, word on page ${v.at}, status ${s.dpage}/${s.total} of ${s.cols})`);
}
for (const d of [10, 10, 10, 10, -10, -10, -10, -10, -10, -10, 10, 10]) {
  await p.eval(`setFont(${d}); 0`); await settle(p);
  const s = await st(p), v = await vis(p, "anchor");
  ok(s.anchor === A && v.at === v.page, `size ${await p.eval("prefs.font")}%: place kept (page ${v.page}, word on ${v.at}, ${s.dpage}/${s.total})`);
}
const s1 = await st(p);
ok(s1.first === s0.first && s1.page === s0.page, `back to the first font and size: same page "${s1.first}" (was "${s0.first}")`);

// 2. quick changes, one after another, without waiting
for (const f of ["lora", "inter", "opendyslexic", "palatino"]) { await face(f); await sleep(60); }
await p.eval("setFont(20); setFont(-10); 0"); await settle(p); await sleep(800); await settle(p);
{ const s = await st(p), v = await vis(p, "anchor"); ok(s.anchor === A && v.at === v.page, `rapid changes: place kept (page ${v.page}, word on ${v.at})`); }

// 3. the next page after a change follows on, and back returns; by taps on the side thirds
{
  const a = await st(p);
  await p.tap(W * 0.85, H / 2); await settle(p);
  const b = await st(p);
  ok(b.idx === a.idx ? b.page === a.page + 1 && b.firstI <= a.lastI + 1 : b.page === 0, `tap right: next page (${a.idx}:${a.page} -> ${b.idx}:${b.page})`);
  await p.tap(W * 0.15, H / 2); await settle(p);
  const c = await st(p);
  ok(c.idx === a.idx && c.page === a.page && c.first === a.first, `tap left: back to the same page (${c.idx}:${c.page})`);
}

// 4. reopen: same page
{
  const a = await st(p);
  await openBook();
  const b = await st(p);
  ok(b.idx === a.idx && b.page === a.page && b.first === a.first, `reopened at the same page (${b.idx}:${b.page} "${b.first}", was ${a.idx}:${a.page} "${a.first}")`);
}

// 4b. the address bar hides and shows (the window height changes), the phone turns
for (const [w, h] of [[W, H - 56], [W, H], [W, H - 56], [H, W], [W, H]]) {
  const a = await st(p);
  await p.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: TOUCH });
  await sleep(700); await settle(p); await sleep(300); await settle(p);
  const s = await st(p), v = await vis(p, "anchor");
  ok(s.anchor === a.anchor && v.at === v.page, `window ${w}x${h}: place kept (page ${v.page}, place on ${v.at}, ${s.dpage}/${s.total})`);
}
// 5. bookmark, change the font, go away, go to the bookmark
await p.eval("toggleMarkHere(); 0");
const M = await p.eval("marks[marks.length - 1].cfi");
ok(await p.eval("document.getElementById('markBtn').classList.contains('on')"), "bookmark added on this page");
await face("opendyslexic"); await settle(p);
await p.eval("setFont(30); 0"); await settle(p);
for (let i = 0; i < 12; i++) { await p.eval("rendition.next().then(() => 0)"); await settle(p); }
ok(!(await p.eval("document.getElementById('markBtn').classList.contains('on')")), "12 pages on: not on the bookmark page");
await p.eval(`goToMark(marks.find(m => m.cfi === ${JSON.stringify(M)})); 0`); await settle(p);
{ const v = await vis(p, JSON.stringify(M)); ok(v.at === v.page, `went to the bookmark (page ${v.page}, bookmark on ${v.at})`); }
ok(await p.eval("document.getElementById('markBtn').classList.contains('on')"), "the bookmark icon shows it");
await face("merriweather"); await p.eval("setFont(-40); 0"); await settle(p);
{ const v = await vis(p, JSON.stringify(M)); ok(v.at === v.page, `bookmark page kept after another font change (page ${v.page}, bookmark on ${v.at})`); }

// 6. search: each hit is on the page shown, also after a font change
await p.eval(`runSearch(${JSON.stringify(WORD)}); 0`);
await p.waitFor("/Done|Showing/.test(document.getElementById('searchStatus').textContent)", 60000);
await settle(p);
const n = await p.eval("hits.length");
console.log(`  ${n} hits for "${WORD}"`);
const pick = [...new Set([0, 1, 2, Math.floor(n / 3), Math.floor(n / 2), n - 2, n - 1].filter(i => i >= 0 && i < n))];
for (const [k, i] of pick.entries()) {
  await p.eval(`goToHit(${i}); 0`); await settle(p);
  const v = await vis(p, `hits[${i}].cfi`), sec = await p.eval(`hits[${i}].section.index`);
  ok(v.idx === sec && v.at === v.page, `hit ${i}: on the page shown (ch ${v.idx}/${sec}, page ${v.page}, hit on ${v.at})`);
  await face(FACES[k % FACES.length]); await p.eval(`setFont(${k % 2 ? -10 : 10}); 0`); await settle(p);
  const w = await vis(p, `hits[${i}].cfi`);
  ok(w.at === w.page, `hit ${i}: still shown after font ${FACES[k % FACES.length]} (page ${w.page}, hit on ${w.at})`);
}
// hit next/prev buttons
await p.eval("goToHit(0); 0"); await settle(p);
for (let i = 1; i <= Math.min(5, n - 1); i++) {
  await p.eval("document.getElementById('hitNext').click(); 0"); await settle(p);
  const v = await vis(p, `hits[hitIdx].cfi`), hi = await p.eval("hitIdx");
  ok(hi === i && v.at === v.page, `next hit -> ${hi}, on the page shown (page ${v.page}, hit on ${v.at})`);
}
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
