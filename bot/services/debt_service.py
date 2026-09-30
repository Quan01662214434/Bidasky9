"""
Service công nợ khách hàng.
Ghi nợ, thu nợ, hồ sơ chứng từ.
"""

import json
import uuid
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import DebtStatus, TransactionType
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


# ==================== KHÁCH HÀNG ====================

def create_customer(
    name: str,
    created_by: int,
    phone: str = None,
    description: str = None,
    is_allowed_debt: bool = False,
    debt_limit: int = None,
) -> int:
    """Tạo khách hàng mới. Trả về ID."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO customers (name, phone, description, is_allowed_debt, debt_limit, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, phone, description, 1 if is_allowed_debt else 0, debt_limit, now_utc_iso(), created_by)
    )
    customer_id = cursor.lastrowid
    conn.commit()
    return customer_id


def find_customers(search: str = None) -> List[dict]:
    """Tìm khách hàng."""
    conn = get_connection()
    if search:
        rows = conn.execute(
            """SELECT * FROM customers 
               WHERE name LIKE ? OR phone LIKE ? OR description LIKE ?
               ORDER BY name""",
            (f"%{search}%", f"%{search}%", f"%{search}%")
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_customer(customer_id: int) -> Optional[dict]:
    """Lấy thông tin khách."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


# ==================== GHI NỢ ====================

