# -*- coding: utf-8 -*-
"""
fetch_subsidy_tender.py
抓取每日政府標案、決標結果與補助新聞。
標案與補助輸出成 pipeline 可讀的 txt（每篇以 ----- 分隔，第一行為標題）；
決標結果本身就是結構化資料，直接輸出 JSON，由 ingest 寫入資料庫。

資料來源：
1. 政府標案／決標：g0v 政府採購網 API（pcc-api.openfun.app），依日期列出公告，再依產業篩選
2. 補助消息：Google 新聞 RSS 搜尋（僅供個人／課程使用，商用請改接官方公告來源）

Usage:
    python fetch_subsidy_tender.py --date 20261009 --outdir data/daily/20261009/raw
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

PCC_API = "https://pcc-api.openfun.app/api"
PCC_SITE = "https://pcc-api.openfun.app"
GNEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
USER_AGENT = "Mozilla/5.0 (subsidy-tender-digest; course project)"
SEPARATOR = "\n" + "-" * 40 + "\n"

# 從標案明細中挑出要放進知識庫的欄位（顯示名稱, 原始欄位）
TENDER_FIELDS = [
    ("機關", "機關資料:機關名稱"),
    ("案號", "採購資料:標案案號"),
    ("標的分類", "採購資料:標的分類"),
    ("預算金額", "採購資料:預算金額"),
    ("採購金額級距", "採購資料:採購金額級距"),
    ("招標方式", "招標資料:招標方式"),
    ("決標方式", "招標資料:決標方式"),
    ("公告日", "招標資料:公告日"),
    ("截止投標", "領投開標:截止投標"),
    ("開標時間", "領投開標:開標時間"),
    ("履約地點", "其他:履約地點"),
    ("履約期限", "其他:履約期限"),
    ("押標金", "領投開標:是否須繳納押標金"),
    ("押標金額度", "領投開標:是否須繳納押標金:押標金額度"),
    ("電子投標", "領投開標:是否提供電子投標"),
    ("聯絡人", "機關資料:聯絡人"),
    ("聯絡電話", "機關資料:聯絡電話"),
]


def http_get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # 429 = 請求太頻繁，等一下再試
            if e.code != 429 or attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))


def oneline(text: str) -> str:
    """去掉多餘空白，避免內文出現連續空行被 pipeline 切成兩篇"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ===========================================================================
# 政府標案
# ===========================================================================

def match_industries(title: str, category: str, industries: dict[str, dict]) -> list[str]:
    """分類代碼字首命中，或（非工程類且）標題含關鍵字"""
    tags = []
    for name, rule in industries.items():
        if any(category.startswith(c) for c in rule.get("categories", [])):
            tags.append(name)
        elif not category.startswith("工程類") and any(k in title for k in rule.get("keywords", [])):
            tags.append(name)
    return tags


def list_tenders(date: str) -> list[dict]:
    """該日公告列表；API 尚未收錄的日期會回傳 PHP 錯誤頁，視為無資料。
    連線失敗（OSError）則直接拋出，避免把「網路斷線」誤判成「沒有資料」"""
    try:
        return json.loads(http_get(f"{PCC_API}/listbydate?date={date}")).get("records") or []
    except json.JSONDecodeError:
        return []


def latest_tender_date(start: str, max_back: int = 7) -> str | None:
    """API 通常落後 2～3 天，從 start 往前找最近有資料的日期"""
    d = datetime.strptime(start, "%Y%m%d")
    for _ in range(max_back):
        if list_tenders(d.strftime("%Y%m%d")):
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return None


def fetch_detail(r: dict) -> dict:
    """抓單筆公告明細；同案可能有多筆公告，取與這筆 filename 相同的那一筆"""
    d = json.loads(http_get(f"{PCC_API}/tender?unit_id={urllib.parse.quote(r['unit_id'])}"
                            f"&job_number={urllib.parse.quote(r['job_number'])}"))
    for rec in d.get("records", []):
        if rec.get("filename") == r.get("filename"):
            return rec.get("detail", {})
    return {}


def industry_counts(records: list[dict], config: dict) -> dict[str, int]:
    """當日各產業的招標公告數（趨勢圖用；只看列表，不需抓明細）"""
    types = set(config.get("tender_types", []))
    industries = config.get("industries", {})
    counts = {name: 0 for name in industries}
    for r in records:
        brief = r.get("brief", {})
        if types and brief.get("type") not in types:
            continue
        for tag in match_industries(brief.get("title", ""), brief.get("category", ""), industries):
            counts[tag] += 1
    return counts


