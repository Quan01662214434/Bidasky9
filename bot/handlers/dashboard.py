"""
Handler cho Dashboard Hôm nay (Dành cho nhân viên).
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from bot.constants import CB, PAGE_SIZE, TransactionType
from bot.services.dashboard_service import get_dashboard_today, get_transaction_details
from bot.services.cash_shift_service import get_open_cash_shift
from bot.utils.formatters import format_money, format_datetime_vn, format_date_vn
from bot.utils.permissions import require_private_chat, require_employee

logger = logging.getLogger(__name__)


@require_private_chat
@require_employee
async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị dashboard tổng quan hôm nay."""
    query = update.callback_query
    await query.answer()
    
    # Parse callback_data: dt:view:mode:page
    # Default mode: all, page: 0
    parts = query.data.split(":")
    mode = parts[2] if len(parts) > 2 else "all"
    page = int(parts[3]) if len(parts) > 3 else 0
    
    user_id = update.effective_user.id
    shift = get_open_cash_shift()
    shift_id = shift["id"] if shift else None
    
    data = get_dashboard_today(user_id, shift_id, mode)
    transactions = data["transactions"]
    
    # Banner nợ
    from bot.services.debt_service import get_active_debts, get_debt_summary, format_debt_banner
    debt_summary = get_debt_summary()
    debts = get_active_debts(limit=5)
    banner = format_debt_banner(debts, debt_summary)
    
    # Text tổng hợp
    text = f"{banner}\n\n"
    text += f"📊 *DASHBOARD HÔM NAY* ({data['date']})\n"
    text += f"_{'Toàn ngày' if mode == 'all' else 'Ca hiện tại' if mode == 'shift' else 'Tôi ghi'}_\n\n"
    text += f"💰 *Tổng chuyển khoản:* {format_money(data['total_amount'])}\n"
    text += f"📑 *Số giao dịch:* {data['count']}\n"
    if shift_id:
        text += f"💼 *Ca hiện tại:* {format_money(data['shift_total'])}\n"
    
    text += "\n"
    
    if not transactions:
        text += "📝 _Chưa ghi nhận chuyển khoản trong ngày này_\n"
    else:
        # Paginator
        start_idx = page * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        page_txns = transactions[start_idx:end_idx]
        
        for txn in page_txns:
            time_str = format_datetime_vn(txn["created_at"], "%H:%M")
            amt = format_money(txn["amount"])
            
            if txn["type"] == TransactionType.BANK_TRANSFER:
                type_name = "Thanh toán bill"
                ref = f"Bill: {txn.get('bill_code', '?')}"
                if txn.get('note'):
                    ref += f" - {txn['note']}"
            elif txn["type"] == TransactionType.DEBT_COLLECT_TRANSFER:
                type_name = "Thu nợ"
                ref = f"Khách: {txn.get('debt_customer', '?')}"
            else:
                type_name = "Khác"
                ref = ""
                
            status = "✅ Nhận" if txn.get("verified") else "⏳ Chờ duyệt"
            
            text += f"• *{time_str}* | {amt}\n"
            text += f"  {type_name} | {ref}\n"
            text += f"  {status} | NV: {txn['recorder_name']}\n"
            text += f"  👉 /dt_{txn['id']}\n\n"
            
    # Keyboard
    keyboard = []
    
    # Lọc
    filter_row = []
    if mode != "all": filter_row.append(InlineKeyboardButton("Toàn ngày", callback_data=f"{CB.DASHBOARD_TODAY}:view:all:0"))
    if mode != "shift": filter_row.append(InlineKeyboardButton("Ca hiện tại", callback_data=f"{CB.DASHBOARD_TODAY}:view:shift:0"))
    if mode != "me": filter_row.append(InlineKeyboardButton("Tôi ghi", callback_data=f"{CB.DASHBOARD_TODAY}:view:me:0"))
    if filter_row:
        keyboard.append(filter_row)
        
    # Phân trang
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Trước", callback_data=f"{CB.DASHBOARD_TODAY}:view:{mode}:{page-1}"))
    if end_idx < len(transactions):
        nav_row.append(InlineKeyboardButton("Tiếp ➡️", callback_data=f"{CB.DASHBOARD_TODAY}:view:{mode}:{page+1}"))
    if nav_row:
        keyboard.append(nav_row)
        
    # Nút chức năng
    keyboard.append([
        InlineKeyboardButton("💵 Ghi chuyển khoản", callback_data=f"{CB.TXN_TRANSFER}:start"),
        InlineKeyboardButton("🔄 Làm mới", callback_data=f"{CB.DASHBOARD_TODAY}:view:{mode}:{page}")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Về Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_txn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem chi tiết giao dịch từ lệnh /dt_id."""
    text = update.message.text.strip()
    try:
        txn_id = int(text.split("_")[1])
    except (IndexError, ValueError):
        return
        
    txn = get_transaction_details(txn_id)
    if not txn:
        await update.message.reply_text("❌ Không tìm thấy giao dịch này.")
        return
        
    time_str = format_datetime_vn(txn["created_at"])
    amt = format_money(txn["amount"])
    status = "✅ Đã xác nhận" if txn.get("verified") else "⏳ Chờ chủ xác nhận"
    
    res = f"Chi tiết giao dịch #{txn['id']}\n"
    res += f"🕒 Thời gian: {time_str}\n"
    res += f"💰 Số tiền: {amt}\n"
    res += f"👤 Nhân viên ghi: {txn['recorder_name']}\n"
    if txn.get('cash_shift_id'):
        res += f"💼 Ca quỹ: #{txn['cash_shift_id']}\n"
    res += f"📊 Trạng thái: {status}\n\n"
    
    if txn["type"] == TransactionType.BANK_TRANSFER:
        res += f"Loại: Thanh toán bill\n"
        res += f"Mã Bill: {txn.get('bill_code', 'N/A')}\n"
    elif txn["type"] == TransactionType.DEBT_COLLECT_TRANSFER:
        res += f"Loại: Thu nợ (Chuyển khoản)\n"
        res += f"Khách hàng: {txn.get('debt_customer', 'N/A')}\n"
        
    if txn.get("note"):
        res += f"📝 Ghi chú: {txn['note']}\n"
        
    await update.message.reply_text(res, parse_mode="Markdown")


def get_handlers():
    from telegram.ext import MessageHandler, filters
    return [
        CallbackQueryHandler(show_dashboard, pattern=f"^{CB.DASHBOARD_TODAY}:view"),
        MessageHandler(filters.Regex(r'^/dt_\d+'), handle_txn_command)
    ]
