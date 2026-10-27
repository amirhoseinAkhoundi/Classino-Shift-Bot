"""عملیات زنده: «مشکل دارم» و «کمکی کیه؟» با دکمه‌های متوالی."""
import os
from html import escape

from telebot import types

import config
import db
from analysis import active_now, current_classes, day_label, now_tehran, span, today_tomorrow
from app import (ADMIN, BOT_MANAGER, BTN_HELPER, BTN_PROBLEM, MANAGER, SUP, TEACHER,
                 bot, phone_display, phone_of, send, staff_context, staff_only)
from schedule_parser import key, normalize, person_matches, split_people

KINDS = [("sound", "🔊 صدا"), ("video", "🎥 تصویر/دوربین"), ("net", "🌐 اینترنت"),
         ("pc", "💻 سیستم/نرم‌افزار"), ("power", "⚡ برق/تجهیزات"), ("other", "❓ سایر")]
KIND_LABEL = dict(KINDS)
S = {}  # telegram_id -> وضعیت گفتگو


# ---------- توابع کمکی (قابل تست) ----------
def load_buildings(path="data/studio_buildings.txt"):
    m = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    a, b = line.split("=", 1)
                    m[key(normalize(a))] = normalize(b.strip())
    for studio_key, building in db.get_settings("studio_building:").items():
        if building:
            m[studio_key] = normalize(building.strip())
        else:
            m.pop(studio_key, None)
    return m


def filter_scope(entries, studio):
    """اگر برای استودیو ساختمان تعریف شده، فقط ناظرهای همان ساختمان؛ وگرنه همه."""
    b = load_buildings().get(key(studio or ""))
    if not b:
        return entries
    hit = [e for e in entries if not e["course"] or b in e["course"]]
    return hit or entries


def dedupe(entries):
    seen, out = set(), []
    for e in entries:
        if e["person"] not in seen:
            seen.add(e["person"])
            out.append(e)
    return out


def roles_now(kind, studio, day, now, shift_date=None):
    """(ناظرها/کمکی‌های فعال در این لحظه، آیا الان در بازه‌اند). اگر کسی الان نبود، لیست کل امروز."""
    es = db.entries_on_date(shift_date, day=day) if shift_date else db.active_entries(day)
    es = [e for e in es if e["kind"] == kind and e["person"]]
    if kind == "komaki":
        classes = db.entries_on_date(shift_date, day=day, kind="class") if shift_date else [
            entry for entry in db.active_entries(day) if entry["kind"] == "class"
        ]
        busy = {
            entry["person"] for entry in es
            if any(person_matches(cls["person"], entry["person"])
                   and cls["start_min"] - 30 <= now < cls["end_min"] + 30
                   and cls["start_min"] - 30 < entry["end_min"]
                   and cls["end_min"] + 30 > entry["start_min"]
                   for cls in classes)
        }
        es = [entry for entry in es if entry["person"] not in busy]
    act = active_now(es, kind, day, now)
    return dedupe(filter_scope(act or es, studio)), bool(act)


def helper_busy_until(day, now, shift_date=None):
    helpers = db.entries_on_date(shift_date, day=day, kind="komaki") if shift_date else [
        entry for entry in db.active_entries(day) if entry["kind"] == "komaki"
    ]
    classes = db.entries_on_date(shift_date, day=day, kind="class") if shift_date else [
        entry for entry in db.active_entries(day) if entry["kind"] == "class"
    ]
    busy = {}
    for helper in helpers:
        if any(person_matches(cls["person"], helper["person"])
               and cls["start_min"] - 30 <= now < cls["end_min"] + 30
               and cls["start_min"] - 30 < helper["end_min"]
               and cls["end_min"] + 30 > helper["start_min"]
               for cls in classes):
            for cls in classes:
                if (person_matches(cls["person"], helper["person"])
                        and cls["start_min"] - 30 <= now < cls["end_min"] + 30):
                    if (cls["start_min"] - 30 >= helper["end_min"]
                            or cls["end_min"] + 30 <= helper["start_min"]):
                        continue
                    busy[helper["person"]] = max(
                        busy.get(helper["person"], 0), cls["end_min"] + 30)
    return busy


def helper_busy_text(busy):
    return "، ".join(f"{name} تا {minute // 60:02d}:{minute % 60:02d}"
                     for name, minute in sorted(busy.items()))


def helper_busy_html(busy):
    return "، ".join(
        f"{group_mention(name)} تا {minute // 60:02d}:{minute % 60:02d}"
        for name, minute in sorted(busy.items())
    )


def helper_unavailable_suffix(day, now, shift_date):
    busy = helper_busy_until(day, now, shift_date)
    return f"\nکمکی کلاسه تا {helper_busy_text(busy)}" if busy else ""


