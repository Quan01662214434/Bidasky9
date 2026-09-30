"""
Handler ca quỹ: nhận ca, kết ca, bàn giao.
"""

import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_private_chat, require_employee
from bot.utils.validators import parse_money
from bot.utils.formatters import format_money, format_datetime_vn
from bot.services.cash_shift_service import (
    open_cash_shift, close_cash_shift, get_open_cash_shift,
    get_last_closed_shift, calculate_expected_closing,
    get_shift_transactions, acknowledge_debt_handover,
    get_any_open_cash_shift,
)
from bot.services.debt_service import get_active_debts, get_debt_summary, format_debt_banner
from bot.models.database import vn_now

logger = logging.getLogger(__name__)

# States
CS_OPEN_CASH, CS_OPEN_CONFIRM = range(10, 12)
CS_CLOSE_BILL, CS_CLOSE_CASH, CS_CLOSE_EXTRA_EXPENSE_AMT, CS_CLOSE_EXTRA_EXPENSE_NOTE, CS_CLOSE_NOTE, CS_CLOSE_CONFIRM = range(12, 18)
CS_CLOSE_OLD_BILL, CS_CLOSE_OLD_TRANSFER, CS_CLOSE_NEW_BILL, CS_CLOSE_NEW_TRANSFER = range(31, 35)


# ==================== NHẬN CA QUỸ ====================

