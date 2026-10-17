"""پارس متن برنامه‌ی روزانه به ورودی‌های ساختاریافته (بدون وابستگی به تلگرام)."""
import re
from dataclasses import dataclass, field

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

DAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]

TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*تا\s*(\d{1,2})(?::(\d{2}))?")
KINDS = {"نظارت": "nazer", "کمکی": "komaki", "ادمین کمکی": "komaki"}
COURSE_STARTS = {
    "آمار", "ادبیات", "احتمال", "ایکیو", "تاریخ", "تجربی", "جامع", "حسابان",
    "خلاقیت", "دبستان", "دهم", "دوازدهم", "ریاضی", "زیست", "شیمی", "فلسفه",
    "فیزیک", "عمومی", "علوم", "مرداد", "منطق", "نمایشی", "نهم", "هفتم",
    "هشتم", "یازدهم", "چهارم", "پنجم", "پیش",
}


def normalize(text: str) -> str:
    text = text.translate(_DIGITS).replace("ي", "ی").replace("ك", "ک")
    return text.replace("\u200f", "").replace("\u200e", "")


def key(s: str) -> str:
    """کلید مقایسه: بدون فاصله و نیم‌فاصله."""
    return re.sub(r"[\s\u200c]", "", s)


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


def parse_schedule(text: str, studios: dict, teachers: dict, remote: dict | None = None) -> ParseResult:
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
        course = " ".join(before[k:]) or None

        if key(teacher) in remote:  # آنلاین: بدون استودیو
            person = " ".join(after) or None
            if person is None:
                res.warnings.append((line, "ادمین کلاس مشخص نیست"))
            res.entries.append(Entry(day, "class", start, end, teacher, course, None, person))
            continue

        studio, k2 = _prefix(after, studios)
        if studio:
            person = " ".join(after[k2:]) or None
        else:
            if not after:
                res.warnings.append((line, "استودیو و ادمین نیست"))
                continue
            if len(after) == 1:
                studio, person = None, after[0]
            else:
                studio = " ".join(after[:-1])
                person = after[-1]
                res.new_studios.add(studio)
        if person is None:
            res.warnings.append((line, "ادمین کلاس مشخص نیست"))
        res.entries.append(Entry(day, "class", start, end, teacher, course, studio, person))
    return res


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
