"""
Handler chấm công: check-in có ảnh, check-out, xem công, sửa công.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    MessageHandler, CommandHandler, filters
)

from bot.constants import CB
from bot.utils.permissions import require_private_chat, require_employee, require_owner
from bot.utils.formatters import format_money, format_datetime_vn, format_minutes, format_duration
from bot.services.attendance_service import (
    start_checkin_flow, complete_checkin_with_photo, checkout,
    get_employee_sessions, get_open_sessions, get_pending_adjustments,
    request_adjustment, process_adjustment
)
from bot.services.shift_service import get_approved_shift_for_checkin, get_shift_template
from bot.utils.photo_storage import save_photo_from_telegram
from bot.models.database import vn_now, now_utc_iso

logger = logging.getLogger(__name__)

CI_SELECT_SHIFT, CI_WAIT_PHOTO = range(60, 62)
ADJ_DATE, ADJ_TIMES, ADJ_REASON, ADJ_CONFIRM = range(62, 66)
ADJ_PROCESS = 66


# ==================== CHECK-IN ====================

async def start_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu check-in. Hỗ trợ cả inline button và reply keyboard."""
    query = update.callback_query
    
    user_id = update.effective_user.id
    now = vn_now()
    
    # Lấy ca đã duyệt có thể check-in
    shifts = get_approved_shift_for_checkin(user_id, now.isoformat())
    
    if not shifts:
        msg = "⚠️ Không có ca đã duyệt hôm nay.\nHãy đăng ký ca trước."
        if query:
            await query.answer()
            await query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END
    
    keyboard = []
    for s in shifts:
        label = f"{s['shift_name']} ({s['start_time']} - {s['end_time']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{CB.ATT_CHECKIN}:shift:{s['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    text = "✅ *VÀO LÀM*\n\nChọn ca:"
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CI_SELECT_SHIFT


