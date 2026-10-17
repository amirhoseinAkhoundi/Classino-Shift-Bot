from datetime import datetime, timezone

from analysis import (current_classes, day_block, day_label, diff_entries, diff_notes, entry_line,
                      find_conflicts, free_staff, jalali_day_month, next_weekday_on_or_after, reporting_period,
                      resolve_day, stats, today_tomorrow)


def E(day="یکشنبه", kind="class", s=900, e=990, teacher="الف", course="ریاضی", studio="بزرگ", person="وحید"):
    return {"day": day, "kind": kind, "start_min": s, "end_min": e, "teacher": teacher, "course": course,
            "studio": studio, "person": person}


def test_today_tomorrow_and_resolve():
    now = datetime(2026, 10, 11, 15, 30)  # یکشنبه
    assert today_tomorrow(now) == ("یکشنبه", "دوشنبه", 15 * 60 + 30)
    assert resolve_day("فردا", now) == "دوشنبه" and resolve_day("", now) == "یکشنبه"
    assert resolve_day("سه شنبه", now) == "سه‌شنبه" and resolve_day("blah", now) is None
    assert today_tomorrow(datetime(2026, 10, 16, 1, 0))[:2] == ("جمعه", "شنبه")


def test_schedule_dates_use_nearest_matching_weekday_from_tehran_send_date():
    sent = datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc)  # چهارشنبه در تهران
    assert next_weekday_on_or_after(sent, "چهارشنبه") == "2026-10-14"
    assert next_weekday_on_or_after(sent, "جمعه") == "2026-10-16"

    monday = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    assert next_weekday_on_or_after(monday, "جمعه") == "2026-10-16"
    assert next_weekday_on_or_after(monday, "جمعه") != "2026-10-23"


def test_solar_day_labels_and_today_tomorrow_styles():
    assert jalali_day_month("2026-10-16") == "24 مهر"
    assert jalali_day_month("2026-11-04") == "13 آبان"
    assert jalali_day_month("2026-12-30") == "9 دی"
    assert jalali_day_month("2025-03-20") == "30 اسفند"
    now = datetime(2026, 10, 12, 12)
    assert day_label("دوشنبه", "2026-10-12", now) == "<b>دوشنبه 20 مهر</b>"
    assert day_label("یکشنبه", "2026-10-11", now) == "یکشنبه 19 مهر"
    assert day_label("سه‌شنبه", "2026-10-13", now) == "سه‌شنبه 21 مهر"
    line = entry_line({**E(), "shift_date": "2026-10-11"}, now=now)
    assert line.startswith("<s>• ") and "ریاضی" in line
    block = day_block("یکشنبه", [{**E(), "shift_date": "2026-10-11"}], now=now)
    assert "📅 یکشنبه 19 مهر\n\n<s>• " in block


def test_reporting_periods_are_current_shamsi_week_and_month():
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    assert reporting_period("week", now) == ("2026-10-17", "2026-10-23")
    assert reporting_period("month", now) == ("2026-09-23", "2026-10-22")


def test_conflicts_studio_teacher_person_and_touching_is_ok():
    a = E(s=900, e=990)
    assert find_conflicts([a, E(s=990, e=1050)]) == []                       # لبه‌به‌لبه تداخل نیست
    assert any("استودیو" in c for c in find_conflicts([a, E(s=950, e=1000, teacher="ب", person="x")]))
    assert any("استاد" in c for c in find_conflicts([a, E(s=950, e=1000, studio="سفید", person="x")]))
    assert any("هم‌زمان دو شیفت" in c for c in find_conflicts([a, E(s=950, e=1000, teacher="ب", studio="سفید")]))
    # آنلاین‌ها استودیو ندارند => تداخل استودیو نمی‌دهند
    assert find_conflicts([E(studio=None, person="p"), E(studio=None, teacher="ب", person="q")]) == []


def test_diff_time_change_and_person_change():
    old = [E(s=900, e=990), E(teacher="ب", course="فیزیک", s=1000, e=1060, person="مهران")]
    new = [E(s=900, e=1020), E(teacher="ب", course="فیزیک", s=1000, e=1060, person="احسانی")]
    rem, add, chg = diff_entries(old, new)
    assert len(chg) == 2 and not rem and not add
    notes = diff_notes(rem, add, chg)
    assert set(notes) == {"وحید", "مهران", "احسانی"}
    assert "تغییر" in notes["وحید"][0] and "حذف" in notes["مهران"][0] and "اضافه" in notes["احسانی"][0]
    assert diff_entries(old, old) == ([], [], [])


def test_added_removed():
    rem, add, chg = diff_entries([E()], [E(), E(teacher="ج", course="شیمی", s=1100, e=1150)])
    assert len(add) == 1 and not rem and not chg
    rem, add, chg = diff_entries([E(), E(teacher="ج", course="شیمی", s=1100, e=1150)], [E()])
    assert len(rem) == 1 and not add and not chg


def test_free_staff():
    staff = [{"name": "وحید"}, {"name": "مهران"}, {"name": "نوشین"}]
    es = [E(s=900, e=990, person="وحید"), E(s=1200, e=1260, person="مهران", teacher="ب")]
    fw, off, busy = free_staff(staff, es, "یکشنبه", 880, 920)
    assert [p["name"] for p in fw] == ["مهران"] and [p["name"] for p in off] == ["نوشین"] \
        and [p["name"] for p in busy] == ["وحید"]


def test_stats_and_current():
    es = [E(s=600, e=690), E(kind="nazer", teacher=None, course="جمشید", studio=None, s=600, e=720, person="سهیل")]
    studios, admins = stats(es)
    assert studios == [("بزرگ", 90, 1)]
    assert {a["person"]: a["total"] for a in admins} == {"وحید": 90, "سهیل": 120}
    assert current_classes([E(s=900, e=990)], 895) and not current_classes([E(s=900, e=990)], 700)


def test_stats_have_stable_name_order_for_equal_totals():
    studios, admins = stats([
        E(studio="طوسی", person="وحید"),
        E(studio="بزرگ", person="آرش"),
    ])
    assert [name for name, _, _ in studios] == ["بزرگ", "طوسی"]
    assert [admin["person"] for admin in admins] == ["آرش", "وحید"]
