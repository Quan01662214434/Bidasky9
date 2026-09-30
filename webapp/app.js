/**
 * BIDA SKY9 — Dashboard App
 * Giao diện quản lý chủ quán bida.
 */

// ─── Telegram WebApp ────────────────────────
let tg = window.Telegram?.WebApp;
if (tg) { tg.expand(); tg.ready(); }

// ─── Globals ────────────────────────────────
let currentPage = 'overview';
let currentDate = null; // null = hôm nay

// ─── Helpers ────────────────────────────────

function fmtMoney(amount) {
    if (amount === null || amount === undefined) return '0đ';
    const n = Math.round(Number(amount));
    return n.toLocaleString('vi-VN') + 'đ';
}

function fmtTime(isoStr) {
    if (!isoStr) return '—';
    try {
        const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
        return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch { return isoStr; }
}

function fmtDate(isoStr) {
    if (!isoStr) return '—';
    try {
        const d = new Date(isoStr);
        return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
    } catch { return isoStr; }
}

function fmtRelative(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
        const now = new Date();
        const diff = Math.floor((now - d) / 1000);
        if (diff < 60) return 'vừa xong';
        if (diff < 3600) return Math.floor(diff / 60) + ' phút trước';
        if (diff < 86400) return Math.floor(diff / 3600) + ' giờ trước';
        return Math.floor(diff / 86400) + ' ngày trước';
    } catch { return ''; }
}

function todayStr() {
    const n = new Date();
    // Adjust for VN timezone (UTC+7)
    const vn = new Date(n.getTime() + 7 * 3600 * 1000);
    return vn.toISOString().slice(0, 10);
}

function yesterdayStr() {
    const n = new Date();
    const vn = new Date(n.getTime() + 7 * 3600 * 1000 - 86400000);
    return vn.toISOString().slice(0, 10);
}

// ─── API Fetch with Error Handling ──────────

async function apiFetch(url, options = {}) {
    const headers = options.headers || {};
    // Add auth token if available (from Telegram WebApp or dev fallback)
    const initData = window.Telegram?.WebApp?.initData || '7496977545';
    headers['Authorization'] = `Bearer ${initData}`;
    
    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
            const err = await res.json();
            if (err.detail) msg = err.detail;
        } catch(e) {}
        throw new Error(msg);
    }
    return res.json();
}

// ─── Navigation ─────────────────────────────

function navigateTo(page) {
    currentPage = page;
    // Update sidebar
    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.page === page);
    });
    // Update mobile nav
    document.querySelectorAll('.mob-btn').forEach(el => {
        el.classList.toggle('active', el.dataset.page === page);
    });
    // Show page
    document.querySelectorAll('.page').forEach(el => {
        el.classList.toggle('active', el.id === 'page-' + page);
    });
    // Close more menu
    closeMoreMenu();
    // Load page data
    loadPageData(page);
}

function closeMoreMenu() {
    document.getElementById('more-overlay').style.display = 'none';
    document.getElementById('more-menu').style.display = 'none';
}

function toggleMoreMenu() {
    const overlay = document.getElementById('more-overlay');
    const menu = document.getElementById('more-menu');
    if (menu.style.display === 'none') {
        overlay.style.display = 'block';
        menu.style.display = 'block';
    } else {
        closeMoreMenu();
    }
}

// ─── Page Data Loaders ──────────────────────

function loadPageData(page) {
    switch (page) {
        case 'overview': loadOverview(); break;
        case 'shifts':   loadCashShifts(); break;
        case 'cashflow': loadCashflow(); break;
        case 'debts':    loadDebts(); break;
        case 'inventory': loadInventory(); break;
        case 'employees': loadEmployees(); break;
    }
}

// ─── Overview Page ──────────────────────────

