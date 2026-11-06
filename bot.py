import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from html import escape, unescape
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)

# Mahalliy .env fayli mavjud bo'lsa avtomatik yuklash
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

from web_server import start_health_server_in_background

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Token va asosiy sozlamalar
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "8191930658"))
DATA_FILE = os.getenv("DATA_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://muhammadjon1505f-dot.github.io/smm-webapp/")

# Provayder valyutasi UZS bo'lmasa, shu kurs bilan so'mga o'giriladi
USD_TO_UZS = 12800
# Provayder narxiga ustama (1.0 = ustamasiz, 1.3 = +30%)
PRICE_MARKUP = 1.0


# ---------------------------------------------------------------- DATA
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {}


def ensure_service_ids(data):
    """Botga ulangan har bir xizmatga o'zining ketma-ket ID sini beradi: 1, 2, 3, ...
    ID yo'q xizmatlar (yangi yoki eski) qo'shilish tartibida raqam oladi; o'chirilgan ID qayta ishlatilmaydi."""
    items = data.get("items")
    if not items:
        return
    st = data.setdefault("settings", {})
    all_items = [it for lst in items.values() for it in lst]
    last = max([st.get("last_service_id", 0) or 0] + [it.get("bid", 0) or 0 for it in all_items])
    for it in all_items:
        if not it.get("bid"):
            last += 1
            it["bid"] = last
    st["last_service_id"] = last


def save_data(data):
    ensure_service_ids(data)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


db_data = load_data()
for k, v in (("services", {}), ("apis", []), ("items", {}), ("users", {}), ("orders", []), ("ui", {}), ("settings", {}), ("payment_systems", []), ("deposits", [])):
    db_data.setdefault(k, v)
db_data["ui"].setdefault("buttons", {})
db_data["ui"].setdefault("net_styles", {})
db_data["ui"].setdefault("cat_styles", {})
db_data["ui"].setdefault("texts", {})
db_data["ui"].setdefault("admin_buttons", {})
db_data["settings"].setdefault("referral_bonus", 0)
db_data["settings"].setdefault("daily_bonus", 0)
db_data["settings"].setdefault("min_deposit", 0)
db_data["settings"].setdefault("deposit_desc", "")
db_data["settings"].setdefault("group_broadcast", {
    "enabled": True,
    "chat_id": -1004283476663,
    "chat_title": "Ybanat",
    "interval": 300,
    "message": (
        "🚀 <b>Muxa SMM — Tezkor va Arzon SMM Xizmatlari!</b>\n\n"
        "✨ <b>Telegram:</b> Obunachilar, ko'rishlar, reaksiyalar\n"
        "📸 <b>Instagram:</b> Obunachilar, layklar, video prosmotr\n"
        "🎬 <b>YouTube & TikTok:</b> 100% ishonchli va sifatli!\n\n"
        "⚡️ Qulay narxlar va 24/7 avtomatik yetkazib berish!\n\n"
        "👇 Buyurtma berish uchun botimizga kiring:\n"
        "👉 @Muxa_aibot"
    )
})
for _p in db_data["payment_systems"]:
    _p.setdefault("active", True)
    _p.setdefault("desc", "")
for _u in db_data["users"].values():
    _u.setdefault("blocked", False)
    _u.setdefault("referrals", 0)
    _u.setdefault("last_bonus_date", None)
    if "deposited" not in _u:
        _u["deposited"] = _u.get("balance", 0) if _u.get("balance", 0) > 0 else 0
db_data["settings"].setdefault("last_order_id", 0)
_last_id = max([o.get("id", 0) or 0 for o in db_data["orders"]] + [db_data["settings"]["last_order_id"]])
for _o in db_data["orders"]:
    if not _o.get("id"):
        _last_id += 1
        _o["id"] = _last_id
db_data["settings"]["last_order_id"] = _last_id
save_data(db_data)

def should_use_proxy():
    proxy = os.getenv("TELEGRAM_PROXY")
    if not proxy:
        return None
    # PythonAnywhere ichki proxy domeni faqat PythonAnywhere xostingida mavjud
    if "proxy.server" in proxy:
        if os.getenv("PYTHONANYWHERE_DOMAIN") or os.getenv("PYTHONANYWHERE_SITE") or os.path.exists("/var/www"):
            return proxy
        # Aks holda 0-latency uchun proksisiz to'g'ridan-to'g'ri ulanish
        return None
    return proxy

TELEGRAM_PROXY = should_use_proxy()
if TELEGRAM_PROXY:
    from aiogram.client.session.aiohttp import AiohttpSession
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=TELEGRAM_PROXY))
else:
    bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())
_newly_registered = set()  # /start orqali endigina ro'yxatdan o'tgan foydalanuvchilar (referal uchun)
_support_questions = {}  # uid(str) -> foydalanuvchining oxirgi qo'llab-quvvatlash savoli
_bot_username = None


async def get_bot_username():
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username


def _has_icons(markup):
    if isinstance(markup, InlineKeyboardMarkup):
        return any(getattr(b, "icon_custom_emoji_id", None) for row in markup.inline_keyboard for b in row)
    if isinstance(markup, ReplyKeyboardMarkup):
        return any(getattr(b, "icon_custom_emoji_id", None) for row in markup.keyboard for b in row)
    return False


def _strip_icons(markup):
    if isinstance(markup, InlineKeyboardMarkup):
        return InlineKeyboardMarkup(inline_keyboard=[
            [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
            for row in markup.inline_keyboard
        ])
    if isinstance(markup, ReplyKeyboardMarkup):
        return ReplyKeyboardMarkup(keyboard=[
            [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
            for row in markup.keyboard
        ], resize_keyboard=markup.resize_keyboard)
    return markup


try:
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware

    class IconFallbackMiddleware(BaseRequestMiddleware):
        # Premium emoji ikonkasi qabul qilinmasa yoki tarmoq xatosi bo'lsa qayta ishlaydi
        async def __call__(self, make_request, bot, method):
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as e:
                # PythonAnywhere proksi 503 bersa yoki uzilsa, 0.3 soniyada 1 marta qayta urinish
                err_str = str(e).lower()
                if "503" in err_str or "unavailable" in err_str or "connection" in err_str:
                    await asyncio.sleep(0.3)
                    return await make_request(bot, method)
                raise
            except TelegramBadRequest as e:
                markup = getattr(method, "reply_markup", None)
                if "not modified" in str(e).lower() or not _has_icons(markup):
                    raise
                fixed = method.model_copy(update={"reply_markup": _strip_icons(markup)})
                return await make_request(bot, fixed)

    bot.session.middleware(IconFallbackMiddleware())
except ImportError:
    pass


class AdminState(StatesGroup):
    waiting_for_network_name = State()
    waiting_for_category_name = State()
    waiting_for_api_url = State()
    waiting_for_api_key = State()
    waiting_for_edit_url = State()
    waiting_for_edit_key = State()
    waiting_for_service_id = State()
    waiting_for_edit_service_id = State()
    waiting_for_edit_net_name = State()
    waiting_for_edit_cat_name = State()
    waiting_for_srv_value = State()
    waiting_for_edit_api_name = State()
    waiting_for_user_query = State()
    waiting_for_balance_amount = State()
    waiting_for_btn_name = State()
    waiting_for_admin_btn_name = State()
    waiting_for_info_text = State()
    waiting_for_referral_amount = State()
    waiting_for_daily_bonus_amount = State()
    waiting_for_pay_name = State()
    waiting_for_pay_card = State()
    waiting_for_pay_owner = State()
    waiting_for_edit_pay_name = State()
    waiting_for_edit_pay_card = State()
    waiting_for_edit_pay_owner = State()
    waiting_for_edit_pay_desc = State()
    waiting_for_min_deposit = State()
    waiting_for_deposit_desc = State()
    waiting_for_force_channel = State()
    waiting_for_order_channel = State()


RENAME_INPUT_STATES = (
    AdminState.waiting_for_btn_name,
    AdminState.waiting_for_admin_btn_name,
    AdminState.waiting_for_network_name,
    AdminState.waiting_for_edit_net_name,
    AdminState.waiting_for_category_name,
    AdminState.waiting_for_edit_cat_name,
    AdminState.waiting_for_srv_value,
)
not_renaming = ~StateFilter(*RENAME_INPUT_STATES)


class OrderState(StatesGroup):
    waiting_for_link = State()
    waiting_for_quantity = State()


class DepositState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_receipt = State()


class StarsDepositState(StatesGroup):
    waiting_for_stars = State()


class StarsAdminState(StatesGroup):
    waiting_for_rate = State()


class OrderInfoState(StatesGroup):
    waiting_for_id = State()


class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()


class ServiceSearchState(StatesGroup):
    waiting_for_query = State()


# ---------------------------------------------------------------- HELPERS
def nets():
    return list(db_data["services"].keys())


def get_net(ni):
    n = nets()
    return n[ni] if 0 <= ni < len(n) else None


def get_cat(net, ci):
    cats = db_data["services"].get(net, [])
    return cats[ci] if 0 <= ci < len(cats) else None


def item_key(net, cat):
    return f"{net}|{cat}"


def get_items(net, cat):
    return db_data["items"].get(item_key(net, cat), [])


def api_title(api):
    if api.get("name"):
        return api["name"]
    url = api.get("url", "")
    return urlparse(url).netloc or url


def find_api_by_url(url):
    for a in db_data["apis"]:
        if a.get("url") == url:
            return a
    return None


def get_user(uid):
    u = db_data["users"].setdefault(str(uid), {"balance": 0, "deposited": 0, "blocked": False})
    return u


def next_order_id():
    """Botning o'z buyurtma ID si: 1, 2, 3, ..."""
    s = db_data["settings"]
    n = max([s.get("last_order_id", 0)] + [o.get("id", 0) or 0 for o in db_data["orders"]]) + 1
    s["last_order_id"] = n
    return n


def dedup_submit_link(link):
    """Agar bu havola avvalroq biror buyurtmada ishlatilgan bo'lsa, provayderga
    yuboriladigan nusxasiga takrorlanmas random raqam qo'shadi (1-100), foydalanuvchiga
    ko'rsatilmaydi va hech qachon bir xil raqam qayta ishlatilmaydi."""
    used_before = any(o.get("link") == link for o in db_data["orders"])
    if not used_before:
        return link
    store = db_data["settings"].setdefault("link_dedup", {})
    taken = set(store.setdefault(link, []))
    choices = [n for n in range(1, 101) if n not in taken]
    if not choices:
        store[link] = []
        choices = list(range(1, 101))
    n = random.choice(choices)
    store[link].append(n)
    if "?" in link:
        head, tail = link.split("?", 1)
        return f"{head}?{n}{tail}"
    return f"{link}?{n}"


def bold_sans(text):
    """Lotin harflarni qalin 𝗦𝗮𝗻𝘀 shriftiga o'giradi (Telegram'da chiroyli ko'rinadi)."""
    out = []
    for ch in str(text):
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - 97))
        else:
            out.append(ch)
    return "".join(out)


def order_accepted_text(order_id, name, qty, price, balance):
    b = bold_sans
    return (
        f"✅ {b('Qabul qilindi.')}\n"
        f"🆔 {b('Buyurtma idisi:')} {order_id}\n"
        f"📦 {b('Xizmat:')} {name}\n"
        f"🔢 {b('Miqdor:')} {qty} ta\n"
        f"💰 {b('Narx:')} {fmt_money(price)} so'm\n"
        f"👛 {b('Qolgan balans:')} {fmt_money(balance)} so'm\n"
        f"📯 {b('Buyurtma holati:')} ⏳ Bajarilmoqda\n\n"
        + "❗" + b("Buyurtma to'liq bajarilganda sizga xabar beriladi!")
    )


def shown_id(o):
    return o.get("id") or o.get("provider_order_id")


def is_blocked(uid):
    if uid == OWNER_ID:
        return False
    return bool(db_data["users"].get(str(uid), {}).get("blocked"))


def fmt_money(v):
    return f"{v:,.0f}"


def price_line(rate_uzs):
    return f"{fmt_money(rate_uzs)} so'm ({round(rate_uzs, 2)} UZS)"


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def grid(buttons, per_row=2):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def rename_network(old, new):
    db_data["services"] = {(new if k == old else k): v for k, v in db_data["services"].items()}
    for k in [k for k in list(db_data["items"].keys()) if k.startswith(f"{old}|")]:
        db_data["items"][f"{new}|{k[len(old) + 1:]}"] = db_data["items"].pop(k)
    ns = db_data["ui"]["net_styles"]
    if old in ns:
        ns[new] = ns.pop(old)
    cs = db_data["ui"]["cat_styles"]
    for k in [k for k in list(cs.keys()) if k.startswith(f"{old}|")]:
        cs[f"{new}|{k[len(old) + 1:]}"] = cs.pop(k)
    ni = db_data["ui"].setdefault("net_icons", {})
    if old in ni:
        ni[new] = ni.pop(old)
    nl = db_data["ui"].setdefault("net_labels", {})
    if old in nl:
        nl[new] = nl.pop(old)
    ci_map = db_data["ui"].setdefault("cat_icons", {})
    for k in [k for k in list(ci_map.keys()) if k.startswith(f"{old}|")]:
        ci_map[f"{new}|{k[len(old) + 1:]}"] = ci_map.pop(k)
    cl_map = db_data["ui"].setdefault("cat_labels", {})
    for k in [k for k in list(cl_map.keys()) if k.startswith(f"{old}|")]:
        cl_map[f"{new}|{k[len(old) + 1:]}"] = cl_map.pop(k)


def rename_category(net, ci, new):
    cats = db_data["services"][net]
    old = cats[ci]
    cats[ci] = new
    old_k, new_k = item_key(net, old), item_key(net, new)
    if old_k in db_data["items"]:
        db_data["items"][new_k] = db_data["items"].pop(old_k)
    cs = db_data["ui"]["cat_styles"]
    if old_k in cs:
        cs[new_k] = cs.pop(old_k)
    ci_map = db_data["ui"].setdefault("cat_icons", {})
    if old_k in ci_map:
        ci_map[new_k] = ci_map.pop(old_k)
    cl_map = db_data["ui"].setdefault("cat_labels", {})
    if old_k in cl_map:
        cl_map[new_k] = cl_map.pop(old_k)


def back_btn(cb):
    return [ibtn("back", cb)]


async def safe_edit(message, text, reply_markup=None, parse_mode=None, **kw):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode, **kw)
    except Exception:
        pass


# ---------------------------------------------------------------- API CALLS
async def api_post(url, payload):
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=payload) as resp:
            if resp.status != 200:
                return False, f"HTTP Error: {resp.status}"
            try:
                return True, await resp.json(content_type=None)
            except Exception:
                return False, "Provayder noto'g'ri javob qaytardi"


async def fetch_api_balance(url: str, key: str):
    try:
        ok, data = await api_post(url, {"key": key, "action": "balance"})
        if not ok:
            return False, data, None
        if isinstance(data, dict):
            if "balance" in data:
                return True, data.get("balance"), data.get("currency", "USD")
            if "error" in data:
                return False, data.get("error"), None
        return False, "Noma'lum javob", None
    except Exception as e:
        return False, str(e), None


async def fetch_api_services(url: str, key: str):
    try:
        ok, data = await api_post(url, {"key": key, "action": "services"})
        if not ok:
            return False, data
        if isinstance(data, list):
            return True, data
        if isinstance(data, dict) and "error" in data:
            return False, data["error"]
        return False, "Noma'lum javob"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- XIZMAT TAVSIFI (provayder saytidan)
_svc_cache = {}  # api_url -> (vaqt, {sid: xizmat})
SVC_CACHE_TTL = 600  # sekund: provayder xizmatlar ro'yxati 10 daqiqa eslab qolinadi
DESC_KEYS = ("description", "desc", "Description", "Desc", "info", "details", "note", "notes")


def clean_desc(text):
    text = re.sub(r"(?i)<\s*br\s*/?\s*>|</\s*(p|div|li)\s*>", "\n", str(text))
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def provider_service_info(it):
    api = find_api_by_url(it.get("api_url"))
    if not api:
        return None
    now = time.time()
    c = _svc_cache.get(api["url"])
    if not c or now - c[0] > SVC_CACHE_TTL:
        ok, services = await fetch_api_services(api["url"], api["key"])
        if ok:
            c = (now, {str(x.get("service")): x for x in services if isinstance(x, dict)})
            _svc_cache[api["url"]] = c
        elif not c:
            return None
    return c[1].get(str(it.get("sid")))


async def service_description(it):
    """Avval admin qo'lda yozgan tavsif, bo'lmasa provayder saytidagi tavsif. Yo'q bo'lsa bo'sh qaytadi."""
    manual = str(it.get("desc") or "").strip()
    if manual:
        return manual[:1500]
    try:
        info = await asyncio.wait_for(provider_service_info(it), timeout=8)
    except Exception:
        return ""
    if not info:
        return ""
    for k in DESC_KEYS:
        v = info.get(k)
        if isinstance(v, str) and clean_desc(v):
            return clean_desc(v)[:1500]
    return ""


def to_uzs(rate, currency):
    rate = float(rate)
    if str(currency).upper() != "UZS":
        rate *= USD_TO_UZS
    return round(rate * PRICE_MARKUP, 2)


# ---------------------------------------------------------------- KEYBOARDS
MAIN_KEYS = ["services", "orders", "account", "deposit", "earn", "hamkorlik", "guide", "support"]
INNER_KEYS = ["buy", "confirm", "cancel", "back"]
EXTRA_KEYS = ["search"]
BTN_KEYS = MAIN_KEYS + INNER_KEYS + EXTRA_KEYS
INFO_KEYS = ["guide", "support", "hamkorlik"]
BTN_DEFAULTS = {
    "services": "🌐  Xizmatlar  🌐",
    "orders": "📦 Buyurtmalarim",
    "account": "💳 Hisobim",
    "deposit": "💰 Pul kiritish",
    "earn": "🔗 Pul yeg'ish",
    "hamkorlik": "🤝 Hamkorlik dasturi",
    "guide": "📖 Qo'llanma",
    "support": "🆘 Qo'llab-quvvatlash",
    "buy": "🛒 Buyurtma berish",
    "confirm": "✅ Tasdiqlash",
    "cancel": "❌ Bekor qilish",
    "back": "⏪ Orqaga",
    "search": "🔍 Qidiruv",
}
BTN_DEFAULT_STYLE = {"buy": "success", "confirm": "success", "cancel": "danger", "search": "primary"}
INFO_DEFAULTS = {
    "guide": (
        "📖 Qo'llanma\n\n"
        "1️⃣ 🌐 Xizmatlar bo'limidan kerakli xizmatni tanlang.\n"
        "2️⃣ Havola va miqdorni yuboring.\n"
        "3️⃣ Buyurtmani tasdiqlang.\n\n"
        "Buyurtma bajarilgach sizga xabar keladi."
    ),
    "support": (
        "🆘 Qo'llab-quvvatlash\n\n"
        "Savol va muammolar bo'yicha admin bilan bog'laning."
    ),
    "hamkorlik": (
        "🤝 Hamkorlik dasturi\n\n"
        "Bu bo'lim tez orada ishga tushadi. Yangiliklardan xabardor bo'lish uchun "
        "kuzatib boring!"
    ),
}
COLOR_LABELS = {None: "⚪ Oddiy", "primary": "🔵 Ko'k", "success": "🟢 Yashil", "danger": "🔴 Qizil"}
COLOR_CB = {"none": None, "primary": "primary", "success": "success", "danger": "danger"}
RESERVED_TEXTS = {
    "Boshqaruv 🗄️", "🛍 Xizmatlar", "🔑 API boshqaruvi",
    "👤 Foydalanuvchilar", "🔘 Bot tugmalari", "💸 Referal/Bonus", "💳 To'lov tizimlar", "🛒 Buyurtmalar", "📨 Xabar yuborish", "📊 Statistika", "📢 Majburiy a'zolik", "📡 Buyurtma kanali", "➕ Tekin Xizmatlar Qo'shish", "Xizmat O'chirish 🗑", "🔙 Orqaga",
}


def sk(style):
    return {"style": style} if style else {}


def btn_cfg(key):
    return db_data["ui"]["buttons"].get(key, {})


def btn_text(key):
    return btn_cfg(key).get("text") or BTN_DEFAULTS[key]


def btn_style(key):
    cfg = btn_cfg(key)
    if "style" in cfg:
        return cfg["style"]
    return BTN_DEFAULT_STYLE.get(key)


def is_btn(text, key):
    if not text:
        return False
    cfg = btn_cfg(key)
    candidates = {
        btn_text(key),
        BTN_DEFAULTS.get(key, ""),
        cfg.get("text", ""),
        cfg.get("label", ""),
    } | set(cfg.get("aliases", []))
    if key == "deposit":
        candidates.update({"⭐ Telegram Stars", "Telegram Stars", "Stars", "💰 Pul kiritish", "Pul kiritish", "hisob to'ldirish"})
    candidates.discard("")
    if text in candidates:
        return True
    clean = lambda s: "".join(c for c in s if c.isalnum()).lower()
    t_clean = clean(text)
    if t_clean:
        for c in candidates:
            if clean(c) == t_clean:
                return True
    return False


def main_button(key):
    cfg = btn_cfg(key)
    kw = sk(btn_style(key))
    eid = cfg.get("emoji_id")
    label = cfg.get("label") or btn_text(key)
    if eid:
        kw["icon_custom_emoji_id"] = str(eid)
        return KeyboardButton(text=label, **kw)
    return KeyboardButton(text=btn_text(key), **kw)


def ibtn(key, cb):
    # ichki (inline) tugma: nom, rang va (bo'lsa) premium emoji ikonkasi
    cfg = btn_cfg(key)
    kw = sk(btn_style(key))
    label = cfg.get("label") or btn_text(key)
    if cfg.get("label") and cfg.get("emoji_id"):
        kw["icon_custom_emoji_id"] = cfg["emoji_id"]
    return InlineKeyboardButton(text=label, callback_data=cb, **kw)


def resolve_style(ck, default):
    if ck is None:
        return default
    return COLOR_CB.get(ck, default)


def net_style(net):
    return resolve_style(db_data["ui"]["net_styles"].get(net), "success")


def cat_style(net, cat):
    return resolve_style(db_data["ui"]["cat_styles"].get(f"{net}|{cat}"), "primary")


def srv_style(it):
    return resolve_style(it.get("style"), None)


def extract_emoji_and_label(message: types.Message):
    orig = (message.text or "").strip()
    emoji_id = None
    label = orig
    custom = [e for e in (message.entities or []) if e.type == "custom_emoji"]
    if custom:
        emoji_id = str(custom[0].custom_emoji_id)
        encoded = orig.encode("utf-16-le")
        ranges = sorted([(e.offset * 2, (e.offset + e.length) * 2) for e in custom], reverse=True)
        for s, en in ranges:
            encoded = encoded[:s] + encoded[en:]
        cleaned = encoded.decode("utf-16-le", errors="ignore").strip()
        label = cleaned if cleaned else orig
    return orig, emoji_id, label


def net_icon_kw(net):
    eid = db_data.get("ui", {}).get("net_icons", {}).get(net)
    return {"icon_custom_emoji_id": str(eid)} if eid else {}


def net_label(net):
    nl = db_data.get("ui", {}).get("net_labels", {})
    return nl.get(net) or net


def cat_icon_kw(net, cat):
    eid = db_data.get("ui", {}).get("cat_icons", {}).get(f"{net}|{cat}")
    return {"icon_custom_emoji_id": str(eid)} if eid else {}


def cat_label(net, cat):
    cl = db_data.get("ui", {}).get("cat_labels", {})
    return cl.get(f"{net}|{cat}") or cat


def srv_icon_kw(it):
    eid = it.get("emoji_id")
    return {"icon_custom_emoji_id": str(eid)} if eid else {}


def srv_label(it):
    return it.get("label") or it.get("name", "")


def get_webapp_url(uid: int) -> str:
    try:
        u = get_user(uid)
        bal = int(u.get("balance", 0))
        orders_cnt = sum(1 for o in db_data.get("orders", []) if str(o.get("user_id")) == str(uid))
        ref_cnt = int(u.get("referrals", 0))
    except Exception:
        bal = 0
        orders_cnt = 0
        ref_cnt = 0
    base = WEBAPP_URL.rstrip("/")
    return f"{base}/?uid={uid}&bal={bal}&orders={orders_cnt}&ref={ref_cnt}"


def build_kb(uid):
    rest_keys = [k for k in MAIN_KEYS if k != "services"]
    rest_btns = [main_button(k) for k in rest_keys]
    rows = [
        [main_button("services")]
    ]
    rows += [rest_btns[i:i + 2] for i in range(0, len(rest_btns), 2)]
    if uid == OWNER_ID:
        rows.append([KeyboardButton(text="Boshqaruv 🗄️", **sk("primary"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


admin_panel_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 Xizmatlar"), KeyboardButton(text="🔑 API boshqaruvi")],
        [KeyboardButton(text="👤 Foydalanuvchilar"), KeyboardButton(text="🔘 Bot tugmalari")],
        [KeyboardButton(text="💸 Referal/Bonus"), KeyboardButton(text="💳 To'lov tizimlar")],
        [KeyboardButton(text="🛒 Buyurtmalar"), KeyboardButton(text="📨 Xabar yuborish")],
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Majburiy a'zolik")],
        [KeyboardButton(text="📡 Buyurtma kanali")],
        [KeyboardButton(text="➕ Tekin Xizmatlar Qo'shish"), KeyboardButton(text="Xizmat O'chirish 🗑")],
        [KeyboardButton(text="🔙 Orqaga", **sk("danger"))],
    ],
    resize_keyboard=True,
)


# ---------------------------------------------------------------- ADMIN PANEL TUGMALARI (sozlanadigan)
ADMIN_KEYS = [
    "adm_services", "adm_api", "adm_users", "adm_bot_btns", "adm_admin_btns",
    "adm_referral", "adm_payment", "adm_orders", "adm_broadcast", "adm_stats",
    "adm_forced", "adm_channel", "adm_free_add", "adm_free_del", "adm_back",
]
ADMIN_BTN_DEFAULTS = {
    "adm_services": "🛍 Xizmatlar",
    "adm_api": "🔑 API boshqaruvi",
    "adm_users": "👤 Foydalanuvchilar",
    "adm_bot_btns": "🔘 Bot tugmalari",
    "adm_admin_btns": "🔘 Admin tugmalari",
    "adm_referral": "💸 Referal/Bonus",
    "adm_payment": "💳 To'lov tizimlar",
    "adm_orders": "🛒 Buyurtmalar",
    "adm_broadcast": "📨 Xabar yuborish",
    "adm_stats": "📊 Statistika",
    "adm_forced": "📢 Majburiy a'zolik",
    "adm_channel": "📡 Buyurtma kanali",
    "adm_free_add": "➕ Tekin Xizmatlar Qo'shish",
    "adm_free_del": "Xizmat O'chirish 🗑",
    "adm_back": "🔙 Orqaga",
}
ADMIN_BTN_DEFAULT_STYLE = {k: "primary" for k in ADMIN_KEYS if k != "adm_back"}
ADMIN_BTN_DEFAULT_STYLE["adm_back"] = "danger"
ADMIN_RESERVED_TEXTS = {"Boshqaruv 🗄️"}


def admin_btn_cfg(key):
    return db_data["ui"]["admin_buttons"].get(key, {})


def admin_btn_text(key):
    return admin_btn_cfg(key).get("text") or ADMIN_BTN_DEFAULTS[key]


def admin_btn_style(key):
    cfg = admin_btn_cfg(key)
    if "style" in cfg:
        return cfg["style"]
    return ADMIN_BTN_DEFAULT_STYLE.get(key)


def is_admin_btn(text, key):
    if not text:
        return False
    cfg = admin_btn_cfg(key)
    candidates = {
        admin_btn_text(key),
        ADMIN_BTN_DEFAULTS.get(key, ""),
        cfg.get("text", ""),
        cfg.get("label", ""),
    } | set(cfg.get("aliases", []))
    candidates.discard("")
    if text in candidates:
        return True
    clean = lambda s: "".join(c for c in s if c.isalnum()).lower()
    t_clean = clean(text)
    if t_clean:
        for c in candidates:
            if clean(c) == t_clean:
                return True
    return False


def is_nav_command_or_button(text):
    if not text:
        return False
    t = text.strip()
    if t.startswith("/"):
        return True

    main_keys = [
        "services", "orders", "account", "deposit", "earn",
        "hamkorlik", "guide", "support", "buy", "confirm", "cancel", "back", "search"
    ]
    for k in main_keys:
        if is_btn(t, k):
            return True

    nav_texts = {
        "🌐  Xizmatlar  🌐", "🌐 Xizmatlar", "Xizmatlar",
        "📦 Buyurtmalarim", "Buyurtmalarim", "Buyurtmalar",
        "💳 Hisobim", "Hisobim", "💰 Balans", "Balans",
        "💰 Pul kiritish", "Pul kiritish", "⭐ Telegram Stars", "Telegram Stars", "Stars",
        "🔗 Pul yeg'ish", "Pul yeg'ish", "Pul yig'ish", "🔗 Pul yig'ish",
        "🤝 Hamkorlik dasturi", "Hamkorlik", "Hamkorlik dasturi",
        "📖 Qo'llanma", "Qo'llanma",
        "🆘 Qo'llab-quvvatlash", "Qo'llab-quvvatlash", "Yordam",
        "🔍 Qidiruv", "Qidiruv",
        "📱 SMM Web App 🚀", "SMM Web App", "webapp", "Mini App",
        "⏪ Orqaga", "🔙 Orqaga", "Orqaga", "Bekor qilish", "❌ Bekor qilish",
        "Bosh menyu", "🏠 Bosh menyu", "Asosiy menyu", "🖥️ Asosiy menyu",
    }
    if t in nav_texts:
        return True

    admin_keys = [
        "adm_users", "adm_bot_btns", "adm_referral", "adm_payment",
        "adm_orders", "adm_broadcast", "adm_stats", "adm_channels",
        "adm_order_channel", "adm_free_add", "adm_free_del", "adm_back", "adm_admin_btns"
    ]
    for ak in admin_keys:
        if is_admin_btn(t, ak):
            return True

    admin_texts = {
        "👤 Foydalanuvchilar", "🔘 Bot tugmalari", "💸 Referal/Bonus",
        "💳 To'lov tizimlar", "🛒 Buyurtmalar", "📨 Xabar yuborish",
        "📊 Statistika", "📢 Majburiy a'zolik", "📡 Buyurtma kanali",
        "➕ Tekin Xizmatlar Qo'shish", "Xizmat O'chirish 🗑", "🔙 Orqaga",
        "Boshqaruv 🗄️"
    }
    if t in admin_texts:
        return True

    return False


def admin_main_button(key):
    cfg = admin_btn_cfg(key)
    kw = sk(admin_btn_style(key))
    eid = cfg.get("emoji_id")
    label = cfg.get("label") or admin_btn_text(key)
    if eid:
        kw["icon_custom_emoji_id"] = str(eid)
        return KeyboardButton(text=label, **kw)
    return KeyboardButton(text=admin_btn_text(key), **kw)


def build_admin_kb():
    rows = [
        [admin_main_button("adm_services"), admin_main_button("adm_api")],
        [admin_main_button("adm_users"), admin_main_button("adm_bot_btns")],
        [admin_main_button("adm_admin_btns"), admin_main_button("adm_referral")],
        [admin_main_button("adm_payment"), admin_main_button("adm_orders")],
        [admin_main_button("adm_broadcast"), admin_main_button("adm_stats")],
        [admin_main_button("adm_forced"), admin_main_button("adm_channel")],
        [admin_main_button("adm_free_add"), admin_main_button("adm_free_del")],
        [admin_main_button("adm_back")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _admin_label(key):
    return " ".join(admin_btn_text(key).split())


def admin_buttons_home_text():
    return (
        "🔘 <b>Admin tugmalari</b> — boshqaruv panelingizning yuzi shu yerda yasaladi!\n\n"
        "<blockquote>✨ Har bir admin panel tugmasi nomini o'zgartiring, rangini tanlang.\n\n"
        "💎 Premium emoji yuborsangiz — tugmada aynan o'sha chiqadi.</blockquote>"
    )


def admin_buttons_home_markup():
    rows = grid([
        InlineKeyboardButton(text=f"✏️ {_admin_label(k)}", callback_data=f"abt_{k}", **sk(admin_btn_style(k)))
        for k in ADMIN_KEYS
    ])
    rows.append([InlineKeyboardButton(text="•---•---•---•---•---•---•---•---•---•---•", callback_data="noop", **sk("primary"))])
    rows.append([InlineKeyboardButton(text="🔸 BEKOR QILISH 🔸", callback_data="abt_delete_msg", style="danger")])
    return kb(rows)


def admin_button_edit_view(key):
    cur = admin_btn_style(key)
    text = (
        "🔘 <b>Tugma</b> ✏️\n\n"
        f"<b>Nomi:</b> {escape(admin_btn_text(key))}\n"
        f"<b>Rangi:</b> {COLOR_LABELS[cur]}\n\n"
        "🎨 Rangni quyidan tanlang:"
    )

    def color_btn(ck):
        st = COLOR_CB[ck]
        label = COLOR_LABELS[st]
        if st == cur:
            label = "✅ " + label
        return InlineKeyboardButton(text=label, callback_data=f"abt_col_{key}_{ck}", **sk(st))

    rows = [
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data=f"abt_ren_{key}")],
        [color_btn("none"), color_btn("primary")],
        [color_btn("success"), color_btn("danger")],
        [InlineKeyboardButton(text="↩️ Asliga qaytarish", callback_data=f"abt_rst_{key}")],
        back_btn("abt_home"),
    ]
    return text, kb(rows)


def set_admin_button_name(key, text, emoji_id, label):
    cfg = db_data["ui"]["admin_buttons"].setdefault(key, {})
    aliases = set(cfg.get("aliases", []))
    old = admin_btn_text(key)
    if old != text:
        aliases.add(old)
    if cfg.get("label"):
        aliases.add(cfg["label"])
    if label:
        aliases.add(label)
    aliases.discard(text)
    cfg["aliases"] = sorted(aliases)
    cfg["text"] = text
    cfg["emoji_id"] = emoji_id
    cfg["label"] = label


def reset_admin_button(key):
    cfg = admin_btn_cfg(key)
    aliases = set(cfg.get("aliases", []))
    if cfg.get("text"):
        aliases.add(cfg["text"])
    aliases.discard(ADMIN_BTN_DEFAULTS[key])
    db_data["ui"]["admin_buttons"][key] = {"aliases": sorted(aliases)}


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_admin_btns"))
async def admin_buttons_menu_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(admin_buttons_home_text(), reply_markup=admin_buttons_home_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "abt_home")
async def admin_buttons_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await safe_edit(call.message, admin_buttons_home_text(), admin_buttons_home_markup(), parse_mode="HTML")
        await call.answer()


@dp.callback_query(F.data.in_({f"abt_{k}" for k in ADMIN_KEYS}))
async def admin_button_view_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        key = call.data[len("abt_"):]
        text, markup = admin_button_edit_view(key)
        await safe_edit(call.message, text, markup, parse_mode="HTML")
        await call.answer()


@dp.callback_query(F.data.startswith("abt_col_"))
async def admin_button_color_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    rest = call.data[len("abt_col_"):]
    key, ck = rest.rsplit("_", 1)
    if key not in ADMIN_KEYS or ck not in COLOR_CB:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    db_data["ui"]["admin_buttons"].setdefault(key, {})["style"] = COLOR_CB[ck]
    save_data(db_data)
    text, markup = admin_button_edit_view(key)
    await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer("🎨 Rang o'zgartirildi")


@dp.callback_query(F.data.startswith("abt_ren_"))
async def admin_button_rename_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    key = call.data[len("abt_ren_"):]
    if key not in ADMIN_KEYS:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_admin_btn_name)
    await state.update_data(key=key)
    await safe_edit(
        call.message,
        f"✏️ [{admin_btn_text(key)}] tugmasi uchun yangi nom yuboring:\n\nPremium emoji bilan yuborsangiz, uning ID si saqlanadi.",
        kb([back_btn(f"abt_{key}")]),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("abt_rst_"))
async def admin_button_reset_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    key = call.data[len("abt_rst_"):]
    if key not in ADMIN_KEYS:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    reset_admin_button(key)
    save_data(db_data)
    text, markup = admin_button_edit_view(key)
    await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer("↩️ Asliga qaytarildi")


@dp.message(StateFilter(AdminState.waiting_for_admin_btn_name))
async def process_admin_button_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    key = d.get("key")
    if key not in ADMIN_KEYS:
        await state.clear()
        return
    back = kb([back_btn(f"abt_{key}")])
    orig, emoji_id, label = extract_emoji_and_label(message)
    new = orig.strip()
    if not new:
        await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=back)
        return
    if len(new) > 60:
        await message.answer("❌ Nom juda uzun (60 belgigacha):", reply_markup=back)
        return

    set_admin_button_name(key, new, emoji_id, label)
    save_data(db_data)
    await state.clear()
    text, markup = admin_button_edit_view(key)
    await message.answer("✅ Tugma nomi yangilandi!\n\n" + text, reply_markup=markup, parse_mode="HTML")
    await message.answer("⌨️ Yangilangan menyu:", reply_markup=build_admin_kb())


