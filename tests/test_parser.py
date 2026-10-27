import pathlib

from schedule_parser import load_list, parse_schedule

ROOT = pathlib.Path(__file__).resolve().parent.parent
STUDIOS = load_list(ROOT / "data/studios.txt")
TEACHERS = load_list(ROOT / "data/teachers.txt")
REMOTE = load_list(ROOT / "data/remote_teachers.txt")
TEXT = (ROOT / "tests/sample_sunday.txt").read_text(encoding="utf-8")


def parse():
    return parse_schedule(TEXT, STUDIOS, TEACHERS, REMOTE)


def test_counts():
    r = parse()
    assert r.days == ["یکشنبه"]
    assert sum(e.kind == "class" for e in r.entries) == 42
    assert sum(e.kind == "nazer" for e in r.entries) == 4
    assert sum(e.kind == "komaki" for e in r.entries) == 1


def test_first_line():
    e = parse().entries[0]
    assert (e.teacher, e.course, e.studio, e.person) == ("مرادی", "مرداد فوق العاده", "بزرگ", "محمدی")
    assert (e.start, e.end) == (11 * 60, 14 * 60 + 30)


def test_two_word_teacher_and_studio_with_space():
    es = {(e.teacher, e.studio) for e in parse().entries if e.kind == "class"}
    assert ("بهرام پور", "طوسی1") in es
    assert ("ملازاده", "استودیو1دوازده فروردین") in es


def test_remote_teacher_has_no_studio():
    r = parse()
    e = next(e for e in r.entries if e.teacher == "بازرگانی")
    assert e.studio is None and e.person == "آخوندی"
    assert r.warnings == []
    h = parse_schedule("شنبه\nهاشمی شیمی یازدهم 19تا21 آخوندی", STUDIOS, TEACHERS, REMOTE).entries[0]
    assert h.studio is None and h.person == "آخوندی"


def test_supervision_colon_formats():
    people = {e.person for e in parse().entries if e.kind in ("nazer", "komaki")}
    assert people == {"سهیل", "سامی", "مهدیان", "عرفان", "اشتری"}


def test_admin_helper_prefix_with_scoped_studios_and_colon():
    result = parse_schedule(
        "جمعه\nادمین کمکی استودیو های جمشید و طوسی 11تا21 : مهران",
        STUDIOS, TEACHERS, REMOTE,
    )
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert (entry.kind, entry.person, entry.course) == (
        "komaki", "مهران", "استودیو های جمشید و طوسی")
    assert result.warnings == []


def test_persian_digits():
    r = parse_schedule("شنبه\nمرادی مرداد ۱۱تا۱۴:۳۰ بزرگ محمدی", STUDIOS, TEACHERS)
    assert r.entries[0].start == 660 and r.entries[0].end == 870


def test_unknown_teacher_and_studio_are_learned_not_warned():
    r = parse_schedule("شنبه\nنوشین ریاضی نهم 15تا16 اتاق7 رضا", STUDIOS, TEACHERS, REMOTE)
    e = r.entries[0]
    assert (e.teacher, e.studio, e.person) == ("نوشین", "اتاق7", "رضا")
    assert r.new_teachers == {"نوشین"} and r.new_studios == {"اتاق7"}
    assert "نام چندبخشی" in r.warnings[0][1]


def test_registered_multiword_teacher_is_parsed_as_one_name():
    teachers = {**TEACHERS, "بامرفیع": "بام رفیع"}
    result = parse_schedule(
        "شنبه\nبام رفیع ریاضی دهم 15تا16 طوسی1 وحید",
        STUDIOS, teachers, REMOTE,
    )
    entry = result.entries[0]
    assert (entry.teacher, entry.course, entry.studio, entry.person) == (
        "بام رفیع", "ریاضی دهم", "طوسی1", "وحید")
    assert result.new_teachers == set()


def test_registered_remote_teacher_needs_no_studio():
    remote = {**REMOTE, "استادآنلاین": "استاد آنلاین"}
    result = parse_schedule(
        "شنبه\nاستاد آنلاین ریاضی دهم 15تا16 وحید",
        STUDIOS, {**TEACHERS, "استادآنلاین": "استاد آنلاین"}, remote,
    )
    entry = result.entries[0]
    assert entry.teacher == "استاد آنلاین" and entry.studio is None and entry.person == "وحید"


def test_unknown_two_word_teacher_is_recognized_before_course():
    result = parse_schedule(
        "شنبه\nنوشین مرادی ریاضی نهم 15تا16 طوسی1 رضا",
        STUDIOS, TEACHERS, REMOTE,
    )
    entry = result.entries[0]
    assert (entry.teacher, entry.course, entry.studio, entry.person) == (
        "نوشین مرادی", "ریاضی نهم", "طوسی1", "رضا")
    assert result.new_teachers == {"نوشین مرادی"}


def test_missing_known_studio_is_classified_as_online():
    result = parse_schedule(
        "شنبه\nنوشین مرادی ریاضی نهم 15تا16 رضا",
        STUDIOS, TEACHERS, REMOTE,
    )
    entry = result.entries[0]
    assert (entry.teacher, entry.studio, entry.person) == ("نوشین مرادی", None, "رضا")
    assert result.new_studios == set()


def test_alias_forms_map_to_canonical_teacher_and_studio_names():
    teachers = {**TEACHERS, "بامرفیع": "بام رفیع"}
    teachers["بام‌رفیع"] = "بام رفیع"
    studios = {**STUDIOS, "استودیوسپید": "سفید1"}
    result = parse_schedule(
        "شنبه\nبام رفیع ریاضی دهم 15تا16 سفید1 وحید\n"
        "شنبه\nبام‌رفیع فیزیک 16تا17 استودیو سپید وحید",
        studios, teachers, REMOTE,
    )
    assert [(entry.teacher, entry.studio) for entry in result.entries] == [
        ("بام رفیع", "سفید1"), ("بام رفیع", "سفید1")]


def test_class_accepts_multiple_admins_and_trailing_class_notes():
    result = parse_schedule(
        "شنبه\n"
        "مودت ریاضی سوم 14:30تا15:30 بابک و احسانی\n"
        "آریان نوبان انسانی دوازدهم 15تا18 منفی1 مهران (مردادی‌ها ساعت ۱۶ اضافه شن)",
        STUDIOS, TEACHERS, REMOTE, admin_names=["بابک", "احسانی", "مهران"],
    )
    first, second = result.entries
    assert first.person == "بابک و احسانی"
    assert first.studio is None
    assert second.person == "مهران"
    assert second.studio == "منفی1"
    assert second.teacher == "آریان نوبان"
    assert second.course.endswith("(مردادی‌ها ساعت 16 اضافه شن)")


def test_unknown_multiple_admins_do_not_become_an_unknown_studio():
    result = parse_schedule(
        "شنبه\nمودت ریاضی سوم 14:30تا15:30 بابک و احسانی",
        STUDIOS, TEACHERS, REMOTE,
    )
    entry = result.entries[0]
    assert entry.studio is None
    assert entry.person == "بابک و احسانی"
    assert not result.new_studios


def test_unknown_single_admin_and_unknown_studio_keep_existing_fallback():
    result = parse_schedule(
        "شنبه\nنوشین ریاضی نهم 15تا16 اتاق7 رضا",
        STUDIOS, TEACHERS, REMOTE,
    )
    entry = result.entries[0]
    assert (entry.studio, entry.person) == ("اتاق7", "رضا")


def test_schedule_admin_names_are_split_into_individual_people():
    from schedule_parser import split_people

    assert split_people("بابک و احسانی") == ["بابک", "احسانی"]
