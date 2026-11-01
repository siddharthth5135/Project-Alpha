"""Print Telegram chat ids from getUpdates (never prints the bot token)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env", override=True)

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    print("NO_TOKEN in .env")
    raise SystemExit(1)

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
data = resp.json()
if not data.get("ok"):
    print("API_FAIL", data.get("description"))
    raise SystemExit(1)

results = data.get("result") or []
print(f"updates={len(results)}")
seen: set[int] = set()
for update in results:
    for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
        obj = update.get(key) or {}
        chat = obj.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
        print(f"chat_id={cid} type={chat.get('type')} name={label}")

if not seen:
    print(
        "NO_CHATS_YET: message the bot privately, OR add bot as admin to your channel, "
        "then re-run."
    )
    raise SystemExit(2)