def main_kb(uid):
    return build_kb(uid)


def get_user_services_inline():
    n = nets()
    if not n:
        return None
    rows = grid([
        InlineKeyboardButton(
            text=net_label(name),
            callback_data=f"u_net_{i}",
            **net_icon_kw(name),
            **sk(net_style(name))
        ) for i, name in enumerate(n)
    ])
    rows.append([ibtn("search", "u_search_start")])
    return kb(rows)


def get_admin_services_inline():
    rows = grid([
        InlineKeyboardButton(
            text=net_label(name),
            callback_data=f"a_net_{i}",
            **net_icon_kw(name),
            **sk(net_style(name))
        ) for i, name in enumerate(nets())
    ])
    rows.append([
        InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_social_network"),
        InlineKeyboardButton(text="➖ Ayirish", callback_data="delete_social_network"),
    ])
    return kb(rows)


async def _notify_admin_new_user(tg_user, now):
    bio = None
    try:
        chat = await bot.get_chat(tg_user.id)
        bio = getattr(chat, "bio", None)
    except Exception:
        pass

    text = (
        "🆕 Yangi foydalanuvchi qo'shildi!\n\n"
        f"👤 Ism: {tg_user.first_name or '—'}\n"
        f"👥 Familiya: {tg_user.last_name or '—'}\n"
        f"🔗 Username: {('@' + tg_user.username) if tg_user.username else '—'}\n"
        f"🆔 ID: {tg_user.id}\n"
        f"🌐 Til: {tg_user.language_code or '—'}\n"
        f"💎 Premium: {'Ha' if getattr(tg_user, 'is_premium', False) else 'Yoq'}\n"
        f"📝 Bio: {bio or '—'}\n"
        f"📅 Sana: {now}\n\n"
        f"👥 Jami foydalanuvchilar: {len(db_data['users'])} ta"
    )
    markup = kb([[InlineKeyboardButton(text="👤 Profilga o'tish", url=f"tg://user?id={tg_user.id}")]])

    sent = False
    try:
        photos = await bot.get_user_profile_photos(tg_user.id, limit=1)
        if photos.total_count > 0:
            await bot.send_photo(OWNER_ID, photos.photos[0][-1].file_id, caption=text, reply_markup=markup)
            sent = True
    except Exception:
        pass
    if not sent:
        try:
            await bot.send_message(OWNER_ID, text, reply_markup=markup)
        except Exception:
            try:
                await bot.send_message(OWNER_ID, text)
            except Exception as e:
                print("Adminga xabar yuborilmadi:", e)


# ---------------------------------------------------------------- START / MENU
async def register_user(tg_user):
    uid = str(tg_user.id)
    if uid in db_data["users"]:
        return
    _newly_registered.add(tg_user.id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_data["users"][uid] = {
        "balance": 0,
        "deposited": 0,
        "blocked": False,
        "referrals": 0,
        "last_bonus_date": None,
        "info": {
            "first_name": tg_user.first_name,
            "last_name": tg_user.last_name,
            "username": tg_user.username,
            "language_code": tg_user.language_code,
            "is_premium": bool(getattr(tg_user, "is_premium", False)),
            "joined": now,
        },
    }
    save_data(db_data)
    if tg_user.id != OWNER_ID:
        asyncio.create_task(_notify_admin_new_user(tg_user, now))


class RegisterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and is_blocked(user.id):
            # bloklangan foydalanuvchiga hech qanday javob berilmaydi
            if isinstance(event, types.CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return None
        if user and not user.is_bot:
            try:
                await register_user(user)
            except Exception as e:
                print("Ro'yxatga olishda xato:", e)
            if await force_sub_gate(event, user):
                return None  # majburiy kanallarga a'zo bo'lmagan — botdan foydalana olmaydi

        # Har qanday menyu tugmasi yoki buyruq yuborilganda, mavjud state avtomatik tozalansin
        if isinstance(event, types.Message) and event.text:
            if is_nav_command_or_button(event.text):
                state: FSMContext = data.get("state")
                if state:
                    curr = await state.get_state()
                    if curr and curr not in RENAME_INPUT_STATES:
                        await state.clear()

        return await handler(event, data)


dp.message.outer_middleware(RegisterMiddleware())
dp.callback_query.outer_middleware(RegisterMiddleware())


async def process_referral(message: types.Message):
    uid = message.from_user.id
    if uid not in _newly_registered:
        return
    _newly_registered.discard(uid)
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if not payload.isdigit():
        return
    ref_id = int(payload)
    ref_uid = str(ref_id)
    if ref_id == uid or ref_uid not in db_data["users"]:
        return
    ref_u = db_data["users"][ref_uid]
    bonus = db_data["settings"].get("referral_bonus", 0)
    ref_u["referrals"] = ref_u.get("referrals", 0) + 1
    if bonus > 0:
        ref_u["balance"] = ref_u.get("balance", 0) + bonus
    save_data(db_data)
    note = "🎉 Sizning referal havolangiz orqali yangi foydalanuvchi qo'shildi!"
    if bonus > 0:
        note += f"\n💰 Balansingizga {fmt_money(bonus)} so'm qo'shildi.\n👛 Jami: {fmt_money(ref_u['balance'])} so'm"
    await notify_user(ref_id, note)


@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    get_user(uid)
    await process_referral(message)

    # Foydalanuvchiga mos shaxsiy menyu tugmasini (balansi bilan) o'rnatish
    async def _setup_user_menu():
        try:
            url = get_webapp_url(uid)
            await bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=types.MenuButtonWebApp(
                    text="Mini App 📱",
                    web_app=types.WebAppInfo(url=url),
                )
            )
        except Exception:
            pass
    asyncio.create_task(_setup_user_menu())

    # Agar chuqur havola orqali buyurtma yuborilgan bo'lsa (/start ord_...)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and (parts[1].startswith("ord_") or parts[1].startswith("order_")):
        try:
            import base64
            enc = parts[1].split("_", 1)[1]
            raw_json = base64.urlsafe_b64decode(enc.encode()).decode()
            order_data = json.loads(raw_json)
            # handle_webapp_data mantig'i orqali qabul qilish
            await execute_direct_order(message, order_data)
            return
        except Exception:
            pass

    await message.reply(
        "👋 <b>Assalomu alaykum va xush kelibsiz!</b>\n\n"
        "🚀 <b>Eng sifatli va arzon SMM xizmatlari</b> botimizga xush kelibsiz!\n"
        "Telegram, Instagram, TikTok, YouTube va boshqa ijtimoiy tarmoqlar uchun xizmatlar mavjud.",
        parse_mode="HTML"
    )
    await message.answer("🖥️ Asosiy menyu tugmalari faollashtirildi:", reply_markup=main_kb(uid))


@dp.message(not_renaming, lambda m: bool(m.text) and (is_btn(m.text, "back") or m.text in {"Orqaga", "🔙 Orqaga", "⏪ Orqaga", "Bekor qilish", "❌ Bekor qilish", "Bosh menyu", "🏠 Bosh menyu"}))
async def universal_back_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🖥️ Asosiy menyudasiz", reply_markup=main_kb(message.from_user.id))


async def dispatch_nav_command_or_button(message: types.Message, state: FSMContext):
    await state.clear()
    t = (message.text or "").strip()
    if is_btn(t, "earn"):
        await earn_handler(message, state)
    elif is_btn(t, "deposit"):
        await deposit_handler(message, state)
    elif is_btn(t, "account") or t in {"💰 Balans", "Balans", "Hisobim", "💳 Hisobim"}:
        await account_handler(message, state)
    elif is_btn(t, "orders") or t in {"📦 Buyurtmalarim", "Buyurtmalarim", "Buyurtmalar"}:
        await my_orders_handler(message, state)
    elif is_btn(t, "services") or t in {"🌐  Xizmatlar  🌐", "🌐 Xizmatlar", "Xizmatlar"}:
        await services_handler(message, state)
    elif is_btn(t, "hamkorlik"):
        await hamkorlik_handler(message, state)
    elif is_btn(t, "guide"):
        await guide_handler(message, state)
    elif is_btn(t, "support"):
        await support_handler(message, state)
    elif is_btn(t, "search"):
        await state.set_state(ServiceSearchState.waiting_for_query)
        await message.answer("🔍 Qidirmoqchi bo'lgan xizmat nomini yozing:")
    elif is_admin_btn(t, "adm_payment") or t == "💳 To'lov tizimlar":
        await payment_types_handler(message, state)
    elif is_admin_btn(t, "adm_stats") or t in ("/stat", "/stats", "📊 Statistika", "Statistika"):
        await admin_stats_handler(message, state)
    elif is_admin_btn(t, "adm_users") or t == "👤 Foydalanuvchilar":
        await users_menu_handler(message, state)
    elif is_admin_btn(t, "adm_orders") or t == "🛒 Buyurtmalar":
        await admin_orders_entry(message, state)
    elif is_admin_btn(t, "adm_broadcast") or t == "📨 Xabar yuborish":
        await bc_entry(message, state)
    elif is_admin_btn(t, "adm_bot_btns") or t == "🔘 Bot tugmalari":
        await bot_buttons_menu_handler(message, state)
    elif is_admin_btn(t, "adm_referral") or t == "💸 Referal/Bonus":
        await referral_settings_handler(message, state)
    elif is_admin_btn(t, "adm_forced") or t == "📢 Majburiy a'zolik":
        await force_admin_handler(message, state)
    elif is_admin_btn(t, "adm_channel") or t == "📡 Buyurtma kanali":
        await order_channel_handler(message, state)
    elif is_btn(t, "back") or t in {"Orqaga", "🔙 Orqaga", "⏪ Orqaga", "Bekor qilish", "❌ Bekor qilish", "Bosh menyu", "🏠 Bosh menyu"}:
        await message.answer("🖥️ Asosiy menyudasiz", reply_markup=main_kb(message.from_user.id))
    elif t.startswith("/start"):
        await start_handler(message, state)
    elif t.startswith("/admin") and message.from_user.id == OWNER_ID:
        await message.answer("🔐 Admin panel:", reply_markup=build_admin_kb())
    else:
        await message.answer("🖥️ Asosiy menyudasiz", reply_markup=main_kb(message.from_user.id))


@dp.message(Command("webapp"))
@dp.message(not_renaming, F.text.in_({"📱 SMM Web App 🚀", "SMM Web App", "webapp", "Mini App"}))
async def webapp_launcher_handler(message: types.Message):
    url = get_webapp_url(message.from_user.id)
    webapp_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 SMM Mini App (Do'kon) 🚀", web_app=types.WebAppInfo(url=url))]
    ])
    await message.answer(
        "📱 <b>SMM Mini App (Do'kon)</b>\n\n"
        "Zamonaviy Apple iOS dizayndagi interaktiv Web App orqali tezkor va qulay buyurtma bering!\n\n"
        "✨ Jonli narx va chegirma hisoblagich\n"
        "✨ Barcha tarmoqlar (Telegram, Instagram, TikTok, YouTube)\n"
        "✨ Botdagi haqiqiy balans bilan 0-latency sinxronlangan\n\n"
        "👇 Mini App-ni ochish uchun quyidagi tugmani bosing:",
        reply_markup=webapp_kb,
        parse_mode="HTML"
    )


async def execute_direct_order(message: types.Message, data: dict):
    await process_webapp_order(message, data)


@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        raw_data = message.web_app_data.data
        data = json.loads(raw_data)
    except Exception as e:
        await message.answer(f"❌ Xatolik: Noto'g'ri Web App ma'lumoti ({e})")
        return
    await process_webapp_order(message, data)


async def process_webapp_order(message: types.Message, data: dict):

    action = data.get("action")
    if action == "new_order":
        uid = message.from_user.id
        u = get_user(uid)
        price = float(data.get("price", 0))
        qty = int(data.get("quantity", 0))
        link = str(data.get("link", "")).strip()
        s_name = data.get("service_name", "Noma'lum xizmat")
        s_id = data.get("service_id", 0)
        network = data.get("network", "SMM")

        if not link:
            await message.answer("❌ Xatolik: Havola kiritilmagan!")
            return
        if qty <= 0:
            await message.answer("❌ Xatolik: Miqdor noto'g'ri kiritilgan!")
            return

        if u["balance"] < price:
            await message.answer(
                f"❌ <b>Balansingiz yetarli emas!</b>\n\n"
                f"💰 Xizmat narxi: <b>{fmt_money(price)} so'm</b>\n"
                f"👛 Sizning balansingiz: <b>{fmt_money(u['balance'])} so'm</b>\n\n"
                f"💡 Hisobingizni to'ldirish uchun asosiy menyudagi <b>💳 Hisobim</b> yoki <b>💰 Pul kiritish</b> bo'limidan foydalaning.",
                parse_mode="HTML"
            )
            return

        u["balance"] -= price
        order_id = next_order_id()
        new_order = {
            "id": order_id,
            "user_id": uid,
            "provider_order_id": f"WA-{order_id}",
            "sid": s_id,
            "service": s_name,
            "network": network,
            "link": link,
            "quantity": qty,
            "price": price,
            "api_url": "WebApp",
            "status": "Pending",
            "final": False,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        db_data["orders"].append(new_order)
        save_data(db_data)

        # Notify owner if order is from another user
        if uid != OWNER_ID:
            try:
                await bot.send_message(
                    OWNER_ID,
                    f"🔔 <b>Yangi Web App buyurtmasi!</b>\n\n"
                    f"🆔 Buyurtma ID: #{order_id}\n"
                    f"👤 Foydalanuvchi: <a href='tg://user?id={uid}'>{message.from_user.full_name}</a> (<code>{uid}</code>)\n"
                    f"🌐 Tarmoq: {network}\n"
                    f"📦 Xizmat: {s_name}\n"
                    f"🔗 Havola: {link}\n"
                    f"🔢 Miqdor: {qty:,} ta\n"
                    f"💰 Narxi: {fmt_money(price)} so'm",
                    parse_mode="HTML"
                )
            except Exception as e:
                print("Admin notify error:", e)

        # Send order channel post if configured
        try:
            it_dummy = {"name": s_name, "sid": s_id}
            await send_order_channel_post(order_id, it_dummy, qty, price, message.from_user, link)
        except Exception as e:
            print("Channel post error:", e)

        await message.answer(
            f"✅ <b>Buyurtmangiz muvaffaqiyatli qabul qilindi!</b>\n\n"
            f"🆔 <b>Buyurtma ID:</b> #{order_id}\n"
            f"🌐 <b>Tarmoq:</b> {network}\n"
            f"📦 <b>Xizmat:</b> {s_name}\n"
            f"🔗 <b>Havola:</b> {link}\n"
            f"🔢 <b>Miqdor:</b> {qty:,} ta\n"
            f"💰 <b>Jami to'lov:</b> {fmt_money(price)} so'm\n"
            f"👛 <b>Qolgan balans:</b> {fmt_money(u['balance'])} so'm\n"
            f"⏳ <b>Holat:</b> Bajarilmoqda (Pending)\n\n"
            f"<i>Buyurtmangiz tez orada bajariladi. Holatini <b>📦 Buyurtmalarim</b> bo'limida kuzatishingiz mumkin!</i>",
            parse_mode="HTML"
        )


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_back"))
async def back_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("🖥️ Asosiy menyudasiz", reply_markup=main_kb(message.from_user.id))


# ---------------------------------------------------------------- ADMIN: NARXNI 0 QILISH (BEPUL QILISH)
def zr_items(target):
    """Tanlangan nishon bo'yicha xizmatlar ro'yxati.
    target: 'a' (hammasi) | 'n2' (tarmoq) | 'c2_1' (bo'lim) | 's2_1_3' (bitta xizmat)"""
    kind, nums = target[0], [int(x) for x in target[1:].split("_") if x != ""]
    if kind == "a":
        return [it for lst in db_data["items"].values() for it in lst]
    net = get_net(nums[0]) if nums else None
    if net is None:
        return []
    if kind == "n":
        return [it for k, lst in db_data["items"].items() if k.startswith(f"{net}|") for it in lst]
    cat = get_cat(net, nums[1]) if len(nums) > 1 else None
    if cat is None:
        return []
    items = get_items(net, cat)
    if kind == "c":
        return list(items)
    if kind == "s" and len(nums) > 2 and nums[2] < len(items):
        return [items[nums[2]]]
    return []


def zr_title(target):
    kind, nums = target[0], [int(x) for x in target[1:].split("_") if x != ""]
    if kind == "a":
        return "🌐 Barcha xizmatlar"
    net = get_net(nums[0]) or "?"
    if kind == "n":
        return f"📱 {net} (butun tarmoq)"
    cat = get_cat(net, nums[1]) or "?"
    if kind == "c":
        return f"📂 {net} › {cat} (butun bo'lim)"
    items = get_items(net, cat)
    name = items[nums[2]]["name"] if nums[2] < len(items) else "?"
    return f"📦 {name}"


def zr_home_view():
    ns = nets()
    if not ns:
        return "⚠️ Hozircha xizmatlar yo'q.", None
    rows = grid([InlineKeyboardButton(text=n, callback_data=f"zr_n_{i}", **sk(net_style(n))) for i, n in enumerate(ns)])
    rows.append([InlineKeyboardButton(text="🌐 Hammasi", callback_data="zr_t_a")])
    return "🆓 Qaysinisini 0 qilmoqchisiz?\n\nIjtimoiy tarmoqni tanlang:", kb(rows)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_free_add"))
async def zr_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != OWNER_ID:
        return
    text, markup = zr_home_view()
    await message.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "zr_home")
async def zr_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    text, markup = zr_home_view()
    await safe_edit(call.message, text, markup)
    await call.answer()


@dp.callback_query(F.data.regexp(r"^zr_n_\d+$"))
async def zr_net_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    ni = int(call.data.rsplit("_", 1)[1])
    net = get_net(ni)
    if net is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    cats = db_data["services"].get(net, [])
    rows = [[InlineKeyboardButton(text="📱 Butun tarmoq", callback_data=f"zr_t_n{ni}")]]
    rows += grid([InlineKeyboardButton(text=c, callback_data=f"zr_c_{ni}_{ci}", **sk(cat_style(net, c))) for ci, c in enumerate(cats)])
    rows.append(back_btn("zr_home"))
    await safe_edit(call.message, f"📱 {net}\n\nQaysinisini 0 qilmoqchisiz?", kb(rows))
    await call.answer()


@dp.callback_query(F.data.regexp(r"^zr_c_\d+_\d+$"))
async def zr_cat_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text="📂 Butun bo'lim", callback_data=f"zr_t_c{ni}_{ci}")]]
    for si, it in enumerate(get_items(net, cat)):
        rows.append([InlineKeyboardButton(
            text=f"{it['name']} — {fmt_money(it['rate'])} so'm", callback_data=f"zr_t_s{ni}_{ci}_{si}",
            **sk(srv_style(it)))])
    rows.append(back_btn(f"zr_n_{ni}"))
    await safe_edit(call.message, f"📂 {net} › {cat}\n\nQaysinisini 0 qilmoqchisiz?", kb(rows))
    await call.answer()


def zr_back_target(target):
    kind, rest = target[0], target[1:]
    if kind == "s":
        ni, ci, _ = rest.split("_")
        return f"zr_c_{ni}_{ci}"
    if kind == "c":
        return f"zr_n_{rest.split('_')[0]}"
    return "zr_home"


@dp.callback_query(F.data.regexp(r"^zr_t_[ancs]"))
async def zr_confirm_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    target = call.data[len("zr_t_"):]
    items = zr_items(target)
    if not items:
        await call.answer("⚠️ Xizmat topilmadi", show_alert=True)
        return
    nonzero = [it for it in items if it.get("rate", 0) > 0]
    backups = [it for it in items if it.get("rate_backup") or it.get("limit_backup")]
    text = (
        f"{zr_title(target)}\n\n"
        f"📦 Xizmatlar: {len(items)} ta\n"
        f"💰 Narxi 0 dan katta: {len(nonzero)} ta\n\n"
        "⚠️ Tanlanganlarning narxi 0 so'm (bepul) bo'ladi.\n"
        "📏 Minimal va maksimal miqdor ikkalasi ham provayder saytidagi minimal miqdorga tenglanadi "
        "(masalan saytda min 100 bo'lsa, botda min 100 va max 100).\n\nDavom etasizmi?"
    )
    rows = [[
        InlineKeyboardButton(text="✅ Ha, 0 qilinsin", callback_data=f"zr_go_{target}", **sk("success")),
        InlineKeyboardButton(text="❌ Bekor", callback_data="zr_home", **sk("danger")),
    ]]
    if backups:
        rows.append([InlineKeyboardButton(
            text=f"↩️ Avvalgi narxga qaytarish ({len(backups)} ta)", callback_data=f"zr_rs_{target}")])
    rows.append(back_btn(zr_back_target(target)))
    await safe_edit(call.message, text, kb(rows))
    await call.answer()


async def zr_provider_min(it):
    """Provayder saytidagi minimal buyurtma miqdori (aniqlanmasa None)."""
    try:
        info = await asyncio.wait_for(provider_service_info(it), timeout=30)
        v = int(float(info.get("min"))) if info else 0
    except Exception:
        return None
    return v if v >= 1 else None


@dp.callback_query(F.data.startswith("zr_go_"))
async def zr_go_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    target = call.data[len("zr_go_"):]
    items = zr_items(target)
    await call.answer()
    await safe_edit(call.message, "⏳ Provayder saytidan minimal miqdor olinmoqda...")
    for it in items:  # eng yangi ma'lumot olinishi uchun keshni tozalaymiz
        _svc_cache.pop(it.get("api_url"), None)
    price_changed = limited = unknown = 0
    for it in items:
        if it.get("rate", 0) > 0:
            it["rate_backup"] = it["rate"]  # keyin qaytarish mumkin bo'lishi uchun
            it["rate"] = 0
            price_changed += 1
        pmin = await zr_provider_min(it)
        if pmin is None:
            unknown += 1
            continue
        if "limit_backup" not in it:
            it["limit_backup"] = [it["min"], it["max"]]
        it["min"] = it["max"] = pmin
        limited += 1
    save_data(db_data)
    text = (
        f"✅ Tayyor!\n\n{zr_title(target)}\n"
        f"💸 Narxi 0 so'm qilindi: {price_changed} ta\n"
        f"📏 Min = Max (saytdagi minimal miqdor): {limited} ta"
    )
    if unknown:
        text += f"\n⚠️ Saytdan minimal miqdor topilmadi: {unknown} ta (ularning miqdori o'zgarmadi)"
    await safe_edit(call.message, text, kb([back_btn("zr_home")]))


@dp.callback_query(F.data.startswith("zr_rs_"))
async def zr_restore_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    target = call.data[len("zr_rs_"):]
    restored = 0
    for it in zr_items(target):
        did = False
        if it.get("rate_backup"):
            it["rate"] = it.pop("rate_backup")
            did = True
        if it.get("limit_backup"):
            it["min"], it["max"] = it.pop("limit_backup")
            did = True
        restored += did
    save_data(db_data)
    await safe_edit(
        call.message,
        f"↩️ Tayyor!\n\n{zr_title(target)}\n💰 {restored} ta xizmat avvalgi narx va miqdoriga qaytarildi.",
        kb([back_btn("zr_home")]),
    )
    await call.answer()


# ---------------------------------------------------------------- ADMIN: XIZMAT / BO'LIM / TARMOQNI O'CHIRISH
def dl_resolve(target):
    """Nishonni tekshiradi: (kind, net, cat, si) yoki None (eskirgan tugma bo'lsa)."""
    kind = target[0]
    nums = [int(x) for x in target[1:].split("_") if x != ""]
    net = get_net(nums[0]) if nums else None
    if net is None:
        return None
    if kind == "n":
        return kind, net, None, None
    cat = get_cat(net, nums[1]) if len(nums) > 1 else None
    if cat is None:
        return None
    if kind == "c":
        return kind, net, cat, None
    if kind == "s" and len(nums) > 2 and nums[2] < len(get_items(net, cat)):
        return kind, net, cat, nums[2]
    return None


def dl_apply(target):
    """O'chirishni bajaradi va o'chirilgan xizmatlar sonini qaytaradi."""
    res = dl_resolve(target)
    if not res:
        return 0
    kind, net, cat, si = res
    ui = db_data["ui"]
    if kind == "n":
        cnt = len(zr_items(target))
        db_data["services"].pop(net, None)
        for k in [k for k in db_data["items"] if k.startswith(f"{net}|")]:
            del db_data["items"][k]
        ui["net_styles"].pop(net, None)
        for k in [k for k in ui["cat_styles"] if k.startswith(f"{net}|")]:
            ui["cat_styles"].pop(k)
    elif kind == "c":
        cnt = len(get_items(net, cat))
        db_data["services"][net].remove(cat)
        db_data["items"].pop(item_key(net, cat), None)
        ui["cat_styles"].pop(f"{net}|{cat}", None)
    else:
        get_items(net, cat).pop(si)
        cnt = 1
    save_data(db_data)
    return cnt


def dl_home_view():
    ns = nets()
    if not ns:
        return "⚠️ Hozircha o'chirish uchun hech narsa yo'q.", None
    rows = grid([InlineKeyboardButton(text=n, callback_data=f"dl_n_{i}", **sk(net_style(n))) for i, n in enumerate(ns)])
    return "🗑 Qaysinisini o'chirmoqchisiz?\n\nIjtimoiy tarmoqni tanlang:", kb(rows)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_free_del"))
async def dl_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != OWNER_ID:
        return
    text, markup = dl_home_view()
    await message.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "dl_home")
async def dl_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    text, markup = dl_home_view()
    await safe_edit(call.message, text, markup)
    await call.answer()


@dp.callback_query(F.data.regexp(r"^dl_n_\d+$"))
async def dl_net_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    ni = int(call.data.rsplit("_", 1)[1])
    net = get_net(ni)
    if net is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    cats = db_data["services"].get(net, [])
    rows = [[InlineKeyboardButton(text="🗑 Butun tarmoq", callback_data=f"dl_t_n{ni}", **sk("danger"))]]
    rows += grid([InlineKeyboardButton(text=c, callback_data=f"dl_c_{ni}_{ci}", **sk(cat_style(net, c))) for ci, c in enumerate(cats)])
    rows.append(back_btn("dl_home"))
    await safe_edit(call.message, f"📱 {net}\n\nQaysinisini o'chirmoqchisiz?", kb(rows))
    await call.answer()


@dp.callback_query(F.data.regexp(r"^dl_c_\d+_\d+$"))
async def dl_cat_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text="🗑 Butun bo'lim", callback_data=f"dl_t_c{ni}_{ci}", **sk("danger"))]]
    for si, it in enumerate(get_items(net, cat)):
        rows.append([InlineKeyboardButton(
            text=f"{it['name']} — {fmt_money(it['rate'])} so'm", callback_data=f"dl_t_s{ni}_{ci}_{si}",
            **sk(srv_style(it)))])
    rows.append(back_btn(f"dl_n_{ni}"))
    await safe_edit(call.message, f"📂 {net} › {cat}\n\nQaysinisini o'chirmoqchisiz?", kb(rows))
    await call.answer()


def dl_back_target(target):
    kind, rest = target[0], target[1:]
    if kind == "s":
        ni, ci, _ = rest.split("_")
        return f"dl_c_{ni}_{ci}"
    if kind == "c":
        return f"dl_n_{rest.split('_')[0]}"
    return "dl_home"


@dp.callback_query(F.data.regexp(r"^dl_t_[ncs]"))
async def dl_confirm_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    target = call.data[len("dl_t_"):]
    res = dl_resolve(target)
    if not res:
        await call.answer("⚠️ Topilmadi (ro'yxat o'zgargan)", show_alert=True)
        return
    kind, net, _, _ = res
    lines = [zr_title(target), ""]
    if kind == "n":
        lines.append(f"📂 Bo'limlar: {len(db_data['services'].get(net, []))} ta")
    if kind in ("n", "c"):
        lines.append(f"📦 Xizmatlar: {len(zr_items(target))} ta")
    lines += ["", "⚠️ O'chirilgach QAYTARIB BO'LMAYDI!", "Davom etasizmi?"]
    rows = [[
        InlineKeyboardButton(text="✅ Ha, o'chirilsin", callback_data=f"dl_go_{target}", **sk("danger")),
        InlineKeyboardButton(text="◀ Yo'q", callback_data="dl_home"),
    ]]
    rows.append(back_btn(dl_back_target(target)))
    await safe_edit(call.message, "\n".join(lines), kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("dl_go_"))
async def dl_go_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    target = call.data[len("dl_go_"):]
    if not dl_resolve(target):
        await call.answer("⚠️ Topilmadi (allaqachon o'chirilgan bo'lishi mumkin)", show_alert=True)
        return
    title = zr_title(target)
    cnt = dl_apply(target)
    await safe_edit(
        call.message,
        f"🗑 O'chirildi!\n\n{title}\n📦 O'chirilgan xizmatlar: {cnt} ta",
        kb([back_btn("dl_home")]),
    )
    await call.answer()


@dp.message(not_renaming, lambda m: bool(m.text) and (is_btn(m.text, "account") or m.text == "💰 Balans"))
async def account_handler(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    u = get_user(uid)
    orders_count = sum(1 for o in db_data["orders"] if o.get("user_id") == uid)
    lines = [
        f"🆔 ID raqamingiz: <code>{uid}</code>",
        f"💵 Balansingiz: {fmt_money(u.get('balance', 0))} so'm",
        f"🌐 Buyurtmalaringiz: {orders_count} ta",
        f"👤 Takliflaringiz soni: {u.get('referrals', 0)} ta",
        f"🎁 Chegirmangiz foizi: {u.get('discount', 0)}%",
        f"💳 Kiritgan pullaringiz: {fmt_money(u.get('deposited', 0))} so'm",
        f"🎉 Siz shu kungacha {fmt_money(u.get('cashback', 0))} so'm keshbek olgansiz",
    ]
    text = "\n\n".join(f"<blockquote>{line}</blockquote>" for line in lines)
    url = get_webapp_url(uid)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 SMM Mini App (Do'kon) 🚀", web_app=types.WebAppInfo(url=url))]
    ])
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


ORDER_STATUS_UZ = {
    "pending": "⏳ Kutilmoqda",
    "processing": "🔄 Jarayonda",
    "in progress": "🔄 Jarayonda",
    "completed": "✅ Bajarildi",
    "partial": "⚠️ Qisman",
    "canceled": "🚫 Bekor qilindi",
    "cancelled": "🚫 Bekor qilindi",
    "fail": "🚫 Bekor qilindi",
    "failed": "🚫 Bekor qilindi",
    "refunded": "↩️ Qaytarildi",
}


def info_text(key, uid):
    txt = db_data["ui"].get("texts", {}).get(key) or INFO_DEFAULTS[key]
    return txt.replace("{id}", str(uid))


# ---------------------------------------------------------------- BUYURTMALARIM (SAHIFALAB)
ORDERS_PER_PAGE = 5

