import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crm_parser import parse_crm_file
from database import get_conn
from app import load_catalog

crm_file = Path("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx")
db_path = Path("data/inventory.db")

with get_conn(db_path) as conn:
    existing_orders = [r[0] for r in conn.execute("SELECT crm_order_no FROM rentals WHERE crm_order_no IS NOT NULL").fetchall()]
    print(f"Current existing in DB: {len(existing_orders)}")

catalog, _ = load_catalog()

with open(crm_file, "rb") as f:
    orders, summary = parse_crm_file(f, catalog, existing_crm_order_nos=set())

print("\n--- Summary when database is fresh ---")
print("Total orders in file:", summary["total_orders"])
print("New orders ready:", summary["new_orders"])
print("Existing orders:", summary["existing_orders"])
print("No-kits / loose orders:", summary["no_kits_orders"])
print("Can import count:", sum(1 for o in orders if o["can_import"]))
print("\nLoose orders detail:")
for o in orders:
    if o.get("is_loose_order"):
        print(f" - {o['order_no']} ({o['customer_name']}): status={o['status']}, status_label={o['status_label']}, can_import={o['can_import']}, kits={o['kits']}")
