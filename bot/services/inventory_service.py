"""
Service kho hàng, nhập hàng và kiểm kê.
"""

import json
import uuid
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.constants import ReceiptStatus, StockIssueType, InventoryCheckStatus, TransactionType
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


# ==================== DANH MỤC ====================

def create_inventory_item(
    name: str,
    base_unit: str,
    created_by: int,
    code: str = None,
    category: str = None,
    pack_unit: str = None,
    pack_size: int = None,
    low_stock_threshold: int = None,
    consumable_item_id: int = None,
    kiotviet_code: str = None,
) -> int:
    """Tạo mặt hàng kho."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO inventory_items 
           (code, name, category, base_unit, pack_unit, pack_size,
            low_stock_threshold, consumable_item_id, kiotviet_code,
            is_active, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (code, name, category, base_unit, pack_unit, pack_size,
         low_stock_threshold, consumable_item_id, kiotviet_code,
         now_utc_iso(), created_by)
    )
    item_id = cursor.lastrowid
    conn.commit()
    return item_id


def get_inventory_items(category: str = None) -> List[dict]:
    """Lấy danh sách mặt hàng kho."""
    conn = get_connection()
    if category:
        rows = conn.execute(
            "SELECT * FROM inventory_items WHERE is_active = 1 AND category = ? ORDER BY name",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM inventory_items WHERE is_active = 1 ORDER BY category, name"
        ).fetchall()
    return [dict(r) for r in rows]


def set_initial_stock(item_id: int, quantity: int, effective_date: str, 
                      recorded_by: int, reason: str = None) -> int:
    """Khai tồn đầu kỳ."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO initial_stock (item_id, quantity, effective_date, recorded_by, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (item_id, quantity, effective_date, recorded_by, reason, now_utc_iso())
    )
    conn.commit()
    log_action(recorded_by, "set_initial_stock", "initial_stock", cursor.lastrowid)
    return cursor.lastrowid


# ==================== TỒN KHO ====================

def calculate_stock(item_id: int) -> dict:
    """
    Tính tồn kho hiện tại.
    Tồn = Đầu kỳ + Nhập thực nhận + Khách trả − Bán/Xuất − Hao hụt − Trả NCC ± Điều chỉnh
    """
    conn = get_connection()
    
    # Tồn đầu kỳ
    initial = conn.execute(
        """SELECT COALESCE(SUM(quantity), 0) as total FROM initial_stock 
           WHERE item_id = ?""",
        (item_id,)
    ).fetchone()["total"]
    
    # Nhập thực nhận
    received = conn.execute(
        """SELECT COALESCE(SUM(ri.quantity_received), 0) as total 
           FROM receipt_items ri
           JOIN stock_receipts sr ON ri.receipt_id = sr.id
           WHERE ri.item_id = ? AND sr.status = 'received'""",
        (item_id,)
    ).fetchone()["total"]
    
    # Xuất (tất cả loại)
    issued = conn.execute(
        """SELECT COALESCE(SUM(quantity), 0) as total FROM stock_issues 
           WHERE item_id = ?""",
        (item_id,)
    ).fetchone()["total"]
    
    # Điều chỉnh (từ kiểm kê đã duyệt)
    adjustments = conn.execute(
        """SELECT COALESCE(SUM(ici.difference), 0) as total 
           FROM inventory_check_items ici
           JOIN inventory_checks ic ON ici.check_id = ic.id
           WHERE ici.item_id = ? AND ici.adjustment_approved = 1""",
        (item_id,)
    ).fetchone()["total"]
    
    book_stock = initial + received - issued + adjustments
    
    # Chi tiết xuất theo loại
    issue_breakdown = {}
    for issue_type in StockIssueType:
        qty = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) as total FROM stock_issues WHERE item_id = ? AND issue_type = ?",
            (item_id, issue_type.value)
        ).fetchone()["total"]
        if qty > 0:
            issue_breakdown[issue_type.value] = qty
    
    item = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    
    return {
        "item_id": item_id,
        "name": item["name"] if item else "?",
        "base_unit": item["base_unit"] if item else "?",
        "initial": initial,
        "received": received,
        "issued": issued,
        "adjustments": adjustments,
        "book_stock": book_stock,
        "issue_breakdown": issue_breakdown,
        "low_threshold": item["low_stock_threshold"] if item else None,
        "is_low": book_stock <= (item["low_stock_threshold"] or 0) if item else False,
    }


# ==================== NHẬP HÀNG ====================

