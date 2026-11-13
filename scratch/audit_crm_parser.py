import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import load_catalog
from crm_parser import parse_crm_file

catalog, _ = load_catalog()

with open("CRM_Saleorder_12.11.2026_13.57.52_512.xlsx", "rb") as f:
    res = parse_crm_file(f, catalog, set())

orders = res["orders"]
print(f"Total parsed orders: {len(orders)}")

empty_orders = []
for o in orders:
    total_kits = len(o.get("kits", []))
    total_custom_items = sum(len(k.get("custom_items", [])) for k in o.get("kits", []))
    if total_kits == 0:
        empty_orders.append((o["order_no"], o["customer"], "NO KITS"))
    # Also check if any order has fewer items than in sheet 2
    print(f"Order {o['order_no']} ({o['customer']}): {total_kits} kits, status={o['status']}, return_status={o.get('return_status')}")

if empty_orders:
    print(f"\nOrders with NO KITS ({len(empty_orders)}):")
    for eo in empty_orders:
        print("  -", eo)
else:
    print("\nNo orders with 0 kits found in parsed output.")

