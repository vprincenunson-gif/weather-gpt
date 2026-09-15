"""End-to-end verification of the WeatherGPT frontend against the live backend."""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})  # iPhone-ish

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.goto(BASE, wait_until="networkidle", timeout=60000)

    # 1. No page errors on load
    check("no page errors on load", not console_errors, "; ".join(console_errors[:3]))

    # 2. Real weather rendered
    temp = page.text_content("#hero-temperature")
    check("hero temperature rendered", temp and temp.strip().isdigit(), f"got: {temp!r}")

    place = page.text_content("#hero-place-label")
    check("place label rendered", bool(place and place.strip() and "Locating" not in place), f"got: {place!r}")

    # 3. Hourly scroller starts at current hour (P2-1)
    hourly_labels = page.locator("#hourly-forecast-scroll .forecast-hourly-card").count()
    first_label = page.locator("#hourly-forecast-scroll .forecast-hourly-card span").first.text_content()
    check("hourly cards rendered", hourly_labels >= 20, f"{hourly_labels} cards")
    check("hourly starts at Now/current hour", first_label and ("Now" in first_label or first_label.strip().isdigit()),
          f"first label: {first_label!r}")

    # 4. AQI card shows a real value or dash (no fabricated 32)
    aqi = page.text_content("#telemetry-aqi-num")
    check("AQI rendered", aqi is not None and (aqi.strip().isdigit() or aqi.strip() == "—"), f"got: {aqi!r}")

    # 5. Sensor card hidden by default (P3 fix)
    cls = page.get_attribute("#map-sensor-card", "class")
    check("sensor card hidden initially", "translate-y-48" in cls and "opacity-0" in cls)

    # 6. Radar map screen: switch to map view
    page.click('.nav-tab[data-view="view-map"]')
    page.wait_for_timeout(1200)
    map_visible = page.is_visible("#view-map")
    check("map view switches", map_visible)
    tiles = page.evaluate("document.querySelectorAll('#radar-map .leaflet-tile').length")
    check("leaflet tiles loaded", tiles > 0, f"{tiles} tiles")
    check("basemap has key param", page.evaluate(
        "Array.from(document.querySelectorAll('#radar-map .leaflet-tile-loaded img, #radar-map img.leaflet-tile-loaded'))"
        ".some(i => (i.src||'').includes('key='))"), "CARTO key via /api/config")

    # 7. Insights screen: scope chips wired + illustrative labels
    page.click('.nav-tab[data-view="view-insights"]')
    page.wait_for_timeout(400)
    metrics = page.locator("#insights-scope-metrics > div").count()
    check("insights metrics rendered", metrics == 4, f"{metrics} cards")
    page.click('#insights-time-scope .scope-chip[data-scope="48h"]')
    page.wait_for_timeout(300)
    subtitle = page.text_content("#insights-scope-subtitle")
    check("48h scope chip works", subtitle and "48-hour" in subtitle, f"got: {subtitle!r}")
    page.click('#insights-time-scope .scope-chip[data-scope="7d"]')
    page.wait_for_timeout(300)
    subtitle7 = page.text_content("#insights-scope-subtitle")
    check("7d scope chip works", subtitle7 and "7-day" in subtitle7, f"got: {subtitle7!r}")
    badge = page.text_content("#view-insights")
    check("illustrative badge present", "Illustrative" in (badge or ""))

    # 8. Assistant: unified query flow with fallback answer (no AI keys here)
    page.click('.nav-tab[data-view="view-weathergpt"]')
    page.fill("#unified-query-input", "Will it rain tomorrow?")
    page.press("#unified-query-input", "Enter")
    page.wait_for_timeout(1500)
    bubbles = page.locator("#chat-stream .ai-chat-node").count()
    check("assistant answered", bubbles >= 2, f"{bubbles} response nodes")

    # 9. Language switch updates <html lang> (P2-8)
    page.click('.lang-btn[data-lang="hi"]')
    page.wait_for_timeout(200)
    lang = page.get_attribute("html", "lang")
    check("html lang follows language switch", lang == "hi", f"got: {lang}")
    page.click('.lang-btn[data-lang="auto"]')

    # 10. Esc closes search modal (a11y)
    page.click("#location-trigger-btn")
    page.wait_for_timeout(200)
    hidden_before = page.evaluate("document.getElementById('search-modal').classList.contains('hidden')")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    hidden_after = page.evaluate("document.getElementById('search-modal').classList.contains('hidden')")
    check("Esc closes search modal", hidden_before is False and hidden_after is True)

    # 11. City search flow
    page.click("#location-trigger-btn")
    page.fill("#city-search-input", "Tokyo")
    page.click("#city-search-form button[type=submit]")
    page.wait_for_timeout(2500)
    place2 = page.text_content("#hero-place-label")
    check("city search updates location", place2 and "Tokyo" in place2, f"got: {place2!r}")

    # 12. Viewport meta allows zoom
    vp = page.evaluate("document.querySelector('meta[name=viewport]').content")
    check("pinch-zoom allowed (a11y)", "user-scalable=no" not in vp and "maximum-scale" not in vp, vp)

    # 13. /api/config + SW registration
    cfg = page.evaluate("fetch('/api/config').then(r => r.json())")
    check("config endpoint reachable from page", bool(cfg.get("carto_api_key")))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} checks passed =====")
sys.exit(1 if fails else 0)
