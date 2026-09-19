"""Capture human-viewable Chromium previews of the four scene states.

For each state (sunny / cloudy / rain / thunder) on the REAL running UI:
  - full-viewport screenshot
  - 2.5x zoomed crop of the open sky band (top 150px) where effects live
  - motion pair (two frames side by side) proving the animation moves
  - thunder: polls up to 10s and saves the lightning-flash peak frame
Also builds one labeled contact sheet stacking all four sky zooms, and
mobile (390px) sun+rain zooms. Output: Weather-Report-main/scene-previews/.
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
        applyPrecipFx('{cat}');
        document.body.dataset.condition = '{cat}';
    }})()""")


STATES = [
    ("01_sunny", "clear-day", 1, 24, "SUNNY - scene 'sun': warm orb cresting the card line + rotating ray fan"),
    ("02_cloudy", "cloudy", 1, 19, "CLOUDY - scene 'clouds': SVG clouds drifting along the top sky strip"),
    ("03_rain", "rain", 1, 13, "RAIN - scene 'rain': storm cloud + bright wind-slanted drops under the frosted header"),
    ("04_thunder", "thunder", 1, 21, "THUNDER - scene 'rain' + lightning flash (cloud flicker synced)"),
]

rows = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)

    for name, cat, is_day, temp, caption in STATES:
        force(page, cat, is_day, temp)
        page.wait_for_timeout(2100)  # 1.2s fade-in + settle

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
        z1 = Image.open(f"{TMP}/{name}_z1.png")
        z2 = Image.open(f"{TMP}/{name}_z2.png")
        pair = Image.new("RGB", (z1.width, z1.height * 2 + 8), (10, 14, 22))
        pair.paste(z1, (0, 0))
        pair.paste(z2, (0, z1.height + 8))
        pair.save(f"{OUT}/{name}_sky_zoom_motion_pair.png")

        w, h = z1.size
        rows.append((caption, z1))
        print(f"captured {name}: full + sky zoom + motion pair (gap {gap}ms)")

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

    # mobile sun + rain zooms
    page_m = browser.new_page(viewport={"width": 390, "height": 844})
    page_m.goto(BASE, wait_until="networkidle", timeout=60000)
    page_m.wait_for_timeout(2400)
    for name, cat, is_day, temp in [("05_mobile_sunny", "clear-day", 1, 24),
                                    ("06_mobile_rain", "rain", 1, 13)]:
        force(page_m, cat, is_day, temp)
        page_m.wait_for_timeout(2100)
        fm = f"{TMP}/{name}.png"
        page_m.screenshot(path=fm)
        os.replace(fm, f"{OUT}/{name}_full.png")
        zoom(f"{OUT}/{name}_full.png", (0, 0, 390, 150), 3.0, f"{OUT}/{name}_sky_zoom.png")
        print(f"captured {name}")
    page_m.close()

    # contact sheet: labeled stack of the four desktop sky zooms
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
    sheet.save(f"{OUT}/00_contact_sheet_all_scenes.png")
    print("contact sheet saved")
    page.close()
    browser.close()

print("\nFiles in", OUT)
for f in sorted(os.listdir(OUT)):
    print(f"  {f:48s} {os.path.getsize(os.path.join(OUT, f)) // 1024} KB")