def helper_unavailable_html(day, now, shift_date):
    busy = helper_busy_until(day, now, shift_date)
    return (
        f"\n🧑‍🔧 کمکی فعلاً در دسترس نیست.\n"
        f"{helper_busy_html(busy)} کلاس داره"
        if busy else ""
    )



def people_line(label, entries, active=True):
    if not entries:
        return f"{label}: ثبت نشده"
    names = "\n".join(f"• {e['person']} ({phone_of(e['person'])})" for e in entries)
    suffix = "" if active else "\n(الان در بازه نیستند؛ فهرست امروز)"
    return f"{label}:\n{names}{suffix}"


def people_line_html(label, entries, active=True):
    if not entries:
        return f"{label}: ثبت نشده"
    names = "\n".join(
        f"• {group_mention(entry['person'])} ({escape(phone_of(entry['person']))})"
        for entry in entries
    )
    suffix = "" if active else "\n(الان در بازه نیستند؛ فهرست امروز)"
    return f"{label}:\n{names}{suffix}"


def compose_incident(staff, opt, kind_label, need_help, nazers, n_active, helpers, h_active):
    e = opt.get("entry")
    cls = (f"{group_mention(e['teacher'] or '')} – {escape(e['course'] or '')} ({span(e)})"
           if e and e["kind"] == "class" else "—")
    day, _, _ = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    lines = [
        f"🚨 مشکل در استودیو {escape(opt['label'])}",
        f"📅 {day_label(day, today_date)}",
        f"👤 ادمین: {escape(staff['name'])}",
        f"📚 کلاس: {cls}",
        f"🔧 مشکل: {escape(kind_label)}",
        "<b>🙋 نیاز به کمک حضوری دارد</b>" if need_help else "ℹ️ فقط اطلاع‌رسانی",
    ]
    if need_help:
        lines.extend([
            f"📞 شماره‌ی ادمین: {phone_display(staff)}",
            people_line_html("👁 ناظر", nazers, n_active),
            people_line_html("🧑‍🔧 کمکی", helpers, h_active),
        ])
    return "\n".join(lines)


def post_group(text):
    gid = config.OPS_GROUP_ID or config.GROUP_CHAT_ID
    return bool(gid) and send(gid, text, parse_mode="HTML")


def group_mentions(label, entries):
    if not entries:
        return ""

    names = [group_mention(entry["person"]) for entry in dedupe(entries)]
    return f"{label}: " + "، ".join(names)



def group_mention(name):
    def mention_person(person):
        staff = db.get_staff_by_name(person)
        telegram_id = staff["telegram_id"] if staff and "telegram_id" in staff.keys() else None

        if not telegram_id:
            teacher = db.get_teacher_assignment(person)
            telegram_id = teacher["telegram_id"] if teacher and "telegram_id" in teacher.keys() else None

        if telegram_id:
            return f'<a href="tg://user?id={int(telegram_id)}">{escape(person)}</a>'

        return escape(person)

    return " و ".join(mention_person(p) for p in split_people(name))


def dm_staff(name, text, parse_mode=None):
    st = db.get_staff_by_name(name)
    options = {"parse_mode": parse_mode} if parse_mode else {}
    return bool(st and st["telegram_id"] and send(st["telegram_id"], text, **options))


def _opts_from(entries):
    seen, out = set(), []
    for e in entries:
        label = e["studio"] or "آنلاین"
        if label in seen:
            continue
        seen.add(label)
        out.append({"studio": e["studio"], "label": label, "entry": e})
    return out


def _kb(rows):
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.row(*[types.InlineKeyboardButton(t, callback_data=d) for t, d in r])
    return kb


def _clear(c):
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:  # noqa
        pass


# ---------- شروع گفتگو ----------
def begin(chat, uid, staff, flow, all_studios=False):
    day, _, now = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    todays = [e for e in db.entries_on_date(today_date, day=day, kind="class")]
    mine = [e for e in todays if person_matches(e["person"], staff["name"])]
    base = [] if all_studios else (current_classes(mine, now) or mine)
    if not base:
        base = [e for e in todays if e["studio"]]
    opts = _opts_from(sorted(base, key=lambda e: e["start_min"]))
    if not opts:
        send(chat, f"برنامه‌ی {day_label(day, today_date)} ثبت نشده؛ از مسئول شیفت بپرس.", parse_mode="HTML")
        return
    S[uid] = {"flow": flow, "opts": opts, "opt": None, "kind": None}
    if len(opts) == 1 and flow != "help":
        S[uid]["opt"] = opts[0]
        _after_studio(chat, uid, staff)
        return
    rows = [[(o["label"], f"op:st:{i}")] for i, o in enumerate(opts[:20])]
    send(chat, "الان کدوم استودیویی؟", reply_markup=_kb(rows + [[("❌ لغو", "op:x")]]))


