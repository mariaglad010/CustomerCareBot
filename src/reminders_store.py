import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {"ПН": 0, "ВТ": 1, "СР": 2, "ЧТ": 3, "ПТ": 4, "СБ": 5, "ВС": 6}
DAY_NAMES = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]

CONFIG_FILE = "config.json"
REMINDERS_FILE = "data/reminders.json"
OFFSET_FILE = "data/offset.json"


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def load_config(root):
    return _read_json(f"{root}/{CONFIG_FILE}", {
        "bot_username": "",
        "admin_ids": [],
        "target_chat_id": 0,
        "timezone": "Europe/Moscow",
    })


def save_config(root, cfg):
    _write_json(f"{root}/{CONFIG_FILE}", cfg)


def load_reminders(root):
    return _read_json(f"{root}/{REMINDERS_FILE}", [])


def save_reminders(root, reminders):
    _write_json(f"{root}/{REMINDERS_FILE}", reminders)


def load_offset(root):
    return int(_read_json(f"{root}/{OFFSET_FILE}", 0))


def save_offset(root, offset):
    _write_json(f"{root}/{OFFSET_FILE}", offset)


def parse_schedule(spec_str):
    s = spec_str.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", s)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return {"type": "once", "datetime": f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"}
    m = re.match(r"ежедневно\s+(\d{2}):(\d{2})", s, re.I)
    if m:
        h, mi = map(int, m.groups())
        return {"type": "daily", "time": f"{h:02d}:{mi:02d}"}
    m = re.match(r"каждый\s+(ПН|ВТ|СР|ЧТ|ПТ|СБ|ВС)\s+(\d{2}):(\d{2})", s, re.I)
    if m:
        day = m.group(1).upper()
        h, mi = map(int, m.groups()[1:])
        return {"type": "weekly", "day": day, "time": f"{h:02d}:{mi:02d}"}
    return None


def schedule_description(spec):
    if spec["type"] == "once":
        return spec.get("datetime", "")[:16]
    if spec["type"] == "weekly":
        return f"каждый {spec['day']} {spec['time']}"
    return f"ежедневно {spec['time']}"


def compute_next(spec, now, tzname):
    tz = ZoneInfo(tzname)
    now = now.astimezone(tz)
    if spec["type"] == "once":
        dt = datetime.strptime(spec["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        return dt
    h, mi = map(int, spec["time"].split(":"))
    if spec["type"] == "daily":
        cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return cand if cand > now else cand + timedelta(days=1)
    target = WEEKDAYS[spec["day"]]
    cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    delta = (target - now.weekday()) % 7
    cand += timedelta(days=delta)
    return cand if cand > now else cand + timedelta(days=7)


def fmt_dt(dt, tzname):
    dt = dt.astimezone(ZoneInfo(tzname))
    return f"{dt.strftime('%d.%m')} {dt.strftime('%H:%M')} ({DAY_NAMES[dt.weekday()]})"


HELP_TEXT = (
    "🤖 Управление напоминаниями:\n\n"
    "/add \"текст\" 2026-09-15 14:00 — одноразовое\n"
    "/add \"текст\" ежедневно 09:00\n"
    "/add \"текст\" каждый ПН 15:30\n"
    "/list — список активных\n"
    "/del N — удалить запись N\n"
    "/clear — убрать завершённые\n"
    "/help — эта справка\n"
    "/id — твой id и id чата\n\n"
    "Управлять (кроме /list /help /id) может только админ."
)