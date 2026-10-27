"""فهرست شماره تماس اساتید برای همه‌ی پرسنل تأییدشده."""

from html import escape

import db
from app import BTN_TEACHER_PHONES, bot, send, staff_only
from schedule_parser import key, normalize


def teachers_with_phones():
    """دریافت اساتیدی که شماره تماس دارند و مرتب‌سازی الفبایی."""
    assignments = [
        teacher
        for teacher in db.list_teacher_assignments()
        if teacher.get("phone")
    ]

    return sorted(
        assignments,
        key=lambda teacher: normalize(teacher["teacher"]).casefold()
    )


def teacher_phone_list():
    """ساخت فهرست شماره تماس اساتید."""
    assignments = teachers_with_phones()

    if not assignments:
        return "فعلاً شماره‌ای برای اساتید ثبت نشده است."

    lines = [
        "📞 شماره تماس اساتید (به ترتیب حروف الفبا):",
        ""
    ]

    for teacher in assignments:
        name = escape(teacher["teacher"])
        phone = escape("0" + teacher["phone"])

        lines.append(f"• {name}: {phone}")

    return "\n".join(lines)


@bot.message_handler(
    commands=["teacherphone"],
    chat_types=["private"]
)
@staff_only()
def teacherphone_command(m, staff):
    """نمایش فهرست یا شماره یک استاد با دستور /teacherphone."""

    # استخراج نام استاد پس از دستور
    name = normalize(
        m.text.partition(" ")[2]
    ).strip()

    # اگر نامی وارد نشده باشد، فهرست نمایش داده می‌شود.
    if not name:
        send(
            m.chat.id,
            teacher_phone_list(),
            parse_mode="HTML"
        )
        return

    # جست‌وجوی استاد با نام نرمال‌شده
    target_key = key(name)

    assignment = next(
        (
            teacher
            for teacher in db.list_teacher_assignments()
            if key(teacher["teacher"]) == target_key
        ),
        None
    )

    if not assignment:
        send(
            m.chat.id,
            f"استادی با نام «{escape(name)}» "
            "در بخش مدیریت استادها پیدا نشد.",
            parse_mode="HTML"
        )
        return

    if not assignment.get("phone"):
        send(
            m.chat.id,
            f"برای استاد «{escape(assignment['teacher'])}» "
            "شماره‌ای ثبت نشده است.",
            parse_mode="HTML"
        )
        return

    # نمایش شماره استاد
    teacher_name = escape(assignment["teacher"])
    phone = escape("0" + assignment["phone"])

    send(
        m.chat.id,
        f"📞 شماره تماس استاد «{teacher_name}»: {phone}",
        parse_mode="HTML"
    )


@bot.message_handler(
    func=lambda m: (
        m.chat.type == "private"
        and m.text == BTN_TEACHER_PHONES
    )
)
@staff_only()
def teacherphone_button(m, staff):
    """نمایش فهرست شماره‌ها با زدن دکمه."""

    send(
        m.chat.id,
        teacher_phone_list(),
        parse_mode="HTML"
    )