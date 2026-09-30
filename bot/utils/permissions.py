"""
Decorator và hàm kiểm tra phân quyền.
Kiểm tra ở mọi handler, callback và tầng nghiệp vụ.
"""

import functools
import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import Config
from bot.constants import Role
from bot.models.database import get_connection

logger = logging.getLogger(__name__)


def get_user_role(telegram_id: int) -> Role:
    """Lấy role của user từ database."""
    if telegram_id == Config.OWNER_TELEGRAM_ID:
        return Role.OWNER
    
    conn = get_connection()
    row = conn.execute(
        "SELECT role, is_active FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    
    if row and row["is_active"] == 1:
        return Role(row["role"])
    return Role.UNKNOWN


def is_owner(telegram_id: int) -> bool:
    """Kiểm tra có phải chủ quán."""
    return get_user_role(telegram_id) == Role.OWNER


def is_employee(telegram_id: int) -> bool:
    """Kiểm tra có phải nhân viên đang hoạt động."""
    role = get_user_role(telegram_id)
    return role in (Role.OWNER, Role.EMPLOYEE)


def get_user_display_name(telegram_id: int) -> Optional[str]:
    """Lấy tên hiển thị."""
    conn = get_connection()
    row = conn.execute(
        "SELECT display_name FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    return row["display_name"] if row else None


def require_private_chat(func):
    """Decorator: chỉ cho phép chat riêng."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_chat.type != "private":
            await update.effective_message.reply_text(
                "⚠️ Vui lòng sử dụng bot trong chat riêng để bảo mật thông tin."
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_owner(func):
    """Decorator: chỉ chủ quán mới được dùng."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_owner(user_id):
            logger.warning("Truy cập quyền chủ bị từ chối: user %s", user_id)
            await update.effective_message.reply_text(
                "🚫 Chức năng này chỉ dành cho chủ quán."
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_employee(func):
    """Decorator: chủ hoặc nhân viên."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_employee(user_id):
            logger.warning("Truy cập nhân viên bị từ chối: user %s", user_id)
            if get_user_role(user_id) == Role.UNKNOWN:
                await update.effective_message.reply_text(
                    f"🔒 Bạn chưa được cấp quyền.\n"
                    f"Telegram ID của bạn: `{user_id}`\n"
                    f"Gửi ID này cho chủ quán để được thêm vào hệ thống.",
                    parse_mode="Markdown"
                )
            else:
                await update.effective_message.reply_text(
                    "🚫 Quyền truy cập của bạn đã bị thu hồi."
                )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_active_cash_shift(func):
    """Decorator: phải có ca quỹ đang mở."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        conn = get_connection()
        
        # Tìm ca quỹ đang mở mà user là người phụ trách hoặc được phân công
        shift = conn.execute(
            "SELECT id FROM cash_shifts WHERE employee_id = ? AND status = 'open'",
            (user_id,)
        ).fetchone()
        
        if not shift:
            await update.effective_message.reply_text(
                "⚠️ Bạn chưa có ca quỹ đang mở.\n"
                "Hãy nhận ca quỹ trước khi thực hiện giao dịch."
            )
            return
        
        context.user_data["active_cash_shift_id"] = shift["id"]
        return await func(update, context, *args, **kwargs)
    return wrapper


def check_permission_for_callback(update: Update, required_role: Role) -> bool:
    """
    Kiểm tra quyền cho callback query.
    Trả về True nếu có quyền, False nếu không.
    Nút cũ sau khi thu hồi quyền sẽ bị chặn.
    """
    user_id = update.effective_user.id
    current_role = get_user_role(user_id)
    
    if required_role == Role.OWNER:
        return current_role == Role.OWNER
    elif required_role == Role.EMPLOYEE:
        return current_role in (Role.OWNER, Role.EMPLOYEE)
    return False


def log_action(actor_id: int, action: str, entity_type: str,
               entity_id: int = None, old_data: str = None,
               new_data: str = None, reason: str = None):
    """Ghi lịch sử thao tác vào audit_log."""
    conn = get_connection()
    from bot.models.database import now_utc_iso
    conn.execute(
        """INSERT INTO audit_log 
           (actor_id, action, entity_type, entity_id, old_data, new_data, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (actor_id, action, entity_type, entity_id, old_data, new_data, reason, now_utc_iso())
    )
    conn.commit()
