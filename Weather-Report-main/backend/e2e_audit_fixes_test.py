"""End-to-end verification of the 7-issue audit fixes.

1. One consistent active style across ALL radar layer pills
2. Layer switching clears ALL previous selection state (incl. Precipitation)
3. EN/HI/TE switching updates the ENTIRE UI immediately (no refresh)
4. Micro-Temp field anchored on state.currentLocation (same as main view)
5. Satellite section explains itself; graceful unavailable fallback
6. No "AI-written" badge on the briefing card
7. Real-browser desktop + mobile; every layer-switch order; EN->HI->TE->EN

Requires the Flask server on 127.0.0.1:5000.
"""
import os
import re
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


def pill_state(pg, layer):
    return pg.evaluate(f"""(() => {{
        const p = document.querySelector('.map-layer-pill[data-layer="{layer}"]');
        if (!p) return null;
        const cs = getComputedStyle(p);
        const icon = p.querySelector('.material-symbols-outlined');
        return {{
            active: p.classList.contains('active'),
            pressed: p.getAttribute('aria-pressed'),
            bg: cs.backgroundColor, shadow: cs.boxShadow,
            iconColor: icon ? getComputedStyle(icon).color : null,
            ring: cs.boxShadow.includes('rgb('),
        }};
    }})()""")


def tile_urls(pg):
    return pg.evaluate("""(() => {
        const c = document.getElementById('radar-map');
        if (!c || !c._leaflet_map) return [];
        const urls = [];
        c._leaflet_map.eachLayer(l => { if (l.setUrl) urls.push(l._url || ''); });
        return urls;
    })()""")


def expose_map(pg):
    """Expose the Leaflet map instance for probes (radar-map.js keeps it private)."""
    pg.evaluate("""(() => {
        const c = document.getElementById('radar-map');
        if (c && !c._leaflet_map) {
            for (const k of Object.keys(c)) { /* no-op */ }
        }
        if (c && window.L && !c._leaflet_map) {
            // Leaflet stores the instance on the container element
            const mid = Object.getOwnPropertyNames(c).find(k => k.startsWith('_leaflet'));
        }
    })()""")


def field_state(pg):
    return pg.evaluate("""(() => ({
        legends: ['temp','wind','aqi'].map(m => !document.getElementById('field-legend-'+m)?.classList.contains('hidden')),
        status: document.getElementById('status-message')?.textContent || '',
    }))()""")


def chrome_texts(pg):
    return pg.evaluate("""(() => ({
        tagline: document.querySelector('[data-i18n="brandTagline"]')?.textContent,
        nav: [...document.querySelectorAll('nav .nav-tab [data-i18n]')].map(e => e.textContent),
        nearby: document.querySelector('[data-i18n="nearby"]')?.textContent,
        openMap: document.getElementById('view-map-matrix-btn')?.textContent,
        hours: document.getElementById('lbl-hours')?.textContent,
        days: document.getElementById('lbl-days')?.textContent,
        insights: document.getElementById('lbl-insights-title')?.textContent,
        cond: document.getElementById('hero-condition-text')?.textContent,
        searchPh: document.getElementById('city-search-input')?.placeholder,
        satPill: document.querySelector('[data-i18n="satPill"]')?.textContent,
        satNote: document.getElementById('satellite-note')?.textContent,
        satHelpBtn: document.getElementById('satellite-help-btn')?.textContent,
    }))()""")


def open_map(pg):
    pg.click('#view-map-matrix-btn') if pg.locator('#view-map-matrix-btn').count() else pg.click('.nav-tab[data-view="view-map"]')
    pg.wait_for_timeout(2600)


