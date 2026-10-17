"""پنل‌های اختصاصی مدیر اصلی: تاریخ شیفت‌ها و تخصیص استادها."""
import logging
import re
import sqlite3
from datetime import date

from telebot import types

import db
from analysis import (
    day_label, day_of, jalali_date_parts, jalali_day_month, jalali_to_gregorian,
)
from app import (OWNER_TELEGRAM_ID, BTN_SCHEDULE_DATES, BTN_TEACHER_MANAGEMENT,
                 bot, send)
from schedule_parser import key, load_list, normalize

SCHEDULE_DATE_PENDING = {}
TEACHER_MANAGEMENT_PENDING = {}
TEACHER_USERNAME_RE = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}\Z")
KNOWN_BASES = {
    "teacher": load_list("data/teachers.txt"),
    "studio": load_list("data/studios.txt"),
    "remote_teacher": load_list("data/remote_teachers.txt"),
}


def _is_owner(telegram_id):
    return telegram_id == OWNER_TELEGRAM_ID and db.get_staff_by_tid(telegram_id) is not None


def _cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.row(types.KeyboardButton("❌ لغو"))
    return keyboard


def _parse_date(text):
    value = normalize(text).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    match = re.fullmatch(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", value)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return jalali_to_gregorian(year, month, day)
    raise ValueError("تاریخ را شمسی به‌شکل سال/ماه/روز یا میلادی به‌شکل YYYY-MM-DD وارد کن.")


def _schedule_date_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    sheets = db.active_sheets()
    for sheet in sheets:
        jalali_date = jalali_day_month(sheet["scheduled_date"]) if sheet["scheduled_date"] else "بدون تاریخ"
        keyboard.row(types.InlineKeyboardButton(
            f"{sheet['day']} {jalali_date} — {sheet['entry_count']} ردیف",
            callback_data=f"shiftdate:sheet:{sheet['id']}"))
    return keyboard, sheets


@bot.message_handler(func=lambda m: m.text == BTN_SCHEDULE_DATES, chat_types=["private"])
def schedule_date_panel(m):
    if not _is_owner(m.from_user.id):
        send(m.chat.id, "این پنل فقط برای مدیر اصلی فعال است.")
        return
    keyboard, sheets = _schedule_date_keyboard()
    if not sheets:
        send(m.chat.id, "شیفت فعالی برای اصلاح تاریخ وجود ندارد.")
        return
    send(m.chat.id, "📅 شیفتی را برای اصلاح تاریخ انتخاب کن:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("shiftdate:"))
def schedule_date_callback(c):
    if not _is_owner(c.from_user.id):
        bot.answer_callback_query(c.id, "این پنل فقط برای مدیر اصلی فعال است.", show_alert=True)
        return
    bot.answer_callback_query(c.id)
    parts = c.data.split(":")
    if len(parts) != 3 or parts[1] != "sheet" or not parts[2].isdigit():
        return
    sheet_id = int(parts[2])
    sheet = next((item for item in db.active_sheets() if item["id"] == sheet_id), None)
    if not sheet:
        send(c.message.chat.id, "این شیفت دیگر فعال نیست؛ فهرست را دوباره باز کن.")
        return
    SCHEDULE_DATE_PENDING[c.from_user.id] = {"sheet_id": sheet_id, "day": sheet["day"]}
    current_date = sheet["scheduled_date"]
    jalali_year, jalali_month, jalali_day = jalali_date_parts(current_date)
    send(c.message.chat.id,
         f"تاریخ فعلی {sheet['day']}: {jalali_year:04d}/{jalali_month:02d}/{jalali_day:02d}\n"
         "تاریخ جدید را شمسی به‌شکل سال/ماه/روز وارد کن؛ ISO میلادی YYYY-MM-DD هم پذیرفته می‌شود.",
         reply_markup=_cancel_keyboard())


def _notify_schedule_date_change(sheet, old_date, new_date):
    entries = db.entries_for_sheet(sheet["id"])
    recipients = {entry["person"] for entry in entries if entry.get("person")}
    text = (f"📅 تاریخ شیفت {sheet['day']} اصلاح شد: "
            f"{day_label(sheet['day'], old_date, html=False)} ← "
            f"{day_label(sheet['day'], new_date, html=False)}")
    failed = []
    for name in recipients:
        member = db.get_staff_by_name(name)
        if member and member["telegram_id"] and not send(member["telegram_id"], text):
            failed.append(name)
    if failed:
        logging.warning("date correction notification failed for staff: %s", ", ".join(sorted(failed)))
    return len(recipients) - len(failed), failed


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                      and m.from_user.id in SCHEDULE_DATE_PENDING and bool(m.text)
                      and (not m.text.startswith("/") or m.text == "/cancel"))
def schedule_date_input(m):
    state = SCHEDULE_DATE_PENDING.get(m.from_user.id)
    if not _is_owner(m.from_user.id) or not state:
        SCHEDULE_DATE_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "این مرحله لغو شد یا دسترسی شما تغییر کرده است.")
        return
    if m.text.strip() in ("❌ لغو", "/cancel"):
        SCHEDULE_DATE_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        return
    try:
        new_date = _parse_date(m.text)
    except (ValueError, OverflowError) as error:
        send(m.chat.id, str(error), reply_markup=_cancel_keyboard())
        return
    if day_of(date.fromisoformat(new_date)) != state["day"]:
        send(m.chat.id, f"این تاریخ {day_of(date.fromisoformat(new_date))} است، نه {state['day']}؛ تاریخ روزِ درست را وارد کن.")
        return
    sheet = next((item for item in db.active_sheets() if item["id"] == state["sheet_id"]), None)
    if not sheet:
        SCHEDULE_DATE_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "این شیفت دیگر فعال نیست؛ اصلاح تاریخ انجام نشد.")
        return
    old_date = sheet["scheduled_date"]
    if not db.update_sheet_schedule_date(sheet["id"], new_date):
        SCHEDULE_DATE_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "ذخیره‌ی تاریخ انجام نشد؛ شیفت پیدا نشد.")
        return
    notified, failed = _notify_schedule_date_change(sheet, old_date, new_date)
    SCHEDULE_DATE_PENDING.pop(m.from_user.id, None)
    year, month, day = jalali_date_parts(new_date)
    result = f"✅ تاریخ همه‌ی ردیف‌های شیفت {sheet['day']} به {year:04d}/{month:02d}/{day:02d} تغییر کرد."
    if notified:
        result += f"\nاصلاحیه برای {notified} نفر ارسال شد."
    if failed:
        result += f"\n⚠️ ارسال اصلاحیه برای این افراد ناموفق بود: {', '.join(sorted(failed))}"
    send(m.chat.id, result, reply_markup=types.ReplyKeyboardRemove())


