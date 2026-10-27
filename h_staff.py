"""تایید هویت، منو، راهنما و شیفت‌های خودم."""
import logging

from telebot import types

import db
from analysis import (day_label, entry_line, fmt_entry, next_weekday_on_or_after,
                      now_tehran, span, today_tomorrow)
from app import (ADMIN, BTN_DAILY_NOTIFICATIONS, BTN_MODE, BTN_MODE_ADMIN, BTN_MY, BTN_TEACHER_ADMIN,
                 BTN_TEACHER_CLASSES, BOT_MANAGER, MANAGER, ROLE_FA, SUP, TEACHER, bot,
                 daily_notifications_enabled, menu_for, normalize_phone, phone_display,
                 staff_role_label, send, staff_context, staff_only)
from schedule_parser import DAYS, key, normalize, split_people

pending = {}  # telegram_id -> staff_id (منتظر تایید «تو فلانی هستی؟»)

HELP = {
    TEACHER: "📚 /my کلاس‌های من",
    ADMIN: "📋 /my شیفت‌هایم  |  /today امروز  |  /tomorrow فردا\n🆘 «مشکل دارم» و 🙋 «کمکی کیه؟» از منو",
    SUP: ("📋 /my /today /tomorrow\n👥 /team [روز]  تیم من\n🔎 /person نام  |  /class نام استاد یا درس  |  /day [روز]\n"
          "🟢 /free روز ساعت  (مثل: /free یکشنبه 17:00-19:30)\n📊 /stats  |  /staff\n"
          "در گروه استاد: روی پیام استاد ریپلای کن و /registerteacher بزن.\n"
          "📣 /notify_teachers [روز]  اطلاع به گروه اساتید (پیش‌فرض: فردا)\n"
          "🔗 فقط برای استادهای تخصیص‌یافته به خودت، در گروهشان: /linkteacher نام استاد\n"
          "👥 /groups فقط گروه استادهای خودت را نشان می‌دهد."),
    MANAGER: ("📋 /my /today /tomorrow\n🔎 /person نام  |  /class نام استاد یا درس  |  /day [روز]\n"
              "🟢 /free روز ساعت  (مثل: /free فردا 15-18)\n📊 /stats  |  /staff\n"
                  "🧑‍💼 افزودن، ویرایش و حذف پرسنل از دکمه‌ی «مدیریت پرسنل»\n"
              "📣 /notify_teachers [روز]\n🔗 فقط در گروه استادی که مدیر ربات به تو سپرده: /linkteacher نام استاد\n"
              "در گروه استاد: روی پیام استاد ریپلای کن و /registerteacher بزن."),
    BOT_MANAGER: ("📋 /my /today /tomorrow\n👥 /team [روز]  |  🔎 /person نام  |  /class نام استاد یا درس  |  /day [روز]\n"
                  "🟢 /free روز ساعت  |  📊 /stats  |  /staff\n"
                      "🧑‍💼 افزودن، ویرایش و حذف پرسنل از دکمه‌ی «مدیریت پرسنل»\n"
                  "📣 /notify_teachers [روز]  |  /groups\n"
                  "👩‍🏫 تخصیص و ویرایش استادها از دکمه‌ی «مدیریت استادها»\n"
                  "🔗 در گروه استاد: /linkteacher نام استاد\n"
                  "روی پیام استاد ریپلای کن و /registerteacher بزن."),
}

BASE_COMMANDS = [
    ("start", "شروع یا تازه‌سازی منو"),
    ("help", "راهنمای نقش شما"),
    ("my", "نمایش شیفت‌های من"),
    ("today", "شیفت‌های امروز"),
    ("tomorrow", "شیفت‌های فردا"),
    ("problem", "گزارش مشکل"),
    ("helper", "اطلاع از نیروی کمکی"),
    ("teacherphone", "شماره تماس اساتید"),
]
MANAGEMENT_COMMANDS = [
    ("person", "برنامه و شماره‌ی یک نفر"),
    ("class", "جست‌وجوی کلاس"),
    ("day", "برنامه‌ی یک روز"),
    ("free", "جست‌وجوی نیروی آزاد"),
    ("stats", "گزارش آمار"),
    ("staff", "فهرست پرسنل"),
    ("notify_teachers", "اطلاع‌رسانی به گروه اساتید"),
]


