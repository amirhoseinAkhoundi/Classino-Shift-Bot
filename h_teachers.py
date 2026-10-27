"""اطلاع‌رسانی سرپرست‌ها به گروه اساتید."""
import time
from collections import defaultdict
from datetime import timedelta
from html import escape

from telebot import types

import db
from analysis import day_label, now_tehran, sort_entries, span, text_line, today_tomorrow
from app import BTN_NOTIFY, BOT_MANAGER, SUP, bot, send, staff_context, staff_only
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
        if s.get("_admin_mode") and (
                not assignment or assignment["supervisor"] != s.get("supervisor")):
            continue
        group = groups.get(k)
        if not assignment or not (assignment["telegram_id"] or assignment["username"]) or not group:
            no_group.append(teacher)
            continue
        msgs.append((group["chat_id"], teacher,
                     compose(teacher, assignment["username"], day, items, label, target_date,
                             assignment["telegram_id"])))
    return msgs, sorted(no_group)


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
        send(m.chat.id, "این استاد هنوز در پنل «مدیریت استادها» ثبت نشده است.")
        return
    if (s["role"] == SUP and assignment["supervisor"] != s["name"]) or (
            s.get("_admin_mode") and assignment["supervisor"] != s.get("supervisor")):
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
    if (s["role"] == SUP and assignment["supervisor"] != s["name"]) or (
            s.get("_admin_mode") and assignment["supervisor"] != s.get("supervisor")):
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
    elif s.get("_admin_mode"):
        gs = [g for g in gs if g["supervisor"] == s["supervisor"]]
        assignments = [a for a in assignments if a["supervisor"] == s["supervisor"]]
    linked = {g["teacher_key"] for g in db.list_teacher_groups()}
    missing = sorted(a["teacher"] for a in assignments if a["teacher_key"] not in linked)
    t = "\n".join(f"• {g['teacher']}" for g in gs) or "—"
    assigned = "\n".join(f"• {a['teacher']}" for a in assignments) or "—"
    send(m.chat.id, f"🔗 گروه‌های وصل‌شده ({len(gs)}):\n{t}\n\n"
                    f"👩‍🏫 استادهای تخصیص‌یافته ({len(assignments)}):\n{assigned}\n\n"
                    "❓ استادهای تخصیص‌یافته که گروه ندارند:\n" + ("، ".join(missing) or "—"))


def _start_notify(m, s, day, label, target_date=None):
    msgs, no_group = build_batches(s, day, label, target_date)
    if not msgs:
        send(m.chat.id, f"برای {day} پیامی برای ارسال نیست." +
             (f"\nبدون تخصیص، ثبت شناسه‌ی تلگرام یا گروه: {'، '.join(no_group)}" if no_group else ""))
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
         + (f"\n\n⚠️ بدون تخصیص، ثبت شناسه‌ی تلگرام یا گروه (پیام نمی‌رود): "
            f"{escape('، '.join(no_group))}" if no_group else ""),
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
    if staff:
        staff = staff_context(staff)
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
                or (staff["role"] == SUP and assignment["supervisor"] != staff["name"])
                or (staff["role"] == BOT_MANAGER
                    and db.get_setting(f"manager_mode:{staff['telegram_id']}") == "admin"
                    and assignment["supervisor"] != staff["supervisor"])):
            fail.append(teacher)
            continue
        if send(chat_id, text, parse_mode="HTML"):
            ok += 1
        else:
            fail.append(teacher)
        time.sleep(0.4)
    send(chat, f"✅ برای {ok} گروه ارسال شد." + (f"\n❌ ناموفق: {'، '.join(fail)} (بات هنوز در گروه هست؟)" if fail else ""))
