import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict

DB_PATH = os.getenv("SQLITE_DB_PATH", "conversation_state.db")
DEFAULT_STATE = "IDLE"


def _get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_db_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_state (
                phone TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                data_json TEXT NOT NULL,
                stack_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(conversation_state)")}
        if "stack_json" not in cols:
            conn.execute("ALTER TABLE conversation_state ADD COLUMN stack_json TEXT NOT NULL DEFAULT '[]'")
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE conversation_state ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def load_state(phone: str, default_state: str = DEFAULT_STATE) -> Dict[str, Any]:
    conn = _get_db_conn()
    try:
        cur = conn.execute(
            "SELECT phone, state, data_json FROM conversation_state WHERE phone = ?",
            (phone,),
        )
        row = cur.fetchone()
        if not row:
            return {"state": default_state, "data": {}}
        return {"state": row["state"], "data": json.loads(row["data_json"] or "{}")}
    finally:
        conn.close()


def save_state(phone: str, state: str, data: Dict[str, Any]) -> None:
    conn = _get_db_conn()
    try:
        conn.execute(
            """
            INSERT INTO conversation_state (phone, state, data_json, stack_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                state=excluded.state,
                data_json=excluded.data_json,
                stack_json=excluded.stack_json,
                updated_at=excluded.updated_at
            """,
            (
                phone,
                state,
                json.dumps(data, ensure_ascii=False),
                "[]",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reset_state(phone: str, default_state: str = DEFAULT_STATE) -> None:
    save_state(phone, default_state, {})
