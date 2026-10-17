"""بارگذاری پرسنل از CSV به دیتابیس (ستون‌ها: name,phone,role[,supervisor]).
بات هنگام هر اجرا خودش این را صدا می‌زند؛ دستی هم می‌شود: python -m scripts.seed_staff data/staff.csv"""
import csv
import logging
import os
import sys
import tempfile

import db
from schedule_parser import key, normalize

ROLES = {"shift_manager", "supervisor", "admin", "bot_manager"}
CSV_FIELDS = ("name", "phone", "role", "supervisor")
DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "staff.csv")


def phone10(p):
    return "".join(c for c in normalize(p or "") if c.isdigit())[-10:]


def read_staff_csv(path=DEFAULT_CSV_PATH):
    if not os.path.exists(path):
        return list(CSV_FIELDS), []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or CSV_FIELDS)
        missing = set(CSV_FIELDS) - set(fields)
        if missing:
            raise ValueError(f"ستون‌های لازم در staff.csv نیستند: {', '.join(sorted(missing))}")
        rows = [{field: (row.get(field) or "").strip() for field in fields} for row in reader]
    return fields, rows


def write_staff_csv(fields, rows, path=DEFAULT_CSV_PATH):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".staff-", suffix=".csv", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, path)
    except Exception:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise


def save_staff_csv(record, path=DEFAULT_CSV_PATH, previous=None):
    fields, rows = read_staff_csv(path)
    record = {field: (record.get(field) or "").strip() for field in CSV_FIELDS}
    old_phone = phone10(previous.get("phone")) if previous else ""
    old_name = key(normalize(previous.get("name", ""))) if previous else ""
    match_index = next(
        (i for i, row in enumerate(rows) if old_phone and phone10(row.get("phone")) == old_phone),
        None,
    )
    if match_index is None and old_name:
        match_index = next(
            (i for i, row in enumerate(rows) if key(normalize(row.get("name", ""))) == old_name),
            None,
        )
    new_phone = phone10(record["phone"])
    new_name = key(normalize(record["name"]))
    for i, row in enumerate(rows):
        if i == match_index:
            continue
        if phone10(row.get("phone")) == new_phone or key(normalize(row.get("name", ""))) == new_name:
            raise ValueError("نام یا شماره‌ی پرسنل دیگری تکراری است.")
    if match_index is None:
        rows.append(record)
    else:
        rows[match_index].update(record)
    write_staff_csv(fields, rows, path)


def remove_staff_csv(record, path=DEFAULT_CSV_PATH):
    fields, rows = read_staff_csv(path)
    phone = phone10(record.get("phone"))
    name = key(normalize(record.get("name", "")))
    match_index = next(
        (i for i, row in enumerate(rows) if phone and phone10(row.get("phone")) == phone),
        None,
    )
    if match_index is None and name:
        match_index = next(
            (i for i, row in enumerate(rows) if key(normalize(row.get("name", ""))) == name),
            None,
        )
    kept = rows[:match_index] + rows[match_index + 1:] if match_index is not None else rows
    removed = int(match_index is not None)
    if removed:
        write_staff_csv(fields, kept, path)
    return removed


def restore_staff_csv(snapshot, path=DEFAULT_CSV_PATH):
    if snapshot is None:
        if os.path.exists(path):
            os.remove(path)
        return
    fields, rows = snapshot
    write_staff_csv(fields, rows, path)


def seed(path):
    """upsert همه‌ی ردیف‌ها؛ تعداد ردیف‌های موفق را برمی‌گرداند. ردیف بد، بقیه را خراب نمی‌کند."""
    ok = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name, role = normalize(row.get("name", "")), row.get("role", "")
            if role == "observer":
                role = "supervisor"
            elif role == "database_admin":
                role = "bot_manager"
            if not name and not row.get("phone"):
                continue  # ردیف خالی
            if not name or role not in ROLES or len(phone10(row.get("phone"))) != 10:
                logging.warning("staff.csv خط %s نامعتبر است (name/phone/role): %s", i, row)
                continue
            try:
                db.upsert_staff(name, phone10(row["phone"]), role, normalize(row.get("supervisor", "")) or None)
                ok += 1
            except Exception as e:  # noqa  (مثلاً شماره‌ی تکراری)
                logging.warning("staff.csv خط %s ثبت نشد: %s", i, e)
    return ok


if __name__ == "__main__":
    db.init_db()
    print(f"{seed(sys.argv[1])} نفر ثبت/به‌روز شد")
