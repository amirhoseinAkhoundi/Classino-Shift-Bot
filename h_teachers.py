"""اطلاع‌رسانی سرپرست‌ها به گروه اساتید."""
import re
import time
from collections import defaultdict
from datetime import timedelta
from html import escape

from telebot import types

import db
from analysis import day_label, now_tehran, sort_entries, span, text_line, today_tomorrow
from app import BTN_NOTIFY, BOT_MANAGER, SUP, bot, send, staff_only
from analysis import resolve_day
from schedule_parser import key, normalize

pending = {}  # telegram_id -> [(chat_id, teacher, text)]


def compose(teacher, username, day, items, label=None, target_date=None, teacher_id=None):
    shift_date = target_date or next((e.get("shift_date") for e in items if e.get("shift_date")), None)
    lines = [text_line(
        f"{span(e)} | {e['course'] or ''} | {('استودیو ' + e['studio']) if e['studio'] else 'آنلاین'}",
        shift_date,
    ) for e in sort_entries(items)]
    day_text = day_label(day, shift_date)
    title = f"فردا ({day_text})" if label == "فردا" else (
        f"امروز ({day_text})" if label == "امروز" else day_text)
    teacher_mention = (f'<a href="tg://user?id={int(teacher_id)}">{escape(teacher)} عزیز</a>'
                       if teacher_id else f"{escape(teacher)} عزیز، @{escape(username)}")
    return (f"📢 یادآوری کلاسینو\nاستاد {teacher_mention}، {title} کلاس دارید:\n"
            + "\n\n" + "\n".join(lines))


def build_batches(s, day, label=None, target_date=None):
    entries = db.entries_on_date(target_date, day=day, kind="class") if target_date else db.active_entries(day)
    es = [e for e in entries if e["kind"] == "class" and e["teacher"]]
    by = defaultdict(list)
    for e in es:
        by[key(e["teacher"])].append(e)
    groups = {g["teacher_key"]: g for g in db.list_teacher_groups()}
    assignments = {a["teacher_key"]: a for a in db.list_teacher_assignments()}
    msgs, no_group = [], []
    for k, items in by.items():
        teacher = items[0]["teacher"]
        assignment = assignments.get(k)
        if s["role"] == SUP and (not assignment or assignment["supervisor"] != s["name"]):
            continue
        group = groups.get(k)
        if not assignment or not (assignment["telegram_id"] or assignment["username"]) or not group:
            no_group.append(teacher)
            continue
        msgs.append((group["chat_id"], teacher,
                     compose(teacher, assignment["username"], day, items, label, target_date,
                             assignment["telegram_id"])))
    return msgs, sorted(no_group)


@bot.message_handler(commands=["assignteacher"], chat_types=["private"])
@staff_only(BOT_MANAGER)
def assign_teacher(m, s):
    parts = [normalize(part).strip() for part in m.text.partition(" ")[2].split("|")]
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        send(m.chat.id, "مثال: /assignteacher نام استاد | نام سرپرست | @username")
        return
    teacher, supervisor_name, username = parts
    supervisor = db.get_staff_by_name(supervisor_name)
    username = username.removeprefix("@")
    if not supervisor or supervisor["role"] != SUP:
        send(m.chat.id, f"«{supervisor_name}» سرپرست ثبت‌شده نیست.")
        return
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        send(m.chat.id, "نام کاربری تلگرام معتبر نیست؛ مثل @teacher_name واردش کن.")
        return
    db.assign_teacher(teacher, supervisor["name"], username)
    db.add_known("teacher", [teacher])
    send(m.chat.id, f"✅ استاد «{teacher}» به سرپرستی {supervisor['name']} ثبت شد؛ اعلان‌ها با @{username} منشن می‌شوند.")


@bot.message_handler(commands=["linkteacher"], chat_types=["group", "supergroup"])
@staff_only(SUP, BOT_MANAGER)
def link(m, s):
    """داخل گروه استاد: سرپرست همان استاد یا مدیر ربات می‌تواند /linkteacher نام استاد بزند."""
    name = normalize(m.text.partition(" ")[2]).strip()
    if not name:
        send(m.chat.id, "مثال: /linkteacher فکری")
        return
    assignment = db.get_teacher_assignment(name)
    if not assignment:
        send(m.chat.id, "این استاد هنوز توسط مدیر ربات به سرپرست و نام کاربری تلگرام وصل نشده.")
        return
    if s["role"] == SUP and assignment["supervisor"] != s["name"]:
        send(m.chat.id, "فقط سرپرست همان استاد یا مدیر ربات می‌تواند گروهش را ثبت کند.")
        return
    db.link_teacher(assignment["teacher"], m.chat.id, assignment["supervisor"])
    send(m.chat.id, f"✅ گروه استاد «{assignment['teacher']}» (سرپرست: {assignment['supervisor']}) وصل شد.")


