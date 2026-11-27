// Minimal Chrome DevTools driver: launch headless Chrome, one page, send commands.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const atExit = [];   // one "exit" listener for all the browsers of a check
process.on("exit", () => atExit.forEach(f => f()));
export async function launch({ width = 390, height = 844, touch = true, args = [] } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "epub-reader-test-"));
  // port 0: Chrome takes a free one and writes it into the profile (DevToolsActivePort), so
  // browsers started side by side never collide
  const proc = spawn("google-chrome", ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", ...args, "about:blank"], { stdio: "ignore", detached: true });
  // the browser and all its helper processes, at once (a closing browser would write the profile again)
  const kill = () => { try { process.kill(-proc.pid, "SIGKILL"); } catch {} };
  atExit.push(() => {   // the browser and its throwaway profile go with the check
    kill();
    try { rmSync(profile, { recursive: true, force: true }); } catch {}
  });
  let port = 0, targets;
  for (let i = 0; i < 150 && !port; i++) {
    try { port = +readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n")[0] || 0; } catch {}
    if (!port) await sleep(100);
  }
  if (!port) throw new Error("Chrome did not start (no DevToolsActivePort)");
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (targets.length) break; } catch {}
    await sleep(200);
  }
  const page = targets.find(t => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r, { once: true }));
  let id = 0; const pending = new Map(); const listeners = [];
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); }
    else if (m.method) listeners.forEach(l => l(m));
  });
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  listeners.push(m => { if (m.method === "Runtime.exceptionThrown") console.log("PAGE ERROR", m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); });
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: +(process.env.DPR || 2), mobile: touch });
  if (touch) {
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "hover", value: "none" }, { name: "pointer", value: "coarse" }] }).catch(() => {});
  }
  const api = {
    send,
    on(fn) { listeners.push(fn); },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async goto(url) { await send("Page.navigate", { url }); await sleep(500); },
    async tap(x, y) {
      await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
      await sleep(60);
      await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await sleep(400);
    },
    async click(x, y) {
      for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
        await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
      await sleep(300);
    },
    async waitFor(expr, ms = 20000) {
      const t = Date.now();
      while (Date.now() - t < ms) { try { if (await api.eval(expr)) return true; } catch {} await sleep(200); }
      throw new Error("timeout waiting for " + expr);
    },
    // screenshot into $SHOTS (a folder), only when it is set
    async shot(name) { if (!process.env.SHOTS) return; const { data } = await send("Page.captureScreenshot", { format: "png" }); writeFileSync(join(process.env.SHOTS, name), Buffer.from(data, "base64")); },
    close() { ws.close(); kill(); },
  };
  return api;
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