def _teacher_panel(chat_id):
    assignments = db.list_teacher_assignments()
    keyboard = types.InlineKeyboardMarkup()
    for assignment in assignments:
        keyboard.row(types.InlineKeyboardButton(
            f"{assignment['teacher']} — {assignment['supervisor']}",
            callback_data=f"teacherm:select:{assignment['id']}"))
    keyboard.row(types.InlineKeyboardButton("➕ افزودن استاد", callback_data="teacherm:add"))
    keyboard.row(types.InlineKeyboardButton("🗂 نام استادها، استودیوها و آنلاین‌ها",
                                            callback_data="teacherm:names"))
    send(chat_id, f"👩‍🏫 مدیریت استادها ({len(assignments)} تخصیص)", reply_markup=keyboard)


KNOWN_LABELS = {
    "teacher": "استاد",
    "studio": "استودیو",
    "remote_teacher": "استاد آنلاین",
}
KNOWN_PLURALS = {
    "teacher": "استادها",
    "studio": "استودیوها",
    "remote_teacher": "استادهای آنلاین/راه دور",
}


def _known_names_panel(chat_id):
    keyboard = types.InlineKeyboardMarkup()
    for kind in KNOWN_LABELS:
        names = _known_names(kind)
        keyboard.row(types.InlineKeyboardButton(
            f"{KNOWN_PLURALS[kind]} ({len(names)})", callback_data=f"teacherm:names:{kind}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="teacherm:home"))
    send(chat_id, "نام‌های مورد استفاده برای تشخیص برنامه:", reply_markup=keyboard)


def _known_name_list(chat_id, kind):
    names = _known_names(kind)
    keyboard = types.InlineKeyboardMarkup()
    for index, name in enumerate(names):
        keyboard.row(types.InlineKeyboardButton(
            name, callback_data=f"teacherm:name:{kind}:{index}"))
    keyboard.row(types.InlineKeyboardButton(
        f"➕ افزودن {KNOWN_LABELS[kind]}", callback_data=f"teacherm:nameadd:{kind}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="teacherm:names"))
    send(chat_id, f"نام‌های ثبت‌شده‌ی {KNOWN_PLURALS[kind]} ({len(names)}):",
         reply_markup=keyboard)


def _known_names(kind):
    excluded = set(db.get_known_excluded(kind))
    names = {key(name): name for name in KNOWN_BASES[kind].values() if key(name) not in excluded}
    names.update({key(name): name for name in db.get_known(kind) if key(name) not in excluded})
    return sorted(names.values())


def _known_name_detail(chat_id, kind, index):
    names = _known_names(kind)
    if index >= len(names):
        _known_name_list(chat_id, kind)
        return
    canonical = names[index]
    aliases = db.list_known_aliases(kind, canonical)
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton(
        "✏️ ویرایش اسم خروجی", callback_data=f"teacherm:namerename:{kind}:{index}"))
    keyboard.row(types.InlineKeyboardButton(
        "➕ افزودن شکل ورودی", callback_data=f"teacherm:aliasadd:{kind}:{index}"))
    for alias in aliases:
        keyboard.row(types.InlineKeyboardButton(
            f"🗑 {alias['alias']}", callback_data=f"teacherm:aliasdelete:{kind}:{alias['id']}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=f"teacherm:names:{kind}"))
    details = "\n".join(f"• {item['alias']}" for item in aliases) or "—"
    send(chat_id, f"اسم خروجی برنامه: {canonical}\nشکل‌های ورودی:\n{details}",
         reply_markup=keyboard)


def _teacher_supervisor_keyboard(action, assignment_id=None):
    keyboard = types.InlineKeyboardMarkup()
    prefix = "teacherm:addsup" if action == "add" else f"teacherm:editsup:{assignment_id}"
    for supervisor in db.list_staff(roles=("supervisor",)):
        keyboard.row(types.InlineKeyboardButton(
            supervisor["name"], callback_data=f"{prefix}:{supervisor['id']}"))
    keyboard.row(types.InlineKeyboardButton("❌ لغو", callback_data="teacherm:cancel"))
    return keyboard


def _teacher_detail(chat_id, assignment):
    group = next((item for item in db.list_teacher_groups()
                  if item["teacher_key"] == assignment["teacher_key"]), None)
    group_status = f"وصل به گروه {group['chat_id']}" if group else "گروه تلگرام وصل نشده"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"teacherm:edit:{assignment['id']}"),
                 types.InlineKeyboardButton("🗑 حذف", callback_data=f"teacherm:delete:{assignment['id']}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="teacherm:home"))
    send(chat_id, f"👩‍🏫 {assignment['teacher']}\nسرپرست: {assignment['supervisor']}\n"
                  f"نام کاربری: @{assignment['username'] or 'ثبت نشده'}\n{group_status}",
         reply_markup=keyboard)


def _teacher_edit_fields(chat_id, assignment):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("نام استاد", callback_data=f"teacherm:field:{assignment['id']}:teacher"),
                 types.InlineKeyboardButton("سرپرست", callback_data=f"teacherm:field:{assignment['id']}:supervisor"))
    keyboard.row(types.InlineKeyboardButton("نام کاربری", callback_data=f"teacherm:field:{assignment['id']}:username"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=f"teacherm:select:{assignment['id']}"))
    send(chat_id, f"کدام مشخصه‌ی «{assignment['teacher']}» ویرایش شود؟", reply_markup=keyboard)


def _finish_teacher_save(telegram_id, state):
    data = state["data"]
    username = data.get("username", "").strip().removeprefix("@")
    if not data.get("teacher", "").strip() or not data.get("supervisor", "").strip():
        send(telegram_id, "نام استاد و سرپرست الزامی‌اند.")
        return
    if not TEACHER_USERNAME_RE.fullmatch(username):
        send(telegram_id, "نام کاربری تلگرام معتبر نیست؛ مثل @teacher_name واردش کن.")
        return
    try:
        if state["action"] == "add":
            db.assign_teacher(data["teacher"].strip(), data["supervisor"], username)
        elif not db.update_teacher_assignment(state["original_teacher"], data["teacher"].strip(),
                                              data["supervisor"], username):
            raise ValueError("این تخصیص استاد دیگر در فهرست نیست.")
        db.add_known("teacher", [data["teacher"].strip()])
    except (sqlite3.Error, ValueError) as error:
        logging.exception("teacher management save failed")
        TEACHER_MANAGEMENT_PENDING.pop(telegram_id, None)
        send(telegram_id, f"ذخیره انجام نشد: {error}", reply_markup=types.ReplyKeyboardRemove())
        return
    TEACHER_MANAGEMENT_PENDING.pop(telegram_id, None)
    send(telegram_id, f"✅ استاد «{data['teacher']}» به {data['supervisor']} با @{username} ذخیره شد.",
         reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: m.text == BTN_TEACHER_MANAGEMENT, chat_types=["private"])
def teacher_management(m):
    if not _is_owner(m.from_user.id):
        send(m.chat.id, "این پنل فقط برای مدیر اصلی فعال است.")
        return
    _teacher_panel(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("teacherm:"))
def teacher_management_callback(c):
    if not _is_owner(c.from_user.id):
        bot.answer_callback_query(c.id, "این پنل فقط برای مدیر اصلی فعال است.", show_alert=True)
        return
    bot.answer_callback_query(c.id)
    parts = c.data.split(":")
    action = parts[1]
    chat_id = c.message.chat.id
    if action == "home":
        _teacher_panel(chat_id)
    elif action == "names":
        if len(parts) == 2:
            _known_names_panel(chat_id)
        elif len(parts) == 3 and parts[2] in KNOWN_LABELS:
            _known_name_list(chat_id, parts[2])
    elif action == "nameadd" and len(parts) == 3 and parts[2] in KNOWN_LABELS:
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "known_add", "step": "known_name", "kind": parts[2], "data": {},
        }
        send(chat_id, f"نام کامل {KNOWN_LABELS[parts[2]]} را وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "name" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        _known_name_detail(chat_id, parts[2], int(parts[3]))
    elif action == "namerename" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2])
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "known_edit", "step": "known_name", "kind": parts[2],
            "original_name": names[index], "data": {},
        }
        send(chat_id, f"نام جدید {KNOWN_LABELS[parts[2]]} را وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "aliasadd" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2])
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "alias_add", "step": "alias", "kind": parts[2],
            "canonical": names[index], "data": {},
        }
        send(chat_id, f"هر متن ورودی که باید به «{names[index]}» تبدیل شود وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "aliasdelete" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        try:
            deleted = db.delete_known_alias(int(parts[3]))
        except sqlite3.Error as error:
            logging.exception("known-name alias deletion failed")
            send(chat_id, f"حذف شکل ورودی انجام نشد: {error}")
            return
        send(chat_id, "✅ شکل ورودی حذف شد." if deleted else "این شکل ورودی دیگر ثبت نیست.")
        _known_names_panel(chat_id)
    elif action == "namedelete" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2])
            return
        try:
            name = names[index]
            db.delete_known(parts[2], name)
            if parts[2] == "teacher" and key(name) in {
                    key(remote_name) for remote_name in _known_names("remote_teacher")}:
                db.delete_known("remote_teacher", name)
            elif parts[2] == "remote_teacher":
                db.delete_known("teacher", name)
        except sqlite3.Error as error:
            logging.exception("known-name deletion failed")
            send(chat_id, f"حذف انجام نشد: {error}")
            return
        send(chat_id, f"✅ «{name}» از فهرست {KNOWN_LABELS[parts[2]]} حذف شد.")
        _known_name_list(chat_id, parts[2])
    elif action == "add":
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "add", "step": "teacher", "data": {},
        }
        send(chat_id, "نام استاد را وارد کن:", reply_markup=_cancel_keyboard())
    elif action == "select" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            _teacher_detail(chat_id, assignment)
    elif action == "edit" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            _teacher_edit_fields(chat_id, assignment)
    elif action == "field" and len(parts) == 4 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if not assignment or parts[3] not in ("teacher", "supervisor", "username"):
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "edit", "step": parts[3], "assignment_id": assignment["id"],
            "original_teacher": assignment["teacher"],
            "data": {"teacher": assignment["teacher"], "supervisor": assignment["supervisor"],
                     "username": assignment["username"]},
        }
        if parts[3] == "supervisor":
            send(chat_id, "سرپرست جدید را انتخاب کن:",
                 reply_markup=_teacher_supervisor_keyboard("edit", assignment["id"]))
        else:
            label = "نام جدید استاد را وارد کن:" if parts[3] == "teacher" else "نام کاربری جدید را وارد کن:"
            send(chat_id, label, reply_markup=_cancel_keyboard())
    elif action == "addsup" and len(parts) == 3 and parts[2].isdigit():
        state = TEACHER_MANAGEMENT_PENDING.get(c.from_user.id)
        supervisor = next((row for row in db.list_staff(roles=("supervisor",))
                           if row["id"] == int(parts[2])), None)
        if state and state["action"] == "add" and state["step"] == "supervisor" and supervisor:
            state["data"]["supervisor"] = supervisor["name"]
            state["step"] = "username"
            send(chat_id, "نام کاربری تلگرام استاد را وارد کن (مثال: @teacher_name):",
                 reply_markup=_cancel_keyboard())
    elif action == "editsup" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        state = TEACHER_MANAGEMENT_PENDING.get(c.from_user.id)
        supervisor = next((row for row in db.list_staff(roles=("supervisor",))
                           if row["id"] == int(parts[3])), None)
        if (state and state["action"] == "edit" and state["step"] == "supervisor"
                and state.get("assignment_id") == int(parts[2]) and supervisor):
            state["data"]["supervisor"] = supervisor["name"]
            _finish_teacher_save(c.from_user.id, state)
    elif action == "delete" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            keyboard = types.InlineKeyboardMarkup()
            keyboard.row(
                types.InlineKeyboardButton("بله، حذف شود",
                                           callback_data=f"teacherm:deleteconfirm:{assignment['id']}"),
                types.InlineKeyboardButton("لغو", callback_data=f"teacherm:select:{assignment['id']}"))
            send(chat_id, f"تخصیص استاد «{assignment['teacher']}» و اتصال گروهش حذف شود؟",
                 reply_markup=keyboard)
    elif action == "deleteconfirm" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment and db.delete_teacher_assignment(assignment["teacher"]):
            send(chat_id, f"🗑 تخصیص استاد «{assignment['teacher']}» و اتصال گروهش حذف شد.")
        else:
            send(chat_id, "تخصیص استاد پیدا نشد.")
    elif action == "cancel":
        TEACHER_MANAGEMENT_PENDING.pop(c.from_user.id, None)
        send(chat_id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                      and m.from_user.id in TEACHER_MANAGEMENT_PENDING and bool(m.text)
                      and (not m.text.startswith("/") or m.text == "/cancel"))
