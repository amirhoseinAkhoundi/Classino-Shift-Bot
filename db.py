import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config
from schedule_parser import key, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,        -- دقیقاً همان‌طور که در برنامه نوشته می‌شود
    phone TEXT UNIQUE,                -- ۱۰ رقم آخر، مثل 9121234567
    role TEXT NOT NULL,               -- shift_manager | supervisor | admin | bot_manager
    supervisor TEXT,                  -- نام سرپرست این عضو تیم
    telegram_id INTEGER UNIQUE,       -- بعد از تایید هویت پر می‌شود
    verified_at TEXT
);
CREATE TABLE IF NOT EXISTS sheets (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    week_start TEXT,
    scheduled_date TEXT,
    source_date TEXT,
    sender_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id, message_id, day)
);
CREATE TABLE IF NOT EXISTS sheet_versions (
    id INTEGER PRIMARY KEY,
    sheet_id INTEGER NOT NULL REFERENCES sheets(id),
    version INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sheet_id, version)
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES sheet_versions(id),
    day TEXT NOT NULL,
    shift_date TEXT,
    kind TEXT NOT NULL,
    start_min INTEGER NOT NULL,
    end_min INTEGER NOT NULL,
    teacher TEXT, course TEXT, studio TEXT, person TEXT
);
CREATE TABLE IF NOT EXISTS known_names (
    kind TEXT NOT NULL,               -- teacher | studio | remote_teacher
    name TEXT NOT NULL,
    PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS known_name_exclusions (
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS known_name_aliases (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    alias TEXT NOT NULL,
    canonical TEXT NOT NULL,
    UNIQUE(kind, alias)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teacher_groups (
    teacher_key TEXT PRIMARY KEY,
    teacher TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    supervisor TEXT,                  -- نام سرپرست مسئول این استاد
    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS teacher_assignments (
    teacher_key TEXT PRIMARY KEY,
    teacher TEXT NOT NULL,
    supervisor TEXT NOT NULL,
    username TEXT NOT NULL,
    telegram_id INTEGER,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    staff_name TEXT, studio TEXT, class_desc TEXT, kind TEXT, need_help INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_entries_person ON entries(person);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(staff)")}
        if "supervisor" not in cols:  # مهاجرت دیتابیس فاز ۱
            c.execute("ALTER TABLE staff ADD COLUMN supervisor TEXT")
        sheet_cols = {r["name"] for r in c.execute("PRAGMA table_info(sheets)")}
        if "week_start" not in sheet_cols:
            c.execute("ALTER TABLE sheets ADD COLUMN week_start TEXT")
        if "scheduled_date" not in sheet_cols:
            c.execute("ALTER TABLE sheets ADD COLUMN scheduled_date TEXT")
        if "source_date" not in sheet_cols:
            c.execute("ALTER TABLE sheets ADD COLUMN source_date TEXT")
        entry_cols = {r["name"] for r in c.execute("PRAGMA table_info(entries)")}
        if "shift_date" not in entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN shift_date TEXT")
        assignment_cols = {r["name"] for r in c.execute("PRAGMA table_info(teacher_assignments)")}
        if "telegram_id" not in assignment_cols:
            c.execute("ALTER TABLE teacher_assignments ADD COLUMN telegram_id INTEGER")
        c.execute("UPDATE staff SET role='supervisor' WHERE role='observer'")
        c.execute("UPDATE staff SET role='bot_manager' WHERE role='database_admin'")
        c.execute(
            "INSERT OR IGNORE INTO teacher_assignments(teacher_key, teacher, supervisor, username) "
            "SELECT teacher_key, teacher, supervisor, '' FROM teacher_groups WHERE supervisor IS NOT NULL"
        )

        from analysis import next_saturday, schedule_date, tehran_timezone
        missing_dates = c.execute(
            "SELECT id, created_at FROM sheets WHERE week_start IS NULL AND scheduled_date IS NULL").fetchall()
        for sheet in missing_dates:
            created = datetime.fromisoformat(sheet["created_at"])
            created = (created.replace(tzinfo=timezone.utc) if created.tzinfo is None
                       else created.astimezone(timezone.utc))
            week_start = next_saturday(created)
            c.execute("UPDATE sheets SET week_start=? WHERE id=?", (week_start, sheet["id"]))

        sheets_without_dates = c.execute(
            "SELECT id, chat_id, message_id, day, week_start, created_at FROM sheets "
            "WHERE scheduled_date IS NULL OR source_date IS NULL").fetchall()
        for sheet in sheets_without_dates:
            assigned = c.execute(
                "SELECT shift_date FROM entries e JOIN sheet_versions v ON v.id=e.version_id "
                "WHERE v.sheet_id=? AND e.shift_date IS NOT NULL "
                "ORDER BY v.version DESC, e.id LIMIT 1",
                (sheet["id"],),
            ).fetchone()
            created = datetime.fromisoformat(sheet["created_at"])
            created = (created.replace(tzinfo=timezone.utc) if created.tzinfo is None
                       else created.astimezone(timezone.utc))
            source_date = created.astimezone(tehran_timezone()).date().isoformat()
            scheduled_date = (assigned["shift_date"] if assigned else
                              schedule_date(sheet["week_start"], sheet["day"]))
            c.execute(
                "UPDATE sheets SET scheduled_date=COALESCE(scheduled_date, ?), "
                "source_date=COALESCE(source_date, ?) WHERE id=?",
                (scheduled_date, source_date, sheet["id"]),
            )

        missing_entries = c.execute(
            "SELECT e.id, e.day, s.week_start FROM entries e "
            "JOIN sheet_versions v ON v.id=e.version_id "
            "JOIN sheets s ON s.id=v.sheet_id WHERE e.shift_date IS NULL AND s.week_start IS NOT NULL"
        ).fetchall()
        c.executemany(
            "UPDATE entries SET shift_date=? WHERE id=?",
            [(schedule_date(row["week_start"], row["day"]), row["id"]) for row in missing_entries],
        )


# ---------- staff / auth ----------
def upsert_staff(name, phone, role, supervisor=None):
    if role == "observer":
        role = "supervisor"
    elif role == "database_admin":
        role = "bot_manager"
    with connect() as c:
        c.execute(
            "INSERT INTO staff(name, phone, role, supervisor) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET phone=excluded.phone, role=excluded.role, supervisor=excluded.supervisor",
            (name, phone, role, supervisor or None),
        )


def update_staff(staff_id, name, phone, role, supervisor=None):
    if role == "observer":
        role = "supervisor"
    elif role == "database_admin":
        role = "bot_manager"
    with connect() as c:
        current = c.execute("SELECT name, role FROM staff WHERE id=?", (staff_id,)).fetchone()
        if not current:
            return False
        has_team = c.execute(
            "SELECT EXISTS(SELECT 1 FROM staff WHERE supervisor=?) "
            "OR EXISTS(SELECT 1 FROM teacher_assignments WHERE supervisor=?) "
            "OR EXISTS(SELECT 1 FROM teacher_groups WHERE supervisor=?)",
            (current["name"], current["name"], current["name"]),
        ).fetchone()[0]
        if current["role"] == "supervisor" and role != "supervisor" and has_team:
            raise ValueError("ابتدا اعضای تیم و استادهای تخصیص‌یافته را به سرپرست دیگری منتقل کن.")
        if current["role"] == "bot_manager" and role != "bot_manager":
            managers = c.execute("SELECT COUNT(*) FROM staff WHERE role='bot_manager'").fetchone()[0]
            if managers <= 1:
                raise ValueError("آخرین مدیر ربات را نمی‌توان تغییر سمت داد.")
        result = c.execute(
            "UPDATE staff SET name=?, phone=?, role=?, supervisor=? WHERE id=?",
            (name, phone, role, supervisor or None, staff_id),
        )
        if current["role"] == "supervisor" and role == "supervisor" and current["name"] != name:
            c.execute("UPDATE staff SET supervisor=? WHERE supervisor=?", (name, current["name"]))
            c.execute("UPDATE teacher_assignments SET supervisor=? WHERE supervisor=?",
                      (name, current["name"]))
            c.execute("UPDATE teacher_groups SET supervisor=? WHERE supervisor=?",
                      (name, current["name"]))
        return result.rowcount > 0


def delete_staff(staff_id):
    with connect() as c:
        current = c.execute("SELECT name, role FROM staff WHERE id=?", (staff_id,)).fetchone()
        if not current:
            return False
        if current["role"] == "supervisor":
            has_team = c.execute(
                "SELECT EXISTS(SELECT 1 FROM staff WHERE supervisor=?) "
                "OR EXISTS(SELECT 1 FROM teacher_assignments WHERE supervisor=?) "
                "OR EXISTS(SELECT 1 FROM teacher_groups WHERE supervisor=?)",
                (current["name"], current["name"], current["name"]),
            ).fetchone()[0]
            if has_team:
                raise ValueError("ابتدا اعضای تیم و استادهای تخصیص‌یافته را به سرپرست دیگری منتقل کن.")
        result = c.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        return result.rowcount > 0


def find_staff_by_phone(phone):
    with connect() as c:
        return c.execute("SELECT * FROM staff WHERE phone=?", (phone,)).fetchone()


def get_staff_by_tid(tid):
    with connect() as c:
        return c.execute("SELECT * FROM staff WHERE telegram_id=?", (tid,)).fetchone()


def list_staff(roles=None):
    with connect() as c:
        rows = c.execute("SELECT * FROM staff ORDER BY name").fetchall()
    return [r for r in rows if not roles or r["role"] in roles]


def get_staff_by_name(name):
    k = key(normalize(name or ""))
    for r in list_staff():
        if key(r["name"]) == k:
            return r
    return None


def bind_telegram(staff_id, tid):
    with connect() as c:
        c.execute("UPDATE staff SET telegram_id=?, verified_at=CURRENT_TIMESTAMP WHERE id=?", (tid, staff_id))
def ensure_owner(tid, name="مدیر ربات"):
    """مالک را بدون CSV و بدون احراز شماره، به‌عنوان bot_manager ثبت می‌کند."""
    with connect() as c:
        if c.execute("SELECT 1 FROM staff WHERE telegram_id=?", (tid,)).fetchone():
            return
        c.execute(
            "INSERT INTO staff(name, phone, role, telegram_id, verified_at) "
            "VALUES(?, NULL, 'bot_manager', ?, CURRENT_TIMESTAMP)", (name, tid))

# ---------- schedule ----------
def sheet_week_start(chat_id, message_id, day):
    with connect() as c:
        row = c.execute("SELECT week_start FROM sheets WHERE chat_id=? AND message_id=? AND day=?",
                        (chat_id, message_id, day)).fetchone()
    return row["week_start"] if row else None


def sheet_schedule_date(chat_id, message_id, day):
    with connect() as c:
        row = c.execute("SELECT scheduled_date FROM sheets WHERE chat_id=? AND message_id=? AND day=?",
                        (chat_id, message_id, day)).fetchone()
    return row["scheduled_date"] if row else None


def message_schedule_anchor(chat_id, message_id):
    with connect() as c:
        row = c.execute(
            "SELECT source_date FROM sheets WHERE chat_id=? AND message_id=? "
            "AND source_date IS NOT NULL ORDER BY id LIMIT 1",
            (chat_id, message_id),
        ).fetchone()
    return row["source_date"] if row else None


def sheet_days_for_message(chat_id, message_id):
    with connect() as c:
        rows = c.execute("SELECT day FROM sheets WHERE chat_id=? AND message_id=?",
                         (chat_id, message_id)).fetchall()
    return [row["day"] for row in rows]


def active_schedule_dates():
    with connect() as c:
        rows = c.execute(
            "SELECT s.day, s.scheduled_date FROM sheets s "
            "JOIN sheet_versions v ON v.sheet_id=s.id "
            "AND v.version=(SELECT MAX(latest.version) FROM sheet_versions latest WHERE latest.sheet_id=s.id) "
            "JOIN entries e ON e.version_id=v.id "
            "WHERE s.active=1 AND s.scheduled_date IS NOT NULL "
            "ORDER BY s.created_at DESC, s.id DESC"
        ).fetchall()
    dates = {}
    for row in rows:
        dates.setdefault(row["day"], row["scheduled_date"])
    return dates


def save_sheet(chat_id, message_id, sender_id, day, raw_text, entries, week_start=None,
               shift_date=None, source_date=None):
    """برای هر (پیام، روز) یک sheet؛ هر ادیت یک نسخه‌ی جدید. برمی‌گرداند (sheet_id, version)."""
    from analysis import next_weekday_on_or_after, now_tehran
    source_date = source_date or now_tehran().date().isoformat()

    with connect() as c:
        row = c.execute("SELECT id, week_start, scheduled_date, source_date FROM sheets "
                        "WHERE chat_id=? AND message_id=? AND day=?",
                        (chat_id, message_id, day)).fetchone()
        if row:
            sheet_id = row["id"]
            week_start = row["week_start"] or week_start
            shift_date = row["scheduled_date"] or shift_date
            source_missing = not row["source_date"]
            source_date = row["source_date"] or source_date
            if week_start and not row["week_start"]:
                c.execute("UPDATE sheets SET week_start=? WHERE id=?", (week_start, sheet_id))
        else:
            shift_date = shift_date or next_weekday_on_or_after(source_date, day)
            sheet_id = c.execute(
                "INSERT INTO sheets(chat_id, message_id, day, week_start, scheduled_date, source_date, sender_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (chat_id, message_id, day, week_start, shift_date, source_date, sender_id)).lastrowid
            # برنامه‌ی جدید همان روز، قبلی را غیرفعال می‌کند
            c.execute("UPDATE sheets SET active=0 WHERE day=? AND id<>?", (day, sheet_id))
        if not shift_date:
            shift_date = next_weekday_on_or_after(source_date, day)
            c.execute("UPDATE sheets SET scheduled_date=? WHERE id=?", (shift_date, sheet_id))
        if not row or source_missing:
            c.execute("UPDATE sheets SET source_date=? WHERE id=?", (source_date, sheet_id))
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM sheet_versions WHERE sheet_id=?",
                            (sheet_id,)).fetchone()[0]
        vid = c.execute("INSERT INTO sheet_versions(sheet_id, version, raw_text) VALUES(?,?,?)",
                        (sheet_id, version, raw_text)).lastrowid
        c.executemany(
            "INSERT INTO entries(version_id, day, shift_date, kind, start_min, end_min, teacher, course, studio, person)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(vid, e.day, shift_date, e.kind, e.start, e.end,
              e.teacher, e.course, e.studio, e.person)
             for e in entries if e.day == day],
        )
        return sheet_id, version


def active_sheets():
    with connect() as c:
        rows = c.execute(
            "SELECT s.id, s.day, s.scheduled_date, s.message_id, s.created_at, COUNT(e.id) AS entry_count "
            "FROM sheets s JOIN sheet_versions v ON v.sheet_id=s.id "
            "AND v.version=(SELECT MAX(latest.version) FROM sheet_versions latest WHERE latest.sheet_id=s.id) "
            "LEFT JOIN entries e ON e.version_id=v.id "
            "WHERE s.active=1 GROUP BY s.id ORDER BY s.scheduled_date, s.day, s.id",
        ).fetchall()
    return [dict(row) for row in rows if row["entry_count"]]


def entries_for_sheet(sheet_id):
    with connect() as c:
        row = c.execute("SELECT MAX(version) AS version FROM sheet_versions WHERE sheet_id=?",
                        (sheet_id,)).fetchone()
        if not row or row["version"] is None:
            return []
        return [dict(entry) for entry in c.execute(
            "SELECT e.* FROM entries e JOIN sheet_versions v ON v.id=e.version_id "
            "WHERE v.sheet_id=? AND v.version=?",
            (sheet_id, row["version"]),
        )]


def update_sheet_schedule_date(sheet_id, shift_date):
    with connect() as c:
        sheet = c.execute("SELECT day, active FROM sheets WHERE id=?", (sheet_id,)).fetchone()
        if not sheet:
            return False
        c.execute("UPDATE sheets SET scheduled_date=? WHERE id=?", (shift_date, sheet_id))
        if sheet["active"]:
            c.execute(
                "UPDATE entries SET shift_date=? WHERE version_id=("
                "SELECT id FROM sheet_versions WHERE sheet_id=? ORDER BY version DESC LIMIT 1)",
                (shift_date, sheet_id),
            )
        return True


def latest_entries_for_sheet(chat_id, message_id, day):
    """ورودی‌های آخرین نسخه‌ی این (پیام، روز)؛ None اگر قبلاً ثبت نشده."""
    with connect() as c:
        s = c.execute("SELECT id FROM sheets WHERE chat_id=? AND message_id=? AND day=?",
                      (chat_id, message_id, day)).fetchone()
        if not s:
            return None
        v = c.execute("SELECT id FROM sheet_versions WHERE sheet_id=? ORDER BY version DESC LIMIT 1",
                      (s["id"],)).fetchone()
        return [dict(r) for r in c.execute("SELECT * FROM entries WHERE version_id=?", (v["id"],))]


ACTIVE_ENTRIES = """
SELECT e.* FROM entries e
JOIN sheet_versions v ON v.id = e.version_id
JOIN sheets s ON s.id = v.sheet_id
WHERE s.active = 1
  AND v.version = (SELECT MAX(version) FROM sheet_versions WHERE sheet_id = s.id)
"""


def active_entries(day=None, shift_date=None):
    with connect() as c:
        query, params = ACTIVE_ENTRIES, []
        if day:
            query += " AND e.day = ?"
            params.append(day)
        if shift_date:
            query += " AND e.shift_date = ?"
            params.append(shift_date)
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def entries_on_date(shift_date, day=None, kind=None):
    """آخرین برنامه‌ی ثبت‌شده برای یک تاریخ، حتی اگر بعداً با هفته‌ی بعد جایگزین شده باشد."""
    query = """
    WITH latest_versions AS (
        SELECT sheet_id, MAX(version) AS version
        FROM sheet_versions
        GROUP BY sheet_id
    ),
    dated_sheet_candidates AS (
        SELECT s.id, s.created_at, e.day, e.shift_date
        FROM sheets s
        JOIN sheet_versions v ON v.sheet_id = s.id
        JOIN latest_versions lv ON lv.sheet_id = v.sheet_id AND lv.version = v.version
        JOIN entries e ON e.version_id = v.id
        WHERE e.shift_date = ?
        GROUP BY s.id, s.created_at, e.day, e.shift_date
    ),
    dated_sheets AS (
        SELECT id, day, shift_date,
               ROW_NUMBER() OVER (
                  PARTITION BY day, shift_date
                   ORDER BY created_at DESC, id DESC
               ) AS rank
        FROM dated_sheet_candidates
    )
    SELECT e.* FROM entries e
    JOIN sheet_versions v ON v.id = e.version_id
    JOIN latest_versions lv ON lv.sheet_id = v.sheet_id AND lv.version = v.version
    JOIN dated_sheets ds ON ds.id = v.sheet_id AND ds.day = e.day AND ds.shift_date = e.shift_date
    WHERE ds.rank = 1
    """
    params = [shift_date]
    if day:
        query += " AND e.day = ?"
        params.append(day)
    if kind:
        query += " AND e.kind = ?"
        params.append(kind)
    with connect() as c:
        return [dict(row) for row in c.execute(query, params)]


def historical_entries(kind=None):
    """Latest revision for each saved schedule, including replaced historical schedules."""
    query = """
    SELECT e.* FROM entries e
    JOIN sheet_versions v ON v.id = e.version_id
    JOIN sheets s ON s.id = v.sheet_id
    WHERE v.version = (
        SELECT MAX(latest.version) FROM sheet_versions latest WHERE latest.sheet_id = s.id
    )
    """
    params = []
    if kind:
        query += " AND e.kind = ?"
        params.append(kind)
    with connect() as c:
        return [dict(row) for row in c.execute(query, params)]


def entries_for_person(name, shift_date=None):
    if shift_date:
        return [entry for entry in entries_on_date(shift_date) if entry["person"] == name]
    with connect() as c:
        query, params = ACTIVE_ENTRIES + " AND e.person = ?", [name]
        return [dict(r) for r in c.execute(query, params)]


# ---------- نام‌های آموخته‌شده (استاد/استودیو) ----------
def get_known(kind):
    with connect() as c:
        return [r["name"] for r in c.execute("SELECT name FROM known_names WHERE kind=?", (kind,))]


def add_known(kind, names):
    with connect() as c:
        c.executemany("INSERT OR IGNORE INTO known_names(kind, name) VALUES(?,?)", [(kind, n) for n in names])
        c.executemany("DELETE FROM known_name_exclusions WHERE kind=? AND name=?",
                      [(kind, key(normalize(n))) for n in names])


def get_known_excluded(kind):
    with connect() as c:
        return [r["name"] for r in c.execute(
            "SELECT name FROM known_name_exclusions WHERE kind=?", (kind,))]


def rename_known(kind, old_name, new_name):
    with connect() as c:
        c.execute("DELETE FROM known_names WHERE kind=? AND name=?", (kind, old_name))
        c.execute("INSERT OR IGNORE INTO known_name_exclusions(kind, name) VALUES(?,?)",
                  (kind, key(normalize(old_name))))
        c.execute("INSERT OR IGNORE INTO known_names(kind, name) VALUES(?,?)", (kind, new_name))
        c.execute("DELETE FROM known_name_exclusions WHERE kind=? AND name=?",
                  (kind, key(normalize(new_name))))
        c.execute("UPDATE known_name_aliases SET canonical=? WHERE kind=? AND canonical=?",
                  (new_name, kind, old_name))
        return True


def delete_known(kind, name):
    with connect() as c:
        c.execute("DELETE FROM known_names WHERE kind=? AND name=?", (kind, name))
        c.execute("INSERT OR IGNORE INTO known_name_exclusions(kind, name) VALUES(?,?)",
                  (kind, key(normalize(name))))
        c.execute("DELETE FROM known_name_aliases WHERE kind=? AND canonical=?", (kind, name))
        return True


def list_known_aliases(kind, canonical):
    with connect() as c:
        return [dict(row) for row in c.execute(
            "SELECT id, alias, canonical FROM known_name_aliases "
            "WHERE kind=? AND canonical=? ORDER BY alias COLLATE NOCASE",
            (kind, canonical),
        )]


def list_aliases(kind):
    with connect() as c:
        return [dict(row) for row in c.execute(
            "SELECT id, alias, canonical FROM known_name_aliases WHERE kind=? ORDER BY id",
            (kind,),
        )]


def add_known_alias(kind, alias, canonical):
    alias = normalize(alias).strip()
    canonical = normalize(canonical).strip()
    alias_key = key(alias)
    if not alias_key or not canonical:
        raise ValueError("متن ورودی و نام خروجی نمی‌توانند خالی باشند.")
    with connect() as c:
        kinds = ("teacher", "remote_teacher") if kind in ("teacher", "remote_teacher") else (kind,)
        placeholders = ",".join("?" for _ in kinds)
        rows = c.execute(
            f"SELECT id, kind, alias, canonical FROM known_name_aliases WHERE kind IN ({placeholders})",
            kinds,
        ).fetchall()
        for row in rows:
            if key(row["alias"]) == alias_key:
                if key(row["canonical"]) != key(canonical):
                    raise ValueError(f"این شکل ورودی قبلاً به «{row['canonical']}» وصل شده است.")
                if row["kind"] == kind:
                    c.execute("UPDATE known_name_aliases SET alias=?, canonical=? WHERE id=?",
                              (alias, canonical, row["id"]))
                    return row["id"]
        c.execute("INSERT INTO known_name_aliases(kind, alias, canonical) VALUES(?,?,?)",
                  (kind, alias, canonical))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def delete_known_alias(alias_id):
    with connect() as c:
        return c.execute("DELETE FROM known_name_aliases WHERE id=?", (alias_id,)).rowcount > 0


def get_setting(name, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
    return row["value"] if row else default


def set_setting(name, value):
    with connect() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name, str(value)))


# ---------- گروه اساتید ----------
def link_teacher(teacher, chat_id, supervisor=None):
    with connect() as c:
        c.execute(
            "INSERT INTO teacher_groups(teacher_key, teacher, chat_id, supervisor) VALUES(?,?,?,?) "
            "ON CONFLICT(teacher_key) DO UPDATE SET teacher=excluded.teacher, chat_id=excluded.chat_id, "
            "supervisor=excluded.supervisor, linked_at=CURRENT_TIMESTAMP",
            (key(teacher), teacher, chat_id, supervisor),
        )


def assign_teacher(teacher, supervisor, username):
    teacher_key = key(normalize(teacher))
    with connect() as c:
        c.execute(
            "INSERT INTO teacher_assignments(teacher_key, teacher, supervisor, username) VALUES(?,?,?,?) "
            "ON CONFLICT(teacher_key) DO UPDATE SET teacher=excluded.teacher, supervisor=excluded.supervisor, "
            "username=excluded.username, assigned_at=CURRENT_TIMESTAMP",
            (teacher_key, normalize(teacher), supervisor, username.lstrip("@")),
        )
        c.execute("UPDATE teacher_groups SET teacher=?, supervisor=? WHERE teacher_key=?",
                  (normalize(teacher), supervisor, teacher_key))


def get_teacher_assignment(teacher):
    with connect() as c:
        row = c.execute("SELECT * FROM teacher_assignments WHERE teacher_key=?", (key(normalize(teacher)),)).fetchone()
    return dict(row) if row else None


def set_teacher_telegram_id(teacher, telegram_id):
    with connect() as c:
        result = c.execute(
            "UPDATE teacher_assignments SET telegram_id=? WHERE teacher_key=?",
            (telegram_id, key(normalize(teacher))),
        )
        return result.rowcount > 0


def get_teacher_assignment_by_key(teacher_key):
    with connect() as c:
        row = c.execute("SELECT * FROM teacher_assignments WHERE teacher_key=?",
                        (teacher_key,)).fetchone()
    return dict(row) if row else None


def get_teacher_assignment_by_id(assignment_id):
    with connect() as c:
        row = c.execute("SELECT rowid AS id, * FROM teacher_assignments WHERE rowid=?",
                        (assignment_id,)).fetchone()
    return dict(row) if row else None


def update_teacher_assignment(teacher, new_teacher, supervisor, username):
    old_key = key(normalize(teacher))
    new_teacher = normalize(new_teacher).strip()
    new_key = key(new_teacher)
    with connect() as c:
        existing = c.execute("SELECT 1 FROM teacher_assignments WHERE teacher_key=?",
                             (old_key,)).fetchone()
        if not existing:
            return False
        conflict = c.execute(
            "SELECT 1 FROM teacher_assignments WHERE teacher_key<>? AND teacher_key=?",
            (old_key, new_key),
        ).fetchone()
        if conflict:
            raise ValueError("این استاد از قبل ثبت شده است.")
        group = c.execute("SELECT chat_id FROM teacher_groups WHERE teacher_key=?",
                          (old_key,)).fetchone()
        if group and new_key != old_key:
            group_conflict = c.execute("SELECT 1 FROM teacher_groups WHERE teacher_key=?",
                                       (new_key,)).fetchone()
            if group_conflict:
                raise ValueError("برای نام جدید گروه دیگری ثبت شده است.")
            c.execute(
                "UPDATE teacher_groups SET teacher_key=?, teacher=?, supervisor=? WHERE teacher_key=?",
                (new_key, new_teacher, supervisor, old_key),
            )
        c.execute(
            "UPDATE teacher_assignments SET teacher_key=?, teacher=?, supervisor=?, username=?, "
            "assigned_at=CURRENT_TIMESTAMP WHERE teacher_key=?",
            (new_key, new_teacher, supervisor, username.lstrip("@"), old_key),
        )
        c.execute("INSERT OR IGNORE INTO known_names(kind, name) VALUES('teacher', ?)",
                  (new_teacher,))
    return True


def delete_teacher_assignment(teacher):
    teacher_key = key(normalize(teacher))
    with connect() as c:
        assignment = c.execute("SELECT teacher FROM teacher_assignments WHERE teacher_key=?",
                               (teacher_key,)).fetchone()
        if not assignment:
            return False
        c.execute("DELETE FROM teacher_groups WHERE teacher_key=?", (teacher_key,))
        c.execute("DELETE FROM teacher_assignments WHERE teacher_key=?", (teacher_key,))
    return True


def list_teacher_assignments():
    with connect() as c:
        return [dict(row) for row in c.execute(
            "SELECT rowid AS id, * FROM teacher_assignments ORDER BY teacher")]


def list_teacher_groups():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM teacher_groups ORDER BY teacher")]


# ---------- حوادث ----------
def add_incident(staff_name, studio, class_desc, kind, need_help):
    with connect() as c:
        c.execute("INSERT INTO incidents(staff_name, studio, class_desc, kind, need_help) VALUES(?,?,?,?,?)",
                  (staff_name, studio, class_desc, kind, int(need_help)))
