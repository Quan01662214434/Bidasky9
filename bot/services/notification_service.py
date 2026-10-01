"""
Service thông báo: nhắc ca, nhắc nợ, báo trễ.
Dùng hàng đợi lưu bền, retry có kiểm soát, chống lặp.
"""

import json
import hashlib
import logging
from typing import Optional
from datetime import datetime, timedelta

import pytz
from telegram import Bot

from bot.models.database import get_connection, now_utc_iso, vn_now
from bot.config import Config

logger = logging.getLogger(__name__)

VN_TZ = pytz.timezone(Config.TIMEZONE)


async def queue_notification(
    recipient_id: int,
    message: str,
    notification_type: str,
    reference_type: str = None,
    reference_id: int = None,
    dedup_key: str = None,
) -> int:
    """
    Thêm thông báo vào hàng đợi.
    Chống lặp bằng dedup_key.
    """
    conn = get_connection()
    
    # Tạo dedup key nếu chưa có
    if not dedup_key:
        key_data = f"{recipient_id}:{notification_type}:{reference_type}:{reference_id}:{datetime.now().strftime('%Y%m%d%H')}"
        dedup_key = hashlib.md5(key_data.encode()).hexdigest()
    
    # Kiểm tra trùng
    existing = conn.execute(
        """SELECT id FROM notification_queue 
           WHERE dedup_key = ? AND status IN ('pending', 'sent')""",
        (dedup_key,)
    ).fetchone()
    if existing:
        return existing["id"]
    
    now = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO notification_queue 
           (recipient_id, message, notification_type, reference_type, reference_id,
            status, dedup_key, created_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (recipient_id, message, notification_type, reference_type, reference_id,
         dedup_key, now)
    )
    conn.commit()
    return cursor.lastrowid


async def send_pending_notifications(bot: Bot):
    """Gửi các thông báo pending."""
    conn = get_connection()
    
    pending = conn.execute(
        """SELECT * FROM notification_queue 
           WHERE status = 'pending' AND retry_count < max_retries
           ORDER BY created_at LIMIT 20"""
    ).fetchall()
    
    for notif in pending:
        try:
            await bot.send_message(
                chat_id=notif["recipient_id"],
                text=notif["message"],
                parse_mode="Markdown"
            )
            conn.execute(
                "UPDATE notification_queue SET status = 'sent', sent_at = ? WHERE id = ?",
                (now_utc_iso(), notif["id"])
            )
        except Exception as e:
            logger.error("Lỗi gửi thông báo #%d: %s", notif["id"], e)
            conn.execute(
                """UPDATE notification_queue SET retry_count = retry_count + 1,
                   last_error = ?, next_retry_at = ?
                   WHERE id = ?""",
                (str(e), (vn_now() + timedelta(minutes=5)).isoformat(), notif["id"])
            )
    
    conn.commit()


async def notify_owner(bot: Bot, message: str, notification_type: str = "general",
                       reference_type: str = None, reference_id: int = None):
    """Gửi thông báo cho chủ quán."""
    owner_id = Config.OWNER_TELEGRAM_ID
    if not owner_id:
        return
    
    await queue_notification(owner_id, message, notification_type, reference_type, reference_id)
    
    try:
        await bot.send_message(chat_id=owner_id, text=message, parse_mode="Markdown")
        # Đánh dấu đã gửi
        conn = get_connection()
        conn.execute(
            """UPDATE notification_queue SET status = 'sent', sent_at = ?
               WHERE id = (
                   SELECT id FROM notification_queue 
                   WHERE recipient_id = ? AND notification_type = ? AND status = 'pending'
                   ORDER BY created_at DESC LIMIT 1
               )""",
            (now_utc_iso(), owner_id, notification_type)
        )
    except Exception as e:
        logger.error("Lỗi gửi cho chủ: %s", e)


