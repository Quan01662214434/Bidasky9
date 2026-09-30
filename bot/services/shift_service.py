"""
Service đăng ký ca làm.
"""

import json
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import ShiftRegStatus, SwapStatus
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


# ==================== KHUNG CA ====================

def create_shift_template(
    name: str,
    start_time: str,
    end_time: str,
    max_staff: int,
    created_by: int,
    crosses_midnight: bool = False,
) -> int:
    """Tạo khung ca mới."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO shift_templates 
           (name, start_time, end_time, crosses_midnight, max_staff, is_active, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (name, start_time, end_time, 1 if crosses_midnight else 0, max_staff, now_utc_iso(), created_by)
    )
    template_id = cursor.lastrowid
    conn.commit()
    return template_id


def get_shift_templates() -> List[dict]:
    """Lấy danh sách khung ca."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM shift_templates WHERE is_active = 1 ORDER BY start_time"
    ).fetchall()
    return [dict(r) for r in rows]


def get_shift_template(template_id: int) -> Optional[dict]:
    """Lấy thông tin khung ca."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM shift_templates WHERE id = ?", (template_id,)).fetchone()
    return dict(row) if row else None


# ==================== ĐĂNG KÝ CA ====================

def register_shift(
    employee_id: int,
    template_id: int,
    shift_date: str,
) -> dict:
    """
    Đăng ký ca.
    Kiểm tra trùng giờ và số người.
    """
    conn = get_connection()
    
    # Kiểm tra đã đăng ký
    existing = conn.execute(
        """SELECT id, status FROM shift_registrations 
           WHERE employee_id = ? AND template_id = ? AND shift_date = ?
           AND status NOT IN ('rejected', 'cancelled')""",
        (employee_id, template_id, shift_date)
    ).fetchone()
    if existing:
        raise ValueError("Bạn đã đăng ký ca này rồi")
    
    # Kiểm tra trùng giờ với ca đã duyệt
    template = get_shift_template(template_id)
    if not template:
        raise ValueError("Khung ca không tồn tại")
    
    # Kiểm tra trùng giờ
    employee_shifts = conn.execute(
        """SELECT sr.*, st.start_time, st.end_time, st.crosses_midnight
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           WHERE sr.employee_id = ? AND sr.shift_date = ? 
           AND sr.status IN ('pending', 'approved')""",
        (employee_id, shift_date)
    ).fetchall()
    
    for es in employee_shifts:
        if _shifts_overlap(template, dict(es)):
            raise ValueError("Trùng giờ với ca đã đăng ký")
    
    now = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO shift_registrations 
           (employee_id, template_id, shift_date, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (employee_id, template_id, shift_date, now)
    )
    reg_id = cursor.lastrowid
    conn.commit()
    
    log_action(employee_id, "register_shift", "shift_registrations", reg_id)
    return {"id": reg_id, "status": "pending"}


def approve_shift(reg_id: int, approved_by: int) -> bool:
    """Duyệt đăng ký ca."""
    conn = get_connection()
    
    reg = conn.execute(
        "SELECT * FROM shift_registrations WHERE id = ? AND status = 'pending'",
        (reg_id,)
    ).fetchone()
    if not reg:
        return False
    
    # Kiểm tra vượt số người
    template = get_shift_template(reg["template_id"])
    approved_count = conn.execute(
        """SELECT COUNT(*) as cnt FROM shift_registrations 
           WHERE template_id = ? AND shift_date = ? AND status = 'approved'""",
        (reg["template_id"], reg["shift_date"])
    ).fetchone()["cnt"]
    
    if approved_count >= template["max_staff"]:
        raise ValueError(f"Ca đã đủ {template['max_staff']} người")
    
    # Kiểm tra trùng giờ đã duyệt
    employee_approved = conn.execute(
        """SELECT sr.*, st.start_time, st.end_time, st.crosses_midnight
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           WHERE sr.employee_id = ? AND sr.shift_date = ? AND sr.status = 'approved'""",
        (reg["employee_id"], reg["shift_date"])
    ).fetchall()
    
    for ea in employee_approved:
        if _shifts_overlap(template, dict(ea)):
            raise ValueError("Trùng giờ với ca đã duyệt của nhân viên này")
    
    conn.execute(
        "UPDATE shift_registrations SET status = 'approved', approved_at = ?, approved_by = ? WHERE id = ?",
        (now_utc_iso(), approved_by, reg_id)
    )
    conn.commit()
    
    log_action(approved_by, "approve_shift", "shift_registrations", reg_id)
    return True


def reject_shift(reg_id: int, rejected_by: int, reason: str = None) -> bool:
    """Từ chối đăng ký ca."""
    conn = get_connection()
    conn.execute(
        """UPDATE shift_registrations SET status = 'rejected', 
           approved_by = ?, approved_at = ?, reject_reason = ?
           WHERE id = ? AND status = 'pending'""",
        (rejected_by, now_utc_iso(), reason, reg_id)
    )
    conn.commit()
    log_action(rejected_by, "reject_shift", "shift_registrations", reg_id, reason=reason)
    return True


def cancel_shift(reg_id: int, cancelled_by: int, reason: str = None) -> bool:
    """Hủy đăng ký ca."""
    conn = get_connection()
    conn.execute(
        """UPDATE shift_registrations SET status = 'cancelled',
           cancelled_at = ?, cancelled_by = ?, cancel_reason = ?
           WHERE id = ? AND status IN ('pending', 'approved')""",
        (now_utc_iso(), cancelled_by, reason, reg_id)
    )
    conn.commit()
    log_action(cancelled_by, "cancel_shift", "shift_registrations", reg_id, reason=reason)
    return True


def batch_approve_shifts(reg_ids: List[int], approved_by: int) -> dict:
    """Duyệt hàng loạt các đăng ký không xung đột."""
    results = {"approved": [], "failed": []}
    for rid in reg_ids:
        try:
            approve_shift(rid, approved_by)
            results["approved"].append(rid)
        except ValueError as e:
            results["failed"].append({"id": rid, "error": str(e)})
    return results


# ==================== ĐỔI CA ====================

def request_swap(
    registration_id: int,
    from_employee: int,
    to_employee: int,
    reason: str = None,
) -> int:
    """Yêu cầu đổi ca."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO shift_swaps 
           (registration_id, from_employee, to_employee, status, reason, created_at)
           VALUES (?, ?, ?, 'pending_receiver', ?, ?)""",
        (registration_id, from_employee, to_employee, reason, now_utc_iso())
    )
    swap_id = cursor.lastrowid
    conn.commit()
    log_action(from_employee, "request_swap", "shift_swaps", swap_id)
    return swap_id


def respond_swap(swap_id: int, accept: bool, by_employee: int) -> bool:
    """Người nhận đồng ý/từ chối đổi ca."""
    conn = get_connection()
    status = SwapStatus.PENDING_OWNER.value if accept else SwapStatus.REJECTED.value
    conn.execute(
        "UPDATE shift_swaps SET status = ?, receiver_responded_at = ? WHERE id = ?",
        (status, now_utc_iso(), swap_id)
    )
    conn.commit()
    return True


def approve_swap(swap_id: int, approved_by: int) -> bool:
    """Chủ duyệt đổi ca."""
    conn = get_connection()
    swap = conn.execute("SELECT * FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()
    if not swap or swap["status"] != SwapStatus.PENDING_OWNER.value:
        return False
    
    # Đổi employee trong registration
    conn.execute(
        "UPDATE shift_registrations SET employee_id = ? WHERE id = ?",
        (swap["to_employee"], swap["registration_id"])
    )
    conn.execute(
        "UPDATE shift_swaps SET status = 'approved', owner_decided_at = ?, owner_decision_by = ? WHERE id = ?",
        (now_utc_iso(), approved_by, swap_id)
    )
    conn.commit()
    log_action(approved_by, "approve_swap", "shift_swaps", swap_id)
    return True


# ==================== TRUY VẤN ====================

def get_registrations_for_date(shift_date: str) -> List[dict]:
    """Lấy đăng ký ca theo ngày."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT sr.*, st.name as shift_name, st.start_time, st.end_time,
                  st.crosses_midnight, u.display_name
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           JOIN users u ON sr.employee_id = u.telegram_id
           WHERE sr.shift_date = ?
           ORDER BY st.start_time, u.display_name""",
        (shift_date,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_employee_registrations(employee_id: int, from_date: str = None, to_date: str = None) -> List[dict]:
    """Lấy đăng ký ca của nhân viên."""
    conn = get_connection()
    sql = """
        SELECT sr.*, st.name as shift_name, st.start_time, st.end_time, st.crosses_midnight
        FROM shift_registrations sr
        JOIN shift_templates st ON sr.template_id = st.id
        WHERE sr.employee_id = ?
    """
    params = [employee_id]
    if from_date:
        sql += " AND sr.shift_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND sr.shift_date <= ?"
        params.append(to_date)
    sql += " ORDER BY sr.shift_date, st.start_time"
    
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_pending_registrations() -> List[dict]:
    """Lấy đăng ký chờ duyệt."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT sr.*, st.name as shift_name, st.start_time, st.end_time,
                  u.display_name
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           JOIN users u ON sr.employee_id = u.telegram_id
           WHERE sr.status = 'pending'
           ORDER BY sr.shift_date, st.start_time""",
    ).fetchall()
    return [dict(r) for r in rows]


def get_approved_shift_for_checkin(employee_id: int, now_iso: str) -> List[dict]:
    """
    Lấy ca đã duyệt có thể check-in vào thời điểm hiện tại.
    """
    conn = get_connection()
    # Lấy config khung check-in (mặc định ±30 phút)
    checkin_window = 30  # phút
    
    today = now_iso[:10]
    rows = conn.execute(
        """SELECT sr.*, st.name as shift_name, st.start_time, st.end_time,
                  st.crosses_midnight
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           WHERE sr.employee_id = ? AND sr.status = 'approved'
           AND sr.shift_date = ?""",
        (employee_id, today)
    ).fetchall()
    
    return [dict(r) for r in rows]


def _shifts_overlap(t1: dict, t2: dict) -> bool:
    """Kiểm tra hai ca có trùng giờ không."""
    def to_minutes(time_str):
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    
    s1_start = to_minutes(t1["start_time"])
    s1_end = to_minutes(t1["end_time"])
    s2_start = to_minutes(t2["start_time"])
    s2_end = to_minutes(t2["end_time"])
    
    if t1.get("crosses_midnight"):
        s1_end += 24 * 60
    if t2.get("crosses_midnight"):
        s2_end += 24 * 60
    
    return s1_start < s2_end and s2_start < s1_end
