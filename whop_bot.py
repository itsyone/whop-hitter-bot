"""
whop_bot.py
Full Whop checkout autofill:
  - billing address (picked NEW from a pool of 50 random USA addresses)
  - random email  @rabbitvore.com
  - provided card (Basis Theory iframes)

Does NOT submit. Edit CARD below; addresses/emails are generated.
Playwright >= 1.40  |  pip install playwright && playwright install chromium
Run: python whop_bot.py
"""

import json
import os
import random
import string
import tempfile
import subprocess
import glob
from playwright.sync_api import sync_playwright

CHECKOUT_URL = "https://whop.com/checkout/2onbgwXn2utmOapDAl-sTbB-xhGu-BQo9-ppzjRbOKz1Pc/"

# ===== CARD (provided) =====
CARD = {
    "number": "5328398287077228",
    "exp_month": "05",
    "exp_year": "2029",   # 2-digit used in form: 29
    "cvc": "211",
}
# ===========================

ADDRESS_POOL_FILE = "addresses.json"
POOL_SIZE = 50
EMAIL_DOMAIN = "rabbitvore.com"

# ===== PROXIES (rotated every run) =====
PROXIES = [
    "208280:Jdp5rbuDkbCb@196.51.106.142:8800",
    "208280:Jdp5rbuDkbCb@196.51.106.238:8800",
    "208280:Jdp5rbuDkbCb@196.51.106.202:8800",
    "208280:Jdp5rbuDkbCb@196.51.106.253:8800",
    "208280:Jdp5rbuDkbCb@196.51.109.56:8800",
    "208280:Jdp5rbuDkbCb@196.51.106.169:8800",
    "208280:Jdp5rbuDkbCb@196.51.109.16:8800",
    "208280:Jdp5rbuDkbCb@196.51.109.250:8800",
    "208280:Jdp5rbuDkbCb@196.51.109.207:8800",
    "208280:Jdp5rbuDkbCb@196.51.109.178:8800",
]

LAST_PROXY = {"v": None}

def pick_proxy():
    """Rotate to a NEW proxy each run (never repeat the last one)."""
    choices = [p for p in PROXIES if p != LAST_PROXY["v"]] or PROXIES
    p = random.choice(choices)
    LAST_PROXY["v"] = p
    auth, hostport = p.rsplit("@", 1)
    user, pw = auth.split(":", 1)
    return {"server": "http://" + hostport, "username": user, "password": pw}

# ===== HUMAN / FINGERPRINT RANDOMIZATION =====
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
VIEWPORTS = [
    (1366, 768), (1536, 864), (1440, 900), (1920, 1080), (1280, 720),
]
# US timezones + a representative city center (lat, lon) for geolocation
US_GEO = [
    ("America/New_York", 40.7128, -74.0060),
    ("America/Chicago", 41.8781, -87.6298),
    ("America/Denver", 39.7392, -104.9903),
    ("America/Los_Angeles", 34.0522, -118.2437),
    ("America/Phoenix", 33.4484, -112.0740),
    ("America/Chicago", 29.7604, -95.3698),
]

STEALTH_JS = """
() => {
  // kill the webdriver flag
  Object.defineProperty(navigator, 'webdriver', { get: () => false });
  // fake chrome object
  if (!window.chrome) { window.chrome = {}; }
  window.chrome.runtime = {};
  // languages / platform
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  // spoofed hardware concurrency + deviceMemory
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => %D_concurrency% });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => %D_memory% });
  // plugins / mimeTypes (non-empty)
  Object.defineProperty(navigator, 'plugins', { get: () => [
    { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer' },
    { name: 'Chrome PDF Viewer', description: 'Portable Document Format', filename: 'mhjfbmdgcfjbbpaeojklgcpcanfnfnlo' },
    { name: 'Native Client', description: '', filename: 'ppapi' }
  ] });
  // permissions query lies about notification
  const origQuery = window.Notification ? window.Notification.permission : 'denied';
  if (window.Notification) { try { Object.defineProperty(window.Notification, 'permission', { get: () => 'default' }); } catch(e){} }
  // block automation detection via CDP
  window.cdc_adoQpoasnfa76pfcZLmc3J3ossfp = undefined;
}
"""

def build_stealth(concurrency, memory):
    return (STEALTH_JS
            .replace("%D_concurrency%", str(concurrency))
            .replace("%D_memory%", str(memory)))

def human_typing_delay():
    return random.uniform(25, 70)

def human_pause(min_s=0.4, max_s=1.6):
    return random.uniform(min_s, max_s)


