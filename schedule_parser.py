"""پارس متن برنامه‌ی روزانه به ورودی‌های ساختاریافته (بدون وابستگی به تلگرام)."""
import re
from dataclasses import dataclass, field, replace

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

DAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]

TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:تا|-|–|—|to)\s*(\d{1,2})(?::(\d{2}))?")
KINDS = {"نظارت": "nazer", "کمکی": "komaki", "ادمین کمکی": "komaki"}
COURSE_STARTS = {
    "آمار", "ادبیات", "احتمال", "ایکیو", "تاریخ", "تجربی", "جامع", "حسابان",
    "خلاقیت", "دبستان", "دهم", "دوازدهم", "ریاضی", "زیست", "شیمی", "فلسفه",
    "فیزیک", "عمومی", "علوم", "مرداد", "منطق", "نمایشی", "نهم", "هفتم",
    "هشتم", "یازدهم", "چهارم", "پنجم", "پیش", "انسانی",
}


def normalize(text: str) -> str:
    text = text.translate(_DIGITS).replace("ي", "ی").replace("ك", "ک")
    return text.replace("\u200f", "").replace("\u200e", "")


def key(s: str) -> str:
    """کلید مقایسه: بدون فاصله و نیم‌فاصله."""
    return re.sub(r"[\s\u200c]", "", s)


def split_people(value):
    return [name.strip() for name in re.split(r"\s+و\s+", normalize(value or "")) if name.strip()]


def person_matches(value, name):
    return any(key(person) == key(name) for person in split_people(value))


DAY_KEYS = {key(d): d for d in DAYS}


