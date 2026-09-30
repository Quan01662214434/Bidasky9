"""
Service chấm công: check-in có ảnh, check-out, tính công, yêu cầu sửa.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pytz

from bot.models.database import get_connection, now_utc_iso, vn_now
from bot.config import Config
from bot.constants import AttendanceStatus, AdjustmentStatus
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)

VN_TZ = pytz.timezone(Config.TIMEZONE)


def start_checkin_flow(
    employee_id: int,
    registration_id: int,
) -> dict:
    """
    Bắt đầu luồng check-in.
    Kiểm tra: đúng người, đúng ca duyệt, trong khung giờ.
    Trả về session_id để chờ ảnh.
    """
    conn = get_connection()
    
    # Kiểm tra ca đã duyệt
    reg = conn.execute(
        """SELECT sr.*, st.start_time, st.end_time, st.name as shift_name,
                  st.crosses_midnight
           FROM shift_registrations sr
           JOIN shift_templates st ON sr.template_id = st.id
           WHERE sr.id = ? AND sr.employee_id = ? AND sr.status = 'approved'""",
        (registration_id, employee_id)
    ).fetchone()
    
    if not reg:
        raise ValueError("Không tìm thấy ca đã duyệt hoặc không phải ca của bạn")
    
    # Kiểm tra phiên trước chưa đóng
    open_session = conn.execute(
        """SELECT id FROM attendance_sessions 
           WHERE employee_id = ? AND status = 'checked_in'""",
        (employee_id,)
    ).fetchone()
    if open_session:
        raise ValueError("Bạn có phiên công chưa đóng (check-out trước)")
    
    # Kiểm tra đã check-in ca này chưa
    existing = conn.execute(
        """SELECT id FROM attendance_sessions 
           WHERE registration_id = ? AND status IN ('checked_in', 'checked_out')""",
        (registration_id,)
    ).fetchone()
    if existing:
        raise ValueError("Ca này đã được check-in")
    
    # Tính giờ lịch
    now = vn_now()
    shift_date = reg["shift_date"]
    start_h, start_m = map(int, reg["start_time"].split(":"))
    end_h, end_m = map(int, reg["end_time"].split(":"))
    
    scheduled_start = VN_TZ.localize(
        datetime.strptime(f"{shift_date} {reg['start_time']}", "%Y-%m-%d %H:%M")
    )
    if reg["crosses_midnight"]:
        scheduled_end = scheduled_start + timedelta(days=1)
        scheduled_end = scheduled_end.replace(hour=end_h, minute=end_m)
    else:
        scheduled_end = VN_TZ.localize(
            datetime.strptime(f"{shift_date} {reg['end_time']}", "%Y-%m-%d %H:%M")
        )
    
    # Tạo session (chưa hoàn tất - chờ ảnh)
    now_iso = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO attendance_sessions 
           (employee_id, registration_id, shift_date,
            scheduled_start, scheduled_end,
            checkin_flow_started_at,
            status, is_exception, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, 'checked_in', 0, ?, ?)""",
        (employee_id, registration_id, shift_date,
         scheduled_start.isoformat(), scheduled_end.isoformat(),
         now_iso, now_iso, employee_id)
    )
    session_id = cursor.lastrowid
    conn.commit()
    
    return {
        "session_id": session_id,
        "shift_name": reg["shift_name"],
        "scheduled_start": scheduled_start.isoformat(),
        "scheduled_end": scheduled_end.isoformat(),
        "waiting_photo": True,
    }


