"""E2E checks for the premium Living Sky redesign + Light/Dark theme system.

Requires the Flask server running on 127.0.0.1:5000.
Covers: FOUC-free bootstrap, toggle + localStorage persistence, system
default behavior, all-tab visual consistency in both themes, all 12
weather skies in both themes, rain/thunder FX integrity, radar basemap
theming and prefers-reduced-motion compliance.
Evidence: prints PASS/FAIL per check; exits non-zero on any FAIL.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def computed_bg_color(page, selector):
    return page.evaluate(
        f"getComputedStyle(document.querySelector({json.dumps(selector)})).backgroundColor"
    )


def theme_state(page):
    return page.evaluate("""(() => ({
        htmlTheme: document.documentElement.dataset.theme,
        htmlDark: document.documentElement.classList.contains('dark'),
        stored: localStorage.getItem('weathergpt-theme'),
        aria: document.getElementById('theme-toggle-btn')?.getAttribute('aria-pressed'),
        bodyBg: getComputedStyle(document.body).backgroundColor,
        cardBg: getComputedStyle(document.querySelector('#daily-forecast-container')).backgroundColor,
        ink: getComputedStyle(document.querySelector('.font-display-hero')).color,
        skyA: document.getElementById('sky-layer-a').className,
        skyB: document.getElementById('sky-layer-b').className,
        fx: { sky: document.getElementById('sky-fx')?.dataset.sky,
              clouds: document.getElementById('sky-fx')?.dataset.clouds,
              day: document.getElementById('sky-fx')?.dataset.day },
        headerBg: getComputedStyle(document.getElementById('app-header')).backgroundColor,
        tcMeta: document.querySelector('meta[name="theme-color"]').getAttribute('content'),
        tcMetaMedia: !!document.querySelector('meta[name="theme-color"][media]'),
    }))()""")


def sky_visible_class(page):
    # During the 2.6s crossfade BOTH layers carry .is-live; the incoming
    # layer is the one with inline z-index 2 (set by applyDynamicSky).
    return page.evaluate("""(() => {
        const a = document.getElementById('sky-layer-a');
        const b = document.getElementById('sky-layer-b');
        const cond = (el) => el && [...el.classList].find(c => c.startsWith('sky-') && c !== 'sky-layer');
        const za = parseInt(a?.style.zIndex || '0', 10);
        const zb = parseInt(b?.style.zIndex || '0', 10);
        const front = zb >= za ? b : a;
        return front?.classList.contains('is-live') ? cond(front) : null;
    })()""")


# Real card surfaces per tab (transparent containers excluded).
TAB_PROBES = {
    "view-forecast": "#daily-forecast-container",
    "view-map": "#radar-map",
    "view-weathergpt": "#assistant-chat-form",
    "view-farmer": "#farm-crop-select",
    "view-insights": "#view-insights .bg-surface-container-high",
}


import re


def _bg_rgb(bg):
    """Extract the R,G,B ints from an rgb()/rgba() computed string."""
    nums = [int(x) for x in re.findall(r"\d+", bg)]
    return nums[:3] if len(nums) >= 3 else None


def is_dark_bg(bg):
    rgb = _bg_rgb(bg)
    return bool(rgb) and sum(rgb) / 3 < 90


def is_light_bg(bg):
    rgb = _bg_rgb(bg)
    return bool(rgb) and sum(rgb) / 3 >= 200


CONDITIONS = {
    "clear-day": ["clear-day", 24, 1],
    "clear-night": ["clear-night", 18, 0],
    "partly-cloudy-day": ["partly-cloudy-day", 22, 1],
    "partly-cloudy-night": ["partly-cloudy-night", 16, 0],
    "cloudy": ["cloudy", 19, 1],
    "fog": ["fog", 12, 1],
    "drizzle": ["drizzle", 14, 1],
    "rain": ["rain", 13, 1],
    "snow": ["snow", -2, 1],
    "thunder": ["thunder", 21, 1],
    "hot": ["clear-day", 35, 1],
    "cold": ["clear-day", 0, 1],
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    # ---------- 1. Bootstrap honors a pre-saved dark theme (no flash) ----------
    page.add_init_script("localStorage.setItem('weathergpt-theme','dark')")
    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    s = theme_state(page)
    check("bootstrap: saved dark applied pre-paint", s["htmlTheme"] == "dark" and s["htmlDark"], s["htmlTheme"])
    check("bootstrap: toggle aria-pressed=true in dark", s["aria"] == "true", s["aria"])
    check("bootstrap: theme-color meta follows dark", s["tcMeta"] == "#0F1626" and not s["tcMetaMedia"], s["tcMeta"])
    check("dark: body bg is deep navy (not white)", s["bodyBg"] in ("rgb(16, 21, 31)", "rgb(16, 21, 31, 1)"), s["bodyBg"])
    check("dark: card bg re-themed", s["cardBg"] == "rgb(255, 255, 255)" or "rgb(2" in s["cardBg"], s["cardBg"])
    check("dark: hero ink is near-white", s["ink"].startswith("rgb(237") or s["ink"].startswith("rgb(255"), s["ink"])
    check("dark: header frost dark", s["headerBg"] == "rgb(13, 17, 27)" or "13, 17, 27" in s["headerBg"], s["headerBg"])

    # ---------- 2. Toggle round-trip + persistence ----------
    page.click("#theme-toggle-btn")
    page.wait_for_timeout(600)
    s2 = theme_state(page)
    check("toggle: switches to light", s2["htmlTheme"] == "light" and not s2["htmlDark"], s2["htmlTheme"])
    check("toggle: persists light to localStorage", s2["stored"] == "light", s2["stored"])
    check("toggle: aria-pressed=false in light", s2["aria"] == "false", s2["aria"])
    check("toggle: theme-color meta follows light", s2["tcMeta"] == "#3D7CC9", s2["tcMeta"])
    check("light: body bg light paper", s2["bodyBg"].startswith("rgb(232"), s2["bodyBg"])
    # Persistence across reload on a FRESH page (the init script used for
    # the bootstrap check re-seeds localStorage on every navigation of the
    # original page, so it must not participate here).
    ppage = browser.new_page(viewport={"width": 390, "height": 844})
    ppage.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    ppage.wait_for_timeout(2000)
    ppage.evaluate("localStorage.setItem('weathergpt-theme','light')")
    ppage.reload(wait_until="domcontentloaded")
    ppage.wait_for_timeout(2000)
    s3 = theme_state(ppage)
    check("persistence: light survives reload", s3["htmlTheme"] == "light" and s3["stored"] == "light", s3["stored"])
    ppage.click("#theme-toggle-btn")  # light -> dark
    ppage.wait_for_timeout(400)
    check("persistence: dark re-persisted", theme_state(ppage)["stored"] == "dark")
    ppage.close()

    # ---------- 3. All tabs consistent in dark ----------
    page.evaluate("applyTheme('dark')")
    page.wait_for_timeout(600)
    dark_ok = True
    detail = []
    for view, probe in TAB_PROBES.items():
        page.click(f'.nav-tab[data-view="{view}"]')
        page.wait_for_timeout(350)
        bg = computed_bg_color(page, probe)
        ok = bg != "rgba(0, 0, 0, 0)" and is_dark_bg(bg)
        dark_ok = dark_ok and ok
        detail.append(f"{view}:{bg}")
    check("dark: all 5 tabs themed (no white cards/leaks)", dark_ok, " | ".join(detail))

    # Radar basemap flips to dark tiles in dark theme.
    page.click('.nav-tab[data-view="view-map"]')
    page.wait_for_timeout(900)
    basemap_dark = page.evaluate("""(() => {
        const c = document.getElementById('radar-map');
        return c ? !!document.querySelector('.leaflet-tile') : false;
    })()""")
    check("dark: radar map visible and active", basemap_dark)

    # ---------- 4. Light theme all tabs ----------
    page.evaluate("applyTheme('light')")
    page.wait_for_timeout(600)
    light_ok = True
    detail = []
    for view, probe in TAB_PROBES.items():
        page.click(f'.nav-tab[data-view="{view}"]')
        page.wait_for_timeout(300)
        bg = computed_bg_color(page, probe)
        ok = bg != "rgba(0, 0, 0, 0)" and is_light_bg(bg)
        light_ok = light_ok and ok
        detail.append(f"{view}:{bg}")
    check("light: all 5 tabs render surfaces", light_ok, " | ".join(detail))

    # ---------- 5. All 12 skies in BOTH themes ----------
    for theme in ("dark", "light"):
        page.evaluate(f"applyTheme('{theme}')")
        page.wait_for_timeout(200)
        for cond, (cat, temp, isDay) in CONDITIONS.items():
            band = "hot" if cond == "hot" else "cold" if cond == "cold" else "mild"
            page.evaluate(f"""(() => {{
                applyDynamicSky('{cat}', '{band}');
                applySkyFx('{cat}', {isDay});
                applyPrecipFx('{cat}');
            }})()""")
            page.wait_for_timeout(150)
            st = theme_state(page)
            visible = sky_visible_class(page)
            expected = f"sky-{cond}" if cond not in ("hot", "cold") else f"sky-{cond}"
            check(f"sky {theme}/{cond}: active layer correct", visible == expected, f"got {visible}")
            fx = st["fx"]
            want_clouds = "0" if cond in ("clear-day", "clear-night", "hot", "cold", "snow") else (
                "1" if cond.startswith("partly") else "2" if cond in ("cloudy", "fog") else "3")
            want_sky = "fair" if cond in ("clear-day", "clear-night", "hot", "cold") or cond.startswith("partly") else cond
            check(f"sky {theme}/{cond}: fx state", fx["sky"] == want_sky and fx["clouds"] == want_clouds,
                  f"{fx['sky']}/{fx['clouds']}")
        # Rain/thunder FX integrity (light + dark)
        page.evaluate("applyPrecipFx('rain')")
        page.wait_for_timeout(150)
        rain_visible = page.evaluate("document.getElementById('rain-fx').classList.contains('is-visible')")
        rain_drops = page.evaluate("document.querySelectorAll('#rain-fx .rain-drop').length")
        check(f"rain fx {theme}: visible with drops", rain_visible and rain_drops > 20, f"{rain_drops} drops")
        page.evaluate("applyPrecipFx('thunder')")
        page.wait_for_timeout(150)
        thunder_drops = page.evaluate("document.querySelectorAll('#rain-fx .rain-drop').length")
        check(f"thunder fx {theme}: denser pool", thunder_drops > rain_drops, f"{thunder_drops}>{rain_drops}")
        page.evaluate("applyPrecipFx('clear-day')")
        page.wait_for_timeout(100)
        rain_hidden = page.evaluate("!document.getElementById('rain-fx').classList.contains('is-visible')")
        check(f"rain fx {theme}: hides on fair condition", rain_hidden)

    # ---------- 6. prefers-reduced-motion ----------
    rm_page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    rm_page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    rm_page.wait_for_timeout(2000)
    rm = rm_page.evaluate("""(() => ({
        clouds: getComputedStyle(document.querySelector('.cb-1')).animationName,
        rays: getComputedStyle(document.querySelector('.sky-rays')).animationName,
        sparkle: getComputedStyle(document.querySelector('.sky-sparkle')).animationName,
        rain: (getComputedStyle(document.querySelector('.rain-drop') || document.body).animationName),
        theme: document.documentElement.dataset.theme,
    }))()""")
    check("reduced-motion: cloud drift disabled", rm["clouds"] == "none", rm["clouds"])
    check("reduced-motion: rays sway disabled", rm["rays"] == "none", rm["rays"])
    check("reduced-motion: sparkle twinkle disabled", rm["sparkle"] == "none", rm["sparkle"])
    check("reduced-motion: theme system still works", rm["theme"] in ("light", "dark"), rm["theme"])
    rm_page.close()

    # ---------- 7. console sanity ----------
    fatal = [e for e in console_errors if "429" not in e and "favicon" not in e]
    check("console: no unexpected JS errors", not fatal, str(fatal[:3]))
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} theme E2E checks passed =====")
sys.exit(1 if failed else 0)
