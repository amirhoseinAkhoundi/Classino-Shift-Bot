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
                 BTN_STUDIO_MANAGEMENT, bot, send)
from schedule_parser import key, load_list, normalize

SCHEDULE_DATE_PENDING = {}
TEACHER_MANAGEMENT_PENDING = {}
KNOWN_BASES = {
    "teacher": load_list("data/teachers.txt"),
    "studio": load_list("data/studios.txt"),
    "remote_teacher": load_list("data/remote_teachers.txt"),
}


def _is_owner(telegram_id):
    return (telegram_id == OWNER_TELEGRAM_ID and db.get_staff_by_tid(telegram_id) is not None
            and db.get_setting(f"manager_mode:{telegram_id}") != "admin")


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
        jalali_date = (
            jalali_day_month(sheet["scheduled_date"])
            if sheet["scheduled_date"] else "بدون تاریخ"
        )

        keyboard.row(
            types.InlineKeyboardButton(
                f"📅 {sheet['day']} {jalali_date} — "
                f"{sheet['entry_count']} ردیف",
                callback_data=f"shiftdate:sheet:{sheet['id']}"
            ),
            types.InlineKeyboardButton(
                "🗑 حذف",
                callback_data=f"shiftdelete:ask:{sheet['id']}"
            ),
        )

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

@bot.callback_query_handler(
    func=lambda c: c.data and c.data.startswith("shiftdelete:")
)
def schedule_delete_callback(c):
    if not _is_owner(c.from_user.id):
        bot.answer_callback_query(
            c.id,
            "این پنل فقط برای مدیر اصلی فعال است.",
            show_alert=True,
        )
        return

    bot.answer_callback_query(c.id)

    parts = c.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return

    action = parts[1]
    sheet_id = int(parts[2])

    sheet = next(
        (
            item for item in db.active_sheets()
            if item["id"] == sheet_id
        ),
        None,
    )

    if not sheet:
        send(
            c.message.chat.id,
            "این شیفت دیگر فعال نیست؛ فهرست را دوباره باز کن.",
        )
        return

    if action == "ask":
        jalali_date = (
            jalali_day_month(sheet["scheduled_date"])
            if sheet["scheduled_date"] else "بدون تاریخ"
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton(
                "✅ بله، حذف شود",
                callback_data=f"shiftdelete:confirm:{sheet_id}",
            ),
            types.InlineKeyboardButton(
                "❌ انصراف",
                callback_data="shiftdelete:cancel:0",
            ),
        )

        bot.send_message(
            c.message.chat.id,
            f"⚠️ برنامه‌ی {sheet['day']} ({jalali_date}) "
            f"با {sheet['entry_count']} ردیف از برنامه‌های فعال حذف شود؟\n\n"
            "این عملیات قابل لغو خودکار نیست.",
            reply_markup=keyboard,
        )
        return

    if action == "cancel":
        try:
            bot.edit_message_reply_markup(
                c.message.chat.id,
                c.message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass

        send(c.message.chat.id, "عملیات حذف لغو شد.")
        return

    if action == "confirm":
        entries = db.entries_for_sheet(sheet_id)

        if not db.deactivate_sheet(sheet_id):
            send(
                c.message.chat.id,
                "حذف انجام نشد؛ احتمالاً این شیفت قبلاً غیرفعال شده.",
            )
            return

        recipients = {
            entry["person"]
            for entry in entries
            if entry.get("person")
        }

        notified = 0
        failed = []

        jalali_date = (
            jalali_day_month(sheet["scheduled_date"])
            if sheet["scheduled_date"] else "بدون تاریخ"
        )

        message = (
            f"🗑 برنامه‌ی {sheet['day']} ({jalali_date}) لغو شد. "
            "لطفاً برنامه‌ی قبلی را ملاک قرار ندهید."
        )

        for name in recipients:
            member = db.get_staff_by_name(name)

            if not member or not member.get("telegram_id"):
                continue

            if send(member["telegram_id"], message):
                notified += 1
            else:
                failed.append(name)

        result = (
            f"✅ برنامه‌ی {sheet['day']} ({jalali_date}) "
            "از برنامه‌های فعال حذف شد."
            f"\nاطلاع‌رسانی موفق: {notified} نفر"
        )

        if failed:
            result += (
                "\n⚠️ ارسال پیام به این افراد ناموفق بود: "
                + ", ".join(sorted(failed))
            )

        send(c.message.chat.id, result)

        try:
            bot.edit_message_reply_markup(
                c.message.chat.id,
                c.message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass

        return

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
    teachers = _teacher_names()
    assigned_keys = {assignment["teacher_key"] for assignment in db.list_teacher_assignments()}
    keyboard = types.InlineKeyboardMarkup()
    for index, teacher in enumerate(teachers):
        assigned = key(teacher) in assigned_keys
        keyboard.row(types.InlineKeyboardButton(
            f"{'✅' if assigned else '📝'} {teacher}",
            callback_data=f"teacherm:select_name:{index}"))
    keyboard.row(types.InlineKeyboardButton("➕ افزودن استاد", callback_data="teacherm:add"))
    send(chat_id, f"👩‍🏫 مدیریت استادها ({len(teachers)} استاد)",
         reply_markup=keyboard)

KNOWN_LABELS = {
    "teacher": "استاد",
    "studio": "استودیو",
}
KNOWN_PLURALS = {
    "teacher": "استادها",
    "studio": "استودیوها",
}


def _studio_panel(chat_id):
    _known_name_list(chat_id, "studio", "studiom")


def _known_name_list(chat_id, kind, namespace):
    names = _known_names(kind)
    keyboard = types.InlineKeyboardMarkup()
    for index, name in enumerate(names):
        keyboard.row(types.InlineKeyboardButton(
            name, callback_data=f"{namespace}:name:{kind}:{index}"))
    keyboard.row(types.InlineKeyboardButton(
        f"➕ افزودن {KNOWN_LABELS[kind]}", callback_data=f"{namespace}:nameadd:{kind}"))
    back = "teacherm:home" if namespace == "teacherm" else "studiom:home"
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=back))
    heading = f"🏢 مدیریت استودیوها\n" if namespace == "studiom" else ""
    send(chat_id, f"{heading}نام‌های {KNOWN_PLURALS[kind]} مورد استفاده در برنامه ({len(names)}):",
         reply_markup=keyboard)


