"""
Service đồ nhân viên dùng.
Ghi số lượng trong tháng, nhập giá cuối tháng, tính thành tiền.
"""

import json
import logging
from typing import Optional, List

from bot.models.database import get_connection, now_utc_iso
from bot.utils.permissions import log_action

logger = logging.getLogger(__name__)


# ==================== QUẢN LÝ MẶT HÀNG ====================

def create_consumable_item(
    name: str,
    unit: str,
    created_by: int,
    category: str = None,
    pack_unit: str = None,
    pack_size: int = None,
) -> int:
    """Tạo mặt hàng đồ dùng."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO consumable_items 
           (name, category, unit, pack_unit, pack_size, is_active, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (name, category, unit, pack_unit, pack_size, now_utc_iso(), created_by)
    )
    item_id = cursor.lastrowid
    conn.commit()
    return item_id


def get_consumable_items(category: str = None) -> List[dict]:
    """Lấy danh sách mặt hàng."""
    conn = get_connection()
    if category:
        rows = conn.execute(
            "SELECT * FROM consumable_items WHERE is_active = 1 AND category = ? ORDER BY name",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM consumable_items WHERE is_active = 1 ORDER BY category, name"
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== GHI DÙNG ====================

def log_consumable_usage(
    employee_id: int,
    item_id: int,
    quantity: int,
    usage_datetime: str = None,
    cash_shift_id: int = None,
    note: str = None,
    photo_id: str = None,
) -> int:
    """
    Ghi đồ nhân viên đã dùng.
    Đồng thời tạo phiếu xuất kho nội bộ.
    """
    conn = get_connection()
    now = now_utc_iso()
    
    if not usage_datetime:
        usage_datetime = now
    
    is_late = usage_datetime < now  # Nhập muộn nếu thời gian dùng < hiện tại
    
    cursor = conn.execute(
        """INSERT INTO consumable_usage 
           (employee_id, item_id, quantity, usage_datetime, recorded_at,
            is_late_entry, cash_shift_id, photo_id, note, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, item_id, quantity, usage_datetime, now,
         1 if is_late else 0, cash_shift_id, photo_id, note, now, now)
    )
    usage_id = cursor.lastrowid
    
    # Xuất kho nội bộ (liên kết consumable_items → inventory_items)
    item = conn.execute("SELECT * FROM consumable_items WHERE id = ?", (item_id,)).fetchone()
    inv_item = conn.execute(
        "SELECT id FROM inventory_items WHERE consumable_item_id = ?", (item_id,)
    ).fetchone()
    
    if inv_item:
        conn.execute(
            """INSERT INTO stock_issues 
               (item_id, quantity, issue_type, consumable_usage_id, 
                reason, recorded_by, cash_shift_id, created_at)
               VALUES (?, ?, 'employee_use', ?, ?, ?, ?, ?)""",
            (inv_item["id"], quantity, usage_id,
             f"NV {employee_id} dùng {item['name']} x{quantity}", employee_id, cash_shift_id, now)
        )
    
    conn.commit()
    log_action(employee_id, "log_consumable", "consumable_usage", usage_id)
    return usage_id


# ==================== TỔNG HỢP CUỐI THÁNG ====================

