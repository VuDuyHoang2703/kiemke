import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from app import load_catalog
from crm_parser import map_item_to_kit

catalog, _ = load_catalog()
kits_by_id = {k["id"]: k for k in catalog.get("kits", [])}

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws2 = wb["Bảng hàng hóa"]

none_items = []
for r_idx, r in enumerate(list(ws2.iter_rows(values_only=True))[1:], 2):
    if not any(r):
        continue
    ono = str(r[0] or "").strip()
    code = str(r[1] or "").strip()
    desc = str(r[2] or "").strip()
    qty = r[7]
    price = r[9]
    
    kit_id, kit_name = map_item_to_kit(code, desc, kits_by_id)
    if kit_id is None:
        none_items.append((r_idx, ono, code, desc, qty, price))

print(f"Total (None, None) items: {len(none_items)}")
for u in none_items:
    print(f"Row {u[0]} | Order: {u[1]} | Code: {u[2]} | Desc: {u[3]} | Qty: {u[4]}")

