"""اطلاع‌رسانی سرپرست‌ها به گروه اساتید.

قوانین تگ‌کردن استاد در گروه:
- استاد خارج از کشور: شماره‌اش فقط در دیتابیس شماره‌ی اساتید نگه‌داری می‌شود و هیچ‌وقت برای تگ
  استفاده نمی‌شود. تگ فقط با شناسه‌ی تلگرامی است که با /registerteacher (ریپلای روی پیام استاد) ثبت شده.
- استاد داخل کشور: تایید دو مرحله‌ای.
  مرحله‌ی ۱: مدیر ربات username را دستی وارد می‌کند (/assignteacher).
  مرحله‌ی ۲: /registerteacher در گروه؛ فقط وقتی username حساب با username واردشده‌ی مدیر یکی باشد ثبت می‌شود.
- استادی که نوعش مشخص نشده، «داخل» حساب می‌شود (سخت‌گیرانه‌تر).
"""

import re
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

DOMESTIC = "domestic"
FOREIGN = "foreign"

_DOMESTIC_WORDS = {"داخل", "داخلی", "ایران", "domestic", "in"}
_FOREIGN_WORDS = {"خارج", "خارجی", "خارج از کشور", "foreign", "out"}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ---------- دیتابیس شماره و نوع اساتید (جدا از تگ‌کردن) ----------

def _ensure_table():
    with db.connect() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS teacher_contacts ("
            "teacher_key TEXT PRIMARY KEY, "
            "teacher TEXT NOT NULL, "
            "origin TEXT NOT NULL DEFAULT 'domestic', "
            "phone TEXT)"
        )


def get_origin(teacher_key):
    _ensure_table()
    with db.connect() as c:
        row = c.execute("SELECT origin FROM teacher_contacts WHERE teacher_key=?",
                        (teacher_key,)).fetchone()
    return row["origin"] if row else DOMESTIC


def _origins():
    _ensure_table()
    with db.connect() as c:
        return {r["teacher_key"]: r["origin"] for r in c.execute(
            "SELECT teacher_key, origin FROM teacher_contacts")}


def set_contact(teacher, origin, phone=None):
    """نوع (داخل/خارج) و شماره‌ی استاد را ثبت می‌کند؛ اگر phone=None شماره‌ی قبلی حفظ می‌شود."""
    _ensure_table()
    name = normalize(teacher).strip()
    with db.connect() as c:
        c.execute(
            "INSERT INTO teacher_contacts(teacher_key, teacher, origin, phone) VALUES(?,?,?,?) "
            "ON CONFLICT(teacher_key) DO UPDATE SET teacher=excluded.teacher, origin=excluded.origin, "
            "phone=COALESCE(excluded.phone, teacher_contacts.phone)",
            (key(name), name, origin, phone),
        )


def list_contacts():
    _ensure_table()
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM teacher_contacts ORDER BY teacher")]


def parse_phone(raw):
    p = re.sub(r"[\s\-()]", "", (raw or "").translate(_DIGITS))
    return p if re.fullmatch(r"\+?\d{7,15}", p) else None


def parse_origin(raw):
    w = normalize(raw or "").strip().lower()
    if w in _DOMESTIC_WORDS:
        return DOMESTIC
    if w in _FOREIGN_WORDS:
        return FOREIGN
    return None


def origin_label(origin):
    return "خارج از کشور" if origin == FOREIGN else "داخل کشور"


def is_taggable(assignment, origin):
    """آیا می‌شود این استاد را در گروهش تگ کرد؟

    خارج: فقط شناسه‌ی ثبت‌شده با /registerteacher.
    داخل: هر دو مرحله لازم است (username واردشده‌ی مدیر + شناسه‌ی ثبت‌شده).
    شماره هیچ نقشی در تگ ندارد.
    """
    if not assignment or not assignment.get("telegram_id"):
        return False
    if origin == FOREIGN:
        return True
    return bool((assignment.get("username") or "").strip())


# ---------- ساخت پیام ----------

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
    origins = _origins()
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
        if not group or not is_taggable(assignment, origins.get(k, DOMESTIC)):
            no_group.append(teacher)
            continue
        msgs.append((group["chat_id"], teacher,
                     compose(teacher, assignment["username"], day, items, label, target_date,
                             assignment["telegram_id"])))
    return msgs, sorted(no_group)