async def start_open_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu nhận ca quỹ."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    # Kiểm tra ca đang mở
    existing = get_any_open_cash_shift()
    if existing:
        from bot.utils.permissions import get_user_display_name
        name = get_user_display_name(existing["employee_id"]) or "?"
        await query.edit_message_text(
            f"⚠️ Đã có ca quỹ đang mở\n"
            f"Người phụ trách: {name}\n"
            f"Mở lúc: {format_datetime_vn(existing['opened_at'])}\n\n"
            f"Cần kết ca trước khi mở ca mới."
        )
        return ConversationHandler.END
    
    # Hiện thông tin ca trước
    last_shift = get_last_closed_shift()
    text = "🏦 *NHẬN CA QUỸ*\n\n"
    
    if last_shift:
        text += f"Ca trước: {format_datetime_vn(last_shift['closed_at'])}\n"
        if last_shift["closing_cash_counted"] is not None:
            text += f"Tiền bàn giao: {format_money(last_shift['closing_cash_counted'])}\n"
        if last_shift["closing_diff"] and last_shift["closing_diff"] != 0:
            text += f"Chênh lệch: {format_money(last_shift['closing_diff'])}\n"
        if last_shift.get("active_tables_note"):
            text += f"Bàn đang chơi: {last_shift['active_tables_note']}\n"
        text += "\n"
        context.user_data["prev_shift_id"] = last_shift["id"]
        context.user_data["expected_opening"] = last_shift["closing_cash_counted"]
    
    # Banner nợ BẮT BUỘC
    debt_summary = get_debt_summary()
    debts = get_active_debts(limit=10)
    banner = format_debt_banner(debts, debt_summary)
    text += f"\n{banner}\n\n"
    
    # Yêu cầu xem nợ
    if debt_summary["total_records"] > 0:
        text += "📋 Bạn *phải* xem và xác nhận danh sách nợ bàn giao.\n\n"
        keyboard = [
            [InlineKeyboardButton("✅ Đã xem nợ bàn giao", callback_data=f"{CB.DEBT_ACK}:ack")],
            [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
        ]
        # Lưu snapshot nợ
        context.user_data["debt_snapshot"] = json.dumps({
            "summary": debt_summary,
            "debts": [{"id": d["id"], "customer": d["customer_name"], "amount": d["remaining_debt"]} 
                      for d in debts],
            "timestamp": vn_now().isoformat()
        }, ensure_ascii=False)
    else:
        text += "Nhập số tiền mặt kiểm đếm đầu ca:\n_(VD: 200000, 200k)_"
        keyboard = [[InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")]]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), 
                                       parse_mode="Markdown")
        return CS_OPEN_CASH
    
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")
    return CS_OPEN_CASH


async def ack_debt_handover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận đã xem nợ bàn giao."""
    query = update.callback_query
    await query.answer("✅ Đã xem")
    
    await query.edit_message_text(
        "✅ Đã xác nhận xem nợ.\n\n"
        "Nhập số tiền mặt kiểm đếm đầu ca:\n"
        "_(VD: 200000, 200k)_",
        parse_mode="Markdown"
    )
    return CS_OPEN_CASH


async def receive_opening_cash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tiền mặt đầu ca."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return CS_OPEN_CASH
    
    context.user_data["opening_cash"] = amount
    expected = context.user_data.get("expected_opening")
    
    text = f"💵 Tiền kiểm đếm: {format_money(amount)}\n"
    if expected is not None:
        diff = amount - expected
        text += f"Tiền bàn giao: {format_money(expected)}\n"
        text += f"Chênh lệch: {format_money(diff)}\n"
    
    text += "\nXác nhận nhận ca quỹ?"
    
    keyboard = []
    if expected is not None and diff < -50000: # Nếu hụt hơn 50k
        keyboard.append([InlineKeyboardButton("⚠️ Báo: Chủ đã lấy tiền quỹ qua đêm", callback_data=f"{CB.CS_OPEN}:owner_took")])
        
    keyboard.append([InlineKeyboardButton("✅ Xác nhận mở ca bình thường", callback_data=f"{CB.CS_OPEN}:confirm")])
    keyboard.append([InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return CS_OPEN_CONFIRM

async def confirm_open_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận mở ca quỹ."""
    query = update.callback_query
    await query.answer()
    
    action = query.data.split(":")[1] # confirm hoặc owner_took
    user_id = update.effective_user.id
    today = vn_now().strftime("%Y-%m-%d")
    
    opening_cash = context.user_data["opening_cash"]
    expected_opening = context.user_data.get("expected_opening")
    note = ""
    
    if action == "owner_took" and expected_opening is not None:
        diff = expected_opening - opening_cash
        note = f"Chủ lấy tiền qua đêm: {format_money(diff)}"
        # Chỉnh lại kỳ vọng để ca làm việc trong sạch từ đầu
        expected_opening = opening_cash

    
    try:
        result = open_cash_shift(
            employee_id=user_id,
            opening_cash=opening_cash,
            shift_date=today,
            previous_shift_id=context.user_data.get("prev_shift_id"),
            expected_opening=expected_opening,
            opening_note=note
        )
        
        # Lưu debt ack nếu có
        debt_snapshot = context.user_data.get("debt_snapshot")
        if debt_snapshot:
            acknowledge_debt_handover(result["id"], user_id, debt_snapshot)
        
        await query.edit_message_text(
            f"✅ *Đã nhận ca quỹ*\n\n"
            f"Mã ca: #{result['id']}\n"
            f"Tiền đầu ca: {format_money(result['opening_cash'])}\n"
            f"Thời gian: {vn_now().strftime('%H:%M %d/%m/%Y')}",
            parse_mode="Markdown"
        )
        
        # Báo chủ
        from bot.services.notification_service import notify_owner
        from bot.utils.permissions import get_user_display_name
        name = get_user_display_name(user_id) or "?"
        await notify_owner(
            context.bot,
            f"🏦 *Nhận ca quỹ*\n"
            f"NV: {name}\n"
            f"Tiền đầu ca: {format_money(result['opening_cash'])}",
            "cash_shift_open"
        )
        
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    # Clear
    for key in ["opening_cash", "prev_shift_id", "expected_opening", "debt_snapshot"]:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END


# ==================== KẾT CA ====================

async def start_close_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu kết ca quỹ."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    shift = get_open_cash_shift(user_id)
    
    if not shift:
        # Thử tìm ca mở bất kỳ (cho chủ)
        shift = get_any_open_cash_shift()
        if not shift:
            await query.edit_message_text("⚠️ Không có ca quỹ đang mở.")
            return ConversationHandler.END
    
    context.user_data["closing_shift_id"] = shift["id"]
    
    # Hiện giao dịch ca
    txns = get_shift_transactions(shift["id"])
    from bot.constants import TransactionType
    expense_txns = [t for t in txns if t["type"] in (TransactionType.EXPENSE_CASH.value, TransactionType.EXPENSE_TRANSFER.value)]
    total_expense = sum(t["amount"] for t in expense_txns)
    
    text = f"📊 *KẾT CA QUỸ* (Ca #{shift['id']})\n\n"
    text += f"Mở lúc: {format_datetime_vn(shift['opened_at'])}\n"
    text += f"Tiền đầu ca: {format_money(shift['opening_cash'])}\n"
    text += f"Giao dịch: {len(txns)} khoản\n"
    text += f"Đã ghi nhận CHI: {format_money(total_expense)}\n\n"
    if expense_txns:
        text += "_Chi tiết:_\n"
        for t in expense_txns:
            text += f"- {format_money(t['amount'])} ({t.get('description', 'Không rõ')})\n"
        text += "\n"
    import datetime
    opened_dt = datetime.datetime.fromisoformat(shift['opened_at'].replace("Z", "+00:00"))
    import pytz
    opened_dt = opened_dt.astimezone(pytz.timezone('Asia/Ho_Chi_Minh'))
    today_dt = vn_now()
    
    crosses_midnight = opened_dt.date() < today_dt.date()
    
    if crosses_midnight:
        text += "Nhập *doanh thu bill ngày CŨ* từ KiotViet (trước 12h đêm):\n_(VD: 3000000)_"
        context.user_data["crosses_midnight"] = True
        await query.edit_message_text(text=text, parse_mode="Markdown")
        return CS_CLOSE_OLD_BILL
    else:
        text += "Nhập *tổng doanh thu bill* của ca từ KiotViet:\n_(VD: 2000000, 2tr)_"
        await query.edit_message_text(text=text, parse_mode="Markdown")
        return CS_CLOSE_BILL

async def receive_bill_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tổng bill (Ca không qua đêm)."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return CS_CLOSE_BILL
    
    context.user_data["total_bill"] = amount
    await update.message.reply_text(
        f"💰 Tổng bill: {format_money(amount)}\n\n"
        "Nhập *tiền mặt kiểm đếm* cuối ca:\n_(Ví dụ: 1500000)_",
        parse_mode="Markdown"
    )
    return CS_CLOSE_CASH

async def receive_old_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Nhập sai. Vui lòng nhập lại số tiền bill Ngày Cũ:")
        return CS_CLOSE_OLD_BILL
    context.user_data["old_bill"] = amount
    
    await update.message.reply_text("Nhập *doanh thu bill ngày MỚI* từ KiotViet (sau 12h đêm):", parse_mode="Markdown")
    return CS_CLOSE_NEW_BILL

async def receive_new_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Nhập sai. Nhập lại số tiền bill Ngày Mới:")
        return CS_CLOSE_NEW_BILL
    context.user_data["new_bill"] = amount
    
    old_bill = context.user_data["old_bill"]
    new_bill = context.user_data["new_bill"]
    
    # Tính tổng để gửi xuống hàm xử lý
    context.user_data["total_bill"] = old_bill + new_bill
    
    # Sinh note ghi chú lại chi tiết gộp
    context.user_data["split_note"] = f"Ngày cũ (Trước 0h): Bill {format_money(old_bill)}\nNgày mới (Sau 0h): Bill {format_money(new_bill)}"
    
    await update.message.reply_text(
        f"✅ Đã ghi nhận tổng Bill Ca 3 là: {format_money(old_bill + new_bill)}\n"
        "(Giao dịch Chuyển khoản trong ca Bot đã tự động lưu và trừ ra khỏi két)\n\n"
        "Nhập *tiền mặt kiểm đếm* cuối ca trong két sắt hiện tại:\n_(Ví dụ: 1500000)_",
        parse_mode="Markdown"
    )
    return CS_CLOSE_CASH


async def receive_closing_cash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tiền mặt cuối ca."""
    amount = parse_money(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại:")
        return CS_CLOSE_CASH
    
    context.user_data["closing_cash"] = amount
    await update.message.reply_text(
        "Bạn có khoản CHI NÀO QUÊN chưa nhập vào bot không?\n"
        "Nhập SỐ TIỀN MẶT ĐÃ CHI thêm (VD: 50000, 50k)\n"
        "Hoặc gõ 0 (hoặc /skip) nếu không có:"
    )
    return CS_CLOSE_EXTRA_EXPENSE_AMT


async def receive_extra_expense_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận tiền chi thêm."""
    text = update.message.text.strip()
    if text == "/skip":
        amount = 0
    else:
        amount = parse_money(text)
        if amount is None:
            await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập lại hoặc 0:")
            return CS_CLOSE_EXTRA_EXPENSE_AMT
    
    if amount > 0:
        context.user_data["extra_expense"] = amount
        await update.message.reply_text("Nhập lý do khoản chi quên chưa nhập (VD: Mua đá, mua khăn):")
        return CS_CLOSE_EXTRA_EXPENSE_NOTE
    else:
        context.user_data["extra_expense"] = 0
        context.user_data["extra_expense_note"] = None
        await update.message.reply_text("Ghi chú bàn đang chơi (hoặc /skip):")
        return CS_CLOSE_NOTE


async def receive_extra_expense_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận lý do chi thêm."""
    note = update.message.text.strip()
    context.user_data["extra_expense_note"] = note
    await update.message.reply_text("Ghi chú bàn đang chơi (hoặc /skip):")
    return CS_CLOSE_NOTE


async def receive_closing_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ghi chú."""
    note = update.message.text.strip()
    if note == "/skip":
        note = None
    context.user_data["closing_note"] = note
    
    shift_id = context.user_data["closing_shift_id"]
    total_bill = context.user_data["total_bill"]
    closing_cash = context.user_data["closing_cash"]
    extra_expense = context.user_data.get("extra_expense", 0)
    extra_expense_note = context.user_data.get("extra_expense_note")
    
    # Preview kết ca
    now = vn_now()
    text = f"📊 *XÁC NHẬN KẾT CA*\n\n"
    text += f"Tổng bill: {format_money(total_bill)}\n"
    if extra_expense > 0:
        text += f"Chi quên nhập (vừa thêm): {format_money(extra_expense)} ({extra_expense_note})\n"
    text += f"Tiền kiểm đếm: {format_money(closing_cash)}\n"
    if note:
        text += f"Bàn đang chơi: {note}\n"
    text += "\nXác nhận kết ca?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận kết ca", callback_data=f"{CB.CS_CLOSE}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                     parse_mode="Markdown")
    return CS_CLOSE_CONFIRM


async def confirm_close_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận kết ca."""
    query = update.callback_query
    await query.answer()
    
    shift_id = context.user_data.get("closing_shift_id")
    if not shift_id:
        await query.edit_message_text("❌ Lỗi: không tìm thấy ca.")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    now = vn_now()
    
    extra_expense = context.user_data.get("extra_expense", 0)
    extra_expense_note = context.user_data.get("extra_expense_note")
    
    try:
        # Nếu có chi thêm lúc đóng ca
        if extra_expense > 0:
            from bot.services.transaction_service import record_expense
            record_expense(
                cash_shift_id=shift_id,
                amount=extra_expense,
                description="Chi thêm lúc kết ca",
                category="other",
                recorded_by=user_id,
                is_cash=True,
                note=extra_expense_note
            )
            
        # Ghi chú tổng hợp
        split_note = context.user_data.get("split_note", "")
        final_note = context.user_data.get("closing_note") or ""
        if split_note:
            final_note = f"{split_note}\n{final_note}".strip()
            
        from bot.services.cash_shift_service import create_draft_handover
        
        draft = create_draft_handover(
            shift_id=shift_id,
            total_bill_revenue=context.user_data["total_bill"],
            closing_cash_counted=context.user_data["closing_cash"],
            closing_note=final_note,
            active_tables_note=context.user_data.get("closing_note"),
            drafted_by=user_id,
        )
        
        details = draft["calc_details"]
        diff = draft["diff"]
        
        # Tìm xem có nhân viên nào đang chờ nhận bàn giao (đã check-in ảnh) không
        from bot.models.database import get_connection
        conn = get_connection()
        next_session = conn.execute(
            """SELECT * FROM attendance_sessions 
               WHERE status = 'pending_handover' 
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        
        if next_session:
            # Có người đang đợi nhận ca
            next_employee_id = next_session["employee_id"]
            
            # Gửi tin nhắn cho người nhận
            handover_msg = (
                f"🤝 *YÊU CẦU NHẬN BÀN GIAO CA QUỸ*\n\n"
                f"Nhân viên ca trước đã kết ca và tạo phiếu bàn giao.\n"
                f"💵 Tiền mặt thực tế bạn sẽ nhận: *{format_money(draft['closing_cash_counted'])}*\n\n"
                f"⚠️ Vui lòng ĐẾM TIỀN MẶT trong két. Nếu khớp với số trên, bấm XÁC NHẬN để ca trước ra về."
            )
            keyboard = [[InlineKeyboardButton("✅ Khớp tiền — Tôi xác nhận", callback_data=f"verify_handover:{shift_id}")]]
            
            try:
                await context.bot.send_message(
                    chat_id=next_employee_id,
                    text=handover_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Lỗi gửi tin nhắn bàn giao: {e}")
            
            text = (
                f"⏳ *ĐÃ TẠO PHIẾU BÀN GIAO NHÁP*\n\n"
                f"Bạn báo cho nhân viên ca sau mở Telegram để bấm nút *Xác nhận nhận đủ tiền*.\n\n"
                f"Sau khi họ xác nhận, bạn sẽ có thể chốt ca và Ra về."
            )
            keyboard = [[InlineKeyboardButton("🔄 Làm mới trạng thái", callback_data=f"check_handover_status:{shift_id}")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            
        else:
            # Không có ai nhận ca sau (ca cuối ngày hoặc ngoại lệ)
            text = (
                f"⚠️ *KHÔNG TÌM THẤY NGƯỜI NHẬN CA SAU*\n\n"
                f"Hệ thống không thấy nhân viên nào đã check-in chờ nhận ca.\n"
                f"Đây là ca cuối ngày hoặc nhân viên ca sau quên check-in?\n\n"
                f"Bạn có thể CHỐT CA NGAY (không cần người xác nhận chéo)."
            )
            keyboard = [
                [InlineKeyboardButton("✅ Chốt ca & Ra về (Ngoại lệ)", callback_data=f"finalize_handover:{shift_id}")],
                [InlineKeyboardButton("🔙 Hủy (Chờ ca sau check-in)", callback_data=f"{CB.BACK}:menu")]
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            
    except Exception as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    # Clear
    for key in ["closing_shift_id", "total_bill", "closing_cash", "closing_note", "extra_expense", "extra_expense_note"]:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END


async def verify_handover_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhân viên ca sau bấm xác nhận khớp tiền."""
    query = update.callback_query
    await query.answer()
    
    shift_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    
    from bot.services.cash_shift_service import verify_handover
    try:
        verify_handover(shift_id, user_id)
        
        await query.edit_message_text(
            "✅ *ĐÃ XÁC NHẬN NHẬN BÀN GIAO*\n\n"
            "Hãy báo cho nhân viên ca trước bấm nút **Chốt ca & Ra về** để hoàn tất nhé!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.edit_message_text(f"❌ {str(e)}")

async def check_handover_status_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ca trước kiểm tra xem ca sau đã xác nhận chưa."""
    query = update.callback_query
    shift_id = int(query.data.split(":")[1])
    
    from bot.models.database import get_connection
    conn = get_connection()
    shift = conn.execute("SELECT handover_status FROM cash_shifts WHERE id = ?", (shift_id,)).fetchone()
    
    if shift and shift["handover_status"] == 'verified':
        await query.answer("✅ Ca sau đã xác nhận!")
        text = "✅ *CA SAU ĐÃ XÁC NHẬN*\n\nSố tiền và hàng hóa bàn giao đã được nhân viên ca sau kiểm tra và xác nhận khớp.\n\nBấm nút dưới đây để CHỐT CA."
        keyboard = [[InlineKeyboardButton("✅ Chốt ca & Ra về", callback_data=f"finalize_handover:{shift_id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await query.answer("⏳ Ca sau vẫn chưa xác nhận, vui lòng chờ...", show_alert=True)

async def finalize_handover_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hoàn tất chốt sổ."""
    query = update.callback_query
    await query.answer()
    
    shift_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    
    from bot.services.cash_shift_service import finalize_handover_and_checkout
    from bot.utils.formatting import format_money
    
    try:
        result = finalize_handover_and_checkout(shift_id, user_id)
        
        text = f"🚪 *ĐÃ CHỐT CA VÀ RA VỀ (Mốc T)*\n\n"
        text += f"Ca quỹ #{shift_id} đã được đóng.\n"
        text += f"Phiên làm việc của bạn đã tự động Check-out.\n\n"
        
        diff = result["diff"]
        if diff == 0:
            text += f"Trạng thái: ✅ *Khớp quỹ*"
        elif diff > 0:
            text += f"Trạng thái: 🟢 *Dư {format_money(diff)}*"
        else:
            text += f"Trạng thái: 🔴 *Thiếu {format_money(abs(diff))}*"
            
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        # Báo chủ
        from bot.services.notification_service import notify_owner
        from bot.utils.permissions import get_user_display_name
        name = get_user_display_name(user_id) or "?"
        await notify_owner(
            context.bot,
            f"🤝 *Bàn Giao Hoàn Tất #{shift_id}*\n"
            f"NV bàn giao: {name}\n"
            f"Trạng thái: {'Khớp' if diff == 0 else ('Dư' if diff > 0 else 'Thiếu')}\n"
            f"Giờ chốt: {result['closed_at']}",
            "cash_shift_close",
            "cash_shifts", shift_id
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ Lỗi: {str(e)}")


async def cancel_shift_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy thao tác ca."""
    for key in ["opening_cash", "prev_shift_id", "expected_opening", "debt_snapshot",
                "closing_shift_id", "total_bill", "closing_cash", "closing_note",
                "extra_expense", "extra_expense_note"]:
        context.user_data.pop(key, None)
    
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
    open_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_open_shift, pattern=f"^{CB.CS_OPEN}:start$"),
        ],
        states={
            CS_OPEN_CASH: [
                CallbackQueryHandler(ack_debt_handover, pattern=f"^{CB.DEBT_ACK}:ack$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_opening_cash),
            ],
            CS_OPEN_CONFIRM: [
                CallbackQueryHandler(confirm_open_shift, pattern=f"^{CB.CS_OPEN}:confirm$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_shift_action, pattern=f"^{CB.BACK}:menu$"),
        ],
        per_user=True, per_chat=True,
    )
    
    close_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_close_shift, pattern=f"^{CB.CS_CLOSE}:start$"),
        ],
        states={
            CS_CLOSE_BILL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bill_total),
            ],
            CS_CLOSE_OLD_BILL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_old_bill),
            ],
            CS_CLOSE_NEW_BILL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_bill),
            ],
            CS_CLOSE_CASH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_closing_cash),
            ],
            CS_CLOSE_EXTRA_EXPENSE_AMT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_extra_expense_amount),
            ],
            CS_CLOSE_EXTRA_EXPENSE_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_extra_expense_note),
            ],
            CS_CLOSE_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_closing_note),
            ],
            CS_CLOSE_CONFIRM: [
                CallbackQueryHandler(confirm_close_shift, pattern=f"^{CB.CS_CLOSE}:confirm$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_shift_action, pattern=f"^{CB.BACK}:menu$"),
        ],
        per_user=True, per_chat=True,
    )
    
    return [
        open_conv, 
        close_conv,
        CallbackQueryHandler(verify_handover_action, pattern=r"^verify_handover:\d+$"),
        CallbackQueryHandler(check_handover_status_action, pattern=r"^check_handover_status:\d+$"),
        CallbackQueryHandler(finalize_handover_action, pattern=r"^finalize_handover:\d+$"),
    ]
