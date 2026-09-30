"""
Web Server cho BIDA SKY9 Dashboard.
FastAPI backend phục vụ Mini App quản lý chủ quán.
"""
import sqlite3
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import pytz

load_dotenv()
db_path = os.getenv("DATABASE_PATH", "data/bida.db")
owner_id = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
timezone_str = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")
business_day_start = os.getenv("BUSINESS_DAY_START", "06:00")

VN_TZ = pytz.timezone(timezone_str)
logger = logging.getLogger(__name__)

app = FastAPI(title="BIDA SKY9 Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="webapp"), name="static")


import pg_wrapper
def get_db():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("Thiếu DATABASE_URL trong file .env")
    conn = pg_wrapper.connect(db_url)
    return conn


def vn_now():
    return datetime.now(VN_TZ)


def get_business_date(target_date=None):
    """Xác định ngày kinh doanh."""
    now = target_date or vn_now()
    try:
        sh, sm = map(int, business_day_start.split(':'))
    except ValueError:
        sh, sm = 6, 0
    if now.hour < sh or (now.hour == sh and now.minute < sm):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def get_business_range(date_str):
    """Trả về (start_utc, end_utc) cho một ngày kinh doanh."""
    try:
        sh, sm = map(int, business_day_start.split(':'))
    except ValueError:
        sh, sm = 6, 0
    base = datetime.strptime(date_str, "%Y-%m-%d")
    start_local = VN_TZ.localize(base.replace(hour=sh, minute=sm, second=0))
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def fmt_money(amount):
    if amount is None:
        return "0đ"
    return f"{int(amount):,}đ".replace(",", ".")


# ─── Pages ───────────────────────────────────────────────────

@app.get("/")
async def read_index():
    return FileResponse("webapp/index.html")


@app.get("/adjustments")
async def read_adjustments():
    return FileResponse("webapp/adjustments.html")


# ─── API: Dashboard Overview ─────────────────────────────────

@app.get("/api/v2/overview")
async def api_overview(date: Optional[str] = None):
    """Trả về tổng quan cho 1 ngày kinh doanh."""
    conn = get_db()
    try:
        if not date:
            date = get_business_date()
        start_utc, end_utc = get_business_range(date)

        # 1. Doanh thu bill ngày
        rev_row = conn.execute("""
            SELECT COALESCE(SUM(total_bill_revenue), 0) as bill_revenue,
                   COUNT(*) as shift_count
            FROM cash_shifts 
            WHERE shift_date = ? AND status IN ('closed', 'pending_review')
        """, (date,)).fetchone()
        bill_revenue = rev_row["bill_revenue"]

        # Chuyển khoản trong ngày
        transfer_row = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) as total
            FROM transactions
            WHERE type = 'bank_transfer'
            AND created_at >= ? AND created_at < ?
            AND (status = 'completed' OR status IS NULL)
        """, (start_utc, end_utc)).fetchone()

        # Chi trong ngày
        expense_row = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) as total
            FROM transactions
            WHERE type = 'expense'
            AND created_at >= ? AND created_at < ?
            AND (status = 'completed' OR status IS NULL)
        """, (start_utc, end_utc)).fetchone()

        # 2. Nợ hiện tại
        debt_row = conn.execute("""
            SELECT COUNT(*) as cnt,
                   COALESCE(SUM(remaining_debt), 0) as total,
                   COUNT(DISTINCT customer_id) as customers
            FROM debt_records 
            WHERE status IN ('active', 'partial', 'overdue')
        """).fetchone()

        # 3. Nhân viên đang làm
        active_row = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM attendance_sessions
            WHERE status = 'checked_in'
        """).fetchone()

        # Tổng nhân viên
        total_emp = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'employee' AND is_active = 1"
        ).fetchone()["cnt"]

        # 4. Việc chờ xử lý
        pending_items = []

        # 4a. Bàn giao chưa xác nhận
        handover = conn.execute("""
            SELECT COUNT(*) as cnt FROM cash_shifts
            WHERE status = 'closed' AND handover_confirmed_by IS NULL
            AND shift_date >= date(?, '-3 days')
        """, (date,)).fetchone()["cnt"]
        if handover > 0:
            pending_items.append({
                "type": "handover", "count": handover,
                "label": f"{handover} bàn giao chưa xác nhận",
                "icon": "handshake", "severity": "warning"
            })

        # 4b. Yêu cầu sửa công
        adj_pending = conn.execute("""
            SELECT COUNT(*) as cnt FROM attendance_adjustments
            WHERE status = 'pending'
        """).fetchone()["cnt"]
        if adj_pending > 0:
            pending_items.append({
                "type": "adjustment", "count": adj_pending,
                "label": f"{adj_pending} yêu cầu sửa công chờ duyệt",
                "icon": "edit", "severity": "warning"
            })

        # 4c. Chênh lệch quỹ
        fund_diff = conn.execute("""
            SELECT COUNT(*) as cnt FROM cash_shifts
            WHERE status IN ('closed', 'pending_review')
            AND shift_date = ? AND closing_diff != 0 AND closing_diff IS NOT NULL
        """, (date,)).fetchone()["cnt"]
        if fund_diff > 0:
            pending_items.append({
                "type": "fund_diff", "count": fund_diff,
                "label": f"{fund_diff} ca có chênh lệch quỹ",
                "icon": "alert-triangle", "severity": "danger"
            })

        # 4d. Kiểm kê lệch
        inv_diff = conn.execute("""
            SELECT COUNT(*) as cnt FROM inventory_check_items
            WHERE difference != 0 AND adjustment_approved = 0
        """).fetchone()["cnt"]
        if inv_diff > 0:
            pending_items.append({
                "type": "inventory_diff", "count": inv_diff,
                "label": f"{inv_diff} mặt hàng kiểm kê lệch",
                "icon": "package", "severity": "warning"
            })

        # 4e. Nợ quá hạn
        overdue = conn.execute("""
            SELECT COUNT(*) as cnt FROM debt_records
            WHERE status IN ('active', 'partial', 'overdue')
            AND due_date IS NOT NULL AND due_date < date('now')
        """).fetchone()["cnt"]
        if overdue > 0:
            pending_items.append({
                "type": "overdue_debt", "count": overdue,
                "label": f"{overdue} khoản nợ quá hạn",
                "icon": "clock", "severity": "danger"
            })

        return {
            "date": date,
            "updated_at": vn_now().strftime("%H:%M %d/%m/%Y"),
            "revenue": {
                "bill_total": bill_revenue,
                "transfer_total": transfer_row["total"],
                "expense_total": expense_row["total"],
                "shift_count": rev_row["shift_count"],
            },
            "debt": {
                "total_amount": debt_row["total"],
                "record_count": debt_row["cnt"],
                "customer_count": debt_row["customers"],
            },
            "staff": {
                "active_count": active_row["cnt"],
                "total_count": total_emp,
            },
            "pending": {
                "total": len(pending_items),
                "items": pending_items,
            }
        }
    finally:
        conn.close()


# ─── API: Ca hôm nay ─────────────────────────────────────────

@app.get("/api/v2/shifts-today")
async def api_shifts_today(date: Optional[str] = None):
    conn = get_db()
    try:
        if not date:
            date = get_business_date()

        rows = conn.execute("""
            SELECT a.id, a.employee_id, u.display_name,
                   a.scheduled_start, a.scheduled_end,
                   a.checkin_time, a.checkout_time,
                   a.checkin_flow_started_at,
                   a.status, a.late_minutes, a.approved_minutes,
                   st.name as shift_name
            FROM attendance_sessions a
            JOIN users u ON a.employee_id = u.telegram_id
            LEFT JOIN shift_registrations sr ON a.registration_id = sr.id
            LEFT JOIN shift_templates st ON sr.template_id = st.id
            WHERE a.shift_date = ?
            ORDER BY a.scheduled_start ASC, a.checkin_time ASC
        """, (date,)).fetchall()

        shifts = []
        for r in rows:
            status_label = {
                "checked_in": "Đang làm",
                "checked_out": "Đã ra",
                "completed": "Hoàn thành",
            }.get(r["status"], r["status"])

            shifts.append({
                "id": r["id"],
                "employee": r["display_name"],
                "shift_name": r["shift_name"] or "—",
                "scheduled_start": r["scheduled_start"],
                "scheduled_end": r["scheduled_end"],
                "checkin_time": r["checkin_time"],
                "checkout_time": r["checkout_time"],
                "status": r["status"],
                "status_label": status_label,
                "late_minutes": r["late_minutes"] or 0,
                "approved_minutes": r["approved_minutes"],
            })

        return {"date": date, "shifts": shifts}
    finally:
        conn.close()


# ─── API: Hoạt động gần đây ──────────────────────────────────

@app.get("/api/v2/activity")
async def api_activity(limit: int = 20):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT al.id, al.actor_id, u.display_name as actor_name,
                   al.action, al.entity_type, al.entity_id,
                   al.old_data, al.new_data, al.reason, al.created_at
            FROM audit_log al
            LEFT JOIN users u ON al.actor_id = u.telegram_id
            ORDER BY al.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        activities = []
        for r in rows:
            activities.append({
                "id": r["id"],
                "actor": r["actor_name"] or f"ID:{r['actor_id']}",
                "action": r["action"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "old_data": r["old_data"],
                "new_data": r["new_data"],
                "reason": r["reason"],
                "time": r["created_at"],
            })

        return {"activities": activities}
    finally:
        conn.close()


# ─── API: Hàng tồn thấp ──────────────────────────────────────

@app.get("/api/v2/low-stock")
async def api_low_stock():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ii.id, ii.name, ii.base_unit, ii.low_stock_threshold, ii.category,
                   COALESCE(
                       (SELECT SUM(quantity_received) FROM receipt_items ri 
                        JOIN stock_receipts sr ON ri.receipt_id = sr.id 
                        WHERE ri.item_id = ii.id AND sr.status = 'confirmed'), 0
                   )
                   - COALESCE(
                       (SELECT SUM(quantity) FROM stock_issues si 
                        WHERE si.item_id = ii.id), 0
                   )
                   + COALESCE(
                       (SELECT SUM(ins.quantity) FROM initial_stock ins 
                        WHERE ins.item_id = ii.id), 0
                   ) as current_stock
            FROM inventory_items ii
            WHERE ii.is_active = 1 AND ii.low_stock_threshold IS NOT NULL
        """).fetchall()

        items = []
        for r in rows:
            current_stock = r["current_stock"]
            threshold = r["low_stock_threshold"]
            if current_stock <= threshold:
                items.append({
                    "id": r["id"],
                    "name": r["name"],
                    "unit": r["base_unit"],
                    "current": current_stock,
                    "threshold": threshold,
                    "category": r["category"],
                })

        # Sort by current_stock ascending
        items.sort(key=lambda x: x["current"])

        return {"items": items}
    finally:
        conn.close()


