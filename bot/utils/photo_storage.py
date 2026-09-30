"""
Lưu trữ ảnh bền vững.
Tải ảnh từ Telegram, lưu vào thư mục cục bộ với metadata.
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime

import pytz
from telegram import Bot

from bot.config import Config

logger = logging.getLogger(__name__)

VN_TZ = pytz.timezone(Config.TIMEZONE)


async def save_photo_from_telegram(
    bot: Bot,
    file_id: str,
    file_unique_id: str,
    category: str,  # checkin/debt/transaction/receipt/report
    reference_id: str,
    uploader_id: int,
) -> str | None:
    """
    Tải ảnh từ Telegram và lưu cục bộ.
    Trả về đường dẫn local hoặc None nếu lỗi.
    """
    try:
        photo_dir = Path(Config.PHOTO_STORAGE_PATH) / category
        photo_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
        filename = f"{category}_{reference_id}_{timestamp}_{file_unique_id}"
        
        # Tải file
        tg_file = await bot.get_file(file_id)
        ext = ".jpg"  # Telegram photos luôn là JPEG
        if tg_file.file_path and "." in tg_file.file_path:
            ext = "." + tg_file.file_path.split(".")[-1]
        
        local_path = str(photo_dir / f"{filename}{ext}")
        await tg_file.download_to_drive(local_path)
        
        # Lưu metadata
        meta = {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "category": category,
            "reference_id": reference_id,
            "uploader_id": uploader_id,
            "downloaded_at": datetime.now(VN_TZ).isoformat(),
            "local_path": local_path,
        }
        meta_path = str(photo_dir / f"{filename}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
        logger.info("Đã lưu ảnh: %s", local_path)
        return local_path
        
    except Exception as e:
        logger.error("Lỗi lưu ảnh: %s", e)
        return None


def check_duplicate_photo(file_unique_id: str, category: str = None) -> bool:
    """
    Kiểm tra ảnh trùng bằng file_unique_id.
    Dùng cho phát hiện ảnh check-in trùng.
    """
    from bot.models.database import get_connection
    conn = get_connection()
    
    # Kiểm tra trong checkin_photos
    existing = conn.execute(
        "SELECT id FROM checkin_photos WHERE file_unique_id = ?",
        (file_unique_id,)
    ).fetchone()
    
    return existing is not None
