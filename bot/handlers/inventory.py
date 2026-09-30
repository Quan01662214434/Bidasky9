"""
Handler kho hàng, nhập hàng, kiểm kê.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_employee
from bot.utils.formatters import format_money
from bot.utils.validators import parse_money, parse_quantity
from bot.services.inventory_service import (
    get_inventory_items, calculate_stock, create_receipt,
    add_receipt_item, confirm_receipt, create_inventory_check,
    add_check_item, get_check_results, get_low_stock_items,
    record_stock_issue,
)
from bot.services.cash_shift_service import get_open_cash_shift
from bot.models.database import vn_now
from bot.constants import StockIssueType

logger = logging.getLogger(__name__)

IMP_ITEM, IMP_QTY, IMP_UNIT, IMP_MORE, IMP_PAY, IMP_CONFIRM = range(80, 86)
CHK_ITEM, CHK_QTY, CHK_MORE = range(86, 89)
LOSS_ITEM, LOSS_QTY, LOSS_REASON = range(89, 92)


# ==================== XEM TỒN KHO ====================

async def view_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem tồn kho."""
    query = update.callback_query
    await query.answer()
    
    items = get_inventory_items()
    
    text = "📦 *TỒN KHO*\n\n"
    if not items:
        text += "_Chưa có mặt hàng nào_"
    else:
        for item in items:
            stock = calculate_stock(item["id"])
            icon = "🔴" if stock["is_low"] else "🟢"
            text += f"{icon} {item['name']}: *{stock['book_stock']}* {item['base_unit']}"
            if stock["is_low"]:
                text += f" _(thấp!)_"
            text += "\n"
    
    # Cảnh báo hàng sắp hết
    low = get_low_stock_items()
    if low:
        text += f"\n⚠️ {len(low)} mặt hàng sắp hết"
    
    keyboard = [
        [InlineKeyboardButton("📥 Nhập hàng", callback_data=f"{CB.INV_IMPORT}:start"),
         InlineKeyboardButton("🔍 Kiểm kê", callback_data=f"{CB.INV_CHECK}:start")],
        [InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


# ==================== NHẬP HÀNG ====================

async def start_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu nhập hàng."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    
    result = create_receipt(
        received_by=user_id,
        received_date=now.strftime("%Y-%m-%d"),
    )
    context.user_data["receipt_id"] = result["id"]
    context.user_data["receipt_items"] = []
    
    items = get_inventory_items()
    keyboard = []
    for item in items:
        keyboard.append([InlineKeyboardButton(
            f"{item['name']} ({item['base_unit']})",
            callback_data=f"{CB.INV_IMPORT}:item:{item['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Hủy", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        f"📥 *NHẬP HÀNG* (Phiếu #{result['code']})\n\nChọn mặt hàng:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return IMP_ITEM


async def select_import_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn mặt hàng nhập."""
    query = update.callback_query
    await query.answer()
    
    item_id = int(query.data.split(":")[2])
    context.user_data["imp_item_id"] = item_id
    
    from bot.models.database import get_connection
    item = get_connection().execute(
        "SELECT * FROM inventory_items WHERE id = ?", (item_id,)
    ).fetchone()
    
    text = f"📥 *{item['name']}*\n\n"
    if item.get("pack_unit") and item.get("pack_size"):
        text += f"Quy cách: 1 {item['pack_unit']} = {item['pack_size']} {item['base_unit']}\n\n"
    text += f"Nhập số lượng (đơn vị: {item['base_unit']}):\n"
    if item.get("pack_unit"):
        text += f"_(VD: nhập 2 {item['pack_unit']} = {2 * (item['pack_size'] or 1)} {item['base_unit']})_"
    
    await query.edit_message_text(text, parse_mode="Markdown")
    return IMP_QTY


async def receive_import_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số lượng nhập."""
    qty = parse_quantity(update.message.text)
    if qty is None:
        await update.message.reply_text("❌ Số lượng không hợp lệ. Nhập lại:")
        return IMP_QTY
    
    item_id = context.user_data["imp_item_id"]
    receipt_id = context.user_data["receipt_id"]
    
    add_receipt_item(receipt_id, item_id, qty)
    context.user_data["receipt_items"].append({"item_id": item_id, "qty": qty})
    
    from bot.models.database import get_connection
    item = get_connection().execute("SELECT name, base_unit FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    
    keyboard = [
        [InlineKeyboardButton("➕ Thêm mặt hàng", callback_data=f"{CB.INV_IMPORT}:more")],
        [InlineKeyboardButton("✅ Xong, xác nhận", callback_data=f"{CB.INV_IMPORT}:done")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(
        f"✅ Đã thêm: {item['name']} x{qty} {item['base_unit']}\n\n"
        f"Tiếp tục?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return IMP_MORE


async def add_more_or_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thêm hoặc hoàn tất."""
    query = update.callback_query
    await query.answer()
    
    if ":more" in query.data:
        items = get_inventory_items()
        keyboard = []
        for item in items:
            keyboard.append([InlineKeyboardButton(
                f"{item['name']}", callback_data=f"{CB.INV_IMPORT}:item:{item['id']}"
            )])
        await query.edit_message_text("Chọn mặt hàng tiếp:", reply_markup=InlineKeyboardMarkup(keyboard))
        return IMP_ITEM
    
    # Xong → chọn thanh toán
    keyboard = [
        [InlineKeyboardButton("💵 Tiền mặt", callback_data=f"{CB.INV_IMPORT}:pay:paid_cash")],
        [InlineKeyboardButton("💳 Chuyển khoản", callback_data=f"{CB.INV_IMPORT}:pay:paid_transfer")],
        [InlineKeyboardButton("👤 Chủ trả", callback_data=f"{CB.INV_IMPORT}:pay:owner_paid")],
        [InlineKeyboardButton("⏳ Chưa trả", callback_data=f"{CB.INV_IMPORT}:pay:unpaid")],
    ]
    await query.edit_message_text(
        "Phương thức thanh toán:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return IMP_PAY


async def select_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn phương thức và xác nhận."""
    query = update.callback_query
    await query.answer()
    
    payment = query.data.split(":")[2]
    
    user_id = update.effective_user.id
    receipt_id = context.user_data["receipt_id"]
    
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    shift_id = shift["id"] if shift and payment == "paid_cash" else None
    
    try:
        result = confirm_receipt(
            receipt_id, user_id,
            payment_status=payment,
            cash_shift_id=shift_id,
        )
        
        text = f"✅ *Đã nhập hàng*\n\n"
        for ri in context.user_data.get("receipt_items", []):
            from bot.models.database import get_connection
            item = get_connection().execute("SELECT name, base_unit FROM inventory_items WHERE id = ?", (ri["item_id"],)).fetchone()
            text += f"• {item['name']}: +{ri['qty']} {item['base_unit']}\n"
        
        payment_labels = {
            "paid_cash": "💵 Tiền mặt (đã giảm quỹ)",
            "paid_transfer": "💳 Chuyển khoản",
            "owner_paid": "👤 Chủ trả ngoài",
            "unpaid": "⏳ Chưa thanh toán",
        }
        text += f"\nThanh toán: {payment_labels.get(payment, payment)}"
        
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                       parse_mode="Markdown")
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    for key in ["receipt_id", "receipt_items", "imp_item_id"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== KIỂM KÊ ====================

async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu kiểm kê."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    
    check_id = create_inventory_check(
        checked_by=user_id,
        check_date=now.strftime("%Y-%m-%d"),
        check_time=now.strftime("%H:%M"),
        cash_shift_id=shift["id"] if shift else None,
    )
    context.user_data["check_id"] = check_id
    
    items = get_inventory_items()
    keyboard = []
    for item in items:
        stock = calculate_stock(item["id"])
        keyboard.append([InlineKeyboardButton(
            f"{item['name']} (sổ: {stock['book_stock']})",
            callback_data=f"{CB.INV_CHECK}:item:{item['id']}"
        )])
    keyboard.append([InlineKeyboardButton("✅ Xong kiểm kê", callback_data=f"{CB.INV_CHECK}:done")])
    
    await query.edit_message_text(
        f"🔍 *KIỂM KÊ*\nMốc: {now.strftime('%H:%M %d/%m/%Y')}\n\nChọn mặt hàng đếm:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CHK_ITEM


async def select_check_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn mặt hàng đếm."""
    query = update.callback_query
    await query.answer()
    
    if ":done" in query.data:
        # Xem kết quả
        check_id = context.user_data["check_id"]
        results = get_check_results(check_id)
        
        if not results:
            await query.edit_message_text("⚠️ Chưa đếm mặt hàng nào.")
        else:
            text = "🔍 *KẾT QUẢ KIỂM KÊ*\n\n"
            has_diff = False
            for r in results:
                diff = r["difference"]
                icon = "✅" if diff == 0 else ("🔴" if diff < 0 else "🟢")
                text += f"{icon} {r['name']}: Sổ {r['book_quantity']} → Thực tế {r['actual_quantity']}"
                if diff != 0:
                    text += f" (lệch {diff:+d})"
                    has_diff = True
                text += f" {r['base_unit']}\n"
            
            if has_diff:
                text += "\n⚠️ _Có chênh lệch — báo chủ xem xét_"
                from bot.services.notification_service import notify_owner
                await notify_owner(
                    context.bot,
                    f"🔍 *Kiểm kê có chênh lệch*\nPhiên #{check_id}",
                    "inventory_check"
                )
            
            keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                           parse_mode="Markdown")
        
        context.user_data.pop("check_id", None)
        return ConversationHandler.END
    
    item_id = int(query.data.split(":")[2])
    context.user_data["chk_item_id"] = item_id
    
    from bot.models.database import get_connection
    item = get_connection().execute("SELECT name, base_unit FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    stock = calculate_stock(item_id)
    
    await query.edit_message_text(
        f"🔍 *{item['name']}*\nTồn sổ: {stock['book_stock']} {item['base_unit']}\n\n"
        f"Nhập số thực tế đếm được:",
        parse_mode="Markdown"
    )
    return CHK_QTY


async def receive_check_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số thực tế."""
    qty = parse_quantity(update.message.text)
    if qty is None:
        # Cho phép 0
        try:
            qty = int(update.message.text.strip())
            if qty < 0:
                await update.message.reply_text("❌ Không chấp nhận số âm. Nhập lại:")
                return CHK_QTY
        except ValueError:
            await update.message.reply_text("❌ Nhập số nguyên:")
            return CHK_QTY
    
    result = add_check_item(
        context.user_data["check_id"],
        context.user_data["chk_item_id"],
        qty,
    )
    
    diff_text = ""
    if result["difference"] != 0:
        diff_text = f" (lệch {result['difference']:+d})"
    
    # Quay lại chọn tiếp
    items = get_inventory_items()
    keyboard = []
    for item in items:
        stock = calculate_stock(item["id"])
        keyboard.append([InlineKeyboardButton(
            f"{item['name']} (sổ: {stock['book_stock']})",
            callback_data=f"{CB.INV_CHECK}:item:{item['id']}"
        )])
    keyboard.append([InlineKeyboardButton("✅ Xong kiểm kê", callback_data=f"{CB.INV_CHECK}:done")])
    
    await update.message.reply_text(
        f"✅ {result['name']}: thực tế {qty}{diff_text}\n\n"
        f"Đếm tiếp hoặc Xong:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHK_ITEM


# ==================== HAO HỤT ====================

async def start_loss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ghi hao hụt."""
    query = update.callback_query
    await query.answer()
    
    items = get_inventory_items()
    keyboard = []
    for item in items:
        keyboard.append([InlineKeyboardButton(
            item["name"], callback_data=f"{CB.INV_LOSS}:item:{item['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        "📉 *GHI HAO HỤT*\n\nChọn mặt hàng:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return LOSS_ITEM


async def select_loss_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn mặt hàng hao hụt."""
    query = update.callback_query
    await query.answer()
    
    item_id = int(query.data.split(":")[2])
    context.user_data["loss_item_id"] = item_id
    await query.edit_message_text("Nhập số lượng hao hụt:")
    return LOSS_QTY


async def receive_loss_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số lượng hao hụt."""
    qty = parse_quantity(update.message.text)
    if qty is None:
        await update.message.reply_text("❌ Nhập số nguyên dương:")
        return LOSS_QTY
    
    context.user_data["loss_qty"] = qty
    await update.message.reply_text("Lý do hao hụt:")
    return LOSS_REASON


async def receive_loss_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận lý do và ghi."""
    reason = update.message.text.strip()
    user_id = update.effective_user.id
    
    record_stock_issue(
        item_id=context.user_data["loss_item_id"],
        quantity=context.user_data["loss_qty"],
        issue_type=StockIssueType.LOSS,
        recorded_by=user_id,
        reason=reason,
    )
    
    await update.message.reply_text(f"✅ Đã ghi hao hụt: {context.user_data['loss_qty']} — {reason}")
    
    for key in ["loss_item_id", "loss_qty"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


async def cancel_inv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy."""
    for key in ["receipt_id", "receipt_items", "imp_item_id", "check_id", "chk_item_id", "loss_item_id", "loss_qty"]:
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
    import_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_import, pattern=f"^{CB.INV_IMPORT}:start$")],
        states={
            IMP_ITEM: [CallbackQueryHandler(select_import_item, pattern=f"^{CB.INV_IMPORT}:item:")],
            IMP_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_import_qty)],
            IMP_MORE: [CallbackQueryHandler(add_more_or_finish, pattern=f"^{CB.INV_IMPORT}:(more|done)$")],
            IMP_PAY: [CallbackQueryHandler(select_payment, pattern=f"^{CB.INV_IMPORT}:pay:")],
        },
        fallbacks=[CommandHandler('start', cancel_inv), CallbackQueryHandler(cancel_inv, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    check_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_check, pattern=f"^{CB.INV_CHECK}:start$")],
        states={
            CHK_ITEM: [CallbackQueryHandler(select_check_item, pattern=f"^{CB.INV_CHECK}:(item|done)")],
            CHK_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_check_qty)],
        },
        fallbacks=[CommandHandler('start', cancel_inv), CallbackQueryHandler(cancel_inv, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    loss_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_loss, pattern=f"^{CB.INV_LOSS}:start$")],
        states={
            LOSS_ITEM: [CallbackQueryHandler(select_loss_item, pattern=f"^{CB.INV_LOSS}:item:")],
            LOSS_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_loss_qty)],
            LOSS_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_loss_reason)],
        },
        fallbacks=[CommandHandler('start', cancel_inv), CallbackQueryHandler(cancel_inv, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    )
    
    return [
        CallbackQueryHandler(view_stock, pattern=f"^{CB.INV_VIEW}:stock$"),
        import_conv, check_conv, loss_conv,
    ]
