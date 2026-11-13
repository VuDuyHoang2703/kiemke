import openpyxl
import re

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

# Map all items from Sheet 2
s2_by_order = {}
for r in range(2, ws2.max_row + 1):
    ono = str(ws2.cell(r, 1).value or "").strip()
    if not ono:
        continue
    code = str(ws2.cell(r, 2).value or "").strip()
    desc = str(ws2.cell(r, 3).value or "").strip()
    unit = str(ws2.cell(r, 7).value or "").strip()
    qty = ws2.cell(r, 8).value
    price = ws2.cell(r, 10).value
    
    if ono not in s2_by_order:
        s2_by_order[ono] = []
    s2_by_order[ono].append({
        "row": r,
        "code": code,
        "desc": desc,
        "unit": unit,
        "qty": qty,
        "price": price
    })

print(f"Total orders in Sheet 2: {len(s2_by_order)}")

# Cross examine every order in Sheet 1
mismatches = []
for r in range(2, ws1.max_row + 1):
    ono_s1 = str(ws1.cell(r, 2).value or "").strip()
    if not ono_s1:
        continue
    cust = str(ws1.cell(r, 3).value or "").strip()
    goods_s1_raw = str(ws1.cell(r, 9).value or "").strip()
    goods_s1_codes = [c.strip() for c in goods_s1_raw.split(",") if c.strip()]
    
    # Find matching items in Sheet 2
    # Check exact, normalized, tokens
    s2_items = s2_by_order.get(ono_s1)
    if not s2_items:
        # try tokens
        for t in ono_s1.split():
            if t in s2_by_order:
                s2_items = s2_by_order[t]
                break
                
    if not s2_items:
        mismatches.append({
            "order_no": ono_s1,
            "customer": cust,
            "type": "MISSING_IN_S2",
            "s1_codes": goods_s1_codes,
            "s2_codes": []
        })
    else:
        s2_codes = [it["code"] for it in s2_items]
        # Compare codes
        # Are there codes in S1 missing in S2?
        diff_s1 = set(goods_s1_codes) - set(s2_codes)
        # Are there codes in S2 missing in S1?
        diff_s2 = set(s2_codes) - set(goods_s1_codes)
        if diff_s1 or diff_s2:
            mismatches.append({
                "order_no": ono_s1,
                "customer": cust,
                "type": "CODE_DIFFERENCE",
                "diff_s1": list(diff_s1),
                "diff_s2": list(diff_s2),
                "s1_codes": goods_s1_codes,
                "s2_codes": s2_codes
            })

print(f"Mismatches count: {len(mismatches)}")
for m in mismatches:
    print(m)