def _known_names(kind):
    excluded = set(db.get_known_excluded(kind))
    names = {key(name): name for name in KNOWN_BASES[kind].values() if key(name) not in excluded}
    names.update({key(name): name for name in db.get_known(kind) if key(name) not in excluded})
    return sorted(names.values())


def _teacher_names():
    teachers = {key(assignment["teacher"]): assignment["teacher"]
                for assignment in db.list_teacher_assignments()}
    teachers.update({key(name): name for name in _known_names("teacher")})
    return sorted(teachers.values(), key=key)


def _teacher_index(teacher):
    target = key(teacher)
    return next((index for index, name in enumerate(_known_names("teacher"))
                 if key(name) == target), None)


def _known_name_detail(chat_id, kind, index, namespace):
    names = _known_names(kind)
    if index >= len(names):
        _known_name_list(chat_id, kind, namespace)
        return
    canonical = names[index]
    aliases = db.list_known_aliases(kind, canonical)
    keyboard = types.InlineKeyboardMarkup()
    details = "\n".join(f"• {item['alias']}" for item in aliases) or "—"
    keyboard.row(types.InlineKeyboardButton(
        "✏️ ویرایش اسم خروجی", callback_data=f"{namespace}:namerename:{kind}:{index}"))
    keyboard.row(types.InlineKeyboardButton(
        "➕ افزودن شکل ورودی", callback_data=f"{namespace}:aliasadd:{kind}:{index}"))
    for alias in aliases:
        keyboard.row(types.InlineKeyboardButton(
            f"🗑 {alias['alias']}", callback_data=f"{namespace}:aliasdelete:{kind}:{alias['id']}"))
    if kind == "studio":
        from h_ops import load_buildings
        building = load_buildings().get(key(canonical), "ثبت نشده")
        keyboard.row(types.InlineKeyboardButton(
            "🏢 تنظیم ساختمان", callback_data=f"studiom:building:studio:{index}"))
        details = f"{details}\nساختمان: {building}"
    keyboard.row(types.InlineKeyboardButton(
        f"🗑 حذف {KNOWN_LABELS[kind]}", callback_data=f"{namespace}:namedelete:{kind}:{index}"))
    back = "teacherm:home" if namespace == "teacherm" else f"{namespace}:names:{kind}"
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=back))
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
    remote = key(assignment["teacher"]) in {key(name) for name in _known_names("remote_teacher")}
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("✏️ ویرایش", callback_data=f"teacherm:edit:{assignment['id']}"),
                 types.InlineKeyboardButton("🗑 حذف", callback_data=f"teacherm:delete:{assignment['id']}"))
    keyboard.row(types.InlineKeyboardButton(
        "🔴 خاموش‌کردن آنلاین" if remote else "🟢 روشن‌کردن آنلاین",
        callback_data=f"teacherm:remoteconfirm:{assignment['id']}:{int(remote)}"))
    keyboard.row(types.InlineKeyboardButton(
        "🔗 تغییر گروه" if group else "🔗 اتصال گروه",
        callback_data=f"teacherm:grouplink:{assignment['id']}"))
    if group:
        keyboard.row(types.InlineKeyboardButton(
            "📣 راهنمای رجیستر استاد", callback_data=f"teacherm:registerinfo:{assignment['id']}"))
    if group:
        keyboard.row(types.InlineKeyboardButton(
            "🔌 قطع اتصال گروه", callback_data=f"teacherm:groupunlink:{assignment['id']}"))
    known_index = _teacher_index(assignment["teacher"])
    if known_index is not None:
        keyboard.row(types.InlineKeyboardButton(
            "🗂 نام ورودی و خروجی برنامه",
            callback_data=f"teacherm:name:teacher:{known_index}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="teacherm:home"))
    send(chat_id, f"👩‍🏫 {assignment['teacher']}\nسرپرست: {assignment['supervisor']}\n"
                  f"شماره تماس: {assignment.get('phone') or 'ثبت نشده'}\n"
                  f"حالت آنلاین: {'روشن' if remote else 'خاموش'}\n{group_status}",
         reply_markup=keyboard)


def _unassigned_teacher_detail(chat_id, teacher, index):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton(
        "👤 تخصیص سرپرست و شماره تماس",
        callback_data=f"teacherm:assign:{index}"))
    keyboard.row(types.InlineKeyboardButton(
        "🗂 نام ورودی و خروجی برنامه",
        callback_data=f"teacherm:name:teacher:{_teacher_index(teacher)}"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="teacherm:home"))
    send(chat_id, f"👩‍🏫 {teacher}\nهنوز سرپرست و شماره تماس در پنل ثبت نشده است.",
         reply_markup=keyboard)


def _start_teacher_assignment(chat_id, telegram_id, teacher):
    supervisors = db.list_staff(roles=("supervisor",))
    if not supervisors:
        send(chat_id, "سرپرستی ثبت نشده؛ ابتدا سرپرست را از مدیریت پرسنل اضافه کن.")
        return
    TEACHER_MANAGEMENT_PENDING[telegram_id] = {
        "action": "add", "step": "supervisor", "data": {"teacher": teacher},
    }
    send(chat_id, f"سرپرست استاد «{teacher}» را انتخاب کن:",
         reply_markup=_teacher_supervisor_keyboard("add"))


def _teacher_edit_fields(chat_id, assignment):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("نام استاد", callback_data=f"teacherm:field:{assignment['id']}:teacher"),
                 types.InlineKeyboardButton("سرپرست", callback_data=f"teacherm:field:{assignment['id']}:supervisor"))
    keyboard.row(types.InlineKeyboardButton("شماره تماس", callback_data=f"teacherm:field:{assignment['id']}:phone"))
    keyboard.row(types.InlineKeyboardButton("⬅️ بازگشت", callback_data=f"teacherm:select:{assignment['id']}"))
    send(chat_id, f"کدام مشخصه‌ی «{assignment['teacher']}» ویرایش شود؟", reply_markup=keyboard)


