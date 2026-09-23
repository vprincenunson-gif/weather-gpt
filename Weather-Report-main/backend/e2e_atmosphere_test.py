"""Page-atmosphere purity + reduced-motion verification.

Focused successor of the reverted compact-hero layout experiment: verifies
the subtle, condition-true page background (clear skies carry NO cloud
band, a reduced horizon glow instead of a large sun disc, a faint night
glow instead of a large moon) against the stable split hero layout.
Works against the Living Sky fx layer, independent of hero geometry.

Requires the Flask server running on 127.0.0.1:5000.
"""
import os
import sys

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))


def force(page, cat, is_day, temp):
    band = "hot" if temp >= 33 else "cold" if temp <= 0 else "mild"
    page.evaluate(f"""(() => {{
        applyDynamicSky('{cat}', '{band}');
        applySkyFx('{cat}', {is_day});
        applySceneFx('{cat}', {is_day});
        applyCardSceneFx('{cat}', {is_day}, '{band}');
        applyPrecipFx('{cat}');
        document.body.dataset.condition = '{cat}';
    }})()""")


def atm_state(pg):
    return pg.evaluate(r"""(() => {
        const fx = document.getElementById('sky-fx');
        const clouds = document.querySelector('.sky-clouds');
        const sun = document.querySelector('.sky-sun');
        const rays = document.querySelector('.sky-rays');
        return { tier: fx.dataset.clouds, cloudsOp: getComputedStyle(clouds).opacity,
                 sunOp: getComputedStyle(sun).opacity, raysOp: getComputedStyle(rays).opacity,
                 sunW: Math.round(sun.getBoundingClientRect().width) };
    })()""")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2400)

    # Whatever the real API reports at the default city, force each sky and
    # let the 2s opacity transitions settle before reading computed styles.
    force(pg, "clear-day", 1, 24)
    pg.wait_for_timeout(2100)
    a = atm_state(pg)
    check("atmosphere clear-day: NO cloud band (tier 0) — clear means clear",
          a["tier"] == "0" and a["cloudsOp"] == "0", f"tier={a['tier']} op={a['cloudsOp']}")
    check("atmosphere clear-day: sun is a subtle glow, no large disc (<= 30% vw)",
          a["sunW"] <= 1440 * 0.30, f"w={a['sunW']}")
    check("atmosphere clear-day: no crepuscular rays", a["raysOp"] == "0", a["raysOp"])
    force(pg, "clear-night", 0, 18)
    pg.wait_for_timeout(2100)
    an = atm_state(pg)
    check("atmosphere night: dark sky, faint glow only (no large moon, no clouds)",
          an["tier"] == "0" and an["cloudsOp"] == "0" and an["sunOp"] == "0.28",
          f"op={an['sunOp']} tier={an['tier']}")
    force(pg, "cloudy", 1, 16)
    pg.wait_for_timeout(2100)
    ac = atm_state(pg)
    check("atmosphere cloudy: subtle clouds only (tier 2, no sun)",
          ac["tier"] == "2" and ac["cloudsOp"] == "0.8" and ac["sunOp"] == "0", str(ac))
    force(pg, "partly-cloudy-day", 1, 22)
    pg.wait_for_timeout(2100)
    apc = atm_state(pg)
    check("atmosphere partly-cloudy: whisper clouds + subtle glow (no large disc, no rays)",
          apc["tier"] == "1" and apc["cloudsOp"] == "0.4"
          and apc["sunOp"] == "1" and apc["raysOp"] == "0" and apc["sunW"] <= 1440 * 0.30, str(apc))
    force(pg, "rain", 1, 13)
    pg.wait_for_timeout(2100)
    ar = atm_state(pg)
    check("atmosphere rain: rain atmosphere only (tier 3 clouds, no sun)",
          ar["tier"] == "3" and ar["cloudsOp"] == "1" and ar["sunOp"] == "0", str(ar))
    force(pg, "snow", 1, -2)
    pg.wait_for_timeout(2100)
    asw = atm_state(pg)
    check("atmosphere snow: whisper clouds + sparkle (no sun)",
          asw["tier"] == "1" and asw["cloudsOp"] == "0.22" and asw["sunOp"] == "0", str(asw))
    check("no page errors", not errs, "; ".join(errs[:2]))
    pg.close()

    # ---------- reduced motion still renders the app ----------
    rm = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    rm.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    rm.wait_for_timeout(2000)
    r = rm.evaluate("""(() => ({
        clouds: getComputedStyle(document.querySelector('.cb-1')).animationName,
        rays: getComputedStyle(document.querySelector('.sky-rays')).animationName,
        hero: !!document.querySelector('#view-forecast > div'),
    }))()""")
    check("reduced-motion: cloud drift disabled", r["clouds"] == "none", r["clouds"])
    check("reduced-motion: rays sway disabled", r["rays"] == "none", r["rays"])
    check("reduced-motion: hero layout still rendered", r["hero"])
    rm.close()
    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== {len(results) - len(fails)}/{len(results)} atmosphere checks passed =====")
if fails:
    print("FAILED:")
    for f in fails:
        print(" -", f)
sys.exit(1 if fails else 0)
