"""E2E audit: startup first-paint — ZERO raw i18n keys / ligature names.

Reproduces the reported bug (raw strings like near_me, light_mode,
location_on, partly_cloudy_day, chat, agriculture, radar briefly visible
at startup) and locks the fix:

A. SENTINEL — an init-script MutationObserver installed before any page
   script runs records every CONNECTED text node that would be EXPOSED
   at paint (computed display:none / visibility:hidden anywhere up the
   chain, or .hidden/.invisible classes, all count as NOT exposed) while
   matching either
     - ligature source text inside a .material-symbols-outlined ancestor
       while the icon font is unconfirmed (neither html.gi nor html.gif
       set) — the actual bug in the screenshot, or
     - a [data-i18n] element whose text equals its raw key name.
   Detached nodes are ignored (they cannot paint).

B. SCENARIOS (each in a fresh context; language seeded via init script,
   so the FIRST navigation is the cold first paint — no reload caching)
   1. Cold load, HI persisted, desktop + mobile.
   2. Cache disabled (service workers blocked, no app shell).
   3. SLOW icon font — the Google Fonts CSS is rewritten to point
      Material Symbols at a local threaded server that delays the woff2
      by 2.5s: during the flight icons must be unexposed, nav text
      already localized HI; after settle html.gi appears with real
      glyphs; sentinel zero.
   4. FAILED icon font (gstatic blocked) — 3s backstop degrades to
      plain-text icons (html.gif), nav text stays localized, sentinel
      zero (gif counts as confirmed: honest fallback, never raw keys).
   5. BROKEN i18n bundle (i18n.js aborted) — boot translator degrades to
      EN markup, script.js applies no pageerror.
   6. localStorage throwing — boot still renders chrome (EN) with zero
      raw-key violations.

C. OFFLINE shell regression (SW-armed context, server dark): navigation
   still serves the cached shell — no regression from the i18n work.

Requires the Flask server on 127.0.0.1:5000 and internet access for the
Google Fonts hosts (delayed/blocked on purpose in some scenarios).
"""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests as pyrequests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(__file__), "..", "scene-previews", "review")
os.makedirs(OUT, exist_ok=True)

SLOW_FONT_PORT = 8777
SLOW_FONT_DELAY = 2.5

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL.append(name)
        print(f" FAIL {name} {detail}")


def txt(pg, sel):
    try:
        return pg.eval_on_selector(sel, "el => el.textContent.trim()")
    except Exception:
        return None


# ---------------------------------------------------------------- sentinel
SENTINEL = r"""
(() => {
  if (window.__wgSentinel) return;
  window.__wgSentinel = { violations: [] };
  const V = window.__wgSentinel.violations;
  function exposed(el) {
    // Paint-time truth: computed visibility inherits; display:none hides
    // subtrees; Tailwind utility classes may apply slightly later, so
    // class-based checks count as hidden too.
    try {
      if (getComputedStyle(el).visibility === "hidden") return false;
    } catch (e) {}
    for (let a = el; a; a = a.parentElement) {
      const cl = a.classList;
      if (cl && (cl.contains("hidden") || cl.contains("invisible"))) return false;
      if (a.style && (a.style.display === "none" || a.style.visibility === "hidden")) return false;
      try {
        if (getComputedStyle(a).display === "none") return false;
      } catch (e) {}
    }
    return true;
  }
  function inspectText(node) {
    if (!node || node.nodeType !== 3) return;
    const parent = node.parentElement;
    if (!parent || !parent.isConnected) return;          // detached: cannot paint
    const s = (node.textContent || "").trim();
    if (!s) return;
    if (!exposed(parent)) return;                        // gated: cannot paint
    // Raw dictionary key painted as literal text?
    if (parent.hasAttribute("data-i18n") && parent.getAttribute("data-i18n") === s) {
      V.push("data-i18n:" + s);
      return;
    }
    // Ligature source text exposed while the icon font is unconfirmed?
    let a = parent, isIcon = false;
    for (; a; a = a.parentElement) {
      if (a.classList && a.classList.contains("material-symbols-outlined")) { isIcon = true; break; }
    }
    const settled = document.documentElement.classList.contains("gi") ||
                    document.documentElement.classList.contains("gif");
    if (isIcon && !settled) V.push("ligature:" + s);
  }
  function walk(node) {
    if (!node) return;
    if (node.nodeType === 3) { inspectText(node); return; }
    if (node.nodeType !== 1) return;
    const w = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let t;
    while ((t = w.nextNode())) inspectText(t);
  }
  function arm() {
    if (!document.documentElement) return false;
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === "characterData") { inspectText(m.target); continue; }
        m.addedNodes.forEach(walk);
      }
    }).observe(document, { childList: true, subtree: true, characterData: true });
    return true;
  }
  const t = setInterval(() => { if (arm()) clearInterval(t); }, 0);
})();
"""

