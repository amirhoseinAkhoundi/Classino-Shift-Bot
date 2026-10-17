import os
import pathlib
import tempfile

os.environ.setdefault("BOT_TOKEN", "123:abc")
os.environ["DB_PATH"] = str(pathlib.Path(tempfile.mkdtemp()) / "boot.db")
os.environ["GROUP_CHAT_ID"] = "-100"
os.environ["SCHEDULE_SENDER_IDS"] = "2"
