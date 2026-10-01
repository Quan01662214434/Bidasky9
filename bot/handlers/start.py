"""
Handler /start và menu chính.
Hiển thị banner nợ bắt buộc trước menu.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from bot.constants import CB, Role
from bot.utils.permissions import (
    get_user_role, require_private_chat, is_owner, get_user_display_name
)
from bot.services.debt_service import get_active_debts, get_debt_summary, format_debt_banner

logger = logging.getLogger(__name__)


def _build_employee_menu() -> InlineKeyboardMarkup:
    """Menu nhân viên chia nhóm."""
    keyboard = [
        # Dashboard Hôm nay
        [InlineKeyboardButton("📊 Hôm nay", callback_data=f"{CB.DASHBOARD_TODAY}:view:all:0")],
        
        # Ca làm & Chấm công
        [InlineKeyboardButton("📅 Lịch làm", callback_data=f"{CB.SHIFT_VIEW}:my"),
         InlineKeyboardButton("✍️ Đăng ký ca", callback_data=f"{CB.SHIFT_REG}:start")],
        [InlineKeyboardButton("📊 Công của tôi", callback_data=f"{CB.ATT_MY_LOG}:view"),
         InlineKeyboardButton("📝 Sửa công", callback_data=f"{CB.ATT_ADJUST_REQ}:start")],
        
        # Lương & Đồ đã dùng
        [InlineKeyboardButton("💰 Lương", callback_data=f"{CB.SAL_VIEW}:my"),
         InlineKeyboardButton("💸 Xin ứng", callback_data=f"{CB.SAL_ADVANCE}:start")],
        [InlineKeyboardButton("🍺 Ghi đồ đã dùng", callback_data=f"{CB.CON_LOG}:start"),
         InlineKeyboardButton("📋 Xem đồ", callback_data=f"{CB.CON_VIEW}:my")],
        
        # Tiền & Nợ
        [InlineKeyboardButton("⚠️ Ai đang nợ", callback_data=f"{CB.DEBT_LIST}:all")],
        [InlineKeyboardButton("🏦 Nhận ca quỹ", callback_data=f"{CB.CS_OPEN}:start"),
         InlineKeyboardButton("💳 Chuyển khoản", callback_data=f"{CB.TXN_TRANSFER}:start")],
        [InlineKeyboardButton("📤 Chi", callback_data=f"{CB.TXN_EXPENSE}:start"),
         InlineKeyboardButton("📋 Nợ mới", callback_data=f"{CB.DEBT_NEW}:start")],
        [InlineKeyboardButton("💵 Thu nợ", callback_data=f"{CB.DEBT_COLLECT}:start"),
         InlineKeyboardButton("📊 Kết ca", callback_data=f"{CB.CS_CLOSE}:start")],
        
        # Hàng hóa
        [InlineKeyboardButton("📦 Xem tồn", callback_data=f"{CB.INV_VIEW}:stock"),
         InlineKeyboardButton("📥 Nhập hàng", callback_data=f"{CB.INV_IMPORT}:start")],
        [InlineKeyboardButton("🔍 Kiểm kê", callback_data=f"{CB.INV_CHECK}:start"),
         InlineKeyboardButton("📉 Hao hụt", callback_data=f"{CB.INV_LOSS}:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _build_owner_menu() -> InlineKeyboardMarkup:
    """Menu chủ quán bổ sung."""
    from bot.config import Config
    keyboard = []
    
    if Config.WEBAPP_URL:
        keyboard.append([InlineKeyboardButton("📱 Bảng Điều Khiển (Web App)", web_app=WebAppInfo(url=Config.WEBAPP_URL))])
        
    keyboard.extend([
        [InlineKeyboardButton("📊 Doanh thu theo ngày", callback_data=f"{CB.RPT_DAY}:select")],
        [InlineKeyboardButton("👥 Xem ca nhân viên", callback_data=f"{CB.SHIFT_VIEW}:all"),
         InlineKeyboardButton("✅ Duyệt lịch/công", callback_data=f"{CB.SHIFT_APPROVE}:list")],
        [InlineKeyboardButton("💰 Tính lương", callback_data=f"{CB.SAL_CALC}:start"),
         InlineKeyboardButton("🏷️ Nhập giá đồ", callback_data=f"{CB.CON_PRICE}:start")],
        [InlineKeyboardButton("⚠️ Ai đang nợ", callback_data=f"{CB.DEBT_LIST}:all"),
         InlineKeyboardButton("📉 Âm tiền quán", callback_data=f"{CB.OWNER_MENU}:deficit")],
        [InlineKeyboardButton("📋 Báo cáo", callback_data=f"{CB.RPT_MONTH}:select"),
         InlineKeyboardButton("🔔 Ngoại lệ", callback_data=f"{CB.OWNER_MENU}:exceptions")],
        [InlineKeyboardButton("👤 Nhân viên", callback_data=f"{CB.AUTH_LIST}:manage"),
         InlineKeyboardButton("⚙️ Quy tắc", callback_data=f"{CB.ADM_RULES}:view")],
        [InlineKeyboardButton("🔧 Cấu hình", callback_data=f"{CB.ADM_CONFIG}:view"),
         InlineKeyboardButton("💾 Backup", callback_data=f"{CB.ADM_BACKUP}:menu")],
        
        # Ca quỹ nhanh
        [InlineKeyboardButton("🏦 Nhận ca quỹ", callback_data=f"{CB.CS_OPEN}:start"),
         InlineKeyboardButton("📊 Kết ca", callback_data=f"{CB.CS_CLOSE}:start")],
        
    ])
    return InlineKeyboardMarkup(keyboard)


async def _show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu chính với banner nợ."""
    user_id = update.effective_user.id
    role = get_user_role(user_id)
    
    # Banner nợ bắt buộc
    debt_summary = get_debt_summary()
    debts = get_active_debts(limit=5)
    banner = format_debt_banner(debts, debt_summary)
    
    # Chào
    name = get_user_display_name(user_id) or update.effective_user.first_name
    
    if role == Role.OWNER:
        greeting = f"👋 Xin chào, *{name}* (Chủ quán)"
        menu = _build_owner_menu()
    elif role == Role.EMPLOYEE:
        greeting = f"👋 Xin chào, *{name}*"
        menu = _build_employee_menu()
    else:
        # Người lạ
        await update.effective_message.reply_text(
            f"🔒 Bạn chưa được cấp quyền.\n\n"
            f"Telegram ID của bạn: `{user_id}`\n\n"
            f"Gửi ID này cho chủ quán để được thêm vào hệ thống.",
            parse_mode="Markdown"
        )
        return
    
    text = f"{greeting}\n\n{banner}"
    
    # Gửi message
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=text, reply_markup=menu, parse_mode="Markdown"
            )
        else:
            await update.effective_message.reply_text(
                text=text, reply_markup=menu, parse_mode="Markdown"
            )
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise


