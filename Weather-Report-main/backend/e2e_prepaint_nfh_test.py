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
        """() => {
          const strip = document.getElementById('next-hours-strip');
          if (!strip) return null;
          const cs = getComputedStyle(strip);
          const slots = [...strip.querySelectorAll('[role="listitem"]')];
          return {
            present: true,
            display: cs.display,
            height: Math.round(strip.getBoundingClientRect().height),
            slotCount: slots.length,
            firstLabel: slots.length ? slots[0].textContent.trim() : null,
            text: strip.textContent.trim(),
            emptyNote: strip.textContent.includes('unavailable') ||
                       strip.textContent.includes('उपलब्ध नहीं') ||
                       strip.textContent.includes('అందుబాటులో లేదు'),
          };
        }"""
    )


def hero_bottom_gap(pg):
    return pg.evaluate(
        """() => {
          const card = document.querySelector('main [class*="order-1"]');
          const strip = document.getElementById('next-hours-strip');
          if (!card || !strip) return null;
          const cr = card.getBoundingClientRect();
          const sr = strip.getBoundingClientRect();
          return Math.round(sr.top - cr.bottom);
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

        # ============ 4: strip from fresh load with real hourly data ============
        dom_click(page, '[data-view="view-forecast"]')
        page.wait_for_timeout(300)
        st = strip_state(page)
        check("strip present", bool(st and st["present"]))
        check("strip visible (flex)", bool(st and "flex" in (st["display"] or "")))
        check("strip: 6 real slots", bool(st and st["slotCount"] == 6), f"slots={st and st['slotCount']}")
        check("strip: compact single row (<= 56px)", bool(st and 0 < st["height"] <= 56), f"h={st and st['height']}")
        check(
            "strip: first slot is localized Now (HI)",
            bool(st and (st["firstLabel"] or "").startswith("अभी")),
            st and st["firstLabel"],
        )
        gap = hero_bottom_gap(page)
        check("strip sits directly below hero card (< 90px gap)", gap is not None and -20 <= gap <= 90, f"gap={gap}")

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
            "switch->TE: strip Now localized",
            bool(st and (st["firstLabel"] or "").startswith("ఇప్పుడు")),
            st and st["firstLabel"],
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
            "switch->EN: strip Now back to EN",
            bool(st and (st["firstLabel"] or "").startswith("Now")),
            st and st["firstLabel"],
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
        check("mobile: strip present with slots", bool(st and st["slotCount"] == 6), f"slots={st and st['slotCount']}")
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
