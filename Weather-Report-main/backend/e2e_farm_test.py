"""E2E verification of the Smart Farm Weather Advisor UI."""
import sys

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

    # 1. Section present, prominent (top of forecast view, after hero)
    check("farm section rendered", page.is_visible("#farm-advisor-section"))
    title = page.text_content("#farm-advisor-title")
    check("prominent headline", title and "What should I do today?" in title, f"got: {title!r}")

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

    # 7. Language switch TE
    page.click('.lang-btn[data-lang="te"]')
    page.wait_for_timeout(2000)
    title_te = page.text_content("#farm-advisor-title")
    check("TE headline", title_te and "ఈరోజు" in title_te, f"got: {title_te!r}")

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
    select_w = pg2.evaluate("document.getElementById('farm-crop-select').getBoundingClientRect().width")
    check("selects usable at 360px", select_w > 120, f"{select_w}px")
    pg2.close()

    # 12. No page errors
    farm_errors = [e for e in console_errors if "favicon" not in e.lower()]
    check("no console/page errors", not farm_errors, "; ".join(farm_errors[:3]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} farm E2E checks passed =====")
sys.exit(1 if fails else 0)