def _finish_teacher_save(telegram_id, state):
    data = state["data"]
    teacher = data.get("teacher", "").strip()
    supervisor = data.get("supervisor", "").strip()
    phone = data.get("phone", "").strip()
    if not teacher or not supervisor:
        send(telegram_id, "نام استاد و سرپرست الزامی‌اند.")
        return
    if phone and not re.fullmatch(r"\d{10,15}", phone):
        send(telegram_id, "شماره تماس باید فقط شامل ۱۰ تا ۱۵ رقم باشد.")
        return
    try:
        if phone:
            existing = db.find_staff_by_phone(phone)
            previous_teacher = state.get("original_teacher")
            if existing and existing["name"] not in (teacher, previous_teacher):
                raise ValueError("این شماره تماس قبلاً برای فرد دیگری ثبت شده است.")
        if state["action"] == "add":
            db.assign_teacher(teacher, supervisor, "")
        elif not db.update_teacher_assignment(state["original_teacher"], teacher,
                                              supervisor, data.get("username", "")):
            raise ValueError("این تخصیص استاد دیگر در فهرست نیست.")
        if phone:
            db.set_teacher_contact(teacher, phone, supervisor)
        db.add_known("teacher", [teacher])
    except (sqlite3.Error, ValueError) as error:
        logging.exception("teacher management save failed")
        TEACHER_MANAGEMENT_PENDING.pop(telegram_id, None)
        send(telegram_id, f"ذخیره انجام نشد: {error}", reply_markup=types.ReplyKeyboardRemove())
        return
    TEACHER_MANAGEMENT_PENDING.pop(telegram_id, None)
    if phone and (state["action"] == "add" or phone != state.get("original_phone")):
        supervisor_staff = db.get_staff_by_name(supervisor)
        if supervisor_staff and supervisor_staff["telegram_id"]:
            send(supervisor_staff["telegram_id"],
                 f"شماره تماس استاد «{teacher}» ثبت شد: {phone}")
    send(telegram_id, f"✅ استاد «{teacher}» ذخیره شد.",
         reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: m.text == BTN_TEACHER_MANAGEMENT, chat_types=["private"])
