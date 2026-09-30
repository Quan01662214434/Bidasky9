"""
Validate và parse input từ người dùng.
"""

import re
from typing import Optional, Tuple


def parse_money(text: str) -> Optional[int]:
    """
    Parse số tiền VND từ input người dùng.
    Hỗ trợ: 150000, 150.000, 150k, 1.5tr, 1500000
    Trả về số nguyên VND hoặc None nếu không hợp lệ.
    Không chấp nhận số âm hoặc chuỗi mơ hồ.
    """
    if not text:
        return None
    
    text = text.strip().lower().replace("đ", "").replace("d", "").replace(",", ".").strip()
    
    # Xử lý "k" (nghìn)
    if text.endswith("k"):
        try:
            num = float(text[:-1].replace(".", ""))
            if num < 0:
                return None
            return int(num * 1000)
        except (ValueError, TypeError):
            return None
    
    # Xử lý "tr" (triệu)
    if text.endswith("tr"):
        try:
            raw = text[:-2]
            # Kiểm tra dấu chấm là phân cách thập phân hay hàng nghìn
            parts = raw.split(".")
            if len(parts) == 2 and len(parts[1]) != 3:
                # Dấu chấm thập phân: 1.5tr = 1.5 triệu
                num = float(raw)
            else:
                # Dấu chấm hàng nghìn hoặc không có: 1.500tr, 2tr
                num = float(raw.replace(".", ""))
            if num < 0:
                return None
            return int(num * 1_000_000)
        except (ValueError, TypeError):
            return None
    
    # Xử lý số thuần (có thể có dấu chấm ngăn cách)
    # 150.000 → 150000, 1.500.000 → 1500000
    try:
        # Nếu có dấu chấm ngăn cách hàng nghìn
        parts = text.split(".")
        if len(parts) > 1:
            # Kiểm tra tất cả phần sau dấu chấm đầu tiên có 3 chữ số
            if all(len(p) == 3 for p in parts[1:]):
                # Dấu chấm ngăn cách hàng nghìn
                num = int("".join(parts))
            else:
                # Không phải format VND hợp lệ
                return None
        else:
            num = int(text)
        
        if num < 0:
            return None
        return num
    except (ValueError, TypeError):
        return None


def validate_bill_code(code: str) -> Tuple[bool, str]:
    """
    Chuẩn hóa mã bill.
    Trả về (is_valid, normalized_code).
    """
    if not code:
        return False, ""
    
    code = code.strip().upper()
    # Chỉ cho phép chữ, số, dấu gạch ngang
    if not re.match(r'^[A-Z0-9\-_]+$', code):
        return False, ""
    
    return True, code


def validate_phone(phone: str) -> Tuple[bool, str]:
    """Validate số điện thoại VN."""
    if not phone:
        return True, ""  # Không bắt buộc
    
    phone = phone.strip().replace(" ", "").replace(".", "").replace("-", "")
    # Chuẩn hóa
    if phone.startswith("+84"):
        phone = "0" + phone[3:]
    elif phone.startswith("84"):
        phone = "0" + phone[2:]
    
    if not re.match(r'^0\d{9,10}$', phone):
        return False, ""
    
    return True, phone


def parse_date(text: str) -> Optional[str]:
    """
    Parse ngày từ DD/MM/YYYY hoặc DD-MM-YYYY.
    Trả về YYYY-MM-DD hoặc None.
    """
    if not text:
        return None
    
    text = text.strip().replace("-", "/")
    parts = text.split("/")
    
    if len(parts) != 3:
        return None
    
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2])
        
        # Validate
        if year < 2020 or year > 2100:
            return None
        if month < 1 or month > 12:
            return None
        if day < 1 or day > 31:
            return None
        
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError):
        return None


def parse_time(text: str) -> Optional[str]:
    """
    Parse giờ từ HH:MM.
    Trả về HH:MM hoặc None.
    """
    if not text:
        return None
    
    text = text.strip().replace(".", ":").replace("h", ":").replace("H", ":")
    parts = text.split(":")
    
    if len(parts) != 2:
        return None
    
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        
        if hour < 0 or hour > 23:
            return None
        if minute < 0 or minute > 59:
            return None
        
        return f"{hour:02d}:{minute:02d}"
    except (ValueError, TypeError):
        return None


def parse_quantity(text: str) -> Optional[int]:
    """Parse số lượng (số nguyên dương)."""
    if not text:
        return None
    try:
        num = int(text.strip())
        if num <= 0:
            return None
        return num
    except (ValueError, TypeError):
        return None
