"""
Hằng số và Enum cho toàn bộ ứng dụng.
Mọi giá trị tiền tệ là số nguyên VND.
"""

from enum import Enum, IntEnum


# ==================== ROLES ====================
class Role(str, Enum):
    OWNER = "owner"
    EMPLOYEE = "employee"
    UNKNOWN = "unknown"


# ==================== SHIFT ====================
class ShiftRegStatus(str, Enum):
    PENDING = "pending"           # Chờ duyệt
    APPROVED = "approved"         # Đã duyệt
    REJECTED = "rejected"         # Từ chối
    CANCELLED = "cancelled"       # Đã hủy


class SwapStatus(str, Enum):
    PENDING_RECEIVER = "pending_receiver"  # Chờ người nhận đồng ý
    PENDING_OWNER = "pending_owner"        # Chờ chủ duyệt
    APPROVED = "approved"
    REJECTED = "rejected"


# ==================== ATTENDANCE ====================
class AttendanceStatus(str, Enum):
    CHECKED_IN = "checked_in"       # Đang làm
    CHECKED_OUT = "checked_out"     # Đã ra về
    EXCEPTION = "exception"         # Ngoại lệ chờ xử lý
    OWNER_CREATED = "owner_created" # Chủ tạo bù


class AdjustmentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"  # Chủ chỉnh giờ khác yêu cầu


# ==================== CASH SHIFT ====================
class CashShiftStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    PENDING_REVIEW = "pending_review"  # Chờ chủ xem xét


# ==================== TRANSACTIONS ====================
class TransactionType(str, Enum):
    BANK_TRANSFER = "bank_transfer"        # Chuyển khoản thanh toán bill
    EXPENSE_CASH = "expense_cash"          # Chi tiền mặt
    EXPENSE_TRANSFER = "expense_transfer"  # Chi chuyển khoản
    CASH_TO_OWNER = "cash_to_owner"        # Giao tiền cho chủ
    CASH_WITHDRAWAL = "cash_withdrawal"    # Rút tiền quỹ
    FUND_ADD = "fund_add"                  # Bổ sung quỹ
    SALARY_ADVANCE_CASH = "salary_advance_cash"  # Ứng lương tiền mặt
    SALARY_PAY_CASH = "salary_pay_cash"    # Trả lương tiền mặt
    DEBT_COLLECT_CASH = "debt_collect_cash"      # Thu nợ tiền mặt
    DEBT_COLLECT_TRANSFER = "debt_collect_transfer"  # Thu nợ CK
    EMPLOYEE_ITEM_CASH = "employee_item_cash"  # Thu tiền đồ NV
    IMPORT_PAY_CASH = "import_pay_cash"    # Thanh toán nhập hàng TM
    REFUND_CASH = "refund_cash"            # Hoàn tiền NCC vào quầy
    OTHER_CASH_IN = "other_cash_in"        # Tiền mặt vào khác
    OTHER_CASH_OUT = "other_cash_out"      # Tiền mặt ra khác


# Giao dịch làm TĂNG tiền quầy
CASH_IN_TYPES = {
    TransactionType.DEBT_COLLECT_CASH,
    TransactionType.FUND_ADD,
    TransactionType.EMPLOYEE_ITEM_CASH,
    TransactionType.REFUND_CASH,
    TransactionType.OTHER_CASH_IN,
}

# Giao dịch làm GIẢM tiền quầy
CASH_OUT_TYPES = {
    TransactionType.EXPENSE_CASH,
    TransactionType.CASH_TO_OWNER,
    TransactionType.CASH_WITHDRAWAL,
    TransactionType.SALARY_ADVANCE_CASH,
    TransactionType.SALARY_PAY_CASH,
    TransactionType.IMPORT_PAY_CASH,
    TransactionType.OTHER_CASH_OUT,
}

# Giao dịch KHÔNG ảnh hưởng tiền quầy
NON_CASH_TYPES = {
    TransactionType.BANK_TRANSFER,
    TransactionType.EXPENSE_TRANSFER,
    TransactionType.DEBT_COLLECT_TRANSFER,
}


# ==================== DEBT ====================
class DebtStatus(str, Enum):
    ACTIVE = "active"           # Còn nợ
    PAID = "paid"               # Đã trả hết
    PARTIAL = "partial"         # Trả một phần
    OVERDUE = "overdue"         # Quá hạn
    CANCELLED = "cancelled"     # Hủy có lý do


class DebtPhotoType(str, Enum):
    BILL = "bill"
    TRANSFER_PROOF = "transfer_proof"
    OTHER = "other"


