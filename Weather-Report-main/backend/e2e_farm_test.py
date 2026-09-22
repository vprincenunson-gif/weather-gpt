"""E2E verification of the Smart Farm Weather Advisor UI."""
import sys

# Windows consoles default to cp1252 — Hindi/Telugu/emoji evidence text
# would crash printing. Force UTF-8 with replacement.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 900})

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.goto(BASE, wait_until="networkidle", timeout=60000)

    # 1. Section hidden on load; opens via the Farmer nav tab
    check("farm view hidden on initial load", not page.is_visible("#farm-advisor-section"))
    check("farmer nav tab present", page.locator('.nav-tab[data-view="view-farmer"]').count() == 1)
    page.click('.nav-tab[data-view="view-farmer"]')
    page.wait_for_timeout(500)
    check("farm section shown after Farmer tab click", page.is_visible("#farm-advisor-section"))
    title = page.text_content("#farm-advisor-title")
    check("prominent headline", title and "What should I do today?" in title, f"got: {title!r}")
    check("farmer tab activates", page.evaluate(
        "document.querySelector('.nav-tab[data-view=\"view-farmer\"]').classList.contains('is-active')"))

    # 2. Real advice loads (after weather pipeline completes)
    page.wait_for_selector("#farm-advice-cards .farm-advice-card.sev-action, #farm-advice-cards .farm-advice-card.sev-caution, #farm-advice-cards .farm-advice-card.sev-info", timeout=20000)
    cards = page.locator("#farm-advice-cards .farm-advice-card").count()
    check("advice cards rendered", cards >= 2, f"{cards} cards")  # dry SF default -> 2 (irrigation + sowing)

    # 3. Advice mentions real rain figure (Hyderabad has ~9mm in fixture weather; just assert a digit)
    first_text = page.locator("#farm-advice-cards .farm-advice-card p").first.text_content()
    check("advice contains real data (mm/digits)", any(c.isdigit() for c in (first_text or "")), f"got: {(first_text or '')[:60]!r}")

    # 4. Stage change -> advice refreshes to include harvest topic
    page.select_option("#farm-stage-select", "harvesting")
    page.wait_for_timeout(1500)
    topic_labels = page.locator("#farm-advice-cards .farm-advice-card span").all_text_contents()
    check("stage switch shows Harvesting topic", any("Harvesting" in t or "कटाई" in t for t in topic_labels),
          f"topics: {topic_labels}")

    # 5. Crop change -> crop emoji updates
    page.select_option("#farm-crop-select", "cotton")
    page.wait_for_timeout(1500)
    emoji = page.text_content("#farm-crop-emoji")
    check("crop switch updates emoji", emoji == "🪴", f"got: {emoji!r}")

    # 6. Language switch HI -> headline translates
    page.click('.lang-btn[data-lang="hi"]')
    page.wait_for_timeout(2000)
    title_hi = page.text_content("#farm-advisor-title")
    check("HI headline", title_hi and "आज" in title_hi, f"got: {title_hi!r}")
    card_hi = page.locator("#farm-advice-cards .farm-advice-card p").first.text_content()
    check("HI advice text", card_hi and any("\u0900" <= c <= "\u097f" for c in card_hi), f"got: {(card_hi or '')[:40]!r}")

    # 6b. Full multilingual UI after HI switch: labels, sr-only help,
    # option labels, subtitle, empty state, and guide chrome in Hindi.
    # Option VALUES must stay stable internal IDs (API contract).
    farm_hi = page.evaluate("""(() => {
      const cropLabel = document.getElementById('farm-crop-label')?.textContent;
      const stageLabel = document.getElementById('farm-stage-label')?.textContent;
      const cropHelp = document.getElementById('farm-crop-help')?.textContent;
      const cropOptions = [...document.querySelectorAll('#farm-crop-select option')].map(o => ({ v: o.value, t: o.textContent }));
      const stageOptions = [...document.querySelectorAll('#farm-stage-select option')].map(o => ({ v: o.value, t: o.textContent }));
      const selectedCropValue = document.getElementById('farm-crop-select').value;
      return { cropLabel, stageLabel, cropHelp, cropOptions, stageOptions, selectedCropValue };
    })()""")
    check("HI crop label", farm_hi["cropLabel"] == "फसल", f"got: {farm_hi['cropLabel']!r}")
    check("HI stage label", farm_hi["stageLabel"] == "फसल अवस्था", f"got: {farm_hi['stageLabel']!r}")
    check("HI sr-only help", "खेत" in (farm_hi["cropHelp"] or ""), f"got: {farm_hi['cropHelp']!r}")
    hi_crop_vals = {o["v"] for o in farm_hi["cropOptions"]}
    hi_stage_vals = {o["v"] for o in farm_hi["stageOptions"]}
    check("HI option values keep stable internal IDs",
          hi_crop_vals == {"rice", "cotton", "maize", "groundnut", "wheat", "sugarcane"}
          and hi_stage_vals == {"sowing", "growing", "flowering", "harvesting"},
          f"crops={sorted(hi_crop_vals)}, stages={sorted(hi_stage_vals)}")
    hi_option_texts = " ".join(o["t"] for o in farm_hi["cropOptions"] + farm_hi["stageOptions"])
    check("HI option labels localized",
          "धान" in hi_option_texts and "कपास" in hi_option_texts and "बुवाई" in hi_option_texts,
          f"got: {hi_option_texts[:80]!r}")
    check("HI selection preserved across re-population", farm_hi["selectedCropValue"] == "cotton",
          f"got: {farm_hi['selectedCropValue']!r}")
    subtitle_hi = page.text_content("#farm-advisor-subtitle")
    check("HI subtitle", "फार्म" in (subtitle_hi or ""), f"got: {subtitle_hi!r}")
    guide_title_hi = page.text_content("#farm-guide-title")
    check("HI guide title", "खेत" in (guide_title_hi or ""), f"got: {guide_title_hi!r}")
    guide_hi_texts = page.evaluate(
        "[...document.querySelectorAll('#farm-visual-guide .farm-guide-title')].map(e => e.textContent)")
    check("HI guide card titles localized",
          any("धान" in x for x in guide_hi_texts) and any("सिंचाई" in x for x in guide_hi_texts),
          f"got: {guide_hi_texts[:4]!r}")
    hi_alts = page.evaluate(
        "[...document.querySelectorAll('#farm-visual-guide img.farm-guide-photo')].map(i => i.alt)")
    check("HI image alt text localized", len(hi_alts) >= 12 and any("फोटो" in a for a in hi_alts),
          f"{len(hi_alts)} alts, sample: {(hi_alts or [''])[0]!r}")

    # 7. Language switch TE
    page.click('.lang-btn[data-lang="te"]')
    page.wait_for_timeout(2000)
    title_te = page.text_content("#farm-advisor-title")
    check("TE headline", title_te and "ఈరోజు" in title_te, f"got: {title_te!r}")
    farm_te = page.evaluate("""(() => {
      const cropLabel = document.getElementById('farm-crop-label')?.textContent;
      const options = [...document.querySelectorAll('#farm-crop-select option')].map(o => o.textContent);
      const guideTitles = [...document.querySelectorAll('#farm-visual-guide .farm-guide-title')].map(e => e.textContent);
      const alts = [...document.querySelectorAll('#farm-visual-guide img.farm-guide-photo')].map(i => i.alt);
      const credit = document.getElementById('farm-guide-photo-credit')?.textContent;
      return { cropLabel, options, guideTitles, alts, credit };
    })()""")
    check("TE crop label", farm_te["cropLabel"] == "పంట", f"got: {farm_te['cropLabel']!r}")
    te_option_texts = " ".join(farm_te["options"])
    check("TE option labels localized",
          "వరి" in te_option_texts and "పత్తి" in te_option_texts and "చెరకు" in te_option_texts,
          f"got: {te_option_texts[:60]!r}")
    check("TE guide card titles localized",
          any("వరి" in x for x in farm_te["guideTitles"]) and any("నీటి పారుదల" in x for x in farm_te["guideTitles"]),
          f"got: {farm_te['guideTitles'][:4]!r}")
    check("TE image alt text localized", any("ఫోటో" in a for a in farm_te["alts"]),
          f"sample: {(farm_te['alts'] or [''])[0]!r}")
    check("TE photo credit localized", "Wikimedia Commons" in (farm_te["credit"] or ""),
          f"got: {farm_te['credit']!r}")

    # 7b. Telugu selects stay usable (no truncation/overflow at 390px)
    te_select_w = page.evaluate("document.getElementById('farm-crop-select').getBoundingClientRect().width")
    check("TE select usable", te_select_w > 120, f"{te_select_w}px")

    # back to EN for remaining checks
    page.click('.lang-btn[data-lang="auto"]')
    page.wait_for_timeout(1500)

    # 8. Accessibility: labels associated, section labelled, badges present
    check("crop select labelled", page.evaluate(
        "document.querySelector('label[for=farm-crop-select]') !== null"))
    check("stage select labelled", page.evaluate(
        "document.querySelector('label[for=farm-stage-select]') !== null"))
    check("section aria-labelledby", page.get_attribute("#farm-advisor-section", "aria-labelledby") == "farm-advisor-title")
    badges = page.locator("#farm-advice-cards .farm-sev-badge").count()
    check("severity badges rendered", badges >= 3, f"{badges} badges")

    # 9b. Switching away and back keeps the advisory (cached, not stale)
    page.click('.nav-tab[data-view="view-forecast"]')
    page.wait_for_timeout(300)
    check("farm view hides when switching to Forecast", not page.is_visible("#farm-advisor-section"))
    page.click('.nav-tab[data-view="view-farmer"]')
    page.wait_for_timeout(400)
    check("farm view restores content on return", page.locator("#farm-advice-cards .farm-advice-card").count() >= 2)

    # 9c. Other four tabs still show their own content
    for view, probe_id in (("view-forecast", "view-forecast"), ("view-map", "radar-map"),
                           ("view-weathergpt", "chat-stream"), ("view-insights", "insights-scope-metrics")):
        page.click(f'.nav-tab[data-view="{view}"]')
        page.wait_for_timeout(300)
        check(f"tab {view} still shows its content", page.is_visible(f"#{probe_id}"))

    # 9. Alert count chip visible when actionable advice exists
    actionable = page.evaluate(
        "document.querySelectorAll('#farm-advice-cards .farm-advice-card.sev-action, #farm-advice-cards .farm-advice-card.sev-caution').length")
    chip_hidden = page.evaluate("document.getElementById('farm-alert-count').classList.contains('hidden')")
    check("alert chip consistent with actionable advice", (actionable > 0) != chip_hidden,
          f"actionable={actionable}, chip_hidden={chip_hidden}")

    # 10. Disclaimer present, no dosage terms anywhere in farm section
    disclaimer = page.text_content("#farm-disclaimer")
    check("disclaimer rendered", bool(disclaimer and len(disclaimer) > 30))
    blob = page.text_content("#farm-advisor-section").lower()
    bad = [t for t in ("ml/acre", "kg/acre", "dosage", "ppm") if t in blob]
    check("no dosage terms", not bad, f"found: {bad}")

    # 11. Responsive: no horizontal overflow with farm section
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("no horizontal overflow (390px)", overflow <= 0, f"{overflow}px")
    pg2 = browser.new_page(viewport={"width": 360, "height": 740})
    pg2.goto(BASE, wait_until="networkidle", timeout=60000)
    overflow2 = pg2.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("no horizontal overflow (360px)", overflow2 <= 0, f"{overflow2}px")
    pg2.click('.nav-tab[data-view="view-farmer"]')
    pg2.wait_for_timeout(1500)
    select_w = pg2.evaluate("document.getElementById('farm-crop-select').getBoundingClientRect().width")
    check("selects usable at 360px", select_w > 120, f"{select_w}px")

    # 11b. Visual guide renders on mobile: 12 cards, 2-col grid, images within viewport
    pg2_guide = pg2.evaluate("""(() => {
      const cards = document.querySelectorAll('#farm-visual-guide .farm-guide-card');
      const grid = document.getElementById('farm-guide-crop-grid');
      const firstCard = document.querySelector('#farm-guide-crop-grid .farm-guide-card');
      const firstImg = document.querySelector('#farm-guide-crop-grid img.farm-guide-photo');
      return {
        cardCount: cards.length,
        gridCols: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
        cardW: firstCard ? firstCard.getBoundingClientRect().width : 0,
        imgLoaded: firstImg ? firstImg.complete && firstImg.naturalWidth > 0 : false,
        vw: window.innerWidth,
      };
    })()""")
    check("mobile guide: 12 cards", pg2_guide["cardCount"] == 12, f"got: {pg2_guide['cardCount']}")
    check("mobile guide: 2-column grid", pg2_guide["gridCols"] == 2, f"cols: {pg2_guide['gridCols']}")
    check("mobile guide: cards fit viewport", 0 < pg2_guide["cardW"] <= pg2_guide["vw"],
          f"cardW={pg2_guide['cardW']:.0f}, vw={pg2_guide['vw']}")
    # Commons thumbnails are generated server-side on first request and can take
    # several seconds; wait for decode instead of checking at a fixed instant.
    try:
        pg2.wait_for_function(
            "(() => { const i = document.querySelector('#farm-guide-crop-grid img.farm-guide-photo');"
            " return i && i.complete && i.naturalWidth > 0; })()", timeout=30000)
        check("mobile guide: first image decoded", True)
    except Exception:
        check("mobile guide: first image decoded", pg2_guide["imgLoaded"])
    pg2.close()

    # 11c. Desktop layout: 3-column guide grid, first crop photo decoded
    pg3 = browser.new_page(viewport={"width": 1280, "height": 900})
    pg3.goto(BASE, wait_until="networkidle", timeout=60000)
    pg3.click('.nav-tab[data-view="view-farmer"]')
    pg3.wait_for_timeout(1500)
    pg3_guide = pg3.evaluate("""(() => {
      const grid = document.getElementById('farm-guide-crop-grid');
      const img = document.querySelector('#farm-guide-crop-grid img.farm-guide-photo');
      return {
        gridCols: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
        imgLoaded: img ? img.complete && img.naturalWidth > 0 : false,
      };
    })()""")
    check("desktop guide: 3-column grid", pg3_guide["gridCols"] == 3, f"cols: {pg3_guide['gridCols']}")
    try:
        pg3.wait_for_function(
            "(() => { const i = document.querySelector('#farm-guide-crop-grid img.farm-guide-photo');"
            " return i && i.complete && i.naturalWidth > 0; })()", timeout=30000)
        check("desktop guide: first image decoded", True)
    except Exception:
        check("desktop guide: first image decoded", pg3_guide["imgLoaded"])
    pg3.close()

    # 12. Visual guide theme support: photo + caption use themed tokens,
    # all alt texts present in EN, no broken images after error fallbacks.
    guide_theme = page.evaluate("""(() => {
      const img = document.querySelector('#farm-visual-guide img.farm-guide-photo, #farm-visual-guide .farm-guide-photo-fallback');
      const caption = document.querySelector('#farm-visual-guide .farm-guide-title');
      return {
        photoEl: !!img,
        captionColor: caption ? getComputedStyle(caption).color : "",
        brokenImgs: [...document.querySelectorAll('#farm-visual-guide img.farm-guide-photo')]
          .filter(i => i.complete && i.naturalWidth === 0).length,
        altCount: document.querySelectorAll('#farm-visual-guide img.farm-guide-photo[alt]').length,
        imgCount: document.querySelectorAll('#farm-visual-guide img.farm-guide-photo').length,
      };
    })()""")
    check("guide photo element present", guide_theme["photoEl"])
    check("guide caption themed color", guide_theme["captionColor"] not in ("", "rgba(0, 0, 0, 0)"),
          guide_theme["captionColor"])
    check("guide has no broken images", guide_theme["brokenImgs"] == 0, f"broken: {guide_theme['brokenImgs']}")
    check("guide every image has alt text", guide_theme["altCount"] == guide_theme["imgCount"] and guide_theme["imgCount"] > 0,
          f"{guide_theme['altCount']}/{guide_theme['imgCount']}")

    # 12b. Dark theme: guide chrome re-themes (photo is a real photograph;
    # captions/borders follow the dark tokens).
    page.evaluate("localStorage.setItem('weathergpt-theme','dark')")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    page.click('.nav-tab[data-view="view-farmer"]')
    page.wait_for_timeout(1500)
    dark_guide = page.evaluate("""(() => {
      const card = document.querySelector('#farm-visual-guide .farm-guide-card');
      const caption = document.querySelector('#farm-visual-guide .farm-guide-title');
      const isDark = document.documentElement.dataset.theme === "dark";
      return {
        isDark,
        cardBg: card ? getComputedStyle(card).backgroundColor : "",
        captionColor: caption ? getComputedStyle(caption).color : "",
        cards: document.querySelectorAll('#farm-visual-guide .farm-guide-card').length,
      };
    })()""")
    check("dark theme active for guide check", dark_guide["isDark"])
    check("dark theme: guide cards render", dark_guide["cards"] == 12, f"got: {dark_guide['cards']}")
    check("dark theme: caption color readable", dark_guide["captionColor"] not in ("", "rgba(0, 0, 0, 0)"),
          dark_guide["captionColor"])
    check("dark theme: card background themed", dark_guide["cardBg"] not in ("", "rgba(0, 0, 0, 0)"),
          dark_guide["cardBg"])
    page.evaluate("localStorage.setItem('weathergpt-theme','light')")

    # 13. No page errors
    farm_errors = [e for e in console_errors if "favicon" not in e.lower()]
    check("no console/page errors", not farm_errors, "; ".join(farm_errors[:3]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} farm E2E checks passed =====")
sys.exit(1 if fails else 0)
