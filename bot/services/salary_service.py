"""
Service lương, ứng lương và bảng lương.
"""

import json
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import PayrollStatus, AdvanceStatus, TransactionType
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


# ==================== ỨNG LƯƠNG ====================

def request_advance(employee_id: int, amount: int, reason: str = None) -> int:
    """Nhân viên đề nghị ứng lương."""
    conn = get_connection()
    now = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO salary_advances 
           (employee_id, amount, reason, status, created_at, updated_at)
           VALUES (?, ?, ?, 'requested', ?, ?)""",
        (employee_id, amount, reason, now, now)
    )
    adv_id = cursor.lastrowid
    conn.commit()
    log_action(employee_id, "request_advance", "salary_advances", adv_id)
    return adv_id


def approve_advance(adv_id: int, approved_by: int) -> bool:
    """Chủ duyệt ứng lương."""
    conn = get_connection()
    conn.execute(
        """UPDATE salary_advances SET status = 'approved', 
           approved_by = ?, approved_at = ?, updated_at = ?
           WHERE id = ? AND status = 'requested'""",
        (approved_by, now_utc_iso(), now_utc_iso(), adv_id)
    )
    conn.commit()
    log_action(approved_by, "approve_advance", "salary_advances", adv_id)
    return True


def reject_advance(adv_id: int, rejected_by: int, reason: str = None) -> bool:
    """Chủ từ chối ứng lương."""
    conn = get_connection()
    now = now_utc_iso()
    conn.execute(
        """UPDATE salary_advances SET status = 'rejected',
           approved_by = ?, approved_at = ?, reject_reason = ?, updated_at = ?
           WHERE id = ? AND status = 'requested'""",
        (rejected_by, now, reason, now, adv_id)
    )
    conn.commit()
    return True


def deliver_advance(
    adv_id: int,
    delivered_by: int,
    payment_source: str,  # cash/transfer/other
    cash_shift_id: int = None,
) -> dict:
    """
    Xác nhận đã giao tiền ứng.
    Nếu từ tiền mặt quầy → tạo giao dịch chi.
    """
    conn = get_connection()
    
    adv = conn.execute(
        "SELECT * FROM salary_advances WHERE id = ? AND status = 'approved'",
        (adv_id,)
    ).fetchone()
    if not adv:
        raise ValueError("Khoản ứng chưa được duyệt hoặc đã giao")
    
    now = now_utc_iso()
    txn_id = None
    
    if payment_source == "cash":
        if not cash_shift_id:
            raise ValueError("Ứng tiền mặt cần ca quỹ đang mở")
        # Tạo giao dịch
        from bot.services.transaction_service import create_transaction
        txn = create_transaction(
            cash_shift_id=cash_shift_id,
            txn_type=TransactionType.SALARY_ADVANCE_CASH,
            amount=adv["amount"],
            recorded_by=delivered_by,
            advance_id=adv_id,
            description=f"Ứng lương NV #{adv['employee_id']}",
        )
        txn_id = txn["id"]
    
    conn.execute(
        """UPDATE salary_advances SET status = 'delivered',
           delivered_at = ?, delivered_by = ?, payment_source = ?,
           cash_shift_id = ?, transaction_id = ?,
           remaining_unsettled = ?, updated_at = ?
           WHERE id = ?""",
        (now, delivered_by, payment_source, cash_shift_id, txn_id,
         adv["amount"], now, adv_id)
    )
    conn.commit()
    
    log_action(delivered_by, "deliver_advance", "salary_advances", adv_id,
               new_data=json.dumps({"source": payment_source, "amount": adv["amount"]}))
    
    return {"advance_id": adv_id, "transaction_id": txn_id}


def get_unsettled_advances(employee_id: int) -> List[dict]:
    """Lấy ứng lương chưa đối trừ."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM salary_advances 
           WHERE employee_id = ? AND status = 'delivered'
           AND (remaining_unsettled > 0 OR remaining_unsettled IS NULL)
           ORDER BY delivered_at""",
        (employee_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ==================== BẢNG LƯƠNG ====================

def calculate_payroll(employee_id: int, year: int, month: int) -> dict:
    """
    Tính bảng lương tháng.
    
    Còn thanh toán = tiền công + thưởng - ứng chưa đối trừ - đồ dùng chưa trả - khấu trừ - đã trả
    """
    conn = get_connection()
    
    # Khoảng thời gian
    from_date = f"{year:04d}-{month:02d}-01"
    if month == 12:
        to_date = f"{year + 1:04d}-01-01"
    else:
        to_date = f"{year:04d}-{month + 1:02d}-01"
    
    # Lấy phiên công đã check-out
    sessions = conn.execute(
        """SELECT * FROM attendance_sessions 
           WHERE employee_id = ? AND shift_date >= ? AND shift_date < ?
           AND status IN ('checked_out', 'owner_created')
           AND approved_minutes IS NOT NULL""",
        (employee_id, from_date, to_date)
    ).fetchall()
    
    # Kiểm tra phiên chưa xử lý
    unresolved_sessions = conn.execute(
        """SELECT COUNT(*) as cnt FROM attendance_sessions 
           WHERE employee_id = ? AND shift_date >= ? AND shift_date < ?
           AND (status = 'checked_in' OR approved_minutes IS NULL OR is_exception = 1)""",
        (employee_id, from_date, to_date)
    ).fetchone()["cnt"]
    
    pending_adjustments = conn.execute(
        """SELECT COUNT(*) as cnt FROM attendance_adjustments 
           WHERE employee_id = ? AND shift_date >= ? AND shift_date < ?
           AND status = 'pending'""",
        (employee_id, from_date, to_date)
    ).fetchone()["cnt"]
    
    # Tính công
    total_minutes = 0
    total_wage = 0
    entries = []
    for s in sessions:
        from bot.services.attendance_service import calculate_wage_for_session
        wage_calc = calculate_wage_for_session(s["id"])
        if "error" not in wage_calc:
            total_minutes += wage_calc["total_minutes"]
            total_wage += wage_calc["total_amount"]
            entries.append({
                "session_id": s["id"],
                "date": s["shift_date"],
                "minutes": wage_calc["total_minutes"],
                "amount": wage_calc["total_amount"],
                "segments": wage_calc["segments"],
            })
    
    # Ứng lương chưa đối trừ
    advances = get_unsettled_advances(employee_id)
    total_advances = sum(a.get("remaining_unsettled") or a["amount"] for a in advances)
    
    # Đồ dùng chưa trả
    from bot.services.consumable_service import get_employee_consumable_summary
    consumable_summary = get_employee_consumable_summary(employee_id, year, month)
    total_consumables = consumable_summary["total_amount"]
    has_unpriced = consumable_summary["has_unpriced"]
    
    # Tính net
    base_wage = total_wage
    bonus = 0  # Chủ nhập riêng
    deductions = 0
    already_paid = 0
    
    # Kiểm tra đã có payroll
    existing = conn.execute(
        "SELECT * FROM payroll WHERE employee_id = ? AND period_year = ? AND period_month = ?",
        (employee_id, year, month)
    ).fetchone()
    if existing:
        bonus = existing["bonus"] or 0
        deductions = existing["deductions"] or 0
        already_paid = existing["already_paid"] or 0
    
    net_pay = base_wage + bonus - total_advances - total_consumables - deductions - already_paid
    
    has_unresolved = unresolved_sessions > 0 or pending_adjustments > 0 or has_unpriced
    unresolved_details = []
    if unresolved_sessions > 0:
        unresolved_details.append(f"{unresolved_sessions} phiên công chưa xử lý")
    if pending_adjustments > 0:
        unresolved_details.append(f"{pending_adjustments} yêu cầu sửa chờ duyệt")
    if has_unpriced:
        unresolved_details.append("Có món chưa nhập giá")
    
    return {
        "employee_id": employee_id,
        "year": year,
        "month": month,
        "entries": entries,
        "total_minutes": total_minutes,
        "total_sessions": len(sessions),
        "base_wage": base_wage,
        "bonus": bonus,
        "total_advances": total_advances,
        "total_consumables": total_consumables,
        "deductions": deductions,
        "already_paid": already_paid,
        "net_pay": net_pay,
        "has_unresolved": has_unresolved,
        "unresolved_details": unresolved_details,
        "advances_detail": advances,
        "consumable_detail": consumable_summary,
    }


def save_payroll(employee_id: int, year: int, month: int, data: dict) -> int:
    """Lưu/cập nhật bảng lương."""
    conn = get_connection()
    now = now_utc_iso()
    
    existing = conn.execute(
        "SELECT id FROM payroll WHERE employee_id = ? AND period_year = ? AND period_month = ?",
        (employee_id, year, month)
    ).fetchone()
    
    status = PayrollStatus.DRAFT.value
    if data["has_unresolved"]:
        status = PayrollStatus.DRAFT.value
    
    if existing:
        conn.execute(
            """UPDATE payroll SET 
               total_approved_minutes = ?, total_sessions = ?,
               base_wage = ?, bonus = ?, total_advances = ?,
               total_consumables = ?, deductions = ?, already_paid = ?,
               net_pay = ?, status = ?, has_unresolved = ?,
               unresolved_details = ?, updated_at = ?
               WHERE id = ?""",
            (data["total_minutes"], data["total_sessions"],
             data["base_wage"], data["bonus"], data["total_advances"],
             data["total_consumables"], data["deductions"], data["already_paid"],
             data["net_pay"], status, 1 if data["has_unresolved"] else 0,
             json.dumps(data["unresolved_details"], ensure_ascii=False), now,
             existing["id"])
        )
        conn.commit()
        return existing["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO payroll 
               (employee_id, period_year, period_month,
                total_approved_minutes, total_sessions,
                base_wage, bonus, total_advances, total_consumables,
                deductions, already_paid, net_pay,
                status, has_unresolved, unresolved_details,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, year, month,
             data["total_minutes"], data["total_sessions"],
             data["base_wage"], data["bonus"], data["total_advances"],
             data["total_consumables"], data["deductions"], data["already_paid"],
             data["net_pay"], status, 1 if data["has_unresolved"] else 0,
             json.dumps(data["unresolved_details"], ensure_ascii=False),
             now, now)
        )
        conn.commit()
        return cursor.lastrowid


def lock_payroll(payroll_id: int, locked_by: int) -> bool:
    """Khóa bảng lương - tạo snapshot."""
    conn = get_connection()
    
    payroll = conn.execute(
        "SELECT * FROM payroll WHERE id = ?", (payroll_id,)
    ).fetchone()
    if not payroll:
        return False
    
    if payroll["has_unresolved"]:
        raise ValueError("Không thể chốt khi còn công/giá chưa xử lý")
    
    snapshot = json.dumps(dict(payroll), ensure_ascii=False, default=str)
    now = now_utc_iso()
    
    conn.execute(
        """UPDATE payroll SET status = 'approved', 
           owner_approved_at = ?, owner_approved_by = ?,
           snapshot = ?, updated_at = ?
           WHERE id = ?""",
        (now, locked_by, snapshot, now, payroll_id)
    )
    conn.commit()
    
    log_action(locked_by, "lock_payroll", "payroll", payroll_id, new_data=snapshot)
    return True


def record_salary_payment(
    payroll_id: int,
    amount: int,
    payment_source: str,
    paid_by: int,
    cash_shift_id: int = None,
) -> dict:
    """Ghi thanh toán lương."""
    conn = get_connection()
    
    payroll = conn.execute("SELECT * FROM payroll WHERE id = ?", (payroll_id,)).fetchone()
    if not payroll:
        raise ValueError("Không tìm thấy bảng lương")
    
    txn_id = None
    if payment_source == "cash":
        if not cash_shift_id:
            raise ValueError("Trả lương tiền mặt cần ca quỹ đang mở")
        from bot.services.transaction_service import create_transaction
        txn = create_transaction(
            cash_shift_id=cash_shift_id,
            txn_type=TransactionType.SALARY_PAY_CASH,
            amount=amount,
            recorded_by=paid_by,
            payroll_id=payroll_id,
            description=f"Trả lương NV #{payroll['employee_id']} T{payroll['period_month']}/{payroll['period_year']}",
        )
        txn_id = txn["id"]
        if txn.get("duplicate"):
            raise ValueError("Khoản trả lương này đã được ghi")
    
    new_paid = (payroll["already_paid"] or 0) + amount
    new_net = payroll["net_pay"] - amount  # net_pay đã trừ already_paid
    
    now = now_utc_iso()
    conn.execute(
        """UPDATE payroll SET already_paid = ?, net_pay = ?,
           paid_at = ?, updated_at = ?
           WHERE id = ?""",
        (new_paid, payroll["net_pay"], now, now, payroll_id)  # Giữ net_pay gốc
    )
    conn.commit()
    
    # Recalc net properly
    # net_pay cần tính lại: base + bonus - advances - consumables - deductions - new_paid
    recalc_net = (
        (payroll["base_wage"] or 0) + (payroll["bonus"] or 0)
        - (payroll["total_advances"] or 0) - (payroll["total_consumables"] or 0)
        - (payroll["deductions"] or 0) - new_paid
    )
    conn.execute("UPDATE payroll SET net_pay = ?, already_paid = ? WHERE id = ?",
                 (recalc_net, new_paid, payroll_id))
    conn.commit()
    
    return {"payroll_id": payroll_id, "paid": amount, "new_net": recalc_net, "txn_id": txn_id}
