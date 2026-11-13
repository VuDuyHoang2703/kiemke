import requests

BASE = "http://127.0.0.1:5000"

# 1. Create order
r = requests.post(f"{BASE}/api/rentals", json={
    "team_name": "Đội Test Quy Trình",
    "returner_name": "Trần Văn A",
    "phone": "0912345678",
    "kits": [{"kit_id": "alpha_a", "kit_code": "ALPHA-99"}]
})
assert r.status_code == 201, r.text
rental = r.json()["rental"]
rid = rental["id"]
rk_id = rental["kit_details"][0]["rental_kit_id"]
print(f"Created rental {rid}")

# 2. In inspecting status, try supplementary preview -> MUST FAIL (status != completed)
r = requests.get(f"{BASE}/api/rentals/{rid}/supplementary-preview")
print("Supplementary preview before complete:", r.status_code, r.json())
assert r.status_code == 400

# 3. Enter shortage
item0 = rental["kit_details"][0]["items"][0]
r = requests.patch(f"{BASE}/api/rentals/{rid}/entries", json={
    "entries": [{"rental_kit_id": rk_id, "item_id": item0["id"], "kit_id": "alpha_a", "actual_input": "-1"}]
})
assert r.status_code == 200

# 4. Complete inspection & record
r = requests.post(f"{BASE}/api/rentals/{rid}/status", json={"status": "completed"})
assert r.status_code == 200
completed_rental = r.json()["rental"]
init_insp = completed_rental.get("initial_inspection") or {}
print("Initial inspection round name:", init_insp.get("round_name"))
assert "Lần 1" not in init_insp.get("round_name", "")

# 5. Now supplementary preview is allowed
r = requests.get(f"{BASE}/api/rentals/{rid}/supplementary-preview")
assert r.status_code == 200
prev_data = r.json()
print("Next round name in preview:", prev_data.get("next_round_name"))
assert prev_data.get("next_round_name") == "Trả bổ sung lần 1"

# 6. Submit supplementary return
r = requests.post(f"{BASE}/api/rentals/{rid}/supplementary-return", json={
    "returned_items": [{"rental_kit_id": rk_id, "item_id": item0["id"], "returned_qty": 1}],
    "actor": "Nhân viên B",
    "note": "Khách trả thêm đủ"
})
assert r.status_code == 200
supp_res = r.json()
print("Supplementary result message:", supp_res.get("message"))
print("New missing qty:", supp_res["rental"]["summary"]["total_missing_qty"])

# Clean up
import sqlite3
conn = sqlite3.connect("data/inventory.db")
conn.execute(f"DELETE FROM rentals WHERE id = {rid}")
conn.execute(f"DELETE FROM rental_kits WHERE rental_id = {rid}")
conn.execute(f"DELETE FROM inspection_entries WHERE rental_id = {rid}")
conn.execute(f"DELETE FROM rental_change_logs WHERE rental_id = {rid}")
conn.execute(f"DELETE FROM supplementary_returns WHERE rental_id = {rid}")
conn.commit()
conn.close()
print("Test completed and cleaned up successfully!")

