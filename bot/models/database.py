"""
Database connection, migration runner và backup cho SQLite.
Dùng WAL mode, foreign keys, và giao dịch ACID.
"""

import os
import sqlite3
import shutil
import logging
from datetime import datetime
from pathlib import Path

import pytz

from bot.config import Config

logger = logging.getLogger(__name__)

_connection: sqlite3.Connection | None = None

VN_TZ = pytz.timezone(Config.TIMEZONE)


import pg_wrapper

def get_connection():
    """Lấy connection singleton."""
    global _connection
    if _connection is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise ValueError("Thiếu DATABASE_URL trong file .env")
        _connection = pg_wrapper.connect(db_url)
        logger.info("Đã kết nối database Postgres (Supabase)")
    return _connection


def close_connection():
    """Đóng connection."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None
        logger.info("Đã đóng database connection")


def run_migrations():
    """Chạy migration files theo thứ tự."""
    conn = get_connection()
    
    # Tạo bảng theo dõi migration
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()
    
    # Tìm migration chưa chạy
    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    if not migrations_dir.exists():
        logger.warning("Thư mục migrations không tồn tại: %s", migrations_dir)
        return
    
    applied = {row["filename"] for row in 
               conn.execute("SELECT filename FROM _migrations").fetchall()}
    
    migration_files = sorted(migrations_dir.glob("*.sql"))
    
    for mf in migration_files:
        if mf.name not in applied:
            logger.info("Chạy migration: %s", mf.name)
            sql = mf.read_text(encoding="utf-8")
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO _migrations (filename, applied_at) VALUES (?, ?)",
                    (mf.name, now_utc_iso())
                )
                conn.commit()
                logger.info("Migration thành công: %s", mf.name)
            except Exception as e:
                logger.error("Migration thất bại %s: %s", mf.name, e)
                raise


def backup_database(reason: str = "manual") -> str:
    """
    Sao lưu database dùng sqlite3 backup API (nhất quán với WAL).
    Trả về đường dẫn file backup.
    """
    conn = get_connection()
    backup_dir = Path(Config.BACKUP_PATH)
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
    backup_name = f"bida_backup_{timestamp}_{reason}.db"
    backup_path = backup_dir / backup_name
    
    # Dùng sqlite3 backup API để đảm bảo nhất quán
    backup_conn = sqlite3.connect(str(backup_path))
    try:
        conn.backup(backup_conn)
        logger.info("Backup thành công: %s", backup_path)
    finally:
        backup_conn.close()
    
    return str(backup_path)


def restore_database(backup_path: str) -> bool:
    """
    Phục hồi database từ backup.
    Đóng connection hiện tại, thay file, mở lại.
    """
    if not os.path.exists(backup_path):
        logger.error("File backup không tồn tại: %s", backup_path)
        return False
    
    close_connection()
    
    db_path = Config.DATABASE_PATH
    
    # Backup trước khi restore
    timestamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
    pre_restore = f"{db_path}.pre_restore_{timestamp}"
    if os.path.exists(db_path):
        shutil.copy2(db_path, pre_restore)
        # Xóa WAL files
        for ext in ["-wal", "-shm"]:
            wal_path = db_path + ext
            if os.path.exists(wal_path):
                os.remove(wal_path)
    
    shutil.copy2(backup_path, db_path)
    logger.info("Restore thành công từ: %s", backup_path)
    return True


def now_utc_iso() -> str:
    """Trả về thời gian hiện tại UTC dạng ISO 8601."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_vn_iso() -> str:
    """Trả về thời gian hiện tại VN dạng ISO 8601."""
    return datetime.now(VN_TZ).isoformat()


def utc_to_vn(utc_str: str) -> datetime:
    """Chuyển chuỗi UTC sang datetime VN."""
    if not utc_str:
        return None
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    return dt.astimezone(VN_TZ)


def vn_now() -> datetime:
    """Datetime hiện tại theo VN."""
    return datetime.now(VN_TZ)


def execute_in_transaction(conn: sqlite3.Connection, operations: list) -> list:
    """
    Chạy nhiều operations trong một transaction.
    Mỗi operation là tuple (sql, params).
    Trả về list kết quả.
    """
    results = []
    try:
        for sql, params in operations:
            cursor = conn.execute(sql, params)
            results.append(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return results
