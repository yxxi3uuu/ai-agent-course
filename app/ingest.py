# -*- coding: utf-8 -*-
"""
把 pipeline 整理好的 final/*.txt 寫進 SQLite，給 Agent 的 tools 查詢。

    python -m app.ingest data/daily/20261008/final
"""

import re
import sys
from pathlib import Path

from app import db


def parse_final(final_dir: Path) -> list[dict]:
    """把 final/*.txt 解析回 [{title, keywords, fields}]"""
    items = []
    for f in sorted(final_dir.glob("*.txt")):
        for block in f.read_text(encoding="utf-8").split("-" * 40):
            m = re.search(r"■\s*\[\d+\]\s*(.+)", block)
            if not m:
                continue
            kw = re.search(r"\[關鍵字\]\s*(.+)", block)
            fields = dict(re.findall(r"^([^：\n]{2,8})：(.+)$", block, re.MULTILINE))
            body_start = kw.end() if kw else m.end()
            items.append({
                "title": m.group(1).strip(),
                "keywords": kw.group(1).strip() if kw else "",
                "fields": fields,
                "body": block[body_start:].strip(),
            })
    return items


def ingest(final_dir: Path, fetched_date: str) -> tuple[int, int]:
    """回傳 (標案數, 補助數)；同一連結重複出現時以新資料覆蓋"""
    items = parse_final(final_dir)
    n_tender = n_subsidy = 0
    with db.connect() as conn:
        for it in items:
            f = it["fields"]
            if it["title"].startswith("【標案】"):
                url = f.get("公告連結")
                if not url:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO tenders
                    (url, title, unit, industries, category, tender_type, budget, deadline,
                     location, announce_date, keywords, body, fetched_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    url,
                    re.sub(r"^【標案】【[^】]*】", "", it["title"]),
                    f.get("機關"),
                    f.get("適合產業"),
                    f.get("標的分類"),
                    re.sub(r"^政府標案（|）$", "", f.get("資料類型", "")),
                    db.parse_amount(f.get("預算金額")),
                    db.roc_to_iso(f.get("截止投標")),
                    f.get("履約地點"),
                    db.roc_to_iso(f.get("公告日")),
                    it["keywords"],
                    it["body"],
                    fetched_date,
                ))
                n_tender += 1
            elif it["title"].startswith("【補助】"):
                url = f.get("新聞連結")
                if not url:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO subsidies
                    (url, title, source, published, summary, keywords, fetched_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""", (
                    url,
                    it["title"].removeprefix("【補助】"),
                    f.get("來源媒體"),
                    f.get("發布時間"),
                    f.get("摘要"),
                    it["keywords"],
                    fetched_date,
                ))
                n_subsidy += 1
    return n_tender, n_subsidy


if __name__ == "__main__":
    d = Path(sys.argv[1])
    print(ingest(d, d.parent.name))
