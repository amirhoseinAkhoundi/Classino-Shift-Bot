"""دستورهای مسئول شیفت و سرپرست: آزاد بودن، آمار، تیم، جست‌وجو."""
import os
import sqlite3
import tempfile
import logging
from html import escape
from datetime import timedelta

from telebot import types

import db
from analysis import (by_day_text, day_block, day_label, entry_line, free_staff, hours, now_tehran,
                     jalali_day_month, reporting_period, resolve_day,
                     sort_entries, stats, today_tomorrow)
from app import (ADMIN, ADMIN_ROLES, BTN_FREE, BTN_STATS, BTN_TEAM, BOT_MANAGER, MANAGER, ROLE_FA, SUP, bot,
                 phone_display, staff_role_label, BTN_STAFF_MANAGEMENT, menu_for, normalize_phone, send,
                 staff_only)
from reports import build_admins_xlsx, build_studios_xlsx
from schedule_parser import (DAYS, apply_helper_class_buffers, key, normalize, parse_range,
                             fmt_min, person_matches, split_people)
from scripts import seed_staff as staff_csv

ROLES_OK = {MANAGER, SUP, ADMIN, BOT_MANAGER}
MANAGEMENT_ROLES = (MANAGER, SUP, BOT_MANAGER)
FULL_ACCESS_ROLES = (MANAGER, BOT_MANAGER)
STAFF_ADMINS_PENDING = {}
STAFF_MANAGEMENT_PENDING = {}
FREE_PENDING = {}  # telegram_id -> day (empty string while choosing a day)
STAFF_CSV_PATH = staff_csv.DEFAULT_CSV_PATH


def team_of(sup):
    sup = dict(sup)
    if is_admin_mode(sup) and sup.get("supervisor"):
        members = [s for s in db.list_staff() if s["supervisor"] == sup["supervisor"]]
        return members + ([sup] if sup["name"] not in {member["name"] for member in members} else [])
    return [s for s in db.list_staff() if s["supervisor"] == sup["name"]]


def is_admin_mode(s):
    value = dict(s)
    return bool(value.get("_admin_mode") or (
        value["role"] == BOT_MANAGER
        and db.get_setting(f"manager_mode:{value['telegram_id']}") == "admin"))


def full_access(s):
    return s["role"] in FULL_ACCESS_ROLES and not is_admin_mode(s)


def scoped_staff(s):
    """افرادی که این کاربر حق دیدنشان را دارد."""
    if full_access(s):
        return db.list_staff()
    return team_of(s)


def entries_for_names(entries, names):
    rows = []
    for entry in entries:
        visible = [name for name in names if person_matches(entry["person"], name)]
        if visible:
            row = dict(entry)
            row["person"] = " و ".join(visible)
            rows.append(row)
    return rows


def scoped_entries(s):
    es = db.active_entries()
    if full_access(s):
        return es
    names = {x["name"] for x in team_of(s)}
    return entries_for_names(es, names)


def can_see(s, name):
    if full_access(s):
        return True
    return name == s["name"] or name in {x["name"] for x in team_of(s)}


def parse_day_range(arg):
    r = parse_range(arg)
    if not r:
        return None
    start, end, rest = r
    day = resolve_day(rest)
    return (day, start, end) if day else None


# ---------- چه کسی آزاد است ----------
def _free_keyboard():
    today, tomorrow, _ = today_tomorrow()
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(f"امروز ({today})", callback_data="free:day:today"),
        types.InlineKeyboardButton(f"فردا ({tomorrow})", callback_data="free:day:tomorrow"),
    )
    for start in range(0, len(DAYS), 3):
        kb.row(*[
            types.InlineKeyboardButton(day, callback_data=f"free:day:{DAYS.index(day)}")
            for day in DAYS[start:start + 3]
        ])
    kb.row(types.InlineKeyboardButton("❌ لغو", callback_data="free:cancel"))
    return kb


def _ask_free_day(chat, uid):
    FREE_PENDING[uid] = ""
    send(chat, "برای کدوم روز دنبال نیروی آزاد می‌گردی؟", reply_markup=_free_keyboard())


def _ask_free_time(chat, uid, day):
    FREE_PENDING[uid] = day
    send(chat, f"بازه‌ی ساعتِ {day} رو بفرست؛ مثلاً ۱۵-۱۸ یا ۱۵:۳۰-۱۸:۳۰.")


