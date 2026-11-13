import io
import json
import re
from datetime import datetime
from pathlib import Path
import openpyxl

from database import generate_ticket_code, now_iso


def get_existing_crm_order_nos(conn):
    """Return a set of already imported crm_order_no values from the rentals table."""
    rows = conn.execute(
        "SELECT crm_order_no FROM rentals WHERE crm_order_no IS NOT NULL AND crm_order_no != ''"
    ).fetchall()
    return {r["crm_order_no"].strip().upper() for r in rows if r["crm_order_no"]}


def map_item_to_kit(code: str, desc: str, kits_by_id: dict):
    c = (code or "").upper().strip()
    d = (desc or "").lower().strip()

    if "2671" in c or "nhiệm vụ 2671" in d or "super track props" in d:
        kid = "nhiem_vu_2671"
    elif "2681" in c or "mở rộng 2026" in d or "mở rộng 2681" in d:
        kid = "mo_rong_2681"
    elif "2108" in c or "intelligence storm" in d:
        kid = "intell_e6"
    elif "2508" in c or "innovation storm" in d:
        kid = "inno_e7"
    elif "2321" in c or "alpha set a" in d:
        kid = "alpha_a"
    elif "2323" in c or "alpha set c" in d:
        kid = "alpha_c"
    elif "2421" in c or "beta set" in d:
        kid = "beta_2"
    elif "alpha b" in d or "alpha set b" in d:
        kid = "alpha_b"
    else:
        return None, None

    kit_name = kits_by_id.get(kid, {}).get("name", kid)
    return kid, kit_name


def _build_catalog_item_index(catalog: dict):
    """Flatten all items across catalog kits for fuzzy matching loose accessories."""
    items = []
    for k in catalog.get("kits", []):
        for it in k.get("items", []):
            items.append({
                "kit_id": k["id"],
                "kit_name": k["name"],
                "item_id": it["id"],
                "code": it.get("code", ""),
                "name": it.get("name", ""),
                "unit": it.get("unit", "cái"),
                "unit_price": it.get("unit_price", 0),
                "images": it.get("images", []),
            })
    return items


def find_catalog_match(code: str, desc: str, all_catalog_items: list):
    """Match loose items against standard catalog parts to reuse image, unit and replacement price."""
    c_clean = re.sub(r"-(RENT|RENT2)$", "", (code or "").strip(), flags=re.I).strip().lower()
    d_clean = (desc or "").lower().strip()

    # 1. Exact or substring code match
    if c_clean:
        for it in all_catalog_items:
            it_c = (it.get("code") or "").strip().lower()
            if it_c and (c_clean == it_c or c_clean in it_c):
                return it

    # 2. Key phrases
    if "lithium" in d_clean or "pin e6" in d_clean or "1005" in c_clean:
        for it in all_catalog_items:
            if "lithium" in it["name"].lower():
                return it

    if "tracker" in d_clean or "dò line" in d_clean or "1149" in c_clean:
        for it in all_catalog_items:
            if "tracking" in it["name"].lower():
                return it

    if "3570" in c_clean or ("động cơ lớn" in d_clean and "e6" in d_clean):
        for it in all_catalog_items:
            if "3581" in it["name"].lower() or "motor" in it["name"].lower():
                return it

    return None