# ─── API: Debts ───────────────────────────────────────────────

@app.get("/api/v2/debts")
async def api_debts():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT dr.id, c.name as customer_name, c.phone,
                   dr.total_bill_amount, dr.remaining_debt,
                   dr.status, dr.due_date, dr.bill_code,
                   dr.created_at, u.display_name as recorded_by_name
            FROM debt_records dr
            JOIN customers c ON dr.customer_id = c.id
            LEFT JOIN users u ON dr.recorded_by = u.telegram_id
            WHERE dr.status IN ('active', 'partial', 'overdue')
            ORDER BY
                CASE WHEN dr.due_date IS NOT NULL AND dr.due_date < date('now') THEN 0 ELSE 1 END,
                dr.due_date ASC,
                dr.created_at DESC
        """).fetchall()

        debts = []
        for r in rows:
            is_overdue = r["due_date"] and r["due_date"] < datetime.now().strftime("%Y-%m-%d")
            debts.append({
                "id": r["id"],
                "customer": r["customer_name"],
                "phone": r["phone"],
                "bill_total": r["total_bill_amount"],
                "remaining": r["remaining_debt"],
                "status": r["status"],
                "due_date": r["due_date"],
                "bill_code": r["bill_code"],
                "is_overdue": is_overdue,
                "created_at": r["created_at"],
                "recorded_by": r["recorded_by_name"],
            })

        summary = conn.execute("""
            SELECT COUNT(*) as cnt,
                   COALESCE(SUM(remaining_debt), 0) as total
            FROM debt_records
            WHERE status IN ('active', 'partial', 'overdue')
        """).fetchone()

        return {
            "debts": debts,
            "summary": {
                "count": summary["cnt"],
                "total": summary["total"],
            }
        }
    finally:
        conn.close()


# ─── API: Cash Shifts ────────────────────────────────────────

@app.get("/api/v2/cash-shifts")
async def api_cash_shifts(date: Optional[str] = None):
    conn = get_db()
    try:
        if not date:
            date = get_business_date()

        rows = conn.execute("""
            SELECT cs.id, cs.employee_id, u.display_name,
                   cs.shift_date, cs.opening_cash, cs.closing_cash_counted,
                   cs.total_bill_revenue, cs.expected_closing_cash,
                   cs.closing_diff, cs.status,
                   cs.opened_at, cs.closed_at,
                   cs.handover_confirmed_by,
                   cs.handover_confirmed_at
            FROM cash_shifts cs
            JOIN users u ON cs.employee_id = u.telegram_id
            WHERE cs.shift_date = ?
            ORDER BY cs.opened_at ASC
        """, (date,)).fetchall()

        shifts = []
        for r in rows:
            shifts.append({
                "id": r["id"],
                "employee": r["display_name"],
                "date": r["shift_date"],
                "opening_cash": r["opening_cash"],
                "closing_cash": r["closing_cash_counted"],
                "bill_revenue": r["total_bill_revenue"],
                "expected_closing": r["expected_closing_cash"],
                "diff": r["closing_diff"],
                "status": r["status"],
                "opened_at": r["opened_at"],
                "closed_at": r["closed_at"],
                "handover_confirmed": r["handover_confirmed_by"] is not None,
            })

        return {"date": date, "shifts": shifts}
    finally:
        conn.close()


# ─── API: Employees ──────────────────────────────────────────

@app.get("/api/v2/employees")
async def api_employees():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT u.telegram_id, u.display_name, u.role, u.phone, u.is_active,
                   u.created_at,
                   a.status as att_status, a.checkin_time, a.checkout_time
            FROM users u
            LEFT JOIN (
                SELECT employee_id, status, checkin_time, checkout_time
                FROM attendance_sessions
                WHERE status = 'checked_in'
            ) a ON u.telegram_id = a.employee_id
            WHERE u.role IN ('employee', 'owner')
            ORDER BY u.is_active DESC, u.display_name ASC
        """).fetchall()

        employees = []
        for r in rows:
            employees.append({
                "id": r["telegram_id"],
                "name": r["display_name"],
                "role": r["role"],
                "phone": r["phone"],
                "is_active": r["is_active"],
                "is_working": r["att_status"] == "checked_in",
                "checkin_time": r["checkin_time"],
                "created_at": r["created_at"],
            })

        return {"employees": employees}
    finally:
        conn.close()


