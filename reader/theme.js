// The app's one theme, shared by the catalog and the reader: Auto (the system's light or dark),
// light, sepia or dark. It lives in the reader's settings (epubReader:prefs.theme), which sync.js
// already carries between devices of the same kind. Loaded at the end of <head>, before the page
// shows, so a dark evening never starts with a white flash: <html data-theme> holds the theme
// shown (never "auto"), data-theme-choice the one chosen (auto too), and the theme-color meta
// (#themeColor) the page's --bg.
(function () {
  "use strict";
  var KEY = "epubReader:prefs", TIME = "epubReader:prefsTime";
  var THEMES = ["auto", "light", "sepia", "dark"];
  var LABELS = { auto: "Theme: Auto (as the system)", light: "Theme: light", sepia: "Theme: sepia", dark: "Theme: dark" };
  var mq = null;
  try { mq = window.matchMedia("(prefers-color-scheme: dark)"); } catch (e) {}

  function readPrefs() {
    try {
      var v = JSON.parse(localStorage.getItem(KEY));
      return v && typeof v === "object" && !Array.isArray(v) ? v : null;
    } catch (e) { return null; }
  }
  function known(t) { return THEMES.indexOf(t) >= 0 ? t : "auto"; }
  function stored() { var p = readPrefs(); return known(p && p.theme); }
  function shown(t) {
    t = known(arguments.length ? t : stored());
    return t === "auto" ? (mq && mq.matches ? "dark" : "light") : t;
  }
  function next(t) { var i = THEMES.indexOf(t); return THEMES[i < 0 ? 1 : (i + 1) % THEMES.length]; }
  function label(t) { return LABELS[known(t)]; }
  function paint(t) {
    var html = document.documentElement;
    t = known(arguments.length ? t : stored());
    html.dataset.theme = shown(t);
    html.dataset.themeChoice = t;   // the chosen one: Auto puts an "A" over the theme button
    var meta = document.getElementById("themeColor");
    if (!meta) return;
    try {
      var bg = getComputedStyle(html).getPropertyValue("--bg").trim();
      if (bg) meta.content = bg;
    } catch (e) {}
  }
  function save(t) {
    var p = readPrefs() || {};
    p.theme = t;
    try {
      localStorage.setItem(KEY, JSON.stringify(p));
      localStorage.setItem(TIME, String(Date.now()));
    } catch (e) {}
    paint(t);
  }
  function watch(fn) {
    if (mq) {
      if (mq.addEventListener) mq.addEventListener("change", function () { fn(); });
      else if (mq.addListener) mq.addListener(function () { fn(); });
    }
    window.addEventListener("storage", function (e) { if (e.key === KEY) fn(); });
  }

  window.AppTheme = { THEMES: THEMES, stored: stored, shown: shown, next: next, label: label,
                      paint: paint, save: save, watch: watch };
  paint();
})();
