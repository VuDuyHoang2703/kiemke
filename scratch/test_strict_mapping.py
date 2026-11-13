import openpyxl
import re

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

# Map by exact normalized order_no
s2_map = {}
for r in range(2, ws2.max_row + 1):
    raw_no = str(ws2.cell(r, 1).value or "").strip()
    if not raw_no:
        continue
    ono = re.sub(r"\s+", " ", raw_no).strip()
    if ono not in s2_map:
        s2_map[ono] = []
    s2_map[ono].append({
        "row": r,
        "code": str(ws2.cell(r, 2).value or "").strip(),
        "desc": str(ws2.cell(r, 3).value or "").strip(),
        "unit": str(ws2.cell(r, 7).value or "Cái").strip(),
        "qty": int(ws2.cell(r, 8).value or 1),
        "price": float(ws2.cell(r, 10).value or 0),
    })

print(f"Total orders in Sheet 2: {len(s2_map)}")

# Audit Sheet 1 orders
perfect_matches = 0
for r in range(2, ws1.max_row + 1):
    raw_no = str(ws1.cell(r, 2).value or "").strip()
    if not raw_no:
        continue
    ono = re.sub(r"\s+", " ", raw_no).strip()
    if ono in s2_map:
        perfect_matches += 1
    else:
        print(f"Not found: '{ono}'")

print(f"Perfect 1-to-1 matches: {perfect_matches}/100")

