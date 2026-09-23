"""Capture human-viewable Chromium previews of the weather-synchronized visual system.

The UI now has TWO synchronized layers driven by the SAME REAL condition:
  - page Living Sky (gradient + clouds/rain/lightning + illustrated scene)
  - in-card hero scene (#card-scene, clipped inside the hero card)

For each state (sunny / cloudy / rain / thunder / fog-mist / snow / night)
on the REAL running UI, captures:
  - full-viewport screenshot
  - 2.5x zoomed page-sky crop (top 150px band) + motion pair
  - 2.5x zoomed CARD-scene crop (inside the hero card) + motion pair
  - thunder: polls up to 10s and saves the lightning-flash peak frame
  - mobile (390px): sun + rain full + card-scene zooms
Also builds labeled contact sheets stacking all page-sky and card-scene
zooms. Output: Weather-Report-main/scene-previews/.
"""
import os
import time

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scene-previews")
os.makedirs(OUT, exist_ok=True)
TMP = "/tmp"
SKY = (0, 0, 1280, 150)   # open sky band on desktop (orb crest + cloud strip)


def zoom(png, box, factor, path):
    im = Image.open(png).convert("RGB").crop(box)
    im = im.resize((int(im.width * factor), int(im.height * factor)), Image.LANCZOS)
    im.save(path)
    return im.size


