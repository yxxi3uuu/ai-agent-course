# -*- coding: utf-8 -*-
"""
標案補助小幫手：網頁服務入口。

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

首頁顯示最新一期的政府標案與補助消息，右側為 AI Agent 對話；
服務開著時每天自動更新資料（app/scheduler.py）。
除了登入／註冊頁與 /api/auth/*，所有頁面與 API 都需要登入（app/auth.py）。
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import auth, db, export, scheduler, seed, settings
from app.agent_tools import format_budget

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed.ensure_database()   # 第一次啟動：用 seed 的公開資料建立資料庫
    scheduler.start()
    yield


app = FastAPI(title="標案補助小幫手", lifespan=lifespan)

PUBLIC_PATHS = {"/login", "/favicon.ico"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    """未登入：頁面導向 /login，API 回 401；已登入的帳號放在 request.state.account"""
    path = request.url.path
    request.state.account = auth.account_from_request(request)
    if request.state.account or path in PUBLIC_PATHS or path.startswith("/api/auth/"):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "請先登入"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def me(request: Request) -> dict:
    return request.state.account


def _page(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index():
    return _page("index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.state.account:
        return RedirectResponse("/", status_code=303)
    return _page("login.html")


@app.get("/profile", response_class=HTMLResponse)
async def profile_page():
    return _page("profile.html")


# ===========================================================================
# 帳號
# ===========================================================================

def _login_response(account: dict) -> JSONResponse:
    resp = JSONResponse({"ok": True, "account": account})
    resp.set_cookie(auth.SESSION_COOKIE, auth.create_session(account["id"]),
                    max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/register")
async def register(payload: dict):
    try:
        account = auth.register(str(payload.get("username", "")), str(payload.get("password", "")),
                                str(payload.get("display_name", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _login_response(account)


@app.post("/api/auth/login")
async def login(payload: dict):
    try:
        account = await asyncio.to_thread(auth.authenticate, str(payload.get("username", "")),
                                          str(payload.get("password", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _login_response(account)


@app.post("/api/auth/logout")
async def logout(request: Request):
    auth.delete_session(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@app.get("/api/me")
async def get_me(request: Request):
    return {**me(request), "industries": settings.industry_names()}


@app.put("/api/me")
async def put_me(request: Request, payload: dict):
    try:
        return auth.update_profile(me(request)["id"], payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/me/password")
async def put_password(request: Request, payload: dict):
    try:
        await asyncio.to_thread(auth.change_password, me(request)["id"],
                                str(payload.get("current", "")), str(payload.get("new", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 其他裝置已登出；目前這個瀏覽器重新發一組 session
    return _login_response(auth.get_account(me(request)["id"]))


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
async def export_favorites(request: Request):
    """收藏標案的截止日 → 行事曆"""
    tenders = [{**t, "budget": format_budget(t["budget"])} for t in db.favorites(me(request)["user_id"])]
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
async def get_favorites(request: Request):
    return {"favorites": [{
        "title": t["title"], "unit": t["unit"], "budget": format_budget(t["budget"]),
        "deadline": t["deadline"], "url": t["url"],
    } for t in db.favorites(me(request)["user_id"])]}


@app.post("/api/favorites")
async def add_favorite(request: Request, payload: dict):
    db.add_favorite(me(request)["user_id"], str(payload["url"]))
    return {"ok": True}


@app.delete("/api/favorites")
async def remove_favorite(request: Request, url: str):
    db.remove_favorite(me(request)["user_id"], url)
    return {"ok": True}


# ===========================================================================
# AI Agent
# ===========================================================================

@app.post("/api/agent/chat")
async def agent_chat(request: Request, payload: dict):
    """{"message": "..."}"""
    from app import agent
    user_id = me(request)["user_id"]
    trace: list[str] = []
    reply = await asyncio.to_thread(agent.chat, user_id, str(payload.get("message", "")), False, trace)
    return {"reply": reply, "trace": trace, "profile": db.get_user(user_id)}


@app.get("/api/agent/profile")
async def agent_profile(request: Request):
    return db.get_user(me(request)["user_id"])


@app.post("/api/agent/reset")
async def agent_reset(request: Request):
    """清除目前帳號的 AI 記憶與對話"""
    db.reset_user(me(request)["user_id"])
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
