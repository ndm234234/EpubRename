// Reader on a touch phone: bars hidden, middle tap shows them over the text, thirds turn pages.
// node touch.mjs <base url serving the project folder> <book path under Out/>
import { launch, sleep } from "./cdp.mjs";
const BASE = process.argv[2], BOOK = process.argv[3];
const url = `${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`;
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const SHOWN = sel => `(e => { if (!e) return false; const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > .5; })(document.querySelector('${sel}'))`;
const CFI = "rendition.location.start.cfi";
const VIEW = "JSON.stringify(document.getElementById('viewer').getBoundingClientRect())";
const READY = "typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start";

// ---- phone
let p = await launch({ width: 390, height: 844, touch: true });
await p.goto(url);
await p.waitFor(READY);
await sleep(1500);
console.log("phone");
ok(await p.eval("matchMedia('(hover: none) and (pointer: coarse)').matches"), "touch media emulated");
ok(await p.eval("document.body.classList.contains('touchui')"), "body.touchui");
ok(!(await p.eval(SHOWN("header"))), "toolbar hidden on open");
ok(!(await p.eval(SHOWN("#seek"))), "slider hidden on open");
ok(await p.eval(SHOWN("#where")) && await p.eval(SHOWN("#pct")), "status line shown");
const st = JSON.parse(await p.eval("JSON.stringify(document.getElementById('stage').getBoundingClientRect())"));
ok(st.top < 5, `text area starts at the top (stage top ${st.top})`);
const view0 = await p.eval(VIEW), cfi0 = await p.eval(CFI);
const midX = st.left + st.width / 2, upY = st.top + st.height * .3;
await p.shot("phone-closed.png");
await p.tap(midX, upY);
ok(await p.eval(SHOWN("header")) && await p.eval(SHOWN("#tocBtn")), "tap middle: toolbar shown");
ok(await p.eval(`${SHOWN("#seek")} || ${SHOWN("#navLoad")}`), "tap middle: slider (or its progress) shown");
ok(await p.eval(VIEW) === view0, "reading area keeps its size");
ok(await p.eval(CFI) === cfi0, "page unchanged");
await p.shot("phone-bars.png");
await p.tap(midX, upY);
ok(!(await p.eval(SHOWN("header"))), "tap middle again: toolbar hidden");
await p.tap(st.right - 20, upY);
ok(await p.eval(CFI) !== cfi0, "tap right third (upper half): next page");
await p.tap(st.left + 20, upY);
ok(await p.eval(CFI) === cfi0, "tap left third: previous page");
// a tap on the text is the reader's alone: the browser gets no click and no tap of its own
// (otherwise Chrome on Android opens its "Touch to Search" sheet for the word tapped)
const WATCH = `(w => { w.__taps = []; window.__tapWin = w;
  if (w.__watched) return; w.__watched = true;   // once per page window: the same chapter may be watched again
  w.addEventListener("touchend", e => w.__taps.push(e.defaultPrevented));
  w.addEventListener("click", () => w.__taps.push("click")); })(rendition.getContents()[0].window); 0`;
for (const [x, what] of [[st.right - 20, "side"], [midX, "middle"]]) {
  await p.eval(WATCH);
  await p.tap(x, st.top + st.height * .45);
  const taps = await p.eval("__tapWin.__taps");   // that page's window, also when the tap turned to the next chapter
  ok(JSON.stringify(taps) === "[true]", `tap on the text (${what}): not passed on to the browser ${JSON.stringify(taps)}`);
  if (what === "side") { await p.tap(st.left + 20, upY); }
}
await p.tap(midX, upY);   // hide the bars again
// a long press stays the browser's: selecting a word, its menu, Chrome's search sheet
await p.eval(WATCH);
await p.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: midX, y: st.top + st.height * .45 }] });
await sleep(800);
await p.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
await sleep(300);
ok(JSON.stringify(await p.eval("__tapWin.__taps")).startsWith("[false"), "long press: left to the browser " + JSON.stringify(await p.eval("__tapWin.__taps")));
ok(!(await p.eval(SHOWN("header"))), "long press: no bars");
await p.tap(midX, upY);
await p.tap(st.right - 20, st.top + st.height * .8);
ok(!(await p.eval(SHOWN("header"))), "page turn hides the bars");
await p.tap(midX, upY);
await p.waitFor("!document.getElementById('tocBtn').disabled");
await p.eval("document.getElementById('tocBtn').click()");
await sleep(400);
await p.eval("(a => a[Math.min(2, a.length - 1)].click())(document.querySelectorAll('#toc a'))");   // short books have few entries
await sleep(1500);
ok(!(await p.eval(SHOWN("header"))), "going to a contents entry hides the bars");
p.close();

// ---- desktop
p = await launch({ width: 1280, height: 800, touch: false });
await p.goto(url);
await p.waitFor(READY);
await sleep(1000);
console.log("desktop");
ok(!(await p.eval("document.body.classList.contains('touchui')")), "no touchui");
ok(await p.eval(SHOWN("header")), "toolbar shown");
ok(await p.eval(`${SHOWN("#seek")} || ${SHOWN("#navLoad")}`), "slider shown");
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
