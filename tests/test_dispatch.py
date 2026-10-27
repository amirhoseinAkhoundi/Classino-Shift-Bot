"""شبیه‌سازی آپدیت‌های واقعی تلگرام از مسیر دیسپچر خودِ telebot (بدون شبکه)."""
import itertools
from datetime import datetime, timezone

import pytest
from telebot import types

import app
import bot as _bot  # noqa: F401  (ثبت همه‌ی هندلرها به همان ترتیب اجرای واقعی)
import db
import daily_notifications
import h_ops
import h_manage
import h_staff
from app import (BTN_FREE, BTN_HELPER, BTN_MY, BTN_PROBLEM, BTN_SCHEDULE_DATES, BTN_STATS,
                 BTN_DAILY_NOTIFICATIONS, BTN_MODE, BTN_MODE_ADMIN, BTN_STAFF_MANAGEMENT,
                 BTN_TEACHER_MANAGEMENT, BTN_TEAM, BTN_STUDIO_MANAGEMENT, BTN_TEACHER_ADMIN,
                 BTN_TEACHER_CLASSES, BOT_MANAGER, OWNER_TELEGRAM_ID, TEACHER, menu_for)
from tests.test_flow import SAMPLE, env  # noqa: F401

_ids = itertools.count(1)
DOCS = []
DELETED = []


def _user(uid):
    return {"id": uid, "is_bot": False, "first_name": "u"}


def message(uid, text, chat_id=None, chat_type="private", mid=None, reply_to_uid=None):
    ents = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
    payload = {"update_id": next(_ids), "message": {
        "message_id": mid or next(_ids),
        "date": int(datetime(2026, 10, 18, 10, tzinfo=timezone.utc).timestamp()),
        "text": text, "entities": ents, "from": _user(uid),
        "chat": {"id": chat_id if chat_id is not None else uid, "type": chat_type}}}
    if reply_to_uid is not None:
        payload["message"]["reply_to_message"] = {
            "message_id": 99,
            "date": 0,
            "from": _user(reply_to_uid),
            "chat": payload["message"]["chat"],
            "text": "سلام",
        }
    return types.Update.de_json(payload)


def callback(uid, data):
    return types.Update.de_json({"update_id": next(_ids), "callback_query": {
        "id": "c", "from": _user(uid), "chat_instance": "x", "data": data,
        "message": {"message_id": 5, "date": 0, "chat": {"id": uid, "type": "private"}}}})


