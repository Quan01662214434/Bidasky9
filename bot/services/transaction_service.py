"""
Service giao dịch: chuyển khoản, chi, giao tiền, bổ sung quỹ.
Mọi giao dịch tiền mặt phải thuộc ca quỹ đang mở.
"""

import json
import uuid
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import TransactionType, CASH_IN_TYPES, CASH_OUT_TYPES, NON_CASH_TYPES
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


def create_transaction(
    cash_shift_id: int | None,
    txn_type: TransactionType,
    amount: int,
    recorded_by: int,
    bill_code: str = None,
    description: str = None,
    category: str = None,
    payment_source: str = None,
    debt_record_id: int = None,
    debt_payment_id: int = None,
    advance_id: int = None,
    payroll_id: int = None,
    receipt_id: int = None,
    note: str = None,
    idempotency_key: str = None,
) -> dict:
    """
    Tạo giao dịch mới.
    
    Quy tắc:
    - Giao dịch tiền mặt (CASH_IN/CASH_OUT) phải có cash_shift_id
    - Giao dịch ngoài quầy (NON_CASH) có thể không có cash_shift_id
    - amount luôn dương
    - Idempotency key chống ghi trùng
    """
    if amount <= 0:
        raise ValueError("Số tiền phải lớn hơn 0")
    
    # Kiểm tra cần ca quỹ
    if txn_type in CASH_IN_TYPES or txn_type in CASH_OUT_TYPES:
        if cash_shift_id is None:
            raise ValueError("Giao dịch tiền mặt phải thuộc ca quỹ đang mở")
    
    conn = get_connection()
    
    # Tạo idempotency key nếu chưa có
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())
    
    # Kiểm tra trùng
    existing = conn.execute(
        "SELECT id FROM transactions WHERE idempotency_key = ?",
        (idempotency_key,)
    ).fetchone()
    if existing:
        logger.warning("Giao dịch trùng idempotency_key: %s", idempotency_key)
        return {"id": existing["id"], "duplicate": True}
    
    now = now_utc_iso()
    cursor = conn.execute(
        """INSERT INTO transactions 
           (cash_shift_id, type, amount, bill_code, description, category,
            payment_source, debt_record_id, debt_payment_id, advance_id,
            payroll_id, receipt_id, idempotency_key, recorded_by, created_at, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cash_shift_id, txn_type.value, amount, bill_code, description,
         category, payment_source, debt_record_id, debt_payment_id,
         advance_id, payroll_id, receipt_id, idempotency_key,
         recorded_by, now, note)
    )
    txn_id = cursor.lastrowid
    conn.commit()
    
    log_action(recorded_by, f"create_transaction_{txn_type.value}", "transactions", txn_id,
               new_data=json.dumps({"amount": amount, "type": txn_type.value, "bill": bill_code}))
    
    return {"id": txn_id, "duplicate": False}


def record_bank_transfer(
    cash_shift_id: int,
    bill_code: str,
    amount: int,
    recorded_by: int,
    verified: bool = False,
    note: str = None,
) -> dict:
    """Ghi chuyển khoản thanh toán bill."""
    conn = get_connection()
    
    # Cảnh báo bill đã tồn tại
    existing_transfers = conn.execute(
        """SELECT id, amount FROM transactions 
           WHERE bill_code = ? AND type = 'bank_transfer'""",
        (bill_code,)
    ).fetchall()
    
    result = create_transaction(
        cash_shift_id=cash_shift_id,
        txn_type=TransactionType.BANK_TRANSFER,
        amount=amount,
        recorded_by=recorded_by,
        bill_code=bill_code,
        description=f"CK bill {bill_code}",
        payment_source="transfer",
        note=note,
    )
    
    if verified:
        conn.execute(
            "UPDATE transactions SET verified = 1, verified_by = ?, verified_at = ? WHERE id = ?",
            (recorded_by, now_utc_iso(), result["id"])
        )
        conn.commit()
    
    result["existing_transfers"] = [dict(r) for r in existing_transfers]
    return result


def record_expense(
    cash_shift_id: int | None,
    amount: int,
    description: str,
    category: str,
    recorded_by: int,
    is_cash: bool = True,
    note: str = None,
) -> dict:
    """Ghi chi tiêu."""
    txn_type = TransactionType.EXPENSE_CASH if is_cash else TransactionType.EXPENSE_TRANSFER
    
    return create_transaction(
        cash_shift_id=cash_shift_id if is_cash else None,
        txn_type=txn_type,
        amount=amount,
        recorded_by=recorded_by,
        description=description,
        category=category,
        payment_source="cash" if is_cash else "transfer",
        note=note,
    )


def record_cash_to_owner(
    cash_shift_id: int,
    amount: int,
    recorded_by: int,
    reason: str = None,
) -> dict:
    """Ghi giao tiền cho chủ / rút tiền quỹ."""
    return create_transaction(
        cash_shift_id=cash_shift_id,
        txn_type=TransactionType.CASH_TO_OWNER,
        amount=amount,
        recorded_by=recorded_by,
        description="Giao tiền chủ",
        note=reason,
    )


def record_fund_addition(
    cash_shift_id: int,
    amount: int,
    recorded_by: int,
    source: str = None,
    reason: str = None,
) -> dict:
    """Ghi bổ sung quỹ."""
    return create_transaction(
        cash_shift_id=cash_shift_id,
        txn_type=TransactionType.FUND_ADD,
        amount=amount,
        recorded_by=recorded_by,
        description=f"Bổ sung quỹ: {source or ''}",
        note=reason,
    )


def get_bill_transactions(bill_code: str) -> List[dict]:
    """Lấy tất cả giao dịch liên quan bill."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, u.display_name as recorder_name
           FROM transactions t
           LEFT JOIN users u ON t.recorded_by = u.telegram_id
           WHERE t.bill_code = ?
           ORDER BY t.created_at""",
        (bill_code,)
    ).fetchall()
    return [dict(r) for r in rows]


def check_bill_total_consistency(bill_code: str, total_bill: int) -> dict:
    """
    Kiểm tra tổng thanh toán/nợ không vượt tổng bill.
    """
    conn = get_connection()
    
    transfers = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE bill_code = ? AND type = 'bank_transfer'",
        (bill_code,)
    ).fetchone()["total"]
    
    debts = conn.execute(
        "SELECT COALESCE(SUM(remaining_debt), 0) as total FROM debt_records WHERE bill_code = ?",
        (bill_code,)
    ).fetchone()["total"]
    
    debt_payments_total = conn.execute(
        """SELECT COALESCE(SUM(dp.amount), 0) as total 
           FROM debt_payments dp
           JOIN debt_records dr ON dp.debt_record_id = dr.id
           WHERE dr.bill_code = ?""",
        (bill_code,)
    ).fetchone()["total"]
    
    total_accounted = transfers + debts + debt_payments_total
    # Phần cash paid khi tạo nợ
    cash_at_creation = conn.execute(
        "SELECT COALESCE(SUM(cash_paid), 0) as total FROM debt_records WHERE bill_code = ?",
        (bill_code,)
    ).fetchone()["total"]
    
    total_all = transfers + debts + debt_payments_total + cash_at_creation
    
    return {
        "bill_code": bill_code,
        "total_bill": total_bill,
        "transfers": transfers,
        "remaining_debt": debts,
        "debt_payments": debt_payments_total,
        "cash_paid": cash_at_creation,
        "total_accounted": total_all,
        "over": total_all > total_bill if total_bill > 0 else False,
    }