@require_private_chat
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý /start."""
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    from bot.utils.permissions import get_user_role
    from bot.constants import Role
    
    role = get_user_role(update.effective_user.id)
    
    if role == Role.OWNER:
        reply_keyboard = [
            [KeyboardButton("🏠 Mở Menu"), KeyboardButton("✅ Check-in"), KeyboardButton("🚪 Check-out")],
            [KeyboardButton("🏦 Nhận ca quỹ"), KeyboardButton("📊 Kết ca"), KeyboardButton("📱 Web App")]
        ]
    else:
        reply_keyboard = [
            [KeyboardButton("🏠 Mở Menu"), KeyboardButton("✅ Check-in"), KeyboardButton("🚪 Check-out")],
            [KeyboardButton("🏦 Nhận ca quỹ"), KeyboardButton("📊 Kết ca")]
        ]
        
    from bot.services.debt_service import get_debt_summary
    debt_summary = get_debt_summary()
    if debt_summary["total_records"] > 0:
        from bot.utils.formatters import format_money
        debt_btn_text = f"⚠️ Khách nợ: {format_money(debt_summary['total_debt'])}"
        reply_keyboard.insert(0, [KeyboardButton(debt_btn_text)])
        
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, is_persistent=True)
    
    if update.message:
        await update.message.reply_text("Bàn phím nhanh đã được bật ở dưới cùng 👇", reply_markup=markup)
        
    await _show_main_menu(update, context)
    
    # Đồng bộ tin nhắn ghim nợ cho người này (nếu gọi /start)
    from bot.services.debt_service import sync_pinned_debt_message
    await sync_pinned_debt_message(context.bot)

async def handle_quick_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🏠 Mở Menu":
        await _show_main_menu(update, context)
    elif text == "✅ Check-in":
        pass  # ConversationHandler trong attendance.py xử lý trực tiếp
    elif text == "🚪 Check-out":
        from bot.services.attendance_service import checkout
        try:
            result = checkout(update.effective_user.id)
            from bot.utils.formatters import format_datetime_vn, format_duration
            msg = f"✅ *ĐÃ RA VỀ*\n\n"
            msg += f"Giờ checkout: {format_datetime_vn(result['checkout_time'])}\n"
            if result.get("approved_minutes"):
                msg += f"Tổng làm: {format_duration(result['approved_minutes'])}\n"
            if result.get("wage_amount"):
                from bot.utils.formatters import format_money
                msg += f"Lương ca: {format_money(result['wage_amount'])}\n"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}")
    elif text == "🏦 Nhận ca quỹ":
        from bot.services.cash_shift_service import open_cash_shift
        try:
            result = open_cash_shift(update.effective_user.id)
            from bot.utils.formatters import format_money
            msg = f"✅ *ĐÃ NHẬN CA QUỸ*\n\n"
            msg += f"Quỹ đầu ca: {format_money(result.get('opening_balance', 0))}\n"
            await update.message.reply_text(msg, parse_mode="Markdown")
        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}")
    elif text == "📊 Kết ca":
        from bot.services.cash_shift_service import get_open_cash_shift
        shift = get_open_cash_shift(update.effective_user.id)
        if not shift:
            await update.message.reply_text("❌ Bạn không có ca quỹ đang mở.")
        else:
            await update.message.reply_text(
                "📊 Để kết ca, vui lòng dùng nút trong *Menu* (bấm 🏠 Mở Menu).",
                parse_mode="Markdown"
            )
    elif text == "📱 Web App":
        from bot.config import Config
        role = get_user_role(update.effective_user.id)
        if role == Role.OWNER:
            webapp_url = Config.WEBAPP_URL or "http://localhost:8000"
            await update.message.reply_text(
                f"📊 Bảng điều khiển BIDA SKY9:\n👉 {webapp_url}\n\n"
                f"_(Lưu ý: Mở bằng máy tính tại quán, hoặc cần cấu hình ngrok/IP tĩnh nếu muốn xem từ xa)_",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("Chỉ chủ quán mới có quyền truy cập Bảng điều khiển.")
    elif text.startswith("⚠️ Khách nợ:"):
        from bot.handlers.debt import show_debt_list
        await show_debt_list(update, context)


async def callback_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback quay về menu chính."""
    await _show_main_menu(update, context)


def get_handlers():
    """Trả về danh sách handlers cho module này."""
    from telegram.ext import MessageHandler, filters
    return [
        CommandHandler("start", cmd_start),
        MessageHandler(filters.Regex("^(🏠 Mở Menu|✅ Check-in|🚪 Check-out|🏦 Nhận ca quỹ|📊 Kết ca|📱 Web App|⚠️ Khách nợ:.*)$"), handle_quick_reply),
        CallbackQueryHandler(callback_main_menu, pattern=f"^{CB.MAIN_MENU}"),
        CallbackQueryHandler(callback_main_menu, pattern=f"^{CB.BACK}:menu$"),
    ]
