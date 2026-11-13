import requests

r = requests.get("http://127.0.0.1:5000/api/rentals")
rentals = r.json()["rentals"]
comp_r = next(r for r in rentals if r["status"] == "completed")
print("Testing completed rental:", comp_r["code"], "ID:", comp_r["id"])

cid = comp_r["id"]
patch_res = requests.patch(f"http://127.0.0.1:5000/api/rentals/{cid}/entries", json={"entries": []})
print("Status code:", patch_res.status_code, patch_res.json())
assert patch_res.status_code == 400
assert "không thể chỉnh sửa số lượng" in patch_res.json()["error"]
print("Backend security check passed!")