# ==================== SALARY / PAYROLL ====================
class PayrollStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"             # Chờ NV xác nhận
    EMPLOYEE_CONFIRMED = "confirmed"  # NV đã xác nhận
    EMPLOYEE_DISPUTED = "disputed"    # NV báo sai
    APPROVED = "approved"           # Chủ duyệt
    PAID = "paid"                   # Đã thanh toán


class AdvanceStatus(str, Enum):
    REQUESTED = "requested"     # NV đề nghị
    APPROVED = "approved"       # Chủ duyệt
    REJECTED = "rejected"       # Từ chối
    DELIVERED = "delivered"     # Đã giao tiền
    SETTLED = "settled"         # Đã đối trừ


# ==================== INVENTORY ====================
class ReceiptStatus(str, Enum):
    DRAFT = "draft"
    RECEIVED = "received"           # Đã nhận hàng
    PENDING_REVIEW = "pending_review"  # Chờ chủ duyệt


class StockIssueType(str, Enum):
    SALE_KIOTVIET = "sale_kiotviet"  # Bán qua KiotViet
    EMPLOYEE_USE = "employee_use"     # NV dùng
    GIFT = "gift"                     # Quà tặng
    DAMAGE = "damage"                 # Hỏng/hết hạn
    LOSS = "loss"                     # Mất mát
    RETURN_SUPPLIER = "return_supplier"  # Trả NCC
    OTHER = "other"


class InventoryCheckStatus(str, Enum):
    DRAFT = "draft"
    PENDING_COMPARE = "pending_compare"  # Chờ đối chiếu KiotViet
    COMPARED = "compared"                # Đã đối chiếu
    ADJUSTMENT_PENDING = "adjustment_pending"  # Chênh lệch chờ duyệt
    APPROVED = "approved"                # Đã duyệt


# ==================== EXPENSE CATEGORIES ====================
EXPENSE_CATEGORIES = [
    "Mua đồ/vật tư",
    "Điện/nước",
    "Sửa chữa/bảo trì",
    "Ăn uống",
    "Vận chuyển",
    "Khác",
]


# ==================== CALLBACK PREFIXES ====================
# Dùng prefix ngắn để tránh vượt 64 byte callback_data
class CB:
    """Callback data prefixes."""
    # Menu
    MAIN_MENU = "mm"
    OWNER_MENU = "om"
    DASHBOARD_TODAY = "dt"

    # Auth
    AUTH_APPROVE = "aa"
    AUTH_REVOKE = "ar"
    AUTH_LIST = "al"

    # Shift
    SHIFT_REG = "sr"
    SHIFT_APPROVE = "sa"
    SHIFT_REJECT = "sj"
    SHIFT_SWAP = "ss"
    SHIFT_VIEW = "sv"

    # Attendance
    ATT_CHECKIN = "ci"
    ATT_CHECKOUT = "co"
    ATT_MY_LOG = "ml"
    ATT_ADJUST_REQ = "aq"
    ATT_ADJUST_APPROVE = "ap"
    ATT_ADJUST_REJECT = "aj"

    # Cash shift
    CS_OPEN = "cso"
    CS_CLOSE = "csc"
    CS_VIEW = "csv"

    # Transaction
    TXN_TRANSFER = "tt"
    TXN_EXPENSE = "te"
    TXN_CASH_OWNER = "tc"
    TXN_FUND = "tf"

    # Debt
    DEBT_NEW = "dn"
    DEBT_COLLECT = "dc"
    DEBT_VIEW = "dv"
    DEBT_LIST = "dl"
    DEBT_ACK = "da"  # Đã xem nợ bàn giao

    # Salary
    SAL_VIEW = "slv"
    SAL_ADVANCE = "sla"
    SAL_CALC = "slc"
    SAL_APPROVE = "slp"

    # Consumables
    CON_LOG = "cl"
    CON_VIEW = "cv"
    CON_PRICE = "cp"

    # Inventory
    INV_VIEW = "iv"
    INV_IMPORT = "ii"
    INV_CHECK = "ic"
    INV_LOSS = "il"

    # Reports
    RPT_DAY = "rd"
    RPT_WEEK = "rw"
    RPT_MONTH = "rm"

    # Admin
    ADM_RULES = "adr"
    ADM_CONFIG = "adc"
    ADM_BACKUP = "adb"

    # General
    BACK = "back"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    PAGE = "pg"


# ==================== LIMITS ====================
MAX_PHOTO_WAIT_SECONDS = 300    # 5 phút chờ ảnh check-in
MAX_SESSION_HOURS = 16          # Ngưỡng phiên quá dài
DEBT_BANNER_MAX_ITEMS = 5       # Số khoản nợ hiện trên banner
PAGE_SIZE = 10                  # Phân trang