async def select_shift_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn ca và yêu cầu ảnh."""
    query = update.callback_query
    await query.answer()
    
    reg_id = int(query.data.split(":")[2])
    user_id = update.effective_user.id
    
    try:
        result = start_checkin_flow(user_id, reg_id)
        context.user_data["checkin_session_id"] = result["session_id"]
        
        await query.edit_message_text(
            f"📸 *{result['shift_name']}*\n\n"
            f"Gửi ảnh tại quán để hoàn tất check-in.\n"
            f"_(Có 5 phút để gửi ảnh)_",
            parse_mode="Markdown"
        )
        return CI_WAIT_PHOTO
        
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
        return ConversationHandler.END


async def receive_checkin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh check-in."""
    if not update.message.photo:
        await update.message.reply_text("📸 Vui lòng gửi ảnh để check-in:")
        return CI_WAIT_PHOTO
    
    user_id = update.effective_user.id
    session_id = context.user_data.get("checkin_session_id")
    
    if not session_id:
        await update.message.reply_text("❌ Phiên check-in đã hết hạn. Thử lại.")
        return ConversationHandler.END
    
    photo = update.message.photo[-1]
    
    # Lưu ảnh cục bộ
    local_path = await save_photo_from_telegram(
        update.get_bot(),
        photo.file_id,
        photo.file_unique_id,
        "checkin",
        str(session_id),
        user_id,
    )
    
    try:
        result = complete_checkin_with_photo(
            session_id=session_id,
            employee_id=user_id,
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            telegram_timestamp=str(update.message.date) if update.message.date else None,
            local_path=local_path,
        )
        
        if result.get("needs_handover"):
            text = f"⏳ *ĐÃ CHECK-IN ẢNH THÀNH CÔNG*\n\n"
            text += f"Giờ nhận ảnh: {format_datetime_vn(result['arrived_at'])}\n"
            text += f"Trạng thái: Đang chờ ca trước Bàn Giao Quỹ\n\n"
            text += f"_(Lương của bạn sẽ tự động được tính từ thời điểm người ca trước bấm Xác nhận chốt ca)._\n"
        else:
            text = f"✅ *ĐÃ VÀO LÀM*\n\n"
            text += f"Giờ check-in: {format_datetime_vn(result['checkin_time'])}\n"
            
            if result["is_late"]:
                text += f"⏰ Trễ: {result['late_minutes']} phút\n"
            else:
                text += f"✅ Đúng giờ\n"
            
            if result["is_duplicate_photo"]:
                text += f"⚠️ _Ảnh đã được sử dụng trước đó — đánh dấu xem xét_\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                         parse_mode="Markdown")
        
        # Báo chủ nếu trễ
        if result["is_late"] and result["late_minutes"] > 0:
            from bot.utils.permissions import get_user_display_name
            name = get_user_display_name(user_id) or "?"
            
            owner_text = (
                f"⏰ *Check-in trễ*\n"
                f"NV: {name}\n"
                f"Trễ: {result['late_minutes']} phút\n"
                f"Giờ thực: {format_datetime_vn(result['checkin_time'])}"
            )
            keyboard = [[InlineKeyboardButton("✅ Bỏ qua đi trễ (Tính đủ lương)", callback_data=f"forgive_late:{session_id}")]]
            
            try:
                from bot.config import Config
                if Config.OWNER_TELEGRAM_ID:
                    await context.bot.send_message(
                        chat_id=Config.OWNER_TELEGRAM_ID,
                        text=owner_text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Lỗi gửi thông báo đi trễ cho chủ: {e}")
        
    except ValueError as e:
        await update.message.reply_text(f"❌ {str(e)}")
    
    context.user_data.pop("checkin_session_id", None)
    return ConversationHandler.END


# ==================== CHECK-OUT ====================

async def do_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check-out."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    try:
        result = checkout(user_id)
        
        text = f"🚪 *ĐÃ RA VỀ*\n\n"
        text += f"Vào: {format_datetime_vn(result['checkin_time'])}\n"
        text += f"Ra: {format_datetime_vn(result['checkout_time'])}\n"
        text += f"Thời gian: {format_duration(result['total_minutes'])}\n"
        
        if result.get("late_minutes", 0) > 0:
            text += f"Trễ: {result['late_minutes']} phút\n"
        if result.get("early_leave_minutes", 0) > 0:
            text += f"Về sớm: {result['early_leave_minutes']} phút\n"
        if result.get("overtime_minutes", 0) > 0:
            text += f"Làm thêm: {result['overtime_minutes']} phút\n"
        
        if result.get("wage_rate") and result.get("wage_amount") is not None:
            text += f"\n💰 Mức lương: {format_money(result['wage_rate'])}/giờ\n"
            text += f"💰 Tiền công: {format_money(result['wage_amount'])}\n"
        
        if result.get("is_long_session"):
            text += f"\n⚠️ _Phiên quá dài ({format_duration(result['total_minutes'])})_\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                       parse_mode="Markdown")
        
    except ValueError as e:
        try:
            await query.edit_message_text(f"❌ {str(e)}")
        except Exception as err:
            if "Message is not modified" not in str(err):
                raise


# ==================== XEM CÔNG ====================

async def view_my_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem công của tôi."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    
    # 7 ngày gần nhất
    from datetime import timedelta
    from_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")
    
    sessions = get_employee_sessions(user_id, from_date, to_date)
    
    text = f"📊 *CÔNG CỦA TÔI* (7 ngày)\n\n"
    
    if not sessions:
        text += "_Chưa có phiên công nào_"
    else:
        total_minutes = 0
        total_wage = 0
        for s in sessions:
            date = s["shift_date"]
            status_icon = "✅" if s["status"] == "checked_out" else "🔵"
            ci = format_datetime_vn(s["checkin_time"], "%H:%M") if s["checkin_time"] else "—"
            co = format_datetime_vn(s["checkout_time"], "%H:%M") if s["checkout_time"] else "—"
            mins = s.get("approved_minutes") or 0
            wage = s.get("wage_amount") or 0
            
            text += f"{status_icon} {date}: {ci}→{co} ({format_minutes(mins)})"
            if s.get("late_minutes", 0) > 0:
                text += f" ⏰{s['late_minutes']}p"
            if s.get("pending_adjustments", 0) > 0:
                text += " 📝"
            text += f" — {format_money(wage)}\n"
            
            total_minutes += mins
            total_wage += wage
        
        text += f"\n*Tổng: {format_duration(total_minutes)} — {format_money(total_wage)}*"
    
    keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


# ==================== SỬA CÔNG ====================

async def start_adjust_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu yêu cầu sửa công."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 *YÊU CẦU SỬA CÔNG*\n\n"
        "Nhập ngày cần sửa (DD/MM/YYYY):",
        parse_mode="Markdown"
    )
    return ADJ_DATE


