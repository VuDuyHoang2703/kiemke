import openpyxl
import re

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

order_items_map = {}
for r in range(2, ws2.max_row + 1):
    raw_no = str(ws2.cell(r, 1).value or "").strip()
    if not raw_no:
        continue
    order_no = re.sub(r"\s+", " ", raw_no).strip()
    token_key = order_no.split()[0] if order_no.split() else order_no
    
    # Notice: what if raw_no is just one key?
    unique_keys = {order_no, token_key, raw_no}
    for k in unique_keys:
        if k not in order_items_map:
            order_items_map[k] = []
        order_items_map[k].append(r)

missing_matches = []
for r in range(2, ws1.max_row + 1):
    order_no_raw = str(ws1.cell(r, 2).value or "").strip()
    if not order_no_raw:
        continue
    order_key = re.sub(r"\s+", " ", order_no_raw).strip()
    
    # Current line 194 logic:
    matched = order_items_map.get(order_no_raw) or order_items_map.get(order_key) or []
    
    if not matched:
        # Try checking tokens
        tokens = order_key.split()
        found_by_token = [t for t in tokens if t in order_items_map]
        missing_matches.append((r, order_no_raw, "Tokens found:", found_by_token))

print(f"Total orders where line 194 failed: {len(missing_matches)}")
for m in missing_matches:
    print("  -", m)

