#!/bin/bash
# Bot Telegram chạy TRONG web server (webhook mode)
# Không cần process riêng nữa

# Khởi động Web Server (bao gồm bot webhook)
# Render cần tiến trình này để biết app đang chạy
uvicorn web_server:app --host 0.0.0.0 --port ${PORT:-8000}
