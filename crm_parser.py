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


def classify_crm_line_item(code: str, desc: str, kits_by_id: dict):
    """
    Phân tích chi tiết hàng thuê từ Diễn giải và Mã hàng hóa CRM.
    Tập trung trích xuất từ khóa chính từ 'Diễn giải' để nhận diện đúng bộ robot hoặc linh kiện lẻ,
    không phụ thuộc hoàn toàn vào mã hàng hóa (vì các bộ kit trong catalog không có mã nội bộ của CRM).
    """
    c = (code or "").upper().strip()
    d = (desc or "").strip()
    d_lower = d.lower()

    if c in ("DVGIAOHANG", "PHIGIAOHANG") or "phí giao hàng" in d_lower:
        return "SERVICE", None, None

    # Các từ khóa chỉ rõ đây là linh kiện lẻ / thiết bị bổ sung đi kèm, không phải cả bộ robot hoàn chỉnh
    is_component = any(w in d_lower for w in [
        "dùng kết hợp cho bộ", "dùng kết hợp", "dùng trong bộ", "sử dụng cùng", 
        "cảm biến", "động cơ", "pin lithium", "mô-đun", "sa bàn", "tay cầm", "tấm plate", "bánh xe", "servo"
    ])

    if not is_component:
        # Nhận diện các bộ Kit hoàn chỉnh từ Diễn giải hoặc mã
        if "alpha set a" in d_lower or "alpha a" in d_lower or "2321" in c:
            kname = kits_by_id.get("alpha_a", {}).get("name", "ALPHA A")
            return "KIT", "alpha_a", kname
        if "alpha set b" in d_lower or "alpha b" in d_lower:
            kname = kits_by_id.get("alpha_b", {}).get("name", "ALPHA B")
            return "KIT", "alpha_b", kname
        if "alpha set c" in d_lower or "alpha c" in d_lower or "2323" in c:
            kname = kits_by_id.get("alpha_c", {}).get("name", "ALPHA C")
            return "KIT", "alpha_c", kname
        if "beta set" in d_lower or "beta" in d_lower or "2421" in c:
            kname = kits_by_id.get("beta", {}).get("name", "BETA")
            return "KIT", "beta", kname
        if "intelligence storm" in d_lower or "intelligent storm" in d_lower or "2108" in c:
            kname = kits_by_id.get("intell_e6", {}).get("name", "INTELL (E6)")
            return "KIT", "intell_e6", kname
        if "innovation storm" in d_lower or "inno" in d_lower or "2508" in c:
            kname = kits_by_id.get("inno_e7", {}).get("name", "INNO (E7)")
            return "KIT", "inno_e7", kname
        if "mở rộng 2026" in d_lower or "mở rộng 2681" in d_lower or "bộ công cụ mở rộng" in d_lower or "2681" in c:
            kname = kits_by_id.get("mo_rong_2681", {}).get("name", "MỞ RỘNG 2681")
            return "KIT", "mo_rong_2681", kname
        if "nhiệm vụ 2026" in d_lower or "nhiệm vụ 2671" in d_lower or "super track props" in d_lower or "bộ nhiệm vụ" in d_lower or "2671" in c:
            kname = kits_by_id.get("nhiem_vu_2671", {}).get("name", "Bộ nhiệm vụ 2671")
            return "KIT", "nhiem_vu_2671", kname

    # Linh kiện lẻ / Thiết bị thuê ngoài bộ: trích xuất tên hiển thị sạch và gọn từ Diễn giải
    clean_name = d
    clean_name = re.sub(r"-\s*Mã:\s*.*$", "", clean_name, flags=re.I).strip()
    clean_name = re.sub(r"\(Mã:\s*.*?\)", "", clean_name, flags=re.I).strip()
    clean_name = re.sub(r"\(cho thuê.*?\)", "", clean_name, flags=re.I).strip()
    clean_name = re.sub(r"\(đã qua sử dụng.*?\)", "", clean_name, flags=re.I).strip()
    clean_name = re.sub(r"\s+", " ", clean_name).strip()
    if not clean_name:
        clean_name = d or c

    return "ITEM", code, clean_name


