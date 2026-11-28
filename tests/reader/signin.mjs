// The sign-in page of the network library (library_sync --serve --gate), as a phone from the
// internet sees it: the host is library.test (not this computer), so the gate is shut.
// node signin.mjs <gated base http://127.0.0.1:PORT> <library code>
import { launch, sleep } from "./cdp.mjs";
const [BASE, CODE] = process.argv.slice(2);
let fails = 0;
const ok = (c, msg) => { console.log((c ? "  ok   " : "  FAIL ") + msg); if (!c) fails++; };
const PORT = new URL(BASE).port, ORIGIN = `http://library.test:${PORT}`, AT = `${ORIGIN}/reader/reader.html`;
const MAP = `--host-resolver-rules=MAP library.test 127.0.0.1`;
const SECURE = `--unsafely-treat-insecure-origin-as-secure=${ORIGIN}`;   // as the router's https:// address
const browser = secure => launch({ args: secure ? [MAP, SECURE] : [MAP] });
const isReader = "!!document.getElementById('typoBtn')";
const isSignin = "!!document.getElementById('signin')";
const shown = sel => `(e => !!e && !!e.offsetParent)(document.querySelector(${JSON.stringify(sel)}))`;
async function fill(p, values) {
  await p.eval(`(v => { for (const [k, x] of Object.entries(v)) { const e = document.querySelector('#signin [name=' + k + ']');
    e.value = x; e.dispatchEvent(new Event("input", { bubbles: true })); } return 0; })(${JSON.stringify(values)})`);
}
const submit = p => p.eval("(f => { f.requestSubmit(); return 0; })(document.querySelector('#signin form'))");
const login = `jane.doe+${Date.now().toString(36)}@example.com`, pw = "quiet-harbour-1";

// plain http (not the https address): no password form, only where to go
let p = await browser(false);
await p.goto(AT);
await p.waitFor(isSignin);
ok(!(await p.eval(isReader)), "plain http: the reader is not given");
ok(!(await p.eval(shown("input[type=password]"))), "plain http: no password field");
ok(/https:\/\//.test(await p.eval("document.getElementById('signin').textContent")), "plain http: says to open the https:// address");
p.close();

// the https address: sign in, create an account
p = await browser(true);
await p.goto(AT);
await p.waitFor(isSignin);
ok(await p.eval(shown("input[type=password]")), "https: the sign-in form");
await fill(p, { login, password: "wrong-password" });
await submit(p);
await p.waitFor("/Wrong email or password/.test(document.getElementById('signin').textContent)", 15000).then(() => ok(true, "wrong password: said so"), () => ok(false, "wrong password: said so"));
await p.eval("document.querySelector('#signin [data-tab=create]').click()");
await sleep(100);
ok(await p.eval(shown("#signin [name=code]")), "create account: asks the library code");
await fill(p, { login, password: pw, password2: pw, code: CODE });
await submit(p);
await p.waitFor(isReader, 20000).then(() => ok(true, "account created: the page asked for opens"), () => ok(false, "account created: the page asked for opens"));
const acc = JSON.parse(await p.eval("localStorage.getItem('librarySync:account') || 'null'") || "null");
ok(acc && acc.login === login && /^[0-9a-f]{64}$/.test(acc.token), "the account is kept for sync (librarySync:account)");
ok(!(await p.eval("document.cookie.includes('libsess')")), "the key is not readable by the page (HttpOnly)");
p.close();

// a device signed in before the gate: its sync account is enough, no form
p = await browser(true);
await p.send("Page.addScriptToEvaluateOnNewDocument", { source: `if (location.host === ${JSON.stringify(`library.test:${PORT}`)} && !sessionStorage.seeded) { sessionStorage.seeded = 1; localStorage.setItem("librarySync:account", ${JSON.stringify(JSON.stringify(acc))}); }` });
await p.goto(AT);
await p.waitFor(isReader, 20000).then(() => ok(true, "signed in before: let in without the form"), () => ok(false, "signed in before: let in without the form"));
// signing out closes the library again
await p.eval("LibrarySync.logout().then(() => 0)");
await p.eval("(location.reload(), 0)");   // as "Sign out" does (a reload asks the server again)
await sleep(500);
await p.waitFor(isSignin, 15000).then(() => ok(true, "signed out: the sign-in page again"), () => ok(false, "signed out: the sign-in page again"));
ok(await p.eval(shown("input[type=password]")) && !(await p.eval(isReader)), "signed out: the form, not the reader");
p.close();
console.log(fails ? `${fails} FAILED` : "ALL OK");
process.exit(fails ? 1 : 0);
