import os
from dotenv import load_dotenv

# Try to connect
load_dotenv()
from bot.models.database import get_connection, run_migrations

# Test migrations
try:
    run_migrations()
    print("Migrations applied successfully!")
    
    conn = get_connection()
    c = conn.execute("SELECT * FROM config")
    print("Config rows:", c.fetchall())
except Exception as e:
    import traceback
    traceback.print_exc()
