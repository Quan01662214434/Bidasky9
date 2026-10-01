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


def _get_supabase_storage_url(path: str) -> str:
    """Trả về public URL cho file trên Supabase Storage."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{path}"


async def _upload_to_supabase(file_bytes: bytes, remote_path: str) -> str | None:
    """Upload file lên Supabase Storage. Trả về public URL hoặc None."""
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
                    "x-upsert": "true",  # Overwrite if exists
                },
            )
            if resp.status_code in (200, 201):
                public_url = _get_supabase_storage_url(remote_path)
                logger.info("Uploaded to Supabase Storage: %s", remote_path)
                return public_url
            else:
                logger.error("Supabase upload failed %s: %s", resp.status_code, resp.text[:200])
                return None
    except Exception as e:
        logger.error("Supabase upload error: %s", e)
        return None


async def save_photo_from_telegram(
    bot: Bot,
    file_id: str,
    file_unique_id: str,
    category: str,  # checkin/debt/transaction/receipt/report
    reference_id: str,
    uploader_id: int,
) -> str | None:
    """
    Tải ảnh từ Telegram và lưu.
    Ưu tiên Supabase Storage nếu đã cấu hình.
    Fallback: lưu filesystem cục bộ.
    Trả về URL/đường dẫn hoặc None.
    """
    try:
        timestamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
        filename = f"{category}_{reference_id}_{timestamp}_{file_unique_id}"

        # Tải file từ Telegram
        tg_file = await bot.get_file(file_id)
        ext = ".jpg"
        if tg_file.file_path and "." in tg_file.file_path:
            ext = "." + tg_file.file_path.split(".")[-1]

        # Thử upload Supabase Storage trước
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            file_bytes = await tg_file.download_as_bytearray()
            remote_path = f"{category}/{filename}{ext}"
            public_url = await _upload_to_supabase(bytes(file_bytes), remote_path)
            if public_url:
                return public_url
            logger.warning("Supabase upload failed, falling back to local storage")

        # Fallback: lưu cục bộ
        photo_dir = Path(Config.PHOTO_STORAGE_PATH) / category
        photo_dir.mkdir(parents=True, exist_ok=True)
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

        logger.info("Đã lưu ảnh local: %s", local_path)
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
