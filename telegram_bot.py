"""
telegram_bot.py  —  Whop checker, Telegram front-end
Commands:
  /start            greeting + buttons (incl. 📊 Database)
  /ccs              add up to 50 cards  (num|mm|yyyy|cvv, one per line)
  /proxy            list proxy sources
  /addproxy         add your own proxy  (user:pass@host:port) — tested before saving
  /whop [url]       run the check (asks: system proxies / add own)
  /live             retry the insufficient cards from the last run
  /db               show the database (which cards actually worked on Whop)

State is persisted to db.json (cards, proxies, last run) so it survives
crashes and restarts.
"""

import json
import os
import re
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot import types
import telebot.apihelper as _ah

# raise request timeouts for slow / high-latency links
_ah.READ_TIMEOUT = 120
_ah.CONNECT_TIMEOUT = 90

import whop_bot as W

# token from env (Railway) with a local fallback
TOKEN = os.environ.get("BOT_TOKEN", "8735014394:AAEitbTYPpH-m21rlmSg0nCO56DXvd3-Ov0")
bot = telebot.TeleBot(TOKEN)

DB_FILE = "db.json"
_lock = threading.RLock()
_db = None

DEFAULT_CHECKOUT = "https://whop.com/checkout/2onbgwXn2utmOapDAl-sTbB-xhGu-BQo9-ppzjRbOKz1Pc/"


# ---------- database ----------
def _default_db():
    return {
        "ccs": [],            # {number,exp_month,exp_year,cvc,raw,status,live,response,proxy,ts}
        "proxies_user": [],   # list of "user:pass@host:port" strings (tested OK)
        "proxies_system": [], # list of "user:pass@host:port" strings
        "last_run": [],       # {raw,status,proxy,response}
        "settings": {"checkout_url": DEFAULT_CHECKOUT},
    }


def load_db():
    global _db
    with _lock:
        if _db is None:
            if os.path.exists(DB_FILE):
                try:
                    _db = json.load(open(DB_FILE))
                except Exception:
                    _db = _default_db()
            else:
                _db = _default_db()
            for k, v in _default_db().items():
                _db.setdefault(k, v)
            if not _db["proxies_system"]:
                _db["proxies_system"] = list(W.PROXIES)
                save_db()
    return _db


def save_db():
    with _lock:
        tmp = DB_FILE + ".tmp"
        json.dump(_db, open(tmp, "w"), indent=2)
        os.replace(tmp, DB_FILE)


def get_db():
    return _db if _db is not None else load_db()


def cmd_args(m):
    """Text that follows a command, whether on the same line (after a space)
    or on the following lines. e.g. '/addproxy\\nuser:pass@host:port' works."""
    s = m.text.split(" ", 1)
    if len(s) > 1 and s[1].strip():
        return s[1]
    return "\n".join(m.text.splitlines()[1:])


