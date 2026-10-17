"""شبیه‌سازی آپدیت‌های واقعی تلگرام از مسیر دیسپچر خودِ telebot (بدون شبکه)."""
import itertools
from datetime import datetime, timezone

import pytest
from telebot import types

import app
import bot as _bot  # noqa: F401  (ثبت همه‌ی هندلرها به همان ترتیب اجرای واقعی)
import db
import h_ops
import h_manage
import h_staff
from app import (BTN_FREE, BTN_HELPER, BTN_MY, BTN_PROBLEM, BTN_SCHEDULE_DATES, BTN_STATS,
                 BTN_STAFF_MANAGEMENT, BTN_TEACHER_MANAGEMENT, BTN_TEAM,
                 BOT_MANAGER, OWNER_TELEGRAM_ID, menu_for)
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


def test_owner_can_add_multiword_teacher_and_remote_teacher_from_management_panel(live):
    import h_admin
    import h_schedule

    register_owner()
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:names")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:nameadd:teacher")])
    say(OWNER_TELEGRAM_ID, "بام")
    names = h_admin._known_names("teacher")
    index = names.index("بام")
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{index}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:namerename:teacher:{index}")])
    say(OWNER_TELEGRAM_ID, "بام رفیع")
    assert "بام رفیع" in db.get_known("teacher") and "بام" not in db.get_known("teacher")

    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:names")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:nameadd:remote_teacher")])
    say(OWNER_TELEGRAM_ID, "استاد آنلاین")
    assert "استاد آنلاین" in db.get_known("teacher")
    assert "استاد آنلاین" in db.get_known("remote_teacher")
    names = h_admin._known_names("remote_teacher")
    index = names.index("استاد آنلاین")
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:name:remote_teacher:{index}")])
    app.bot.process_new_updates([
        callback(OWNER_TELEGRAM_ID, f"teacherm:namerename:remote_teacher:{index}")])
    say(OWNER_TELEGRAM_ID, "آنلاین نمونه")
    assert "استاد آنلاین" not in h_schedule.known("teacher", h_schedule.TEACHERS).values()
    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع"
    assert h_schedule.known("remote_teacher", h_schedule.REMOTE)["آنلایننمونه"] == "آنلاین نمونه"


def test_owner_can_add_arbitrary_alias_for_teacher_from_panel(live):
    import h_admin
    import h_schedule

    register_owner()
    db.add_known("teacher", ["بام رفیع"])
    say(OWNER_TELEGRAM_ID, BTN_TEACHER_MANAGEMENT)
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:names")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, "teacherm:names:teacher")])
    index = h_admin._known_names("teacher").index("بام رفیع")
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{index}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:aliasadd:teacher:{index}")])
    say(OWNER_TELEGRAM_ID, "بام‌رفیع")

    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع"
    index = h_admin._known_names("teacher").index("بام رفیع")
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:name:teacher:{index}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:namerename:teacher:{index}")])
    say(OWNER_TELEGRAM_ID, "بام رفیع اصلی")
    assert h_schedule.known("teacher", h_schedule.TEACHERS)["بامرفیع"] == "بام رفیع اصلی"


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
    say(OWNER_TELEGRAM_ID, "@ostad_test")
    assignment = db.get_teacher_assignment("استاد آزمایشی")
    assert assignment["supervisor"] == "فرستنده" and assignment["username"] == "ostad_test"

    assignment_id = db.list_teacher_assignments()[0]["id"]
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:edit:{assignment_id}")])
    app.bot.process_new_updates(
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:teacher")])
    say(OWNER_TELEGRAM_ID, "استاد ویرایش‌شده")
    assignment = db.get_teacher_assignment("استاد ویرایش‌شده")
    assert assignment and assignment["teacher"] == "استاد ویرایش‌شده"
    assert assignment["username"] == "ostad_test"

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
        [callback(OWNER_TELEGRAM_ID, f"teacherm:field:{assignment_id}:username")])
    say(OWNER_TELEGRAM_ID, "@ostad_new")
    assert db.get_teacher_assignment("استاد ویرایش‌شده")["username"] == "ostad_new"

    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:select:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:delete:{assignment_id}")])
    app.bot.process_new_updates([callback(OWNER_TELEGRAM_ID, f"teacherm:deleteconfirm:{assignment_id}")])
    assert db.get_teacher_assignment("استاد ویرایش‌شده") is None


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
    assert group == ["وحید کمک می‌خواد."]
    response = to(live, 3)[-1]
    assert "مهدیان (09120000005)" in response and "اشتری (09120000006)" in response
    assert to(live, 5) and to(live, 6)                     # چون کمک خواسته، ناظر/کمکی مستقیم هم خبر شدند


def test_problem_number_only_does_not_ask_for_studio_again(live):
    say(3, BTN_PROBLEM)
    app.bot.process_new_updates([callback(3, "op:pk:sound")])
    app.bot.process_new_updates([callback(3, "op:nh:0")])

    replies = to(live, 3)
    assert "اشتری (09120000006)" in replies[-1]
    assert not any("کدوم استودیویی؟" in text for text in replies)
    assert h_ops.S.get(3) is None


def test_helper_button_confirms_identity_then_notifies(live):
    say(4, "/helper")
    assert "اشتری (09120000006)" in to(live, 4)[0]
    app.bot.process_new_updates([callback(4, "op:hy")])    # مهران ۱۷:۰۰: منفی1
    assert "تو استودیو «منفی1» هستی؟" in to(live, 4)[1]
    app.bot.process_new_updates([callback(4, "op:cf:1")])
    assert "منفی1" in to(live, 6)[0] and "09120000004" in to(live, 6)[0]   # اشتری شماره‌ی مهران را گرفت
    assert "اشتری" in to(live, 4)[2]


def test_supervisor_links_teacher_group_and_notifies(live, monkeypatch):
    import h_teachers
    monkeypatch.setattr(h_teachers.time, "sleep", lambda s: None)
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
    say(8, "/assignteacher بهرام پور | فرستنده | @bahram_teacher")
    assignment = db.get_teacher_assignment("بهرام پور")
    assert assignment["supervisor"] == "فرستنده" and assignment["username"] == "bahram_teacher"
    say(8, "/linkteacher بهرام پور", chat_id=-701, chat_type="supergroup")
    assert "وصل شد" in to(live, -701)[-1]
    say(8, "/registerteacher", chat_id=-701, chat_type="supergroup", reply_to_uid=405)
    assert db.get_teacher_assignment("بهرام پور")["telegram_id"] == 405
