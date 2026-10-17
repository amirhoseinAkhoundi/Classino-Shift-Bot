import os
import re

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip("'\"")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN خالی است: فایل .env (نه .env.example) را کنار bot.py بساز و BOT_TOKEN=... را بدون کوت بنویس.")


def _int(name):
    v = os.getenv(name, "").strip()
    return int(v) if re.fullmatch(r"-?\d+", v) else 0


# می‌تواند خالی باشد تا با /chatid آیدی گروه را بگیرید؛ تا آن موقع پیام گروه نادیده گرفته می‌شود.
GROUP_CHAT_ID = _int("GROUP_CHAT_ID")
# گروهی که پیام «مشکل/کمک» در آن ارسال می‌شود (اگر خالی باشد همان گروه برنامه)
OPS_GROUP_ID = _int("OPS_GROUP_ID")

# پروکسی محلی فیلترشکن، مثلاً socks5://127.0.0.1:10808 (روی سرور خارج از ایران لازم نیست)
PROXY_URL = os.getenv("PROXY_URL", "").strip()

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
SCHEDULE_ROLES = {
    "bot_manager" if role.strip() == "database_admin" else role.strip()
    for role in os.getenv("SCHEDULE_ROLES", "shift_manager,supervisor,bot_manager").split(",")
}
SCHEDULE_SENDER_IDS = {int(x) for x in os.getenv("SCHEDULE_SENDER_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
MIN_PARSED_LINES = int(os.getenv("MIN_PARSED_LINES", "5"))
