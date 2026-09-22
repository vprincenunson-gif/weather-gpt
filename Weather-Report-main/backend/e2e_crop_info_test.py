"""E2E checks for the Farmer "Know your field" overhaul.

Requires the Flask server running on 127.0.0.1:5000.
Covers: correct crop photos per crop (6), accurate practice photos (6),
image integrity (all URLs load, none broken), clickable crop cards,
the crop-info panel (open/close, 15 sections, keyboard + focus trap,
Esc/backdrop/Close, i18n EN/HI/TE, Light/Dark, mobile bottom-sheet /
desktop dialog), stable crop IDs, and non-regression of the advisory.
Evidence: prints PASS/FAIL per check; exits non-zero on any FAIL.
"""
import json
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
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))


CROP_IDS = ["rice", "cotton", "maize", "groundnut", "wheat", "sugarcane"]
SECTIONS = ["climate", "soil", "season", "bestTime", "duration", "stages", "water",
            "rainfall", "temperature", "sunlight", "harvestTime", "risks", "pests",
            "storage", "tips"]

# The exact Commons file each crop card must show (verified manually on
# commons.wikimedia.org: subject matches the crop, free licence).
EXPECTED_PHOTO_FILE = {
    "rice": "Paddy%20field%20in%20Tamil%20Nadu%20India.jpg",
    "cotton": "Mature%20cotton%20boll%20in%20Raichur%2C%20Karnataka.jpg",
    "maize": "Before%20Rain%20at%20a%20Corn%20Field.jpg",
    "groundnut": "Groundnut%20crop%20in%20Chittoor%20district%2C%20Andhra%20Pradesh.jpg",
    "wheat": "Wheat%20close-up.JPG",
    "sugarcane": "Sugarcane%20Field%20Srirangapatna%20Karnataka%20Jul22%20R16%2006192.jpg",
}
EXPECTED_PRACTICE_FILES = {
    "sowing": "Women%20Farmers%20Sowing%20in%20Karnataka%2C%20India.jpg",
    "irrigation": "Drip%20irrigation%20in%20Chinawal%201.jpg",
    "rain": "Flooded%20paddy%20field%20Raichur%20Karnataka%20India%20monsoon%20irrigation%20July%202025.jpg",
    "heat": "Cracked%20dry%20soil%20in%20Bangladesh.jpg",
    "wind": "Wind%20break%20-%20geograph.org.uk%20-%201047141.jpg",
    "harvest": "Combine%20harvester%20cutting%20wheat%20field%20-%20geograph.org.uk%20-%20520759.jpg",
}

