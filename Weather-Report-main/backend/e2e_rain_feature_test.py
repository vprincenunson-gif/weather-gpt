"""E2E checks for the Smart Rain Alert + Rain Timeline feature.

Requires the Flask server running on 127.0.0.1:5000.
The live rain window is weather-dependent, so this suite route()-mocks the
backend payload: both the rain-window and the no-rain paths are covered
deterministically (following the mocked-offline style of test_app.py).
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


def with_rain_payload(tl):
    """Inject a timeline dict (JSON-encoded so true/null stay valid JS)."""
    payload = json.dumps(tl)
    return """(() => {
        state.rainTimeline = %s;
        renderSmartRainAlert();
        renderRainTimeline();
        return (() => ({
            smartVisible: !document.getElementById('smart-rain-alert').classList.contains('hidden'),
            smartTitle: document.getElementById('smart-rain-title')?.textContent,
            smartBadge: document.getElementById('smart-rain-badge')?.textContent,
            smartText: document.getElementById('smart-rain-text')?.textContent,
            barLeft: document.getElementById('rain-timeline-bar')?.style.left,
            barWidth: document.getElementById('rain-timeline-bar')?.style.width,
            peak: document.getElementById('rain-timeline-peak')?.textContent,
            note: document.getElementById('rain-timeline-note')?.textContent,
            startLabel: document.getElementById('rain-timeline-start-label')?.textContent,
            endLabel: document.getElementById('rain-timeline-end-label')?.textContent,
            trackLabel: document.getElementById('rain-timeline-track')?.getAttribute('aria-label'),
        }))();
    })()""" % payload


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)

    # ---------- Path 1: real rain window (deterministic mock) ----------
    tl_rain = {
        "has_event": True, "starts_in_h": 3, "duration_h": 3,
        "start_label": "14:00", "end_label": "16:00", "peak_probability": 80,
        "total_mm": 1.2, "start_iso": "2026-09-17T14:00", "end_iso": "2026-09-17T16:00",
        "horizon_h": 18,
    }
    s = page.evaluate(with_rain_payload(tl_rain))

    check("alert: banner visible for rain window", s["smartVisible"])
    check("alert: title correct", s["smartTitle"] == "Smart Rain Alert", s["smartTitle"])
    check("alert: badge correct", s["smartBadge"] == "Live", s["smartBadge"])
    check("alert: text cites 'in 3h' and clock label",
          "in 3h" in (s["smartText"] or "") and "14:00" in (s["smartText"] or ""), s["smartText"])
    check("alert: text cites peak probability", "80%" in (s["smartText"] or ""), s["smartText"])
    check("alert: text carries an action (umbrella)", "umbrella" in (s["smartText"] or ""), s["smartText"])

    expect_left = 100 * 3 / 18
    expect_width = max(100 * 3 / 18, 3)
    actual_left = float((s["barLeft"] or "0%").rstrip("%") or 0)
    actual_width = float((s["barWidth"] or "0%").rstrip("%") or 0)
    check("timeline: bar left matches starts_in_h", abs(actual_left - expect_left) < 0.6,
          f"left={actual_left} expected={expect_left:.1f}")
    check("timeline: bar width matches duration_h", abs(actual_width - expect_width) < 0.6,
          f"width={actual_width} expected={expect_width:.1f}")
    check("timeline: peak chip shows probability", "80" in (s["peak"] or ""), s["peak"])
    check("timeline: window note has clock labels",
          "14:00" in (s["note"] or "") and "16:00" in (s["note"] or ""), s["note"])
    check("a11y: track labelled", bool(s["trackLabel"]), s["trackLabel"])
    check("labels: now/+18h rendered", s["startLabel"] == "now" and s["endLabel"] == "+18h",
          f"{s['startLabel']}/{s['endLabel']}")

    # ---------- Language switching (cached evidence, no refetch) ----------
    page.click('.lang-btn[data-lang="hi"]')
    page.wait_for_timeout(400)
    hi = page.evaluate("""(() => ({
        title: document.getElementById('smart-rain-title')?.textContent,
        badge: document.getElementById('smart-rain-badge')?.textContent,
        text: document.getElementById('smart-rain-text')?.textContent,
        note: document.getElementById('rain-timeline-note')?.textContent,
        start: document.getElementById('rain-timeline-start-label')?.textContent,
    }))()""")
    check("i18n HI: alert title translated", hi["title"] == "स्मार्ट बारिश अलर्ट", hi["title"])
    check("i18n HI: badge translated", hi["badge"] == "लाइव", hi["badge"])
    check("i18n HI: alert text translated", "छाता" in (hi["text"] or ""), hi["text"])
    check("i18n HI: window note translated", "बारिश की विंडो" in (hi["note"] or ""), hi["note"])
    check("i18n HI: track labels translated", hi["start"] == "अभी", hi["start"])

    page.click('.lang-btn[data-lang="te"]')
    page.wait_for_timeout(400)
    te = page.evaluate("document.getElementById('smart-rain-text')?.textContent")
    check("i18n TE: alert text translated", "గొడుగు" in (te or ""), te)

    # Insights ledger (Today scope) under Telugu: generic rain alert localized.
    page.click('.nav-tab[data-view="view-insights"]')
    page.wait_for_timeout(400)
    ledger_has_en_rain = page.evaluate(
        "[...document.querySelectorAll('#insights-alerts-container div')].some(d => d.textContent.includes('likelihood of rain'))")
    check("insights: daily rain alert not English-only under TE", not ledger_has_en_rain)

    # Back to English for the no-rain path.
    page.click('.lang-btn[data-lang="en"]')
    page.wait_for_timeout(300)

    # ---------- Path 2: no rain window ----------
    tl_none = {
        "has_event": False, "starts_in_h": None, "duration_h": 0,
        "start_label": None, "end_label": None, "peak_probability": None,
        "total_mm": None, "start_iso": None, "end_iso": None, "horizon_h": 18,
    }
    s2 = page.evaluate(with_rain_payload(tl_none))
    page.click('.nav-tab[data-view="view-forecast"]')
    page.wait_for_timeout(300)
    check("no-rain: banner hidden", not s2["smartVisible"])
    check("no-rain: truthful note", "No rain expected" in (s2["note"] or ""), s2["note"])
    check("no-rain: peak chip cleared", (s2["peak"] or "") == "", s2["peak"])
    check("no-rain: bar empty", float((s2["barWidth"] or "0%").rstrip("%") or 0) == 0, s2["barWidth"])

    check("console: no unexpected JS errors", not console_errors, str(console_errors[:3]))
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} rain-feature E2E checks passed =====")
sys.exit(1 if failed else 0)
