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
TEACHER = "teacher"
ROLE_FA = {MANAGER: "مسئول شیفت", SUP: "سرپرست", ADMIN: "ادمین",
           BOT_MANAGER: "مدیر ربات", TEACHER: "استاد"}
ADMIN_ROLES = (ADMIN, BOT_MANAGER)


def staff_role_label(staff):
    if staff["role"] == BOT_MANAGER and staff["supervisor"]:
        return ROLE_FA[ADMIN]
    return ROLE_FA.get(staff["role"], staff["role"])

BTN_MY = "📋 شیفت‌های من"
BTN_TEACHER_CLASSES = "📚 کلاس‌های من"
BTN_TEACHER_ADMIN = "👤 ادمین کیه؟"
BTN_DAILY_NOTIFICATIONS = "🔔 اطلاع‌رسانی روزانه"
BTN_PROBLEM = "🆘 مشکل دارم"
BTN_HELPER = "🙋 کمکی کیه؟"
BTN_TEAM = "👥 تیم من"
BTN_NOTIFY = "📣 اطلاع به اساتید"
BTN_FREE = "🟢 چه کسی آزاده؟"
BTN_STATS = "📊 آمار"
BTN_STAFF_MANAGEMENT = "🧑‍💼 مدیریت پرسنل"
BTN_SCHEDULE_DATES = "📅 اصلاح تاریخ شیفت"
BTN_TEACHER_MANAGEMENT = "👩‍🏫 مدیریت استادها"
BTN_STUDIO_MANAGEMENT = "🏢 مدیریت استودیوها"
BTN_MODE = "🔄 تغییر حالت: مدیر ربات"
BTN_MODE_ADMIN = "🛡 تغییر حالت: ادمین"
BTN_NOTIFY = "📣 اطلاع به اساتید"
BTN_TEACHER_PHONES = "📞 شماره تماس اساتید"
BTN_BROADCAST = "📨 پیام به کاربران"
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
            context = staff_context(s)
            if roles and context["role"] not in roles:
                send(m.chat.id, "این دستور برای نقش تو نیست.")
                return
            return fn(m, context, *a, **k)
        return wrapper
    return deco


def staff_context(staff):
    value = dict(staff)
    if value["role"] in (ADMIN, TEACHER):
        assignment = db.get_teacher_assignment(value["name"])
        if assignment and assignment.get("phone") and assignment["phone"] == value.get("phone"):
            value["role"] = TEACHER
    admin_mode = (staff["role"] == BOT_MANAGER
                  and db.get_setting(f"manager_mode:{staff['telegram_id']}") == "admin")
    if admin_mode:
        value["_admin_mode"] = True
        value["role"] = ADMIN
    return value


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


def daily_notifications_enabled(staff_id):
    return db.get_setting(f"daily_notifications:{staff_id}") != "off"


def menu_for(s):
    s = dict(s)
    s = staff_context(s) if not s.get("_admin_mode") else s
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    mode = bool(s.get("_admin_mode"))
    rows = ([(BTN_TEACHER_CLASSES,), (BTN_TEACHER_ADMIN,)]
            if s["role"] == TEACHER else [(BTN_MY,)])
    notification_state = "روشن" if daily_notifications_enabled(s["id"]) else "خاموش"
    rows.append((f"{BTN_DAILY_NOTIFICATIONS}: {notification_state}",))
    rows.append((BTN_TEACHER_PHONES,))
    if s["role"] != TEACHER:
        if mode or s["role"] == ADMIN:
            rows.append((BTN_PROBLEM, BTN_HELPER))
        elif s["role"] == SUP:
            rows.append((BTN_PROBLEM, BTN_HELPER))
            rows.extend(((BTN_TEAM, BTN_NOTIFY), (BTN_FREE, BTN_STATS)))
        elif s["role"] == BOT_MANAGER:
            rows.append((BTN_MODE_ADMIN,))
            rows.extend(((BTN_TEAM, BTN_NOTIFY), (BTN_FREE, BTN_STATS)))
            rows.append((BTN_BROADCAST,))
        elif s["role"] == MANAGER:
            rows.append((BTN_PROBLEM, BTN_HELPER))
            rows.extend(((BTN_FREE, BTN_STATS), (BTN_NOTIFY,)))
        elif s["role"] == ADMIN:
            rows.append((BTN_PROBLEM, BTN_HELPER))
    if mode:
        rows.append((BTN_MODE,))
    if s["role"] in (MANAGER, BOT_MANAGER) and not mode:
        rows.append((BTN_STAFF_MANAGEMENT,))
    if s["telegram_id"] == OWNER_TELEGRAM_ID and not mode:
        rows.extend(((BTN_SCHEDULE_DATES,), (BTN_TEACHER_MANAGEMENT, BTN_STUDIO_MANAGEMENT)))
    for row in rows:
        kb.row(*row)
    return kb
