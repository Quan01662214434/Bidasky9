import asyncio
import logging

logger = logging.getLogger(__name__)

async def process_photo_upload_queue():
    from bot.models.database import get_connection
    from bot.utils.photo_storage import _upload_to_supabase
    import os
    
    conn = get_connection()
    pending = conn.execute(
        "SELECT id, file_data, category, reference_id, file_unique_id, retry_count FROM pending_photo_uploads WHERE status = 'pending' AND retry_count < 10"
    ).fetchall()
    
    for row in pending:
        if not row["file_data"]:
            conn.execute("UPDATE pending_photo_uploads SET status = 'failed', last_error = 'No byte data' WHERE id = ?", (row["id"],))
            continue
            
        ext = ".jpg"
        filename = f"{row['category']}_{row['reference_id']}_retry_{row['file_unique_id']}{ext}"
        remote_path = f"{row['category']}/{filename}"
        
        try:
            storage_path = await _upload_to_supabase(row["file_data"], remote_path)
            if storage_path:
                # Cập nhật DB
                conn.execute("UPDATE pending_photo_uploads SET status = 'success' WHERE id = ?", (row["id"],))
                # Có thể cần cập nhật đường dẫn mới vào bảng gốc, nhưng vì bot ưu tiên get từ tg file_id nên không ảnh hưởng.
            else:
                conn.execute("UPDATE pending_photo_uploads SET retry_count = retry_count + 1, last_error = 'Retry failed' WHERE id = ?", (row["id"],))
        except Exception as e:
            logger.error(f"Error retrying photo upload {row['id']}: {e}")
            conn.execute("UPDATE pending_photo_uploads SET retry_count = retry_count + 1, last_error = ? WHERE id = ?", (str(e), row["id"]))
            
        conn.commit()
