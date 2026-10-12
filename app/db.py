# -*- coding: utf-8 -*-
"""
SQLite 資料庫：標案、補助、使用者設定（Memory）、對話紀錄、token 用量。
檔案位置 data/agent.db（不進 git）。
"""

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    unit          TEXT,
    industries    TEXT,          -- 以「、」分隔
    category      TEXT,
    tender_type   TEXT,
    budget        INTEGER,       -- 元；未公開為 NULL
    deadline      TEXT,          -- 'YYYY-MM-DD HH:MM'（西元）
    location      TEXT,
    announce_date TEXT,
    keywords      TEXT,
    body          TEXT,
    fetched_date  TEXT
);
CREATE TABLE IF NOT EXISTS subsidies (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT,
    published     TEXT,
    summary       TEXT,
    keywords      TEXT,
    fetched_date  TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    industry      TEXT,
    company_size  INTEGER,
    location      TEXT,
    need          TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    role          TEXT NOT NULL,   -- user / model
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT,
    kind          TEXT,            -- agent / input_guard / output_guard
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key           TEXT PRIMARY KEY,
    value         TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def roc_to_iso(text: str) -> str | None:
    """民國日期「115/10/20 17:00」→「2026-10-20 17:00」"""
    m = re.search(r"(\d{2,3})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text or "")
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    return f"{int(y) + 1911:04d}-{int(mo):02d}-{int(d):02d} {int(hh or 23):02d}:{mm or '59'}"


def parse_amount(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


# ===========================================================================
# 使用者（Memory）
# ===========================================================================

def get_user(user_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return dict(row)
        conn.execute("INSERT INTO users (user_id, updated_at) VALUES (?, ?)", (user_id, now()))
        return {"user_id": user_id, "industry": None, "company_size": None,
                "location": None, "need": None}


def update_user(user_id: str, **fields) -> dict:
    """只更新有值的欄位（沒提到的不覆蓋）"""
    get_user(user_id)
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with connect() as conn:
            conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE user_id = ?",
                         (*fields.values(), now(), user_id))
    return get_user(user_id)


def reset_user(user_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


# ===========================================================================
# 對話紀錄與用量
# ===========================================================================

def add_message(user_id: str, role: str, content: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                     (user_id, role, content, now()))


def recent_messages(user_id: str, limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def log_usage(user_id: str, kind: str, model: str, input_tokens: int, output_tokens: int) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO usage (user_id, kind, model, input_tokens, output_tokens, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)",
                     (user_id, kind, model, input_tokens or 0, output_tokens or 0, now()))


def usage_summary(days: int = 14) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("""
            SELECT substr(created_at, 1, 10) AS day,
                   COUNT(DISTINCT user_id)   AS users,
                   SUM(kind = 'agent')       AS chats,
                   SUM(input_tokens)         AS input_tokens,
                   SUM(output_tokens)        AS output_tokens
            FROM usage GROUP BY day ORDER BY day DESC LIMIT ?""", (days,)).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# 系統狀態（每日更新紀錄）
# ===========================================================================

def get_meta(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


# ===========================================================================
# 首頁：最新一期資料
# ===========================================================================

def latest_fetched_date() -> str | None:
    with connect() as conn:
        return conn.execute("SELECT MAX(fetched_date) FROM tenders").fetchone()[0]


def latest_tenders(industry: str = "") -> list[dict]:
    """最新一期、尚未截止的標案，依預算由大到小"""
    date = latest_fetched_date()
    if not date:
        return []
    sql = ("SELECT * FROM tenders WHERE fetched_date = ? "
           "AND (deadline IS NULL OR deadline >= ?)")
    params: list = [date, datetime.now().strftime("%Y-%m-%d %H:%M")]
    if industry:
        sql += " AND industries LIKE ?"
        params.append(f"%{industry}%")
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY budget IS NULL, budget DESC", params).fetchall()
    return [dict(r) for r in rows]


def latest_subsidies(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM subsidies ORDER BY published DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