def fetch_tenders(date: str, config: dict, log=print, records: list[dict] | None = None) -> list[tuple[str, str]]:
    """回傳 [(標題, 內文)]"""
    records = records if records is not None else list_tenders(date)
    types = set(config.get("tender_types", []))
    industries = config.get("industries", {})
    log(f"  標案：{date} 共 {len(records)} 筆公告")

    picked = []
    for r in records:
        brief = r.get("brief", {})
        if types and brief.get("type") not in types:
            continue
        tags = match_industries(brief.get("title", ""), brief.get("category", ""), industries)
        if tags:
            picked.append((r, tags))
    log(f"  標案：符合招標類型與產業者 {len(picked)} 筆")

    max_details = config.get("max_tender_details", 80)
    articles = []
    for i, (r, tags) in enumerate(picked):
        brief = r["brief"]
        detail = {}
        if i < max_details:
            try:
                detail = fetch_detail(r)
                time.sleep(1)
            except Exception as e:
                log(f"  ⚠️ 明細抓取失敗 {r['job_number']}: {e}")

        title = f"【標案】【{r.get('unit_name', '')}】{oneline(brief.get('title'))}"
        lines = [
            f"資料類型：政府標案（{brief.get('type', '')}）",
            f"適合產業：{'、'.join(tags)}",
        ]
        for label, key in TENDER_FIELDS:
            if detail.get(key):
                lines.append(f"{label}：{oneline(detail[key])}")
        if not detail:
            lines.append(f"機關：{r.get('unit_name', '')}")
            lines.append(f"案號：{r.get('job_number', '')}")
            lines.append(f"標的分類：{brief.get('category', '')}")
        qual = detail.get("其他:廠商資格摘要")
        if qual:
            lines.append(f"廠商資格摘要：{oneline(qual)[:300]}")
        lines.append(f"公告連結：{detail.get('url') or PCC_SITE + r.get('url', '')}")
        articles.append((title, "\n".join(lines)))
    return articles


# ===========================================================================
# 決標結果（得標行情）
# ===========================================================================

def _winners(detail: dict) -> list[dict]:
    """從「投標廠商:投標廠商N:*」欄位整理出得標廠商"""
    winners = []
    for key, value in detail.items():
        m = re.fullmatch(r"投標廠商:(投標廠商\d+):是否得標", key)
        if m and value == "是":
            prefix = f"投標廠商:{m.group(1)}"
            winners.append({
                "name": detail.get(f"{prefix}:廠商名稱", ""),
                "amount": detail.get(f"{prefix}:決標金額", ""),
                "sme": detail.get(f"{prefix}:是否為中小企業", ""),
            })
    return winners


def fetch_awards(date: str, config: dict, log=print, records: list[dict] | None = None,
                 stats: dict | None = None) -> list[dict]:
    """回傳決標結果 [{title, unit, category, industries, budget, award_amount, ...}]；
    stats 傳入 dict 時會記錄 attempted / failed 筆數"""
    records = records if records is not None else list_tenders(date)
    industries = config.get("industries", {})
    hints = set(config.get("award_title_hints", []))
    for rule in industries.values():
        hints.update(rule.get("keywords", []))

    # 列表沒有標的分類，先用標題粗篩，抓明細後再用分類精確判斷產業
    candidates = [r for r in records
                  if r.get("brief", {}).get("type") == "決標公告"
                  and "契約變更" not in r["brief"].get("title", "")
                  and any(h in r["brief"].get("title", "") for h in hints)]
    max_details = config.get("max_award_details", 120)
    log(f"  決標：{date} 共 {sum(1 for r in records if r.get('brief', {}).get('type') == '決標公告')} 筆，"
        f"標題粗篩 {len(candidates)} 筆，抓取明細上限 {max_details} 筆")

    awards = []
    stats = stats if stats is not None else {}
    stats.update(attempted=0, failed=0)
    for r in candidates[:max_details]:
        stats["attempted"] += 1
        try:
            detail = fetch_detail(r)
            time.sleep(1)
        except Exception as e:
            stats["failed"] += 1
            log(f"  ⚠️ 決標明細抓取失敗 {r['job_number']}: {e}")
            continue
        category = re.sub(r"[<>]", "", detail.get("採購資料:標的分類", ""))
        title = oneline(detail.get("採購資料:標案名稱") or r["brief"].get("title"))
        tags = match_industries(title, category, industries)
        amount = detail.get("決標資料:總決標金額", "")
        if not tags or not re.search(r"\d", amount):
            continue
        awards.append({
            "url": detail.get("url") or PCC_SITE + r.get("url", ""),
            "title": title,
            "unit": detail.get("機關資料:機關名稱") or r.get("unit_name", ""),
            "category": category,
            "industries": "、".join(tags),
            "budget": detail.get("採購資料:預算金額", ""),
            "award_amount": amount,
            "floor_price": detail.get("決標資料:底價金額", ""),
            "bidders": detail.get("投標廠商:投標廠商家數", ""),
            "winners": _winners(detail),
            "method": detail.get("採購資料:決標方式", ""),
            "award_date": detail.get("決標資料:決標日期", ""),
            "location": detail.get("採購資料:履約地點", ""),
        })
    log(f"  決標：符合產業者 {len(awards)} 筆")
    return awards


