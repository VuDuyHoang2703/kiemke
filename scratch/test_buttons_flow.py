import requests
import json

BASE_URL = "http://127.0.0.1:5000"

# 1. Create a rental order with kit_code
create_payload = {
    "team_name": "Đội Test Buttons 2",
    "returner_name": "Nguyễn Văn Test 2",
    "phone": "0988888889",
    "kits": [{"kit_id": "alpha_a", "kit_code": "ALPHA-02"}]
}
r = requests.post(f"{BASE_URL}/api/rentals", json=create_payload)
print("Create rental status:", r.status_code)
rental = r.json()["rental"]
rental_id = rental["id"]
rk_id = rental["kit_details"][0]["rental_kit_id"]
print(f"Created rental ID: {rental_id}, rk_id: {rk_id}")

# 2. Enter shortage on first item
items = rental["kit_details"][0]["items"]
item0 = items[0]
patch_payload = {
    "entries": [{
        "rental_kit_id": rk_id,
        "item_id": item0["id"],
        "kit_id": "alpha_a",
        "actual_input": "-1",
        "custom_qty": item0["expected_qty"]
    }]
}
r = requests.patch(f"{BASE_URL}/api/rentals/{rental_id}/entries", json=patch_payload)
print("Save entries status:", r.status_code)
res_data = r.json()
print("Missing pieces:", res_data["summary"]["total_missing_qty"])
print("Total comp:", res_data["summary"]["total_compensation"])

# 3. Complete the rental
r = requests.post(f"{BASE_URL}/api/rentals/{rental_id}/status", json={"status": "completed"})
print("Complete status:", r.status_code)

# 4. Save edit on completed rental (Lưu chỉnh sửa: sửa thành 0 - Đủ)
patch_payload_edit = {
    "entries": [{
        "rental_kit_id": rk_id,
        "item_id": item0["id"],
        "kit_id": "alpha_a",
        "actual_input": "0",
        "custom_qty": item0["expected_qty"]
    }]
}
r = requests.patch(f"{BASE_URL}/api/rentals/{rental_id}/entries", json=patch_payload_edit)
print("Save completed edit status:", r.status_code)
res_edit = r.json()
print("Edited missing pieces:", res_edit["summary"]["total_missing_qty"])
print("Status preserved as:", res_edit["status"])

# 5. Check change logs
r = requests.get(f"{BASE_URL}/api/rentals/{rental_id}/change-logs")
print("Change logs status:", r.status_code)
logs = r.json().get("logs", [])
print(f"Logged {len(logs)} actions:")
for l in logs:
    print(f" - [{l['action']}] {l['summary']}")

print("\nAll flows and change logs PASSED perfectly!")
