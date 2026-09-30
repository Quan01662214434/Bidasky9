let allItems = [];

const tg = window.Telegram?.WebApp;
if (tg) tg.expand();

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

async function init() {
    try {
        const data = await apiFetch('/api/v2/inventory'); // The overview inventory API returns stock
        allItems = data.items || [];
        renderTable();
    } catch (err) {
        document.getElementById('items-tbody').innerHTML = `<tr><td colspan="5" style="color:red; text-align:center;">Lỗi: ${err.message}</td></tr>`;
    }
}

function renderTable() {
    const tbody = document.getElementById('items-tbody');
    if (allItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Không có mặt hàng nào.</td></tr>';
        return;
    }
    
    let html = '';
    allItems.forEach(item => {
        html += `
            <tr data-id="${item.id}" data-sys="${item.current_stock}">
                <td>
                    <div style="font-weight: 500;">${item.name}</div>
                    <div style="font-size: 11px; color: var(--text-muted);">${item.base_unit}</div>
                </td>
                <td style="text-align: center; font-weight: 600;">${item.current_stock}</td>
                <td style="text-align: center;">
                    <input type="number" class="input-qty" value="${item.current_stock}" oninput="calcDiff(this)">
                </td>
                <td style="text-align: center;" class="diff-cell diff-zero">0</td>
                <td>
                    <input type="text" class="input-reason" placeholder="Lý do..." style="display:none;">
                </td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function calcDiff(input) {
    const tr = input.closest('tr');
    const sys = parseFloat(tr.dataset.sys) || 0;
    const act = parseFloat(input.value);
    
    const diffCell = tr.querySelector('.diff-cell');
    const reasonInput = tr.querySelector('.input-reason');
    
    if (isNaN(act)) {
        diffCell.textContent = '-';
        diffCell.className = 'diff-cell';
        reasonInput.style.display = 'none';
        return;
    }
    
    const diff = act - sys;
    diffCell.textContent = diff > 0 ? '+' + diff : diff;
    
    if (diff > 0) {
        diffCell.className = 'diff-cell diff-pos';
        reasonInput.style.display = 'block';
    } else if (diff < 0) {
        diffCell.className = 'diff-cell diff-neg';
        reasonInput.style.display = 'block';
    } else {
        diffCell.className = 'diff-cell diff-zero';
        reasonInput.style.display = 'none';
        reasonInput.value = '';
    }
}

let isSubmitting = false;

async function submitCheck() {
    if (isSubmitting) return;
    
    const adjustments = [];
    let hasMissingReason = false;
    
    document.querySelectorAll('#items-tbody tr').forEach(tr => {
        const id = parseInt(tr.dataset.id);
        const sys = parseFloat(tr.dataset.sys) || 0;
        const actInput = tr.querySelector('.input-qty').value;
        if (actInput === '') return;
        
        const act = parseFloat(actInput);
        if (sys !== act) {
            const reason = tr.querySelector('.input-reason').value.trim();
            if (!reason) hasMissingReason = true;
            adjustments.push({
                item_id: id,
                system_stock: sys,
                counted_stock: act,
                diff: act - sys,
                reason: reason
            });
        }
    });
    
    if (adjustments.length === 0) {
        return alert("Không có sự chênh lệch nào cần lưu.");
    }
    
    if (hasMissingReason) {
        return alert("Vui lòng nhập LÝ DO cho tất cả các mặt hàng có chênh lệch.");
    }
    
    if (!confirm(`Xác nhận điều chỉnh kho cho ${adjustments.length} mặt hàng?`)) return;
    
    isSubmitting = true;
    const btn = document.getElementById('btn-submit');
    btn.textContent = "ĐANG LƯU...";
    btn.disabled = true;
    
    try {
        await apiFetch('/api/v2/inventory/check', {
            method: 'POST',
            body: JSON.stringify({ adjustments })
        });
        
        alert("Lưu kết quả kiểm kho thành công!");
        window.history.back();
    } catch (err) {
        alert("Lỗi: " + err.message);
        btn.textContent = "LƯU KẾT QUẢ KIỂM KHO";
        btn.disabled = false;
        isSubmitting = false;
    }
}

window.addEventListener('DOMContentLoaded', init);
