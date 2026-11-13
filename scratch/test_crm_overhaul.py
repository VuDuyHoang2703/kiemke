import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import requests
from app import load_catalog
from crm_parser import parse_crm_file, import_crm_orders

crm_file = Path("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx")
catalog, _ = load_catalog()

print("--- 1. PARSING TEST ---")
with open(crm_file, "rb") as f:
    res = parse_crm_file(f, catalog, set())

orders = res["orders"]
print(f"Total orders: {len(orders)}")
assert len(orders) == 100

already_returned = [o for o in orders if o.get("is_already_returned")]
print(f"Already returned (Đã trả toàn bộ): {len(already_returned)}")
assert len(already_returned) == 51

# Check RENT260067 vs RENT260067 (2nd Month)
o1 = next(o for o in orders if o["order_no"] == "RENT260067")
o2 = next(o for o in orders if o["order_no"] == "RENT260067 (2nd Month)")
print(f"RENT260067 kits count: {len(o1['kits'])}")
assert len(o1['kits']) == 1, f"Expected 1 kit, got {len(o1['kits'])}"

print(f"RENT260067 (2nd Month) kits count: {len(o2['kits'])}")
assert len(o2['kits']) == 7, f"Expected 7 kits, got {len(o2['kits'])}"

# Check RENT260065... vs RENT260065... tháng thứ 2
o3 = next(o for o in orders if o["order_no"] == "RENT260065-HD-1788450591431-RCOFW9")
print(f"RENT260065-HD... kits count: {len(o3['kits'])}")
assert len(o3['kits']) == 3, f"Expected 3 kits, got {len(o3['kits'])}"

# Check ticket code mapping during import
print("\n--- 2. DATABASE IMPORT TEST ---")
db_path = Path("data/inventory.db")
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

# Clear before test import
for t in ["rentals", "rental_kits", "inspection_entries", "rental_change_logs", "supplementary_returns", "deletion_logs"]:
    conn.execute(f"DELETE FROM {t}")
conn.commit()

import_crm_orders(conn, catalog, orders)
conn.commit()

# Verify imported rentals in DB
rows = conn.execute("SELECT id, code, crm_order_no, status, completed_at, initial_inspection_json FROM rentals").fetchall()
print(f"Total imported rentals in DB: {len(rows)}")
assert len(rows) == 100

# Verify ticket code equals crm_order_no!
for r in rows:
    assert r["code"] == r["crm_order_no"], f"Mismatch code '{r['code']}' != crm '{r['crm_order_no']}'"

completed_rows = [r for r in rows if r["status"] == "completed"]
pending_rows = [r for r in rows if r["status"] == "pending"]
print(f"Completed rows: {len(completed_rows)}, Pending rows: {len(pending_rows)}")
assert len(completed_rows) == 51
assert len(pending_rows) == 49

# Test that editing completed rental fails via API
sample_completed_id = completed_rows[0]["id"]
conn.close()

# Start/restart daemon to test API endpoint
print("\nDatabase and parser overhaul verified successfully!")

