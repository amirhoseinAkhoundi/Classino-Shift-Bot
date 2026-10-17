"""منطق خالص (بدون تلگرام و دیتابیس): تداخل، دیف نسخه‌ها، آمار، آزاد بودن، زمان تهران."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

from schedule_parser import DAYS, fmt_min, normalize, parse_day

PY_TO_FA = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه"}
JALALI_MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")


# ---------- زمان ----------
def tehran_timezone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Tehran")
    except Exception:
        return timezone(timedelta(hours=3, minutes=30))


def now_tehran():
    return datetime.now(tehran_timezone())


def day_of(dt):
    return PY_TO_FA[dt.weekday()]


def today_tomorrow(now=None):
    """(نام روز امروز، نام روز فردا، دقیقه‌ی فعلی از نیمه‌شب) به وقت تهران."""
    now = now or now_tehran()
    return day_of(now), day_of(now + timedelta(days=1)), now.hour * 60 + now.minute


def next_saturday(after=None):
    """تاریخ اولین شنبه‌ی تقویمی پس از زمان داده‌شده، به وقت تهران."""
    after = after or now_tehran()
    tz = tehran_timezone()
    after = after.replace(tzinfo=tz) if after.tzinfo is None else after.astimezone(tz)
    days = (5 - after.weekday()) % 7 or 7
    return (after.date() + timedelta(days=days)).isoformat()


def next_weekday_on_or_after(after, day):
    """نزدیک‌ترین تاریخِ روز هفته، از خود تاریخ مبنا به بعد."""
    if isinstance(after, datetime):
        tz = tehran_timezone()
        after = after.replace(tzinfo=tz) if after.tzinfo is None else after.astimezone(tz)
        after = after.date()
    elif isinstance(after, str):
        after = date.fromisoformat(after)
    target_weekday = (DAYS.index(day) + 5) % 7
    return (after + timedelta(days=(target_weekday - after.weekday()) % 7)).isoformat()


def schedule_date(saturday, day):
    """تاریخ میلادی روزی از برنامه‌ی هفتگی که شنبه‌ی آغاز آن مشخص شده است."""
    return (date.fromisoformat(saturday) + timedelta(days=DAYS.index(day))).isoformat()


def jalali_date_parts(gregorian_date):
    value = date.fromisoformat(gregorian_date) if isinstance(gregorian_date, str) else gregorian_date
    gy, gm, gd = value.year, value.month, value.day
    g_days_in_month = (31, 29 if _is_gregorian_leap(gy) else 28, 31, 30, 31, 30,
                       31, 31, 30, 31, 30, 31)
    gy -= 1600
    gm -= 1
    gd -= 1
    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    g_day_no += sum(g_days_in_month[:gm]) + gd
    j_day_no = g_day_no - 79
    j_year = 979 + 33 * (j_day_no // 12053)
    j_day_no %= 12053
    j_year += 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        j_year += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        j_month = 1 + j_day_no // 31
        j_day = 1 + j_day_no % 31
    else:
        j_month = 7 + (j_day_no - 186) // 30
        j_day = 1 + (j_day_no - 186) % 30
    return j_year, j_month, j_day


def _jalali_year_start(j_year):
    candidate = date(j_year + 621, 3, 20)
    for offset in range(-3, 4):
        value = candidate + timedelta(days=offset)
        if jalali_date_parts(value) == (j_year, 1, 1):
            return value
    raise ValueError(f"could not resolve Jalali year start: {j_year}")


def jalali_to_gregorian(j_year, j_month, j_day):
    if not 1 <= j_month <= 12:
        raise ValueError("Jalali month must be between 1 and 12")
    year_start = _jalali_year_start(j_year)
    next_year_start = _jalali_year_start(j_year + 1)
    month_lengths = [31] * 6 + [30] * 5 + [30 if (next_year_start - year_start).days == 366 else 29]
    if not 1 <= j_day <= month_lengths[j_month - 1]:
        raise ValueError("invalid Jalali day for month")
    offset = sum(month_lengths[:j_month - 1]) + j_day - 1
    return (year_start + timedelta(days=offset)).isoformat()


def reporting_period(period, now=None):
    """بازه‌ی تاریخ جاری بر مبنای تقویم شمسی و هفته‌ی شنبه تا جمعه."""
    current_time = now or now_tehran()
    if isinstance(current_time, datetime):
        tz = tehran_timezone()
        current_time = (current_time.replace(tzinfo=tz) if current_time.tzinfo is None
                        else current_time.astimezone(tz))
        current = current_time.date()
    else:
        current = current_time
    if period == "week":
        start = current - timedelta(days=(current.weekday() - 5) % 7)
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    if period == "month":
        year, month, _ = jalali_date_parts(current)
        month_start = jalali_to_gregorian(year, month, 1)
        if month == 12:
            next_start = jalali_to_gregorian(year + 1, 1, 1)
        else:
            next_start = jalali_to_gregorian(year, month + 1, 1)
        return month_start, (date.fromisoformat(next_start) - timedelta(days=1)).isoformat()
    raise ValueError(f"unsupported reporting period: {period}")


def jalali_day_month(gregorian_date):
    """تبدیل تاریخ میلادی به روز و نام ماه شمسی، مثل 24 مهر."""
    _, month, day = jalali_date_parts(gregorian_date)
    return f"{day} {JALALI_MONTHS[month - 1]}"


def _is_gregorian_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def day_label(day, shift_date=None, now=None, html=True):
    """برچسب روز با تاریخ شمسی؛ روز جاری بولد است."""
    label = escape(day) if html else day
    if shift_date:
        label += " " + jalali_day_month(shift_date)
    if shift_date and html:
        today = (now or now_tehran()).date().isoformat()
        if shift_date == today:
            return f"<b>{label}</b>"
    return label


def resolve_day(text, now=None):
    """'' یا امروز -> امروز؛ فردا -> فردا؛ نام روز -> همان؛ نامعتبر -> None."""
    today, tomorrow, _ = today_tomorrow(now)
    t = normalize(text or "").strip()
    if t in ("", "امروز"):
        return today
    if t == "فردا":
        return tomorrow
    return parse_day(t)


# ---------- نمایش ----------
def span(e):
    return f"{fmt_min(e['start_min'])}–{fmt_min(e['end_min'])}"


def fmt_entry(e, with_person=False):
    if e["kind"] == "class":
        what = (e["teacher"] or "") + (f" – {e['course']}" if e["course"] else "")
        s = f"{span(e)} | {e['studio'] or 'آنلاین'} | {what}"
        if with_person:
            s += f" | ادمین: {e['person'] or '—'}"
        return s
    label = "ناظر" if e["kind"] == "nazer" else "کمکی"
    s = f"{span(e)} | {label}" + (f" ({e['course']})" if e["course"] else "")
    if with_person:
        s += f" | {e['person']}"
    return s


def entry_line(e, shift_date=None, with_person=False, now=None):
    """یک ردیف شیفت برای پیام HTML؛ ردیف روزهای گذشته خط‌خورده می‌شود."""
    line = "• " + escape(fmt_entry(e, with_person))
    return strike_past(line, shift_date or e.get("shift_date"), now)


def text_line(text, shift_date=None, now=None):
    """متن یک ردیف HTML با بولت؛ ردیف روزهای گذشته خط‌خورده می‌شود."""
    return strike_past("• " + escape(text), shift_date, now)


def strike_past(text, shift_date=None, now=None):
    """متن HTML را فقط برای تاریخ‌های گذشته خط‌خورده می‌کند."""
    today = (now or now_tehran()).date().isoformat()
    return f"<s>{text}</s>" if shift_date and shift_date < today else text


def sort_entries(es):
    return sorted(es, key=lambda e: (DAYS.index(e["day"]), e["start_min"], e["end_min"]))


def day_block(day, entries, with_person=False, target_date=None, now=None):
    shift_date = target_date or next((e.get("shift_date") for e in entries if e.get("shift_date")), None)
    heading = f"📅 {day_label(day, shift_date, now)}"
    body = "\n".join(entry_line(e, shift_date, with_person, now) for e in sort_entries(entries))
    return heading + "\n\n" + body


def by_day_text(entries, with_person=False):
    days = defaultdict(list)
    for e in entries:
        days[e["day"]].append(e)
    return "\n\n".join(day_block(d, days[d], with_person) for d in sorted(days, key=DAYS.index))


# ---------- تداخل ----------
def overlap(a, b):
    return a["start_min"] < b["end_min"] and b["start_min"] < a["end_min"]


def find_conflicts(entries):
    """تداخل ساعت: دو کلاس در یک استودیو، یک استاد در دو جا، یک نفر در دو شیفت. (آنلاین‌ها استودیو ندارند)"""
    out = []
    n = len(entries)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = entries[i], entries[j]
            if a["day"] != b["day"] or not overlap(a, b):
                continue
            label = day_label(a["day"], a.get("shift_date"))
            both = (f"\n   {entry_line(a, a.get('shift_date'), with_person=True)}"
                    f"\n   {entry_line(b, b.get('shift_date'), with_person=True)}")
            if a["kind"] == "class" and b["kind"] == "class":
                if a["studio"] and a["studio"] == b["studio"]:
                    out.append(f"⛔ تداخل استودیو «{escape(a['studio'])}» ({label}):{both}")
                if a["teacher"] == b["teacher"]:
                    out.append(f"⛔ استاد «{escape(a['teacher'] or '')}» هم‌زمان دو کلاس دارد ({label}):{both}")
            if a["person"] and a["person"] == b["person"]:
                out.append(f"⛔ «{escape(a['person'])}» هم‌زمان دو شیفت دارد ({label}):{both}")
    return out


# ---------- دیف نسخه‌ها ----------
def _key(e):
    if e["kind"] == "class":
        return (e["kind"], e["teacher"], e["course"], e["start_min"], e["end_min"])
    return (e["kind"], e["course"], e["start_min"], e["end_min"], e["person"])


def _pkey(e):  # برای جفت‌کردن «تغییر ساعت» یک کلاس
    if e["kind"] == "class":
        return (e["kind"], e["teacher"], e["course"])
    return (e["kind"], e["course"], e["person"])


def diff_entries(old, new):
    """برمی‌گرداند (حذف‌شده‌ها، اضافه‌شده‌ها، [(قبل، بعد)] تغییرها)."""
    om, nm = defaultdict(list), defaultdict(list)
    for e in old:
        om[_key(e)].append(e)
    for e in new:
        nm[_key(e)].append(e)
    removed, added, changed = [], [], []
    for k in set(om) | set(nm):
        o, n = om.get(k, []), nm.get(k, [])
        m = min(len(o), len(n))
        for i in range(m):
            if (o[i]["studio"], o[i]["person"]) != (n[i]["studio"], n[i]["person"]):
                changed.append((o[i], n[i]))
        removed += o[m:]
        added += n[m:]
    rg, ag = defaultdict(list), defaultdict(list)
    for e in removed:
        rg[_pkey(e)].append(e)
    for e in added:
        ag[_pkey(e)].append(e)
    rem, add = [], []
    for k in set(rg) | set(ag):
        r, a = rg.get(k, []), ag.get(k, [])
        if len(r) == 1 and len(a) == 1:
            changed.append((r[0], a[0]))
        else:
            rem += r
            add += a
    return rem, add, changed


def diff_notes(removed, added, changed):
    """برای هر نفرِ درگیر، فقط خطوط مربوط به خودش."""
    notes = defaultdict(list)
    for e in removed:
        if e["person"]:
            notes[e["person"]].append("➖ حذف شد: " + fmt_entry(e))
    for e in added:
        if e["person"]:
            notes[e["person"]].append("➕ اضافه شد: " + fmt_entry(e))
    for o, n in changed:
        if o["person"] == n["person"]:
            if o["person"]:
                notes[o["person"]].append(f"✏️ تغییر:\n   قبل: {fmt_entry(o)}\n   بعد: {fmt_entry(n)}")
        else:
            if o["person"]:
                notes[o["person"]].append("➖ حذف شد: " + fmt_entry(o))
            if n["person"]:
                notes[n["person"]].append("➕ اضافه شد: " + fmt_entry(n))
    return notes


# ---------- آزاد بودن ----------
def free_staff(staff, entries, day, s, e):
    """(آزاد در بازه ولی آن روز شیفت دارند، آن روز اصلاً شیفت ندارند، مشغول در بازه)."""
    todays = [x for x in entries if x["day"] == day and x["person"]]
    works = {x["person"] for x in todays}
    busy = {x["person"] for x in todays if x["start_min"] < e and s < x["end_min"]}
    free_working = sorted((p for p in staff if p["name"] in works and p["name"] not in busy),
                          key=lambda p: p["name"])
    off = sorted((p for p in staff if p["name"] not in works), key=lambda p: p["name"])
    busy_list = sorted((p for p in staff if p["name"] in busy), key=lambda p: p["name"])
    return free_working, off, busy_list


# ---------- آمار ----------
def stats(entries):
    studio = defaultdict(lambda: [0, 0])
    admin = defaultdict(lambda: {"class": 0, "nazer": 0, "komaki": 0})
    for e in entries:
        d = e["end_min"] - e["start_min"]
        if e["kind"] == "class":
            name = e["studio"] or "آنلاین"
            studio[name][0] += d
            studio[name][1] += 1
        if e["person"]:
            admin[e["person"]][e["kind"]] += d
    studios = sorted(((n, v[0], v[1]) for n, v in studio.items()), key=lambda x: (-x[1], x[0]))
    admins = sorted(({"person": p, **v, "total": sum(v.values())} for p, v in admin.items()),
                    key=lambda x: (-x["total"], x["person"]))
    return studios, admins


def hours(minutes):
    return f"{minutes / 60:.1f}"


# ---------- لحظه‌ی حال ----------
def current_classes(entries, now, slack=20):
    return [e for e in entries if e["start_min"] - slack <= now < e["end_min"] + slack]


def active_now(entries, kind, day, now):
    return [e for e in entries if e["kind"] == kind and e["day"] == day and e["start_min"] <= now < e["end_min"]]
