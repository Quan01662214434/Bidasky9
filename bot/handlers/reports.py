"""
Handler báo cáo và admin.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_owner
from bot.utils.formatters import format_money, format_date_vn
from bot.utils.validators import parse_money, parse_date
from bot.services.report_service import (
    get_daily_report, save_daily_revenue, format_daily_report, get_owner_dashboard
)
from bot.models.database import vn_now, backup_database

logger = logging.getLogger(__name__)

RPT_DATE, RPT_BILL, RPT_CONFIRM = range(90, 93)


# ==================== BÁO CÁO NGÀY ====================

async def select_report_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn ngày xem báo cáo."""
    query = update.callback_query
    await query.answer()
    
    from datetime import timedelta
    now = vn_now()
    
    keyboard = []
    for i in range(7):
        day = now - timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        label = day.strftime("%d/%m/%Y") + (" (Hôm nay)" if i == 0 else " (Hôm qua)" if i == 1 else "")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{CB.RPT_DAY}:date:{date_str}")])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        "📊 *BÁO CÁO DOANH THU*\n\nChọn ngày:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def view_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem báo cáo ngày."""
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split(":")[2]
    report = get_daily_report(date_str)
    text = format_daily_report(report)
    
    keyboard = [
        [InlineKeyboardButton("📝 Nhập tổng bill", callback_data=f"{CB.RPT_DAY}:input:{date_str}")],
        [InlineKeyboardButton("🔙 Chọn ngày", callback_data=f"{CB.RPT_DAY}:select")],
        [InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")],
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def start_input_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu nhập tổng bill."""
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split(":")[2]
    context.user_data["rpt_date"] = date_str
    
    await query.edit_message_text(
        f"📝 Nhập tổng doanh thu bill ngày {format_date_vn(date_str)}:\n"
        f"_(Từ báo cáo KiotViet)_",
        parse_mode="Markdown"
    )
    return RPT_BILL


async def receive_bill_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tổng bill."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return RPT_BILL
    
    date_str = context.user_data["rpt_date"]
    save_daily_revenue(date_str, amount, update.effective_user.id)
    
    report = get_daily_report(date_str)
    text = format_daily_report(report)
    
    await update.message.reply_text(f"✅ Đã lưu tổng bill: {format_money(amount)}\n\n{text}",
                                     parse_mode="Markdown")
    
    context.user_data.pop("rpt_date", None)
    return ConversationHandler.END


# ==================== DASHBOARD CHỦ ====================

