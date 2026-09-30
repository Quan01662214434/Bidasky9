"""
Service quản lý ca quỹ (cash shift).
Mỗi quầy tiền chỉ có một ca quỹ đang mở.
"""

import json
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso, vn_now
from bot.constants import (
    CashShiftStatus, TransactionType,
    CASH_IN_TYPES, CASH_OUT_TYPES
)
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


def get_open_cash_shift(employee_id: int = None) -> Optional[dict]:
    """Lấy ca quỹ đang mở. Nếu không truyền employee_id, lấy bất kỳ ca mở nào."""
    conn = get_connection()
    if employee_id:
        row = conn.execute(
            "SELECT * FROM cash_shifts WHERE employee_id = ? AND status = 'open'",
            (employee_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM cash_shifts WHERE status = 'open' ORDER BY opened_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_any_open_cash_shift() -> Optional[dict]:
    """Lấy ca quỹ đang mở bất kỳ (kiểm tra chỉ có 1)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM cash_shifts WHERE status = 'open'"
    ).fetchall()
    if len(rows) > 1:
        logger.warning("Có %d ca quỹ đang mở cùng lúc!", len(rows))
    return dict(rows[0]) if rows else None


def open_cash_shift(
    employee_id: int,
    opening_cash: int,
    shift_date: str,
    previous_shift_id: int = None,
    expected_opening: int = None,
    opening_note: str = None
) -> dict:
    """
    Mở ca quỹ mới.
    Kiểm tra không có ca quỹ đang mở.
    """
    conn = get_connection()
    
    # Kiểm tra ca đang mở
    existing = conn.execute(
        "SELECT id FROM cash_shifts WHERE status = 'open'"
    ).fetchone()
    if existing:
        raise ValueError("Đã có ca quỹ đang mở (mã ca: {})".format(existing["id"]))
    
    opening_diff = 0
    if expected_opening is not None:
        opening_diff = opening_cash - expected_opening
    
    now = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO cash_shifts 
           (employee_id, shift_date, opening_cash, expected_opening, opening_diff,
            previous_shift_id, status, opened_at, created_at, opening_note)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
        (employee_id, shift_date, opening_cash, expected_opening, opening_diff,
         previous_shift_id, now, now, opening_note)
    )
    shift_id = cursor.lastrowid
    conn.commit()
    
    log_action(employee_id, "open_cash_shift", "cash_shifts", shift_id,
               new_data=json.dumps({"opening_cash": opening_cash}))
    
    return {"id": shift_id, "opening_cash": opening_cash, "status": "open"}


def calculate_expected_closing(shift_id: int) -> dict:
    """
    Tính tiền mặt kỳ vọng cuối ca.
    
    Công thức:
    Tiền mặt kỳ vọng = opening_cash 
        + total_bill_revenue 
        - bank_transfers_for_bills_in_shift
        - remaining_debt_from_bills_in_shift
        - non_cash_settlements_for_bills_in_shift
        - cash_out_transactions
        - cash_to_owner
        + fund_additions
        + old_debt_collected_cash
        + other_cash_in
    """
    conn = get_connection()
    shift = conn.execute("SELECT * FROM cash_shifts WHERE id = ?", (shift_id,)).fetchone()
    if not shift:
        raise ValueError("Không tìm thấy ca quỹ")
    
    opening = shift["opening_cash"]
    total_bill = shift["total_bill_revenue"] or 0
    
    # Chuyển khoản thanh toán bill TRONG ca
    bank_transfers = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type = 'bank_transfer'""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Nợ còn lại từ bill phát sinh trong ca
    remaining_debt = conn.execute(
        """SELECT COALESCE(SUM(remaining_debt), 0) as total FROM debt_records 
           WHERE cash_shift_id = ?""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Các khoản tiền mặt RA (chi, ứng lương, giao chủ, nhập hàng...)
    cash_out = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type IN ({})""".format(
            ",".join(f"'{t.value}'" for t in CASH_OUT_TYPES)
        ),
        (shift_id,)
    ).fetchone()["total"]
    
    # Thu nợ ca trước bằng TIỀN MẶT
    old_debt_cash = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type = 'debt_collect_cash'""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Bổ sung quỹ
    fund_add = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type = 'fund_add'""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Các khoản tiền mặt VÀO khác
    other_cash_in = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type IN ('employee_item_cash', 'refund_cash', 'other_cash_in')""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Thu nợ CK (không ảnh hưởng tiền quầy)
    old_debt_transfer = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type = 'debt_collect_transfer'""",
        (shift_id,)
    ).fetchone()["total"]
    
    # Chi CK (không giảm quầy)
    expense_transfer = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) as total FROM transactions 
           WHERE cash_shift_id = ? AND type = 'expense_transfer'""",
        (shift_id,)
    ).fetchone()["total"]
    
    expected = (
        opening
        + total_bill
        - bank_transfers
        - remaining_debt
        - cash_out
        + old_debt_cash
        + fund_add
        + other_cash_in
    )
    
    # Danh sách khoản chờ xác minh
    pending_items = conn.execute(
        """SELECT COUNT(*) as cnt FROM transactions 
           WHERE cash_shift_id = ? AND verified = 0 AND type IN ('bank_transfer', 'debt_collect_transfer')""",
        (shift_id,)
    ).fetchone()["cnt"]
    
    # Nợ vượt quyền
    over_limit_debts = conn.execute(
        """SELECT COUNT(*) as cnt FROM debt_records 
           WHERE cash_shift_id = ? AND is_over_limit = 1""",
        (shift_id,)
    ).fetchone()["cnt"]
    
    return {
        "opening_cash": opening,
        "total_bill": total_bill,
        "bank_transfers": bank_transfers,
        "remaining_debt": remaining_debt,
        "cash_out": cash_out,
        "old_debt_cash": old_debt_cash,
        "old_debt_transfer": old_debt_transfer,
        "fund_add": fund_add,
        "other_cash_in": other_cash_in,
        "expense_transfer": expense_transfer,
        "expected_closing": expected,
        "pending_verifications": pending_items,
        "over_limit_debts": over_limit_debts,
    }


def close_cash_shift(
    shift_id: int,
    total_bill_revenue: int,
    bill_report_start: str,
    bill_report_end: str,
    closing_cash_counted: int,
    closing_note: str = None,
    active_tables_note: str = None,
    closed_by: int = None,
    bill_report_photo_id: str = None,
) -> dict:
    """
    Kết ca quỹ.
    Kiểm tra giao dịch chờ, tính chênh lệch.
    """
    conn = get_connection()
    
    shift = conn.execute(
        "SELECT * FROM cash_shifts WHERE id = ? AND status = 'open'",
        (shift_id,)
    ).fetchone()
    if not shift:
        raise ValueError("Ca quỹ không tồn tại hoặc đã đóng")
    
    # Cập nhật tổng bill
    conn.execute(
        """UPDATE cash_shifts SET total_bill_revenue = ?, bill_report_start = ?,
           bill_report_end = ?, bill_report_photo_id = ?
           WHERE id = ?""",
        (total_bill_revenue, bill_report_start, bill_report_end, bill_report_photo_id, shift_id)
    )
    conn.commit()
    
    # Tính kỳ vọng
    calc = calculate_expected_closing(shift_id)
    expected = calc["expected_closing"]
    diff = closing_cash_counted - expected
    
    # Xác định trạng thái
    has_issues = (
        calc["pending_verifications"] > 0 
        or calc["over_limit_debts"] > 0
        or diff != 0
    )
    status = CashShiftStatus.PENDING_REVIEW.value if has_issues else CashShiftStatus.CLOSED.value
    
    # Tạo snapshot
    snapshot = json.dumps({
        **calc,
        "closing_cash_counted": closing_cash_counted,
        "diff": diff,
        "closing_note": closing_note,
        "active_tables": active_tables_note,
    }, ensure_ascii=False)
    
    now = now_utc_iso()
    conn.execute(
        """UPDATE cash_shifts SET 
           closing_cash_counted = ?, expected_closing_cash = ?,
           closing_diff = ?, closing_diff_reason = ?,
           closing_note = ?, active_tables_note = ?,
           closing_snapshot = ?, status = ?, closed_at = ?
           WHERE id = ?""",
        (closing_cash_counted, expected, diff, closing_note,
         closing_note, active_tables_note, snapshot, status, now, shift_id)
    )
    conn.commit()
    
    log_action(closed_by or shift["employee_id"], "close_cash_shift", "cash_shifts", shift_id,
               new_data=snapshot)
    
    
    return {
        "shift_id": shift_id,
        "status": status,
        "expected": expected,
        "counted": closing_cash_counted,
        "diff": diff,
        "details": calc,
    }


def create_draft_handover(
    shift_id: int,
    total_bill_revenue: int,
    closing_cash_counted: int,
    closing_note: str = None,
    active_tables_note: str = None,
    drafted_by: int = None,
    bill_report_photo_id: str = None,
) -> dict:
    """Tạo bản nháp kết ca để chờ bàn giao."""
    conn = get_connection()
    
    # Tính kỳ vọng
    calc = calculate_expected_closing(shift_id)
    expected = calc["expected_closing"]
    diff = closing_cash_counted - expected
    
    draft_data = {
        "total_bill_revenue": total_bill_revenue,
        "closing_cash_counted": closing_cash_counted,
        "closing_note": closing_note,
        "active_tables_note": active_tables_note,
        "expected_closing_cash": expected,
        "diff": diff,
        "calc_details": calc,
        "bill_report_photo_id": bill_report_photo_id,
    }
    
    conn.execute(
        """UPDATE cash_shifts SET 
           handover_status = 'pending_verification',
           closing_draft_data = ?
           WHERE id = ?""",
        (json.dumps(draft_data, ensure_ascii=False), shift_id)
    )
    conn.commit()
    
    return draft_data

def verify_handover(shift_id: int, verified_by: int):
    """Ca sau xác nhận bàn giao."""
    conn = get_connection()
    conn.execute(
        """UPDATE cash_shifts SET 
           handover_status = 'verified',
           handover_to_employee_id = ?,
           verified_at = ?
           WHERE id = ?""",
        (verified_by, now_utc_iso(), shift_id)
    )
    conn.commit()

def finalize_handover_and_checkout(shift_id: int, closed_by: int):
    """Hoàn tất chốt sổ (Thời điểm T).
       1. Đóng cash shift.
       2. Checkout phiên điểm danh ca trước.
       3. Tính lương ca sau (paid_start_at).
    """
    conn = get_connection()
    shift = conn.execute("SELECT * FROM cash_shifts WHERE id = ?", (shift_id,)).fetchone()
    
    if shift["handover_status"] != 'verified':
        # Ngoại lệ: nếu không có ai nhận (VD: ca cuối ngày), vẫn cho phép chốt
        pass 
        
    draft = json.loads(shift["closing_draft_data"] or "{}")
    if not draft:
        raise ValueError("Lỗi: Không tìm thấy dữ liệu nháp")
        
    now = vn_now()
    now_iso_utc = now_utc_iso()
    
    # 1. Đóng cash shift
    calc = draft.get("calc_details", {})
    has_issues = (
        calc.get("pending_verifications", 0) > 0 
        or calc.get("over_limit_debts", 0) > 0
        or draft.get("diff", 0) != 0
    )
    status = CashShiftStatus.PENDING_REVIEW.value if has_issues else CashShiftStatus.CLOSED.value
    
    conn.execute(
        """UPDATE cash_shifts SET 
           total_bill_revenue = ?, bill_report_photo_id = ?,
           closing_cash_counted = ?, expected_closing_cash = ?,
           closing_diff = ?, closing_note = ?, active_tables_note = ?,
           closing_snapshot = ?, status = ?, closed_at = ?, handover_status = 'locked'
           WHERE id = ?""",
        (
            draft.get("total_bill_revenue"), draft.get("bill_report_photo_id"),
            draft.get("closing_cash_counted"), draft.get("expected_closing_cash"),
            draft.get("diff"), draft.get("closing_note"), draft.get("active_tables_note"),
            shift["closing_draft_data"], status, now_iso_utc, shift_id
        )
    )
    
    # 2. Checkout phiên trước (Tại thời điểm T)
    from bot.services.attendance_service import checkout
    try:
        # Tạm thời bypass kiểm tra active_cash_shift bằng cách truyền thẳng checkout logic ở đây
        session = conn.execute(
            """SELECT * FROM attendance_sessions 
               WHERE employee_id = ? AND status = 'checked_in'
               ORDER BY created_at DESC LIMIT 1""",
            (closed_by,)
        ).fetchone()
        
        if session:
            checkin_dt = datetime.fromisoformat(session["checkin_time"].replace("Z", "+00:00"))
            checkout_dt = datetime.fromisoformat(now_iso_utc.replace("Z", "+00:00"))
            
            if checkin_dt.tzinfo is None:
                checkin_dt = pytz.utc.localize(checkin_dt)
            if checkout_dt.tzinfo is None:
                checkout_dt = pytz.utc.localize(checkout_dt)
            
            total_minutes = int((checkout_dt - checkin_dt).total_seconds() / 60)
            approved_minutes = total_minutes - session.get("break_minutes", 0)
            
            from bot.services.auth_service import get_current_wage_rate
            wage_rate = get_current_wage_rate(closed_by, session["checkin_time"])
            wage_amount = (approved_minutes * wage_rate) // 60 if wage_rate and approved_minutes else None
            
            conn.execute(
                """UPDATE attendance_sessions SET 
                   checkout_time = ?, approved_minutes = ?, wage_amount = ?, status = 'checked_out'
                   WHERE id = ?""",
                (now_iso_utc, approved_minutes, wage_amount, session["id"])
            )
    except Exception as e:
        logger.error(f"Lỗi checkout khi bàn giao: {e}")
        
    # 3. Tính lương ca sau (paid_start_at) (Thời điểm T)
    if shift["handover_to_employee_id"]:
        next_session = conn.execute(
            """SELECT * FROM attendance_sessions 
               WHERE employee_id = ? AND status = 'pending_handover'
               ORDER BY created_at DESC LIMIT 1""",
            (shift["handover_to_employee_id"],)
        ).fetchone()
        
        if next_session:
            conn.execute(
                """UPDATE attendance_sessions SET 
                   checkin_time = ?, status = 'checked_in'
                   WHERE id = ?""",
                (now_iso_utc, next_session["id"])
            )
            
            # 4. Tự động mở Ca Quỹ (Cash Shift) cho ca sau luôn vì họ đã xác nhận tiền
            conn.execute(
                """INSERT INTO cash_shifts (
                    employee_id, opened_at, opening_cash, expected_opening_cash,
                    opening_note, status, previous_shift_id
                ) VALUES (?, ?, ?, ?, ?, 'open', ?)""",
                (
                    shift["handover_to_employee_id"], 
                    now_iso_utc, 
                    draft.get("closing_cash_counted"), 
                    draft.get("closing_cash_counted"),
                    "Tự động mở từ xác nhận bàn giao",
                    shift_id
                )
            )
            
    conn.commit()
    
    return {
        "status": status,
        "diff": draft.get("diff"),
        "closed_at": now_iso_utc,
    }


def get_last_closed_shift() -> Optional[dict]:
    """Lấy ca quỹ đã đóng gần nhất."""
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM cash_shifts 
           WHERE status IN ('closed', 'pending_review')
           ORDER BY closed_at DESC LIMIT 1"""
    ).fetchone()
    return dict(row) if row else None


def get_shift_transactions(shift_id: int) -> List[dict]:
    """Lấy danh sách giao dịch của ca."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, u.display_name as recorder_name
           FROM transactions t
           LEFT JOIN users u ON t.recorded_by = u.telegram_id
           WHERE t.cash_shift_id = ?
           ORDER BY t.created_at""",
        (shift_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def acknowledge_debt_handover(
    shift_id: int, 
    employee_id: int,
    debt_snapshot: str
):
    """Ghi nhận đã xem nợ bàn giao."""
    conn = get_connection()
    now = now_utc_iso()
    conn.execute(
        """UPDATE cash_shifts SET debt_ack_at = ?, debt_ack_snapshot = ?
           WHERE id = ?""",
        (now, debt_snapshot, shift_id)
    )
    conn.commit()
    log_action(employee_id, "ack_debt_handover", "cash_shifts", shift_id)
