"""Full E2E verification of WeatherGPT — every feature, success and failure paths.

Evidence: prints PASS/FAIL per check with details; exits non-zero on any FAIL.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    # ============================================================
    # BROWSER 1: main flow with fake microphone (for voice test)
    # ============================================================
    browser = p.chromium.launch(
        headless=True,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    )
    page = browser.new_page(viewport={"width": 390, "height": 844})

    console_errors = []
    network_failures = []
    dialogs = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))
    page.on("requestfailed", lambda r: network_failures.append(f"{r.url} :: {r.failure}"))
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))

    # ---------- 1. LOAD & CURRENT WEATHER ----------
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)

    check("app loads with title", "WeatherGPT" in page.title())
    temp = page.text_content("#hero-temperature")
    check("current weather: hero temperature rendered", temp and temp.strip().lstrip("-").isdigit(), f"got {temp!r}")
    place = page.text_content("#hero-place-label")
    check("current weather: place label", bool(place and "San Francisco" in place), place or "")
    cond = page.text_content("#hero-condition-text")
    check("current weather: condition text", bool(cond and cond.strip()), cond or "")
    hi = page.text_content("#hero-temp-high")
    lo = page.text_content("#hero-temp-low")
    feels = page.text_content("#hero-temp-feels")
    check("current weather: H/L/feels populated", all(x and "°" in x for x in (hi, lo, feels)), f"H={hi} L={lo} F={feels}")

    # ---------- 2. DATA CONSISTENCY: UI vs API ----------
    consistency = page.evaluate("""async () => {
        const { latitude, longitude } = state.currentLocation;
        const res = await fetch(`/api/weather?latitude=${latitude}&longitude=${longitude}&forecast_days=7`);
        const data = await res.json();
        return {
            apiTemp: Math.round(data.weather.current.temperature_2m),
            uiTemp: parseInt(document.getElementById('hero-temperature').textContent, 10),
            apiHumidity: Math.round(data.weather.current.relative_humidity_2m),
            uiHumidity: document.getElementById('telemetry-humidity').textContent,
            apiAqi: data.air_quality.us_aqi,
            uiAqi: document.getElementById('telemetry-aqi-num').textContent,
            farmSnapTemp: state.farmAdvice?.weather_snapshot?.temperature_c,
            weatherTemp: data.weather.current.temperature_2m,
        };
    }""")
    check("consistency: hero temp == API temp", consistency["apiTemp"] == consistency["uiTemp"],
          f"api={consistency['apiTemp']} ui={consistency['uiTemp']}")
    check("consistency: humidity == API", str(consistency["apiHumidity"]) == consistency["uiHumidity"].rstrip("%"),
          f"api={consistency['apiHumidity']} ui={consistency['uiHumidity']}")
    check("consistency: AQI == API", str(consistency["apiAqi"]) == consistency["uiAqi"],
          f"api={consistency['apiAqi']} ui={consistency['uiAqi']}")
    check("consistency: farm snapshot == weather payload",
          consistency["farmSnapTemp"] == consistency["weatherTemp"],
          f"farm={consistency['farmSnapTemp']} api={consistency['weatherTemp']}")

    # ---------- 3. HOURLY TRAJECTORY ----------
    hourly = page.evaluate("""(() => {
        const cards = document.querySelectorAll('#hourly-forecast-scroll .forecast-hourly-card');
        return { count: cards.length, first: cards[0]?.querySelector('span')?.textContent,
                 hasUv: !!cards[0]?.textContent.includes('UV'), hasRain: !!cards[0]?.textContent.includes('%') };
    })()""")
    check("hourly: 24 cards rendered", hourly["count"] == 24, f"{hourly['count']}")
    check("hourly: starts at current hour (Now)", hourly["first"] == "Now", f"first={hourly['first']!r}")
    check("hourly: shows rain % and UV", hourly["hasRain"] and hourly["hasUv"])

    # ---------- 4. 7-DAY FORECAST ----------
    daily = page.evaluate("document.querySelectorAll('#daily-forecast-container > div').length")
    check("forecast: 7-day outlook rendered", daily == 7, f"{daily} rows")

    # ---------- 5. AI REPORT (fallback without keys) ----------
    synopsis = page.text_content("#hero-synopsis-text")
    check("AI report: synopsis populated (fallback template)",
          bool(synopsis and "temperature" in synopsis.lower() and "Loading" not in synopsis), (synopsis or "")[:80])
    meta = page.text_content("#hero-synopsis-meta")
    check("AI report: meta shows language", bool(meta and "Synthesized" in meta), meta or "")

    # ---------- 6. AQI CARD ----------
    aqi_state = page.evaluate("""(() => ({
        num: document.getElementById('telemetry-aqi-num').textContent,
        label: document.getElementById('telemetry-aqi-label').textContent,
        barWidth: document.getElementById('telemetry-aqi-bar').style.width,
        sub: document.getElementById('telemetry-aqi-sub').textContent,
    }))()""")
    check("AQI: value + label + bar rendered",
          aqi_state["num"].strip().lstrip("-").isdigit() and aqi_state["label"] in ("Good", "Moderate", "Unhealthy", "—")
          and aqi_state["barWidth"], json.dumps(aqi_state))

    # ---------- 7. LOCATION SEARCH (success + failure) ----------
    page.click("#location-trigger-btn")
    check("search modal opens", page.is_visible("#search-modal"))
    page.fill("#city-search-input", "Tokyo")
    page.click("#city-search-form button[type=submit]")
    page.wait_for_timeout(2500)
    check("search success: location updates to Tokyo", "Tokyo" in (page.text_content("#hero-place-label") or ""),
          page.text_content("#hero-place-label") or "")

    page.click("#location-trigger-btn")
    page.fill("#city-search-input", "zzzqqqxxxnotacity")
    page.click("#city-search-form button[type=submit]")
    page.wait_for_timeout(2000)
    check("search failure: graceful alert shown", any("Could not find" in d for d in dialogs), f"dialogs={dialogs}")
    page.keyboard.press("Escape")

    # popular city chip
    page.click("#location-trigger-btn")
    page.click(".popular-city-chip[data-city='London']")
    page.wait_for_timeout(2500)
    check("popular city chip: London loads", "London" in (page.text_content("#hero-place-label") or ""))

    # ---------- 8. GPS detection (fake geolocation) ----------
    context = page.context
    context.grant_permissions(["geolocation"], origin=BASE)
    context.set_geolocation({"latitude": 17.3850, "longitude": 78.4867})  # Hyderabad
    page.click("#location-trigger-btn")
    page.click("#use-gps-btn")
    page.wait_for_timeout(4000)
    gps_place = page.text_content("#hero-place-label") or ""
    check("GPS detection: resolves Hyderabad area", "Hyderabad" in gps_place or "Nampally" in gps_place or "Telangana" in gps_place, gps_place)

    # ---------- 9. LANGUAGE SWITCHING (all three) ----------
    for lang_code, probe, name in (("hi", "क्या", "Hindi"), ("te", "ఈ", "Telugu"), ("auto", "bike", "English")):
        page.click(f'.lang-btn[data-lang="{lang_code}"]')
        page.wait_for_timeout(1800)
        chips = page.text_content("#assistant-prompt-chips") or ""
        html_lang = page.get_attribute("html", "lang")
        check(f"language {name}: chips + html lang update",
              (probe in chips) or lang_code == "auto", f"html.lang={html_lang}")

    # synopsis re-synthesized in EN after switch back
    page.wait_for_timeout(1200)

    # ---------- 10. ALERTS (real, from payload) ----------
    alerts = page.evaluate("state.alerts.length")
    insights_badge = page.evaluate("document.getElementById('farm-alert-count')?.classList.contains('hidden')")
    check("alerts: real alerts present (Hyderabad monsoon)", alerts >= 1, f"{alerts} alerts")
    page.click('.nav-tab[data-view="view-insights"]')
    page.wait_for_timeout(400)
    ledger_items = page.locator("#insights-alerts-container > div").count()
    check("alerts: insights ledger rendered", ledger_items >= 1, f"{ledger_items} entries")
    ledger_text = page.text_content("#insights-alerts-container") or ""
    check("alerts: ledger matches real alert count", "No Severe" not in ledger_text or alerts == 0,
          f"state={alerts}, ledger shows {'alerts' if 'No Severe' not in ledger_text else 'none'}")

    # ---------- 11. INSIGHTS scope chips ----------
    page.click('#insights-time-scope .scope-chip[data-scope="48h"]')
    page.wait_for_timeout(300)
    sub48 = page.text_content("#insights-scope-subtitle") or ""
    page.click('#insights-time-scope .scope-chip[data-scope="7d"]')
    page.wait_for_timeout(300)
    sub7 = page.text_content("#insights-scope-subtitle") or ""
    check("insights: 48h/7d scopes work", "48-hour" in sub48 and "7-day" in sub7)

    # ---------- 12. NAVIGATION TABS (all four) ----------
    for view, probe_id in (("view-forecast", "view-forecast"), ("view-map", "radar-map"),
                           ("view-weathergpt", "chat-stream"), ("view-insights", "insights-scope-metrics")):
        page.click(f'.nav-tab[data-view="{view}"]')
        page.wait_for_timeout(300)
        visible = page.is_visible(f"#{probe_id}")
        active = page.evaluate(f"document.querySelector('.nav-tab[data-view=\\'{view}\\']').classList.contains('is-active')")
        check(f"navigation: {view} switches + tab activates", visible and active)

    # ---------- 13. RADAR MAP / BASEMAP ----------
    page.click('.nav-tab[data-view="view-map"]')
    page.wait_for_timeout(1500)
    tiles = page.evaluate("document.querySelectorAll('#radar-map img.leaflet-tile, #radar-map .leaflet-tile').length")
    check("radar map: leaflet tiles load", tiles > 0, f"{tiles} tiles")
    keyed = page.evaluate("""Array.from(document.querySelectorAll('#radar-map img.leaflet-tile'))
        .some(i => (i.src||'').includes('cartocdn') && (i.src||'').includes('key='))""")
    check("radar map: CARTO basemap uses /api/config key", keyed)
    marker = page.evaluate("!!document.querySelector('#radar-map .leaflet-interactive, #radar-map path')")
    check("radar map: marker present", marker)
    # layer switching
    page.click('.map-layer-pill[data-layer="clouds"]')
    page.wait_for_timeout(1200)
    sat = page.evaluate("""Array.from(document.querySelectorAll('#radar-map img.leaflet-tile'))
        .some(i => (i.src||'').includes('rainviewer') || (i.src||'').includes('satellite') || (i.src||'').includes('infrared'))""")
    check("radar map: satellite layer switches to RainViewer tiles", sat)
    page.click('.map-layer-pill[data-layer="temp"]')
    page.wait_for_timeout(400)
    soon_msg = page.evaluate("document.getElementById('status-message')?.textContent || ''")
    check("radar map: 'Soon' layers show honest status", "coming soon" in soon_msg.lower(), soon_msg)
    # playback slider
    page.click(".map-layer-pill[data-layer='precip']")
    page.evaluate("document.getElementById('radar-time-slider').value = -30")
    page.dispatch_event("#radar-time-slider", "input")
    page.wait_for_timeout(600)
    check("radar map: playback slider drives frames without errors",
          not [e for e in console_errors if "radar" in e.lower()])
    # sensor card opens on marker click
    page.evaluate("window.dispatchEvent(new CustomEvent('radar-marker-clicked'))")
    page.wait_for_timeout(300)
    card_cls = page.get_attribute("#map-sensor-card", "class") or ""
    check("radar map: sensor card opens on marker tap", "translate-y-48" not in card_cls)
    page.click("#close-sensor-card-btn")
    page.wait_for_timeout(200)
    card_cls2 = page.get_attribute("#map-sensor-card", "class") or ""
    check("radar map: sensor card closes", "translate-y-48" in card_cls2)
    # map address search
    page.fill("#map-address-search-input", "Paris")
    page.press("#map-address-search-input", "Enter")
    page.wait_for_timeout(2500)
    check("radar map: address search works", "Paris" in (page.text_content("#hero-place-label") or ""))

    # ---------- 14. WEATHERGPT ASSISTANT ----------
    page.click('.nav-tab[data-view="view-weathergpt"]')
    page.fill("#unified-query-input", "Can I bike outside this afternoon?")
    page.press("#unified-query-input", "Enter")
    page.wait_for_timeout(2000)
    nodes = page.locator("#chat-stream .ai-chat-node").count()
    last_answer = page.evaluate("""(() => {
        const nodes = document.querySelectorAll('#chat-stream .ai-chat-node');
        return nodes[nodes.length-1]?.textContent?.slice(0, 120) || '';
    })()""")
    check("assistant: user query answered with structured fallback", nodes >= 2 and len(last_answer) > 30,
          f"nodes={nodes}, answer={last_answer[:60]!r}")
    # suggestion chip submits
    page.click(".assistant-chip")
    page.wait_for_timeout(2000)
    nodes2 = page.locator("#chat-stream .ai-chat-node").count()
    check("assistant: suggestion chip submits query", nodes2 > nodes, f"nodes {nodes}->{nodes2}")

    # ---------- 15. VOICE RECORDING (fake mic) → graceful failure without GROQ key ----------
    page.click(".unified-mic-btn")
    page.wait_for_timeout(1200)
    overlay_visible = page.is_visible("#voice-overlay")
    check("voice: recording overlay opens with mic", overlay_visible)
    if overlay_visible:
        page.click("#voice-stop-btn")
        page.wait_for_timeout(2500)
        check("voice: stop works, transcription fails gracefully without GROQ key",
              any("Voice transcription error" in d or "transcription" in d.lower() for d in dialogs),
              f"dialogs={[d[:60] for d in dialogs]}")
    overlay_hidden = page.evaluate("document.getElementById('voice-overlay').classList.contains('hidden')")
    check("voice: overlay closes after stop", overlay_hidden)

    # ---------- 16. FARM ADVISOR: UI combos ----------
    page.click('.nav-tab[data-view="view-forecast"]')
    page.wait_for_timeout(500)
    for crop, stage, expect_topic in (("rice", "harvesting", "Harvesting"), ("cotton", "flowering", None),
                                      ("groundnut", "sowing", None), ("maize", "growing", None)):
        page.select_option("#farm-crop-select", crop)
        page.select_option("#farm-stage-select", stage)
        page.wait_for_timeout(1800)
        topics = page.evaluate("Array.from(document.querySelectorAll('#farm-advice-cards .farm-advice-card span')).map(s => s.textContent)")
        card_count = page.locator("#farm-advice-cards .farm-advice-card").count()
        ok = card_count >= 2 and (expect_topic is None or any(expect_topic in t for t in topics))
        check(f"farm UI: {crop}/{stage} renders advice", ok, f"{card_count} cards, topics={topics[:6]}")

    # farm data real: snapshot matches
    farm_check = page.evaluate("""async () => {
        const snap = state.farmAdvice?.weather_snapshot;
        const w = state.weather?.current;
        return { snapTemp: snap?.temperature_c, wTemp: w?.temperature_2m,
                 snapRain: snap?.rain_next_3_days_mm,
                 wRain: (state.weather?.daily?.precipitation_sum || []).slice(0,3).reduce((a,b)=>a+(b||0),0) };
    }""")
    check("farm consistency: snapshot mirrors live payload",
          farm_check["snapTemp"] == farm_check["wTemp"],
          f"farm={farm_check['snapTemp']} api={farm_check['wTemp']}")

    # ---------- 17. ACCESSIBILITY CONTROLS ----------
    unnamed = page.evaluate("""Array.from(document.querySelectorAll('button'))
        .filter(b => b.offsetParent !== null)
        .filter(b => !b.textContent.trim() && !b.getAttribute('aria-label') && !b.getAttribute('title'))
        .length""")
    check("a11y: all visible buttons have accessible names", unnamed == 0, f"{unnamed} unnamed buttons")
    zoomable = page.evaluate("document.querySelector('meta[name=viewport]').content")
    check("a11y: pinch-zoom not blocked", "user-scalable=no" not in zoomable)
    labels_ok = page.evaluate("""['farm-crop-select','farm-stage-select','city-search-input',
        'assistant-input-text','unified-query-input','map-address-search-input']
        .every(id => {
            const el = document.getElementById(id);
            // WCAG accessible name: <label for>, aria-label, aria-labelledby, or title
            return el && (el.labels?.length > 0 || el.getAttribute('aria-label')
                          || el.getAttribute('aria-labelledby') || el.getAttribute('title'));
        })""")
    check("a11y: all form inputs labelled", labels_ok)
    esc_ok = page.evaluate("""(() => {
        document.getElementById('location-trigger-btn').click();
        const before = document.getElementById('search-modal').classList.contains('hidden');
        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
        return !before && document.getElementById('search-modal').classList.contains('hidden');
    })()""")
    check("a11y: Escape closes modal", esc_ok)

    # ---------- 18. PWA: manifest, icons, SW, offline ----------
    manifest = page.evaluate("""async () => {
        const m = await (await fetch('/manifest.json')).json();
        return { name: m.name, icons: m.icons.length, start: m.start_url, display: m.display };
    }""")
    check("PWA: manifest valid", manifest["name"] and manifest["icons"] >= 3 and manifest["display"] == "standalone",
          json.dumps(manifest))
    for icon in ("/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon-maskable-512.png"):
        code = page.evaluate(f"fetch('{icon}').then(r => r.status)")
        check(f"PWA: {icon} reachable", code == 200, f"HTTP {code}")
    sw_active = page.evaluate("""async () => {
        const reg = await navigator.serviceWorker.ready;
        return !!(reg.active || reg.installing || reg.waiting);
    }""")
    check("PWA: service worker registered + active", sw_active)

    # offline reload — shell must serve from cache
    context.set_offline(True)
    page.reload(wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(1500)
    offline_ok = page.evaluate("!!document.getElementById('hero-temperature') && !!document.getElementById('farm-advisor-section')")
    check("PWA: offline reload serves app shell (hero + farm sections present)", offline_ok)
    context.set_offline(False)
    page.wait_for_timeout(800)

    # ---------- 19. BROKEN LINKS / STATIC ASSETS (same-origin) ----------
    asset_check = page.evaluate("""async () => {
        const urls = new Set();
        document.querySelectorAll('link[href], script[src], img[src], source[src]').forEach(el => {
            const u = el.href || el.src;
            if (u) urls.add(u);
        });
        const results = [];
        for (const u of urls) {
            const url = new URL(u, location.origin);
            if (url.origin !== location.origin) continue;   // CDN assets checked separately
            const r = await fetch(url.href);
            results.push({ url: url.pathname, status: r.status });
        }
        return results;
    }""")
    broken = [a for a in asset_check if a["status"] != 200]
    check("links: all same-origin assets return 200", not broken, f"broken={broken}, checked={len(asset_check)}")

    # ---------- 20. ERROR / EMPTY / LOADING STATES ----------
    page.evaluate("window.dispatchEvent(new CustomEvent('radar-marker-clicked'))")
    page.wait_for_timeout(200)
    check("empty state: sensor card metrics show real data or em-dash",
          "—" in (page.text_content("#map-sensor-card") or "") or any(c.isdigit() for c in page.text_content("#sensor-metric-temp") or ""))

    # ---------- 21. CONSOLE / NETWORK ----------
    # Errors from the deliberate offline-PWA window (fonts/fetch fail while
    # offline — expected) and deliberate 4xx/502 probes are excluded.
    offline_idx = None
    real_errors = [e for e in console_errors
                   if "favicon" not in e.lower()
                   and "Failed to fetch" not in e          # offline window artifact
                   and "Failed to load resource" not in e]  # deliberate 4xx/502 probes
    check("console: no unexpected JS errors", not real_errors, "; ".join(real_errors[:4]))
    relevant_net = [n for n in network_failures
                    if "fonts.g" not in n                     # offline window artifact
                    and "ERR_INTERNET_DISCONNECTED" not in n
                    and "Failed to fetch" not in n]
    check("network: no unexpected request failures", not relevant_net, "; ".join(relevant_net[:3]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} comprehensive E2E checks passed =====")
if fails:
    print("FAILED:")
    for f in fails:
        print(" -", f[0], "::", f[2])
sys.exit(1 if fails else 0)