def _show_commands(chat_id, role):
    if role == BOT_MANAGER and db.get_setting(f"manager_mode:{chat_id}") == "admin":
        role = ADMIN
    if role == TEACHER:
        commands = [("my", "کلاس‌های من")]
    else:
        commands = list(BASE_COMMANDS)
        if role == SUP:
            commands.extend([("team", "نمایش تیم من"), *MANAGEMENT_COMMANDS,
                             ("linkteacher", "ثبت گروه استاد"), ("groups", "گروه‌های استادهای من")])
        elif role == MANAGER:
            commands.extend(MANAGEMENT_COMMANDS)
        elif role == BOT_MANAGER:
            commands.extend([("team", "نمایش تیم من"), *MANAGEMENT_COMMANDS,
                             ("linkteacher", "ثبت گروه استاد"), ("groups", "گروه‌های استادها")])
    bot.set_my_commands(
        [types.BotCommand(command, description) for command, description in commands],
        scope=types.BotCommandScopeChat(chat_id=chat_id),
    )


@bot.message_handler(commands=["start"], chat_types=["private"])
def start(m):
    s = db.get_staff_by_tid(m.from_user.id)
    if s:
        context = staff_context(s)
        _show_commands(m.chat.id, context["role"])
        welcome = (f"سلام استاد {s['name']} گرامی 👋" if context["role"] == TEACHER
                   else f"سلام {s['name']} 👋 ({staff_role_label(context)})")
        if context["role"] != TEACHER:
            welcome += "\n/help راهنما"
        send(m.chat.id, welcome,
             reply_markup=menu_for(s))
        return
    bot.set_my_commands(
        [types.BotCommand("start", "شروع و احراز هویت")],
        scope=types.BotCommandScopeChat(chat_id=m.chat.id),
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 اشتراک شماره من", request_contact=True))
    send(m.chat.id, "برای احراز هویت، شماره‌ی خود را با دکمه‌ی زیر به اشتراک بگذارید.", reply_markup=kb)


@bot.message_handler(commands=["myid"], chat_types=["private"])
def myid(m):
    send(m.chat.id, str(m.from_user.id))


@bot.message_handler(commands=["help"], chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER)
def help_(m, s):
    send(m.chat.id, HELP.get(s["role"], HELP[ADMIN]))


@bot.message_handler(content_types=["contact"], chat_types=["private"])
def on_contact(m):
    c = m.contact
    if c.user_id != m.from_user.id:  # جلوگیری از فوروارد کردن شماره‌ی دیگران
        send(m.chat.id, "فقط شماره‌ی خودت را با دکمه بفرست، نه مخاطب دیگری.")
        return
    phone = normalize_phone(c.phone_number)
    s = db.find_staff_by_phone(phone)
    rm = types.ReplyKeyboardRemove()
    if not s:
        logging.warning("ورود ناموفق: شماره‌ی %s*** در دیتابیس نیست (staff.csv بارگذاری شده؟ /staff را چک کن)", phone[:4])
        send(m.chat.id, "این شماره در لیست ثبت نشده. با مسئول شیفت هماهنگ کن.", reply_markup=rm)
        return
    if s["telegram_id"] and s["telegram_id"] != m.from_user.id:
        send(m.chat.id, "این شماره قبلاً به حساب دیگری وصل شده. با آخوندی ارتباط بگیر.", reply_markup=rm)
        return
    pending[m.from_user.id] = s["id"]
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ بله", callback_data="auth:yes"),
           types.InlineKeyboardButton("❌ خیر", callback_data="auth:no"))
    send(m.chat.id, f"شما به نام «{s['name']}» ثبت شده‌ای؛ درسته؟", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data in ("auth:yes", "auth:no"))
def on_auth(c):
    bot.answer_callback_query(c.id)
    sid = pending.pop(c.from_user.id, None)
    if c.data == "auth:no" or sid is None:
        send(c.message.chat.id, "لغو شد. دوباره /start بزن.")
        return
    db.bind_telegram(sid, c.from_user.id)
    s = db.get_staff_by_tid(c.from_user.id)
    context = staff_context(s)
    _show_commands(c.message.chat.id, context["role"])
    welcome = f"✅ هویتت تایید شد. خوش اومدی {s['name']} ({staff_role_label(context)})!"
    if context["role"] != TEACHER:
        welcome += "\n/help راهنما"
    send(c.message.chat.id, welcome, reply_markup=menu_for(s))


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                      and m.text in (BTN_MODE_ADMIN, BTN_MODE))
def ask_manager_mode_change(m):
    staff = db.get_staff_by_tid(m.from_user.id)
    if not staff or staff["role"] != BOT_MANAGER:
        send(m.chat.id, "این تغییر فقط برای مدیر ربات فعال است.")
        return
    target = "admin" if m.text == BTN_MODE_ADMIN else "bot_manager"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton(
        "تأیید تغییر حالت", callback_data=f"manager_mode:{target}"),
        types.InlineKeyboardButton("لغو", callback_data="manager_mode:cancel"))
    warning = ("حالت ادمین ساده فعال می‌شود؛ فقط شیفت‌های خودت، گزارش مشکل و درخواست کمکی در دسترس "
               "خواهد بود و ابزارهای تیم، آمار و مدیریت ربات پنهان می‌شوند."
               if target == "admin" else
               "دسترسی کامل مدیر ربات و مدیریت همه‌ی تیم‌ها دوباره فعال می‌شود.")
    send(m.chat.id, "هشدار: " + warning, reply_markup=keyboard)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("manager_mode:"))