async def receive_adj_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ngày sửa."""
    from bot.utils.validators import parse_date
    date = parse_date(update.message.text)
    if not date:
        await update.message.reply_text("❌ Ngày không hợp lệ. Nhập DD/MM/YYYY:")
        return ADJ_DATE
    
    context.user_data["adj_date"] = date
    await update.message.reply_text(
        "Nhập giờ vào và giờ ra đề nghị:\n"
        "_(VD: 14:00 22:00)_",
        parse_mode="Markdown"
    )
    return ADJ_TIMES


async def receive_adj_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận giờ đề nghị."""
    from bot.utils.validators import parse_time
    parts = update.message.text.strip().split()
    if len(parts) != 2:
        await update.message.reply_text("❌ Nhập 2 giờ cách nhau: HH:MM HH:MM")
        return ADJ_TIMES
    
    ci = parse_time(parts[0])
    co = parse_time(parts[1])
    if not ci or not co:
        await update.message.reply_text("❌ Giờ không hợp lệ. VD: 14:00 22:00")
        return ADJ_TIMES
    
    context.user_data["adj_checkin"] = f"{context.user_data['adj_date']}T{ci}:00"
    context.user_data["adj_checkout"] = f"{context.user_data['adj_date']}T{co}:00"
    
    await update.message.reply_text("Lý do sửa công:")
    return ADJ_REASON