def map_item_to_kit(code: str, desc: str, kits_by_id: dict):
    typ, kid, kname = classify_crm_line_item(code, desc, kits_by_id)
    if typ == "KIT":
        return kid, kname
    return None, None


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
    c_clean = re.sub(r"-(RENT|RENT2|USED)$", "", (code or "").strip(), flags=re.I).strip().lower()
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

    if "màu sắc" in d_clean or "color sensor" in d_clean or "1147" in c_clean:
        for it in all_catalog_items:
            if "color" in it["name"].lower():
                return it

    if "3570" in c_clean or ("động cơ lớn" in d_clean and "e6" in d_clean):
        for it in all_catalog_items:
            if "large motor" in it["name"].lower():
                return it

    if "3581" in c_clean or "động cơ giảm tốc trung bình" in d_clean:
        for it in all_catalog_items:
            if "medium motor" in it["name"].lower():
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

    ws_list = None
    ws_items = None
    for name in wb.sheetnames:
        clean = name.strip().lower()
        if clean in ("danh sách", "danh sach", "orders", "danh sách đơn hàng", "ds"):
            ws_list = wb[name]
        elif clean in ("bảng hàng hóa", "bang hang hoa", "hàng hóa", "hang hoa", "chi tiết", "chi tiet", "items"):
            ws_items = wb[name]

    if ws_list is None:
        ws_list = wb.active
    if ws_items is None and len(wb.sheetnames) > 1:
        for name in wb.sheetnames:
            if wb[name] != ws_list:
                ws_items = wb[name]
                break

    # 1. Detect columns and parse items grouped by order_no from Bảng hàng hóa
    order_items_map = {}
    if ws_items:
        items_cols = {
            "order_col": 1,
            "code_col": 2,
            "desc_col": 3,
            "unit_col": 7,
            "qty_col": 8,
            "price_col": 10,
        }
        for c in range(1, min(30, ws_items.max_column + 1)):
            h = str(ws_items.cell(1, c).value or "").strip().lower()
            if not h:
                continue
            if any(k in h for k in ("số đơn hàng", "mã đơn hàng", "số đơn")):
                items_cols["order_col"] = c
            elif any(k in h for k in ("mã hàng hóa", "mã hàng", "mã sản phẩm")):
                items_cols["code_col"] = c
            elif any(k in h for k in ("diễn giải", "tên hàng hóa", "tên hàng", "mô tả")):
                items_cols["desc_col"] = c
            elif any(k in h for k in ("đơn vị tính", "đvt")):
                items_cols["unit_col"] = c
            elif any(k in h for k in ("số lượng", "sl")):
                if "giao" not in h:
                    items_cols["qty_col"] = c
            elif any(k in h for k in ("đơn giá", "giá")):
                if "sau" not in h:
                    items_cols["price_col"] = c

        for r in range(2, ws_items.max_row + 1):
            raw_no = str(ws_items.cell(r, items_cols["order_col"]).value or "").strip()
            if not raw_no:
                continue
            order_no = re.sub(r"\s+", " ", raw_no).strip()
            token_key = order_no.split()[0] if order_no.split() else order_no

            code = str(ws_items.cell(r, items_cols["code_col"]).value or "").strip()
            desc = str(ws_items.cell(r, items_cols["desc_col"]).value or "").strip()
            unit_str = str(ws_items.cell(r, items_cols["unit_col"]).value or "Cái").strip()
            if "/" in unit_str:
                unit_str = unit_str.split("/")[0].strip()

            raw_qty = ws_items.cell(r, items_cols["qty_col"]).value or 1
            try:
                qty = int(raw_qty)
            except (ValueError, TypeError):
                qty = 1

            raw_price = ws_items.cell(r, items_cols["price_col"]).value or 0
            try:
                price = float(raw_price)
            except (ValueError, TypeError):
                price = 0.0

            if not code and not desc:
                continue
            if not code:
                code = desc[:30].strip()

            item_obj = {
                "code": code,
                "desc": desc,
                "unit": unit_str or "Cái",
                "qty": max(1, qty),
                "unit_price": price,
            }
            # Map items strictly by order_no (normalized spaces), raw_no, and token_key
            if code.upper() in ("DVGIAOHANG", "PHIGIAOHANG"):
                continue

            for k in {order_no, raw_no, token_key}:
                if k not in order_items_map:
                    order_items_map[k] = []
                order_items_map[k].append(item_obj)

    # 2. Parse orders list from Danh sách
    orders = []

    list_cols = {
        "order_col": 2,
        "customer_col": 3,
        "book_date_col": 6,
        "due_date_col": 7,
        "return_status_col": 8,
        "goods_col": 9,
        "recipient_col": 10,
        "phone_col": 11,
        "address_col": 12,
        "staff_col": 13,
    }
    for c in range(1, min(25, ws_list.max_column + 1)):
        h = str(ws_list.cell(1, c).value or "").strip().lower()
        if not h:
            continue
        if any(k in h for k in ("số đơn hàng", "mã đơn hàng", "số đơn")):
            list_cols["order_col"] = c
        elif any(k in h for k in ("khách hàng", "tên khách", "đội thuê")):
            list_cols["customer_col"] = c
        elif any(k in h for k in ("ngày ghi sổ", "ngày ghi", "ngày đặt")):
            list_cols["book_date_col"] = c
        elif any(k in h for k in ("đến hạn trả", "hạn trả")):
            list_cols["due_date_col"] = c
        elif any(k in h for k in ("tình trạng hoàn trả", "hoàn trả")):
            list_cols["return_status_col"] = c
        elif any(k in h for k in ("hàng hóa", "sản phẩm")):
            list_cols["goods_col"] = c
        elif any(k in h for k in ("người nhận hàng", "người nhận")):
            list_cols["recipient_col"] = c
        elif any(k in h for k in ("điện thoại", "sđt")):
            list_cols["phone_col"] = c
        elif any(k in h for k in ("địa chỉ",)):
            list_cols["address_col"] = c
        elif any(k in h for k in ("người thực hiện", "nhân viên", "sale")):
            list_cols["staff_col"] = c

    def fmt_dt(val):
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d")
        return str(val or "").strip()

    for r in range(2, ws_list.max_row + 1):
        order_no_raw = str(ws_list.cell(r, list_cols["order_col"]).value or "").strip()
        if not order_no_raw:
            continue

        # Normalized order identifier
        order_key = re.sub(r"\s+", " ", order_no_raw).strip()
        tokens = order_key.split()
        short_token = tokens[0] if tokens else order_key

        customer = str(ws_list.cell(r, list_cols["customer_col"]).value or "").strip()
        date_book = ws_list.cell(r, list_cols["book_date_col"]).value
        date_due = ws_list.cell(r, list_cols["due_date_col"]).value
        return_status = str(ws_list.cell(r, list_cols["return_status_col"]).value or "").strip()
        goods_str = str(ws_list.cell(r, list_cols["goods_col"]).value or "").strip()
        recipient = str(ws_list.cell(r, list_cols["recipient_col"]).value or "").strip()
        phone = str(ws_list.cell(r, list_cols["phone_col"]).value or "").strip()
        address = str(ws_list.cell(r, list_cols["address_col"]).value or "").strip()
        staff = str(ws_list.cell(r, list_cols["staff_col"]).value or "").strip()

        due_date_str = fmt_dt(date_due)
        book_date_str = fmt_dt(date_book)

        raw_matched = list(
            order_items_map.get(order_key)
            or order_items_map.get(order_no_raw)
            or order_items_map.get(short_token)
            or []
        )
        # Deduplicate matched items by (code, desc, qty, unit_price)
        seen_matched = set()
        matched_items = []
        for it in raw_matched:
            mkey = (it.get("code"), it.get("desc"), it.get("qty"), it.get("unit_price"))
            if mkey not in seen_matched:
                seen_matched.add(mkey)
                matched_items.append(it)

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
                _, _, clean_name = classify_crm_line_item(it["code"], it["desc"], kits_by_id)
                name = clean_name or it.get("desc") or it.get("code")

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
                _, _, clean_name = classify_crm_line_item(it["code"], it["desc"], kits_by_id)
                name = clean_name or it.get("desc") or it.get("code")

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

        if not kits:
            # Fallback placeholder kit so staff can always open, inspect, and add items manually!
            placeholder_name = f"Thiết bị thuê (Đơn {order_key})"
            kits.append({
                "kit_id": "thiet_bi_thue",
                "kit_name": placeholder_name,
                "instance_index": 1,
                "same_type_total": 1,
                "display_name": placeholder_name,
                "is_loose_order": True,
                "custom_items": [{
                    "id": "item_1",
                    "row_num": 1,
                    "code": order_key,
                    "name": placeholder_name,
                    "unit": "Bộ",
                    "qty": 1,
                    "expected_qty": 1,
                    "unit_price": 0.0,
                    "images": [],
                    "pair_group": None,
                    "is_flat_set_price": False,
                }],
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
            status_label = "Mới (Khách đã trả thiết bị - Chờ kiểm kê)"
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
        return_status = "Đã trả" if is_already_returned else "Chưa trả"
        # "Đã trả toàn bộ" = đã trả thiết bị về kho, không đồng nghĩa là đã kiểm kê
        # Trạng thái kiểm kê luôn bắt đầu bằng 'pending' để nhân viên kiểm tra đủ/thiếu
        order_status = "pending"
        completed_at = None
        init_snapshot_json = None

        orig_info = {
            "team_name": order["team_name"],
            "returner_name": order["returner_name"],
            "phone": order["phone"],
            "email": order.get("email", ""),
            "due_date": order.get("due_date", ""),
            "delivery_address": order.get("address", ""),
            "note": order.get("note", ""),
            "return_status": return_status,
        }
        orig_info_json = json.dumps(orig_info, ensure_ascii=False)

        cur = conn.execute(
            """
            INSERT INTO rentals (
                code, crm_order_no, returner_name, team_name, phone, email,
                delivery_address, due_date, note, status, return_status,
                completed_at, initial_inspection_json, original_info_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                return_status,
                completed_at,
                init_snapshot_json,
                orig_info_json,
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