def change_manager_mode(c):
    staff = db.get_staff_by_tid(c.from_user.id)
    if not staff or staff["role"] != BOT_MANAGER:
        bot.answer_callback_query(c.id, "این تغییر فقط برای مدیر ربات فعال است.", show_alert=True)
        return
    target = c.data.partition(":")[2]
    if target == "cancel":
        bot.answer_callback_query(c.id, "لغو شد.")
        send(c.message.chat.id, "حالت تغییر نکرد.")
        return
    if target not in ("admin", "bot_manager"):
        bot.answer_callback_query(c.id, "درخواست نامعتبر است.", show_alert=True)
        return
    db.set_setting(f"manager_mode:{c.from_user.id}", target if target == "admin" else "bot_manager")
    _show_commands(c.message.chat.id, staff["role"])
    bot.answer_callback_query(c.id, "حالت تغییر کرد.")
    mode_name = "ادمین" if target == "admin" else "مدیر ربات"
    send(c.message.chat.id, f"حالت «{mode_name}» فعال شد.", reply_markup=menu_for(staff))


def _my(m, s, day=None, shift_date=None):
    teacher_assignment = db.get_teacher_assignment(s["name"]) if s["role"] == TEACHER else None
    if teacher_assignment:
        rows = db.entries_for_teacher(s["name"], shift_date=shift_date)
    else:
        rows = db.entries_for_person(s["name"], shift_date=shift_date) if shift_date else \
            db.entries_for_person(s["name"])
    now = now_tehran()
    if day:
        selected = [r for r in rows if r["day"] == day]
        if not selected:
            no_class = "کلاسی ندارید." if s["role"] == TEACHER else "کلاسی نداری."
            send(m.chat.id, f"برای {day_label(day, shift_date, now)} {no_class}", parse_mode="HTML")
            return
        lines = [entry_line(r, shift_date, with_person=s["role"] == TEACHER, now=now)
                 for r in selected]
        heading = f"📅 {day_label(day, shift_date, now)}"
        send(m.chat.id, heading + "\n\n" + "\n".join(lines), parse_mode="HTML")
        return

    today = now.date().isoformat()
    by_day = {d: [r for r in rows if r["day"] == d] for d in DAYS}
    dates = db.active_schedule_dates()
    blocks = []
    for d in DAYS:
        day_date = dates.get(d) or next_weekday_on_or_after(today, d)
        lines = [entry_line(r, day_date, with_person=s["role"] == TEACHER, now=now)
                 for r in by_day[d]]
        if not lines:
            no_class = "در این روز کلاسی ندارید." if s["role"] == TEACHER \
                else "در این روز کلاسی نداری."
            lines = [f"• {no_class}"]
        heading = f"📅 {day_label(d, day_date, now)}"
        body = heading + "\n\n" + "\n".join(lines)
        blocks.append(body)
    send(m.chat.id, "\n\n".join(blocks), parse_mode="HTML")


@bot.message_handler(
    func=lambda m: m.chat.type == "private" and m.text
    and m.text.startswith(BTN_DAILY_NOTIFICATIONS),
)
def toggle_daily_notifications(m):
    raw_staff = db.get_staff_by_tid(m.from_user.id)
    if not raw_staff:
        send(m.chat.id, "برای تغییر این گزینه ابتدا با /start وارد شوید.")
        return
    enabled = not daily_notifications_enabled(raw_staff["id"])
    db.set_setting(f"daily_notifications:{raw_staff['id']}", "on" if enabled else "off")
    status = "روشن شد" if enabled else "خاموش شد"
    role = staff_context(raw_staff)["role"]
    detail = ("هر روز ساعت ۴:۰۰ بامداد، برنامه‌ی همان روز برایتان ارسال می‌شود."
              if enabled else "از این پس اعلان روزانه برایتان ارسال نمی‌شود.")
    if role == TEACHER:
        text = f"استاد {raw_staff['name']} گرامی، اطلاع‌رسانی روزانه {status}.\n{detail}"
    else:
        text = f"اطلاع‌رسانی روزانه {status}.\n{detail}"
    send(m.chat.id, text, reply_markup=menu_for(raw_staff))


