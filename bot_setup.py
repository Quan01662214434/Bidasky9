"""
Bot Application setup - dùng chung cho cả webhook (web_server.py) và polling (main.py).
Tách riêng để tránh duplicate code.
"""
import logging
from telegram.ext import ApplicationBuilder
from bot.config import Config
from bot.models.database import run_migrations
from bot.services.auth_service import ensure_owner_exists

logger = logging.getLogger(__name__)


def create_bot_application():
    """Tạo và cấu hình Bot Application với toàn bộ handlers.
    
    Dùng updater=None để tự quản lý update (webhook hoặc polling thủ công).
    """
    # Validate config
    Config.validate()
    Config.ensure_dirs()
    
    # Init database
    run_migrations()
    ensure_owner_exists()
    
    # Ensure default shift templates
    from bot.models.database import get_connection
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) as c FROM shift_templates").fetchone()["c"]
    if count == 0:
        from bot.services.shift_service import create_shift_template
        try:
            create_shift_template('Ca 1 (Sáng)', '08:30', '13:30', 2, Config.OWNER_ID, False)
            create_shift_template('Ca 2 (Chiều)', '13:30', '17:30', 2, Config.OWNER_ID, False)
            create_shift_template('Ca 3 (Tối)', '17:00', '02:00', 2, Config.OWNER_ID, True)
            logger.info("Đã khởi tạo 3 ca mặc định.")
        except Exception as e:
            logger.error("Lỗi tạo ca mặc định: %s", e)
    
    # Build application with DB-backed persistence
    # Saves ConversationHandler state + user_data to user_states table
    from bot.utils.persistence import PostgresPersistence
    persistence = PostgresPersistence()
    
    application = (
        ApplicationBuilder()
        .token(Config.BOT_TOKEN)
        .updater(None)
        .persistence(persistence)
        .build()
    )
    
    # ─── Global Error Handler ───
    # Bắt MỌI lỗi, log lại nhưng KHÔNG crash bot
    async def error_handler(update, context):
        logger.error("Bot handler error: %s", context.error, exc_info=context.error)
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "⚠️ Đã xảy ra lỗi. Vui lòng thử lại hoặc bấm /start."
                )
        except Exception:
            pass
    
    application.add_error_handler(error_handler)
    
    # Import handlers
    from bot.handlers import (
        start, auth, cash_shift, transactions,
        debt, attendance, shift_reg, salary,
        inventory, reports, dashboard
    )
    
    # Register handlers theo thứ tự ưu tiên
    handler_modules = [
        auth, cash_shift, transactions, debt, attendance,
        shift_reg, salary, inventory, reports, dashboard, start,
    ]
    
    for module in handler_modules:
        for handler in module.get_handlers():
            application.add_handler(handler)
    
    logger.info("Đã đăng ký %d handler modules", len(handler_modules))
    
    return application


async def setup_periodic_jobs(application):
    """Đăng ký các job định kỳ (thông báo, kiểm tra check-in trễ, v.v.)."""
    from bot.services.notification_service import (
        send_pending_notifications, check_late_checkins, check_unclosed_sessions
    )
    
    async def periodic_notifications(context):
        await send_pending_notifications(context.bot)
    
    async def periodic_late_check(context):
        await check_late_checkins(context.bot)
    
    async def periodic_unclosed_check(context):
        await check_unclosed_sessions(context.bot)
        
    async def periodic_photo_retry(context):
        from bot.services.photo_queue_service import process_photo_upload_queue
        await process_photo_upload_queue()
    
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(periodic_notifications, interval=60, first=10)
        job_queue.run_repeating(periodic_late_check, interval=300, first=60)
        job_queue.run_repeating(periodic_unclosed_check, interval=60, first=60)
        job_queue.run_repeating(periodic_photo_retry, interval=300, first=30)
        logger.info("Đã đăng ký periodic jobs")
