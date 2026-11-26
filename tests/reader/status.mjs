// Phone status line: "4/24" (page in chapter) and "120/560 21%" (page of the book), the text centred,
// no fullscreen on a tap (the clock stays).
import { launch, sleep } from "./cdp.mjs";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const p = await launch({ width: 390, height: 844, touch: true });
await p.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
await p.waitFor("locationsReady", 120000);
for (let i = 0; i < 6; i++) { await p.eval("rendition.next()"); await sleep(600); }
const where = await p.eval("document.getElementById('where').textContent");
const pct = await p.eval("document.getElementById('pct').textContent");
console.log("  status: " + JSON.stringify(where) + " | " + JSON.stringify(pct));
ok(/^\d+\/\d+$/.test(where), "page in chapter, numbers only");
const m = pct.match(/^(\d+)\/(\d+) (\d+)%$/);
ok(!!m && +m[1] >= 1 && +m[1] <= +m[2], "page of the book and %");
// centred: space above the text area = space below it (down to the screen bottom)
const v = JSON.parse(await p.eval("JSON.stringify(document.getElementById('viewer').getBoundingClientRect())"));
const footTop = await p.eval("document.querySelector('footer').getBoundingClientRect().top");
ok(v.top === 0 && v.bottom === footTop, `page from the top of the screen to the status line (${v.top}–${v.bottom}, status at ${footTop})`);
const pad = await p.eval("(s => [s.paddingTop, s.paddingBottom].join(' '))(getComputedStyle(rendition.getContents()[0].document.body))");
ok(pad === "17px 17px", "same margin above and below the text: " + pad);
ok(Math.abs(v.left - (390 - v.right)) <= 2, `and horizontally (${v.left} / ${390 - v.right})`);
// no fullscreen on a tap (a page turn)
ok(!(await p.eval("!!document.fullscreenElement")), "not fullscreen before a tap");
await p.tap(370, 400);
await sleep(800);
ok(!(await p.eval("!!document.fullscreenElement")), "no fullscreen after a tap (the clock stays)");
await p.waitFor("rendition.location && rendition.location.start");
const pct2 = await p.eval("document.getElementById('pct').textContent");
ok(/^\d+\/\d+ \d+%$/.test(pct2), "status right after a page turn: " + pct2);
await p.shot("phone-status.png");
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
