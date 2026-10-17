import pathlib
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app
import config
import db
import h_ops
import h_schedule
from analysis import stats

SAMPLE = (pathlib.Path(__file__).parent / "sample_sunday.txt").read_text(encoding="utf-8")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.set_setting("schedule_preview_enabled", "0")
    people = [("مدیر", "9120000001", "shift_manager", 1), ("فرستنده", "9120000002", "supervisor", 2),
              ("وحید", "9120000003", "admin", 3), ("مهران", "9120000004", "admin", 4),
              ("مهدیان", "9120000005", "supervisor", 5), ("اشتری", "9120000006", "admin", 6),
              ("احسانی", "9120000007", "admin", None)]
    for name, ph, role, tid in people:
        db.upsert_staff(name, ph, role, None)
        if tid:
            db.bind_telegram(db.find_staff_by_phone(ph)["id"], tid)
    sent = []
    monkeypatch.setattr(app.bot, "send_message", lambda chat_id, text, **kw: sent.append((chat_id, text)))
    monkeypatch.setattr(h_schedule.time, "sleep", lambda s: None)
    return sent


def msg(text, uid=2, mid=10):
    return SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup"), message_id=mid, text=text,
                           date=datetime(2026, 10, 11, 10, tzinfo=timezone.utc).timestamp(),
                           from_user=SimpleNamespace(id=uid))


def to(sent, chat):
    return [t for c, t in sent if c == chat]


def test_new_schedule_notifies_each_person_and_sender_summary(env):
    h_schedule.ingest(msg(SAMPLE))
    assert "برنامه‌ی جدید" in to(env, 3)[0] and "طوسی2" in to(env, 3)[0]     # وحید
    assert "یکشنبه 19 مهر" in to(env, 3)[0]
    assert {e["shift_date"] for e in db.active_entries("یکشنبه")} == {"2026-10-11"}
    assert "منفی1" in to(env, 4)[0]                                           # مهران
    assert "ناظر" in to(env, 5)[0] and "کمکی" in to(env, 6)[0]               # مهدیان، اشتری
    summary = to(env, 2)[0]
    assert "ثبت شد" in summary and "احسانی" in summary and "⏳" in summary    # احسانی هنوز وارد نشده
    assert to(env, 1) == []                                                   # بدون تداخل: مدیر مزاحم نمی‌شود


def test_unauthorized_sender_and_non_schedule_ignored(env):
    h_schedule.ingest(msg(SAMPLE, uid=99))
    h_schedule.ingest(msg("سلام، امروز کی میاد؟"))
    assert env == [] and db.active_entries() == []


def test_edit_notifies_only_affected_and_reports_conflict(env):
    h_schedule.ingest(msg(SAMPLE))
    original_dates = {e["shift_date"] for e in db.active_entries("یکشنبه")}
    env.clear()
    edited = SAMPLE.replace("رضایی مهر دهم تاریخ 15:30تا17 طوسی2 وحید", "رضایی مهر دهم تاریخ 15:30تا17:30 طوسی2 وحید") \
                   .replace("آریان ریاضی تجربی و حسابان یازدهم 15:30تا18:30 منفی1 مهران",
                            "آریان ریاضی تجربی و حسابان یازدهم 15:30تا18:30 منفی1 احسانی")
    edited_message = msg(edited)
    edited_message.date = datetime(2026, 10, 20, 10, tzinfo=timezone.utc).timestamp()
    h_schedule.ingest(edited_message)
    assert {e["shift_date"] for e in db.active_entries("یکشنبه")} == original_dates
    assert "تغییر" in to(env, 3)[0]                       # وحید: ساعتش عوض شد
    assert "حذف شد" in to(env, 4)[0]                      # مهران: کلاسش رفت
    assert to(env, 5) and "تداخل" in to(env, 5)[0]        # سرپرست از تداخل باخبر می‌شود
    assert to(env, 6) == []                               # افراد نامرتبط پیامی نمی‌گیرند
    assert any("تداخل" in t for t in to(env, 1))          # ۱۷:۰۰–۱۷:۳۰ در طوسی2 تداخل دارد -> مدیر باخبر
    assert len(db.active_entries("یکشنبه")) == 47


def test_schedule_date_uses_nearest_occurrence_not_next_saturday(env):
    from analysis import next_weekday_on_or_after

    assert next_weekday_on_or_after("2026-10-12", "جمعه") == "2026-10-16"
    friday_schedule = SAMPLE.replace("یکشنبه", "جمعه")
    scheduled = msg(friday_schedule)
    scheduled.date = datetime(2026, 10, 12, 10, tzinfo=timezone.utc).timestamp()
    h_schedule.ingest(scheduled)

    entries = db.active_entries("جمعه")
    assert entries and {entry["shift_date"] for entry in entries} == {"2026-10-16"}
    assert all(entry["shift_date"] != "2026-10-23" for entry in entries)