def parse_crm_file(file_source, catalog: dict, existing_crm_order_nos: set = None):
    """
    Parse CRM Saleorder Excel file.
    file_source: Path, string path, or file-like / bytes object.
    catalog: catalog dict containing 'kits'.
    existing_crm_order_nos: set of CRM order codes already in DB for deduplication.
    """
    if existing_crm_order_nos is None:
        existing_crm_order_nos = set()

    kits_by_id = {k["id"]: k for k in catalog.get("kits", [])}
    all_catalog_items = _build_catalog_item_index(catalog)

    if isinstance(file_source, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(file_source), data_only=True)
    elif hasattr(file_source, "read"):
        wb = openpyxl.load_workbook(file_source, data_only=True)
    else:
        wb = openpyxl.load_workbook(str(file_source), data_only=True)

    ws_list = wb["Danh sách"] if "Danh sách" in wb.sheetnames else wb.active
    ws_items = wb["Bảng hàng hóa"] if "Bảng hàng hóa" in wb.sheetnames else None

    # 1. Parse items grouped by order_no from Bảng hàng hóa
    order_items_map = {}
    if ws_items:
        for r in range(2, ws_items.max_row + 1):
            raw_no = str(ws_items.cell(r, 1).value or "").strip()
            if not raw_no:
                continue
            order_no = re.sub(r"\s+", " ", raw_no).strip()
            token_key = order_no.split()[0] if order_no.split() else order_no

            code = str(ws_items.cell(r, 2).value or "").strip()
            desc = str(ws_items.cell(r, 3).value or "").strip()
            unit_str = str(ws_items.cell(r, 7).value or "Cái").strip()
            if "/" in unit_str:
                unit_str = unit_str.split("/")[0].strip()

            raw_qty = ws_items.cell(r, 8).value or ws_items.cell(r, 7).value or 1
            try:
                qty = int(raw_qty)
            except (ValueError, TypeError):
                qty = 1

            raw_price = ws_items.cell(r, 10).value or 0
            try:
                price = float(raw_price)
            except (ValueError, TypeError):
                price = 0.0

            if not code:
                continue

            item_obj = {
                "code": code,
                "desc": desc,
                "unit": unit_str or "Cái",
                "qty": max(1, qty),
                "unit_price": price,
            }
            # Map items strictly by order_no (normalized spaces) and raw_no
            if code.upper() in ("DVGIAOHANG", "PHIGIAOHANG"):
                continue

            for k in {order_no, raw_no}:
                if k not in order_items_map:
                    order_items_map[k] = []
                order_items_map[k].append(item_obj)

    # 2. Parse orders list from Danh sách
    orders = []

    def fmt_dt(val):
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d")
        return str(val or "").strip()

    for r in range(2, ws_list.max_row + 1):
        order_no_raw = str(ws_list.cell(r, 2).value or "").strip()
        if not order_no_raw:
            continue

        # Normalized order identifier
        order_key = re.sub(r"\s+", " ", order_no_raw).strip()
        tokens = order_key.split()
        short_token = tokens[0] if tokens else order_key

        customer = str(ws_list.cell(r, 3).value or "").strip()
        date_book = ws_list.cell(r, 6).value
        date_due = ws_list.cell(r, 7).value
        return_status = str(ws_list.cell(r, 8).value or "").strip()
        goods_str = str(ws_list.cell(r, 9).value or "").strip()
        recipient = str(ws_list.cell(r, 10).value or "").strip()
        phone = str(ws_list.cell(r, 11).value or "").strip()
        address = str(ws_list.cell(r, 12).value or "").strip()
        staff = str(ws_list.cell(r, 13).value or "").strip()

        due_date_str = fmt_dt(date_due)
        book_date_str = fmt_dt(date_book)

        matched_items = list(order_items_map.get(order_key) or order_items_map.get(order_no_raw) or [])

        # Cross-check with goods_str from Sheet 1:
        # If Sheet 1 has codes not present in matched_items, add them so no item is ever missed!
        if goods_str:
            existing_codes = {it["code"].upper() for it in matched_items}
            s1_codes = [c.strip() for c in goods_str.split(",") if c.strip()]
            for scode in s1_codes:
                if scode.upper() not in existing_codes and scode.upper() not in ("DVGIAOHANG", "PHIGIAOHANG"):
                    matched_items.append({
                        "code": scode,
                        "desc": scode,
                        "unit": "Cái",
                        "qty": 1,
                        "unit_price": 0.0,
                    })

        kits = []
        extra_items = []
        extra_parts = []
        kit_counter = {}

        if matched_items:
            for it in matched_items:
                kid, kname = map_item_to_kit(it["code"], it["desc"], kits_by_id)
                if kid:
                    for _ in range(it["qty"]):
                        kit_counter[kid] = kit_counter.get(kid, 0) + 1
                        idx = kit_counter[kid]
                        kits.append({
                            "kit_id": kid,
                            "kit_name": kname,
                            "instance_index": idx,
                            "kit_code": "",
                            "source_code": it["code"],
                            "source_desc": it["desc"],
                        })
                else:
                    extra_items.append(it)
                    extra_parts.append(f"{it['desc']} (SL: {it['qty']})")
        elif goods_str:
            for code_part in [c.strip() for c in goods_str.split(",") if c.strip()]:
                kid, kname = map_item_to_kit(code_part, "", kits_by_id)
                if kid:
                    kit_counter[kid] = kit_counter.get(kid, 0) + 1
                    idx = kit_counter[kid]
                    kits.append({
                        "kit_id": kid,
                        "kit_name": kname,
                        "instance_index": idx,
                        "kit_code": "",
                        "source_code": code_part,
                        "source_desc": "",
                    })
                else:
                    extra_item = {
                        "code": code_part,
                        "desc": code_part,
                        "unit": "Cái",
                        "qty": 1,
                        "unit_price": 0.0,
                    }
                    extra_items.append(extra_item)
                    extra_parts.append(code_part)

        # Distinguish identical standard kits as "Bộ 1/2", "Bộ 2/2" etc.
        total_counts = {}
        for k in kits:
            total_counts[k["kit_id"]] = total_counts.get(k["kit_id"], 0) + 1
        for k in kits:
            tot = total_counts[k["kit_id"]]
            k["same_type_total"] = tot
            if tot > 1:
                k["display_name"] = f"{k['kit_name']} (Bộ {k['instance_index']}/{tot})"
            else:
                k["display_name"] = k["kit_name"]

        # Support Loose Accessories Rental Orders as inspectable kits!
        if not kits and extra_items:
            custom_items = []
            for i, it in enumerate(extra_items):
                cat_match = find_catalog_match(it["code"], it["desc"], all_catalog_items)
                price = it.get("unit_price") or 0.0
                images = []
                unit = it.get("unit") or "Cái"
                name = it.get("desc") or it.get("code")

                if cat_match:
                    if price <= 0:
                        price = cat_match.get("unit_price") or 0.0
                    images = cat_match.get("images") or []
                    if not unit or unit == "Cái":
                        unit = cat_match.get("unit") or unit

                custom_items.append({
                    "id": f"loose_{i+1}",
                    "row_num": i + 1,
                    "code": it["code"],
                    "name": name,
                    "unit": unit,
                    "qty": it["qty"],
                    "expected_qty": it["qty"],
                    "unit_price": price,
                    "images": images,
                    "pair_group": None,
                    "is_flat_set_price": False,
                })

            kits.append({
                "kit_id": "phu_kien_le",
                "kit_name": "Phụ kiện & Thiết bị lẻ",
                "instance_index": 1,
                "kit_code": "",
                "same_type_total": 1,
                "display_name": "Phụ kiện & Thiết bị lẻ",
                "is_loose_order": True,
                "custom_items": custom_items,
            })
        elif kits and extra_items:
            # Order has standard robot kits AND extra accessories
            custom_items = []
            for i, it in enumerate(extra_items):
                cat_match = find_catalog_match(it["code"], it["desc"], all_catalog_items)
                price = it.get("unit_price") or 0.0
                images = []
                unit = it.get("unit") or "Cái"
                name = it.get("desc") or it.get("code")

                if cat_match:
                    if price <= 0:
                        price = cat_match.get("unit_price") or 0.0
                    images = cat_match.get("images") or []
                    if not unit or unit == "Cái":
                        unit = cat_match.get("unit") or unit

                custom_items.append({
                    "id": f"extra_{i+1}",
                    "row_num": i + 1,
                    "code": it["code"],
                    "name": name,
                    "unit": unit,
                    "qty": it["qty"],
                    "expected_qty": it["qty"],
                    "unit_price": price,
                    "images": images,
                    "pair_group": None,
                    "is_flat_set_price": False,
                })

            kits.append({
                "kit_id": "phu_kien_di_kem",
                "kit_name": "Phụ kiện đi kèm",
                "instance_index": 1,
                "kit_code": "",
                "same_type_total": 1,
                "display_name": "Phụ kiện đi kèm",
                "is_loose_order": True,
                "custom_items": custom_items,
            })

        note_parts = []
        if extra_parts:
            note_parts.append(f"Phụ kiện: {', '.join(extra_parts)}")
        if staff:
            note_parts.append(f"Sale: {staff}")
        if order_no_raw != order_key:
            note_parts.append(f"HĐ gốc: {order_no_raw}")

        note = " · ".join(note_parts)
        team_name = customer or recipient or f"Đơn hàng {order_key}"
        returner = recipient or customer or "Chưa rõ"

        # Determine import status (new, duplicate, or no_kits)
        is_dup = order_key.upper() in existing_crm_order_nos
        has_kits = len(kits) > 0
        is_loose = any(k.get("kit_id") == "phu_kien_le" for k in kits)
        is_already_returned = ("đã trả toàn bộ" in return_status.lower() or "đã trả hết" in return_status.lower())

        if is_dup:
            status = "duplicate"
            status_label = "Đã tồn tại trong hệ thống (sẽ bỏ qua)"
        elif not has_kits:
            status = "no_kits"
            status_label = "Đơn rỗng (không có linh kiện)"
        elif is_already_returned:
            status = "new"
            status_label = "Mới (Đã trả toàn bộ - Tự hoàn tất)"
        elif is_loose:
            status = "new"
            status_label = "Mới (Thuê phụ kiện lẻ)"
        else:
            status = "new"
            status_label = "Mới - Sẵn sàng nạp"

        orders.append({
            "order_no": order_key,
            "order_no_raw": order_no_raw,
            "customer": customer,
            "team_name": team_name,
            "returner_name": returner,
            "phone": phone or "0900000000",
            "email": "",
            "address": address,
            "due_date": due_date_str,
            "book_date": book_date_str,
            "return_status": return_status,
            "is_already_returned": is_already_returned,
            "note": note,
            "kits": kits,
            "extra_parts": extra_parts,
            "has_kits": has_kits,
            "is_loose": is_loose,
            "status": status,
            "status_label": status_label,
            "can_import": (status == "new"),
        })

    total_orders = len(orders)
    new_count = sum(1 for o in orders if o["status"] == "new")
    duplicate_count = sum(1 for o in orders if o["status"] == "duplicate")
    loose_count = sum(1 for o in orders if o.get("is_loose"))
    no_kits_count = sum(1 for o in orders if o["status"] == "no_kits")

    return {
        "total_orders": total_orders,
        "new_count": new_count,
        "duplicate_count": duplicate_count,
        "loose_count": loose_count,
        "no_kits_count": no_kits_count,
        "orders": orders,
    }