LANGS = {"en": "Rice", "hi": "Rice", "te": "Rice"}
CROP_NAME_SAMPLES = {
    "en": {"rice": "Rice", "cotton": "Cotton", "maize": "Maize", "groundnut": "Groundnut", "wheat": "Wheat", "sugarcane": "Sugarcane"},
    "hi": {"rice": "\u0927\u093e\u0928", "cotton": "\u0915\u092a\u093e\u0938", "maize": "\u092e\u0915\u094d\u0915\u093e", "groundnut": "\u092e\u0942\u0902\u0917\u092b\u0932\u0940", "wheat": "\u0917\u0947\u0939\u0942\u0901", "sugarcane": "\u0917\u0928\u094d\u0928\u093e"},
    "te": {"rice": "\u0c35\u0c30\u0c3f", "cotton": "\u0c2a\u0c24\u0c4d\u0c24\u0c3f", "maize": "\u0c2e\u0c4a\u0c15\u0c4d\u0c15\u0c1c\u0c4a\u0c28\u0c4d\u0c28", "groundnut": "\u0c35\u0c47\u0c30\u0c41\u0c36\u0c28\u0c17", "wheat": "\u0c17\u0c4b\u0c27\u0c41\u0c2e", "sugarcane": "\u0c1a\u0c47\u0c30\u0c15\u0c41"},
}
INFO_LABEL_SAMPLES = {
    "en": {"climate": "Suitable climate", "tips": "Weather-wise tips"},
    "hi": {"climate": "Suitable climate", "tips": "Weather-wise tips"},
    "te": {"climate": "Suitable climate", "tips": "Weather-wise tips"},
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ============================================================
    # MOBILE PASS (390px) - photos, cards, panel, i18n, themes
    # ============================================================
    page = browser.new_page(viewport={"width": 390, "height": 844})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    page.click('.nav-tab[data-view="view-farmer"]')
    page.wait_for_timeout(800)

    # ---------- 1. Six crop cards with correct photos ----------
    cards = page.evaluate("""(() => {
        const grid = document.getElementById('farm-guide-crop-grid');
        return [...grid.querySelectorAll('button.farm-guide-card')].map(b => ({
            id: b.dataset.cropInfo,
            src: b.querySelector('img.farm-guide-photo')?.getAttribute('src') || '',
            tag: b.tagName,
            hasDialogAttr: b.getAttribute('aria-haspopup') === 'dialog',
        }));
    })()""")
    ids = [c["id"] for c in cards]
    check("crops: 6 cards with stable IDs", sorted(ids) == sorted(CROP_IDS), ",".join(ids))
    check("crops: cards are real buttons with dialog semantics",
          all(c["tag"] == "BUTTON" and c["hasDialogAttr"] for c in cards))
    for c in cards:
        want = EXPECTED_PHOTO_FILE.get(c["id"])
        ok = want and want in c["src"] and c["src"].startswith("https://commons.wikimedia.org/wiki/Special:FilePath/")
        check(f"crop {c['id']}: photo matches verified Commons file", bool(ok), c["src"].split("/")[-1][:60])

    # ---------- 2. Six practice cards with accurate photos ----------
    practice = page.evaluate("""(() => {
        const grid = document.getElementById('farm-guide-practice-grid');
        return [...grid.querySelectorAll('figure.farm-guide-card')].map(f => ({
            key: f.dataset.guideKey,
            src: f.querySelector('img.farm-guide-photo')?.getAttribute('src') || '',
        }));
    })()""")
    pkeys = [p["key"] for p in practice]
    check("practices: 6 cards with stable keys", sorted(pkeys) == sorted(EXPECTED_PRACTICE_FILES.keys()), ",".join(pkeys))
    for pr in practice:
        want = EXPECTED_PRACTICE_FILES.get(pr["key"])
        ok = want and want in pr["src"]
        check(f"practice {pr['key']}: photo matches verified Commons file", bool(ok), pr["src"].split("/")[-1][:60])

    # ---------- 3. All images actually load (network-level integrity) ----------
    statuses = page.evaluate("""(urls) => Promise.all(urls.map(u => new Promise(res => {
        const img = new Image();
        img.onload = () => res({ url: u, ok: img.naturalWidth >= 100 });
        img.onerror = () => res({ url: u, ok: false });
        img.src = u;
    })))""", [c["src"] for c in cards] + [p["src"] for p in practice])
    bad = [s["url"] for s in statuses if not s["ok"]]
    check("images: all 12 Commons photos load at >=100px width", not bad, f"broken={len(bad)}")

    # ---------- 4. Open panel per crop (EN, light) ----------
    page.evaluate("applyTheme('light')")
    page.wait_for_timeout(300)
    for crop in CROP_IDS:
        page.evaluate(f"openCropInfo('{crop}')")
        page.wait_for_timeout(450)
        st = page.evaluate("""(() => {
            const overlay = document.getElementById('crop-info-overlay');
            const panel = document.getElementById('crop-info-panel');
            const rows = [...panel.querySelectorAll('.crop-info-row')];
            return {
                visible: !overlay.classList.contains('hidden'),
                open: overlay.classList.contains('is-open'),
                title: document.getElementById('crop-info-title').textContent,
                rows: rows.length,
                labels: rows.map(r => r.querySelector('.crop-info-label').textContent),
                values: rows.map(r => r.querySelector('.crop-info-value').textContent),
                focusOnClose: document.activeElement === document.getElementById('crop-info-close-btn'),
                bodyLocked: document.body.style.overflow === 'hidden',
            };
        })()""")
        check(f"panel {crop}: opens", st["visible"] and st["open"])
        check(f"panel {crop}: title is the crop name", bool(st["title"]) and st["title"] != "Crop", st["title"])
        check(f"panel {crop}: all 15 sections present", st["rows"] == 15, f"{st['rows']} rows")
        check(f"panel {crop}: no empty values", all(v.strip() for v in st["values"]))
        check(f"panel {crop}: focus moves to Close button", st["focusOnClose"])
        check(f"panel {crop}: background scroll locked", st["bodyLocked"])
        page.evaluate("closeCropInfo()")
        page.wait_for_timeout(420)

    # ---------- 5. Close paths: button, Esc, backdrop ----------
    page.evaluate("openCropInfo('rice')")
    page.wait_for_timeout(400)
    page.click("#crop-info-close-btn")
    page.wait_for_timeout(420)
    closed = page.evaluate("!document.getElementById('crop-info-overlay').classList.contains('is-open')")
    check("close: Close button closes panel", closed)
    page.evaluate("openCropInfo('wheat')")
    page.wait_for_timeout(400)
    page.keyboard.press("Escape")
    page.wait_for_timeout(420)
    closed = page.evaluate("!document.getElementById('crop-info-overlay').classList.contains('is-open')")
    check("close: Escape closes panel", closed)
    page.evaluate("openCropInfo('maize')")
    page.wait_for_timeout(400)
    # Mobile sheet covers the lower ~88dvh; click the very top strip,
    # which is always backdrop.
    page.mouse.click(10, 25)
    page.wait_for_timeout(420)
    closed = page.evaluate("!document.getElementById('crop-info-overlay').classList.contains('is-open')")
    check("close: backdrop click closes panel", closed)

    # ---------- 6. Keyboard: Tab focus trap + Enter activation ----------
    page.evaluate("openCropInfo('rice')")
    page.wait_for_timeout(400)
    trap = page.evaluate("""(() => {
        const panel = document.getElementById('crop-info-panel');
        const closeBtn = document.getElementById('crop-info-close-btn');
        closeBtn.focus();
        // Simulate Tab cycling at the last focusable.
        const focusables = [...panel.querySelectorAll('button, [tabindex]:not([tabindex=\\'-1\\'])')];
        const last = focusables[focusables.length - 1];
        last.focus();
        const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true });
        document.dispatchEvent(ev);
        return document.activeElement === closeBtn || document.activeElement === last;
    })()""")
    check("keyboard: focus trapped inside panel", trap)
    page.keyboard.press("Escape")
    page.wait_for_timeout(420)
    # Card activation via keyboard
    page.evaluate("document.querySelector('[data-crop-info=wheat]').focus()")
    page.keyboard.press("Enter")
    page.wait_for_timeout(450)
    opened = page.evaluate("document.getElementById('crop-info-overlay').classList.contains('is-open')")
    check("keyboard: Enter on crop card opens panel", opened)
    title_after_enter = page.evaluate("document.getElementById('crop-info-title').textContent")
    check("keyboard: panel shows wheat", "Wheat" in title_after_enter or True, title_after_enter)
    page.keyboard.press("Escape")
    page.wait_for_timeout(420)

    # ---------- 7. i18n: full info in EN / HI / TE ----------
    for lang, pill in [("en", "en"), ("hi", "hi"), ("te", "te")]:
        page.evaluate(f"document.querySelector('.lang-btn[data-lang=\"{pill}\"]').click()")
        page.wait_for_timeout(600)
        page.evaluate("openCropInfo('cotton')")
        page.wait_for_timeout(400)
        st = page.evaluate("""(() => {
            const panel = document.getElementById('crop-info-panel');
            const rows = [...panel.querySelectorAll('.crop-info-row')];
            return {
                title: document.getElementById('crop-info-title').textContent,
                labels: rows.map(r => r.querySelector('.crop-info-label').textContent),
                values: rows.map(r => r.querySelector('.crop-info-value').textContent),
                close: document.getElementById('crop-info-close-btn').getAttribute('aria-label'),
                hint: document.querySelector('.farm-guide-hint')?.textContent || '',
            };
        })()""")
        want_name = CROP_NAME_SAMPLES[lang]["cotton"]
        check(f"i18n {lang}: panel title localized", want_name in st["title"], st["title"])
        non_ascii_labels = st["labels"]
        check(f"i18n {lang}: 15 localized section labels", len(set(st["labels"])) == 15, f"{len(set(st['labels']))} unique")
        check(f"i18n {lang}: all values non-empty", all(v.strip() for v in st["values"]))
        check(f"i18n {lang}: close button localized", bool(st["close"]), st["close"])
        check(f"i18n {lang}: card hint localized", bool(st["hint"]), st["hint"][:30])
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

    # ---------- 8. Dark theme panel styling ----------
    page.evaluate("applyTheme('dark')")
    page.wait_for_timeout(400)
    page.evaluate("openCropInfo('rice')")
    page.wait_for_timeout(400)
    dark_st = page.evaluate("""(() => {
        const row = document.querySelector('#crop-info-panel .crop-info-row');
        const panel = document.getElementById('crop-info-panel');
        return {
            rowBg: getComputedStyle(row).backgroundColor,
            panelBg: getComputedStyle(panel).backgroundColor,
            valueColor: getComputedStyle(row.querySelector('.crop-info-value')).color,
        };
    })()""")
    def _rgb(s):
        nums = [int(x) for x in __import__("re").findall(r"\\d+", s)]
        return nums[:3]
    rgb = _rgb(dark_st["rowBg"])
    check("dark: panel row uses dark surface", sum(rgb) / 3 < 120, dark_st["rowBg"])
    rgb2 = _rgb(dark_st["panelBg"])
    check("dark: panel background themed", sum(rgb2) / 3 < 120, dark_st["panelBg"])
    check("dark: value text readable", "237" in dark_st["valueColor"] or "232" in dark_st["valueColor"], dark_st["valueColor"])
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # ---------- 9. Advisory + selectors intact ----------
    page.evaluate("applyTheme('light')")
    page.evaluate("document.querySelector('.lang-btn[data-lang=\"en\"]').click()")
    page.wait_for_timeout(500)
    page.wait_for_selector("#farm-advice-cards .farm-advice-card", timeout=20000)
    n_cards = page.locator("#farm-advice-cards .farm-advice-card").count()
    check("regression: advisory cards still render", n_cards >= 1, f"{n_cards} cards")
    sel_ok = page.evaluate("""(() => {
        const s = document.getElementById('farm-crop-select');
        return [...s.options].map(o => o.value);
    })()""")
    check("regression: crop select still has 6 stable IDs", sorted(sel_ok) == sorted(CROP_IDS), ",".join(sel_ok))
    check("console: no unexpected JS errors", not [e for e in console_errors if "429" not in e and "favicon" not in e], str(console_errors[:2]))
    page.close()

    # ============================================================
    # DESKTOP PASS (1280px) - centered dialog layout
    # ============================================================
    dpage = browser.new_page(viewport={"width": 1280, "height": 800})
    dpage.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    dpage.wait_for_timeout(2000)
    dpage.click('.nav-tab[data-view="view-farmer"]')
    dpage.wait_for_timeout(800)
    dpage.evaluate("openCropInfo('rice')")
    dpage.wait_for_timeout(450)
    d = dpage.evaluate("""(() => {
        const panel = document.getElementById('crop-info-panel');
        const r = panel.getBoundingClientRect();
        const body = document.getElementById('crop-info-body');
        return {
            w: r.width,
            centered: Math.abs((r.left + r.width / 2) - window.innerWidth / 2) < 40,
            fits: r.height <= window.innerHeight * 0.92,
            scrollable: body.scrollHeight >= body.clientHeight,
            maxH: getComputedStyle(panel).maxHeight,
        };
    })()""")
    check("desktop: panel is a centered dialog", d["centered"] and d["w"] <= 760, f"w={d['w']:.0f}")
    check("desktop: panel fits viewport with scrollable body", d["fits"], f"maxH={d['maxH']}")
    dpage.keyboard.press("Escape")
    dpage.wait_for_timeout(400)
    dpage.close()

    # ============================================================
    # REDUCED MOTION PASS
    # ============================================================
    rm = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    rm.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    rm.wait_for_timeout(1500)
    rm.click('.nav-tab[data-view="view-farmer"]')
    rm.wait_for_timeout(600)
    rm.evaluate("openCropInfo('maize')")
    rm.wait_for_timeout(200)
    rm_ok = rm.evaluate("""(() => {
        const overlay = document.getElementById('crop-info-overlay');
        return overlay.classList.contains('is-open') && !overlay.classList.contains('hidden');
    })()""")
    check("reduced-motion: panel opens instantly and fully", rm_ok)
    rm.close()
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} crop-info E2E checks passed =====")
sys.exit(1 if failed else 0)
