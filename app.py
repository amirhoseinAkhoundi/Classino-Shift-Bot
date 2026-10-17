"""نمونه‌ی مشترک بات و ابزارهای عمومی."""
import functools
import logging
import os
from logging.handlers import RotatingFileHandler

import telebot
from telebot import apihelper, types

import config
import db
from schedule_parser import normalize

MANAGER, SUP, ADMIN, BOT_MANAGER = "shift_manager", "supervisor", "admin", "bot_manager"
ROLE_FA = {MANAGER: "مسئول شیفت", SUP: "سرپرست", ADMIN: "ادمین",
           BOT_MANAGER: "مدیر ربات"}
ADMIN_ROLES = (ADMIN, BOT_MANAGER)


def staff_role_label(staff):
    if staff["role"] == BOT_MANAGER and staff["supervisor"]:
        return ROLE_FA[ADMIN]
    return ROLE_FA.get(staff["role"], staff["role"])

BTN_MY = "📋 شیفت‌های من"
BTN_PROBLEM = "🆘 مشکل دارم"
BTN_HELPER = "🙋 کمکی کیه؟"
BTN_TEAM = "👥 تیم من"
BTN_NOTIFY = "📣 اطلاع به اساتید"
BTN_FREE = "🟢 چه کسی آزاده؟"
BTN_STATS = "📊 آمار"
BTN_STAFF_MANAGEMENT = "🧑‍💼 مدیریت پرسنل"
BTN_SCHEDULE_DATES = "📅 اصلاح تاریخ شیفت"
BTN_TEACHER_MANAGEMENT = "👩‍🏫 مدیریت استادها"
OWNER_TELEGRAM_ID = 309425613

_log_dir = os.path.dirname(config.DB_PATH) or "."
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              RotatingFileHandler(os.path.join(_log_dir, "bot.log"), maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")],
)

# if config.PROXY_URL:
    # apihelper.proxy = {"http": config.PROXY_URL, "https": config.PROXY_URL}


class _ErrorHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        logging.error("handler error", exc_info=exception)
        return True


bot = telebot.TeleBot(config.BOT_TOKEN, exception_handler=_ErrorHandler())


# ---------- ارسال ----------
def _split(text, n=4000):
    if len(text) <= n:
        return text, ""
    cut = text.rfind("\n", 0, n)
    if cut <= 0:
        cut = n
    return text[:cut], text[cut:].lstrip("\n")


def send(chat_id, text, **kw):
    """ارسال امن؛ True/False برمی‌گرداند (مثلاً False اگر کاربر بات را بلاک کرده یا /start نزده)."""
    try:
        while text:
            chunk, text = _split(text)
            bot.send_message(chat_id, chunk, **(kw if not text else {}))
        return True
    except Exception as e:  # noqa
        logging.warning("send failed to %s: %s", chat_id, e)
        return False


# ---------- دسترسی ----------
def staff_only(*roles):
    """دکوراتور: کاربر باید تایید هویت‌شده باشد و (اگر roles داده شود) نقش مجاز داشته باشد."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(m, *a, **k):
            s = db.get_staff_by_tid(m.from_user.id)
            if not s:
                send(m.chat.id, "اول در پی‌وی بات با /start هویتت را تایید کن.")
                return
            if roles and s["role"] not in roles:
                send(m.chat.id, "این دستور برای نقش تو نیست.")
                return
            return fn(m, s, *a, **k)
        return wrapper
    return deco


def can_post(user_id) -> bool:
    if config.SCHEDULE_SENDER_IDS:
        return user_id in config.SCHEDULE_SENDER_IDS
    s = db.get_staff_by_tid(user_id)
    return bool(s and s["role"] in config.SCHEDULE_ROLES)


# ---------- ابزار ----------
def normalize_phone(p: str) -> str:
    return "".join(ch for ch in normalize(p) if ch.isdigit())[-10:]


def phone_display(s) -> str:
    return ("0" + s["phone"]) if s and s["phone"] else "—"


def phone_of(name) -> str:
    return phone_display(db.get_staff_by_name(name))


def menu_for(s):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    rows = [
        (BTN_MY,),
        (BTN_PROBLEM, BTN_HELPER),
    ]
    if s["role"] == SUP:
        rows.extend(((BTN_TEAM, BTN_NOTIFY), (BTN_FREE, BTN_STATS)))
    elif s["role"] == BOT_MANAGER:
        rows.extend(((BTN_TEAM, BTN_NOTIFY), (BTN_FREE, BTN_STATS)))
    elif s["role"] == MANAGER:
        rows.extend(((BTN_FREE, BTN_STATS), (BTN_NOTIFY,)))
    if s["role"] in (MANAGER, BOT_MANAGER):
        rows.append((BTN_STAFF_MANAGEMENT,))
    if s["telegram_id"] == OWNER_TELEGRAM_ID:
        rows.extend(((BTN_SCHEDULE_DATES,), (BTN_TEACHER_MANAGEMENT,)))
    for row in rows:
        kb.row(*row)
    return kb
