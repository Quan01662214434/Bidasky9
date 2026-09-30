"""
Service báo cáo doanh thu và tổng hợp.
"""

import json
import logging
from typing import Optional, List
from datetime import datetime, timedelta

import pytz

from bot.models.database import get_connection, now_utc_iso, vn_now
from bot.config import Config
from bot.utils.formatters import format_money, format_datetime_vn

logger = logging.getLogger(__name__)

VN_TZ = pytz.timezone(Config.TIMEZONE)


def get_business_day_range(date_str: str) -> tuple:
    """
    Tính khoảng thời gian ngày kinh doanh.
    Dùng BUSINESS_DAY_START từ cấu hình.
    """
    start_time = Config.BUSINESS_DAY_START
    h, m = map(int, start_time.split(":"))
    
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_start = VN_TZ.localize(dt.replace(hour=h, minute=m))
    day_end = day_start + timedelta(days=1)
    
    return day_start.isoformat(), day_end.isoformat()


def get_daily_report(business_date: str) -> dict:
    """
    Báo cáo doanh thu ngày.
    
    Tổng doanh thu bill lấy từ KiotViet (nhập thủ công).
    Các khoản chuyển khoản, nợ, chi, thu nợ cũ hiển thị riêng.
    """
    conn = get_connection()
    period_start, period_end = get_business_day_range(business_date)
    
    # Lấy doanh thu đã nhập
    daily = conn.execute(
        "SELECT * FROM daily_revenue WHERE business_date = ?",
        (business_date,)
    ).fetchone()
    
    # Lấy ca quỹ trong ngày
    shifts = conn.execute(
        """SELECT cs.*, u.display_name
           FROM cash_shifts cs
           JOIN users u ON cs.employee_id = u.telegram_id
           WHERE cs.shift_date = ?
           ORDER BY cs.opened_at""",
        (business_date,)
    ).fetchall()
    
    # Tổng hợp từ ca
    total_bill_from_shifts = 0
    total_transfers = 0
    total_debt_new = 0
    total_cash_out = 0
    total_old_debt_cash = 0
    total_old_debt_transfer = 0
    total_cash_to_owner = 0
    shift_details = []
    
    has_open_shifts = False
    
    for s in shifts:
        if s["status"] == "open":
            has_open_shifts = True
        
        bill = s["total_bill_revenue"] or 0
        total_bill_from_shifts += bill
        
        if s["status"] in ("closed", "pending_review"):
            from bot.services.cash_shift_service import calculate_expected_closing
            try:
                calc = calculate_expected_closing(s["id"])
            except Exception:
                calc = {}
            
            total_transfers += calc.get("bank_transfers", 0)
            total_debt_new += calc.get("remaining_debt", 0)
            total_cash_out += calc.get("cash_out", 0)
            total_old_debt_cash += calc.get("old_debt_cash", 0)
            total_old_debt_transfer += calc.get("old_debt_transfer", 0)
        
        shift_details.append({
            "id": s["id"],
            "employee": s["display_name"],
            "status": s["status"],
            "bill_revenue": bill,
            "opened_at": s["opened_at"],
            "closed_at": s["closed_at"],
            "diff": s["closing_diff"],
        })
    
    # Xác định nguồn tổng doanh thu
    total_bill = None
    source_type = None
    status = "draft"
    
    if daily:
        total_bill = daily["total_bill_revenue"]
        source_type = daily["source_type"]
        status = daily["status"]
    elif total_bill_from_shifts > 0 and not has_open_shifts:
        total_bill = total_bill_from_shifts
        source_type = "sum_of_shifts"
        status = "partial"
    
    return {
        "business_date": business_date,
        "period_start": period_start,
        "period_end": period_end,
        "total_bill_revenue": total_bill,
        "source_type": source_type,
        "status": status,
        "has_open_shifts": has_open_shifts,
        
        "shifts": shift_details,
        "total_bill_from_shifts": total_bill_from_shifts,
        
        "transfers_for_bills": total_transfers,
        "new_debt": total_debt_new,
        "cash_expenses": total_cash_out,
        "old_debt_cash": total_old_debt_cash,
        "old_debt_transfer": total_old_debt_transfer,
        "cash_to_owner": total_cash_to_owner,
    }