try:
    from aiogram.types import LinkPreviewOptions
    NO_PREVIEW = {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
except ImportError:
    NO_PREVIEW = {"disable_web_page_preview": True}


def order_status_text(o, raw_status=None):
    st = str(raw_status or o.get("status") or "Pending")
    return ORDER_STATUS_UZ.get(st.lower().strip(), escape(st))


def orders_page(uid, page):
    mine = [o for o in db_data["orders"] if o.get("user_id") == uid][::-1]  # yangilari birinchi
    if not mine:
        return None
    total = (len(mine) + ORDERS_PER_PAGE - 1) // ORDERS_PER_PAGE
    page = min(max(page, 1), total)
    chunk = mine[(page - 1) * ORDERS_PER_PAGE: page * ORDERS_PER_PAGE]
    lines = ["📦 <b>Buyurtma idsi — 📋 Holat</b>\n"]
    for o in chunk:
        lines.append(f"<code>{shown_id(o)}</code> - {order_status_text(o)}")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ord_p_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total}", callback_data="ord_noop"))
    if page < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ord_p_{page + 1}"))
    rows = [
        nav,
        [InlineKeyboardButton(text="🔄 Qayta tiklash", callback_data=f"ord_act_refill_{page}"),
         InlineKeyboardButton(text="⛔ Bekor qilish", callback_data=f"ord_act_cancel_{page}")],
        [InlineKeyboardButton(text="🔎 Buyurtma ma'lumoti", callback_data=f"ord_act_info_{page}")],
    ]
    return "\n".join(lines), kb(rows)


def order_detail_text(o, live=None):
    try:
        dt = datetime.strptime(o["time"], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        dt = "—"
    try:
        price = f"{float(o.get('price', 0)):.2f}"
    except (TypeError, ValueError):
        price = "0.00"
    return (
        f"📦 Buyurtma ID: <b>{shown_id(o)}</b>\n"
        f"📅 Buyurtma sanasi: {dt}\n"
        f"📋 Xizmat nomi: {escape(str(o.get('service', '—')))}\n"
        f"🔗 Havola: {escape(str(o.get('link', '—')))}\n"
        f"👤 Buyurtma miqdori: {o.get('quantity')} ta\n"
        f"💰 Sarflangan pul: {price} so'm\n"
        f"📊 Holat: {order_status_text(o, (live or {}).get('status'))}"
    )


def find_order(raw_id):
    for o in reversed(db_data["orders"]):
        if str(shown_id(o)) == raw_id:
            return o
    return None


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "orders"))
async def my_orders_handler(message: types.Message, state: FSMContext):
    await state.clear()
    res = orders_page(message.from_user.id, 1)
    if not res:
        await message.answer("⚠️ Sizda hali buyurtmalar yo'q.")
        return
    text, markup = res
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@dp.callback_query(F.data.regexp(r"^ord_p_\d+$"))
async def orders_page_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    res = orders_page(call.from_user.id, int(call.data.rsplit("_", 1)[1]))
    if not res:
        await safe_edit(call.message, "⚠️ Sizda hali buyurtmalar yo'q.")
    else:
        text, markup = res
        await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "ord_noop")
async def orders_noop_cb(call: types.CallbackQuery):
    await call.answer()


ORDER_ACTION_PROMPTS = {
    "info": (
        "<b>Bu bo'limda buyurtmangiz haqida ma'lumot olishingiz mumkin!</b>\n\n"
        "Buning uchun buyurtmaning 🆔 raqamini botga yuboring 👇"
    ),
    "refill": (
        "<b>🔄 Buyurtmani qayta tiklash uchun</b>\n\n"
        "buyurtmaning 🆔 raqamini botga yuboring 👇"
    ),
    "cancel": (
        "<b>⛔ Buyurtmani bekor qilish uchun</b>\n\n"
        "buyurtmaning 🆔 raqamini botga yuboring 👇"
    ),
}


@dp.callback_query(F.data.regexp(r"^ord_act_(info|refill|cancel)_\d+$"))
async def order_action_start(call: types.CallbackQuery, state: FSMContext):
    _, _, mode, page = call.data.split("_")
    await state.set_state(OrderInfoState.waiting_for_id)
    await state.update_data(mode=mode, page=int(page))
    await safe_edit(call.message, ORDER_ACTION_PROMPTS[mode], kb([back_btn(f"ord_p_{page}")]), parse_mode="HTML")
    await call.answer()


def _api_error(resp):
    if isinstance(resp, dict):
        return resp.get("error") or resp
    return resp


@dp.message(StateFilter(OrderInfoState.waiting_for_id))
async def order_id_received(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    d = await state.get_data()
    mode, page = d.get("mode", "info"), d.get("page", 1)
    back = kb([back_btn(f"ord_p_{page}")])
    raw = (message.text or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("❌ Faqat buyurtma ID raqamini yuboring. Masalan: 20490", reply_markup=back)
        return
    o = find_order(raw)
    if not o:
        await message.answer("❌ Bunday buyurtma topilmadi. ID raqamini tekshirib, qayta yuboring:", reply_markup=back)
        return
    if o.get("user_id") != message.from_user.id:
        await message.answer("❗ Buyurtma sizga tegishli emas", reply_markup=back)
        return

    api = find_api_by_url(o.get("api_url", ""))

    if mode == "info":
        wait = await message.answer("⏳ Ma'lumot olinmoqda...")
        live = None
        if api:
            try:
                ok, resp = await api_post(api["url"], {
                    "key": api["key"], "action": "status", "order": o["provider_order_id"],
                })
                if ok and isinstance(resp, dict) and "status" in resp:
                    live = resp
            except Exception as e:
                print("Buyurtma holatini olishda xato:", e)
        text = order_detail_text(o, live)
        if live is None:
            text += "\n\n⚠️ Provayderdan yangi holat olinmadi, oxirgi saqlangan holat ko'rsatilmoqda."
        try:
            await wait.edit_text(text, parse_mode="HTML", **NO_PREVIEW)
        except Exception:
            await message.answer(text, parse_mode="HTML", **NO_PREVIEW)
        return

    if not api:
        await state.clear()
        await message.answer("⚠️ Bu buyurtmaning provayderi topilmadi. Admin bilan bog'laning.")
        return

    if mode == "refill":
        if str(o.get("status", "")).lower().strip() != "completed":
            await message.answer("❌ Qayta tiklash faqat bajarilgan buyurtmalar uchun mumkin.", reply_markup=back)
            return
        try:
            ok, resp = await api_post(api["url"], {"key": api["key"], "action": "refill", "order": o["provider_order_id"]})
        except Exception as e:
            ok, resp = False, str(e)
        await state.clear()
        if ok and isinstance(resp, dict) and resp.get("refill") not in (None, "", 0, "0") and "error" not in resp:
            await message.answer(f"✅ {shown_id(o)} raqamli buyurtma uchun qayta tiklash so'rovi yuborildi.")
        else:
            await message.answer(f"❌ Qayta tiklash so'rovi qabul qilinmadi.\nSabab: {_api_error(resp)}")
        return

    # mode == "cancel"
    if o.get("final"):
        await message.answer("❌ Bu buyurtma allaqachon yakunlangan, uni bekor qilib bo'lmaydi.", reply_markup=back)
        return
    try:
        ok, resp = await api_post(api["url"], {"key": api["key"], "action": "cancel", "orders": str(o["provider_order_id"])})
    except Exception as e:
        ok, resp = False, str(e)
    await state.clear()
    result = None
    if ok:
        item = resp[0] if isinstance(resp, list) and resp and isinstance(resp[0], dict) else resp
        if isinstance(item, dict):
            result = item.get("cancel", item.get("error"))
    if result in (1, "1", True):
        await message.answer(
            f"✅ {shown_id(o)} raqamli buyurtmani bekor qilish so'rovi yuborildi.\n\n"
            "Provayder tasdiqlagach, mablag' balansingizga avtomatik qaytariladi."
        )
    else:
        err = result.get("error") if isinstance(result, dict) else (result or resp)
        await message.answer(f"❌ Buyurtmani bekor qilib bo'lmadi.\nSabab: {err}")


# ---------------------------------------------------------------- PUL KIRITISH (TELEGRAM STARS VA KARTALAR)
def is_stars_active():
    return db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200}).get("active", True)


def get_stars_rate():
    return db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200}).get("rate", 200)


def user_active_payments():
    return [p for p in db_data.get("payment_systems", []) if p.get("active", True)]


def min_deposit():
    try:
        return max(float(db_data["settings"].get("min_deposit", 0) or 0), 1)
    except (TypeError, ValueError):
        return 1


def user_payment_list_text():
    text = "💳 <b>To'lov usulini tanlang:</b>\n\n"
    desc = db_data.get("settings", {}).get("deposit_desc")
    if desc:
        text += f"<i>{escape(desc)}</i>\n\n"
    if is_stars_active():
        rate = get_stars_rate()
        text += (
            "⭐ <b>Telegram Stars (Avtomatik to'lov):</b>\n"
            f"— 1 ta Stars = <b>{rate:,} so'm</b>\n"
            "— To'lov amalga oshishi bilan balansingiz <b>bir zumda avtomatik</b> to'ldiriladi!\n\n"
        )
    cards = user_active_payments()
    if cards:
        text += (
            "💳 <b>Karta / Boshqa to'lov tizimlari:</b>\n"
            "— Kartaga to'lov qilib chekni yuborasiz va admin tasdiqlagach balansingiz to'ldiriladi.\n\n"
        )
    text += "<i>Quyidagi to'lov usullaridan birini tanlang:</i>"
    return text.strip()


def user_payment_list_markup():
    rows = []
    if is_stars_active():
        rate = get_stars_rate()
        rows.append([InlineKeyboardButton(text=f"⭐ Telegram Stars (1 ta = {rate:,} so'm)", callback_data="udep_stars")])
    for i, p in enumerate(db_data.get("payment_systems", [])):
        if p.get("active", True):
            rows.append([InlineKeyboardButton(text=f"💳 {p['name']}", callback_data=f"udep_{i}")])
    return kb(rows)


def stars_fast_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ 1", callback_data="stars_fast_1"),
            InlineKeyboardButton(text="⭐ 5", callback_data="stars_fast_5"),
            InlineKeyboardButton(text="⭐ 10", callback_data="stars_fast_10"),
        ],
        [
            InlineKeyboardButton(text="⭐ 25", callback_data="stars_fast_25"),
            InlineKeyboardButton(text="⭐ 50", callback_data="stars_fast_50"),
            InlineKeyboardButton(text="⭐ 100", callback_data="stars_fast_100"),
        ],
        [
            InlineKeyboardButton(text="🔙 Boshqa to'lov usullari", callback_data="udep_back"),
        ]
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "deposit"))
async def deposit_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_stars_active() and not user_active_payments():
        await message.answer("❌ Hozircha faol to'lov usullari mavjud emas. Birozdan so'ng qayta urinib ko'ring.")
        return
    await message.answer(user_payment_list_text(), reply_markup=user_payment_list_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "udep_stars")
async def user_deposit_stars_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    rate = get_stars_rate()
    await state.set_state(StarsDepositState.waiting_for_stars)
    text = (
        "⭐ <b>Telegram Stars</b>\n\n"
        f"1 ta Stars = <b>{rate:,} so'm</b>\n\n"
        "Qancha to'ldirmoqchisiz?"
    )
    await safe_edit(call.message, text, stars_fast_keyboard(), parse_mode="HTML")
    await call.answer()


async def create_stars_invoice_data(user_id: int, stars_count: int):
    rate = get_stars_rate()
    total_uzs = stars_count * rate
    prices = [LabeledPrice(label=f"{stars_count} Stars", amount=stars_count)]
    payload = f"stars_dep_{user_id}_{stars_count}_{total_uzs}_{int(time.time())}"
    
    try:
        invoice_link = await bot.create_invoice_link(
            title=f"⭐ {stars_count} Stars",
            description=f"Hisobingizni {total_uzs:,} so'mga to'ldirish",
            payload=payload,
            currency="XTR",
            prices=prices
        )
    except Exception as e:
        return False, str(e)
        
    text = (
        f"⭐ <b>{stars_count} ta Stars = {total_uzs:,} so'm bo'ladi.</b>\n\n"
        "To'lov qilish uchun quyidagi tugmani bosing:"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐️ {stars_count} Stars to'lash", url=invoice_link)],
        [InlineKeyboardButton(text="🔄 Boshqa miqdor", callback_data="udep_stars")],
        [InlineKeyboardButton(text="🔙 Boshqa to'lov usullari", callback_data="udep_back")]
    ])
    return True, (text, markup)


@dp.callback_query(F.data.startswith("stars_fast_"))
async def user_stars_fast_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    stars_count = int(call.data.replace("stars_fast_", ""))
    ok, res = await create_stars_invoice_data(call.from_user.id, stars_count)
    if not ok:
        await call.answer(f"Xatolik: {res}", show_alert=True)
        return
    msg_text, markup = res
    await safe_edit(call.message, msg_text, markup, parse_mode="HTML")
    await call.answer()


@dp.message(StarsDepositState.waiting_for_stars)
async def user_stars_amount_input(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("⚠️ Iltimos, musbat butun son kiriting (Masalan: <code>1</code> yoki <code>10</code>):", parse_mode="HTML")
        return
    
    stars_count = int(text)
    if stars_count > 100000:
        await message.answer("⚠️ Bir martalik to'lov uchun maksimal 100,000 Stars kiritish mumkin.", parse_mode="HTML")
        return
        
    await state.clear()
    ok, res = await create_stars_invoice_data(message.from_user.id, stars_count)
    if not ok:
        await message.answer(f"❌ To'lov havolasi yaratishda xatolik: {res}")
        return
    msg_text, markup = res
    await message.answer(msg_text, reply_markup=markup, parse_mode="HTML")


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    sp = message.successful_payment
    if sp.currency == "XTR":
        stars_count = sp.total_amount
        payload = sp.invoice_payload or ""
        parts = payload.split("_")
        if len(parts) >= 4 and parts[3].isdigit():
            total_uzs = int(parts[3])
        else:
            rate = get_stars_rate()
            total_uzs = stars_count * rate
            
        uid = str(message.from_user.id)
        u = get_user(uid)
        u["balance"] = u.get("balance", 0) + total_uzs
        u["deposited"] = u.get("deposited", 0) + total_uzs
        
        dep_entry = {
            "id": len(db_data.get("deposits", [])) + 1,
            "user_id": uid,
            "username": message.from_user.username or "",
            "type": "stars",
            "stars": stars_count,
            "amount": total_uzs,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "charge_id": sp.telegram_payment_charge_id
        }
        db_data.setdefault("deposits", []).append(dep_entry)
        save_data(db_data)
        
        user_text = (
            "🎉 <b>TO'LOV MUVAFFAQIYATLI QABUL QILINDI!</b>\n\n"
            f"⭐️ <b>To'langan:</b> <code>{stars_count:,}</code> ta Stars\n"
            f"💰 <b>Balansingizga qo'shildi:</b> <b>+{total_uzs:,} so'm</b>\n"
            f"💳 <b>Yangi balansingiz:</b> <b>{u['balance']:,} so'm</b>\n\n"
            "<i>Bizni tanlaganingiz uchun rahmat! Xizmatlarimizdan bemalol foydalanishingiz mumkin. 🚀</i>"
        )
        await message.answer(user_text, parse_mode="HTML")
        
        u_name = f"@{message.from_user.username}" if message.from_user.username else "mavjud emas"
        admin_text = (
            "⭐️ <b>YANGI AVTOMATIK STARS TO'LOVI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Foydalanuvchi:</b> {message.from_user.full_name} ({u_name})\n"
            f"🆔 <b>ID:</b> <code>{uid}</code>\n"
            f"⭐ <b>Stars miqdori:</b> <code>{stars_count:,}</code> ta\n"
            f"💵 <b>Balansiga qo'shildi:</b> <code>+{total_uzs:,}</code> so'm\n"
            f"🧾 <b>Telegram Charge ID:</b> <code>{sp.telegram_payment_charge_id}</code>\n"
            f"🕒 <b>Sana:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        try:
            await bot.send_message(OWNER_ID, admin_text, parse_mode="HTML")
        except Exception:
            pass


def user_payment_card_text(p):
    text = f"💳 <b>To'lov tizimi:</b>  {escape(p['name'])}\n\n"
    text += f"💳 <b>Karta raqami:</b>  <code>{escape(str(p['card']))}</code>\n"
    if p.get("owner"):
        text += f"👤 <b>Karta egasi:</b>  {escape(str(p['owner']))}\n"
    if p.get("desc"):
        text += f"\n📋 {escape(p['desc'])}\n"
    text += (
        "\n📄 <b>Hisobingizni to'ldirish uchun quyidagi amallarni bajaring:</b>\n\n"
        "1️⃣ Yuqoridagi kartaga to'lov qiling;\n"
        "2️⃣ «✅ To'lov qildim» tugmasini bosing;\n"
        "3️⃣ Kartaga tashlagan pul miqdorini kiriting;\n"
        "4️⃣ To'lov chekini (skrinshot) yuboring;\n"
        "5️⃣ Admin to'lovni tekshirib tasdiqlagach balansingizga qo'shiladi.\n\n"
        "⚠️ <b>To'lovingiz 5 daqiqadan 24 soatgacha bo'lgan vaqt ichida amalga oshiriladi!</b>"
    )
    return text


def deposit_back_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=btn_text("back"))]], resize_keyboard=True)


@dp.callback_query(F.data == "udep_back")
async def user_deposit_back_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_stars_active() and not user_active_payments():
        await safe_edit(call.message, "❌ Hozircha faol to'lov usullari mavjud emas.", None)
        await call.answer()
        return
    await safe_edit(call.message, user_payment_list_text(), user_payment_list_markup(), parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data.regexp(r"^udep_\d+$"))
async def user_deposit_view_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    idx = int(call.data.replace("udep_", ""))
    items = db_data.get("payment_systems", [])
    if idx >= len(items) or not items[idx].get("active", True):
        await call.answer("⚠️ Bu to'lov usuli topilmadi", show_alert=True)
        return
    p = items[idx]
    markup = kb([
        [InlineKeyboardButton(text="✅ To'lov qildim", callback_data=f"udep_paid_{idx}", style="success")],
        back_btn("udep_back"),
    ])
    await safe_edit(call.message, user_payment_card_text(p), markup, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data.regexp(r"^udep_paid_\d+$"))
async def user_deposit_paid_cb(call: types.CallbackQuery, state: FSMContext):
    idx = int(call.data.rsplit("_", 1)[1])
    items = db_data.get("payment_systems", [])
    if idx >= len(items) or not items[idx].get("active", True):
        await call.answer("⚠️ Bu to'lov usuli topilmadi", show_alert=True)
        return
    await state.clear()
    await state.set_state(DepositState.waiting_for_amount)
    await state.update_data(pay_name=items[idx]["name"])
    await call.message.answer(
        f"💵 <b>To'lov miqdorini kiriting:</b>\n\n<i>Minimal: {fmt_money(min_deposit())} so'm</i>",
        parse_mode="HTML",
        reply_markup=deposit_back_kb(),
    )
    await call.answer()


@dp.message(
    lambda m: bool(m.text) and is_btn(m.text, "back"),
    StateFilter(DepositState.waiting_for_amount, DepositState.waiting_for_receipt),
)
async def deposit_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🖥️ Asosiy menyudasiz", reply_markup=main_kb(message.from_user.id))


@dp.message(StateFilter(DepositState.waiting_for_amount))
async def deposit_amount(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or len(raw) > 12:
        await message.answer("❌ Faqat son kiriting. Masalan: 10000", reply_markup=deposit_back_kb())
        return
    amount = int(raw)
    mn = min_deposit()
    if amount < mn:
        await message.answer(f"❌ Minimal to'lov miqdori: {fmt_money(mn)} so'm", reply_markup=deposit_back_kb())
        return
    await state.update_data(amount=amount)
    await state.set_state(DepositState.waiting_for_receipt)
    await message.answer("📝 To'lov chekini rasmga olib yuboring:", reply_markup=deposit_back_kb())


def deposit_admin_text(d):
    return (
        f"💳 <b>Yangi to'lov so'rovi #{d.get('id', '—')}</b>\n\n"
        f"👤 Foydalanuvchi: {escape(d.get('name') or '—')}\n"
        f"📱 Username: {escape(d.get('username') or '—')}\n"
        f"🆔 Telegram ID: <code>{d.get('user_id', '—')}</code>\n"
        f"💳 To'lov tizimi: {escape(d.get('payment') or '—')}\n"
        f"💰 Miqdor: {fmt_money(d.get('amount', 0))} so'm\n"
        f"📅 Sana: {d.get('date', '—')}"
    )


def deposit_admin_markup(dep_id):
    return kb([[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"dep_ok_{dep_id}", style="success"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"dep_no_{dep_id}", style="danger"),
    ]])


@dp.message(StateFilter(DepositState.waiting_for_receipt))
async def deposit_receipt(message: types.Message, state: FSMContext):
    if message.text and is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    file_id, file_type = None, None
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, file_type = message.document.file_id, "document"
    if not file_id:
        await message.answer("❌ Iltimos, to'lov chekini rasm (screenshot) ko'rinishida yuboring.", reply_markup=deposit_back_kb())
        return

    data = await state.get_data()
    if "amount" not in data:
        await state.clear()
        await message.answer("⚠️ So'rov topilmadi, qaytadan boshlang.", reply_markup=main_kb(message.from_user.id))
        return

    tg = message.from_user
    dep_id = max([d.get("id", 0) for d in db_data.get("deposits", [])] or [0]) + 1
    rec = {
        "id": dep_id,
        "user_id": tg.id,
        "name": " ".join(x for x in (tg.first_name, tg.last_name) if x),
        "username": f"@{tg.username}" if tg.username else "",
        "payment": data.get("pay_name", ""),
        "amount": data["amount"],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
        "file_id": file_id,
        "file_type": file_type,
    }
    try:
        if file_type == "photo":
            await bot.send_photo(OWNER_ID, file_id, caption=deposit_admin_text(rec),
                                 parse_mode="HTML", reply_markup=deposit_admin_markup(dep_id))
        else:
            await bot.send_document(OWNER_ID, file_id, caption=deposit_admin_text(rec),
                                    parse_mode="HTML", reply_markup=deposit_admin_markup(dep_id))
    except Exception as e:
        print("To'lov so'rovi adminga yuborilmadi:", e)
        await message.answer("❌ So'rovni yuborishda xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring.",
                             reply_markup=main_kb(tg.id))
        await state.clear()
        return

    db_data.setdefault("deposits", []).append(rec)
    save_data(db_data)
    await state.clear()
    await message.answer(
        "⏳ <b>Qabul qilindi!</b>\n\n<i>To'lovingiz admin tomonidan tekshirilgach balansingizga qo'shiladi!</i>",
        parse_mode="HTML",
        reply_markup=main_kb(tg.id),
    )


@dp.callback_query(F.data.regexp(r"^dep_(ok|no)_\d+$"))
async def deposit_decision(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    _, act, sid = call.data.split("_")
    d = next((x for x in db_data["deposits"] if x["id"] == int(sid)), None)
    if not d:
        await call.answer("⚠️ So'rov topilmadi", show_alert=True)
        return
    if d["status"] != "pending":
        await call.answer("ℹ️ Bu so'rov allaqachon ko'rib chiqilgan", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    amount = d["amount"]
    uid = d["user_id"]
    d["handled"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if act == "ok":
        u = get_user(uid)
        u["balance"] = u.get("balance", 0) + amount
        u["deposited"] = u.get("deposited", 0) + amount
        d["status"] = "approved"
        note = f"✅ Hisobingizga {fmt_money(amount)} so'm qo'shildi.\n👛 Jami: {fmt_money(u['balance'])} so'm"
        result = f"\n\n✅ <b>TASDIQLANDI</b> — {fmt_money(amount)} so'm qo'shildi"
        toast = f"✅ {fmt_money(amount)} so'm tasdiqlandi!"
    else:
        d["status"] = "rejected"
        note = (
            f"❌ To'lov so'rovingiz #{d['id']} rad etildi.\n💵 Miqdor: {fmt_money(amount)} so'm\n\n"
            "Savollaringiz bo'lsa, admin bilan bog'laning."
        )
        result = "\n\n❌ <b>RAD ETILDI</b>"
        toast = "❌ So'rov rad etildi"
    save_data(db_data)

    try:
        await call.message.edit_caption(caption=deposit_admin_text(d) + result, parse_mode="HTML", reply_markup=None)
    except Exception as e:
        print("Admin xabarini tahrirlashda xato:", e)
    await call.answer(toast)
    await notify_user(uid, note)


# ---------------------------------------------------------------- PUL YEG'ISH (REFERAL / KUNLIK BONUS)
def earn_home_markup():
    return kb([
        [InlineKeyboardButton(text="🔗 Takliflar", callback_data="earn_ref")],
        [InlineKeyboardButton(text="🎁 Kunlik bonus", callback_data="earn_bonus")],
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "earn"))
async def earn_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("⬇️ Quyidagilardan birini tanlang:", reply_markup=earn_home_markup())


@dp.callback_query(F.data == "earn_home")
async def earn_home_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, "⬇️ Quyidagilardan birini tanlang:", earn_home_markup())
    await call.answer()


@dp.callback_query(F.data == "earn_ref")
async def earn_ref_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    u = get_user(uid)
    username = await get_bot_username()
    link = f"https://t.me/{username}?start={uid}"
    bonus = db_data["settings"].get("referral_bonus", 0)
    text = (
        "🔗 Sizning referal havolangiz:\n\n"
        f"{link}\n\n"
        f"1 ta referal uchun {fmt_money(bonus)} so'm beriladi\n\n"
        f"🗣 Referallaringiz: {u.get('referrals', 0)} ta"
    )
    rows = [
        [InlineKeyboardButton(text="Ulashish", url=f"https://t.me/share/url?url={link}")],
        [InlineKeyboardButton(text="Orqaga", callback_data="earn_home")],
    ]
    await safe_edit(call.message, text, kb(rows))
    await call.answer()


@dp.callback_query(F.data == "earn_bonus")
async def earn_bonus_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    u = get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    back_row = [InlineKeyboardButton(text="Orqaga", callback_data="earn_home")]
    if u.get("last_bonus_date") == today:
        text = "❌ Siz bugun bonus olgansiz!\n\n🎁 Keyingi bonus 00:00 dan so'ng."
    else:
        bonus = db_data["settings"].get("daily_bonus", 0)
        u["balance"] = u.get("balance", 0) + bonus
        u["last_bonus_date"] = today
        save_data(db_data)
        text = f"🎁 Kunlik bonus berildi!\n\n✅ Bonus: {fmt_money(bonus)} so'm"
    await safe_edit(call.message, text, kb([back_row]))
    await call.answer()


# ---------------------------------------------------------------- HAMKORLIK DASTURI
@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "hamkorlik"))
async def hamkorlik_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(info_text("hamkorlik", message.from_user.id))


# ---------------------------------------------------------------- QO'LLANMA / FOYDALANISH QOIDALARI
# Rasmiy kanal (masalan "@kanalim"). Bo'sh qoldirsangiz, kanal qatori chiqmaydi.
BOT_CHANNEL = ""

GUIDE_PROMPT = "👇 Kerakli tugmani bosing"
BAR = "━━━━━━━━━━━━━━━━━━"


_bot_name = None


async def get_bot_name():
    global _bot_name
    if _bot_name is None:
        me = await bot.get_me()
        _bot_name = me.first_name or me.username or "Bot"
    return _bot_name


async def rules_text():
    uname = escape(await get_bot_username())
    name = escape(await get_bot_name())
    official = f"\n📢 Rasmiy kanal: {escape(BOT_CHANNEL)}" if BOT_CHANNEL else ""
    text = (
        f"⚖️ {name} — Foydalanish Qoidalari\n"
        "👋 Hurmatli foydalanuvchi!\n"
        "Botdan foydalanishni davom ettirish orqali siz quyidagi qoidalarning barchasiga rozilik bildirgan hisoblanasiz.\n"
        "⚠️ Keyinchalik \"bilmagan edim\", \"o'qimagan edim\", \"menga aytilmagan edi\" yoki shunga o'xshash murojaatlar asos bo'la olmaydi.\n"
        f"{BAR}\n"
        "💳 Balansni to'ldirish qoidalari\n"
        "• 💰 Balansni to'ldirishdan oldin summa va to'lov ma'lumotlarini diqqat bilan tekshiring.\n"
        "• 🚫 Bot balansiga muvaffaqiyatli tushirilgan mablag' hech qanday holatda qaytarib berilmaydi.\n"
        "• 👤 To'lovni amalga oshirish jarayonida uchinchi shaxslar (do'st, qarindosh, tanish va boshqalar) aralashgan holatlar bo'yicha murojaatlar ko'rib chiqilmaydi. To'lov faqat hisob egasining shaxsiy javobgarligi ostida amalga oshirilishi kerak.\n"
        "• ⏰ To'lov balansga tushmagan bo'lsa, 24 soat ichida adminga murojaat qilishingiz shart. Ushbu muddatdan keyingi murojaatlar ko'rib chiqilmaydi.\n"
        f"{BAR}\n"
        "🛍 Buyurtma berish qoidalari\n"
        "• 📖 Har qanday xizmatni xarid qilishdan oldin uning tavsifi va ishlash tartibini to'liq o'qib chiqing.\n"
        "• 🔗 Buyurtma berishda havola, username yoki boshqa ma'lumotlarni to'g'ri kiritganingizga ishonch hosil qiling.\n"
        "• ❌ Noto'g'ri kiritilgan havolalar bo'yicha buyurtmalar bajarilgan hisoblanadi va mablag' qaytarilmaydi.\n"
        "• 💸 Xizmat tavsifida bekor qilish imkoniyati ko'rsatilmagan bo'lsa, buyurtmani bekor qilish yoki pulni qaytarishning imkoni mavjud emas.\n"
        f"{BAR}\n"
        "📱 Virtual raqamlar\n"
        "• 📲 Virtual raqamlar kafolatsiz xizmat hisoblanadi.\n"
        "• ⚠️ Telegram yoki boshqa platformalar tomonidan qo'yilgan spam, blok, cheklov yoki boshqa muammolar uchun bot ma'muriyati javobgar emas.\n"
        "• ✅ Virtual raqam xarid qilishingiz orqali ushbu shartlarga rozilik bildirgan hisoblanasiz.\n"
        f"{BAR}\n"
        "⭐ Kafolatsiz va boshqa xizmatlar\n"
        "• ‼️ Kafolatsiz va tavsifida kafolat ko'rsatilmagan xizmatlar uchun hech qanday kafolat berilmaydi.\n"
        f"• 🌐 Telegram, Instagram, YouTube yoki boshqa platformalar siyosati va texnik o'zgarishlari sabab yuzaga kelgan muammolar uchun {name} javobgar hisoblanmaydi.\n"
        f"{BAR}\n"
        "👥 Referal tizimi\n"
        "• 🚫 Soxta akkauntlar orqali referal yig'ish taqiqlanadi.\n"
        "• 🚫 Qalbaki reklama yoki aldamchi usullar bilan foydalanuvchilarni referal qilish mumkin emas.\n"
        "• ⛔ Referal tizimini aldashga uringan foydalanuvchilar ogohlantirishsiz bloklanadi.\n"
        f"{BAR}\n"
        "⚙️ Tizimdan foydalanish\n"
        "• 🤖 Botdagi texnik xatolar yoki kamchiliklardan shaxsiy manfaat uchun foydalanishga urinish qat'iyan taqiqlanadi.\n"
        "• 🔒 Bunday holat aniqlansa, hisob doimiy bloklanadi va balans qaytarilmaydi.\n"
        "• 🚷 Botni bloklash yoki chatni o'chirish tavsiya etilmaydi. Natijada ayrim xizmatlar ishlamasligi yoki hisobingiz bilan bog'liq muammolar yuzaga kelishi mumkin.\n"
        f"{BAR}\n"
        "👨‍💻 Admin bilan muloqot\n"
        "• 🤝 Adminga hurmat bilan murojaat qiling.\n"
        "• ❌ Haqorat, tahdid, yolg'on yoki chalg'ituvchi ma'lumot yuborgan foydalanuvchilar ogohlantirishsiz doimiy banlanadi.\n"
        "• 📋 Admin zarurat tug'ilganda buyurtma yoki to'lov bo'yicha qo'shimcha ma'lumot talab qilish huquqiga ega.\n"
        f"{BAR}\n"
        "🔄 Qoida va xizmatlar\n"
        "• 🛠 Texnik ishlar sabab ayrim xizmatlar vaqtinchalik ishlamasligi mumkin.\n"
        "• 📢 Muhim o'zgarishlar rasmiy kanal orqali e'lon qilinadi.\n"
        f"• 📝 {name} ma'muriyati xizmat narxlari, xizmat tavsiflari, kafolat shartlari va ushbu qoidalarni istalgan vaqtda o'zgartirish yoki yangilash huquqiga ega.\n"
        f"{BAR}\n"
        "📌 Yakuniy eslatma\n"
        f"⚖️ @{uname} da barcha foydalanuvchilar uchun bir xil qoidalar amal qiladi.\n"
        "❗ Ushbu qoidalar bo'yicha hech qanday istisno qilinmaydi.\n"
        "💙 Bizning maqsadimiz — barcha foydalanuvchilarga sifatli, tezkor va adolatli xizmat ko'rsatish.\n"
        f"{BAR}\n"
        "🌐 Rasmiy sahifalarimiz\n"
        f"🤖 Bot: @{uname}"
        f"{official}\n"
        f"🙏 {name} xizmatlarini tanlaganingiz uchun rahmat! Sizga sifatli xizmat ko'rsatish biz uchun doimo ustuvor hisoblanadi."
    )
    return f"<blockquote>{text}</blockquote>"


GUIDE_HELP_TEXT = (
    "📘 Botdan foydalanish yo'riqnomasi\n\n"
    "🔄 Buyurtma bekor qilindimi?\n"
    "Xavotir olmang! Mablag‘ingiz avtomatik ravishda hisobingizga qaytariladi.\n\n"
    "🕒 To‘lovdan so‘ng kutish vaqti:\n"
    "Pullar hisobingizga 24 soat ichida tushadi, sabrli bo‘ling.\n\n"
    "🚫 Mablag‘lar qaytarilmaydi!\n"
    "Botga joylashtirilgan mablag‘lar qaytarib berilmaydi. To‘g‘ri va aniq harakat qiling.\n\n"
    "📩 Buyurtma haqida savol bormi?\n"
    "Muammo yoki savol tug‘ilsa, guruhdagi adminlarga murojaat qiling.\n\n"
    "⚠️ Bitta xizmat — bitta buyurtma:\n"
    "Bir vaqtda faqat bitta buyurtma berishingiz mumkin. Ko‘proq buyurtma uchun vaqt tanlashingiz kerak.\n\n"
    "🎯 Referal mukofoti:\n"
    "Agar referallingiz kanallarga qo‘shilmasa, mukofot puli berilmaydi.\n\n"
    "❗️ Xatolik yoki muammolar uchun: @offlien_boy_uzb"
)


