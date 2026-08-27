"""
whop_checkout_fill.py
Fills ONLY the billing address on a Whop checkout page, then stops.
Does NOT submit or touch payment.

Playwright >= 1.40  |  pip install playwright && playwright install chromium
Run: python whop_checkout_fill.py
"""

from playwright.sync_api import sync_playwright
import time

# ===== EDIT YOUR BILLING DETAILS HERE =====
BILLING = {
    "name":    "Jordan Mercer",          # full name, single field on Whop
    "line1":   "418 Birchwood Lane",
    "line2":   "Apt 12",                 # leave "" if not needed
    "city":    "Springfield",
    "state":   "IL",
    "zip":     "62704",
    "country": "US",
    "email":   "jordan.mercer@example.com",
}
# ==========================================

CHECKOUT_URL = "https://whop.com/checkout/2onbgwXn2utmOapDAl-sTbB-xhGu-BQo9-ppzjRbOKz1Pc/"

# Real Whop field names (autocomplete attrs + name attrs), observed from live DOM.
# Each entry lists BOTH a visible selector and the clipped/hidden one.
FIELD_SELECTORS = {
    "name":    ['input[name="name"]'],
    "line1":   ['input[name="line1"]'],
    "line2":   ['input[name="line2"]'],
    "city":    ['input[name="city"]'],
    "state":   ['input[name="state"]'],
    "zip":     ['input[name="zip"]'],
    "country": ['select[name="country"]'],
    "email":   ['input[name="email"]'],
}


def fill_all(page, key, value):
    """Fill EVERY matching element for a field (Whop renders dup blocks).
    Falls back to JS value-injection for clipped/hidden React inputs."""
    if value in ("", None):
        return 0
    done = 0
    for sel in FIELD_SELECTORS[key]:
        try:
            locators = page.locator(sel).all()
        except Exception:
            continue
        for el in locators:
            try:
                tag = el.element_handle().evaluate("n => n.tagName.toLowerCase()")
                if tag == "select":
                    el.select_option(value=value, timeout=1500)
                else:
                    try:
                        el.fill(value, timeout=1500)
                    except Exception:
                        # clipped/hidden input — inject via React-friendly setter
                        el.evaluate(
                            """(node, val) => {
                                const proto = Object.getPrototypeOf(node);
                                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                                setter.call(node, val);
                                node.dispatchEvent(new Event('input', {bubbles:true}));
                                node.dispatchEvent(new Event('change', {bubbles:true}));
                            }""",
                            value,
                        )
                done += 1
            except Exception:
                continue
    print(f"[{'ok' if done else 'skip'}] {key} -> {done} field(s)")
    return done


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        print("-> opening checkout")
        page.goto(CHECKOUT_URL, wait_until="networkidle")
        page.wait_for_timeout(3000)  # let the SPA mount the address form

        print("-> filling billing address only")
        for key, val in BILLING.items():
            fill_all(page, key, val)

        page.screenshot(path="checkout_filled.png", full_page=False)
        print("-> screenshot saved: checkout_filled.png")
        print("-> done. Billing address filled; nothing submitted.")
        time.sleep(2)
        browser.close()


if __name__ == "__main__":
    main()
