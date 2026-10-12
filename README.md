# 🤖 標案補助小幫手

每天自動整理**政府電子採購網的新標案**與**補助／獎勵消息**，打開網頁就能看到當天最新資訊；
右側的 **AI Agent**（Gemini）會記住你的公司條件，從資料庫中精準篩選、推薦適合的案子，並能讀取公告回答資格與押標金。

> 中央大學 115-1「AI Agent」期末專題。設計說明與課綱對照見 [docs/agent_設計.md](docs/agent_設計.md)。

---

## 快速開始

### 1. 安裝

需要 Python 3.10 以上。

```bash
pip install -r requirements.txt
```

### 2. 設定 Gemini API key

1. 到 https://aistudio.google.com/apikey 建立 API key（免費）
2. 把 `.env.example` 複製成 `.env`，填入 `GEMINI_API_KEY`

### 3. 啟動

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

瀏覽器打開 **http://localhost:8000**

第一次啟動時資料庫是空的，服務會自動在背景抓取資料（約 3～5 分鐘），完成後頁面會自動顯示。

---

## 功能

| 區塊 | 說明 |
|------|------|
| 📅 每日最新資訊 | 最新一期未截止標案（依預算排序，可依產業篩選、搜尋）、3 天內截止提醒、補助消息 |
| 💬 AI 小幫手 | 記住產業／人數／地區、依條件查標案與補助、讀公告回答資格與押標金 |
| 🔄 自動更新 | 服務開著時每天 `DAILY_UPDATE_TIME`（預設 07:30）自動更新；也可按「立即更新」 |
| 📊 系統用量 | 每日使用者數、對話數、token 用量 |

## 每日資料流程

```
g0v 政府採購網 API ─┐
Google 新聞 RSS ────┴→ fetch_subsidy_tender.py
                      → pipeline：jieba 關鍵字 → 過濾 → 去重 → 清理 → 整理
                      → ingest.py 寫入 SQLite（data/agent.db）
                      → 首頁顯示／Agent 查詢
```

也可以手動執行一次更新：

```bash
python -m app.run_daily
```

## 設定

| 檔案 | 內容 |
|------|------|
| `.env` | Gemini API key、模型、每日更新時間 |
| `app/daily_config.json` | 追蹤的產業與標的分類、補助搜尋關鍵字、過濾詞、停用詞 |

## 資料夾結構

```
├── app/
│   ├── main.py              # FastAPI 入口（首頁、每日資訊 API、Agent API）
│   ├── scheduler.py         # 每日自動更新
│   ├── run_daily.py         # 抓取 → 清理 → 入庫
│   ├── pipeline.py          # 6 步驟清理流程
│   ├── ingest.py            # 清理結果寫入 SQLite
│   ├── db.py                # SQLite（標案、補助、使用者記憶、用量）
│   ├── agent.py             # Gemini Agent＋輸入／輸出護欄
│   ├── agent_tools.py       # Agent 工具（查標案、查補助、讀公告、記住條件）
│   ├── daily_config.json    # 每日資料設定
│   └── static/index.html    # 首頁
├── scripts/
│   ├── fetch_subsidy_tender.py  # 抓政府標案＋補助新聞
│   ├── add_keywords_fast.py     # jieba 關鍵字
│   ├── filter_articles.py       # 過濾
│   ├── dedup_articles.py        # 去重複
│   └── clean.py                 # 正文清理
├── docs/agent_設計.md
├── data/                    # 每日資料與資料庫（自動產生，不進 git）
├── .env.example
└── requirements.txt
```

## 常見問題

**Q: 頁面顯示「上次更新失敗」？**
g0v 採購網 API 通常晚 2～3 天收錄，或暫時連不上；服務會在 1 小時後自動重試，也可以按「立即更新」。

**Q: 小幫手回覆「系統忙碌中」？**
Gemini 免費額度每分鐘次數有限，等一分鐘再試；程式已會自動改用備援模型。

**Q: 資料來源可以商用嗎？**
Google 新聞 RSS 僅限個人使用；正式商用請改接各部會官方公告來源。
