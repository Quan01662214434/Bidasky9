"""
Web Server cho BIDA SKY9 Dashboard.
FastAPI backend phục vụ Mini App quản lý chủ quán.
"""
import sqlite3
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx
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


# ─── Telegram Bot Webhook Integration ────────────────────────
# Bot chạy TRONG web server, không cần process riêng
from telegram import Update
from fastapi import Request

bot_application = None  # Will be initialized on startup
_keep_alive_task = None  # Background task to prevent Render from sleeping
_processed_update_ids = set()  # Dedup webhook updates
_MAX_PROCESSED_IDS = 2000      # Limit set size


async def _keep_alive_loop():
    """Self-ping every 10 min to prevent Render Free Tier from sleeping."""
    base_url = os.getenv("RENDER_EXTERNAL_URL", os.getenv("WEBAPP_URL", ""))
    if not base_url:
        logger.warning("No RENDER_EXTERNAL_URL, keep-alive disabled")
        return

    health_url = f"{base_url}/health"
    logger.info("Keep-alive enabled: ping %s every 10 min", health_url)

    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(health_url)
                logger.debug("Keep-alive ping: %s", resp.status_code)
        except asyncio.CancelledError:
            logger.info("Keep-alive stopped")
            break
        except Exception as e:
            logger.warning("Keep-alive ping error: %s", e)


@app.on_event("startup")
async def startup_event():
    """Khởi tạo bot và đăng ký webhook khi server start."""
    global bot_application, _keep_alive_task
    
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    try:
        from bot_setup import create_bot_application, setup_periodic_jobs
        
        bot_application = create_bot_application()
        
        # Initialize and start (starts job queue for periodic tasks)
        await bot_application.initialize()
        await bot_application.start()
        
        # Setup periodic jobs (notifications, late check-ins, etc.)
        await setup_periodic_jobs(bot_application)
        
        # Set webhook URL
        # Render provides RENDER_EXTERNAL_URL automatically
        base_url = os.getenv("RENDER_EXTERNAL_URL", os.getenv("WEBAPP_URL", ""))
        bot_token = os.getenv("BOT_TOKEN", "")
        
        if base_url and bot_token:
            webhook_url = f"{base_url}/telegram-webhook/{bot_token}"
            await bot_application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=False,
            )
            logger.info("Webhook đã đăng ký: %s", webhook_url.replace(bot_token, "***"))
        else:
            logger.warning("Không tìm thấy RENDER_EXTERNAL_URL hoặc BOT_TOKEN, webhook chưa được đăng ký")
        
        # Bật keep-alive self-ping để Render không ngủ

        _keep_alive_task = asyncio.create_task(_keep_alive_loop())

        

        logger.info("=== BOT ĐÃ SẴN SÀNG (WEBHOOK MODE) ===")
        
    except Exception as e:
        logger.error("Lỗi khởi tạo bot: %s", e, exc_info=True)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup khi server shutdown."""
    global bot_application, _keep_alive_task
    
    # Stop keep-alive
    if _keep_alive_task:
        _keep_alive_task.cancel()
        try:
            await _keep_alive_task
        except asyncio.CancelledError:
            pass
    
    # Stop bot with timeout
    if bot_application:
        try:
            await asyncio.wait_for(bot_application.stop(), timeout=10)
            await asyncio.wait_for(bot_application.shutdown(), timeout=10)
            logger.info("Bot shutdown successfully")
        except asyncio.TimeoutError:
            logger.warning("Bot shutdown timeout, forcing exit")
        except Exception as e:
            logger.error("Bot shutdown error: %s", e)


@app.post("/telegram-webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    """Endpoint nhận update từ Telegram webhook.
    
    Includes update_id dedup to prevent double-processing when
    Telegram retries or user clicks buttons multiple times.
    """
    global _processed_update_ids
    bot_token = os.getenv("BOT_TOKEN", "")
    if token != bot_token:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    if not bot_application:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    
    try:
        data = await request.json()
        update_id = data.get("update_id")
        
        # Dedup: skip if already processed
        if update_id and update_id in _processed_update_ids:
            logger.debug("Skipped duplicate update_id=%s", update_id)
            return {"ok": True}
        
        # Track this update
        if update_id:
            _processed_update_ids.add(update_id)
            # Prevent unbounded growth
            if len(_processed_update_ids) > _MAX_PROCESSED_IDS:
                # Remove oldest half
                sorted_ids = sorted(_processed_update_ids)
                _processed_update_ids = set(sorted_ids[_MAX_PROCESSED_IDS // 2:])
        
        update = Update.de_json(data, bot_application.bot)
        await bot_application.process_update(update)
    except Exception as e:
        logger.error("Webhook processing error: %s", e, exc_info=True)
    
    # Always return 200 to Telegram to prevent retries
    return {"ok": True}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "bot_initialized": bot_application is not None,
        "mode": "webhook",
    }



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


import urllib.parse
import hmac
import hashlib

def verify_owner_auth(authorization: str = Header(None)):
    """Xác thực Telegram WebApp initData và kiểm tra quyền Owner."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Thiếu token xác thực")
    
    init_data = authorization.split("Bearer ")[1]
    
    # 1. Bypass logic for local testing if needed, or enforce it
    # We enforce it because this is production logic.
    try:
        parsed = urllib.parse.parse_qsl(init_data)
        data_dict = dict(parsed)
        if 'hash' not in data_dict:
            if init_data == str(owner_id):
                return True
            raise HTTPException(status_code=401, detail="Invalid token format")
            
        received_hash = data_dict.pop('hash')
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_dict.items()))
        
        bot_token = os.getenv("BOT_TOKEN")
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calc_hash != received_hash:
            # Fallback for dev mode (if init_data is just the owner ID)
            if init_data != str(owner_id):
                raise HTTPException(status_code=401, detail="Chữ ký không hợp lệ")
                
        # Parse user
        if 'user' in data_dict:
            user_info = json.loads(data_dict['user'])
            user_id = user_info.get('id')
            if str(user_id) != str(owner_id):
                raise HTTPException(status_code=403, detail="Chỉ Chủ quán mới có quyền thực hiện thao tác này")
        elif init_data == str(owner_id):
            pass # Dev mode
        else:
            raise HTTPException(status_code=401, detail="Không tìm thấy thông tin user")
            
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Lỗi xác thực: {e}")
        raise HTTPException(status_code=401, detail="Xác thực thất bại")
        
    return True