def test_schedule_is_saved_and_sent_without_manager_approval(env):
    db.set_setting("schedule_preview_enabled", "1")

    h_schedule.ingest(msg(SAMPLE))

    assert len(db.active_entries("یکشنبه")) == 47
    assert "برنامه‌ی جدید" in to(env, 3)[0]
    assert "ثبت شد" in to(env, 2)[0]


def test_friday_helper_with_admin_prefix_is_saved_as_helper(env):
    schedule = SAMPLE + (
        "\nجمعه\n"
        "ادمین کمکی استودیو های جمشید و طوسی 11تا21 : مهران"
    )
    h_schedule.ingest(msg(schedule))

    helpers = [entry for entry in db.active_entries("جمعه") if entry["kind"] == "komaki"]
    assert len(helpers) == 1
    assert helpers[0]["person"] == "مهران"
    assert helpers[0]["shift_date"] == "2026-10-16"


def test_legacy_schedule_database_migrates_dates_without_losing_entries(tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy.db"
    monkeypatch.setattr(config, "DB_PATH", str(legacy_db))
    conn = sqlite3.connect(legacy_db)
    try:
        conn.executescript("""
            CREATE TABLE sheets (
                id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                day TEXT NOT NULL, week_start TEXT, sender_id INTEGER, active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE sheet_versions (
                id INTEGER PRIMARY KEY, sheet_id INTEGER NOT NULL, version INTEGER NOT NULL,
                raw_text TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL, day TEXT NOT NULL,
                kind TEXT NOT NULL, start_min INTEGER NOT NULL, end_min INTEGER NOT NULL,
                teacher TEXT, course TEXT, studio TEXT, person TEXT
            );
            INSERT INTO sheets(id, chat_id, message_id, day, week_start, sender_id, created_at)
            VALUES(1, -100, 10, 'یکشنبه', NULL, 2, '2026-10-11 10:00:00');
            INSERT INTO sheet_versions(id, sheet_id, version, raw_text, created_at)
            VALUES(1, 1, 1, 'legacy schedule', '2026-10-11 10:00:00');
            INSERT INTO entries(id, version_id, day, kind, start_min, end_min, teacher, course, studio, person)
            VALUES(1, 1, 'یکشنبه', 'class', 900, 960, 'استاد نمونه', 'ریاضی', 'بزرگ', 'وحید');
        """)
    finally:
        conn.close()

    db.init_db()

    assert db.sheet_schedule_date(-100, 10, "یکشنبه") == "2026-10-18"
    assert db.message_schedule_anchor(-100, 10) == "2026-10-11"
    migrated = db.active_entries("یکشنبه")
    assert len(migrated) == 1 and migrated[0]["shift_date"] == "2026-10-18"


def test_editing_schedule_preserves_dates_and_removing_a_day_removes_old_entries(env):
    source_lines = [line for line in SAMPLE.splitlines() if line.strip() and line.strip() != "یکشنبه"]
    two_days = SAMPLE + "\nجمعه\n" + "\n".join(source_lines)
    initial = msg(two_days, mid=44)
    h_schedule.ingest(initial)
    assert {entry["shift_date"] for entry in db.active_entries("جمعه")} == {"2026-10-16"}

    edited = msg(SAMPLE, mid=44)
    edited.date = datetime(2026, 10, 25, 10, tzinfo=timezone.utc).timestamp()
    h_schedule.ingest(edited)
    assert db.active_entries("جمعه") == []
    assert db.active_entries("یکشنبه")
    assert any("حذف شد" in text for _, text in env)


def test_second_post_same_day_replaces_old_week(env):
    h_schedule.ingest(msg(SAMPLE, mid=10))
    h_schedule.ingest(msg(SAMPLE, mid=11))
    assert len(db.active_entries("یکشنبه")) == 47        # نسخه‌ی هفته‌ی قبل غیرفعال شد
    assert len(db.historical_entries(kind="class")) == 84  # دو برنامه‌ی جداگانه‌ی هفتگی


def test_entries_on_date_reads_replaced_schedule_without_leaking_next_week(env):
    h_schedule.ingest(msg(SAMPLE, mid=10))
    next_week = msg(SAMPLE, mid=11)
    next_week.date = datetime(2026, 10, 18, 10, tzinfo=timezone.utc).timestamp()
    h_schedule.ingest(next_week)

    assert db.active_entries("یکشنبه", shift_date="2026-10-11") == []
    assert len(db.entries_on_date("2026-10-11", day="یکشنبه")) == 47
    assert len(db.entries_on_date("2026-10-18", day="یکشنبه")) == 47
    assert all(entry["shift_date"] == "2026-10-11"
               for entry in db.entries_on_date("2026-10-11", day="یکشنبه"))


def test_roles_now_and_incident_text(env):
    h_schedule.ingest(msg(SAMPLE))
    nazers, active = h_ops.roles_now("nazer", None, "یکشنبه", 19 * 60)
    assert active and {e["person"] for e in nazers} == {"مهدیان", "عرفان"}      # سهیل/سامی تا ۱۸ بودند
    helpers, _ = h_ops.roles_now("komaki", None, "یکشنبه", 16 * 60)
    assert [e["person"] for e in helpers] == ["اشتری"]
    mine = [e for e in db.active_entries("یکشنبه") if e["person"] == "وحید" and e["kind"] == "class"]
    opt = h_ops._opts_from(mine)[0]
    text = h_ops.compose_incident(db.get_staff_by_name("وحید"), opt, "🔊 صدا", True, nazers, True, helpers, True)
    assert "🚨" in text and "09120000003" in text and "اشتری (09120000006)" in text and "نیاز به کمک" in text


def test_help_fallback_group_message_only_names_requester(env, monkeypatch):
    h_schedule.ingest(msg(SAMPLE))
    monkeypatch.setattr(h_ops, "today_tomorrow", lambda: ("یکشنبه", "دوشنبه", 17 * 60))
    monkeypatch.setattr(h_ops, "now_tehran", lambda: datetime(2026, 10, 11, 17))
    group_messages = []
    monkeypatch.setattr(h_ops, "dm_staff", lambda *args, **kwargs: False)
    monkeypatch.setattr(h_ops, "post_group", lambda text: group_messages.append(text) or True)
    mine = [e for e in db.active_entries("یکشنبه") if e["person"] == "وحید" and e["kind"] == "class"]
    h_ops.S[3] = {"opt": h_ops._opts_from(mine)[0]}
    h_ops.finish_help(2, 3, db.get_staff_by_tid(3))
    assert group_messages == ["وحید کمک می‌خواد."]
    assert "09120000003" not in group_messages[0]
    assert "اشتری" not in group_messages[0]


def test_stats_on_sample(env):
    h_schedule.ingest(msg(SAMPLE))
    studios, admins = stats(db.active_entries())
    assert dict((n, m) for n, m, _ in studios)["بزرگ"] == 615
    assert {a["person"]: a["class"] for a in admins}["مهران"] == 7 * 60
    assert "آنلاین" in dict((n, m) for n, m, _ in studios)


def test_staff_csv_seed(env, tmp_path):
    from scripts.seed_staff import seed
    p = tmp_path / "staff.csv"
    p.write_text("name,phone,role,supervisor\nآخوندی,09129271665,shift_manager,\n\nبد,123,admin,\n", encoding="utf-8-sig")
    assert seed(str(p)) == 1
    assert db.find_staff_by_phone("9129271665")["role"] == "shift_manager"


def test_renaming_supervisor_preserves_team_and_prevents_orphaning(env):
    db.upsert_staff("سرپرست نمونه", "9120000088", "supervisor")
    db.upsert_staff("ادمین نمونه", "9120000089", "admin", "سرپرست نمونه")
    db.assign_teacher("استاد نمونه", "سرپرست نمونه", "sample_teacher")
    db.link_teacher("استاد نمونه", -123, "سرپرست نمونه")

    supervisor = db.get_staff_by_name("سرپرست نمونه")
    assert db.update_staff(supervisor["id"], "سرپرست تازه", supervisor["phone"],
                           "supervisor", None)
    assert db.get_staff_by_name("ادمین نمونه")["supervisor"] == "سرپرست تازه"
    assert db.get_teacher_assignment("استاد نمونه")["supervisor"] == "سرپرست تازه"
    assert next(group for group in db.list_teacher_groups()
                if group["teacher"] == "استاد نمونه")["supervisor"] == "سرپرست تازه"

    with pytest.raises(ValueError, match="ابتدا اعضای تیم"):
        db.delete_staff(supervisor["id"])


def test_legacy_observer_role_is_migrated_to_supervisor(env, tmp_path):
    from scripts.seed_staff import seed
    p = tmp_path / "legacy.csv"
    p.write_text("name,phone,role,supervisor\nناظر قدیمی,09120000999,observer,سرپرست\n", encoding="utf-8")
    assert seed(str(p)) == 1
    assert db.find_staff_by_phone("9120000999")["role"] == "supervisor"
    with db.connect() as conn:
        conn.execute("UPDATE staff SET role='observer' WHERE phone='9120000999'")
    db.init_db()
    assert db.find_staff_by_phone("9120000999")["role"] == "supervisor"