@pytest.fixture
def live(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app.bot, "threaded", False)
    monkeypatch.setattr(app.bot, "exception_handler", None)      # خطاها بالا بیایند
    monkeypatch.setattr(app.bot, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(app.bot, "edit_message_reply_markup", lambda *a, **k: None)
    monkeypatch.setattr(app.bot, "set_my_commands", lambda *a, **k: True)
    DOCS.clear()
    DELETED.clear()
    monkeypatch.setattr(app.bot, "send_document", lambda chat, f, **k: DOCS.append((chat, k)))
    monkeypatch.setattr(app.bot, "delete_message", lambda chat, mid: DELETED.append((chat, mid)))
    monkeypatch.setattr(h_ops, "today_tomorrow", lambda: ("یکشنبه", "دوشنبه", 17 * 60))
    monkeypatch.setattr(h_ops, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    monkeypatch.setattr(h_manage, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    app.bot.process_new_updates([message(2, SAMPLE, chat_id=-100, chat_type="supergroup", mid=10)])
    env.clear()
    return env


def say(uid, text, **kw):
    app.bot.process_new_updates([message(uid, text, **kw)])


def to(sent, chat):
    return [t for c, t in sent if c == chat]


def register_owner():
    db.upsert_staff("مدیر اصلی", "9120000080", BOT_MANAGER)
    db.bind_telegram(db.find_staff_by_phone("9120000080")["id"], OWNER_TELEGRAM_ID)


def test_group_schedule_goes_through_real_dispatch(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app.bot, "threaded", False)
    monkeypatch.setattr(app.bot, "exception_handler", None)
    app.bot.process_new_updates([message(2, SAMPLE, chat_id=-100, chat_type="supergroup", mid=10)])
    assert "برنامه‌ی جدید" in to(env, 3)[0]


def test_my_command_and_button(live):
    say(3, "/my")
    say(3, BTN_MY)
    t = to(live, 3)
    assert len(t) == 2 and "📅 یکشنبه" in t[0] and "طوسی2" in t[0]


def test_teacher_my_button_shows_classes_by_teacher_name(live, monkeypatch):
    monkeypatch.setattr(h_staff, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    db.assign_teacher("استاد نمونه", "فرستنده", "")
    db.set_teacher_contact("استاد نمونه", "9120000777", "فرستنده")
    db.bind_telegram(db.find_staff_by_phone("9120000777")["id"], 77)
    assert db.get_staff_by_tid(77)["role"] == TEACHER
    labels = {button["text"] for row in menu_for(db.get_staff_by_tid(77)).keyboard for button in row}
    assert BTN_TEACHER_CLASSES in labels and BTN_MY not in labels
    with db.connect() as conn:
        entry = conn.execute(
            "SELECT rowid FROM entries WHERE kind='class' ORDER BY rowid LIMIT 1").fetchone()
        conn.execute("UPDATE entries SET teacher=? WHERE rowid=?", ("استاد نمونه", entry[0]))

    say(77, BTN_TEACHER_CLASSES)
    text = to(live, 77)[-1]
    assert "استاد نمونه" in text
    assert "آریان ریاضی تجربی و حسابان یازدهم" not in text
    assert "در این روز کلاسی ندارید." in text


def test_teacher_only_has_my_classes_and_no_helper_or_problem_access(live, monkeypatch):
    commands = []
    alerts = []
    monkeypatch.setattr(app.bot, "set_my_commands",
                        lambda items, **kwargs: commands.extend(item.command for item in items))
    monkeypatch.setattr(app.bot, "answer_callback_query",
                        lambda *args, **kwargs: alerts.append(kwargs))
    db.assign_teacher("استاد دسترسی", "فرستنده", "")
    db.set_teacher_contact("استاد دسترسی", "9120000778", "فرستنده")
    staff = db.find_staff_by_phone("9120000778")
    db.bind_telegram(staff["id"], 78)
    teacher = db.get_staff_by_tid(78)

    say(78, "/start")
    labels = {button["text"] for row in menu_for(teacher).keyboard for button in row}
    assert labels == {BTN_TEACHER_CLASSES, BTN_TEACHER_ADMIN,
                      f"{BTN_DAILY_NOTIFICATIONS}: روشن"}
    assert commands == ["my"]

    say(78, "/problem")
    assert "این دستور برای نقش تو نیست" in to(live, 78)[-1]
    say(78, "/helper")
    assert "این دستور برای نقش تو نیست" in to(live, 78)[-1]
    say(78, "/today")
    assert "این دستور برای نقش تو نیست" in to(live, 78)[-1]
    say(78, "/help")
    assert "این دستور برای نقش تو نیست" in to(live, 78)[-1]

    app.bot.process_new_updates([callback(78, "op:hy")])
    assert 78 not in h_ops.S
    assert alerts[-1].get("show_alert") is True


def test_daily_notifications_toggle_is_direct_and_available_to_every_role(live):
    roles = ("admin", "supervisor", "shift_manager", "bot_manager")
    for index, role in enumerate(roles, start=100):
        db.upsert_staff(f"کاربر {role}", f"9120000{index:03d}", role)
        staff = db.find_staff_by_phone(f"9120000{index:03d}")
        db.bind_telegram(staff["id"], index)
        labels = {button["text"] for row in menu_for(db.get_staff_by_tid(index)).keyboard
                  for button in row}
        assert f"{BTN_DAILY_NOTIFICATIONS}: روشن" in labels

    assert app.daily_notifications_enabled(db.get_staff_by_tid(3)["id"])
    say(3, f"{BTN_DAILY_NOTIFICATIONS}: روشن")
    assert not app.daily_notifications_enabled(db.get_staff_by_tid(3)["id"])
    labels = {button["text"] for row in menu_for(db.get_staff_by_tid(3)).keyboard
              for button in row}
    assert f"{BTN_DAILY_NOTIFICATIONS}: خاموش" in labels

    say(3, f"{BTN_DAILY_NOTIFICATIONS}: خاموش")
    assert app.daily_notifications_enabled(db.get_staff_by_tid(3)["id"])


def test_daily_notifications_send_today_schedule_once_to_all_staff_roles(live, monkeypatch):
    db.assign_teacher("استاد یادآوری", "فرستنده", "")
    db.set_teacher_contact("استاد یادآوری", "9120000780", "فرستنده")
    teacher = db.find_staff_by_phone("9120000780")
    db.bind_telegram(teacher["id"], 80)
    roles = ("admin", "supervisor", "shift_manager", "bot_manager")
    for index, role in enumerate(roles, start=110):
        db.upsert_staff(f"کاربر {role}", f"9120001{index:03d}", role)
        staff = db.find_staff_by_phone(f"9120001{index:03d}")
        db.bind_telegram(staff["id"], index)

    entries = [{
        "id": 1, "day": "یکشنبه", "kind": "class", "start_min": 900, "end_min": 960,
        "teacher": "استاد یادآوری", "course": "ریاضی", "studio": "بزرگ",
        "person": "ادمین نمونه", "shift_date": "2026-10-18",
    }]
    reminder_recipients = [db.get_staff_by_tid(uid) for uid in
                           [80, *range(110, 110 + len(roles))]]
    monkeypatch.setattr(db, "list_staff", lambda roles=None: reminder_recipients)
    monkeypatch.setattr(db, "entries_for_teacher", lambda name, shift_date=None: entries)
    monkeypatch.setattr(db, "entries_for_person", lambda name, shift_date=None: entries)
    sent = []
    monkeypatch.setattr(app.bot, "send_message",
                        lambda chat, text, **kwargs: sent.append((chat, text)))

    assert daily_notifications.send_daily_notifications("2026-10-18") == len(roles) + 1
    assert len(sent) == len(roles) + 1
    teacher_text = next(text for chat, text in sent if chat == 80)
    assert "استاد استاد یادآوری گرامی" in teacher_text
    assert "کلاس‌های امروز شما" in teacher_text
    assert "ادمین: ادمین نمونه" in teacher_text
    assert daily_notifications.send_daily_notifications("2026-10-18") == 0
    assert len(sent) == len(roles) + 1


def test_teacher_can_look_up_class_admin_names_and_phones_by_day(live, monkeypatch):
    db.assign_teacher("استاد ارتباط", "فرستنده", "")
    db.set_teacher_contact("استاد ارتباط", "9120000779", "فرستنده")
    teacher = db.find_staff_by_phone("9120000779")
    db.bind_telegram(teacher["id"], 79)
    db.upsert_staff("بابک", "9120000099", "admin")
    with db.connect() as conn:
        entries = conn.execute(
            "SELECT id FROM entries WHERE kind='class' ORDER BY id LIMIT 2").fetchall()
        conn.execute("UPDATE entries SET teacher=?, person=? WHERE id=?",
                     ("استاد ارتباط", "بابک و احسانی", entries[0]["id"]))
        conn.execute("UPDATE entries SET teacher=?, person=? WHERE id=?",
                     ("استاد ارتباط", "احسانی و ادمین بدون شماره", entries[1]["id"]))

    sent = []
    monkeypatch.setattr(app.bot, "send_message",
                        lambda chat, text, **kwargs: sent.append((chat, text, kwargs)))
    say(79, BTN_TEACHER_CLASSES)
    assert "ادمین: بابک و احسانی" in sent[-1][1]

    say(79, BTN_TEACHER_ADMIN)
    day_button = sent[-1][2]["reply_markup"].keyboard[0][0]
    app.bot.process_new_updates([callback(79, day_button.callback_data)])
    class_buttons = sent[-1][2]["reply_markup"].keyboard
    class_callbacks = [row[0].callback_data for row in class_buttons]
    assert len(class_callbacks) == 2
    from analysis import span
    matching = h_staff._teacher_schedule_rows("استاد ارتباط")[:2]
    for button_row, entry in zip(class_buttons, matching):
        label = button_row[0].text
        assert "استاد ارتباط" not in label
        assert (entry["course"] or "بدون عنوان") in label
        assert (entry["studio"] or "آنلاین") in label
        assert span(entry) in label

    app.bot.process_new_updates([callback(79, class_callbacks[0])])
    assert "بابک — 09120000099" in sent[-1][1]
    assert "احسانی — 09120000007" in sent[-1][1]
    assert "ادمین(های) این کلاس:" in sent[-1][1]

    app.bot.process_new_updates([callback(79, class_callbacks[1])])
    assert "احسانی — 09120000007" in sent[-1][1]
    assert "ادمین بدون شماره — شماره موجود نیست" in sent[-1][1]

    with db.connect() as conn:
        conn.execute("UPDATE entries SET person=? WHERE id=?",
                     ("ادمین به‌روز", entries[0]["id"]))
    say(79, BTN_TEACHER_CLASSES)
    assert "ادمین: ادمین به‌روز" in sent[-1][1]


def test_my_schedule_marks_past_today_and_days_off(live, monkeypatch):
    monkeypatch.setattr(h_staff, "today_tomorrow", lambda: ("یکشنبه", "دوشنبه", 17 * 60))
    monkeypatch.setattr(h_staff, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    say(3, BTN_MY)
    text = to(live, 3)[0]
    assert "شنبه 2 آبان" in text
    assert "<s>شنبه 25 مهر</s>" not in text
    assert "<b>یکشنبه 26 مهر</b>" in text
    assert "در این روز کلاسی نداری." in text


def test_today_and_tomorrow_use_current_calendar_dates_not_saved_week(live, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(h_staff, "today_tomorrow", lambda: ("دوشنبه", "سه‌شنبه", 12 * 60))
    monkeypatch.setattr(h_staff, "now_tehran", lambda: datetime(2026, 10, 12, 12))
    with db.connect() as conn:
        conn.execute(
            "UPDATE entries SET day='دوشنبه', shift_date='2026-10-26' "
            "WHERE person='وحید' AND kind='class'"
        )
        conn.execute(
            "UPDATE entries SET shift_date='2026-10-12' WHERE id=("
            "SELECT MIN(id) FROM entries WHERE person='وحید' AND kind='class')"
        )

    say(3, "/today")
    today_text = to(live, 3)[-1]
    assert "<b>دوشنبه 20 مهر</b>" in today_text and "طوسی2" in today_text
    assert "2026-10-26" not in today_text

    say(3, "/tomorrow")
    tomorrow_text = to(live, 3)[-1]
    assert "سه‌شنبه 21 مهر" in tomorrow_text and "کلاسی نداری" in tomorrow_text


def test_role_menus_hide_management_options_from_admin(live):
    admin = db.get_staff_by_tid(3)
    supervisor = db.get_staff_by_tid(2)
    manager = db.get_staff_by_tid(1)

    def labels(staff):
        return {button["text"] for row in menu_for(staff).keyboard for button in row}

    assert BTN_STATS not in labels(admin) and BTN_TEAM not in labels(admin)
    assert BTN_STATS in labels(supervisor) and BTN_TEAM in labels(supervisor)
    assert BTN_STATS in labels(manager) and BTN_TEAM not in labels(manager)
    assert BTN_STAFF_MANAGEMENT not in labels(admin) and BTN_STAFF_MANAGEMENT not in labels(supervisor)
    assert BTN_STAFF_MANAGEMENT in labels(manager)


def test_bot_manager_menu_and_permissions(live):
    db.upsert_staff("آخوندی", "9129271665", BOT_MANAGER)
    db.bind_telegram(db.find_staff_by_phone("9129271665")["id"], 8)
    bot_manager = db.get_staff_by_tid(8)
    labels = {button["text"] for row in menu_for(bot_manager).keyboard for button in row}
    assert BTN_STATS in labels and BTN_TEAM in labels and BTN_FREE in labels
    assert BTN_STAFF_MANAGEMENT in labels
    say(8, "/stats")
    app.bot.process_new_updates([callback(8, "stats:studios")])
    assert "بازه‌ی گزارش را انتخاب کن" in to(live, 8)[-1]
    app.bot.process_new_updates([callback(8, "stats:run:studios:week")])
    assert "استفاده از استودیوها" in to(live, 8)[-1]
    assert DOCS[-1][1]["visible_file_name"] == "classino-studios-week.xlsx"


def test_bot_manager_can_switch_between_manager_and_regular_admin_modes(live):
    db.upsert_staff("مدیر ربات", "9120000088", BOT_MANAGER, "فرستنده")
    db.bind_telegram(db.find_staff_by_phone("9120000088")["id"], 8)
    say(8, BTN_MODE_ADMIN)
    assert "ادمین ساده" in to(live, 8)[-1]
    app.bot.process_new_updates([callback(8, "manager_mode:admin")])
    manager = db.get_staff_by_tid(8)
    labels = {button["text"] for row in menu_for(manager).keyboard for button in row}
    assert db.get_setting("manager_mode:8") == "admin"
    assert BTN_MODE in labels and BTN_MY in labels
    assert BTN_PROBLEM in labels and BTN_HELPER in labels
    assert BTN_TEAM not in labels and BTN_FREE not in labels and BTN_STATS not in labels
    assert BTN_STAFF_MANAGEMENT not in labels
    assert app.staff_context(manager)["role"] == "admin"
    say(8, "/team")
    assert "این دستور برای نقش تو نیست" in to(live, 8)[-1]
    say(8, "/stats")
    assert "این دستور برای نقش تو نیست" in to(live, 8)[-1]
    say(8, "/help")
    assert "/team" not in to(live, 8)[-1]

    say(8, BTN_MODE)
    app.bot.process_new_updates([callback(8, "manager_mode:bot_manager")])
    labels = {button["text"] for row in menu_for(manager).keyboard for button in row}
    assert BTN_MODE_ADMIN in labels and BTN_MODE not in labels
    assert BTN_STAFF_MANAGEMENT in labels


def test_owner_can_manage_teacher_names_without_a_remote_teacher_list(live):
    import h_admin
    import h_schedule

    register_owner()
    teacher_index = h_admin._teacher_names().index("بام رفیع")
    known_index = h_admin._known_names("teacher").index("بام رفیع")
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:select_name:{teacher_index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{known_index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:namerename:teacher:{known_index}")])
    say(OWNER_TELEGRAM_ID, "بام رفیع")
    assert "بام رفیع" in db.get_known("teacher")

    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع"
    assert h_schedule.known("remote_teacher", h_schedule.REMOTE)


def test_owner_can_add_arbitrary_alias_for_teacher_from_panel(live):
    import h_admin
    import h_schedule

    register_owner()
    db.add_known("teacher", ["بام رفیع"])
    teacher_index = h_admin._teacher_names().index("بام رفیع")
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:select_name:{teacher_index}")])
    known_index = h_admin._known_names("teacher").index("بام رفیع")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{known_index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:aliasadd:teacher:{known_index}")])
    say(OWNER_TELEGRAM_ID, "بام‌رفیع")

    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع"
    known_index = h_admin._known_names("teacher").index("بام رفیع")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{known_index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:namerename:teacher:{known_index}")])
    say(OWNER_TELEGRAM_ID, "بام رفیع اصلی")
    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع اصلی"


def test_teacher_panel_merges_schedule_names_and_assignments(live, monkeypatch):
    import h_admin

    register_owner()
    db.assign_teacher("همدانی", "فرستنده", "")
    messages = []
    monkeypatch.setattr(app.bot, "send_message",
                        lambda chat_id, text, **kwargs: messages.append((chat_id, text, kwargs)))
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)

    teacher_names = h_admin._teacher_names()
    teacher_keys = [h_admin.key(name) for name in teacher_names]
    assert len(teacher_keys) == len(set(teacher_keys))
    assert "همدانی" in teacher_names and "بام رفیع" in teacher_names
    buttons = {button.text for row in messages[-1][2]["reply_markup"].keyboard for button in row}
    assert any("همدانی" in label for label in buttons)
    assert any("بام رفیع" in label for label in buttons)
    assert not any("نام‌های استاد در برنامه" in label for label in buttons)

    known_index = teacher_names.index("بام رفیع")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:select_name:{known_index}")])
    assert "هنوز سرپرست" in messages[-1][1]
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:assign:{known_index}")])
    supervisor_id = db.get_staff_by_name("فرستنده")["id"]
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:addsup:{supervisor_id}")])
    say(OWNER_TELEGRAM_ID, "09120000126")
    assignment = db.get_teacher_assignment("بام رفیع")
    assert assignment and assignment["supervisor"] == "فرستنده"
    assert assignment["phone"] == "9120000126"


def test_owner_manages_studios_from_separate_home_panel(live):
    import h_admin
    import h_schedule

    register_owner()
    say(OWNER_TELEGRAM_ID, BTN_STUDIO_MANAGEMENT)
    assert "مدیریت استودیوها" in to(live, OWNER_TELEGRAM_ID)[-1]
    keyboard = menu_for(db.get_staff_by_tid(OWNER_TELEGRAM_ID)).keyboard
    buttons = {button["text"] for row in keyboard for button in row}
    assert BTN_TEACHER_MANAGEMENT in buttons and BTN_STUDIO_MANAGEMENT in buttons

    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "studiom:nameadd:studio")])
    say(OWNER_TELEGRAM_ID, "استودیو آزمایشی")
    index = h_admin._known_names("studio").index("استودیو آزمایشی")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:name:studio:{index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:building:studio:{index}")])
    say(OWNER_TELEGRAM_ID, "ساختمان آزمایشی")
    assert h_ops.load_buildings()[h_ops.key("استودیو آزمایشی")] == "ساختمان آزمایشی"

    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:name:studio:{index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:aliasadd:studio:{index}")])
    say(OWNER_TELEGRAM_ID, "استودیو تست")
    assert h_schedule.known("studio", h_schedule.STUDIOS)["استودیوتست"] == "استودیو آزمایشی"

    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:namerename:studio:{index}")])
    say(OWNER_TELEGRAM_ID, "استودیو اصلی")
    assert h_schedule.known("studio", h_schedule.STUDIOS)["استودیوتست"] == "استودیو اصلی"
    assert h_ops.load_buildings()[h_ops.key("استودیو اصلی")] == "ساختمان آزمایشی"
    index = h_admin._known_names("studio").index("استودیو اصلی")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:namedelete:studio:{index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"studiom:namedeleteconfirm:studio:{index}")])
    assert "استودیو اصلی" not in h_admin._known_names("studio")
    assert h_ops.key("استودیو اصلی") not in h_ops.load_buildings()
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, "teacherm:nameadd:studio")])
    say(OWNER_TELEGRAM_ID, "استودیو مسیر تکراری")
    assert "استودیو مسیر تکراری" not in db.get_known("studio")


def test_teacher_management_has_no_schedule_approval_toggle(live):
    register_owner()
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    assert "پیش‌نمایش" not in to(live, OWNER_TELEGRAM_ID)[-1]


def test_staff_management_add_edit_delete_updates_database_and_csv(live, tmp_path, monkeypatch):
    import h_manage

    csv_path = tmp_path / "staff.csv"
    csv_path.write_text("name,phone,role,supervisor\n", encoding="utf-8")
    monkeypatch.setattr(h_manage, "STAFF_CSV_PATH", str(csv_path))

    say(1, BTN_STAFF_MANAGEMENT)
    app.bot.process_new_updates([callback(1, "staffm:add")])
    say(1, "کارمند آزمایشی")
    app.bot.process_new_updates([callback(1, "staffm:addrole:admin")])
    say(1, "09330001122")
    supervisor_id = db.get_staff_by_name("فرستنده")["id"]
    app.bot.process_new_updates([callback(1, f"staffm:addsup:{supervisor_id}")])

    created = db.get_staff_by_name("کارمند آزمایشی")
    assert created["role"] == "admin" and created["supervisor"] == "فرستنده"
    assert "کارمند آزمایشی,9330001122,admin,فرستنده" in csv_path.read_text(encoding="utf-8")

    app.bot.process_new_updates([callback(1, "staffm:category:admin")])
    app.bot.process_new_updates([callback(1, f"staffm:person:{created['id']}")])
    app.bot.process_new_updates([callback(1, f"staffm:edit:{created['id']}")])
    app.bot.process_new_updates([callback(1, f"staffm:field:{created['id']}:phone")])
    say(1, "09330001123")
    edited = db.get_staff_by_name("کارمند آزمایشی")
    assert edited["phone"] == "9330001123"
    assert "کارمند آزمایشی,9330001123,admin,فرستنده" in csv_path.read_text(encoding="utf-8")

    app.bot.process_new_updates([callback(1, "staffm:category:admin")])
    app.bot.process_new_updates([callback(1, f"staffm:person:{created['id']}")])
    app.bot.process_new_updates([callback(1, f"staffm:delete:{created['id']}")])
    app.bot.process_new_updates([callback(1, f"staffm:deleteconfirm:{created['id']}")])
    assert db.get_staff_by_name("کارمند آزمایشی") is None
    assert "کارمند آزمایشی" not in csv_path.read_text(encoding="utf-8")


def test_staff_management_adds_supervisor_without_asking_for_supervisor(live, tmp_path, monkeypatch):
    import h_manage

    csv_path = tmp_path / "staff.csv"
    csv_path.write_text("name,phone,role,supervisor\n", encoding="utf-8")
    monkeypatch.setattr(h_manage, "STAFF_CSV_PATH", str(csv_path))

    say(1, BTN_STAFF_MANAGEMENT)
    app.bot.process_new_updates([callback(1, "staffm:add")])
    say(1, "سرپرست آزمایشی")
    app.bot.process_new_updates([callback(1, "staffm:addrole:supervisor")])
    say(1, "09330001124")

    supervisor = db.get_staff_by_name("سرپرست آزمایشی")
    assert supervisor["role"] == "supervisor" and supervisor["supervisor"] is None
    assert "سرپرست آزمایشی,9330001124,supervisor," in csv_path.read_text(encoding="utf-8")


def test_stats_choice_limits_studio_lifetime_report(live):
    say(1, "/stats")
    assert "کدوم گزارش رو می‌خوای؟" in to(live, 1)[0]
    app.bot.process_new_updates([callback(1, "stats:studios")])
    assert "بازه‌ی گزارش را انتخاب کن" in to(live, 1)[-1]
    assert (1, 5) in DELETED
    from h_manage import _stats_period_keyboard
    choices = {button.callback_data for row in _stats_period_keyboard("studios").keyboard for button in row}
    assert {"stats:run:studios:week", "stats:run:studios:month",
            "stats:run:studios:all"} <= choices
    app.bot.process_new_updates([callback(1, "stats:run:studios:all")])
    assert "کل دوره" in to(live, 1)[-1]
    assert DOCS[-1][1]["visible_file_name"] == "classino-studios-all-time.xlsx"


def test_supervisor_stats_only_include_own_admins(live):
    db.upsert_staff("وحید", "9120000003", "admin", "فرستنده")
    say(2, "/stats")
    app.bot.process_new_updates([callback(2, "stats:admins")])
    assert "بازه‌ی گزارش را انتخاب کن" in to(live, 2)[-1]
    app.bot.process_new_updates([callback(2, "stats:run:admins:month")])
    report = to(live, 2)[-1]
    assert "وحید" in report and "مهران" not in report
    app.bot.process_new_updates([callback(2, "stats:studios")])
    assert "فقط برای مسئول شیفت" in to(live, 2)[-1]


def test_free_button_guides_day_and_time_and_lists_admins_and_helpers(live):
    db.upsert_staff("ادمین آزاد", "9120000999", "admin")
    say(1, BTN_FREE)
    assert "برای کدوم روز" in to(live, 1)[0]
    app.bot.process_new_updates([callback(1, "free:day:1")])
    assert "بازه‌ی ساعتِ یکشنبه" in to(live, 1)[1]
    say(1, "15-16")
    report = to(live, 1)[-1]
    assert "ادمین‌های شیفت‌دار و آزاد" in report
    assert "🟢 کمکی‌های این بازه" in report and "اشتری — 09120000006" in report
    assert "🔴 ادمین‌های شیفت‌دار و مشغول" in report
    assert "⚪️ ادمین‌های بدون شیفت در این روز" in report
    assert "ادمین آزاد — 09120000999" in report
    assert "ناظر" not in report and "مهدیان" not in report


def test_free_report_categories_are_ordered_and_distinguish_shift_status(live, monkeypatch):
    db.upsert_staff("آزاد در بازه", "9120000091", "admin")
    db.upsert_staff("مشغول در بازه", "9120000092", "admin")
    db.upsert_staff("بدون شیفت امروز", "9120000093", "admin")
    monkeypatch.setattr(h_manage.db, "entries_on_date", lambda *args, **kwargs: [
        {"person": "آزاد در بازه", "day": "یکشنبه", "shift_date": "2026-10-18",
         "start_min": 600, "end_min": 660, "kind": "class"},
        {"person": "مشغول در بازه", "day": "یکشنبه", "shift_date": "2026-10-18",
         "start_min": 720, "end_min": 780, "kind": "class"},
        {"person": "اشتری", "day": "یکشنبه", "shift_date": "2026-10-18",
         "start_min": 720, "end_min": 780, "kind": "komaki"},
    ])

    say(1, "/free یکشنبه 12-13")
    report = to(live, 1)[-1]
    categories = [
        "✅ ادمین‌های شیفت‌دار و آزاد:",
        "🟢 کمکی‌های این بازه:",
        "🔴 ادمین‌های شیفت‌دار و مشغول:",
        "⚪️ ادمین‌های بدون شیفت در این روز:",
    ]
    assert [report.index(category) for category in categories] == sorted(
        report.index(category) for category in categories)
    assert "آزاد در بازه — 09120000091" in report
    assert "اشتری — 09120000006" in report
    assert "مشغول در بازه — 09120000092" in report
    assert "بدون شیفت امروز — 09120000093" in report


def test_unverified_user_is_asked_for_contact_and_blocked(live):
    say(77, "/start")
    say(77, "/my")
    t = to(live, 77)
    assert "اشتراک بگذار" in t[0] and "/start" in t[1]


def test_start_registers_commands_for_current_role(live, monkeypatch):
    registered = []
    monkeypatch.setattr(app.bot, "set_my_commands", lambda commands, **kwargs: registered.append(
        ([command.command for command in commands], kwargs["scope"].chat_id)) or True)

    say(2, "/start")
    supervisor_commands, supervisor_chat = registered[-1]
    assert supervisor_chat == 2 and "team" in supervisor_commands
    assert "stats" in supervisor_commands and "addstaff" not in supervisor_commands

    db.upsert_staff("آخوندی", "9129271665", BOT_MANAGER, "فرستنده")
    db.bind_telegram(db.find_staff_by_phone("9129271665")["id"], 8)
    say(8, "/start")
    manager_commands, manager_chat = registered[-1]
    assert manager_chat == 8 and "team" in manager_commands and "addstaff" not in manager_commands
    assert "آخوندی" in to(live, 8)[-1] and "ادمین" in to(live, 8)[-1]
    assert "مدیر ربات" not in to(live, 8)[-1]


def test_bot_manager_with_supervisor_is_listed_as_admin_and_keeps_manager_access(live, monkeypatch):
    db.upsert_staff("سهیل", "9120000099", "supervisor")
    db.bind_telegram(db.find_staff_by_phone("9120000099")["id"], 9)
    db.upsert_staff("آخوندی", "9129271665", BOT_MANAGER, "سهیل")
    db.bind_telegram(db.find_staff_by_phone("9129271665")["id"], 8)

    say(9, "/staff")
    assert "آخوندی — ادمین — 09129271665 — سرپرست: سهیل" in to(live, 9)[-1]
    say(9, "/free یکشنبه 12-13")
    assert "آخوندی — 09129271665" in to(live, 9)[-1]
    assert "ادمین‌های بدون شیفت در این روز" in to(live, 9)[-1]

    sent = []
    monkeypatch.setattr(app.bot, "send_message",
                        lambda chat_id, text, **kwargs: sent.append((chat_id, text, kwargs)))
    say(8, BTN_STAFF_MANAGEMENT)
    app.bot.process_new_updates([callback(8, "staffm:category:admin")])
    buttons = sent[-1][2]["reply_markup"].keyboard
    assert any("آخوندی" in button.text for row in buttons for button in row)
    assert db.get_staff_by_tid(8)["role"] == BOT_MANAGER


def test_bot_managers_are_in_admin_statistics_scope(live):
    db.upsert_staff("آخوندی", "9129271665", BOT_MANAGER, "فرستنده")
    db.upsert_staff("مدیر ربات", "9120000088", BOT_MANAGER)
    entries = [
        {"person": "آخوندی"},
        {"person": "وحید"},
        {"person": "سهیل"},
    ]
    result = h_manage._admin_report_entries(db.get_staff_by_name("مدیر ربات"), entries)
    assert [entry["person"] for entry in result] == ["آخوندی", "وحید"]


def test_roles_are_enforced(live):
    say(3, "/free یکشنبه 12-13")       # ادمین عادی
    assert "برای نقش تو نیست" in to(live, 3)[0]
    say(1, "/free یکشنبه 12:00-13:00")  # مسئول شیفت
    assert "ادمین‌های شیفت‌دار و آزاد" in to(live, 1)[0]


def test_helper_confirmation_buttons_are_on_separate_rows(live, monkeypatch):
    markup = []
    monkeypatch.setattr(app.bot, "send_message",
                        lambda chat_id, text, **kwargs: markup.append(kwargs.get("reply_markup")))
    say(3, BTN_HELPER)
    buttons = markup[-1].keyboard
    assert [[button.text for button in row] for row in buttons] == [
        ["بله، خبرش کن"], ["نه، شماره کافی بود"]]


def test_stats_sends_text_and_excel(live):
    say(1, "/stats")
    assert "کدوم گزارش رو می‌خوای؟" in to(live, 1)[0]
    app.bot.process_new_updates([callback(1, "stats:admins")])
    assert "بازه‌ی گزارش را انتخاب کن" in to(live, 1)[-1]
    app.bot.process_new_updates([callback(1, "stats:run:admins:month")])
    assert "آمار ادمین‌ها" in to(live, 1)[-1]
    assert DOCS and DOCS[0][1]["visible_file_name"] == "classino-admins-month.xlsx"


def test_owner_can_correct_a_shift_date_and_only_owner_can_open_panel(live):
    register_owner()
    original = db.active_sheets()[0]

    say(1, BTN_SCHEDULE_DATES)
    assert "فقط برای مدیر اصلی" in to(live, 1)[-1]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"shiftdate:sheet:{original['id']}")])
    assert db.active_sheets()[0]["scheduled_date"] == "2026-10-18"

    say(OWNER_TELEGRAM_ID, BTN_SCHEDULE_DATES)
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"shiftdate:sheet:{original['id']}")])
    assert "1405/07/26" in to(live, OWNER_TELEGRAM_ID)[-1]
    say(OWNER_TELEGRAM_ID, "1405/08/04")
    assert "نه یکشنبه" in to(live, OWNER_TELEGRAM_ID)[-1]
    say(OWNER_TELEGRAM_ID, "1405/08/03")

    assert {entry["shift_date"] for entry in db.active_entries("یکشنبه")} == {"2026-10-25"}
    assert "به 1405/08/03 تغییر کرد" in to(live, OWNER_TELEGRAM_ID)[-1]
    assert any(chat == 3 and "تاریخ شیفت یکشنبه اصلاح شد" in text for chat, text in live)


