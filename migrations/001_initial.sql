-- Migration 001: Schema ban đầu
-- Bot Telegram Quản Lý Quán Bida
-- Mọi cột tiền: BIGINT (VND), không dùng REAL/FLOAT
-- Thời gian: TEXT ISO 8601 UTC, hiển thị theo Asia/Ho_Chi_Minh




-- ==================== CẤU HÌNH ====================
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by BIGINT,  -- telegram_id
    version BIGINT DEFAULT 1
);

CREATE TABLE IF NOT EXISTS config_history (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    changed_by BIGINT NOT NULL,
    version BIGINT NOT NULL
);

-- ==================== NGƯỜI DÙNG ====================
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'unknown',  -- owner/employee/unknown
    phone TEXT,
    is_active BIGINT NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by BIGINT,
    deactivated_at TEXT,
    deactivated_by BIGINT
);

-- ==================== MỨC LƯƠNG ====================
CREATE TABLE IF NOT EXISTS wage_rates (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    hourly_rate BIGINT NOT NULL,      -- VND/giờ
    effective_from TEXT NOT NULL,       -- Ngày hiệu lực ISO
    effective_to TEXT,                  -- NULL = hiện tại
    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL,
    note TEXT
);

-- ==================== KHUNG CA ====================
CREATE TABLE IF NOT EXISTS shift_templates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,                 -- Tên ca: "Ca sáng", "Ca tối"
    start_time TEXT NOT NULL,           -- HH:MM
    end_time TEXT NOT NULL,             -- HH:MM (có thể < start nếu qua 0h)
    crosses_midnight BIGINT DEFAULT 0, -- 1 nếu ca qua nửa đêm
    max_staff BIGINT NOT NULL DEFAULT 1,
    is_active BIGINT NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL
);

-- ==================== ĐĂNG KÝ CA ====================
CREATE TABLE IF NOT EXISTS shift_registrations (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    template_id BIGINT NOT NULL,
    shift_date TEXT NOT NULL,           -- YYYY-MM-DD ngày bắt đầu ca
    status TEXT NOT NULL DEFAULT 'pending',
    approved_at TEXT,
    approved_by BIGINT,
    reject_reason TEXT,
    cancelled_at TEXT,
    cancelled_by BIGINT,
    cancel_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(employee_id, template_id, shift_date)
);

-- ==================== ĐỔI CA ====================
CREATE TABLE IF NOT EXISTS shift_swaps (
    id SERIAL PRIMARY KEY,
    registration_id BIGINT NOT NULL,
    from_employee BIGINT NOT NULL,
    to_employee BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_receiver',
    receiver_responded_at TEXT,
    owner_decided_at TEXT,
    owner_decision_by BIGINT,
    reason TEXT,
    created_at TEXT NOT NULL
);

-- ==================== CHẤM CÔNG ====================
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    registration_id BIGINT,
    shift_date TEXT NOT NULL,
    scheduled_start TEXT,              -- Giờ lịch ISO
    scheduled_end TEXT,                -- Giờ lịch ISO

    -- Check-in
    checkin_flow_started_at TEXT,       -- Bấm nút check-in
    checkin_photo_received_at TEXT,     -- Máy chủ nhận ảnh (= giờ check-in)
    checkin_telegram_timestamp TEXT,    -- Giờ Telegram ghi trên ảnh
    checkin_time TEXT,                  -- Giờ check-in chính thức (= photo_received hoặc chủ duyệt)

    -- Check-out
    checkout_time TEXT,                -- Máy chủ nhận thao tác

    -- Tính công
    approved_minutes BIGINT,          -- Phút được công nhận (NULL = chưa tính)
    late_minutes BIGINT DEFAULT 0,
    early_leave_minutes BIGINT DEFAULT 0,
    overtime_minutes BIGINT DEFAULT 0,
    break_minutes BIGINT DEFAULT 0,   -- Nghỉ không lương

    status TEXT NOT NULL DEFAULT 'checked_in',
    is_exception BIGINT DEFAULT 0,    -- Ngoại lệ cần chủ xử lý
    exception_reason TEXT,
    wage_amount BIGINT,               -- Tiền công phiên này (VND)

    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL        -- Người tạo (NV hoặc chủ bù)
);

