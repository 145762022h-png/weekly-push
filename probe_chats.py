#!/usr/bin/env python3
"""List Feishu group chats the bot is in, to find the chat_id."""

import os, json, sys, urllib.request

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

if not APP_ID or not APP_SECRET:
    print("Usage: FEISHU_APP_ID=xxx FEISHU_APP_SECRET=xxx python3 probe_chats.py")
    sys.exit(1)

# Get token
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode(),
    headers={"Content-Type": "application/json; charset=utf-8"}
)
with urllib.request.urlopen(req, timeout=15) as resp:
    token_data = json.loads(resp.read())
if token_data.get("code") != 0:
    print(f"Auth failed: {token_data}")
    sys.exit(1)
token = token_data["tenant_access_token"]

# List chats
req2 = urllib.request.Request(
    "https://open.feishu.cn/open-apis/im/v1/chats?page_size=50",
    headers={"Authorization": f"Bearer {token}"}
)
with urllib.request.urlopen(req2, timeout=15) as resp:
    chats = json.loads(resp.read())

if chats.get("code") != 0:
    print(f"List chats failed: code={chats.get('code')} msg={chats.get('msg')}")
    print("Make sure the app has 'im:chat' permission and the bot is in at least one group.")
    sys.exit(1)

items = chats.get("data", {}).get("items", [])
if not items:
    print("No group chats found. Add the bot to a Feishu group first, then re-run.")
    sys.exit(1)

print(f"Found {len(items)} chat(s):\n")
for c in items:
    print(f"  Name: {c.get('name', '(unnamed)')}")
    print(f"  ID:   {c['chat_id']}")
    print()
