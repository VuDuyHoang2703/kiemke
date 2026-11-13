import openpyxl

wb = openpyxl.load_workbook("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", data_only=True)

print("--- SHEET 1: Danh sách ---")
ws1 = wb["Danh sách"]
for r_idx, row in enumerate(ws1.iter_rows(values_only=True), 1):
    if r_idx == 1:
        headers1 = [str(c) if c is not None else "" for c in row]
        for col_idx, h in enumerate(headers1):
            if h.strip():
                print(f"Col {col_idx}: {h}")
    elif r_idx <= 4:
        print(f"Row {r_idx}: {row[:15]}")
    else:
        break

print("\n--- SHEET 2: Bảng hàng hóa ---")
ws2 = wb["Bảng hàng hóa"]
for r_idx, row in enumerate(ws2.iter_rows(values_only=True), 1):
    if r_idx == 1:
        headers2 = [str(c) if c is not None else "" for c in row]
        for col_idx, h in enumerate(headers2):
            if h.strip():
                print(f"Col {col_idx}: {h}")
    elif r_idx <= 4:
        print(f"Row {r_idx}: {row[:15]}")
    else:
        break

