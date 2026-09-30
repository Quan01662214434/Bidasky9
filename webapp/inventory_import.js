let allItems = [];

// Initialize Telegram WebApp
const tg = window.Telegram?.WebApp;
if (tg) tg.expand();

// Helper API Fetch
async function apiFetch(url, options = {}) {
    const headers = options.headers || {};
    const initData = window.Telegram?.WebApp?.initData || '7496977545';
    headers['Authorization'] = `Bearer ${initData}`;
    if (options.body) headers['Content-Type'] = 'application/json';
    
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

function fmtMoney(amount) {
    return Math.floor(amount).toLocaleString('vi-VN') + 'đ';
}

async function init() {
    // Set default date
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('import-date').value = today;

    try {
        const data = await apiFetch('/api/v2/inventory/items');
        allItems = data.items || [];
        addItemRow(); // Add first empty row
    } catch (err) {
        alert("Lỗi tải danh sách hàng hóa: " + err.message);
    }
}

function updatePaymentUI() {
    const status = document.getElementById('payment-status').value;
    const group = document.getElementById('payment-details-group');
    const amountInput = document.getElementById('paid-amount');
    
    if (status === 'unpaid') {
        group.style.display = 'none';
        amountInput.value = 0;
    } else if (status === 'paid') {
        group.style.display = 'grid';
        amountInput.value = calculateGrandTotal();
        amountInput.disabled = true; // Auto fill, can't change
    } else {
        group.style.display = 'grid';
        amountInput.value = '';
        amountInput.disabled = false;
    }
}

function calculateGrandTotal() {
    let total = 0;
    let qty = 0;
    document.querySelectorAll('.item-row').forEach(row => {
        const p = parseFloat(row.querySelector('.item-price').value) || 0;
        const q = parseFloat(row.querySelector('.item-qty').value) || 0;
        total += (p * q);
        qty += q;
    });
    
    document.getElementById('sum-total').textContent = fmtMoney(total);
    document.getElementById('sum-qty').textContent = qty;
    return total;
}

function calculateRow(el) {
    const row = el.closest('.item-row');
    const p = parseFloat(row.querySelector('.item-price').value) || 0;
    const q = parseFloat(row.querySelector('.item-qty').value) || 0;
    row.querySelector('.item-total-text').textContent = fmtMoney(p * q);
    
    calculateGrandTotal();
    
    const status = document.getElementById('payment-status').value;
    if (status === 'paid') {
        document.getElementById('paid-amount').value = calculateGrandTotal();
    }
}

function addItemRow() {
    const template = document.getElementById('item-row-template');
    const clone = template.content.cloneNode(true);
    
    const select = clone.querySelector('.item-select');
    select.innerHTML = '<option value="">-- Chọn món --</option>' + 
        allItems.map(item => `<option value="${item.id}" data-base-unit="${item.base_unit}">${item.name}</option>`).join('');
        
    document.getElementById('items-tbody').appendChild(clone);
}

function removeRow(btn) {
    const tbody = document.getElementById('items-tbody');
    if (tbody.children.length > 1) {
        btn.closest('.item-row').remove();
        calculateGrandTotal();
    } else {
        alert("Phải có ít nhất 1 dòng!");
    }
}

function onItemChange(select) {
    const row = select.closest('.item-row');
    const unitSelect = row.querySelector('.unit-select');
    const itemId = select.value;
    
    if (!itemId) {
        unitSelect.innerHTML = '';
        return;
    }
    
    const item = allItems.find(i => i.id == itemId);
    if (!item) return;
    
    // Default base unit
    let options = `<option value="${item.base_unit}" data-ratio="1">${item.base_unit}</option>`;
    
    // Add conversion units if available (dummy data for now, actual implementation needs units in API)
    if (item.units) {
        item.units.forEach(u => {
            options += `<option value="${u.name}" data-ratio="${u.ratio}">${u.name} (${u.ratio} ${item.base_unit})</option>`;
        });
    }
    
    unitSelect.innerHTML = options;
}

let isSubmitting = false;

async function submitImport() {
    if (isSubmitting) return;
    
    const supplier = document.getElementById('supplier').value.trim();
    const importDate = document.getElementById('import-date').value;
    if (!supplier) return alert("Vui lòng nhập nhà cung cấp");
    
    const items = [];
    let hasError = false;
    
    document.querySelectorAll('.item-row').forEach(row => {
        const itemId = row.querySelector('.item-select').value;
        const unit = row.querySelector('.unit-select').value;
        const qty = parseFloat(row.querySelector('.item-qty').value) || 0;
        const price = parseFloat(row.querySelector('.item-price').value) || 0;
        
        if (!itemId || qty <= 0) hasError = true;
        
        items.push({
            item_id: parseInt(itemId),
            unit: unit,
            quantity: qty,
            unit_price: price,
            total: qty * price
        });
    });
    
    if (hasError || items.length === 0) return alert("Vui lòng chọn hàng hóa và nhập số lượng hợp lệ");
    
    const paymentStatus = document.getElementById('payment-status').value;
    const paidAmount = parseFloat(document.getElementById('paid-amount').value) || 0;
    const paymentMethod = document.getElementById('payment-method').value;
    
    const payload = {
        supplier,
        import_date: importDate,
        notes: document.getElementById('notes').value,
        total_amount: calculateGrandTotal(),
        payment_status: paymentStatus,
        paid_amount: paidAmount,
        payment_method: paymentMethod,
        items
    };
    
    // Confirm
    if (!confirm(`Xác nhận nhập hàng?\nTổng tiền: ${fmtMoney(payload.total_amount)}`)) return;
    
    isSubmitting = true;
    const btn = document.getElementById('btn-submit');
    btn.textContent = "ĐANG LƯU...";
    btn.disabled = true;
    
    try {
        await apiFetch('/api/v2/inventory/import', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        
        alert("Lưu phiếu nhập thành công!");
        window.history.back();
    } catch (err) {
        alert("Lỗi: " + err.message);
        btn.textContent = "LƯU PHIẾU NHẬP";
        btn.disabled = false;
        isSubmitting = false;
    }
}

// Load on start
window.addEventListener('DOMContentLoaded', init);