async function loadOverview() {
    const dateParam = currentDate ? `?date=${currentDate}` : '';
    const refreshBtn = document.getElementById('btn-refresh');
    refreshBtn.classList.add('spinning');

    try {
        const [overview, shifts, activity, lowStock] = await Promise.all([
            apiFetch('/api/v2/overview' + dateParam),
            apiFetch('/api/v2/shifts-today' + dateParam),
            apiFetch('/api/v2/activity?limit=10'),
            apiFetch('/api/v2/low-stock'),
        ]);

        renderOverviewStats(overview);
        renderPendingActions(overview.pending);
        renderShiftsTable(shifts.shifts);
        renderActivityFeed(activity.activities);
        renderLowStock(lowStock.items);
    } catch (err) {
        console.error('loadOverview error:', err);
        document.getElementById('val-revenue').textContent = 'Lỗi';
    } finally {
        refreshBtn.classList.remove('spinning');
    }
}

function renderOverviewStats(data) {
    document.getElementById('update-time').textContent = 'Cập nhật: ' + data.updated_at;

    // Revenue
    document.getElementById('val-revenue').textContent = fmtMoney(data.revenue.bill_total);
    document.getElementById('sub-revenue').textContent =
        `Ngày ${fmtDate(data.date)} · CK: ${fmtMoney(data.revenue.transfer_total)}`;

    // Debt
    const debtAmt = data.debt.total_amount;
    const valDebt = document.getElementById('val-debt');
    valDebt.textContent = fmtMoney(debtAmt);
    valDebt.classList.toggle('danger', debtAmt > 0);
    document.getElementById('sub-debt').textContent =
        `${data.debt.record_count} khoản · ${data.debt.customer_count} khách`;

    // Staff
    document.getElementById('val-staff').textContent =
        `${data.staff.active_count}/${data.staff.total_count}`;
    document.getElementById('sub-staff').textContent = 'đang làm việc';

    // Pending
    const pendingCount = data.pending.total;
    const valPending = document.getElementById('val-pending');
    valPending.textContent = pendingCount;
    valPending.style.color = pendingCount > 0 ? 'var(--warning)' : 'var(--success)';
    document.getElementById('sub-pending').textContent =
        pendingCount > 0 ? 'việc cần xử lý' : 'không có gì cần xử lý';
}

function renderPendingActions(pending) {
    const section = document.getElementById('pending-section');
    const list = document.getElementById('pending-list');

    if (!pending.items || pending.items.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = '';
    list.innerHTML = '';

    const iconSvgs = {
        handshake: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 17a4 4 0 01-8 0c0-2 2-3 2-3"/><path d="M22 17a4 4 0 01-8 0c0-2 2-3 2-3"/><path d="M7 7h10l4 4v2H3V11l4-4z"/></svg>',
        edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
        'alert-triangle': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        package: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>',
        clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
    };

    const pageMap = {
        handover: 'shifts',
        adjustment: 'shifts',
        fund_diff: 'shifts',
        inventory_diff: 'inventory',
        overdue_debt: 'debts',
    };

    pending.items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'pending-item';
        el.innerHTML = `
            <div class="pending-icon ${item.severity}">${iconSvgs[item.icon] || iconSvgs['alert-triangle']}</div>
            <span class="pending-text">${item.label}</span>
            <button class="pending-action" data-go="${pageMap[item.type] || 'overview'}">Xem →</button>
        `;
        el.querySelector('.pending-action').addEventListener('click', (e) => {
            navigateTo(e.target.dataset.go);
        });
        list.appendChild(el);
    });
}