# ---------- helpers ----------
def parse_ccs(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[|]", line)
        if len(parts) < 4:
            continue
        num, mm, yyraw, cvc = (p.strip() for p in parts[:4])
        yy = ("20" + yyraw) if len(yyraw) == 2 else yyraw
        out.append({"number": num, "exp_month": mm, "exp_year": yy,
                    "cvc": cvc, "raw": line})
    return out


def proxy_str_to_pw(s):
    s = s.strip()
    if "@" in s:
        auth, hostport = s.rsplit("@", 1)
        user, pw = auth.split(":", 1)
    else:
        hostport, user, pw = s, None, None
    d = {"server": "http://" + hostport}
    if user:
        d["username"], d["password"] = user, pw
    return d


def system_proxies():
    return [proxy_str_to_pw(p) for p in get_db()["proxies_system"]]


def user_proxies():
    return [proxy_str_to_pw(p) for p in get_db()["proxies_user"]]


def all_proxies():
    return system_proxies() + user_proxies()


def test_proxy(s):
    """Return (ok, detail). Tests reachability of whop.com through the proxy."""
    pstr = s if "://" in s else "http://" + s
    proxies = {"http": pstr, "https": pstr}
    try:
        r = requests.get("https://whop.com", proxies=proxies, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code < 500:
            return True, f"HTTP {r.status_code}"
        return False, f"bad HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:100]


def add_proxies_tested(chat_id, text):
    db = get_db()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        bot.send_message(chat_id, "⚠️ no proxy provided")
        return 0
    bot.send_message(chat_id, f"✦ testing {len(lines)} proxy(ies)…")
    added = 0
    report = []
    for l in lines:
        ok, detail = test_proxy(l)
        if ok:
            if l not in db["proxies_user"]:
                db["proxies_user"].append(l)
                added += 1
            report.append(f"✅ `{l}`  ({detail})")
        else:
            report.append(f"⛔ `{l}`  — {detail}")
    save_db()
    bot.send_message(chat_id, "\n".join(report) +
                     f"\n\n✦ added {added} · your proxies total {len(db['proxies_user'])}",
                     parse_mode="Markdown")
    return added


def send_result(chat_id, res):
    icon = {"success": "✅", "insufficient": "⚠️", "declined": "⛔",
            "missing": "❓", "error": "💥"}.get(res["status"], "ℹ️")
    line = (f"{icon} `…{res['last4']}` · {res['status'].upper()}\n"
            f"└ proxy {res['proxy'].replace('http://','')}")
    bot.send_message(chat_id, line, parse_mode="Markdown")
    if res["status"] == "success":
        try:
            with open(res["screenshot"], "rb") as ph:
                msg = bot.send_photo(
                    chat_id, ph,
                    caption=f"✅ HIT `…{res['last4']}`\n{res['response'][:600]}")
            try:
                bot.pin_chat_message(chat_id, msg.message_id)
            except Exception:
                pass
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ shot err: {e}")


def record_result(cc, res):
    db = get_db()
    for c in db["ccs"]:
        if c.get("raw") == cc.get("raw"):
            c["status"] = res["status"]
            c["response"] = res["response"][:500]
            c["proxy"] = res["proxy"]
            c["ts"] = time.time()
            c["live"] = (res["status"] == "success")
            break
    db["last_run"].append({
        "raw": cc.get("raw"), "status": res["status"],
        "proxy": res["proxy"], "response": res["response"][:300],
    })
    save_db()


# ---------- the check run ----------
def run_check(chat_id, url, proxy_list, ccs_override=None):
    db = get_db()
    target = ccs_override if ccs_override else db["ccs"]
    if not target:
        bot.send_message(chat_id, "⚠️ no cards. add with /ccs first")
        return
    if not proxy_list:
        bot.send_message(chat_id, "⚠️ no proxies available")
        return

    n = len(target)
    bot.send_message(chat_id,
                     f"✦ checking {n} card(s) · 4 workers · proxies {len(proxy_list)}")
    ex = ThreadPoolExecutor(max_workers=4)

    def worker(cc, idx):
        px = proxy_list[idx % len(proxy_list)]
        try:
            res = W.run_checkout(url, cc, proxy=px, headless=True,
                                 tag=f"tg_{cc['number'][-4:]}")
        except Exception as e:
            res = {"cc": cc["number"], "last4": cc["number"][-4:],
                   "status": "error", "response": str(e),
                   "screenshot": None, "proxy": px["server"]}
        return cc, res

    futures = [ex.submit(worker, cc, i) for i, cc in enumerate(target)]
    paired = []
    for f in futures:
        cc, res = f.result()
        record_result(cc, res)
        send_result(chat_id, res)
        paired.append((cc, res))
    ex.shutdown(wait=False)

    icon = {"success": "✅", "insufficient": "⚠️", "declined": "⛔",
            "missing": "❓", "error": "💥"}
    lines = [f"{icon.get(r['status'],'ℹ️')} `…{r['last4']}` {r['status'].upper()}"
             for _, r in paired]
    hits = sum(1 for _, r in paired if r["status"] == "success")
    ins = sum(1 for _, r in paired if r["status"] == "insufficient")
    bot.send_message(
        chat_id,
        "✦ *Results*\n" + "\n".join(lines) +
        f"\n\n✦ ✅{hits} live · ⚠️{ins} insufficient · use /live to retry",
        parse_mode="Markdown")


# ---------- database view ----------
def build_db_text():
    db = get_db()
    ccs = db["ccs"]
    total = len(ccs)
    live = [c for c in ccs if c.get("live")]
    ins = [c for c in ccs if c.get("status") == "insufficient"]
    dec = [c for c in ccs if c.get("status") == "declined"]
    other = [c for c in ccs if c.get("status") in ("missing", "error")]
    pend = [c for c in ccs if not c.get("status")]

    t = "📊 *Database*\n"
    t += f"├ cards total      : {total}\n"
    t += f"├ ✅ live (worked) : {len(live)}\n"
    t += f"├ ⚠️ insufficient   : {len(ins)}\n"
    t += f"├ ⛔ declined       : {len(dec)}\n"
    t += f"├ ❓ other/error    : {len(other)}\n"
    t += f"└ ◽ pending        : {len(pend)}\n\n"

    if live:
        t += "✅ *CARDS THAT WORKED ON WHOP:*\n"
        for c in live[:50]:
            t += f"  `…{c['number'][-4:]}`  {c.get('raw','')}\n"
        if len(live) > 50:
            t += f"  …and {len(live)-50} more\n"
    else:
        t += "✅ no working cards yet\n"

    t += (f"\nproxies — system {len(db['proxies_system'])} · "
          f"your {len(db['proxies_user'])} (tested OK)")
    return t


def send_db(chat_id, message_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="m_db"))
    text = build_db_text()
    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id,
                                  parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


# ---------- handlers ----------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📊 Database", callback_data="m_db"))
    kb.add(types.InlineKeyboardButton("➕ Add Proxy", callback_data="m_addp"),
           types.InlineKeyboardButton("⚡ Proxies", callback_data="m_prox"))
    kb.add(types.InlineKeyboardButton("▶️ Run /whop", callback_data="m_whop"))
    bot.send_message(
        m.chat.id,
        "⚡ *Whop Checker*\n\n"
        "The database is saved automatically — surviving restarts.\n\n"
        "/ccs — add cards (up to 50)\n"
        "/whop `<url>` — run check\n"
        "/proxy — list proxies\n"
        "/addproxy — add + test your proxy\n"
        "/live — retry insufficient\n"
        "/db — view database",
        parse_mode="Markdown", reply_markup=kb)


@bot.message_handler(commands=["ccs"])
def cmd_ccs(m):
    text = cmd_args(m)
    if not text.strip():
        bot.send_message(m.chat.id,
                         "✦ send: /ccs then cards one per line\n`num|mm|yyyy|cvv`",
                         parse_mode="Markdown")
        return
    new = parse_ccs(text)
    if not new:
        bot.send_message(m.chat.id,
                         "⚠️ no valid cards (need `num|mm|yyyy|cvv`)",
                         parse_mode="Markdown")
        return
    db = get_db()
    room = 50 - len(db["ccs"])
    if room <= 0:
        bot.send_message(m.chat.id, "⚠️ limit 50 reached")
        return
    add = new[:room]
    for c in add:
        c.update({"status": "", "live": False, "response": "", "proxy": "", "ts": 0})
        db["ccs"].append(c)
    save_db()
    bot.send_message(m.chat.id, f"✦ added {len(add)} · total {len(db['ccs'])}/50")


@bot.message_handler(commands=["proxy"])
def cmd_proxy(m):
    db = get_db()
    bot.send_message(
        m.chat.id,
        f"✦ proxies\n├ system : {len(db['proxies_system'])}\n"
        f"└ your    : {len(db['proxies_user'])} (all tested OK)\n\n"
        f"use /addproxy to append",
        parse_mode="Markdown")


@bot.message_handler(commands=["addproxy"])
def cmd_addproxy(m):
    text = cmd_args(m)
    if not text.strip():
        bot.send_message(m.chat.id, "✦ /addproxy `user:pass@host:port`",
                         parse_mode="Markdown")
        return
    add_proxies_tested(m.chat.id, text)


@bot.message_handler(commands=["whop"])
def cmd_whop(m):
    body = cmd_args(m)
    blines = body.splitlines()
    url_line = next((l.strip() for l in blines if l.strip().startswith("http")), None)
    if not url_line:
        bot.send_message(m.chat.id, "✦ /whop `<checkout url>`",
                         parse_mode="Markdown")
        return
    url = url_line
    db = get_db()
    # allow pasting cards on the following lines, e.g.
    #   /whop <url>
    #   5328398287077228|05|2029|211
    card_text = "\n".join(l for l in blines if not l.strip().startswith("http"))
    added = 0
    if card_text.strip():
        new = parse_ccs(card_text)
        room = 50 - len(db["ccs"])
        for c in new[:room]:
            c.update({"status": "", "live": False, "response": "",
                      "proxy": "", "ts": 0})
            db["ccs"].append(c)
            added += 1
    db["settings"]["checkout_url"] = url
    save_db()
    PENDING[m.chat.id] = url
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⚡ System Proxies", callback_data="px_sys"))
    kb.add(types.InlineKeyboardButton("➕ Add Own Proxy", callback_data="px_add"))
    card_note = f" · added {added} card(s)" if added else ""
    bot.send_message(m.chat.id, f"✦ choose proxy source:{card_note}",
                     reply_markup=kb)


@bot.message_handler(commands=["live"])
def cmd_live(m):
    db = get_db()
    ins = [c for c in db["ccs"] if c.get("status") == "insufficient"]
    if not ins:
        bot.send_message(m.chat.id, "✦ nothing insufficient to retry")
        return
    url = db["settings"].get("checkout_url")
    if not url:
        bot.send_message(m.chat.id, "⚠️ run /whop first so I know the url")
        return
    bot.send_message(m.chat.id, f"✦ live retry · {len(ins)} insufficient")
    run_check(m.chat.id, url, all_proxies(), ccs_override=ins)


@bot.message_handler(commands=["db"])
def cmd_db(m):
    send_db(m.chat.id)


PENDING = {}        # chat_id -> checkout url (awaiting proxy choice)
AWAIT_PROXY = {}    # chat_id -> checkout url (next msg is a proxy)


@bot.callback_query_handler(func=lambda c: c.data in ("px_sys", "px_add"))
def cb_proxy(c):
    url = PENDING.get(c.message.chat.id) or get_db()["settings"].get("checkout_url")
    if not url:
        bot.edit_message_text("⚠️ run /whop first", c.message.chat.id,
                              c.message.message_id)
        return
    if c.data == "px_sys":
        bot.edit_message_text("✦ using system proxies", c.message.chat.id,
                              c.message.message_id)
        run_check(c.message.chat.id, url, system_proxies())
    else:
        AWAIT_PROXY[c.message.chat.id] = url
        bot.edit_message_text("➕ send your proxy: `user:pass@host:port`",
                              c.message.chat.id, c.message.message_id,
                              parse_mode="Markdown")


@bot.message_handler(func=lambda m: AWAIT_PROXY.get(m.chat.id))
def await_proxy_msg(m):
    url = AWAIT_PROXY.pop(m.chat.id)
    added = add_proxies_tested(m.chat.id, m.text)
    if added > 0:
        bot.send_message(m.chat.id, "✦ running with your proxies")
        run_check(m.chat.id, url, user_proxies() or system_proxies())
    else:
        bot.send_message(m.chat.id, "⚠️ no working proxy added — run /whop again")


@bot.callback_query_handler(func=lambda c: c.data.startswith("m_"))
def cb_menu(c):
    bot.answer_callback_query(c.id)
    if c.data == "m_db":
        send_db(c.message.chat.id, c.message.message_id)
    elif c.data == "m_addp":
        bot.send_message(c.message.chat.id,
                         "➕ /addproxy `user:pass@host:port`",
                         parse_mode="Markdown")
    elif c.data == "m_prox":
        cmd_proxy(c.message)
    elif c.data == "m_whop":
        bot.send_message(c.message.chat.id,
                         "▶️ /whop `<checkout url>`", parse_mode="Markdown")


if __name__ == "__main__":
    load_db()
    # clear any webhook (e.g. left by a previous/foreign bot on this token)
    # so long-polling works and no stale updates are replayed
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    print("[whop checker bot online]")
    bot.infinity_polling()
