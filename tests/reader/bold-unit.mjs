// Bold word starts (reader/bold.js), in the page: the wrapping and unwrapping of word starts, and
// every address (CFI) the same with the bold as without it, both ways, in a real book.
// node bold-unit.mjs <base> <book under Out/>
import { launch, sleep } from "./cdp.mjs";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const p = await launch({ width: 390, height: 844 });
await p.goto(`${BASE}/reader/reader.html`);
await p.waitFor("document.readyState === 'complete'");
const run = async (name, js) => {
  try { return await p.eval(js); } catch (e) { ok(false, `${name}: ${e.message.split("\n")[0]}`); return null; }
};

// 1. how many letters get bold
const pre = await run("prefix", `JSON.stringify([1,2,3,4,5,7,10].map(n => BoldStarts.prefix(n)))`);
ok(pre === "[1,1,1,2,3,4,5]", `prefix of 1,2,3,4,5,7,10 letters = 1,1,1,2,3,4,5 (got ${pre})`);

// 2. wrapping, idempotent, code left alone, unwrapping back to the same text nodes
const r = JSON.parse(await run("wrap", `(() => {
  const doc = document.implementation.createHTMLDocument("");
  const html = "<p>Съешь же этих <code>longword</code> мягких</p><pre>keep me</pre><p>пере\\u00ADход и\\u0306</p>";
  doc.body.innerHTML = html;
  const before = doc.body.innerHTML;
  BoldStarts.apply(doc, true);
  const p0 = doc.body.children[0].innerHTML, pre = doc.body.children[1].innerHTML, p2 = doc.body.children[2].innerHTML;
  const once = doc.body.innerHTML;
  BoldStarts.apply(doc, true);
  const again = doc.body.innerHTML === once;
  const bolds = doc.querySelectorAll("b.bio").length;
  BoldStarts.apply(doc, false);
  return JSON.stringify({ p0, pre, p2, again, bolds, after: doc.body.innerHTML === before,
    nodes: doc.body.children[0].childNodes.length });
})()`) || "{}");
ok(r.p0 === '<b class="bio">Съе</b>шь <b class="bio">ж</b>е <b class="bio">эт</b>их <code>longword</code> <b class="bio">мяг</b>ких',
   `word starts in bold, code left alone (got ${r.p0})`);
ok(r.pre === "keep me", `pre left alone (got ${r.pre})`);
ok(r.p2 === '<b class="bio">пере</b>\u00ADход <b class="bio">и\u0306</b>',
   `a soft hyphen keeps the word whole, a combining mark stays with its letter (got ${JSON.stringify(r.p2)})`);
ok(r.again && r.bolds === 6, `switching on twice wraps once (${r.bolds} bold starts)`);
ok(r.after, "switching off gives the text back as it was");
ok(r.nodes === 3, `and whole text nodes again (${r.nodes} nodes in the first paragraph: text, code, text)`);

p.close();

