"""
Handler đăng ký ca và xem lịch.
"""

import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from bot.constants import CB
from bot.utils.permissions import require_employee, require_owner
from bot.utils.formatters import format_date_vn
from bot.services.shift_service import (
    get_shift_templates, register_shift, get_registrations_for_date,
    get_employee_registrations, get_pending_registrations,
    approve_shift, reject_shift, batch_approve_shifts
)
from bot.models.database import vn_now

logger = logging.getLogger(__name__)


async def view_shift_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem lịch ca."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    now = vn_now()
    
    is_all = ":all" in query.data
    
    # 7 ngày tới
    text = "📅 *LỊCH LÀM VIỆC*\n\n"
    keyboard = []
    
    for i in range(7):
        day = now + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        display = day.strftime("%d/%m (%a)")
        
        regs = get_registrations_for_date(date_str)
        if not is_all:
            regs = [r for r in regs if r["employee_id"] == user_id]
        
        if regs:
            text += f"📅 *{display}*\n"
            for r in regs:
                status_icon = {"pending": "⏳", "approved": "✅", "rejected": "❌", "cancelled": "🚫"}.get(r["status"], "?")
                name = r.get("display_name", "")
                text += f"  {status_icon} {r['shift_name']} ({r['start_time']}-{r['end_time']}) {name}\n"
            text += "\n"
    
    if not any(get_registrations_for_date((now + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(7)):
        text += "_Chưa có lịch_\n"
    
    keyboard.append([InlineKeyboardButton("✍️ Đăng ký ca", callback_data=f"{CB.SHIFT_REG}:start")])
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def start_register_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bắt đầu đăng ký ca."""
    query = update.callback_query
    await query.answer()
    
    templates = get_shift_templates()
    if not templates:
        await query.edit_message_text(
            "⚠️ Chưa có khung ca nào.\n"
            "Chủ quán cần tạo khung ca trước."
        )
        return
    
    now = vn_now()
    keyboard = []
    for i in range(7):
        day = now + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        display = day.strftime("%d/%m/%Y (%a)")
        keyboard.append([InlineKeyboardButton(
            display, callback_data=f"{CB.SHIFT_REG}:date:{date_str}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(
        "📅 *ĐĂNG KÝ CA*\n\nChọn ngày:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def select_date_for_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chọn ngày đăng ký."""
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split(":")[2]
    context.user_data["reg_date"] = date_str
    
    templates = get_shift_templates()
    keyboard = []
    for t in templates:
        label = f"{t['name']} ({t['start_time']} - {t['end_time']})"
        keyboard.append([InlineKeyboardButton(
            label, callback_data=f"{CB.SHIFT_REG}:template:{t['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Quay lại", callback_data=f"{CB.SHIFT_REG}:start")])
    
    await query.edit_message_text(
        f"📅 Ngày: {format_date_vn(date_str)}\n\nChọn ca:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def confirm_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận đăng ký."""
    query = update.callback_query
    await query.answer()
    
    template_id = int(query.data.split(":")[2])
    date_str = context.user_data.get("reg_date")
    user_id = update.effective_user.id
    
    try:
        result = register_shift(user_id, template_id, date_str)
        await query.edit_message_text(
            f"✅ *Đã đăng ký ca*\n"
            f"Ngày: {format_date_vn(date_str)}\n"
            f"Trạng thái: Chờ duyệt\n\n"
            f"Chủ quán sẽ duyệt lịch.",
            parse_mode="Markdown"
        )
        
        # Báo chủ
        from bot.services.notification_service import notify_owner
        from bot.utils.permissions import get_user_display_name
        from bot.services.shift_service import get_shift_template
        name = get_user_display_name(user_id) or "?"
        template = get_shift_template(template_id)
        await notify_owner(
            context.bot,
            f"📅 *Đăng ký ca mới*\n"
            f"NV: {name}\n"
            f"Ngày: {format_date_vn(date_str)}\n"
            f"Ca: {template['name'] if template else '?'}",
            "shift_registration"
        )
        
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")
    
    context.user_data.pop("reg_date", None)


# ==================== CHỦ DUYỆT ====================

async def owner_approve_shifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chủ duyệt hàng loạt."""
    query = update.callback_query
    await query.answer()
    
    pending = get_pending_registrations()
    if not pending:
        await query.edit_message_text("✅ Không có đăng ký chờ duyệt.")
        return
    
    text = "📅 *DUYỆT ĐĂNG KÝ CA*\n\n"
    keyboard = []
    
    approve_ids = []
    for r in pending:
        text += f"• {r['display_name']} — {r['shift_name']} ngày {format_date_vn(r['shift_date'])}\n"
        approve_ids.append(r["id"])
        keyboard.append([
            InlineKeyboardButton(f"✅ {r['display_name']}", callback_data=f"{CB.SHIFT_APPROVE}:one:{r['id']}"),
            InlineKeyboardButton(f"❌", callback_data=f"{CB.SHIFT_REJECT}:one:{r['id']}"),
        ])
    
    if len(pending) > 1:
        ids_str = ",".join(str(i) for i in approve_ids)
        keyboard.insert(0, [InlineKeyboardButton("✅ Duyệt tất cả", callback_data=f"{CB.SHIFT_APPROVE}:batch:{ids_str}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode="Markdown")


async def approve_one_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Duyệt một ca."""
    query = update.callback_query
    await query.answer()
    
    reg_id = int(query.data.split(":")[2])
    try:
        approve_shift(reg_id, update.effective_user.id)
        await query.edit_message_text(f"✅ Đã duyệt ca #{reg_id}")
    except ValueError as e:
        await query.edit_message_text(f"❌ {str(e)}")


async def approve_batch_shifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Duyệt hàng loạt."""
    query = update.callback_query
    await query.answer()
    
    ids_str = query.data.split(":")[2]
    ids = [int(x) for x in ids_str.split(",")]
    
    results = batch_approve_shifts(ids, update.effective_user.id)
    
    text = f"✅ Đã duyệt: {len(results['approved'])} ca\n"
    if results["failed"]:
        text += f"❌ Lỗi: {len(results['failed'])} ca\n"
        for f in results["failed"]:
            text += f"  • #{f['id']}: {f['error']}\n"
    
    await query.edit_message_text(text)


async def reject_one_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Từ chối ca."""
    query = update.callback_query
    await query.answer()
    
    reg_id = int(query.data.split(":")[2])
    reject_shift(reg_id, update.effective_user.id)
    await query.edit_message_text(f"❌ Đã từ chối ca #{reg_id}")


async def add_shift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh thêm khung ca dành cho chủ (VD: /add_shift Sáng 08:00 14:00 2)."""
    if not require_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ chủ quán mới được dùng lệnh này.")
        return
        
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "⚠️ Cú pháp sai.\n"
            "Sử dụng: `/add_shift <Tên_Ca> <Giờ_Vào> <Giờ_Ra> <Số_Người>`\n"
            "Ví dụ: `/add_shift Sáng 08:00 14:00 2`",
            parse_mode="Markdown"
        )
        return
        
    name = args[0]
    start_time = args[1]
    end_time = args[2]
    try:
        max_staff = int(args[3])
    except:
        await update.message.reply_text("❌ Số người phải là số.")
        return
        
    try:
        from bot.services.shift_service import create_shift_template
        # Basic validation (could be improved)
        if len(start_time) != 5 or ":" not in start_time:
            raise ValueError("Giờ vào phải định dạng HH:MM (VD: 08:00)")
            
        crosses_midnight = False
        h1, m1 = map(int, start_time.split(":"))
        h2, m2 = map(int, end_time.split(":"))
        if h2 < h1 or (h2 == h1 and m2 <= m1):
            crosses_midnight = True
            
        create_shift_template(name, start_time, end_time, max_staff, update.effective_user.id, crosses_midnight)
        
        await update.message.reply_text(
            f"✅ Đã thêm khung ca:\n"
            f"Tên: {name}\n"
            f"Giờ: {start_time} - {end_time}\n"
            f"Số lượng: {max_staff} người\n"
            f"{'(Ca qua đêm)' if crosses_midnight else ''}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {str(e)}")


ADD_SHIFT_NAME, ADD_SHIFT_TIME, ADD_SHIFT_STAFF = range(50, 53)

async def start_add_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not require_owner(update.effective_user.id):
        await query.edit_message_text("⛔ Chỉ chủ quán mới được dùng chức năng này.")
        return ConversationHandler.END
        
    await query.edit_message_text(
        "➕ *THÊM KHUNG CA*\n\n"
        "Nhập TÊN CA (Ví dụ: Ca Sáng, Ca Chiều, Ca Tối):",
        parse_mode="Markdown"
    )
    return ADD_SHIFT_NAME

async def receive_shift_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["add_shift_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Nhập KHUNG GIỜ của ca này theo định dạng HH:MM-HH:MM (Ví dụ: 08:00-14:00):"
    )
    return ADD_SHIFT_TIME

async def receive_shift_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()
    try:
        if "-" not in time_str:
            raise ValueError()
        start, end = time_str.split("-")
        start = start.strip()
        end = end.strip()
        if len(start) != 5 or len(end) != 5:
            raise ValueError()
        context.user_data["add_shift_start"] = start
        context.user_data["add_shift_end"] = end
        
        await update.message.reply_text(
            "Nhập SỐ NHÂN VIÊN cần cho ca này (Ví dụ: 2):"
        )
        return ADD_SHIFT_STAFF
    except:
        await update.message.reply_text("❌ Định dạng giờ không đúng. Hãy nhập lại (VD: 08:00-14:00):")
        return ADD_SHIFT_TIME

async def receive_shift_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        staff = int(update.message.text.strip())
        if staff <= 0:
            raise ValueError()
    except:
        await update.message.reply_text("❌ Số người phải là số lớn hơn 0. Nhập lại:")
        return ADD_SHIFT_STAFF
        
    name = context.user_data["add_shift_name"]
    start = context.user_data["add_shift_start"]
    end = context.user_data["add_shift_end"]
    
    # crosses midnight logic
    crosses_midnight = False
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    if h2 < h1 or (h2 == h1 and m2 <= m1):
        crosses_midnight = True
        
    from bot.services.shift_service import create_shift_template
    create_shift_template(name, start, end, staff, update.effective_user.id, crosses_midnight)
    
    await update.message.reply_text(
        f"✅ *Đã tạo Khung Ca thành công!*\n\n"
        f"Tên ca: {name}\n"
        f"Giờ làm: {start} - {end} {'(Ca qua đêm)' if crosses_midnight else ''}\n"
        f"Số nhân viên: {staff} người\n\n"
        f"Nhân viên đã có thể thấy ca này để đăng ký lịch làm việc.",
        parse_mode="Markdown"
    )
    
    for k in ["add_shift_name", "add_shift_start", "add_shift_end"]:
        context.user_data.pop(k, None)
        
    return ConversationHandler.END
    
async def cancel_add_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ["add_shift_name", "add_shift_start", "add_shift_end"]:
        context.user_data.pop(k, None)
    if update.callback_query:
        await update.callback_query.answer()
    from bot.handlers.start import _show_main_menu
    await _show_main_menu(update, context)
    return ConversationHandler.END

def get_handlers():
    """Trả về handlers."""
    from telegram.ext import CommandHandler, MessageHandler, filters, ConversationHandler
    
        entry_points=[CallbackQueryHandler(start_add_shift, pattern="^add_shift:start$")],
        states={
            ADD_SHIFT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_name)],
            ADD_SHIFT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_time)],
            ADD_SHIFT_STAFF: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_staff)],
        },
        fallbacks=[CommandHandler('start', cancel_add_shift), CallbackQueryHandler(cancel_add_shift, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    add_shift_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_shift, pattern="^add_shift:start$")],
        states={
            ADD_SHIFT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_name)],
            ADD_SHIFT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_time)],
            ADD_SHIFT_STAFF: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_shift_staff)],
        },
        fallbacks=[CommandHandler('start', cancel_add_shift), CallbackQueryHandler(cancel_add_shift, pattern=f"^{CB.BACK}:menu$")],
        name="shift_reg_conv_1", persistent=True,
        per_user=True, per_chat=True,
    )
    
    EDIT_SHIFT_SELECT = 4
    EDIT_SHIFT_TIME = 5
    
    async def start_edit_shift(update, context):
        query = update.callback_query
        await query.answer()
        
        from bot.services.shift_service import get_active_shift_templates
        templates = get_active_shift_templates()
        if not templates:
            await query.edit_message_text("❌ Chưa có khung ca nào để sửa.")
            return ConversationHandler.END
            
        keyboard = []
        for t in templates:
            keyboard.append([InlineKeyboardButton(f"{t['name']} ({t['start_time']} - {t['end_time']})", callback_data=f"edit_shift_sel:{t['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data=f"{CB.BACK}:menu")])
        
        await query.edit_message_text("Chọn ca bạn muốn sửa giờ:", reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_SHIFT_SELECT
        
    async def select_edit_shift(update, context):
        query = update.callback_query
        await query.answer()
        shift_id = int(query.data.split(":")[1])
        context.user_data["edit_shift_id"] = shift_id
        
        await query.edit_message_text("Nhập giờ mới cho ca này theo định dạng HH:MM-HH:MM (Ví dụ: 08:30-13:30):")
        return EDIT_SHIFT_TIME
        
    async def receive_edit_shift_time(update, context):
        text = update.message.text.strip()
        if "-" not in text:
            await update.message.reply_text("❌ Sai định dạng. Vui lòng nhập kiểu: 08:30-13:30")
            return EDIT_SHIFT_TIME
            
        start, end = [x.strip() for x in text.split("-", 1)]
        try:
            import datetime
            datetime.datetime.strptime(start, "%H:%M")
            datetime.datetime.strptime(end, "%H:%M")
        except ValueError:
            await update.message.reply_text("❌ Giờ không hợp lệ. Vui lòng nhập HH:MM (VD: 08:30)")
            return EDIT_SHIFT_TIME
            
        crosses = False
        if end < start:
            crosses = True
            
        shift_id = context.user_data.get("edit_shift_id")
        
        from bot.models.database import get_connection
        conn = get_connection()
        conn.execute(
            "UPDATE shift_templates SET start_time = ?, end_time = ?, crosses_midnight = ? WHERE id = ?",
            (start, end, 1 if crosses else 0, shift_id)
        )
        conn.commit()
        
        await update.message.reply_text(f"✅ Đã cập nhật giờ ca thành công thành {start} - {end}.")
        context.user_data.pop("edit_shift_id", None)
        return ConversationHandler.END
        
        entry_points=[CallbackQueryHandler(start_edit_shift, pattern="^edit_shift:start$")],
        states={
            EDIT_SHIFT_SELECT: [CallbackQueryHandler(select_edit_shift, pattern="^edit_shift_sel:")],
            EDIT_SHIFT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_shift_time)],
        },
        fallbacks=[CommandHandler('start', cancel_add_shift), CallbackQueryHandler(cancel_add_shift, pattern=f"^{CB.BACK}:menu$")],
        per_user=True, per_chat=True,
    edit_shift_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_shift, pattern="^edit_shift:start$")],
        states={
            EDIT_SHIFT_SELECT: [CallbackQueryHandler(select_edit_shift, pattern="^edit_shift_sel:")],
            EDIT_SHIFT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_shift_time)],
        },
        fallbacks=[CommandHandler('start', cancel_add_shift), CallbackQueryHandler(cancel_add_shift, pattern=f"^{CB.BACK}:menu$")],
        name="shift_reg_conv_2", persistent=True,
        per_user=True, per_chat=True,
    )
    
    return [
        add_shift_conv,
        edit_shift_conv,
        CommandHandler("add_shift", add_shift_cmd),
        CallbackQueryHandler(view_shift_schedule, pattern=f"^{CB.SHIFT_VIEW}:(my|all)$"),
        CallbackQueryHandler(start_register_shift, pattern=f"^{CB.SHIFT_REG}:start$"),
        CallbackQueryHandler(select_date_for_reg, pattern=f"^{CB.SHIFT_REG}:date:"),
        CallbackQueryHandler(confirm_register, pattern=f"^{CB.SHIFT_REG}:template:"),
        CallbackQueryHandler(owner_approve_shifts, pattern=f"^{CB.SHIFT_APPROVE}:list$"),
        CallbackQueryHandler(approve_one_shift, pattern=f"^{CB.SHIFT_APPROVE}:one:"),
        CallbackQueryHandler(approve_batch_shifts, pattern=f"^{CB.SHIFT_APPROVE}:batch:"),
        CallbackQueryHandler(reject_one_shift, pattern=f"^{CB.SHIFT_REJECT}:one:"),
    ]
