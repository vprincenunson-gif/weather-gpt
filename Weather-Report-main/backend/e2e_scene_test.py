"""E2E checks for the Weather Scene Animations layer merged into the Living Sky.

Requires the Flask server running on 127.0.0.1:5000.
Covers: all weather states (sun/clouds/rain/night scenes from the REAL
condition), scene transitions, lazy pool seeding exactly once,
theme-independence (the scene follows the API's is_day, never the UI
dark theme), reduced-motion behavior, thunder-only lightning gating,
merge integrity (sky gradients + rain-fx + lightning-fx still active),
memory bounds across rapid switching, responsiveness and non-interactivity.
Evidence: prints PASS/FAIL per check; exits non-zero on any FAIL.
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))


def scene_state(page):
    return page.evaluate("""(() => {
        const host = document.getElementById('weather-scene');
        const parts = {};
        for (const p of host.querySelectorAll('.scene-part')) {
            parts[p.classList.contains('scene-sun') ? 'sun'
                 : p.classList.contains('scene-clouds') ? 'clouds'
                 : p.classList.contains('scene-storm') ? 'rain'
                 : 'night'] = getComputedStyle(p).display;
        }
        return {
            scene: host.dataset.scene,
            ariaHidden: host.getAttribute('aria-hidden'),
            pe: getComputedStyle(host).pointerEvents,
            parts,
            drops: document.querySelectorAll('#weather-scene .scene-drop').length,
            ripples: document.querySelectorAll('#weather-scene .scene-ripple').length,
            stars: document.querySelectorAll('#weather-scene .scene-star').length,
            theme: document.documentElement.dataset.theme,
            bodyCond: document.body.dataset.condition,
        };
    })()""")


def apply_condition(page, cat, is_day=1, temp=22):
    band = "hot" if temp >= 33 else "cold" if temp <= 0 else "mild"
    page.evaluate(f"""(() => {{
        applyDynamicSky('{cat}', '{band}');
        applySkyFx('{cat}', {is_day});
        applySceneFx('{cat}', {is_day});
        applyPrecipFx('{cat}');
    }})()""")


EXPECTED_SCENE = {
    "clear-day": "sun", "clear-night": "night",
    "partly-cloudy-day": "clouds", "partly-cloudy-night": "night",
    "cloudy": "clouds", "fog": "clouds",
    "drizzle": "rain", "rain": "rain", "thunder": "rain",
    "snow": "sun", "hot": "sun", "cold": "sun",
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    # ---------- 1. Host layer exists and boots into a valid scene ----------
    s = scene_state(page)
    check("boot: scene host present in Living Sky container", bool(s["scene"]))
    check("boot: scene is one of sun/clouds/rain/night", s["scene"] in ("sun", "clouds", "rain", "night"), s["scene"])
    check("boot: host is aria-hidden + pointer-events none", s["ariaHidden"] == "true" and s["pe"] == "none")
    check("boot: exactly one scene part displayed",
          list(s["parts"].values()).count("block") == 1, json.dumps(s["parts"]))
    # Boot scene must follow the REAL live condition (whatever it is),
    # with the dark-theme sun->night remap applied on top.
    boot_expect = EXPECTED_SCENE.get(s["bodyCond"], "sun")
    if s["theme"] == "dark" and boot_expect == "sun":
        boot_expect = "night"
    check("boot: scene follows the real live condition + theme",
          s["scene"] == boot_expect, f"{s['bodyCond']}/{s['theme']} -> {s['scene']} (want {boot_expect})")

    # ---------- 2. Every weather state maps to the right scene ----------
    for cond, (cat, temp, is_day) in {
        "clear-day": ["clear-day", 24, 1], "clear-night": ["clear-night", 18, 0],
        "partly-cloudy-day": ["partly-cloudy-day", 22, 1], "partly-cloudy-night": ["partly-cloudy-night", 16, 0],
        "cloudy": ["cloudy", 19, 1], "fog": ["fog", 12, 1],
        "drizzle": ["drizzle", 14, 1], "rain": ["rain", 13, 1],
        "snow": ["snow", -2, 1], "thunder": ["thunder", 21, 1],
        "hot": ["clear-day", 35, 1], "cold": ["clear-day", 0, 1],
    }.items():
        apply_condition(page, cat, is_day, temp)
        page.wait_for_timeout(120)
        s = scene_state(page)
        want = EXPECTED_SCENE[cond]
        check(f"scene {cond}: data-scene == {want}", s["scene"] == want, f"got {s['scene']}")
        visible_part = [k for k, v in s["parts"].items() if v == "block"]
        check(f"scene {cond}: exactly part '{want}' displayed", visible_part == [want], json.dumps(s["parts"]))

    # Rain scene pools seeded for rain/drizzle/thunder states
    apply_condition(page, "rain")
    page.wait_for_timeout(120)
    s = scene_state(page)
    check("rain scene: drop pool seeded (70)", s["drops"] == 70, s["drops"])
    check("rain scene: ripple pool seeded (10)", s["ripples"] == 10, s["ripples"])

    # Night scene stars
    apply_condition(page, "clear-night", 0)
    page.wait_for_timeout(120)
    s = scene_state(page)
    check("night scene: star pool seeded (40)", s["stars"] == 40, s["stars"])

    # ---------- 3. Merge integrity: Living Sky systems still active ----------
    apply_condition(page, "rain")
    page.wait_for_timeout(200)
    merged = page.evaluate("""(() => {
        const a = document.getElementById('sky-layer-a'), b = document.getElementById('sky-layer-b');
        const cond = (el) => el && [...el.classList].find(c => c.startsWith('sky-') && c !== 'sky-layer');
        const za = parseInt(a?.style.zIndex || '0', 10), zb = parseInt(b?.style.zIndex || '0', 10);
        const front = zb >= za ? b : a;
        return {
            sky: front?.classList.contains('is-live') ? cond(front) : null,
            rainFxVisible: document.getElementById('rain-fx').classList.contains('is-visible'),
            rainDrops: document.querySelectorAll('#rain-fx .rain-drop').length,
            sceneActive: document.getElementById('weather-scene').dataset.scene,
        };
    })()""")
    check("merge: sky gradient crossfade still drives the background", merged["sky"] == "sky-rain", merged["sky"])
    check("merge: real-precipitation rain-fx still active", merged["rainFxVisible"] and merged["rainDrops"] > 20,
          f"{merged['rainDrops']} drops")
    check("merge: scene layer active alongside rain-fx", merged["sceneActive"] == "rain")

    apply_condition(page, "thunder")
    page.wait_for_timeout(150)
    lightning_ok = page.evaluate("""(() => {
        document.body.dataset.condition = 'thunder';
        const flash = getComputedStyle(document.querySelector('#weather-scene .scene-flash')).animationName;
        const flicker = getComputedStyle(document.querySelector('#weather-scene .scene-storm-cloud')).animationName;
        const legacy = document.getElementById('lightning-fx');
        return { flash, flicker, legacyPresent: !!legacy };
    })()""")
    check("thunder: scene flash + cloud flicker gated to real thunder",
          lightning_ok["flash"] == "scene-lightning" and lightning_ok["flicker"] == "scene-cloud-flicker",
          json.dumps(lightning_ok))
    check("thunder: legacy random-strike lightning-fx still present", lightning_ok["legacyPresent"])
    gate_off = page.evaluate("""(() => {
        document.body.dataset.condition = 'rain';
        return {
            flash: getComputedStyle(document.querySelector('#weather-scene .scene-flash')).animationName,
            flicker: getComputedStyle(document.querySelector('#weather-scene .scene-storm-cloud')).animationName,
        };
    })()""")
    check("plain rain: NO lightning flash/flicker", gate_off["flash"] == "none" and gate_off["flicker"] == "none",
          json.dumps(gate_off))

    # ---------- 4. Transitions across all four scenes + pool reuse ----------
    seen = []
    pools_stable = True
    for cat, is_day in [("clear-day", 1), ("rain", 1), ("clear-night", 0), ("cloudy", 1),
                        ("clear-day", 1), ("rain", 1), ("clear-night", 0), ("cloudy", 1)]:
        apply_condition(page, cat, is_day)
        page.wait_for_timeout(80)
        seen.append(scene_state(page)["scene"])
    check("transitions: cycle sun->rain->night->cloudsx2 correct",
          seen == ["sun", "rain", "night", "clouds", "sun", "rain", "night", "clouds"], "->".join(seen))
    s = scene_state(page)
    check("memory: pools seeded exactly once after 8 switches (70/10/40)",
          s["drops"] == 70 and s["ripples"] == 10 and s["stars"] == 40,
          f"{s['drops']}/{s['ripples']}/{s['stars']}")

    node_count = page.evaluate("document.getElementById('weather-scene').querySelectorAll('*').length")
    check("memory: scene layer DOM bounded (< 200 nodes)", 0 < node_count < 200, f"{node_count} nodes")

    # Rapid switching stress: pool counts must never grow
    for i in range(20):
        page.evaluate(f"applySceneFx('{['clear-day', 'rain', 'clear-night', 'cloudy'][i % 4]}', 1)")
    s = scene_state(page)
    check("memory: 20 rapid switches do not grow the pools",
          s["drops"] == 70 and s["ripples"] == 10 and s["stars"] == 40,
          f"{s['drops']}/{s['ripples']}/{s['stars']}")

    # ---------- 5. Dark theme: scene follows the API's is_day, never the theme ----------
    # Daytime + dark theme must keep the sun scene (the old behavior remapped
    # sun -> night, which rendered a night atmosphere at 3 PM).
    page.evaluate("applyTheme('dark')")
    page.wait_for_timeout(300)
    apply_condition(page, "clear-day")
    page.wait_for_timeout(120)
    s = scene_state(page)
    check("dark: clear-day keeps sun scene (no night at daytime)", s["scene"] == "sun", s["scene"])
    dark_night_part = page.evaluate("getComputedStyle(document.querySelector('#weather-scene .scene-night')).display")
    check("dark: night part hidden for day condition", dark_night_part == "none", dark_night_part)
    apply_condition(page, "cloudy")
    page.wait_for_timeout(120)
    check("dark: cloudy still clouds scene", scene_state(page)["scene"] == "clouds")
    apply_condition(page, "rain")
    page.wait_for_timeout(120)
    check("dark: rain still rain scene", scene_state(page)["scene"] == "rain")
    apply_condition(page, "clear-night", is_day=0)
    page.wait_for_timeout(120)
    check("dark: clear-night still night scene", scene_state(page)["scene"] == "night")

    # Theme toggle must not flip the scene either way (through the real
    # handler). syncSceneFxTheme re-applies from body.dataset.condition —
    # in production renderForecastScreen always keeps it in sync with the
    # last applied condition, so mirror that here before toggling.
    page.evaluate("document.body.dataset.condition = 'clear-night'")
    page.click("#theme-toggle-btn")
    page.wait_for_timeout(300)
    s = scene_state(page)
    check("toggle: dark->light keeps night scene for night condition", s["scene"] == "night", s["scene"])
    apply_condition(page, "clear-day")
    page.wait_for_timeout(120)
    check("toggle: light theme + clear-day -> sun scene again", scene_state(page)["scene"] == "sun")

    # Cloud tint follows the dark theme (moonlit SVG fill)
    page.evaluate("applyTheme('dark')")
    page.wait_for_timeout(250)
    apply_condition(page, "cloudy")
    page.wait_for_timeout(120)
    fill = page.evaluate("getComputedStyle(document.querySelector('#weather-scene .scene-cloud ellipse')).fill")
    check("dark: cloud ellipses repainted moonlit", fill not in ("rgb(255, 255, 255)", "#fff"), fill)

    # ---------- 6. Reduced motion ----------
    rm_page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    rm_page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    rm_page.wait_for_timeout(2000)
    rm_page.evaluate("applySceneFx('rain', 1)")
    rm_page.wait_for_timeout(150)
    rm = rm_page.evaluate("""(() => {
        const host = document.getElementById('weather-scene');
        const drop = document.querySelector('#weather-scene .scene-drop');
        return {
            scene: host.dataset.scene,
            drops: document.querySelectorAll('#weather-scene .scene-drop').length,
            ripples: document.querySelectorAll('#weather-scene .scene-ripple').length,
            dropAnim: drop ? getComputedStyle(drop).animationName : 'no-drop',
            cloudAnim: getComputedStyle(document.querySelector('#weather-scene .scene-cloud-1')).animationName,
            sunAnim: getComputedStyle(document.querySelector('#weather-scene .scene-sun-rays')).animationName,
        };
    })()""")
    check("reduced-motion: rain scene applies but pools NOT seeded",
          rm["scene"] == "rain" and rm["drops"] == 0 and rm["ripples"] == 0, f"{rm['drops']}/{rm['ripples']}")
    check("reduced-motion: no drop animation element created", rm["dropAnim"] in ("none", "no-drop"), rm["dropAnim"])
    rm_page.evaluate("applySceneFx('cloudy', 1)")
    rm_page.wait_for_timeout(120)
    rm_cloud = rm_page.evaluate("""(() => ({
        anim: getComputedStyle(document.querySelector('#weather-scene .scene-cloud-1')).animationName,
        scene: document.getElementById('weather-scene').dataset.scene,
    }))()""")
    check("reduced-motion: cloud drift frozen, scene still applied",
          rm_cloud["anim"] == "none" and rm_cloud["scene"] == "clouds", json.dumps(rm_cloud))
    rm_page.evaluate("applySceneFx('clear-night', 0)")
    rm_page.wait_for_timeout(150)
    rm_night = rm_page.evaluate("""(() => {
        const stars = document.querySelectorAll('#weather-scene .scene-star');
        const star = stars[0];
        return {
            stars: stars.length,
            anim: star ? getComputedStyle(star).animationName : 'none',
            opacity: star ? getComputedStyle(star).opacity : '0',
        };
    })()""")
    check("reduced-motion: night stars static but visible",
          rm_night["stars"] == 40 and rm_night["anim"] == "none" and float(rm_night["opacity"]) > 0.4,
          json.dumps(rm_night))
    rm_page.close()

    # ---------- 7. Desktop responsive pass ----------
    desktop = browser.new_page(viewport={"width": 1280, "height": 800})
    desktop.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    desktop.wait_for_timeout(2000)
    d = desktop.evaluate("""(() => {
        const host = document.getElementById('weather-scene');
        return { scene: host ? host.dataset.scene : null };
    })()""")
    check("desktop: scene layer active at 1280px", d["scene"] in ("sun", "night", "clouds", "rain"), d["scene"])
    # Force the sun scene (light theme) to measure the responsive clamp.
    desktop.evaluate("applyTheme('light'); applySceneFx('clear-day', 1)")
    desktop.wait_for_timeout(300)
    orb_w = desktop.evaluate(
        "document.querySelector('#weather-scene .scene-sun-orb').getBoundingClientRect().width")
    check("desktop: sun orb scales with viewport (clamp active)", 100 <= orb_w <= 180, f"{orb_w:.0f}px")
    desktop.close()

    # ---------- 8. Tab switching does not disturb the scene ----------
    page.evaluate("applySceneFx('cloudy', 1)")
    page.wait_for_timeout(120)
    before = scene_state(page)["scene"]
    for view in ("view-map", "view-weathergpt", "view-farmer", "view-insights", "view-forecast"):
        page.click(f'.nav-tab[data-view="{view}"]')
        page.wait_for_timeout(200)
    after = scene_state(page)["scene"]
    check("tabs: scene persists across all 5 views", before == after == "clouds", f"{before}->{after}")

    # ---------- 9. Console sanity ----------
    fatal = [e for e in console_errors if "429" not in e and "favicon" not in e]
    check("console: no unexpected JS errors", not fatal, str(fatal[:3]))
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} scene E2E checks passed =====")
sys.exit(1 if failed else 0)
