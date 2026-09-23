"""Service-worker shell freshness e2e test.

Guards against the exact regression class seen after the hero redesign:
the shell used to be cache-first, so a browser that had cached the old
layout kept rendering it (fresh HTML + stale CSS = broken hero) until a
second reload. The shell must now be stale-while-revalidate, and its
observable guarantees are:

  1. First visit (empty cache)      -> network shell renders the NEW layout.
  2. Poisoned cache entry           -> next load still RENDERS (stale copy
                                       is applied) instead of breaking,
  3. ...and heals by the following  -> fresh shell served, hero card.
     load (no manual cache purge,
     no double reload needed).
  4. Version upgrade                -> activation deletes old cache buckets
                                       (exactly how v17 -> v18 cleaned up).
  5. Offline                        -> cached shell still boots the app.

Requires the Flask server on 127.0.0.1:5000.
"""
import os
import re

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
# Derive the current shell version from the source files so the test
# tracks future bumps instead of hardcoding one.
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
_sw_src = open(os.path.join(_frontend_dir, "sw.js"), encoding="utf-8").read()
_script_src = open(os.path.join(_frontend_dir, "script.js"), encoding="utf-8").read()
_m = re.search(r'CACHE_NAME\s*=\s*"([^"]+)"', _sw_src)
CACHE = _m.group(1) if _m else "weathergpt-shell-v19"
_mq = re.search(r'serviceWorker\.register\("sw\.js\?v=([^\"]+)"\)', _script_src)
SW_QUERY = f"sw.js?v={_mq.group(1)}" if _mq else "sw.js?v=19"
FRESH_MARKER = "cs-cloud-drift"  # present in the real /style.css
POISON = "/*POISONED-STALE-SHELL*/"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def style_rules_count(page):
    """Number of parsed rules in the stylesheet whose href is /style.css."""
    return page.evaluate("""
      () => {
        const sheet = [...document.styleSheets].find(s => (s.href || '').includes('/style.css'));
        if (!sheet) return -1;
        try { return sheet.cssRules.length; } catch (e) { return -2; }
      }
    """)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()

    # ---- 1. First visit: empty cache -> network shell, new layout ----
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)  # SW install + activate + claim
    controlled = page.evaluate("navigator.serviceWorker.controller !== null")
    check("SW controls the page after first load", controlled)
    card = page.locator("#view-forecast > div").first  # Ambient Sky Canvas Card (stable layout)
    check("first visit renders the hero card", card.count() == 1)
    box = card.bounding_box()
    check("hero card renders with its scene on first visit",
          box is not None and box["height"] > 100
          and page.evaluate("!!document.querySelector('#view-forecast > div #card-scene')"),
          f"h={box['height'] if box else None}px")
    rules = style_rules_count(page)
    check("applied stylesheet is the real shell CSS", rules > 50, f"cssRules={rules}")

    # ---- 2+3. Poison the cache: next load renders stale, heals by the load after ----
    page.evaluate(f"""
      (async () => {{
        const c = await caches.open('{CACHE}');
        await c.put('/style.css', new Response('{POISON}'));
      }})()
    """)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    rules_stale = style_rules_count(page)
    check("poisoned load still renders (stale copy applied, nothing breaks)",
          rules_stale == 0, f"cssRules={rules_stale}")

    page.wait_for_timeout(2500)  # let the background revalidation land
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    rules_fresh = style_rules_count(page)
    check("heals by the next load (fresh shell applied, no manual purge)",
          rules_fresh > 50, f"cssRules={rules_fresh}")
    healed = page.locator("#view-forecast > div").first.bounding_box()
    check("hero card renders again after healing",
          healed is not None and healed["height"] > 100,
          f"h={healed['height'] if healed else None}px")

    # ---- 5. Offline: cached shell still boots the app ----
    ctx.set_offline(True)
    page.reload(wait_until="load")
    page.wait_for_timeout(1200)
    check("offline boot renders the hero card", page.locator("#view-forecast > div").first.count() == 1)
    check("offline boot renders the card scene",
          page.evaluate("!!document.getElementById('card-scene')"))
    ctx.set_offline(False)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)

    # ---- Registration uses the cache-busting query (new script.js) ----
    reg_url = page.evaluate(
        "navigator.serviceWorker.getRegistration().then(r => (r && (r.active||r.installing||r.waiting)) ? (r.active||r.installing||r.waiting).scriptURL : null)")
    check("SW registered via versioned script URL", reg_url is not None and SW_QUERY in reg_url,
          f"scriptURL={reg_url}")

    # ---- 4. Version upgrade: activation deletes old cache buckets ----
    # Ship a real next-version worker (byte-different CACHE_NAME, exactly
    # what shipping v18 did to v17 buckets). Its activate handler must
    # delete every bucket except its own.
    frontend_dir = _frontend_dir
    sw_body = open(os.path.join(frontend_dir, "sw.js"), encoding="utf-8").read()
    test_sw = os.path.join(frontend_dir, "sw-upgrade-test.js")
    with open(test_sw, "w", encoding="utf-8") as f:
        f.write(sw_body.replace(CACHE, CACHE + "-upgrade-test"))
    try:
        page.evaluate("navigator.serviceWorker.register('sw-upgrade-test.js?v=19')")
        page.wait_for_timeout(4000)  # update -> install -> activate
        keys = page.evaluate("caches.keys()")
        check("version upgrade deleted the previous cache bucket",
              CACHE not in keys and (CACHE + "-upgrade-test") in keys, f"keys={keys}")
    finally:
        page.evaluate("navigator.serviceWorker.getRegistrations().then(rs => Promise.all(rs.map(r => r.unregister())))")
        os.remove(test_sw)

    browser.close()

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{passed}/{total} checks passed")
if passed != total:
    raise SystemExit(1)