async def receive_adj_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận lý do và xác nhận."""
    reason = update.message.text.strip()
    context.user_data["adj_reason"] = reason
    
    keyboard = [
        [InlineKeyboardButton("✅ Gửi yêu cầu", callback_data=f"{CB.ATT_ADJUST_REQ}:confirm")],
        [InlineKeyboardButton("❌ Hủy", callback_data=f"{CB.BACK}:menu")],
    ]
    await update.message.reply_text(
        f"📝 *XÁC NHẬN SỬA CÔNG*\n\n"
        f"Ngày: {context.user_data['adj_date']}\n"
        f"Vào: {context.user_data['adj_checkin'][-8:-3]}\n"
        f"Ra: {context.user_data['adj_checkout'][-8:-3]}\n"
        f"Lý do: {reason}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADJ_CONFIRM


async def confirm_adjustment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gửi yêu cầu sửa công."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    adj_id = request_adjustment(
        employee_id=user_id,
        shift_date=context.user_data["adj_date"],
        requested_checkin=context.user_data["adj_checkin"],
        requested_checkout=context.user_data["adj_checkout"],
        reason=context.user_data["adj_reason"],
    )
    
    await query.edit_message_text(
        f"✅ *Đã gửi yêu cầu sửa công #{adj_id}*\n"
        f"Chờ chủ quán duyệt.",
        parse_mode="Markdown"
    )
    
    # Báo chủ
    from bot.services.notification_service import notify_owner
    from bot.utils.permissions import get_user_display_name
    name = get_user_display_name(user_id) or "?"
    await notify_owner(
        context.bot,
        f"📝 *Yêu cầu sửa công #{adj_id}*\n"
        f"NV: {name}\n"
        f"Ngày: {context.user_data['adj_date']}\n"
        f"Giờ: {context.user_data['adj_checkin'][-8:-3]}→{context.user_data['adj_checkout'][-8:-3]}\n"
        f"Lý do: {context.user_data['adj_reason']}",
        "adjustment_request",
        "attendance_adjustments", adj_id
    )
    
    for key in ["adj_date", "adj_checkin", "adj_checkout", "adj_reason"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ==================== CHỦ DUYỆT SỬA CÔNG ====================

async def list_pending_adjustments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chủ xem yêu cầu sửa công chờ duyệt."""
    query = update.callback_query
    await query.answer()
    
    pending = get_pending_adjustments()
    if not pending:
        await query.edit_message_text("✅ Không có yêu cầu sửa công.")
        return
    
    text = "📝 *YÊU CẦU SỬA CÔNG*\n\n"
    keyboard = []
    for adj in pending:
        ci = adj.get("requested_checkin", "?")[-8:-3] if adj.get("requested_checkin") else "?"
        co = adj.get("requested_checkout", "?")[-8:-3] if adj.get("requested_checkout") else "?"
        text += f"• #{adj['id']} {adj['display_name']} ngày {adj['shift_date']}: {ci}→{co}\n"
        text += f"  Lý do: {adj.get('reason', '—')}\n\n"
        keyboard.append([
            InlineKeyboardButton(f"✅ Duyệt #{adj['id']}", callback_data=f"{CB.ATT_ADJUST_APPROVE}:{adj['id']}"),
            InlineKeyboardButton(f"❌ Từ chối", callback_data=f"{CB.ATT_ADJUST_REJECT}:{adj['id']}"),
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def approve_adj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Duyệt sửa công."""
    query = update.callback_query
    await query.answer()
    
    adj_id = int(query.data.split(":")[1])
    try:
        result = process_adjustment(adj_id, update.effective_user.id, approve=True)
        await query.edit_message_text(f"✅ Đã duyệt sửa công #{adj_id}")
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")


async def reject_adj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Từ chối sửa công."""
    query = update.callback_query
    await query.answer()
    
    adj_id = int(query.data.split(":")[1])
    try:
        result = process_adjustment(adj_id, update.effective_user.id, approve=False, reason="Từ chối")
        await query.edit_message_text(f"❌ Đã từ chối sửa công #{adj_id}")
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")


async def forgive_late_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bỏ qua đi trễ."""
    query = update.callback_query
    await query.answer()
    
    session_id = int(query.data.split(":")[1])
    from bot.models.database import get_connection
    conn = get_connection()
    session = conn.execute("SELECT scheduled_start FROM attendance_sessions WHERE id = ?", (session_id,)).fetchone()
    if session and session["scheduled_start"]:
        conn.execute("UPDATE attendance_sessions SET checkin_time = ?, late_minutes = 0 WHERE id = ?", (session["scheduled_start"], session_id))
        conn.commit()
        
        new_text = query.message.text + "\n\n✅ *Đã bỏ qua đi trễ, tính từ đầu ca.*"
        await query.edit_message_text(new_text, parse_mode="Markdown")

async def cancel_att(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy."""
    for key in ["checkin_session_id", "adj_date", "adj_checkin", "adj_checkout", "adj_reason"]:
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END


def get_handlers():
    """Trả về handlers."""
        entry_points=[
            CallbackQueryHandler(start_checkin, pattern=f"^{CB.ATT_CHECKIN}:start$"),
            MessageHandler(filters.Regex(r"^✅ Check-in$"), start_checkin),
        ],
        states={
            CI_SELECT_SHIFT: [CallbackQueryHandler(select_shift_checkin, pattern=f"^{CB.ATT_CHECKIN}:shift:")],
            CI_WAIT_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, receive_checkin_photo)],
        },
        fallbacks=[CommandHandler('start', cancel_att), CallbackQueryHandler(cancel_att, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    checkin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_checkin, pattern=f"^{CB.ATT_CHECKIN}:start$"),
            MessageHandler(filters.Regex(r"^✅ Check-in$"), start_checkin),
        ],
        states={
            CI_SELECT_SHIFT: [CallbackQueryHandler(select_shift_checkin, pattern=f"^{CB.ATT_CHECKIN}:shift:")],
            CI_WAIT_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, receive_checkin_photo)],
        },
        fallbacks=[CommandHandler('start', cancel_att), CallbackQueryHandler(cancel_att, pattern=f"^{CB.BACK}:menu$")],
        name="attendance_conv_1", persistent=True,
        per_user=True, per_chat=True,
    )
    
        entry_points=[CallbackQueryHandler(start_adjust_request, pattern=f"^{CB.ATT_ADJUST_REQ}:start$")],
        states={
            ADJ_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_date)],
            ADJ_TIMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_times)],
            ADJ_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_reason)],
            ADJ_CONFIRM: [CallbackQueryHandler(confirm_adjustment, pattern=f"^{CB.ATT_ADJUST_REQ}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_att), CallbackQueryHandler(cancel_att, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    adjust_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_adjust_request, pattern=f"^{CB.ATT_ADJUST_REQ}:start$")],
        states={
            ADJ_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_date)],
            ADJ_TIMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_times)],
            ADJ_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_adj_reason)],
            ADJ_CONFIRM: [CallbackQueryHandler(confirm_adjustment, pattern=f"^{CB.ATT_ADJUST_REQ}:confirm$")],
        },
        fallbacks=[CommandHandler('start', cancel_att), CallbackQueryHandler(cancel_att, pattern=f"^{CB.BACK}:menu$")],
        name="attendance_conv_2", persistent=True,
        per_user=True, per_chat=True,
    )
    
    return [
        checkin_conv,
        CallbackQueryHandler(do_checkout, pattern=f"^{CB.ATT_CHECKOUT}:do$"),
        CallbackQueryHandler(view_my_attendance, pattern=f"^{CB.ATT_MY_LOG}:view$"),
        adjust_conv,
        CallbackQueryHandler(list_pending_adjustments, pattern=f"^{CB.SHIFT_APPROVE}:list$"),
        CallbackQueryHandler(approve_adj, pattern=f"^{CB.ATT_ADJUST_APPROVE}:"),
        CallbackQueryHandler(reject_adj, pattern=f"^{CB.ATT_ADJUST_REJECT}:"),
        CallbackQueryHandler(forgive_late_callback, pattern=f"^forgive_late:"),
    ]
