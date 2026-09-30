"""
Bot Telegram Quản Lý Quán Bida
Entry point chính.
"""

import logging
import asyncio
from datetime import timedelta

from telegram.ext import ApplicationBuilder, JobQueue

from bot.config import Config
from bot.models.database import run_migrations, backup_database
from bot.services.auth_service import ensure_owner_exists

# Handler imports
from bot.handlers import (
    start, auth, cash_shift, transactions,
    debt, attendance, shift_reg, salary,
    inventory, reports, dashboard
)

# Notification jobs
from bot.services.notification_service import (
    send_pending_notifications, check_late_checkins, check_unclosed_sessions
)


def setup_logging():
    """Cấu hình logging."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    )
    # Giảm noise từ httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def periodic_notifications(context):
    """Job gửi thông báo pending."""
    await send_pending_notifications(context.bot)


async def periodic_late_check(context):
    """Job kiểm tra check-in trễ."""
    await check_late_checkins(context.bot)


async def periodic_unclosed_check(context):
    """Job kiểm tra phiên chưa đóng."""
    await check_unclosed_sessions(context.bot)


async def periodic_backup(context):
    """Job backup định kỳ."""
    path = backup_database()
    if path:
        logging.info("Auto backup: %s", path)


async def post_init(application):
    """Khởi tạo sau khi bot sẵn sàng."""
    # Job queue
    job_queue = application.job_queue
    
    # Gửi thông báo pending mỗi phút
    job_queue.run_repeating(periodic_notifications, interval=60, first=10)
    
    # Kiểm tra check-in trễ mỗi 5 phút
    job_queue.run_repeating(periodic_late_check, interval=300, first=60)
    
    # Kiểm tra phiên chưa đóng mỗi 15 phút
    job_queue.run_repeating(periodic_unclosed_check, interval=900, first=120)
    
    # Backup mỗi 6 giờ
    job_queue.run_repeating(periodic_backup, interval=6 * 3600, first=3600)
    
    logging.info("Bot đã sẵn sàng!")


def main():
    """Entry point."""
    # Setup
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info("=== KHỞI ĐỘNG BOT QUẢN LÝ QUÁN BIDA ===")
    
    # Validate config
    Config.validate()
    Config.ensure_dirs()
    
    # Init database
    run_migrations()
    ensure_owner_exists()
    
    from bot.models.database import get_connection
    def ensure_default_shifts():
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM shift_templates").fetchone()["c"]
        if count == 0:
            from bot.services.shift_service import create_shift_template
            try:
                create_shift_template('Ca 1 (Sáng)', '08:30', '13:30', 2, Config.OWNER_ID, False)
                create_shift_template('Ca 2 (Chiều)', '13:30', '17:30', 2, Config.OWNER_ID, False)
                create_shift_template('Ca 3 (Tối)', '17:00', '02:00', 2, Config.OWNER_ID, True)
                logging.info("Đã khởi tạo 3 ca mặc định.")
            except Exception as e:
                logging.error(f"Lỗi tạo ca mặc định: {e}")
    
    ensure_default_shifts()
    
    logger.info("Database đã sẵn sàng")
    
    # Build application
    application = (
        ApplicationBuilder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Register handlers theo thứ tự ưu tiên
    # ConversationHandlers phải trước CallbackQueryHandlers đơn lẻ
    handler_modules = [
        auth,           # ConversationHandler thêm NV
        cash_shift,     # ConversationHandler mở/đóng ca
        transactions,   # ConversationHandler CK, chi, giao tiền
        debt,           # ConversationHandler ghi nợ, thu nợ + callback
        attendance,     # ConversationHandler check-in/sửa công + callback
        shift_reg,      # Callbacks đăng ký ca
        salary,         # ConversationHandler ứng lương, ghi đồ + callbacks
        inventory,      # ConversationHandler nhập hàng, kiểm kê + callback
        reports,        # ConversationHandler nhập bill + callbacks
        dashboard,      # Dashboard Hôm nay
        start,          # Menu chính (catch-all callbacks cuối cùng)
    ]
    
    for module in handler_modules:
        for handler in module.get_handlers():
            application.add_handler(handler)
    
    logger.info("Đã đăng ký %d handler modules", len(handler_modules))
    
    # Start polling
    logger.info("Bắt đầu polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
