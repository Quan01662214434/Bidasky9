"""
Lưu trữ ảnh bền vững với Supabase Storage.
Tải ảnh từ Telegram, upload lên Supabase Storage.
Fallback: lưu local nếu chưa cấu hình SUPABASE_URL.
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

# Supabase Storage config
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "photos")


def _get_supabase_storage_path(path: str) -> str:
    """Trả về remote path thay vì public URL cho private storage."""
    return f"supabase://{STORAGE_BUCKET}/{path}"


async def _upload_to_supabase(file_bytes: bytes, remote_path: str) -> str | None:
    """Upload file lên Supabase Storage. Trả về remote path hoặc None."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None

    import httpx
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{remote_path}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                upload_url,
                content=file_bytes,
                headers={
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "image/jpeg",
                    "x-upsert": "true",
                },
            )
            if resp.status_code in (200, 201):
                logger.info("Uploaded to Supabase Storage (Private): %s", remote_path)
                return _get_supabase_storage_path(remote_path)
            else:
                logger.error("Supabase upload failed %s: %s", resp.status_code, resp.text[:200])
                return None
    except Exception as e:
        logger.error("Supabase upload error: %s", e)
        return None

def _queue_failed_upload(file_id: str, file_unique_id: str, category: str, reference_id: str, uploader_id: int, local_path: str, error_msg: str):
    from bot.models.database import get_connection, now_utc_iso
    conn = get_connection()
    conn.execute(
        """INSERT INTO pending_photo_uploads 
           (file_id, file_unique_id, category, reference_id, uploader_id, local_path, last_error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, file_unique_id, category, reference_id, uploader_id, local_path, error_msg, now_utc_iso())
    )

async def save_photo_from_telegram(
    bot: Bot,
    file_id: str,
    file_unique_id: str,
    category: str,
    reference_id: str,
    uploader_id: int,
) -> str | None:
    """
    Tải ảnh từ Telegram và lưu lên Supabase Storage riêng tư.
    Nếu lỗi mạng, đưa vào hàng đợi retry.
    """
    timestamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"{category}_{reference_id}_{timestamp}_{file_unique_id}"

    try:
        tg_file = await bot.get_file(file_id)
        ext = ".jpg"
        if tg_file.file_path and "." in tg_file.file_path:
            ext = "." + tg_file.file_path.split(".")[-1]
            
        file_bytes = await tg_file.download_as_bytearray()
        remote_path = f"{category}/{filename}{ext}"
        
        # Luôn lưu local path tạm thời để retry
        photo_dir = Path(Config.PHOTO_STORAGE_PATH) / category
        photo_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(photo_dir / f"{filename}{ext}")
        with open(local_path, "wb") as f:
            f.write(file_bytes)

        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            storage_path = await _upload_to_supabase(bytes(file_bytes), remote_path)
            if storage_path:
                return storage_path
            
            # Queue for retry
            logger.warning("Upload failed, queuing for retry: %s", remote_path)
            _queue_failed_upload(file_id, file_unique_id, category, reference_id, uploader_id, local_path, "Upload failed")
            return f"pending://{local_path}"
        
        # Nếu chưa config supabase
        _queue_failed_upload(file_id, file_unique_id, category, reference_id, uploader_id, local_path, "No Supabase config")
        return f"pending://{local_path}"

    except Exception as e:
        logger.error("Lỗi tải ảnh từ Telegram: %s", e)
        raise ValueError("Lỗi tải ảnh từ Telegram. Vui lòng thử lại sau.")

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