def fmt_min(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def load_list(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return {key(normalize(l.strip())): normalize(l.strip()) for l in f if l.strip()}


@dataclass
class Entry:
    day: str
    kind: str            # class | nazer | komaki
    start: int
    end: int
    teacher: str | None = None
    course: str | None = None   # برای نظارت/کمکی: محدوده‌ی استودیوها
    studio: str | None = None
    person: str | None = None   # ادمین کلاس / ناظر / کمکی


@dataclass
class ParseResult:
    entries: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # (line, reason)
    new_teachers: set = field(default_factory=set)  # استادهایی که در لیست‌ها نبودند
    new_studios: set = field(default_factory=set)

    @property
    def days(self):
        return list(dict.fromkeys(e.day for e in self.entries))


def _prefix(tokens, known):
    """طولانی‌ترین پیشوند توکن‌ها که در لیست شناخته‌شده‌ها هست."""
    for k in range(len(tokens), 0, -1):
        hit = known.get(key(" ".join(tokens[:k])))
        if hit:
            return hit, k
    return None, 0


def _unknown_teacher_prefix(tokens):
    """نام ناشناخته‌ی دوواژه‌ای را از درسِ پس از آن جدا می‌کند."""
    if len(tokens) > 1 and key(tokens[1]) in COURSE_STARTS:
        return tokens[0], 1
    if len(tokens) > 2 and key(tokens[2]) in COURSE_STARTS:
        return " ".join(tokens[:2]), 2
    if len(tokens) > 1:
        return " ".join(tokens[:2]), 2
    return tokens[0], 1


def _time(m):
    s = int(m.group(1)) * 60 + int(m.group(2) or 0)
    e = int(m.group(3)) * 60 + int(m.group(4) or 0)
    return s, e


def _person_and_note(value, admin_names):
    value = value.strip()
    note = re.search(r"\s*\(([^()]*)\)\s*$", value)
    note_text = f"({note.group(1).strip()})" if note else ""
    value = value[:note.start()].strip() if note else value
    tokens = value.split()
    names = sorted(admin_names, key=lambda name: len(name.split()), reverse=True)
    matched = []
    cursor = 0
    while cursor < len(tokens):
        hit = next((name for name in names
                    if key(" ".join(tokens[cursor:cursor + len(name.split())])) == key(name)), None)
        if not hit:
            break
        matched.append(hit)
        cursor += len(hit.split())
        if cursor < len(tokens) and key(tokens[cursor]) == key("و"):
            cursor += 1
        elif cursor < len(tokens):
            break
    if matched:
        note_text = " ".join(part for part in (note_text, " ".join(tokens[cursor:])) if part)
        person_text = " و ".join(matched)
    else:
        person_text = value
    return person_text, note_text


def parse_schedule(text: str, studios: dict, teachers: dict, remote: dict | None = None,
                   admin_names=()) -> ParseResult:
    """remote: اساتید خارج از ایران؛ استودیو ندارند و بعد از ساعت فقط اسم ادمین می‌آید."""
    remote = remote or {}
    res = ParseResult()
    day = None
    for raw in normalize(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        dk = key(line).strip(":：،.")
        if dk in DAY_KEYS:
            day = DAY_KEYS[dk]
            continue
        m = TIME_RE.search(line)
        if not m:
            res.warnings.append((line, "ساعت پیدا نشد"))
            continue
        if day is None:
            res.warnings.append((line, "قبل از عنوان روز آمده"))
            continue
        start, end = _time(m)
        if not (0 <= start < end <= 24 * 60):
            res.warnings.append((line, "بازه‌ی ساعت نامعتبر"))
            continue
        before = line[: m.start()].split()
        after = line[m.end():].lstrip(" :：").split()

        kind_prefix = next(
            ((prefix, kind) for prefix, kind in sorted(
                KINDS.items(), key=lambda item: len(item[0].split()), reverse=True)
             if len(before) >= len(prefix.split())
             and key(" ".join(before[:len(prefix.split())])) == key(prefix)),
            None,
        )
        if kind_prefix:
            prefix, kind = kind_prefix
            res.entries.append(Entry(day, kind, start, end,
                                     course=" ".join(before[len(prefix.split()):]) or None,
                                     person=" ".join(after) or None))
            if not after:
                res.warnings.append((line, "نام ناظر/کمکی نیست"))
            continue

        teacher, k = _prefix(before, teachers)
        if teacher is None:
            if not before:
                res.warnings.append((line, "نام استاد نیست"))
                continue
            teacher, k = _unknown_teacher_prefix(before)
            res.new_teachers.add(teacher)
            res.warnings.append((line, "نام استاد در فهرست نیست؛ ممکن است نام چندبخشی باشد"))
        elif k < len(before):
            course_start = next((index for index in range(k, len(before))
                                 if key(before[index]) in COURSE_STARTS), None)
            if course_start is not None and 0 < course_start - k <= 2:
                teacher = " ".join(before[:course_start])
                k = course_start
                res.new_teachers.add(teacher)
        course = " ".join(before[k:]) or None

        if key(teacher) in remote:  # آنلاین: بدون استودیو
            person_text, note = _person_and_note(" ".join(after), admin_names)
            if note:
                course = f"{course or ''} {note}".strip()
            person = " و ".join(split_people(person_text)) or None
            if person is None:
                res.warnings.append((line, "ادمین کلاس مشخص نیست"))
            res.entries.append(Entry(day, "class", start, end, teacher, course, None, person))
            continue

        studio, k2 = _prefix(after, studios)
        if studio:
            person_text = " ".join(after[k2:])
        else:
            if not after:
                res.warnings.append((line, "استودیو و ادمین نیست"))
                continue
            if len(after) == 1:
                studio, person_text = None, after[0]
            else:
                if "و" in after:
                    separator = after.index("و")
                    studio = " ".join(after[:separator - 1]) or None
                    person_text = " ".join(after[separator - 1:])
                else:
                    admin_count = len(split_people(" ".join(after)))
                    studio = " ".join(after[:-admin_count]) or None
                    person_text = " ".join(after[-admin_count:])
                    if admin_count == 1 and not any(
                            key(name) == key(person_text) for name in admin_names):
                        studio, person_text = " ".join(after[:-1]), after[-1]
                if studio:
                    res.new_studios.add(studio)
        person_text, note = _person_and_note(person_text, admin_names)
        if note:
            course = f"{course or ''} {note}".strip()
        person = " و ".join(split_people(person_text)) or None
        if person is None:
            res.warnings.append((line, "ادمین کلاس مشخص نیست"))
        res.entries.append(Entry(day, "class", start, end, teacher, course, studio, person))
    return res


def apply_helper_class_buffers(entries, minutes=30):
    def value(entry, field):
        if isinstance(entry, dict):
            return entry[field]
        return getattr(entry, field)

    classes = [entry for entry in entries
               if value(entry, "kind") == "class" and value(entry, "person")]
    result = []
    for entry in entries:
        if value(entry, "kind") != "komaki" or not value(entry, "person"):
            result.append(entry)
            continue
        blocks = sorted(
            (max(0, value(cls, "start_min" if isinstance(cls, dict) else "start") - minutes),
             min(24 * 60, value(cls, "end_min" if isinstance(cls, dict) else "end") + minutes))
            for cls in classes
            if value(cls, "day") == value(entry, "day")
            and person_matches(value(cls, "person"), value(entry, "person"))
            and value(cls, "start_min" if isinstance(cls, dict) else "start") - minutes
            < value(entry, "end_min" if isinstance(entry, dict) else "end")
            and value(cls, "end_min" if isinstance(cls, dict) else "end") + minutes
            > value(entry, "start_min" if isinstance(entry, dict) else "start")
        )
        start_field = "start_min" if isinstance(entry, dict) else "start"
        end_field = "end_min" if isinstance(entry, dict) else "end"
        segments = [(value(entry, start_field), value(entry, end_field))]
        for block_start, block_end in blocks:
            remaining = []
            for start, end in segments:
                if block_end <= start or block_start >= end:
                    remaining.append((start, end))
                    continue
                if start < block_start:
                    remaining.append((start, block_start))
                if block_end < end:
                    remaining.append((block_end, end))
            segments = remaining
        for start, end in segments:
            if start >= end:
                continue
            if isinstance(entry, dict):
                segment = dict(entry)
                segment[start_field], segment[end_field] = start, end
                result.append(segment)
            else:
                result.append(replace(entry, start=start, end=end))
    return result


def entry_to_dict(e: "Entry") -> dict:
    return {"day": e.day, "kind": e.kind, "start_min": e.start, "end_min": e.end,
            "teacher": e.teacher, "course": e.course, "studio": e.studio, "person": e.person}


RANGE_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:تا|-|–|—|to)\s*(\d{1,2})(?::(\d{2}))?")


def parse_day(text: str):
    k = key(normalize(text)).strip(":：،.")
    return DAY_KEYS.get(k)


def parse_range(text: str):
    """«یکشنبه 17:00-19:30» -> (start_min, end_min, باقی‌متن) یا None."""
    t = normalize(text)
    m = RANGE_RE.search(t)
    if not m:
        return None
    s, e = _time(m)
    if not (0 <= s < e <= 24 * 60):
        return None
    return s, e, (t[: m.start()] + " " + t[m.end():]).strip()
