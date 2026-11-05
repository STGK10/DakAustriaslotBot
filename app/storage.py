import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


class Store:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS seen_slots (
                category TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(category, slot_id)
            )
        """)
        self.db.commit()

    def mark_if_new(self, category: str, slot_id: str, payload: dict) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO seen_slots(category, slot_id, first_seen, payload) VALUES(?,?,?,?)",
            (category, slot_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        self.db.commit()
        return cur.rowcount == 1