def create_receipt(
    received_by: int,
    received_date: str,
    supplier_name: str = None,
    cash_shift_id: int = None,
    note: str = None,
) -> dict:
    """Tạo phiếu nhập hàng (bản nháp)."""
    conn = get_connection()
    now = now_utc_iso()
    receipt_code = f"NK-{uuid.uuid4().hex[:8].upper()}"
    
    cursor = conn.execute(
        """INSERT INTO stock_receipts 
           (receipt_code, supplier_name, received_date, recorded_at,
            received_by, cash_shift_id, status, note,
            created_at, updated_at, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)""",
        (receipt_code, supplier_name, received_date, now,
         received_by, cash_shift_id, note, now, now, str(uuid.uuid4()))
    )
    receipt_id = cursor.lastrowid
    conn.commit()
    return {"id": receipt_id, "code": receipt_code}


def add_receipt_item(
    receipt_id: int,
    item_id: int,
    quantity_received: int,  # Đơn vị cơ sở
    unit_used: str = None,
    raw_quantity: float = None,
    conversion_factor: int = None,
    unit_price: int = None,
    note: str = None,
) -> int:
    """Thêm mặt hàng vào phiếu nhập."""
    conn = get_connection()
    
    line_total = None
    if unit_price and quantity_received:
        line_total = quantity_received * unit_price
    
    cursor = conn.execute(
        """INSERT INTO receipt_items 
           (receipt_id, item_id, quantity_received, unit_used,
            raw_quantity, conversion_factor, unit_price, line_total, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (receipt_id, item_id, quantity_received, unit_used,
         raw_quantity, conversion_factor, unit_price, line_total, note)
    )
    
    # Cập nhật tổng phiếu
    total = conn.execute(
        "SELECT COALESCE(SUM(line_total), 0) as total FROM receipt_items WHERE receipt_id = ?",
        (receipt_id,)
    ).fetchone()["total"]
    
    conn.execute(
        "UPDATE stock_receipts SET total_amount = ?, updated_at = ? WHERE id = ?",
        (total, now_utc_iso(), receipt_id)
    )
    
    conn.commit()
    return cursor.lastrowid


def confirm_receipt(
    receipt_id: int,
    confirmed_by: int,
    payment_status: str = "unpaid",
    payment_source: str = None,
    cash_shift_id: int = None,
) -> dict:
    """
    Xác nhận nhận hàng thực tế → cộng tồn.
    Nếu trả tiền mặt → tạo giao dịch chi.
    """
    conn = get_connection()
    
    receipt = conn.execute(
        "SELECT * FROM stock_receipts WHERE id = ? AND status = 'draft'",
        (receipt_id,)
    ).fetchone()
    if not receipt:
        raise ValueError("Phiếu nhập không tồn tại hoặc đã xác nhận")
    
    # Tính net_amount
    total = receipt["total_amount"] or 0
    discount = receipt["discount"] or 0
    shipping = receipt["shipping_fee"] or 0
    net_amount = total - discount + shipping
    
    txn_id = None
    if payment_status == "paid_cash":
        if not cash_shift_id:
            raise ValueError("Thanh toán tiền mặt cần ca quỹ đang mở")
        from bot.services.transaction_service import create_transaction
        txn = create_transaction(
            cash_shift_id=cash_shift_id,
            txn_type=TransactionType.IMPORT_PAY_CASH,
            amount=net_amount,
            recorded_by=confirmed_by,
            receipt_id=receipt_id,
            description=f"Nhập hàng {receipt['receipt_code']}",
        )
        txn_id = txn["id"]
    
    now = now_utc_iso()
    conn.execute(
        """UPDATE stock_receipts SET status = 'received',
           net_amount = ?, payment_status = ?, payment_source = ?,
           transaction_id = ?, updated_at = ?
           WHERE id = ?""",
        (net_amount, payment_status, payment_source, txn_id, now, receipt_id)
    )
    conn.commit()
    
    log_action(confirmed_by, "confirm_receipt", "stock_receipts", receipt_id,
               new_data=json.dumps({"net": net_amount, "payment": payment_status}))
    
    return {"receipt_id": receipt_id, "net_amount": net_amount, "txn_id": txn_id}


# ==================== XUẤT KHO ====================

def record_stock_issue(
    item_id: int,
    quantity: int,
    issue_type: StockIssueType,
    recorded_by: int,
    reason: str = None,
    cash_shift_id: int = None,
    approved_by: int = None,
    import_batch_id: str = None,
) -> int:
    """Ghi xuất kho."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO stock_issues 
           (item_id, quantity, issue_type, import_batch_id, reason,
            approved_by, recorded_by, cash_shift_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, quantity, issue_type.value, import_batch_id, reason,
         approved_by, recorded_by, cash_shift_id, now_utc_iso())
    )
    conn.commit()
    return cursor.lastrowid


# ==================== IMPORT BÁN HÀNG KIOTVIET ====================