# Curated real-ish US (city, state-abbr, zip) tuples for realism.
US_PLACES = [
    ("Springfield", "IL", "62704"), ("Austin", "TX", "73301"), ("Denver", "CO", "80202"),
    ("Portland", "OR", "97201"), ("Seattle", "WA", "98101"), ("Miami", "FL", "33101"),
    ("Boston", "MA", "02108"), ("Phoenix", "AZ", "85001"), ("Atlanta", "GA", "30301"),
    ("Nashville", "TN", "37201"), ("Columbus", "OH", "43215"), ("Detroit", "MI", "48201"),
    ("Minneapolis", "MN", "55401"), ("Salt Lake City", "UT", "84101"), ("Las Vegas", "NV", "89101"),
    ("San Diego", "CA", "92101"), ("Sacramento", "CA", "95814"), ("Dallas", "TX", "75201"),
    ("Houston", "TX", "77001"), ("Chicago", "IL", "60601"), ("Newark", "NJ", "07102"),
    ("Pittsburgh", "PA", "15201"), ("Baltimore", "MD", "21201"), ("Charlotte", "NC", "28201"),
    ("Indianapolis", "IN", "46201"), ("Kansas City", "MO", "64101"), ("Oklahoma City", "OK", "73101"),
    ("Milwaukee", "WI", "53201"), ("Cincinnati", "OH", "45201"), ("Louisville", "KY", "40201"),
    ("Richmond", "VA", "23218"), ("Buffalo", "NY", "14201"), ("Albuquerque", "NM", "87101"),
    ("Tucson", "AZ", "85701"), ("Fresno", "CA", "93701"), ("Omaha", "NE", "68101"),
    ("Tampa", "FL", "33601"), ("Cleveland", "OH", "44101"), ("St. Louis", "MO", "63101"),
    ("Raleigh", "NC", "27601"), ("Boise", "ID", "83701"), ("Spokane", "WA", "99201"),
]
STREETS = ["Birchwood Lane", "Maple Avenue", "Oak Hollow Drive", "Cedar Ridge Road",
           "Elm Street", "Sunset Boulevard", "Lakeview Court", "Pinecrest Way",
           "Willow Branch Lane", "Ironhorse Trail", "Magnolia Drive", "Foxglove Circle"]
FIRST = ["Jordan", "Avery", "Kai", "Riley", "Morgan", "Devon", "Sasha", "Quinn",
         "Theo", "Lena", "Marco", "Noor", "Wren", "Cyrus", "Indie", "Beau"]
LAST = ["Mercer", "Vance", "Okafor", "Castellano", "Nakamura", "Reyes", "Bianchi",
        "Holloway", "Petrov", "Sullivan", "Abara", "Delacroix", "Voss", "Tran"]


def gen_address():
    city, st, zipc = random.choice(US_PLACES)
    return {
        "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
        "line1": f"{random.randint(100, 9999)} {random.choice(STREETS)}",
        "line2": "",
        "city": city,
        "state": st,
        "zip": zipc,
        "country": "US",
    }


def chromium_missing_libs():
    """Return the list of missing shared libraries for the Chromium binary,
    or None if it can't be determined. Useful to diagnose launch crashes."""
    try:
        base = os.path.expanduser("~/.cache/ms-playwright")
        bins = (glob.glob(os.path.join(base, "chromium*", "chrome-linux", "chrome"))
                + glob.glob(os.path.join(base, "chromium_headless_shell*",
                                         "chrome-linux", "headless_shell")))
        if not bins:
            return "chromium binary not found in " + base
        out = subprocess.run(["ldd", bins[0]], capture_output=True, text=True,
                             timeout=30)
        miss = [ln for ln in out.stdout.splitlines() if "not found" in ln]
        return "\n".join(miss) if miss else "no missing libs reported by ldd"
    except Exception as e:
        return f"(ldd check failed: {e})"


def _write_pool(pool):
    # unique temp file per call so concurrent workers don't clobber each other
    d = os.path.dirname(os.path.abspath(ADDRESS_POOL_FILE)) or "."
    fd, tmppath = tempfile.mkstemp(dir=d, prefix="addr_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(pool, f, indent=2)
        os.replace(tmppath, ADDRESS_POOL_FILE)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


def load_pool():
    if os.path.exists(ADDRESS_POOL_FILE):
        try:
            with open(ADDRESS_POOL_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("addresses"), list) \
                    and data["addresses"]:
                data.setdefault("used", [])
                return data
        except Exception:
            # corrupt / empty file — discard and regenerate
            pass
    pool = {"used": [], "addresses": [gen_address() for _ in range(POOL_SIZE)]}
    _write_pool(pool)
    return pool


def get_new_address():
    pool = load_pool()
    avail = [a for i, a in enumerate(pool["addresses"]) if i not in pool["used"]]
    if not avail:  # all used -> regenerate a fresh 50
        pool = {"used": [], "addresses": [gen_address() for _ in range(POOL_SIZE)]}
        avail = pool["addresses"]
    addr = random.choice(avail)
    idx = pool["addresses"].index(addr)
    pool["used"].append(idx)
    _write_pool(pool)
    return addr


def random_email():
    pre = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(8, 13)))
    return f"{pre}@{EMAIL_DOMAIN}"