def guide_markup():
    return kb([
        [InlineKeyboardButton(text="Foydalanish Qoidalari ❗", callback_data="guide_rules", **sk("primary"))],
        [InlineKeyboardButton(text="📘 Botdan foydalanish yo'riqnomasi", callback_data="guide_help", **sk("primary"))],
    ])


def guide_rules_markup():
    # qizil "ORQAGA" tugmasi
    return kb([[InlineKeyboardButton(text="🔸 ORQAGA 🔸", callback_data="guide_back", **sk("danger"))]])


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "guide"))
async def guide_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(GUIDE_PROMPT, reply_markup=guide_markup())


@dp.callback_query(F.data == "guide_rules")
async def guide_rules_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, await rules_text(), guide_rules_markup(), parse_mode="HTML", **NO_PREVIEW)
    await call.answer()


@dp.callback_query(F.data == "guide_help")
async def guide_help_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(
        call.message,
        f"<blockquote>{escape(GUIDE_HELP_TEXT)}</blockquote>",
        guide_rules_markup(),
        parse_mode="HTML",
        **NO_PREVIEW,
    )
    await call.answer()


@dp.callback_query(F.data == "guide_back")
async def guide_back_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, GUIDE_PROMPT, guide_markup())
    await call.answer()


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "support"))
async def support_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SupportState.waiting_for_question)
    await message.answer(
        "✍️ Savolingizni yozing:\n\n"
        "Biz avtomatik javob beramiz yoki mutaxassisga yuboramiz 📩",
        reply_markup=kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sup_q_cancel", **sk("danger"))]]),
    )


@dp.callback_query(F.data == "sup_q_cancel")
async def support_question_cancel_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, "Bekor qilindi ❌")
    await call.answer()


@dp.message(StateFilter(SupportState.waiting_for_question))
async def support_question_received(message: types.Message, state: FSMContext):
    if message.text and is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("❌ Iltimos, savolingizni matn ko'rinishida yozing:")
        return
    await state.clear()
    uid = message.from_user.id
    _support_questions[str(uid)] = text

    uname = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    try:
        await bot.forward_message(OWNER_ID, message.chat.id, message.message_id)
    except Exception:
        pass
    await bot.send_message(
        OWNER_ID,
        f"🆘 Yangi savol!\n\n👤 {uname} (ID: <code>{uid}</code>)\n\n💬 {escape(text)}",
        reply_markup=kb([[InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"sup_ans_{uid}", style="success")]]),
        parse_mode="HTML",
    )
    await message.answer("✅ Savolingiz qabul qilindi!\n\nBiz avtomatik javob beramiz yoki mutaxassisga yuboramiz 📩")


@dp.callback_query(F.data.startswith("sup_ans_"))
async def support_answer_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    uid = call.data.replace("sup_ans_", "")
    await state.clear()
    await state.set_state(SupportState.waiting_for_answer)
    await state.update_data(uid=uid)
    await call.message.answer(f"✍️ <code>{uid}</code> foydalanuvchiga javobingizni yozing:", parse_mode="HTML")
    await call.answer()


@dp.message(StateFilter(SupportState.waiting_for_answer))
async def support_answer_received(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    answer = (message.text or "").strip()
    if not answer:
        await message.answer("❌ Javob matn ko'rinishida bo'lishi kerak:")
        return
    data = await state.get_data()
    uid = data.get("uid")
    await state.update_data(answer=answer)
    question = _support_questions.get(str(uid), "")
    preview = (
        f"📩 <b>{uid}</b> foydalanuvchiga yuborilsinmi?\n\n"
        f"<b>Savol:</b>\n<blockquote>{escape(question)}</blockquote>\n\n"
        f"<b>Javobingiz:</b>\n<blockquote>{escape(answer)}</blockquote>"
    )
    await message.answer(
        preview,
        reply_markup=kb([[
            InlineKeyboardButton(text="✅ Yuborish", callback_data="sup_send"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sup_cancel"),
        ]]),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "sup_cancel")
async def support_answer_cancel(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.clear()
    await safe_edit(call.message, "❌ Bekor qilindi.")
    await call.answer()


@dp.callback_query(F.data == "sup_send")
async def support_answer_send(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    data = await state.get_data()
    uid, answer = data.get("uid"), data.get("answer")
    if not uid or not answer:
        await call.answer("⚠️ Ma'lumot topilmadi, qaytadan boshlang", show_alert=True)
        return
    question = _support_questions.get(str(uid), "")
    text = (
        "📩 <b>Administrator javob berdi!</b>\n\n"
        f"<b>Sizning savolingiz:</b>\n<blockquote>{escape(question)}</blockquote>\n\n"
        f"<b>Administrator javobi:</b>\n<blockquote>{escape(answer)}</blockquote>"
    )
    try:
        await bot.send_message(int(uid), text, parse_mode="HTML")
        ok = True
    except Exception:
        ok = False
    await state.clear()
    if ok:
        await safe_edit(call.message, "✅ Javob foydalanuvchiga muvaffaqiyatli yuborildi!")
    else:
        await safe_edit(call.message, "❌ Foydalanuvchiga yuborib bo'lmadi (botni bloklagan bo'lishi mumkin).")
    await call.answer()


@dp.message(Command("addbalance"))
async def add_balance_cmd(message: types.Message):
    """Faqat owner: /addbalance USER_ID SUMMA"""
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Foydalanish: /addbalance USER_ID SUMMA")
        return
    try:
        uid, amount = int(parts[1]), float(parts[2])
    except ValueError:
        await message.answer("❌ USER_ID va SUMMA raqam bo'lishi kerak.")
        return
    u = get_user(uid)
    u["balance"] += amount
    u["deposited"] = u.get("deposited", 0) + max(amount, 0)
    save_data(db_data)
    await message.answer(f"✅ {uid} balansi: {fmt_money(u['balance'])} so'm")
    try:
        await bot.send_message(uid, f"💰 Balansingiz {fmt_money(amount)} so'mga to'ldirildi.\nJami: {fmt_money(u['balance'])} so'm")
    except Exception:
        pass


def users_stats_text():
    owner = str(OWNER_ID)
    us = {k: v for k, v in db_data["users"].items() if k != owner}
    total = len(us)
    active = sum(1 for u in us.values() if u.get("deposited", 0) > 0)
    blocked = sum(1 for u in us.values() if u.get("blocked"))
    return (
        "👥 Foydalanuvchilar\n\n"
        f"📊 Jami: {total} ta\n"
        f"💰 Faol (to'lov qilgan): {active} ta\n"
        f"🚫 Bloklangan: {blocked} ta"
    )


def users_home_markup():
    return kb([
        [InlineKeyboardButton(text="🔍 Qidiruv", callback_data="usr_search", style="primary")],
        [InlineKeyboardButton(text="📋 Oxirgi 20 ta", callback_data="usr_last", style="primary")],
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_users"))
async def users_menu_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(users_stats_text(), reply_markup=users_home_markup())


def buttons_home_text():
    return (
        "🔘 <b>Bot tugmalari</b> — botingizning yuzi shu yerda yasaladi!\n\n"
        "<blockquote>✨ Tugma nomini o'zgartiring, rangini tanlang — mijozlaringiz birinchi ko'rishdayoq "
        "\"zo'r ekan\" desin.\n\n"
        "💎 Premium emoji yuborsangiz — inline tugmada aynan o'sha chiqadi.</blockquote>"
    )


def _label(key):
    return " ".join(btn_text(key).split())


def buttons_home_markup():
    rows = grid([
        InlineKeyboardButton(text=f"✏️ {_label(k)}", callback_data=f"bt_{k}", **sk(btn_style(k)))
        for k in MAIN_KEYS
    ])
    rows.append([InlineKeyboardButton(text="•---•---•---•---•---•---•---•---•---•---•", callback_data="noop", **sk("primary"))])
    rows += grid([
        InlineKeyboardButton(text=name, callback_data=f"cs_n_{i}", **sk(net_style(name)))
        for i, name in enumerate(nets())
    ])
    rows.append([InlineKeyboardButton(text=f"✏️ {_label('search')}", callback_data="bt_search", **sk(btn_style("search")))])
    rows.append([InlineKeyboardButton(text="🎲 Ichki tugmalar", callback_data="bt_inner")])
    return kb(rows)


def inner_text():
    return "🎲 Ichki tugmalar\n\nBotdagi ichki (inline) tugmalar nomi va rangini shu yerdan o'zgartirasiz."


def inner_markup():
    rows = grid([
        InlineKeyboardButton(text=f"✏️ {_label(k)}", callback_data=f"bt_{k}", **sk(btn_style(k)))
        for k in INNER_KEYS
    ])
    rows.append(back_btn("bt_home"))
    return kb(rows)


def button_edit_view(key):
    cur = btn_style(key)
    eid = btn_cfg(key).get("emoji_id") or "—"
    text = (
        "🔘 <b>Tugma</b> ✏️\n\n"
        f"<b>Nomi:</b> {escape(btn_text(key))}\n"
        f"<b>Premium emoji id:</b> <code>{eid}</code>\n"
        f"<b>Rangi:</b> {COLOR_LABELS[cur]}\n\n"
        "🎨 Rangni quyidan tanlang:"
    )

    def color_btn(ck):
        st = COLOR_CB[ck]
        label = COLOR_LABELS[st]
        if st == cur:
            label = "✅ " + label
        return InlineKeyboardButton(text=label, callback_data=f"bt_col_{key}_{ck}", **sk(st))

    rows = [
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data=f"bt_ren_{key}")],
        [color_btn("none"), color_btn("primary")],
        [color_btn("success"), color_btn("danger")],
    ]
    if key in INFO_KEYS:
        rows.append([InlineKeyboardButton(text="📝 Matnni o'zgartirish", callback_data=f"bt_txt_{key}")])
    rows.append([InlineKeyboardButton(text="↩️ Asliga qaytarish", callback_data=f"bt_rst_{key}")])
    rows.append(back_btn("bt_inner" if key in INNER_KEYS else "bt_home"))
    return text, kb(rows)


def set_button_name(key, text, emoji_id, label):
    cfg = db_data["ui"]["buttons"].setdefault(key, {})
    aliases = set(cfg.get("aliases", []))
    old = btn_text(key)
    if old != text:
        aliases.add(old)
    if cfg.get("label"):
        aliases.add(cfg["label"])
    if label:
        aliases.add(label)
    aliases.discard(text)
    cfg["aliases"] = sorted(aliases)
    cfg["text"] = text
    cfg["emoji_id"] = emoji_id
    cfg["label"] = label


def reset_button(key):
    cfg = btn_cfg(key)
    aliases = set(cfg.get("aliases", []))
    if cfg.get("text"):
        aliases.add(cfg["text"])
    aliases.discard(BTN_DEFAULTS[key])
    db_data["ui"]["buttons"][key] = {"aliases": sorted(aliases)}


# --- xizmatlar (tarmoq / bo'lim / xizmat) tugmalari rangi
def style_target(kind, nums):
    ui = db_data["ui"]
    if kind == "n":
        net = get_net(nums[0])
        if net is None:
            return None
        return {"title": f"🌐 {net}", "store": ui["net_styles"], "key": net, "default": "success",
                "next": ("📂 Bo'limlar ranglari", f"cl_c_{nums[0]}"), "back": "bt_home"}
    net = get_net(nums[0])
    cat = get_cat(net, nums[1]) if net and len(nums) > 1 else None
    if cat is None:
        return None
    if kind == "c":
        return {"title": f"📂 {net} › {cat}", "store": ui["cat_styles"], "key": f"{net}|{cat}", "default": "primary",
                "next": ("📦 Xizmatlar ranglari", f"cl_s_{nums[0]}_{nums[1]}"), "back": f"cl_c_{nums[0]}"}
    if kind == "s":
        items = get_items(net, cat)
        if len(nums) < 3 or nums[2] >= len(items):
            return None
        it = items[nums[2]]
        return {"title": f"📦 {it['name']}", "store": it, "key": "style", "default": None,
                "next": None, "back": f"cl_s_{nums[0]}_{nums[1]}"}
    return None


def style_view(kind, nums):
    t = style_target(kind, nums)
    if not t:
        return None
    cur = resolve_style(t["store"].get(t["key"]), t["default"])
    base = f"{kind}_" + "_".join(str(n) for n in nums)
    text = (
        "🎨 <b>Tugma rangi</b>\n\n"
        f"<b>Tugma:</b> {escape(t['title'])}\n"
        f"<b>Rangi:</b> {COLOR_LABELS[cur]}\n\n"
        "🎨 Rangni quyidan tanlang:"
    )

    def color_btn(ck):
        st = COLOR_CB[ck]
        label = COLOR_LABELS[st]
        if st == cur:
            label = "✅ " + label
        return InlineKeyboardButton(text=label, callback_data=f"cc_{base}_{ck}", **sk(st))

    rows = [
        [color_btn("none"), color_btn("primary")],
        [color_btn("success"), color_btn("danger")],
        [InlineKeyboardButton(text="↩️ Asliga qaytarish", callback_data=f"cc_{base}_def")],
    ]
    if t["next"]:
        rows.append([InlineKeyboardButton(text=t["next"][0], callback_data=t["next"][1])])
    rows.append(back_btn(t["back"]))
    return text, kb(rows)


@dp.callback_query(F.data == "noop")
async def noop_callback(call: types.CallbackQuery):
    await call.answer()


@dp.callback_query(F.data == "abt_delete_msg")
async def admin_buttons_delete_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_bot_btns"))
async def bot_buttons_menu_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(buttons_home_text(), reply_markup=buttons_home_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "bt_home")
async def bot_buttons_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await safe_edit(call.message, buttons_home_text(), buttons_home_markup(), parse_mode="HTML")
        await call.answer()


@dp.callback_query(F.data == "bt_inner")
async def bot_buttons_inner_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await safe_edit(call.message, inner_text(), inner_markup())
        await call.answer()


@dp.callback_query(F.data.in_({f"bt_{k}" for k in BTN_KEYS}))
async def bot_button_view_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        key = call.data.split("_")[1]
        text, markup = button_edit_view(key)
        await safe_edit(call.message, text, markup, parse_mode="HTML")
        await call.answer()


@dp.callback_query(F.data.startswith("bt_col_"))
async def bot_button_color_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    _, _, key, ck = call.data.split("_")
    if key not in BTN_KEYS or ck not in COLOR_CB:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    db_data["ui"]["buttons"].setdefault(key, {})["style"] = COLOR_CB[ck]
    save_data(db_data)
    text, markup = button_edit_view(key)
    await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer("🎨 Rang o'zgartirildi")


@dp.callback_query(F.data.startswith("bt_ren_"))
async def bot_button_rename_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    key = call.data.split("_")[2]
    if key not in BTN_KEYS:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_btn_name)
    await state.update_data(key=key)
    await safe_edit(
        call.message,
        f"✏️ [{btn_text(key)}] tugmasi uchun yangi nom yuboring:\n\nPremium emoji bilan yuborsangiz, uning ID si saqlanadi.",
        kb([back_btn(f"bt_{key}")]),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("bt_txt_"))
async def info_text_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    key = call.data.split("_")[2]
    if key not in INFO_KEYS:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_info_text)
    await state.update_data(key=key)
    await safe_edit(
        call.message,
        f"📝 [{btn_text(key)}] bo'limi uchun yangi matnni yuboring:\n\n"
        f"Hozirgi matn:\n{info_text(key, OWNER_ID)}\n\n"
        "Matnda {id} yozsangiz, o'rniga foydalanuvchining ID si chiqadi.",
        kb([back_btn(f"bt_{key}")]),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("bt_rst_"))
async def bot_button_reset_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    key = call.data.split("_")[2]
    if key not in BTN_KEYS:
        await call.answer("Noma'lum tugma", show_alert=True)
        return
    reset_button(key)
    save_data(db_data)
    text, markup = button_edit_view(key)
    await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer("↩️ Asliga qaytarildi")


# --- tarmoq / bo'lim / xizmat ranglari
@dp.callback_query(F.data.startswith("cs_"))
async def style_view_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    parts = call.data.split("_")
    v = style_view(parts[1], [int(x) for x in parts[2:]])
    if not v:
        await call.answer("Topilmadi", show_alert=True)
        return
    await safe_edit(call.message, v[0], v[1], parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data.startswith("cc_"))
async def style_set_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    kind, ck = parts[1], parts[-1]
    nums = [int(x) for x in parts[2:-1]]
    t = style_target(kind, nums)
    if not t or (ck != "def" and ck not in COLOR_CB):
        await call.answer("Topilmadi", show_alert=True)
        return
    if ck == "def":
        t["store"].pop(t["key"], None)
    else:
        t["store"][t["key"]] = ck
    save_data(db_data)
    v = style_view(kind, nums)
    await safe_edit(call.message, v[0], v[1], parse_mode="HTML")
    await call.answer("🎨 Rang o'zgartirildi")


@dp.callback_query(F.data.startswith("cl_c_"))
async def style_cat_list_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    ni = int(call.data.split("_")[2])
    net = get_net(ni)
    cats = db_data["services"].get(net, []) if net else []
    if not cats:
        await call.answer("⚠️ Bo'limlar mavjud emas!", show_alert=True)
        return
    rows = grid([
        InlineKeyboardButton(text=c, callback_data=f"cs_c_{ni}_{ci}", **sk(cat_style(net, c)))
        for ci, c in enumerate(cats)
    ])
    rows.append(back_btn(f"cs_n_{ni}"))
    await safe_edit(call.message, f"📂 {net} bo'limlari — rangini o'zgartirish uchun tanlang:", kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("cl_s_"))
async def style_srv_list_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if not items:
        await call.answer("⚠️ Xizmatlar mavjud emas!", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=it["name"], callback_data=f"cs_s_{ni}_{ci}_{si}", **sk(srv_style(it)))]
        for si, it in enumerate(items)
    ]
    rows.append(back_btn(f"cs_c_{ni}_{ci}"))
    await safe_edit(call.message, f"📦 {cat} xizmatlari — rangini o'zgartirish uchun tanlang:", kb(rows))
    await call.answer()


@dp.message(not_renaming, F.text == "Boshqaruv 🗄️")
async def admin_panel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.reply("Admin Panel", reply_markup=build_admin_kb())


# ---------------------------------------------------------------- ADMIN: REFERAL / KUNLIK BONUS SOZLAMALARI
def referral_settings_text():
    s = db_data["settings"]
    return (
        "💸 Referal va bonus sozlamalari\n\n"
        f"🔗 Referal narxi: {fmt_money(s.get('referral_bonus', 0))} so'm\n"
        f"🎁 Kunlik bonus: {fmt_money(s.get('daily_bonus', 0))} so'm"
    )


def referral_settings_markup():
    return kb([
        [InlineKeyboardButton(text="✏️ Referal narxini o'zgartirish", callback_data="set_ref_amt")],
        [InlineKeyboardButton(text="✏️ Kunlik bonusni o'zgartirish", callback_data="set_daily_amt")],
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_referral"))
async def referral_settings_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(referral_settings_text(), reply_markup=referral_settings_markup())


@dp.callback_query(F.data == "set_ref_amt")
async def set_ref_amt_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_referral_amount)
    await safe_edit(
        call.message,
        "🔗 Har bir taklif (referal) uchun beriladigan summani kiriting (so'mda):\n\nMasalan: 1000",
        None,
    )
    await call.answer()


@dp.callback_query(F.data == "set_daily_amt")
async def set_daily_amt_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_daily_bonus_amount)
    await safe_edit(
        call.message,
        "🎁 Kunlik bonus summasini kiriting (so'mda):\n\nMasalan: 500",
        None,
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_referral_amount))
async def process_referral_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("❌ Faqat son kiriting. Masalan: 1000")
        return
    if amount < 0:
        await message.answer("❌ Summa manfiy bo'lmasin:")
        return
    db_data["settings"]["referral_bonus"] = amount
    save_data(db_data)
    await state.clear()
    await message.answer("✅ Referal narxi yangilandi!\n\n" + referral_settings_text(), reply_markup=referral_settings_markup())


@dp.message(StateFilter(AdminState.waiting_for_daily_bonus_amount))
async def process_daily_bonus_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("❌ Faqat son kiriting. Masalan: 500")
        return
    if amount < 0:
        await message.answer("❌ Summa manfiy bo'lmasin:")
        return
    db_data["settings"]["daily_bonus"] = amount
    save_data(db_data)
    await state.clear()
    await message.answer("✅ Kunlik bonus yangilandi!\n\n" + referral_settings_text(), reply_markup=referral_settings_markup())


# ---------------------------------------------------------------- MAJBURIY A'ZOLIK (KANALLAR)
_sub_ok = {}          # uid -> oxirgi muvaffaqiyatli tekshiruv vaqti (keshi)
_pending_start = {}   # uid -> a'zo bo'lmasdan oldin yuborgan /start xabari (referal yo'qolmasligi uchun)
SUB_CACHE_TTL = 120   # sekund: a'zo bo'lgan foydalanuvchi qayta-qayta tekshirilmaydi


def force_channels():
    """Majburiy a'zolik yoqilgan bo'lsa kanallar ro'yxati, aks holda bo'sh ro'yxat."""
    st = db_data["settings"]
    if not st.get("force_sub_on", True):
        return []
    return st.get("force_channels", [])


async def is_member(uid, ch):
    try:
        m = await bot.get_chat_member(ch["id"], uid)
    except Exception as e:
        # bot kanaldan chiqarilgan yoki boshqa xato: foydalanuvchilarni bloklab qo'ymaymiz
        print("Majburiy a'zolikni tekshirishda xato:", ch.get("title"), e)
        return True
    status = getattr(m, "status", "")
    status = getattr(status, "value", status)
    if status in ("member", "administrator", "creator"):
        return True
    if status == "restricted":
        return bool(getattr(m, "is_member", False))
    return False


async def missing_channels(uid, channels):
    """Foydalanuvchi a'zo bo'lmagan kanallar (hammasiga a'zo bo'lsa — bo'sh ro'yxat)."""
    t = _sub_ok.get(uid)
    if t and time.time() - t < SUB_CACHE_TTL:
        return []
    results = await asyncio.gather(*[is_member(uid, ch) for ch in channels], return_exceptions=True)
    missing = [ch for ch, ok in zip(channels, results) if ok is not True]
    if not missing:
        _sub_ok[uid] = time.time()
    return missing


FORCE_TEXT = (
    "📢 Botdan foydalanish uchun majburiy a'zo kanallarga a'zo bo'lishingiz kerak!\n\n"
    "Quyidagi kanallarga a'zo bo'ling va «✅ A'zo bo'ldim» tugmasini bosing 👇"
)


def force_markup(missing):
    rows = [[InlineKeyboardButton(text=f"📢 {ch.get('title') or 'Kanal'}", url=ch["url"])] for ch in missing]
    rows.append([InlineKeyboardButton(text="✅ A'zo bo'ldim", callback_data="fs_check", **sk("success"))])
    return kb(rows)


async def force_sub_gate(event, user):
    """True qaytarsa — foydalanuvchi a'zo emas va handler ishga tushmasligi kerak."""
    if user.id == OWNER_ID:
        return False
    if isinstance(event, types.CallbackQuery) and event.data == "fs_check":
        return False
    channels = force_channels()
    if not channels:
        return False
    missing = await missing_channels(user.id, channels)
    if not missing:
        return False  # avvaldan a'zo bo'lgan — hech narsa chiqmaydi
    if isinstance(event, types.Message) and (event.text or "").startswith("/start"):
        _pending_start[user.id] = event
    try:
        await bot.send_message(user.id, FORCE_TEXT, reply_markup=force_markup(missing))
    except Exception as e:
        print("Majburiy a'zolik xabarini yuborishda xato:", e)
    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
    return True


@dp.callback_query(F.data == "fs_check")
async def fs_check_cb(call: types.CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    _sub_ok.pop(uid, None)
    channels = force_channels() if uid != OWNER_ID else []
    missing = await missing_channels(uid, channels) if channels else []
    if missing:
        await call.answer("❌ Siz hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
        await safe_edit(call.message, FORCE_TEXT, force_markup(missing))
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    pending = _pending_start.pop(uid, None)
    if pending:
        try:
            await process_referral(pending)
        except Exception as e:
            print("Referalni qayta ishlashda xato:", e)
    await bot.send_message(uid, "✅ Rahmat! Endi botdan foydalanishingiz mumkin.\n\n🖥️ Asosiy menyudasiz", reply_markup=main_kb(uid))
    await call.answer()


# --- Admin: majburiy a'zolik sozlamalari
def force_admin_text():
    st = db_data["settings"]
    chs = st.get("force_channels", [])
    on = st.get("force_sub_on", True)
    lines = [
        "📢 Majburiy a'zolik",
        "",
        "Holati: " + ("✅ Yoqilgan" if on else "⛔ O'chirilgan"),
        f"Kanallar: {len(chs)} ta",
    ]
    for i, ch in enumerate(chs, 1):
        uname = f" (@{ch['username']})" if ch.get("username") else " (yopiq kanal)"
        lines.append(f"{i}. {ch.get('title') or ch['id']}{uname}")
    if not chs:
        lines += ["", "Hozircha kanal qo'shilmagan."]
    lines += ["", "ℹ️ Bot har bir kanalda admin bo'lishi shart.", "🗑 tugmasi kanalni ro'yxatdan o'chiradi."]
    return "\n".join(lines)


def force_admin_markup():
    st = db_data["settings"]
    rows = [[InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="fs_add", **sk("success"))]]
    on = st.get("force_sub_on", True)
    rows.append([InlineKeyboardButton(
        text="⛔ O'chirish" if on else "✅ Yoqish", callback_data="fs_toggle",
        **sk("danger" if on else "success"))])
    for i, ch in enumerate(st.get("force_channels", [])):
        rows.append([InlineKeyboardButton(text=f"🗑 {ch.get('title') or ch['id']}", callback_data=f"fs_del_{i}")])
    return kb(rows)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_forced"))
async def force_admin_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(force_admin_text(), reply_markup=force_admin_markup())


@dp.callback_query(F.data == "fs_home")
async def force_admin_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    await safe_edit(call.message, force_admin_text(), force_admin_markup())
    await call.answer()


@dp.callback_query(F.data == "fs_toggle")
async def force_toggle_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    st = db_data["settings"]
    st["force_sub_on"] = not st.get("force_sub_on", True)
    _sub_ok.clear()
    save_data(db_data)
    await safe_edit(call.message, force_admin_text(), force_admin_markup())
    await call.answer("✅ Yoqildi" if st["force_sub_on"] else "⛔ O'chirildi")


@dp.callback_query(F.data.startswith("fs_del_"))
async def force_delete_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.rsplit("_", 1)[1])
    chs = db_data["settings"].get("force_channels", [])
    if 0 <= idx < len(chs):
        chs.pop(idx)
        _sub_ok.clear()
        save_data(db_data)
    await safe_edit(call.message, force_admin_text(), force_admin_markup())
    await call.answer("🗑 O'chirildi")


@dp.callback_query(F.data == "fs_add")
async def force_add_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_force_channel)
    await safe_edit(
        call.message,
        "📢 Kanalni yuboring:\n\n"
        "• @username (masalan @kanalim)\n"
        "• yoki kanal ID si (masalan -1001234567890)\n"
        "• yoki kanaldan istalgan postni forward qiling\n\n"
        "⚠️ Avval botni shu kanalga ADMIN qilib qo'shing!",
        kb([back_btn("fs_home")]),
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_force_channel))
async def force_add_process(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    back = kb([back_btn("fs_home")])
    ref = None
    fo = getattr(message, "forward_origin", None)
    fchat = getattr(fo, "chat", None) or getattr(message, "forward_from_chat", None)
    if fchat is not None:
        ref = fchat.id
    else:
        raw = (message.text or "").strip()
        if re.fullmatch(r"-?\d+", raw):
            ref = int(raw)
        else:
            m = re.search(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", raw) or re.fullmatch(r"([A-Za-z][A-Za-z0-9_]{3,})", raw)
            if m:
                ref = "@" + m.group(1)
    if ref is None:
        await message.answer("❌ Kanal aniqlanmadi. @username, ID yoki forward xabar yuboring:", reply_markup=back)
        return
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        await message.answer("❌ Kanal topilmadi. Username/ID to'g'riligini va bot kanalda borligini tekshiring:", reply_markup=back)
        return
    if str(getattr(chat, "type", "")) not in ("channel", "supergroup", "ChatType.CHANNEL", "ChatType.SUPERGROUP"):
        await message.answer("❌ Bu kanal yoki guruh emas. Boshqasini yuboring:", reply_markup=back)
        return
    try:
        me = await bot.get_me()
        bm = await bot.get_chat_member(chat.id, me.id)
        bstatus = getattr(getattr(bm, "status", ""), "value", getattr(bm, "status", ""))
    except Exception:
        bstatus = ""
    if bstatus not in ("administrator", "creator"):
        await message.answer("❌ Bot bu kanalda admin emas! Avval botni kanalga admin qiling, keyin qayta yuboring:", reply_markup=back)
        return
    chs = db_data["settings"].setdefault("force_channels", [])
    if any(c["id"] == chat.id for c in chs):
        await message.answer("⚠️ Bu kanal allaqachon qo'shilgan. Boshqasini yuboring:", reply_markup=back)
        return
    if chat.username:
        url = f"https://t.me/{chat.username}"
    else:
        try:
            link = await bot.create_chat_invite_link(chat.id)
            url = link.invite_link
        except Exception:
            await message.answer(
                "❌ Bu yopiq kanal uchun havola yaratib bo'lmadi. Botga «Havola orqali taklif qilish» huquqini bering va qayta yuboring:",
                reply_markup=back,
            )
            return
    chs.append({"id": chat.id, "title": chat.title or str(chat.id), "url": url, "username": chat.username})
    _sub_ok.clear()
    save_data(db_data)
    await state.clear()
    await message.answer("✅ Kanal qo'shildi!\n\n" + force_admin_text(), reply_markup=force_admin_markup())


# ---------------------------------------------------------------- BUYURTMA KANALI
def order_channel():
    return db_data["settings"].get("order_channel")


def order_channel_text():
    ch = order_channel()
    if ch:
        uname = f" (@{ch['username']})" if ch.get("username") else " (yopiq kanal)"
        return (
            "📡 Buyurtma kanali\n\n"
            f"Biriktirilgan: {ch.get('title') or ch['id']}{uname}\n\n"
            "Yangi buyurtma tushganda shu kanalga xabar yuboriladi."
        )
    return (
        "📡 Buyurtma kanali\n\n"
        "Hozircha kanal biriktirilmagan.\n\n"
        "Yangi buyurtma tushganda xabar yuborish uchun kanal biriktiring."
    )


def order_channel_markup():
    ch = order_channel()
    rows = []
    if ch:
        rows.append([InlineKeyboardButton(text="✏️ Almashtirish", callback_data="ordch_add", **sk("primary"))])
        rows.append([InlineKeyboardButton(text="🗑 Olib tashlash", callback_data="ordch_del", **sk("danger"))])
    else:
        rows.append([InlineKeyboardButton(text="➕ Kanal biriktirish", callback_data="ordch_add", **sk("success"))])
    return kb(rows)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_channel"))
async def order_channel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await message.answer(order_channel_text(), reply_markup=order_channel_markup())


@dp.callback_query(F.data == "ordch_home")
async def ordch_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    await safe_edit(call.message, order_channel_text(), order_channel_markup())
    await call.answer()


@dp.callback_query(F.data == "ordch_del")
async def ordch_del_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    db_data["settings"]["order_channel"] = None
    save_data(db_data)
    await safe_edit(call.message, order_channel_text(), order_channel_markup())
    await call.answer("🗑 Olib tashlandi")


@dp.callback_query(F.data == "ordch_add")
async def ordch_add_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_order_channel)
    await safe_edit(
        call.message,
        "📡 Kanalni yuboring:\n\n"
        "• @username (masalan @kanalim)\n"
        "• yoki kanal ID si (masalan -1001234567890)\n"
        "• yoki kanaldan istalgan postni forward qiling\n\n"
        "⚠️ Avval botni shu kanalga ADMIN qilib qo'shing!",
        kb([back_btn("ordch_home")]),
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_order_channel))
async def ordch_process(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    back = kb([back_btn("ordch_home")])
    ref = None
    fo = getattr(message, "forward_origin", None)
    fchat = getattr(fo, "chat", None) or getattr(message, "forward_from_chat", None)
    if fchat is not None:
        ref = fchat.id
    else:
        raw = (message.text or "").strip()
        if re.fullmatch(r"-?\d+", raw):
            ref = int(raw)
        else:
            m = re.search(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", raw) or re.fullmatch(r"([A-Za-z][A-Za-z0-9_]{3,})", raw)
            if m:
                ref = "@" + m.group(1)
    if ref is None:
        await message.answer("❌ Kanal aniqlanmadi. @username, ID yoki forward xabar yuboring:", reply_markup=back)
        return
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        await message.answer("❌ Kanal topilmadi. Username/ID to'g'riligini va bot kanalda borligini tekshiring:", reply_markup=back)
        return
    if str(getattr(chat, "type", "")) not in ("channel", "supergroup", "ChatType.CHANNEL", "ChatType.SUPERGROUP"):
        await message.answer("❌ Bu kanal yoki guruh emas. Boshqasini yuboring:", reply_markup=back)
        return
    try:
        me = await bot.get_me()
        bm = await bot.get_chat_member(chat.id, me.id)
        bstatus = getattr(getattr(bm, "status", ""), "value", getattr(bm, "status", ""))
    except Exception:
        bstatus = ""
    if bstatus not in ("administrator", "creator"):
        await message.answer("❌ Bot bu kanalda admin emas! Avval botni kanalga admin qiling, keyin qayta yuboring:", reply_markup=back)
        return
    db_data["settings"]["order_channel"] = {"id": chat.id, "title": chat.title or str(chat.id), "username": chat.username}
    save_data(db_data)
    await state.clear()
    await message.answer("✅ Kanal biriktirildi!\n\n" + order_channel_text(), reply_markup=order_channel_markup())


async def send_order_channel_post(order_id, it, qty, price, user, link):
    ch = order_channel()
    if not ch:
        return
    bot_username = await get_bot_username()
    u = get_user(user.id)
    text = (
        f"📦 Buyurtmalar @{bot_username}\n\n"
        f"🆕 Yangi buyurtma (@{bot_username})\n"
        f"☎️ ADMIN {OWNER_ID}\n\n"
        f"🛍 Buyurtma ID raqami: {order_id}\n"
        f"🆔 Xizmat id raqami: {it.get('sid')}\n"
        f"🔢 Buyurtma miqdori: {qty} ta\n"
        f"📊 Buyurtma narxi: {fmt_money(price)} so'm\n"
        f"📖 Xizmat nomi: {it['name']}\n"
        f"👤 Buyurtmachi: {user.first_name or user.id}\n"
        f"🆔 IDisi: {user.id}\n"
        f"🔗 Havola: {link}\n"
        f"💸 Buyurtmachini balansi: {fmt_money(u['balance'])} so'm"
    )
    rows = [[
        InlineKeyboardButton(text="🔍 Buyurtma xolati", callback_data=f"ordch_st_{order_id}"),
        InlineKeyboardButton(text="👤 Buyurtmachini ko'rish", callback_data=f"ordch_us_{user.id}"),
    ]]
    try:
        await bot.send_message(ch["id"], text, reply_markup=kb(rows))
    except Exception as e:
        print("Buyurtma kanaliga yuborishda xato:", e)


@dp.callback_query(F.data.startswith("ordch_st_"))
async def ordch_status_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔ Faqat admin uchun", show_alert=True)
        return
    order_id = int(call.data.rsplit("_", 1)[1])
    order = next((o for o in db_data["orders"] if o.get("id") == order_id), None)
    if not order:
        await call.answer("⚠️ Buyurtma topilmadi", show_alert=True)
        return
    try:
        await refresh_order(order)
    except Exception:
        pass
    await call.answer(f"📌 Holat: {order.get('status', 'Pending')}", show_alert=True)


@dp.callback_query(F.data.startswith("ordch_us_"))
async def ordch_user_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("⛔ Faqat admin uchun", show_alert=True)
        return
    uid = int(call.data.rsplit("_", 1)[1])
    u = get_user(uid)
    cnt = sum(1 for o in db_data["orders"] if o.get("user_id") == uid)
    await call.answer(
        f"🆔 {uid}\n👛 Balans: {fmt_money(u['balance'])} so'm\n🛒 Buyurtmalar: {cnt} ta",
        show_alert=True,
    )


# ---------------------------------------------------------------- ADMIN: TO'LOV TIZIMLARI (TELEGRAM STARS VA KARTALAR)
def get_stars_admin_text():
    stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
    st_text = "✅ Faol (Yoqilgan)" if stars_cfg.get("active", True) else "🚫 Nofaol (O'chirilgan)"
    rate = stars_cfg.get("rate", 200)
    return (
        "⭐ <b>TELEGRAM STARS SOZLAMALARI:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔘 <b>Holat:</b> {st_text}\n"
        f"💱 <b>Joriy kurs:</b> 1 ta Stars = <b>{rate:,} so'm</b>\n\n"
        "⚡️ <b>Qanday ishlaydi:</b>\n"
        "• Foydalanuvchi Telegram Stars orqali to'lov qilganda avtomatik uning hisobiga o'tkaziladi;\n"
        "• Masalan: 1 Stars = 200 so'm, 10 Stars = 2,000 so'm;\n\n"
        "<i>Kursni tezkor o'zgartirish uchun quyidagi tugmalardan birini bosing yoki qo'lda kiriting:</i>"
    )


def get_stars_admin_keyboard():
    stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
    is_act = stars_cfg.get("active", True)
    toggle_text = "🚫 O'chirish" if is_act else "✅ Yoqish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 100 so'm", callback_data="stars_setrate_100"),
            InlineKeyboardButton(text="💵 150 so'm", callback_data="stars_setrate_150"),
            InlineKeyboardButton(text="💵 200 so'm", callback_data="stars_setrate_200"),
        ],
        [
            InlineKeyboardButton(text="💵 250 so'm", callback_data="stars_setrate_250"),
            InlineKeyboardButton(text="💵 300 so'm", callback_data="stars_setrate_300"),
            InlineKeyboardButton(text="💵 500 so'm", callback_data="stars_setrate_500"),
        ],
        [
            InlineKeyboardButton(text="✏️ Boshqa kurs kiritish", callback_data="stars_set_rate", style="primary"),
            InlineKeyboardButton(text=f"🔄 {toggle_text}", callback_data="stars_toggle", style="danger" if is_act else "success"),
        ],
        [
            InlineKeyboardButton(text="🔙 Boshqaruv menyusiga qaytish", callback_data="back_to_pay_types")
        ]
    ])


