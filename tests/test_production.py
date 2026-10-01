import os
import time
import asyncio
import httpx
import json
import sys
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = "https://bidasky9.onrender.com"
WEBHOOK_URL = f"{BASE_URL}/telegram-webhook/{BOT_TOKEN}"
OWNER_ID = int(os.getenv("OWNER_ID", "7496977545"))

async def test_webhook_concurrency():
    print("--- KIỂM THỬ: Double-click Webhook ---")
    update_id = 123456789
    
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": OWNER_ID, "is_bot": False, "first_name": "Test"},
            "chat": {"id": OWNER_ID, "type": "private"},
            "date": int(time.time()),
            "text": "/start"
        }
    }
    
    async with httpx.AsyncClient() as client:
        # Gửi 2 request đồng thời cùng 1 update_id
        reqs = [
            client.post(WEBHOOK_URL, json=payload, timeout=30),
            client.post(WEBHOOK_URL, json=payload, timeout=30)
        ]
        resps = await asyncio.gather(*reqs, return_exceptions=True)
        
        print(f"Kết quả gửi đồng thời: {resps}")
        for r in resps:
            if isinstance(r, httpx.Response):
                print(f"Status: {r.status_code}, Body: {r.text}")
            else:
                print(f"Exception: {r}")

async def test_auth_enforcement():
    print("\n--- KIỂM THỬ: Strict Auth WebApp ---")
    # Gửi auth sai hash
    fake_init_data = f"user=%7B%22id%22%3A{OWNER_ID}%7D"
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/api/v2/salary?month=2026-10", headers={
            "Authorization": f"Bearer {fake_init_data}"
        })
        print(f"Kết quả Bypass: HTTP {r.status_code} - {r.text}")

if __name__ == "__main__":
    asyncio.run(test_webhook_concurrency())
    asyncio.run(test_auth_enforcement())