def _teacher_schedule_rows(name):
    rows = db.entries_for_teacher(name)
    today = now_tehran().date().isoformat()
    dates = db.active_schedule_dates()
    for row in rows:
        row["_schedule_date"] = (row.get("shift_date") or dates.get(row["day"])
                                 or next_weekday_on_or_after(today, row["day"]))
    return sorted(rows, key=lambda row: (DAYS.index(row["day"]), row["_schedule_date"],
                                         row["start_min"], row["end_min"]))


@bot.message_handler(func=lambda m: m.text == BTN_TEACHER_ADMIN, chat_types=["private"])
@staff_only(TEACHER)
def teacher_admin_lookup(m, s):
    rows = _teacher_schedule_rows(s["name"])
    days = sorted({(row["day"], row["_schedule_date"]) for row in rows},
                  key=lambda item: (DAYS.index(item[0]), item[1]))
    if not days:
        send(m.chat.id, "استاد گرامی، در برنامه‌ی فعلی کلاسی برای شما ثبت نشده است.")
        return
    keyboard = types.InlineKeyboardMarkup()
    for day, shift_date in days:
        label = day_label(day, shift_date, html=False)
        keyboard.add(types.InlineKeyboardButton(
            label, callback_data=f"teacheradmin:day:{day}:{shift_date}"))
    send(m.chat.id, "لطفاً روز کلاس را انتخاب بفرمایید:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("teacheradmin:"))
def teacher_admin_callback(c):
    raw_staff = db.get_staff_by_tid(c.from_user.id)
    if not raw_staff or staff_context(raw_staff)["role"] != TEACHER:
        bot.answer_callback_query(c.id, "این بخش فقط برای استادهاست.", show_alert=True)
        return
    staff = staff_context(raw_staff)
    parts = c.data.split(":")
    rows = _teacher_schedule_rows(staff["name"])

    if len(parts) == 4 and parts[1] == "day":
        _, _, day, shift_date = parts
        matching = [row for row in rows
                    if row["day"] == day and row["_schedule_date"] == shift_date]
        if not matching:
            bot.answer_callback_query(c.id, "برنامه تغییر کرده است؛ لطفاً روز را دوباره انتخاب بفرمایید.",
                                      show_alert=True)
            return
        keyboard = types.InlineKeyboardMarkup()
        for row in matching:
            label = f"{row['course'] or 'بدون عنوان'} | {row['studio'] or 'آنلاین'} | {span(row)}"
            if len(label) > 64:
                label = label[:61] + "..."
            keyboard.add(types.InlineKeyboardButton(
                label, callback_data=f"teacheradmin:class:{row['id']}"))
        bot.answer_callback_query(c.id)
        send(c.message.chat.id,
             f"📅 {day_label(day, shift_date, html=False)}\nلطفاً کلاس را انتخاب بفرمایید:",
             reply_markup=keyboard)
        return

    if len(parts) == 3 and parts[1] == "class" and parts[2].isdigit():
        entry = next((row for row in rows if row["id"] == int(parts[2])), None)
        if not entry:
            bot.answer_callback_query(c.id, "برنامه تغییر کرده است؛ لطفاً دوباره انتخاب بفرمایید.",
                                      show_alert=True)
            return
        staff_by_name = {key(normalize(person["name"])): person for person in db.list_staff()}
        admins = split_people(entry.get("person"))
        lines = []
        for name in admins:
            person = staff_by_name.get(key(normalize(name)))
            phone = phone_display(person) if person and person["phone"] else "شماره موجود نیست"
            lines.append(f"• {name} — {phone}")
        admin_info = "\n".join(lines) if lines else "ادمین این کلاس در برنامه مشخص نشده است."
        bot.answer_callback_query(c.id)
        send(c.message.chat.id,
             f"📅 {day_label(entry['day'], entry['_schedule_date'], html=False)}\n"
             f"{fmt_entry(entry)}\n\nادمین(های) این کلاس:\n{admin_info}")
        return

    bot.answer_callback_query(c.id, "درخواست نامعتبر است.", show_alert=True)


@bot.message_handler(commands=["my"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_MY, chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_TEACHER_CLASSES, chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER, TEACHER)
def my(m, s):
    _my(m, s)


@bot.message_handler(commands=["today"], chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER)
def today(m, s):
    _my(m, s, today_tomorrow()[0], now_tehran().date().isoformat())


@bot.message_handler(commands=["tomorrow"], chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER)
def tomorrow(m, s):
    from datetime import timedelta
    _my(m, s, today_tomorrow()[1], (now_tehran().date() + timedelta(days=1)).isoformat())
