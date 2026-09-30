let currentData = [];
let editingId = null;

async function loadData() {
    const type = document.getElementById('filter-type').value;
    const date = document.getElementById('filter-date').value;
    
    document.getElementById('table-body').innerHTML = '<tr><td colspan="5" class="loading-text">Đang tải...</td></tr>';
    
    try {
        const res = await fetch(`/api/adjustments/list?type=${type}&date=${date}`);
        const data = await res.json();
        currentData = data;
        renderTable(type, data);
    } catch (e) {
        alert("Lỗi tải dữ liệu: " + e);
    }
}

function renderTable(type, data) {
    const thead = document.getElementById('table-head');
    const tbody = document.getElementById('table-body');
    
    if (type === 'attendance') {
        thead.innerHTML = `
            <th>ID</th>
            <th>Nhân viên</th>
            <th>Giờ vào</th>
            <th>Giờ ra</th>
            <th>Trạng thái</th>
            <th>Hành động</th>
        `;
        
        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="loading-text">Không có dữ liệu</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.map(row => `
            <tr>
                <td>#${row.id}</td>
                <td>${row.employee_name}</td>
                <td>${row.checkin_time || '---'}</td>
                <td>${row.checkout_time || '---'}</td>
                <td>${row.is_adjusted ? '<span style="color:#f59e0b">Chủ sửa</span>' : row.status}</td>
                <td>
                    <button class="action-btn" onclick="openEditModal('attendance', ${row.id})">Sửa</button>
                </td>
            </tr>
        `).join('');
    }
}

function openEditModal(type, id) {
    editingId = id;
    const row = currentData.find(r => r.id === id);
    const formFields = document.getElementById('form-fields');
    
    document.getElementById('edit-reason').value = '';
    document.getElementById('preview-box').style.display = 'none';
    document.getElementById('confirm-btn').style.display = 'none';
    
    if (type === 'attendance') {
        document.getElementById('modal-title').innerText = `Sửa Giờ Công #${id} - ${row.employee_name}`;
        formFields.innerHTML = `
            <div class="form-group">
                <label>Giờ vào (ISO Format):</label>
                <input type="text" id="edit-checkin" value="${row.checkin_time || ''}">
            </div>
            <div class="form-group">
                <label>Giờ ra (ISO Format):</label>
                <input type="text" id="edit-checkout" value="${row.checkout_time || ''}">
            </div>
        `;
    }
    
    document.getElementById('editModal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('editModal').style.display = 'none';
}

async function previewChanges() {
    const reason = document.getElementById('edit-reason').value;
    if (!reason) {
        alert("Bắt buộc nhập lý do sửa đổi!");
        return;
    }
    
    const type = document.getElementById('filter-type').value;
    let payload = { reason };
    
    if (type === 'attendance') {
        payload.checkin_time = document.getElementById('edit-checkin').value || null;
        payload.checkout_time = document.getElementById('edit-checkout').value || null;
    }
    
    try {
        const res = await fetch(`/api/adjustments/preview/${type}/${editingId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        const preview = await res.json();
        
        if (res.ok) {
            const pbox = document.getElementById('preview-box');
            pbox.style.display = 'block';
            pbox.innerHTML = preview.messages.map(m => `<p>🔹 ${m}</p>`).join('');
            document.getElementById('confirm-btn').style.display = 'block';
        } else {
            alert(preview.detail || "Lỗi preview");
        }
    } catch (e) {
        alert(e);
    }
}

async function submitChanges() {
    const reason = document.getElementById('edit-reason').value;
    const type = document.getElementById('filter-type').value;
    
    let payload = { reason };
    if (type === 'attendance') {
        payload.checkin_time = document.getElementById('edit-checkin').value || null;
        payload.checkout_time = document.getElementById('edit-checkout').value || null;
    }
    
    try {
        const res = await fetch(`/api/adjustments/apply/${type}/${editingId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            alert("✅ Đã lưu thay đổi thành công!");
            closeModal();
            loadData();
        } else {
            const err = await res.json();
            alert("❌ Lỗi: " + (err.detail || JSON.stringify(err)));
        }
    } catch (e) {
        alert(e);
    }
}
