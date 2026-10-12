# -*- coding: utf-8 -*-
"""
標案補助小幫手：網頁服務入口。

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

首頁顯示最新一期的政府標案與補助消息，右側為 AI Agent 對話；
服務開著時每天自動更新資料（app/scheduler.py）。
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from app import db, export, scheduler, settings
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


@app.get("/api/trends")
async def get_trends(days: int = 30):
    """每日各產業標案數、預算總額走勢，以及各產業決標統計"""
    return db.trends(days)


@app.get("/api/update")
async def update_status():
    return scheduler.info()


@app.post("/api/update")
async def update_now():
    """手動觸發更新（約 3～5 分鐘，背景執行）"""
    return {"started": scheduler.update_now(), **scheduler.info()}


# ===========================================================================
# 匯出
# ===========================================================================

def _download(content: bytes, filename: str, media_type: str) -> Response:
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/export/tenders.xlsx")
async def export_tenders(industry: str = "", q: str = ""):
    """目前篩選條件下的最新標案 → Excel"""
    tenders = [t for t in db.latest_tenders(industry)
               if not q or any(q in (t[k] or "") for k in ("title", "unit", "location"))]
    name = f"標案_{db.latest_fetched_date() or ''}{'_' + industry if industry else ''}.xlsx"
    return _download(export.tenders_xlsx(tenders), name,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/export/favorites.ics")
async def export_favorites(user_id: str):
    """收藏標案的截止日 → 行事曆"""
    tenders = [{**t, "budget": format_budget(t["budget"])} for t in db.favorites(user_id)]
    return _download(export.deadlines_ics(tenders), "標案截止日.ics", "text/calendar; charset=utf-8")


@app.get("/api/export/tender.ics")
async def export_one(url: str):
    """單一標案的截止日 → 行事曆"""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM tenders WHERE url = ?", (url,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到這個標案")
    t = dict(row)
    return _download(export.deadlines_ics([{**t, "budget": format_budget(t["budget"])}]),
                     "標案截止日.ics", "text/calendar; charset=utf-8")


# ===========================================================================
# 自訂產業
# ===========================================================================

@app.get("/api/industries")
async def get_industries():
    return {
        "industries": settings.load_config().get("industries", {}),
        "catalog": [{"code": c, "name": n} for c, n in settings.CATEGORY_CATALOG],
    }


@app.put("/api/industries")
async def put_industries(payload: dict):
    """儲存產業設定；下次更新資料時生效"""
    try:
        industries = settings.save_industries(payload.get("industries"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "industries": industries}


# ===========================================================================
# 收藏
# ===========================================================================

@app.get("/api/favorites")
async def get_favorites(user_id: str):
    return {"favorites": [{
        "title": t["title"], "unit": t["unit"], "budget": format_budget(t["budget"]),
        "deadline": t["deadline"], "url": t["url"],
    } for t in db.favorites(user_id)]}


@app.post("/api/favorites")
async def add_favorite(payload: dict):
    db.add_favorite(str(payload["user_id"]), str(payload["url"]))
    return {"ok": True}


@app.delete("/api/favorites")
async def remove_favorite(user_id: str, url: str):
    db.remove_favorite(user_id, url)
    return {"ok": True}


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


_eval_state = {"running": False, "log": []}


def _run_eval() -> None:
    from app import evaluate
    try:
        evaluate.run(log=lambda m: _eval_state["log"].append(m))
    except Exception as e:
        _eval_state["log"].append(f"❌ 評估失敗：{e}")
    finally:
        _eval_state["running"] = False


@app.post("/api/eval")
async def start_eval():
    """在背景跑一次自動評估（約 21 題，5～10 分鐘）"""
    if not _eval_state["running"]:
        _eval_state.update(running=True, log=[])
        threading.Thread(target=_run_eval, daemon=True).start()
    return {"running": True}


@app.get("/api/eval")
async def eval_status():
    from app import evaluate
    report = evaluate.latest()
    return {"running": _eval_state["running"], "log": _eval_state["log"][-3:], "latest": report}


@app.get("/api/agent/usage")
async def agent_usage():
    """每日使用人數、對話數與 token 用量（監控用）"""
    return {"days": db.usage_summary()}