def test_owner_teacher_panel_adds_edits_and_deletes_assignment(live):
    register_owner()
    supervisor_id = db.get_staff_by_name("فرستنده")["id"]
    db.upsert_staff("سرپرست دوم", "9120000081", "supervisor")
    second_supervisor_id = db.get_staff_by_name("سرپرست دوم")["id"]
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:add")])
    say(OWNER_TELEGRAM_ID, "استاد آزمایشی")
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:addsup:{supervisor_id}")])
    assert "شماره تماس" in to(live, OWNER_TELEGRAM_ID)[-1]
    assert "نام کاربری" not in to(live, OWNER_TELEGRAM_ID)[-1]
    say(OWNER_TELEGRAM_ID, "09120000124")
    assignment = db.get_teacher_assignment("استاد آزمایشی")
    assert assignment["supervisor"] == "فرستنده" and assignment["phone"] == "9120000124"
    assert db.find_staff_by_phone("9120000124")["role"] == TEACHER

    assignment_id = db.list_teacher_assignments()[0]["id"]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    assert "سرپرست: فرستنده" in to(live, OWNER_TELEGRAM_ID)[-1]
    assert "شماره تماس: 9120000124" in to(live, OWNER_TELEGRAM_ID)[-1]
    assert "شناسه تلگرام" not in to(live, OWNER_TELEGRAM_ID)[-1]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:edit:{assignment_id}")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:teacher")])
    say(OWNER_TELEGRAM_ID, "استاد ویرایش‌شده")
    assignment = db.get_teacher_assignment("استاد ویرایش‌شده")
    assert assignment and assignment["teacher"] == "استاد ویرایش‌شده"
    assert assignment["phone"] == "9120000124"

    assignment_id = db.list_teacher_assignments()[0]["id"]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:edit:{assignment_id}")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:supervisor")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:editsup:{assignment_id}:{second_supervisor_id}")])
    assert db.get_teacher_assignment("استاد ویرایش‌شده")["supervisor"] == "سرپرست دوم"

    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:edit:{assignment_id}")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:phone")])
    say(OWNER_TELEGRAM_ID, "09120000555")
    assert db.get_teacher_assignment("استاد ویرایش‌شده")["phone"] == "9120000555"

    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:delete:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:deleteconfirm:{assignment_id}")])
    assert db.get_teacher_assignment("استاد ویرایش‌شده") is None