async def check_late_checkins(bot: Bot):
    """
    Kiểm tra nhân viên chưa check-in sau giờ bắt đầu ca.
    Gọi định kỳ.
    """
    conn = get_connection()
    now = vn_now()
    today = now.strftime("%Y-%m-%d")
    
    # Lấy cấu hình ngưỡng
    late_threshold = 15  # phút mặc định
    config_row = conn.execute(
        "SELECT value FROM config WHERE key = 'late_checkin_threshold'"
    ).fetchone()
    if config_row:
        late_threshold = int(config_row["value"])
    
    # Tìm ca đã duyệt hôm nay mà chưa có check-in
    approved_shifts = conn.execute(
        """SELECT sr.*, st.start_time, st.name as shift_name, 
                  u.display_name, u.telegram_id
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           JOIN users u ON sr.employee_id = u.telegram_id
           WHERE sr.shift_date = ? AND sr.status = 'approved'""",
        (today,)
    ).fetchall()
    
    for s in approved_shifts:
        # Kiểm tra đã có session
        session = conn.execute(
            """SELECT id FROM attendance_sessions 
               WHERE registration_id = ? AND status IN ('checked_in', 'checked_out')""",
            (s["id"],)
        ).fetchone()
        if session:
            continue
        
        # Kiểm tra thời gian
        h, m = map(int, s["start_time"].split(":"))
        shift_start = now.replace(hour=h, minute=m, second=0)
        
        if now > shift_start + timedelta(minutes=late_threshold):
            elapsed = int((now - shift_start).total_seconds() / 60)
            
            # Thông báo (chống lặp bằng dedup_key)
            dedup = f"late_{s['id']}_{today}"
            
            # Nhắc nhân viên
            await queue_notification(
                s["telegram_id"],
                f"⏰ Bạn chưa check-in ca {s['shift_name']} ({s['start_time']}). "
                f"Đã quá {elapsed} phút.",
                "late_reminder",
                "shift_registrations", s["id"],
                dedup_key=dedup + "_emp"
            )
            
            # Báo chủ
            await notify_owner(
                bot,
                f"⚠️ *Chưa check-in*\n"
                f"NV: {s['display_name']}\n"
                f"Ca: {s['shift_name']} ({s['start_time']})\n"
                f"Đã chờ: {elapsed} phút",
                "late_alert",
                "shift_registrations", s["id"]
            )


async def check_unclosed_sessions(bot: Bot):
    """Kiểm tra phiên công đang mở quá giờ."""
    conn = get_connection()
    now = vn_now()
    
    checkout_threshold = 30  # phút sau giờ kết thúc ca
    
    open_sessions = conn.execute(
        """SELECT a.*, u.display_name
           FROM attendance_sessions a
           JOIN users u ON a.employee_id = u.telegram_id
           WHERE a.status = 'checked_in' AND a.scheduled_end IS NOT NULL"""
    ).fetchall()
    
    for s in open_sessions:
        scheduled_end = datetime.fromisoformat(s["scheduled_end"])
        if now > scheduled_end + timedelta(minutes=checkout_threshold):
            elapsed = int((now - scheduled_end).total_seconds() / 60)
            
            dedup = f"unclosed_{s['id']}_{now.strftime('%Y%m%d%H')}"
            
            await queue_notification(
                s["employee_id"],
                f"⏰ Phiên công chưa đóng. Bạn đang làm thêm? "
                f"Đã quá giờ kết thúc ca {elapsed} phút.\n"
                f"Nhớ check-out khi xong.",
                "unclosed_reminder",
                "attendance_sessions", s["id"],
                dedup_key=dedup + "_emp"
            )
            
            await notify_owner(
                bot,
                f"⚠️ *Phiên công chưa đóng*\n"
                f"NV: {s['display_name']}\n"
                f"Quá giờ kết thúc: {elapsed} phút\n"
                f"_(Có thể đang làm thêm)_",
                "unclosed_alert",
                "attendance_sessions", s["id"]
            )
