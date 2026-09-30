@echo off
echo =======================================================
echo          KHOI DONG HE THONG QUAN LY BIDA
echo =======================================================
echo.

echo 1. Dang khoi dong Bot Telegram...
start "Bida Telegram Bot" cmd /c "python main.py & pause"

echo 2. Dang khoi dong Web Server...
start "Bida Web App" cmd /c "python web_server.py & pause"

echo 3. Mo trang quan ly tren trinh duyet...
timeout /t 3 > nul
start http://localhost:8000

echo Hoan tat! De tat he thong, hay dong cac cua so mau den lai.
pause