def test_teacher_panel_manages_phone_group_and_online_mode(live):
    register_owner()
    db.assign_teacher("استاد پنل", "فرستنده", "teacher_panel")
    assignment_id = db.list_teacher_assignments()[0]["id"]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:phone")])
    say(OWNER_TELEGRAM_ID, "09120000123")

    assert db.get_teacher_assignment("استاد پنل")["phone"] == "9120000123"
    assert db.get_staff_by_name("استاد پنل")["role"] == TEACHER
    assert db.find_staff_by_phone("9120000123")["supervisor"] == "فرستنده"
    assert "9120000123" in to(live, 2)[-1]

    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:grouplink:{assignment_id}")])
    say(OWNER_TELEGRAM_ID, "-500")
    assert db.get_teacher_group("استاد پنل")["chat_id"] == -500
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:groupunlink:{assignment_id}")])
    assert db.get_teacher_group("استاد پنل") is None

    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:remoteconfirm:{assignment_id}:0")])
    assert "استودیو از برنامه‌های این استاد حذف" in to(live, OWNER_TELEGRAM_ID)[-1]
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:remotetoggle:{assignment_id}:0")])
    assert "استاد پنل" in db.get_known("remote_teacher")


def test_non_owner_cannot_use_teacher_management_callbacks(live):
    db.assign_teacher("استاد محدود", "فرستنده", "teacher_restricted")
    assignment_id = db.list_teacher_assignments()[0]["id"]
    app.bot.process_new_updates([callback(1, f"teacherm:deleteconfirm:{assignment_id}")])
    assert db.get_teacher_assignment("استاد محدود")


