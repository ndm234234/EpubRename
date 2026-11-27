// Bold word starts (reader/bold.js), in the page: the wrapping and unwrapping of word starts.
// node bold-unit.mjs <base>
import { launch } from "./cdp.mjs";
const [BASE] = process.argv.slice(2);
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
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
