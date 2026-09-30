"""
Handler xác thực và quản lý nhân viên.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, filters, CommandHandler
)

from bot.constants import CB
from bot.utils.permissions import require_private_chat, require_owner
from bot.services.auth_service import (
    add_employee, remove_employee, get_all_employees,
    set_wage_rate, get_current_wage_rate, update_owner_name
)
from bot.utils.formatters import format_money
from bot.utils.validators import parse_money
from bot.models.database import vn_now

logger = logging.getLogger(__name__)

# ConversationHandler states
ADD_NAME, ADD_ID, ADD_PHONE, ADD_WAGE = range(4)


@require_private_chat
@require_owner
async def manage_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Danh sách nhân viên."""
    employees = get_all_employees()
    
    text = "👥 *Quản lý nhân viên*\n\n"
    if employees:
        for i, emp in enumerate(employees, 1):
            rate = get_current_wage_rate(emp["telegram_id"])
            rate_text = format_money(rate) + "/giờ" if rate else "Chưa đặt"
            text += f"{i}. *{emp['display_name']}*\n"
            text += f"   ID: `{emp['telegram_id']}`\n"
            text += f"   Lương: {rate_text}\n\n"
    else:
        text += "_Chưa có nhân viên nào_\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Thêm nhân viên", callback_data=f"{CB.AUTH_APPROVE}:add")],
        [InlineKeyboardButton("🔙 Menu chính", callback_data=f"{CB.BACK}:menu")],
    ]
    
    if employees:
        for emp in employees:
            keyboard.insert(-1, [
                InlineKeyboardButton(
                    f"⚙️ {emp['display_name']}",
                    callback_data=f"{CB.AUTH_LIST}:detail:{emp['telegram_id']}"
                )
            ])
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )


async def start_add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu thêm nhân viên."""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "👤 *Thêm nhân viên mới*\n\n"
        "Nhập Telegram ID của nhân viên:\n"
        "_(Nhân viên có thể gửi /start cho bot để xem ID)_",
        parse_mode="Markdown"
    )
    return ADD_ID


async def receive_employee_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận Telegram ID."""
    try:
        emp_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID phải là số. Nhập lại:")
        return ADD_ID
    
    context.user_data["new_emp_id"] = emp_id
    await update.message.reply_text("Nhập tên hiển thị cho nhân viên:")
    return ADD_NAME


async def receive_employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tên."""
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("❌ Tên quá ngắn. Nhập lại:")
        return ADD_NAME
    
    context.user_data["new_emp_name"] = name
    await update.message.reply_text(
        "Nhập số điện thoại (hoặc gửi /skip để bỏ qua):"
    )
    return ADD_PHONE


async def receive_employee_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận SĐT."""
    phone = None
    if update.message.text.strip() != "/skip":
        phone = update.message.text.strip()
    
    context.user_data["new_emp_phone"] = phone
    await update.message.reply_text(
        "Nhập mức lương/giờ (VND):\n"
        "VD: 25000, 25k, 30.000\n"
        "_(hoặc /skip để dùng mặc định 25.000đ)_"
    )
    return ADD_WAGE


async def receive_employee_wage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận mức lương và hoàn tất."""
    user_id = update.effective_user.id
    emp_id = context.user_data.get("new_emp_id")
    emp_name = context.user_data.get("new_emp_name")
    emp_phone = context.user_data.get("new_emp_phone")
    
    wage = 25000
    if update.message.text.strip() != "/skip":
        wage = parse_money(update.message.text)
        if wage is None:
            await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
            return ADD_WAGE
    
    # Thêm nhân viên
    success = add_employee(emp_id, emp_name, emp_phone, user_id)
    
    if not success:
        await update.message.reply_text(
            f"⚠️ Nhân viên ID `{emp_id}` đã tồn tại.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    # Đặt lương
    if wage:
        today = vn_now().strftime("%Y-%m-%d")
        set_wage_rate(emp_id, wage, today, user_id)
    
    result = f"✅ *Đã thêm nhân viên*\n\n"
    result += f"Tên: {emp_name}\n"
    result += f"ID: `{emp_id}`\n"
    if emp_phone:
        result += f"SĐT: {emp_phone}\n"
    if wage:
        result += f"Lương: {format_money(wage)}/giờ\n"
    result += f"\nNhân viên cần /start bot để bắt đầu."
    
    keyboard = [[InlineKeyboardButton("🔙 Quản lý NV", callback_data=f"{CB.AUTH_LIST}:manage")]]
    await update.message.reply_text(
        result, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    
    # Clear data
    context.user_data.pop("new_emp_id", None)
    context.user_data.pop("new_emp_name", None)
    context.user_data.pop("new_emp_phone", None)
    
    return ConversationHandler.END


async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy thêm nhân viên."""
    context.user_data.pop("new_emp_id", None)
    context.user_data.pop("new_emp_name", None)
    context.user_data.pop("new_emp_phone", None)
    
    await update.message.reply_text("❌ Đã hủy.")
    return ConversationHandler.END


async def employee_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chi tiết nhân viên."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    emp_id = int(parts[2])
    
    from bot.services.auth_service import get_employee
    emp = get_employee(emp_id)
    if not emp:
        await query.edit_message_text("❌ Không tìm thấy nhân viên.")
        return
    
    rate = get_current_wage_rate(emp_id)
    
    text = f"👤 *{emp['display_name']}*\n\n"
    text += f"ID: `{emp_id}`\n"
    text += f"SĐT: {emp.get('phone') or '—'}\n"
    text += f"Lương: {format_money(rate) + '/giờ' if rate else 'Chưa đặt'}\n"
    
    keyboard = [
        [InlineKeyboardButton("💰 Đặt lương", callback_data=f"{CB.AUTH_LIST}:wage:{emp_id}")],
        [InlineKeyboardButton("🚫 Thu hồi quyền", callback_data=f"{CB.AUTH_REVOKE}:{emp_id}")],
        [InlineKeyboardButton("🔙 Danh sách", callback_data=f"{CB.AUTH_LIST}:manage")],
    ]
    
    await query.edit_message_text(
        text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def revoke_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thu hồi quyền nhân viên."""
    query = update.callback_query
    await query.answer()
    
    emp_id = int(query.data.split(":")[1])
    success = remove_employee(emp_id, update.effective_user.id)
    
    if success:
        await query.edit_message_text("✅ Đã thu hồi quyền nhân viên.")
    else:
        await query.edit_message_text("❌ Không tìm thấy nhân viên.")


def get_handlers():
    """Trả về handlers."""
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_employee, pattern=f"^{CB.AUTH_APPROVE}:add$")
        ],
        states={
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_employee_id)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_employee_name)],
            ADD_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_employee_phone),
                CommandHandler("skip", receive_employee_phone),
            ],
            ADD_WAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_employee_wage),
                CommandHandler("skip", receive_employee_wage),
            ],
        },
        fallbacks=[CommandHandler("start", cancel_add), CommandHandler("cancel", cancel_add)],
        per_user=True,
        per_chat=True,
    )
    
    return [
        conv_handler,
        CallbackQueryHandler(manage_employees, pattern=f"^{CB.AUTH_LIST}:manage$"),
        CallbackQueryHandler(employee_detail, pattern=f"^{CB.AUTH_LIST}:detail:"),
        CallbackQueryHandler(revoke_employee, pattern=f"^{CB.AUTH_REVOKE}:"),
    ]
