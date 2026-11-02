"""سه ابزار برنامه و اطلاعات استادها برای مدیران مجموعه."""
from html import escape

from telebot import types

import db
from analysis import (jalali_day_month, now_tehran, reporting_period,
                      sort_entries, span, today_tomorrow)
from app import (BTN_COMPANY_PHONES, BTN_COMPANY_TEACHER, BTN_COMPANY_TODAY,
                 COMPANY_MANAGER, bot, send, staff_only)
from schedule_parser import DAYS, key, normalize, split_people

pending = {}


def _is_company_manager(telegram_id):
    staff = db.get_staff_by_tid(telegram_id)
    return bool(staff and staff["role"] == COMPANY_MANAGER)


def _send_today_schedule(chat_id):
    now = now_tehran()
    today, _, _ = today_tomorrow(now)
    target_date = now.date().isoformat()
    entries = db.entries_on_date(target_date)
    if not entries:
        send(chat_id, f"برای امروز ({today}) برنامه‌ای در دیتابیس ثبت نشده است.")
        return
    messages = _today_schedule_messages(today, target_date, entries)
    for message in messages:
        send(chat_id, message, parse_mode="HTML")


def _today_schedule_messages(today, target_date, entries):
    persian_digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

    def fa(value):
        return str(value).translate(persian_digits)

    classes = {}
    support = []
    for entry in entries:
        if entry["kind"] == "class":
            teacher = entry.get("teacher") or "استاد نامشخص"
            classes.setdefault(teacher, []).append(entry)
        else:
            support.append(entry)

    studios = {entry["studio"] for entry in entries
               if entry["kind"] == "class" and entry.get("studio")}
    admins = {
        key(normalize(person))
        for entry in entries if entry["kind"] == "class"
        for person in split_people(entry.get("person"))
    }
    support_people = {
        person
        for entry in support
        for person in split_people(entry.get("person"))
    }
    start_min = min(entry["start_min"] for entry in entries)
    end_min = max(entry["end_min"] for entry in entries)
    date_label = fa(f"{today} | {jalali_day_month(target_date)}")
    header = (f"📅 <b>{escape(date_label)}</b>\n"
              "━━━━━━━━━━━━━━━━━━\n\n"
              f"📊 <b>خلاصه روز</b>\n"
              f"{fa(len([e for e in entries if e['kind'] == 'class']))} کلاس"
              f" | {fa(len(studios))} استودیو\n"
              f"{fa(len(admins))} ادمین\n"
              f"{fa(len(support_people))} نیروی پشتیبانی\n"
              f"{fa(span({'start_min': start_min, 'end_min': end_min}))}\n"
              "━━━━━━━━━━━━━━━━━━")

    sections = []
    for teacher in sorted(classes, key=lambda name: normalize(name).casefold()):
        lines = [f"استاد <b>{escape(teacher)}</b>"]
        for entry in sorted(classes[teacher], key=lambda row: (
                row["start_min"], row["end_min"])):
            studio = escape(entry.get("studio") or "آنلاین")
            operators = split_people(entry.get("person"))
            operator = " و ".join(f"<b>{escape(name)}</b>" for name in operators)
            if not operator:
                operator = "<b>—</b>"
            lines.append(
                f"🕒 <b>{escape(fa(span(entry)))}</b>"
                f" | <b>{studio}</b>"
                f" | اپراتور: {operator}"
            )
        sections.append("\n".join(lines))

    if support:
        lines = ["<b>نیروهای پشتیبانی</b>"]
        labels = {"nazer": ("🛡", "ناظر"), "komaki": ("🙋", "کمکی")}
        for entry in sorted(support, key=lambda row: (
                row["start_min"], row["end_min"], row["kind"])):
            icon, label = labels.get(entry["kind"], ("•", entry["kind"]))
            person = escape(entry.get("person") or "—")
            detail = (f" | {escape(entry['course'])}"
                      if entry.get("course") else "")
            lines.append(
                f"{icon} <b>{label}</b> | "
                f"🕒 <b>{escape(fa(span(entry)))}</b>"
                f"{detail} | <b>{person}</b>"
            )
        sections.append("\n".join(lines))

    messages = []
    current = header
    for section in sections:
        candidate = f"{current}\n\n{section}"
        if len(candidate) > 3500 and current != header:
            messages.append(current)
            continuation = f"📅 <b>{escape(date_label)} (ادامه)</b>"
            current = f"{continuation}\n━━━━━━━━━━━━━━━━━━\n\n{section}"
        else:
            current = candidate
    messages.append(current)
    return messages


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text == BTN_COMPANY_TODAY)
@staff_only(COMPANY_MANAGER)
def company_today(m, _staff):
    _send_today_schedule(m.chat.id)