# ─── API: Inventory ──────────────────────────────────────────

@app.get("/api/v2/inventory")
async def api_inventory():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ii.id, ii.name, ii.category, ii.base_unit, ii.pack_unit,
                   ii.pack_size, ii.low_stock_threshold, ii.is_active,
                   ii.last_import_price,
                   COALESCE(
                       (SELECT SUM(ri.quantity_received) FROM receipt_items ri 
                        JOIN stock_receipts sr ON ri.receipt_id = sr.id
                        WHERE ri.item_id = ii.id AND sr.status = 'confirmed'), 0
                   )
                   - COALESCE(
                       (SELECT SUM(si.quantity) FROM stock_issues si
                        WHERE si.item_id = ii.id), 0
                   )
                   + COALESCE(
                       (SELECT SUM(ins.quantity) FROM initial_stock ins
                        WHERE ins.item_id = ii.id), 0
                   ) as current_stock
            FROM inventory_items ii
            WHERE ii.is_active = 1
            ORDER BY ii.category, ii.name
        """).fetchall()

        items = []
        for r in rows:
            is_low = (r["low_stock_threshold"] is not None and
                      r["current_stock"] <= r["low_stock_threshold"])
            items.append({
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "unit": r["base_unit"],
                "pack_unit": r["pack_unit"],
                "pack_size": r["pack_size"],
                "stock": r["current_stock"],
                "threshold": r["low_stock_threshold"],
                "is_low": is_low,
                "price": r["last_import_price"],
            })

        return {"items": items}
    finally:
        conn.close()


# ─── Keep old endpoints for compatibility ─────────────────────

@app.get("/api/dashboard")
async def get_dashboard_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users")
    members_count = cursor.fetchone()["count"]
    cursor.execute("SELECT COUNT(*) as count FROM attendance_sessions WHERE status = 'checked_in'")
    active_staff = cursor.fetchone()["count"]
    import datetime as dt
    now = dt.datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
    cursor.execute("""
        SELECT SUM(total_bill_revenue) as total 
        FROM cash_shifts 
        WHERE status = 'closed' AND closed_at >= ?
    """, (month_start,))
    row = cursor.fetchone()
    monthly_revenue = row["total"] if row and row["total"] else 0
    conn.close()
    return {
        "revenue": monthly_revenue,
        "active_tables": 0,
        "members": members_count,
        "active_staff": active_staff
    }


@app.get("/api/employees")
async def get_employees_status():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.display_name, u.role, a.status, a.checkin_time 
        FROM users u
        LEFT JOIN (
            SELECT employee_id, status, checkin_time 
            FROM attendance_sessions 
            WHERE status = 'checked_in'
        ) a ON u.telegram_id = a.employee_id
    """)
    rows = cursor.fetchall()
    employees = []
    for r in rows:
        employees.append({
            "name": r["display_name"],
            "role": r["role"],
            "status": "Active" if r["status"] == "checked_in" else "Inactive",
            "checkin_time": r["checkin_time"] if r["checkin_time"] else "N/A"
        })
    conn.close()
    return employees


