# -*- coding: utf-8 -*-
"""
產業設定：讀寫 app/daily_config.json 的 industries，給網頁「自訂產業」與 Agent 提示詞使用。
"""

import json
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "daily_config.json"
CATEGORY_RE = re.compile(r"^(勞務類|財物類|工程類)\d{1,4}$")

# 常用標的分類（政府採購標的分類代碼，字首比對：「勞務類84」涵蓋 841～849）
CATEGORY_CATALOG = [
    ("勞務類84", "電腦及相關服務（軟體開發、系統維運、資料處理）"),
    ("勞務類85", "研究與發展服務"),
    ("勞務類86", "法律、會計、工程、管理顧問等專業服務"),
    ("勞務類865", "管理顧問服務"),
    ("勞務類866", "與管理顧問有關之服務"),
    ("勞務類867", "建築、工程服務"),
    ("勞務類871", "廣告服務"),
    ("勞務類874", "建築物清潔服務"),
    ("勞務類879", "其他商業服務"),
    ("勞務類88", "附帶於農林漁牧、製造業之服務"),
    ("勞務類92", "教育服務"),
    ("勞務類93", "醫療及社會服務"),
    ("勞務類94", "污水、垃圾處理及環保服務"),
    ("勞務類96", "娛樂、文化、體育服務（活動、展演）"),
    ("勞務類97", "其他服務"),
    ("勞務類64", "旅館、餐飲服務"),
    ("勞務類71", "陸地運輸服務"),
    ("財物類452", "電腦及其零件與配件"),
    ("財物類47", "收音機、電視、通訊器材"),
    ("財物類48", "醫療器材、精密儀器"),
    ("財物類32", "紙製品、印刷品"),
    ("財物類38", "傢具及其他製品"),
    ("財物類35", "化學製品、藥品"),
    ("財物類49", "運輸設備（車輛、船舶）"),
    ("工程類51", "營建工程"),
    ("工程類5177", "室內裝潢工程"),
]


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def industry_names() -> list[str]:
    return list(load_config().get("industries", {}))


def validate_industries(industries: dict) -> dict:
    """檢查並整理使用者送來的產業設定；有問題時丟出 ValueError（訊息給使用者看）"""
    if not isinstance(industries, dict) or not industries:
        raise ValueError("至少要保留一個產業")
    if len(industries) > 12:
        raise ValueError("產業最多 12 個")
    cleaned = {}
    for name, rule in industries.items():
        name = str(name).strip()
        if not name or len(name) > 10 or "、" in name:
            raise ValueError(f"產業名稱「{name}」需為 1～10 字，且不能含「、」")
        categories = [str(c).strip() for c in rule.get("categories", []) if str(c).strip()]
        keywords = [str(k).strip() for k in rule.get("keywords", []) if str(k).strip()]
        bad = [c for c in categories if not CATEGORY_RE.match(c)]
        if bad:
            raise ValueError(f"「{name}」的標的分類格式不對：{'、'.join(bad)}（例：勞務類84、財物類452）")
        if not categories and not keywords:
            raise ValueError(f"「{name}」至少要有一個標的分類或關鍵字")
        if any(len(k) < 2 for k in keywords):
            raise ValueError(f"「{name}」的關鍵字至少 2 個字，避免抓到太多不相關的案子")
        cleaned[name] = {"categories": list(dict.fromkeys(categories)), "keywords": list(dict.fromkeys(keywords))}
    return cleaned


def save_industries(industries: dict) -> dict:
    cleaned = validate_industries(industries)
    config = load_config()
    config["industries"] = cleaned
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cleaned
