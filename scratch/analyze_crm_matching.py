import openpyxl

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

orders_s1 = []
for r in list(ws1.iter_rows(values_only=True))[1:]:
    if any(r):
        orders_s1.append({
            "order_no": str(r[1] or "").strip(),
            "customer": str(r[2] or "").strip(),
            "return_status": str(r[7] or "").strip(),
            "goods_s1": str(r[8] or "").strip(),
        })

print(f"Total orders in Sheet 1: {len(orders_s1)}")

items_s2 = []
s2_order_nos = set()
for r in list(ws2.iter_rows(values_only=True))[1:]:
    if any(r):
        ono = str(r[0] or "").strip()
        s2_order_nos.add(ono)
        items_s2.append({
            "order_no": ono,
            "code": str(r[1] or "").strip(),
            "desc": str(r[2] or "").strip(),
            "unit": str(r[6] or "").strip(),
            "qty": r[7],
            "unit_price": r[9],
        })

print(f"Total items in Sheet 2: {len(items_s2)}")
print(f"Unique order_nos in Sheet 2: {len(s2_order_nos)}")

# Check matching between Sheet 1 and Sheet 2
matched = 0
unmatched_s1 = []
for o in orders_s1:
    ono = o["order_no"]
    tokens = ono.split()
    found = False
    for t in [ono] + tokens:
        if t in s2_order_nos:
            found = True
            break
    if found:
        matched += 1
    else:
        unmatched_s1.append(o)

print(f"Matched Sheet 1 orders with Sheet 2: {matched}/{len(orders_s1)}")
if unmatched_s1:
    print(f"Unmatched {len(unmatched_s1)} orders from Sheet 1:")
    for u in unmatched_s1:
        print("  -", u)

# Check if there are orders in Sheet 2 not in Sheet 1
s1_tokens = set()
for o in orders_s1:
    s1_tokens.add(o["order_no"])
    for t in o["order_no"].split():
        s1_tokens.add(t)

s2_only = [ono for ono in s2_order_nos if ono not in s1_tokens]
print(f"Orders in Sheet 2 but not in Sheet 1: {len(s2_only)}")
if s2_only:
    print("  - Sample s2_only:", s2_only[:5])

