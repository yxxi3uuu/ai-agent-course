# 🤖 標案補助小幫手

每天自動整理**政府電子採購網的新標案**、**決標結果（得標行情）**與**補助／獎勵消息**，打開網頁就能看到當天最新資訊；
右側的 **AI Agent**（Gemini）會記住你的公司條件，從資料庫中精準篩選、推薦適合的案子，分析「這類案子通常幾折得標、誰常得標」，並能讀取公告回答資格與押標金。

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

瀏覽器打開 **http://localhost:8000**，第一次會看到登入頁，先按「註冊」建立帳號。

第一次啟動時會用專案附的初始資料（`seed/seed.db`：標案、197 筆決標結果、補助、每日標案數）建立資料庫，
馬上就能看到得標行情與趨勢圖；之後服務會在背景抓取最新資料。初始資料只含公開資料，不含任何帳號或個人資料。

想補抓更多過去的決標資料（選用）：

```bash
python -m app.backfill_awards --days 30 --counts-only
python -m app.backfill_awards --days 30
```

---

## 功能

| 區塊 | 說明 |
|------|------|
| 👤 帳號 | 註冊／登入／登出；個人資料頁可改顯示名稱、Email、公司條件與密碼。公司條件（產業、人數、地區、需求）就是 AI 記住的內容，兩邊同步 |
| 📅 每日最新資訊 | 最新一期未截止標案（依預算排序，可依產業篩選、搜尋）、3 天內截止提醒、補助消息 |
| ⭐ 我的追蹤 | 收藏標案、截止倒數（3 天內紅字）、一鍵加入行事曆（.ics，截止前一天提醒） |
| ⬇️ 匯出 | 目前篩選的標案匯出 Excel |
| 📈 趨勢與得標行情 | 各產業每日新標案數走勢、各產業平均決標價占預算比例（滑鼠移上去看細節，也可切表格） |
| 💬 AI 小幫手 | 記住產業／人數／地區、依條件查標案與補助、分析得標行情、讀公告回答資格與押標金、查詢收藏 |
| ⚙️ 產業設定 | 在網頁上新增／修改追蹤的產業（標的分類＋關鍵字），下次更新生效 |
| 🧪 自動評估 | 21 題測試題，標準答案即時由資料庫計算，統計通過率、工具選擇、護欄、編造連結 |
| 🔄 自動更新 | 服務開著時每天 `DAILY_UPDATE_TIME`（預設 07:30）自動更新；也可按「立即更新」 |
| 📊 系統用量 | 每日使用者數、對話數、token 用量 |

## 每日資料流程

```
g0v 政府採購網 API ─┐
Google 新聞 RSS ────┴→ fetch_subsidy_tender.py
                      ├ 標案、補助 → pipeline：jieba 關鍵字 → 過濾 → 去重 → 清理 → 整理
                      ├ 決標結果（結構化 JSON）
                      └ 各產業每日標案數
                      → ingest.py 寫入 SQLite（data/agent.db）
                      → 首頁顯示／Agent 查詢
```

也可以手動執行：

```bash
python -m app.run_daily      # 更新一次資料
python -m app.evaluate       # 跑一次自動評估，報告存在 data/eval/
python -m app.seed export    # 用目前資料庫的公開資料更新 seed/seed.db
```

過去的評估報告存放在 [docs/eval/](docs/eval/)。

## 設定

| 檔案 | 內容 |
|------|------|
| `.env` | Gemini API key、模型、每日更新時間 |
| `app/daily_config.json` | 追蹤的產業與標的分類（也可在網頁「⚙️ 產業設定」修改）、補助搜尋關鍵字、過濾詞、停用詞 |

## 資料夾結構

```
├── app/
│   ├── main.py              # FastAPI 入口（頁面、登入檢查、每日資訊 API、Agent API）
│   ├── auth.py              # 帳號：註冊、登入、session、密碼雜湊
│   ├── scheduler.py         # 每日自動更新
│   ├── run_daily.py         # 抓取 → 清理 → 入庫
│   ├── pipeline.py          # 6 步驟清理流程
│   ├── ingest.py            # 清理結果寫入 SQLite
│   ├── db.py                # SQLite（標案、補助、使用者記憶、用量）
│   ├── agent.py             # Gemini Agent＋輸入／輸出護欄
│   ├── agent_tools.py       # Agent 工具（查標案、查補助、讀公告、得標行情、收藏、記住條件）
│   ├── backfill_awards.py   # 補抓過去的決標結果與每日標案數
│   ├── evaluate.py          # 自動評估（21 題）
│   ├── export.py            # 匯出 Excel／行事曆
│   ├── seed.py              # 初始資料匯出／首次啟動建立資料庫
│   ├── settings.py          # 產業設定讀寫與檢查
│   ├── daily_config.json    # 每日資料設定
│   └── static/             # 首頁 index.html、登入 login.html、個人資料 profile.html
├── scripts/
│   ├── fetch_subsidy_tender.py  # 抓政府標案、決標結果、補助新聞
│   ├── add_keywords_fast.py     # jieba 關鍵字
│   ├── filter_articles.py       # 過濾
│   ├── dedup_articles.py        # 去重複
│   └── clean.py                 # 正文清理
├── seed/seed.db             # 初始資料（只含公開資料）
├── docs/
│   ├── agent_設計.md
│   └── eval/                # 自動評估報告
├── data/                    # 每日資料與資料庫（自動產生，不進 git）
├── .env.example
└── requirements.txt
```

## 常見問題

**Q: 忘記密碼？**
帳號只存在這台電腦的資料庫（`data/agent.db`），沒有寄信重設功能。在執行服務的電腦上開終端機重設：

```bash
python -m app.auth reset-password 你的帳號
```

**Q: 頁面顯示「上次更新失敗」？**
g0v 採購網 API 通常晚 2～3 天收錄，或暫時連不上；服務會在 1 小時後自動重試，也可以按「立即更新」。

**Q: 小幫手回覆「系統忙碌中」？**
Gemini 免費額度每分鐘次數有限，等一分鐘再試；程式已會自動改用備援模型。

**Q: 資料來源可以商用嗎？**
Google 新聞 RSS 僅限個人使用；正式商用請改接各部會官方公告來源。
