"""Журнал событий в формате JSON Lines (FR-011). Изображения в журнал не пишутся."""
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
