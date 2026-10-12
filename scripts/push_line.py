# -*- coding: utf-8 -*-
"""
push_line.py
每日快報推播。

預設：對每位訂閱者（在 LINE 回覆過「訂閱」的人）依其產業／地區推播個人化快報。
--broadcast：把 data/latest/digest.md 廣播給所有好友（不分產業）。

需要 .env 的 LINE_CHANNEL_ACCESS_TOKEN。注意 LINE 免費方案每月推播則數有限。

Usage:
    python push_line.py              # 個人化推播給訂閱者
    python push_line.py --dry-run    # 只印出內容不發送
    python push_line.py --broadcast
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import db  # noqa: E402
from app.agent_tools import personal_digest  # noqa: E402
from app.line_bot import line_api, push, text_message  # noqa: E402  (會一併載入 .env)

DEFAULT_DIGEST = PROJECT_ROOT / "data" / "latest" / "digest.md"


def to_line_text(md: str) -> str:
    """Markdown → LINE 純文字：去掉 #、>，長連結省略（完整內容請問 Agent）"""
    lines = []
    for line in md.splitlines():
        if line.strip().startswith("http"):
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^>\s*", "💬 ", line)
        lines.append(line)
    return "\n".join(lines).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="LINE 每日快報推播")
    ap.add_argument("--broadcast", action="store_true", help="廣播 digest.md 給所有好友")
    ap.add_argument("--digest", default=str(DEFAULT_DIGEST))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.broadcast:
        text = to_line_text(Path(args.digest).read_text(encoding="utf-8"))
        if args.dry_run:
            print(text)
            return 0
        line_api("/message/broadcast", {"messages": [text_message(text)]})
        print("✅ 已廣播")
        return 0

    users = db.subscribed_users()
    print(f"訂閱者 {len(users)} 人")
    sent = 0
    for user in users:
        text = personal_digest(user)
        if args.dry_run:
            print(f"--- {user['user_id']} ---\n{text}\n")
            continue
        try:
            push(user["user_id"], text)
            sent += 1
        except Exception as e:
            print(f"⚠️ 推播失敗 {user['user_id']}: {e}")
    if not args.dry_run:
        print(f"✅ 已推播 {sent}/{len(users)} 人")
    return 0


if __name__ == "__main__":
    sys.exit(main())