def teacher_management_input(m):
    state = TEACHER_MANAGEMENT_PENDING.get(m.from_user.id)
    if not _is_owner(m.from_user.id) or not state:
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "این مرحله لغو شد یا دسترسی شما تغییر کرده است.")
        return
    text = normalize(m.text).strip()
    if text in ("❌ لغو", "/cancel"):
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        return
    field = state["step"]
    if field == "alias":
        try:
            canonical_names = _known_names("teacher" if state["kind"] == "remote_teacher"
                                           else state["kind"])
            if any(key(name) == key(text) and key(name) != key(state["canonical"])
                   for name in canonical_names):
                raise ValueError("این متن خودش نام خروجیِ دیگری است؛ شکل ورودی متفاوتی انتخاب کن.")
            db.add_known_alias(state["kind"], text, state["canonical"])
        except (sqlite3.Error, ValueError) as error:
            logging.exception("known-name alias save failed")
            send(m.chat.id, f"ثبت شکل ورودی انجام نشد: {error}\nیک متن ورودی دیگر بفرست:",
                 reply_markup=_cancel_keyboard())
            return
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, f"✅ «{text}» از این به بعد به «{state['canonical']}» تبدیل می‌شود.",
             reply_markup=types.ReplyKeyboardRemove())
        return
    if field == "known_name":
        if not text:
            send(m.chat.id, "نام نمی‌تواند خالی باشد؛ دوباره وارد کن:")
            return
        kind = state["kind"]
        try:
            if state["action"] == "known_add":
                db.add_known(kind, [text])
            else:
                original = state["original_name"]
                db.rename_known(kind, original, text)
                if kind == "teacher" and key(original) in {
                        key(remote_name) for remote_name in _known_names("remote_teacher")}:
                    db.rename_known("remote_teacher", original, text)
                elif kind == "remote_teacher":
                    db.delete_known("teacher", original)
            if kind == "remote_teacher":
                db.add_known("teacher", [text])
        except (sqlite3.Error, ValueError) as error:
            logging.exception("known-name management operation failed")
            TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
            send(m.chat.id, f"ذخیره انجام نشد: {error}",
                 reply_markup=types.ReplyKeyboardRemove())
            return
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, f"✅ «{text}» در فهرست {KNOWN_LABELS[kind]} ذخیره شد؛ "
                        "از برنامه‌ی بعدی اعمال می‌شود.",
             reply_markup=types.ReplyKeyboardRemove())
        return
    if field == "teacher":
        if not text:
            send(m.chat.id, "نام استاد نمی‌تواند خالی باشد؛ دوباره وارد کن:")
            return
        state["data"]["teacher"] = text
        if state["action"] == "add":
            supervisors = db.list_staff(roles=("supervisor",))
            if not supervisors:
                TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
                send(m.chat.id, "سرپرستی ثبت نشده؛ ابتدا سرپرست را از مدیریت پرسنل اضافه کن.")
                return
            state["step"] = "supervisor"
            send(m.chat.id, "سرپرست استاد را انتخاب کن:",
                 reply_markup=_teacher_supervisor_keyboard("add"))
        else:
            _finish_teacher_save(m.from_user.id, state)
    elif field == "username":
        state["data"]["username"] = text
        if state["action"] == "add":
            _finish_teacher_save(m.from_user.id, state)
        else:
            state["step"] = "username"
            _finish_teacher_save(m.from_user.id, state)
