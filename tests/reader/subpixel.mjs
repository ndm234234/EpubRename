// Phones with a fractional pixel ratio (2.625, 2.75...) snap scrolling to device pixels, so the text's
// scroll position reads a fraction of a pixel past the page (e.g. 9700.19 for 9700). epub.js's "is
// there a page after this one" test had no room for that: on the page before a chapter's last it
// said "no" and went on to the next chapter, skipping the last page (going back found it).
// Headless Chrome scrolls in whole pixels, so the phone's reading is put in by hand here.
// node subpixel.mjs <base> <book under Out/>
import { launch, sleep } from "./cdp.mjs";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const p = await launch({ width: 390, height: 844, touch: true });
await p.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
await sleep(800);
const where = () => p.eval(`(l => [l.index, l.displayed.page, l.displayed.total].join(","))(rendition.location.start)`)
  .then(s => s.split(",").map(Number));
// a chapter of 3 pages or more
let chapter = null;
for (let i = 0; i < 40 && !chapter; i++) {
  const [idx, , total] = await where();
  if (total >= 3) chapter = idx;
  else { await p.eval(`rendition.display(book.spine.get(${idx + 1}).href).then(() => 0)`); await sleep(500); }
}
ok(chapter !== null, "a chapter of 3 pages or more");
// the scroll position as a phone reads it: a little past the page
await p.eval(`(() => {
  const c = rendition.manager.container, d = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
  Object.defineProperty(c, "scrollLeft", { configurable: true,
    get() { const v = d.get.call(this); return v ? v + 0.4 : v; }, set(v) { d.set.call(this, v); } });
})()`);
for (const off of [0.4, 0.6]) {
  await p.eval(`rendition.display(book.spine.get(${chapter}).href).then(() => 0)`); await sleep(500);
  let [idx, page, total] = await where();
  while (page < total - 1) { await p.eval("rendition.next().then(() => 0)"); await sleep(150); [idx, page, total] = await where(); }
  await p.eval("rendition.next().then(() => 0)"); await sleep(400);
  const [idx2, page2] = await where();
  ok(idx2 === chapter && page2 === total, `+${off}px: from page ${total - 1}/${total} on to ${total}/${total} (got chapter ${idx2} page ${page2})`);
  await p.eval("rendition.next().then(() => 0)"); await sleep(600);
  const [idx3, page3] = await where();
  ok(idx3 === chapter + 1 && page3 === 1, `then into the next chapter at its first page (got ${idx3}:${page3})`);
  await p.eval(`(() => { const c = rendition.manager.container; delete c.scrollLeft; })()`);
  await p.eval(`(() => {
    const c = rendition.manager.container, d = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
    Object.defineProperty(c, "scrollLeft", { configurable: true,
      get() { const v = d.get.call(this); return v ? v + 0.6 : v; }, set(v) { d.set.call(this, v); } });
  })()`);
}
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