async def view_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dashboard chủ quán."""
    query = update.callback_query
    if query:
        await query.answer()
    
    dashboard = get_owner_dashboard()
    
    text = "🏠 *TỔNG QUAN*\n\n"
    
    # Ai đang làm
    if dashboard["working_now"]:
        text += "👥 *Đang làm:*\n"
        for w in dashboard["working_now"]:
            text += f"  🟢 {w['display_name']}\n"
    else:
        text += "👥 Không ai đang làm\n"
    
    # Ca quỹ
    if dashboard["open_cash_shifts"]:
        text += f"\n🏦 Ca quỹ mở: {len(dashboard['open_cash_shifts'])}\n"
    
    # Chờ duyệt
    if dashboard["pending_registrations"]:
        text += f"\n📅 Đăng ký chờ duyệt: {dashboard['pending_registrations']}\n"
    if dashboard["pending_adjustments"]:
        text += f"📝 Sửa công chờ: {dashboard['pending_adjustments']}\n"
    if dashboard["pending_handover"]:
        text += f"🏦 Ca chưa bàn giao: {dashboard['pending_handover']}\n"
    if dashboard["problem_shifts"]:
        text += f"⚠️ Ca thiếu/dư: {dashboard['problem_shifts']}\n"
    
    # Nợ
    ds = dashboard["debt_summary"]
    if ds["total_records"] > 0:
        text += f"\n💸 Nợ: {ds['total_customers']} khách — {format_money(ds['total_debt'])}\n"
        if ds["overdue_count"]:
            text += f"   🔴 Quá hạn: {ds['overdue_count']}\n"
    
    # Kho
    if dashboard["low_stock_count"]:
        text += f"\n📦 Hàng sắp hết: {dashboard['low_stock_count']}\n"
    
    # Lương
    if dashboard["pending_payroll"]:
        text += f"\n💰 Bảng lương chờ: {dashboard['pending_payroll']}\n"
    
    keyboard = [
        [InlineKeyboardButton("📊 Doanh thu", callback_data=f"{CB.RPT_DAY}:select"),
         InlineKeyboardButton("⚠️ Nợ", callback_data=f"{CB.DEBT_LIST}:all")],
        [InlineKeyboardButton("📅 Duyệt ca", callback_data=f"{CB.SHIFT_APPROVE}:list"),
         InlineKeyboardButton("📝 Duyệt công", callback_data=f"{CB.ATT_ADJUST_APPROVE}:list")],
        [InlineKeyboardButton("➕ Thêm ca", callback_data="add_shift:start"),
         InlineKeyboardButton("✏️ Sửa giờ ca", callback_data="edit_shift:start")],
        [InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")],
    ]
    
    target = query.edit_message_text if query else update.effective_message.reply_text
    await target(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ==================== BACKUP ====================

async def backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu backup."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💾 Tạo backup ngay", callback_data=f"{CB.ADM_BACKUP}:now")],
        [InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")],
    ]
    await query.edit_message_text(
        "💾 *BACKUP*\n\n"
        "Backup tự động mỗi 6 giờ.\n"
        "Tạo backup thủ công khi cần.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tạo backup."""
    query = update.callback_query
    await query.answer("💾 Đang backup...")
    
    path = backup_database()
    
    if path:
        await query.edit_message_text(f"✅ Đã backup: `{path}`", parse_mode="Markdown")
        # Gửi file backup
        try:
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=open(path, "rb"),
                filename=path.split("/")[-1] if "/" in path else path.split("\\")[-1],
                caption="💾 Backup database"
            )
        except Exception as e:
            logger.error("Lỗi gửi file backup: %s", e)
    else:
        await query.edit_message_text("❌ Lỗi backup.")


# ==================== CẤU HÌNH ====================

async def view_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem cấu hình."""
    query = update.callback_query
    await query.answer()
    
    from bot.config import Config
    from bot.models.database import get_connection
    conn = get_connection()
    
    configs = conn.execute("SELECT * FROM config ORDER BY key").fetchall()
    
    text = "⚙️ *CẤU HÌNH HỆ THỐNG*\n\n"
    text += f"Timezone: {Config.TIMEZONE}\n"
    text += f"Quỹ cơ sở: {format_money(Config.DEFAULT_FUND)}\n"
    text += f"Ngày bắt đầu: {Config.BUSINESS_DAY_START}\n\n"
    
    if configs:
        text += "*Tuỳ chỉnh:*\n"
        for c in configs:
            text += f"• `{c['key']}` = `{c['value']}`\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def cancel_rpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy."""
    context.user_data.pop("rpt_date", None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
    bill_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_input_bill, pattern=f"^{CB.RPT_DAY}:input:")],
        states={
            RPT_BILL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bill_total)],
        },
        fallbacks=[CommandHandler('start', cancel_rpt), CallbackQueryHandler(cancel_rpt, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    return [
        CallbackQueryHandler(select_report_date, pattern=f"^{CB.RPT_DAY}:select$"),
        CallbackQueryHandler(view_report, pattern=f"^{CB.RPT_DAY}:date:"),
        bill_conv,
        CallbackQueryHandler(view_dashboard, pattern=f"^{CB.OWNER_MENU}"),
        CallbackQueryHandler(backup_menu, pattern=f"^{CB.ADM_BACKUP}:menu$"),
        CallbackQueryHandler(do_backup, pattern=f"^{CB.ADM_BACKUP}:now$"),
        CallbackQueryHandler(view_config, pattern=f"^{CB.ADM_CONFIG}:view$"),
    ]
