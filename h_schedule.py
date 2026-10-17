"""دریافت برنامه از گروه، تشخیص ادیت، پیام فقط به افراد درگیر، تداخل‌ها."""
import logging
import time
from collections import defaultdict
from datetime import datetime
from html import escape

import config
import db
from analysis import (day_label, diff_entries, diff_notes, entry_line, find_conflicts,
                     next_weekday_on_or_after, now_tehran, sort_entries, strike_past,
                     tehran_timezone)
from app import BOT_MANAGER, MANAGER, SUP, bot, can_post, send
from schedule_parser import DAYS, entry_to_dict, key, load_list, normalize, parse_schedule

STUDIOS = load_list("data/studios.txt")
TEACHERS = load_list("data/teachers.txt")
REMOTE = load_list("data/remote_teachers.txt")


def known(kind, base):
    excluded = set(db.get_known_excluded(kind))
    names = {k: v for k, v in base.items() if k not in excluded}
    names.update({key(n): n for n in db.get_known(kind) if key(n) not in excluded})
    aliases = db.list_aliases(kind)
    if kind == "teacher":
        aliases += db.list_aliases("remote_teacher")
    for alias in aliases:
        if key(alias["canonical"]) not in excluded:
            names[key(alias["alias"])] = alias["canonical"]
    return names


@bot.message_handler(commands=["chatid"], chat_types=["group", "supergroup"])
def chatid(m):
    send(m.chat.id, str(m.chat.id))


@bot.message_handler(commands=["addteacher"], chat_types=["private"])
def addteacher(m):
    """برای استادهای چندکلمه‌ای: /addteacher بهرام پور  (از پیام/ادیت بعدی برنامه اعمال می‌شود)"""
    if not can_post(m.from_user.id):
        return
    name = normalize(m.text.partition(" ")[2].strip())
    if not name:
        send(m.chat.id, "مثال: /addteacher بهرام پور")
        return
    db.add_known("teacher", [name])
    send(m.chat.id, f"✅ «{name}» اضافه شد. از پیام/ادیت بعدی برنامه اعمال می‌شود.")


def _leaders(exclude_id):
    return [s for s in db.list_staff(roles=(MANAGER, SUP, BOT_MANAGER))
            if s["telegram_id"] and s["telegram_id"] != exclude_id]


def _message_source_date(m):
    sent_at = getattr(m, "date", None)
    if isinstance(sent_at, datetime):
        tz = tehran_timezone()
        sent_at = sent_at.replace(tzinfo=tz) if sent_at.tzinfo is None else sent_at.astimezone(tz)
        return sent_at.date().isoformat()
    if isinstance(sent_at, (int, float)) and sent_at > 0:
        return datetime.fromtimestamp(sent_at, tehran_timezone()).date().isoformat()
    return now_tehran().date().isoformat()


def ingest(m):
    if not getattr(m, "text", None) or not m.from_user or not can_post(m.from_user.id):
        return
    in_group = m.chat.id == config.GROUP_CHAT_ID
    if not (in_group or m.chat.type == "private"):  # پی‌وی فقط برای تست توسط فرستنده‌ی مجاز
        return
    remote = known("remote_teacher", REMOTE)
    res = parse_schedule(m.text, known("studio", STUDIOS), known("teacher", TEACHERS), remote)
    if len(res.entries) < config.MIN_PARSED_LINES:
        return  # شبیه برنامه نیست

    source_date = db.message_schedule_anchor(m.chat.id, m.message_id) or _message_source_date(m)
    _commit_schedule(m.chat.id, m.message_id, m.from_user.id, m.text, source_date, res)