# Name = random keyword strings containing "rabbit vore" / "rabbit vorist"
RABBIT_BASES = ["rabbit vore", "rabbit vorist", "rabbitvore", "rabbit vorist"]
RABBIT_WORDS = ["zelda", "mosswick", "hollow", "thorn", "vesper", "cinder", "lumen",
                "sable", "wren", "onyx", "feral", "mire", "ash", "glade", "pyre",
                "drift", "ember", "coy", "marrow", "sol", "nyx", "bram", "quill"]


def rabbit_name():
    base = random.choice(RABBIT_BASES)
    return f"{base} {random.choice(RABBIT_WORDS)}"


# --- field filling (visible fill + JS fallback for clipped React inputs) ---
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
    if value in ("", None):
        return 0
    done = 0
    for sel in FIELD_SELECTORS[key]:
        for el in page.locator(sel).all():
            try:
                tag = el.element_handle().evaluate("n => n.tagName.toLowerCase()")
                if tag == "select":
                    el.select_option(value=value, timeout=1500)
                else:
                    try:
                        el.fill(value, timeout=1500)
                    except Exception:
                        el.evaluate(
                            """(node, val) => {
                                const setter = Object.getOwnPropertyDescriptor(
                                    Object.getPrototypeOf(node), 'value').set;
                                setter.call(node, val);
                                node.dispatchEvent(new Event('input', {bubbles:true}));
                                node.dispatchEvent(new Event('change', {bubbles:true}));
                            }""", value)
                done += 1
            except Exception:
                continue
    print(f"[{'ok' if done else 'skip'}] {key} -> {done}")
    return done


def frame_by_keyword(page, kw):
    for f in page.frames:
        if kw in f.url:
            return f
    return None


def inject_hidden(page, key, value):
    """Pure JS value injection for clipped/hidden React inputs
    (city/state/zip/line2). Must NOT use .fill() or Whop re-renders
    and wipes line1."""
    if value in ("", None):
        return 0
    done = 0
    for sel in FIELD_SELECTORS[key]:
        for el in page.locator(sel).all():
            try:
                el.evaluate(
                    """(node, val) => {
                        const setter = Object.getOwnPropertyDescriptor(
                            Object.getPrototypeOf(node), 'value').set;
                        setter.call(node, val);
                        node.dispatchEvent(new Event('input', {bubbles:true}));
                        node.dispatchEvent(new Event('change', {bubbles:true}));
                    }""", value)
                done += 1
            except Exception:
                continue
    print(f"[{'ok' if done else 'skip'}] {key} (hidden) -> {done}")
    return done


def fill_first(page, sel, value):
    """Fill only the first matching element (Whop renders duplicate
    address blocks; filling both cancels out via shared state)."""
    try:
        page.locator(sel).first.fill(value, timeout=3000)
        print(f"[ok] {sel} -> 1 (first)")
        return 1
    except Exception as e:
        print(f"[skip] {sel}: {e}")
        return 0


def fill_any(page, selectors, value):
    """Try several selectors for the same field; Whop's form field names
    vary, so fall back through known possibilities."""
    for sel in selectors:
        try:
            page.locator(sel).first.fill(value, timeout=2500)
            print(f"[ok] {sel} -> name")
            return 1
        except Exception:
            continue
    print("[skip] name (no selector matched)")
    return 0


def fill_card(page, cc=CARD):
    num_f = frame_by_keyword(page, "card-number")
    exp_f = frame_by_keyword(page, "card-expiration")
    cvc_f = frame_by_keyword(page, "card-verification")
    if num_f:
        num_f.fill('input', cc["number"])
        print("[ok] card number")
    if exp_f:
        num_f_exp = f"{cc['exp_month']} / {cc['exp_year'][2:]}"
        exp_f.fill('input', num_f_exp)
        print("[ok] card expiration")
    if cvc_f:
        cvc_f.fill('input', cc["cvc"])
        print("[ok] card cvc")


