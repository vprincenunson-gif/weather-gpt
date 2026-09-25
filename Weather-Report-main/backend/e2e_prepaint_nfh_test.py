"""E2E verification: pre-paint i18n + Next Few Hours strip.

1. No raw translation keys ever visible: fresh load with a persisted
   language renders chrome in that language from the very first paint
   (pre-paint inline translator, before script.js runs).
2. Language persists across reload; pill row reflects it.
3. EN -> HI -> TE -> EN switching updates the strip title, slot labels
   (incl. localized "Now") and all chrome together, no reload.
4. Next Few Hours strip: real hourly data only (6 upcoming slots with
   time/temp/condition-icon/precip%), sits directly below the hero card,
   compact (single row <= 56px tall), localized, updates on language
   switch immediately without refetch.
5. Graceful missing-data: an hourly-less weather payload hides the slots
   and shows the localized unavailable note (never fabricated).
6. Desktop + mobile from a fresh load; screenshots into scene-previews/.

Requires the Flask server on 127.0.0.1:5000.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(__file__), "..", "scene-previews", "review")
os.makedirs(OUT, exist_ok=True)

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


def dom_click(pg, sel):
    pg.eval_on_selector(sel, "el => el.click()")


def switch_to(pg, lang):
    t0 = time.time()
    dom_click(pg, f'.lang-btn[data-lang="{lang}"]')
    pg.wait_for_timeout(50)
    return (time.time() - t0) * 1000


HOURLY_PAYLOAD = {
    "current": {
        "time": "2026-09-23T14:00",
        "temperature_2m": 29.4,
        "relative_humidity_2m": 61,
        "apparent_temperature": 32.0,
        "is_day": 1,
        "weather_code": 2,
        "wind_speed_10m": 12.5,
        "wind_direction_10m": 200,
        "precipitation": 0.0,
        "surface_pressure": 1008.0,
        "dew_point_2m": 21.0,
        "uv_index": 6.0,
    },
    "hourly": {
        "time": [],
        "temperature_2m": [29.4, 30.1, 30.6, 29.8, 28.2, 26.5, 25.1, 24.3],
        "precipitation_probability": [5, 10, 45, 60, 35, 20, 10, 5],
        "weather_code": [2, 2, 3, 61, 61, 3, 2, 1],
    },
    "daily": {
        "time": ["2026-09-23", "2026-09-24"],
        "temperature_2m_max": [31.0, 30.0],
        "temperature_2m_min": [23.0, 22.5],
        "precipitation_probability_max": [60, 20],
        "precipitation_sum": [4.2, 0.5],
        "wind_speed_10m_max": [22.0, 15.0],
        "weather_code": [61, 2],
    },
}

NO_HOURLY_PAYLOAD = {
    "current": HOURLY_PAYLOAD["current"],
    "hourly": None,
    "daily": HOURLY_PAYLOAD["daily"],
}


# Hour stamps are generated relative to the actual clock so the
# "upcoming slots" window always contains future hours.
_now = datetime.now().replace(minute=0, second=0, microsecond=0)
HOURLY_PAYLOAD["hourly"]["time"] = [
    (_now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(0, 8)
]
HOURLY_PAYLOAD["current"]["time"] = _now.strftime("%Y-%m-%dT%H:%M")


def install_routes(ctx, payload):
    def route_json(pattern, body):
        def handler(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        ctx.route(pattern, handler)

    # The backend wraps the weather object: { weather, alerts, rain_timeline }.
    route_json("**/api/weather*", {"weather": payload, "alerts": [], "rain_timeline": None})
    route_json("**/api/report*", {"report": "Intercepted synopsis."})
    route_json("**/api/alerts*", {"alerts": []})


def strip_state(pg):
    return pg.evaluate(
        r"""() => {
          const strip = document.getElementById('next-hours-strip');
          const nowRow = document.getElementById('nfh-now-row');
          if (!strip) return null;
          const slots = [...strip.querySelectorAll('[role="listitem"]')];
          const nowText = nowRow ? nowRow.textContent : "";
          const has = (re) => re.test(nowText);
          return {
            present: true,
            display: getComputedStyle(strip).display,
            gridCols: getComputedStyle(strip).gridTemplateColumns.split(" ").length,
            height: Math.round(strip.getBoundingClientRect().height),
            nowRow: !!nowRow,
            nowText: nowText.trim(),
            nowTemp: has(/\d+°/),
            nowRain: has(/Rain|वर्षा|వర్ష/i),
            nowWind: has(/Wind|हवा|గాలి/i),
            nowHumidity: has(/Humidity|आर्द्रता|తేమ/i),
            slotCount: slots.length,
            firstLabel: slots.length ? slots[0].textContent.trim() : null,
            slotWidthsEqual: (() => {
              const ws = slots.map((s) => Math.round(s.getBoundingClientRect().width));
              return ws.length > 1 && Math.max(...ws) - Math.min(...ws) <= 2;
            })(),
            text: strip.textContent.trim(),
            emptyNote: strip.textContent.includes('unavailable') ||
                       strip.textContent.includes('उपलब्ध नहीं') ||
                       strip.textContent.includes('అందుబాటులో లేదు'),
          };
        }"""
    )


def hero_bottom_gap(pg):
    """Gap between the animated scene card's bottom edge and the NFH panel
    (the panel is the hero column block directly under the card; measuring
    the inner hourly grid would wrongly include the NOW row's height)."""
    return pg.evaluate(
        """() => {
          const scene = document.querySelector('main .cs-sky-a');
          const panel = document.getElementById('next-hours-panel');
          if (!scene || !panel) return null;
          const card = scene.closest('.rounded-3xl');
          if (!card) return null;
          const cr = card.getBoundingClientRect();
          const pr = panel.getBoundingClientRect();
          return Math.round(pr.top - cr.bottom);
        }"""
    )


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ============ 1+2+3: pre-paint & switching (desktop) ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        install_routes(ctx, HOURLY_PAYLOAD)

        # Persist HI, then a COLD load: first visible paint must be HI.
        page.goto(BASE, wait_until="domcontentloaded")
        page.evaluate("localStorage.setItem('weathergpt-lang','hi')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(300)

        check(
            "pre-paint: nav is HI at first paint",
            "रडार" in (txt(page, '[data-view="view-map"]') or ""),
            txt(page, '[data-view="view-map"]'),
        )
        check(
            "pre-paint: no raw data-i18n keys visible",
            page.evaluate(
                """() => {
                  const els = [...document.querySelectorAll('[data-i18n]')];
                  return els.filter(el => el.textContent.trim() === el.getAttribute('data-i18n')).length;
                }"""
            ) == 0,
        )
        check(
            "pre-paint: <html lang> is hi",
            (page.evaluate("document.documentElement.lang") or "").startswith("hi"),
        )

        # Language persists + runtime state agrees with the pill row.
        check(
            "persisted language drives pill row (HI active)",
            page.evaluate(
                """() => {
                  const act = document.querySelector('.lang-btn.is-active');
                  return act ? act.dataset.lang : null;
                }"""
            ) == "hi",
        )

        # ============ 4: approved concept — NOW row + equal-width hourly cards ============
        dom_click(page, '[data-view="view-forecast"]')
        page.wait_for_timeout(300)
        st = strip_state(page)
        check("panel present", bool(st and st["present"]))
        check("NOW row present (wide top row)", bool(st and st["nowRow"]))
        check("NOW row: current temp", bool(st and st["nowTemp"]), st and st["nowText"][:80])
        check("NOW row: rain chance", bool(st and st["nowRain"]), st and st["nowText"][:80])
        check("NOW row: wind", bool(st and st["nowWind"]), st and st["nowText"][:80])
        check("NOW row: humidity", bool(st and st["nowHumidity"]), st and st["nowText"][:80])
        check("NOW row: localized Now (HI)", bool(st and "अभी" in (st["nowText"] or "")), st and st["nowText"][:40])
        check("hourly row: 5 upcoming slots", bool(st and st["slotCount"] == 5), f"slots={st and st['slotCount']}")
        check("hourly row: grid display", bool(st and "grid" in (st["display"] or "")))
        check("hourly row: equal widths (5 equal columns)", bool(st and st["gridCols"] == 5 and st["slotWidthsEqual"]),
              f"cols={st and st['gridCols']}")
        check("hourly row: no Now label in cards (NOW moved up)",
              bool(st and not (st["firstLabel"] or "").startswith("अभी")), st and st["firstLabel"])
        # Measure only after layout has been stable for two consecutive
        # animation frames (late-loading fonts can shift the hero card
        # height mid-settle; a fixed wait races that).
        page.wait_for_function(
            """() => {
              const scene = document.querySelector('main .cs-sky-a');
              const card = scene && scene.closest('.rounded-3xl');
              const panel = document.getElementById('next-hours-panel');
              if (!card || !panel) return false;
              const g = () => Math.round(panel.getBoundingClientRect().top - card.getBoundingClientRect().bottom);
              const a = g();
              return new Promise((res) => requestAnimationFrame(() =>
                requestAnimationFrame(() => res(g() === a))));
            }""",
            timeout=5000,
        )
        gap = hero_bottom_gap(page)
        check("panel sits directly below hero scene card (0..60px gap)", gap is not None and 0 <= gap <= 60, f"gap={gap}")
        check(
            "panel is inside the left hero column",
            page.evaluate(
                """() => {
                  const strip = document.getElementById('next-hours-strip');
                  return !!strip.closest('[class*="order-1"]');
                }"""
            ),
        )
        check(
            "all 5 slots visible without scrolling (desktop)",
            page.evaluate(
                """() => {
                  const s = document.getElementById('next-hours-strip');
                  return s.scrollWidth <= s.clientWidth + 2;
                }"""
            ),
        )

        page.screenshot(path=os.path.join(OUT, "nfh_hi_desktop.png"))

        # Switching updates the strip without refetch
        api_wx = {"n": 0}
        page.evaluate(
            """() => {
              window.__wx = 0;
              const o = window.fetch;
              window.fetch = function(...a){ const p=o.apply(this,a);
                if (String(a[0]).includes('/api/weather')) p.then(()=>{window.__wx++;}).catch(()=>{});
                return p; };
            }"""
        )
        switch_to(page, "te")
        page.wait_for_timeout(80)
        st = strip_state(page)
        check(
            "switch->TE: strip title localized",
            "తర్వాతి" in (txt(page, '[data-i18n="nfhTitle"]') or ""),
            txt(page, '[data-i18n="nfhTitle"]'),
        )
        check(
            "switch->TE: NOW row localized (ఇప్పుడు)",
            bool(st and "ఇప్పుడు" in (st["nowText"] or "")),
            st and st["nowText"][:40],
        )
        switch_to(page, "en")
        page.wait_for_timeout(80)
        st = strip_state(page)
        check(
            "switch->EN: strip title back to EN",
            "Next few hours" in (txt(page, '[data-i18n="nfhTitle"]') or ""),
            txt(page, '[data-i18n="nfhTitle"]'),
        )
        check(
            "switch->EN: NOW row back to EN",
            bool(st and "Now" in (st["nowText"] or "")),
            st and st["nowText"][:40],
        )
        check("no weather refetch during switches", page.evaluate("window.__wx") == 0, page.evaluate("window.__wx"))
        check("zero page errors (desktop)", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 5: graceful missing hourly data ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        install_routes(ctx, NO_HOURLY_PAYLOAD)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        dom_click(page, '[data-view="view-forecast"]')
        page.wait_for_timeout(300)
        st = strip_state(page)
        check("missing hourly: no slots rendered", bool(st and st["slotCount"] == 0), f"slots={st and st['slotCount']}")
        check("missing hourly: honest localized note", bool(st and st["emptyNote"]), st and st["text"][:60])
        check("zero page errors (no-hourly)", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        # ============ 6: mobile fresh load ============
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        install_routes(ctx, HOURLY_PAYLOAD)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        dom_click(page, '[data-view="view-forecast"]')
        page.wait_for_timeout(300)
        st = strip_state(page)
        check("mobile: NOW row present with 5 slots", bool(st and st["nowRow"] and st["slotCount"] == 5),
              f"slots={st and st['slotCount']} now={st and st['nowRow']}")
        check("mobile: NOW row has temp/rain/wind/humidity",
              bool(st and st["nowTemp"] and st["nowRain"] and st["nowWind"] and st["nowHumidity"]))
        check("mobile: no horizontal overflow", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        page.screenshot(path=os.path.join(OUT, "nfh_en_mobile.png"))
        switch_to(page, "hi")
        page.wait_for_timeout(80)
        check(
            "mobile: switch->HI updates strip title",
            "अगले कुछ घंटे" in (txt(page, '[data-i18n="nfhTitle"]') or ""),
            txt(page, '[data-i18n="nfhTitle"]'),
        )
        page.screenshot(path=os.path.join(OUT, "nfh_hi_mobile.png"))
        check("zero page errors (mobile)", len(errors) == 0, "; ".join(errors[:3]))
        ctx.close()

        browser.close()

    print(f"\n==== RESULT: {PASS} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("Failed:", *FAIL, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