-- ==================== ẢNH CHECK-IN ====================
CREATE TABLE IF NOT EXISTS checkin_photos (
    id SERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL,
    employee_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,             -- Telegram file_id
    file_unique_id TEXT NOT NULL,      -- Telegram file_unique_id (phát hiện trùng)
    local_path TEXT,                   -- Đường dẫn lưu cục bộ
    telegram_timestamp TEXT,           -- Giờ Telegram
    server_received_at TEXT NOT NULL,  -- Máy chủ nhận
    is_duplicate BIGINT DEFAULT 0,   -- Phát hiện ảnh trùng
    created_at TEXT NOT NULL
);

-- ==================== YÊU CẦU SỬA CÔNG ====================
CREATE TABLE IF NOT EXISTS attendance_adjustments (
    id SERIAL PRIMARY KEY,
    session_id BIGINT,  -- NULL nếu tạo phiên mới
    employee_id BIGINT NOT NULL,
    shift_date TEXT NOT NULL,
    requested_checkin TEXT,
    requested_checkout TEXT,
    reason TEXT NOT NULL,
    evidence_photo_id TEXT,            -- Telegram file_id

    -- Xử lý
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by BIGINT,
    decided_at TEXT,
    decision_reason TEXT,
    approved_checkin TEXT,             -- Giờ chủ duyệt (có thể khác yêu cầu)
    approved_checkout TEXT,

    -- Giá trị gốc trước điều chỉnh
    original_checkin TEXT,
    original_checkout TEXT,
    original_minutes BIGINT,

    created_at TEXT NOT NULL,
    version BIGINT DEFAULT 1
);

-- ==================== CA QUỸ ====================
CREATE TABLE IF NOT EXISTS cash_shifts (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    shift_date TEXT NOT NULL,
    
    -- Mở ca
    opening_cash BIGINT NOT NULL,     -- Tiền mặt kiểm đếm đầu ca
    expected_opening BIGINT,          -- Tiền được bàn giao từ ca trước
    opening_diff BIGINT DEFAULT 0,    -- Chênh lệch đầu ca
    previous_shift_id BIGINT ,
    
    -- Doanh thu KiotViet
    total_bill_revenue BIGINT,        -- Tổng bill nhập từ KiotViet
    bill_report_start TEXT,            -- Mốc giờ đầu báo cáo
    bill_report_end TEXT,              -- Mốc giờ cuối báo cáo
    bill_report_photo_id TEXT,         -- Ảnh báo cáo KiotViet
    
    -- Kết ca
    closing_cash_counted BIGINT,      -- Tiền mặt kiểm đếm cuối ca
    expected_closing_cash BIGINT,     -- Tiền mặt kỳ vọng (bot tính)
    closing_diff BIGINT,              -- Chênh lệch cuối ca
    closing_diff_reason TEXT,
    closing_note TEXT,
    
    -- Xác nhận giao nhận
    handover_confirmed_by BIGINT,     -- Người ca sau xác nhận
    handover_confirmed_at TEXT,
    debt_ack_at TEXT,                   -- Thời điểm xem nợ bàn giao
    debt_ack_snapshot TEXT,             -- JSON snapshot danh sách nợ
    
    -- Bàn đang chơi
    active_tables_note TEXT,           -- Ghi chú bàn đang chơi
    
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    
    -- Snapshot khi khóa ca (JSON)
    closing_snapshot TEXT,
    snapshot_version BIGINT DEFAULT 1,
    
    created_at TEXT NOT NULL
);

-- ==================== GIAO DỊCH ====================