def create_sales_import(
    period_start: str,
    period_end: str,
    imported_by: int,
    source_description: str = None,
    import_method: str = "manual",
) -> dict:
    """Tạo đợt import lượng bán từ KiotViet."""
    conn = get_connection()
    batch_id = f"SALE-{uuid.uuid4().hex[:8].upper()}"
    
    # Kiểm tra trùng khoảng thời gian
    existing = conn.execute(
        """SELECT batch_id FROM sales_imports 
           WHERE period_start = ? AND period_end = ? AND status = 'active'""",
        (period_start, period_end)
    ).fetchone()
    
    cursor = conn.execute(
        """INSERT INTO sales_imports 
           (batch_id, period_start, period_end, source_description,
            imported_by, import_method, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
        (batch_id, period_start, period_end, source_description,
         imported_by, import_method, now_utc_iso())
    )
    import_id = cursor.lastrowid
    conn.commit()
    
    return {
        "id": import_id,
        "batch_id": batch_id,
        "existing_batch": existing["batch_id"] if existing else None,
    }


def add_sales_import_item(
    import_id: int,
    item_id: int,
    quantity_sold: int,
    quantity_returned: int = 0,
    note: str = None,
) -> int:
    """Thêm mặt hàng vào đợt import bán."""
    conn = get_connection()
    net = quantity_sold - quantity_returned
    
    cursor = conn.execute(
        """INSERT INTO sales_import_items 
           (import_id, item_id, quantity_sold, quantity_returned, net_quantity, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (import_id, item_id, quantity_sold, quantity_returned, net, note)
    )
    
    # Tạo phiếu xuất kho
    si = conn.execute("SELECT batch_id FROM sales_imports WHERE id = ?", (import_id,)).fetchone()
    record_stock_issue(
        item_id=item_id,
        quantity=net,
        issue_type=StockIssueType.SALE_KIOTVIET,
        recorded_by=0,  # System
        reason=f"Import KiotViet batch {si['batch_id']}",
        import_batch_id=si['batch_id'],
    )
    
    conn.commit()
    return cursor.lastrowid


# ==================== KIỂM KÊ ====================

def create_inventory_check(
    checked_by: int,
    check_date: str,
    check_time: str,
    cash_shift_id: int = None,
    note: str = None,
) -> int:
    """Tạo phiên kiểm kê."""
    conn = get_connection()
    now = now_utc_iso()
    
    # Snapshot tồn sổ hiện tại
    items = get_inventory_items()
    snapshot = {}
    for item in items:
        stock = calculate_stock(item["id"])
        snapshot[str(item["id"])] = {
            "name": item["name"],
            "book_stock": stock["book_stock"],
        }
    
    cursor = conn.execute(
        """INSERT INTO inventory_checks 
           (check_date, check_time, cash_shift_id, checked_by,
            status, snapshot, note, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)""",
        (check_date, check_time, cash_shift_id, checked_by,
         json.dumps(snapshot, ensure_ascii=False), note, now, now)
    )
    check_id = cursor.lastrowid
    conn.commit()
    return check_id


def add_check_item(
    check_id: int,
    item_id: int,
    actual_quantity: int,
    unit_used: str = None,
    note: str = None,
) -> dict:
    """Thêm kết quả đếm cho mặt hàng."""
    conn = get_connection()
    
    stock = calculate_stock(item_id)
    book_qty = stock["book_stock"]
    diff = actual_quantity - book_qty
    
    cursor = conn.execute(
        """INSERT INTO inventory_check_items 
           (check_id, item_id, book_quantity, actual_quantity, difference, unit_used, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (check_id, item_id, book_qty, actual_quantity, diff, unit_used, note)
    )
    conn.commit()
    
    return {
        "item_id": item_id,
        "name": stock["name"],
        "book": book_qty,
        "actual": actual_quantity,
        "difference": diff,
    }


def get_check_results(check_id: int) -> List[dict]:
    """Lấy kết quả kiểm kê."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT ici.*, ii.name, ii.base_unit
           FROM inventory_check_items ici
           JOIN inventory_items ii ON ici.item_id = ii.id
           WHERE ici.check_id = ?
           ORDER BY ii.name""",
        (check_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def approve_adjustment(
    check_id: int,
    item_id: int,
    approved_by: int,
    reason: str = None,
) -> bool:
    """Duyệt điều chỉnh tồn kho từ kiểm kê."""
    conn = get_connection()
    now = now_utc_iso()
    conn.execute(
        """UPDATE inventory_check_items SET 
           adjustment_approved = 1, adjustment_approved_by = ?,
           adjustment_approved_at = ?, adjustment_reason = ?
           WHERE check_id = ? AND item_id = ?""",
        (approved_by, now, reason, check_id, item_id)
    )
    conn.commit()
    log_action(approved_by, "approve_stock_adjustment", "inventory_check_items", check_id)
    return True


def get_low_stock_items() -> List[dict]:
    """Lấy mặt hàng sắp hết."""
    items = get_inventory_items()
    low_items = []
    for item in items:
        if item.get("low_stock_threshold"):
            stock = calculate_stock(item["id"])
            if stock["book_stock"] <= item["low_stock_threshold"]:
                low_items.append(stock)
    return low_items
