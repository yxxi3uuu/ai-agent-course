# -*- coding: utf-8 -*-
"""
Agent 可呼叫的工具（tool calling）。
每次對話用 make_tools(ctx) 產生一組綁定該使用者的函式；Gemini 依函式簽名與 docstring 決定何時呼叫。
長網址不直接交給模型（模型抄寫時常錯幾個字），而是給短代碼 <<T1>>、<<S1>>，
輸出護欄再把代碼換回真正的網址；ctx.seen_urls 記錄可信的連結，用來刪除模型自己編造的網址。
"""

import html
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import db

LATEST_DIGEST = db.PROJECT_ROOT / "data" / "latest" / "digest.md"
# read_tender_page 只允許讀這些網站，避免被拿來存取任意網址
ALLOWED_HOSTS = {"web.pcc.gov.tw", "pcc-api.openfun.app"}
MAX_RESULTS = 10

# 採購網憑證缺 Subject Key Identifier，Python 3.13 的嚴格模式會拒絕；仍驗證憑證鏈，只關閉嚴格檢查
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.verify_flags &= ~ssl.VERIFY_X509_STRICT


LINK_REF_RE = re.compile(r"<<([TS]\d+)>>")


@dataclass
class ToolContext:
    user_id: str
    seen_urls: set[str] = field(default_factory=set)
    link_refs: dict[str, str] = field(default_factory=dict)   # 代碼 → 網址
    calls: list[str] = field(default_factory=list)   # 呼叫紀錄（除錯／報告展示用）

    def ref(self, url: str, kind: str) -> str:
        """網址 → 短代碼（同一網址重複出現時沿用同一代碼）"""
        self.seen_urls.add(url)
        for code, u in self.link_refs.items():
            if u == url:
                return f"<<{code}>>"
        code = f"{kind}{len(self.link_refs) + 1}"
        self.link_refs[code] = url
        return f"<<{code}>>"

    def resolve(self, text: str) -> str:
        m = LINK_REF_RE.fullmatch(text.strip())
        return self.link_refs.get(m.group(1), text) if m else text


def normalize_place(text: str) -> str:
    return (text or "").replace("台", "臺").strip()


def format_budget(budget: int | None) -> str:
    if not budget:
        return "未公開"
    if budget >= 10_000:
        return f"{budget / 10_000:,.0f} 萬元"
    return f"{budget:,} 元"


def _qualification(body: str) -> str:
    m = re.search(r"廠商資格摘要：(.+)", body or "")
    return m.group(1)[:150] if m else ""


