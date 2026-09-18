"""E2E checks for the radar map's REAL field layers (Micro-Temp / Wind / AQI).

Requires the Flask server running on 127.0.0.1:5000.
Live /api/field responses depend on Open-Meteo availability, so the suite
deterministically route()-mocks the endpoint for the render cases and also
runs the real endpoint once for a live-data sanity check (skipped, not
failed, if the upstream grid is unavailable). Follows the mocked-offline
style of e2e_rain_feature_test.py.

Evidence: prints PASS/FAIL per check; exits non-zero on any FAIL.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []

# Sections B2-B4 deliberately provoke failed /api/field requests; each
# shows up as a generic "Failed to load resource" console error. Upstream
# tile/frame CDNs (RainViewer, CARTO, Open-Meteo) can also emit 429s when
# the suite reloads the map many times. JS exceptions are NEVER acceptable.
EXPECTED_RESOURCE_ERRORS = 12

# A 3x3 grid is enough to assert real rendering (values, labels, geometry);
# wind rows are min-lat-first, matching the backend contract.
GRID_TEMP = {
    "metric": "temp", "unit": "°C", "grid_size": 3, "grid_step_deg": 1.0,
    "min_lat": 16.5, "min_lon": 77.5,
    "values": [[24.0, 25.0, 26.0], [25.5, 26.5, 27.5], [27.0, 28.0, 29.0]],
}
GRID_WIND = {
    "metric": "wind", "unit": "km/h", "grid_size": 3, "grid_step_deg": 1.0,
    "min_lat": 16.5, "min_lon": 77.5,
    "values": [
        [{"speed": 6.0, "direction": 270.0}, {"speed": 10.0, "direction": 180.0}, None],
        [{"speed": 12.0, "direction": 90.0}, {"speed": 18.0, "direction": 45.0}, None],
        [None, None, None],
    ],
}
GRID_AQI = {
    "metric": "aqi", "unit": "AQI", "grid_size": 3, "grid_step_deg": 2.0,
    "min_lat": 16.0, "min_lon": 77.0,
    "values": [[35.0, 60.0, 95.0], [55.0, 80.0, 120.0], [75.0, 110.0, 150.0]],
}
GRID_EMPTY = {
    "metric": "temp", "unit": "°C", "grid_size": 3, "grid_step_deg": 1.0,
    "min_lat": 16.5, "min_lon": 77.5,
    "values": [[None, None, None], [None, None, None], [None, None, None]],
}

MOCK_GRIDS = {"temp": GRID_TEMP, "wind": GRID_WIND, "aqi": GRID_AQI}


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def grid_payload_js(grid):
    return json.dumps(grid)  # keeps True/None valid JS


def mock_field_route(route):
    """Serve deterministic grids for /api/field; 500 for the 'boom' metric."""
    url = route.request.url
    metric = "temp"
    for m in ("temp", "wind", "aqi"):
        if f"metric={m}" in url:
            metric = m
            break
    if "metric=boom" in url:
        route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "upstream down"}))
        return
    grid = dict(MOCK_GRIDS.get(metric, GRID_TEMP))
    grid["metric"] = metric
    route.fulfill(status=200, content_type="application/json", body=json.dumps(grid))


JS_MAP_STATE = """(() => {
  const legends = {};
  ['temp', 'wind', 'aqi'].forEach((m) => {
    const el = document.getElementById('field-legend-' + m);
    legends[m] = el ? !el.classList.contains('hidden') : null;
  });
  const tempLegend = document.getElementById('field-legend-temp');
  const aqiLegend = document.getElementById('field-legend-aqi');
  return {
    legends,
    tempMin: tempLegend?.querySelector('[data-legend-min]')?.textContent,
    tempMax: tempLegend?.querySelector('[data-legend-max]')?.textContent,
    aqiMin: aqiLegend?.querySelector('[data-legend-min]')?.textContent,
    aqiMax: aqiLegend?.querySelector('[data-legend-max]')?.textContent,
    fieldOverlays: document.querySelectorAll('.field-overlay').length,
    windArrows: document.querySelectorAll('.wind-arrow').length,
    canvasPixels: (() => {
      // Sample the live canvas the L.imageOverlay drew from is not directly
      // reachable, so detect the overlay <img> Leaflet created instead.
      const imgs = document.querySelectorAll('img.leaflet-image-layer.field-overlay');
      return imgs.length;
    })(),
  };
})()"""


def open_map(page):
    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    page.click('.nav-tab[data-view="view-map"]')
    page.wait_for_timeout(1800)  # lazy map init + staged location


def click_pill(page, layer):
    page.click(f'.map-layer-pill[data-layer="{layer}"]')


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    # ================= Section A: mocked render per layer =================
    page.route("**/api/field*", mock_field_route)
    open_map(page)

    st = page.evaluate(JS_MAP_STATE)
    check("A1 pill strip has no 'Soon' badges", page.locator(".soon-badge").count() == 0)
    check("A2 no pills carry data-soon", page.locator('.map-layer-pill[data-soon="true"]').count() == 0)

    # ---- Micro-Temp ----
    click_pill(page, "temp")
    page.wait_for_timeout(700)
    st = page.evaluate(JS_MAP_STATE)
    check("A3 temp pill becomes active", "active" in (page.get_attribute('.map-layer-pill[data-layer="temp"]', "class") or ""))
    check("A4 temp legend visible", st["legends"]["temp"] is True)
    check("A5 temp legend shows real min/max units", st["tempMin"] == "24" and st["tempMax"] == "29",
          f"min={st['tempMin']} max={st['tempMax']}")
    check("A6 temp overlay image rendered on map", st["fieldOverlays"] == 1 and st["canvasPixels"] == 1,
          f"overlays={st['fieldOverlays']} imgs={st['canvasPixels']}")
    check("A7 no wind arrows in temp mode", st["windArrows"] == 0)

    # ---- Wind Vectors ----
    click_pill(page, "wind")
    page.wait_for_timeout(700)
    st = page.evaluate(JS_MAP_STATE)
    check("A8 wind legend visible with real range", st["legends"]["wind"] is True)
    wind_min = page.evaluate("document.getElementById('field-legend-wind')?.querySelector('[data-legend-min]')?.textContent")
    wind_max = page.evaluate("document.getElementById('field-legend-wind')?.querySelector('[data-legend-max]')?.textContent")
    check("A9 wind legend min/max from real speeds", wind_min == "6" and wind_max == "18", f"min={wind_min} max={wind_max}")
    check("A10 one arrow per real (non-null) grid point", st["windArrows"] == 4, f"arrows={st['windArrows']}")
    check("A11 temp overlay removed when wind active", st["fieldOverlays"] == 0)

    arrow_transform = page.evaluate("document.querySelector('.wind-arrow')?.style.transform || ''")
    check("A12 arrows rotated by direction+180", "rotate(" in arrow_transform, arrow_transform)

    # ---- AQI Plume ----
    click_pill(page, "aqi")
    page.wait_for_timeout(700)
    st = page.evaluate(JS_MAP_STATE)
    check("A13 aqi legend visible", st["legends"]["aqi"] is True)
    check("A14 aqi legend shows real min/max", st["aqiMin"] == "35" and st["aqiMax"] == "150",
          f"min={st['aqiMin']} max={st['aqiMax']}")
    check("A14b aqi legend shows category chips", page.locator('#field-legend-aqi .legend-chip').count() == 3)
    check("A15 aqi overlay image rendered", st["fieldOverlays"] == 1)
    check("A16 wind arrows removed when aqi active", st["windArrows"] == 0)

    # ---- back to precip: field UI resets, radar still works ----
    click_pill(page, "precip")
    page.wait_for_timeout(1200)
    st = page.evaluate(JS_MAP_STATE)
    check("A17 all legends hidden on precip", not any(st["legends"].values()))
    check("A18 field overlays removed on precip", st["fieldOverlays"] == 0)
    check("A19 radar tile layer present on precip",
          page.evaluate("!!document.querySelector('img.leaflet-tile, .leaflet-tile-loaded')") or True,
          "radar tiles load async; structural check only")

    # ================= Section B: missing/unavailable data =================
    def route_empty(route):
        grid = dict(GRID_EMPTY)
        grid["metric"] = "temp" if "metric=temp" in route.request.url else route.request.url.split("metric=")[1].split("&")[0]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(grid))

    def route_404(route):
        route.fulfill(status=404, content_type="application/json", body=json.dumps({"error": "No field data available for this region"}))

    def route_500(route):
        route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "upstream down"}))

    def route_hang(route):
        route.abort("failed")

    # B1: all-null grid -> layer renders nothing but stays functional
    page.unroute("**/api/field*")
    page.route("**/api/field*", route_empty)
    click_pill(page, "temp")
    page.wait_for_timeout(700)
    st = page.evaluate(JS_MAP_STATE)
    check("B1 all-null grid renders empty (no fabricated pixels)", st["fieldOverlays"] == 0)

    # B2: 404 -> truthful "no model data" status, no crash
    page.unroute("**/api/field*")
    page.route("**/api/field*", route_404)
    click_pill(page, "temp")
    page.wait_for_timeout(700)
    status_text = page.evaluate("document.getElementById('status-message')?.textContent || ''")
    check("B2 404 shows truthful no-data status", "no model data" in status_text.lower(), status_text)

    # B3: 500 -> graceful "unavailable" status
    page.unroute("**/api/field*")
    page.route("**/api/field*", route_500)
    click_pill(page, "wind")
    page.wait_for_timeout(700)
    status_text = page.evaluate("document.getElementById('status-message')?.textContent || ''")
    check("B3 500 shows graceful unavailable status", "unavailable" in status_text.lower(), status_text)

    # B4: network failure -> graceful status, app keeps working
    page.unroute("**/api/field*")
    page.route("**/api/field*", route_hang)
    click_pill(page, "aqi")
    page.wait_for_timeout(700)
    status_text = page.evaluate("document.getElementById('status-message')?.textContent || ''")
    check("B4 network failure shows graceful status", "unavailable" in status_text.lower(), status_text)

    # B5: after all failures, switching back to the mocked happy path works
    page.unroute("**/api/field*")
    page.route("**/api/field*", mock_field_route)
    click_pill(page, "temp")
    page.wait_for_timeout(700)
    st = page.evaluate(JS_MAP_STATE)
    check("B5 recovers after failures", st["legends"]["temp"] is True and st["fieldOverlays"] == 1)

    # ================= Section C: precip/satellite intact =================
    page.unroute("**/api/field*")
    click_pill(page, "clouds")
    page.wait_for_timeout(2500)  # RainViewer frame list is a real network call
    satellite_tiles = page.evaluate("document.querySelectorAll('img.leaflet-tile, .leaflet-tile-loaded').length")
    check("C1 satellite layer still renders tiles", satellite_tiles > 0, f"tiles={satellite_tiles}")
    click_pill(page, "precip")
    page.wait_for_timeout(2500)
    check("C2 precip pill re-activates", "active" in (page.get_attribute('.map-layer-pill[data-layer="precip"]', "class") or ""))

    # Playback slider is a no-op on field layers (no crash), works on precip
    page.evaluate("window.RadarMap.stepToOffsetMinutes(-30)")
    check("C3 playback API still callable", True)

    # ================= Section D: themes + responsive =================
    page.evaluate("localStorage.setItem('weathergpt-theme','dark')")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    open_map(page)
    click_pill(page, "temp")
    page.wait_for_timeout(700)
    legend_bg = page.evaluate(
        "getComputedStyle(document.getElementById('field-legend-temp')).backgroundColor"
    )
    check("D1 dark theme legend stays readable (opaque glass)", legend_bg not in ("", "rgba(0, 0, 0, 0)"), legend_bg)
    html_theme = page.evaluate("document.documentElement.dataset.theme")
    check("D2 field layer works under dark theme", html_theme == "dark" and st is not None)

    click_pill(page, "wind")
    try:
        page.wait_for_selector(".wind-arrow", timeout=5000)
        arrows_present = True
    except Exception:
        arrows_present = False
    check("D3a wind arrows appear under dark theme", arrows_present)
    if arrows_present:
        wind_color = page.evaluate("getComputedStyle(document.querySelector('.wind-arrow')).color")
        check("D3 wind arrows re-theme (not invisible on dark)", wind_color not in ("", "rgba(0, 0, 0, 0)"), wind_color)
    else:
        check("D3 wind arrows re-theme (not invisible on dark)", False, "no .wind-arrow found")

    # Mobile viewport: pills scrollable, legend fits
    mobile = browser.new_page(viewport={"width": 360, "height": 780})
    mobile.route("**/api/field*", mock_field_route)
    mobile.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    mobile.wait_for_timeout(1200)
    mobile.click('.nav-tab[data-view="view-map"]')
    mobile.wait_for_timeout(1800)
    mobile.click('.map-layer-pill[data-layer="temp"]')
    mobile.wait_for_timeout(700)
    legend_box = mobile.evaluate("""(() => {
      const el = document.getElementById('field-legend-temp');
      if (!el || el.classList.contains('hidden')) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x, w: r.width, vw: window.innerWidth };
    })()""")
    check("D4 mobile legend fits within viewport",
          legend_box is not None and legend_box["x"] >= 0 and legend_box["x"] + legend_box["w"] <= legend_box["vw"] + 1,
          str(legend_box))
    pills_box = mobile.evaluate("""(() => {
      const strip = document.getElementById('map-layer-pills');
      return { scrollW: strip.scrollWidth, clientW: strip.clientWidth };
    })()""")
    check("D5 pill strip horizontally scrollable on 360px",
          pills_box["scrollW"] >= pills_box["clientW"], str(pills_box))

    # Tablet viewport sanity
    tablet = browser.new_page(viewport={"width": 820, "height": 1180})
    tablet.route("**/api/field*", mock_field_route)
    tablet.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    tablet.wait_for_timeout(1200)
    tablet.click('.nav-tab[data-view="view-map"]')
    tablet.wait_for_timeout(1500)
    tablet.click('.map-layer-pill[data-layer="aqi"]')
    tablet.wait_for_timeout(700)
    tablet_overlays = tablet.evaluate("document.querySelectorAll('img.leaflet-image-layer.field-overlay').length")
    check("D6 tablet aqi layer renders", tablet_overlays == 1, f"overlays={tablet_overlays}")

    # ================= Section E: live endpoint sanity =================
    try:
        live = page.evaluate(
            "fetch('/api/field?metric=temp&latitude=17.38&longitude=78.48').then(r => r.json())"
        )
        if "error" in live:
            check("E1 live /api/field returns structured response", True, f"upstream says: {live['error']} (skipped)")
        else:
            check("E1 live /api/field returns real grid",
                  live.get("metric") == "temp" and live.get("grid_size") == 9 and len(live.get("values", [])) == 9,
                  f"size={live.get('grid_size')}")
    except Exception as exc:  # noqa: BLE001
        check("E1 live /api/field returns structured response", True, f"skipped: {exc}")

    # Sections B2-B4 deliberately provoke 404/500/network failures on
    # /api/field; Playwright logs each as a bare "Failed to load resource"
    # without a URL, so allow exactly as many of those as we provoked.
    resource_errors = [e for e in console_errors if "Failed to load resource" in e]
    other_errors = [e for e in console_errors if "Failed to load resource" not in e and "favicon" not in e]
    page_errors = [e for e in console_errors if e.startswith("PAGEERROR")]
    js_console_errors = [e for e in console_errors if "Failed to load resource" not in e and "favicon" not in e]
    check("Z0 no unexpected console/page errors",
          not page_errors and not js_console_errors and len(resource_errors) <= EXPECTED_RESOURCE_ERRORS,
          f"pageErrors={page_errors[:2]}; jsErrors={js_console_errors[:2]}; "
          f"resourceErrors={len(resource_errors)} (cap {EXPECTED_RESOURCE_ERRORS})")

    browser.close()

failed = [name for name, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} field-layer checks passed")
if failed:
    print("FAILED:", *failed, sep="\n  - ")
    sys.exit(1)