def payment_types_menu_markup():
    stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
    st_text = "✅ Faol" if stars_cfg.get("active", True) else "🚫 Nofaol"
    rate = stars_cfg.get("rate", 200)
    card_count = len([p for p in db_data.get("payment_systems", []) if p.get("active", True)])
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Telegram Stars ({st_text}, 1 ⭐ = {rate:,} so'm)", callback_data="pay_stars_cfg")],
        [InlineKeyboardButton(text=f"💳 Karta / Boshqa tizimlar ({card_count} ta faol)", callback_data="pay_list")],
        [InlineKeyboardButton(text="➕ Yangi to'lov tizimi qo'shish", callback_data="pay_add", style="primary")],
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_payment"))
async def payment_types_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
        st_text = "✅ Faol" if stars_cfg.get("active", True) else "🚫 Nofaol"
        rate = stars_cfg.get("rate", 200)
        card_count = len([p for p in db_data.get("payment_systems", []) if p.get("active", True)])
        
        text = (
            "💳 <b>TO'LOV TIZIMLARI BOSHQARUVI:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⭐ <b>Telegram Stars:</b> {st_text} (1 ta Stars = <b>{rate:,} so'm</b>)\n"
            f"💳 <b>Karta / Boshqa to'lovlar:</b> {card_count} ta faol tizim\n\n"
            "Qaysi to'lov tizimini sozlamoqchisiz yoki yangisini ulamoqchisiz?"
        )
        await message.answer(text, reply_markup=payment_types_menu_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("stars_setrate_"))
async def stars_quick_rate_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        new_rate = int(call.data.replace("stars_setrate_", ""))
        stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
        stars_cfg["rate"] = new_rate
        save_data(db_data)
        await safe_edit(call.message, get_stars_admin_text(), get_stars_admin_keyboard(), parse_mode="HTML")
        await call.answer(f"✅ Kurs 1 ⭐ = {new_rate:,} so'm qilib belgilandi!")


@dp.callback_query(F.data == "pay_stars_cfg")
async def pay_stars_cfg_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        await safe_edit(call.message, get_stars_admin_text(), get_stars_admin_keyboard(), parse_mode="HTML")
        await call.answer()


@dp.callback_query(F.data == "stars_toggle")
async def stars_toggle_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
        stars_cfg["active"] = not stars_cfg.get("active", True)
        save_data(db_data)
        await safe_edit(call.message, get_stars_admin_text(), get_stars_admin_keyboard(), parse_mode="HTML")
        status_msg = "✅ Stars to'lovi yoqildi!" if stars_cfg["active"] else "🚫 Stars to'lovi o'chirildi!"
        await call.answer(status_msg)


@dp.callback_query(F.data == "stars_set_rate")
async def stars_set_rate_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        await state.set_state(StarsAdminState.waiting_for_rate)
        rate = get_stars_rate()
        text = (
            "✏️ <b>Telegram Stars kursini belgilash</b>\n\n"
            f"Hozirgi kurs: 1 ta Stars = <b>{rate:,} so'm</b>\n\n"
            "Yangi kursni kiriting (Masalan: <code>200</code>):"
        )
        back_kb = kb([[InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="pay_stars_cfg")]])
        await safe_edit(call.message, text, back_kb, parse_mode="HTML")
        await call.answer()


@dp.message(StarsAdminState.waiting_for_rate)
async def stars_rate_input_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("⚠️ Iltimos, musbat son kiriting (Masalan: <code>200</code>):", parse_mode="HTML")
        return
    new_rate = int(text)
    stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
    stars_cfg["rate"] = new_rate
    save_data(db_data)
    await state.clear()
    
    await message.answer(
        f"✅ <b>Telegram Stars kursi muvaffaqiyatli saqlandi!</b>\n\n"
        f"Yangi kurs: <b>1 ta Stars = {new_rate:,} so'm</b>",
        reply_markup=get_stars_admin_keyboard(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back_to_pay_types")
async def back_to_pay_types_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        stars_cfg = db_data.setdefault("settings", {}).setdefault("stars", {"active": True, "rate": 200})
        st_text = "✅ Faol" if stars_cfg.get("active", True) else "🚫 Nofaol"
        rate = stars_cfg.get("rate", 200)
        card_count = len([p for p in db_data.get("payment_systems", []) if p.get("active", True)])
        
        text = (
            "💳 <b>TO'LOV TIZIMLARI BOSHQARUVI:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⭐ <b>Telegram Stars:</b> {st_text} (1 ta Stars = <b>{rate:,} so'm</b>)\n"
            f"💳 <b>Karta / Boshqa to'lovlar:</b> {card_count} ta faol tizim\n\n"
            "Qaysi to'lov tizimini sozlamoqchisiz yoki yangisini ulamoqchisiz?"
        )
        await safe_edit(call.message, text, payment_types_menu_markup(), parse_mode="HTML")
        await call.answer()


async def show_payment_list(message_or_call):
    items = db_data["payment_systems"]
    if items:
        lines = ["💳 To'lov tizimlari:\n"]
        for i, p in enumerate(items):
            status = "✅" if p.get("active", True) else "🚫"
            lines.append(f"{i+1}. {status} {p['name']}")
        text = "\n".join(lines)
    else:
        text = "⚠️ To'lov tizimlari mavjud emas."

    rows = [[InlineKeyboardButton(text=f"{i+1}. {p['name']}", callback_data=f"pay_view_{i}", style="success")]
            for i, p in enumerate(items)]
    rows.append([InlineKeyboardButton(text="➕ To'lov tizimi qo'shish", callback_data="pay_add")])
    rows.append([InlineKeyboardButton(text="💵 Minimal to'lov", callback_data="pay_min"),
                 InlineKeyboardButton(text="📋 To'lov tavsifi", callback_data="pay_desc")])
    rows.append(back_btn("back_to_pay_types"))
    markup = kb(rows)

    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=markup)
    else:
        await safe_edit(message_or_call.message, text, markup)


@dp.callback_query(F.data == "pay_list")
async def pay_list_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        await show_payment_list(call)
        await call.answer()





def payment_item_text(p):
    status = "✅ Faol" if p.get("active", True) else "🚫 Nofaol"
    text = f"{p['name']}\n\nHolat: {status}\n\n💳 Karta: {p['card']}\n👤 Egasi: {p['owner']}"
    if p.get("desc"):
        text += f"\n\n📋 Tavsif: {p['desc']}"
    return text


def payment_item_markup(idx, p):
    toggle_text = "🚫 Nofaollashtirish" if p.get("active", True) else "✅ Faollashtirish"
    rows = [
        [InlineKeyboardButton(text="✏️ Nom", callback_data=f"pay_edit_name_{idx}")],
        [InlineKeyboardButton(text="💳 Karta raqami", callback_data=f"pay_edit_card_{idx}"),
         InlineKeyboardButton(text="👤 Karta egasi", callback_data=f"pay_edit_owner_{idx}")],
        [InlineKeyboardButton(text="📋 Tavsif", callback_data=f"pay_edit_desc_{idx}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"pay_toggle_{idx}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"pay_del_{idx}")],
        back_btn("pay_list"),
    ]
    return kb(rows)


@dp.callback_query(F.data.startswith("pay_view_"))
async def pay_view_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_view_", ""))
    items = db_data["payment_systems"]
    if idx >= len(items):
        await call.answer("Topilmadi", show_alert=True)
        return
    p = items[idx]
    await safe_edit(call.message, payment_item_text(p), payment_item_markup(idx, p))
    await call.answer()


@dp.callback_query(F.data == "pay_add")
async def pay_add_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.set_state(AdminState.waiting_for_pay_name)
        await safe_edit(
            call.message,
            "✏️ To'lov tizimi nomini kiriting:\n\nPremium emoji ishlatsangiz ham bo'ladi. Masalan:\n💳 UzCard",
            kb([back_btn("pay_list")]),
        )
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_pay_name))
async def process_pay_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=kb([back_btn("pay_list")]))
        return
    await state.update_data(pay_name=name)
    await state.set_state(AdminState.waiting_for_pay_card)
    await message.answer(
        "💳 Karta raqamini kiriting:\nMisol: 8600 1234 5678 9012",
        reply_markup=kb([back_btn("pay_list")]),
    )