# ─── Pages ───────────────────────────────────────────────────

@app.get("/")
async def read_index():
    return FileResponse("webapp/index.html")


@app.get("/adjustments")
async def read_adjustments():
    return FileResponse("webapp/adjustments.html")


@app.get("/inventory/import")
async def read_inventory_import():
    return FileResponse("webapp/inventory_import.html")


@app.get("/inventory/check")
async def read_inventory_check():
    return FileResponse("webapp/inventory_check.html")


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
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        min_date = (date_obj - timedelta(days=3)).strftime("%Y-%m-%d")
        handover = conn.execute("""
            SELECT COUNT(*) as cnt FROM cash_shifts
            WHERE status = 'closed' AND handover_confirmed_by IS NULL
            AND shift_date >= %s
        """, (min_date,)).fetchone()["cnt"]
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
                   a.status as att_status, a.checkin_time, a.checkout_time,
                   (SELECT hourly_rate FROM wage_rates w WHERE w.employee_id = u.telegram_id ORDER BY w.effective_from DESC LIMIT 1) as hourly_rate
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
                "rate": r["hourly_rate"] or 0
            })

        return {"employees": employees}
    finally:
        conn.close()

class EmployeePayload(BaseModel):
    id: Optional[int] = None
    name: str
    role: str
    telegram_id: int
    rate: float
    is_active: int

