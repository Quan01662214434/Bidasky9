#!/bin/bash
# Khởi động Bot Telegram trong background
python main.py &

# Khởi động Web Server ở foreground (Render cần tiến trình này để biết app đang chạy)
# Sử dụng port do Render cung cấp qua biến môi trường $PORT
uvicorn web_server:app --host 0.0.0.0 --port ${PORT:-8000}
