// Initialize Telegram WebApp
let tg = window.Telegram.WebApp;
tg.expand(); // Expand to full height

// Format currency
const formatMoney = (amount) => {
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
};

// Set User Profile from Telegram
if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const user = tg.initDataUnsafe.user;
    document.getElementById('user-name').innerText = user.first_name + (user.last_name ? ' ' + user.last_name : '');
    
    // Attempt to get initial letter for avatar
    const initial = user.first_name ? user.first_name.charAt(0).toUpperCase() : '?';
    document.getElementById('user-avatar').innerText = initial;
} else {
    document.getElementById('user-name').innerText = "Admin Profile";
    document.getElementById('user-avatar').innerText = "A";
}

// Fetch Data
async function loadDashboardData() {
    try {
        // Fetch stats
        const statsRes = await fetch('/api/dashboard');
        const stats = await statsRes.json();
        
        document.getElementById('val-revenue').innerText = formatMoney(stats.revenue);
        document.getElementById('val-tables').innerText = stats.active_tables;
        document.getElementById('val-members').innerText = stats.members;
        document.getElementById('val-active-staff').innerText = stats.active_staff;
        
        // Fetch employees
        const empRes = await fetch('/api/employees');
        const employees = await empRes.json();
        
        const tbody = document.getElementById('employee-table-body');
        tbody.innerHTML = '';
        
        if (employees.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="loading-text">Chưa có nhân viên nào</td></tr>';
        } else {
            employees.forEach(emp => {
                const statusClass = emp.status === 'Active' ? 'status-active' : 'status-inactive';
                const statusText = emp.status === 'Active' ? 'Đang làm' : 'Nghỉ';
                
                const roleTranslate = {
                    'owner': 'Chủ quán',
                    'manager': 'Quản lý',
                    'staff': 'Nhân viên'
                };
                
                const timeStr = emp.checkin_time !== 'N/A' 
                    ? new Date(emp.checkin_time + 'Z').toLocaleTimeString('vi-VN', {hour: '2-digit', minute:'2-digit'})
                    : '—';
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${emp.name}</strong></td>
                    <td style="color: var(--text-secondary)">${roleTranslate[emp.role] || emp.role}</td>
                    <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                    <td>${timeStr}</td>
                `;
                tbody.appendChild(tr);
            });
        }
    } catch (err) {
        console.error("Error loading data:", err);
    }
}

// Initialize Chart
function initChart() {
    const ctx = document.getElementById('revenueChart').getContext('2d');
    
    // Create gradient
    let gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(123, 97, 255, 0.5)');
    gradient.addColorStop(1, 'rgba(123, 97, 255, 0.0)');

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['1', '5', '10', '15', '20', '25', '30'],
            datasets: [{
                label: 'Doanh thu (Triệu VNĐ)',
                data: [5, 12, 10, 25, 22, 35, 48.2], // Mock data for visual appeal
                borderColor: '#7b61ff',
                borderWidth: 3,
                backgroundColor: gradient,
                fill: true,
                tension: 0.4,
                pointBackgroundColor: '#ffffff',
                pointBorderColor: '#7b61ff',
                pointBorderWidth: 2,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#94a0b8'
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: '#94a0b8'
                    }
                }
            },
            interaction: {
                intersect: false,
                mode: 'index',
            },
        }
    });
}

// Startup
document.addEventListener('DOMContentLoaded', () => {
    loadDashboardData();
    initChart();
    
    // Inform Telegram app is ready
    tg.ready();
});
