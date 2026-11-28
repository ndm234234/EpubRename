// Navigation e2e: page turns forward/back with no skips or repeats, status total = real columns,
// no clipped lines, place kept on font/size changes, bookmarks and search hits land on the shown page.
// node nav.mjs <base> <book under Out/> [maxTurns]   env FACES=book,literata,... SIZES=100,140 W H BOLD=1 (bold word starts on)
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK, MAXT = "100000"] = process.argv.slice(2);
const FACES = (process.env.FACES || "book,literata,ptserif,lora,merriweather,ptsans,inter,palatino,caecilia,helvetica,futura,opendyslexic").split(",");
const SIZES = (process.env.SIZES || "100,140").split(",").map(Number);
const W = +(process.env.W || 390), H = +(process.env.H || 844), TOUCH = process.env.TOUCH !== "0";
let fails = 0;
const fail = (msg) => { console.log("  FAIL " + msg); fails++; };


async function settle(p) {
  await sleep(120);
  await p.waitFor("!settling", 15000);
  await p.eval("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))).then(() => 0)");
}
const st = async p => JSON.parse(await p.eval("JSON.stringify(__state())"));

async function open(face, size) {
  const p = await launch({ width: W, height: H, touch: TOUCH });
  await p.send("Page.addScriptToEvaluateOnNewDocument", { source:
    `try { localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light", font: ${size}, face: "${face}", layout: "book" })); } catch {}` + (process.env.BOLD ? `;try { localStorage.setItem("epubReader:bold", "true"); } catch {}` : "") });
  await p.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
  await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
  await p.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches"));
  await settle(p);
  return p;
}

function checkPage(tag, s, seen) {
  if (Math.abs(s.page - Math.round(s.page)) > 0.01) fail(`${tag} scroll not on a page boundary: ${s.page}`);
  if (s.total > s.cols * s.div || s.total <= (s.cols - 1) * s.div) fail(`${tag} status total ${s.total} != real pages ${s.cols}x${s.div}`);
  if (s.dpage !== Math.round(s.page) * s.div + 1) fail(`${tag} status page ${s.dpage} != shown ${Math.round(s.page) + 1}`);
  if (s.clipped.length) fail(`${tag} clipped lines: ${s.clipped.slice(0, 4).join(" ")}`);
  if (s.beyond) fail(`${tag} ${s.beyond} words beyond the last page`);
  // No word measured on the page shown: a blank page, or text Chrome can't place (tables in
  // columns report the wrong column); the place can't be judged there
  if (s.anchorAt !== Math.round(s.page) && s.n === 0) console.log(`  note ${tag} no text measured on the page, place on page ${s.anchorAt}`);
  else if (s.anchorAt !== Math.round(s.page)) fail(`${tag} reading place on page ${s.anchorAt}, shown ${s.page}`);
  const k = s.idx + ":" + Math.round(s.page);
  if (seen) { if (seen.has(k)) fail(`${tag} page shown twice ${k}`); seen.add(k); }
}

for (const size of SIZES) for (const face of FACES) {
  const tag0 = `[${face} ${size}%]`;
  const f0 = fails;
  const p = await open(face, size);
  // 1. forward through the book
  const fwd = []; const seen = new Set();
  let s = await st(p);
  if (s.idx !== 0 && s.page !== 0) fail(`${tag0} does not open at the start: ${s.idx}:${s.page}`);
  checkPage(tag0 + " start", s, seen); fwd.push(s);
  for (let t = 0; t < +MAXT && !s.atEnd; t++) {
    await p.eval("rendition.next().then(() => 0)"); await settle(p);
    const n = await st(p);
    const tag = `${tag0} fwd#${t + 1} ${n.idx}:${n.page}`;
    if (n.idx === s.idx) {
      if (n.page !== s.page + 1) fail(`${tag} expected page ${s.page + 1} in chapter ${s.idx}`);
      else if (s.lastI >= 0 && n.firstI >= 0 && n.firstI > s.lastI + 1) fail(`${tag} words skipped ${s.lastI}..${n.firstI}`);
      else if (s.lastI >= 0 && n.firstI >= 0 && n.firstI <= s.lastI && n.first === s.last && n.firstI < s.lastI) fail(`${tag} words repeated`);
    } else {
      if (n.idx < s.idx) fail(`${tag} went back a chapter`);
      if (s.page !== s.cols - 1) fail(`${tag} left chapter ${s.idx} at page ${s.page} of ${s.cols}`);
      if (n.page !== 0) fail(`${tag} entered chapter ${n.idx} at page ${n.page}`);
    }
    if (n.blank) console.log(`  note ${tag} blank page`);
    checkPage(tag, n, seen);
    fwd.push(n); s = n;
    if (fails - f0 > 8) break;
  }
  // re-measure: does a late font load change the last chapter's page count?
  await sleep(1500);
  const again = await st(p);
  if (again.cols !== s.cols || again.page !== s.page) fail(`${tag0} layout changed after waiting: ${s.page}/${s.cols} -> ${again.page}/${again.cols}`);
  // 2. back the same way
  for (let i = fwd.length - 2; i >= 0 && fails - f0 <= 8; i--) {
    await p.eval("rendition.prev().then(() => 0)"); await settle(p);
    const b = await st(p), e = fwd[i];
    if (b.idx !== e.idx || b.page !== e.page || b.first !== e.first) { fail(`${tag0} back#${fwd.length - 1 - i}: at ${b.idx}:${b.page} "${b.first}", expected ${e.idx}:${e.page} "${e.first}"`); break; }
    checkPage(`${tag0} back ${b.idx}:${b.page}`, b, null);
  }
  console.log(`${tag0} ${fwd.length} pages forward & back` + (fails === f0 ? " ok" : ""));
  p.close();
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