-- ==================== ẢNH GIAO DỊCH ====================
CREATE TABLE IF NOT EXISTS transaction_photos (
    id SERIAL PRIMARY KEY,
    transaction_id BIGINT NOT NULL ,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    local_path TEXT,
    photo_type TEXT,                   -- bill/transfer_proof/receipt/other
    note TEXT,
    uploaded_by BIGINT NOT NULL,
    created_at TEXT NOT NULL
);

-- ==================== KHÁCH HÀNG ====================
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    description TEXT,                  -- Mô tả nhận diện
    note TEXT,
    is_allowed_debt BIGINT DEFAULT 0, -- Được phép nợ
    debt_limit BIGINT,                -- Hạn mức nợ (VND)
    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL
);

-- ==================== CÔNG NỢ ====================
CREATE TABLE IF NOT EXISTS debt_records (
    id SERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    cash_shift_id BIGINT ,
    bill_code TEXT,                    -- Mã bill KiotViet
    table_name TEXT,                   -- Bàn nếu cần
    
    -- Thời gian
    bill_datetime TEXT,                -- Ngày giờ bill/phát sinh
    server_recorded_at TEXT NOT NULL,  -- Máy chủ ghi nhận
    is_retroactive BIGINT DEFAULT 0, -- Nhập hồi tố
    
    -- Tiền
    total_bill_amount BIGINT NOT NULL, -- Tổng bill
    cash_paid BIGINT DEFAULT 0,       -- Đã trả TM khi tạo nợ
    transfer_paid BIGINT DEFAULT 0,   -- Đã trả CK khi tạo nợ
    remaining_debt BIGINT NOT NULL,   -- Số còn nợ
    
    -- Hạng mục
    item_categories TEXT,              -- JSON: [{type, description, amount}]
    item_note TEXT,
    
    -- Hạn trả
    due_date TEXT,                     -- NULL = Chưa hẹn
    
    -- Trạng thái
    status TEXT NOT NULL DEFAULT 'active',
    is_over_limit BIGINT DEFAULT 0,   -- Vượt quyền cho nợ
    exception_note TEXT,
    
    -- Audit
    recorded_by BIGINT NOT NULL,
    shift_id BIGINT,                  -- Ca làm liên quan
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ==================== THANH TOÁN NỢ ====================
CREATE TABLE IF NOT EXISTS debt_payments (
    id SERIAL PRIMARY KEY,
    debt_record_id BIGINT NOT NULL ,
    amount BIGINT NOT NULL,           -- VND
    payment_method TEXT NOT NULL,      -- cash/transfer/offset (đối trừ lương)
    cash_shift_id BIGINT ,
    transaction_id BIGINT ,
    
    -- Xác minh
    transfer_verified BIGINT DEFAULT 0,
    verified_by BIGINT,
    verified_at TEXT,
    
    collected_by BIGINT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    
    -- Idempotency
    idempotency_key TEXT UNIQUE
);

-- ==================== ẢNH NỢ/CHỨNG TỪ ====================
CREATE TABLE IF NOT EXISTS debt_photos (
    id SERIAL PRIMARY KEY,
    debt_record_id BIGINT NOT NULL ,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    local_path TEXT,
    photo_type TEXT NOT NULL DEFAULT 'bill',  -- bill/transfer_proof/other
    note TEXT,
    uploaded_by BIGINT NOT NULL,
    created_at TEXT NOT NULL
);

-- ==================== BẢNG LƯƠNG ====================
CREATE TABLE IF NOT EXISTS payroll (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    period_year BIGINT NOT NULL,
    period_month BIGINT NOT NULL,
    
    -- Công
    total_approved_minutes BIGINT DEFAULT 0,
    total_sessions BIGINT DEFAULT 0,
    
    -- Tiền
    base_wage BIGINT DEFAULT 0,       -- Tiền công = phút × rate/60
    bonus BIGINT DEFAULT 0,           -- Thưởng/phụ cấp
    total_advances BIGINT DEFAULT 0,  -- Tổng ứng đã giao chưa đối trừ
    total_consumables BIGINT DEFAULT 0, -- Đồ dùng chưa trả
    deductions BIGINT DEFAULT 0,      -- Khấu trừ hợp lệ
    already_paid BIGINT DEFAULT 0,    -- Đã thanh toán trong kỳ
    net_pay BIGINT DEFAULT 0,         -- Còn thanh toán
    
    -- Trạng thái
    status TEXT NOT NULL DEFAULT 'draft',
    has_unresolved BIGINT DEFAULT 0,  -- Còn công/giá chưa xử lý
    unresolved_details TEXT,           -- JSON mô tả
    
    -- Snapshot khi khóa
    snapshot TEXT,                      -- JSON toàn bộ chi tiết
    snapshot_version BIGINT DEFAULT 1,
    
    -- Xác nhận
    employee_confirmed_at TEXT,
    employee_dispute_reason TEXT,
    owner_approved_at TEXT,
    owner_approved_by BIGINT,
    paid_at TEXT,
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    
    UNIQUE(employee_id, period_year, period_month)
);

-- ==================== CHI TIẾT CÔNG TRONG BẢNG LƯƠNG ====================
CREATE TABLE IF NOT EXISTS payroll_entries (
    id SERIAL PRIMARY KEY,
    payroll_id BIGINT NOT NULL ,
    session_id BIGINT NOT NULL,
    minutes BIGINT NOT NULL,
    wage_rate BIGINT NOT NULL,        -- Mức lương áp dụng
    amount BIGINT NOT NULL,           -- Tiền = minutes * rate / 60
    note TEXT
);

-- ==================== ỨNG LƯƠNG ====================
CREATE TABLE IF NOT EXISTS salary_advances (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    reason TEXT,
    
    -- Trạng thái
    status TEXT NOT NULL DEFAULT 'requested',
    
    -- Duyệt
    approved_by BIGINT,
    approved_at TEXT,
    reject_reason TEXT,
    
    -- Giao tiền
    delivered_at TEXT,
    delivered_by BIGINT,
    payment_source TEXT,               -- cash/transfer/other
    cash_shift_id BIGINT ,
    transaction_id BIGINT ,
    
    -- Đối trừ
    settled_in_payroll_id BIGINT ,
    settled_at TEXT,
    settled_amount BIGINT,            -- Số đã đối trừ
    remaining_unsettled BIGINT,       -- Số chưa đối trừ
    
    evidence_note TEXT,
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ==================== ĐỒ NHÂN VIÊN DÙNG ====================
CREATE TABLE IF NOT EXISTS consumable_items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,                     -- nước/thuốc/đồ ăn/khác
    unit TEXT NOT NULL,                -- chai/lon/ly/điếu/gói/cái
    pack_unit TEXT,                    -- gói/bao (đơn vị lớn)
    pack_size BIGINT,                 -- Số đơn vị trong gói (VD: 20 điếu/bao)
    is_active BIGINT DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS consumable_usage (
    id SERIAL PRIMARY KEY,
    employee_id BIGINT NOT NULL,
    item_id BIGINT NOT NULL,
    quantity BIGINT NOT NULL,          -- Số đơn vị cơ sở
    usage_datetime TEXT NOT NULL,       -- Thời gian thực dùng
    recorded_at TEXT NOT NULL,          -- Thời gian ghi
    is_late_entry BIGINT DEFAULT 0,   -- Nhập muộn
    cash_shift_id BIGINT ,
    shift_registration_id BIGINT,
    photo_id TEXT,                     -- Ảnh tùy chọn
    note TEXT,
    
    -- Thanh toán
    is_free BIGINT DEFAULT 0,         -- Miễn phí (chủ xác nhận)
    free_approved_by BIGINT,
    paid_amount BIGINT DEFAULT 0,     -- Đã trả (VND)
    paid_at TEXT,
    
    -- Liên kết kho
    stock_issue_id BIGINT,
    
    -- Audit
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_adjusted BIGINT DEFAULT 0,
    adjustment_note TEXT
);

-- ==================== GIÁ ĐỒ DÙNG CUỐI THÁNG ====================
CREATE TABLE IF NOT EXISTS consumable_prices (
    id SERIAL PRIMARY KEY,
    item_id BIGINT NOT NULL,
    period_year BIGINT NOT NULL,
    period_month BIGINT NOT NULL,
    unit_price BIGINT NOT NULL,       -- VND/đơn vị cơ sở
    set_by BIGINT NOT NULL,
    set_at TEXT NOT NULL,
    copied_from_previous BIGINT DEFAULT 0,
    note TEXT,
    UNIQUE(item_id, period_year, period_month)
);

-- Ngoại lệ giá theo nhân viên
CREATE TABLE IF NOT EXISTS consumable_price_exceptions (
    id SERIAL PRIMARY KEY,
    price_id BIGINT NOT NULL,
    employee_id BIGINT NOT NULL,
    unit_price BIGINT NOT NULL,
    reason TEXT NOT NULL,
    set_by BIGINT NOT NULL,
    set_at TEXT NOT NULL
);

-- ==================== KHO HÀNG ====================
CREATE TABLE IF NOT EXISTS inventory_items (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE,                  -- Mã sản phẩm
    name TEXT NOT NULL,
    category TEXT,                     -- nước/thuốc/đồ ăn/vật tư
    base_unit TEXT NOT NULL,           -- lon/chai/gói/cái
    pack_unit TEXT,                    -- thùng/lốc
    pack_size BIGINT,                 -- Số đơn vị/thùng
    low_stock_threshold BIGINT,       -- Ngưỡng tồn thấp
    
    -- Giá nhập (chỉ chủ/người được cấp quyền xem)
    last_import_price BIGINT,
    
    -- Liên kết với consumable_items
    consumable_item_id BIGINT,
    
    -- Mã KiotViet nếu ánh xạ
    kiotviet_code TEXT,
    
    is_active BIGINT DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by BIGINT NOT NULL
);

-- Tồn kho đầu kỳ
CREATE TABLE IF NOT EXISTS initial_stock (
    id SERIAL PRIMARY KEY,
    item_id BIGINT NOT NULL,
    quantity BIGINT NOT NULL,         -- Đơn vị cơ sở
    effective_date TEXT NOT NULL,
    recorded_by BIGINT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

-- ==================== PHIẾU NHẬP HÀNG ====================
CREATE TABLE IF NOT EXISTS stock_receipts (
    id SERIAL PRIMARY KEY,
    receipt_code TEXT UNIQUE,          -- Mã phiếu
    supplier_name TEXT,
    received_date TEXT NOT NULL,       -- Ngày nhận thực tế
    recorded_at TEXT NOT NULL,         -- Ngày ghi
    received_by BIGINT NOT NULL,
    cash_shift_id BIGINT ,
    
    -- Thanh toán
    total_amount BIGINT DEFAULT 0,
    discount BIGINT DEFAULT 0,
    shipping_fee BIGINT DEFAULT 0,
    net_amount BIGINT DEFAULT 0,      -- total - discount + shipping
    payment_status TEXT DEFAULT 'unpaid', -- paid_cash/paid_transfer/owner_paid/unpaid/partial
    payment_source TEXT,
    transaction_id BIGINT ,
    
    -- Ảnh hóa đơn
    invoice_photo_id TEXT,
    invoice_photo_path TEXT,
    
    status TEXT NOT NULL DEFAULT 'draft',
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    
    -- Idempotency
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id SERIAL PRIMARY KEY,
    receipt_id BIGINT NOT NULL ,
    item_id BIGINT NOT NULL,
    quantity_ordered BIGINT,          -- Số đặt
    quantity_received BIGINT NOT NULL, -- Số thực nhận (đơn vị cơ sở)
    unit_used TEXT,                    -- Đơn vị nhập (thùng/lon)
    raw_quantity REAL,                 -- Số lượng đơn vị nhập (VD: 2 thùng)
    conversion_factor BIGINT,         -- Hệ số quy đổi
    unit_price BIGINT,               -- Giá/đơn vị cơ sở
    line_total BIGINT,               -- quantity_received × unit_price
    note TEXT
);

-- ==================== XUẤT KHO ====================
CREATE TABLE IF NOT EXISTS stock_issues (
    id SERIAL PRIMARY KEY,
    item_id BIGINT NOT NULL,
    quantity BIGINT NOT NULL,         -- Đơn vị cơ sở
    issue_type TEXT NOT NULL,          -- sale_kiotviet/employee_use/gift/damage/loss/return_supplier/other
    
    -- Nguồn
    consumable_usage_id BIGINT,
    import_batch_id TEXT,             -- Mã đợt import KiotViet
    
    reason TEXT,
    approved_by BIGINT,
    evidence_photo_id TEXT,
    
    recorded_by BIGINT NOT NULL,
    cash_shift_id BIGINT ,
    created_at TEXT NOT NULL
);

-- ==================== IMPORT BÁN HÀNG KIOTVIET ====================
CREATE TABLE IF NOT EXISTS sales_imports (
    id SERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL UNIQUE,     -- Mã đợt import
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    source_description TEXT,           -- "Báo cáo KiotViet ca 1 ngày 30/09"
    imported_by BIGINT NOT NULL,
    import_method TEXT,                -- manual/csv
    status TEXT DEFAULT 'active',      -- active/superseded/cancelled
    superseded_by BIGINT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_import_items (
    id SERIAL PRIMARY KEY,
    import_id BIGINT NOT NULL,
    item_id BIGINT NOT NULL,
    quantity_sold BIGINT NOT NULL,
    quantity_returned BIGINT DEFAULT 0,
    net_quantity BIGINT NOT NULL,      -- sold - returned
    note TEXT
);

-- ==================== KIỂM KÊ ====================
CREATE TABLE IF NOT EXISTS inventory_checks (
    id SERIAL PRIMARY KEY,
    check_date TEXT NOT NULL,
    check_time TEXT NOT NULL,          -- Mốc kiểm kê
    cash_shift_id BIGINT ,
    checked_by BIGINT NOT NULL,
    
    status TEXT NOT NULL DEFAULT 'draft',
    has_pending_sales BIGINT DEFAULT 0, -- Chưa nhập đủ KV
    sales_import_id BIGINT,
    
    snapshot TEXT,                      -- JSON snapshot tồn sổ tại mốc
    note TEXT,
    
    approved_by BIGINT,
    approved_at TEXT,
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_check_items (
    id SERIAL PRIMARY KEY,
    check_id BIGINT NOT NULL,
    item_id BIGINT NOT NULL,
    book_quantity BIGINT NOT NULL,    -- Tồn sổ
    actual_quantity BIGINT NOT NULL,  -- Tồn thực tế
    difference BIGINT NOT NULL,      -- actual - book
    unit_used TEXT,                    -- Đơn vị đếm
    note TEXT,
    
    -- Điều chỉnh
    adjustment_approved BIGINT DEFAULT 0,
    adjustment_approved_by BIGINT,
    adjustment_approved_at TEXT,
    adjustment_reason TEXT
);

-- ==================== DOANH THU NGÀY ====================
CREATE TABLE IF NOT EXISTS daily_revenue (
    id SERIAL PRIMARY KEY,
    business_date TEXT NOT NULL UNIQUE, -- Ngày kinh doanh
    period_start TEXT NOT NULL,         -- Mốc giờ đầu
    period_end TEXT NOT NULL,           -- Mốc giờ cuối
    
    -- Nguồn: tổng ngày hoặc cộng ca
    source_type TEXT NOT NULL,          -- daily_total/sum_of_shifts
    total_bill_revenue BIGINT,        -- Tổng doanh thu bill
    report_photo_id TEXT,
    
    -- Trạng thái
    status TEXT NOT NULL DEFAULT 'draft', -- draft/partial/complete/reconciled
    has_open_shifts BIGINT DEFAULT 0,
    missing_data_note TEXT,
    
    entered_by BIGINT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version BIGINT DEFAULT 1
);

-- ==================== LỊCH SỬ THAO TÁC ====================
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    actor_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,          -- Bảng/loại đối tượng
    entity_id BIGINT,
    old_data TEXT,                      -- JSON trước
    new_data TEXT,                      -- JSON sau
    reason TEXT,
    ip_info TEXT,
    created_at TEXT NOT NULL
);

-- ==================== HÀNG ĐỢI THÔNG BÁO ====================
CREATE TABLE IF NOT EXISTS notification_queue (
    id SERIAL PRIMARY KEY,
    recipient_id BIGINT NOT NULL,
    message TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    reference_type TEXT,
    reference_id BIGINT,
    
    -- Trạng thái gửi
    status TEXT DEFAULT 'pending',     -- pending/sent/failed/suppressed
    retry_count BIGINT DEFAULT 0,
    max_retries BIGINT DEFAULT 3,
    last_error TEXT,
    
    -- Chống lặp
    dedup_key TEXT,
    
    created_at TEXT NOT NULL,
    sent_at TEXT,
    next_retry_at TEXT
);

-- ==================== TRẠNG THÁI NHẬP DỞ ====================
CREATE TABLE IF NOT EXISTS user_states (
    telegram_id BIGINT PRIMARY KEY,
    state_key TEXT,                    -- Tên luồng đang nhập
    state_data TEXT,                   -- JSON dữ liệu tạm
    updated_at TEXT NOT NULL
);

-- ==================== INDEX ====================
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    cash_shift_id BIGINT ,  -- NULL nếu ngoài quầy
    type TEXT NOT NULL,
    amount BIGINT NOT NULL,           -- VND, luôn dương
    bill_code TEXT,                    -- Mã bill KiotViet
    description TEXT,
    category TEXT,                     -- Nhóm chi
    payment_source TEXT,               -- cash/transfer/other
    
    -- Liên kết
    debt_record_id BIGINT ,
    debt_payment_id BIGINT ,
    advance_id BIGINT ,
    payroll_id BIGINT ,
    receipt_id BIGINT ,
    
    -- Idempotency
    idempotency_key TEXT UNIQUE,
    
    -- Audit
    recorded_by BIGINT NOT NULL,
    verified BIGINT DEFAULT 0,
    verified_by BIGINT,
    verified_at TEXT,
    
    created_at TEXT NOT NULL,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_shift_reg_employee ON shift_registrations(employee_id, shift_date);
CREATE INDEX IF NOT EXISTS idx_shift_reg_date ON shift_registrations(shift_date, status);
CREATE INDEX IF NOT EXISTS idx_attendance_employee ON attendance_sessions(employee_id, shift_date);
CREATE INDEX IF NOT EXISTS idx_attendance_status ON attendance_sessions(status);
CREATE INDEX IF NOT EXISTS idx_cash_shift_status ON cash_shifts(status, shift_date);
CREATE INDEX IF NOT EXISTS idx_transactions_shift ON transactions(cash_shift_id, type);
CREATE INDEX IF NOT EXISTS idx_transactions_bill ON transactions(bill_code);
CREATE INDEX IF NOT EXISTS idx_debt_customer ON debt_records(customer_id, status);
CREATE INDEX IF NOT EXISTS idx_debt_status ON debt_records(status);
CREATE INDEX IF NOT EXISTS idx_debt_due ON debt_records(due_date, status);
CREATE INDEX IF NOT EXISTS idx_payroll_period ON payroll(employee_id, period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_consumable_usage_emp ON consumable_usage(employee_id, usage_datetime);
CREATE INDEX IF NOT EXISTS idx_inventory_item_code ON inventory_items(code);
CREATE INDEX IF NOT EXISTS idx_stock_issues_item ON stock_issues(item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_notification_status ON notification_queue(status, next_retry_at);