def _send_free_report(chat, s, day, start, end):
    staff = [member for member in scoped_staff(s) if member["role"] in ADMIN_ROLES]
    today, tomorrow, _ = today_tomorrow()
    now = now_tehran()
    target_date = now.date().isoformat() if day == today else (
        (now.date() + timedelta(days=1)).isoformat() if day == tomorrow else None)
    entries = db.entries_on_date(target_date, day=day) if target_date else db.active_entries(day)
    day_entries = [e for e in entries if e["person"]]
    if is_admin_mode(s):
        allowed = {member["name"] for member in scoped_staff(s)}
        day_entries = entries_for_names(day_entries, allowed)
    if not day_entries:
        send(chat, f"برای {day_label(day, target_date)} هنوز برنامه‌ای ثبت نشده؛ "
             "نتیجه‌ی آزادبودن قابل اتکا نیست.", parse_mode="HTML")
        return
    shift_date = target_date or next((e["shift_date"] for e in day_entries if e.get("shift_date")), None)

    free, off, busy = free_staff(staff, day_entries, day, start, end)
    available_entries = apply_helper_class_buffers(day_entries)
    helper_entries = sort_entries([
        entry for entry in available_entries
        if entry["kind"] == "komaki" and entry["start_min"] < end and start < entry["end_min"]
    ])
    helpers = {}
    for entry in helper_entries:
        helpers.setdefault(entry["person"], []).append(entry)

    def people(rows):
        return "\n".join(f"• {escape(r['name'])} — {phone_display(r)}" for r in rows) or "—"

    helper_lines = []
    for name, shifts in helpers.items():
        spans = "، ".join(f"{fmt_min(e['start_min'])}–{fmt_min(e['end_min'])}" for e in shifts)
        helper_lines.append(f"• {escape(name)} — {phone_display(db.get_staff_by_name(name))} ({spans})")
    send(chat,
         f"🟢 وضعیت نیروها — {day_label(day, shift_date)}"
         + f"، {fmt_min(start)} تا {fmt_min(end)}\n\n"
         f"✅ ادمین‌های شیفت‌دار و آزاد:\n{people(free)}\n\n"
         "🟢 کمکی‌های این بازه:\n"
         + ("\n".join(helper_lines) or "—") + "\n\n"
         f"🔴 ادمین‌های شیفت‌دار و مشغول:\n{people(busy)}\n\n"
         f"⚪️ ادمین‌های بدون شیفت در این روز:\n{people(off)}", parse_mode="HTML")


