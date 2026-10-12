# -*- coding: utf-8 -*-
"""
帳號：註冊、登入、登出、修改資料與密碼。

- 密碼以 PBKDF2-SHA256（隨機 salt、20 萬次迭代）雜湊後儲存，不存明碼
- 登入後發一組隨機 session token 放在 HttpOnly cookie；資料庫只存 token 的 SHA-256
- 每個帳號對應 Agent 的使用者 ID「acct-<id>」，個人資料中的產業、人數、地區、需求
  就是 AI 小幫手記住的公司條件（users 表），兩邊同步
"""

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app import db, settings

SESSION_COOKIE = "session"
SESSION_DAYS = 7
PBKDF2_ITERATIONS = 200_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")

# 登入失敗次數限制（記憶體內）：同一帳號 10 分鐘內最多錯 5 次
_failed: dict[str, list[float]] = {}
MAX_FAILS, FAIL_WINDOW = 5, 600


# ===========================================================================
# 密碼
# ===========================================================================

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def check_password_rule(password: str) -> None:
    if len(password) < 8:
        raise ValueError("密碼至少 8 個字元")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise ValueError("密碼需同時包含英文字母與數字")


# ===========================================================================
# 帳號
# ===========================================================================

def agent_user_id(account_id: int) -> str:
    return f"acct-{account_id}"


def register(username: str, password: str, display_name: str) -> dict:
    username = username.strip()
    display_name = display_name.strip() or username
    if not USERNAME_RE.match(username):
        raise ValueError("帳號需為 3～30 個英文、數字或 _ . -")
    if len(display_name) > 20:
        raise ValueError("顯示名稱最多 20 字")
    check_password_rule(password)
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE username = ? COLLATE NOCASE", (username,)).fetchone():
            raise ValueError("這個帳號已經有人使用")
        cur = conn.execute(
            "INSERT INTO accounts (username, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), display_name, db.now()))
        account_id = cur.lastrowid
    db.get_user(agent_user_id(account_id))   # 建立對應的 AI 記憶
    return get_account(account_id)


def authenticate(username: str, password: str) -> dict:
    key = username.strip().lower()
    now = time.time()
    recent = [t for t in _failed.get(key, []) if now - t < FAIL_WINDOW]
    if len(recent) >= MAX_FAILS:
        raise ValueError("登入失敗次數過多，請 10 分鐘後再試")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        _failed[key] = recent + [now]
        raise ValueError("帳號或密碼錯誤")
    _failed.pop(key, None)
    return get_account(row["id"])


def get_account(account_id: int) -> dict:
    """帳號基本資料＋AI 記住的公司條件"""
    with db.connect() as conn:
        row = conn.execute("SELECT id, username, display_name, email, company_name, created_at FROM accounts "
                           "WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise KeyError(account_id)
    account = dict(row)
    account["user_id"] = agent_user_id(account_id)
    profile = db.get_user(account["user_id"])
    for k in ("industry", "company_size", "location", "need"):
        account[k] = profile.get(k)
    return account


def update_profile(account_id: int, data: dict) -> dict:
    display_name = str(data.get("display_name", "")).strip()
    email = str(data.get("email", "")).strip()
    company_name = str(data.get("company_name", "")).strip()
    if not display_name or len(display_name) > 20:
        raise ValueError("顯示名稱需為 1～20 字")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Email 格式不正確")
    if len(company_name) > 50:
        raise ValueError("公司名稱最多 50 字")
    size = data.get("company_size")
    if size in ("", None):
        size = None
    else:
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise ValueError("公司人數請填數字")
        if not 1 <= size <= 1_000_000:
            raise ValueError("公司人數不合理")
    need = data.get("need") or None
    if need not in (None, "標案", "補助", "都要"):
        raise ValueError("需求只能是標案、補助或都要")
    industry = data.get("industry") or None
    if industry and industry not in settings.industry_names():
        raise ValueError("請從清單選擇產業")
    db.get_user(agent_user_id(account_id))   # 確保 AI 記憶那一列存在

    with db.connect() as conn:
        conn.execute("UPDATE accounts SET display_name = ?, email = ?, company_name = ? WHERE id = ?",
                     (display_name, email or None, company_name or None, account_id))
        # 公司條件直接寫入 AI 記憶；留空代表清除
        conn.execute("UPDATE users SET industry = ?, company_size = ?, location = ?, need = ?, updated_at = ? "
                     "WHERE user_id = ?",
                     (industry, size,
                      (str(data.get("location") or "").replace("台", "臺").strip() or None),
                      need, db.now(), agent_user_id(account_id)))
    return get_account(account_id)


def change_password(account_id: int, current: str, new: str) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT password_hash FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row or not verify_password(current, row["password_hash"]):
        raise ValueError("目前的密碼不正確")
    check_password_rule(new)
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(new), account_id))
        # 改密碼後讓其他裝置的登入失效
        conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))


# ===========================================================================
# Session
# ===========================================================================

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=SESSION_DAYS)
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        conn.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     (_token_hash(token), account_id, db.now(), expires.strftime("%Y-%m-%d %H:%M:%S")))
    return token


def delete_session(token: str | None) -> None:
    if token:
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def account_from_request(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT account_id FROM sessions WHERE token_hash = ? AND expires_at >= ?",
                           (_token_hash(token), db.now())).fetchone()
    if not row:
        return None
    try:
        return get_account(row["account_id"])
    except KeyError:
        return None


def require_account(request: Request) -> dict:
    """FastAPI 相依：未登入時回 401"""
    account = account_from_request(request)
    if not account:
        raise HTTPException(401, "請先登入")
    return account


# ===========================================================================
# 終端機工具：忘記密碼時重設（只有能操作這台電腦的人才能執行）
#     python -m app.auth reset-password <帳號>
# ===========================================================================

def _cli() -> int:
    import getpass
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "reset-password":
        print("用法：python -m app.auth reset-password <帳號>")
        return 1
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE username = ? COLLATE NOCASE", (sys.argv[2],)).fetchone()
    if not row:
        print("找不到這個帳號")
        return 1
    new = getpass.getpass("新密碼：")
    if new != getpass.getpass("再輸入一次："):
        print("兩次輸入不一樣")
        return 1
    try:
        check_password_rule(new)
    except ValueError as e:
        print(e)
        return 1
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(new), row["id"]))
        conn.execute("DELETE FROM sessions WHERE account_id = ?", (row["id"],))
    print("✅ 密碼已重設，請用新密碼登入")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