HI_SEED = "try{localStorage.setItem('weathergpt-lang','hi')}catch(e){}"


def violations(pg):
    try:
        return pg.evaluate("window.__wgSentinel ? window.__wgSentinel.violations : null")
    except Exception:
        return None


def intercept_json(ctx, pattern, body):
    def handler(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    ctx.route(pattern, handler)


HOURLY_PAYLOAD = {
    "current": {
        "time": "2026-09-23T14:00", "temperature_2m": 29.4,
        "relative_humidity_2m": 61, "apparent_temperature": 32.0,
        "is_day": 1, "weather_code": 2, "wind_speed_10m": 12.5,
        "wind_direction_10m": 200, "precipitation": 0.0,
        "surface_pressure": 1008.0, "dew_point_2m": 21.0, "uv_index": 6.0,
    },
    "hourly": {"time": [], "temperature_2m": [29.4] * 8,
               "precipitation_probability": [5] * 8, "weather_code": [2] * 8},
    "daily": {
        "time": ["2026-09-23", "2026-09-24"],
        "temperature_2m_max": [31.0, 30.0], "temperature_2m_min": [23.0, 22.5],
        "precipitation_probability_max": [60, 20], "precipitation_sum": [4.2, 0.5],
        "wind_speed_10m_max": [22.0, 15.0], "weather_code": [61, 2],
    },
}


def arm_context(ctx, seed_hi=True):
    intercept_json(ctx, "**/api/weather*",
                   {"weather": HOURLY_PAYLOAD, "alerts": [], "rain_timeline": None})
    intercept_json(ctx, "**/api/report*", {"report": "ok."})
    intercept_json(ctx, "**/api/alerts*", {"alerts": []})
    if seed_hi:
        ctx.add_init_script(HI_SEED)
    ctx.add_init_script(SENTINEL)


# ------------------------------------------------- local slow-font server
def start_slow_font_server(font_bytes):
    """Serves the real woff2 after a fixed delay, in its own thread, so the
    Playwright event loop is never blocked (a sleeping route handler would
    serialize every Playwright call and destroy the flight window)."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                time.sleep(SLOW_FONT_DELAY)
                self.send_response(200)
                self.send_header("Content-Type", "font/woff2")
                self.send_header("Content-Length", str(len(font_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(font_bytes)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                pass  # browser gave up or closed mid-response — fine

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", SLOW_FONT_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fetch_icon_font():
    """Download the real Material Symbols woff2 once (Chrome UA -> woff2 CSS
    with the same URLs the browser will request)."""
    css_url = (
        "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined"
        ":opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap"
    )
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    css = pyrequests.get(css_url, headers={"User-Agent": ua}, timeout=15).text
    m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css)
    if not m:
        raise RuntimeError("no woff2 URL found in Google Fonts CSS")
    font = pyrequests.get(m.group(1), timeout=15).content
    return css, m.group(1), font


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ============ 0: NEGATIVE CONTROL — sentinel catches the pre-fix bug ============
        # Serve style.css WITHOUT the gate rules and DELAY the icon font
        # (the real-world bug: font-display:swap paints fallback text for
        # the whole fetch). Under the exact conditions of scenario 3 minus
        # the gate, the sentinel MUST flag raw ligatures — proving it is
        # not vacuously green.
        with open(os.path.join(os.path.dirname(__file__), "..", "frontend", "style.css"),
                  encoding="utf-8") as f:
            css_src = f.read()
        css_nogate = re.sub(
            r"ICON FONT READINESS GATE.*?THEME TOGGLE", "THEME TOGGLE", css_src, flags=re.S)
        assert css_nogate != css_src, "gate block not found in style.css"

        css_body, font_url, font_bytes = fetch_icon_font()
        slow_srv = start_slow_font_server(font_bytes)
        slow_url = f"http://127.0.0.1:{SLOW_FONT_PORT}/slow.woff2"

        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        arm_context(ctx)
        ctx.route("**/style.css", lambda route: route.fulfill(
            status=200, content_type="text/css", body=css_nogate))

        def rewrite_css_ctrl(route):
            body = css_body.replace(font_url, slow_url)
            route.fulfill(status=200, content_type="text/css", body=body,
                          headers={"Cache-Control": "no-store"})

        ctx.route("**fonts.googleapis.com/**", rewrite_css_ctrl)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(400)  # mid-flight: font arrives at ~2.5s
        check("negative control: pre-fix page DOES expose raw ligatures mid-flight (sentinel catches it)",
              bool(violations(page)) and all(v.startswith("ligature:") for v in violations(page)),
              str(violations(page))[:160])
        ctx.close()
        slow_srv.shutdown()

        # ============ 1: cold load, HI persisted (desktop + mobile) ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx)

        page.goto(BASE, wait_until="domcontentloaded")
        check("cold: <html lang> is hi at first paint",
              (page.evaluate("document.documentElement.lang") or "") == "hi")
        check("cold: nav Map label already localized HI at first paint",
              "रडार" in (txt(page, '[data-i18n="navMap"]') or ""),
              txt(page, '[data-i18n="navMap"]'))
        check("cold: header location pill not a raw ligature",
              (txt(page, "#header-location-name") or "") != "near_me",
              txt(page, "#header-location-name"))
        check("cold: sentinel recorded zero raw keys/ligatures",
              violations(page) == [], str(violations(page))[:200])
        page.screenshot(path=os.path.join(OUT, "i18n_audit_hi_desktop.png"))

        page2 = ctx.new_page()
        page2.set_viewport_size({"width": 390, "height": 844})
        page2.goto(BASE, wait_until="domcontentloaded")
        page2.wait_for_timeout(250)
        check("cold mobile: nav Map label localized HI",
              "रडार" in (txt(page2, '[data-i18n="navMap"]') or ""),
              txt(page2, '[data-i18n="navMap"]'))
        check("cold mobile: sentinel zero", violations(page2) == [],
              str(violations(page2))[:200])
        page2.screenshot(path=os.path.join(OUT, "i18n_audit_hi_mobile.png"))
        check("cold: zero page errors", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 2: cache disabled (service workers blocked) ============
        ctx = browser.new_context(service_workers="block",
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        check("cache-off: no service worker shell in play",
              page.evaluate("navigator.serviceWorker.controller === null"))
        check("cache-off: nav Map label localized HI at first paint",
              "रडार" in (txt(page, '[data-i18n="navMap"]') or ""),
              txt(page, '[data-i18n="navMap"]'))
        check("cache-off: sentinel zero", violations(page) == [],
              str(violations(page))[:200])
        check("cache-off: zero page errors", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 3: SLOW icon font (woff2 delayed 2.5s) ============
        css_body, font_url, font_bytes = fetch_icon_font()
        slow_srv = start_slow_font_server(font_bytes)
        slow_url = f"http://127.0.0.1:{SLOW_FONT_PORT}/slow.woff2"
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx)

        def rewrite_css(route):
            body = css_body.replace(font_url, slow_url)
            route.fulfill(status=200, content_type="text/css", body=body,
                          headers={"Cache-Control": "no-store"})

        ctx.route("**fonts.googleapis.com/**", rewrite_css)

        page.goto(BASE, wait_until="domcontentloaded")
        check("slow font: html.gi NOT set while font in flight",
              page.evaluate("!document.documentElement.classList.contains('gi')"),
              str(page.evaluate("document.documentElement.className")))
        check("slow font: header icons not exposed during flight (visibility hidden)",
              page.eval_on_selector(
                  "#app-header .material-symbols-outlined",
                  "el => getComputedStyle(el).visibility === 'hidden'"))
        check("slow font: nav Map label localized HI DURING font flight",
              "रडार" in (txt(page, '[data-i18n="navMap"]') or ""),
              txt(page, '[data-i18n="navMap"]'))
        page.evaluate(
            """() => {
              window.__exposed = 0;
              window.__sampler = setInterval(() => {
                const settled = document.documentElement.classList.contains('gi') ||
                                document.documentElement.classList.contains('gif');
                if (settled) return;               // only sample the flight
                document.querySelectorAll('.material-symbols-outlined').forEach(el => {
                  if (getComputedStyle(el).visibility !== 'hidden') window.__exposed++;
                });
              }, 100);
            }"""
        )
        page.wait_for_timeout(int((SLOW_FONT_DELAY + 1.6) * 1000))  # font + settle margin
        exposed_during_flight = page.evaluate(
            "(clearInterval(window.__sampler), window.__exposed)")
        check("slow font: icons stayed hidden for the whole flight (sampled @10Hz)",
              exposed_during_flight == 0,
              f"exposed samples during flight: {exposed_during_flight}")
        check("slow font: html.gi set after font arrives",
              page.evaluate("document.documentElement.classList.contains('gi')"),
              str(page.evaluate("document.documentElement.className")))
        check("slow font: nav icons render with the icon font after settle",
              page.eval_on_selector(
                  '[data-view="view-map"] .material-symbols-outlined',
                  """el => {
                    const cs = getComputedStyle(el);
                    return cs.visibility === 'visible' &&
                      cs.fontFamily.includes('Material Symbols');
                  }"""))
        check("slow font: sentinel zero (no raw ligature/key ever exposed)",
              violations(page) == [], str(violations(page))[:200])
        page.screenshot(path=os.path.join(OUT, "i18n_audit_slowfont.png"))
        check("slow font: zero page errors", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()
        slow_srv.shutdown()

        # ============ 4: FAILED icon font (gstatic blocked) ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx)
        ctx.route("**fonts.gstatic.com/**", lambda route: route.abort())
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(4200)  # fonts.ready settle or 3s backstop

        check("failed font: backstop applied (gif)",
              page.evaluate("document.documentElement.classList.contains('gif')"),
              str(page.evaluate("document.documentElement.className")))
        check("failed font: nav Map label still localized HI (never raw)",
              "रडार" in (txt(page, '[data-i18n="navMap"]') or ""),
              txt(page, '[data-i18n="navMap"]'))
        check("failed font: sentinel zero (gif fallback is honest, not raw keys)",
              violations(page) == [], str(violations(page))[:200])
        page.screenshot(path=os.path.join(OUT, "i18n_audit_failedfont.png"))
        check("failed font: zero page errors", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 5: BROKEN i18n bundle (i18n.js aborted) ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx, seed_hi=False)
        ctx.route("**/i18n.js", lambda route: route.abort())
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        check("broken i18n: static chrome present (page usable)",
              page.evaluate("document.querySelectorAll('[data-i18n]').length > 5"))
        check("broken i18n: zero raw-key violations (EN markup shown as-is)",
              violations(page) == [], str(violations(page))[:200])
        check("broken i18n: zero uncaught page errors (applyChromeI18n guarded)",
              len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 6: localStorage throwing ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        arm_context(ctx, seed_hi=False)
        ctx.add_init_script(
            "Object.defineProperty(window, 'localStorage', "
            "{ get() { throw new Error('storage blocked'); } });"
        )
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        check("storage error: chrome rendered with zero raw-key violations",
              violations(page) == [], str(violations(page))[:200])
        check("storage error: nav Map label shows EN fallback (not a raw key)",
              (txt(page, '[data-i18n="navMap"]') or "") == "Radar Map",
              txt(page, '[data-i18n="navMap"]'))
        check("storage error: zero page errors", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 7: offline shell fallback regression (SW armed) ============
        ctx = browser.new_context()
        page = ctx.new_page()
        intercept_json(ctx, "**/api/weather*",
                       {"weather": HOURLY_PAYLOAD, "alerts": [], "rain_timeline": None})
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # SW install + shell cache
        armed = page.evaluate(
            "async () => !!(await navigator.serviceWorker.getRegistration())")
        ctx.offline = True
        page.reload(wait_until="load")
        page.wait_for_timeout(500)
        check("offline: SW shell still serves the app (no regression)",
              armed and page.evaluate(
                  "document.querySelectorAll('[data-i18n]').length > 5"))
        ctx.close()

        browser.close()

    print(f"\n==== RESULT: {PASS} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("Failed:", *FAIL, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run())
