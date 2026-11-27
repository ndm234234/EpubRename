// "Bold word starts" (Aa → Font): the switch is on this device only, switching keeps the reading
// place, and a bookmark made with the bold opens on its page without it (and the reverse).
// node bold.mjs <base> <book under Out/>
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const URL = `${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`;

async function open(bold) {
  const p = await launch({ width: 390, height: 844 });
  if (bold) await p.send("Page.addScriptToEvaluateOnNewDocument", { source: `try { localStorage.setItem("epubReader:bold", "true"); } catch {}` });
  await p.goto(URL);
  await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
  await p.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches"));
  await settle(p);
  return p;
}
async function settle(p) {
  await sleep(150);
  await p.waitFor("!settling", 15000);
  await p.eval("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))).then(() => 0)");
}
const st = async p => JSON.parse(await p.eval("JSON.stringify(__state())"));
const bolds = p => p.eval("rendition.getContents()[0].document.querySelectorAll('b.bio').length");
async function flip(p) {   // the checkbox, as the reader taps it
  await p.eval("document.getElementById('typoBtn').click()"); await sleep(300);
  await p.eval("document.querySelector('#typo [data-tab=font]').click()"); await sleep(100);
  await p.eval("document.getElementById('boldStarts').click()");
  await settle(p);
  await p.eval("toggleTypo(false)");
}
const placeOk = (s, tag) => ok(s.anchorAt === Math.round(s.page), `${tag}: reading place on the page shown (place on ${s.anchorAt}, shown ${s.page})`);

const p = await open(false);
ok(await p.eval("!!document.querySelector('#typo [data-pane=font] #boldStarts')"), "the switch is on the Font tab");
// a long chapter, its third page
const ch = await p.eval(`(async () => { for (let i = 0; i < book.spine.length; i++) { await rendition.display(book.spine.get(i).href);
  if (rendition.location.start.displayed.total >= 6) return i; } return -1; })()`);
await settle(p);
for (let i = 0; i < 2; i++) { await p.eval("rendition.next().then(() => 0)"); await settle(p); }
ok(await bolds(p) === 0, "no bold before the switch");

await flip(p);
ok(await bolds(p) > 100, "switched on: word starts in bold");
ok(await p.eval("localStorage.getItem('epubReader:bold')") === "true", "kept on this device (epubReader:bold)");
ok(!/bold/i.test(await p.eval("localStorage.getItem('epubReader:prefs') || ''")), "not in the synced settings");
placeOk(await st(p), "switched on");

// a bookmark with the bold, opened without it
await p.eval("toggleMarkHere()");
const m1 = JSON.parse(await p.eval("JSON.stringify(marks[marks.length - 1])"));
await flip(p);
ok(await bolds(p) === 0, "switched off: no bold");
ok(await p.eval("localStorage.getItem('epubReader:bold')") !== "true", "off kept on this device");
placeOk(await st(p), "switched off");
await p.eval(`rendition.display(book.spine.get(${ch + 1}).href).then(() => 0)`); await settle(p);
await p.eval(`goToMark(marks.find(m => m.id === ${JSON.stringify(m1.id)}))`); await settle(p);
let s = await st(p);
ok(s.idx === ch && (await p.eval(`__pageOf(${JSON.stringify(m1.cfi)})`)) === Math.round(s.page),
   `bookmark made with the bold opens on its page without it (${s.idx}:${s.page})`);

// a bookmark without the bold, opened with it
for (let i = 0; i < 2; i++) { await p.eval("rendition.next().then(() => 0)"); await settle(p); }
await p.eval("toggleMarkHere()");
const m2 = JSON.parse(await p.eval("JSON.stringify(marks.find(m => m.id !== " + JSON.stringify(m1.id) + "))"));
await flip(p);
await p.eval(`rendition.display(book.spine.get(${ch + 1}).href).then(() => 0)`); await settle(p);
await p.eval(`goToMark(marks.find(m => m.id === ${JSON.stringify(m2.id)}))`); await settle(p);
s = await st(p);
ok(s.idx === ch && (await p.eval(`__pageOf(${JSON.stringify(m2.cfi)})`)) === Math.round(s.page),
   `bookmark made without the bold opens on its page with it (${s.idx}:${s.page})`);
p.close();

// opened again on a device where it is on: bold at once, the switch shows it
const q = await open(true);
await q.eval(`rendition.display(book.spine.get(${ch}).href).then(() => 0)`); await settle(q);   // (it opens at the cover)
ok(await bolds(q) > 100, "on when the reader opens (this device's setting)");
await q.eval("document.getElementById('typoBtn').click()"); await sleep(300);
ok(await q.eval("document.getElementById('boldStarts').checked"), "the switch shows it on");
q.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