def create_debt_record(
    customer_id: int,
    cash_shift_id: int | None,
    total_bill_amount: int,
    remaining_debt: int,
    recorded_by: int,
    bill_code: str = None,
    table_name: str = None,
    bill_datetime: str = None,
    cash_paid: int = 0,
    transfer_paid: int = 0,
    item_categories: list = None,
    item_note: str = None,
    due_date: str = None,
    is_retroactive: bool = False,
    is_over_limit: bool = False,
    exception_note: str = None,
) -> dict:
    """
    Ghi nợ khách.
    
    Quy tắc:
    - remaining_debt = total_bill_amount - cash_paid - transfer_paid
    - Không thu quá tổng bill
    - Nếu vượt quyền, đánh dấu is_over_limit
    """
    if remaining_debt < 0:
        raise ValueError("Số nợ không thể âm")
    if cash_paid + transfer_paid + remaining_debt != total_bill_amount:
        raise ValueError("Tổng các khoản phải bằng tổng bill")
    
    conn = get_connection()
    now = now_utc_iso()
    
    # Kiểm tra hạn mức
    customer = get_customer(customer_id)
    if customer and not customer["is_allowed_debt"]:
        is_over_limit = True
    elif customer and customer["debt_limit"]:
        current_debt = get_customer_total_debt(customer_id)
        if current_debt + remaining_debt > customer["debt_limit"]:
            is_over_limit = True
    
    categories_json = json.dumps(item_categories, ensure_ascii=False) if item_categories else None
    
    cursor = conn.execute(
        """INSERT INTO debt_records 
           (customer_id, cash_shift_id, bill_code, table_name,
            bill_datetime, server_recorded_at, is_retroactive,
            total_bill_amount, cash_paid, transfer_paid, remaining_debt,
            item_categories, item_note, due_date,
            status, is_over_limit, exception_note,
            recorded_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (customer_id, cash_shift_id, bill_code, table_name,
         bill_datetime or now, now, 1 if is_retroactive else 0,
         total_bill_amount, cash_paid, transfer_paid, remaining_debt,
         categories_json, item_note, due_date,
         DebtStatus.ACTIVE.value, 1 if is_over_limit else 0, exception_note,
         recorded_by, now, now)
    )
    debt_id = cursor.lastrowid
    conn.commit()
    
    log_action(recorded_by, "create_debt", "debt_records", debt_id,
               new_data=json.dumps({
                   "customer_id": customer_id, "amount": remaining_debt,
                   "bill": bill_code, "over_limit": is_over_limit
               }))
    
    return {
        "id": debt_id,
        "remaining_debt": remaining_debt,
        "is_over_limit": is_over_limit,
        "has_photo": False,
    }


def collect_debt_payment(
    debt_record_id: int,
    amount: int,
    payment_method: str,  # cash/transfer
    collected_by: int,
    cash_shift_id: int = None,
    note: str = None,
    transfer_verified: bool = False,
) -> dict:
    """
    Thu nợ.
    
    Quy tắc:
    - Không thu quá số nợ
    - Tiền mặt cần ca quỹ đang mở
    - Tạo giao dịch tương ứng
    - Idempotency key chống ghi trùng
    """
    conn = get_connection()
    
    debt = conn.execute(
        "SELECT * FROM debt_records WHERE id = ?", (debt_record_id,)
    ).fetchone()
    if not debt:
        raise ValueError("Không tìm thấy khoản nợ")
    
    if amount > debt["remaining_debt"]:
        raise ValueError(f"Số thu ({amount}) vượt số nợ ({debt['remaining_debt']})")
    
    if amount <= 0:
        raise ValueError("Số tiền phải lớn hơn 0")
    
    # Xác định loại giao dịch
    if payment_method == "cash":
        txn_type = TransactionType.DEBT_COLLECT_CASH
        if not cash_shift_id:
            raise ValueError("Thu nợ tiền mặt cần ca quỹ đang mở")
    else:
        txn_type = TransactionType.DEBT_COLLECT_TRANSFER
    
    now = now_utc_iso()
    idempotency_key = str(uuid.uuid4())
    
    # Tạo payment record
    cursor = conn.execute(
        """INSERT INTO debt_payments 
           (debt_record_id, amount, payment_method, cash_shift_id,
            transfer_verified, collected_by, note, created_at, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (debt_record_id, amount, payment_method, cash_shift_id,
         1 if transfer_verified else 0, collected_by, note, now, idempotency_key)
    )
    payment_id = cursor.lastrowid
    
    # Cập nhật nợ
    new_remaining = debt["remaining_debt"] - amount
    new_status = DebtStatus.PAID.value if new_remaining == 0 else DebtStatus.PARTIAL.value
    
    conn.execute(
        "UPDATE debt_records SET remaining_debt = ?, status = ?, updated_at = ? WHERE id = ?",
        (new_remaining, new_status, now, debt_record_id)
    )
    
    # Tạo giao dịch (chỉ khi ảnh hưởng tiền quầy hoặc cần tracking)
    from bot.services.transaction_service import create_transaction
    txn_result = create_transaction(
        cash_shift_id=cash_shift_id if payment_method == "cash" else None,
        txn_type=txn_type,
        amount=amount,
        recorded_by=collected_by,
        bill_code=debt["bill_code"],
        description=f"Thu nợ khách #{debt['customer_id']}",
        debt_record_id=debt_record_id,
        debt_payment_id=payment_id,
        note=note,
    )
    
    # Cập nhật transaction_id trong payment
    conn.execute(
        "UPDATE debt_payments SET transaction_id = ? WHERE id = ?",
        (txn_result["id"], payment_id)
    )
    conn.commit()
    
    log_action(collected_by, f"collect_debt_{payment_method}", "debt_payments", payment_id,
               new_data=json.dumps({"debt_id": debt_record_id, "amount": amount}))
    
    return {
        "payment_id": payment_id,
        "new_remaining": new_remaining,
        "is_fully_paid": new_remaining == 0,
        "debt_record_id": debt_record_id,
    }


def add_debt_photo(
    debt_record_id: int,
    file_id: str,
    file_unique_id: str,
    uploaded_by: int,
    photo_type: str = "bill",
    local_path: str = None,
    note: str = None,
) -> int:
    """Thêm ảnh chứng từ cho khoản nợ."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO debt_photos 
           (debt_record_id, file_id, file_unique_id, local_path, photo_type, note, uploaded_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (debt_record_id, file_id, file_unique_id, local_path, photo_type, note, uploaded_by, now_utc_iso())
    )
    photo_id = cursor.lastrowid
    conn.commit()
    return photo_id


# ==================== TRUY VẤN NỢ ====================

def get_active_debts(limit: int = None) -> List[dict]:
    """Lấy danh sách nợ đang active, ưu tiên quá hạn."""
    conn = get_connection()
    sql = """
        SELECT dr.*, c.name as customer_name, c.phone as customer_phone,
               (SELECT COUNT(*) FROM debt_photos dp WHERE dp.debt_record_id = dr.id) as photo_count
        FROM debt_records dr
        JOIN customers c ON dr.customer_id = c.id
        WHERE dr.status IN ('active', 'partial', 'overdue')
        ORDER BY 
            CASE WHEN dr.due_date IS NOT NULL AND dr.due_date < CURRENT_DATE THEN 0 ELSE 1 END,
            dr.due_date ASC NULLS LAST,
            dr.created_at DESC
    """
    if limit:
        sql += f" LIMIT {limit}"
    
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_debt_summary() -> dict:
    """Lấy tổng hợp nợ cho banner."""
    conn = get_connection()
    
    total = conn.execute(
        """SELECT COUNT(DISTINCT customer_id) as customers, 
                  COALESCE(SUM(remaining_debt), 0) as total_debt,
                  COUNT(*) as records
           FROM debt_records WHERE status IN ('active', 'partial', 'overdue')"""
    ).fetchone()
    
    overdue = conn.execute(
        """SELECT COUNT(*) as cnt, COALESCE(SUM(remaining_debt), 0) as total
           FROM debt_records 
           WHERE status IN ('active', 'partial', 'overdue')
           AND due_date IS NOT NULL AND due_date < CURRENT_DATE"""
    ).fetchone()
    
    missing_photo = conn.execute(
        """SELECT COUNT(*) as cnt FROM debt_records dr
           WHERE dr.status IN ('active', 'partial', 'overdue')
           AND NOT EXISTS (SELECT 1 FROM debt_photos dp WHERE dp.debt_record_id = dr.id)"""
    ).fetchone()["cnt"]
    
    return {
        "total_customers": total["customers"],
        "total_debt": total["total_debt"],
        "total_records": total["records"],
        "overdue_count": overdue["cnt"],
        "overdue_amount": overdue["total"],
        "missing_photo_count": missing_photo,
    }


def get_customer_total_debt(customer_id: int) -> int:
    """Lấy tổng nợ của khách."""
    conn = get_connection()
    row = conn.execute(
        """SELECT COALESCE(SUM(remaining_debt), 0) as total 
           FROM debt_records WHERE customer_id = ? AND status IN ('active', 'partial', 'overdue')""",
        (customer_id,)
    ).fetchone()
    return row["total"]


def get_customer_debts(customer_id: int) -> List[dict]:
    """Lấy chi tiết nợ của khách."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT dr.*,
                  (SELECT COUNT(*) FROM debt_photos dp WHERE dp.debt_record_id = dr.id) as photo_count,
                  (SELECT COUNT(*) FROM debt_payments dpy WHERE dpy.debt_record_id = dr.id) as payment_count
           FROM debt_records dr
           WHERE dr.customer_id = ?
           ORDER BY dr.created_at DESC""",
        (customer_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_debt_payments(debt_record_id: int) -> List[dict]:
    """Lấy lịch sử thanh toán của khoản nợ."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT dp.*, u.display_name as collector_name
           FROM debt_payments dp
           LEFT JOIN users u ON dp.collected_by = u.telegram_id
           WHERE dp.debt_record_id = ?
           ORDER BY dp.created_at""",
        (debt_record_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_debt_photos(debt_record_id: int) -> List[dict]:
    """Lấy ảnh chứng từ của khoản nợ."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM debt_photos WHERE debt_record_id = ? ORDER BY created_at",
        (debt_record_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def format_debt_banner(debts: List[dict], summary: dict) -> str:
    """Format banner KHÁCH ĐANG NỢ cho Telegram."""
    if summary["total_records"] == 0:
        return "✅ Hiện không có khoản nợ chưa thu."
    
    from bot.utils.formatters import format_money, format_date_vn
    
    lines = [
        "🚨🚨 *CẢNH BÁO: DANH SÁCH KHÁCH ĐANG NỢ* 🚨🚨",
        f"‼️ Có {summary['total_customers']} khách chưa trả tiền — TỔNG: {format_money(summary['total_debt'])}",
    ]
    
    if summary["overdue_count"] > 0:
        lines.append(f"🔴 Quá hạn: {summary['overdue_count']} khoản — {format_money(summary['overdue_amount'])}")
    
    if summary["missing_photo_count"] > 0:
        lines.append(f"📷 Thiếu chứng từ: {summary['missing_photo_count']} khoản")
    
    lines.append("")
    
    for d in debts[:5]:
        status_icon = "🔴" if d.get("due_date") and d["due_date"] < str(now_utc_iso()[:10]) else "🟡"
        photo_icon = "📷" if d.get("photo_count", 0) == 0 else ""
        due_text = f" (hạn {format_date_vn(d['due_date'])})" if d.get("due_date") else " (chưa hẹn)"
        lines.append(
            f"{status_icon} {d['customer_name']}: "
            f"{format_money(d['remaining_debt'])}{due_text} {photo_icon}"
        )
    
    if summary["total_records"] > 5:
        lines.append(f"\n📋 ...và {summary['total_records'] - 5} khoản khác")
    
    from bot.models.database import vn_now
    lines.append(f"\n🕐 Cập nhật: {vn_now().strftime('%H:%M %d/%m')}")
    
    return "\n".join(lines)


async def sync_pinned_debt_message(bot, update_all=False):
    """
    Cập nhật tin nhắn ghim nợ cho tất cả người dùng.
    """
    summary = get_debt_summary()
    conn = get_connection()
    users = conn.execute("SELECT telegram_id FROM users WHERE role IN ('owner', 'employee')").fetchall()
    
    if summary["total_records"] == 0:
        # Hủy ghim cho tất cả
        for u in users:
            try:
                await bot.unpin_all_chat_messages(chat_id=u["telegram_id"])
            except Exception as e:
                pass
        return

    from bot.utils.formatters import format_money
    text = f"🚨 CẢNH BÁO: Đang có {summary['total_records']} khoản nợ (Tổng: {format_money(summary['total_debt'])}). Nhớ thu liền nhé!"
    
    for u in users:
        chat_id = u["telegram_id"]
        try:
            # Gửi tin nhắn mới và ghim (không có tiếng)
            msg = await bot.send_message(chat_id=chat_id, text=text, disable_notification=True)
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            logger.error(f"Lỗi ghim tin nợ cho {chat_id}: {e}")