def complete_checkin_with_photo(
    session_id: int,
    employee_id: int,
    file_id: str,
    file_unique_id: str,
    telegram_timestamp: str = None,
    local_path: str = None,
) -> dict:
    """
    Hoàn tất check-in khi nhận ảnh.
    Giờ check-in = giờ máy chủ nhận ảnh hợp lệ.
    """
    conn = get_connection()
    server_received = now_utc_iso()
    
    session = conn.execute(
        "SELECT * FROM attendance_sessions WHERE id = ? AND employee_id = ?",
        (session_id, employee_id)
    ).fetchone()
    if not session:
        raise ValueError("Không tìm thấy phiên check-in")
    
    # Kiểm tra ảnh trùng
    is_duplicate = False
    existing_photo = conn.execute(
        "SELECT id FROM checkin_photos WHERE file_unique_id = ?",
        (file_unique_id,)
    ).fetchone()
    if existing_photo:
        is_duplicate = True
    
    # Lưu ảnh
    conn.execute(
        """INSERT INTO checkin_photos 
           (session_id, employee_id, file_id, file_unique_id, local_path,
            telegram_timestamp, server_received_at, is_duplicate, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, employee_id, file_id, file_unique_id, local_path,
         telegram_timestamp, server_received, 1 if is_duplicate else 0, server_received)
    )
    
    # Cập nhật session - giờ đến = giờ máy chủ nhận ảnh
    arrived_at = server_received
    
    # Kiểm tra xem có ai đang làm không (cần bàn giao)
    active_other = conn.execute(
        """SELECT id FROM attendance_sessions 
           WHERE status = 'checked_in' AND employee_id != ?""",
        (employee_id,)
    ).fetchone()
    
    status = 'pending_handover'
    checkin_time = None
    late_minutes = 0
    
    # Nếu không có ai đang làm (ca đầu ngày) hoặc ngoại lệ
    if not active_other:
        status = 'checked_in'
        checkin_time = arrived_at
        
        # Tính trễ
        if session["scheduled_start"]:
            scheduled = datetime.fromisoformat(session["scheduled_start"])
            actual = datetime.fromisoformat(arrived_at.replace("Z", "+00:00"))
            if actual.tzinfo is None:
                actual = pytz.utc.localize(actual)
            diff = (actual - scheduled).total_seconds() / 60
            if diff > 0:
                late_minutes = int(diff)
    
    conn.execute(
        """UPDATE attendance_sessions SET 
           arrived_at = ?,
           checkin_photo_received_at = ?,
           checkin_telegram_timestamp = ?,
           checkin_time = ?,
           late_minutes = ?,
           status = ?
           WHERE id = ?""",
        (arrived_at, server_received, telegram_timestamp, checkin_time, late_minutes, status, session_id)
    )
    conn.commit()
    
    log_action(employee_id, "checkin_complete", "attendance_sessions", session_id,
               new_data=json.dumps({"late": late_minutes, "status": status}))
    
    return {
        "session_id": session_id,
        "arrived_at": arrived_at,
        "checkin_time": checkin_time,
        "status": status,
        "late_minutes": late_minutes,
        "is_duplicate_photo": is_duplicate,
        "is_late": late_minutes > 0,
        "needs_handover": status == 'pending_handover'
    }


def checkout(employee_id: int) -> dict:
    """
    Check-out. Giờ = máy chủ nhận thao tác.
    Không tự đóng ca quỹ.
    """
    conn = get_connection()
    
    session = conn.execute(
        """SELECT * FROM attendance_sessions 
           WHERE employee_id = ? AND status = 'checked_in'
           ORDER BY created_at DESC LIMIT 1""",
        (employee_id,)
    ).fetchone()
    
    if not session:
        raise ValueError("Bạn chưa check-in hoặc đã check-out")
    
    if not session["checkin_time"]:
        raise ValueError("Check-in chưa hoàn tất (chưa gửi ảnh hoặc đang chờ bàn giao)")
        
    # Kiểm tra xem có ca quỹ nào đang mở không (chưa kết ca / bàn giao xong)
    active_cash_shift = conn.execute(
        """SELECT id, handover_status FROM cash_shifts 
           WHERE employee_id = ? AND closed_at IS NULL""",
        (employee_id,)
    ).fetchone()
    
    if active_cash_shift:
        raise ValueError("Bạn phải hoàn thành Kết ca quỹ và Bàn giao trước khi Ra về!")
        
    checkout_time = now_utc_iso()
    
    # Tính thời gian làm
    checkin_dt = datetime.fromisoformat(session["checkin_time"].replace("Z", "+00:00"))
    checkout_dt = datetime.fromisoformat(checkout_time.replace("Z", "+00:00"))
    
    if checkin_dt.tzinfo is None:
        checkin_dt = pytz.utc.localize(checkin_dt)
    if checkout_dt.tzinfo is None:
        checkout_dt = pytz.utc.localize(checkout_dt)
    
    total_minutes = int((checkout_dt - checkin_dt).total_seconds() / 60)
    
    if total_minutes < 0:
        raise ValueError("Lỗi: giờ ra trước giờ vào")
    
    # Cảnh báo phiên quá dài
    is_long_session = total_minutes > (16 * 60)  # > 16 giờ
    
    # Tính về sớm
    early_leave = 0
    if session["scheduled_end"]:
        scheduled_end = datetime.fromisoformat(session["scheduled_end"])
        if checkout_dt < scheduled_end:
            early_leave = int((scheduled_end - checkout_dt).total_seconds() / 60)
    
    # Tính overtime
    overtime = 0
    if session["scheduled_end"]:
        scheduled_end = datetime.fromisoformat(session["scheduled_end"])
        if checkout_dt > scheduled_end:
            overtime = int((checkout_dt - scheduled_end).total_seconds() / 60)
    
    # approved_minutes = tổng phút (chưa trừ nghỉ, chờ chủ duyệt nếu cần)
    approved_minutes = total_minutes - session.get("break_minutes", 0)
    
    # Tính lương tạm
    from bot.services.auth_service import get_current_wage_rate
    wage_rate = get_current_wage_rate(employee_id, session["checkin_time"])
    wage_amount = None
    if wage_rate and approved_minutes:
        wage_amount = (approved_minutes * wage_rate) // 60
    
    conn.execute(
        """UPDATE attendance_sessions SET 
           checkout_time = ?,
           approved_minutes = ?,
           early_leave_minutes = ?,
           overtime_minutes = ?,
           wage_amount = ?,
           status = 'checked_out'
           WHERE id = ?""",
        (checkout_time, approved_minutes, early_leave, overtime, wage_amount, session["id"])
    )
    conn.commit()
    
    log_action(employee_id, "checkout", "attendance_sessions", session["id"],
               new_data=json.dumps({"minutes": approved_minutes, "wage": wage_amount}))
    
    return {
        "session_id": session["id"],
        "checkin_time": session["checkin_time"],
        "checkout_time": checkout_time,
        "total_minutes": total_minutes,
        "approved_minutes": approved_minutes,
        "late_minutes": session.get("late_minutes", 0),
        "early_leave_minutes": early_leave,
        "overtime_minutes": overtime,
        "wage_rate": wage_rate,
        "wage_amount": wage_amount,
        "is_long_session": is_long_session,
    }


# ==================== SỬA CÔNG ====================

def request_adjustment(
    employee_id: int,
    shift_date: str,
    session_id: int = None,
    requested_checkin: str = None,
    requested_checkout: str = None,
    reason: str = None,
    evidence_photo_id: str = None,
) -> int:
    """Nhân viên yêu cầu sửa công."""
    conn = get_connection()
    
    # Lưu giá trị gốc nếu có session
    original_checkin = None
    original_checkout = None
    original_minutes = None
    if session_id:
        session = conn.execute(
            "SELECT * FROM attendance_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session:
            original_checkin = session["checkin_time"]
            original_checkout = session["checkout_time"]
            original_minutes = session["approved_minutes"]
    
    cursor = conn.execute(
        """INSERT INTO attendance_adjustments 
           (session_id, employee_id, shift_date,
            requested_checkin, requested_checkout, reason, evidence_photo_id,
            status, original_checkin, original_checkout, original_minutes,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (session_id, employee_id, shift_date,
         requested_checkin, requested_checkout, reason, evidence_photo_id,
         original_checkin, original_checkout, original_minutes, now_utc_iso())
    )
    adj_id = cursor.lastrowid
    conn.commit()
    
    log_action(employee_id, "request_adjustment", "attendance_adjustments", adj_id)
    return adj_id


def process_adjustment(
    adj_id: int,
    decided_by: int,
    approve: bool,
    approved_checkin: str = None,
    approved_checkout: str = None,
    reason: str = None,
) -> dict:
    """Chủ xử lý yêu cầu sửa công."""
    conn = get_connection()
    
    adj = conn.execute(
        "SELECT * FROM attendance_adjustments WHERE id = ?", (adj_id,)
    ).fetchone()
    if not adj:
        raise ValueError("Không tìm thấy yêu cầu sửa công")
    
    now = now_utc_iso()
    
    if not approve:
        conn.execute(
            """UPDATE attendance_adjustments SET status = 'rejected',
               decided_by = ?, decided_at = ?, decision_reason = ?
               WHERE id = ?""",
            (decided_by, now, reason, adj_id)
        )
        conn.commit()
        return {"status": "rejected"}
    
    # Duyệt - có thể chỉnh giờ khác yêu cầu
    final_checkin = approved_checkin or adj["requested_checkin"]
    final_checkout = approved_checkout or adj["requested_checkout"]
    
    # Validate giờ ra > giờ vào
    if final_checkin and final_checkout:
        ci_dt = datetime.fromisoformat(final_checkin.replace("Z", "+00:00"))
        co_dt = datetime.fromisoformat(final_checkout.replace("Z", "+00:00"))
        if co_dt <= ci_dt:
            raise ValueError("Giờ ra phải sau giờ vào")
    
    # Kiểm tra không trùng phiên khác
    if final_checkin and final_checkout:
        overlapping = conn.execute(
            """SELECT id FROM attendance_sessions 
               WHERE employee_id = ? AND id != ? AND status IN ('checked_in', 'checked_out')
               AND checkin_time < ? AND checkout_time > ?""",
            (adj["employee_id"], adj["session_id"] or 0, final_checkout, final_checkin)
        ).fetchone()
        if overlapping:
            raise ValueError("Giờ công trùng với phiên khác")
    
    status = AdjustmentStatus.APPROVED.value
    if (approved_checkin and approved_checkin != adj["requested_checkin"]) or \
       (approved_checkout and approved_checkout != adj["requested_checkout"]):
        status = AdjustmentStatus.MODIFIED.value
    
    conn.execute(
        """UPDATE attendance_adjustments SET 
           status = ?, decided_by = ?, decided_at = ?,
           decision_reason = ?, approved_checkin = ?, approved_checkout = ?
           WHERE id = ?""",
        (status, decided_by, now, reason, final_checkin, final_checkout, adj_id)
    )
    
    # Cập nhật hoặc tạo session
    if adj["session_id"]:
        # Cập nhật session hiện có
        updates = []
        params = []
        if final_checkin:
            updates.append("checkin_time = ?")
            params.append(final_checkin)
        if final_checkout:
            updates.append("checkout_time = ?")
            updates.append("status = 'checked_out'")
            params.append(final_checkout)
        
        if final_checkin and final_checkout:
            ci_dt = datetime.fromisoformat(final_checkin.replace("Z", "+00:00"))
            co_dt = datetime.fromisoformat(final_checkout.replace("Z", "+00:00"))
            minutes = int((co_dt - ci_dt).total_seconds() / 60)
            updates.append("approved_minutes = ?")
            params.append(minutes)
            
            # Tính lại lương
            from bot.services.auth_service import get_current_wage_rate
            rate = get_current_wage_rate(adj["employee_id"], final_checkin)
            if rate:
                wage = (minutes * rate) // 60
                updates.append("wage_amount = ?")
                params.append(wage)
        
        params.append(adj["session_id"])
        conn.execute(
            f"UPDATE attendance_sessions SET {', '.join(updates)} WHERE id = ?",
            params
        )
    else:
        # Tạo session mới (bù phiên thiếu)
        minutes = None
        wage_amount = None
        if final_checkin and final_checkout:
            ci_dt = datetime.fromisoformat(final_checkin.replace("Z", "+00:00"))
            co_dt = datetime.fromisoformat(final_checkout.replace("Z", "+00:00"))
            minutes = int((co_dt - ci_dt).total_seconds() / 60)
            from bot.services.auth_service import get_current_wage_rate
            rate = get_current_wage_rate(adj["employee_id"], final_checkin)
            if rate:
                wage_amount = (minutes * rate) // 60
        
        cursor = conn.execute(
            """INSERT INTO attendance_sessions 
               (employee_id, shift_date, checkin_time, checkout_time,
                approved_minutes, wage_amount, status, is_exception,
                exception_reason, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, 'owner_created', 1, ?, ?, ?)""",
            (adj["employee_id"], adj["shift_date"], final_checkin, final_checkout,
             minutes, wage_amount, f"Sửa công #{adj_id}: {reason}",
             now, decided_by)
        )
        new_session_id = cursor.lastrowid
        conn.execute(
            "UPDATE attendance_adjustments SET session_id = ? WHERE id = ?",
            (new_session_id, adj_id)
        )
    
    conn.commit()
    
    log_action(decided_by, "process_adjustment", "attendance_adjustments", adj_id,
               old_data=json.dumps({"checkin": adj["original_checkin"], "checkout": adj["original_checkout"]}),
               new_data=json.dumps({"checkin": final_checkin, "checkout": final_checkout}),
               reason=reason)
    
    return {"status": status, "checkin": final_checkin, "checkout": final_checkout}


# ==================== TRUY VẤN ====================

def get_employee_sessions(
    employee_id: int,
    from_date: str = None,
    to_date: str = None,
) -> List[dict]:
    """Lấy phiên công của nhân viên."""
    conn = get_connection()
    sql = """
        SELECT a.*, 
               (SELECT COUNT(*) FROM checkin_photos cp WHERE cp.session_id = a.id) as photo_count,
               (SELECT COUNT(*) FROM attendance_adjustments aa 
                WHERE aa.session_id = a.id AND aa.status = 'pending') as pending_adjustments
        FROM attendance_sessions a
        WHERE a.employee_id = ?
    """
    params = [employee_id]
    if from_date:
        sql += " AND a.shift_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND a.shift_date <= ?"
        params.append(to_date)
    sql += " ORDER BY a.shift_date DESC, a.checkin_time DESC"
    
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_all_sessions_for_period(from_date: str, to_date: str) -> List[dict]:
    """Lấy tất cả phiên công trong khoảng (cho chủ)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, u.display_name,
                  (SELECT COUNT(*) FROM checkin_photos cp WHERE cp.session_id = a.id) as photo_count
           FROM attendance_sessions a
           JOIN users u ON a.employee_id = u.telegram_id
           WHERE a.shift_date BETWEEN ? AND ?
           ORDER BY a.shift_date, a.checkin_time""",
        (from_date, to_date)
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_adjustments() -> List[dict]:
    """Lấy yêu cầu sửa công chờ duyệt."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT aa.*, u.display_name
           FROM attendance_adjustments aa
           JOIN users u ON aa.employee_id = u.telegram_id
           WHERE aa.status = 'pending'
           ORDER BY aa.created_at""",
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_sessions() -> List[dict]:
    """Lấy phiên công đang mở (chưa check-out)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, u.display_name
           FROM attendance_sessions a
           JOIN users u ON a.employee_id = u.telegram_id
           WHERE a.status = 'checked_in'
           ORDER BY a.checkin_time""",
    ).fetchall()
    return [dict(r) for r in rows]