def save_daily_revenue(
    business_date: str,
    total_bill_revenue: int,
    entered_by: int,
    source_type: str = "daily_total",
    report_photo_id: str = None,
    note: str = None,
) -> int:
    """Lưu doanh thu ngày."""
    conn = get_connection()
    now = now_utc_iso()
    period_start, period_end = get_business_day_range(business_date)
    
    existing = conn.execute(
        "SELECT id, version FROM daily_revenue WHERE business_date = ?",
        (business_date,)
    ).fetchone()
    
    if existing:
        new_version = (existing["version"] or 1) + 1
        conn.execute(
            """UPDATE daily_revenue SET total_bill_revenue = ?, source_type = ?,
               report_photo_id = ?, status = 'complete',
               missing_data_note = ?, entered_by = ?,
               updated_at = ?, version = ?
               WHERE id = ?""",
            (total_bill_revenue, source_type, report_photo_id, note,
             entered_by, now, new_version, existing["id"])
        )
        conn.commit()
        return existing["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO daily_revenue 
               (business_date, period_start, period_end,
                source_type, total_bill_revenue, report_photo_id,
                status, entered_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?)""",
            (business_date, period_start, period_end,
             source_type, total_bill_revenue, report_photo_id,
             entered_by, now, now)
        )
        conn.commit()
        return cursor.lastrowid


def format_daily_report(report: dict) -> str:
    """Format báo cáo doanh thu ngày cho Telegram."""
    from bot.utils.formatters import format_money
    
    date_display = datetime.strptime(report["business_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    
    lines = [
        f"📊 *DOANH THU NGÀY {date_display}*",
        f"Khoảng thời gian: {format_datetime_vn(report['period_start'], '%H:%M %d/%m')} — "
        f"{format_datetime_vn(report['period_end'], '%H:%M %d/%m')}",
        "",
    ]
    
    # Tổng doanh thu
    if report["total_bill_revenue"] is not None:
        lines.append(f"💰 *TỔNG DOANH THU BILL: {format_money(report['total_bill_revenue'])}*")
        if report["source_type"] == "sum_of_shifts":
            lines.append(f"   _(Tổng từ {len(report['shifts'])} ca)_")
        elif report["source_type"] == "daily_total":
            lines.append("   _(Nhập tổng ngày)_")
    else:
        lines.append("💰 TỔNG DOANH THU BILL: _Chưa có dữ liệu_")
    
    # Trạng thái
    status_map = {
        "draft": "📝 Bản nháp",
        "partial": "⏳ Tạm tính",
        "complete": "✅ Đã đủ dữ liệu",
        "reconciled": "✅ Đã đối soát",
    }
    lines.append(f"Trạng thái: {status_map.get(report['status'], report['status'])}")
    
    if report["has_open_shifts"]:
        lines.append("⚠️ _Còn ca đang mở_")
    
    # Chi tiết ca
    if report["shifts"]:
        lines.append("\n📋 *Chi tiết ca:*")
        for s in report["shifts"]:
            status_icon = "🟢" if s["status"] == "closed" else ("🟡" if s["status"] == "open" else "🔴")
            bill_text = format_money(s["bill_revenue"]) if s["bill_revenue"] else "—"
            diff_text = ""
            if s["diff"] is not None and s["diff"] != 0:
                diff_text = f" | {'🔴' if s['diff'] < 0 else '🟢'} {format_money(s['diff'])}"
            lines.append(
                f"  {status_icon} {s['employee']}: {bill_text}{diff_text}"
            )
    
    # Các khoản khác
    details = []
    if report["transfers_for_bills"]:
        details.append(f"💳 CK bill ngày: {format_money(report['transfers_for_bills'])}")
    if report["new_debt"]:
        details.append(f"📋 Khách nợ mới: {format_money(report['new_debt'])}")
    if report["old_debt_cash"]:
        details.append(f"💵 Thu nợ cũ (TM): {format_money(report['old_debt_cash'])}")
    if report["old_debt_transfer"]:
        details.append(f"💳 Thu nợ cũ (CK): {format_money(report['old_debt_transfer'])}")
    if report["cash_expenses"]:
        details.append(f"📤 Chi tiền mặt: {format_money(report['cash_expenses'])}")
    if report["cash_to_owner"]:
        details.append(f"🏧 Giao tiền chủ: {format_money(report['cash_to_owner'])}")
    
    if details:
        lines.append("\n📊 *Các khoản khác:*")
        lines.extend(f"  {d}" for d in details)
    
    lines.append(f"\n🕐 Cập nhật: {vn_now().strftime('%H:%M %d/%m/%Y')}")
    
    return "\n".join(lines)


def get_owner_dashboard() -> dict:
    """Tổng hợp nhanh cho chủ quán."""
    conn = get_connection()
    now = vn_now()
    today = now.strftime("%Y-%m-%d")
    
    # Ai đang làm
    working = conn.execute(
        """SELECT a.*, u.display_name FROM attendance_sessions a
           JOIN users u ON a.employee_id = u.telegram_id
           WHERE a.status = 'checked_in'"""
    ).fetchall()
    
    # Ca quỹ đang mở
    open_shifts = conn.execute(
        """SELECT cs.*, u.display_name FROM cash_shifts cs
           JOIN users u ON cs.employee_id = u.telegram_id
           WHERE cs.status = 'open'"""
    ).fetchall()
    
    # Đăng ký chờ duyệt
    pending_regs = conn.execute(
        "SELECT COUNT(*) as cnt FROM shift_registrations WHERE status = 'pending'"
    ).fetchone()["cnt"]
    
    # Yêu cầu sửa công chờ
    pending_adj = conn.execute(
        "SELECT COUNT(*) as cnt FROM attendance_adjustments WHERE status = 'pending'"
    ).fetchone()["cnt"]
    
    # Nợ tổng
    from bot.services.debt_service import get_debt_summary
    debt_summary = get_debt_summary()
    
    # Ca chưa nhận bàn giao
    pending_handover = conn.execute(
        """SELECT COUNT(*) as cnt FROM cash_shifts 
           WHERE status IN ('closed', 'pending_review') AND handover_confirmed_by IS NULL"""
    ).fetchone()["cnt"]
    
    # Ca thiếu/dư
    problem_shifts = conn.execute(
        """SELECT COUNT(*) as cnt FROM cash_shifts 
           WHERE closing_diff IS NOT NULL AND closing_diff != 0
           AND status = 'pending_review'"""
    ).fetchone()["cnt"]
    
    # Hàng sắp hết
    from bot.services.inventory_service import get_low_stock_items
    low_stock = get_low_stock_items()
    
    # Kỳ lương chưa chốt
    pending_payroll = conn.execute(
        "SELECT COUNT(*) as cnt FROM payroll WHERE status IN ('draft', 'pending', 'disputed')"
    ).fetchone()["cnt"]
    
    return {
        "working_now": [dict(w) for w in working],
        "open_cash_shifts": [dict(s) for s in open_shifts],
        "pending_registrations": pending_regs,
        "pending_adjustments": pending_adj,
        "debt_summary": debt_summary,
        "pending_handover": pending_handover,
        "problem_shifts": problem_shifts,
        "low_stock_count": len(low_stock),
        "pending_payroll": pending_payroll,
    }
