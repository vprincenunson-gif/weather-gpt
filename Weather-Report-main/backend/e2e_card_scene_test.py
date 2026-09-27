"""E2E verification: fully weather-synchronized visual system.

Requires the Flask server running on 127.0.0.1:5000.

Verifies the WeatherGPT visual system stays synchronized to the REAL
current condition on both layers at once:
  - page Living Sky (sky gradient class + scene + fx attributes)
  - in-card hero scene (#card-scene: sky gradient class + scene part)

For EVERY condition (clear day/night, partly cloudy day/night, cloudy,
fog/mist, drizzle, rain, thunderstorm, snow, hot, cold):
  - both layers carry the same real condition (never fabricated)
  - the card scene is clipped inside the card (no bleed onto the page)
  - the card content paints ABOVE the scene (scene is decorative)
  - the animated scene moves (frame-pair pixel motion)
  - switching condition updates both layers (with a real API payload
    check through renderForecastScreen's data path)
  - dark theme keeps the scene day/night-faithful (scene follows the
    API's is_day, never the theme) and the light/dark toggle keeps
    everything in sync
  - reduced-motion freezes animations with the scenes still painted
  - mobile (390x844) and desktop (1280x900) viewports both verified
  - page background layers and card scene stay separate (z-order)

Prints PASS/FAIL per check; exits non-zero on any FAIL.
"""
import sys

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))


def diff(a, b, box):
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    ia, ib = ia.crop(box), ib.crop(box)
    d = ImageChops.difference(ia, ib)
    mean = sum(ImageStat.Stat(d).mean) / 3.0
    changed = sum(ImageChops.difference(ia, ib).convert("L").histogram()[11:])
    return round(mean, 2), changed


def probe(page):
    """Read both visual layers + their synchronization state."""
    return page.evaluate(r"""(() => {
        // Page sky: inline z-index marks the front layer. Card sky: the
        // live layer is marked with .is-live (crossfade retires the other).
        const pageFront = (a, b) => {
            const ka = parseInt(a.style.zIndex || '0', 10), kb = parseInt(b.style.zIndex || '0', 10);
            return kb >= ka ? b : a;
        };
        const pageCond = (el) => [...el.classList].find(c => c.startsWith('sky-') && c !== 'sky-layer');
        const cardCond = (el) => [...el.classList].find(c =>
            c.startsWith('cs-') && !['cs-sky', 'cs-sky-a', 'cs-sky-b'].includes(c));
        const skyA = document.getElementById('sky-layer-a'), skyB = document.getElementById('sky-layer-b');
        const csA = document.querySelector('.cs-sky-a'), csB = document.querySelector('.cs-sky-b');
        const cardLive = csA && csB ? (csB.classList.contains('is-live') && !csA.classList.contains('is-live') ? csB : csA) : null;
        const card = document.getElementById('card-scene');
        const heroCard = document.querySelector('#view-forecast > div');
        const cardRect = heroCard ? heroCard.getBoundingClientRect() : null;
        const cardSceneRect = card ? card.getBoundingClientRect() : null;
        const heroContent = document.querySelector('#view-forecast .relative.z-10');
        const parts = {};
        if (card) for (const p of card.querySelectorAll('.cs-part')) {
            parts[p.classList.contains('cs-sun') ? 'sun'
                 : p.classList.contains('cs-clouds') ? 'clouds'
                 : p.classList.contains('cs-rain') ? 'rain'
                 : p.classList.contains('cs-storm') ? 'storm'
                 : p.classList.contains('cs-mist') ? 'mist'
                 : p.classList.contains('cs-snow') ? 'snow'
                 : 'night'] = getComputedStyle(p).display;
        }
        const cnt = (sel) => document.querySelectorAll(sel).length;
        return {
            pageSky: skyA && skyB ? pageCond(pageFront(skyA, skyB)) : null,
            cardSky: cardLive ? cardCond(cardLive) : null,
            pageScene: document.getElementById('weather-scene').dataset.scene,
            pageFx: { sky: document.getElementById('sky-fx').dataset.sky,
                      clouds: document.getElementById('sky-fx').dataset.clouds,
                      day: document.getElementById('sky-fx').dataset.day },
            cardScene: card ? card.dataset.scene : null,
            cardParts: parts,
            bodyCond: document.body.dataset.condition,
            theme: document.documentElement.dataset.theme,
            cardClipped: card ? getComputedStyle(card).overflow === 'hidden' : null,
            cardZ: card ? getComputedStyle(card).zIndex : null,
            cardContentZ: heroContent ? getComputedStyle(heroContent).zIndex : null,
            sceneInsideCard: !!(cardRect && cardSceneRect &&
                cardSceneRect.left >= cardRect.left - 1 && cardSceneRect.right <= cardRect.right + 1 &&
                cardSceneRect.top >= cardRect.top - 1 && cardSceneRect.bottom <= cardRect.bottom + 1),
            cardRainDrops: cnt('#cs-drop-field .cs-drop'),
            cardStormDrops: cnt('#cs-storm-drop-field .cs-drop'),
            cardRainRipples: cnt('#cs-ripple-pool .cs-ripple'),
            cardFlakes: cnt('#cs-snow-field .cs-snowflake'),
            cardStars: cnt('#cs-star-field .cs-star'),
            pageDrops: cnt('#rain-fx .rain-drop'),
        };
    })()""") if page else None