def _after_studio(chat, uid, staff):
    st = S[uid]
    label = st["opt"]["label"]
    day, _, _ = today_tomorrow()
    day_date = now_tehran().date().isoformat()
    if st["flow"] == "problem":
        rows = [[(KIND_LABEL[k], f"op:pk:{k}")] for k in KIND_LABEL]
        send(chat, f"📅 {day_label(day, day_date)}\nاستودیو «{escape(label)}» — مشکل از چه نوعیه؟",
             parse_mode="HTML", reply_markup=_kb(rows + [[("❌ لغو", "op:x")]]))
    elif st["flow"] == "help":
        finish_help(chat, uid, db.get_staff_by_tid(uid))
    else:
        send(chat, f"📅 {day_label(day, day_date)}\nالان تو استودیو «{escape(label)}» هستی؟",
             parse_mode="HTML",
             reply_markup=_kb([[("✅ بله، تایید", "op:cf:1"), ("❌ نه", "op:cf:0")]]))


# ---------- «مشکل دارم» ----------
@bot.message_handler(commands=["problem"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_PROBLEM, chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER)
def problem(m, s):
    begin(m.chat.id, m.from_user.id, s, "problem")


def finish_problem(chat, uid, staff, need_help):
    st = S.pop(uid)
    day, _, now = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    opt = st["opt"]
    nazers, na = roles_now("nazer", opt["studio"], day, now, today_date)
    helpers, ha = roles_now("komaki", opt["studio"], day, now, today_date)
    text = compose_incident(staff, opt, st["kind"], need_help, nazers, na, helpers, ha)
    e = opt.get("entry")
    db.add_incident(staff["name"], opt["label"], e["teacher"] if e else None, st["kind"], need_help)
    group_text = (
        f"{group_mention(staff['name'])} در {escape(opt['label'])} \n مشکل {escape(st['kind'])} دارد\n"
        + group_mentions("👁 ناظر", nazers) + "\n"
        + group_mentions("🧑‍🔧 کمکی", helpers)
    )
    posted = post_group(group_text)
    unavailable = helper_unavailable_html(day, now, today_date)
    helper_line = (people_line("🧑‍🔧 کمکی", helpers, ha) if helpers else
                   "🧑‍🔧 کمکی فعالی نیست" if unavailable else "🧑‍🔧 کمکی: ثبت نشده")
    send(chat, f"📅 {day_label(day, today_date)}\n"
         + ("✅ پیام در گروه ارسال شد.\n\n" if posted else "⚠️ گروه تنظیم نشده یا بات دسترسی ندارد؛ مستقیم تماس بگیر.\n\n")
         + people_line_html("👁 ناظر", nazers, na) + "\n"
         + (people_line_html("🧑‍🔧 کمکی", helpers, ha) if helpers else escape(helper_line))
         + unavailable, parse_mode="HTML")
    if need_help:  # ناظر و کمکی‌های فعال هم مستقیم خبر می‌شوند
        for x in nazers + helpers:
            dm_staff(x["person"], text, parse_mode="HTML")


# ---------- «کمکی کیه؟» ----------
def finish_number_only(chat, uid, staff):
    st = S.pop(uid)
    day, _, now = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    opt = st["opt"]
    helpers, active = roles_now("komaki", opt["studio"], day, now, today_date)
    e = opt.get("entry")
    db.add_incident(staff["name"], opt["label"], e["teacher"] if e else None, st["kind"], False)
    busy = helper_busy_until(day, now, today_date)
    helper_line = (people_line_html("🧑‍🔧 کمکی", helpers, active) if helpers else
                   "🧑‍🔧 کمکی فعالی نیست" if busy else "🧑‍🔧 کمکی: ثبت نشده")
    unavailable = (
        f"\n🧑‍🔧 کمکی فعلاً در دسترس نیست.\n"
        f"{helper_busy_html(busy)} کلاس داره"
        if busy else ""
    )
    send(chat, f"📅 {day_label(day, today_date)}\n"
         + (helper_line if helpers else escape(helper_line))
         + unavailable, parse_mode="HTML")


    
@bot.message_handler(commands=["helper"], chat_types=["private"])
@bot.message_handler(func=lambda m: m.text == BTN_HELPER, chat_types=["private"])
@staff_only(ADMIN, SUP, MANAGER, BOT_MANAGER)
def helper_info(m, s):
    day, _, now = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    helpers, active = roles_now("komaki", None, day, now, today_date)
    unavailable = helper_unavailable_html(day, now, today_date)
    if not helpers:
        message = "کمکی فعالی نیست." if unavailable else "کمکی ثبت نشده."
        send(m.chat.id, f"برای {day_label(day, today_date)} {message}{unavailable}",
             parse_mode="HTML")
        return
    text = (f"📅 {day_label(day, today_date)}\n" + people_line_html("🧑‍🔧 کمکی", helpers, active)
            + escape(helper_unavailable_suffix(day, now, today_date))
            + "\n\nمی‌خوای بهش بگم بیاد کمک؟")
    send(m.chat.id, text, parse_mode="HTML",
         reply_markup=_kb([[("بله، خبرش کن", "op:hy")],
                           [("نه، شماره کافی بود", "op:hn")]]))


def finish_help(chat, uid, staff):
    st = S.pop(uid)
    day, _, now = today_tomorrow()
    today_date = now_tehran().date().isoformat()
    opt = st["opt"]
    nazers, n_active = roles_now("nazer", opt["studio"], day, now, today_date)
    helpers, active = roles_now("komaki", opt["studio"], day, now, today_date)
    if not helpers:
        unavailable = helper_busy_until(day, now, today_date)
        helper_status = "کمکی فعالی نیست." if unavailable else "کمکی‌ای ثبت نشده."
        send(chat, f"📅 {day_label(day, today_date)}\n{helper_status}\n"
             + people_line_html("👁 ناظر", nazers, n_active), parse_mode="HTML")
        if unavailable:
            send(chat, f"کمکی تا کلاسه {helper_busy_html(unavailable)}", parse_mode="HTML")
        return
    text = (f"📅 {day_label(day, today_date)}\n"
            f"🙋 {escape(staff['name'])} از استودیو {escape(opt['label'])} کمک می‌خواد.\n"
            f"📞 {phone_display(staff)}")
    delivered = [x["person"] for x in helpers if dm_staff(x["person"], text, parse_mode="HTML")]
    tagged_helpers = [group_mention(helper["person"]) for helper in helpers]
    posted = post_group(
        f"{'، '.join(tagged_helpers)} "
        f"{'می‌تونی بری' if len(tagged_helpers) == 1 else 'می‌تونید برید'} "
        f"کمک {group_mention(staff['name'])}؟"
    )

    where = ("مستقیم در بات و گروه" if delivered and posted else
             "مستقیم در بات" if delivered else "در گروه" if posted else "ارسال پیام ناموفق بود")
    send(chat, f"📅 {day_label(day, today_date)}\n"
               + (f"✅ {where} خبر دادم.\n" if delivered or posted else f"⚠️ {where}.\n")
               + people_line_html("👁 ناظر", nazers, n_active) + "\n"
               + people_line_html("🧑‍🔧 کمکی", helpers, active)
               + helper_unavailable_html(day, now, today_date), parse_mode="HTML")


# ---------- دکمه‌ها ----------
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("op:"))
def on_op(c):
    _clear(c)
    uid, chat = c.from_user.id, c.message.chat.id
    staff = db.get_staff_by_tid(uid)
    if not staff:
        return
    staff = staff_context(staff)
    if staff["role"] == TEACHER:
        bot.answer_callback_query(c.id, "این عملیات برای استاد در دسترس نیست.", show_alert=True)
        S.pop(uid, None)
        return
    bot.answer_callback_query(c.id)
    parts = c.data.split(":")
    act = parts[1]
    if act == "x":
        S.pop(uid, None)
        send(chat, "لغو شد.")
        return
    if act == "hn":
        send(chat, "باشه 👍")
        return
    if act == "hy":
        begin(chat, uid, staff, "help")
        return
    st = S.get(uid)
    if not st:
        send(chat, "این درخواست منقضی شده؛ دوباره از منو شروع کن.")
        return
    if act == "st":
        st["opt"] = st["opts"][int(parts[2])]
        _after_studio(chat, uid, staff)
    elif act == "pk":
        st["kind"] = KIND_LABEL[parts[2]]
        send(chat, f"«{st['kind']}» در استودیو «{st['opt']['label']}»؛ کمک حضوری لازم داری؟",
             reply_markup=_kb([[("🙋 آره، کمک لازم دارم", "op:nh:1")],
                               [("📞 نه، فقط شماره‌ها", "op:nh:0")]]))
    elif act == "nh":
        if parts[2] == "1":
            finish_problem(chat, uid, staff, True)
        else:
            finish_number_only(chat, uid, staff)
    elif act == "cf":
        if parts[2] == "1":
            finish_help(chat, uid, staff)
        else:
            begin(chat, uid, staff, "help", all_studios=True)
