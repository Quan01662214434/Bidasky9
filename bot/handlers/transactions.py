"""
Handler giao dịch: chuyển khoản, chi, giao tiền.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB, EXPENSE_CATEGORIES
from bot.utils.permissions import require_employee, require_active_cash_shift
from bot.utils.validators import parse_money, validate_bill_code
from bot.utils.formatters import format_money
from bot.services.transaction_service import (
    record_bank_transfer, record_expense, record_cash_to_owner, record_fund_addition
)
from bot.services.cash_shift_service import get_open_cash_shift

logger = logging.getLogger(__name__)

# States
TXN_BILL, TXN_AMOUNT, TXN_CONFIRM = range(20, 23)
EXP_AMOUNT, EXP_DESC, EXP_CAT, EXP_SOURCE, EXP_CONFIRM = range(23, 28)
CASH_OWNER_AMOUNT, CASH_OWNER_REASON, CASH_OWNER_CONFIRM = range(28, 31)


# ==================== CHUYỂN KHOẢN ====================

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu ghi chuyển khoản."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    if not shift:
        await query.edit_message_text("⚠️ Cần ca quỹ đang mở để ghi chuyển khoản.")
        return ConversationHandler.END
    
    context.user_data["txn_shift_id"] = shift["id"]
    await query.edit_message_text(
        "💳 *GHI CHUYỂN KHOẢN*\n\n"
        "Nhập hoặc dán mã bill KiotViet:",
        parse_mode="Markdown"
    )
    return TXN_BILL


async def receive_txn_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận mã bill."""
    valid, code = validate_bill_code(update.message.text)
    if not valid:
        await update.message.reply_text("❌ Mã bill không hợp lệ. Nhập lại:")
        return TXN_BILL
    
    context.user_data["txn_bill"] = code
    
    # Kiểm tra bill đã có CK chưa
    from bot.services.transaction_service import get_bill_transactions
    existing = get_bill_transactions(code)
    if existing:
        transfers = [t for t in existing if t["type"] == "bank_transfer"]
        if transfers:
            total = sum(t["amount"] for t in transfers)
            await update.message.reply_text(
                f"⚠️ Bill {code} đã có {len(transfers)} khoản CK "
                f"(tổng {format_money(total)}).\n"
                f"Bạn muốn thêm lần trả CK mới?\n\n"
                f"Nhập số tiền chuyển khoản:"
            )
            return TXN_AMOUNT
    
    await update.message.reply_text(
        f"Bill: {code}\n\nNhập số tiền chuyển khoản:\n_(VD: 150000, 150k)_",
        parse_mode="Markdown"
    )
    return TXN_AMOUNT


async def receive_txn_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số tiền CK."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return TXN_AMOUNT
    
    context.user_data["txn_amount"] = amount
    
    text = (
        f"💳 *XÁC NHẬN CHUYỂN KHOẢN*\n\n"
        f"Bill: {context.user_data['txn_bill']}\n"
        f"Số tiền: {format_money(amount)}\n\n"
        f"Đã kiểm tra tiền vào tài khoản?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Đã kiểm tra, xác nhận", callback_data=f"{CB.TXN_TRANSFER}:confirm:verified")],
        [InlineKeyboardButton("⏳ Chưa xác nhận tiền vào", callback_data=f"{CB.TXN_TRANSFER}:confirm:pending")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                     parse_mode="Markdown")
    return TXN_CONFIRM


