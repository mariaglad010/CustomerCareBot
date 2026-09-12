import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from reminders_store import (
    fmt_dt, compute_next, schedule_description, parse_schedule,
    load_config, save_config, load_reminders, save_reminders,
    load_offset, save_offset, HELP_TEXT,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.telegram.org/bot{token}/{method}"


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def tg_call(token, method, **params):
    url = API.format(token=token, method=method)
    for attempt in range(3):
        try:
            resp = requests.post(url, data=params, timeout=30)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log(f"HTTP/parse error on {method}: {e}")
            time.sleep(5)
            continue
        if data.get("ok"):
            return data.get("result")
        err = data.get("error_code"), data.get("description")
        log(f"Telegram error on {method}: {err}")
        if data.get("error_code") == 429:
            retry = data.get("parameters", {}).get("retry_after", 5)
            if retry > 0 and retry <= 60:
                time.sleep(retry)
                continue
        return None
    return None


def send_message(token, chat_id, text):
    return tg_call(token, "sendMessage", chat_id=chat_id, text=text)


def get_updates(token, offset):
    return tg_call(
        token, "getUpdates",
        offset=offset, timeout=25, allowed_updates='["message"]',
    ) or []


def pure_command(text):
    strip = text.strip()
    if not strip.startswith("/"):
        return None, None
    first, _, rest = strip.partition(" ")
    name = first.split("@", 1)[0].lower()
    return name, rest.strip()


def reminder_message(r, tzname):
    nxt = datetime.fromisoformat(r["next"])
    return (
        "⏰ Напоминание\n\n"
        f"{r['text']}\n\n"
        f"📅 {fmt_dt(nxt, tzname)}"
    )


def fire_due(root, cfg, now):
    tzname = cfg["timezone"]
    tz = ZoneInfo(tzname)
    now = now.astimezone(tz)
    reminders = load_reminders(root)
    fired, remaining = [], []
    for r in reminders:
        if not r.get("active"):
            remaining.append(r)
            continue
        nxt = datetime.fromisoformat(r["next"]).astimezone(tz)
        if now < nxt:
            remaining.append(r)
            continue
        fired.append(r)
        r["last_fired"] = nxt.isoformat()
        if r["spec"]["type"] == "once":
            r["active"] = False
            remaining.append(r)
            continue
        base = nxt
        for _ in range(366):
            base = compute_next(r["spec"], base + timedelta(seconds=1), tzname)
            if base > now:
                break
        r["next"] = base.isoformat()
        remaining.append(r)
    save_reminders(root, remaining)
    return fired


def handle_command(root, cfg, name, rest, sender_id, chat_id):
    is_admin = sender_id in cfg["admin_ids"]

    if name == "/claim":
        if cfg["admin_ids"]:
            return "⛔ Админ уже назначен."
        cfg["admin_ids"] = [sender_id]
        cfg["target_chat_id"] = chat_id
        save_config(root, cfg)
        return "✅ Ты назначен админом, чат привязан к напоминаниям."

    if name == "/id":
        return f"Твой user id: {sender_id}\nChat id: {chat_id}"

    if name == "/help":
        return HELP_TEXT

    if name == "/list":
        reminders = [r for r in load_reminders(root) if r.get("active")]
        if not reminders:
            return "Активных напоминаний нет."
        lines = ["📋 Напоминания:"]
        for r in sorted(reminders, key=lambda x: x.get("next", "")):
            nxt = datetime.fromisoformat(r["next"])
            lines.append(
                f"#{r['id']} — {r['text']}\n"
                f"     ⏰ {schedule_description(r['spec'])} → {fmt_dt(nxt, cfg['timezone'])}"
            )
        return "\n".join(lines)

    if name in ("/add", "/del", "/clear") and not is_admin:
        return "⛔ Только админ может использовать эту команду."

    if name == "/add":
        m = re.match(r'^"([^"]+)"\s+(.+)$', rest, re.S)
        if not m:
            return 'Формат: /add "текст" РАСПИСАНИЕ\nПример: /add "Созвон" ежедневно 09:00'
        text, spec_str = m.group(1), m.group(2).strip()
        spec = parse_schedule(spec_str)
        if not spec:
            return (
                "Не понял расписание. Примеры:\n"
                "2026-09-15 14:00\n"
                "ежедневно 09:00\n"
                "каждый ПН 15:30"
            )
        reminders = load_reminders(root)
        rid = max((r["id"] for r in reminders), default=0) + 1
        nxt = compute_next(spec, datetime.now(), cfg["timezone"])
        reminders.append({
            "id": rid, "text": text, "spec": spec,
            "next": nxt.isoformat(), "last_fired": None, "active": True,
        })
        save_reminders(root, reminders)
        return (
            f"✅ Добавлено #{rid}: «{text}»\n"
            f"   ⏰ {schedule_description(spec)} → следующее {fmt_dt(nxt, cfg['timezone'])}"
        )

    if name == "/del":
        try:
            rid = int(rest.strip())
        except ValueError:
            return "Формат: /del N"
        reminders = load_reminders(root)
        before = len(reminders)
        reminders = [r for r in reminders if r["id"] != rid]
        if len(reminders) == before:
            return "Такого напоминания нет."
        save_reminders(root, reminders)
        return f"🗑 Удалено #{rid}."

    if name == "/clear":
        reminders = load_reminders(root)
        active = [r for r in reminders if r.get("active")]
        removed = len(reminders) - len(active)
        save_reminders(root, active)
        return f"🧹 Удалено завершённых: {removed}."

    return None


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log("TELEGRAM_BOT_TOKEN не задан — выходим.")
        sys.exit(1)

    cfg = load_config(ROOT)
    tzname = cfg.get("timezone", "Europe/Moscow")
    now = datetime.now(ZoneInfo(tzname))
    offset = load_offset(ROOT)
    log(f"Запуск, tz={tzname}, offset={offset}")

    updates = get_updates(token, offset)
    if updates:
        max_id = max(u["update_id"] for u in updates)
        offset = max_id + 1
        save_offset(ROOT, offset)

    replies = []
    for u in updates:
        msg = (u.get("message") or {}).copy()
        text = msg.get("text")
        if not text:
            continue
        sender = msg.get("from", {}).get("id")
        chat_id = msg.get("chat", {}).get("id")
        name, rest = pure_command(text)
        if name is None:
            continue
        reply = handle_command(ROOT, cfg, name, rest or "", sender, chat_id)
        if reply:
            replies.append((chat_id, reply))

    fired = fire_due(ROOT, cfg, now)
    if fired:
        for r in fired:
            replies.append((cfg["target_chat_id"], reminder_message(r, tzname)))

    sent = 0
    for chat_id, text in replies:
        if not chat_id:
            continue
        if send_message(token, chat_id, text):
            sent += 1
            log(f"Отправлено в {chat_id}: {text.splitlines()[0][:60]}")
        else:
            log(f"Не удалось отправить в {chat_id}")

    log(f"Готово: команд={len([1 for _ in updates])}, напоминаний={len(fired)}, отправлено={sent}")


if __name__ == "__main__":
    main()