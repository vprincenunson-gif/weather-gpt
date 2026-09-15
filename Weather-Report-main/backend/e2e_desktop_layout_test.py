"""Desktop layout verification for the widened WeatherGPT dashboard."""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---------- DESKTOP ----------
    for width, tag in [(1440, "desktop-1440"), (1280, "desktop-1280"), (1024, "laptop-1024")]:
        pg = browser.new_page(viewport={"width": width, "height": 900})
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2500)

        overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"{tag}: no page overflow", overflow <= 0, f"overflow={overflow}px")

        main_w = pg.evaluate("document.querySelector('main').getBoundingClientRect().width")
        check(f"{tag}: main uses wide container", 1020 <= main_w <= 1150, f"main width={main_w:.0f}px")

        # Hero card and synopsis sit side-by-side (same top, different x)
        side = pg.evaluate("""() => {
          const hero = document.querySelector('#view-forecast > div');
          const syn = [...document.querySelectorAll('#view-forecast > div')].find(d => d.querySelector('#hero-synopsis-text'));
          if (!hero || !syn) return false;
          const a = hero.getBoundingClientRect(), b = syn.getBoundingClientRect();
          return Math.abs(a.top - b.top) < 40 && b.left > a.right - 20;
        }""")
        check(f"{tag}: hero + AI synopsis side-by-side", side)

        hero_w = pg.evaluate("document.querySelector('#view-forecast > div').getBoundingClientRect().width")
        check(f"{tag}: hero card ~420px column", 400 <= hero_w <= 450, f"width={hero_w:.0f}px")

        # Telemetry: 4 cards in one row at >=1024
        row = pg.evaluate("""() => {
          const cards = [...document.querySelectorAll('#view-forecast .grid.grid-cols-2 > div')];
          if (cards.length < 4) return false;
          const tops = cards.map(c => Math.round(c.getBoundingClientRect().top));
          return new Set(tops).size === 1;
        }""")
        check(f"{tag}: telemetry 4-in-a-row", row)

        # Hourly scroller shows more cards than mobile
        n_hours = pg.evaluate("[...document.querySelectorAll('.forecast-hourly-card')].filter(c => { const r = c.getBoundingClientRect(); return r.right > 0 && r.left < window.innerWidth; }).length")
        check(f"{tag}: hourly cards visible >= 9", n_hours >= 9, f"visible={n_hours}")

        # Daily range bar widened on desktop
        bar_w = pg.evaluate("(() => { const el = document.querySelector('#daily-forecast-container .w-24'); return el ? el.getBoundingClientRect().width : 0; })()")
        check(f"{tag}: daily range bar widened", bar_w >= 200, f"bar={bar_w:.0f}px")
        pg.close()

    # ---------- TABLET 768 (md: two-col starts, single row telemetry not yet) ----------
    pg = browser.new_page(viewport={"width": 768, "height": 1000})
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("tablet-768: no overflow", overflow <= 0, f"overflow={overflow}px")
    main_w = pg.evaluate("document.querySelector('main').getBoundingClientRect().width")
    check("tablet-768: main > 700px", main_w > 700, f"main={main_w:.0f}px")
    # Assistant & Farmer views centered max-w-760
    for view in ["view-weathergpt", "view-farmer"]:
        pg.click(f'button[data-view="{view}"]')
        pg.wait_for_timeout(300)
        vw = pg.evaluate(f"document.getElementById('{view}').getBoundingClientRect().width")
        check(f"tablet-768: {view} width <= 760", vw <= 762, f"width={vw:.0f}px")
    pg.close()

    # ---------- MOBILE 390 + 360: unchanged layout ----------
    for width in (390, 360):
        pg = browser.new_page(viewport={"width": width, "height": 800})
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2000)
        overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"mobile-{width}: no overflow", overflow <= 0, f"overflow={overflow}px")
        main_w = pg.evaluate("document.querySelector('main').getBoundingClientRect().width")
        check(f"mobile-{width}: main is mobile width", main_w <= width, f"main={main_w:.0f}px")
        stacked = pg.evaluate("""() => {
          const hero = document.querySelector('#view-forecast > div');
          const syn = [...document.querySelectorAll('#view-forecast > div')].find(d => d.querySelector('#hero-synopsis-text'));
          const a = hero.getBoundingClientRect(), b = syn.getBoundingClientRect();
          return b.top >= a.bottom - 5;
        }""")
        check(f"mobile-{width}: hero stacked above synopsis (unchanged)", stacked)
        tel = pg.evaluate("""() => {
          const cards = [...document.querySelectorAll('#view-forecast .grid.grid-cols-2 > div')];
          const tops = cards.slice(0,4).map(c => Math.round(c.getBoundingClientRect().top));
          return new Set(tops).size === 2;
        }""")
        check(f"mobile-{width}: telemetry still 2x2", tel)
        n_hours = pg.evaluate("[...document.querySelectorAll('.forecast-hourly-card')].filter(c => { const r = c.getBoundingClientRect(); return r.right > 0 && r.left < window.innerWidth; }).length")
        check(f"mobile-{width}: hourly ~4 cards visible (unchanged)", 3 <= n_hours <= 6, f"visible={n_hours}")
        tabs = pg.evaluate("document.querySelectorAll('nav button.nav-tab').length")
        check(f"mobile-{width}: 5 nav tabs intact", tabs == 5, f"tabs={tabs}")
        pg.close()

    # ---------- Farmer navigation still works on desktop ----------
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    for view, marker in [("view-farmer", "#farm-crop-select"), ("view-forecast", "#hero-temperature"),
                         ("view-map", "#radar-map"), ("view-weathergpt", "#chat-stream"),
                         ("view-insights", "#insights-scope-metrics")]:
        pg.click(f'button[data-view="{view}"]')
        pg.wait_for_timeout(400)
        visible = pg.evaluate(f"document.getElementById('{view}').offsetParent !== null")
        check(f"desktop-1440: {view} opens via nav", visible)
    pg.close()
    browser.close()

failed = [n for n, ok, _ in results if not ok]
print(f"\n===== {len(results) - len(failed)}/{len(results)} desktop-layout checks passed =====")
raise SystemExit(1 if failed else 0)