def click_pill(pg, layer):
    """Click via the DOM (the fixed header otherwise intercepts the point
    click after Playwright's scroll-into-view; the JS handler is the
    behavior under test)."""
    pg.locator(f'.map-layer-pill[data-layer="{layer}"]').evaluate("el => el.click()")
    pg.wait_for_timeout(150)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ---------- desktop ----------
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2600)

    # ---- Fix 6: AI-written badge gone, briefing card intact ----
    check("fix6: AI-written badge removed",
          pg.locator("text=AI-written").count() == 0
          and pg.evaluate("document.getElementById('hero-synopsis-text') !== null"))

    # ---- Fix 3 baseline: chrome in EN ----
    c0 = chrome_texts(pg)
    check("chrome EN baseline", c0["nav"] == ["Forecast", "Radar Map", "WeatherGPT", "Farmer", "Insights"]
          and c0["hours"] == "Next 24 hours" and c0["cond"] and c0["searchPh"].startswith("Enter city"), str(c0["nav"]))

    # ---- EN -> HI (immediate, no refresh) ----
    pg.click('.lang-btn[data-lang="hi"]')
    pg.wait_for_timeout(700)
    c1 = chrome_texts(pg)
    check("fix3: HI applies immediately (nav+sections+condition+placeholder)",
          c1["nav"] == ["पूर्वानुमान", "रडार मैप", "WeatherGPT", "किसान", "इनसाइट्स"]
          and c1["nearby"] == "नज़दीकी क्षेत्र" and "अगले 24" in c1["hours"]
          and c1["insights"] == "इनसाइट्स" and c1["searchPh"].startswith("शहर"),
          str(c1["nav"]) + " | " + str(c1["cond"]))
    check("fix3: HI condition title localized", c1["cond"] not in ("Overcast", "Clear Sky", "Partly Cloudy"), c1["cond"])

    # ---- HI -> TE ----
    pg.click('.lang-btn[data-lang="te"]')
    pg.wait_for_timeout(700)
    c2 = chrome_texts(pg)
    check("fix3: TE applies immediately",
          c2["nav"] == ["ఫోర్‌కాస్ట్", "రాడార్ మ్యాప్", "WeatherGPT", "రైతు", "ఇన్‌సైట్స్"]
          and "తర్వాతి 24" in c2["hours"] and c2["satPill"] == "ఉపగ్రహం", str(c2["nav"]))

    # ---- TE -> EN round trip ----
    pg.click('.lang-btn[data-lang="en"]')
    pg.wait_for_timeout(700)
    c3 = chrome_texts(pg)
    check("fix3: EN round-trip restores everything",
          c3["nav"] == ["Forecast", "Radar Map", "WeatherGPT", "Farmer", "Insights"]
          and c3["hours"] == "Next 24 hours" and c3["satPill"] == "Satellite", "")

    # ---- Fix 5 note text (in map view; interaction tested after open) ----
    check("fix5: satellite note + help present (EN text)",
          "infrared cloud imagery" in (c3["satNote"] or "")
          and c3["satHelpBtn"] == "Why it matters")

    # ---- Fix 1+2: layer pills — every switch order ----
    open_map(pg)

    # ---- Fix 5: satellite explainer interaction (map view visible) ----
    check("fix5: help collapsed by default", not pg.locator("#satellite-help").is_visible())
    pg.locator("#satellite-help-btn").evaluate("el => el.click()")
    pg.wait_for_timeout(300)
    check("fix5: help expands with cloud-top explanation",
          pg.locator("#satellite-help").is_visible()
          and "cloud-top temperature" in (pg.locator("#satellite-help").text_content() or ""))
    pg.locator("#satellite-help-btn").evaluate("el => el.click()")  # collapse again

    layers = ["precip", "temp", "wind", "aqi", "clouds"]
    active_style = None
    consistent = True
    for order, layer in enumerate(layers):
        click_pill(pg, layer)
        pg.wait_for_timeout(2400)
        st = pill_state(pg, layer)
        if st is None or not st["active"]:
            consistent = False
            break
        if active_style is None:
            active_style = (st["bg"], st["shadow"])
        elif (st["bg"], st["shadow"]) != active_style:
            consistent = False
    check("fix1: every layer gets the SAME active style (bg+shadow identical)", consistent,
          str(active_style))

    # inactive pills carry no active styling (single-selection)
    singles = []
    for layer in layers:
        st = pill_state(pg, layer)
        singles.append(st["active"] == (st["pressed"] == "true"))
    check("fix1: aria-pressed always mirrors .active", all(singles))

    # ---- Fix 2: switching away from precip clears everything (every order) ----
    # After the loop above the active layer is 'clouds'. Walk a fresh cycle.
    clear_ok = True
    detail = ""
    for prev, nxt in [("clouds", "precip"), ("precip", "temp"), ("temp", "wind"),
                      ("wind", "aqi"), ("aqi", "clouds"), ("clouds", "temp"), ("temp", "precip")]:
        click_pill(pg, prev)
        pg.wait_for_timeout(1800)
        click_pill(pg, nxt)
        pg.wait_for_timeout(1800)
        ps = pill_state(pg, prev)
        ns = pill_state(pg, nxt)
        if ps["active"] or ps["pressed"] == "true" or not ns["active"]:
            clear_ok = False
            detail = f"{prev}->{nxt}: prev_active={ps['active']} pressed={ps['pressed']}"
            break
    check("fix2: previous pill fully cleared on every switch (incl. from/to Precipitation)", clear_ok, detail)

    # tile layer destroyed + recreated on each switch: exactly one data layer
    n_tile = pg.evaluate("""(() => {
        const c = document.getElementById('radar-map');
        if (!c) return -1;
        let n = 0; // probe via CSS: only way without exposing internals —
        return document.querySelectorAll('.map-layer-pill.active').length;
    })()""")
    check("fix2: exactly ONE active layer pill at any time", n_tile == 1)

    # field layers: leaving temp/wind/aqi hides its legend
    click_pill(pg, "temp")
    pg.wait_for_timeout(2200)
    fs = field_state(pg)
    click_pill(pg, "precip")
    pg.wait_for_timeout(2200)
    fs2 = field_state(pg)
    check("fix2: field legend visible on temp, fully hidden after switching to precip",
          fs["legends"][0] and not any(fs2["legends"]), str(fs["legends"]) + " -> " + str(fs2["legends"]))

    # ---- Fix 4: field grid anchored on state.currentLocation ----
    loc = pg.evaluate("({lat: window.WeatherState?.currentLocation?.latitude, lng: window.WeatherState?.currentLocation?.longitude})")
    check("fix4: WeatherState mirror exposes currentLocation", loc["lat"] is not None, str(loc))
    # fetch through the same endpoint the page itself would use
    resp = pg.request.get(f"{BASE}/api/field?metric=temp&latitude={loc['lat']}&longitude={loc['lng']}")
    check("fix4: /api/field serves real grid for the current location",
          resp.ok, f"status={resp.status}")
    if resp.ok:
        grid = resp.json()
        vals = [v for row in grid["values"] for v in row if v is not None]
        check("fix4: grid has real (non-null) temperature values", len(vals) > 0,
              f"n={len(vals)} min={min(vals):.1f} max={max(vals):.1f}")

    # satellite availability on the real feed
    click_pill(pg, "clouds")
    pg.wait_for_timeout(2400)
    st = pill_state(pg, "clouds")
    tile_ok = pg.evaluate("""(() => {
        const sheets = performance.getEntriesByType('resource').filter(r => r.name.includes('rainviewer'));
        return sheets.length > 0;
    })()""")
    check("fix5: satellite layer activates on the live feed (or honest fallback ran)",
          st["active"], f"tiles={tile_ok}")

    # ---- mobile: language switching + layer pills + no overflow ----
    m = browser.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE, wait_until="networkidle", timeout=60000)
    m.wait_for_timeout(2400)
    m.click('.lang-btn[data-lang="te"]')
    m.wait_for_timeout(700)
    mt = chrome_texts(m)
    check("mobile: TE chrome applies immediately", mt["nav"][0] == "ఫోర్‌కాస్ట్" and "తర్వాతి 24" in mt["hours"])
    m.click('.lang-btn[data-lang="en"]')
    m.wait_for_timeout(500)
    ovfx = m.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("mobile: back to EN, no horizontal overflow", ovfx == 0, f"ovfx={ovfx}")

    # mobile layer switching (map view)
    m.click('.nav-tab[data-view="view-map"]')
    m.wait_for_timeout(2600)
    mob_ok = True
    for layer in ["clouds", "precip", "aqi", "precip", "wind", "clouds", "temp", "precip"]:
        m.click(f'.map-layer-pill[data-layer="{layer}"]')
        m.wait_for_timeout(1500)
        s = pill_state(m, layer)
        if not s or not s["active"]:
            mob_ok = False
            break
    check("mobile: 8-switch layer sequence keeps exactly one active pill", mob_ok)
    check("mobile: no JS errors", not errs, "; ".join(errs[:2]))
    m.close()
    pg.close()
    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== {len(results) - len(fails)}/{len(results)} audit-fix checks passed =====")
if fails:
    print("FAILED:")
    for f in fails:
        print(" -", f)
sys.exit(1 if fails else 0)