def teacher_management(m):
    if not _is_owner(m.from_user.id):
        send(m.chat.id, "این پنل فقط برای مدیر اصلی فعال است.")
        return
    _teacher_panel(m.chat.id)


@bot.message_handler(func=lambda m: m.text == BTN_STUDIO_MANAGEMENT, chat_types=["private"])
def studio_management(m):
    if not _is_owner(m.from_user.id):
        send(m.chat.id, "این پنل فقط برای مدیر اصلی فعال است.")
        return
    _studio_panel(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith(("teacherm:", "studiom:")))
def teacher_management_callback(c):
    if not _is_owner(c.from_user.id):
        bot.answer_callback_query(c.id, "این پنل فقط برای مدیر اصلی فعال است.", show_alert=True)
        return
    bot.answer_callback_query(c.id)
    parts = c.data.split(":")
    namespace = parts[0]
    action = parts[1]
    chat_id = c.message.chat.id
    if action == "home":
        _studio_panel(chat_id) if namespace == "studiom" else _teacher_panel(chat_id)
    elif action == "names":
        kind = parts[2] if len(parts) == 3 else ("studio" if namespace == "studiom" else "teacher")
        if kind in KNOWN_LABELS and ((namespace == "studiom" and kind == "studio")
                                     or (namespace == "teacherm" and kind == "teacher")):
            _known_name_list(chat_id, kind, namespace)
    elif action == "select_name" and namespace == "teacherm" \
            and len(parts) == 3 and parts[2].isdigit():
        teachers = _teacher_names()
        index = int(parts[2])
        if index >= len(teachers):
            _teacher_panel(chat_id)
            return
        teacher = teachers[index]
        assignment = db.get_teacher_assignment(teacher)
        if assignment:
            assignment_row = next(
                row for row in db.list_teacher_assignments()
                if row["teacher_key"] == assignment["teacher_key"])
            _teacher_detail(chat_id, assignment_row)
        else:
            _unassigned_teacher_detail(chat_id, teacher, index)
    elif action == "assign" and namespace == "teacherm" \
            and len(parts) == 3 and parts[2].isdigit():
        teachers = _teacher_names()
        index = int(parts[2])
        if index >= len(teachers):
            _teacher_panel(chat_id)
            return
        teacher = teachers[index]
        if db.get_teacher_assignment(teacher):
            assignment = db.get_teacher_assignment(teacher)
            assignment_row = next(
                row for row in db.list_teacher_assignments()
                if row["teacher_key"] == assignment["teacher_key"])
            _teacher_detail(chat_id, assignment_row)
            return
        _start_teacher_assignment(chat_id, c.from_user.id, teacher)
    elif action == "nameadd" and len(parts) == 3 and parts[2] in KNOWN_LABELS:
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "known_add", "step": "known_name", "kind": parts[2], "data": {},
        }
        send(chat_id, f"نام کامل {KNOWN_LABELS[parts[2]]} را وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "name" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) in (("teacherm", "teacher"), ("studiom", "studio")):
            _known_name_detail(chat_id, parts[2], int(parts[3]), namespace)
    elif action == "namerename" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2], namespace)
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "known_edit", "step": "known_name", "kind": parts[2],
            "original_name": names[index], "data": {},
        }
        send(chat_id, f"نام جدید {KNOWN_LABELS[parts[2]]} را وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "aliasadd" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2], namespace)
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "alias_add", "step": "alias", "kind": parts[2],
            "canonical": names[index], "data": {},
        }
        send(chat_id, f"هر متن ورودی که باید به «{names[index]}» تبدیل شود وارد کن:",
             reply_markup=_cancel_keyboard())
    elif action == "aliasdelete" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        try:
            deleted = db.delete_known_alias(int(parts[3]))
        except sqlite3.Error as error:
            logging.exception("known-name alias deletion failed")
            send(chat_id, f"حذف شکل ورودی انجام نشد: {error}")
            return
        send(chat_id, "✅ شکل ورودی حذف شد." if deleted else "این شکل ورودی دیگر ثبت نیست.")
        _known_name_list(chat_id, parts[2], namespace)
    elif action == "building" and len(parts) == 4 and namespace == "studiom" \
            and parts[2] == "studio" and parts[3].isdigit():
        names = _known_names("studio")
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, "studio", "studiom")
            return
        from h_ops import load_buildings
        current = load_buildings().get(key(names[index]), "ثبت نشده")
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "studio_building", "step": "building", "studio": names[index],
            "data": {},
        }
        send(chat_id, f"ساختمان فعلی «{names[index]}»: {current}\n"
                      "نام ساختمان جدید را بفرست؛ برای پاک‌کردن نگاشت «پاک‌کردن» را بفرست.",
             reply_markup=_cancel_keyboard())
    elif action == "namedelete" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2], namespace)
            return
        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton(
                "بله، حذف شود",
                callback_data=f"{namespace}:namedeleteconfirm:{parts[2]}:{index}"),
            types.InlineKeyboardButton(
                "لغو", callback_data=f"{namespace}:name:{parts[2]}:{index}"))
        send(chat_id, f"نام «{names[index]}» و شکل‌های ورودی ثبت‌شده حذف شوند؟",
             reply_markup=keyboard)
    elif action == "namedeleteconfirm" and len(parts) == 4 and parts[2] in KNOWN_LABELS \
            and parts[3].isdigit():
        if (namespace, parts[2]) not in (("teacherm", "teacher"), ("studiom", "studio")):
            return
        names = _known_names(parts[2])
        index = int(parts[3])
        if index >= len(names):
            _known_name_list(chat_id, parts[2], namespace)
            return
        try:
            name = names[index]
            db.delete_known(parts[2], name)
            if parts[2] == "studio":
                db.set_setting(f"studio_building:{key(name)}", "")
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
        _known_name_list(chat_id, parts[2], namespace)
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
        if not assignment or parts[3] not in ("teacher", "supervisor", "phone"):
            return
        TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
            "action": "edit", "step": parts[3], "assignment_id": assignment["id"],
            "original_teacher": assignment["teacher"],
            "data": {"teacher": assignment["teacher"], "supervisor": assignment["supervisor"],
                     "username": assignment["username"], "phone": assignment.get("phone") or ""},
            "original_phone": assignment.get("phone") or "",
        }
        if parts[3] == "supervisor":
            send(chat_id, "سرپرست جدید را انتخاب کن:",
                 reply_markup=_teacher_supervisor_keyboard("edit", assignment["id"]))
        else:
            labels = {"teacher": "نام جدید استاد را وارد کن:",
                      "phone": "شماره تماس استاد را با ۱۰ تا ۱۵ رقم وارد کن:"}
            label = labels[parts[3]]
            send(chat_id, label, reply_markup=_cancel_keyboard())
    elif action == "remoteconfirm" and len(parts) == 4 and parts[2].isdigit() and parts[3] in ("0", "1"):
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            becoming_remote = parts[3] == "0"
            keyboard = types.InlineKeyboardMarkup()
            keyboard.row(types.InlineKeyboardButton(
                "تأیید تغییر", callback_data=f"teacherm:remotetoggle:{assignment['id']}:{parts[3]}"),
                types.InlineKeyboardButton("لغو", callback_data=f"teacherm:select:{assignment['id']}"))
            change = ("استودیو از برنامه‌های این استاد حذف و کلاس‌های او آنلاین ثبت می‌شود."
                      if becoming_remote else
                      "استودیو دوباره برای این استاد الزامی می‌شود و برنامه‌های بعدی حضوری ثبت می‌شوند.")
            send(chat_id, f"هشدار: با {'روشن' if becoming_remote else 'خاموش'}کردن حالت آنلاین، {change}\n"
                          "این تغییر فقط روی برنامه‌های جدید و ادیت‌های بعدی اعمال می‌شود. ادامه می‌دهی؟",
                 reply_markup=keyboard)
    elif action == "remotetoggle" and len(parts) == 4 and parts[2].isdigit() and parts[3] in ("0", "1"):
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            names = _known_names("remote_teacher")
            is_remote = key(assignment["teacher"]) in {key(name) for name in names}
            if parts[3] == "0" and not is_remote:
                db.add_known("remote_teacher", [assignment["teacher"]])
                db.add_known("teacher", [assignment["teacher"]])
            elif parts[3] == "1" and is_remote:
                db.delete_known("remote_teacher", assignment["teacher"])
            _teacher_detail(chat_id, db.get_teacher_assignment_by_id(assignment["id"]))
    elif action == "grouplink" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment:
            TEACHER_MANAGEMENT_PENDING[c.from_user.id] = {
                "action": "group_link", "step": "chat_id", "teacher": assignment["teacher"],
                "supervisor": assignment["supervisor"], "data": {},
            }
            send(chat_id, "شناسه عددی گروه استاد را وارد کن (مثال: -100123456789):",
                 reply_markup=_cancel_keyboard())
    elif action == "groupunlink" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        if assignment and db.unlink_teacher(assignment["teacher"]):
            send(chat_id, f"اتصال گروه استاد «{assignment['teacher']}» قطع شد.")
            _teacher_detail(chat_id, db.get_teacher_assignment_by_id(assignment["id"]))
        else:
            send(chat_id, "گروه متصل برای این استاد ثبت نشده است.")
    elif action == "registerinfo" and len(parts) == 3 and parts[2].isdigit():
        assignment = db.get_teacher_assignment_by_id(int(parts[2]))
        group = db.get_teacher_group(assignment["teacher"]) if assignment else None
        if assignment and group:
            send(chat_id, f"برای ثبت شناسه‌ی «{assignment['teacher']}»، در گروه {group['chat_id']} "
                          "روی پیام خود استاد Reply کن و /registerteacher بزن.")
    elif action == "addsup" and len(parts) == 3 and parts[2].isdigit():
        state = TEACHER_MANAGEMENT_PENDING.get(c.from_user.id)
        supervisor = next((row for row in db.list_staff(roles=("supervisor",))
                           if row["id"] == int(parts[2])), None)
        if state and state["action"] == "add" and state["step"] == "supervisor" and supervisor:
            state["data"]["supervisor"] = supervisor["name"]
            state["step"] = "phone"
            send(chat_id, "شماره تماس استاد را با ۱۰ تا ۱۵ رقم وارد کن؛ اگر فعلاً ثبت نمی‌شود /skip بفرست:",
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
                      and (not m.text.startswith("/") or m.text in ("/cancel", "/skip")))