def test_class_lookup_shows_admin_and_phone(live):
    say(1, "/class بهرام پور")
    assert "طوسی1" in to(live, 1)[0] and "صابری" in to(live, 1)[0]


def test_problem_button_chain_posts_to_group_and_dms_helpers(live):
    say(3, BTN_PROBLEM)                                   # وحید ساعت ۱۷: طوسی2 -> مستقیم به نوع مشکل
    assert "مشکل از چه نوعیه" in to(live, 3)[0]
    app.bot.process_new_updates([callback(3, "op:pk:sound")])
    assert "کمک حضوری" in to(live, 3)[1]
    app.bot.process_new_updates([callback(3, "op:nh:1")])
    group = to(live, -100)
    assert len(group) == 1
    assert '<a href="tg://user?id=3">وحید</a> در طوسی2 مشکل' in group[0]
    assert "ناظر شیفت:" in group[0] and "کمکی شیفت:" in group[0]
    assert '<a href="tg://user?id=6">اشتری</a>' in group[0]
    response = to(live, 3)[-1]
    assert '<a href="tg://user?id=5">مهدیان</a> (09120000005)' in response
    assert '<a href="tg://user?id=6">اشتری</a> (09120000006)' in response
    assert '<a href="tg://user?id=5">مهدیان</a>' in to(live, 6)[0]
    assert to(live, 5) and to(live, 6)                     # چون کمک خواسته، ناظر/کمکی مستقیم هم خبر شدند


