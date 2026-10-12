# -*- coding: utf-8 -*-
"""
往前補資料，讓「得標行情」與「趨勢圖」一開始就有足夠樣本。

    python -m app.backfill_awards --days 14                 # 決標結果（每天約 120 筆明細，約 2～3 分鐘）＋每日標案數
    python -m app.backfill_awards --days 30 --counts-only   # 只補每日標案數（每天 1 次請求，很快）
已補過的日期會跳過，可以放心重跑。
"""

import argparse
import json
import sys
from datetime import datetime, timedelta

from app import db
from app.ingest import ingest_awards
from app.run_daily import DAILY_CONFIG_PATH, DATA_DIR
from fetch_subsidy_tender import fetch_awards, industry_counts, list_tenders


def backfill_counts(days: int, log=print) -> int:
    config = json.loads(DAILY_CONFIG_PATH.read_text(encoding="utf-8"))
    n = 0
    day = datetime.now() - timedelta(days=1)
    for _ in range(days):
        date = day.strftime("%Y%m%d")
        day -= timedelta(days=1)
        with db.connect() as conn:
            if conn.execute("SELECT 1 FROM daily_stats WHERE date = ?", (date,)).fetchone():
                continue
        try:
            records = list_tenders(date)
        except OSError as e:
            log(f"❌ 無法連線到採購網 API：{e}")
            break
        if records:
            db.save_daily_counts(date, industry_counts(records, config))
            n += 1
            log(f"{date} 已補每日標案數")
    return n


def backfill(days: int, log=print) -> int:
    config = json.loads(DAILY_CONFIG_PATH.read_text(encoding="utf-8"))
    out_dir = DATA_DIR / "awards_backfill"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    day = datetime.now() - timedelta(days=1)
    for _ in range(days):
        date = day.strftime("%Y%m%d")
        day -= timedelta(days=1)
        if db.get_meta(f"awards_backfilled_{date}"):
            log(f"{date} 已補過，略過")
            continue
        try:
            records = list_tenders(date)
        except OSError as e:
            log(f"❌ 無法連線到採購網 API，停止補資料：{e}")
            break
        if not records:
            log(f"{date} 沒有資料（假日或 API 尚未收錄）")
            continue
        db.save_daily_counts(date, industry_counts(records, config))
        stats = {}
        awards = fetch_awards(date, config, log, records=records, stats=stats)
        path = out_dir / f"awards_{date}.json"
        path.write_text(json.dumps(awards, ensure_ascii=False, indent=1), encoding="utf-8")
        n = ingest_awards(path, date)
        total += n
        # 失敗太多（多半是網路問題）就不標記完成，下次重跑會再補
        if stats["failed"] <= stats["attempted"] * 0.1:
            db.set_meta(f"awards_backfilled_{date}", str(n))
            log(f"{date} 寫入 {n} 筆，累計 {total} 筆")
        else:
            log(f"{date} 寫入 {n} 筆，但有 {stats['failed']}/{stats['attempted']} 筆失敗，下次重跑會再補")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="往前補抓決標結果")
    ap.add_argument("--days", type=int, default=14, help="往前幾天（含假日）")
    ap.add_argument("--counts-only", action="store_true", help="只補每日標案數（趨勢圖用）")
    args = ap.parse_args()
    if args.counts_only:
        print(f"完成：補了 {backfill_counts(args.days)} 天的每日標案數")
    else:
        print(f"完成：共寫入 {backfill(args.days)} 筆決標資料")
    return 0


if __name__ == "__main__":
    sys.exit(main())
