// every search hit (up to N, evenly spread) lands on the page shown
import { launch, sleep } from "./cdp.mjs";
import { HELPERS } from "./helpers.mjs";
const [BASE, BOOK, WORD, N = "120", FACE = "book", SIZE = "100"] = process.argv.slice(2);
const p = await launch({ width: 390, height: 844, touch: true });
await p.send("Page.addScriptToEvaluateOnNewDocument", { source: `localStorage.setItem("epubReader:prefs", JSON.stringify({ theme: "light", font: ${SIZE}, face: "${FACE}", layout: "book" }));` });
await p.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
await p.eval(HELPERS.replace("TOUCH_UI", "touchUi.matches")); await sleep(1000);
await p.eval(`runSearch(${JSON.stringify(WORD)}); 0`);
await p.waitFor("/Done|Showing/.test(document.getElementById('searchStatus').textContent)", 60000);
const n = await p.eval("hits.length"); let bad = 0, done = 0;
const step = Math.max(1, Math.floor(n / +N));
for (let i = 0; i < n; i += step) {
  await p.eval(`goToHit(${i}); 0`); await sleep(100); await p.waitFor("!settling"); await sleep(150);
  const v = JSON.parse(await p.eval(`JSON.stringify(Object.assign(__vis(hits[${i}].cfi), { sec: hits[${i}].section.index }))`));
  done++; if (v.at !== v.page || v.idx !== v.sec) { bad++; console.log("  FAIL hit", i, JSON.stringify(v)); }
}
console.log(`[${FACE} ${SIZE}%] ${done} of ${n} hits checked, ${bad} wrong`);
p.close(); process.exit(bad ? 1 : 0);