@bot.message_handler(commands=["free"], chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def free(m, s):
    arg = m.text.partition(" ")[2].strip()
    p = parse_day_range(arg)
    if p:
        _send_free_report(m.chat.id, s, *p)
        return
    day = resolve_day(arg)
    if day:
        _ask_free_time(m.chat.id, m.from_user.id, day)
        return
    if not arg:
        _ask_free_day(m.chat.id, m.from_user.id)
        return
    send(m.chat.id, "روز یا بازه‌ی ساعت رو متوجه نشدم. نمونه: /free یکشنبه ۱۵-۱۸")


@bot.message_handler(func=lambda m: m.text == BTN_FREE, chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def free_btn(m, s):
    _ask_free_day(m.chat.id, m.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("free:"))
def free_callback(c):
    bot.answer_callback_query(c.id)
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception as e:  # noqa
        logging.warning("could not clear free-search buttons: %s", e)

    uid, chat = c.from_user.id, c.message.chat.id
    staff = db.get_staff_by_tid(uid)
    if not staff or staff["role"] not in MANAGEMENT_ROLES or is_admin_mode(staff):
        return
    if c.data == "free:cancel":
        FREE_PENDING.pop(uid, None)
        send(chat, "باشه، لغو شد.")
        return
    if uid not in FREE_PENDING:
        send(chat, "این درخواست تموم شده؛ دوباره «چه کسی آزاده؟» رو بزن.")
        return

    value = c.data.partition("free:day:")[2]
    if value == "today":
        day = today_tomorrow()[0]
    elif value == "tomorrow":
        day = today_tomorrow()[1]
    else:
        try:
            day = DAYS[int(value)]
        except (ValueError, IndexError):
            send(chat, "روز انتخاب‌شده معتبر نیست؛ دوباره «چه کسی آزاده؟» رو بزن.")
            return
    _ask_free_time(chat, uid, day)


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                     and m.from_user.id in FREE_PENDING and bool(m.text) and not m.text.startswith("/"))
def free_time_input(m):
    uid = m.from_user.id
    staff = db.get_staff_by_tid(uid)
    if not staff or staff["role"] not in MANAGEMENT_ROLES or is_admin_mode(staff):
        FREE_PENDING.pop(uid, None)
        return
    day = FREE_PENDING[uid]
    if not day:
        _ask_free_day(m.chat.id, uid)
        return
    parsed = parse_range(m.text)
    if not parsed or parsed[2]:
        send(m.chat.id, f"ساعت رو به شکل ۱۵-۱۸ بفرست. روز انتخاب‌شده: {day}.")
        return
    start, end, _ = parsed
    FREE_PENDING.pop(uid, None)
    _send_free_report(m.chat.id, staff, day, start, end)


# ---------- آمار ----------
@bot.message_handler(commands=["stats"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_STATS, chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def stats_cmd(m, s):
    kb = types.InlineKeyboardMarkup()
    if full_access(s):
        kb.row(types.InlineKeyboardButton("🏢 گزارش استودیوها", callback_data="stats:studios"))
    kb.row(types.InlineKeyboardButton("👤 گزارش ادمین‌ها", callback_data="stats:admins"))
    send(m.chat.id, "کدوم گزارش رو می‌خوای؟", reply_markup=kb)


def _admin_report_entries(s, entries):
    s = dict(s)
    if (s["role"] == BOT_MANAGER
            and db.get_setting(f"manager_mode:{s['telegram_id']}") == "admin"):
        s["_admin_mode"] = True
    admins = {person["name"] for person in db.list_staff(roles=ADMIN_ROLES)}
    if s["role"] == SUP or s.get("_admin_mode"):
        admins &= {person["name"] for person in team_of(s)}
    return entries_for_names(entries, admins)


def _send_report_file(chat, uid, builder, caption, filename, entries):
    path = os.path.join(tempfile.gettempdir(), f"stats_{uid}.xlsx")
    try:
        builder(entries, path)
        with open(path, "rb") as report:
            bot.send_document(chat, report, visible_file_name=filename, caption=caption)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _delete_callback_message(c, purpose):
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception as e:  # noqa
        logging.warning("could not delete %s prompt: %s", purpose, e)


def _stats_period_keyboard(report):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("🗓 هفته‌ی جاری", callback_data=f"stats:run:{report}:week"),
           types.InlineKeyboardButton("📆 ماه جاری (شمسی)", callback_data=f"stats:run:{report}:month"))
    if report == "studios":
        kb.row(types.InlineKeyboardButton("📚 کل دوره", callback_data="stats:run:studios:all"))
    return kb


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stats:"))
def stats_report(c):
    bot.answer_callback_query(c.id)
    staff = db.get_staff_by_tid(c.from_user.id)
    if not staff or staff["role"] not in MANAGEMENT_ROLES or is_admin_mode(staff):
        return
    parts = c.data.split(":")
    if len(parts) == 2 and parts[1] in ("studios", "admins"):
        report = parts[1]
        if report == "studios" and not full_access(staff):
            send(c.message.chat.id, "گزارش استودیوها فقط برای مسئول شیفت و مدیر ربات در دسترسه.")
            return
        _delete_callback_message(c, "statistics report selection")
        send(c.message.chat.id, "بازه‌ی گزارش را انتخاب کن:",
             reply_markup=_stats_period_keyboard(report))
        return
    if len(parts) != 4 or parts[1] != "run" or parts[2] not in ("studios", "admins") \
            or parts[3] not in ("week", "month", "all"):
        return
    report, period = parts[2], parts[3]
    if report == "studios" and not full_access(staff):
        return
    if period == "all" and report != "studios":
        return
    _delete_callback_message(c, "statistics period selection")
    if period == "all":
        entries = [e for e in db.historical_entries(kind="class") if e.get("studio")]
        period_label = "کل دوره"
        filename_suffix = "all-time"
    else:
        start, end = reporting_period(period, now_tehran())
        entries = [e for e in db.active_entries()
                   if e.get("shift_date") and start <= e["shift_date"] <= end]
        period_label = (f"هفته‌ی جاری ({jalali_day_month(start)} تا {jalali_day_month(end)})"
                        if period == "week" else f"ماه جاری شمسی ({jalali_day_month(start)} تا "
                        f"{jalali_day_month(end)})")
        filename_suffix = period
    if report == "studios":
        entries = [entry for entry in entries if entry["kind"] == "class" and entry.get("studio")]
        if not entries:
            send(c.message.chat.id, "برای این بازه استفاده‌ای از استودیو ثبت نشده.")
            return
        studios, _ = stats(entries)
        lines = "\n".join(
            f"{'🟢' if index == 0 else '⚪' if index == len(studios) - 1 else '•'} "
            f"{name}: {hours(minutes)} ساعت ({count} کلاس)"
            for index, (name, minutes, count) in enumerate(studios)
        )
        send(c.message.chat.id, f"🏢 استفاده از استودیوها — {period_label}:\n{lines}")
        _send_report_file(c.message.chat.id, c.from_user.id, build_studios_xlsx,
                          f"استفاده از استودیوها — {period_label}",
                          f"classino-studios-{filename_suffix}.xlsx", entries)
        return
    if report == "admins":
        entries = _admin_report_entries(staff, entries)
        if not entries:
            send(c.message.chat.id, "برای ادمین‌های مجاز در این بازه برنامه‌ای ثبت نشده.")
            return
        _, admins = stats(entries)
        scope = " (فقط ادمین‌های تیم شما)" if staff["role"] == SUP else ""
        lines = "\n".join(f"• {a['person']}: {hours(a['total'])} ساعت"
                          f" (کلاس {hours(a['class'])} | نظارت {hours(a['nazer'])} | کمکی {hours(a['komaki'])})"
                          for a in admins)
        send(c.message.chat.id, f"👤 آمار ادمین‌ها{scope} — {period_label}:\n{lines}")
        _send_report_file(c.message.chat.id, c.from_user.id, build_admins_xlsx,
                          f"آمار ادمین‌ها — {period_label}",
                          f"classino-admins-{filename_suffix}.xlsx", entries)
        return


# ---------- تیم و جست‌وجو ----------
@bot.message_handler(commands=["team"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_TEAM, chat_types=["private"])
@staff_only(SUP, BOT_MANAGER)
def team(m, s):
    arg = m.text.partition(" ")[2] if m.text.startswith("/") else ""
    day = resolve_day(arg)
    if not day:
        send(m.chat.id, "روز نامعتبر. مثال: /team یکشنبه")
        return
    members = team_of(s)
    if not members:
        send(m.chat.id, "هنوز کسی زیر نظر تو ثبت نشده (ستون supervisor در staff.csv).")
        return
    es = [entry for entry in scoped_entries(s) if entry["day"] == day]
    blocks = []
    for mem in members:
        mine = sort_entries([e for e in es if person_matches(e["person"], mem["name"])])
        if is_admin_mode(s):
            mine = [{**entry, "person": mem["name"]} for entry in mine]
        body = "\n".join("   " + entry_line(e, e.get("shift_date")) for e in mine) if mine else "   — شیفت ندارد"
        blocks.append(f"👤 {escape(mem['name'])} ({phone_display(mem)})\n{body}")
    today, tomorrow, _ = today_tomorrow()
    now = now_tehran()
    shift_date = now.date().isoformat() if day == today else (
        (now.date() + timedelta(days=1)).isoformat() if day == tomorrow else
        next((e.get("shift_date") for e in es if e.get("shift_date")), None))
    send(m.chat.id, f"📅 تیم من — {day_label(day, shift_date)}\n\n" + "\n\n".join(blocks), parse_mode="HTML")


@bot.message_handler(commands=["person"], chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def person(m, s):
    name = normalize(m.text.partition(" ")[2]).strip()
    if not name:
        send(m.chat.id, "مثال: /person وحید")
        return
    st = db.get_staff_by_name(name)
    name = st["name"] if st else name
    if not can_see(s, name):
        send(m.chat.id, "این فرد جزو تیم تو نیست.")
        return
    rows = db.entries_for_person(name)
    if is_admin_mode(s):
        rows = [{**row, "person": name} for row in rows
                if person_matches(row["person"], name)]
    if not rows:
        send(m.chat.id, f"برای «{name}» شیفتی ثبت نشده.")
        return
    send(m.chat.id, f"👤 {escape(name)} ({phone_display(st)})\n\n" + by_day_text(rows), parse_mode="HTML")


@bot.message_handler(commands=["class"], chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def class_(m, s):
    q = key(normalize(m.text.partition(" ")[2]))
    if not q:
        send(m.chat.id, "مثال: /class فکری   یا   /class شیمی دهم")
        return
    entries = scoped_entries(s) if is_admin_mode(s) else db.active_entries()
    hits = [e for e in entries if e["kind"] == "class"
            and q in key((e["teacher"] or "") + (e["course"] or ""))]
    if not hits:
        send(m.chat.id, "کلاسی پیدا نشد.")
        return
    lines = []
    for e in sort_entries(hits)[:40]:
        admins = [db.get_staff_by_name(name) for name in split_people(e["person"])]
        phones = "، ".join(phone_display(admin) for admin in admins) or "—"
        lines.append(f"{day_label(e['day'], e.get('shift_date'))}\n\n"
                     f"{entry_line(e, e.get('shift_date'), with_person=True)} ({phones})")
    more = f"\n… و {len(hits) - 40} مورد دیگر" if len(hits) > 40 else ""
    send(m.chat.id, "\n".join(lines) + more, parse_mode="HTML")


@bot.message_handler(commands=["day"], chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def day_cmd(m, s):
    day = resolve_day(m.text.partition(" ")[2])
    if not day:
        send(m.chat.id, "روز نامعتبر. مثال: /day یکشنبه")
        return
    entries = scoped_entries(s) if is_admin_mode(s) else db.active_entries(day)
    es = [entry for entry in entries if entry["day"] == day]
    if not es:
        send(m.chat.id, f"برای {day} برنامه‌ای ثبت نشده.")
        return
    today, tomorrow, _ = today_tomorrow()
    now = now_tehran()
    target_date = now.date().isoformat() if day == today else (
        (now.date() + timedelta(days=1)).isoformat() if day == tomorrow else None)
    send(m.chat.id, day_block(day, es, with_person=True, target_date=target_date), parse_mode="HTML")


# ---------- مدیریت پرسنل ----------
@bot.message_handler(commands=["staff"], chat_types=["private"])
@staff_only(*MANAGEMENT_ROLES)
def staff_list(m, s):
    rows = db.list_staff() if full_access(s) else team_of(s)
    if not rows:
        send(m.chat.id, "لیستی نیست.")
        return
    lines = [f"{'✅' if r['telegram_id'] else '⏳'} {r['name']} — {staff_role_label(r)}"
             f" — {phone_display(r)}" + (f" — سرپرست: {r['supervisor']}" if r["supervisor"] else "")
             for r in rows]
    send(m.chat.id, "✅ تایید هویت‌شده  ⏳ هنوز وارد نشده\n\n" + "\n".join(lines))


def _staff_categories_keyboard():
    labels = {
        ADMIN: "ادمین‌ها",
        SUP: "سرپرست‌ها",
        MANAGER: "مسئول‌های شیفت",
    }
    kb = types.InlineKeyboardMarkup()
    for role in (ADMIN, SUP, MANAGER):
        roles = ADMIN_ROLES if role == ADMIN else (role,)
        count = len(db.list_staff(roles=roles))
        kb.row(types.InlineKeyboardButton(
            f"{labels[role]} ({count})", callback_data=f"staffm:category:{role}"))
    kb.row(types.InlineKeyboardButton("➕ افزودن پرسنل", callback_data="staffm:add"))
    return kb


def _staff_role_keyboard(prefix):
    labels = {
        ADMIN: "ادمین",
        SUP: "سرپرست",
        MANAGER: "مسئول شیفت",
        BOT_MANAGER: "مدیر ربات",
    }
    kb = types.InlineKeyboardMarkup()
    for role in (ADMIN, SUP, MANAGER, BOT_MANAGER):
        kb.row(types.InlineKeyboardButton(labels[role], callback_data=f"{prefix}:{role}"))
    kb.row(types.InlineKeyboardButton("❌ لغو", callback_data="staffm:cancel"))
    return kb


def _staff_cancel_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(types.KeyboardButton("❌ لغو"))
    return kb


def _staff_supervisor_keyboard(prefix):
    supervisors = db.list_staff(roles=(SUP,))
    kb = types.InlineKeyboardMarkup()
    for supervisor in supervisors:
        kb.row(types.InlineKeyboardButton(
            supervisor["name"], callback_data=f"{prefix}:{supervisor['id']}"))
    kb.row(types.InlineKeyboardButton("❌ لغو", callback_data="staffm:cancel"))
    return kb


def _staff_snapshot():
    return staff_csv.read_staff_csv(STAFF_CSV_PATH)


def _restore_staff_snapshot(snapshot):
    staff_csv.restore_staff_csv(snapshot, STAFF_CSV_PATH)


def _save_staff(data, staff_id=None):
    previous = db.list_staff()
    old = next((row for row in previous if row["id"] == staff_id), None) if staff_id else None
    old = dict(old) if old else None
    snapshot = _staff_snapshot()
    record = {
        "name": normalize(data["name"]).strip(),
        "phone": normalize_phone(data["phone"]),
        "role": data["role"],
        "supervisor": normalize(data.get("supervisor") or "").strip(),
    }
    if not record["name"] or len(record["phone"]) != 10 or record["role"] not in ROLES_OK:
        raise ValueError("نام، شماره یا سمت معتبر نیست.")
    staff_csv.save_staff_csv(record, STAFF_CSV_PATH, previous=old)
    try:
        if old:
            changed = db.update_staff(staff_id, record["name"], record["phone"],
                                      record["role"], record["supervisor"])
            if not changed:
                raise ValueError("این پرسنل دیگر در فهرست نیست.")
        else:
            db.upsert_staff(record["name"], record["phone"], record["role"], record["supervisor"])
    except Exception:
        try:
            _restore_staff_snapshot(snapshot)
        except OSError:
            logging.exception("CSV پرسنل پس از شکست ذخیره‌ی دیتابیس بازیابی نشد")
        raise
    return record


def _delete_staff(staff, actor_id):
    staff = dict(staff)
    if staff.get("telegram_id") == actor_id:
        raise ValueError("برای جلوگیری از قفل‌شدن پنل، نمی‌توان حساب خودت را حذف کنی.")
    if staff["role"] == BOT_MANAGER and len(db.list_staff(roles=(BOT_MANAGER,))) <= 1:
        raise ValueError("آخرین مدیر ربات را نمی‌توان حذف کرد.")
    snapshot = _staff_snapshot()
    staff_csv.remove_staff_csv(staff, STAFF_CSV_PATH)
    try:
        if not db.delete_staff(staff["id"]):
            raise ValueError("این پرسنل دیگر در فهرست نیست.")
    except Exception:
        try:
            _restore_staff_snapshot(snapshot)
        except OSError:
            logging.exception("CSV پرسنل پس از شکست حذف از دیتابیس بازیابی نشد")
        raise


def _staff_manager(telegram_id):
    staff = db.get_staff_by_tid(telegram_id)
    return staff if staff and staff["role"] in FULL_ACCESS_ROLES and full_access(staff) else None


def _show_staff_management(chat_id, text="🧑‍💼 مدیریت پرسنل\nانتخاب سمت برای مشاهده و ویرایش:"):
    send(chat_id, text, reply_markup=_staff_categories_keyboard())


def _show_staff_category(chat_id, role):
    if role not in ROLES_OK:
        _show_staff_management(chat_id, "سمت نامعتبر است.")
        return
    roles = ADMIN_ROLES if role == ADMIN else (role,)
    rows = db.list_staff(roles=roles)
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        kb.row(types.InlineKeyboardButton(
            f"{row['name']} — {phone_display(row)}", callback_data=f"staffm:person:{row['id']}"))
    kb.row(types.InlineKeyboardButton("⬅️ همه‌ی سمت‌ها", callback_data="staffm:home"))
    label = "ادمین" if role in ADMIN_ROLES else ROLE_FA.get(role, role)
    send(chat_id, f"{label} ({len(rows)} نفر):", reply_markup=kb)


def _show_staff_person(chat_id, staff_id):
    staff = next((row for row in db.list_staff() if row["id"] == staff_id), None)
    if not staff:
        _show_staff_management(chat_id, "این پرسنل در فهرست پیدا نشد.")
        return
    supervisor = f"\nسرپرست: {staff['supervisor']}" if staff["supervisor"] else ""
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"staffm:edit:{staff_id}"),
           types.InlineKeyboardButton("🗑 حذف", callback_data=f"staffm:delete:{staff_id}"))
    category_role = ADMIN if staff["role"] in ADMIN_ROLES else staff["role"]
    kb.row(types.InlineKeyboardButton("⬅️ بازگشت به سمت",
                                     callback_data=f"staffm:category:{category_role}"))
    send(chat_id, f"👤 {staff['name']}\nسمت: {staff_role_label(staff)}"
                  f"\nشماره: {phone_display(staff)}{supervisor}", reply_markup=kb)


def _show_staff_edit_fields(chat_id, staff):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("نام", callback_data=f"staffm:field:{staff['id']}:name"),
           types.InlineKeyboardButton("شماره", callback_data=f"staffm:field:{staff['id']}:phone"))
    kb.row(types.InlineKeyboardButton("سمت", callback_data=f"staffm:field:{staff['id']}:role"))
    if staff["role"] in ADMIN_ROLES:
        kb.row(types.InlineKeyboardButton("سرپرست", callback_data=f"staffm:field:{staff['id']}:supervisor"))
    kb.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=f"staffm:person:{staff['id']}"))
    send(chat_id, f"کدام موردِ «{staff['name']}» ویرایش شود؟", reply_markup=kb)


def _ask_staff_supervisor(telegram_id, data, action, staff_id=None):
    supervisors = db.list_staff(roles=(SUP,))
    if not supervisors:
        STAFF_MANAGEMENT_PENDING.pop(telegram_id, None)
        send(telegram_id, "سرپرستی در فهرست نیست؛ ابتدا یک سرپرست ثبت کن.",
             reply_markup=types.ReplyKeyboardRemove())
        return
    state = {"action": action, "step": "supervisor", "data": data}
    if staff_id is not None:
        state["staff_id"] = staff_id
    STAFF_MANAGEMENT_PENDING[telegram_id] = state
    prefix = "staffm:addsup" if action == "add" else f"staffm:editsup:{staff_id}"
    send(telegram_id, "سرپرست این عضو را انتخاب کن:",
         reply_markup=_staff_supervisor_keyboard(prefix))


def _finish_staff_operation(telegram_id, state):
    notify_failed = False
    try:
        if state["action"] == "delete":
            staff = next((row for row in db.list_staff()
                          if row["id"] == state["staff_id"]), None)
            if not staff:
                raise ValueError("این پرسنل دیگر در فهرست نیست.")
            staff = dict(staff)
            _delete_staff(staff, telegram_id)
            notify_failed = not _notify_staff_deletion(staff)
            result = f"🗑 «{staff['name']}» از فهرست و staff.csv حذف شد."
        else:
            previous = next((dict(row) for row in db.list_staff()
                             if row["id"] == state.get("staff_id")), None)
            record = _save_staff(state["data"], state.get("staff_id"))
            if previous and previous["role"] != record["role"]:
                notify_failed = not _notify_staff_role_change(previous, record)
            result = (f"✅ «{record['name']}» ({ROLE_FA[record['role']]}) "
                      "در دیتابیس و staff.csv ذخیره شد.")
    except (sqlite3.Error, OSError, ValueError) as error:
        logging.exception("staff management operation failed")
        STAFF_MANAGEMENT_PENDING.pop(telegram_id, None)
        send(telegram_id, f"انجام نشد: {error}",
             reply_markup=types.ReplyKeyboardRemove())
        return
    STAFF_MANAGEMENT_PENDING.pop(telegram_id, None)
    if notify_failed:
        result += "\n⚠️ منوی تلگرامِ فرد به‌روز نشد؛ از او بخواه /start بزند."
    send(telegram_id, result, reply_markup=menu_for(db.get_staff_by_tid(telegram_id)))


def _notify_staff_role_change(previous, record):
    if not previous["telegram_id"]:
        return True
    try:
        from h_staff import _show_commands

        _show_commands(previous["telegram_id"], record["role"])
        updated = db.get_staff_by_tid(previous["telegram_id"])
        return send(previous["telegram_id"],
                    f"سمت شما به «{ROLE_FA[record['role']]}» تغییر کرد. منوی جدید اعمال شد.",
                    reply_markup=menu_for(updated))
    except Exception:
        logging.exception("could not refresh Telegram menu after staff role change")
        return False


def _notify_staff_deletion(staff):
    if not staff["telegram_id"]:
        return True
    try:
        bot.set_my_commands(
            [types.BotCommand("start", "شروع یا احراز هویت")],
            scope=types.BotCommandScopeChat(chat_id=staff["telegram_id"]),
        )
        return send(staff["telegram_id"], "دسترسی شما به سامانه‌ی پرسنلی حذف شد.",
                    reply_markup=types.ReplyKeyboardRemove())
    except Exception:
        logging.exception("could not notify deleted staff member")
        return False


@bot.message_handler(func=lambda m: m.text == BTN_STAFF_MANAGEMENT, chat_types=["private"])
@staff_only(MANAGER, BOT_MANAGER)
def staff_management(m, s):
    if s.get("_admin_mode"):
        send(m.chat.id, "مدیریت پرسنل فقط در حالت مدیر ربات در دسترس است.")
        return
    _show_staff_management(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("staffm:"))
def staff_management_callback(c):
    staff = _staff_manager(c.from_user.id)
    if not staff:
        bot.answer_callback_query(c.id, "این بخش فقط برای مسئول شیفت و مدیر ربات است.", show_alert=True)
        return
    bot.answer_callback_query(c.id)
    parts = c.data.split(":")
    action = parts[1]
    chat_id = c.message.chat.id

    if action == "home":
        _show_staff_management(chat_id)
    elif action == "category" and len(parts) == 3:
        _show_staff_category(chat_id, parts[2])
    elif action == "person" and len(parts) == 3:
        _show_staff_person(chat_id, int(parts[2]))
    elif action == "add":
        STAFF_MANAGEMENT_PENDING[c.from_user.id] = {"action": "add", "step": "name", "data": {}}
        send(chat_id, "نام کامل پرسنل را بنویس:", reply_markup=_staff_cancel_keyboard())
    elif action == "edit" and len(parts) == 3:
        target = next((row for row in db.list_staff() if row["id"] == int(parts[2])), None)
        if target:
            _show_staff_edit_fields(chat_id, target)
        else:
            _show_staff_management(chat_id, "این پرسنل دیگر در فهرست نیست.")
    elif action == "field" and len(parts) == 4:
        target = next((row for row in db.list_staff() if row["id"] == int(parts[2])), None)
        field = parts[3]
        if not target or field not in ("name", "phone", "role", "supervisor"):
            _show_staff_management(chat_id, "درخواست ویرایش معتبر نیست.")
        elif field == "role":
            STAFF_MANAGEMENT_PENDING[c.from_user.id] = {
                "action": "edit", "step": "role", "staff_id": target["id"], "data": {
                    "name": target["name"], "phone": target["phone"], "role": target["role"],
                    "supervisor": target["supervisor"] or "",
                }}
            send(chat_id, "سمت جدید را انتخاب کن:", reply_markup=_staff_role_keyboard("staffm:editrole"))
        elif field == "supervisor":
            if target["role"] not in ADMIN_ROLES:
                _show_staff_management(chat_id, "فقط برای ادمین یا مدیر ربات می‌توان سرپرست تعیین کرد.")
            else:
                _ask_staff_supervisor(c.from_user.id, {
                    "name": target["name"], "phone": target["phone"], "role": target["role"],
                    "supervisor": "",
                }, "edit", target["id"])
        else:
            STAFF_MANAGEMENT_PENDING[c.from_user.id] = {
                "action": "edit", "step": field, "staff_id": target["id"],
                "data": {"name": target["name"], "phone": target["phone"],
                         "role": target["role"], "supervisor": target["supervisor"] or ""},
            }
            prompt = "نام جدید را بنویس:" if field == "name" else "شماره‌ی جدید را بنویس:"
            send(chat_id, prompt, reply_markup=_staff_cancel_keyboard())
    elif action == "delete" and len(parts) == 3:
        target = next((row for row in db.list_staff() if row["id"] == int(parts[2])), None)
        if target:
            kb = types.InlineKeyboardMarkup()
            kb.row(types.InlineKeyboardButton("بله، حذف شود", callback_data=f"staffm:deleteconfirm:{target['id']}"),
                   types.InlineKeyboardButton("لغو", callback_data=f"staffm:person:{target['id']}"))
            send(chat_id, f"«{target['name']}» ({ROLE_FA[target['role']]}) حذف شود؟", reply_markup=kb)
    elif action == "deleteconfirm" and len(parts) == 3:
        _finish_staff_operation(c.from_user.id, {
            "action": "delete", "staff_id": int(parts[2]), "data": {},
        })
    elif action == "addrole" and len(parts) == 3:
        state = STAFF_MANAGEMENT_PENDING.get(c.from_user.id)
        if not state or state["action"] != "add" or state["step"] != "role" or parts[2] not in ROLES_OK:
            send(chat_id, "این مرحله منقضی شده؛ از مدیریت پرسنل دوباره شروع کن.")
        else:
            state["data"]["role"] = parts[2]
            state["step"] = "phone"
            send(chat_id, "شماره‌ی موبایل را وارد کن:", reply_markup=_staff_cancel_keyboard())
    elif action == "editsup" and len(parts) == 4:
        state = STAFF_MANAGEMENT_PENDING.get(c.from_user.id)
        supervisor = next((row for row in db.list_staff(roles=(SUP,))
                           if row["id"] == int(parts[3])), None)
        if (state and state["action"] == "edit" and state["step"] == "supervisor"
                and state.get("staff_id") == int(parts[2]) and supervisor):
            state["data"]["supervisor"] = supervisor["name"]
            _finish_staff_operation(c.from_user.id, state)
    elif action == "addsup" and len(parts) == 3:
        state = STAFF_MANAGEMENT_PENDING.get(c.from_user.id)
        supervisor = next((row for row in db.list_staff(roles=(SUP,))
                           if row["id"] == int(parts[2])), None)
        if state and state["action"] == "add" and state["step"] == "supervisor" and supervisor:
            state["data"]["supervisor"] = supervisor["name"]
            _finish_staff_operation(c.from_user.id, state)
    elif action == "editrole" and len(parts) == 4:
        state = STAFF_MANAGEMENT_PENDING.get(c.from_user.id)
        role = parts[3]
        if state and state["action"] == "edit" and state["step"] == "role" and role in ROLES_OK:
            state["data"]["role"] = role
            if role in ADMIN_ROLES:
                _ask_staff_supervisor(c.from_user.id, state["data"], "edit", state["staff_id"])
            else:
                state["data"]["supervisor"] = ""
                _finish_staff_operation(c.from_user.id, state)
    elif action == "cancel":
        STAFF_MANAGEMENT_PENDING.pop(c.from_user.id, None)
        send(chat_id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                      and m.from_user.id in STAFF_MANAGEMENT_PENDING and bool(m.text)
                      and (not m.text.startswith("/") or m.text == "/cancel"))
def staff_management_input(m):
    state = STAFF_MANAGEMENT_PENDING.get(m.from_user.id)
    staff = _staff_manager(m.from_user.id)
    if not state or not staff:
        STAFF_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "این مرحله لغو شد یا دسترسی شما تغییر کرده است.")
        return
    text = normalize(m.text).strip()
    if text in ("❌ لغو", "/cancel"):
        STAFF_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        return
    if state["action"] == "add":
        if state["step"] == "name":
            if not text:
                send(m.chat.id, "نام نمی‌تواند خالی باشد؛ دوباره بنویس:")
                return
            state["data"]["name"] = text
            state["step"] = "role"
            send(m.chat.id, "سمت را انتخاب کن:", reply_markup=_staff_role_keyboard("staffm:addrole"))
            return
        if state["step"] != "phone":
            send(m.chat.id, "از دکمه‌های مرحله‌ی فعلی استفاده کن یا «لغو» را بزن.")
            return
        phone = normalize_phone(text)
        if len(phone) != 10:
            send(m.chat.id, "شماره معتبر نیست؛ شماره‌ی موبایل ۱۱ رقمی را دوباره وارد کن:")
            return
        state["data"]["phone"] = phone
        if state["data"]["role"] == ADMIN:
            _ask_staff_supervisor(m.from_user.id, state["data"], "add")
        else:
            state["data"]["supervisor"] = ""
            _finish_staff_operation(m.from_user.id, state)
        return
    if state["step"] in ("name", "phone"):
        field = state["step"]
        if field == "name":
            if not text:
                send(m.chat.id, "نام نمی‌تواند خالی باشد؛ دوباره بنویس:")
                return
            state["data"]["name"] = text
        else:
            phone = normalize_phone(text)
            if len(phone) != 10:
                send(m.chat.id, "شماره معتبر نیست؛ شماره‌ی موبایل ۱۱ رقمی را دوباره وارد کن:")
                return
            state["data"]["phone"] = phone
        _finish_staff_operation(m.from_user.id, state)
        return
    send(m.chat.id, "از دکمه‌های مرحله‌ی فعلی استفاده کن یا «لغو» را بزن.")