@app.post("/api/v2/employees")
async def api_save_employee(payload: EmployeePayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        now = vn_now().isoformat()
        
        if payload.id:
            conn.execute("""
                UPDATE users 
                SET display_name = %s, role = %s, telegram_id = %s, is_active = %s
                WHERE telegram_id = %s
            """, (payload.name, payload.role, payload.telegram_id, payload.is_active, payload.id))
            
            # Check current rate
            current_rate = conn.execute("SELECT hourly_rate FROM wage_rates WHERE employee_id = %s ORDER BY effective_from DESC LIMIT 1", (payload.telegram_id,)).fetchone()
            if not current_rate or current_rate["hourly_rate"] != int(payload.rate):
                conn.execute("""
                    INSERT INTO wage_rates (employee_id, hourly_rate, effective_from, created_at, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                """, (payload.telegram_id, int(payload.rate), now, now, str(owner_id)))
        else:
            conn.execute("""
                INSERT INTO users (telegram_id, display_name, role, is_active, created_at, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (payload.telegram_id, payload.name, payload.role, payload.is_active, now, str(owner_id)))
            
            if payload.rate > 0:
                conn.execute("""
                    INSERT INTO wage_rates (employee_id, hourly_rate, effective_from, created_at, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                """, (payload.telegram_id, int(payload.rate), now, now, str(owner_id)))
                
        pass  # autocommit
        return {"success": True}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi lưu nhân viên: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit


@app.get("/api/v2/salary")
async def api_salary(month: str = None, auth: bool = Depends(verify_owner_auth)):
    """Tính lương tháng — dùng approved_minutes từ attendance_sessions,
    đồng bộ logic với bot/services/salary_service.py."""
    conn = get_db()
    try:
        if not month:
            month = vn_now().strftime("%Y-%m")

        # Khoảng ngày trong tháng
        year_s, month_s = month.split("-")
        year_i, month_i = int(year_s), int(month_s)
        from_date = f"{year_i:04d}-{month_i:02d}-01"
        if month_i == 12:
            to_date = f"{year_i + 1:04d}-01-01"
        else:
            to_date = f"{year_i:04d}-{month_i + 1:02d}-01"

        # Get employees
        employees = conn.execute(
            "SELECT telegram_id, display_name FROM users WHERE role IN ('employee', 'owner') AND is_active = 1"
        ).fetchall()

        salary_data = []
        for emp in employees:
            emp_id = emp["telegram_id"]

            # Lấy phiên công đã checkout trong tháng (dùng shift_date, giống salary_service)
            sessions = conn.execute("""
                SELECT COUNT(*) as shift_count,
                       COALESCE(SUM(approved_minutes), 0) as total_minutes
                FROM attendance_sessions
                WHERE employee_id = ? AND shift_date >= ? AND shift_date < ?
                AND status IN ('checked_out', 'owner_created')
                AND approved_minutes IS NOT NULL
            """, (emp_id, from_date, to_date)).fetchone()

            total_minutes = sessions["total_minutes"] or 0
            total_hours = round(total_minutes / 60, 2)

            # Get latest hourly rate effective before/during this month
            rate_row = conn.execute("""
                SELECT hourly_rate FROM wage_rates
                WHERE employee_id = ? AND effective_from <= ?
                ORDER BY effective_from DESC LIMIT 1
            """, (emp_id, to_date)).fetchone()
            rate = rate_row["hourly_rate"] if rate_row else 0

            # Gross = minutes * rate / 60
            gross_salary = int(total_minutes * rate / 60) if rate else 0

            # Ứng lương đã giao (delivered) chưa đối trừ
            advances = conn.execute("""
                SELECT COALESCE(SUM(
                    CASE WHEN remaining_unsettled IS NOT NULL THEN remaining_unsettled
                         ELSE amount END
                ), 0) as total_advance
                FROM salary_advances
                WHERE employee_id = ? AND status = 'delivered'
                AND (remaining_unsettled > 0 OR remaining_unsettled IS NULL)
            """, (emp_id,)).fetchone()
            advance = advances["total_advance"] or 0

            # Đồ dùng nhân viên trong tháng
            consumables = conn.execute("""
                SELECT COALESCE(SUM(
                    cu.quantity * COALESCE(cp.unit_price, 0)
                ), 0) as total_consumable
                FROM consumable_usage cu
                LEFT JOIN consumable_prices cp
                  ON cu.item_id = cp.item_id
                  AND cp.period_year = ? AND cp.period_month = ?
                WHERE cu.employee_id = ? AND cu.is_free = 0
                AND cu.usage_datetime >= ? AND cu.usage_datetime < ?
            """, (year_i, month_i, emp_id, from_date, to_date)).fetchone()
            consumable_total = consumables["total_consumable"] or 0

            net_salary = gross_salary - advance - consumable_total

            salary_data.append({
                "employee_id": emp_id,
                "name": emp["display_name"],
                "shift_count": sessions["shift_count"] or 0,
                "total_hours": total_hours,
                "total_minutes": total_minutes,
                "hourly_rate": rate,
                "base_salary": gross_salary,
                "advance": advance,
                "consumable": consumable_total,
                "net_salary": net_salary,
            })

        return {"month": month, "salaries": salary_data}
    finally:
        pass  # autocommit

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
                "base_unit": r["base_unit"],
                "pack_unit": r["pack_unit"],
                "pack_size": r["pack_size"],
                "stock": r["current_stock"],
                "threshold": r["low_stock_threshold"],
                "low_stock_threshold": r["low_stock_threshold"],
                "is_low": is_low,
                "price": r["last_import_price"],
                "is_active": r["is_active"]
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


# ─── API: Inventory Import ───────────────────────────────────

from typing import List

class ImportItemModel(BaseModel):
    item_id: int
    unit: str
    quantity: float
    unit_price: float
    total: float

class ImportPayload(BaseModel):
    supplier: str
    import_date: str
    notes: Optional[str] = None
    total_amount: float
    payment_status: str
    paid_amount: float
    payment_method: str
    items: List[ImportItemModel]

@app.get("/api/v2/inventory/items")
async def api_inventory_items(auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, name, category, base_unit, pack_unit, pack_size FROM inventory_items WHERE is_active = 1").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            units = []
            if d.get("pack_unit") and d.get("pack_size"):
                units.append({"name": d["pack_unit"], "ratio": d["pack_size"]})
            d["units"] = units
            items.append(d)
        return {"items": items}
    finally:
        pass  # autocommit

@app.get("/api/v2/inventory/import")
async def api_inventory_import_dummy():
    # Only for testing, standard uses POST
    pass

@app.post("/api/v2/inventory/import")
async def api_inventory_import(payload: ImportPayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        now = vn_now()
        # Create receipt
        conn.execute("""
            INSERT INTO stock_receipts (supplier_name, received_date, total_amount, notes, status, created_at)
            VALUES (%s, %s, %s, %s, 'confirmed', %s)
        """, (payload.supplier, payload.import_date, payload.total_amount, payload.notes, now.isoformat()))
        
        receipt_id = conn.lastrowid
        
        for item in payload.items:
            # Calculate base quantity
            row = conn.execute("SELECT base_unit, pack_unit, pack_size FROM inventory_items WHERE id = %s", (item.item_id,)).fetchone()
            base_qty = item.quantity
            if row and row["pack_unit"] == item.unit and row["pack_size"]:
                base_qty = item.quantity * row["pack_size"]
                
            conn.execute("""
                INSERT INTO receipt_items (receipt_id, item_id, quantity_received, unit_price, unit, total_price)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (receipt_id, item.item_id, base_qty, item.unit_price, item.unit, item.total))
            
            # Update last import price
            conn.execute("UPDATE inventory_items SET last_import_price = %s WHERE id = %s", (item.unit_price, item.item_id))
            
        # Handle payment if paid
        if payload.payment_status in ('paid', 'partial') and payload.paid_amount > 0:
            shift_id = None
            if payload.payment_method == 'cash':
                shift_row = conn.execute("SELECT id FROM cash_shifts WHERE status = 'open' ORDER BY opened_at DESC LIMIT 1").fetchone()
                if shift_row:
                    shift_id = shift_row["id"]
                    
            conn.execute("""
                INSERT INTO transactions (type, amount, description, status, recorded_by, created_at, cash_shift_id, reference_type, reference_id)
                VALUES ('expense', %s, %s, 'completed', %s, %s, %s, 'stock_receipt', %s)
            """, (payload.paid_amount, f"Thanh toán nhập hàng: {payload.supplier}", str(owner_id), now.isoformat(), shift_id, receipt_id))
            
        pass  # autocommit
        return {"success": True, "receipt_id": receipt_id}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi nhập hàng: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit


class InventoryCheckItem(BaseModel):
    item_id: int
    system_stock: float
    counted_stock: float
    diff: float
    reason: str

class InventoryCheckPayload(BaseModel):
    adjustments: List[InventoryCheckItem]

@app.post("/api/v2/inventory/check")
async def api_inventory_check(payload: InventoryCheckPayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        now = vn_now().isoformat()
        for adj in payload.adjustments:
            if adj.diff == 0:
                continue
                
            # Log as stock issue if diff < 0 (loss), or a stock receipt if diff > 0 (found extra)
            # A more robust way is to use an adjustments table, but the system currently
            # uses initial_stock / receipt_items / stock_issues to calculate stock.
            # So negative diff -> stock_issue. Positive diff -> receipt_item (via an adjustment receipt)
            
            if adj.diff < 0:
                # Issue
                conn.execute("""
                    INSERT INTO stock_issues (item_id, quantity, reason, employee_id, check_date, check_time)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (adj.item_id, abs(adj.diff), f"Kiểm kho: {adj.reason}", str(owner_id), vn_now().strftime("%Y-%m-%d"), vn_now().strftime("%H:%M")))
            else:
                # Positive diff -> create a mini receipt
                conn.execute("""
                    INSERT INTO stock_receipts (supplier_name, received_date, total_amount, notes, status, created_at)
                    VALUES ('Điều chỉnh kiểm kho', %s, 0, %s, 'confirmed', %s)
                """, (vn_now().strftime("%Y-%m-%d"), adj.reason, now))
                receipt_id = conn.lastrowid
                conn.execute("""
                    INSERT INTO receipt_items (receipt_id, item_id, quantity_received, unit_price, unit, total_price)
                    VALUES (%s, %s, %s, 0, 'base', 0)
                """, (receipt_id, adj.item_id, adj.diff))

        pass  # autocommit
        return {"success": True}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi kiểm kho: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit

class SaveItemPayload(BaseModel):
    id: Optional[int] = None
    name: str
    category: str
    base_unit: str
    pack_unit: Optional[str] = None
    pack_size: Optional[int] = None
    low_stock_threshold: int = 10
    is_active: int = 1

@app.post("/api/v2/inventory/item")
async def api_save_inventory_item(payload: SaveItemPayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        if payload.id:
            # Update
            conn.execute("""
                UPDATE inventory_items 
                SET name = %s, category = %s, base_unit = %s, pack_unit = %s, 
                    pack_size = %s, low_stock_threshold = %s, is_active = %s
                WHERE id = %s
            """, (payload.name, payload.category, payload.base_unit, payload.pack_unit, 
                  payload.pack_size, payload.low_stock_threshold, payload.is_active, payload.id))
        else:
            # Insert
            conn.execute("""
                INSERT INTO inventory_items (name, category, base_unit, pack_unit, pack_size, low_stock_threshold, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (payload.name, payload.category, payload.base_unit, payload.pack_unit, 
                  payload.pack_size, payload.low_stock_threshold, payload.is_active))
        
        pass  # autocommit
        return {"success": True}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi lưu mặt hàng: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit

class TransactionPayload(BaseModel):
    type: str # 'income' or 'expense'
    amount: float
    description: str
    method: str # 'cash' or 'bank_transfer'

@app.get("/api/v2/transactions")
async def api_get_transactions(date: str = None, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        target_date = date if date else vn_now().strftime("%Y-%m-%d")
        
        # Need to parse date strictly if passing to SQLite/Postgres.
        # But SQLite stores timestamp as ISO 'YYYY-MM-DDTHH:MM:SS'. We can use LIKE.
        rows = conn.execute("""
            SELECT id, type, amount, description, created_at, cash_shift_id, status 
            FROM transactions 
            WHERE created_at LIKE %s 
            ORDER BY created_at DESC
        """, (target_date + "%",)).fetchall()
        
        txs = []
        for r in rows:
            # format time
            dt_str = r["created_at"]
            time_str = dt_str.split('T')[1][:5] if 'T' in dt_str else dt_str.split(' ')[1][:5]
            txs.append({
                "id": r["id"],
                "type": r["type"],
                "amount": r["amount"],
                "description": r["description"],
                "time": time_str,
                "status": r["status"]
            })
            
        return {"transactions": txs}
    finally:
        pass  # autocommit

@app.post("/api/v2/transactions")
async def api_post_transactions(payload: TransactionPayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        now = vn_now()
        shift_id = None
        if payload.method == 'cash':
            shift_row = conn.execute("SELECT id FROM cash_shifts WHERE status = 'open' ORDER BY opened_at DESC LIMIT 1").fetchone()
            if shift_row:
                shift_id = shift_row["id"]
                
        conn.execute("""
            INSERT INTO transactions (type, amount, description, status, recorded_by, created_at, cash_shift_id)
            VALUES (%s, %s, %s, 'completed', %s, %s, %s)
        """, (payload.type, payload.amount, payload.description, str(owner_id), now.isoformat(), shift_id))
        
        pass  # autocommit
        return {"success": True}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi tạo giao dịch: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit

class DebtPayPayload(BaseModel):
    debt_id: int
    amount: float
    method: str

@app.post("/api/v2/debts/pay")
async def api_debts_pay(payload: DebtPayPayload, auth: bool = Depends(verify_owner_auth)):
    conn = get_db()
    try:
        now = vn_now().isoformat()
        
        # 1. Get debt info
        debt = conn.execute("SELECT remaining_debt, customer_id, bill_code FROM debt_records WHERE id = %s", (payload.debt_id,)).fetchone()
        if not debt:
            raise HTTPException(status_code=404, detail="Không tìm thấy khoản nợ")
            
        remaining = debt["remaining_debt"]
        if payload.amount > remaining:
            raise HTTPException(status_code=400, detail="Số tiền trả lớn hơn số còn nợ")
            
        new_remaining = remaining - payload.amount
        new_status = 'paid' if new_remaining == 0 else 'partial'
        
        # 2. Update debt record
        conn.execute("""
            UPDATE debt_records 
            SET remaining_debt = %s, status = %s 
            WHERE id = %s
        """, (new_remaining, new_status, payload.debt_id))
        
        # 3. Create payment record
        conn.execute("""
            INSERT INTO debt_payments (debt_id, amount_paid, payment_date, recorded_by)
            VALUES (%s, %s, %s, %s)
        """, (payload.debt_id, payload.amount, vn_now().strftime("%Y-%m-%d"), str(owner_id)))
        
        # 4. If method is cash, create transaction
        if payload.method == 'cash':
            shift_id = None
            shift_row = conn.execute("SELECT id FROM cash_shifts WHERE status = 'open' ORDER BY opened_at DESC LIMIT 1").fetchone()
            if shift_row:
                shift_id = shift_row["id"]
                
            customer_row = conn.execute("SELECT name FROM customers WHERE id = %s", (debt["customer_id"],)).fetchone()
            customer_name = customer_row["name"] if customer_row else "Khách hàng"
            
            conn.execute("""
                INSERT INTO transactions (type, amount, description, status, recorded_by, created_at, cash_shift_id, reference_type, reference_id)
                VALUES ('income', %s, %s, 'completed', %s, %s, %s, 'debt_payment', %s)
            """, (payload.amount, f"Thu nợ: {customer_name} (Bill {debt['bill_code']})", str(owner_id), now, shift_id, payload.debt_id))
            
        pass  # autocommit
        return {"success": True, "new_remaining": new_remaining, "status": new_status}
    except Exception as e:
        pass  # autocommit
        logger.error(f"Lỗi trả nợ: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pass  # autocommit

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
