"""
Format tiền VND, ngày giờ và các giá trị hiển thị.
"""

from datetime import datetime
from typing import Optional

import pytz

from bot.config import Config

VN_TZ = pytz.timezone(Config.TIMEZONE)


def format_money(amount: int) -> str:
    """
    Format số tiền VND.
    VD: 1350000 → '1.350.000đ'
    """
    if amount is None:
        return "—"
    sign = ""
    if amount < 0:
        sign = "-"
        amount = abs(amount)
    formatted = f"{amount:,}".replace(",", ".")
    return f"{sign}{formatted}đ"


def format_money_short(amount: int) -> str:
    """
    Format ngắn cho banner.
    VD: 1350000 → '1.350k'
    """
    if amount is None:
        return "—"
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}tr"
    if amount >= 1000:
        return f"{amount // 1000}k"
    return f"{amount}đ"


def format_datetime_vn(dt_str: Optional[str], fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Format chuỗi datetime sang hiển thị VN."""
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        dt_vn = dt.astimezone(VN_TZ)
        return dt_vn.strftime(fmt)
    except (ValueError, TypeError):
        return dt_str


def format_date_vn(dt_str: Optional[str]) -> str:
    """Format ngày."""
    return format_datetime_vn(dt_str, "%d/%m/%Y")


def format_time_vn(dt_str: Optional[str]) -> str:
    """Format giờ."""
    return format_datetime_vn(dt_str, "%H:%M")


def format_minutes(minutes: Optional[int]) -> str:
    """Format phút thành giờ:phút."""
    if minutes is None:
        return "—"
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}g{mins:02d}p"
    return f"{mins}p"


def format_duration(minutes: int) -> str:
    """Format duration dạng '7 giờ 45 phút'."""
    if minutes is None:
        return "—"
    hours = minutes // 60
    mins = minutes % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} giờ")
    if mins > 0:
        parts.append(f"{mins} phút")
    return " ".join(parts) if parts else "0 phút"


def truncate(text: str, max_len: int = 30) -> str:
    """Cắt ngắn text."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


def sanitize_csv_field(value: str) -> str:
    """
    Ngăn CSV formula injection.
    Nếu bắt đầu bằng =, +, -, @, tab, CR thì thêm dấu ' phía trước.
    """
    if not value:
        return value
    dangerous_chars = ('=', '+', '-', '@', '\t', '\r', '\n')
    if value.startswith(dangerous_chars):
        return f"'{value}"
    return value
