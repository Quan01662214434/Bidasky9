"""
Handler công nợ: ghi nợ, thu nợ, xem nợ.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_employee
from bot.utils.validators import parse_money, validate_bill_code
from bot.utils.formatters import format_money, format_datetime_vn, format_date_vn
from bot.services.debt_service import (
    create_customer, find_customers, create_debt_record,
    collect_debt_payment, add_debt_photo,
    get_active_debts, get_debt_summary, format_debt_banner,
    get_customer_debts, get_customer_total_debt, get_debt_payments,
)
from bot.services.cash_shift_service import get_open_cash_shift
from bot.models.database import vn_now

logger = logging.getLogger(__name__)

# States
DN_CUSTOMER, DN_BILL, DN_TOTAL, DN_PAID, DN_REMAIN, DN_ITEMS, DN_DUE, DN_PHOTO, DN_CONFIRM = range(40, 49)
DC_CUSTOMER, DC_DEBT, DC_AMOUNT, DC_METHOD, DC_CONFIRM = range(49, 54)


# ==================== DANH SÁCH NỢ ====================

async def show_debt_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách nợ."""
    query = update.callback_query
    if query:
        await query.answer()
    
    summary = get_debt_summary()
    debts = get_active_debts(limit=20)
    
    if summary["total_records"] == 0:
        text = "✅ *Hiện không có khoản nợ chưa thu.*"
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
    else:
        text = "📋 *DANH SÁCH NỢ*\n\n"
        text += f"👥 {summary['total_customers']} khách — Tổng: {format_money(summary['total_debt'])}\n"
        if summary["overdue_count"] > 0:
            text += f"🔴 Quá hạn: {summary['overdue_count']} khoản\n"
        if summary["missing_photo_count"] > 0:
            text += f"📷 Thiếu ảnh: {summary['missing_photo_count']} khoản\n"
        text += "\n"
        
        for d in debts:
            icon = "🔴" if d.get("due_date") and d["due_date"] < vn_now().strftime("%Y-%m-%d") else "🟡"
            photo = " 📷" if d.get("photo_count", 0) == 0 else ""
            due = f" hạn {format_date_vn(d['due_date'])}" if d.get("due_date") else ""
            text += f"{icon} *{d['customer_name']}*: {format_money(d['remaining_debt'])}{due}{photo}\n"
            if d.get("bill_code"):
                text += f"   Bill: {d['bill_code']}\n"
        
        keyboard = [
            [InlineKeyboardButton("📋 Nợ mới", callback_data=f"{CB.DEBT_NEW}:start"),
             InlineKeyboardButton("💵 Thu nợ", callback_data=f"{CB.DEBT_COLLECT}:start")],
            [InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")],
        ]
    
    target = query.edit_message_text if query else update.effective_message.reply_text
    await target(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ==================== GHI NỢ ====================

async def start_new_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu ghi nợ."""
    query = update.callback_query
    await query.answer()
    
    # Hiện danh sách khách quen
    customers = find_customers()
    
    text = "📋 *GHI NỢ MỚI*\n\nChọn khách hoặc nhập tên mới:"
    keyboard = []
    for c in customers[:10]:
        debt = get_customer_total_debt(c["id"])
        label = f"{c['name']}"
        if debt > 0:
            label += f" ({format_money(debt)})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{CB.DEBT_NEW}:cust:{c['id']}")])
    
    keyboard.append([InlineKeyboardButton("➕ Khách mới", callback_data=f"{CB.DEBT_NEW}:new_cust")])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")
    return DN_CUSTOMER


async def select_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn khách có sẵn."""
    query = update.callback_query
    await query.answer()
    
    if ":new_cust" in query.data:
        await query.edit_message_text("Nhập tên khách mới:")
        return DN_CUSTOMER
    
    cust_id = int(query.data.split(":")[2])
    context.user_data["debt_customer_id"] = cust_id
    
    await query.edit_message_text(
        "Nhập mã bill KiotViet (hoặc /skip):"
    )
    return DN_BILL


async def receive_customer_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tên khách mới."""
    name = update.message.text.strip()
    cust_id = create_customer(name, update.effective_user.id)
    context.user_data["debt_customer_id"] = cust_id
    
    await update.message.reply_text(
        f"✅ Khách: {name}\n\nNhập mã bill KiotViet (hoặc /skip):"
    )
    return DN_BILL


async def receive_debt_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận mã bill."""
    text = update.message.text.strip()
    if text == "/skip":
        context.user_data["debt_bill"] = None
    else:
        valid, code = validate_bill_code(text)
        if not valid:
            await update.message.reply_text("❌ Mã bill không hợp lệ. Nhập lại hoặc /skip:")
            return DN_BILL
        context.user_data["debt_bill"] = code
    
    await update.message.reply_text("Nhập *tổng bill*:\n_(VD: 350000, 350k)_", parse_mode="Markdown")
    return DN_TOTAL


async def receive_debt_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tổng bill."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return DN_TOTAL
    
    context.user_data["debt_total"] = amount
    await update.message.reply_text(
        f"Tổng bill: {format_money(amount)}\n\n"
        f"Khách đã trả bao nhiêu (TM+CK)?\n_(Nhập 0 nếu chưa trả gì)_"
    )
    return DN_PAID


async def receive_debt_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số đã trả."""
    paid = parse_money(update.message.text)
    if paid is None:
        paid = 0
    
    total = context.user_data["debt_total"]
    remaining = total - paid
    
    if remaining < 0:
        await update.message.reply_text("❌ Số trả vượt tổng bill. Nhập lại:")
        return DN_PAID
    
    context.user_data["debt_paid"] = paid
    context.user_data["debt_remaining"] = remaining
    
    await update.message.reply_text(
        f"Còn nợ: {format_money(remaining)}\n\n"
        f"Nợ gì? (VD: tiền bàn 3h + 2 Sting + 1 gói thuốc)\n"
        f"_(hoặc /skip nếu đã rõ trên bill)_"
    )
    return DN_ITEMS


async def receive_debt_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận hạng mục nợ."""
    text = update.message.text.strip()
    context.user_data["debt_items"] = None if text == "/skip" else text
    
    await update.message.reply_text(
        "Hạn trả (DD/MM/YYYY hoặc /skip cho 'Chưa hẹn'):"
    )
    return DN_DUE


async def receive_debt_due(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận hạn trả."""
    text = update.message.text.strip()
    if text == "/skip":
        context.user_data["debt_due"] = None
    else:
        from bot.utils.validators import parse_date
        due = parse_date(text)
        if not due:
            await update.message.reply_text("❌ Ngày không hợp lệ. Nhập DD/MM/YYYY hoặc /skip:")
            return DN_DUE
        context.user_data["debt_due"] = due
    
    keyboard = [
        [InlineKeyboardButton("⏭ Bỏ qua (Không có ảnh)", callback_data=f"{CB.DEBT_NEW}:skip_photo")]
    ]
    await update.message.reply_text(
        "📷 Gửi ảnh bill (hoặc bấm Bỏ qua - sẽ bị đánh dấu Thiếu chứng từ):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DN_PHOTO


async def receive_debt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh hoặc skip."""
    query = update.callback_query
    if query:
        await query.answer()
        context.user_data["debt_photo"] = None
    elif update.message.text and update.message.text.strip() == "/skip":
        context.user_data["debt_photo"] = None
    elif update.message.photo:
        photo = update.message.photo[-1]  # Lấy ảnh lớn nhất
        context.user_data["debt_photo"] = {
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
        }
    else:
        await update.message.reply_text("Gửi ảnh hoặc bấm Bỏ qua:")
        return DN_PHOTO
    
    # Preview xác nhận
    d = context.user_data
    text = (
        f"📋 *XÁC NHẬN GHI NỢ*\n\n"
        f"Bill: {d.get('debt_bill') or '—'}\n"
        f"Tổng bill: {format_money(d['debt_total'])}\n"
        f"Đã trả: {format_money(d['debt_paid'])}\n"
        f"Còn nợ: {format_money(d['debt_remaining'])}\n"
        f"Hạng mục: {d.get('debt_items') or '—'}\n"
        f"Hạn trả: {format_date_vn(d.get('debt_due')) or 'Chưa hẹn'}\n"
        f"Ảnh: {'Có' if d.get('debt_photo') else '❌ Thiếu'}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận", callback_data=f"{CB.DEBT_NEW}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    
    # Nếu là callback (bỏ qua ảnh) thì edit message, nếu là message (gửi ảnh) thì reply
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                       parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                         parse_mode="Markdown")
    return DN_CONFIRM


async def confirm_new_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận ghi nợ."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    d = context.user_data
    
    shift = get_open_cash_shift(user_id) or get_open_cash_shift()
    shift_id = shift["id"] if shift else None
    
    try:
        result = create_debt_record(
            customer_id=d["debt_customer_id"],
            cash_shift_id=shift_id,
            total_bill_amount=d["debt_total"],
            remaining_debt=d["debt_remaining"],
            recorded_by=user_id,
            bill_code=d.get("debt_bill"),
            cash_paid=d.get("debt_paid", 0),
            item_note=d.get("debt_items"),
            due_date=d.get("debt_due"),
        )
        
        # Lưu ảnh nếu có
        if d.get("debt_photo"):
            add_debt_photo(
                debt_record_id=result["id"],
                file_id=d["debt_photo"]["file_id"],
                file_unique_id=d["debt_photo"]["file_unique_id"],
                uploaded_by=user_id,
            )
            # Tải và lưu cục bộ
            from bot.utils.photo_storage import save_photo_from_telegram
            await save_photo_from_telegram(
                context.bot,
                d["debt_photo"]["file_id"],
                d["debt_photo"]["file_unique_id"],
                "debt",
                str(result["id"]),
                user_id,
            )
        
        status_text = ""
        if result["is_over_limit"]:
            status_text = "\n⚠️ Vượt quyền cho nợ - chờ chủ xử lý"
        if not d.get("debt_photo"):
            status_text += "\n📷 Thiếu chứng từ"
        
        await query.edit_message_text(
            f"✅ *Đã ghi nợ #{result['id']}*\n\n"
            f"Còn nợ: {format_money(result['remaining_debt'])}{status_text}",
            parse_mode="Markdown"
        )
        
        # Báo chủ
        from bot.services.notification_service import notify_owner
        from bot.services.debt_service import get_customer, sync_pinned_debt_message
        cust = get_customer(d["debt_customer_id"])
        await notify_owner(
            context.bot,
            f"📋 *Nợ mới*\n"
            f"Khách: {cust['name'] if cust else '?'}\n"
            f"Số nợ: {format_money(result['remaining_debt'])}\n"
            f"Bill: {d.get('debt_bill') or '—'}"
            + ("\n⚠️ Vượt quyền" if result["is_over_limit"] else "")
            + ("\n📷 Thiếu ảnh" if not d.get("debt_photo") else ""),
            "new_debt",
            "debt_records", result["id"]
        )
        
        # Cập nhật tin nhắn ghim nợ cho toàn bộ nhân viên
        await sync_pinned_debt_message(context.bot)
        
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    # Clear
    for key in ["debt_customer_id", "debt_bill", "debt_total", "debt_paid",
                "debt_remaining", "debt_items", "debt_due", "debt_photo"]:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END


# ==================== THU NỢ ====================

async def start_collect_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu thu nợ."""
    query = update.callback_query
    await query.answer()
    
    debts = get_active_debts(limit=20)
    if not debts:
        await query.edit_message_text("✅ Không có khoản nợ nào.")
        return ConversationHandler.END
    
    # Nhóm theo khách
    customers = {}
    for d in debts:
        cid = d["customer_id"]
        if cid not in customers:
            customers[cid] = {"name": d["customer_name"], "total": 0, "debts": []}
        customers[cid]["total"] += d["remaining_debt"]
        customers[cid]["debts"].append(d)
    
    text = "💵 *THU NỢ*\n\nChọn khách:"
    keyboard = []
    for cid, info in customers.items():
        keyboard.append([InlineKeyboardButton(
            f"{info['name']} — {format_money(info['total'])}",
            callback_data=f"{CB.DEBT_COLLECT}:cust:{cid}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")
    return DC_CUSTOMER


async def select_debt_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn khách để thu nợ."""
    query = update.callback_query
    await query.answer()
    
    cust_id = int(query.data.split(":")[2])
    context.user_data["collect_customer_id"] = cust_id
    
    debts = get_customer_debts(cust_id)
    active_debts = [d for d in debts if d["status"] in ("active", "partial", "overdue")]
    
    text = f"💵 *Chi tiết nợ*\n\n"
    keyboard = []
    for d in active_debts:
        text += f"• #{d['id']}: {format_money(d['remaining_debt'])}"
        if d.get("bill_code"):
            text += f" (bill {d['bill_code']})"
        text += "\n"
        keyboard.append([InlineKeyboardButton(
            f"Thu #{d['id']} — {format_money(d['remaining_debt'])}",
            callback_data=f"{CB.DEBT_COLLECT}:debt:{d['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Quay lại", callback_data=f"{CB.DEBT_COLLECT}:start")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")
    return DC_DEBT


async def select_debt_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn khoản nợ."""
    query = update.callback_query
    await query.answer()
    
    debt_id = int(query.data.split(":")[2])
    context.user_data["collect_debt_id"] = debt_id
    
    await query.edit_message_text(
        "Nhập số tiền thu:\n_(VD: 150000, 150k)_",
        parse_mode="Markdown"
    )
    return DC_AMOUNT


async def receive_collect_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận số tiền thu."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return DC_AMOUNT
    
    context.user_data["collect_amount"] = amount
    
    keyboard = [
        [InlineKeyboardButton("💵 Tiền mặt", callback_data=f"{CB.DEBT_COLLECT}:method:cash")],
        [InlineKeyboardButton("💳 Chuyển khoản", callback_data=f"{CB.DEBT_COLLECT}:method:transfer")],
    ]
    await update.message.reply_text(
        f"Số tiền: {format_money(amount)}\n\nPhương thức thu:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DC_METHOD


async def select_collect_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn phương thức."""
    query = update.callback_query
    await query.answer()
    
    method = query.data.split(":")[2]
    context.user_data["collect_method"] = method
    
    method_text = "💵 Tiền mặt" if method == "cash" else "💳 Chuyển khoản"
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận", callback_data=f"{CB.DEBT_COLLECT}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await query.edit_message_text(
        f"💵 *XÁC NHẬN THU NỢ*\n\n"
        f"Khoản #{context.user_data['collect_debt_id']}\n"
        f"Số tiền: {format_money(context.user_data['collect_amount'])}\n"
        f"Phương thức: {method_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return DC_CONFIRM


async def confirm_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận thu nợ."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    method = context.user_data["collect_method"]
    
    shift_id = None
    if method == "cash":
        shift = get_open_cash_shift(user_id) or get_open_cash_shift()
        if not shift:
            await query.edit_message_text("⚠️ Thu nợ tiền mặt cần ca quỹ đang mở.")
            return ConversationHandler.END
        shift_id = shift["id"]
    
    try:
        result = collect_debt_payment(
            debt_record_id=context.user_data["collect_debt_id"],
            amount=context.user_data["collect_amount"],
            payment_method=method,
            collected_by=user_id,
            cash_shift_id=shift_id,
        )
        
        status = "✅ Đã trả hết" if result["is_fully_paid"] else f"Còn nợ: {format_money(result['new_remaining'])}"
        
        await query.edit_message_text(
            f"✅ *Đã thu nợ*\n\n"
            f"Số tiền: {format_money(context.user_data['collect_amount'])}\n"
            f"{status}",
            parse_mode="Markdown"
        )
        
        # Đồng bộ lại tin ghim
        from bot.services.debt_service import sync_pinned_debt_message
        await sync_pinned_debt_message(context.bot)
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    # Clear
    for key in ["collect_customer_id", "collect_debt_id", "collect_amount", "collect_method"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


async def cancel_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy thao tác nợ."""
    for key in ["debt_customer_id", "debt_bill", "debt_total", "debt_paid",
                "debt_remaining", "debt_items", "debt_due", "debt_photo",
                "collect_customer_id", "collect_debt_id", "collect_amount", "collect_method"]:
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
        entry_points=[CallbackQueryHandler(start_new_debt, pattern=f"^{CB.DEBT_NEW}:start$")],
        states={
            DN_CUSTOMER: [
                CallbackQueryHandler(select_customer, pattern=f"^{CB.DEBT_NEW}:(cust|new_cust)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_customer_name),
            ],
            DN_BILL: [MessageHandler(filters.TEXT, receive_debt_bill)],
            DN_TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_total)],
            DN_PAID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_paid)],
            DN_ITEMS: [MessageHandler(filters.TEXT, receive_debt_items)],
            DN_DUE: [MessageHandler(filters.TEXT, receive_debt_due)],
            DN_PHOTO: [
                MessageHandler(filters.PHOTO, receive_debt_photo),
                MessageHandler(filters.TEXT, receive_debt_photo),
                CallbackQueryHandler(receive_debt_photo, pattern=f"^{CB.DEBT_NEW}:skip_photo$"),
            ],
            DN_CONFIRM: [CallbackQueryHandler(confirm_new_debt, pattern=f"^{CB.DEBT_NEW}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_debt), CallbackQueryHandler(cancel_debt, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    new_debt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_new_debt, pattern=f"^{CB.DEBT_NEW}:start$")],
        states={
            DN_CUSTOMER: [
                CallbackQueryHandler(select_customer, pattern=f"^{CB.DEBT_NEW}:(cust|new_cust)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_customer_name),
            ],
            DN_BILL: [MessageHandler(filters.TEXT, receive_debt_bill)],
            DN_TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_total)],
            DN_PAID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_debt_paid)],
            DN_ITEMS: [MessageHandler(filters.TEXT, receive_debt_items)],
            DN_DUE: [MessageHandler(filters.TEXT, receive_debt_due)],
            DN_PHOTO: [
                MessageHandler(filters.PHOTO, receive_debt_photo),
                MessageHandler(filters.TEXT, receive_debt_photo),
                CallbackQueryHandler(receive_debt_photo, pattern=f"^{CB.DEBT_NEW}:skip_photo$"),
            ],
            DN_CONFIRM: [CallbackQueryHandler(confirm_new_debt, pattern=f"^{CB.DEBT_NEW}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_debt), CallbackQueryHandler(cancel_debt, pattern=f"^{CB.BACK}:menu$")],
        name="debt_conv_1", persistent=True,
        per_user=True, per_chat=True,
    )
    
        entry_points=[CallbackQueryHandler(start_collect_debt, pattern=f"^{CB.DEBT_COLLECT}:start$")],
        states={
            DC_CUSTOMER: [CallbackQueryHandler(select_debt_customer, pattern=f"^{CB.DEBT_COLLECT}:cust:")],
            DC_DEBT: [CallbackQueryHandler(select_debt_record, pattern=f"^{CB.DEBT_COLLECT}:debt:")],
            DC_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_collect_amount)],
            DC_METHOD: [CallbackQueryHandler(select_collect_method, pattern=f"^{CB.DEBT_COLLECT}:method:")],
            DC_CONFIRM: [CallbackQueryHandler(confirm_collect, pattern=f"^{CB.DEBT_COLLECT}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_debt), CallbackQueryHandler(cancel_debt, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    collect_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_collect_debt, pattern=f"^{CB.DEBT_COLLECT}:start$")],
        states={
            DC_CUSTOMER: [CallbackQueryHandler(select_debt_customer, pattern=f"^{CB.DEBT_COLLECT}:cust:")],
            DC_DEBT: [CallbackQueryHandler(select_debt_record, pattern=f"^{CB.DEBT_COLLECT}:debt:")],
            DC_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_collect_amount)],
            DC_METHOD: [CallbackQueryHandler(select_collect_method, pattern=f"^{CB.DEBT_COLLECT}:method:")],
            DC_CONFIRM: [CallbackQueryHandler(confirm_collect, pattern=f"^{CB.DEBT_COLLECT}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_debt), CallbackQueryHandler(cancel_debt, pattern=f"^{CB.BACK}:menu$")],
        name="debt_conv_2", persistent=True,
        per_user=True, per_chat=True,
    )
    
    return [
        CallbackQueryHandler(show_debt_list, pattern=f"^{CB.DEBT_LIST}:all$"),
        new_debt_conv,
        collect_conv,
    ]
