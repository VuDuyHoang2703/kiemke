import openpyxl
import json

wb = openpyxl.load_workbook('CRM_Saleorder_12.11.2026_13.57.52_512.xlsx', data_only=True)
ws_items = wb['Bảng hàng hóa']
ws_list = wb['Danh sách']

print("=== CRM LIST SHEET HEADERS ===")
headers_list = [ws_list.cell(1, c).value for c in range(1, ws_list.max_column + 1)]
print(headers_list)

print("\n=== CRM ITEMS SHEET HEADERS ===")
headers_items = [ws_items.cell(1, c).value for c in range(1, ws_items.max_column + 1)]
print(headers_items)

goods = {}
for r in range(2, ws_items.max_row + 1):
    code = str(ws_items.cell(r, 2).value or '').strip()
    name = str(ws_items.cell(r, 3).value or '').strip()
    uom = str(ws_items.cell(r, 7).value or '').strip()
    sl = ws_items.cell(r, 8).value
    sl_nhan = ws_items.cell(r, 9).value
    if code and code not in goods:
        goods[code] = {"name": name, "uom": uom, "sample_qty": sl, "sl_nhan": sl_nhan}

print("\n=== UNIQUE GOODS IN CRM FILE ===")
for c, info in sorted(goods.items()):
    print(f"{c:25} | {info['uom']:10} | {info['name']}")

cat = json.load(open('data/catalog.json', encoding='utf-8'))
print("\n=== SYSTEM KITS IN CATALOG ===")
for k in cat['kits']:
    print(f"{k['id']:15} | {k['name']:25} | {k.get('subtitle')}")