def make_tools(ctx: ToolContext) -> list:

    def search_tenders(industry: str = "", keyword: str = "", location: str = "",
                       min_budget: int = 0, max_budget: int = 0,
                       only_open: bool = True, limit: int = 8) -> dict:
        """查詢政府標案資料庫。

        Args:
            industry: 產業分類，只能是「資訊軟體」「行銷設計」「教育訓練」「顧問研究」其中之一；不限則留空
            keyword: 標案名稱或內容的關鍵字，多個以空白分隔（全部都要命中），例如「AI 伺服器」
            location: 履約地點縣市，例如「臺中」「新北」；不限則留空
            min_budget: 最低預算（新臺幣元），0 表示不限
            max_budget: 最高預算（新臺幣元），0 表示不限
            only_open: True 只列出還沒截止投標的案子
            limit: 最多回傳幾筆（上限 10）
        """
        ctx.calls.append(f"search_tenders(industry={industry!r}, keyword={keyword!r}, "
                         f"location={location!r}, min_budget={min_budget}, max_budget={max_budget})")
        where, params = [], []
        if industry:
            where.append("industries LIKE ?")
            params.append(f"%{industry}%")
        for kw in keyword.split():
            where.append("(title LIKE ? OR keywords LIKE ? OR category LIKE ? OR body LIKE ?)")
            params += [f"%{kw}%"] * 4
        if location:
            where.append("location LIKE ?")
            params.append(f"%{normalize_place(location)}%")
        if min_budget:
            where.append("budget >= ?")
            params.append(min_budget)
        if max_budget:
            where.append("budget <= ?")
            params.append(max_budget)
        if only_open:
            where.append("(deadline IS NULL OR deadline >= ?)")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
        sql = "SELECT * FROM tenders" + (" WHERE " + " AND ".join(where) if where else "")
        with db.connect() as conn:
            total = conn.execute(sql.replace("SELECT *", "SELECT COUNT(*)"), params).fetchone()[0]
            rows = conn.execute(sql + " ORDER BY budget IS NULL, budget DESC LIMIT ?",
                                (*params, min(limit, MAX_RESULTS))).fetchall()

        results = []
        for r in rows:
            results.append({
                "標案名稱": r["title"],
                "機關": r["unit"],
                "產業": r["industries"],
                "預算": format_budget(r["budget"]),
                "截止投標": r["deadline"] or "未知（請看公告）",
                "履約地點": r["location"],
                "招標方式": r["tender_type"],
                "廠商資格摘要": _qualification(r["body"]),
                "公告連結": ctx.ref(r["url"], "T"),
            })
        return {"符合筆數": total, "回傳筆數": len(results), "標案": results}

    def search_subsidies(keyword: str = "", limit: int = 8) -> dict:
        """查詢政府補助、獎勵、津貼相關新聞。

        Args:
            keyword: 關鍵字，多個以空白分隔（全部都要命中），例如「數位轉型」「青年 創業」；留空列出最新消息
            limit: 最多回傳幾筆（上限 10）
        """
        ctx.calls.append(f"search_subsidies(keyword={keyword!r})")
        where, params = [], []
        for kw in keyword.split():
            where.append("(title LIKE ? OR summary LIKE ? OR keywords LIKE ?)")
            params += [f"%{kw}%"] * 3
        sql = "SELECT * FROM subsidies" + (" WHERE " + " AND ".join(where) if where else "")
        with db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY published DESC LIMIT ?",
                                (*params, min(limit, MAX_RESULTS))).fetchall()
        results = []
        for r in rows:
            results.append({
                "標題": r["title"], "來源": r["source"], "發布時間": r["published"],
                "摘要": r["summary"], "新聞連結": ctx.ref(r["url"], "S"),
            })
        return {"回傳筆數": len(results), "補助消息": results}

    def read_tender_page(url: str) -> dict:
        """讀取政府採購網公告頁面的完整內容，用來回答押標金、資格、履約期限等細節。

        Args:
            url: 公告連結（完整網址，或 search_tenders 回傳的代碼如 <<T1>>），只接受政府電子採購網
        """
        ctx.calls.append(f"read_tender_page({url!r})")
        url = ctx.resolve(url)
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in ALLOWED_HOSTS:
            return {"錯誤": "只能讀取政府電子採購網的公告連結"}
        ctx.seen_urls.add(url)

        with db.connect() as conn:
            row = conn.execute("SELECT body FROM tenders WHERE url = ?", (url,)).fetchone()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                page = resp.read().decode("utf-8", errors="ignore")
            page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()
        except Exception as e:
            text = f"（網頁讀取失敗：{e}）"
        return {"資料庫內容": row["body"] if row else "（資料庫沒有這筆）", "網頁內容": text[:5000]}

    def save_profile(industry: str = "", company_size: int = 0,
                     location: str = "", need: str = "") -> dict:
        """記住使用者的公司條件，之後對話會自動帶入。使用者提到任何一項就呼叫，沒提到的欄位留空不覆蓋。

        Args:
            industry: 產業，只能是「資訊軟體」「行銷設計」「教育訓練」「顧問研究」其中之一
            company_size: 公司人數
            location: 所在或想承接的縣市，例如「臺中」
            need: 需求，只能是「標案」「補助」「都要」其中之一
        """
        ctx.calls.append(f"save_profile(industry={industry!r}, company_size={company_size}, "
                         f"location={location!r}, need={need!r})")
        user = db.update_user(ctx.user_id, industry=industry, company_size=company_size or None,
                              location=normalize_place(location), need=need)
        return {"已儲存": {k: user[k] for k in ("industry", "company_size", "location", "need")}}

    def get_today_digest() -> dict:
        """取得最新一期的「政府標案＋補助快報」全文。"""
        ctx.calls.append("get_today_digest()")
        if not LATEST_DIGEST.exists():
            return {"錯誤": "目前還沒有快報"}
        text = LATEST_DIGEST.read_text(encoding="utf-8")
        ctx.seen_urls.update(re.findall(r"https?://\S+", text))
        return {"快報": text}

    return [search_tenders, search_subsidies, read_tender_page,
            save_profile, get_today_digest]
