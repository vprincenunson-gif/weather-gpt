"""LIVE verification at daytime: Visakhapatnam real weather payload vs the
rendered scene. No route mocking — the page hits the real /api/weather and
the real Open-Meteo upstream, then every day/night consumer must agree with
the API's location-local is_day.

Checks:
  1. Real payload: is_day, weather_code, utc_offset_seconds, current time.
  2. body.dataset.condition matches getConditionCategory(code, is_day).
  3. Page scene / card scene / page-sky stars / sky-fx data-day all agree.
  4. Sun elevation sanity: the API's is_day must agree with the computed
     local solar state (within the model's tolerance) — catches a wrong
     timezone or a stale/cached night payload.
  5. No JS errors; screenshots for review.

Requires the Flask server on 127.0.0.1:5000 (real network access).
"""
import json
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
VIZAG = {"latitude": 17.6868, "longitude": 83.2185}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))


def run():
    # ---- Fetch the real payload exactly as the frontend does ----
    r = requests.get(
        f"{BASE}/api/weather",
        params={**VIZAG, "forecast_days": 7},
        timeout=40,
    )
    r.raise_for_status()
    payload = r.json().get("weather", {})
    cur = payload.get("current", {})
    is_day = cur.get("is_day")
    wcode = cur.get("weather_code")
    check("live API: is_day present", is_day is not None, f"is_day={is_day} code={wcode} "
          f"utc_offset={payload.get('utc_offset_seconds')} time={cur.get('time')}")
    print(f"  [live] time={cur.get('time')} local_temp={cur.get('temperature_2m')}C "
          f"is_day={is_day} wmo={wcode} utc_offset={payload.get('utc_offset_seconds')}s")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Dark-theme user context — the regression case.
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  color_scheme="dark")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Point the app at Visakhapatnam exactly like a manual search would,
        # through the app's own pipeline (updateLocation -> refreshWeatherData).
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        page.evaluate(f"""(() => {{
          state.currentLocation = {{ name: "Visakhapatnam", admin1: "Andhra Pradesh",
              country: "India", latitude: {VIZAG['latitude']}, longitude: {VIZAG['longitude']} }};
          state.locationRequestId++;
          refreshWeatherData(state.locationRequestId);
        }})()""")
        page.wait_for_timeout(4000)

        st = page.evaluate(r"""(() => {
            const pageScene = document.getElementById('weather-scene');
            const card = document.getElementById('card-scene');
            const fx = document.getElementById('sky-fx');
            const stars = document.getElementById('sky-stars');
            const liveSky = [...document.querySelectorAll('#card-scene .cs-sky')]
                .filter(l => l.classList.contains('is-live'))
                .map(l => [...l.classList].find(c => c.startsWith('cs-')
                     && !['cs-sky', 'cs-sky-a', 'cs-sky-b'].includes(c)))[0];
            return {
                cond: document.body.dataset.condition,
                pageScene: pageScene ? pageScene.dataset.scene : null,
                cardScene: card ? card.dataset.scene : null,
                cardSky: liveSky || null,
                fxDay: fx ? fx.dataset.day : null,
                starsVisible: stars ? stars.classList.contains('is-visible') : null,
                temp: document.getElementById('hero-temperature')?.textContent,
                place: document.getElementById('hero-place-label')?.textContent,
                stateIsDay: state.weather?.current?.is_day,
                stateCode: state.weather?.current?.weather_code,
            };
        })()""")

        expected_cat = page.evaluate(
            f"getConditionCategory({wcode}, {is_day})")
        check("live: state payload is_day matches direct API call",
              st["stateIsDay"] == is_day, f"ui={st['stateIsDay']} api={is_day}")
        check("live: body condition = getConditionCategory(real code, real is_day)",
              st["cond"] == expected_cat, f"{st['cond']} vs {expected_cat}")

        want_scene = page.evaluate(f"sceneForCondition('{expected_cat}', {is_day})")
        check("live: page scene follows real is_day",
              st["pageScene"] == want_scene, f"{st['pageScene']} vs {want_scene}")
        check("live: card scene follows real is_day",
              st["cardScene"] == want_scene, f"{st['cardScene']} vs {want_scene}")

        if is_day == 1:
            check("live: DAYTIME — page scene is NOT night",
                  st["pageScene"] != "night", st["pageScene"])
            check("live: DAYTIME — card scene is NOT night (moon/stars hidden)",
                  st["cardScene"] != "night", st["cardScene"])
            check("live: DAYTIME — night stars layer hidden",
                  st["starsVisible"] is False, st["starsVisible"])
            check("live: DAYTIME — sky-fx data-day=1",
                  st["fxDay"] == "1", st["fxDay"])
        else:
            check("live: NIGHT — page scene is night",
                  st["pageScene"] == "night", st["pageScene"])
            check("live: NIGHT — card scene is night",
                  st["cardScene"] == "night", st["cardScene"])
            check("live: NIGHT — stars layer visible",
                  st["starsVisible"] is True, st["starsVisible"])
            check("live: NIGHT — sky-fx data-day=0", st["fxDay"] == "0", st["fxDay"])

        # Solar sanity: the API's is_day must agree with the location's real
        # solar state (catches wrong-timezone or stale-night payloads).
        solar = page.evaluate(
            f"isLocalSunUp({VIZAG['latitude']}, {VIZAG['longitude']})")
        check("live: API is_day agrees with computed local solar state",
              solar is not None and (solar is True) == (is_day == 1),
              f"api_is_day={is_day} solar_up={solar}")

        check("live: hero renders the real temperature",
              st["temp"] is not None and st["temp"] != "", st["temp"])
        check("live: zero page errors", len(errors) == 0, "; ".join(errors[:3]))

        page.screenshot(path="../scene-previews/review/live_vizag_daytime_desktop.png",
                        full_page=False)
        ctx.close()
        browser.close()

    print(f"\n==== RESULT: {sum(1 for _, ok, _ in results if ok)} passed, "
          f"{sum(1 for _, ok, _ in results if not ok)} failed ====")
    if any(not ok for _, ok, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    run()