def jitter(page):
    """Random mouse drift + micro-pause so the session doesn't look scripted."""
    try:
        w = page.viewport_size or {"width": 1366, "height": 768}
        x = random.randint(50, max(60, w["width"] - 50))
        y = random.randint(50, max(60, w["height"] - 50))
        page.mouse.move(x, y, steps=random.randint(3, 10))
    except Exception:
        pass
    page.wait_for_timeout(int(human_pause(0.3, 1.2) * 1000))


def run_checkout(checkout_url, cc, proxy=None, headless=True, tag="run"):
    """Core checker. Fills a fresh billing identity + the given card through a
    (rotated) proxy with a fresh stealth fingerprint, submits, and returns a
    result dict: {cc, last4, status, response, screenshot, proxy}."""
    addr = get_new_address()
    email = random_email()
    name = rabbit_name()
    if proxy is None:
        proxy = pick_proxy()
    print(f"[{tag}] START card …{cc['number'][-4:]} via {proxy['server']}", flush=True)

    ua = random.choice(USER_AGENTS)
    vw, vh = random.choice(VIEWPORTS)
    tz, lat, lon = random.choice(US_GEO)
    concurrency = random.choice([4, 8, 12, 16])
    memory = random.choice([4, 8, 16])
    last4 = cc["number"][-4:]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            proxy=proxy,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-infobars", f"--window-size={vw},{vh}",
                  "--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            user_agent=ua,
            viewport={"width": vw, "height": vh},
            locale="en-US",
            timezone_id=tz,
            geolocation={"latitude": lat, "longitude": lon},
            permissions=[],
            color_scheme="light",
        )
        stealth = build_stealth(concurrency, memory)
        context.add_init_script(stealth)
        page = context.new_page()
        page.add_init_script(stealth)
        page.set_default_timeout(30000)

        print(f"[{tag}] goto {checkout_url}", flush=True)
        page.goto(checkout_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        jitter(page)
        page.mouse.wheel(0, random.randint(120, 360))
        page.wait_for_timeout(int(human_pause(0.4, 1.0) * 1000))

        try:
            page.locator('select[name="country"]').first.select_option("US", timeout=4000)
        except Exception:
            pass
        jitter(page)
        fill_any(page, ['input[name="name"]',
                        'input[autocomplete="name"]',
                        'input[name="cardName"]',
                        'input[placeholder*="Name" i]'], name)
        jitter(page)

        full = f"{addr['line1']}, {addr['city']}, {addr['state']} {addr['zip']}"
        el = page.locator('input[name="line1"]').first

        def good_sel(cur):
            return bool(cur) and cur.strip() != full and any(c.isalpha() for c in cur) and len(cur.strip()) > 3

        selected = False
        for attempt in range(3):
            el.click()
            el.type(full, delay=human_typing_delay())
            page.wait_for_timeout(1500)
            cur = el.input_value()
            if good_sel(cur):
                selected = True
                break
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            cur = el.input_value()
            if good_sel(cur):
                selected = True
                break
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            cur = el.input_value()
            if good_sel(cur):
                selected = True
                break
        print(f"[{tag}] address selected={selected}", flush=True)

        inject_hidden(page, "state", addr["state"])
        jitter(page)
        fill_first(page, 'input[name="email"]', email)
        jitter(page)
        fill_card(page, cc)
        jitter(page)

        page.screenshot(path=f"{tag}_{last4}_pre.png", full_page=True)
        try:
            page.get_by_role("button", name="Get access").click(timeout=8000, delay=20)
        except Exception:
            pass

        page.wait_for_timeout(4000)
        try:
            page.wait_for_function(
                "() => { const b=document.querySelector('button[type=submit]'); "
                "return b && !/process|loading|.../i.test(b.innerText); }",
                timeout=10000)
        except Exception:
            pass
        print(f"[{tag}] submitted, reading result", flush=True)

        try:
            resp = page.inner_text("body")
        except Exception:
            resp = ""
        shot = f"{tag}_{last4}.png"
        page.screenshot(path=shot, full_page=True)
        browser.close()

    low = resp.lower()
    if "missing fields" in low:
        status, reason = "missing", "Missing required fields"
    elif "insufficient" in low:
        status, reason = "insufficient", "Insufficient funds"
    elif ("security reasons" in low or "couldn't be processed" in low
          or "try a different payment" in low or "couldn" in low):
        status, reason = "declined", "Card declined by issuer"
    else:
        status, reason = "success", "Payment approved"

    print(f"[{tag}] DONE status={status}", flush=True)
    return {"cc": cc["number"], "last4": last4, "status": status,
            "response": reason, "screenshot": shot, "proxy": proxy["server"]}


def main():
    res = run_checkout(CHECKOUT_URL, CARD, headless=False)
    print(res)


if __name__ == "__main__":
    main()