def calculate_wage_for_session(session_id: int) -> dict:
    """
    Tính lương cho một phiên công.
    Hỗ trợ đổi mức lương giữa phiên.
    """
    conn = get_connection()
    session = conn.execute(
        "SELECT * FROM attendance_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        raise ValueError("Không tìm thấy phiên công")
    
    if not session["checkin_time"] or not session["checkout_time"]:
        return {"error": "Phiên chưa có đủ giờ vào/ra"}
    
    from bot.services.auth_service import get_current_wage_rate
    
    ci_dt = datetime.fromisoformat(session["checkin_time"].replace("Z", "+00:00"))
    co_dt = datetime.fromisoformat(session["checkout_time"].replace("Z", "+00:00"))
    
    # Lấy tất cả mức lương có hiệu lực trong khoảng phiên
    rates = conn.execute(
        """SELECT * FROM wage_rates 
           WHERE employee_id = ? 
           AND effective_from <= ?
           AND (effective_to IS NULL OR effective_to > ?)
           ORDER BY effective_from""",
        (session["employee_id"], session["checkout_time"], session["checkin_time"])
    ).fetchall()
    
    if not rates:
        return {"error": "Chưa có mức lương"}
    
    # Nếu chỉ 1 mức lương trong phiên
    if len(rates) == 1:
        minutes = session["approved_minutes"] or int((co_dt - ci_dt).total_seconds() / 60)
        rate = rates[0]["hourly_rate"]
        amount = (minutes * rate) // 60
        return {
            "segments": [{"minutes": minutes, "rate": rate, "amount": amount}],
            "total_minutes": minutes,
            "total_amount": amount,
        }
    
    # Nhiều mức lương - phân đoạn
    segments = []
    total_amount = 0
    total_minutes = 0
    
    for i, r in enumerate(rates):
        seg_start = max(ci_dt, datetime.fromisoformat(r["effective_from"].replace("Z", "+00:00")))
        if r["effective_to"]:
            seg_end = min(co_dt, datetime.fromisoformat(r["effective_to"].replace("Z", "+00:00")))
        else:
            seg_end = co_dt
        
        if seg_start >= seg_end:
            continue
        
        seg_minutes = int((seg_end - seg_start).total_seconds() / 60)
        seg_amount = (seg_minutes * r["hourly_rate"]) // 60
        
        segments.append({
            "minutes": seg_minutes,
            "rate": r["hourly_rate"],
            "amount": seg_amount,
        })
        total_amount += seg_amount
        total_minutes += seg_minutes
    
    return {
        "segments": segments,
        "total_minutes": total_minutes,
        "total_amount": total_amount,
    }
