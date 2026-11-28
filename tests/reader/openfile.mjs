// The reader opened by the catalog's "Open file" (reader.html#from=catalog): its "Catalog" button
// is there, before and after a file is chosen, and leads back to the catalog. Opened on its own
// (no catalog around it), there is no such button.
// node openfile.mjs <base> <book under Out/>   (OUT=dir: where Out/ is, if not here)
import { launch, sleep } from "./cdp.mjs";
import { resolve } from "node:path";
const [BASE, BOOK] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const shown = "(e => !!e && !e.classList.contains('hidden') && !!e.offsetParent)(document.getElementById('back'))";

let p = await launch({ width: 390, height: 844 });
await p.goto(`${BASE}/reader/reader.html`);
await p.waitFor("document.readyState === 'complete'");
ok(!(await p.eval(shown)), "on its own: no Catalog button");
p.close();

p = await launch({ width: 390, height: 844 });
await p.goto(`${BASE}/reader/reader.html#from=catalog`);
await p.waitFor("document.readyState === 'complete'");
await sleep(300);
ok(await p.eval(shown), "from the catalog's Open file: the Catalog button, before a file is chosen");
// choose a file, as the file dialog does
const { root } = await p.send("DOM.getDocument");
const { nodeId } = await p.send("DOM.querySelector", { nodeId: root.nodeId, selector: "#file" });
await p.send("DOM.setFileInputFiles", { nodeId, files: [resolve(process.env.OUT || "Out", BOOK)] });
await p.waitFor("typeof rendition !== 'undefined' && rendition && rendition.location && rendition.location.start", 30000);
await sleep(300);
ok(await p.eval("(e => !e.classList.contains('hidden'))(document.getElementById('back'))"),
   "a file open: the Catalog button still there");
await p.eval("document.getElementById('back').click()");
await p.waitFor("location.pathname.endsWith('/index.html')", 10000)
  .then(() => ok(true, "Catalog leads to the catalog"), () => ok(false, "Catalog leads to the catalog"));
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