@dp.message(StateFilter(AdminState.waiting_for_pay_card))
async def process_pay_card(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    card = (message.text or "").strip()
    if not card:
        await message.answer("❌ Karta raqami bo'sh bo'lmasin:", reply_markup=kb([back_btn("pay_list")]))
        return
    await state.update_data(pay_card=card)
    await state.set_state(AdminState.waiting_for_pay_owner)
    await message.answer(
        "👤 Karta egasining ism-familiyasini kiriting:\nMisol: Lutfullayev Mansur",
        reply_markup=kb([back_btn("pay_list")]),
    )


@dp.message(StateFilter(AdminState.waiting_for_pay_owner))
async def process_pay_owner(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    owner = (message.text or "").strip()
    if not owner:
        await message.answer("❌ Karta egasi bo'sh bo'lmasin:", reply_markup=kb([back_btn("pay_list")]))
        return
    data = await state.get_data()
    name = data.get("pay_name")
    card = data.get("pay_card")
    db_data["payment_systems"].append({"name": name, "card": card, "owner": owner, "active": True, "desc": ""})
    save_data(db_data)
    await state.clear()
    await message.answer(
        f"✅ To'lov tizimi qo'shildi: {name}",
        reply_markup=kb([[InlineKeyboardButton(text="◀ To'lov tizimlari", callback_data="pay_list", style="primary")]]),
    )


@dp.callback_query(F.data.startswith("pay_toggle_"))
async def pay_toggle_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_toggle_", ""))
    items = db_data["payment_systems"]
    if idx >= len(items):
        await call.answer("Topilmadi", show_alert=True)
        return
    items[idx]["active"] = not items[idx].get("active", True)
    save_data(db_data)
    await safe_edit(call.message, payment_item_text(items[idx]), payment_item_markup(idx, items[idx]))
    await call.answer()


@dp.callback_query(F.data.startswith("pay_del_"))
async def pay_del_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_del_", ""))
    items = db_data["payment_systems"]
    if idx < len(items):
        items.pop(idx)
        save_data(db_data)
        await call.answer("🗑 O'chirildi!", show_alert=True)
    await show_payment_list(call)


@dp.callback_query(F.data.startswith("pay_edit_name_"))
async def pay_edit_name_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_edit_name_", ""))
    await state.set_state(AdminState.waiting_for_edit_pay_name)
    await state.update_data(edit_idx=idx)
    await safe_edit(call.message, "✏️ Yangi nom kiriting:", kb([back_btn(f"pay_view_{idx}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_pay_name))
async def process_edit_pay_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    idx = data.get("edit_idx")
    name = (message.text or "").strip()
    if idx is not None and idx < len(db_data["payment_systems"]) and name:
        db_data["payment_systems"][idx]["name"] = name
        save_data(db_data)
    await state.clear()
    await message.answer("✅ Nom yangilandi!", reply_markup=kb([back_btn(f"pay_view_{idx}")]))


@dp.callback_query(F.data.startswith("pay_edit_card_"))
async def pay_edit_card_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_edit_card_", ""))
    await state.set_state(AdminState.waiting_for_edit_pay_card)
    await state.update_data(edit_idx=idx)
    await safe_edit(call.message, "💳 Yangi karta raqamini kiriting:", kb([back_btn(f"pay_view_{idx}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_pay_card))
async def process_edit_pay_card(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    idx = data.get("edit_idx")
    card = (message.text or "").strip()
    if idx is not None and idx < len(db_data["payment_systems"]) and card:
        db_data["payment_systems"][idx]["card"] = card
        save_data(db_data)
    await state.clear()
    await message.answer("✅ Karta raqami yangilandi!", reply_markup=kb([back_btn(f"pay_view_{idx}")]))


@dp.callback_query(F.data.startswith("pay_edit_owner_"))
async def pay_edit_owner_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_edit_owner_", ""))
    await state.set_state(AdminState.waiting_for_edit_pay_owner)
    await state.update_data(edit_idx=idx)
    await safe_edit(call.message, "👤 Karta egasining yangi ism-familiyasini kiriting:", kb([back_btn(f"pay_view_{idx}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_pay_owner))
async def process_edit_pay_owner(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    idx = data.get("edit_idx")
    owner = (message.text or "").strip()
    if idx is not None and idx < len(db_data["payment_systems"]) and owner:
        db_data["payment_systems"][idx]["owner"] = owner
        save_data(db_data)
    await state.clear()
    await message.answer("✅ Karta egasi yangilandi!", reply_markup=kb([back_btn(f"pay_view_{idx}")]))


@dp.callback_query(F.data.startswith("pay_edit_desc_"))
async def pay_edit_desc_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("pay_edit_desc_", ""))
    await state.set_state(AdminState.waiting_for_edit_pay_desc)
    await state.update_data(edit_idx=idx)
    await safe_edit(call.message, "📋 Ushbu to'lov tizimi uchun tavsif/izoh kiriting:", kb([back_btn(f"pay_view_{idx}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_pay_desc))
async def process_edit_pay_desc(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    idx = data.get("edit_idx")
    desc = (message.text or "").strip()
    if idx is not None and idx < len(db_data["payment_systems"]):
        db_data["payment_systems"][idx]["desc"] = desc
        save_data(db_data)
    await state.clear()
    await message.answer("✅ Tavsif yangilandi!", reply_markup=kb([back_btn(f"pay_view_{idx}")]))


@dp.callback_query(F.data == "pay_min")
async def pay_min_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_min_deposit)
    await safe_edit(call.message, "💵 Yangi minimal to'lov summasini so'mda yuboring:", kb([back_btn("pay_list")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_min_deposit))
async def process_min_deposit(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("❌ Faqat son kiriting.")
        return
    if amount < 0:
        await message.answer("❌ Summa manfiy bo'lmasin:")
        return
    db_data["settings"]["min_deposit"] = amount
    save_data(db_data)
    await state.clear()
    await message.answer(f"✅ Minimal to'lov: {fmt_money(amount)} saqlandi!", reply_markup=kb([back_btn("pay_list")]))


@dp.callback_query(F.data == "pay_desc")
async def pay_desc_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    cur = db_data["settings"].get("deposit_desc") or "(kiritilmagan)"
    await safe_edit(
        call.message,
        f"📋 To'lov tavsifi\n\nHozirgi: {cur}",
        kb([[InlineKeyboardButton(text="✏️ O'zgartirish", callback_data="pay_desc_edit")], back_btn("pay_list")]),
    )
    await call.answer()


@dp.callback_query(F.data == "pay_desc_edit")
async def pay_desc_edit_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_deposit_desc)
    await safe_edit(call.message, "📋 Yangi to'lov tavsifini yuboring:", kb([back_btn("pay_list")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_deposit_desc))
async def process_deposit_desc(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    text = (message.text or "").strip()
    db_data["settings"]["deposit_desc"] = text
    save_data(db_data)
    await state.clear()
    await message.answer("✅ To'lov tavsifi yangilandi!", reply_markup=kb([back_btn("pay_list")]))


@dp.message(not_renaming, lambda m: bool(m.text) and is_btn(m.text, "services"))
async def services_handler(message: types.Message, state: FSMContext):
    await state.clear()
    k = get_user_services_inline()
    if k:
        await message.answer("📁 Quyidagi ijtimoiy tarmoqlardan birini tanlang.", reply_markup=k)
    else:
        await message.answer("⚠️ Ijtimoiy tarmoqlar mavjud emas!")


# ---------------------------------------------------------------- USER: XIZMATLAR
@dp.callback_query(F.data == "u_back_to_services")
async def user_back_to_services(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    k = get_user_services_inline()
    if k:
        await safe_edit(call.message, "📁 Quyidagi ijtimoiy tarmoqlardan birini tanlang.", k)
    else:
        await safe_edit(call.message, "⚠️ Ijtimoiy tarmoqlar mavjud emas!")
    await call.answer()


@dp.callback_query(F.data == "u_search_start")
async def user_search_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ServiceSearchState.waiting_for_query)
    await safe_edit(
        call.message,
        "🔍 Xizmat qidirish\n\n"
        "🆔 ID raqam yoki xizmat nomini yuboring:\n\n"
        "Misol: 15 yoki Instagram followers",
        kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="u_search_cancel", **sk("danger"))]]),
    )
    await call.answer()


@dp.callback_query(F.data == "u_search_cancel")
async def user_search_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    k = get_user_services_inline()
    if k:
        await safe_edit(call.message, "📁 Quyidagi ijtimoiy tarmoqlardan birini tanlang.", k)
    else:
        await safe_edit(call.message, "⚠️ Ijtimoiy tarmoqlar mavjud emas!")
    await call.answer()


def find_services_by_query(query):
    """Barcha tarmoq/bo'lim xizmatlari orasidan ID (bid) yoki nomi bo'yicha qidiradi."""
    q = query.strip()
    q_digit = q.isdigit()
    results = []
    for ni, net in enumerate(nets()):
        cats = db_data["services"].get(net, [])
        for ci, cat in enumerate(cats):
            for si, it in enumerate(get_items(net, cat)):
                if q_digit and str(it.get("bid", "")) == q:
                    results.append((ni, ci, si, net, it))
                elif not q_digit and q.lower() in str(it.get("name", "")).lower():
                    results.append((ni, ci, si, net, it))
    return results


def search_result_text(net, it):
    orders = service_orders(it)
    return (
        f"<b>{escape(str(net))}</b> — {escape(str(it['name']))}\n\n"
        f"🆔 ID: {it.get('bid', '—')}\n"
        f"💰 Narx (x1000): {fmt_money(it['rate'])} so'm\n"
        f"📉 Minimal: {it['min']} ta\n"
        f"📈 Maksimal: {it['max']} ta\n"
        f"📊 Buyurtmalar: {len(orders)} ta"
    )


@dp.message(StateFilter(ServiceSearchState.waiting_for_query))
async def user_search_query(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    query = (message.text or "").strip()
    cancel_kb = kb([[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="u_search_cancel", **sk("danger"))]])
    if not query:
        await message.answer("❌ Bo'sh qidiruv. ID raqam yoki xizmat nomini yuboring:", reply_markup=cancel_kb)
        return
    results = find_services_by_query(query)
    if not results:
        await message.answer(
            "❌ Hech narsa topilmadi.\n\n🆔 ID raqam yoki xizmat nomini qaytadan yuboring:",
            reply_markup=cancel_kb,
        )
        return
    await state.clear()
    if len(results) == 1:
        ni, ci, si, net, it = results[0]
        rows = [
            [ibtn("buy", f"u_buy_{ni}_{ci}_{si}")],
            back_btn(f"u_cat_{ni}_{ci}"),
        ]
        await message.answer(search_result_text(net, it), reply_markup=kb(rows), parse_mode="HTML", **NO_PREVIEW)
    else:
        rows = [
            [InlineKeyboardButton(
                text=f"{net} — {srv_label(it)} ({it.get('bid', '—')})",
                callback_data=f"u_srv_{ni}_{ci}_{si}",
                **srv_icon_kw(it),
                **sk(srv_style(it)))]
            for ni, ci, si, net, it in results[:30]
        ]
        rows.append([InlineKeyboardButton(text="🔍 Qayta qidirish", callback_data="u_search_start", **sk("primary"))])
        rows.append(back_btn("u_back_to_services"))
        await message.answer(f"🔍 Topildi: {len(results)} ta\n\nKerakli xizmatni tanlang:", reply_markup=kb(rows))


@dp.callback_query(F.data.startswith("u_net_"))
async def user_network_click(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    ni = int(call.data.split("_")[2])
    net = get_net(ni)
    if net is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    cats = db_data["services"].get(net, [])
    if cats:
        rows = [[InlineKeyboardButton(
            text=cat_label(net, c),
            callback_data=f"u_cat_{ni}_{ci}",
            **cat_icon_kw(net, c),
            **sk(cat_style(net, c))
        )] for ci, c in enumerate(cats)]
        rows.append(back_btn("u_back_to_services"))
        await safe_edit(call.message, "📁 Quyidagi Bo'limlardan birini tanlang.", kb(rows))
    else:
        await safe_edit(call.message, "⚠️ Bo'limlar mavjud emas!", kb([back_btn("u_back_to_services")]))
    await call.answer()


@dp.callback_query(F.data.startswith("u_cat_"))
async def user_category_click(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    items = get_items(net, cat)
    if not items:
        await safe_edit(call.message, "⚠️ Xizmatlar mavjud emas!", kb([back_btn(f"u_net_{ni}")]))
    else:
        rows = [
            [InlineKeyboardButton(
                text=f"{srv_label(it)} — {fmt_money(it['rate'])} so'm",
                callback_data=f"u_srv_{ni}_{ci}_{si}",
                **srv_icon_kw(it),
                **sk(srv_style(it)))]
            for si, it in enumerate(items)
        ]
        rows.append(back_btn(f"u_net_{ni}"))
        await safe_edit(call.message, f"📂 {cat}\n\nXizmatni tanlang (narx 1000 ta uchun):", kb(rows))
    await call.answer()


def service_text(it):
    return (
        f"📦 Xizmat nomi: {it['name']}\n"
        f"💰 Narx (1000 ta): {price_line(it['rate'])}\n"
        f"📉 Minimal: {it['min']} ta\n"
        f"📈 Maksimal: {it['max']} ta"
    )


def service_orders(it):
    """Shu xizmatga botdan berilgan barcha buyurtmalar."""
    res = []
    for o in db_data["orders"]:
        if o.get("api_url") != it.get("api_url"):
            continue
        if (o.get("sid") is not None and str(o.get("sid")) == str(it.get("sid"))) or o.get("service") == it.get("name"):
            res.append(o)
    return res


def fmt_duration(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, sc = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} soat")
    parts.append(f"{m} daqiqa")
    parts.append(f"{sc} sekund")
    return " ".join(parts)


def fmt_ago(sec):
    sec = max(int(sec), 0)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, sc = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} kun")
    parts += [f"{h} soat", f"{m} daqiqa", f"{sc} sekund"]
    return " ".join(parts) + " oldin"


def service_last_order(orders):
    """Oxirgi buyurtma matni: sana va necha vaqt oldin berilgani."""
    fmt = "%Y-%m-%d %H:%M:%S"
    last = None
    for o in orders:
        try:
            t = datetime.strptime(o["time"], fmt)
        except Exception:
            continue
        if last is None or t > last:
            last = t
    if last is None:
        return "hali buyurtma berilmagan"
    return f"{last.strftime('%d.%m.%Y %H:%M')} ({fmt_ago((datetime.now() - last).total_seconds())})"


def service_avg_time(orders):
    """Bajarilgan buyurtmalarning o'rtacha bajarilish vaqti (sekundda) yoki None."""
    fmt = "%Y-%m-%d %H:%M:%S"
    durs = []
    for o in orders:
        if str(o.get("status") or "").lower().strip() != "completed" or not o.get("done_time"):
            continue
        try:
            d = (datetime.strptime(o["done_time"], fmt) - datetime.strptime(o["time"], fmt)).total_seconds()
        except Exception:
            continue
        if d >= 0:
            durs.append(d)
    return sum(durs) / len(durs) if durs else None


# Xizmat tavsifi oxiriga avtomatik qo'shiladigan yozuv ({bot} o'rniga botingizning nomi qo'yiladi)
SERVICE_CREDIT = (
    "🌟 Ushbu xizmat {bot} botida @offline_boy_uzb tomonidan taqdim etilgan!\n"
    "👑 Loyihamiz Rahbari — @offline_boy_uzb: sifat va ishonch kafili 🔥\n"
    "💎 {bot} — eng zo'r xizmatlar faqat shu yerda!"
)
# Faqat bepul (narxi 0) xizmatlarga qo'shiladigan qator
SERVICE_CREDIT_FREE = "🎁 {bot} uchun maxsus qo'shilgan TEKIN xizmat!"


def service_credit(it, bot_name):
    lines = []
    if float(it.get("rate", 0) or 0) == 0:
        lines.append(SERVICE_CREDIT_FREE.format(bot=bot_name))
    lines.append(SERVICE_CREDIT.format(bot=bot_name))
    return "\n".join(lines)


def user_service_text(it, desc="", bot_name="Bot"):
    orders = service_orders(it)
    avg = service_avg_time(orders)
    avg_txt = fmt_duration(avg) if avg is not None else "hali ma'lumot yo'q"
    text = (
        f"<blockquote>🎯 <b><u>Xizmat nomi:</u></b> {escape(str(it['name']))}</blockquote>\n"
        f"🔑 <b>Xizmat IDsi:</b> {escape(str(it.get('bid', '—')))}\n"
        f"💵 <b><u>Narxi (1000 ta)</u></b> - {fmt_money(it['rate'])} so'm\n\n"
        f"<blockquote>🧮 Ushbu xizmat {len(orders)} marotaba buyurtma berilgan!</blockquote>\n"
        f"<blockquote>🕒 <b>Oxirgi buyurtma:</b> {service_last_order(orders)}</blockquote>\n"
        f"<blockquote>⏱ <b>O'rtacha bajarilish vaqti:</b> {avg_txt}</blockquote>\n"
    )
    body = escape(desc) if desc else "ℹ️ Xizmat haqida ma'lumot yo'q."
    body += "\n\n" + escape(service_credit(it, bot_name))
    text += f"\n<blockquote>📄 <b>Xizmat haqida ma'lumotlar</b> 👇\n{body}</blockquote>\n"
    text += (
        f"\n🔽 <b>Minimal:</b> {it['min']} ta\n"
        f"🔼 <b>Maksimal:</b> {it['max']} ta"
    )
    return text


@dp.callback_query(F.data.startswith("u_srv_"))
async def user_service_click(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, ni, ci, si = call.data.split("_")
    ni, ci, si = int(ni), int(ci), int(si)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await call.answer("Xizmat topilmadi", show_alert=True)
        return
    rows = [
        [ibtn("buy", f"u_buy_{ni}_{ci}_{si}")],
        back_btn(f"u_cat_{ni}_{ci}"),
    ]
    desc = await service_description(items[si])
    try:
        bot_name = await get_bot_name()
    except Exception:
        bot_name = "Bot"
    await safe_edit(call.message, user_service_text(items[si], desc, bot_name), kb(rows), parse_mode="HTML", **NO_PREVIEW)
    await call.answer()


# ---------------------------------------------------------------- USER: BUYURTMA
@dp.callback_query(F.data.startswith("u_buy_"))
async def order_start(call: types.CallbackQuery, state: FSMContext):
    _, _, ni, ci, si = call.data.split("_")
    ni, ci, si = int(ni), int(ci), int(si)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await call.answer("Xizmat topilmadi", show_alert=True)
        return
    await state.set_state(OrderState.waiting_for_link)
    await state.update_data(net=net, cat=cat, si=si, ni=ni, ci=ci)
    await safe_edit(
        call.message,
        "🔗 Havolani (link) yuboring:\n\n📝 Namuna:\nhttps://t.me/kanal_nomi/123",
        kb([back_btn(f"u_srv_{ni}_{ci}_{si}")]),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("u_again_"))
async def order_again(call: types.CallbackQuery, state: FSMContext):
    """«➕ Yana Buyurtma»: shu xizmatga darhol yangi buyurtma boshlaydi (qabul xabari joyida qoladi)."""
    _, _, ni, ci, si = call.data.split("_")
    ni, ci, si = int(ni), int(ci), int(si)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await call.answer("Xizmat topilmadi", show_alert=True)
        return
    await state.clear()
    await state.set_state(OrderState.waiting_for_link)
    await state.update_data(net=net, cat=cat, si=si, ni=ni, ci=ci)
    await call.message.answer(
        f"📦 Xizmat: {items[si]['name']}\n\n"
        "🔗 Havolani (link) yuboring:\n\n📝 Namuna:\nhttps://t.me/kanal_nomi/123",
        reply_markup=kb([back_btn(f"u_srv_{ni}_{ci}_{si}")]),
    )
    await call.answer()


@dp.message(StateFilter(OrderState.waiting_for_link))
async def order_link(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    link = (message.text or "").strip()
    data = await state.get_data()
    back = kb([back_btn(f"u_srv_{data['ni']}_{data['ci']}_{data['si']}")])
    if not (link.startswith("http://") or link.startswith("https://") or link.startswith("@")):
        await message.answer("❌ Havola noto'g'ri. Qaytadan yuboring:", reply_markup=back)
        return
    it = get_items(data["net"], data["cat"])[data["si"]]
    await state.update_data(link=link)
    await state.set_state(OrderState.waiting_for_quantity)
    await message.answer(
        f"🔢 Miqdorni kiriting:\n\n📉 Minimal: {it['min']} ta\n📈 Maksimal: {it['max']} ta",
        reply_markup=back,
    )


@dp.message(StateFilter(OrderState.waiting_for_quantity))
async def order_quantity(message: types.Message, state: FSMContext):
    if is_nav_command_or_button(message.text):
        await dispatch_nav_command_or_button(message, state)
        return
    data = await state.get_data()
    items = get_items(data["net"], data["cat"])
    back = kb([back_btn(f"u_srv_{data['ni']}_{data['ci']}_{data['si']}")])
    if data["si"] >= len(items):
        await state.clear()
        await message.answer("⚠️ Xizmat topilmadi.")
        return
    it = items[data["si"]]
    try:
        qty = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Faqat son kiriting:", reply_markup=back)
        return
    if qty < it["min"] or qty > it["max"]:
        await message.answer(f"❌ Miqdor {it['min']} dan {it['max']} gacha bo'lishi kerak:", reply_markup=back)
        return

    price = round(it["rate"] / 1000 * qty, 2)
    await state.update_data(qty=qty, price=price)
    u = get_user(message.from_user.id)
    text = (
        f"🧾 Buyurtma ma'lumotlari\n\n"
        f"📦 Xizmat: {it['name']}\n"
        f"🔗 Havola: {data['link']}\n"
        f"🔢 Miqdor: {qty} ta\n"
        f"💰 Narx: {fmt_money(price)} so'm\n"
        f"👛 Balansingiz: {fmt_money(u['balance'])} so'm"
    )
    rows = [[
        ibtn("confirm", "confirm_order"),
        ibtn("cancel", "cancel_order"),
    ]]
    await message.answer(text, reply_markup=kb(rows))


@dp.callback_query(F.data == "cancel_order")
async def cancel_order(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, "❌ Buyurtma bekor qilindi.")
    await call.answer()


@dp.callback_query(F.data == "confirm_order")
async def confirm_order(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or "qty" not in data:
        await call.answer("Buyurtma topilmadi, qaytadan boshlang", show_alert=True)
        return
    await state.clear()

    items = get_items(data["net"], data["cat"])
    if data["si"] >= len(items):
        await safe_edit(call.message, "⚠️ Xizmat topilmadi.")
        await call.answer()
        return
    it = items[data["si"]]
    price = data["price"]
    u = get_user(call.from_user.id)

    if u["balance"] < price:
        await safe_edit(
            call.message,
            f"❌ Balansingiz yetarli emas!\n\n💰 Kerak: {fmt_money(price)} so'm\n👛 Sizda: {fmt_money(u['balance'])} so'm\n\nBalansni to'ldirish uchun 💰 Pul kiritish bo'limiga kiring.",
        )
        await call.answer()
        return

    api = find_api_by_url(it.get("api_url")) if it.get("api_url") else None
    if not api:
        u["balance"] -= price
        order_id = next_order_id()
        db_data["orders"].append({
            "id": order_id,
            "user_id": call.from_user.id,
            "provider_order_id": f"MANUAL-{order_id}",
            "sid": it.get("sid", it.get("bid")),
            "service": it["name"],
            "link": data["link"],
            "quantity": data["qty"],
            "price": price,
            "api_url": "manual",
            "status": "Pending",
            "final": False,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_data(db_data)
        try:
            await send_order_channel_post(order_id, it, data["qty"], price, call.from_user, data["link"])
        except Exception:
            pass
        if call.from_user.id != OWNER_ID:
            try:
                await bot.send_message(
                    OWNER_ID,
                    f"🔔 <b>Yangi ichki buyurtma!</b>\n\n"
                    f"🆔 Buyurtma ID: #{order_id}\n"
                    f"👤 Foydalanuvchi: {call.from_user.full_name} (<code>{call.from_user.id}</code>)\n"
                    f"📦 Xizmat: {it['name']}\n"
                    f"🔗 Havola: {data['link']}\n"
                    f"🔢 Miqdor: {data['qty']} ta\n"
                    f"💰 Narxi: {fmt_money(price)} so'm",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await safe_edit(
            call.message,
            order_accepted_text(order_id, it["name"], data["qty"], price, u["balance"]),
            kb([[InlineKeyboardButton(
                text="➕ Yana Buyurtma",
                callback_data=f"u_again_{data['ni']}_{data['ci']}_{data['si']}",
                **sk("success"),
            )]]),
        )
        await call.answer()
        return

    await safe_edit(call.message, "🔄 Buyurtma yuborilmoqda...")
    submit_link = dedup_submit_link(data["link"])
    try:
        ok, resp = await api_post(api["url"], {
            "key": api["key"],
            "action": "add",
            "service": it["sid"],
            "link": submit_link,
            "quantity": data["qty"],
        })
    except Exception as e:
        ok, resp = False, str(e)

    if ok and isinstance(resp, dict) and "order" in resp:
        u["balance"] -= price
        order_id = next_order_id()
        db_data["orders"].append({
            "id": order_id,
            "user_id": call.from_user.id,
            "provider_order_id": resp["order"],
            "sid": it.get("sid"),
            "service": it["name"],
            "link": data["link"],
            "quantity": data["qty"],
            "price": price,
            "api_url": it["api_url"],
            "status": "Pending",
            "final": False,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_data(db_data)
        await send_order_channel_post(order_id, it, data["qty"], price, call.from_user, data["link"])
        await safe_edit(
            call.message,
            order_accepted_text(order_id, it["name"], data["qty"], price, u["balance"]),
            kb([[InlineKeyboardButton(
                text="➕ Yana Buyurtma",
                callback_data=f"u_again_{data['ni']}_{data['ci']}_{data['si']}",
                **sk("success"),
            )]]),
        )
    else:
        err = resp.get("error") if isinstance(resp, dict) else resp
        await safe_edit(call.message, f"❌ Buyurtma berilmadi!\nXato: {err}\n\nPul yechilmadi.")
    await call.answer()


# ---------------------------------------------------------------- ADMIN: API BOSHQARUVI
async def show_api_list(message_or_call):
    apis = db_data.get("apis", [])
    text = f"🔑 API pravayderlar ro'yhati: {len(apis)} ta\n\n"
    results = await asyncio.gather(
        *(fetch_api_balance(api.get("url"), api.get("key")) for api in apis)
    )
    buttons = []
    for idx, (api, (ok, balance, currency)) in enumerate(zip(apis, results), 1):
        bal_str = f"{balance} {currency}" if ok else "Xato!"
        text += f"{idx}. {api_title(api)}  ({bal_str})\n"
        buttons.append(InlineKeyboardButton(text=f"| • {idx} • |", callback_data=f"view_api_{idx-1}", style="primary"))
    buttons = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]  # raqamlar yonma-yon, qatorda 3 tadan
    buttons.append([InlineKeyboardButton(text="➕ Api qo'shish", callback_data="add_api", style="success")])

    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=kb(buttons))
    else:
        await safe_edit(message_or_call.message, text, kb(buttons))


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_api"))
async def api_management_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await show_api_list(message)


@dp.callback_query(F.data == "back_to_api_list")
async def back_to_api_list_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        await show_api_list(call)
        await call.answer()


@dp.callback_query(F.data.startswith("view_api_"))
async def view_api_details(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    idx = int(call.data.replace("view_api_", ""))
    apis = db_data.get("apis", [])
    if idx < len(apis):
        url, key = apis[idx].get("url"), apis[idx].get("key")
        ok, balance, currency = await fetch_api_balance(url, key)
        bal_str = f"{balance} {currency}" if ok else "Xato!"
        text = f"{idx+1}. {api_title(apis[idx])}  ✅\n\n🏷 Nomi: {api_title(apis[idx])}\n🔗 Havola: {url}\n🔑 Kalit: {key}\n\n💰 Balans: {bal_str}"
        rows = [
            [InlineKeyboardButton(text="✒️ Nomi", callback_data=f"edit_api_name_{idx}", style="success"),
             InlineKeyboardButton(text="✒️ URL", callback_data=f"edit_api_url_{idx}", style="success")],
            [InlineKeyboardButton(text="✒️ Kalit", callback_data=f"edit_api_key_{idx}", style="success")],
            [InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f"del_api_{idx}", style="danger"),
             InlineKeyboardButton(text="⏪ Orqaga", callback_data="back_to_api_list", style="primary")],
        ]
        await safe_edit(call.message, text, kb(rows))
    await call.answer()


@dp.callback_query(F.data == "add_api")
async def add_api_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.set_state(AdminState.waiting_for_api_url)
        await safe_edit(call.message, "🔗 API manzilini kiriting:\n\n📝 Namuna:\nhttp://example.com/api/v2", kb([back_btn("back_to_api_list")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_api_url))
async def process_api_url(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        await state.update_data(api_url=message.text.strip())
        await state.set_state(AdminState.waiting_for_api_key)
        await message.answer("✅ URL qabul qilindi!\n\n🔑 Endi API kalitini kiriting:", reply_markup=kb([back_btn("back_to_api_list")]))


@dp.message(StateFilter(AdminState.waiting_for_api_key))
async def process_api_key(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        key = message.text.strip()
        data = await state.get_data()
        url = data.get("api_url")
        msg = await message.answer("🔄 API ulanishi va balans tekshirilmoqda...")
        ok, balance, currency = await fetch_api_balance(url, key)
        back = kb([back_btn("back_to_api_list")])
        if ok:
            db_data["apis"].append({"url": url, "key": key})
            save_data(db_data)
            await state.clear()
            await msg.edit_text(
                f"✅ API muvaffaqiyatli qo'shildi!\n\n💰 Balans: {balance} {currency}\n🔗 Havola: {url}\nKalit: {key}",
                reply_markup=back)
        else:
            await msg.edit_text(f"❌ Ulanishda xatolik yuz berdi!\nXato: {balance}\n\nQaytadan urinib ko'ring.", reply_markup=back)


@dp.callback_query(F.data.startswith("del_api_"))
async def delete_api(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        idx = int(call.data.replace("del_api_", ""))
        apis = db_data.get("apis", [])
        if idx < len(apis):
            apis.pop(idx)
            save_data(db_data)
            await call.answer("API o'chirildi!", show_alert=True)
        await show_api_list(call)


@dp.callback_query(F.data.startswith("edit_api_name_"))
async def edit_api_name_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        idx = int(call.data.replace("edit_api_name_", ""))
        if idx >= len(db_data["apis"]):
            await call.answer("API topilmadi", show_alert=True)
            return
        await state.set_state(AdminState.waiting_for_edit_api_name)
        await state.update_data(edit_idx=idx)
        await safe_edit(
            call.message,
            f"📝 Provayder uchun yangi nom kiriting:\n\nHozirgi nom: {api_title(db_data['apis'][idx])}\n\nMasalan: TOP | SMM",
            kb([back_btn(f"view_api_{idx}")]),
        )
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_api_name))
async def process_edit_api_name(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        data = await state.get_data()
        idx = data.get("edit_idx")
        name = (message.text or "").strip()
        if not name:
            await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=kb([back_btn(f"view_api_{idx}")]))
            return
        if idx is not None and idx < len(db_data["apis"]):
            db_data["apis"][idx]["name"] = name
            save_data(db_data)
        await state.clear()
        await message.answer(f"✅ API nomi yangilandi: {name}", reply_markup=kb([back_btn(f"view_api_{idx}")]))


@dp.callback_query(F.data.startswith("edit_api_url_"))
async def edit_api_url_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        idx = int(call.data.replace("edit_api_url_", ""))
        await state.set_state(AdminState.waiting_for_edit_url)
        await state.update_data(edit_idx=idx)
        await safe_edit(call.message, "📝 Yangi URL manzilini kiriting:", kb([back_btn(f"view_api_{idx}")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_url))
async def process_edit_api_url(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        data = await state.get_data()
        idx = data.get("edit_idx")
        if idx is not None and idx < len(db_data["apis"]):
            old_url = db_data["apis"][idx]["url"]
            new_url = message.text.strip()
            db_data["apis"][idx]["url"] = new_url
            # shu provayderdagi xizmatlar ham yangi manzilga ulanadi
            for lst in db_data["items"].values():
                for it in lst:
                    if it.get("api_url") == old_url:
                        it["api_url"] = new_url
            for o in db_data["orders"]:
                if o.get("api_url") == old_url:
                    o["api_url"] = new_url
            save_data(db_data)
        await state.clear()
        await message.answer("✅ API URL yangilandi!", reply_markup=kb([back_btn(f"view_api_{idx}")]))


@dp.callback_query(F.data.startswith("edit_api_key_"))
async def edit_api_key_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        idx = int(call.data.replace("edit_api_key_", ""))
        await state.set_state(AdminState.waiting_for_edit_key)
        await state.update_data(edit_idx=idx)
        await safe_edit(call.message, "📝 Yangi API kalitini kiriting:", kb([back_btn(f"view_api_{idx}")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_key))
async def process_edit_api_key(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        data = await state.get_data()
        idx = data.get("edit_idx")
        if idx is not None and idx < len(db_data["apis"]):
            db_data["apis"][idx]["key"] = message.text.strip()
            save_data(db_data)
        await state.clear()
        await message.answer("✅ API kaliti yangilandi!", reply_markup=kb([back_btn(f"view_api_{idx}")]))


# ---------------------------------------------------------------- ADMIN: TARMOQLAR
async def show_admin_networks(target, edit=True):
    n = nets()
    if n:
        text, k = f"📂 Ijtimoiy tarmoqlar  ({len(n)} ta):", get_admin_services_inline()
    else:
        text = "⚠️ Ijtimoiy tarmoqlar mavjud emas!"
        k = kb([[InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_social_network")]])
    if edit:
        await safe_edit(target, text, k)
    else:
        await target.answer(text, reply_markup=k)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_services"))
async def admin_services_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == OWNER_ID:
        await show_admin_networks(message, edit=False)


@dp.callback_query(F.data == "add_social_network")
async def add_social_network_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.set_state(AdminState.waiting_for_network_name)
        await safe_edit(call.message, "📝 Yangi ijtimoiy tarmoq nomini kiriting:", kb([back_btn("back_to_services")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_network_name))
async def process_network_name(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        orig, emoji_id, label = extract_emoji_and_label(message)
        name = orig
        if name not in db_data["services"]:
            db_data["services"][name] = []
            if emoji_id:
                db_data["ui"].setdefault("net_icons", {})[name] = emoji_id
                db_data["ui"].setdefault("net_labels", {})[name] = label
            save_data(db_data)
        await state.clear()
        rows = [
            [InlineKeyboardButton(text="➕ Yana qo'shish", callback_data="add_social_network")],
            back_btn("back_to_services"),
        ]
        await message.answer(f"✅ {name} - muvaffaqiyatli qo'shildi!", reply_markup=kb(rows))


@dp.callback_query(F.data == "delete_social_network")
async def delete_social_network_mode(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        n = nets()
        if not n:
            await call.answer("⚠️ O'chirish uchun tarmoq yo'q!", show_alert=True)
            return
        rows = grid([InlineKeyboardButton(text=f"❌ {net_label(name)}", callback_data=f"del_net_{i}", **net_icon_kw(name), style="danger") for i, name in enumerate(n)])
        rows.append(back_btn("back_to_services"))
        await safe_edit(call.message, "🗑 O'chirmoqchi bo'lgan tarmoqni tanlang:", kb(rows))
        await call.answer()


@dp.callback_query(F.data.startswith("del_net_"))
async def process_delete_network(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        ni = int(call.data.replace("del_net_", ""))
        net = get_net(ni)
        if net:
            del db_data["services"][net]
            for k in [k for k in list(db_data["items"].keys()) if k.startswith(f"{net}|")]:
                del db_data["items"][k]
            db_data["ui"].get("net_icons", {}).pop(net, None)
            db_data["ui"].get("net_labels", {}).pop(net, None)
            ci_map = db_data["ui"].get("cat_icons", {})
            for k in [k for k in list(ci_map.keys()) if k.startswith(f"{net}|")]:
                ci_map.pop(k, None)
            cl_map = db_data["ui"].get("cat_labels", {})
            for k in [k for k in list(cl_map.keys()) if k.startswith(f"{net}|")]:
                cl_map.pop(k, None)
            save_data(db_data)
            await call.answer(f"{net} o'chirildi!", show_alert=True)
        await show_admin_networks(call.message)


@dp.callback_query(F.data == "back_to_services")
async def back_to_services_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await show_admin_networks(call.message)
        await call.answer()


# ---------------------------------------------------------------- ADMIN: BO'LIMLAR
async def show_admin_categories(call_message, ni):
    net = get_net(ni)
    if net is None:
        await show_admin_networks(call_message)
        return
    cats = db_data["services"].get(net, [])
    rows = [[InlineKeyboardButton(
        text=cat_label(net, c),
        callback_data=f"cat_info_{ni}_{ci}",
        **cat_icon_kw(net, c),
        **sk(cat_style(net, c))
    )] for ci, c in enumerate(cats)]
    rows.append([
        InlineKeyboardButton(text="➕ Qo'shish", callback_data=f"add_cat_{ni}"),
        InlineKeyboardButton(text="➖ Ayirish", callback_data=f"del_mode_cat_{ni}"),
    ])
    rows.append([
        InlineKeyboardButton(text="✒️ Tahrirlash", callback_data=f"edit_net_{ni}"),
        InlineKeyboardButton(text="🎨 Rangi", callback_data=f"cs_n_{ni}"),
    ])
    rows.append(back_btn("back_to_services"))
    text = f"📂 {net} bo'limlari ({len(cats)} ta):" if cats else f"⚠️ {net} ichida bo'limlar mavjud emas!"
    await safe_edit(call_message, text, kb(rows))


@dp.callback_query(F.data.startswith("edit_net_"))
async def edit_network_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        ni = int(call.data.split("_")[2])
        net = get_net(ni)
        if net is None:
            await call.answer("Topilmadi", show_alert=True)
            return
        await state.set_state(AdminState.waiting_for_edit_net_name)
        await state.update_data(ni=ni)
        await safe_edit(call.message, f"📝 [{net}] uchun yangi nom kiriting:\n\nPremium emoji bilan yuborsangiz, animatsiyalik emoji bilan chiqadi.", kb([back_btn(f"a_net_{ni}")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_net_name))
async def process_edit_network_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    ni = data.get("ni")
    old = get_net(ni)
    orig, emoji_id, label = extract_emoji_and_label(message)
    new = orig
    back = kb([back_btn(f"a_net_{ni}")])
    if old is None:
        await state.clear()
        await message.answer("⚠️ Tarmoq topilmadi.")
        return
    if not new or "|" in new:
        await message.answer("❌ Nom bo'sh bo'lmasin va '|' belgisi bo'lmasin:", reply_markup=back)
        return
    if new != old and new in db_data["services"]:
        await message.answer("❌ Bunday nomli tarmoq allaqachon bor. Boshqa nom kiriting:", reply_markup=back)
        return
    if new != old:
        rename_network(old, new)
    if emoji_id:
        db_data["ui"].setdefault("net_icons", {})[new] = emoji_id
        db_data["ui"].setdefault("net_labels", {})[new] = label
    else:
        db_data["ui"].setdefault("net_icons", {}).pop(new, None)
        db_data["ui"].setdefault("net_labels", {}).pop(new, None)
    save_data(db_data)
    await state.clear()
    await message.answer(f"✅ Tarmoq nomi o'zgartirildi: {old} → {new}", reply_markup=back)


@dp.callback_query(F.data.startswith("a_net_"))
async def admin_network_click(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await show_admin_categories(call.message, int(call.data.split("_")[2]))
        await call.answer()


@dp.callback_query(F.data.startswith("add_cat_"))
async def add_category_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        ni = int(call.data.replace("add_cat_", ""))
        net = get_net(ni)
        if net is None:
            await call.answer("Topilmadi", show_alert=True)
            return
        await state.set_state(AdminState.waiting_for_category_name)
        await state.update_data(ni=ni)
        await safe_edit(call.message, f"📝 [{net}] uchun yangi bo'lim nomini kiriting:", kb([back_btn(f"a_net_{ni}")]))
        await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_category_name))
async def process_category_name(message: types.Message, state: FSMContext):
    if message.from_user.id == OWNER_ID:
        data = await state.get_data()
        ni = data.get("ni")
        net = get_net(ni)
        orig, emoji_id, label = extract_emoji_and_label(message)
        name = orig
        if net and name not in db_data["services"][net]:
            db_data["services"][net].append(name)
            k = item_key(net, name)
            if emoji_id:
                db_data["ui"].setdefault("cat_icons", {})[k] = emoji_id
                db_data["ui"].setdefault("cat_labels", {})[k] = label
            save_data(db_data)
        await state.clear()
        rows = [
            [InlineKeyboardButton(text="➕ Yana qo'shish", callback_data=f"add_cat_{ni}")],
            back_btn(f"a_net_{ni}"),
        ]
        await message.answer(f"✅ {name} bo'limi muvaffaqiyatli qo'shildi!", reply_markup=kb(rows))


@dp.callback_query(F.data.startswith("del_mode_cat_"))
async def delete_category_mode(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        ni = int(call.data.replace("del_mode_cat_", ""))
        net = get_net(ni)
        cats = db_data["services"].get(net, []) if net else []
        if not cats:
            await call.answer("⚠️ O'chirish uchun bo'lim yo'q!", show_alert=True)
            return
        rows = grid([InlineKeyboardButton(text=f"❌ {cat_label(net, c)}", callback_data=f"del_cat_{ni}_{ci}", **cat_icon_kw(net, c), style="danger") for ci, c in enumerate(cats)])
        rows.append(back_btn(f"a_net_{ni}"))
        await safe_edit(call.message, "🗑 O'chirmoqchi bo'lgan bo'limni tanlang:", kb(rows))
        await call.answer()


@dp.callback_query(F.data.startswith("del_cat_"))
async def process_delete_category(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        _, _, ni, ci = call.data.split("_")
        ni, ci = int(ni), int(ci)
        net = get_net(ni)
        cat = get_cat(net, ci) if net else None
        if cat:
            db_data["services"][net].pop(ci)
            k = item_key(net, cat)
            db_data["items"].pop(k, None)
            db_data["ui"].get("cat_icons", {}).pop(k, None)
            db_data["ui"].get("cat_labels", {}).pop(k, None)
            save_data(db_data)
            await call.answer(f"{cat} o'chirildi!", show_alert=True)
        await show_admin_categories(call.message, ni)


# ---------------------------------------------------------------- ADMIN: BO'LIM ICHIDAGI XIZMATLAR
async def show_admin_category(call_message, ni, ci):
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None:
        await show_admin_networks(call_message)
        return
    items = get_items(net, cat)
    rows = [
        [InlineKeyboardButton(
            text=f"{srv_label(it)} — {fmt_money(it['rate'])} so'm",
            callback_data=f"srv_info_{ni}_{ci}_{si}",
            **srv_icon_kw(it),
            **sk(srv_style(it)))]
        for si, it in enumerate(items)
    ]
    rows.append([
        InlineKeyboardButton(text="➕ Qo'shish", callback_data=f"add_srv_{ni}_{ci}"),
        InlineKeyboardButton(text="📥 Yuklab olish", callback_data=f"imp_srv_{ni}_{ci}"),
    ])
    rows.append([
        InlineKeyboardButton(text="✒️ Tahrirlash", callback_data=f"edit_cat_{ni}_{ci}"),
        InlineKeyboardButton(text="🎨 Rangi", callback_data=f"cs_c_{ni}_{ci}"),
    ])
    rows.append(back_btn(f"a_net_{ni}"))
    if items:
        text = f"📂 {net} › {cat}\n\nXizmatlar ({len(items)} ta):"
    else:
        text = "⚠️ Xizmatlar mavjud emas!"
    await safe_edit(call_message, text, kb(rows))


@dp.callback_query(F.data.startswith("cat_info_"))
async def admin_category_click(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        _, _, ni, ci = call.data.split("_")
        await show_admin_category(call.message, int(ni), int(ci))
        await call.answer()


@dp.callback_query(F.data.startswith("srv_info_"))
async def admin_service_info(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        _, _, ni, ci, si = call.data.split("_")
        ni, ci, si = int(ni), int(ci), int(si)
        net = get_net(ni)
        cat = get_cat(net, ci) if net else None
        items = get_items(net, cat) if cat else []
        if si >= len(items):
            await call.answer("Topilmadi", show_alert=True)
            return
        it = items[si]
        text = service_text(it) + f"\n\n🆔 Provayder ID: {it['sid']}\n🔗 Provayder: {urlparse(it['api_url']).netloc or it['api_url']}"
        rows = [
            [InlineKeyboardButton(text="✒️ Tahrirlash", callback_data=f"edit_srv_{ni}_{ci}_{si}"),
             InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f"del_srv_{ni}_{ci}_{si}", style="danger")],
            back_btn(f"cat_info_{ni}_{ci}"),
        ]
        await safe_edit(call.message, text, kb(rows))
        await call.answer()


@dp.callback_query(F.data.startswith("del_srv_"))
async def admin_delete_service(call: types.CallbackQuery):
    if call.from_user.id == OWNER_ID:
        _, _, ni, ci, si = call.data.split("_")
        ni, ci, si = int(ni), int(ci), int(si)
        net = get_net(ni)
        cat = get_cat(net, ci) if net else None
        items = get_items(net, cat) if cat else []
        if si < len(items):
            items.pop(si)
            save_data(db_data)
            await call.answer("Xizmat o'chirildi!", show_alert=True)
        await show_admin_category(call.message, ni, ci)


# --- Bo'lim nomini tahrirlash
@dp.callback_query(F.data.startswith("edit_cat_"))
async def edit_category_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None:
        await call.answer("Topilmadi", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_edit_cat_name)
    await state.update_data(ni=ni, ci=ci)
    await safe_edit(call.message, f"📝 [{cat}] bo'limi uchun yangi nom kiriting:\n\nPremium emoji bilan yuborsangiz, animatsiyalik emoji bilan chiqadi.", kb([back_btn(f"cat_info_{ni}_{ci}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_cat_name))
async def process_edit_category_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    ni, ci = data.get("ni"), data.get("ci")
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    orig, emoji_id, label = extract_emoji_and_label(message)
    new = orig
    back = kb([back_btn(f"cat_info_{ni}_{ci}")])
    if cat is None:
        await state.clear()
        await message.answer("⚠️ Bo'lim topilmadi.")
        return
    if not new:
        await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=back)
        return
    if new != cat and new in db_data["services"][net]:
        await message.answer("❌ Bunday nomli bo'lim allaqachon bor. Boshqa nom kiriting:", reply_markup=back)
        return
    if new != cat:
        rename_category(net, ci, new)
    k = item_key(net, new)
    if emoji_id:
        db_data["ui"].setdefault("cat_icons", {})[k] = emoji_id
        db_data["ui"].setdefault("cat_labels", {})[k] = label
    else:
        db_data["ui"].setdefault("cat_icons", {}).pop(k, None)
        db_data["ui"].setdefault("cat_labels", {}).pop(k, None)
    save_data(db_data)
    await state.clear()
    await message.answer(f"✅ Bo'lim nomi o'zgartirildi: {cat} → {new}", reply_markup=back)


# --- Xizmatni tahrirlash (nom, narx, min, max)
@dp.callback_query(F.data.startswith("edit_srv_"))
async def edit_service_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    _, _, ni, ci, si = call.data.split("_")
    ni, ci, si = int(ni), int(ci), int(si)
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await call.answer("Topilmadi", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text="✒️ Nomi", callback_data=f"esf_name_{ni}_{ci}_{si}"),
         InlineKeyboardButton(text="✒️ Narxi", callback_data=f"esf_rate_{ni}_{ci}_{si}")],
        [InlineKeyboardButton(text="✒️ Minimal", callback_data=f"esf_min_{ni}_{ci}_{si}"),
         InlineKeyboardButton(text="✒️ Maksimal", callback_data=f"esf_max_{ni}_{ci}_{si}")],
        [InlineKeyboardButton(text="📄 Tavsif", callback_data=f"esf_desc_{ni}_{ci}_{si}")],
        [InlineKeyboardButton(text="🔌 Provayder / ID", callback_data=f"esrv_prov_{ni}_{ci}_{si}")],
        [InlineKeyboardButton(text="🎨 Rangi", callback_data=f"cs_s_{ni}_{ci}_{si}")],
        back_btn(f"srv_info_{ni}_{ci}_{si}"),
    ]
    await safe_edit(call.message, service_text(items[si]) + "\n\nNimani tahrirlaysiz?", kb(rows))
    await call.answer()


# --- Xizmatning provayderi va provayderdagi ID sini o'zgartirish
@dp.callback_query(F.data.startswith("esrv_prov_"))
async def edit_service_pick_provider(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    parts = call.data.split("_")
    ni, ci, si = int(parts[-3]), int(parts[-2]), int(parts[-1])
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await call.answer("Topilmadi", show_alert=True)
        return
    apis = db_data["apis"]
    if not apis:
        await call.answer("⚠️ Avval API provayder qo'shing!", show_alert=True)
        return

    it = items[si]
    cur_api = find_api_by_url(it.get("api_url"))
    text = "🔌 Xizmatni qaysi provayderga bog'lamoqchisiz?\n\n"
    if cur_api:
        text += f"Hozirgi provayder: {api_title(cur_api)} (ID: {it['sid']})\n\n"
    text += "Quyida mavjud provayderlar ro'yxati keltirilgan.\nKerakli API raqamini tanlang 👇\n\n"
    for i, a in enumerate(apis, 1):
        text += f"{i}. {api_title(a)}\n"

    nums = [InlineKeyboardButton(text=str(i + 1), callback_data=f"esrv_api_{ni}_{ci}_{si}_{i}") for i in range(len(apis))]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append(back_btn(f"edit_srv_{ni}_{ci}_{si}"))
    await safe_edit(call.message, text, kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("esrv_api_"))
async def edit_service_ask_id(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, si, ai = int(parts[-4]), int(parts[-3]), int(parts[-2]), int(parts[-1])
    if ai >= len(db_data["apis"]):
        await call.answer("API topilmadi", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_edit_service_id)
    await state.update_data(ni=ni, ci=ci, si=si, ai=ai)
    await safe_edit(
        call.message,
        "🔑 Provayderdagi yangi Xizmat (Service) ID sini kiriting:\n\nMasalan: 1227",
        kb([back_btn(f"edit_srv_{ni}_{ci}_{si}")]),
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_edit_service_id))
async def process_edit_service_id(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    ni, ci, si, ai = data["ni"], data["ci"], data["si"], data["ai"]
    back = kb([back_btn(f"edit_srv_{ni}_{ci}_{si}")])

    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if cat is None or si >= len(items) or ai >= len(db_data["apis"]):
        await state.clear()
        await message.answer("⚠️ Xizmat, bo'lim yoki API topilmadi.")
        return

    api = db_data["apis"][ai]
    title = api_title(api)
    sid = (message.text or "").strip()
    if not sid.isdigit():
        await message.answer("❌ Faqat raqam kiriting.\n\nMasalan: 1227", reply_markup=back)
        return

    msg = await message.answer("🔄 Xizmat qidirilmoqda...")
    ok, services = await fetch_api_services(api["url"], api["key"])
    if not ok:
        await msg.edit_text(f"❌ Provayderdan xizmatlarni olib bo'lmadi!\nXato: {services}\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    found = next((s for s in services if str(s.get("service")) == sid), None)
    if not found:
        await msg.edit_text(f"❌ {title} provayderida #{sid} ID li xizmat mavjud emas!\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    if any(i for i, it in enumerate(items) if i != si and it["sid"] == sid and it["api_url"] == api["url"]):
        await msg.edit_text(f"⚠️ #{sid} ID li xizmat bu bo'limga allaqachon qo'shilgan!\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    _, _, currency = await fetch_api_balance(api["url"], api["key"])
    it = items[si]
    it["sid"] = sid
    it["api_url"] = api["url"]
    it["name"] = found.get("name", it.get("name", f"Xizmat #{sid}"))
    it["rate"] = to_uzs(found.get("rate", 0), currency or "USD")
    it["min"] = int(found.get("min", it.get("min", 1)))
    it["max"] = int(found.get("max", it.get("max", 1000000)))
    save_data(db_data)

    await state.clear()
    rows = [
        [InlineKeyboardButton(text="✒️ Yana tahrirlash", callback_data=f"edit_srv_{ni}_{ci}_{si}")],
        back_btn(f"srv_info_{ni}_{ci}_{si}"),
    ]
    await msg.edit_text(
        f"✅ Provayder yangilandi!\n\n🔌 Yangi provayder: {title}\n\n" + service_text(it),
        reply_markup=kb(rows),
    )


FIELD_PROMPTS = {
    "name": "📝 Xizmatning yangi nomini kiriting:\n\nPremium emoji bilan yuborsangiz, animatsiyalik emoji bilan chiqadi.",
    "rate": "💰 Yangi narxni kiriting (1000 ta uchun, so'mda):\n\nMasalan: 6500\nBepul qilish uchun: 0",
    "min": "📉 Yangi minimal miqdorni kiriting:\n\nMasalan: 10",
    "max": "📈 Yangi maksimal miqdorni kiriting:\n\nMasalan: 1000000",
    "desc": "📄 Xizmat tavsifini yuboring (foydalanuvchiga «Xizmat haqida ma'lumotlar» bo'limida ko'rinadi).\n\nBu tavsif provayder saytidagi tavsifdan ustun turadi.\nO'chirish uchun: 0 (shunda tavsif provayder saytidan olinadi)",
}


@dp.callback_query(F.data.startswith("esf_"))
async def edit_service_field(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, field, ni, ci, si = call.data.split("_")
    ni, ci, si = int(ni), int(ci), int(si)
    if field not in FIELD_PROMPTS:
        await call.answer("Noma'lum maydon", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_srv_value)
    await state.update_data(field=field, ni=ni, ci=ci, si=si)
    await safe_edit(call.message, FIELD_PROMPTS[field], kb([back_btn(f"edit_srv_{ni}_{ci}_{si}")]))
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_srv_value))
async def process_service_value(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    field, ni, ci, si = d["field"], d["ni"], d["ci"], d["si"]
    back = kb([back_btn(f"edit_srv_{ni}_{ci}_{si}")])
    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    items = get_items(net, cat) if cat else []
    if si >= len(items):
        await state.clear()
        await message.answer("⚠️ Xizmat topilmadi.")
        return
    it = items[si]
    raw = (message.text or "").strip()

    if field == "name":
        if not raw:
            await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=back)
            return
        orig, emoji_id, label = extract_emoji_and_label(message)
        it["name"] = orig
        if emoji_id:
            it["emoji_id"] = emoji_id
            it["label"] = label
        else:
            it.pop("emoji_id", None)
            it.pop("label", None)
    elif field == "desc":
        if not raw:
            await message.answer("❌ Tavsif bo'sh bo'lmasin (o'chirish uchun 0 yuboring):", reply_markup=back)
            return
        if raw == "0":
            it.pop("desc", None)
        else:
            it["desc"] = raw[:1500]
    elif field == "rate":
        try:
            v = float(raw.replace(",", ".").replace(" ", ""))
        except ValueError:
            await message.answer("❌ Faqat son kiriting. Masalan: 6500", reply_markup=back)
            return
        if v < 0:
            await message.answer("❌ Narx manfiy bo'lmasin (0 = bepul):", reply_markup=back)
            return
        it["rate"] = round(v, 2)
    else:
        try:
            v = int(raw)
        except ValueError:
            await message.answer("❌ Faqat butun son kiriting:", reply_markup=back)
            return
        if v < 1:
            await message.answer("❌ Miqdor 1 dan kichik bo'lmasin:", reply_markup=back)
            return
        if field == "min" and v > it["max"]:
            await message.answer(f"❌ Minimal maksimaldan ({it['max']}) katta bo'lmasin:", reply_markup=back)
            return
        if field == "max" and v < it["min"]:
            await message.answer(f"❌ Maksimal minimaldan ({it['min']}) kichik bo'lmasin:", reply_markup=back)
            return
        it[field] = v

    save_data(db_data)
    await state.clear()
    rows = [
        [InlineKeyboardButton(text="✒️ Yana tahrirlash", callback_data=f"edit_srv_{ni}_{ci}_{si}")],
        back_btn(f"srv_info_{ni}_{ci}_{si}"),
    ]
    await message.answer("✅ Xizmat yangilandi!\n\n" + service_text(it), reply_markup=kb(rows))


# --- Xizmat qo'shish: 1) API tanlash
@dp.callback_query(F.data.startswith("add_srv_"))
async def add_service_choose_api(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    apis = db_data["apis"]
    if not apis:
        await call.answer("⚠️ Avval API provayder qo'shing!", show_alert=True)
        return

    text = "🔑 Xizmatni qaysi API orqali qo'shmoqchisiz?\n\nQuyida mavjud provayderlar ro'yxati keltirilgan.\nKerakli API raqamini tanlang 👇\n\n"
    for i, a in enumerate(apis, 1):
        text += f"{i}. {api_title(a)}\n"

    nums = [InlineKeyboardButton(text=str(i + 1), callback_data=f"pick_api_{ni}_{ci}_{i}") for i in range(len(apis))]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append(back_btn(f"cat_info_{ni}_{ci}"))
    await safe_edit(call.message, text, kb(rows))
    await call.answer()


# --- 2) Service ID so'rash
@dp.callback_query(F.data.startswith("pick_api_"))
async def add_service_ask_id(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, _, ni, ci, ai = call.data.split("_")
    ni, ci, ai = int(ni), int(ci), int(ai)
    if ai >= len(db_data["apis"]):
        await call.answer("API topilmadi", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_service_id)
    await state.update_data(ni=ni, ci=ci, ai=ai)
    await safe_edit(
        call.message,
        "🔑 Provayderdagi Xizmat (Service) ID sini kiriting:\n\nMasalan: 1227",
        kb([back_btn(f"cat_info_{ni}_{ci}")]),
    )
    await call.answer()


# --- 3) ID ni qidirish va qo'shish
@dp.message(StateFilter(AdminState.waiting_for_service_id))
async def process_service_id(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    ni, ci, ai = data["ni"], data["ci"], data["ai"]
    back = kb([back_btn(f"cat_info_{ni}_{ci}")])

    net = get_net(ni)
    cat = get_cat(net, ci) if net else None
    if cat is None or ai >= len(db_data["apis"]):
        await state.clear()
        await message.answer("⚠️ Tarmoq, bo'lim yoki API topilmadi.")
        return

    api = db_data["apis"][ai]
    title = api_title(api)
    sid = (message.text or "").strip()
    if not sid.isdigit():
        await message.answer("❌ Faqat raqam kiriting.\n\nMasalan: 1227", reply_markup=back)
        return

    msg = await message.answer("🔄 Xizmat qidirilmoqda...")
    ok, services = await fetch_api_services(api["url"], api["key"])
    if not ok:
        await msg.edit_text(f"❌ Provayderdan xizmatlarni olib bo'lmadi!\nXato: {services}\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    found = next((s for s in services if str(s.get("service")) == sid), None)
    if not found:
        await msg.edit_text(f"❌ {title} provayderida #{sid} ID li xizmat mavjud emas!\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    lst = db_data["items"].setdefault(item_key(net, cat), [])
    if any(it["sid"] == sid and it["api_url"] == api["url"] for it in lst):
        await msg.edit_text(f"⚠️ #{sid} ID li xizmat bu bo'limga allaqachon qo'shilgan!\n\nBoshqa ID kiriting:", reply_markup=back)
        return

    _, _, currency = await fetch_api_balance(api["url"], api["key"])
    rate_uzs = to_uzs(found.get("rate", 0), currency or "USD")
    item = {
        "sid": sid,
        "name": found.get("name", f"Xizmat #{sid}"),
        "rate": rate_uzs,
        "min": int(found.get("min", 1)),
        "max": int(found.get("max", 1000000)),
        "api_url": api["url"],
    }
    lst.append(item)
    save_data(db_data)

    await msg.edit_text(
        "✅ Xizmat muvaffaqiyatli qo'shildi!\n\n"
        f"📦 Xizmat nomi: {item['name']}\n"
        f"💰 Narx: {price_line(item['rate'])}\n"
        f"📉 Minimal: {item['min']} ta\n"
        f"📈 Maksimal: {item['max']} ta\n\n"
        "➕ Yana xizmat qo'shish uchun xizmat ID sini kiriting:",
        reply_markup=back,
    )


# ---------------------------------------------------------------- ADMIN: XIZMATLARNI PROVAYDERDAN YUKLAB OLISH
IMPORT_PAGE_SIZE = 8


def import_cat_items(services, cat):
    return [s for s in services if str(s.get("category") or "Boshqa").strip() == cat]


def import_build_item(found, api_url, currency):
    sid = str(found.get("service"))
    return {
        "sid": sid,
        "name": found.get("name") or f"Xizmat #{sid}",
        "rate": to_uzs(found.get("rate", 0), currency or "USD"),
        "min": int(found.get("min", 1) or 1),
        "max": int(found.get("max", 1000000) or 1000000),
        "api_url": api_url,
    }


# --- 1) API tanlash
@dp.callback_query(F.data.startswith("imp_srv_"))
async def import_choose_api(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    _, _, ni, ci = call.data.split("_")
    ni, ci = int(ni), int(ci)
    apis = db_data["apis"]
    if not apis:
        await call.answer("⚠️ Avval API provayder qo'shing!", show_alert=True)
        return

    text = "🔑 Xizmatlarni qaysi API orqali yuklab olmoqchisiz?\n\nQuyida mavjud provayderlar ro'yxati keltirilgan.\nKerakli API raqamini tanlang 👇\n\n"
    for i, a in enumerate(apis, 1):
        text += f"{i}. {api_title(a)}\n"

    nums = [InlineKeyboardButton(text=str(i + 1), callback_data=f"imp_api_{ni}_{ci}_{i}") for i in range(len(apis))]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append(back_btn(f"cat_info_{ni}_{ci}"))
    await safe_edit(call.message, text, kb(rows))
    await call.answer()


# --- 2) Tanlangan APIdan kategoriyalarni olish
@dp.callback_query(F.data.startswith("imp_api_"))
async def import_choose_category(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai = int(parts[-3]), int(parts[-2]), int(parts[-1])
    if ai >= len(db_data["apis"]):
        await call.answer("API topilmadi", show_alert=True)
        return
    api = db_data["apis"][ai]
    await call.answer("🔄 Yuklanmoqda...")

    ok, services = await fetch_api_services(api["url"], api["key"])
    if not ok:
        await safe_edit(
            call.message,
            f"❌ Provayderdan xizmatlarni olib bo'lmadi!\nXato: {services}",
            kb([back_btn(f"imp_srv_{ni}_{ci}")]),
        )
        return

    cats = []
    for s in services:
        c = str(s.get("category") or "Boshqa").strip()
        if c not in cats:
            cats.append(c)
    if not cats:
        await safe_edit(call.message, "⚠️ Bu provayderda xizmatlar topilmadi.", kb([back_btn(f"imp_srv_{ni}_{ci}")]))
        return

    _, _, currency = await fetch_api_balance(api["url"], api["key"])
    await state.update_data(
        imp_ni=ni, imp_ci=ci, imp_ai=ai,
        imp_services=services, imp_cats=cats, imp_currency=currency or "USD",
    )

    rows = [[InlineKeyboardButton(text=f"📂 {c}", callback_data=f"imp_cat_{ni}_{ci}_{ai}_{idx}")] for idx, c in enumerate(cats)]
    rows.append(back_btn(f"imp_srv_{ni}_{ci}"))
    await safe_edit(call.message, f"📂 Kategoriyani tanlang\nJami: {len(cats)} ta", kb(rows))


# --- 3) Tanlangan kategoriya ichidagi xizmatlarni sahifalab ko'rsatish
async def show_import_page(call_message, state: FSMContext, ni, ci, ai, cidx, page):
    data = await state.get_data()
    services = data.get("imp_services") or []
    cats = data.get("imp_cats") or []
    currency = data.get("imp_currency") or "USD"

    if ai >= len(db_data["apis"]) or cidx >= len(cats):
        await safe_edit(call_message, "⚠️ Ma'lumot eskirgan, qaytadan boshlang.", kb([back_btn(f"imp_srv_{ni}_{ci}")]))
        return

    cat = cats[cidx]
    items = import_cat_items(services, cat)
    total = len(items)
    if total == 0:
        await safe_edit(call_message, "⚠️ Bu kategoriyada xizmatlar topilmadi.", kb([back_btn(f"imp_api_{ni}_{ci}_{ai}")]))
        return

    pages = max(1, (total + IMPORT_PAGE_SIZE - 1) // IMPORT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * IMPORT_PAGE_SIZE:(page + 1) * IMPORT_PAGE_SIZE]
    await state.update_data(imp_ni=ni, imp_ci=ci, imp_ai=ai, imp_cidx=cidx, imp_page=page, imp_currency=currency)

    net = get_net(ni)
    cat_local = get_cat(net, ci) if net else None
    existing = set()
    if net and cat_local:
        api = db_data["apis"][ai]
        existing = {it["sid"] for it in get_items(net, cat_local) if it["api_url"] == api["url"]}

    rows = []
    for i, s in enumerate(chunk):
        sid = str(s.get("service"))
        mark = "✅ " if sid in existing else ""
        label = f"{mark}{sid} - {(s.get('name') or '')[:40]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"imp_one_{ni}_{ci}_{ai}_{cidx}_{page}_{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"imp_page_{ni}_{ci}_{ai}_{cidx}_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="imp_noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"imp_page_{ni}_{ci}_{ai}_{cidx}_{page + 1}"))
    if len(nav) > 1:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=f"📥 Shu sahifani yuklash ({len(chunk)} ta)", callback_data=f"imp_pg_{ni}_{ci}_{ai}_{cidx}_{page}")])
    rows.append([InlineKeyboardButton(text=f"📥 Barchasini yuklash ({total} ta)", callback_data=f"imp_all_{ni}_{ci}_{ai}_{cidx}")])
    rows.append(back_btn(f"imp_api_{ni}_{ci}_{ai}"))

    text = f"📋 {cat}\n{total} ta xizmat. Birini tanlang yoki barchasini yuklang:"
    await safe_edit(call_message, text, kb(rows))


@dp.callback_query(F.data.startswith("imp_cat_"))
async def import_pick_category(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai, cidx = int(parts[-4]), int(parts[-3]), int(parts[-2]), int(parts[-1])
    await show_import_page(call.message, state, ni, ci, ai, cidx, 0)
    await call.answer()


@dp.callback_query(F.data.startswith("imp_page_"))
async def import_change_page(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai, cidx, page = int(parts[-5]), int(parts[-4]), int(parts[-3]), int(parts[-2]), int(parts[-1])
    await show_import_page(call.message, state, ni, ci, ai, cidx, page)
    await call.answer()


@dp.callback_query(F.data == "imp_noop")
async def import_noop(call: types.CallbackQuery):
    await call.answer()


# --- 4) Bitta xizmatni qo'shish (ro'yxatdan bosilganda)
@dp.callback_query(F.data.startswith("imp_one_"))
async def import_add_one(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai, cidx, page, i = (int(p) for p in parts[-6:])

    data = await state.get_data()
    services = data.get("imp_services") or []
    cats = data.get("imp_cats") or []
    currency = data.get("imp_currency") or "USD"

    net = get_net(ni)
    cat_local = get_cat(net, ci) if net else None
    if net is None or cat_local is None or ai >= len(db_data["apis"]) or cidx >= len(cats):
        await call.answer("⚠️ Topilmadi, qaytadan boshlang", show_alert=True)
        return

    api = db_data["apis"][ai]
    items = import_cat_items(services, cats[cidx])
    chunk = items[page * IMPORT_PAGE_SIZE:(page + 1) * IMPORT_PAGE_SIZE]
    if i >= len(chunk):
        await call.answer("⚠️ Topilmadi", show_alert=True)
        return

    found = chunk[i]
    lst = db_data["items"].setdefault(item_key(net, cat_local), [])
    item = import_build_item(found, api["url"], currency)
    if any(it["sid"] == item["sid"] and it["api_url"] == api["url"] for it in lst):
        await call.answer("⚠️ Bu xizmat allaqachon qo'shilgan!", show_alert=True)
        return

    lst.append(item)
    save_data(db_data)
    await call.answer(f"✅ {item['name']} qo'shildi!", show_alert=True)
    await show_import_page(call.message, state, ni, ci, ai, cidx, page)


# --- 5) Sahifadagi yoki barcha xizmatlarni birdaniga qo'shish
async def import_bulk_add(call: types.CallbackQuery, state: FSMContext, ni, ci, ai, cidx, page_only):
    data = await state.get_data()
    services = data.get("imp_services") or []
    cats = data.get("imp_cats") or []
    currency = data.get("imp_currency") or "USD"

    net = get_net(ni)
    cat_local = get_cat(net, ci) if net else None
    if net is None or cat_local is None or ai >= len(db_data["apis"]) or cidx >= len(cats):
        await call.answer("⚠️ Topilmadi, qaytadan boshlang", show_alert=True)
        return

    api = db_data["apis"][ai]
    items = import_cat_items(services, cats[cidx])
    if page_only is not None:
        items = items[page_only * IMPORT_PAGE_SIZE:(page_only + 1) * IMPORT_PAGE_SIZE]

    lst = db_data["items"].setdefault(item_key(net, cat_local), [])
    existing = {(it["sid"], it["api_url"]) for it in lst}
    added = skipped = 0
    for found in items:
        item = import_build_item(found, api["url"], currency)
        key = (item["sid"], item["api_url"])
        if key in existing:
            skipped += 1
            continue
        lst.append(item)
        existing.add(key)
        added += 1

    save_data(db_data)
    msg = f"✅ {added} ta xizmat qo'shildi!"
    if skipped:
        msg += f"\n⚠️ {skipped} ta xizmat allaqachon mavjud edi."
    await call.answer(msg, show_alert=True)
    await show_admin_category(call.message, ni, ci)


@dp.callback_query(F.data.startswith("imp_pg_"))
async def import_load_page(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai, cidx, page = (int(p) for p in parts[-5:])
    await import_bulk_add(call, state, ni, ci, ai, cidx, page_only=page)


@dp.callback_query(F.data.startswith("imp_all_"))
async def import_load_all(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    ni, ci, ai, cidx = (int(p) for p in parts[-4:])
    await import_bulk_add(call, state, ni, ci, ai, cidx, page_only=None)


# ---------------------------------------------------------------- RUN
# ---------------------------------------------------------------- ADMIN: FOYDALANUVCHILAR
@dp.message(StateFilter(AdminState.waiting_for_btn_name))
async def process_button_name(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    key = d.get("key")
    if key not in BTN_KEYS:
        await state.clear()
        return
    back = kb([back_btn(f"bt_{key}")])
    orig, emoji_id, label = extract_emoji_and_label(message)
    new = orig.strip()
    if not new:
        await message.answer("❌ Nom bo'sh bo'lmasin:", reply_markup=back)
        return
    if len(new) > 60:
        await message.answer("❌ Nom juda uzun (60 belgigacha):", reply_markup=back)
        return

    set_button_name(key, new, emoji_id, label)
    save_data(db_data)
    await state.clear()
    text, markup = button_edit_view(key)
    await message.answer("✅ Tugma nomi yangilandi!\n\n" + text, reply_markup=markup, parse_mode="HTML")
    await message.answer("⌨️ Yangilangan menyu:", reply_markup=main_kb(message.from_user.id))


@dp.message(StateFilter(AdminState.waiting_for_info_text))
async def process_info_text(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    key = d.get("key")
    if key not in INFO_KEYS:
        await state.clear()
        return
    txt = (message.text or "").strip()
    if not txt:
        await message.answer("❌ Matn bo'sh bo'lmasin:", reply_markup=kb([back_btn(f"bt_{key}")]))
        return
    db_data["ui"]["texts"][key] = txt
    save_data(db_data)
    await state.clear()
    await message.answer("✅ Matn yangilandi!", reply_markup=kb([back_btn(f"bt_{key}")]))


def user_label(uid):
    u = db_data["users"].get(uid, {})
    info = u.get("info", {})
    name = " ".join(x for x in (info.get("first_name"), info.get("last_name")) if x)
    if not name:
        name = f"@{info['username']}" if info.get("username") else "Noma'lum"
    return f"{'🚫 ' if u.get('blocked') else ''}{name} ({uid})"


def user_card(uid):
    u = db_data["users"].get(uid)
    if not u:
        return "⚠️ Foydalanuvchi topilmadi.", kb([back_btn("usr_home")])
    info = u.get("info", {})
    ords = [o for o in db_data["orders"] if str(o.get("user_id")) == uid]
    spent = sum(o.get("price", 0) for o in ords)
    premium = "Ha" if info.get("is_premium") else "Yo'q"
    blocked = u.get("blocked", False)
    username = f"@{info['username']}" if info.get("username") else "—"
    text = (
        "👤 Foydalanuvchi ma'lumotlari\n\n"
        f"🆔 ID: {uid}\n"
        f"👤 Ism: {info.get('first_name') or '—'}\n"
        f"👥 Familiya: {info.get('last_name') or '—'}\n"
        f"🔗 Username: {username}\n"
        f"🌐 Til: {info.get('language_code') or '—'}\n"
        f"💎 Premium: {premium}\n"
        f"📅 Qo'shilgan: {info.get('joined') or '—'}\n\n"
        f"💰 Balans: {fmt_money(u.get('balance', 0))} so'm\n"
        f"💳 Jami to'ldirgan: {fmt_money(u.get('deposited', 0))} so'm\n"
        f"🛒 Buyurtmalar: {len(ords)} ta ({fmt_money(spent)} so'm)\n\n"
        f"📌 Holat: {'🚫 Bloklangan' if blocked else '✅ Faol'}"
    )
    rows = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data=f"usr_add_{uid}", style="success"),
         InlineKeyboardButton(text="➖ Ayirish", callback_data=f"usr_sub_{uid}", style="danger")],
        [InlineKeyboardButton(
            text="✅ Blokdan chiqarish" if blocked else "🚫 Bloklash",
            callback_data=f"usr_blk_{uid}",
            style="success" if blocked else "danger")],
        back_btn("usr_home"),
    ]
    return text, kb(rows)


def users_list_markup(uids):
    rows = [[InlineKeyboardButton(text=user_label(uid), callback_data=f"usr_view_{uid}")] for uid in uids]
    rows.append(back_btn("usr_home"))
    return kb(rows)


@dp.callback_query(F.data == "usr_home")
async def users_home_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id == OWNER_ID:
        await state.clear()
        await safe_edit(call.message, users_stats_text(), users_home_markup())
        await call.answer()


@dp.callback_query(F.data == "usr_last")
async def users_last_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    owner = str(OWNER_ID)
    uids = [k for k in db_data["users"].keys() if k != owner][-20:][::-1]
    if not uids:
        await safe_edit(call.message, "⚠️ Foydalanuvchilar mavjud emas!", kb([back_btn("usr_home")]))
    else:
        await safe_edit(call.message, f"📋 Oxirgi {len(uids)} ta foydalanuvchi:", users_list_markup(uids))
    await call.answer()


@dp.callback_query(F.data == "usr_search")
async def users_search_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminState.waiting_for_user_query)
    await safe_edit(
        call.message,
        "🔍 Foydalanuvchi ID raqami, username yoki ismini kiriting:\n\nMasalan: 8648727479 yoki @username",
        kb([back_btn("usr_home")]),
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_user_query))
async def process_user_query(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    q = (message.text or "").strip().lstrip("@").lower()
    if not q:
        return
    owner = str(OWNER_ID)
    found = []
    for uid, u in db_data["users"].items():
        if uid == owner:
            continue
        info = u.get("info", {})
        hay = " ".join(str(x or "") for x in (uid, info.get("first_name"), info.get("last_name"), info.get("username"))).lower()
        if q == uid or q in hay:
            found.append(uid)
    found = found[:20]
    if not found:
        await message.answer("❌ Foydalanuvchi topilmadi.\n\nBoshqa so'z kiriting:", reply_markup=kb([back_btn("usr_home")]))
        return
    await message.answer(f"🔍 Topildi: {len(found)} ta\n\nBoshqa qidirish uchun yana yozing:", reply_markup=users_list_markup(found))


@dp.callback_query(F.data.startswith("usr_view_"))
async def user_view_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    uid = call.data.split("_")[2]
    text, markup = user_card(uid)
    await safe_edit(call.message, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("usr_add_") | F.data.startswith("usr_sub_"))
async def user_balance_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    _, mode, uid = call.data.split("_")
    if uid not in db_data["users"]:
        await call.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_balance_amount)
    await state.update_data(uid=uid, mode=mode)
    word = "qo'shiladigan" if mode == "add" else "ayiriladigan"
    bal = fmt_money(db_data["users"][uid].get("balance", 0))
    await safe_edit(
        call.message,
        f"💰 {word.capitalize()} summani kiriting (so'mda):\n\n👛 Hozirgi balans: {bal} so'm\n\nMasalan: 10000",
        kb([back_btn(f"usr_view_{uid}")]),
    )
    await call.answer()


@dp.message(StateFilter(AdminState.waiting_for_balance_amount))
async def process_balance_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    uid, mode = d["uid"], d["mode"]
    back = kb([back_btn(f"usr_view_{uid}")])
    u = db_data["users"].get(uid)
    if not u:
        await state.clear()
        await message.answer("⚠️ Foydalanuvchi topilmadi.")
        return
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("❌ Faqat son kiriting. Masalan: 10000", reply_markup=back)
        return
    if amount <= 0:
        await message.answer("❌ Summa 0 dan katta bo'lishi kerak:", reply_markup=back)
        return

    if mode == "add":
        u["balance"] = u.get("balance", 0) + amount
        u["deposited"] = u.get("deposited", 0) + amount
        note = f"💰 Balansingiz {fmt_money(amount)} so'mga to'ldirildi.\n👛 Jami: {fmt_money(u['balance'])} so'm"
        result = f"✅ {fmt_money(amount)} so'm qo'shildi."
    else:
        if amount > u.get("balance", 0):
            await message.answer(
                f"❌ Balansda yetarli mablag' yo'q!\n👛 Hozirgi balans: {fmt_money(u.get('balance', 0))} so'm\n\nKichikroq summa kiriting:",
                reply_markup=back)
            return
        u["balance"] -= amount
        note = f"➖ Balansingizdan {fmt_money(amount)} so'm ayirildi.\n👛 Jami: {fmt_money(u['balance'])} so'm"
        result = f"✅ {fmt_money(amount)} so'm ayirildi."
    save_data(db_data)
    await state.clear()
    await notify_user(int(uid), note)

    text, markup = user_card(uid)
    await message.answer(f"{result}\n\n{text}", reply_markup=markup)


@dp.callback_query(F.data.startswith("usr_blk_"))
async def user_toggle_block(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    uid = call.data.split("_")[2]
    u = db_data["users"].get(uid)
    if not u or uid == str(OWNER_ID):
        await call.answer("Bu foydalanuvchini bloklab bo'lmaydi", show_alert=True)
        return
    u["blocked"] = not u.get("blocked", False)
    save_data(db_data)
    if not u["blocked"]:
        await notify_user(int(uid), "✅ Siz blokdan chiqarildingiz. Botdan yana foydalanishingiz mumkin.\n\n/start")
    text, markup = user_card(uid)
    await safe_edit(call.message, text, markup)
    await call.answer("🚫 Bloklandi" if u["blocked"] else "✅ Blokdan chiqarildi", show_alert=True)


async def notify_user(uid, text):
    if is_blocked(uid):
        return
    try:
        await bot.send_message(uid, text)
    except Exception as e:
        print("Foydalanuvchiga xabar yuborilmadi:", uid, e)


def order_head(o):
    return (
        f"🆔 Buyurtma ID: {shown_id(o)}\n"
        f"📦 Xizmat: {o['service']}\n"
        f"🔢 Miqdor: {o['quantity']} ta\n"
        f"🔗 Havola: {o['link']}"
    )


def mark_done(o):
    """Buyurtma «Bajarildi» bo'lgan vaqtni yozib qo'yadi (xizmat kartasidagi o'rtacha vaqt uchun)."""
    o["done_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def refresh_order(o):
    """Provayderdan buyurtma holatini olib, kerak bo'lsa yakunlaydi. Javob olinsa True qaytaradi."""
    api = find_api_by_url(o.get("api_url", ""))
    if not api:
        return False
    ok, resp = await api_post(api["url"], {
        "key": api["key"],
        "action": "status",
        "order": o["provider_order_id"],
    })
    if not ok or not isinstance(resp, dict) or "status" not in resp:
        return False
    if o.get("final"):  # kutish paytida (masalan admin tomonidan) yakunlab bo'lingan
        return True

    status = str(resp["status"])
    low = status.lower().strip()
    uid = o["user_id"]
    u = get_user(uid)
    head = order_head(o)

    if low == "completed":
        o["final"], o["status"] = True, status
        mark_done(o)
        save_data(db_data)
        await notify_user(uid, f"✅ Buyurtmangiz bajarildi!\n\n{head}")
    elif low in ("canceled", "cancelled", "fail", "failed", "refunded"):
        o["final"], o["status"] = True, status
        u["balance"] += o["price"]
        save_data(db_data)
        await notify_user(
            uid,
            f"❌ Buyurtmangiz bekor qilindi!\n\n{head}\n\n"
            f"💰 {fmt_money(o['price'])} so'm balansingizga qaytarildi.\n"
            f"👛 Balans: {fmt_money(u['balance'])} so'm",
        )
    elif low == "partial":
        try:
            remains = float(resp.get("remains", 0) or 0)
        except (TypeError, ValueError):
            remains = 0
        qty = o["quantity"] or 1
        refund = round(o["price"] * min(remains, qty) / qty, 2)
        o["final"], o["status"] = True, status
        u["balance"] += refund
        save_data(db_data)
        await notify_user(
            uid,
            f"⚠️ Buyurtmangiz qisman bajarildi!\n\n{head}\n\n"
            f"📉 Bajarilmagan: {int(remains)} ta\n"
            f"💰 {fmt_money(refund)} so'm balansingizga qaytarildi.\n"
            f"👛 Balans: {fmt_money(u['balance'])} so'm",
        )
    elif o.get("status") != status:
        o["status"] = status
        save_data(db_data)
    return True


# ---------------------------------------------------------------- ADMIN: BUYURTMALAR
ADM_ORD_PER_PAGE = 10
ADM_FILTER_LABELS = {"act": "⏳ Faol", "done": "✅ Bajarilgan", "can": "⛔ Bekor", "all": "📋 Barchasi"}
ADM_FILTER_TITLES = {"act": "Faol", "done": "Bajarilgan", "can": "Bekor", "all": "Barchasi"}


class AdminOrdState(StatesGroup):
    waiting_for_id = State()


def order_bucket(o):
    st = str(o.get("status") or "").lower().strip()
    if st in ("canceled", "cancelled", "fail", "failed", "refunded"):
        return "can"
    if st in ("completed", "partial"):
        return "done"
    return "act"


def order_icon(o):
    if str(o.get("status") or "").lower().strip() == "partial":
        return "⚠️"
    return {"act": "⏳", "done": "✅", "can": "❌"}[order_bucket(o)]


def get_order_by_id(oid):
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        return None
    for o in db_data["orders"]:
        if o.get("id") == oid:
            return o
    return None


def admin_orders_view(f, page):
    orders = db_data["orders"]
    counts = {"act": 0, "done": 0, "can": 0}
    for o in orders:
        counts[order_bucket(o)] += 1
    lst = [o for o in reversed(orders) if f == "all" or order_bucket(o) == f]
    pages = max(1, (len(lst) + ADM_ORD_PER_PAGE - 1) // ADM_ORD_PER_PAGE)
    page = min(max(page, 1), pages)
    chunk = lst[(page - 1) * ADM_ORD_PER_PAGE: page * ADM_ORD_PER_PAGE]

    text = (
        "🛍 <b>SMM buyurtmalari</b>\n\n"
        f"📦 Jami: <b>{len(orders)} ta</b>\n"
        f"⏳ Faol: <b>{counts['act']} ta</b>\n"
        f"✅ Bajarilgan: <b>{counts['done']} ta</b>\n"
        f"⛔ Bekor: <b>{counts['can']} ta</b>\n\n"
        f"🔎 Ko'rsatilmoqda: <b>{ADM_FILTER_TITLES[f]}</b> ({len(lst)} ta)"
    )
    if not chunk:
        text += "\n\n⚠️ Bu bo'limda buyurtmalar yo'q."

    fbtns = []
    for key in ("act", "done", "can", "all"):
        label = ADM_FILTER_LABELS[key]
        if key == f:
            label = ("☑️ " if key == "done" else "✅ ") + label
        fbtns.append(InlineKeyboardButton(text=label, callback_data=f"aord_l_{key}_1"))
    rows = grid(fbtns, 2)
    rows += grid([
        InlineKeyboardButton(text=f"#{o['id']} {order_icon(o)}", callback_data=f"aord_v_{o['id']}_{f}_{page}")
        for o in chunk
    ], 2)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"aord_l_{f}_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{pages}", callback_data="aord_noop"))
    if page < pages:
        nav.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"aord_l_{f}_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔍 ID bo'yicha qidirish", callback_data="aord_search")])
    rows.append([
        InlineKeyboardButton(text="🔄 Pending→Done", callback_data="aord_b_pend"),
        InlineKeyboardButton(text="🔄 Processing→Done", callback_data="aord_b_proc"),
    ])
    rows.append(back_btn("aord_close"))
    return text, kb(rows)


def admin_order_card(o, f, page):
    u = db_data["users"].get(str(o.get("user_id")), {})
    info = u.get("info", {})
    name = " ".join(x for x in (info.get("first_name"), info.get("last_name")) if x) or "—"
    username = f"@{info['username']}" if info.get("username") else "—"
    api = find_api_by_url(o.get("api_url", ""))
    try:
        dt = datetime.strptime(o["time"], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        dt = "—"
    try:
        price = f"{float(o.get('price', 0)):.2f}"
    except (TypeError, ValueError):
        price = "0.00"
    text = (
        f"🛒 <b>Buyurtma #{o['id']}</b>\n\n"
        f"🌐 Provayder: {escape(api_title(api)) if api else '—'}\n"
        f"🆔 Provayderdagi ID: <code>{escape(str(o.get('provider_order_id')))}</code>\n"
        f"👤 Foydalanuvchi: {escape(name)} ({escape(username)})\n"
        f"🆔 Telegram ID: <code>{o.get('user_id')}</code>\n"
        f"📅 Sana: {dt}\n"
        f"📋 Xizmat: {escape(str(o.get('service', '—')))}\n"
        f"🔗 Havola: {escape(str(o.get('link', '—')))}\n"
        f"🔢 Miqdor: {o.get('quantity')} ta\n"
        f"💰 Narx: {price} so'm\n"
        f"📊 Holat: {order_status_text(o)}"
    )
    tail = f"{o['id']}_{f}_{page}"
    rows = []
    if not o.get("final"):
        rows.append([
            InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"aord_a_done_{tail}", style="success"),
            InlineKeyboardButton(text="⛔ Bekor + pul qaytarish", callback_data=f"aord_c_{tail}", style="danger"),
        ])
    rows.append([InlineKeyboardButton(text="🔄 Provayderdan yangilash", callback_data=f"aord_a_sync_{tail}")])
    rows.append([InlineKeyboardButton(text="👤 Foydalanuvchi", callback_data=f"usr_view_{o.get('user_id')}")])
    rows.append(back_btn(f"aord_l_{f}_{page}"))
    return text, kb(rows)


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_orders"))
async def admin_orders_entry(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != OWNER_ID:
        return
    text, markup = admin_orders_view("all", 1)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@dp.callback_query(F.data.regexp(r"^aord_l_(all|act|done|can)_\d+$"))
async def admin_orders_list_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.clear()
    _, _, f, p = call.data.split("_")
    text, markup = admin_orders_view(f, int(p))
    await safe_edit(call.message, text, markup, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "aord_noop")
async def admin_orders_noop_cb(call: types.CallbackQuery):
    await call.answer()


@dp.callback_query(F.data == "aord_close")
async def admin_orders_close_cb(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id == OWNER_ID:
        try:
            await call.message.delete()
        except Exception:
            pass
    await call.answer()


@dp.callback_query(F.data.regexp(r"^aord_v_\d+_(all|act|done|can)_\d+$"))
async def admin_order_view_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.clear()
    _, _, oid, f, p = call.data.split("_")
    o = get_order_by_id(oid)
    if not o:
        await call.answer("⚠️ Buyurtma topilmadi", show_alert=True)
        return
    text, markup = admin_order_card(o, f, int(p))
    await safe_edit(call.message, text, markup, parse_mode="HTML", **NO_PREVIEW)
    await call.answer()


@dp.callback_query(F.data.regexp(r"^aord_c_\d+_(all|act|done|can)_\d+$"))
async def admin_order_cancel_ask_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    _, _, oid, f, p = call.data.split("_")
    o = get_order_by_id(oid)
    if not o or o.get("final"):
        await call.answer("ℹ️ Bu buyurtma allaqachon yakunlangan", show_alert=True)
        return
    tail = f"{oid}_{f}_{p}"
    await safe_edit(
        call.message,
        f"⚠️ #{oid} buyurtma bekor qilinadi va {fmt_money(o.get('price', 0))} so'm foydalanuvchi balansiga qaytariladi.\n\n"
        "Diqqat: bu faqat botda bekor qiladi. Provayderdagi buyurtma to'xtatilmaydi.\n\nDavom etasizmi?",
        kb([[
            InlineKeyboardButton(text="✅ Ha, qaytarilsin", callback_data=f"aord_a_cancel_{tail}", style="danger"),
            InlineKeyboardButton(text="◀ Yo'q", callback_data=f"aord_v_{tail}"),
        ]]),
    )
    await call.answer()


@dp.callback_query(F.data.regexp(r"^aord_a_(done|sync|cancel)_\d+_(all|act|done|can)_\d+$"))
async def admin_order_action_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    _, _, act, oid, f, p = call.data.split("_")
    o = get_order_by_id(oid)
    if not o:
        await call.answer("⚠️ Buyurtma topilmadi", show_alert=True)
        return

    toast, alert = None, False
    if act == "sync":
        try:
            got = await refresh_order(o)
        except Exception as e:
            print("Buyurtmani yangilashda xato:", e)
            got = False
        toast = "🔄 Yangilandi" if got else "⚠️ Provayderdan javob olinmadi"
    elif o.get("final"):
        toast, alert = "ℹ️ Bu buyurtma allaqachon yakunlangan", True
    elif act == "done":
        o["status"], o["final"] = "Completed", True
        mark_done(o)
        save_data(db_data)
        toast = "✅ Bajarildi deb belgilandi"
        await notify_user(o["user_id"], f"✅ Buyurtmangiz bajarildi!\n\n{order_head(o)}")
    else:  # cancel
        u = get_user(o["user_id"])
        u["balance"] = u.get("balance", 0) + o.get("price", 0)
        o["status"], o["final"] = "Canceled", True
        save_data(db_data)
        toast = "❌ Bekor qilindi, pul qaytarildi"
        await notify_user(
            o["user_id"],
            f"❌ Buyurtmangiz bekor qilindi!\n\n{order_head(o)}\n\n"
            f"💰 {fmt_money(o.get('price', 0))} so'm balansingizga qaytarildi.\n"
            f"👛 Balans: {fmt_money(u['balance'])} so'm",
        )

    text, markup = admin_order_card(o, f, int(p))
    await safe_edit(call.message, text, markup, parse_mode="HTML", **NO_PREVIEW)
    await call.answer(toast, show_alert=alert)


def bulk_orders(kind):
    names = ("pending",) if kind == "pend" else ("processing", "in progress")
    return [o for o in db_data["orders"]
            if not o.get("final") and str(o.get("status") or "").lower().strip() in names]


@dp.callback_query(F.data.regexp(r"^aord_b_(pend|proc)$"))
async def admin_orders_bulk_ask_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    kind = call.data.rsplit("_", 1)[1]
    n = len(bulk_orders(kind))
    if not n:
        await call.answer("ℹ️ Bunday buyurtmalar yo'q", show_alert=True)
        return
    name = "Pending" if kind == "pend" else "Processing"
    await safe_edit(
        call.message,
        f"⚠️ {n} ta «{name}» buyurtma «Bajarildi» deb belgilanadi va foydalanuvchilarga xabar yuboriladi.\n\n"
        "Diqqat: pul qaytarilmaydi va provayderdagi holat tekshirilmaydi.\n\nDavom etasizmi?",
        kb([[
            InlineKeyboardButton(text="✅ Ha, belgilansin", callback_data=f"aord_bgo_{kind}", style="success"),
            InlineKeyboardButton(text="◀ Yo'q", callback_data="aord_l_all_1"),
        ]]),
    )
    await call.answer()


@dp.callback_query(F.data.regexp(r"^aord_bgo_(pend|proc)$"))
async def admin_orders_bulk_go_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    targets = bulk_orders(call.data.rsplit("_", 1)[1])
    for o in targets:
        o["status"], o["final"] = "Completed", True
        mark_done(o)
    save_data(db_data)
    await call.answer(f"✅ {len(targets)} ta buyurtma belgilandi")
    for o in targets:
        await notify_user(o["user_id"], f"✅ Buyurtmangiz bajarildi!\n\n{order_head(o)}")
        await asyncio.sleep(0.05)
    text, markup = admin_orders_view("all", 1)
    await safe_edit(call.message, f"✅ {len(targets)} ta buyurtma «Bajarildi» deb belgilandi.\n\n" + text, markup, parse_mode="HTML")


@dp.callback_query(F.data == "aord_search")
async def admin_order_search_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.set_state(AdminOrdState.waiting_for_id)
    await safe_edit(call.message, "🔍 Buyurtma ID raqamini kiriting:\n\nMasalan: 21", kb([back_btn("aord_l_all_1")]))
    await call.answer()


@dp.message(StateFilter(AdminOrdState.waiting_for_id))
async def admin_order_search_msg(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    raw = (message.text or "").strip().lstrip("#")
    back = kb([back_btn("aord_l_all_1")])
    if not raw.isdigit():
        await message.answer("❌ Faqat son kiriting. Masalan: 21", reply_markup=back)
        return
    o = get_order_by_id(raw)
    if not o:
        await message.answer(f"❌ #{raw} raqamli buyurtma topilmadi.\n\nBoshqa ID kiriting:", reply_markup=back)
        return
    await state.clear()
    text, markup = admin_order_card(o, "all", 1)
    await message.answer(text, reply_markup=markup, parse_mode="HTML", **NO_PREVIEW)


# ---------------------------------------------------------------- ADMIN: OMMAVIY XABAR
class BroadcastState(StatesGroup):
    waiting_for_message = State()


BC_BAR_LEN = 14
_bc_job = None  # bir vaqtda bitta ommaviy xabar (xotirada saqlanadi)


def bc_targets():
    owner = str(OWNER_ID)
    return [int(k) for k, u in db_data["users"].items() if k != owner and not u.get("blocked")]


def bc_kind_label(kind, short=False):
    if kind == "forward":
        return "↪️ Forward" if short else "↪️ Forward xabar"
    return "💬 Oddiy" if short else "💬 Oddiy xabar"


def bc_active():
    return _bc_job is not None and _bc_job["status"] in ("running", "paused")


def bc_type_screen():
    text = (
        "📨 <b>Ommaviy xabar yuborish</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{len(bc_targets())} ta</b>\n\n"
        "📌 <b>Xabar turini tanlang:</b>\n\n"
        "<blockquote>💬 <b>Oddiy</b> — xabar botdan kelgandek (manba ko'rinmaydi).\n"
        "↪️ <b>Forward</b> — kim yuborgani ko'rinadi.</blockquote>"
    )
    markup = kb([
        [InlineKeyboardButton(text="💬 Oddiy xabar", callback_data="bc_t_copy")],
        [InlineKeyboardButton(text="↪️ Forward xabar", callback_data="bc_t_forward")],
        [InlineKeyboardButton(text="❌ Bekor", callback_data="bc_cancel", **sk("danger"))],
    ])
    return text, markup


def bc_status_text(job):
    total = len(job["targets"])
    done = job["sent"] + job["failed"]
    pct = 100.0 if total == 0 else done / total * 100
    filled = min(BC_BAR_LEN, int(pct / 100 * BC_BAR_LEN))
    bar = "▬" * filled + "▭" * (BC_BAR_LEN - filled)
    pct_txt = "100%" if pct >= 100 else f"{pct:.1f}%"
    state = {
        "running": "🟢 Yuborilmoqda",
        "paused": "⏸ Pauza",
        "done": "✅ Yakunlandi",
        "stopped": "🛑 To'xtatildi",
    }[job["status"]]
    live = job["status"] in ("running", "paused")
    lines = ["📨 <b>Ommaviy xabar</b>\n"]
    if live:
        lines.append(f"📌 <b>Turi:</b> {bc_kind_label(job['kind'])}")
    lines += [
        f"⚙️ <b>Holat:</b> {state}",
        f"✅ <b>Yuborildi:</b> {job['sent']} / {total} ta",
        f"❌ <b>Yuborilmadi:</b> {job['failed']} ta",
        f"⏳ <b>Jarayon:</b> {bar} {pct_txt}",
    ]
    if live:
        lines.append(f"\n🕒 {job['started']}")
    return "\n".join(lines)


def bc_status_markup(job):
    if job["status"] in ("done", "stopped"):
        return kb([[InlineKeyboardButton(text="🆕 Yangi xabar", callback_data="bc_new")]])
    mid = (InlineKeyboardButton(text="▶️ Davom ettirish", callback_data="bc_resume")
           if job["status"] == "paused"
           else InlineKeyboardButton(text="⏸ Pauza", callback_data="bc_pause"))
    return kb([
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="bc_refresh")],
        [mid],
        [InlineKeyboardButton(text="🗑 To'xtatish", callback_data="bc_stop")],
    ])


async def bc_edit_status(job):
    try:
        await bot.edit_message_text(
            bc_status_text(job),
            chat_id=job["chat_id"],
            message_id=job["message_id"],
            reply_markup=bc_status_markup(job),
            parse_mode="HTML",
        )
    except Exception:
        pass  # "message is not modified" va shunga o'xshash xatolar


async def bc_send_one(job, uid):
    for attempt in range(2):
        try:
            if job["kind"] == "forward":
                await bot.forward_message(uid, job["from_chat"], job["msg_id"])
            else:
                await bot.copy_message(uid, job["from_chat"], job["msg_id"])
            return True
        except Exception as e:
            wait = getattr(e, "retry_after", None)
            if wait and attempt == 0:  # Telegram "biroz kuting" desa, kutib qayta urinamiz
                await asyncio.sleep(wait + 1)
                continue
            return False
    return False


async def run_broadcast(job):
    loop = asyncio.get_running_loop()
    last_edit = 0.0
    try:
        while job["pos"] < len(job["targets"]):
            if job["status"] == "stopped":
                break
            if job["status"] == "paused":
                await asyncio.sleep(1)
                continue
            uid = job["targets"][job["pos"]]
            if await bc_send_one(job, uid):
                job["sent"] += 1
            else:
                job["failed"] += 1
            job["pos"] += 1
            if loop.time() - last_edit >= 3:
                last_edit = loop.time()
                await bc_edit_status(job)
            await asyncio.sleep(0.05)
        if job["status"] != "stopped":
            job["status"] = "done"
    except Exception as e:
        print("Ommaviy xabarda xato:", e)
        job["status"] = "stopped"
    finally:
        await bc_edit_status(job)


async def bc_show_start(target, edit=False):
    """Boshlang'ich oyna: yuborilayotgan xabar bo'lsa uning holati, bo'lmasa tur tanlash."""
    if bc_active():
        job = _bc_job
        text, markup = bc_status_text(job), bc_status_markup(job)
    else:
        job = None
        text, markup = bc_type_screen()
    if edit:
        await safe_edit(target, text, markup, parse_mode="HTML")
        msg = target
    else:
        msg = await target.answer(text, reply_markup=markup, parse_mode="HTML")
    if job:
        job["chat_id"], job["message_id"] = msg.chat.id, msg.message_id


@dp.message(not_renaming, lambda m: bool(m.text) and is_admin_btn(m.text, "adm_broadcast"))
async def bc_entry(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != OWNER_ID:
        return
    await bc_show_start(message)


@dp.callback_query(F.data == "bc_new")
async def bc_new_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.clear()
    await bc_show_start(call.message, edit=True)
    await call.answer()


@dp.callback_query(F.data == "bc_cancel")
async def bc_cancel_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    await state.clear()
    await safe_edit(call.message, "❌ Bekor qilindi.")
    await call.answer()


@dp.callback_query(F.data.regexp(r"^bc_t_(copy|forward)$"))
async def bc_type_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    if bc_active():
        await call.answer("⏳ Hozir boshqa xabar yuborilmoqda", show_alert=True)
        return
    kind = call.data.rsplit("_", 1)[1]
    await state.set_state(BroadcastState.waiting_for_message)
    await state.update_data(kind=kind)
    await safe_edit(
        call.message,
        "✍️ <b>Yubormoqchi bo'lgan xabaringizni yuboring</b>\n\n"
        "<i>Matn, rasm, video, tugma — istalgan xabar. Premium emoji va HTML saqlanadi.</i>",
        kb([[InlineKeyboardButton(text="❌ Bekor", callback_data="bc_cancel", **sk("danger"))]]),
        parse_mode="HTML",
    )
    await call.answer()


@dp.message(StateFilter(BroadcastState.waiting_for_message))
async def bc_message_received(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    d = await state.get_data()
    kind = d.get("kind", "copy")
    total = len(bc_targets())
    if not total:
        await state.clear()
        await message.answer("⚠️ Yuboriladigan foydalanuvchilar yo'q.")
        return
    # ko'rib chiqish uchun xabarning nusxasini adminning o'ziga yuboramiz
    try:
        if kind == "forward":
            await bot.forward_message(message.chat.id, message.chat.id, message.message_id)
        else:
            await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    except Exception as e:
        await message.answer(f"❌ Bu xabarni yuborib bo'lmaydi: {e}\n\nBoshqa xabar yuboring:")
        return
    await state.update_data(from_chat=message.chat.id, msg_id=message.message_id)
    await message.answer(
        "📨 <b>Yuborilsinmi?</b>\n\n"
        f"📌 Turi: {bc_kind_label(kind, short=True).split(' ')[0]} <b>{bc_kind_label(kind, short=True).split(' ', 1)[1]}</b>\n"
        f"👥 Jami: <b>{total} ta</b> foydalanuvchi",
        reply_markup=kb([[
            InlineKeyboardButton(text="✅ Ha, yuborish", callback_data="bc_go"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="bc_cancel", **sk("danger")),
        ]]),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "bc_go")
async def bc_go_cb(call: types.CallbackQuery, state: FSMContext):
    global _bc_job
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    if bc_active():
        await call.answer("⏳ Hozir boshqa xabar yuborilmoqda", show_alert=True)
        return
    d = await state.get_data()
    if "msg_id" not in d:
        await call.answer("⚠️ Xabar topilmadi, qaytadan boshlang", show_alert=True)
        return
    await state.clear()
    targets = bc_targets()
    job = {
        "kind": d.get("kind", "copy"),
        "from_chat": d["from_chat"],
        "msg_id": d["msg_id"],
        "targets": targets,
        "pos": 0,
        "sent": 0,
        "failed": 0,
        "status": "running",
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chat_id": call.message.chat.id,
        "message_id": call.message.message_id,
    }
    _bc_job = job
    await safe_edit(call.message, bc_status_text(job), bc_status_markup(job), parse_mode="HTML")
    job["task"] = asyncio.create_task(run_broadcast(job))
    await call.answer("🚀 Yuborish boshlandi")


@dp.callback_query(F.data == "bc_refresh")
async def bc_refresh_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    job = _bc_job
    if not job:
        await call.answer("ℹ️ Ma'lumot topilmadi", show_alert=True)
        return
    job["chat_id"], job["message_id"] = call.message.chat.id, call.message.message_id
    await safe_edit(call.message, bc_status_text(job), bc_status_markup(job), parse_mode="HTML")
    await call.answer("🔄 Yangilandi")


@dp.callback_query(F.data.regexp(r"^bc_(pause|resume|stop)$"))
async def bc_control_cb(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer()
        return
    job = _bc_job
    if not job or job["status"] not in ("running", "paused"):
        await call.answer("ℹ️ Yuborish allaqachon yakunlangan", show_alert=True)
        return
    act = call.data.rsplit("_", 1)[1]
    job["status"] = {"pause": "paused", "resume": "running", "stop": "stopped"}[act]
    job["chat_id"], job["message_id"] = call.message.chat.id, call.message.message_id
    await safe_edit(call.message, bc_status_text(job), bc_status_markup(job), parse_mode="HTML")
    await call.answer({"pause": "⏸ Pauza qilindi", "resume": "▶️ Davom etmoqda", "stop": "🛑 To'xtatildi"}[act])


# ---------------------------------------------------------------- ADMIN: STATISTIKA
def generate_full_statistics_text():
    users = db_data.get("users", {})
    orders = db_data.get("orders", [])
    apis = db_data.get("apis", [])
    items = db_data.get("items", {})
    
    total_users = len(users)
    active_users = sum(1 for u in users.values() if not u.get("blocked", False))
    paying_users = sum(1 for u in users.values() if u.get("deposited", 0) > 0)
    blocked_users = sum(1 for u in users.values() if u.get("blocked", False))
    
    total_user_balance = sum(u.get("balance", 0) for u in users.values())
    total_deposited = sum(u.get("deposited", 0) for u in users.values())
    
    total_orders = len(orders)
    completed_orders = sum(1 for o in orders if str(o.get("status", "")).lower() in ("completed", "bajarildi", "success"))
    pending_orders = sum(1 for o in orders if str(o.get("status", "")).lower() in ("pending", "in_progress", "processing", "kutilmoqda", "jarayonda"))
    cancelled_orders = sum(1 for o in orders if str(o.get("status", "")).lower() in ("canceled", "cancelled", "refunded", "bekor qilindi"))
    total_spent_on_orders = sum(o.get("price", 0) or o.get("cost", 0) for o in orders)
    
    now = datetime.now()
    today_prefix = now.strftime("%Y-%m-%d")
    today_users = sum(1 for u in users.values() if str(u.get("info", {}).get("joined", "")).startswith(today_prefix))
    
    total_services = sum(len(srvs) for srvs in items.values()) if items else 0
    total_apis = len(apis)

    text = (
        "╔═══════════════════════╗\n"
        "📊 <b>BOTNING MUKAMMAL JONLI STATISTIKASI</b>\n"
        "╚═══════════════════════╝\n\n"
        "👥 <b>Foydalanuvchilar Ko'rsatkichi:</b>\n"
        f" ├ 👤 Jami a'zolar: <b>{total_users:,}</b> ta\n"
        f" ├ 🟢 Faol foydalanuvchilar: <b>{active_users:,}</b> ta\n"
        f" ├ 💳 Xarid qilganlar: <b>{paying_users:,}</b> ta\n"
        f" ├ 🚫 Bloklanganlar: <b>{blocked_users:,}</b> ta\n"
        f" └ 🆕 Bugun qo'shilganlar: <b>+{today_users}</b> ta\n\n"
        "💰 <b>Moliya & Kassa Hisoboti:</b>\n"
        f" ├ 💵 Jami kiritilgan mablag': <b>{total_deposited:,}</b> so'm\n"
        f" ├ 💳 Foydalanuvchilar qoldig'i: <b>{total_user_balance:,}</b> so'm\n"
        f" └ 🛒 Buyurtmalarga sarflangan: <b>{total_spent_on_orders:,}</b> so'm\n\n"
        "📦 <b>SMM Buyurtmalar Holati:</b>\n"
        f" ├ 📊 Jami buyurtmalar: <b>{total_orders:,}</b> ta\n"
        f" ├ ✅ Bajarilgan: <b>{completed_orders:,}</b> ta\n"
        f" ├ ⏳ Jarayonda: <b>{pending_orders:,}</b> ta\n"
        f" └ ❌ Bekor qilingan: <b>{cancelled_orders:,}</b> ta\n\n"
        "⚙️ <b>Tizim & Server Holati:</b>\n"
        f" ├ 🔌 Ulangan SMM panellar: <b>{total_apis}</b> ta\n"
        f" ├ 🛍 Faol xizmatlar soni: <b>{total_services}</b> ta\n"
        " ├ 🌐 Xosting: <b>PythonAnywhere (24/7 Webhook)</b>\n"
        f" └ 🕒 Yangilangan vaqt: <code>{now.strftime('%d.%m.%Y %H:%M:%S')}</code>\n\n"
        "<i>Quyidagi tugmalar orqali batafsil tahlillarni ko'rishingiz mumkin:</i>"
    )
    return text


def statistics_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_stat_refresh"),
            InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="adm_stat_users_detail"),
        ],
        [
            InlineKeyboardButton(text="🛒 Oxirgi buyurtmalar", callback_data="adm_stat_orders_detail"),
            InlineKeyboardButton(text="🔌 Provayderlar", callback_data="adm_stat_apis_detail"),
        ],
        [
            InlineKeyboardButton(text="💰 Kassa tafsilotlari", callback_data="adm_stat_finance_detail"),
            InlineKeyboardButton(text="❌ Yopish", callback_data="adm_stat_close")
        ]
    ])


@dp.message(not_renaming, lambda m: bool(m.text) and (is_admin_btn(m.text, "adm_stats") or m.text in ("/stat", "/stats", "📊 Statistika", "Statistika")))
async def admin_stats_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != OWNER_ID:
        return
    text = generate_full_statistics_text()
    await message.answer(text, parse_mode="HTML", reply_markup=statistics_keyboard())


@dp.callback_query(F.data == "adm_stat_refresh")
async def admin_stat_refresh_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
    text = generate_full_statistics_text()
    try:
        await safe_edit(call.message, text, reply_markup=statistics_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await call.answer("🔄 Statistika yangilandi!")


@dp.callback_query(F.data == "adm_stat_users_detail")
async def admin_stat_users_detail_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
    
    users = db_data.get("users", {})
    sorted_by_balance = sorted(users.items(), key=lambda x: x[1].get("balance", 0), reverse=True)[:5]
    
    msg = "👥 <b>ENG YUQORI BALANSGA EGA FOYDALANUVCHILAR:</b>\n\n"
    if not sorted_by_balance:
        msg += "<i>Foydalanuvchilar hali mavjud emas.</i>\n"
    else:
        for idx, (uid, uinfo) in enumerate(sorted_by_balance, 1):
            uname = uinfo.get("info", {}).get("username")
            uname_str = f"@{uname}" if uname else f"ID: {uid}"
            bal = uinfo.get("balance", 0)
            dep = uinfo.get("deposited", 0)
            msg += f"<b>{idx}.</b> {uname_str} | 💰 {bal:,} so'm (Kiritgan: {dep:,} so'm)\n"
            
    msg += "\n🆕 <b>OXIRGI RO'YXATDAN O'TGANLAR:</b>\n"
    sorted_recent = sorted(users.items(), key=lambda x: str(x[1].get("info", {}).get("joined", "")), reverse=True)[:5]
    for idx, (uid, uinfo) in enumerate(sorted_recent, 1):
        uname = uinfo.get("info", {}).get("username")
        uname_str = f"@{uname}" if uname else f"ID: {uid}"
        j_time = uinfo.get("info", {}).get("joined", "Noma'lum")
        msg += f"<b>{idx}.</b> {uname_str} (Sana: {j_time})\n"

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Statistikaga qaytish", callback_data="adm_stat_refresh")]
    ])
    await safe_edit(call.message, msg, reply_markup=back_kb, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "adm_stat_orders_detail")
async def admin_stat_orders_detail_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
        
    orders = db_data.get("orders", [])
    recent_orders = orders[-5:] if orders else []
    
    msg = "🛒 <b>OXIRGI BUYURTMALAR TAHLILI:</b>\n\n"
    if not recent_orders:
        msg += "<i>Hozircha hech qanday buyurtma berilmagan.</i>\n"
    else:
        for o in reversed(recent_orders):
            oid = o.get("id", "—")
            srv = o.get("service_name") or o.get("name") or "Xizmat"
            cnt = o.get("count") or o.get("quantity") or 0
            prc = o.get("price") or o.get("cost") or 0
            st = o.get("status", "Noma'lum")
            msg += (
                f"🆔 <b>Buyurtma #{oid}</b>\n"
                f" • Xizmat: {srv}\n"
                f" • Miqdor: {cnt:,} dona\n"
                f" • Narx: {prc:,} so'm\n"
                f" • Holat: <code>{st}</code>\n"
                "─────────────────\n"
            )
            
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Statistikaga qaytish", callback_data="adm_stat_refresh")]
    ])
    await safe_edit(call.message, msg, reply_markup=back_kb, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "adm_stat_apis_detail")
async def admin_stat_apis_detail_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
        
    apis = db_data.get("apis", [])
    msg = "🔌 <b>ULANGAN SMM PROVAYDERLAR (API):</b>\n\n"
    if not apis:
        msg += "<i>Hali hech qanday provayder ulanmagan. Boshqaruv orqali API qo'shishingiz mumkin.</i>\n"
    else:
        for idx, a in enumerate(apis, 1):
            name = a.get("name", "Provayder")
            url = a.get("url", "—")
            msg += f"<b>{idx}.</b> <b>{name}</b>\n🔗 <code>{url}</code>\n\n"
            
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Statistikaga qaytish", callback_data="adm_stat_refresh")]
    ])
    await safe_edit(call.message, msg, reply_markup=back_kb, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "adm_stat_finance_detail")
async def admin_stat_finance_detail_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
        
    users = db_data.get("users", {})
    orders = db_data.get("orders", [])
    deposits = db_data.get("deposits", [])
    
    total_deposited = sum(u.get("deposited", 0) for u in users.values())
    total_user_balance = sum(u.get("balance", 0) for u in users.values())
    total_orders_price = sum(o.get("price", 0) or o.get("cost", 0) for o in orders)
    
    msg = (
        "💰 <b>MOLIYAVIY HISOBOT VA KASSA TAFSILOTLARI:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 <b>Jami kirim (Depozitlar):</b> {total_deposited:,} so'm\n"
        f"💳 <b>Foydalanuvchilar joriy balansi:</b> {total_user_balance:,} so'm\n"
        f"🛒 <b>Xizmatlarga ishlatilgan:</b> {total_orders_price:,} so'm\n"
        f"📊 <b>Muvaffaqiyatli to'lovlar soni:</b> {len(deposits)} ta\n\n"
        "<i>Barcha hisob-kitoblar doimiy data.json bazasidan real vaqtda olinadi.</i>"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Statistikaga qaytish", callback_data="adm_stat_refresh")]
    ])
    await safe_edit(call.message, msg, reply_markup=back_kb, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "adm_stat_close")
async def admin_stat_close_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("Ruxsat berilmagan!", show_alert=True)
    try:
        await call.message.delete()
    except Exception:
        await safe_edit(call.message, "✅ Statistika oynasi yopildi.")
    await call.answer()


async def check_orders_once():
    for o in list(db_data["orders"]):
        if o.get("final"):
            continue
        try:
            got = await refresh_order(o)
        except Exception as e:
            print("Buyurtma holatini olishda xato:", e)
            continue
        if got:
            await asyncio.sleep(0.5)


async def order_status_checker():
    while True:
        try:
            await check_orders_once()
        except Exception as e:
            print("Buyurtmalarni tekshirishda xato:", e)
        await asyncio.sleep(60)


# ---------------------------------------------------------------- YBANAT GURUHIGA AVTOMATIK XABARLAR (HAR 5 DAQIQADA)
def get_ybanat_cfg():
    cfg = db_data.setdefault("settings", {}).setdefault("group_broadcast", {})
    cfg.setdefault("enabled", True)
    cfg.setdefault("chat_id", -1004283476663)
    cfg.setdefault("chat_title", "Ybanat")
    cfg.setdefault("interval", 300)
    cfg.setdefault("message", (
        "🚀 <b>Muxa SMM — Tezkor va Arzon SMM Xizmatlari!</b>\n\n"
        "✨ <b>Telegram:</b> Obunachilar, ko'rishlar, reaksiyalar\n"
        "📸 <b>Instagram:</b> Obunachilar, layklar, video prosmotr\n"
        "🎬 <b>YouTube & TikTok:</b> 100% ishonchli va sifatli!\n\n"
        "⚡️ Qulay narxlar va 24/7 avtomatik yetkazib berish!\n\n"
        "👇 Buyurtma berish uchun botimizga kiring:\n"
        "👉 @Muxa_aibot"
    ))
    return cfg


async def send_ybanat_broadcast(bot: Bot):
    cfg = get_ybanat_cfg()
    if not cfg.get("enabled", True):
        return False, "O'chirilgan"
    chat_id = cfg.get("chat_id", -1004283476663)
    text = cfg.get("message")
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Botga kirish 🚀", url="https://t.me/Muxa_aibot?start=ybanat")]
        ]
    )
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        logging.info(f"Ybanat guruhiga 5 daqiqalik xabar yuborildi! msg_id={sent.message_id}")
        return True, sent.message_id
    except Exception as e:
        logging.error(f"Ybanat guruhiga xabar yuborishda xato: {e}")
        return False, str(e)


async def group_broadcast_loop(bot: Bot):
    # Bot ishga tushgach, birinchi xabarni 10 soniyadan so'ng yuboradi
    await asyncio.sleep(10)
    while True:
        try:
            cfg = get_ybanat_cfg()
            if cfg.get("enabled", True):
                await send_ybanat_broadcast(bot)
        except Exception as e:
            logging.error(f"group_broadcast_loop xatosi: {e}")

        cfg = get_ybanat_cfg()
        interval = max(30, int(cfg.get("interval", 300)))
        await asyncio.sleep(interval)


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_activity_listener(message: types.Message):
    # Agar bot Ybanat guruhida xabar ko'rsa, chat_id ni avto-yangilaydi
    chat = message.chat
    if "ybanat" in (chat.title or "").lower():
        cfg = get_ybanat_cfg()
        if cfg.get("chat_id") != chat.id:
            cfg["chat_id"] = chat.id
            cfg["chat_title"] = chat.title
            save_data(db_data)
            logging.info(f"Ybanat guruhi aniqlandi: {chat.title} ({chat.id})")


@dp.my_chat_member()
async def group_member_update_listener(event: types.ChatMemberUpdated):
    chat = event.chat
    if chat.type in ("group", "supergroup") and "ybanat" in (chat.title or "").lower():
        cfg = get_ybanat_cfg()
        cfg["chat_id"] = chat.id
        cfg["chat_title"] = chat.title
        save_data(db_data)
        logging.info(f"Bot Ybanat guruhiga a'zo bo'ldi/yangilandi: {chat.title} ({chat.id})")


# Admin boshqaruv buyruqlari
@dp.message(Command("ybanat"), F.chat.type == "private")
async def admin_ybanat_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    cfg = get_ybanat_cfg()
    status_text = "🟢 Yoqilgan (Faol)" if cfg.get("enabled", True) else "🔴 O'chirilgan"
    interval_min = cfg.get("interval", 300) // 60
    msg = (
        f"📢 <b>Ybanat Guruhi Avto-Xabar Tizimi</b>\n\n"
        f"Holat: <b>{status_text}</b>\n"
        f"Guruh: <b>{cfg.get('chat_title')}</b> (<code>{cfg.get('chat_id')}</code>)\n"
        f"Oraliq vaqt: <b>{interval_min} daqiqa</b> ({cfg.get('interval', 300)} soniya)\n\n"
        f"📝 <b>Joriy xabar matni:</b>\n"
        f"---------------------------\n"
        f"{cfg.get('message')}\n"
        f"---------------------------\n\n"
        f"⚙️ <b>Boshqaruv buyruqlari:</b>\n"
        f"• /ybanat_on — Avto-yuborishni yoqish\n"
        f"• /ybanat_off — Avto-yuborishni to'xtatish\n"
        f"• /ybanat_send_now — Hozir darhol xabar yuborish\n"
        f"• /set_ybanat_msg [matn] — Xabar matnini yangilash\n"
        f"• /ybanat_interval [soniya] — Oraliq vaqtni o'zgartirish (masalan: 300)"
    )
    await message.answer(msg, parse_mode="HTML")


@dp.message(Command("ybanat_on"), F.chat.type == "private")
async def admin_ybanat_on(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    cfg = get_ybanat_cfg()
    cfg["enabled"] = True
    save_data(db_data)
    await message.answer("✅ Ybanat guruhiga har 5 daqiqada xabar yuborish <b>YOQILDI</b>!", parse_mode="HTML")


@dp.message(Command("ybanat_off"), F.chat.type == "private")
async def admin_ybanat_off(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    cfg = get_ybanat_cfg()
    cfg["enabled"] = False
    save_data(db_data)
    await message.answer("⏸ Ybanat guruhiga avto-xabar yuborish <b>TO'XTATILDI</b>!", parse_mode="HTML")


@dp.message(Command("ybanat_send_now"), F.chat.type == "private")
async def admin_ybanat_send_now(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    status, res = await send_ybanat_broadcast(bot)
    if status:
        await message.answer(f"✅ Xabar Ybanat guruhiga yuborildi! (Xabar ID: {res})")
    else:
        await message.answer(f"❌ Xabar yuborishda xatolik yuz berdi:\n<code>{res}</code>", parse_mode="HTML")


@dp.message(Command("set_ybanat_msg"), F.chat.type == "private")
async def admin_ybanat_set_msg(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Yangi xabar matnini kiriting!\nMasalan: <code>/set_ybanat_msg Salom guruh!</code>", parse_mode="HTML")
        return
    new_text = parts[1].strip()
    cfg = get_ybanat_cfg()
    cfg["message"] = new_text
    save_data(db_data)
    await message.answer("✅ Ybanat guruhiga yuboriladigan xabar matni muvaffaqiyatli saqlandi!", parse_mode="HTML")


@dp.message(Command("ybanat_interval"), F.chat.type == "private")
async def admin_ybanat_set_interval(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("⚠️ Oraliq vaqtni soniyalarda kiriting (kamida 30 soniya)!\nMasalan: <code>/ybanat_interval 300</code> (5 daqiqa)", parse_mode="HTML")
        return
    sec = int(parts[1].strip())
    if sec < 30:
        sec = 30
    cfg = get_ybanat_cfg()
    cfg["interval"] = sec
    save_data(db_data)
    await message.answer(f"✅ Oraliq vaqt muvaffaqiyatli saqlandi: <b>{sec} soniya</b> ({sec // 60} daqiqa)!", parse_mode="HTML")



async def set_main_menu(bot: Bot):
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Qayta boshlash 🚀"),
            BotCommand(command="webapp", description="SMM Mini App 📱"),
        ])
    except Exception as e:
        print("set_my_commands xatosi:", e)
    try:
        await bot.set_chat_menu_button(
            menu_button=types.MenuButtonWebApp(
                text="SMM Do'kon 📱",
                web_app=types.WebAppInfo(url=WEBAPP_URL),
            )
        )
    except Exception as e:
        print("set_chat_menu_button xatosi:", e)


async def main():
    start_health_server_in_background()
    await set_main_menu(bot)
    checker_task = asyncio.create_task(order_status_checker())
    broadcast_task = asyncio.create_task(group_broadcast_loop(bot))
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        checker_task.cancel()
        broadcast_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
