// Aa: tabs Size / Font / Layout; on a phone a sheet from the top, under the toolbar, at most half the screen.
import { launch, sleep } from "./cdp.mjs";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const R = sel => `JSON.stringify(document.querySelector('${sel}').getBoundingClientRect())`;
const VISIBLE = "[...document.querySelectorAll('#typo [data-pane]')].filter(e => e.offsetParent).map(e => e.dataset.pane).join()";
for (const phone of [true, false]) {
  const W = phone ? 390 : 1280, H = phone ? 844 : 800;
  const p = await launch({ width: W, height: H, touch: phone });
  await p.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
  await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
  await sleep(1200);
  console.log(phone ? "phone" : "desktop");
  if (phone) await p.tap(W / 2, 300);
  await p.eval("document.getElementById('typoBtn').click()"); await sleep(500);
  ok(await p.eval("document.querySelectorAll('#typo [role=tab]').length") === 3, "three tabs");
  ok(await p.eval(VISIBLE) === "size", "Size shown first: " + await p.eval(VISIBLE));
  await p.eval("document.querySelector('#typo [data-tab=font]').click()"); await sleep(200);
  ok(await p.eval(VISIBLE) === "font", "Font tab shows only the fonts");
  ok(await p.eval("getComputedStyle(document.getElementById('typo')).backgroundColor").then(c => !/\/ 0\.|rgba/.test(c)), "not see-through");
  const t = JSON.parse(await p.eval(R("#typo"))), h = JSON.parse(await p.eval(R("header")));
  if (phone) {
    ok(Math.abs(t.top - h.bottom) <= 1 && t.left === 0 && t.width === W, `sheet under the toolbar, full width (${JSON.stringify([t.top, h.bottom, t.left, t.width])})`);
    ok(t.bottom <= H / 2 + 1, `at most half the screen (bottom ${t.bottom})`);
    await p.shot("phone-sheet.png");
  } else ok(t.width < 400 && t.right > W - 20, "desktop: popover on the right as before");
  // closes, and opens again on the tab used last
  await p.eval("document.getElementById('typoBtn').click()"); await sleep(400);
  ok(!(await p.eval("document.getElementById('typo').classList.contains('open')")), "closes");
  await p.eval("document.getElementById('typoBtn').click()"); await sleep(400);
  ok(await p.eval(VISIBLE) === "font", "opens on the last tab");
  await p.eval("document.querySelector('#typo [data-tab=size]').click()"); await sleep(100);
  p.close();
}
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
