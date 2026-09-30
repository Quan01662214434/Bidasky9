import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
db_path = os.getenv("DATABASE_PATH", "data/bida.db")

def reset_operational_data():
    if not os.path.exists(db_path):
        print(f"Không tìm thấy file {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables_to_clear = [
        "shift_registrations",
        "attendance_sessions",
        "checkin_photos",
        "attendance_adjustments",
        "cash_shifts",
        "transactions",
        "debts",
        "debt_payments",
        "salary_advances",
        "consumable_usage",
        "payroll",
        "notification_queue"
    ]
    
    print("Đang dọn dẹp dữ liệu test...")
    try:
        for table in tables_to_clear:
            cursor.execute(f"DELETE FROM {table};")
            # Reset Auto Increment id
            cursor.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            print(f" - Đã xóa dữ liệu bảng {table}")
        
        conn.commit()
        print("\n✅ Đã reset toàn bộ lịch sử chấm công, ca quỹ, thu chi thành công!")
        print("💡 (Danh sách nhân viên, mặt hàng, lịch ca cơ bản VẪN ĐƯỢC GIỮ NGUYÊN)")
    except Exception as e:
        conn.rollback()
        print(f"❌ Lỗi: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("CẢNH BÁO: Hành động này sẽ xóa toàn bộ dữ liệu làm việc (chỉ giữ lại danh sách nhân viên và cấu hình).")
    confirm = input("Bạn có chắc chắn muốn xóa dữ liệu test không? (gõ 'yes' để đồng ý): ")
    if confirm.lower() == 'yes':
        reset_operational_data()
    else:
        print("Đã hủy.")