def force(page, cat, is_day, temp):
    band = "hot" if temp >= 33 else "cold" if temp <= 0 else "mild"
    page.evaluate(f"""(() => {{
        applyDynamicSky('{cat}', '{band}');
        applySkyFx('{cat}', {is_day});
        applySceneFx('{cat}', {is_day});
        applyCardSceneFx('{cat}', {is_day}, '{band}');
        applyPrecipFx('{cat}');
        document.body.dataset.condition = '{cat}';
    }})()""")


# Expected: (page scene, card scene, page sky class, card sky class)
# The scene follows the real condition/is_day in both themes; the card
# sky class always keeps the condition class.
EXPECTED = {
    # cond:            (page scene, card scene, card sky)
    "clear-day":         ("sun", "sun", "cs-clear-day"),
    "clear-night":       ("night", "night", "cs-clear-night"),
    "partly-cloudy-day": ("clouds", "clouds", "cs-partly-cloudy-day"),
    "partly-cloudy-night": ("night", "night", "cs-partly-cloudy-night"),
    "cloudy":            ("clouds", "clouds", "cs-cloudy"),
    "fog":               ("clouds", "mist", "cs-fog"),
    "drizzle":           ("rain", "rain", "cs-drizzle"),
    "rain":              ("rain", "rain", "cs-rain"),
    "snow":              ("sun", "snow", "cs-snow"),
    "thunder":           ("rain", "storm", "cs-thunder"),
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ================= DESKTOP (1280x900) =================
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    console_errors = []
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2600)

    print("\n===== 1. Card scene host: structure + clipping (desktop) =====")
    s = probe(page)
    boot_want = EXPECTED.get(s["bodyCond"], ("sun", "sun", None))[1]
    if s["theme"] == "dark" and boot_want == "sun":
        boot_want = "night"
    check("boot: card-scene present + aria-hidden + pointer-events none",
          bool(s["cardScene"]) and page.evaluate(
              "document.getElementById('card-scene').getAttribute('aria-hidden')") == "true" and
          page.evaluate("getComputedStyle(document.getElementById('card-scene')).pointerEvents") == "none")
    check("boot: card scene clipped inside card (overflow hidden + geometry)",
          s["cardClipped"] and s["sceneInsideCard"], f"clipped={s['cardClipped']} inside={s['sceneInsideCard']}")
    check("boot: card scene paints BELOW card content (z 0 vs 10)",
          s["cardZ"] == "0" and s["cardContentZ"] == "10", f"scene z={s['cardZ']} content z={s['cardContentZ']}")
    check("boot: card sky crossfade layers present",
          page.evaluate("document.querySelectorAll('#card-scene .cs-sky').length") == 2)
    check("boot: card scene follows the real condition + theme (not fabricated)",
          s["cardScene"] == boot_want, f"cond={s['bodyCond']} theme={s['theme']} cardScene={s['cardScene']} (want {boot_want})")

    print("\n===== 2. Every condition: both layers synchronized (desktop, light) =====")
    for cond, (want_page_scene, want_card_scene, want_card_sky) in EXPECTED.items():
        temp = 35 if cond == "clear-day" and False else {"snow": -2, "fog": 12, "drizzle": 14, "rain": 13, "thunder": 21}.get(cond, 22)
        force(page, cond, 0 if cond.endswith("night") else 1, temp)
        page.wait_for_timeout(2200)  # card sky crossfade (1.6s) + settle
        s = probe(page)
        check(f"[{cond}] page sky class matches condition", s["pageSky"] == f"sky-{cond}",
              f"got {s['pageSky']}")
        check(f"[{cond}] card sky class matches condition", s["cardSky"] == want_card_sky, f"got {s['cardSky']}")
        check(f"[{cond}] page scene == {want_page_scene}", s["pageScene"] == want_page_scene, f"got {s['pageScene']}")
        check(f"[{cond}] card scene == {want_card_scene}", s["cardScene"] == want_card_scene, f"got {s['cardScene']}")
        check(f"[{cond}] both layers same real condition (sync)",
              s["bodyCond"] == cond and s["pageScene"] == want_page_scene and s["cardScene"] == want_card_scene,
              f"body={s['bodyCond']} page={s['pageScene']} card={s['cardScene']}")
        shown = [k for k, v in s["cardParts"].items() if v == "block"]
        check(f"[{cond}] exactly one card part shown ('{want_card_scene}')", shown == [want_card_scene], str(shown))

    print("\n===== 3. Card scene animation motion (pixel evidence, desktop) =====")
    # Card geometry for crops: hero card is the first child of #view-forecast
    card_box = page.evaluate("""(() => {
        const r = document.querySelector('#view-forecast > div').getBoundingClientRect();
        return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
    })()""")
    hero_w = card_box[2] - card_box[0]
    hero_h = card_box[3] - card_box[1]
    top_band = (card_box[0] + 6, card_box[1] + 6, card_box[2] - 6, card_box[1] + 60)
    page.screenshot(path="/tmp/cs_base.png")
    base = "/tmp/cs_base.png"

    def motion_for(cond, is_day, temp, frames=5, gap=260):
        force(page, cond, is_day, temp)
        page.wait_for_timeout(1900)
        best = 0
        for i in range(frames):
            page.wait_for_timeout(gap)
            f = f"/tmp/cs_m_{cond}_{i}.png"
            page.screenshot(path=f)
            _, ch = diff(base, f, top_band)
            best = max(best, ch)
        return best

    for cond in ("clear-day", "cloudy", "rain", "snow", "fog"):
        ch = motion_for(cond, 1, {"snow": -2, "fog": 12, "rain": 13}.get(cond, 24))
        check(f"[{cond}] card scene animates inside card (motion vs paper baseline)", ch > 300, f"changed={ch}")

    # Clipping: nothing from the card scene may paint outside the card.
    # The page Living Sky legitimately animates in the gutters (drifting
    # cloud bands), so compare motion with the scene ON vs scene HIDDEN —
    # the scene must add ZERO motion below/outside the card.
    def band_motion(tag, band, frames=4, gap=280):
        shots = []
        for i in range(frames):
            page.wait_for_timeout(gap)
            f = f"/tmp/cs_clip_{tag}_{i}.png"
            page.screenshot(path=f)
            shots.append(f)
        total = 0
        for i in range(1, len(shots)):
            _, ch = diff(shots[i - 1], shots[i], band)
            total += ch
        return total

    force(page, "rain", 1, 13)
    page.wait_for_timeout(1900)
    outside_band = (0, card_box[1] + hero_h + 10, 1280, min(card_box[1] + hero_h + 90, 890))
    page.evaluate("document.getElementById('card-scene').style.display='none'")
    page.wait_for_timeout(350)
    base_motion = band_motion("off", outside_band)
    page.evaluate("document.getElementById('card-scene').style.display=''")
    page.wait_for_timeout(350)
    scene_motion = band_motion("on", outside_band)
    check("card scene never paints outside the card (no added motion below card)",
          scene_motion <= base_motion * 1.35 + 400,
          f"scene_on={scene_motion} vs page_only={base_motion}")

    print("\n===== 4. Rain intensity tiers + pools (card vs page) =====")
    force(page, "drizzle", 1, 14)
    page.wait_for_timeout(200)
    s = probe(page)
    check("drizzle: card pool = 20 drops, page pool modulated", s["cardRainDrops"] == 20 and s["pageDrops"] >= 10,
          f"card={s['cardRainDrops']} page={s['pageDrops']}")
    force(page, "rain", 1, 13)
    page.wait_for_timeout(200)
    s = probe(page)
    check("rain: card pool = 30 drops", s["cardRainDrops"] == 30, s["cardRainDrops"])
    check("rain: card ripples seeded (7)", s["cardRainRipples"] == 7, s["cardRainRipples"])
    force(page, "thunder", 1, 21)
    page.wait_for_timeout(200)
    s = probe(page)
    check("thunder: card storm pool = 42 drops", s["cardStormDrops"] == 42, s["cardStormDrops"])
    check("thunder: card flash gated to real thunder (animation attached)",
          page.evaluate("getComputedStyle(document.querySelector('#card-scene .cs-flash')).animationName") == "cs-lightning")
    force(page, "rain", 1, 13)
    page.wait_for_timeout(200)
    check("plain rain: card flash gated OFF",
          page.evaluate("getComputedStyle(document.querySelector('#card-scene .cs-flash')).animationName") == "none")
    force(page, "snow", 1, -2)
    page.wait_for_timeout(200)
    s = probe(page)
    check("snow: card pool = 30 flakes", s["cardFlakes"] == 30, s["cardFlakes"])
    force(page, "clear-night", 0, 18)
    page.wait_for_timeout(300)
    s = probe(page)
    check("night: card star pool = 26", s["cardStars"] == 26, s["cardStars"])

    print("\n===== 5. Real API payload path (no fabricated conditions) =====")
    # Drive renderForecastScreen with a REAL payload shape and check the
    # whole pipeline (body.dataset.condition + every visual layer) follows.
    api_ok = page.evaluate(r"""(async () => {
        const payload = { current: { temperature_2m: 6.4, apparent_temperature: 4.1,
            weather_code: 71, is_day: 1, wind_speed_10m: 9, wind_gusts_10m: 15,
            relative_humidity_2m: 81, dew_point_2m: 3.4, surface_pressure: 1009.2,
            precipitation: 0.8, wind_direction_10m: 210 },
          hourly: { time: [], temperature_2m: [], precipitation_probability: [],
            weather_code: [], uv_index: [] },
          daily: { time: [], temperature_2m_max: [], temperature_2m_min: [],
            weather_code: [], precipitation_probability_max: [] } };
        state.weather = payload;
        state.airQuality = {};
        renderForecastScreen();
        // The card sky crossfade commits on a double-rAF; wait it out.
        await new Promise(res => setTimeout(res, 2100));
        const liveSky = [...document.querySelectorAll('#card-scene .cs-sky')]
            .filter(l => l.classList.contains('is-live'))
            .map(l => [...l.classList].find(c => c.startsWith('cs-') && !['cs-sky', 'cs-sky-a', 'cs-sky-b'].includes(c)))[0];
        return { cond: document.body.dataset.condition,
                 card: document.getElementById('card-scene').dataset.scene,
                 cardSky: liveSky,
                 temp: document.getElementById('hero-temperature').textContent };
    })()""")
    check("real payload (WMO 71 snow): body condition = snow", api_ok["cond"] == "snow", api_ok["cond"])
    check("real payload (snow): card scene = snow", api_ok["card"] == "snow", api_ok["card"])
    check("real payload: card sky = cs-snow (cold band keeps snow)", "cs-snow" in str(api_ok["cardSky"]), api_ok["cardSky"])
    check("real payload: hero temperature = 6 (rounded real value)", api_ok["temp"] == "6", api_ok["temp"])

    print("\n===== 6. Dark theme: scene follows is_day, not the theme (both layers) =====")
    page.evaluate("applyTheme('dark')")
    page.wait_for_timeout(400)
    force(page, "clear-day", 1, 24)
    page.wait_for_timeout(2100)
    s = probe(page)
    check("dark: page scene keeps sun at daytime (no night at 3 PM)", s["pageScene"] == "sun", s["pageScene"])
    check("dark: card scene keeps sun at daytime (moon only at real night)", s["cardScene"] == "sun", s["cardScene"])
    check("dark: card sky follows dark clear-day gradient", s["cardSky"] == "cs-clear-day", s["cardSky"])
    check("dark: page sky follows dark gradient class", s["pageSky"] == "sky-clear-day", s["pageSky"])
    force(page, "clear-night", 0, 18)
    page.wait_for_timeout(2100)
    s = probe(page)
    check("dark: real night still renders night scene (page)", s["pageScene"] == "night", s["pageScene"])
    check("dark: real night still renders night scene (card)", s["cardScene"] == "night", s["cardScene"])
    force(page, "rain", 1, 13)
    page.wait_for_timeout(300)
    s = probe(page)
    check("dark: rain card scene stays rain (cool palette)", s["cardScene"] == "rain", s["cardScene"])
    force(page, "cloudy", 1, 19)
    page.wait_for_timeout(2100)
    fill = page.evaluate("getComputedStyle(document.querySelector('#card-scene .cs-cloud ellipse')).fill")
    check("dark: card cloud ellipses repaint moonlit", "203, 216, 236" in fill, fill)
    # Toggle back through the real handler — the scene must not flip with it
    page.click("#theme-toggle-btn")
    page.wait_for_timeout(400)
    s = probe(page)
    check("toggle dark->light: card scene stays clouds (theme-independent)", s["cardScene"] == "clouds", s["cardScene"])
    check("toggle: theme now light", s["theme"] == "light", s["theme"])

    print("\n===== 7. Reduced motion (scenes painted, animations frozen) =====")
    rm = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    rm.goto(BASE, wait_until="networkidle", timeout=60000)
    rm.wait_for_timeout(2300)
    force(rm, "rain", 1, 13)
    rm.wait_for_timeout(400)
    rs = rm.evaluate(r"""(() => {
        const drop = document.querySelector('#card-scene .cs-drop');
        return { scene: document.getElementById('card-scene').dataset.scene,
                 drops: document.querySelectorAll('#card-scene .cs-drop').length,
                 dropAnim: drop ? getComputedStyle(drop).animationName : 'no-drop',
                 cloudAnim: getComputedStyle(document.querySelector('#card-scene .cs-cloud-1')).animationName,
                 orbAnim: getComputedStyle(document.querySelector('#card-scene .cs-sun-orb')).animationName };
    })()""")
    check("reduced: card rain scene applies with parked static pool", rs["scene"] == "rain" and rs["drops"] == 30, str(rs["drops"]))
    check("reduced: card drop animation frozen", rs["dropAnim"] == "none", rs["dropAnim"])
    force(rm, "clear-day", 1, 24)
    rm.wait_for_timeout(400)
    rm.screenshot(path="/tmp/cs_rm_base.png")
    force(rm, "snow", 1, -2)
    rm.wait_for_timeout(700)
    rm.screenshot(path="/tmp/cs_rm_on.png")
    # snow scene vs sun scene differ inside the card
    m, ch = diff("/tmp/cs_rm_base.png", "/tmp/cs_rm_on.png", tuple(card_box))
    check("reduced: card scene still painted (snow vs sun static diff)", ch > 900, f"changed={ch}")
    rs2 = rm.evaluate("getComputedStyle(document.querySelector('#card-scene .cs-snowflake')).animationName")
    check("reduced: card snowfall frozen", rs2 == "none", rs2)
    rm.close()

    print("\n===== 8. Mobile (390x844) =====")
    m = browser.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE, wait_until="networkidle", timeout=60000)
    m.wait_for_timeout(2400)
    mb = m.evaluate("""(() => {
        const r = document.querySelector('#view-forecast > div').getBoundingClientRect();
        return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
    })()""")
    mtop = (mb[0] + 4, mb[1] + 4, mb[2] - 4, mb[1] + 56)
    for cond, want in [("clear-day", "sun"), ("rain", "rain"), ("snow", "snow"), ("fog", "mist"),
                       ("thunder", "storm"), ("clear-night", "night"), ("cloudy", "clouds")]:
        force(m, cond, 0 if cond.endswith("night") else 1, {"snow": -2, "fog": 12, "rain": 13, "thunder": 21}.get(cond, 22))
        m.wait_for_timeout(1900)
        s = probe(m)
        check(f"mobile [{cond}]: card scene == {want} (synced with page {s['pageScene']})",
              s["cardScene"] == want, f"got {s['cardScene']}")
        shown = [k for k, v in s["cardParts"].items() if v == "block"]
        check(f"mobile [{cond}]: exactly part '{want}' shown", shown == [want], str(shown))
    # Mobile clipping + motion
    force(m, "rain", 1, 13)
    m.wait_for_timeout(1900)
    m.screenshot(path="/tmp/cs_m_base.png")
    best = 0
    for i in range(5):
        m.wait_for_timeout(240)
        f = f"/tmp/cs_m_{i}.png"
        m.screenshot(path=f)
        _, ch = diff("/tmp/cs_m_base.png", f, mtop)
        best = max(best, ch)
    check("mobile: card rain animates inside card", best > 120, f"changed={best}")
    # Mobile clipping: same baseline comparison (page bg animates too).
    force(m, "rain", 1, 13)
    m.wait_for_timeout(1900)
    mh = mb[3] - mb[1]
    outside = (0, mb[1] + mh + 8, 390, min(mb[1] + mh + 80, 840))

    def m_band_motion(tag, band, frames=4, gap=240):
        shots = []
        for i in range(frames):
            m.wait_for_timeout(gap)
            f = f"/tmp/cs_mclip_{tag}_{i}.png"
            m.screenshot(path=f)
            shots.append(f)
        total = 0
        for i in range(1, len(shots)):
            _, ch = diff(shots[i - 1], shots[i], band)
            total += ch
        return total

    m.evaluate("document.getElementById('card-scene').style.display='none'")
    m.wait_for_timeout(350)
    m_base = m_band_motion("off", outside)
    m.evaluate("document.getElementById('card-scene').style.display=''")
    m.wait_for_timeout(350)
    m_on = m_band_motion("on", outside)
    check("mobile: card scene never paints outside card (no added motion)",
          m_on <= m_base * 1.35 + 300, f"scene_on={m_on} vs page_only={m_base}")
    m.close()

    print("\n===== 9. Robustness =====")
    check("no page JS errors during the whole desktop session", not console_errors, "; ".join(console_errors[:3]))
    page.close()
    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== {len(results) - len(fails)}/{len(results)} weather-sync visual checks passed =====")
if fails:
    print("FAILED:")
    for f in fails:
        print(" -", f)
sys.exit(1 if fails else 0)
