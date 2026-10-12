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
CREATE TABLE IF NOT EXISTS awards (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    unit          TEXT,
    industries    TEXT,
    category      TEXT,
    budget        INTEGER,
    award_amount  INTEGER,
    floor_price   INTEGER,
    bidders       INTEGER,
    winners       TEXT,          -- 得標廠商，以「、」分隔
    winner_sme    TEXT,          -- 是否為中小企業，與 winners 對應
    method        TEXT,
    award_date    TEXT,          -- 'YYYY-MM-DD'
    location      TEXT,
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
CREATE TABLE IF NOT EXISTS favorites (
    user_id       TEXT NOT NULL,
    url           TEXT NOT NULL,     -- 對應 tenders.url
    created_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, url)
);
CREATE TABLE IF NOT EXISTS daily_stats (
    date          TEXT NOT NULL,     -- 公告日期 YYYYMMDD
    industry      TEXT NOT NULL,
    tender_count  INTEGER,           -- 當日招標公告數
    budget_total  INTEGER,           -- 有抓明細的標案預算合計（補資料的日期為 NULL）
    PRIMARY KEY (date, industry)
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
    """清除記憶與對話（收藏保留）"""
    with connect() as conn:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


# ===========================================================================
# 收藏
# ===========================================================================

def add_favorite(user_id: str, url: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO favorites (user_id, url, created_at) VALUES (?, ?, ?)",
                     (user_id, url, now()))


def remove_favorite(user_id: str, url: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND url = ?", (user_id, url))


def favorites(user_id: str) -> list[dict]:
    """收藏的標案，依截止日由近到遠（截止日未知的排最後）"""
    with connect() as conn:
        rows = conn.execute("""
            SELECT t.* FROM favorites f JOIN tenders t ON t.url = f.url
            WHERE f.user_id = ?
            ORDER BY t.deadline IS NULL, t.deadline""", (user_id,)).fetchall()
    return [dict(r) for r in rows]


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


# ===========================================================================
# 得標行情（統計由程式計算，避免 LLM 自己算錯）
# ===========================================================================

def award_stats(industry: str = "", keyword: str = "", location: str = "",
                min_budget: int = 0, max_budget: int = 0, examples: int = 5) -> dict:
    where, params = ["award_amount > 0"], []
    if industry:
        where.append("industries LIKE ?")
        params.append(f"%{industry}%")
    for kw in keyword.split():
        where.append("(title LIKE ? OR category LIKE ?)")
        params += [f"%{kw}%"] * 2
    if location:
        where.append("location LIKE ?")
        params.append(f"%{location.replace('台', '臺')}%")
    if min_budget:
        where.append("budget >= ?")
        params.append(min_budget)
    if max_budget:
        where.append("budget <= ?")
        params.append(max_budget)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM awards WHERE " + " AND ".join(where) + " ORDER BY award_date DESC", params)]
    if not rows:
        return {"樣本數": 0}

    amounts = sorted(r["award_amount"] for r in rows)
    ratios = [r["award_amount"] / r["budget"] for r in rows if r["budget"]]
    bidders = [r["bidders"] for r in rows if r["bidders"]]
    winners: dict[str, dict] = {}
    sme_flags = []
    for r in rows:
        names = [n for n in (r["winners"] or "").split("、") if n]
        smes = (r["winner_sme"] or "").split("、")
        for i, name in enumerate(names):
            w = winners.setdefault(name, {"得標件數": 0, "得標總額": 0})
            w["得標件數"] += 1
            w["得標總額"] += r["award_amount"] // max(len(names), 1)
            if i < len(smes) and smes[i]:
                sme_flags.append(smes[i] == "是")
    top = sorted(winners.items(), key=lambda kv: (-kv[1]["得標件數"], -kv[1]["得標總額"]))[:5]

    return {
        "樣本數": len(rows),
        "資料期間": f"{rows[-1]['award_date']} ～ {rows[0]['award_date']}",
        "決標金額中位數": amounts[len(amounts) // 2],
        "決標金額範圍": [amounts[0], amounts[-1]],
        "平均決標價／預算": round(sum(ratios) / len(ratios), 3) if ratios else None,
        "平均投標家數": round(sum(bidders) / len(bidders), 1) if bidders else None,
        "只有 1 家投標的比例": round(sum(1 for b in bidders if b == 1) / len(bidders), 2) if bidders else None,
        "得標者為中小企業比例": round(sum(sme_flags) / len(sme_flags), 2) if sme_flags else None,
        "得標最多的廠商": [{"廠商": n, **v} for n, v in top],
        "近期案例": rows[:examples],
    }


# ===========================================================================
# 趨勢
# ===========================================================================

def save_daily_counts(date: str, counts: dict[str, int]) -> None:
    """寫入當日各產業標案數，預算總額由 tenders 表計算（沒有明細時為 NULL）"""
    with connect() as conn:
        for industry, n in counts.items():
            total = conn.execute(
                "SELECT SUM(budget) FROM tenders WHERE fetched_date = ? AND industries LIKE ?",
                (date, f"%{industry}%")).fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO daily_stats (date, industry, tender_count, budget_total)"
                         " VALUES (?, ?, ?, ?)", (date, industry, n, total))


def trends(days: int = 30) -> dict:
    with connect() as conn:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM daily_stats ORDER BY date DESC LIMIT ?", (days,))][::-1]
        rows = conn.execute("SELECT * FROM daily_stats WHERE date >= ? ORDER BY date",
                            (dates[0] if dates else "",)).fetchall()
        award_rows = conn.execute("""
            SELECT industries, COUNT(*) AS n, SUM(award_amount) AS total,
                   AVG(CASE WHEN budget > 0 THEN award_amount * 1.0 / budget END) AS ratio,
                   AVG(bidders) AS bidders
            FROM awards GROUP BY industries""").fetchall()

    series: dict[str, dict] = {}
    for r in rows:
        s = series.setdefault(r["industry"], {"counts": {}, "budgets": {}})
        s["counts"][r["date"]] = r["tender_count"]
        s["budgets"][r["date"]] = r["budget_total"]

    # 決標統計：跨產業的案子拆到各產業
    award_by_industry: dict[str, dict] = {}
    for r in award_rows:
        for ind in (r["industries"] or "").split("、"):
            if not ind:
                continue
            a = award_by_industry.setdefault(ind, {"n": 0, "total": 0, "ratio_sum": 0.0, "ratio_n": 0, "bid_sum": 0.0})
            a["n"] += r["n"]
            a["total"] += r["total"] or 0
            if r["ratio"] is not None:
                a["ratio_sum"] += r["ratio"] * r["n"]
                a["ratio_n"] += r["n"]
            a["bid_sum"] += (r["bidders"] or 0) * r["n"]

    return {
        "dates": dates,
        "industries": {
            name: {"counts": [s["counts"].get(d) for d in dates],
                   "budgets": [s["budgets"].get(d) for d in dates]}
            for name, s in series.items()
        },
        "awards": {
            name: {"count": a["n"], "total": a["total"],
                   "avg_ratio": round(a["ratio_sum"] / a["ratio_n"], 3) if a["ratio_n"] else None,
                   "avg_bidders": round(a["bid_sum"] / a["n"], 1) if a["n"] else None}
            for name, a in award_by_industry.items()
        },
    }