# ---------- دستورهای گروه ----------

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
    """ریپلای روی پیام استاد + /registerteacher → ثبت شناسه‌ی تلگرام برای تگ‌کردن."""
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

    origin = get_origin(group["teacher_key"])
    if origin == DOMESTIC:
        # تایید دو مرحله‌ای: مرحله‌ی ۱ (ورود دستی مدیر) باید انجام شده باشد و با حساب فعلی بخواند
        expected = (assignment.get("username") or "").lstrip("@").strip().lower()
        if not expected:
            send(m.chat.id, "استاد داخلی: مرحله‌ی ۱ انجام نشده است. مدیر ربات باید اول نام کاربری "
                            "تلگرام استاد را دستی وارد کند (/assignteacher)، بعد دوباره /registerteacher بزن.")
            return
        actual = (teacher_user.username or "").strip().lower()
        if actual != expected:
            send(m.chat.id, "❌ نام کاربری این حساب با نام کاربری ثبت‌شده توسط مدیر یکی نیست؛ "
                            "شناسه ثبت نشد. روی پیام خود همان استاد ریپلای کن، یا مدیر ربات "
                            "نام کاربری را اصلاح کند.")
            return

    if not db.set_teacher_telegram_id(assignment["teacher"], teacher_user.id):
        send(m.chat.id, "ذخیره‌ی شناسه انجام نشد؛ دوباره تلاش کن یا با مدیر ربات هماهنگ کن.")
        return
    username = f" (@{teacher_user.username})" if teacher_user.username else ""
    send(m.chat.id, f"✅ شناسه‌ی تلگرام استاد «{assignment['teacher']}»{username} ثبت شد "
                    f"({origin_label(origin)})؛ یادآوری‌های بعدی او را مستقیم منشن می‌کنند.")


# ---------- دستورهای خصوصی ----------

@bot.message_handler(commands=["teachercontact"], chat_types=["private"])
@staff_only(BOT_MANAGER)
def teacher_contact(m, s):
    """/teachercontact نام استاد | داخل یا خارج | شماره (اختیاری) — ثبت نوع و شماره در دیتابیس شماره‌ی اساتید."""
    usage = ("مثال:\n/teachercontact فکری | داخل\n"
             "/teachercontact John | خارج | +491701234567\n"
             "شماره فقط در دیتابیس شماره‌ی اساتید ذخیره می‌شود و برای تگ استفاده نمی‌شود.")
    parts = [p.strip() for p in m.text.partition(" ")[2].split("|")]
    if len(parts) < 2 or not parts[0]:
        send(m.chat.id, usage)
        return
    name = normalize(parts[0]).strip()
    origin = parse_origin(parts[1])
    if not origin:
        send(m.chat.id, "نوع باید «داخل» یا «خارج» باشد.\n" + usage)
        return
    phone = None
    if len(parts) >= 3 and parts[2]:
        phone = parse_phone(parts[2])
        if not phone:
            send(m.chat.id, "شماره نامعتبر است (فقط رقم، با + اختیاری در ابتدا).\n" + usage)
            return
    assignment = db.get_teacher_assignment(name)
    if not assignment:
        send(m.chat.id, "این استاد هنوز ثبت نشده؛ اول با /assignteacher "
                        "(یا پنل «مدیریت استادها») ثبتش کن.")
        return
    set_contact(assignment["teacher"], origin, phone)
    extra = ("\nبرای تگ فقط باید در گروهش /registerteacher زده شود." if origin == FOREIGN else
             "\nبرای تگ: username را دستی وارد کن (/assignteacher) و بعد در گروهش /registerteacher بزن.")
    send(m.chat.id, f"✅ «{assignment['teacher']}» ثبت شد: {origin_label(origin)}"
                    + (" | شماره ذخیره شد" if phone else "") + extra)


@bot.message_handler(commands=["teacherphones"], chat_types=["private"])
@staff_only(BOT_MANAGER)
def teacher_phones(m, s):
    """فهرست دیتابیس شماره‌ی اساتید (فقط مدیر ربات)."""
    rows = list_contacts()
    if not rows:
        send(m.chat.id, "هنوز استادی در دیتابیس شماره‌ها ثبت نشده است.")
        return
    assignments = {a["teacher_key"]: a for a in db.list_teacher_assignments()}
    lines = []
    for r in rows:
        a = assignments.get(r["teacher_key"])
        tag = "✅ قابل تگ" if is_taggable(a, r["origin"]) else "⏳ هنوز قابل تگ نیست"
        lines.append(f"• {r['teacher']} — {origin_label(r['origin'])} — {r['phone'] or '—'} — {tag}")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 3500:
            send(m.chat.id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        send(m.chat.id, chunk)


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
    origins = _origins()
    not_taggable = sorted(
        a["teacher"] for a in assignments
        if not is_taggable(a, origins.get(a["teacher_key"], DOMESTIC)))
    t = "\n".join(f"• {g['teacher']}" for g in gs) or "—"
    assigned = "\n".join(f"• {a['teacher']}" for a in assignments) or "—"
    send(m.chat.id, f"🔗 گروه‌های وصل‌شده ({len(gs)}):\n{t}\n\n"
                    f"👩‍🏫 استادهای تخصیص‌یافته ({len(assignments)}):\n{assigned}\n\n"
                    "❓ استادهای تخصیص‌یافته که گروه ندارند:\n" + ("، ".join(missing) or "—")
                    + "\n\n🏷 هنوز قابل تگ نیستند (شناسه یا مرحله‌ی تایید ناقص):\n"
                    + ("، ".join(not_taggable) or "—"))


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
    origins = _origins()
    ok, fail = 0, []
    for chat_id, teacher, text in msgs:
        teacher_key = key(teacher)
        assignment = assignments.get(teacher_key)
        group = groups.get(teacher_key)
        if (not assignment or not group or group["chat_id"] != chat_id
                or not is_taggable(assignment, origins.get(teacher_key, DOMESTIC))
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
