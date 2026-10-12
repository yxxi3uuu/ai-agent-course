# -*- coding: utf-8 -*-
"""
標案補助小幫手：網頁服務入口。

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

首頁顯示最新一期的政府標案與補助消息，右側為 AI Agent 對話；
服務開著時每天自動更新資料（app/scheduler.py）。
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import db, scheduler
from app.agent_tools import format_budget

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield


app = FastAPI(title="標案補助小幫手", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ===========================================================================
# 每日最新資訊
# ===========================================================================

@app.get("/api/digest")
async def digest(industry: str = ""):
    """最新一期未截止標案（可依產業篩選）＋補助消息"""
    tenders = db.latest_tenders(industry)
    return {
        "date": db.latest_fetched_date(),
        "tenders": [{
            "title": t["title"], "unit": t["unit"], "industries": t["industries"],
            "budget": format_budget(t["budget"]), "deadline": t["deadline"],
            "location": t["location"], "url": t["url"],
        } for t in tenders],
        "subsidies": [{
            "title": s["title"], "source": s["source"], "published": s["published"], "url": s["url"],
        } for s in db.latest_subsidies()],
    }


@app.get("/api/update")
async def update_status():
    return scheduler.info()


@app.post("/api/update")
async def update_now():
    """手動觸發更新（約 3～5 分鐘，背景執行）"""
    return {"started": scheduler.update_now(), **scheduler.info()}


# ===========================================================================
# AI Agent
# ===========================================================================

@app.post("/api/agent/chat")
async def agent_chat(payload: dict):
    """{"user_id": "...", "message": "..."}"""
    from app import agent
    user_id = str(payload.get("user_id") or "web-test")
    trace: list[str] = []
    reply = await asyncio.to_thread(agent.chat, user_id, str(payload.get("message", "")), False, trace)
    return {"reply": reply, "trace": trace, "profile": db.get_user(user_id)}


@app.get("/api/agent/profile")
async def agent_profile(user_id: str):
    return db.get_user(user_id)


@app.post("/api/agent/reset")
async def agent_reset(payload: dict):
    """清除某位使用者的記憶與對話"""
    db.reset_user(str(payload.get("user_id") or ""))
    return {"ok": True}


@app.get("/api/agent/usage")
async def agent_usage():
    """每日使用人數、對話數與 token 用量（監控用）"""
    return {"days": db.usage_summary()}