@bot.message_handler(commands=["registerteacher"], chat_types=["group", "supergroup"])
@staff_only(SUP, BOT_MANAGER)
def register_teacher(m, s):
    reply = m.reply_to_message
    teacher_user = reply.from_user if reply else None
    if not teacher_user or teacher_user.is_bot:
        send(m.chat.id, "روی یکی از پیام‌های خود استاد ریپلای کن و /registerteacher بزن.")
        return

    group = next((g for g in db.list_teacher_groups() if g["chat_id"] == m.chat.id), None)
    if not group:
        send(m.chat.id, "این گروه به استادی وصل نیست؛ ابتدا /linkteacher نام استاد را اجرا کن.")
        return
    assignment = db.get_teacher_assignment_by_key(group["teacher_key"])
    if not assignment:
        send(m.chat.id, "این استاد هنوز به سرپرست تخصیص داده نشده؛ مدیر ربات باید ثبتش کند.")
        return
    if s["role"] == SUP and assignment["supervisor"] != s["name"]:
        send(m.chat.id, "فقط سرپرست همان استاد یا مدیر ربات می‌تواند شناسه‌اش را ثبت کند.")
        return
    if not db.set_teacher_telegram_id(assignment["teacher"], teacher_user.id):
        send(m.chat.id, "ذخیره‌ی شناسه انجام نشد؛ دوباره تلاش کن یا با مدیر ربات هماهنگ کن.")
        return

    username = f" (@{teacher_user.username})" if teacher_user.username else ""
    send(m.chat.id, f"✅ شناسه‌ی تلگرام استاد «{assignment['teacher']}»{username} ثبت شد؛ "
                    "یادآوری‌های بعدی او را مستقیم منشن می‌کنند.")


@bot.message_handler(commands=["groups"], chat_types=["private"])
@staff_only(SUP, BOT_MANAGER)
def groups(m, s):
    gs = db.list_teacher_groups()
    assignments = db.list_teacher_assignments()
    if s["role"] == SUP:
        gs = [g for g in gs if g["supervisor"] == s["name"]]
        assignments = [a for a in assignments if a["supervisor"] == s["name"]]
    linked = {g["teacher_key"] for g in db.list_teacher_groups()}
    missing = sorted(a["teacher"] for a in assignments if a["teacher_key"] not in linked)
    t = "\n".join(f"• {g['teacher']}" + (f" ← {g['supervisor']}" if g["supervisor"] else "") for g in gs) or "—"
    assigned = "\n".join(
        f"• {a['teacher']} — {a['supervisor']} — @{a['username'] or 'نام کاربری ثبت نشده'}"
        for a in assignments
    ) or "—"
    send(m.chat.id, f"🔗 گروه‌های وصل‌شده ({len(gs)}):\n{t}\n\n"
                    f"👩‍🏫 استادهای تخصیص‌یافته ({len(assignments)}):\n{assigned}\n\n"
                    "❓ استادهای تخصیص‌یافته که گروه ندارند:\n" + ("، ".join(missing) or "—"))


def _start_notify(m, s, day, label, target_date=None):
    msgs, no_group = build_batches(s, day, label, target_date)
    if not msgs:
        send(m.chat.id, f"برای {day} پیامی برای ارسال نیست." +
             (f"\nبدون تخصیص، نام کاربری یا گروه: {'، '.join(no_group)}" if no_group else ""))
        return
    pending[m.from_user.id] = msgs
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ ارسال", callback_data="nt:go"),
           types.InlineKeyboardButton("❌ لغو", callback_data="nt:no"))
    shift_date = target_date or next((entry.get("shift_date") for entry in db.active_entries(day)
                                      if entry.get("shift_date")), None)
    date_label = day_label(day, shift_date)
    send(m.chat.id, f"📣 کلاس‌های {label or day} ({date_label}) به {len(msgs)} گروه استاد ارسال می‌شود:\n"
         + escape("، ".join(t for _, t, _ in msgs))
         + (f"\n\n⚠️ بدون تخصیص، نام کاربری یا گروه (پیام نمی‌رود): {escape('، '.join(no_group))}" if no_group else ""),
         reply_markup=kb, parse_mode="HTML")


@bot.message_handler(commands=["notify_teachers"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_NOTIFY, chat_types=["private"])
@staff_only(SUP, BOT_MANAGER)
def notify(m, s):
    arg = m.text.partition(" ")[2] if m.text.startswith("/") else ""
    today, tomorrow, _ = today_tomorrow()
    day = resolve_day(arg or "فردا")
    if not day:
        send(m.chat.id, "روز نامعتبر. مثال: /notify_teachers یکشنبه")
        return
    label = "فردا" if day == tomorrow else ("امروز" if day == today else None)
    now = now_tehran()
    target_date = (now.date() + timedelta(days=1)).isoformat() if label == "فردا" else (
        now.date().isoformat() if label == "امروز" else None)
    _start_notify(m, s, day, label, target_date)


@bot.callback_query_handler(func=lambda c: c.data in ("nt:go", "nt:no"))
def on_notify(c):
    bot.answer_callback_query(c.id)
    msgs = pending.pop(c.from_user.id, None)
    chat = c.message.chat.id
    if c.data == "nt:no" or not msgs:
        send(chat, "لغو شد." if c.data == "nt:no" else "این درخواست منقضی شده؛ دوباره بزن.")
        return
    staff = db.get_staff_by_tid(c.from_user.id)
    if not staff or staff["role"] not in (SUP, BOT_MANAGER):
        send(chat, "اجازه‌ی ارسال این اعلان را نداری.")
        return
    assignments = {assignment["teacher_key"]: assignment for assignment in db.list_teacher_assignments()}
    groups = {group["teacher_key"]: group for group in db.list_teacher_groups()}
    ok, fail = 0, []
    for chat_id, teacher, text in msgs:
        teacher_key = key(teacher)
        assignment = assignments.get(teacher_key)
        group = groups.get(teacher_key)
        if (not assignment or not group or group["chat_id"] != chat_id
                or not (assignment["telegram_id"] or assignment["username"])
                or (staff["role"] == SUP and assignment["supervisor"] != staff["name"])):
            fail.append(teacher)
            continue
        if send(chat_id, text, parse_mode="HTML"):
            ok += 1
        else:
            fail.append(teacher)
        time.sleep(0.4)
    send(chat, f"✅ برای {ok} گروه ارسال شد." + (f"\n❌ ناموفق: {'، '.join(fail)} (بات هنوز در گروه هست؟)" if fail else ""))
