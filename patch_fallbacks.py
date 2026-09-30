import os
import re

handlers_dir = r"c:\Users\quan\Desktop\Phanmem bida\bot\handlers"

cancel_funcs = {
    "transactions.py": "cancel_txn",
    "salary.py": "cancel_sal",
    "reports.py": "cancel_rpt",
    "inventory.py": "cancel_inv",
    "debt.py": "cancel_debt",
    "cash_shift.py": "cancel_shift_action",
    "attendance.py": "cancel_att",
}

for filename, func_name in cancel_funcs.items():
    filepath = os.path.join(handlers_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Thêm CommandHandler vào import
    if "CommandHandler" not in content:
        content = content.replace("MessageHandler", "MessageHandler, CommandHandler")
    
    # Sửa các fallbacks
    pattern = r"fallbacks=\[CallbackQueryHandler\(" + func_name + r", pattern=[^]]+\)\]"
    replacement = f"fallbacks=[CommandHandler('start', {func_name}), CallbackQueryHandler({func_name}, pattern=f\"^{{CB.BACK}}:menu$\")]"
    
    # Tìm fallback dạng list dài
    pattern2 = r"fallbacks=\[\s*CallbackQueryHandler\(" + func_name + r",[^]]+\)\s*\]"
    replacement2 = f"fallbacks=[\n            CommandHandler('start', {func_name}),\n            CallbackQueryHandler({func_name}, pattern=f\"^{{CB.BACK}}:menu$\")\n        ]"

    new_content = re.sub(pattern, replacement, content)
    new_content = re.sub(pattern2, replacement2, new_content)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

print("Done patching fallbacks!")
