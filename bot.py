"""ربات شیفت کلاسینو — نقطه‌ی ورود."""
import logging
import os
import threading

import app  # noqa: F401  (لاگ، پروکسی و نمونه‌ی بات)
import config
import db

from daily_notifications import run_daily_notifications
# ترتیب import = اولویت هندلرها؛ h_schedule (هندلر عمومیِ متن) باید آخر باشد
import h_staff, h_manage, h_teachers, h_ops, h_admin, h_broadcast, h_teacherphone, h_schedule # noqa: E401,F401


def load_staff():
    """data/staff.csv را (اگر هست) در دیتابیس ثبت می‌کند؛ هر بار اجرا امن و تکرارپذیر است."""
    from scripts.seed_staff import seed
    path = "data/staff.csv"
    if os.path.exists(path):
        logging.info("پرسنل از %s بارگذاری شد: %s نفر", path, seed(path))
    elif not db.list_staff():
        logging.warning("هیچ پرسنلی ثبت نشده و data/staff.csv هم نیست؛ هیچ‌کس نمی‌تواند وارد شود.")


def main():
    db.init_db()
    load_staff()
    if not config.GROUP_CHAT_ID:
        logging.warning("GROUP_CHAT_ID خالی است: پیام‌های گروه نادیده گرفته می‌شود (در گروه /chatid بزن).")
    threading.Thread(target=run_daily_notifications, name="daily-notifications", daemon=True).start()
    logging.info("bot started")
    app.bot.infinity_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()
