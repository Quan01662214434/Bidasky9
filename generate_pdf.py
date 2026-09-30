import os
import urllib.request
from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font("ArialVN", "B", 20)
        self.cell(0, 15, "SỔ TAY NHÂN VIÊN QUÁN BIDA", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("ArialVN", "", 12)
        self.cell(0, 5, "Hướng dẫn thao tác trên Telegram Bot", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("ArialVN", "", 10)
        self.cell(0, 10, f"Trang {self.page_no()}", align="C")

pdf = PDF()
pdf.add_font("ArialVN", "", r"C:\Windows\Fonts\arial.ttf")
pdf.add_font("ArialVN", "B", r"C:\Windows\Fonts\arialbd.ttf")
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()

# Intro Image
intro_img = r"C:\Users\quan\.gemini\antigravity-ide\brain\1a44bad6-e918-416f-a1e4-cb9e8f83cbd6\bida_telegram_intro_1790741914555.jpg"
if os.path.exists(intro_img):
    pdf.image(intro_img, x=55, w=100)
    pdf.ln(5)

pdf.set_font("ArialVN", "B", 14)
pdf.cell(0, 10, "1. LẦN ĐẦU SỬ DỤNG BOT", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("ArialVN", "", 12)
pdf.multi_cell(0, 8, "- Bước 1: Mở ứng dụng Telegram, tìm kiếm tên Bot của quán.\n- Bước 2: Bấm nút START hoặc gõ lệnh /start.\n- Bước 3: Bot sẽ báo lỗi 'Chưa cấp quyền' và cấp cho bạn 1 dãy số (ID). Hãy copy dãy số đó gửi cho Quản lý/Chủ quán để được thêm vào danh sách nhân viên.\n- Bước 4: Sau khi Chủ quán thêm xong, bạn gõ lại /start để hiện Menu làm việc chính.")
pdf.ln(5)

pdf.set_font("ArialVN", "B", 14)
pdf.cell(0, 10, "2. ĐĂNG KÝ CA VÀ ĐI LÀM", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("ArialVN", "", 12)
pdf.multi_cell(0, 8, "- Đăng ký ca: Chọn nút [✍️ Đăng ký ca] -> Chọn ngày -> Chọn ca muốn làm. Quản lý sẽ duyệt.\n- Check-in (Rất quan trọng): Khi tới quán, bạn BẮT BUỘC phải bấm [✅ Vào làm] -> Chọn ca -> Gửi 1 bức ảnh chụp rõ mặt bạn tại quán. Hệ thống mới bắt đầu tính lương từ thời điểm đó.")
pdf.ln(5)

# Checkin Image
checkin_img = r"C:\Users\quan\.gemini\antigravity-ide\brain\1a44bad6-e918-416f-a1e4-cb9e8f83cbd6\bida_checkin_1790741927239.jpg"
if os.path.exists(checkin_img):
    pdf.image(checkin_img, x=55, w=100)
    pdf.ln(5)

pdf.add_page()
pdf.set_font("ArialVN", "B", 14)
pdf.cell(0, 10, "3. GHI NHẬN ĐỒ UỐNG & XIN ỨNG LƯƠNG", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("ArialVN", "", 12)
pdf.multi_cell(0, 8, "- Ăn uống tại quán: Nếu bạn uống nước suối, bò húc... hãy bấm nút [🍺 Ghi đồ đã dùng] để ghi nhận. Hệ thống sẽ tự trừ vào lương cuối tháng, giúp bạn không cần nhớ nhớ quên quên.\n- Ứng lương: Kẹt tiền đột xuất? Chỉ cần bấm [💸 Xin ứng], nhập số tiền và lý do. Khi Chủ quán duyệt, tiền sẽ tự động cập nhật vào bảng lương.")
pdf.ln(10)

pdf.set_font("ArialVN", "B", 14)
pdf.cell(0, 10, "4. CHỐT CA VÀ RA VỀ", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("ArialVN", "", 12)
pdf.multi_cell(0, 8, "- Hết ca làm, bạn PHẢI bấm nút [🚪 Ra về]. Bot sẽ tính tổng số giờ bạn làm và quy ra tiền công ngay lập tức.\n- Nếu quên bấm, hôm sau bạn phải dùng nút [📝 Sửa công] để xin Chủ quán sửa lại giờ, nếu không sẽ không có lương của ca đó.\n- Nếu bạn là Thu ngân: Bắt buộc phải bấm [📊 Kết ca] để nhập tổng số tiền mặt bàn giao lại cho quán.")
pdf.ln(20)

pdf.set_font("ArialVN", "B", 12)
pdf.set_text_color(123, 97, 255) # Primary color
pdf.cell(0, 10, "--- Chúc các bạn làm việc hiệu quả và vui vẻ! ---", align="C", new_x="LMARGIN", new_y="NEXT")

pdf.output("Huong_Dan_Nhan_Vien.pdf")
print("PDF created successfully!")
