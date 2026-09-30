"""
Service xác thực và quản lý người dùng.
"""

import json
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import Role
from bot.config import Config
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


def ensure_owner_exists():
    """Đảm bảo chủ quán tồn tại trong DB."""
    conn = get_connection()
    owner_id = Config.OWNER_TELEGRAM_ID
    if owner_id == 0:
        return
    
    existing = conn.execute(
        "SELECT telegram_id FROM users WHERE telegram_id = ?",
        (owner_id,)
    ).fetchone()
    
    if not existing:
        conn.execute(
            """INSERT INTO users (telegram_id, display_name, role, is_active, created_at)
               VALUES (?, ?, ?, 1, ?)""",
            (owner_id, "Chủ quán", Role.OWNER.value, now_utc_iso())
        )
        conn.commit()
        logger.info("Đã tạo tài khoản chủ quán: %s", owner_id)


def add_employee(
    telegram_id: int,
    display_name: str,
    phone: str = None,
    created_by: int = None
) -> bool:
    """Thêm nhân viên mới."""
    conn = get_connection()
    
    existing = conn.execute(
        "SELECT telegram_id, is_active FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    
    if existing:
        if existing["is_active"] == 1:
            return False  # Đã tồn tại và đang hoạt động
        # Kích hoạt lại
        conn.execute(
            """UPDATE users SET display_name = ?, role = ?, is_active = 1,
               phone = ?, deactivated_at = NULL, deactivated_by = NULL
               WHERE telegram_id = ?""",
            (display_name, Role.EMPLOYEE.value, phone, telegram_id)
        )
    else:
        conn.execute(
            """INSERT INTO users (telegram_id, display_name, role, phone, is_active, created_at, created_by)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (telegram_id, display_name, Role.EMPLOYEE.value, phone, now_utc_iso(), created_by)
        )
    
    conn.commit()
    log_action(created_by or 0, "add_employee", "users", telegram_id,
               new_data=json.dumps({"name": display_name, "phone": phone}))
    return True


def remove_employee(telegram_id: int, removed_by: int) -> bool:
    """Thu hồi quyền nhân viên (vô hiệu hóa, không xóa dữ liệu)."""
    conn = get_connection()
    
    existing = conn.execute(
        "SELECT telegram_id, display_name FROM users WHERE telegram_id = ? AND is_active = 1",
        (telegram_id,)
    ).fetchone()
    
    if not existing:
        return False
    
    conn.execute(
        """UPDATE users SET is_active = 0, deactivated_at = ?, deactivated_by = ?
           WHERE telegram_id = ?""",
        (now_utc_iso(), removed_by, telegram_id)
    )
    conn.commit()
    
    log_action(removed_by, "remove_employee", "users", telegram_id,
               old_data=json.dumps({"name": existing["display_name"]}),
               reason="Thu hồi quyền")
    return True


def get_all_employees() -> List[dict]:
    """Lấy danh sách nhân viên đang hoạt động."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT telegram_id, display_name, phone, role, created_at 
           FROM users WHERE role = 'employee' AND is_active = 1
           ORDER BY display_name"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_employee(telegram_id: int) -> Optional[dict]:
    """Lấy thông tin nhân viên."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
        (telegram_id,)
    ).fetchone()
    return dict(row) if row else None


def update_owner_name(telegram_id: int, name: str):
    """Cập nhật tên chủ quán."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET display_name = ? WHERE telegram_id = ?",
        (name, telegram_id)
    )
    conn.commit()


def set_wage_rate(employee_id: int, hourly_rate: int, 
                  effective_from: str, created_by: int, note: str = None):
    """Đặt mức lương cho nhân viên."""
    conn = get_connection()
    
    # Đóng mức lương cũ
    conn.execute(
        """UPDATE wage_rates SET effective_to = ?
           WHERE employee_id = ? AND effective_to IS NULL""",
        (effective_from, employee_id)
    )
    
    conn.execute(
        """INSERT INTO wage_rates (employee_id, hourly_rate, effective_from, created_at, created_by, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (employee_id, hourly_rate, effective_from, now_utc_iso(), created_by, note)
    )
    conn.commit()
    
    log_action(created_by, "set_wage_rate", "wage_rates", employee_id,
               new_data=json.dumps({"rate": hourly_rate, "from": effective_from}))


def get_current_wage_rate(employee_id: int, at_datetime: str = None) -> Optional[int]:
    """Lấy mức lương hiện tại hoặc tại thời điểm cho trước."""
    conn = get_connection()
    
    if at_datetime:
        row = conn.execute(
            """SELECT hourly_rate FROM wage_rates 
               WHERE employee_id = ? AND effective_from <= ?
               AND (effective_to IS NULL OR effective_to > ?)
               ORDER BY effective_from DESC LIMIT 1""",
            (employee_id, at_datetime, at_datetime)
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT hourly_rate FROM wage_rates 
               WHERE employee_id = ? AND effective_to IS NULL
               ORDER BY effective_from DESC LIMIT 1""",
            (employee_id,)
        ).fetchone()
    
    return row["hourly_rate"] if row else None
