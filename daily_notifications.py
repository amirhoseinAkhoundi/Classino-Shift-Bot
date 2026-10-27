"""Daily per-user class and shift reminders."""
import time
from datetime import date
from html import escape

import db
from analysis import day_label, day_of, entry_line, now_tehran, sort_entries
from app import TEACHER, daily_notifications_enabled, send, staff_context


def send_daily_notifications(target_date=None):
    target_date = target_date or now_tehran().date().isoformat()
    scheduled_day = day_of(date.fromisoformat(target_date))
    sent = 0

    for staff in db.list_staff():
        telegram_id = staff["telegram_id"]
        if not telegram_id or not daily_notifications_enabled(staff["id"]):
            continue
        sent_key = f"daily_notifications_sent:{staff['id']}"
        if db.get_setting(sent_key) == target_date:
            continue

        context = staff_context(staff)
        entries = (db.entries_for_teacher(staff["name"], target_date)
                   if context["role"] == TEACHER
                   else db.entries_for_person(staff["name"], target_date))
        entries = sort_entries(entries)
        date_label = day_label(scheduled_day, target_date)
        if context["role"] == TEACHER:
            heading = (f"🔔 استاد {escape(staff['name'])} گرامی، برنامه‌ی کلاس‌های امروز شما "
                       f"({date_label}):")
            empty_message = "برای امروز کلاسی در برنامه ثبت نشده است."
            lines = [entry_line(entry, target_date, with_person=True) for entry in entries]
        else:
            heading = f"🔔 {escape(staff['name'])} گرامی، برنامه‌ی امروز شما ({date_label}):"
            empty_message = "برای امروز برنامه‌ای برای شما ثبت نشده است."
            lines = [entry_line(entry, target_date) for entry in entries]

        body = "\n\n".join(lines) if lines else empty_message
        if send(telegram_id, f"{heading}\n\n{body}", parse_mode="HTML"):
            db.set_setting(sent_key, target_date)
            sent += 1

    return sent


def run_daily_notifications():
    while True:
        now = now_tehran()
        if now.hour >= 4:
            send_daily_notifications(now.date().isoformat())
        time.sleep(60)