def teacher_management_input(m):
    state = TEACHER_MANAGEMENT_PENDING.get(m.from_user.id)
    if not _is_owner(m.from_user.id) or not state:
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "این مرحله لغو شد یا دسترسی شما تغییر کرده است.")
        return
    raw_text = m.text.strip()
    text = normalize(m.text).strip()
    if text in ("❌ لغو", "/cancel"):
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        return
    field = state["step"]
    if field == "chat_id":
        try:
            chat_id = int(text)
            if chat_id >= 0:
                raise ValueError("شناسه گروه باید عدد منفی باشد.")
            db.link_teacher(state["teacher"], chat_id, state["supervisor"])
        except (ValueError, sqlite3.Error) as error:
            send(m.chat.id, f"اتصال گروه انجام نشد: {error}\nشناسه گروه را دوباره وارد کن:",
                 reply_markup=_cancel_keyboard())
            return
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, f"✅ گروه {chat_id} به استاد «{state['teacher']}» وصل شد.",
             reply_markup=types.ReplyKeyboardRemove())
        return
    if field == "building" and state["action"] == "studio_building":
        setting_key = f"studio_building:{key(state['studio'])}"
        building = "" if key(text) in ("پاککردن", "حذف") else text
        try:
            db.set_setting(setting_key, building)
        except sqlite3.Error as error:
            logging.exception("studio building update failed")
            send(m.chat.id, f"ذخیره‌ی ساختمان انجام نشد: {error}",
                 reply_markup=_cancel_keyboard())
            return
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "✅ نگاشت ساختمان استودیو پاک شد." if not building
             else f"✅ ساختمان «{building}» برای استودیو «{state['studio']}» ثبت شد.",
             reply_markup=types.ReplyKeyboardRemove())
        return
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
                if kind == "studio":
                    from h_ops import load_buildings
                    original_building = load_buildings().get(key(original))
                    if original_building is not None:
                        db.set_setting(f"studio_building:{key(text)}", original_building)
                        db.set_setting(f"studio_building:{key(original)}", "")
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
    elif field == "phone" and state["action"] == "add":
        phone = "" if raw_text == "/skip" else raw_text
        if phone and not re.fullmatch(r"\d{10,15}", phone):
            send(m.chat.id, "شماره تماس باید فقط شامل ۱۰ تا ۱۵ رقم باشد؛ دوباره وارد کن یا /skip بفرست:",
                 reply_markup=_cancel_keyboard())
            return
        state["data"]["phone"] = phone
        _finish_teacher_save(m.from_user.id, state)
    elif field == "phone":
        phone = raw_text
        if not re.fullmatch(r"\d{10,15}", phone):
            send(m.chat.id, "شماره تماس باید فقط شامل ۱۰ تا ۱۵ رقم باشد؛ دوباره وارد کن:",
                 reply_markup=_cancel_keyboard())
            return
        try:
            db.set_teacher_contact(state["original_teacher"], phone, state["data"]["supervisor"])
        except (sqlite3.IntegrityError, ValueError) as error:
            logging.exception("teacher contact save failed")
            send(m.chat.id, f"شماره تماس قبلاً برای فرد دیگری ثبت شده است: {error}",
                 reply_markup=_cancel_keyboard())
            return
        TEACHER_MANAGEMENT_PENDING.pop(m.from_user.id, None)
        supervisor = db.get_staff_by_name(state["data"]["supervisor"])
        if supervisor and supervisor["telegram_id"]:
            send(supervisor["telegram_id"],
                 f"شماره تماس استاد «{state['original_teacher']}» ثبت شد: 0{phone}")
        send(m.chat.id, f"✅ شماره تماس استاد ثبت شد: {phone}",
             reply_markup=types.ReplyKeyboardRemove())
