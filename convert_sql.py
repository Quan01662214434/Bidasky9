import os
import re
import glob

def convert_to_postgres(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Remove PRAGMA
    content = re.sub(r'^PRAGMA.*?;', '', content, flags=re.MULTILINE)
    
    # AUTOINCREMENT to SERIAL
    content = re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY', content, flags=re.IGNORECASE)
    
    # date('now') to CURRENT_DATE
    content = re.sub(r"date\('now'\)", "CURRENT_DATE", content, flags=re.IGNORECASE)
    
    # IFNULL to COALESCE
    content = re.sub(r"\bIFNULL\b", "COALESCE", content, flags=re.IGNORECASE)

    # datetime('now') to CURRENT_TIMESTAMP
    content = re.sub(r"datetime\('now'\)", "CURRENT_TIMESTAMP", content, flags=re.IGNORECASE)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Converted {filepath}")

for f in glob.glob("migrations/*.sql"):
    convert_to_postgres(f)