def _commit_schedule(chat_id, message_id, sender_id, schedule_text, source_date, res):
    old_days = db.sheet_days_for_message(chat_id, message_id)
    days = list(res.days)
    days.extend(day for day in old_days if day not in res.days)
    blocks, conflicts, kinds = defaultdict(list), [], []
    for day in days:
        shift_date = db.sheet_schedule_date(chat_id, message_id, day)
        if not shift_date:
            shift_date = next_weekday_on_or_after(source_date, day)
        new = []
        for e in res.entries:
            if e.day == day:
                item = entry_to_dict(e)
                item["shift_date"] = shift_date
                new.append(item)
        old = db.latest_entries_for_sheet(chat_id, message_id, day)
        db.save_sheet(chat_id, message_id, sender_id, day, schedule_text, res.entries,
                      shift_date=shift_date, source_date=source_date)
        conflicts += find_conflicts(new)
        if old is None:
            by = defaultdict(list)
            for e in new:
                if e["person"]:
                    by[e["person"]].append(e)
            for p, es in by.items():
                blocks[p].append(
                    f"📅 برنامه‌ی جدید — {day_label(day, shift_date)}\n"
                    + "\n\n".join(entry_line(x, shift_date) for x in sort_entries(es)))
            kinds.append(f"{day_label(day, shift_date)}: جدید")
        else:
            rem, add, chg = diff_entries(old, new)
            for p, lines in diff_notes(rem, add, chg).items():
                blocks[p].append(f"✏️ ویرایش برنامه‌ی {day_label(day, shift_date)}\n\n"
                                 + "\n".join(strike_past(escape(line), shift_date) for line in lines))
            kinds.append(f"{day_label(day, shift_date)}: ویرایش "
                         f"({len(rem) + len(add) + len(chg)} تغییر)")
    logging.info("schedule ingested: %s", kinds)

    # فقط به افراد درگیر پیام می‌رود
    sent, unreg, unver = 0, set(), set()
    for p, bl in blocks.items():
        s = db.get_staff_by_name(p)
        if not s:
            unreg.add(p)
        elif not s["telegram_id"]:
            unver.add(p)
        elif send(s["telegram_id"], "\n\n".join(bl), parse_mode="HTML"):
            sent += 1
            time.sleep(0.05)
        else:
            unver.add(p)

    db.add_known("teacher", res.new_teachers)
    db.add_known("studio", res.new_studios)

    summary = f"✅ ثبت شد — {'، '.join(kinds)}\n✉️ پیام به {sent} نفر ارسال شد."
    if unver:
        summary += f"\n⏳ هنوز وارد بات نشده‌اند: {escape('، '.join(sorted(unver)))}"
    if unreg:
        summary += f"\n❓ در لیست پرسنل نیستند: {escape('، '.join(sorted(unreg)))}"
    if res.new_teachers:
        summary += ("\n\n👤 نام استادِ ثبت‌نشده یا نیازمند بررسی: "
                    + escape("، ".join(sorted(res.new_teachers)))
                    + "\nاگر اسم چندبخشی اشتباه تشخیص داده شده، نام کامل را در «مدیریت استادها» ثبت "
                    "و پیام برنامه را ویرایش کن؛ اگر درست است، همین نام تازه ذخیره شده است.")
    if res.new_studios:
        summary += ("\n\n🏢 استودیوی ثبت‌نشده یا نیازمند بررسی: "
                    + escape("، ".join(sorted(res.new_studios)))
                    + "\nاگر نام درست است ذخیره شده؛ اگر نه، برنامه را ویرایش و نام درست را "
                    "در «مدیریت استادها» اضافه کن.")
    if res.warnings:
        summary += f"\n⚠️ {len(res.warnings)} هشدار:\n" + "\n".join(
            f"- {escape(l)} ← {escape(why)}" for l, why in res.warnings[:15])
    if conflicts:
        summary += f"\n\n⛔ {len(conflicts)} تداخل:\n" + "\n".join(conflicts[:10])
    send(sender_id, summary, parse_mode="HTML")
    if conflicts:  # تداخل‌ها برای مسئول شیفت و سرپرست‌ها هم می‌رود
        text = f"⛔ {len(conflicts)} تداخل در برنامه:\n" + "\n".join(conflicts[:10])
        for s in _leaders(sender_id):
            send(s["telegram_id"], text, parse_mode="HTML")

@bot.message_handler(content_types=["text"], chat_types=["group", "supergroup"])
def on_group_message(m):
    ingest(m)


@bot.edited_message_handler(content_types=["text"], chat_types=["group", "supergroup", "private"])
def on_edit(m):
    ingest(m)


@bot.message_handler(content_types=["text"], chat_types=["private"])
def on_private_text(m):  # آخرین هندلر: متن‌های آزاد (برای تست برنامه در پی‌وی)
    ingest(m)