def test_problem_number_only_does_not_ask_for_studio_again(live):
    say(3, BTN_PROBLEM)
    app.bot.process_new_updates([callback(3, "op:pk:sound")])
    app.bot.process_new_updates([callback(3, "op:nh:0")])

    replies = to(live, 3)
    assert '<a href="tg://user?id=6">اشتری</a> (09120000006)' in replies[-1]
    assert not any("کدوم استودیویی؟" in text for text in replies)
    assert h_ops.S.get(3) is None


def test_helper_button_confirms_identity_then_notifies(live):
    say(4, "/helper")
    assert '<a href="tg://user?id=6">اشتری</a> (09120000006)' in to(live, 4)[0]
    app.bot.process_new_updates([callback(4, "op:hy")])    # مهران ۱۷:۰۰: منفی1
    assert "کدوم استودیویی؟" in to(live, 4)[1]
    app.bot.process_new_updates([callback(4, "op:st:0")])
    assert "منفی1" in to(live, 6)[0] and "09120000004" in to(live, 6)[0]   # اشتری شماره‌ی مهران را گرفت
    assert '<a href="tg://user?id=4">مهران</a>' in to(live, -100)[-1]
    assert "مستقیم در بات" in to(live, 4)[-1]


def test_supervisor_links_teacher_group_and_notifies(live, monkeypatch):
    import h_teachers
    monkeypatch.setattr(h_teachers.time, "sleep", lambda s: None)
    monkeypatch.setattr(h_teachers, "today_tomorrow", lambda: ("یکشنبه", "دوشنبه", 17 * 60))
    monkeypatch.setattr(h_teachers, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    db.assign_teacher("فکری", "فرستنده", "fikri_teacher")
    db.assign_teacher("مرادی", "فرستنده", "moradi_teacher")
    say(2, "/linkteacher فکری", chat_id=-500, chat_type="supergroup")
    assert "وصل شد" in to(live, -500)[0]
    say(2, "/linkteacher مرادی", chat_id=-501, chat_type="supergroup")
    say(2, "/notify_teachers یکشنبه")
    preview = to(live, 2)[-1]
    assert "به 2 گروه" in preview and "بدون تخصیص" not in preview
    assert "گروه" not in preview or "حسینی" not in preview
    app.bot.process_new_updates([callback(2, "nt:go")])
    teacher_msg = to(live, -500)[-1]
    assert "استاد فکری عزیز، @fikri_teacher" in teacher_msg
    assert "بزرگ" in teacher_msg and "یکشنبه 26 مهر" in teacher_msg
    assert "ارسال شد" in to(live, 2)[-1]


def test_supervisor_registers_teacher_id_and_future_notification_mentions_id(live, monkeypatch):
    import h_teachers
    monkeypatch.setattr(h_teachers.time, "sleep", lambda s: None)
    monkeypatch.setattr(h_teachers, "today_tomorrow", lambda: ("یکشنبه", "دوشنبه", 17 * 60))
    monkeypatch.setattr(h_teachers, "now_tehran", lambda: datetime(2026, 10, 18, 17))
    db.assign_teacher("فکری", "فرستنده", "fikri_teacher")
    say(2, "/linkteacher فکری", chat_id=-500, chat_type="supergroup")

    say(2, "/registerteacher", chat_id=-500, chat_type="supergroup", reply_to_uid=404)
    assignment = db.get_teacher_assignment("فکری")
    assert assignment["telegram_id"] == 404
    assert "ثبت شد" in to(live, -500)[-1]
    assert "404" not in to(live, -500)[-1]

    say(2, "/notify_teachers یکشنبه")
    app.bot.process_new_updates([callback(2, "nt:go")])
    teacher_message = to(live, -500)[-1]
    assert '<a href="tg://user?id=404">فکری عزیز</a>' in teacher_message


def test_supervisor_can_only_register_teacher_in_own_group(live):
    db.upsert_staff("سرپرست۲", "9120000099", "supervisor", None)
    db.bind_telegram(db.find_staff_by_phone("9120000099")["id"], 9)
    db.assign_teacher("فکری", "سرپرست۲", "fikri_teacher")
    say(9, "/linkteacher فکری", chat_id=-600, chat_type="supergroup")

    say(2, "/registerteacher", chat_id=-600, chat_type="supergroup", reply_to_uid=404)
    assert "فقط سرپرست همان استاد" in to(live, -600)[-1]
    assert db.get_teacher_assignment("فکری")["telegram_id"] is None


def test_supervisor_cannot_notify_other_supervisors_teachers(live, monkeypatch):
    import h_teachers
    db_ = __import__("db")
    db_.upsert_staff("سرپرست۲", "9120000099", "supervisor", None)
    db_.bind_telegram(db_.find_staff_by_phone("9120000099")["id"], 9)
    db_.assign_teacher("فکری", "سرپرست۲", "fikri_teacher")
    say(9, "/linkteacher فکری", chat_id=-600, chat_type="supergroup")
    assert "وصل شد" in to(live, -600)[-1]
    msgs, _ = h_teachers.build_batches(db_.get_staff_by_tid(2), "یکشنبه")
    assert all(t != "فکری" for _, t, _ in msgs)


def test_only_assigned_supervisor_or_bot_manager_can_link_teacher_group(live):
    db.assign_teacher("فکری", "فرستنده", "fikri_teacher")
    say(1, "/linkteacher فکری", chat_id=-700, chat_type="supergroup")
    assert "این دستور برای نقش تو نیست" in to(live, -700)[-1]

    db.upsert_staff("آخوندی", "9129271665", BOT_MANAGER)
    db.bind_telegram(db.find_staff_by_phone("9129271665")["id"], 8)
    db.assign_teacher("بهرام پور", "فرستنده", "")
    assignment = db.get_teacher_assignment("بهرام پور")
    assert assignment["supervisor"] == "فرستنده"
    say(8, "/linkteacher بهرام پور", chat_id=-701, chat_type="supergroup")
    assert "وصل شد" in to(live, -701)[-1]
    say(8, "/registerteacher", chat_id=-701, chat_type="supergroup", reply_to_uid=405)
    assert db.get_teacher_assignment("بهرام پور")["telegram_id"] == 405
