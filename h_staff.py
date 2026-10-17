"""تایید هویت، منو، راهنما و شیفت‌های خودم."""
import logging

from telebot import types

import db
from analysis import (day_label, entry_line, next_weekday_on_or_after, now_tehran,
                      today_tomorrow)
from app import (ADMIN, BTN_MY, BOT_MANAGER, MANAGER, ROLE_FA, SUP, bot, menu_for, normalize_phone,
                 staff_role_label,
                 send, staff_only)
from schedule_parser import DAYS

pending = {}  # telegram_id -> staff_id (منتظر تایید «تو فلانی هستی؟»)

HELP = {
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
                  "👩‍🏫 /assignteacher نام استاد | نام سرپرست | @username\n"
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
    commands = list(BASE_COMMANDS)
    if role == SUP:
        commands.extend([("team", "نمایش تیم من"), *MANAGEMENT_COMMANDS,
                         ("linkteacher", "ثبت گروه استاد"), ("groups", "گروه‌های استادهای من")])
    elif role == MANAGER:
        commands.extend(MANAGEMENT_COMMANDS)
    elif role == BOT_MANAGER:
        commands.extend([("team", "نمایش تیم من"), *MANAGEMENT_COMMANDS,
                         ("assignteacher", "تخصیص استاد به سرپرست"),
                         ("linkteacher", "ثبت گروه استاد"), ("groups", "گروه‌های استادها")])
    bot.set_my_commands(
        [types.BotCommand(command, description) for command, description in commands],
        scope=types.BotCommandScopeChat(chat_id=chat_id),
    )


@bot.message_handler(commands=["start"], chat_types=["private"])
def start(m):
    s = db.get_staff_by_tid(m.from_user.id)
    if s:
        _show_commands(m.chat.id, s["role"])
        send(m.chat.id, f"سلام {s['name']} 👋 ({staff_role_label(s)})\n/help راهنما",
             reply_markup=menu_for(s))
        return
    bot.set_my_commands(
        [types.BotCommand("start", "شروع و احراز هویت")],
        scope=types.BotCommandScopeChat(chat_id=m.chat.id),
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 اشتراک شماره من", request_contact=True))
    send(m.chat.id, "برای احراز هویت، شماره‌ی خودت را با دکمه‌ی زیر به اشتراک بگذار.", reply_markup=kb)


@bot.message_handler(commands=["myid"], chat_types=["private"])
def myid(m):
    send(m.chat.id, str(m.from_user.id))


@bot.message_handler(commands=["help"], chat_types=["private"])
@staff_only()
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
    _show_commands(c.message.chat.id, s["role"])
    send(c.message.chat.id, f"✅ هویتت تایید شد. خوش اومدی {s['name']}!", reply_markup=menu_for(s))


def _my(m, s, day=None, shift_date=None):
    rows = db.entries_for_person(s["name"], shift_date=shift_date) if shift_date else \
        db.entries_for_person(s["name"])
    now = now_tehran()
    if day:
        selected = [r for r in rows if r["day"] == day]
        if not selected:
            send(m.chat.id, f"برای {day_label(day, shift_date, now)} کلاسی نداری.", parse_mode="HTML")
            return
        lines = [entry_line(r, shift_date, now=now) for r in selected]
        heading = f"📅 {day_label(day, shift_date, now)}"
        send(m.chat.id, heading + "\n\n" + "\n".join(lines), parse_mode="HTML")
        return

    today = now.date().isoformat()
    by_day = {d: [r for r in rows if r["day"] == d] for d in DAYS}
    dates = db.active_schedule_dates()
    blocks = []
    for d in DAYS:
        day_date = dates.get(d) or next_weekday_on_or_after(today, d)
        lines = [entry_line(r, day_date, now=now) for r in by_day[d]]
        if not lines:
            lines = ["• در این روز کلاسی نداری."]
        heading = f"📅 {day_label(d, day_date, now)}"
        body = heading + "\n\n" + "\n".join(lines)
        blocks.append(body)
    send(m.chat.id, "\n\n".join(blocks), parse_mode="HTML")


@bot.message_handler(commands=["my"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_MY, chat_types=["private"])
@staff_only()
def my(m, s):
    _my(m, s)


@bot.message_handler(commands=["today"], chat_types=["private"])
@staff_only()
def today(m, s):
    _my(m, s, today_tomorrow()[0], now_tehran().date().isoformat())


@bot.message_handler(commands=["tomorrow"], chat_types=["private"])
@staff_only()
def tomorrow(m, s):
    from datetime import timedelta
    _my(m, s, today_tomorrow()[1], (now_tehran().date() + timedelta(days=1)).isoformat())
