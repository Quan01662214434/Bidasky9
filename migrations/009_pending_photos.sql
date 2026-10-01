-- Bảng lưu trữ các ảnh cần retry upload lên Supabase (do mạng hoặc Supabase lỗi)
CREATE TABLE IF NOT EXISTS pending_photo_uploads (
    id SERIAL PRIMARY KEY,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    category TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    uploader_id BIGINT NOT NULL,
    local_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending/failed/success
    retry_count BIGINT DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL
);
