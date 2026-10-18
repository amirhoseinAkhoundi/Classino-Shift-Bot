"""ربات شیفت کلاسینو — نقطه‌ی ورود."""
import logging
import os, io, csv, sqlite3

OWNER_ID = int(os.getenv("OWNER_ID", "309425613"))  # آیدی عددی مالک
DB_PATH = "/data/bot.db"  # باید داخل ولوم باشه

def is_owner(user_id):
    return user_id == OWNER_ID

import app  # noqa: F401  (لاگ، پروکسی و نمونه‌ی بات)
import config
import db
# ترتیب import = اولویت هندلرها؛ h_schedule (هندلر عمومیِ متن) باید آخر باشد
import h_staff, h_manage, h_teachers, h_ops, h_admin,h_import, h_schedule  # noqa: E401,F401


def load_staff():
    db.ensure_owner(app.OWNER_TELEGRAM_ID)
    if len(db.list_staff()) <= 1:
        logging.warning("فقط مالک ثبت است؛ فایل CSV پرسنل را در پی‌وی بات بفرست.")


def main():
    db.init_db()
    load_staff()
    if not config.GROUP_CHAT_ID:
        logging.warning("GROUP_CHAT_ID خالی است: پیام‌های گروه نادیده گرفته می‌شود (در گروه /chatid بزن).")
    logging.info("bot started")
    app.bot.infinity_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()
