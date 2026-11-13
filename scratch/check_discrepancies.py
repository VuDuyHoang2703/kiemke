import openpyxl

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

# Map Sheet 2 items by order_no
s2_map = {}
for r in list(ws2.iter_rows(values_only=True))[1:]:
    if any(r):
        ono = str(r[0] or "").strip()
        if ono not in s2_map:
            s2_map[ono] = []
        s2_map[ono].append({
            "code": str(r[1] or "").strip(),
            "desc": str(r[2] or "").strip(),
            "unit": str(r[6] or "").strip(),
            "qty": r[7],
            "price": r[9],
        })

print("Checking Sheet 1 orders vs Sheet 2 items...")
discrepancies = []
for r_idx, r in enumerate(list(ws1.iter_rows(values_only=True))[1:], 2):
    if not any(r):
        continue
    ono = str(r[1] or "").strip()
    cust = str(r[2] or "").strip()
    goods_s1 = str(r[8] or "").strip()
    status_s1 = str(r[7] or "").strip()
    
    # How did we match ono to Sheet 2?
    # Test all tokens of ono
    tokens = ono.split()
    matched_items = []
    for t in [ono] + tokens:
        if t in s2_map:
            matched_items.extend(s2_map[t])
            break
            
    # Also check: what if multiple tokens matched? Or what if tokens had prefixes?
    if not matched_items:
        discrepancies.append((r_idx, ono, cust, "NO ITEMS IN S2", goods_s1))
    else:
        # Check goods_s1 codes vs matched_items codes
        s1_codes = [c.strip() for c in goods_s1.split(",") if c.strip()]
        s2_codes = [it["code"] for it in matched_items]
        
        # Check if any code in S1 is not in S2
        missing_in_s2 = [c for c in s1_codes if c not in s2_codes]
        if missing_in_s2:
            discrepancies.append((r_idx, ono, cust, f"S1 has codes not in S2: {missing_in_s2}", s2_codes))

print(f"Total discrepancies: {len(discrepancies)}")
for d in discrepancies[:20]:
    print("  -", d)

