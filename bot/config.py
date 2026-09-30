"""
Bot Telegram Quản Lý Quán Bida
Cấu hình từ biến môi trường và database.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Cấu hình ứng dụng từ .env"""

    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OWNER_TELEGRAM_ID: int = int(os.getenv("OWNER_TELEGRAM_ID", "0"))

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bida.db")
    PHOTO_STORAGE_PATH: str = os.getenv("PHOTO_STORAGE_PATH", "data/photos")
    BACKUP_PATH: str = os.getenv("BACKUP_PATH", "data/backups")

    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")
    BUSINESS_DAY_START: str = os.getenv("BUSINESS_DAY_START", "06:00")
    DEFAULT_FUND: int = int(os.getenv("DEFAULT_FUND", "200000"))
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls):
        """Kiểm tra cấu hình bắt buộc."""
        errors = []
        if not cls.BOT_TOKEN or cls.BOT_TOKEN == "your_bot_token_here":
            errors.append("BOT_TOKEN chưa được cấu hình trong .env")
        if cls.OWNER_TELEGRAM_ID == 0:
            errors.append("OWNER_TELEGRAM_ID chưa được cấu hình trong .env")
        if errors:
            raise ValueError("Lỗi cấu hình:\n" + "\n".join(errors))

    @classmethod
    def ensure_dirs(cls):
        """Tạo thư mục cần thiết."""
        Path(cls.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(cls.PHOTO_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
        Path(cls.BACKUP_PATH).mkdir(parents=True, exist_ok=True)
