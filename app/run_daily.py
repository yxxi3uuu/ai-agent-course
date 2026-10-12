# -*- coding: utf-8 -*-
"""
每日標案／補助快報：抓資料 → 跑既有 pipeline（關鍵字、過濾、去重、清理、整理）→ 產生快報。

給排程器（Windows 工作排程器 / cron / GitHub Actions）呼叫：
    python -m app.run_daily
    python -m app.run_daily --date 20261009
    python -m app.run_daily --skip-fetch      # 只重跑 pipeline，不重新抓資料

輸出（data/daily/<date>/）：
    raw/        原始抓取結果
    final/      清理後的 txt + xlsx
    digest.md   今日快報（LINE 推播 / 報告展示用）
並寫進 data/agent.db 給 Agent 查詢。
另外會複製一份到 data/latest/，方便固定路徑上傳。
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.pipeline import (
    PROJECT_ROOT, JobStatus,
    _step1_extract, _step2_keywords, _step3_filter, _step4_dedup,
    _step5_clean, _step6_fix_headers,
)
from fetch_subsidy_tender import fetch_all, latest_tender_date
from app.ingest import ingest, parse_final

DAILY_CONFIG_PATH = Path(__file__).parent / "daily_config.json"
DATA_DIR = PROJECT_ROOT / "data"
DIGEST_TOP_N = 10


def run_pipeline_sync(job: JobStatus, log=print) -> dict:
    out = job.output_dir
    stats = {"extracted": _step1_extract(job, job.input_dir, out)}
    log(f"  提取 {stats['extracted']} 篇")
    stats["keywords"] = _step2_keywords(job, out)
    kept, removed = _step3_filter(job, out)
    stats["filtered_out"] = removed
    log(f"  過濾：保留 {kept}、刪除 {removed}")
    kept, removed = _step4_dedup(job, out)
    stats["dedup_removed"] = removed
    log(f"  去重：保留 {kept}、刪除 {removed}")
    _step5_clean(job, out)
    _step6_fix_headers(job, out)
    return stats


def budget_of(item: dict) -> int:
    """「4,984,000元」→ 4984000；沒有預算資料的排最後"""
    digits = re.sub(r"\D", "", item["fields"].get("預算金額", ""))
    return int(digits) if digits else -1


def build_digest(date: str, items: list[dict]) -> str:
    tenders = sorted((i for i in items if i["title"].startswith("【標案】")), key=budget_of, reverse=True)
    subsidies = [i for i in items if i["title"].startswith("【補助】")]
    d = datetime.strptime(date, "%Y%m%d")

    lines = [f"# 📢 政府標案＋補助快報 {d:%Y/%m/%d}", ""]
    lines.append(f"今日整理：標案 {len(tenders)} 件、補助消息 {len(subsidies)} 則（已過濾、去重）")
    lines.append("")

    lines.append("## 🏛️ 新標案")
    by_industry: dict[str, list[dict]] = {}
    for t in tenders:
        for ind in t["fields"].get("適合產業", "其他").split("、"):
            by_industry.setdefault(ind, []).append(t)
    for ind, ts in by_industry.items():
        lines.append(f"### {ind}（{len(ts)} 件）")
        for t in ts[:DIGEST_TOP_N]:
            f = t["fields"]
            name = t["title"].removeprefix("【標案】")
            extra = "｜".join(x for x in [
                f.get("預算金額") and f"預算 {f['預算金額']}",
                f.get("截止投標") and f"截止 {f['截止投標']}",
            ] if x)
            lines.append(f"- {name}" + (f"（{extra}）" if extra else ""))
            if f.get("公告連結"):
                lines.append(f"  {f['公告連結']}")
        if len(ts) > DIGEST_TOP_N:
            lines.append(f"- …另有 {len(ts) - DIGEST_TOP_N} 件，可直接問 Agent")
        lines.append("")

    lines.append("## 💰 補助／獎勵消息")
    for s in subsidies[:DIGEST_TOP_N]:
        f = s["fields"]
        lines.append(f"- {s['title'].removeprefix('【補助】')}")
        if f.get("新聞連結"):
            lines.append(f"  {f['新聞連結']}")
    if len(subsidies) > DIGEST_TOP_N:
        lines.append(f"- …另有 {len(subsidies) - DIGEST_TOP_N} 則")
    lines.append("")
    lines.append("> 想知道自己公司適合哪一案？直接問：「我是 10 人的軟體公司，可以投什麼標、申請什麼補助？」")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日政府標案／補助快報")
    ap.add_argument("--date", default=None, help="標案公告日期 YYYYMMDD（預設最近有資料的一天）")
    ap.add_argument("--skip-fetch", action="store_true", help="不重新抓資料，直接用 raw/ 重跑")
    ap.add_argument("--config", default=str(DAILY_CONFIG_PATH))
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if not args.date:
        # 當天資料還沒收齊，從昨天往前找
        args.date = latest_tender_date((datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))
        if not args.date:
            print("❌ 最近 7 天都沒有標案資料")
            return 1
    day_dir = DATA_DIR / "daily" / args.date
    raw_dir = day_dir / "raw"
    work_dir = day_dir / "work"

    if not args.skip_fetch:
        print(f"🌐 抓取資料（{args.date}）...")
        counts = fetch_all(args.date, raw_dir, config)
        print(f"  標案 {counts['tenders']} 筆、補助新聞 {counts['subsidies']} 則")

    if not raw_dir.exists() or not any(raw_dir.glob("*.txt")):
        print("❌ 沒有可處理的資料")
        return 1

    print("⚙️ 執行 pipeline...")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    job = JobStatus(
        job_id=f"daily-{args.date}", total_files=0, selected_steps=[1, 2, 3, 4, 5, 6],
        input_dir=raw_dir, output_dir=work_dir, config=config,
    )
    run_pipeline_sync(job)

    final_dir = day_dir / "final"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.copytree(work_dir / "final", final_dir)

    n_tender, n_subsidy = ingest(final_dir, args.date)
    print(f"🗄️ 寫入資料庫：標案 {n_tender}、補助 {n_subsidy}")

    items = parse_final(final_dir)
    digest = build_digest(args.date, items)
    (day_dir / "digest.md").write_text(digest, encoding="utf-8")

    latest = DATA_DIR / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(final_dir, latest)
    shutil.copy2(day_dir / "digest.md", latest / "digest.md")

    print(f"🎉 完成：{len(items)} 篇 → {final_dir}")
    print(f"📢 快報：{day_dir / 'digest.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