def import_crm_orders(conn, catalog: dict, orders_data: list, actor: str = "Người kiểm kê"):
    """
    Import parsed orders into rentals and rental_kits.
    Strictly skips duplicates already in the database.
    """
    current_existing = get_existing_crm_order_nos(conn)
    imported_tickets = []
    duplicate_skipped = 0
    no_kits_skipped = 0

    now = now_iso()

    for order in orders_data:
        order_key = order["order_no"].strip().upper()
        if order_key in current_existing:
            duplicate_skipped += 1
            continue

        kits = order.get("kits", [])
        if not kits:
            no_kits_skipped += 1
            continue

        # Use exact CRM order number as ticket code so staff can search and reference directly!
        ticket_code = order["order_no"]

        is_already_returned = order.get("is_already_returned") or ("đã trả toàn bộ" in str(order.get("return_status") or "").lower())
        order_status = "completed" if is_already_returned else "pending"
        completed_at = now if is_already_returned else None

        init_snapshot_json = None
        if is_already_returned:
            init_snapshot = {
                "round_number": 1,
                "round_name": "Kiểm kê ban đầu khi nhận hàng trả",
                "actor": "Hệ thống (CRM - Đã trả toàn bộ)",
                "created_at": now,
                "shortages": [],
                "total_missing_qty": 0,
                "total_compensation": 0,
            }
            init_snapshot_json = json.dumps(init_snapshot, ensure_ascii=False)

        cur = conn.execute(
            """
            INSERT INTO rentals (
                code, crm_order_no, returner_name, team_name, phone, email,
                delivery_address, due_date, note, status, completed_at, initial_inspection_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_code,
                order["order_no"],
                order["returner_name"],
                order["team_name"],
                order["phone"],
                order.get("email", ""),
                order.get("address", ""),
                order.get("due_date", ""),
                order.get("note", ""),
                order_status,
                completed_at,
                init_snapshot_json,
                now,
                now,
            ),
        )
        rental_id = cur.lastrowid

        # Insert rental kits (standard or custom loose items)
        for k in kits:
            custom_json = (
                json.dumps(k["custom_items"], ensure_ascii=False)
                if k.get("custom_items")
                else None
            )
            conn.execute(
                """
                INSERT INTO rental_kits (
                    rental_id, kit_id, kit_name, instance_index, kit_code, kit_count, custom_items_json
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    rental_id,
                    k["kit_id"],
                    k["kit_name"],
                    k.get("instance_index", 1),
                    k.get("kit_code", ""),
                    custom_json,
                ),
            )

        # Log creation
        kit_names_summary = ", ".join(f"{k['kit_name']} ({k.get('kit_code', '')})" for k in kits)
        conn.execute(
            """
            INSERT INTO rental_change_logs (
                rental_id, action, actor, actor_role, summary, details, created_at
            ) VALUES (?, 'IMPORT_CRM', ?, 'staff', ?, ?, ?)
            """,
            (
                rental_id,
                actor,
                f"Nạp phiếu từ CRM đơn {order['order_no']}: {len(kits)} bộ ({order['team_name']})",
                json.dumps({"order_no": order["order_no"], "kits": kit_names_summary}, ensure_ascii=False),
                now,
            ),
        )

        current_existing.add(order_key)
        imported_tickets.append({
            "ticket_code": ticket_code,
            "crm_order_no": order["order_no"],
            "team_name": order["team_name"],
            "kits_count": len(kits),
        })

    return {
        "imported_count": len(imported_tickets),
        "duplicate_skipped_count": duplicate_skipped,
        "no_kits_skipped_count": no_kits_skipped,
        "imported_tickets": imported_tickets,
    }
