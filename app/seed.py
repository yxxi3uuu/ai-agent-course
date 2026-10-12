# -*- coding: utf-8 -*-
"""
初始資料庫（seed/seed.db）：只含公開資料（標案、決標、補助、每日標案數），不含任何帳號或個人資料。

- 第一次啟動時若 data/agent.db 不存在，會自動用 seed 建立，馬上就有得標行情與趨勢圖可看
- 產生／更新 seed：python -m app.seed export
"""

import shutil
import sqlite3
import sys

from app import db

SEED_PATH = db.PROJECT_ROOT / "seed" / "seed.db"
PUBLIC_TABLES = ["tenders", "awards", "subsidies", "daily_stats"]


def export_seed() -> dict:
    """把目前資料庫的公開資料表匯出成 seed/seed.db"""
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEED_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript(db.SCHEMA)          # 完整結構（個人資料表保持空白）
    out.execute("ATTACH DATABASE ? AS src", (str(db.DB_PATH),))
    counts = {}
    for table in PUBLIC_TABLES:
        out.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
        counts[table] = out.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out.commit()
    out.execute("DETACH DATABASE src")
    out.execute("VACUUM")
    out.close()
    tmp.replace(SEED_PATH)
    return counts


def ensure_database() -> bool:
    """資料庫還不存在時用 seed 建立；回傳是否有用到 seed"""
    if db.DB_PATH.exists() or not SEED_PATH.exists():
        return False
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEED_PATH, db.DB_PATH)
    print(f"[seed] 已用初始資料建立 {db.DB_PATH}")
    return True


if __name__ == "__main__":
    if sys.argv[1:] != ["export"]:
        print("用法：python -m app.seed export")
        raise SystemExit(1)
    counts = export_seed()
    print(f"✅ 已匯出 {SEED_PATH}：" + "、".join(f"{k} {v} 筆" for k, v in counts.items()))
