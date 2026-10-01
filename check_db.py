import os, sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
import pg_wrapper
conn = pg_wrapper.connect(os.getenv("DATABASE_URL"))

# Check attendance_sessions columns
cols = conn.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'attendance_sessions' ORDER BY ordinal_position"
).fetchall()
print("attendance_sessions columns:")
for c in cols:
    print(" ", c["column_name"])

# Add missing column arrived_at if needed
if not any(c["column_name"] == "arrived_at" for c in cols):
    print("\nAdding missing column: arrived_at")
    conn.execute("ALTER TABLE attendance_sessions ADD COLUMN arrived_at TEXT")
    print("Done!")
else:
    print("\narrived_at already exists")