def label_bar(text, width, h=52):
    bar = Image.new("RGB", (width, h), (17, 24, 38))
    d = ImageDraw.Draw(bar)
    try:
        f = ImageFont.load_default(size=30)
    except TypeError:
        f = ImageFont.load_default()
    d.text((14, h // 2), text, anchor="lm", fill=(235, 240, 248), font=f)
    return bar


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


# name, condition, is_day, temp, caption
STATES = [
    ("01_sunny", "clear-day", 1, 24, "SUNNY - page 'sun' + card sun orb, both live"),
    ("02_cloudy", "cloudy", 1, 19, "CLOUDY - page clouds + card cloud drift"),
    ("03_rain", "rain", 1, 13, "RAIN - page rain-fx + card storm cloud & drops"),
    ("04_thunder", "thunder", 1, 21, "THUNDER - storm sky + card flash (lightning peak)"),
    ("05_fog", "fog", 1, 12, "FOG - page clouds + card mist veils"),
    ("06_snow", "snow", 1, -2, "SNOW - page sparkle + card snowfall"),
    ("07_night", "clear-night", 0, 18, "NIGHT - page moon/stars + card moon/stars"),
]

rows_sky = []
rows_card = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)

    # Hero-card geometry for the card-scene crops
    cb = page.evaluate("""(() => {
        const r = document.querySelector('#view-forecast > div').getBoundingClientRect();
        return {l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom)};
    })()""")
    CARD = (cb["l"] + 2, cb["t"] + 2, cb["r"] - 2, cb["t"] + 150)  # upper card band

    for name, cat, is_day, temp, caption in STATES:
        force(page, cat, is_day, temp)
        page.wait_for_timeout(2300)  # 1.6s card fade + 1.2s scene fade settle

        full = f"{TMP}/{name}_full.png"
        page.screenshot(path=full)
        os.replace(full, f"{OUT}/{name}_full.png")

        f1 = f"{TMP}/{name}_m1.png"
        page.screenshot(path=f1)
        gap = 2000 if "cloudy" in name else 350
        page.wait_for_timeout(gap)
        f2 = f"{TMP}/{name}_m2.png"
        page.screenshot(path=f2)

        zoom(f1, SKY, 2.5, f"{TMP}/{name}_z1.png")
        zoom(f2, SKY, 2.5, f"{TMP}/{name}_z2.png")
        zoom(f1, CARD, 2.5, f"{TMP}/{name}_c1.png")
        zoom(f2, CARD, 2.5, f"{TMP}/{name}_c2.png")

        z1 = Image.open(f"{TMP}/{name}_z1.png")
        z2 = Image.open(f"{TMP}/{name}_z2.png")
        pair = Image.new("RGB", (z1.width, z1.height * 2 + 8), (10, 14, 22))
        pair.paste(z1, (0, 0))
        pair.paste(z2, (0, z1.height + 8))
        pair.save(f"{OUT}/{name}_sky_zoom_motion_pair.png")

        c1 = Image.open(f"{TMP}/{name}_c1.png")
        c2 = Image.open(f"{TMP}/{name}_c2.png")
        cpair = Image.new("RGB", (c1.width, c1.height * 2 + 8), (10, 14, 22))
        cpair.paste(c1, (0, 0))
        cpair.paste(c2, (0, c1.height + 8))
        cpair.save(f"{OUT}/{name}_card_zoom_motion_pair.png")

        w, h = z1.size
        rows_sky.append((caption + " | PAGE SKY", z1))
        rows_card.append((caption + " | CARD SCENE", c1))
        print(f"captured {name}: full + sky zoom + card zoom + motion pairs (gap {gap}ms)")

        if name == "04_thunder":
            # poll for the lightning burst (~7.3s into the 8s cycle)
            best, best_png, t0 = -1.0, None, time.time()
            i = 0
            while time.time() - t0 < 10.5:
                fp = f"{TMP}/{name}_flash_{i}.png"
                page.screenshot(path=fp)
                im = Image.open(fp).convert("L").crop(SKY)
                st = sum(im.histogram()[160:]) / (im.width * im.height)
                if st > best:
                    best, best_png = st, fp
                i += 1
                page.wait_for_timeout(140)
            os.replace(best_png, f"{OUT}/{name}_lightning_peak.png")
            print(f"lightning peak frame saved (bright-px fraction {best:.3f}, {i} frames polled)")

    # mobile sun + rain card zooms
    page_m = browser.new_page(viewport={"width": 390, "height": 844})
    page_m.goto(BASE, wait_until="networkidle", timeout=60000)
    page_m.wait_for_timeout(2400)
    mb = page_m.evaluate("""(() => {
        const r = document.querySelector('#view-forecast > div').getBoundingClientRect();
        return {l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom)};
    })()""")
    MCARD = (mb["l"] + 2, mb["t"] + 2, mb["r"] - 2, mb["t"] + 130)
    for name, cat, is_day, temp in [("08_mobile_sunny", "clear-day", 1, 24),
                                    ("09_mobile_rain", "rain", 1, 13)]:
        force(page_m, cat, is_day, temp)
        page_m.wait_for_timeout(2300)
        fm = f"{TMP}/{name}.png"
        page_m.screenshot(path=fm)
        os.replace(fm, f"{OUT}/{name}_full.png")
        zoom(f"{OUT}/{name}_full.png", (0, 0, 390, 150), 3.0, f"{OUT}/{name}_sky_zoom.png")
        zoom(f"{OUT}/{name}_full.png", MCARD, 3.0, f"{OUT}/{name}_card_zoom.png")
        print(f"captured {name}")
    page_m.close()

    # contact sheets: labeled stacks of the page-sky and card-scene zooms
    def build_sheet(rows, path):
        sheet_w = rows[0][1].width
        label_h = 52
        total_h = sum(label_h + im.height for _, im in rows) + 12 * (len(rows) + 1)
        sheet = Image.new("RGB", (sheet_w, total_h), (10, 14, 22))
        y = 12
        for caption, im in rows:
            sheet.paste(label_bar(caption, sheet_w), (0, y))
            y += label_h
            sheet.paste(im, (0, y))
            y += im.height + 12
        sheet.save(path)

    build_sheet(rows_sky, f"{OUT}/00_contact_sheet_all_scenes.png")
    build_sheet(rows_card, f"{OUT}/00b_contact_sheet_all_card_scenes.png")
    print("contact sheets saved (page sky + card scene)")
    page.close()
    browser.close()

print("\nFiles in", OUT)
for f in sorted(os.listdir(OUT)):
    print(f"  {f:52s} {os.path.getsize(os.path.join(OUT, f)) // 1024} KB")