def _teacher_names():
    names = set(db.get_known("teacher"))
    names.update(a["teacher"] for a in db.list_teacher_assignments())
    names.update(e["teacher"] for e in db.active_entries()
                 if e["kind"] == "class" and e.get("teacher"))
    return sorted(names, key=lambda name: normalize(name).casefold())


def _search_matches(query, names):
    search_key = key(normalize(query).strip())
    return [name for name in names if search_key in key(normalize(name))] if search_key else []


def _ask_search(chat_id, telegram_id, mode):
    pending[telegram_id] = {"mode": mode}
    message = ("فامیلی استاد را بفرست:"
               if mode == "teacher" else "فامیلی استاد را برای جستجوی شماره بفرست:")
    send(chat_id, message)


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text == BTN_COMPANY_TEACHER)
@staff_only(COMPANY_MANAGER)
def company_teacher_search(m, _staff):
    _ask_search(m.chat.id, m.from_user.id, "teacher")


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text == BTN_COMPANY_PHONES)
@staff_only(COMPANY_MANAGER)
def company_phone_menu(m, _staff):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("فهرست همه‌ی شماره‌ها", callback_data="company:phones:list"),
        types.InlineKeyboardButton("جستجوی شماره", callback_data="company:phones:search"),
    )
    send(m.chat.id, "فهرست شماره‌های استادان یا جستجوی یک استاد را انتخاب کن:",
         reply_markup=keyboard)


def _teacher_phone_entries():
    return [assignment for assignment in db.list_teacher_assignments()
            if assignment.get("phone") and assignment["phone"].strip()]


def _send_phone_list(chat_id):
    assignments = _teacher_phone_entries()
    if not assignments:
        send(chat_id, "فعلاً شماره‌ای برای استادان ثبت نشده است.")
        return
    lines = ["📞 شماره تماس استادان:", ""]
    lines.extend(f"• {escape(item['teacher'])}: {escape(item['phone'])}"
                 for item in assignments)
    send(chat_id, "\n".join(lines), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("company:"))
def company_callback(c):
    bot.answer_callback_query(c.id)
    if not _is_company_manager(c.from_user.id):
        send(c.message.chat.id, "این قابلیت فقط برای مدیران مجموعه فعال است.")
        return
    parts = c.data.split(":")
    if parts == ["company", "phones", "list"]:
        _send_phone_list(c.message.chat.id)
        return
    if parts == ["company", "phones", "search"]:
        _ask_search(c.message.chat.id, c.from_user.id, "phone")
        return
    if len(parts) != 3 or parts[1] != "select" or not parts[2].isdigit():
        return
    state = pending.get(c.from_user.id)
    index = int(parts[2])
    if not state or index >= len(state.get("matches", [])):
        send(c.message.chat.id, "این جستجو منقضی شده؛ دوباره جستجو را انجام بده.")
        return
    name = state["matches"][index]
    mode = state["mode"]
    pending.pop(c.from_user.id, None)
    if mode == "phone":
        assignment = db.get_teacher_assignment(name)
        if not assignment or not assignment.get("phone"):
            send(c.message.chat.id, f"برای استاد «{escape(name)}» شماره‌ای ثبت نشده است.",
                 parse_mode="HTML")
            return
        send(c.message.chat.id,
             f"📞 شماره تماس استاد «{escape(name)}»: {escape(assignment['phone'])}",
             parse_mode="HTML")
        return
    _send_teacher_report(c.message.chat.id, name)


