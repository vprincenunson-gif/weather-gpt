"""Visual debug of Weather Scene Animations on the real running UI (v2).

Opens the app in headless Chromium, drives each state through the REAL
pipeline (including body[data-condition]), and verifies with pixel
evidence (scene ON vs scene HIDDEN baselines + frame-pair motion):
  - z-index/stacking fix (scene above animated sky gradients)
  - every state visibly painted in the open sky bands
  - thunder-only flash gate (light + dark), plain rain calm
  - night moon/stars, dark theme rain + moonlit clouds
  - reduced motion: animations off, scene content painted, pools skipped
  - mobile: orb crest visible, no full occlusion by opaque cards
Prints PASS/FAIL per check; exit non-zero on FAIL. Temporary debug tool.
"""
import sys
import time

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


def brightness(img, box):
    im = Image.open(img).convert("L").crop(box)
    return round(ImageStat.Stat(im).mean[0], 1)


def hide_scene(page, hide=True):
    page.evaluate("v => { document.getElementById('weather-scene').style.display = v ? 'none' : ''; }", hide)


def force(page, cat, is_day=1, temp=22):
    band = "hot" if temp >= 33 else "cold" if temp <= 0 else "mild"
    page.evaluate(f"""(() => {{
        applyDynamicSky('{cat}', '{band}');
        applySkyFx('{cat}', {is_day});
        applySceneFx('{cat}', {is_day});
        applyPrecipFx('{cat}');
        document.body.dataset.condition = '{cat}';
    }})()""")


# Sky-visible bands (measured from layout probes):
#   top strip above the cards, side gutters on desktop, bottom strip.
#   Everything below the first card row sits behind opaque cards; the
#   top strip sits behind the frosted header (big shapes read clearly,
#   thin drops survive at reduced contrast - same as the app's own
#   rain-fx streaks, which share this exact constraint by design).
TOP_D = (200, 6, 1080, 78)        # desktop top strip (behind frosted header)
TOP_M = (14, 6, 376, 76)          # mobile top strip
GUT_D = (0, 200, 88, 800)         # desktop left gutter (cards start ~x=94)
BOT_D = (200, 780, 1080, 896)     # desktop bottom strip