function renderShiftsTable(shifts) {
    const tbody = document.getElementById('shifts-tbody');

    if (!shifts || shifts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="state-empty">Chưa có dữ liệu ca hôm nay</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    shifts.forEach(s => {
        const statusMap = {
            checked_in: { label: 'Đang làm', cls: 'badge-success' },
            checked_out: { label: 'Đã ra', cls: 'badge-neutral' },
            completed: { label: 'Hoàn thành', cls: 'badge-info' },
        };
        const st = statusMap[s.status] || { label: s.status, cls: 'badge-neutral' };

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${s.employee}</strong></td>
            <td>${s.shift_name}</td>
            <td>${fmtTime(s.checkin_time)}</td>
            <td>${fmtTime(s.checkout_time)}</td>
            <td><span class="badge ${st.cls}">${st.label}</span>${s.late_minutes > 0 ? ` <span class="badge badge-warning" style="margin-left:4px">Trễ ${s.late_minutes}p</span>` : ''}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderActivityFeed(activities) {
    const container = document.getElementById('activity-list');

    if (!activities || activities.length === 0) {
        container.innerHTML = '<div class="state-empty">Chưa có hoạt động nào được ghi nhận</div>';
        return;
    }

    container.innerHTML = '';
    activities.slice(0, 8).forEach(a => {
        const el = document.createElement('div');
        el.className = 'activity-item';

        const actionLabels = {
            checkin: 'đã check-in',
            checkout: 'đã check-out',
            open_shift: 'mở ca quỹ',
            close_shift: 'đóng ca quỹ',
            create_debt: 'ghi nợ mới',
            collect_debt: 'thu nợ',
            add_expense: 'ghi chi',
            add_transfer: 'ghi chuyển khoản',
            import_stock: 'nhập hàng',
        };

        el.innerHTML = `
            <div class="activity-dot"></div>
            <div class="activity-body">
                <span class="activity-actor">${a.actor}</span>
                <span class="activity-desc"> ${actionLabels[a.action] || a.action}</span>
                ${a.entity_type ? ` <span class="activity-desc">(${a.entity_type})</span>` : ''}
                <div class="activity-time">${fmtRelative(a.time)}</div>
            </div>
        `;
        container.appendChild(el);
    });
}

function renderLowStock(items) {
    const container = document.getElementById('lowstock-list');

    if (!items || items.length === 0) {
        container.innerHTML = '<div class="state-empty">Tất cả hàng hoá đang đủ số lượng 👍</div>';
        return;
    }

    container.innerHTML = '';
    items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'lowstock-item';
        el.innerHTML = `
            <span class="lowstock-name">${item.name}</span>
            <span class="lowstock-qty">${item.current} ${item.unit}</span>
        `;
        container.appendChild(el);
    });
}

// ─── Cash Shifts Page ───────────────────────

async function loadCashShifts() {
    const dateParam = currentDate ? `?date=${currentDate}` : '';
    const tbody = document.getElementById('cashshift-tbody');
    tbody.innerHTML = '<tr><td colspan="6" class="state-loading">Đang tải...</td></tr>';

    try {
        const data = await apiFetch('/api/v2/cash-shifts' + dateParam);
        if (!data.shifts || data.shifts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="state-empty">Chưa có ca quỹ trong ngày này</td></tr>';
            return;
        }
        tbody.innerHTML = '';
        data.shifts.forEach(s => {
            const statusMap = {
                open: { label: 'Đang mở', cls: 'badge-success' },
                closed: { label: 'Đã đóng', cls: 'badge-neutral' },
                pending_review: { label: 'Chờ duyệt', cls: 'badge-warning' },
            };
            const st = statusMap[s.status] || { label: s.status, cls: 'badge-neutral' };

            let diffHtml = '—';
            if (s.diff !== null && s.diff !== undefined) {
                const cls = s.diff === 0 ? '' : (s.diff > 0 ? 'money-pos' : 'money-neg');
                const sign = s.diff > 0 ? '+' : '';
                diffHtml = `<span class="money ${cls}">${sign}${fmtMoney(s.diff)}</span>`;
            }

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${s.employee}</strong></td>
                <td class="money">${fmtMoney(s.opening_cash)}</td>
                <td class="money">${fmtMoney(s.bill_revenue)}</td>
                <td class="money">${fmtMoney(s.closing_cash)}</td>
                <td>${diffHtml}</td>
                <td><span class="badge ${st.cls}">${st.label}</span>${s.handover_confirmed ? ' <span class="badge badge-success" style="margin-left:4px">✓ Bàn giao</span>' : ''}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="state-error">Lỗi tải dữ liệu<br><button class="retry-btn" onclick="loadCashShifts()">Thử lại</button></td></tr>`;
    }
}

// ─── Debts Page ─────────────────────────────

async function loadDebts() {
    const tbody = document.getElementById('debts-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="state-loading">Đang tải...</td></tr>';

    try {
        const data = await apiFetch('/api/v2/debts');
        document.getElementById('debt-total-badge').textContent = fmtMoney(data.summary.total);

        if (!data.debts || data.debts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="state-empty">Không có khoản nợ nào 🎉</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        data.debts.forEach(d => {
            const tr = document.createElement('tr');
            const statusCls = d.is_overdue ? 'badge-danger' : 'badge-warning';
            const statusLabel = d.is_overdue ? 'Quá hạn' : (d.status === 'partial' ? 'Trả một phần' : 'Đang nợ');

            tr.style.cursor = 'pointer';
            tr.onclick = () => openDebtModal(d.id, d.customer, d.remaining);
            tr.innerHTML = `
                <td>
                    <strong>${d.customer}</strong>
                    ${d.phone ? `<br><small style="color:var(--text-muted)">${d.phone}</small>` : ''}
                </td>
                <td class="money">${fmtMoney(d.bill_total)}</td>
                <td class="money money-neg">${fmtMoney(d.remaining)}</td>
                <td>${d.due_date ? fmtDate(d.due_date) : '<span style="color:var(--text-muted)">Chưa hẹn</span>'}</td>
                <td><span class="badge ${statusCls}">${statusLabel}</span></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="state-error">Lỗi tải dữ liệu<br><button class="retry-btn" onclick="loadDebts()">Thử lại</button></td></tr>`;
    }
}

// ─── Inventory Page ─────────────────────────

async function loadInventory() {
    const tbody = document.getElementById('inventory-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="state-loading">Đang tải...</td></tr>';

    try {
        const data = await apiFetch('/api/v2/inventory');
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="state-empty">Chưa có hàng hoá nào</td></tr>';
            return;
        }
        tbody.innerHTML = '';
        data.items.forEach(item => {
            const tr = document.createElement('tr');
            const stockCls = item.is_low ? 'money-neg' : '';
            const statusBadge = item.is_low
                ? '<span class="badge badge-danger">Tồn thấp</span>'
                : '<span class="badge badge-success">Bình thường</span>';

            tr.style.cursor = 'pointer';
            tr.onclick = () => openItemModal(item.id);
            tr.innerHTML = `
                <td><strong>${item.name}</strong></td>
                <td>${item.category || '—'}</td>
                <td class="money ${stockCls}">${item.stock}</td>
                <td>${item.unit}</td>
                <td>${statusBadge}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="state-error">Lỗi tải dữ liệu<br><button class="retry-btn" onclick="loadInventory()">Thử lại</button></td></tr>`;
    }
}

// ─── Employees Page ─────────────────────────

async function loadEmployees() {
    const tbody = document.getElementById('employees-tbody');
    tbody.innerHTML = '<tr><td colspan="4" class="state-loading">Đang tải...</td></tr>';

    try {
        const data = await apiFetch('/api/v2/employees');
        if (!data.employees || data.employees.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="state-empty">Chưa có nhân viên</td></tr>';
            return;
        }
        tbody.innerHTML = '';
        const roleLabels = { owner: 'Chủ quán', employee: 'Nhân viên' };

        data.employees.forEach(e => {
            const tr = document.createElement('tr');
            const statusBadge = e.is_working
                ? '<span class="badge badge-success">Đang làm</span>'
                : (e.is_active
                    ? '<span class="badge badge-neutral">Nghỉ</span>'
                    : '<span class="badge badge-danger">Ngưng</span>');

            tr.innerHTML = `
                <td><strong>${e.name}</strong></td>
                <td>${roleLabels[e.role] || e.role}</td>
                <td>${e.phone || '—'}</td>
                <td>${statusBadge}${e.is_working && e.checkin_time ? `<br><small style="color:var(--text-muted)">Vào lúc ${fmtTime(e.checkin_time)}</small>` : ''}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="state-error">Lỗi tải dữ liệu<br><button class="retry-btn" onclick="loadEmployees()">Thử lại</button></td></tr>`;
    }
}

// ─── Date Selection ─────────────────────────

function setDate(dateStr, activeBtn) {
    currentDate = dateStr;
    // Update button states
    document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
    if (activeBtn) activeBtn.classList.add('active');
    // Reload current page
    loadPageData(currentPage);
}

// ─── User Profile ───────────────────────────

function setUserProfile() {
    if (tg?.initDataUnsafe?.user) {
        const u = tg.initDataUnsafe.user;
        const name = u.first_name + (u.last_name ? ' ' + u.last_name : '');
        document.getElementById('user-name').textContent = name;
        document.getElementById('user-avatar').textContent = u.first_name?.charAt(0)?.toUpperCase() || '?';
    }
}

// ─── Event Bindings ─────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    setUserProfile();

    // Sidebar nav
    document.querySelectorAll('.nav-item').forEach(el => {
        el.addEventListener('click', (e) => {
            e.preventDefault();
            navigateTo(el.dataset.page);
        });
    });

    // Mobile nav
    document.querySelectorAll('.mob-btn').forEach(el => {
        el.addEventListener('click', () => {
            if (el.dataset.page === 'more') {
                toggleMoreMenu();
            } else {
                navigateTo(el.dataset.page);
            }
        });
    });

    // More menu items
    document.querySelectorAll('.more-item').forEach(el => {
        el.addEventListener('click', () => navigateTo(el.dataset.page));
    });

    // More overlay close
    document.getElementById('more-overlay').addEventListener('click', closeMoreMenu);

    // Date buttons
    document.getElementById('btn-today').addEventListener('click', function () {
        setDate(null, this);
    });
    document.getElementById('btn-yesterday').addEventListener('click', function () {
        setDate(yesterdayStr(), this);
    });
    document.getElementById('date-input').addEventListener('change', function () {
        if (this.value) {
            document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
            setDate(this.value, null);
        }
    });

    // Refresh
    document.getElementById('btn-refresh').addEventListener('click', () => {
        loadPageData(currentPage);
    });

    // Stat card clicks → navigate to related page
    document.querySelectorAll('.stat-card').forEach(card => {
        card.addEventListener('click', () => {
            const stat = card.dataset.stat;
            const pageMap = { revenue: 'shifts', debt: 'debts', staff: 'employees', pending: 'overview' };
            if (pageMap[stat] && pageMap[stat] !== currentPage) {
                navigateTo(pageMap[stat]);
            }
        });
    });

    // Initial load
    loadOverview();
});


// ─── Item Management Modal ──────────

function openItemModal(itemId = null) {
    const modal = document.getElementById('item-modal');
    modal.classList.add('active');
    document.getElementById('item-id').value = '';
    document.getElementById('item-name').value = '';
    document.getElementById('item-category').value = 'drink';
    document.getElementById('item-base-unit').value = '';
    document.getElementById('item-low-stock').value = 10;
    document.getElementById('item-pack-unit').value = '';
    document.getElementById('item-pack-size').value = '';
    document.getElementById('item-active').checked = true;
    
    document.getElementById('item-modal-title').textContent = itemId ? "Sửa hàng hóa" : "Thêm hàng hóa";
    
    if (itemId) {
        apiFetch('/api/v2/inventory').then(data => {
            const item = data.items.find(i => i.id == itemId);
            if(item) {
                document.getElementById('item-id').value = item.id;
                document.getElementById('item-name').value = item.name;
                document.getElementById('item-category').value = item.category;
                document.getElementById('item-base-unit').value = item.base_unit;
                document.getElementById('item-low-stock').value = item.low_stock_threshold;
                document.getElementById('item-pack-unit').value = item.pack_unit || '';
                document.getElementById('item-pack-size').value = item.pack_size || '';
                document.getElementById('item-active').checked = (item.is_active === 1);
            }
        });
    }
}

function closeItemModal() {
    document.getElementById('item-modal').classList.remove('active');
}

async function saveItem() {
    const id = document.getElementById('item-id').value;
    const payload = {
        id: id ? parseInt(id) : null,
        name: document.getElementById('item-name').value.trim(),
        category: document.getElementById('item-category').value,
        base_unit: document.getElementById('item-base-unit').value.trim(),
        pack_unit: document.getElementById('item-pack-unit').value.trim() || null,
        pack_size: parseInt(document.getElementById('item-pack-size').value) || null,
        low_stock_threshold: parseInt(document.getElementById('item-low-stock').value) || 0,
        is_active: document.getElementById('item-active').checked ? 1 : 0
    };
    
    if (!payload.name || !payload.base_unit) return alert("Vui lòng nhập Tên và Đơn vị cơ bản");
    
    const btn = document.getElementById('btn-save-item');
    btn.disabled = true;
    btn.textContent = 'Đang lưu...';
    
    try {
        await apiFetch('/api/v2/inventory/item', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        closeItemModal();
        loadInventory(); // Reload table
    } catch (e) {
        alert("Lỗi: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Lưu';
    }
}

// ─── Cashflow Page & Transaction Modal ──────────

async function loadCashflow() {
    const tbody = document.getElementById('cashflow-tbody');
    tbody.innerHTML = '<tr><td colspan="4" class="state-loading">Đang tải...</td></tr>';

    try {
        const dateParam = currentDate ? `?date=${currentDate}` : '';
        const data = await apiFetch('/api/v2/transactions' + dateParam);
        if (!data.transactions || data.transactions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="state-empty">Chưa có giao dịch nào</td></tr>';
            return;
        }
        tbody.innerHTML = '';
        data.transactions.forEach(tx => {
            const tr = document.createElement('tr');
            const typeLabel = tx.type === 'income' ? '<span class="badge badge-success">Thu</span>' : '<span class="badge badge-danger">Chi</span>';
            const moneyCls = tx.type === 'income' ? 'money-pos' : 'money-neg';
            const sign = tx.type === 'income' ? '+' : '-';
            
            tr.innerHTML = `
                <td>${tx.time}</td>
                <td>${typeLabel}</td>
                <td class="money ${moneyCls}">${sign}${fmtMoney(tx.amount)}</td>
                <td>${tx.description}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="state-error">Lỗi tải dữ liệu<br><button class="retry-btn" onclick="loadCashflow()">Thử lại</button></td></tr>`;
    }
}

function openTxnModal() {
    const modal = document.getElementById('txn-modal');
    modal.classList.add('active');
    document.getElementById('txn-id').value = '';
    document.getElementById('txn-type').value = 'income';
    document.getElementById('txn-amount').value = '';
    document.getElementById('txn-desc').value = '';
    document.getElementById('txn-method').value = 'cash';
}

function closeTxnModal() {
    document.getElementById('txn-modal').classList.remove('active');
}

async function saveTxn() {
    const payload = {
        type: document.getElementById('txn-type').value,
        amount: parseFloat(document.getElementById('txn-amount').value) || 0,
        description: document.getElementById('txn-desc').value.trim(),
        method: document.getElementById('txn-method').value
    };
    
    if (payload.amount <= 0 || !payload.description) {
        return alert("Vui lòng nhập Số tiền và Lý do hợp lệ");
    }
    
    const btn = document.getElementById('btn-save-txn');
    btn.disabled = true;
    btn.textContent = 'Đang lưu...';
    
    try {
        await apiFetch('/api/v2/transactions', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        closeTxnModal();
        if (currentPage === 'cashflow') loadCashflow();
        loadOverview();
    } catch (e) {
        alert("Lỗi: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Lưu Giao Dịch';
    }
}

// ─── Debt Payment Modal ──────────

function openDebtModal(id, customerName, remaining) {
    const modal = document.getElementById('debt-modal');
    modal.classList.add('active');
    
    document.getElementById('debt-id').value = id;
    document.getElementById('debt-customer-name').textContent = customerName;
    document.getElementById('debt-remaining').textContent = fmtMoney(remaining);
    
    document.getElementById('debt-pay-amount').value = remaining;
    document.getElementById('debt-method').value = 'cash';
}

function closeDebtModal() {
    document.getElementById('debt-modal').classList.remove('active');
}

async function saveDebtPayment() {
    const debtId = document.getElementById('debt-id').value;
    const amount = parseFloat(document.getElementById('debt-pay-amount').value) || 0;
    const method = document.getElementById('debt-method').value;
    
    if (amount <= 0) return alert("Vui lòng nhập số tiền hợp lệ");
    
    const btn = document.getElementById('btn-save-debt');
    btn.disabled = true;
    btn.textContent = 'Đang xử lý...';
    
    try {
        await apiFetch('/api/v2/debts/pay', {
            method: 'POST',
            body: JSON.stringify({ debt_id: parseInt(debtId), amount: amount, method: method })
        });
        closeDebtModal();
        loadDebts();
        loadOverview();
    } catch (e) {
        alert("Lỗi: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Xác nhận';
    }
}