# ===========================================================================
# 補助新聞
# ===========================================================================

def strip_html(text: str) -> str:
    return oneline(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))


def fetch_subsidies(config: dict, log=print) -> list[tuple[str, str]]:
    days = config.get("subsidy_days", 2)
    # 搜尋結果很雜，標題沒有這些字的多半不是補助消息
    must = config.get("subsidy_title_must", [])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    articles = []
    seen_links = set()

    for q in config.get("subsidy_queries", []):
        url = GNEWS_RSS.format(q=urllib.parse.quote(f"{q} when:{days}d"))
        try:
            root = ET.fromstring(http_get(url))
        except Exception as e:
            log(f"  ⚠️ 補助新聞搜尋失敗「{q}」: {e}")
            continue

        n = 0
        for item in root.iter("item"):
            link = item.findtext("link", "")
            if link in seen_links:
                continue
            try:
                pub = parsedate_to_datetime(item.findtext("pubDate", ""))
            except (TypeError, ValueError):
                pub = None
            if pub and pub < since:
                continue
            seen_links.add(link)
            title = oneline(item.findtext("title", ""))
            if must and not any(w in title for w in must):
                continue
            source = item.findtext("source", "")
            desc = strip_html(item.findtext("description", ""))
            lines = [
                "資料類型：補助／獎勵相關新聞",
                f"搜尋主題：{q}",
                f"來源媒體：{source}",
                f"發布時間：{pub.astimezone().strftime('%Y-%m-%d %H:%M') if pub else ''}",
                f"摘要：{desc}",
                f"新聞連結：{link}",
            ]
            articles.append((f"【補助】{title}", "\n".join(lines)))
            n += 1
        log(f"  補助新聞：「{q}」{n} 則")
        time.sleep(1)
    return articles


# ===========================================================================
# 輸出
# ===========================================================================

def write_articles(path: Path, articles: list[tuple[str, str]]) -> None:
    blocks = [f"{t}\n{b}" for t, b in articles]
    path.write_text(SEPARATOR.join(blocks) + "\n", encoding="utf-8")


def fetch_all(date: str, outdir: Path, config: dict, log=print) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    counts = {}

    records = list_tenders(date)
    tenders = fetch_tenders(date, config, log, records=records)
    if tenders:
        write_articles(outdir / f"tenders_{date}.txt", tenders)
    counts["tenders"] = len(tenders)
    (outdir / f"stats_{date}.json").write_text(
        json.dumps(industry_counts(records, config), ensure_ascii=False), encoding="utf-8")

    awards = fetch_awards(date, config, log, records=records)
    (outdir / f"awards_{date}.json").write_text(json.dumps(awards, ensure_ascii=False, indent=1), encoding="utf-8")
    counts["awards"] = len(awards)

    subsidies = fetch_subsidies(config, log)
    if subsidies:
        write_articles(outdir / f"subsidies_{date}.txt", subsidies)
    counts["subsidies"] = len(subsidies)
    return counts


def main():
    ap = argparse.ArgumentParser(description="抓取每日政府標案與補助新聞")
    ap.add_argument("--date", default=None, help="標案公告日期 YYYYMMDD（預設最近有資料的一天）")
    ap.add_argument("--outdir", default=None, help="輸出資料夾（預設 data/daily/<date>/raw）")
    ap.add_argument("--config", default=str(Path(__file__).parent.parent / "app" / "daily_config.json"))
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    date = args.date or latest_tender_date((datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))
    if not date:
        print("❌ 最近 7 天都沒有標案資料")
        return 1
    outdir = Path(args.outdir) if args.outdir else Path("data/daily") / date / "raw"
    counts = fetch_all(date, outdir, config)
    print(f"完成：標案 {counts['tenders']} 筆、決標 {counts['awards']} 筆、補助新聞 {counts['subsidies']} 則 → {outdir}")


if __name__ == "__main__":
    sys.exit(main())