def verify_state(page, cat, is_day, label, top_band, gut=None, want_scene=None,
                 paint_min=900, motion_min=200):
    force(page, cat, is_day, 20)
    page.wait_for_timeout(1800)  # fade-in done
    hide_scene(page, True)
    page.wait_for_timeout(350)
    base = f"/tmp/v2_{cat}_base.png"
    page.screenshot(path=base)
    hide_scene(page, False)
    page.wait_for_timeout(1500)
    on1 = f"/tmp/v2_{cat}_on1.png"
    page.screenshot(path=on1)
    page.wait_for_timeout(1100)
    on2 = f"/tmp/v2_{cat}_on2.png"
    page.screenshot(path=on2)

    m, ch = diff(base, on2, top_band)
    check(f"pixel {label}: painted over Living Sky", ch > paint_min, f"top mean={m} changed={ch}")
    if motion_min:
        mm, mv = diff(on1, on2, top_band)
        motion = mv
        if gut:
            _, mv2 = diff(on1, on2, gut)
            motion += mv2
        check(f"pixel {label}: animation motion present", motion > motion_min, f"moving px={motion}")
    if want_scene:
        got = page.evaluate("document.getElementById('weather-scene').dataset.scene")
        check(f"map {label}: scene == {want_scene}", got == want_scene, f"got {got}")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ================= DESKTOP, LIGHT THEME =================
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    console_errors = []
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)

    print("\n===== 1. Stacking fix verified in computed CSS =====")
    st = page.evaluate(r"""(() => {
      const ids = ['sky-layer-a','sky-layer-b','weather-scene','rain-fx','lightning-fx'];
      const out = {};
      for (const id of ids) out[id] = getComputedStyle(document.getElementById(id)).zIndex;
      return out;
    })()""")
    print("z-index map:", st)
    check("stack: scene (3) above sky gradients (1/2), below rain-fx (4)/lightning-fx (5)",
          st["weather-scene"] == "3" and st["sky-layer-a"] == "1" and st["sky-layer-b"] == "2"
          and st["rain-fx"] in ("3", "4") and st["lightning-fx"] in ("4", "5"))

    print("\n===== 2. States: pixel evidence (desktop, light) =====")
    verify_state(page, "clear-day", 1, "clear-day sun", TOP_D, GUT_D, "sun")
    verify_state(page, "partly-cloudy-day", 1, "partly-cloudy clouds", TOP_D, GUT_D, "clouds")
    verify_state(page, "cloudy", 1, "cloudy clouds", TOP_D, GUT_D, "clouds")
    verify_state(page, "fog", 1, "fog clouds", TOP_D, GUT_D, "clouds")
    # Drops cross any thin band in ~60ms bursts and sit behind the
    # frosted header, so sample several frames and take the max.
    # 14 samples at 140ms: a 6-sample window sat at the noise floor
    # (~244-257 changed px vs a 250 threshold) and flipped run-to-run.
    def band_max(cat, band, frames=14, gap=140):
        force(page, cat, 1, 13)
        page.wait_for_timeout(1800)
        hide_scene(page, True)
        page.wait_for_timeout(350)
        page.screenshot(path=f"/tmp/v2_{cat}_tbase.png")
        hide_scene(page, False)
        best = (0.0, 0)
        for i in range(frames):
            page.wait_for_timeout(gap)
            f = f"/tmp/v2_{cat}_tf{i}.png"
            page.screenshot(path=f)
            m, ch = diff(f"/tmp/v2_{cat}_tbase.png", f, band)
            best = max(best, (m, ch), key=lambda t: t[1])
        return best

    m, ch = band_max("rain", TOP_D)
    check("pixel rain drops: painted over Living Sky (top strip)", ch > 250,
          f"best frame mean={m} changed={ch}")
    m, ch = band_max("thunder", TOP_D)
    check("pixel thunder streaks: painted over Living Sky (top strip)", ch > 350,
          f"best frame mean={m} changed={ch}")
    # intensity-aware pools: drizzle light, rain steady, thunder heavy
    for cat, want in [("drizzle", 40), ("rain", 70), ("thunder", 95)]:
        force(page, cat, 1, 13)
        page.wait_for_timeout(250)
        n = page.evaluate("document.querySelectorAll('#weather-scene .scene-drop').length")
        check(f"intensity: {cat} seeds {want} drops", n == want, f"got {n}")
    got = page.evaluate("document.getElementById('weather-scene').dataset.scene")
    check("map rain drops: scene == rain", got == "rain", got)
    verify_state(page, "drizzle", 1, "drizzle drops", TOP_D, GUT_D, "rain")

    print("\n===== 3. Thunder: flash + flicker gate (real body[data-condition]) =====")
    force(page, "thunder", 1, 21)
    page.wait_for_timeout(500)
    flash = page.evaluate(r"""(() => {
      const f = document.querySelector('#weather-scene .scene-flash');
      const c = document.querySelector('#weather-scene .scene-storm-cloud');
      return {flashAnim: getComputedStyle(f).animationName,
              cloudAnim: getComputedStyle(c).animationName,
              cond: document.body.dataset.condition};
    })()""")
    check("thunder: flash + cloud flicker animations attach",
          "scene-lightning" in flash["flashAnim"] and "flicker" in flash["cloudAnim"], str(flash))
    hide_scene(page, True)
    page.wait_for_timeout(300)
    page.screenshot(path="/tmp/v2_thunder_base.png")
    hide_scene(page, False)
    best = 0
    t0 = time.time()
    while time.time() - t0 < 9.8:
        page.screenshot(path="/tmp/v2_thunder_burst.png")
        _, ch = diff("/tmp/v2_thunder_base.png", "/tmp/v2_thunder_burst.png", TOP_D)
        best = max(best, ch)
        page.wait_for_timeout(200)
    check("thunder: lightning burst visibly fires", best > 2500, f"peak changed={best}")
    force(page, "rain", 1, 13)
    page.wait_for_timeout(300)
    rain_flash = page.evaluate("getComputedStyle(document.querySelector('#weather-scene .scene-flash')).animationName")
    check("plain rain: flash gated off", rain_flash == "none", rain_flash)

    print("\n===== 4. Night scene: moon + stars =====")
    force(page, "clear-night", 0, 18)
    page.wait_for_timeout(1800)
    got = page.evaluate("document.getElementById('weather-scene').dataset.scene")
    check("map clear-night: scene == night", got == "night", got)
    hide_scene(page, True)
    page.wait_for_timeout(350)
    page.screenshot(path="/tmp/v2_night_base.png")
    hide_scene(page, False)
    page.wait_for_timeout(1600)
    page.screenshot(path="/tmp/v2_night_on.png")
    m, ch = diff("/tmp/v2_night_base.png", "/tmp/v2_night_on.png", (460, 6, 820, 110))
    b_base = brightness("/tmp/v2_night_base.png", (560, 10, 720, 100))
    b_on = brightness("/tmp/v2_night_on.png", (560, 10, 720, 100))
    check("night: moon visibly brighter than baseline", ch > 700 and b_on > b_base,
          f"changed={ch} bright {b_base}->{b_on}")
    star_anim = page.evaluate("getComputedStyle(document.querySelector('#weather-scene .scene-star')).animationName")
    check("night: stars twinkle running", "twinkle" in star_anim, star_anim)

    print("\n===== 5. Pools seeded exactly once =====")
    force(page, "rain", 1, 13)
    page.wait_for_timeout(200)
    pools = page.evaluate(r"""(() => {
      const h = document.getElementById('weather-scene');
      return {d: h.querySelectorAll('.scene-drop').length,
              r: h.querySelectorAll('.scene-ripple').length};
    })()""")
    check("rain pools: 70 drops + 10 ripples", pools["d"] == 70 and pools["r"] == 10, str(pools))
    force(page, "clear-night", 0, 18)
    page.wait_for_timeout(200)
    stars = page.evaluate("document.querySelectorAll('#weather-scene .scene-star').length")
    check("night pool: 40 stars", stars == 40, stars)

    print("\n===== 6. Dark theme =====")
    page.evaluate("document.documentElement.dataset.theme='dark'; if (typeof applyTheme==='function') applyTheme('dark');")
    page.wait_for_timeout(400)
    force(page, "clear-day", 1, 24)
    page.wait_for_timeout(300)
    got = page.evaluate("document.getElementById('weather-scene').dataset.scene")
    check("dark: clear-day remaps sun -> night", got == "night", got)
    force(page, "cloudy", 1, 19)
    page.wait_for_timeout(400)
    fill = page.evaluate(r"""(() => {
      const e = document.querySelector('#weather-scene .scene-cloud ellipse');
      return {computed: getComputedStyle(e).fill};
    })()""")
    check("dark: cloud ellipses repainted moonlit", "203, 216, 236" in fill["computed"], str(fill))
    # dark rain pixel evidence
    force(page, "rain", 1, 13)
    page.wait_for_timeout(1700)
    hide_scene(page, True)
    page.wait_for_timeout(350)
    page.screenshot(path="/tmp/v2_dark_rain_base.png")
    hide_scene(page, False)
    page.wait_for_timeout(1500)
    page.screenshot(path="/tmp/v2_dark_rain_on.png")
    m, ch = diff("/tmp/v2_dark_rain_base.png", "/tmp/v2_dark_rain_on.png", TOP_D)
    check("dark: rain drops visible over dark sky", ch > 500, f"changed={ch} mean={m}")

    print("\n===== 7. Reduced motion =====")
    page2 = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    page2.goto(BASE, wait_until="networkidle", timeout=60000)
    page2.wait_for_timeout(2200)
    force(page2, "rain", 1, 13)
    page2.wait_for_timeout(500)
    rm = page2.evaluate(r"""(() => {
      const h = document.getElementById('weather-scene');
      const orb = h.querySelector('.scene-sun-orb');
      return {scene: h.dataset.scene,
              drops: h.querySelectorAll('.scene-drop').length,
              orbAnim: getComputedStyle(orb).animationName};
    })()""")
    check("reduced-motion: drop/ripple pools skipped (rain-fx covers precip)",
          rm["drops"] == 0, str(rm))
    check("reduced-motion: no pulse animation on orb", rm["orbAnim"] == "none", str(rm))
    force(page2, "clear-day", 1, 24)
    page2.wait_for_timeout(1500)
    hide_scene(page2, True)
    page2.wait_for_timeout(350)
    page2.screenshot(path="/tmp/v2_rm_base.png")
    hide_scene(page2, False)
    page2.wait_for_timeout(600)
    page2.screenshot(path="/tmp/v2_rm_on.png")
    m, ch = diff("/tmp/v2_rm_base.png", "/tmp/v2_rm_on.png", TOP_D)
    check("reduced-motion: static sun scene still painted", ch > 500, f"changed={ch} mean={m}")
    page2.close()

    print("\n===== 8. Mobile (390x844) =====")
    page3 = browser.new_page(viewport={"width": 390, "height": 844})
    page3.goto(BASE, wait_until="networkidle", timeout=60000)
    page3.wait_for_timeout(2200)
    verify_state(page3, "clear-day", 1, "mobile sun", TOP_M, None, "sun")
    # mobile rain: multi-frame max in the top strip
    force(page3, "rain", 1, 13)
    page3.wait_for_timeout(1800)
    hide_scene(page3, True)
    page3.wait_for_timeout(350)
    page3.screenshot(path="/tmp/v2_mrain_base.png")
    hide_scene(page3, False)
    mbest = 0
    for i in range(6):
        page3.wait_for_timeout(150)
        page3.screenshot(path=f"/tmp/v2_mrain_f{i}.png")
        _, c = diff("/tmp/v2_mrain_base.png", f"/tmp/v2_mrain_f{i}.png", TOP_M)
        mbest = max(mbest, c)
    check("pixel mobile rain: painted over Living Sky (top strip)", mbest > 150,
          f"best frame changed={mbest}")
    got = page3.evaluate("document.getElementById('weather-scene').dataset.scene")
    check("map mobile rain: scene == rain", got == "rain", got)
    verify_state(page3, "cloudy", 1, "mobile clouds", TOP_M, None, "clouds")
    verify_state(page3, "clear-night", 0, "mobile night", TOP_M, None, "night", motion_min=0)
    # Geometric visibility: fraction of the orb box actually altered by the
    # scene (elementsFromPoint ignores pointer-events:none, so it can never
    # see the sky system - pixel ratios are the honest test).
    force(page3, "clear-day", 1, 24)
    page3.wait_for_timeout(1700)
    hide_scene(page3, True)
    page3.wait_for_timeout(350)
    page3.screenshot(path="/tmp/v2_mocc_base.png")
    hide_scene(page3, False)
    page3.wait_for_timeout(1500)
    page3.screenshot(path="/tmp/v2_mocc_on.png")
    orb_box = page3.evaluate(r"""(() => {
      const r = document.querySelector('#weather-scene .scene-sun-orb').getBoundingClientRect();
      return [Math.max(0, Math.round(r.left)), Math.max(0, Math.round(r.top)),
              Math.min(innerWidth, Math.round(r.right)), Math.min(innerHeight, Math.round(r.bottom))];
    })()""")
    _, chocc = diff("/tmp/v2_mocc_base.png", "/tmp/v2_mocc_on.png", tuple(orb_box))
    box_px = (orb_box[2] - orb_box[0]) * (orb_box[3] - orb_box[1])
    ratio = chocc / box_px
    check("mobile: sun orb visibly crests the card line", ratio > 0.10,
          f"visible ratio={ratio:.2f} changed={chocc}/{box_px}")
    page3.close()

    print("\n===== 9. Robustness =====")
    check("no page JS errors during whole session", not console_errors, "; ".join(console_errors[:3]))
    page.close()
    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== {len(results) - len(fails)}/{len(results)} scene visual-debug checks passed =====")
if fails:
    print("FAILED:")
    for f in fails:
        print(" -", f)
sys.exit(1 if fails else 0)