async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận chuyển khoản."""
    query = update.callback_query
    await query.answer()
    
    verified = "verified" in query.data
    user_id = update.effective_user.id
    
    try:
        result = record_bank_transfer(
            cash_shift_id=context.user_data["txn_shift_id"],
            bill_code=context.user_data["txn_bill"],
            amount=context.user_data["txn_amount"],
            recorded_by=user_id,
            verified=verified,
        )
        
        status = "✅ Đã xác nhận" if verified else "⏳ Chờ xác nhận tiền vào"
        await query.edit_message_text(
            f"✅ *Đã ghi chuyển khoản*\n\n"
            f"Bill: {context.user_data['txn_bill']}\n"
            f"Số tiền: {format_money(context.user_data['txn_amount'])}\n"
            f"Trạng thái: {status}",
            parse_mode="Markdown"
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    for key in ["txn_shift_id", "txn_bill", "txn_amount"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== CHI ====================

async def start_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu ghi chi."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📤 *GHI CHI*\n\nNhập số tiền chi:",
        parse_mode="Markdown"
    )
    return EXP_AMOUNT


async def receive_exp_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số tiền chi."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return EXP_AMOUNT
    
    context.user_data["exp_amount"] = amount
    await update.message.reply_text("Nội dung chi:")
    return EXP_DESC


async def receive_exp_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận nội dung chi."""
    context.user_data["exp_desc"] = update.message.text.strip()
    
    # Chọn nhóm chi
    keyboard = []
    for cat in EXPENSE_CATEGORIES:
        keyboard.append([InlineKeyboardButton(cat, callback_data=f"{CB.TXN_EXPENSE}:cat:{cat}")])
    
    await update.message.reply_text(
        "Chọn nhóm chi:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EXP_CAT


async def receive_exp_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận nhóm chi."""
    query = update.callback_query
    await query.answer()
    
    cat = query.data.split(":")[2]
    context.user_data["exp_cat"] = cat
    
    keyboard = [
        [InlineKeyboardButton("💵 Tiền mặt", callback_data=f"{CB.TXN_EXPENSE}:src:cash")],
        [InlineKeyboardButton("💳 Chuyển khoản", callback_data=f"{CB.TXN_EXPENSE}:src:transfer")],
    ]
    await query.edit_message_text(
        f"Số tiền: {format_money(context.user_data['exp_amount'])}\n"
        f"Nội dung: {context.user_data['exp_desc']}\n"
        f"Nhóm: {cat}\n\n"
        f"Nguồn tiền:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EXP_SOURCE


async def receive_exp_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận nguồn chi và xác nhận."""
    query = update.callback_query
    await query.answer()
    
    is_cash = "cash" in query.data
    context.user_data["exp_is_cash"] = is_cash
    
    source_text = "💵 Tiền mặt" if is_cash else "💳 Chuyển khoản"
    text = (
        f"📤 *XÁC NHẬN CHI*\n\n"
        f"Số tiền: {format_money(context.user_data['exp_amount'])}\n"
        f"Nội dung: {context.user_data['exp_desc']}\n"
        f"Nhóm: {context.user_data['exp_cat']}\n"
        f"Nguồn: {source_text}\n"
    )
    if not is_cash:
        text += "\n_Chi CK không giảm tiền mặt quầy_"
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận", callback_data=f"{CB.TXN_EXPENSE}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")
    return EXP_CONFIRM


async def confirm_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận chi."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    is_cash = context.user_data.get("exp_is_cash", True)
    
    shift_id = None
    if is_cash:
        shift = get_open_cash_shift(user_id) or get_open_cash_shift()
        if not shift:
            await query.edit_message_text("⚠️ Chi tiền mặt cần ca quỹ đang mở.")
            return ConversationHandler.END
        shift_id = shift["id"]
    
    try:
        record_expense(
            cash_shift_id=shift_id,
            amount=context.user_data["exp_amount"],
            description=context.user_data["exp_desc"],
            category=context.user_data["exp_cat"],
            recorded_by=user_id,
            is_cash=is_cash,
        )
        await query.edit_message_text(
            f"✅ *Đã ghi chi*\n\n"
            f"{format_money(context.user_data['exp_amount'])} — {context.user_data['exp_desc']}",
            parse_mode="Markdown"
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    for key in ["exp_amount", "exp_desc", "exp_cat", "exp_is_cash"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== GIAO TIỀN CHỦ ====================

async def start_cash_to_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu giao tiền cho chủ."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🏧 *GIAO TIỀN CHỦ*\n\nNhập số tiền giao:",
        parse_mode="Markdown"
    )
    return CASH_OWNER_AMOUNT


async def receive_cash_owner_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số tiền giao chủ."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return CASH_OWNER_AMOUNT
    
    context.user_data["cash_owner_amount"] = amount
    await update.message.reply_text("Lý do giao tiền (hoặc /skip):")
    return CASH_OWNER_REASON


async def receive_cash_owner_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận lý do và xác nhận."""
    reason = update.message.text.strip()
    if reason == "/skip":
        reason = None
    context.user_data["cash_owner_reason"] = reason
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận", callback_data=f"{CB.TXN_CASH_OWNER}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(
        f"🏧 *XÁC NHẬN GIAO TIỀN*\n\n"
        f"Số tiền: {format_money(context.user_data['cash_owner_amount'])}\n"
        f"Lý do: {reason or '—'}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CASH_OWNER_CONFIRM


async def confirm_cash_to_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận giao tiền."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    if not shift:
        await query.edit_message_text("⚠️ Cần ca quỹ đang mở.")
        return ConversationHandler.END
    
    try:
        record_cash_to_owner(
            cash_shift_id=shift["id"],
            amount=context.user_data["cash_owner_amount"],
            recorded_by=user_id,
            reason=context.user_data.get("cash_owner_reason"),
        )
        await query.edit_message_text(
            f"✅ *Đã ghi giao tiền chủ*\n"
            f"Số tiền: {format_money(context.user_data['cash_owner_amount'])}",
            parse_mode="Markdown"
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    for key in ["cash_owner_amount", "cash_owner_reason"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


async def cancel_txn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy giao dịch."""
    for key in ["txn_shift_id", "txn_bill", "txn_amount",
                "exp_amount", "exp_desc", "exp_cat", "exp_is_cash",
                "cash_owner_amount", "cash_owner_reason"]:
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
        entry_points=[CallbackQueryHandler(start_transfer, pattern=f"^{CB.TXN_TRANSFER}:start$")],
        states={
            TXN_BILL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txn_bill)],
            TXN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txn_amount)],
            TXN_CONFIRM: [CallbackQueryHandler(confirm_transfer, pattern=f"^{CB.TXN_TRANSFER}:confirm")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern=f"^{CB.TXN_TRANSFER}:start$")],
        states={
            TXN_BILL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txn_bill)],
            TXN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txn_amount)],
            TXN_CONFIRM: [CallbackQueryHandler(confirm_transfer, pattern=f"^{CB.TXN_TRANSFER}:confirm")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        name="transactions_conv_1", persistent=True,
        per_user=True, per_chat=True,
    )
    
        entry_points=[CallbackQueryHandler(start_expense, pattern=f"^{CB.TXN_EXPENSE}:start$")],
        states={
            EXP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_exp_amount)],
            EXP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_exp_desc)],
            EXP_CAT: [CallbackQueryHandler(receive_exp_category, pattern=f"^{CB.TXN_EXPENSE}:cat:")],
            EXP_SOURCE: [CallbackQueryHandler(receive_exp_source, pattern=f"^{CB.TXN_EXPENSE}:src:")],
            EXP_CONFIRM: [CallbackQueryHandler(confirm_expense, pattern=f"^{CB.TXN_EXPENSE}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    expense_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_expense, pattern=f"^{CB.TXN_EXPENSE}:start$")],
        states={
            EXP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_exp_amount)],
            EXP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_exp_desc)],
            EXP_CAT: [CallbackQueryHandler(receive_exp_category, pattern=f"^{CB.TXN_EXPENSE}:cat:")],
            EXP_SOURCE: [CallbackQueryHandler(receive_exp_source, pattern=f"^{CB.TXN_EXPENSE}:src:")],
            EXP_CONFIRM: [CallbackQueryHandler(confirm_expense, pattern=f"^{CB.TXN_EXPENSE}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        name="transactions_conv_2", persistent=True,
        per_user=True, per_chat=True,
    )
    
        entry_points=[CallbackQueryHandler(start_cash_to_owner, pattern=f"^{CB.TXN_CASH_OWNER}:start$")],
        states={
            CASH_OWNER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cash_owner_amount)],
            CASH_OWNER_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cash_owner_reason)],
            CASH_OWNER_CONFIRM: [CallbackQueryHandler(confirm_cash_to_owner, pattern=f"^{CB.TXN_CASH_OWNER}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    cash_owner_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_cash_to_owner, pattern=f"^{CB.TXN_CASH_OWNER}:start$")],
        states={
            CASH_OWNER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cash_owner_amount)],
            CASH_OWNER_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cash_owner_reason)],
            CASH_OWNER_CONFIRM: [CallbackQueryHandler(confirm_cash_to_owner, pattern=f"^{CB.TXN_CASH_OWNER}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_txn), CallbackQueryHandler(cancel_txn, pattern=f"^{CB.BACK}:menu$")],
        name="transactions_conv_3", persistent=True,
        per_user=True, per_chat=True,
    )
    
    return [transfer_conv, expense_conv, cash_owner_conv]
