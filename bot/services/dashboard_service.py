import logging
from datetime import timedelta
from typing import Dict, Any, List
import pytz

from bot.config import Config
from bot.models.database import get_connection, vn_now
from bot.constants import TransactionType

logger = logging.getLogger(__name__)

def get_business_date() -> str:
    """Xác định ngày kinh doanh hiện tại dựa vào BUSINESS_DAY_START."""
    now = vn_now()
    business_start_str = Config.BUSINESS_DAY_START  # VD: "06:00"
    try:
        start_hour, start_minute = map(int, business_start_str.split(':'))
    except ValueError:
        start_hour, start_minute = 6, 0

    if now.hour < start_hour or (now.hour == start_hour and now.minute < start_minute):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def get_dashboard_today(employee_id: int, current_shift_id: int = None, filter_mode: str = "all") -> Dict[str, Any]:
    """
    Lấy số liệu tổng hợp cho dashboard Hôm nay (Theo ngày kinh doanh).
    filter_mode: 'all' (Toàn ngày), 'shift' (Ca hiện tại), 'me' (Tôi ghi)
    """
    date = get_business_date()
    now = vn_now()
    business_start_str = Config.BUSINESS_DAY_START
    try:
        start_hour, start_minute = map(int, business_start_str.split(':'))
    except ValueError:
        start_hour, start_minute = 6, 0

    if now.hour < start_hour or (now.hour == start_hour and now.minute < start_minute):
        start_time = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0) - timedelta(days=1)
    else:
        start_time = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        
    end_time = start_time + timedelta(days=1)
    
    start_utc = start_time.astimezone(pytz.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_time.astimezone(pytz.utc).isoformat().replace("+00:00", "Z")
    
    conn = get_connection()
    
    query = """
        SELECT t.*, u.display_name as recorder_name,
        d.customer_name as debt_customer
        FROM transactions t
        LEFT JOIN users u ON t.recorded_by = u.telegram_id
        LEFT JOIN debt_records d ON t.debt_record_id = d.id
        WHERE t.type IN ('bank_transfer', 'debt_collect_transfer')
        AND t.created_at >= ? AND t.created_at < ?
        AND (t.status = 'completed' OR t.status IS NULL)
    """
    params = [start_utc, end_utc]
    
    if filter_mode == "shift" and current_shift_id:
        query += " AND t.cash_shift_id = ?"
        params.append(current_shift_id)
    elif filter_mode == "me":
        query += " AND t.recorded_by = ?"
        params.append(employee_id)
        
    query += " ORDER BY t.created_at DESC"
    
    rows = conn.execute(query, params).fetchall()
    
    total_amount = sum(row["amount"] for row in rows)
    count = len(rows)
    
    # Calculate shift total
    shift_total = 0
    if current_shift_id:
        shift_rows = conn.execute("""
            SELECT amount FROM transactions 
            WHERE type IN ('bank_transfer', 'debt_collect_transfer')
            AND cash_shift_id = ?
            AND (status = 'completed' OR status IS NULL)
        """, (current_shift_id,)).fetchall()
        shift_total = sum(r["amount"] for r in shift_rows)
        
    return {
        "date": date,
        "total_amount": total_amount,
        "count": count,
        "shift_total": shift_total,
        "transactions": [dict(r) for r in rows]
    }

def get_transaction_details(txn_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("""
        SELECT t.*, u.display_name as recorder_name,
        d.customer_name as debt_customer,
        dp.photo_id as debt_photo_id, dp.payment_date
        FROM transactions t
        LEFT JOIN users u ON t.recorded_by = u.telegram_id
        LEFT JOIN debt_records d ON t.debt_record_id = d.id
        LEFT JOIN debt_payments dp ON t.debt_payment_id = dp.id
        WHERE t.id = ?
    """, (txn_id,)).fetchone()
    
    if not row:
        return None
    return dict(row)
