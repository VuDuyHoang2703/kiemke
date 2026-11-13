import openpyxl

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)
ws1 = wb["Danh sách"]
ws2 = wb["Bảng hàng hóa"]

s1_nos = [str(ws1.cell(r, 2).value or "").strip() for r in range(2, ws1.max_row + 1) if any(ws1.iter_rows(min_row=r, max_row=r, values_only=True))]
s2_nos = [str(ws2.cell(r, 1).value or "").strip() for r in range(2, ws2.max_row + 1) if any(ws2.iter_rows(min_row=r, max_row=r, values_only=True))]

print(f"Total non-empty in S1: {len(s1_nos)}")
print(f"Total rows in S2: {len(s2_nos)}, unique in S2: {len(set(s2_nos))}")

# Check exact string match
exact_matches = set(s1_nos).intersection(set(s2_nos))
print(f"Exact string matches: {len(exact_matches)}/100")

s1_only = set(s1_nos) - set(s2_nos)
s2_only = set(s2_nos) - set(s1_nos)

print(f"S1 strings not exactly in S2: {len(s1_only)}")
for s in s1_only:
    print(f"  S1: '{s}'")
    # find closest in S2
    for s2 in set(s2_nos):
        if s.startswith(s2) or s2.startswith(s) or s in s2 or s2 in s:
            print(f"     -> possible S2 candidate: '{s2}'")

print(f"\nS2 strings not exactly in S1: {len(s2_only)}")
for s in s2_only:
    print(f"  S2: '{s}'")

