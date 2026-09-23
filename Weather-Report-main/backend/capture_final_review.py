"""Visual review capture of the RESTORED stable Forecast layout.

Renders the page as-is (real API data) at desktop and mobile sizes plus
per-condition page-atmosphere states. Screenshots go to
scene-previews/review/restore_*. Read-only: render, measure, screenshot.
"""
import os
import sys

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "scene-previews", "review")
os.makedirs(OUT, exist_ok=True)
GAUGE = []


def gauge(label, ok, detail=""):
    GAUGE.append((label, bool(ok), detail))
    print(f"[{'OK' if ok else '--'}] {label}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ---------------- Desktop 1440x900 ----------------
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2600)
    d = pg.evaluate(r"""(() => {
        const hero = document.querySelector('#view-forecast > div');
        const r = hero.getBoundingClientRect();
        const scene = hero.querySelector('#card-scene');
        const sr = scene ? scene.getBoundingClientRect() : null;
        const glyph = document.getElementById('hero-weather-glyph');
        return { w: Math.round(r.width), h: Math.round(r.height),
                 sceneIn: scene && sr.left >= r.left - 1 && sr.right <= r.right + 1,
                 glyph: !!glyph, cond: document.body.dataset.condition,
                 temp: document.getElementById('hero-temperature')?.textContent,
                 condText: document.getElementById('hero-condition-text')?.textContent,
                 sideBySide: getComputedStyle(document.getElementById('view-forecast')).flexDirection,
                 ovfx: document.documentElement.scrollWidth - document.documentElement.clientWidth };
    })()""")
    gauge("desktop: stable hero card renders", d["w"] > 0 and d["h"] > 0, f"{d['w']}x{d['h']}")
    gauge("desktop: card scene clipped inside hero", d["sceneIn"])
    gauge("desktop: legacy glyph + condition live", d["glyph"] and bool(d["condText"]),
          f"{d['cond']} → {d['condText']}, {d['temp']}°")
    gauge("desktop: split layout active on wide screens", "row" in d["sideBySide"], d["sideBySide"])
    gauge("desktop: no overflow, no JS errors", d["ovfx"] == 0 and not errs, "; ".join(errs[:2]))
    pg.screenshot(path=f"{OUT}/restore_desktop_1440.png")
    pg.screenshot(path=f"{OUT}/restore_desktop_1440_full.png", full_page=True)

    # ---------------- Per-condition atmosphere ----------------
    for cat, is_day, temp in [("clear-day", 1, 24), ("cloudy", 1, 16), ("rain", 1, 13),
                              ("thunder", 1, 21), ("fog", 1, 12), ("snow", 1, -2),
                              ("clear-night", 0, 18)]:
        pg.evaluate(f"""(() => {{
            applyDynamicSky('{cat}', 'mild');
            applySkyFx('{cat}', {is_day});
            applySceneFx('{cat}', {is_day});
            applyCardSceneFx('{cat}', {is_day}, 'mild');
            applyPrecipFx('{cat}');
            document.body.dataset.condition = '{cat}';
        }})()""")
        pg.wait_for_timeout(2300)
        pg.screenshot(path=f"{OUT}/restore_atmo_{cat}.png")
    gauge("desktop: 7 atmosphere states captured", True)

    # ---------------- Mobile 390x844 ----------------
    m = browser.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE, wait_until="networkidle", timeout=60000)
    m.wait_for_timeout(2600)
    mi = m.evaluate(r"""(() => {
        const hero = document.querySelector('#view-forecast > div');
        const r = hero.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height),
                 fits: r.width <= 390,
                 sceneIn: !!hero.querySelector('#card-scene'),
                 ovfx: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                 stacked: getComputedStyle(document.getElementById('view-forecast')).flexDirection };
    })()""")
    gauge("mobile: hero renders full-width stacked", mi["fits"] and "column" in mi["stacked"],
          f"{mi['w']}x{mi['h']}, {mi['stacked']}")
    gauge("mobile: card scene present", mi["sceneIn"])
    gauge("mobile: no horizontal overflow", mi["ovfx"] == 0, f"ovfx={mi['ovfx']}")
    m.screenshot(path=f"{OUT}/restore_mobile_390.png")
    m.screenshot(path=f"{OUT}/restore_mobile_390_full.png", full_page=True)
    m.close()
    pg.close()
    browser.close()

print(f"\n{sum(1 for _, ok, _ in GAUGE if ok)}/{len(GAUGE)} gauges OK — scene-previews/review/restore_*")
sys.exit(0 if all(ok for _, ok, _ in GAUGE) else 1)
