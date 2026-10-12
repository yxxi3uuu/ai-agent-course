# -*- coding: utf-8 -*-
"""
每日自動更新：網頁服務開著時，每天 DAILY_UPDATE_TIME（預設 07:30）後自動抓取並整理一次。
服務啟動時若今天還沒更新（或資料庫是空的），也會立刻在背景更新。
"""

import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from app import db
from app.run_daily import run_daily

UPDATE_TIME = os.environ.get("DAILY_UPDATE_TIME", "07:30")
CHECK_INTERVAL = 600           # 每 10 分鐘檢查一次
RETRY_AFTER = timedelta(hours=1)  # 失敗後隔 1 小時再試，避免一直打外部 API

_lock = threading.Lock()
status = {"running": False, "started_at": None, "log": []}


def _log(msg: str) -> None:
    print(f"[daily] {msg}")
    status["log"] = (status["log"] + [msg])[-30:]


def needs_update(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if not db.latest_fetched_date():
        return True
    last_attempt = db.get_meta("last_attempt_at")
    if last_attempt and now - datetime.fromisoformat(last_attempt) < RETRY_AFTER:
        return False
    hh, mm = map(int, UPDATE_TIME.split(":"))
    if (now.hour, now.minute) < (hh, mm):
        return False
    return db.get_meta("last_update_day") != now.strftime("%Y-%m-%d")


def _run() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        status.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"), log=[])
        db.set_meta("last_attempt_at", datetime.now().isoformat(timespec="seconds"))
        result = run_daily(log=_log)
        if result["ok"]:
            db.set_meta("last_update_day", datetime.now().strftime("%Y-%m-%d"))
            db.set_meta("last_update_at", datetime.now().isoformat(timespec="seconds"))
            db.set_meta("last_update_error", "")
        else:
            db.set_meta("last_update_error", result["message"])
            _log(f"❌ {result['message']}")
    except Exception as e:
        db.set_meta("last_update_error", str(e))
        _log(f"❌ 更新失敗：{e}")
        traceback.print_exc()
    finally:
        status["running"] = False
        _lock.release()


def update_now() -> bool:
    """在背景立即更新；已在更新中回傳 False"""
    if status["running"]:
        return False
    threading.Thread(target=_run, daemon=True).start()
    return True


def _loop() -> None:
    while True:
        try:
            if needs_update():
                _run()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="daily-update").start()


def info() -> dict:
    return {
        "running": status["running"],
        "log": status["log"][-5:],
        "update_time": UPDATE_TIME,
        "last_update_at": db.get_meta("last_update_at"),
        "last_error": db.get_meta("last_update_error") or None,
        "data_date": db.latest_fetched_date(),
    }