# ─── Keep old adjustment endpoints ───────────────────────────

@app.get("/api/adjustments/list")
async def list_adjustments(type: str, date: str = ""):
    conn = get_db()
    cursor = conn.cursor()
    if type == "attendance":
        cursor.execute("""
            SELECT a.id, a.employee_id, u.display_name as employee_name, 
                   a.checkin_time, a.checkout_time, a.status, a.is_adjusted
            FROM attendance_sessions a
            JOIN users u ON a.employee_id = u.telegram_id
            ORDER BY a.created_at DESC LIMIT 50
        """)
        rows = cursor.fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result
    conn.close()
    return []


class AttendanceAdjustRequest(BaseModel):
    reason: str
    checkin_time: str = None
    checkout_time: str = None


@app.post("/api/adjustments/preview/attendance/{session_id}")
async def preview_attendance_adjustment(session_id: int, req: AttendanceAdjustRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM attendance_sessions WHERE id = ?", (session_id,))
    session = cursor.fetchone()
    if not session:
        conn.close()
        return {"detail": "Không tìm thấy phiên công"}
    messages = []
    messages.append(f"Giờ vào cũ: {session['checkin_time']} → Mới: {req.checkin_time}")
    messages.append(f"Giờ ra cũ: {session['checkout_time']} → Mới: {req.checkout_time}")
    conn.close()
    return {"messages": messages}


@app.post("/api/adjustments/apply/attendance/{session_id}")
async def apply_attendance_adjustment(session_id: int, req: AttendanceAdjustRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM attendance_sessions WHERE id = ?", (session_id,))
    session = cursor.fetchone()
    import datetime as dt
    now_str = dt.datetime.now().isoformat()
    old_data = json.dumps(dict(session))
    try:
        cursor.execute("""
            INSERT INTO audit_logs (entity_type, entity_id, adjusted_by, adjusted_at, reason, old_data, new_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('attendance_sessions', session_id, 1, now_str, req.reason, old_data, 'pending'))
        cursor.execute("""
            UPDATE attendance_sessions 
            SET checkin_time = ?, checkout_time = ?, is_adjusted = 1,
                original_checkin_time = COALESCE(original_checkin_time, checkin_time),
                original_checkout_time = COALESCE(original_checkout_time, checkout_time)
            WHERE id = ?
        """, (req.checkin_time, req.checkout_time, session_id))
        if session['checkout_time'] and req.checkout_time and session['checkout_time'] != req.checkout_time:
            cursor.execute("""
                UPDATE attendance_sessions
                SET checkin_time = ?, is_adjusted = 1,
                    original_checkin_time = COALESCE(original_checkin_time, checkin_time)
                WHERE checkin_time = ?
            """, (req.checkout_time, session['checkout_time']))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
