# 文件提取服務

一個本地端的 Web 工具，把 PDF / Word / Excel / TXT 丟進去，自動幫你：
1. 提取文章內容
2. 用 jieba 產生關鍵字
3. 過濾不相關的文章（可自訂規則）
4. 去除重複文章
5. 清理正文雜訊
6. 整理成乾淨的 txt + xlsx 輸出

---

## 快速開始

### 1. 安裝 Python

需要 Python 3.10 以上。確認方式：

```bash
python --version
```

### 2. 安裝依賴套件

在專案資料夾內執行：

```bash
pip install -r requirements.txt
```

### 3. 啟動服務

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 4. 開始使用

瀏覽器打開 **http://localhost:8000**

---

## 使用方式

1. 把檔案（PDF / Word / Excel / TXT）拖到上傳區，或點選檔案
2. 勾選你要跑的步驟（預設全部打勾）
3. 選匯出格式（TXT / Excel / 兩個都要）
4. 按「開始處理」，等進度跑完
5. 下載 ZIP 或直接在網頁預覽結果

---

## 頁面說明

| 網址 | 功能 |
|------|------|
| `http://localhost:8000` | 主頁 — 上傳檔案、處理、下載 |
| `http://localhost:8000/settings` | 設定過濾關鍵字（決定哪些文章要刪掉） |
| `http://localhost:8000/stopwords` | 設定停用詞（哪些詞不要出現在關鍵字裡） |

---

## 標案補助小幫手（AI Agent 期末專題）

把上面的處理流程接上每日排程，做成 LINE 上的 AI Agent：每天整理政府標案與補助消息，
依使用者的產業與地區推播，並用 Gemini（tool calling）回答查詢。
完整設計、安裝步驟（Gemini key、LINE、ngrok、排程）與報告對照請看 [docs/agent_設計.md](docs/agent_設計.md)。

```bash
cp .env.example .env                   # 填入 GEMINI_API_KEY 與 LINE 金鑰
python -m app.run_daily                # 抓資料 → 6 步驟清理 → 寫入 data/agent.db
python -m app.agent                    # 在終端機與 Agent 對話測試
python scripts/push_line.py --dry-run  # 預覽每位訂閱者的推播內容
```

| 輸出 | 用途 |
|------|------|
| `data/daily/<日期>/final/` | 清理後 txt + xlsx |
| `data/daily/<日期>/digest.md` | 當日快報（依產業分類、預算排序） |
| `data/agent.db` | 標案、補助、使用者記憶、對話紀錄、token 用量 |

| 網址 | 功能 |
|------|------|
| `POST /line/webhook` | LINE webhook |
| `POST /api/agent/chat` | 不經 LINE 的測試入口 `{"user_id": "...", "message": "..."}` |
| `GET /api/agent/usage` | 每日使用人數與 token 用量 |

要追蹤的產業、補助搜尋關鍵字、過濾詞和停用詞都在 `app/daily_config.json` 設定。

---

## 處理步驟說明

| 步驟 | 做什麼 |
|------|--------|
| 1. 提取文章 | 從各種格式的檔案中抓出文章內容 |
| 2. 產生關鍵字 | 用 jieba 分詞自動幫每篇文章標關鍵字 |
| 3. 過濾不相關 | 根據你設定的關鍵字規則，刪掉不相關的文章 |
| 4. 去重複 | 跨檔比對，刪掉標題相同或相似的重複文章 |
| 5. 清理正文 | 移除 URL、email、版權聲明等雜訊 |
| 6. 整理格式 | 修正表頭、重新編號、同步產生 Excel |

步驟 1 和 2 是基本流程，3~6 可以依需求自行勾選。

---

## 資料夾結構

```
doc-extractor/
├── app/                    # Web 應用程式
│   ├── main.py             # FastAPI 路由
│   ├── pipeline.py         # 處理流程邏輯
│   ├── config_manager.py   # 設定管理
│   ├── config.json         # 過濾關鍵字 + 停用詞設定
│   ├── run_daily.py        # 每日排程入口
│   ├── ingest.py           # 清理結果寫入 SQLite
│   ├── db.py               # SQLite（標案、補助、使用者記憶、用量）
│   ├── agent.py            # Gemini Agent＋輸入／輸出護欄
│   ├── agent_tools.py      # Agent 工具（查標案、查補助、讀公告…）
│   ├── line_bot.py         # LINE webhook 與推播
│   ├── daily_config.json   # 每日快報設定（產業、搜尋字、過濾詞）
│   ├── history.json        # 處理歷史紀錄（自動產生）
│   ├── static/             # 前端頁面
│   │   ├── index.html
│   │   ├── settings.html
│   │   └── stopwords.html
│   └── temp/               # 暫存（自動產生，處理中的檔案）
├── scripts/                # 核心處理腳本
│   ├── extract_keyword_context.py  # PDF 提取核心
│   ├── add_keywords_fast.py        # jieba 關鍵字
│   ├── filter_articles.py          # 文章過濾
│   ├── dedup_articles.py           # 去重複
│   ├── clean.py                    # 正文清理
│   ├── fix_headers.py              # 表頭修正
│   ├── fetch_subsidy_tender.py     # 抓政府標案＋補助新聞
│   ├── push_line.py                # LINE 個人化推播
│   ├── daily_task.bat              # 給工作排程器呼叫
│   └── count_pdf_chars.py          # 輔助：字數統計
├── docs/agent_設計.md       # Agent 設計、安裝與期末報告對照
├── data/                   # 每日輸出（不進 git）
├── requirements.txt
├── README.md
└── .gitignore
```

---

## 依賴套件

- **fastapi** + **uvicorn** — Web 服務
- **sse-starlette** — 即時進度推送
- **python-multipart** — 檔案上傳
- **PyMuPDF** — PDF 文字提取
- **python-docx** — Word 文件讀取
- **jieba** — 中文分詞 / 關鍵字
- **openpyxl** — Excel 讀寫
- **tqdm** — 命令列進度條

---

## 常見問題

**Q: 啟動後瀏覽器打不開？**
確認用的是 `http://localhost:8000`，不是 `http://0.0.0.0:8000`。

**Q: PyMuPDF 安裝失敗？**
不要鎖版本，直接 `pip install PyMuPDF` 讓它自動找相容的預編譯版本。

**Q: 處理完下載是空的？**
確認你上傳的 PDF 裡面有文字內容（掃描圖片的 PDF 沒辦法提取）。
