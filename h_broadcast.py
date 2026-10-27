"""ارسال پیام فوریِ مدیر ربات به پرسنل بر اساس سمت؛ بدون صف یا ارسال زمان‌بندی‌شده."""
from time import time

from telebot import types

import db
from app import (ADMIN, BOT_MANAGER, MANAGER, ROLE_FA, SUP, TEACHER,
                 bot, send)

BTN_BROADCAST = "📨 پیام به کاربران"
PENDING = {}  # telegram_id -> draft; فقط در حافظه و با انقضای کوتاه
DRAFT_TTL_SECONDS = 600
ROLE_ORDER = (MANAGER, SUP, ADMIN, TEACHER, BOT_MANAGER)
ROLE_LABELS = {
    MANAGER: "مسئول شیفت",
    SUP: "سرپرست",
    ADMIN: "ادمین",
    TEACHER: "استاد",
    BOT_MANAGER: "مدیر ربات",
}


def _authorized(user_id):
    staff = db.get_staff_by_tid(user_id)
    return bool(staff and staff["role"] == BOT_MANAGER
                and db.get_setting(f"manager_mode:{user_id}") != "admin")


def _fresh_draft(user_id):
    draft = PENDING.get(user_id)
    if not draft or time() - draft["created_at"] > DRAFT_TTL_SECONDS:
        PENDING.pop(user_id, None)
        return None
    return draft


def _role_keyboard(selected):
    kb = types.InlineKeyboardMarkup()
    for role in ROLE_ORDER:
        mark = "✅" if role in selected else "▫️"
        kb.row(types.InlineKeyboardButton(
            f"{mark} {ROLE_LABELS[role]}", callback_data=f"broadcast:role:{role}"))
    kb.row(types.InlineKeyboardButton("ادامه ➡️", callback_data="broadcast:next"),
           types.InlineKeyboardButton("❌ لغو", callback_data="broadcast:cancel"))
    return kb


def _show_roles(chat_id, user_id):
    draft = _fresh_draft(user_id)
    if not draft:
        PENDING[user_id] = {"roles": set(), "created_at": time(), "stage": "roles"}
        draft = PENDING[user_id]
    draft["stage"] = "roles"
    send(chat_id, "پیام برای کدام سمت‌ها ارسال شود؟\n"
         "فقط کاربرانی هدف قرار می‌گیرند که تا همین لحظه هویتشان را در بات تأیید کرده‌اند.\n"
         "برای ارسال فوری، سمت‌ها را انتخاب کن:",
         reply_markup=_role_keyboard(draft["roles"]))


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text == BTN_BROADCAST)
def broadcast_start(m):
    if not _authorized(m.from_user.id):
        send(m.chat.id, "این امکان فقط برای مدیر ربات در حالت مدیریت ربات فعال است.")
        return
    PENDING.pop(m.from_user.id, None)
    _show_roles(m.chat.id, m.from_user.id)


