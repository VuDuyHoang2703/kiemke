import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from app import load_catalog
from crm_parser import parse_crm_file

catalog, _ = load_catalog()

with open("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", "rb") as f:
    res = parse_crm_file(f, catalog, set())

orders = res["orders"]

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

# Map S2 items by order_no
s2_items = {}
for r in range(2, ws2.max_row + 1):
    ono = str(ws2.cell(r, 1).value or "").strip()
    if not ono:
        continue
    code = str(ws2.cell(r, 2).value or "").strip()
    desc = str(ws2.cell(r, 3).value or "").strip()
    qty = ws2.cell(r, 8).value or 1
    if ono not in s2_items:
        s2_items[ono] = []
    s2_items[ono].append({"code": code, "desc": desc, "qty": qty})

print("Auditing 100 orders...")
for o in orders:
    ono = o["order_no"]
    raw_s2 = s2_items.get(ono)
    if not raw_s2:
        for t in ono.split():
            if t in s2_items:
                raw_s2 = s2_items[t]
                break
    
    # Calculate total kits and extra items in parsed order
    kits = o.get("kits", [])
    standard_kits = [k for k in kits if k["kit_id"] not in ("phu_kien_le", "phu_kien_di_kem")]
    loose_kits = [k for k in kits if k["kit_id"] in ("phu_kien_le", "phu_kien_di_kem")]
    loose_items_count = sum(len(k.get("custom_items", [])) for k in loose_kits)
    
    # Total S2 qty
    total_s2_qty = sum(int(it["qty"] or 1) for it in raw_s2) if raw_s2 else 0
    
    # Total items represented in kits: standard kit count + custom items qty
    total_parsed_qty = len(standard_kits) + sum(sum(int(ci.get("qty", 1)) for ci in k.get("custom_items", [])) for k in loose_kits)
    
    if total_s2_qty != total_parsed_qty or not kits:
        print(f"DISCREPANCY in {ono} ({o['customer']}): S2 total qty={total_s2_qty}, Parsed qty={total_parsed_qty}, kits={len(kits)} (std={len(standard_kits)}, loose_items={loose_items_count})")
        if raw_s2:
            for it in raw_s2:
                print(f"   S2 item: {it['code']} - {it['desc']} (SL: {it['qty']})")
        for k in kits:
            print(f"   Parsed kit: {k['kit_id']} - {k['kit_name']} (instance: {k.get('instance_index')}, custom_items: {len(k.get('custom_items', []))})")

