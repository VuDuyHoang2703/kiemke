import openpyxl
import re

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

# Map Sheet 2 items strictly by exact order_no
s2_map = {}
for r in range(2, ws2.max_row + 1):
    raw_no = str(ws2.cell(r, 1).value or "").strip()
    if not raw_no:
        continue
    ono = re.sub(r"\s+", " ", raw_no).strip()
    code = str(ws2.cell(r, 2).value or "").strip()
    desc = str(ws2.cell(r, 3).value or "").strip()
    unit = str(ws2.cell(r, 7).value or "Cái").strip()
    qty = int(ws2.cell(r, 8).value or 1)
    price = float(ws2.cell(r, 10).value or 0)
    
    # Skip non-inventory service/shipping fees
    if code.upper() in ("DVGIAOHANG", "PHIGIAOHANG"):
        continue
        
    if ono not in s2_map:
        s2_map[ono] = []
    s2_map[ono].append({
        "code": code,
        "desc": desc,
        "unit": unit,
        "qty": qty,
        "price": price
    })

# Check all orders in Sheet 1
orders_analysis = []
for r in range(2, ws1.max_row + 1):
    raw_no = str(ws1.cell(r, 2).value or "").strip()
    if not raw_no:
        continue
    ono = re.sub(r"\s+", " ", raw_no).strip()
    cust = str(ws1.cell(r, 3).value or "").strip()
    ret_status = str(ws1.cell(r, 8).value or "").strip()
    goods_s1 = str(ws1.cell(r, 9).value or "").strip()
    
    items = s2_map.get(ono, [])
    orders_analysis.append({
        "row": r,
        "order_no": ono,
        "customer": cust,
        "return_status": ret_status,
        "items_count": len(items),
        "total_qty": sum(it["qty"] for it in items),
        "items": items,
        "goods_s1": goods_s1
    })

print(f"Total analyzed orders: {len(orders_analysis)}")
zero_items = [o for o in orders_analysis if o["items_count"] == 0]
print(f"Orders with 0 items: {len(zero_items)}")
if zero_items:
    for z in zero_items:
        print("Zero items order:", z)

print("\nSample orders summary:")
for o in orders_analysis[:10]:
    print(f"Row {o['row']} | {o['order_no']} | Cust: {o['customer']} | Status: '{o['return_status']}' | Items: {o['items_count']} (SL: {o['total_qty']})")