def get_employee_consumable_summary(employee_id: int, year: int, month: int) -> dict:
    """
    Tổng hợp đồ dùng tháng của nhân viên.
    Nhóm theo mặt hàng, hiển thị lượng dùng / miễn phí / đã trả / còn tính.
    """
    conn = get_connection()
    
    from_date = f"{year:04d}-{month:02d}-01"
    if month == 12:
        to_date = f"{year + 1:04d}-01-01"
    else:
        to_date = f"{year:04d}-{month + 1:02d}-01"
    
    rows = conn.execute(
        """SELECT cu.item_id, ci.name, ci.unit, ci.category,
                  SUM(cu.quantity) as total_used,
                  SUM(CASE WHEN cu.is_free = 1 THEN cu.quantity ELSE 0 END) as free_qty,
                  SUM(CASE WHEN cu.paid_amount > 0 THEN cu.quantity ELSE 0 END) as paid_qty,
                  SUM(cu.paid_amount) as total_paid
           FROM consumable_usage cu
           JOIN consumable_items ci ON cu.item_id = ci.id
           WHERE cu.employee_id = ? AND cu.usage_datetime >= ? AND cu.usage_datetime < ?
           GROUP BY cu.item_id
           ORDER BY ci.category, ci.name""",
        (employee_id, from_date, to_date)
    ).fetchall()
    
    items = []
    total_amount = 0
    has_unpriced = False
    
    for r in rows:
        # Lấy giá
        price = conn.execute(
            """SELECT unit_price FROM consumable_prices 
               WHERE item_id = ? AND period_year = ? AND period_month = ?""",
            (r["item_id"], year, month)
        ).fetchone()
        
        # Kiểm tra ngoại lệ giá
        price_exception = None
        if price:
            exc = conn.execute(
                """SELECT unit_price FROM consumable_price_exceptions 
                   WHERE price_id = ? AND employee_id = ?""",
                (price["id"], employee_id)
            ).fetchone()
            if exc:
                price_exception = exc["unit_price"]
        
        unit_price = price_exception or (price["unit_price"] if price else None)
        
        chargeable_qty = r["total_used"] - r["free_qty"]
        # Trừ số lượng đã trả (nếu trả theo món)
        remaining_qty = chargeable_qty  # Simplified - thực tế cần track theo từng lần
        
        if unit_price is not None:
            line_total = remaining_qty * unit_price
            total_amount += line_total
        else:
            line_total = None
            has_unpriced = True
        
        items.append({
            "item_id": r["item_id"],
            "name": r["name"],
            "unit": r["unit"],
            "category": r["category"],
            "total_used": r["total_used"],
            "free_qty": r["free_qty"],
            "paid_qty": r["paid_qty"],
            "chargeable_qty": remaining_qty,
            "unit_price": unit_price,
            "line_total": line_total,
            "total_paid": r["total_paid"],
            "has_price": unit_price is not None,
        })
    
    return {
        "items": items,
        "total_amount": total_amount,
        "has_unpriced": has_unpriced,
        "employee_id": employee_id,
        "year": year,
        "month": month,
    }


# ==================== NHẬP GIÁ ====================

def set_consumable_price(
    item_id: int,
    year: int,
    month: int,
    unit_price: int,
    set_by: int,
    copied_from_previous: bool = False,
    note: str = None,
) -> int:
    """Nhập giá cho mặt hàng trong kỳ."""
    conn = get_connection()
    now = now_utc_iso()
    
    existing = conn.execute(
        """SELECT id FROM consumable_prices 
           WHERE item_id = ? AND period_year = ? AND period_month = ?""",
        (item_id, year, month)
    ).fetchone()
    
    if existing:
        conn.execute(
            """UPDATE consumable_prices SET unit_price = ?, set_by = ?, set_at = ?,
               copied_from_previous = ?, note = ?
               WHERE id = ?""",
            (unit_price, set_by, now, 1 if copied_from_previous else 0, note, existing["id"])
        )
        conn.commit()
        return existing["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO consumable_prices 
               (item_id, period_year, period_month, unit_price, set_by, set_at,
                copied_from_previous, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, year, month, unit_price, set_by, now,
             1 if copied_from_previous else 0, note)
        )
        conn.commit()
        return cursor.lastrowid


def copy_prices_from_previous(year: int, month: int, set_by: int) -> int:
    """Sao chép giá tháng trước."""
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    
    conn = get_connection()
    prev_prices = conn.execute(
        "SELECT * FROM consumable_prices WHERE period_year = ? AND period_month = ?",
        (prev_year, prev_month)
    ).fetchall()
    
    count = 0
    for p in prev_prices:
        existing = conn.execute(
            """SELECT id FROM consumable_prices 
               WHERE item_id = ? AND period_year = ? AND period_month = ?""",
            (p["item_id"], year, month)
        ).fetchone()
        if not existing:
            set_consumable_price(
                p["item_id"], year, month, p["unit_price"],
                set_by, copied_from_previous=True
            )
            count += 1
    
    return count


def get_all_consumable_summary(year: int, month: int) -> List[dict]:
    """Tổng hợp đồ dùng tháng cho tất cả nhân viên."""
    conn = get_connection()
    employees = conn.execute(
        "SELECT telegram_id, display_name FROM users WHERE role = 'employee' AND is_active = 1"
    ).fetchall()
    
    results = []
    for emp in employees:
        summary = get_employee_consumable_summary(emp["telegram_id"], year, month)
        summary["display_name"] = emp["display_name"]
        results.append(summary)
    
    return results