@bot.message_handler(func=lambda m: (
    m.chat.type == "private" and bool(m.text)
    and m.from_user.id in pending
    and (not m.text.startswith("/") or m.text == "/cancel")
))
@staff_only(COMPANY_MANAGER)
def company_search_input(m, _staff):
    state = pending.get(m.from_user.id)
    if not state:
        return
    query = m.text
    if query in ("❌ لغو", "/cancel"):
        pending.pop(m.from_user.id, None)
        send(m.chat.id, "جستجو لغو شد.")
        return
    names = (_teacher_names() if state["mode"] == "teacher" else
             [item["teacher"] for item in _teacher_phone_entries()])
    matches = _search_matches(query, names)
    if not matches:
        send(m.chat.id, "استادی با این فامیلی پیدا نشد؛ دوباره بفرست:")
        return
    if len(matches) == 1:
        pending.pop(m.from_user.id, None)
        if state["mode"] == "teacher":
            _send_teacher_report(m.chat.id, matches[0])
        else:
            assignment = db.get_teacher_assignment(matches[0])
            if assignment and assignment.get("phone"):
                send(m.chat.id,
                     f"📞 شماره تماس استاد «{escape(assignment['teacher'])}»: "
                     f"{escape(assignment['phone'])}", parse_mode="HTML")
            else:
                send(m.chat.id, f"برای استاد «{escape(matches[0])}» شماره‌ای ثبت نشده است.")
        return
    state["matches"] = matches
    keyboard = types.InlineKeyboardMarkup()
    for index, name in enumerate(matches[:50]):
        keyboard.row(types.InlineKeyboardButton(
            name, callback_data=f"company:select:{index}"))
    send(m.chat.id, "چند استاد پیدا شد؛ یکی را انتخاب کن:", reply_markup=keyboard)


def _send_teacher_report(chat_id, teacher):
    now = now_tehran()
    today, _, minute = today_tomorrow(now)
    today_date = now.date().isoformat()
    assignment = db.get_teacher_assignment(teacher)
    phone = (f"\n📞 شماره تماس: {escape(assignment['phone'])}"
             if assignment and assignment.get("phone") else "\n📞 شماره تماس ثبت نشده است")

    today_rows = db.entries_for_teacher(teacher, shift_date=today_date)
    today_lines = [f"• {span(row)} | {escape(row.get('studio') or 'آنلاین')} | "
                   f"{escape(row.get('course') or 'بدون عنوان')}"
                   for row in sort_entries(today_rows)]
    today_text = "\n".join(today_lines) if today_lines else "امروز برنامه‌ای ندارد."

    week_start, week_end = reporting_period("week", now)
    week_rows = []
    schedule_dates = db.active_schedule_dates()
    for row in db.entries_for_teacher(teacher):
        shift_date = row.get("shift_date") or schedule_dates.get(row["day"])
        if shift_date and week_start <= shift_date <= week_end:
            week_rows.append({**row, "_display_date": shift_date})
    grouped = {day: [] for day in DAYS}
    for row in sort_entries(week_rows):
        grouped[row["day"]].append(row)
    week_lines = []
    for day in DAYS:
        for row in grouped[day]:
            week_lines.append(
                f"• {escape(day)} {escape(row['_display_date'])} | {span(row)} | "
                f"{escape(row.get('studio') or 'آنلاین')} | "
                f"{escape(row.get('course') or 'بدون عنوان')}"
            )
    week_text = "\n".join(week_lines) if week_lines else "برای این هفته برنامه‌ای ندارد."

    current_rows = [row for row in today_rows
                    if row["start_min"] <= minute < row["end_min"]]
    if current_rows:
        location = "؛ ".join(
            f"{escape(row.get('studio') or 'کلاس آنلاین')} ({span(row)})"
            for row in current_rows
        )
    else:
        location = "در حال حاضر کلاس فعالی ندارد."
    send(chat_id, f"👩‍🏫 استاد: {escape(teacher)}{phone}\n\n"
                  f"📅 برنامه‌ی امروز ({today}):\n{today_text}\n\n"
                  f"🗓 برنامه‌ی این هفته:\n{week_text}\n\n"
                  f"📍 موقعیت فعلی: {location}", parse_mode="HTML")
