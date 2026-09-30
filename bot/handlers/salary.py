"""
Handler lương, ứng lương, đồ dùng NV.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_owner, require_employee, get_user_role
from bot.utils.formatters import format_money, format_duration
from bot.utils.validators import parse_money
from bot.services.salary_service import (
    calculate_payroll, save_payroll, request_advance,
    approve_advance, deliver_advance, lock_payroll, record_salary_payment,
)
from bot.services.consumable_service import (
    get_consumable_items, log_consumable_usage,
    get_employee_consumable_summary, set_consumable_price,
    copy_prices_from_previous, get_all_consumable_summary,
)
from bot.services.cash_shift_service import get_open_cash_shift
from bot.models.database import vn_now
from bot.constants import Role

logger = logging.getLogger(__name__)

ADV_AMOUNT, ADV_REASON, ADV_CONFIRM = range(70, 73)
CON_ITEM, CON_QTY, CON_CONFIRM = range(73, 76)
PRICE_ITEM, PRICE_AMOUNT = range(76, 78)
SAL_EMP, SAL_MONTH = range(78, 80)


# ==================== XEM LƯƠNG ====================

async def view_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem bảng lương."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    
    data = calculate_payroll(user_id, now.year, now.month)
    
    text = f"💰 *LƯƠNG THÁNG {now.month}/{now.year}*\n\n"
    text += f"📊 Số ca: {data['total_sessions']}\n"
    text += f"⏱ Tổng giờ: {format_duration(data['total_minutes'])}\n"
    text += f"💵 Tiền công: {format_money(data['base_wage'])}\n"
    if data['bonus']:
        text += f"🎁 Thưởng/phụ cấp: {format_money(data['bonus'])}\n"
    if data['total_advances']:
        text += f"💸 Ứng lương: -{format_money(data['total_advances'])}\n"
    if data['total_consumables']:
        text += f"🍺 Đồ đã dùng: -{format_money(data['total_consumables'])}\n"
    if data['deductions']:
        text += f"📉 Khấu trừ: -{format_money(data['deductions'])}\n"
    if data['already_paid']:
        text += f"✅ Đã thanh toán: -{format_money(data['already_paid'])}\n"
    
    text += f"\n{'='*25}\n"
    if data['net_pay'] >= 0:
        text += f"💰 *Còn thanh toán: {format_money(data['net_pay'])}*\n"
    else:
        text += f"⚠️ *Nghĩa vụ còn: {format_money(abs(data['net_pay']))}*\n"
    
    if data['has_unresolved']:
        text += f"\n⚠️ _Chưa xử lý:_\n"
        for detail in data['unresolved_details']:
            text += f"  • {detail}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


# ==================== ỨNG LƯƠNG ====================

async def start_advance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu xin ứng."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💸 *XIN ỨNG LƯƠNG*\n\nNhập số tiền muốn ứng:",
        parse_mode="Markdown"
    )
    return ADV_AMOUNT


async def receive_adv_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số tiền ứng."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return ADV_AMOUNT
    
    context.user_data["adv_amount"] = amount
    await update.message.reply_text("Lý do ứng (hoặc /skip):")
    return ADV_REASON


async def receive_adv_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận lý do."""
    reason = update.message.text.strip()
    if reason == "/skip":
        reason = None
    context.user_data["adv_reason"] = reason
    
    keyboard = [
        [InlineKeyboardButton("✅ Gửi yêu cầu", callback_data=f"{CB.SAL_ADVANCE}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(
        f"💸 *XÁC NHẬN*\n\n"
        f"Số tiền: {format_money(context.user_data['adv_amount'])}\n"
        f"Lý do: {reason or '—'}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADV_CONFIRM


async def confirm_advance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gửi yêu cầu ứng."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    adv_id = request_advance(
        user_id,
        context.user_data["adv_amount"],
        context.user_data.get("adv_reason")
    )
    
    await query.edit_message_text(
        f"✅ *Đã gửi yêu cầu ứng #{adv_id}*\nChờ chủ quán duyệt.",
        parse_mode="Markdown"
    )
    
    from bot.services.notification_service import notify_owner
    from bot.utils.permissions import get_user_display_name
    name = get_user_display_name(user_id) or "?"
    await notify_owner(
        context.bot,
        f"💸 *Yêu cầu ứng lương*\n"
        f"NV: {name}\n"
        f"Số tiền: {format_money(context.user_data['adv_amount'])}",
        "advance_request"
    )
    
    for key in ["adv_amount", "adv_reason"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== GHI ĐỒ ĐÃ DÙNG ====================

async def start_log_consumable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu ghi đồ dùng."""
    query = update.callback_query
    await query.answer()
    
    items = get_consumable_items()
    if not items:
        await query.edit_message_text(
            "⚠️ Chưa có danh mục đồ dùng.\nChủ quán cần thêm trước."
        )
        return ConversationHandler.END
    
    keyboard = []
    for item in items:
        label = f"{item['name']} ({item['unit']})"
        keyboard.append([InlineKeyboardButton(
            label, callback_data=f"{CB.CON_LOG}:item:{item['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        "🍺 *GHI ĐỒ ĐÃ DÙNG*\n\nChọn món:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CON_ITEM


async def select_consumable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn món."""
    query = update.callback_query
    await query.answer()
    
    item_id = int(query.data.split(":")[2])
    context.user_data["con_item_id"] = item_id
    
    await query.edit_message_text("Nhập số lượng:")
    return CON_QTY


async def receive_con_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số lượng."""
    from bot.utils.validators import parse_quantity
    qty = parse_quantity(update.message.text)
    if qty is None:
        await update.message.reply_text("❌ Số lượng không hợp lệ. Nhập số nguyên dương:")
        return CON_QTY
    
    context.user_data["con_qty"] = qty
    
    from bot.models.database import get_connection
    item = get_connection().execute(
        "SELECT name, unit FROM consumable_items WHERE id = ?",
        (context.user_data["con_item_id"],)
    ).fetchone()
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận", callback_data=f"{CB.CON_LOG}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(
        f"🍺 Xác nhận: {item['name']} x{qty} {item['unit']}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CON_CONFIRM


async def confirm_consumable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận ghi đồ."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    
    usage_id = log_consumable_usage(
        employee_id=user_id,
        item_id=context.user_data["con_item_id"],
        quantity=context.user_data["con_qty"],
        cash_shift_id=shift["id"] if shift else None,
    )
    
    await query.edit_message_text(f"✅ Đã ghi đồ đã dùng #{usage_id}")
    
    for key in ["con_item_id", "con_qty"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== XEM ĐỒ ĐÃ DÙNG ====================

async def view_consumables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem đồ đã dùng tháng này."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    summary = get_employee_consumable_summary(user_id, now.year, now.month)
    
    text = f"🍺 *ĐỒ ĐÃ DÙNG T{now.month}/{now.year}*\n\n"
    
    if not summary["items"]:
        text += "_Chưa có gì_"
    else:
        for item in summary["items"]:
            price_text = format_money(item["unit_price"]) if item["has_price"] else "Chưa nhập giá"
            total_text = format_money(item["line_total"]) if item["line_total"] is not None else "—"
            text += f"• {item['name']}: {item['chargeable_qty']} {item['unit']} × {price_text} = {total_text}\n"
            if item.get("free_qty", 0) > 0:
                text += f"  _(miễn phí: {item['free_qty']})_\n"
        
        text += f"\n*Tổng: {format_money(summary['total_amount'])}*"
        if summary["has_unpriced"]:
            text += "\n⚠️ _Có món chưa nhập giá_"
    
    keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


# ==================== CHỦ: TÍNH LƯƠNG ====================

async def start_calc_payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chủ bắt đầu tính lương."""
    query = update.callback_query
    await query.answer()
    
    from bot.services.auth_service import get_all_employees
    employees = get_all_employees()
    
    keyboard = []
    for emp in employees:
        keyboard.append([InlineKeyboardButton(
            emp["display_name"],
            callback_data=f"{CB.SAL_CALC}:emp:{emp['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        "💰 *TÍNH LƯƠNG*\n\nChọn nhân viên:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def calc_employee_payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tính lương cho nhân viên."""
    query = update.callback_query
    await query.answer()
    
    emp_id = int(query.data.split(":")[2])
    now = vn_now()
    
    data = calculate_payroll(emp_id, now.year, now.month)
    save_payroll(emp_id, now.year, now.month, data)
    
    from bot.utils.permissions import get_user_display_name
    name = get_user_display_name(emp_id) or "?"
    
    text = f"💰 *BẢNG LƯƠNG: {name}*\n"
    text += f"Kỳ: T{now.month}/{now.year}\n\n"
    text += f"📊 Số ca: {data['total_sessions']}\n"
    text += f"⏱ Tổng giờ: {format_duration(data['total_minutes'])}\n"
    text += f"💵 Tiền công: {format_money(data['base_wage'])}\n"
    if data['bonus']:
        text += f"🎁 Thưởng: +{format_money(data['bonus'])}\n"
    if data['total_advances']:
        text += f"💸 Ứng lương: -{format_money(data['total_advances'])}\n"
    if data['total_consumables']:
        text += f"🍺 Đồ dùng: -{format_money(data['total_consumables'])}\n"
    if data['deductions']:
        text += f"📉 Khấu trừ: -{format_money(data['deductions'])}\n"
    if data['already_paid']:
        text += f"✅ Đã trả: -{format_money(data['already_paid'])}\n"
    
    text += f"\n{'='*25}\n"
    text += f"💰 *Còn thanh toán: {format_money(data['net_pay'])}*\n"
    
    if data['has_unresolved']:
        text += f"\n⚠️ Chưa xử lý:\n"
        for d in data['unresolved_details']:
            text += f"  • {d}\n"
    
    keyboard = [
        [InlineKeyboardButton("🎁 Nhập Thưởng/Khấu trừ", callback_data=f"{CB.SAL_CALC}:bonus:{emp_id}")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data=f"{CB.SAL_CALC}:start")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


# ==================== CHỦ: NHẬP THƯỞNG ====================

SAL_BONUS_AMT = 85

async def start_bonus_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    emp_id = int(query.data.split(":")[2])
    context.user_data["bonus_emp_id"] = emp_id
    
    await query.edit_message_text(
        "🎁 *NHẬP THƯỞNG/KHẤU TRỪ*\n\n"
        "Nhập số tiền thưởng (VND). Nếu là khấu trừ thì nhập số âm (VD: -50000):\n"
        "_(Nhập 0 để xóa thưởng)_",
        parse_mode="Markdown"
    )
    return SAL_BONUS_AMT

async def receive_bonus_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_text = update.message.text.strip()
    is_negative = amount_text.startswith("-")
    if is_negative:
        amount_text = amount_text[1:]
    
    amount = parse_money(amount_text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return SAL_BONUS_AMT
        
    if is_negative:
        amount = -amount
        
    emp_id = context.user_data.get("bonus_emp_id")
    now = vn_now()
    
    from bot.models.database import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE payroll SET bonus = ? WHERE employee_id = ? AND period_year = ? AND period_month = ?",
        (amount, emp_id, now.year, now.month)
    )
    conn.commit()
    
    await update.message.reply_text(
        f"✅ Đã cập nhật thưởng/khấu trừ: {format_money(amount)}.\n"
        f"Vui lòng bấm Tính Lương lại để xem kết quả."
    )
    context.user_data.pop("bonus_emp_id", None)
    return ConversationHandler.END


# ==================== CHỦ: NHẬP GIÁ ĐỒ ====================

async def start_set_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhập giá đồ dùng cuối tháng."""
    query = update.callback_query
    await query.answer()
    
    items = get_consumable_items()
    now = vn_now()
    
    text = f"🏷️ *NHẬP GIÁ ĐỒ DÙNG T{now.month}/{now.year}*\n\n"
    keyboard = []
    
    from bot.models.database import get_connection
    conn = get_connection()
    
    for item in items:
        price = conn.execute(
            "SELECT unit_price FROM consumable_prices WHERE item_id = ? AND period_year = ? AND period_month = ?",
            (item["id"], now.year, now.month)
        ).fetchone()
        
        if price:
            label = f"✅ {item['name']}: {format_money(price['unit_price'])}/{item['unit']}"
        else:
            label = f"❌ {item['name']}: Chưa nhập"
        
        keyboard.append([InlineKeyboardButton(
            label, callback_data=f"{CB.CON_PRICE}:item:{item['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("📋 Sao chép giá tháng trước", callback_data=f"{CB.CON_PRICE}:copy")])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def select_price_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn món để nhập giá."""
    query = update.callback_query
    await query.answer()
    
    if ":copy" in query.data:
        now = vn_now()
        count = copy_prices_from_previous(now.year, now.month, update.effective_user.id)
        await query.edit_message_text(f"✅ Đã sao chép {count} giá từ tháng trước.")
        return
    
    item_id = int(query.data.split(":")[2])
    context.user_data["price_item_id"] = item_id
    
    from bot.models.database import get_connection
    item = get_connection().execute(
        "SELECT name, unit FROM consumable_items WHERE id = ?", (item_id,)
    ).fetchone()
    
    await query.edit_message_text(
        f"Nhập giá cho *{item['name']}* (VND/{item['unit']}):",
        parse_mode="Markdown"
    )
    return PRICE_AMOUNT


async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận giá."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return PRICE_AMOUNT
    
    now = vn_now()
    set_consumable_price(
        context.user_data["price_item_id"],
        now.year, now.month, amount,
        update.effective_user.id
    )
    
    await update.message.reply_text(
        f"✅ Đã nhập giá {format_money(amount)}"
    )
    context.user_data.pop("price_item_id", None)
    return ConversationHandler.END


async def cancel_sal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy."""
    for key in ["adv_amount", "adv_reason", "con_item_id", "con_qty", "price_item_id"]:
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
    advance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_advance, pattern=f"^{CB.SAL_ADVANCE}:start$")],
        states={
            ADV_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adv_amount)],
            ADV_REASON: [MessageHandler(filters.TEXT, receive_adv_reason)],
            ADV_CONFIRM: [CallbackQueryHandler(confirm_advance, pattern=f"^{CB.SAL_ADVANCE}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_sal), CallbackQueryHandler(cancel_sal, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    consumable_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_log_consumable, pattern=f"^{CB.CON_LOG}:start$")],
        states={
            CON_ITEM: [CallbackQueryHandler(select_consumable, pattern=f"^{CB.CON_LOG}:item:")],
            CON_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_con_qty)],
            CON_CONFIRM: [CallbackQueryHandler(confirm_consumable, pattern=f"^{CB.CON_LOG}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_sal), CallbackQueryHandler(cancel_sal, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(select_price_item, pattern=f"^{CB.CON_PRICE}:(item|copy)")],
        states={
            PRICE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
        },
        fallbacks=[CommandHandler('start', cancel_sal), CallbackQueryHandler(cancel_sal, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_bonus_input, pattern=f"^{CB.SAL_CALC}:bonus:")],
        states={
            SAL_BONUS_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bonus_amount)],
        },
        fallbacks=[CommandHandler('start', cancel_sal), CallbackQueryHandler(cancel_sal, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    return [
        CallbackQueryHandler(view_salary, pattern=f"^{CB.SAL_VIEW}:my$"),
        advance_conv,
        bonus_conv,
        consumable_conv,
        CallbackQueryHandler(view_consumables, pattern=f"^{CB.CON_VIEW}:my$"),
        CallbackQueryHandler(start_calc_payroll, pattern=f"^{CB.SAL_CALC}:start$"),
        CallbackQueryHandler(calc_employee_payroll, pattern=f"^{CB.SAL_CALC}:emp:"),
        CallbackQueryHandler(start_set_prices, pattern=f"^{CB.CON_PRICE}:start$"),
        price_conv,
    ]