// 3. addresses in a book: made with the bold = made without; resolved with the bold = without
const b = await launch({ width: 390, height: 844 });
await b.goto(`${BASE}/reader/reader.html#book=${encodeURIComponent("../Out/" + BOOK)}`);
await b.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start");
await sleep(800);
// chapters of 3 pages or more
const chapters = JSON.parse(await b.eval(`(async () => {
  const out = [];
  for (let i = 0; i < book.spine.length && out.length < 3; i++) {
    await rendition.display(book.spine.get(i).href);
    const l = rendition.location.start;
    if (l.displayed.total >= 3 && rendition.getContents()[0].document.querySelectorAll("p").length > 5) out.push(i);
  }
  return JSON.stringify(out);
})()`));
ok(chapters.length === 3, `three chapters of 3 pages or more (${chapters})`);
for (const ch of chapters) {
  const r = JSON.parse(await b.eval(`(async () => {
    await rendition.display(book.spine.get(${ch}).href);
    const doc = rendition.getContents()[0].document, base = book.spine.get(${ch}).cfiBase;
    const texts = () => { const a = [], w = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT); for (let n; (n = w.nextNode()); ) a.push(n); return a; };
    const at = g => { for (const n of texts()) { if (g < n.length) return [n, g]; g -= n.length; } return null; };
    const gof = (node, off) => { let g = 0; for (const n of texts()) { if (n === node) return g + off; g += n.length; } return -1; };
    const els = () => [...doc.querySelectorAll("*")].filter(e => !e.matches("b.bio"));
    // a place as the text sees it, the same with and without the bold
    const pos = (node, off) => {
      if (node.nodeType === 3) return "t" + gof(node, off);
      const child = node.childNodes[off];
      return "e" + els().indexOf(node) + ":" + (!child ? "end" : child.nodeType === 1 && !child.matches("b.bio") ? "e" + els().indexOf(child) : "t" + gof(child.nodeType === 3 ? child : texts().find(t => child.contains(t)), 0));
    };
    const desc = rg => rg ? pos(rg.startContainer, rg.startOffset) + ".." + pos(rg.endContainer, rg.endOffset) + " " + rg.toString().slice(0, 40) : "none";
    const mk = (g, len) => { const rg = doc.createRange(); const [a, ao] = at(g); const [z, zo] = at(g + len - (len ? 1 : 0)); rg.setStart(a, ao); rg.setEnd(z, len ? zo + 1 : zo); return rg; };
    const words = [...doc.body.textContent.matchAll(/[\\p{L}]{3,}/gu)];
    const pick = words.filter((_, i) => i % Math.max(1, Math.floor(words.length / 150)) === 0);
    const elsPicked = [...doc.querySelectorAll("img, sup, image")].concat([doc.querySelector("p")]).filter(Boolean);
    const plain = { points: pick.map(m => new ePub.CFI(mk(m.index, 0), base).toString()),
                    spans: pick.map(m => new ePub.CFI(mk(m.index, m[0].length), base).toString()),
                    nodes: elsPicked.map(e => new ePub.CFI(e, base).toString()) };
    const all = [...plain.points, ...plain.spans, ...plain.nodes];
    const before = all.map(c => { try { return desc(rendition.getRange(c)); } catch (e) { return "error " + e.message; } });
    BoldStarts.apply(doc, true);
    const bolds = doc.querySelectorAll("b.bio").length;
    const elsBold = elsPicked;   // the same elements: the bold only wraps text
    const made = { points: pick.map(m => new ePub.CFI(mk(m.index, 0), base).toString()),
                   spans: pick.map(m => new ePub.CFI(mk(m.index, m[0].length), base).toString()),
                   nodes: elsBold.map(e => new ePub.CFI(e, base).toString()) };
    const builds0 = BoldStarts._cacheBuilds;
    const after = all.map(c => { try { return desc(rendition.getRange(c)); } catch (e) { return "error " + e.message; } });
    const builds1 = BoldStarts._cacheBuilds;
    BoldStarts.apply(doc, false); BoldStarts.apply(doc, true);
    rendition.getRange(all[0]);
    const builds2 = BoldStarts._cacheBuilds;
    BoldStarts.apply(doc, false);
    const diff = (a, b) => a.map((x, i) => x === b[i] ? null : [x, b[i]]).filter(Boolean);
    return JSON.stringify({ n: pick.length, els: elsPicked.length, bolds,
      madeDiff: [...diff(plain.points, made.points), ...diff(plain.spans, made.spans), ...diff(plain.nodes, made.nodes)].slice(0, 3),
      madeN: diff(plain.points, made.points).length + diff(plain.spans, made.spans).length + diff(plain.nodes, made.nodes).length,
      resolvedDiff: diff(before, after).slice(0, 3), resolvedN: diff(before, after).length,
      nodesBefore: before.slice(-elsPicked.length).slice(0, 2), builds: [builds0, builds1, builds2] });
  })()`));
  const tag = `chapter ${ch} (${r.n} words, ${r.els} elements, ${r.bolds} bold starts)`;
  ok(r.bolds > 100, `${tag}: the bold is on`);
  ok(r.madeN === 0, `${tag}: addresses made with the bold = without it (${r.madeN} differ ${JSON.stringify(r.madeDiff)})`);
  ok(r.resolvedN === 0, `${tag}: addresses resolve with the bold as without it (${r.resolvedN} differ ${JSON.stringify(r.resolvedDiff)})`);
  ok(r.builds[1] - r.builds[0] <= 1 && r.builds[2] === r.builds[1] + 1,
     `${tag}: one plain copy per chapter, made anew after switching (builds ${r.builds})`);
}
b.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
