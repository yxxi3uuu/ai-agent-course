# -*- coding: utf-8 -*-
"""
LINE Messaging API webhook 與推播。

LINE Developers 後台的 Webhook URL 設成：https://<你的網址>/line/webhook
需要 .env：LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.request

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import agent, db
from app.agent_tools import personal_digest

router = APIRouter()

LINE_API = "https://api.line.me/v2/bot"
MAX_TEXT = 4900  # LINE 單則文字上限 5000

WELCOME = (
    "👋 嗨！我是標案補助小幫手\n\n"
    "我每天整理政府電子採購網的新標案和補助消息，幫你找適合的案子。\n\n"
    "你可以這樣問：\n"
    "・我是台中 10 人的軟體公司，有什麼標案？\n"
    "・最近有什麼數位轉型補助？\n"
    "・貼上採購網連結，問我資格和截止日\n\n"
    "回覆「訂閱」，每天早上收到你的專屬快報 📬"
)


def _token() -> str:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("請在 .env 設定 LINE_CHANNEL_ACCESS_TOKEN")
    return token


def line_api(path: str, payload: dict) -> None:
    req = urllib.request.Request(
        f"{LINE_API}{path}", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def text_message(text: str) -> dict:
    if len(text) > MAX_TEXT:
        text = text[:text.rfind("\n", 0, MAX_TEXT)] + "\n…（內容較長，請再問我細節）"
    return {"type": "text", "text": text}


def reply(reply_token: str, text: str) -> None:
    line_api("/message/reply", {"replyToken": reply_token, "messages": [text_message(text)]})


def push(user_id: str, text: str) -> None:
    line_api("/message/push", {"to": user_id, "messages": [text_message(text)]})


def verify_signature(body: bytes, signature: str) -> bool:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def handle_text(user_id: str, reply_token: str, text: str) -> None:
    try:
        # 顯示「輸入中…」動畫（失敗不影響回覆）
        try:
            line_api("/chat/loading/start", {"chatId": user_id, "loadingSeconds": 60})
        except Exception:
            pass

        # 固定指令不經過 LLM，省額度也比較穩
        cmd = text.strip()
        if cmd in ("訂閱", "訂閱快報"):
            db.update_user(user_id, subscribed=1)
            user = db.get_user(user_id)
            answer = "✅ 已訂閱，每天早上會收到快報！"
            if not user.get("industry"):
                answer += "\n告訴我你的產業和地區（例如：台中的軟體公司），快報會更精準。"
        elif cmd in ("取消訂閱", "停止推播"):
            db.update_user(user_id, subscribed=0)
            answer = "已取消訂閱，想再收到快報時回覆「訂閱」即可。"
        elif cmd in ("快報", "今日快報"):
            answer = personal_digest(db.get_user(user_id))
        else:
            answer = agent.chat(user_id, text)
        try:
            reply(reply_token, answer)
        except Exception as e:
            # Agent 回答太久時 reply token 可能已失效，改用 push（會計入每月推播則數）
            print(f"[line] reply 失敗，改用 push：{e}", file=sys.stderr)
            push(user_id, answer)
    except Exception as e:
        print(f"[line] 回覆失敗：{e}", file=sys.stderr)


@router.post("/line/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Line-Signature", "")):
        raise HTTPException(status_code=400, detail="invalid signature")

    for event in json.loads(body).get("events", []):
        user_id = event.get("source", {}).get("userId")
        token = event.get("replyToken")
        if not user_id or not token:
            continue
        if event["type"] == "follow":
            db.get_user(user_id)
            background.add_task(asyncio.to_thread, reply, token, WELCOME)
        elif event["type"] == "message" and event["message"].get("type") == "text":
            # 先回 200 給 LINE，再在背景呼叫 LLM
            background.add_task(asyncio.to_thread, handle_text, user_id, token, event["message"]["text"])
    return {"ok": True}
