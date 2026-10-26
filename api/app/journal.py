"""Журнал событий (FR-011): тот же файл JSON Lines, что пишет воркер."""
import json
import os
import time

from . import config

JOURNAL_FILE = os.path.join(config.DATA_DIR, "logs", "journal.jsonl")


def write(event: str, **fields) -> None:
    os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
    record = {"ts": round(time.time(), 3), "event": event, **fields}
    with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read() -> list[dict]:
    if not os.path.exists(JOURNAL_FILE):
        return []
    records = []
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # строка, обрезанная при аварийной остановке
    return records
