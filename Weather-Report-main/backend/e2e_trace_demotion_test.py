"""E2E regression: trace-precipitation demotion in getConditionCategory.

Locks the Option A rain-condition rule: WMO codes 51-82 with a reported
current.precipitation below 0.1 mm (gauge "trace") demote ONE honest visual
step (drizzle->partly cloudy, rain->drizzle) across every scene surface
(icon/label, page sky, card scene, rain-fx pool). Amounts >= 0.1 mm keep the
genuine category; missing/legacy payloads behave exactly as before; snow and
thunder codes are never demoted.

Requires the Flask server on 127.0.0.1:5000.
"""
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


def render_with(page, current):
    """Install a real payload shape and run the full render path."""
    page.evaluate(
        """(current) => {
            state.weather = { current,
                hourly: { time: [], temperature_2m: [], precipitation_probability: [],
                          weather_code: [], uv_index: [] },
                daily: { time: [], temperature_2m_max: [], temperature_2m_min: [],
                         precipitation_probability_max: [], weather_code: [], uv_index_max: [] } };
            renderForecastScreen();
        }""",
        current,
    )
    page.wait_for_timeout(200)
    return page.evaluate(
        """(() => {
            const skyA = document.getElementById('sky-layer-a'), skyB = document.getElementById('sky-layer-b');
            const cond = (el) => el && [...el.classList].find(c => c.startsWith('sky-') && c !== 'sky-layer');
            const za = parseInt(skyA?.style.zIndex || '0', 10), zb = parseInt(skyB?.style.zIndex || '0', 10);
            const front = zb >= za ? skyB : skyA;
            return {
                bodyCond: document.body.dataset.condition,
                label: document.getElementById('hero-condition-text')?.textContent,
                icon: document.getElementById('hero-condition-icon')?.textContent,
                sky: front?.classList.contains('is-live') ? cond(front) : null,
                scene: document.getElementById('weather-scene')?.dataset.scene,
                cardScene: document.getElementById('card-scene')?.dataset.scene,
                rainFx: document.getElementById('rain-fx').classList.contains('is-visible'),
                drops: document.querySelectorAll('#rain-fx .rain-drop').length,
            };
        })()"""
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(1500)

    # ---------- 1. Pure-function matrix ----------
    print("\n===== 1. getConditionCategory(code, isDay, precipMm) matrix =====")
    matrix = pg.evaluate(
        """(() => {
            const codes = [51, 80, 63, 95, 71];
            const out = {};
            for (const code of codes) {
                out[code] = {
                    nullAmt: getConditionCategory(code, 1, null),
                    noArg:   getConditionCategory(code, 1),
                    trace:   getConditionCategory(code, 1, 0.05),
                    boundary: getConditionCategory(code, 1, 0.1),
                    real:    getConditionCategory(code, 1, 2.0),
                    nightTrace: getConditionCategory(code, 0, 0.05),
                };
            }
            return out;
        })()"""
    )

    legacy_cat = {"51": "drizzle", "80": "rain", "63": "rain"}
    genuine_cat = {"51": "drizzle", "80": "rain", "63": "rain"}
    demoted_cat = {"51": "partly-cloudy-day", "80": "drizzle", "63": "drizzle"}

    for code in ("51", "80", "63"):
        m = matrix[code]
        check(f"code {code}: null amount -> legacy behavior (no demotion)",
              m["nullAmt"] == m["noArg"] == legacy_cat[code], m["nullAmt"])
        check(f"code {code}: 0.05mm trace -> demoted one step",
              m["trace"] == demoted_cat[code], m["trace"])
        check(f"code {code}: 0.1mm boundary -> genuine (no demotion)",
              m["boundary"] == genuine_cat[code], m["boundary"])
        check(f"code {code}: 2.0mm -> genuine category",
              m["real"] == genuine_cat[code], m["real"])

    check("code 51: night trace -> night-aware partly cloudy",
          matrix["51"]["nightTrace"] == "partly-cloudy-night", matrix["51"]["nightTrace"])
    check("code 95 thunder never demoted (any amount)",
          matrix["95"]["trace"] == matrix["95"]["real"] == "thunder", matrix["95"]["trace"])
    check("code 71 snow never demoted (any amount)",
          matrix["71"]["trace"] == matrix["71"]["real"] == "snow", matrix["71"]["trace"])
    check("strict boundary: 0.099 demotes, 0.1 stays",
          pg.evaluate("getConditionCategory(61, 1, 0.099)") == "drizzle"
          and pg.evaluate("getConditionCategory(61, 1, 0.1)") == "rain",
          "0.099->drizzle, 0.1->rain")

    # ---------- 2. Full render pipeline (real payload shapes) ----------
    print("\n===== 2. Full render pipeline (real payload shapes) =====")

    # 2a. Genuine drizzle at the boundary: code 51 + 0.1mm stays drizzle
    vizag = render_with(pg, {"time": "2026-09-27T13:00", "temperature_2m": 29.4,
                             "weather_code": 51, "is_day": 1, "precipitation": 0.1,
                             "cloud_cover": 92, "relative_humidity_2m": 84,
                             "wind_speed_10m": 12.2, "wind_direction_10m": 230,
                             "apparent_temperature": 32.0})
    check("code51+0.1mm: genuine drizzle kept (icon/label/state)",
          vizag["icon"] == "water_drop" and vizag["bodyCond"] == "drizzle",
          f"{vizag['icon']}/{vizag['bodyCond']}")
    check("code51+0.1mm: drizzle rain-fx active", vizag["rainFx"] and vizag["drops"] > 0,
          f"{vizag['drops']} drops")

    # 2b. Trace rain: code 61 + 0.05mm demotes everywhere, lighter scene
    trace = render_with(pg, {"time": "2026-09-27T13:00", "temperature_2m": 29.4,
                             "weather_code": 61, "is_day": 1, "precipitation": 0.05,
                             "cloud_cover": 95, "relative_humidity_2m": 86,
                             "wind_speed_10m": 9.1, "wind_direction_10m": 210,
                             "apparent_temperature": 31.5})
    check("code61+0.05mm: demoted rain->drizzle (icon/state)",
          trace["bodyCond"] == "drizzle" and trace["icon"] == "water_drop",
          f"{trace['bodyCond']}/{trace['icon']}")
    check("code61+0.05mm: light rain scene (not the full rain pool)",
          trace["scene"] == "rain" and trace["cardScene"] == "rain"
          and trace["rainFx"] and 10 <= trace["drops"] < 60,
          f"scene={trace['scene']} card={trace['cardScene']} drops={trace['drops']}")
    check("code61+0.05mm: drizzle sky, NOT the full rain sky",
          trace["sky"] == "sky-drizzle", trace["sky"])

    # 2c. Control: genuine rain untouched
    genuine = render_with(pg, {"time": "2026-09-27T13:00", "temperature_2m": 26.1,
                               "weather_code": 63, "is_day": 1, "precipitation": 2.0,
                               "cloud_cover": 100, "relative_humidity_2m": 93,
                               "wind_speed_10m": 18.0, "wind_direction_10m": 250,
                               "apparent_temperature": 27.0})
    check("code63+2mm control: full Rain Showers preserved",
          genuine["bodyCond"] == "rain" and genuine["icon"] == "rainy"
          and genuine["label"] == "Rain Showers",
          f"{genuine['bodyCond']}/{genuine['icon']}/{genuine['label']}")
    check("code63+2mm control: rain sky + dense drop pool",
          genuine["sky"] == "sky-rain" and genuine["drops"] > 60,
          f"{genuine['sky']}/{genuine['drops']} drops")

    # 2d. Legacy payload without a precipitation field: unchanged behavior
    legacy = render_with(pg, {"time": "2026-09-27T13:00", "temperature_2m": 29.4,
                              "weather_code": 80, "is_day": 1,
                              "cloud_cover": 90, "relative_humidity_2m": 80,
                              "wind_speed_10m": 8.0, "wind_direction_10m": 200,
                              "apparent_temperature": 31.0})
    check("legacy payload (no precipitation field): code80 -> full rain (unchanged)",
          legacy["bodyCond"] == "rain" and legacy["sky"] == "sky-rain",
          f"{legacy['bodyCond']}/{legacy['sky']}")

    check("no page JS errors during the run", not errs, "; ".join(errs[:2]))

    browser.close()

passed = sum(1 for _, ok in results if ok)
print(f"\n===== {passed}/{len(results)} trace-demotion regression checks passed =====")
sys.exit(0 if passed == len(results) else 1)
