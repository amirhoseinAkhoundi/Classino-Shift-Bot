"""ایمپورت پرسنل از فایل CSV که مالک در پی‌وی بات می‌فرستد."""
import csv
import io
import logging

import db
from app import OWNER_TELEGRAM_ID, bot, normalize_phone, send
from schedule_parser import normalize

ROLES = {"shift_manager", "supervisor", "admin", "bot_manager"}


@bot.message_handler(content_types=["document"], chat_types=["private"])
def on_document(m):
    if m.from_user.id != OWNER_TELEGRAM_ID:
        return
    doc = m.document
    if not (doc.file_name or "").lower().endswith(".csv"):
        send(m.chat.id, "فقط فایل .csv بفرست.")
        return
    raw = bot.download_file(bot.get_file(doc.file_id).file_path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        send(m.chat.id, "فایل باید UTF-8 باشد (در اکسل: Save As → CSV UTF-8).")
        return
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") > first.count(",") else ","

    ok, bad = 0, []
    for i, r in enumerate(csv.DictReader(io.StringIO(text), delimiter=delim), start=2):
        r = {(k or "").strip().lower(): (v.strip() if isinstance(v, str) else "")
             for k, v in r.items()}
        name = normalize(r.get("name", "")).strip()
        phone = normalize_phone(r.get("phone", ""))
        role = r.get("role", "")
        sup = normalize(r.get("supervisor", "")).strip()
        if not name or len(phone) != 10 or role not in ROLES:
            bad.append(f"ردیف {i}: {name or '؟'}")
            continue
        try:
            db.upsert_staff(name, phone, role, sup)
            ok += 1
        except Exception as e:  # مثلاً شماره‌ی تکراری با اسم دیگر
            logging.warning("import row %s failed: %s", i, e)
            bad.append(f"ردیف {i}: {name} ({e})")
    msg = f"✅ {ok} نفر ثبت یا به‌روز شد."
    if bad:
        msg += "\n⚠️ ردشده:\n" + "\n".join(bad[:15])
    send(m.chat.id, msg)