@bot.callback_query_handler(func=lambda c: bool(c.data and c.data.startswith("broadcast:")))
def broadcast_callback(c):
    if not _authorized(c.from_user.id):
        bot.answer_callback_query(c.id, "دسترسی مجاز نیست.", show_alert=True)
        PENDING.pop(c.from_user.id, None)
        return
    draft = _fresh_draft(c.from_user.id)
    if not draft:
        bot.answer_callback_query(c.id, "این مرحله منقضی شده؛ دوباره شروع کن.", show_alert=True)
        send(c.message.chat.id, "درخواست منقضی شد. دوباره «پیام به کاربران» را بزن.")
        return

    action = c.data.partition(":")[2]
    if action == "cancel":
        PENDING.pop(c.from_user.id, None)
        bot.answer_callback_query(c.id, "لغو شد.")
        send(c.message.chat.id, "ارسال پیام لغو شد.")
        return
    if action.startswith("role:"):
        role = action.split(":", 1)[1]
        if role not in ROLE_ORDER or draft["stage"] != "roles":
            bot.answer_callback_query(c.id, "انتخاب نامعتبر است.", show_alert=True)
            return
        if role in draft["roles"]:
            draft["roles"].remove(role)
        else:
            draft["roles"].add(role)
        bot.answer_callback_query(c.id)
        # ویرایش همان پیام؛ در صورت خطای تلگرام، کیبورد تازه هم ارسال می‌شود.
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id,
                                          reply_markup=_role_keyboard(draft["roles"]))
        except Exception:
            send(c.message.chat.id, "سمت‌های انتخاب‌شده را مشخص کن:",
                 reply_markup=_role_keyboard(draft["roles"]))
        return
    if action == "next":
        if not draft["roles"]:
            bot.answer_callback_query(c.id, "حداقل یک سمت را انتخاب کن.", show_alert=True)
            return
        draft["stage"] = "text"
        bot.answer_callback_query(c.id)
        send(c.message.chat.id, "متن پیام را بفرست. هنوز چیزی برای کاربران ارسال نمی‌شود؛ قبل از ارسال نهایی تأیید می‌گیری.",
             reply_markup=types.ReplyKeyboardRemove())
        return
    if action == "confirm":
        if draft.get("stage") != "confirm" or not draft.get("text"):
            bot.answer_callback_query(c.id, "درخواست معتبر نیست.", show_alert=True)
            return
        # درست در لحظه‌ی تأیید، گیرندگان از دیتابیس خوانده می‌شوند؛ شناسه‌های ثبت‌نشده حذف‌اند.
        recipients = [dict(s) for s in db.list_staff(roles=tuple(draft["roles"]))
                      if s["telegram_id"]]
        bot.answer_callback_query(c.id)
        sent_count, failed = 0, 0
        for recipient in recipients:
            if send(recipient["telegram_id"], draft["text"]):
                sent_count += 1
            else:
                failed += 1
        PENDING.pop(c.from_user.id, None)
        send(c.message.chat.id,
             f"✅ ارسال تمام شد.\nموفق: {sent_count}\nناموفق: {failed}\n"
             f"کاربران بدون شناسه‌ی تأییدشده: در این ارسال لحاظ نشدند.")
        return
    bot.answer_callback_query(c.id, "درخواست نامعتبر است.", show_alert=True)


@bot.message_handler(func=lambda m: m.chat.type == "private" and m.from_user
                     and bool(m.text) and m.from_user.id in PENDING
                     and PENDING[m.from_user.id].get("stage") == "text"
                     and not m.text.startswith("/"))
def broadcast_text(m):
    if not _authorized(m.from_user.id):
        PENDING.pop(m.from_user.id, None)
        send(m.chat.id, "دسترسی شما تغییر کرده است؛ ارسال لغو شد.")
        return
    draft = _fresh_draft(m.from_user.id)
    if not draft or draft.get("stage") != "text":
        send(m.chat.id, "این مرحله منقضی شده؛ از ابتدا شروع کن.")
        return
    body = m.text.strip()
    if not body:
        send(m.chat.id, "متن پیام خالی است؛ متن را دوباره بفرست.")
        return
    if len(body) > 4000:
        send(m.chat.id, "پیام بیش از حد طولانی است؛ حداکثر ۴۰۰۰ نویسه بفرست.")
        return
    draft["text"] = body
    draft["stage"] = "confirm"
    selected = "، ".join(ROLE_LABELS[r] for r in ROLE_ORDER if r in draft["roles"])
    # برآورد گیرندگان ثبت‌شده برای نمایش شفاف؛ شمار نهایی در لحظه‌ی تأیید محاسبه می‌شود.
    count = sum(1 for s in db.list_staff(roles=tuple(draft["roles"])) if s["telegram_id"])
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ ارسال فوری", callback_data="broadcast:confirm"),
           types.InlineKeyboardButton("❌ لغو", callback_data="broadcast:cancel"))
    send(m.chat.id, f"پیش‌نمایش پیام:\n\n{body}\n\nسمت‌ها: {selected}\n"
         f"گیرندگان دارای شناسه‌ی تأییدشده در حال حاضر: {count}\n"
         "با تأیید، همین حالا ارسال می‌شود؛ برای کاربران ثبت‌نشده پیام معوقه‌ای ذخیره نمی‌شود.",
         reply_markup=kb)
