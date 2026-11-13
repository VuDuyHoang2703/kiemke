import io
import json
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, send_file, send_from_directory, session
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

from database import (
    RETENTION_DAYS,
    add_days_iso,
    build_rental_detail,
    generate_ticket_code,
    get_change_logs,
    get_conn,
    get_supplementary_returns,
    init_db,
    now_iso,
    purge_expired_deleted_rentals,
    record_change_log,
)
from excel_parser import parse_workbook, resolve_kit_cover_image
from crm_parser import get_existing_crm_order_nos, import_crm_orders, parse_crm_file

BASE_DIR = Path(__file__).resolve().parent
EXCEL_PATH = BASE_DIR / "KIEM KE HANG THUE.xlsx"
SAMPLE_CRM_PATH = BASE_DIR / "CRM_Saleorder_12.11.2026_13.57.52_512.xlsx"
STATIC_DIR = BASE_DIR / "static"
IMAGE_DIR = BASE_DIR / "Image"
DATA_DIR = BASE_DIR / "data"
CATALOG_PATH = DATA_DIR / "catalog.json"
DB_PATH = DATA_DIR / "inventory.db"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(val: str) -> bool:
    return bool(val and EMAIL_RE.fullmatch(val.strip()))


def get_current_actor() -> str:
    """
    Trích xuất danh tính người thực hiện thao tác.
    Được thiết kế sẵn để dễ dàng tích hợp sau này với các hệ thống:
    1. Body JSON: trường 'actor', 'changed_by', 'inspector_name' (hỗ trợ Unicode tiếng Việt đầy đủ)
    2. Header HTTP: 'X-User-Name', 'X-Actor', 'X-Staff-Name' (tự động unquote nếu là URL-encoded)
    3. Flask g hoặc session (khi có module đăng nhập tài khoản)
    4. Mặc định: 'Người kiểm kê'
    """
    if hasattr(g, "user") and g.user:
        if isinstance(g.user, dict):
            return str(g.user.get("name") or g.user.get("username") or "Người kiểm kê").strip()
        return str(g.user).strip()

    if request.is_json:
        data = request.get_json(silent=True) or {}
        for key in ("actor", "changed_by", "inspector_name", "user_name"):
            val = data.get(key)
            if val and str(val).strip():
                return str(val).strip()

    for header in ("X-User-Name", "X-Actor", "X-Staff-Name"):
        val = request.headers.get(header)
        if val and val.strip():
            try:
                return urllib.parse.unquote(val.strip())
            except Exception:
                return val.strip()

    return "Người kiểm kê"


app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(BASE_DIR / "templates"))
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

init_db(DB_PATH)


def load_catalog(force_reload=False):
    catalog = None
    if not force_reload and CATALOG_PATH.exists():
        try:
            catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            catalog = None
    if force_reload or not catalog or int(catalog.get("catalog_version") or 1) < 3:
        catalog = parse_workbook(EXCEL_PATH, STATIC_DIR, CATALOG_PATH)
    else:
        for k in catalog.get("kits", []):
            cover = resolve_kit_cover_image(
                IMAGE_DIR,
                k.get("sheet_name") or k.get("name", ""),
                k.get("id", ""),
            )
            if cover:
                k["preview_image"] = cover
    by_id = {k["id"]: k for k in catalog["kits"]}
    return catalog, by_id


@app.route("/kit-covers/<path:filename>")
def serve_kit_cover(filename):
    return send_from_directory(IMAGE_DIR, filename)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/catalog", methods=["GET"])
def api_get_catalog():
    catalog, _ = load_catalog()
    # Return kit summaries + items
    return jsonify(catalog)


@app.route("/api/catalog/reload", methods=["POST"])
def api_reload_catalog():
    try:
        catalog, _ = load_catalog(force_reload=True)
        return jsonify({"ok": True, "catalog": catalog})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/rentals", methods=["GET"])
def api_list_rentals():
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        rows = conn.execute(
            "SELECT * FROM rentals WHERE deleted_at IS NULL ORDER BY id DESC"
        ).fetchall()
        del_cnt_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM rentals WHERE deleted_at IS NOT NULL"
        ).fetchone()
        deleted_count = int(del_cnt_row["cnt"]) if del_cnt_row else 0
        result = []
        for r in rows:
            detail = build_rental_detail(conn, r, catalog_by_id)
            result.append(
                {
                    "id": detail["id"],
                    "code": detail["code"],
                    "crm_order_no": detail.get("crm_order_no") or "",
                    "due_date": detail.get("due_date") or "",
                    "delivery_address": detail.get("delivery_address") or "",
                    "returner_name": detail["returner_name"],
                    "team_name": detail["team_name"],
                    "phone": detail["phone"],
                    "email": detail.get("email") or "",
                    "note": detail["note"],
                    "status": detail["status"],
                    "created_at": detail["created_at"],
                    "updated_at": detail["updated_at"],
                    "completed_at": detail["completed_at"],
                    "kits": detail["kits"],
                    "summary": detail["summary"],
                }
            )
    return jsonify(
        {
            "rentals": result,
            "deleted_count": deleted_count,
            "retention_days": RETENTION_DAYS,
        }
    )


def validate_unique_kit_codes(
    conn,
    entries,
    exclude_rental_id=None,
    exclude_rk_id=None,
    current_rental_id=None,
):
    """
    Enforce uniqueness of (kit_id, normalized kit_code):
    - Same kit type (tên bộ) CANNOT share the same kit_code within a ticket or across tickets.
    - Different kit types (e.g. ALPHA A vs ALPHA B) CAN share the same kit_code.
    - Multiple tickets for the same team/person ARE allowed as long as each (kit_id, kit_code) is unique.
    """
    seen = {}
    for kit_id, kit_name, kit_code in entries:
        code_clean = str(kit_code or "").strip()
        if not code_clean:
            continue
        key = (kit_id, code_clean.lower())
        if key in seen:
            return (
                f'Cùng một bộ "{kit_name}" không thể có 2 mã giống nhau ("{code_clean}"). '
                f"Mỗi bộ cùng tên phải có mã riêng; khác tên bộ mới được trùng mã.",
                {
                    "scope": "self",
                    "kit_id": kit_id,
                    "kit_name": kit_name,
                    "kit_code": code_clean,
                },
            )
        seen[key] = code_clean

    sql = """
        SELECT rk.id, rk.rental_id, rk.kit_id, rk.kit_name, rk.kit_code,
               r.code AS ticket_code, r.team_name, r.returner_name
        FROM rental_kits rk
        JOIN rentals r ON r.id = rk.rental_id
        WHERE r.deleted_at IS NULL
          AND TRIM(COALESCE(rk.kit_code, '')) != ''
    """
    params = []
    if exclude_rental_id is not None:
        sql += " AND rk.rental_id != ?"
        params.append(exclude_rental_id)
    if exclude_rk_id is not None:
        sql += " AND rk.id != ?"
        params.append(exclude_rk_id)

    existing_rows = conn.execute(sql, params).fetchall()
    existing_map = {
        (r["kit_id"], str(r["kit_code"]).strip().lower()): dict(r)
        for r in existing_rows
    }

    for kit_id, kit_name, kit_code in entries:
        code_clean = str(kit_code or "").strip()
        if not code_clean:
            continue
        key = (kit_id, code_clean.lower())
        if key in existing_map:
            c = existing_map[key]
            if current_rental_id is not None and c["rental_id"] == current_rental_id:
                msg = (
                    f'Cùng một bộ "{kit_name}" trong phiếu này đã có mã "{code_clean}". '
                    f"Không thể đặt 2 mã giống nhau cho cùng tên bộ."
                )
                scope = "self"
            else:
                msg = (
                    f'Bộ "{kit_name}" có mã "{code_clean}" đã tồn tại ở phiếu '
                    f'{c["ticket_code"]} (Đội: {c["team_name"]} · Người trả: {c["returner_name"]}). '
                    f"Cùng một tên bộ không thể có 2 mã giống nhau!"
                )
                scope = "existing"
            return (
                msg,
                {
                    "scope": scope,
                    "kit_id": kit_id,
                    "kit_name": kit_name,
                    "kit_code": code_clean,
                    "rental_id": c["rental_id"],
                    "ticket_code": c["ticket_code"],
                    "team_name": c["team_name"],
                    "returner_name": c["returner_name"],
                },
            )
    return None, None


@app.route("/api/rentals", methods=["POST"])
def api_create_rental():
    data = request.get_json(force=True) or {}
    returner_name = (data.get("returner_name") or "").strip()
    team_name = (data.get("team_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    note = (data.get("note") or "").strip()
    kits = data.get("kits") or []

    if not returner_name:
        return jsonify({"error": "Vui lòng nhập tên người trả hàng"}), 400
    if not team_name:
        return jsonify({"error": "Vui lòng nhập tên đội"}), 400
    if not phone:
        return jsonify({"error": "Vui lòng nhập số điện thoại"}), 400
    if email and not is_valid_email(email):
        return jsonify({"error": "Địa chỉ Gmail/Email không hợp lệ"}), 400
    if not kits:
        return jsonify({"error": "Vui lòng chọn ít nhất 1 bộ robot đã thuê"}), 400

    _, catalog_by_id = load_catalog()
    expanded_kits = []  # (kit_id, kit_name, kit_code)
    for k in kits:
        if not isinstance(k, dict):
            continue
        kid = k.get("kit_id")
        if kid not in catalog_by_id:
            continue
        kname = catalog_by_id[kid]["name"]
        if isinstance(k.get("kit_codes"), list):
            cnt = max(1, int(k.get("kit_count") or len(k["kit_codes"]) or 1))
            codes = [str(c or "").strip() for c in k["kit_codes"]]
            for i in range(cnt):
                expanded_kits.append((kid, kname, codes[i] if i < len(codes) else ""))
        elif "kit_count" in k and int(k.get("kit_count") or 1) > 1:
            cnt = max(1, int(k.get("kit_count") or 1))
            first_code = str(k.get("kit_code") or "").strip()
            for i in range(cnt):
                expanded_kits.append((kid, kname, first_code if i == 0 else ""))
        else:
            kcode = str(k.get("kit_code") or "").strip()
            expanded_kits.append((kid, kname, kcode))

    if not expanded_kits:
        return jsonify({"error": "Danh sách bộ robot không hợp lệ"}), 400

    missing_code_kits = [kname for _kid, kname, kcode in expanded_kits if not kcode]
    if missing_code_kits:
        return (
            jsonify(
                {
                    "error": f"Vui lòng nhập mã cho tất cả các bộ robot đã chọn (còn thiếu mã của bộ '{missing_code_kits[0]}')."
                }
            ),
            400,
        )

    ts = now_iso()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        dup_err, dup_conflict = validate_unique_kit_codes(conn, expanded_kits)
        if dup_err:
            return jsonify({"error": dup_err, "conflict": dup_conflict}), 400

        rental_id = None
        code = None
        for attempt in range(5):
            code = generate_ticket_code(conn)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO rentals (code, returner_name, team_name, phone, email, note, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (code, returner_name, team_name, phone, email, note, ts, ts),
                )
                rental_id = cur.lastrowid
                break
            except sqlite3.IntegrityError as err:
                if "rentals.code" in str(err) and attempt < 4:
                    continue
                raise
        idx_map = {}
        for kid, kname, kcode in expanded_kits:
            idx = idx_map.get(kid, 0) + 1
            idx_map[kid] = idx
            conn.execute(
                """
                INSERT INTO rental_kits (rental_id, kit_id, kit_name, instance_index, kit_code, kit_count)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (rental_id, kid, kname, idx, kcode),
            )
        row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        detail = build_rental_detail(conn, row, catalog_by_id)

        actor = get_current_actor()
        record_change_log(
            conn,
            rental_id,
            action="CREATE_RENTAL",
            actor=actor,
            summary=f"Tiếp nhận trả hàng từ đội '{team_name}' ({len(expanded_kits)} bộ robot)",
            details=json.dumps(
                {
                    "returner": returner_name,
                    "phone": phone,
                    "team": team_name,
                    "kits": [f"{k[1]} ({k[2]})" if k[2] else k[1] for k in expanded_kits],
                },
                ensure_ascii=False,
            ),
            created_at=ts,
        )

    return jsonify({"rental": detail}), 201


@app.route("/api/rentals/<int:rental_id>", methods=["GET"])
def api_get_rental(rental_id):
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê"}), 404
        detail = build_rental_detail(conn, row, catalog_by_id)
    return jsonify({"rental": detail})


@app.route("/api/rentals/<int:rental_id>", methods=["PUT"])
def api_update_rental(rental_id):
    return (
        jsonify(
            {
                "error": "Toàn bộ thông tin phiếu đã tiếp nhận không được phép chỉnh sửa. Nếu nhập sai, vui lòng xoá phiếu cũ (có xác nhận lý do) và lập phiếu mới."
            }
        ),
        403,
    )


@app.route("/api/rentals/<int:rental_id>", methods=["DELETE"])
def api_delete_rental(rental_id):
    return (
        jsonify(
            {
                "error": "Hệ thống không cho phép xóa phiếu kiểm kê. Bạn chỉ có thể chỉnh sửa số lượng hoặc thông tin phiếu."
            }
        ),
        403,
    )


@app.route("/api/deleted-rentals", methods=["GET"])
def api_list_deleted_rentals():
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        rows = conn.execute(
            "SELECT * FROM rentals WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
        ).fetchall()
        result = []
        for r in rows:
            detail = build_rental_detail(conn, r, catalog_by_id)
            result.append(
                {
                    "id": detail["id"],
                    "code": detail["code"],
                    "crm_order_no": detail.get("crm_order_no") or "",
                    "due_date": detail.get("due_date") or "",
                    "delivery_address": detail.get("delivery_address") or "",
                    "returner_name": detail["returner_name"],
                    "team_name": detail["team_name"],
                    "phone": detail["phone"],
                    "email": detail.get("email") or "",
                    "note": detail["note"],
                    "status": detail["status"],
                    "created_at": detail["created_at"],
                    "updated_at": detail["updated_at"],
                    "completed_at": detail["completed_at"],
                    "deleted_at": detail["deleted_at"],
                    "delete_reason": detail.get("delete_reason") or "",
                    "deleted_expires_at": detail.get("deleted_expires_at"),
                    "days_remaining": detail.get("days_remaining", 0),
                    "kits": detail["kits"],
                    "summary": detail["summary"],
                }
            )
    return jsonify({"deleted_rentals": result, "retention_days": RETENTION_DAYS})


@app.route("/api/deleted-rentals/<int:rental_id>", methods=["GET"])
def api_get_deleted_rental(rental_id):
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NOT NULL", (rental_id,)
        ).fetchone()
        if not row:
            return (
                jsonify(
                    {
                        "error": "Không tìm thấy phiếu trong mục đã xoá (hoặc đã quá thời hạn 90 ngày)."
                    }
                ),
                404,
            )
        detail = build_rental_detail(conn, row, catalog_by_id)
    return jsonify({"rental": detail, "retention_days": RETENTION_DAYS})


@app.route("/api/deleted-rentals/<int:rental_id>/restore", methods=["POST"])
def api_restore_deleted_rental(rental_id):
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NOT NULL", (rental_id,)
        ).fetchone()
        if not row:
            return (
                jsonify(
                    {
                        "error": "Không tìm thấy phiếu trong mục đã xoá (hoặc đã quá thời hạn 90 ngày)."
                    }
                ),
                404,
            )

        rk_rows = conn.execute(
            "SELECT kit_id, kit_name, kit_code FROM rental_kits WHERE rental_id = ? ORDER BY id",
            (rental_id,),
        ).fetchall()
        restore_entries = [
            (r["kit_id"], r["kit_name"], r["kit_code"]) for r in rk_rows
        ]
        dup_err, dup_conflict = validate_unique_kit_codes(
            conn, restore_entries, exclude_rental_id=rental_id
        )
        if dup_err:
            return (
                jsonify(
                    {
                        "error": f"Không thể khôi phục phiếu: {dup_err}",
                        "conflict": dup_conflict,
                    }
                ),
                400,
            )

        conn.execute(
            """
            UPDATE rentals
            SET deleted_at = NULL, delete_reason = '', deleted_expires_at = NULL
            WHERE id = ?
            """,
            (rental_id,),
        )
        restored_row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        detail = build_rental_detail(conn, restored_row, catalog_by_id)

    return jsonify({"ok": True, "rental": detail})


@app.route("/api/deleted-rentals/<int:rental_id>", methods=["DELETE"])
def api_purge_deleted_rental(rental_id):
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, code FROM rentals WHERE id = ? AND deleted_at IS NOT NULL",
            (rental_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu trong mục đã xoá"}), 404
        conn.execute("DELETE FROM rentals WHERE id = ? AND deleted_at IS NOT NULL", (rental_id,))
    return jsonify({"ok": True, "purged_code": row["code"]})


@app.route("/api/deleted-rentals/<int:rental_id>/export/excel", methods=["GET"])
def api_export_deleted_excel(rental_id):
    mode = request.args.get("mode", "shortage")
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        purge_expired_deleted_rentals(conn)
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NOT NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu trong mục đã xoá"}), 404
        detail = build_rental_detail(conn, row, catalog_by_id)

    out, filename = build_excel_workbook_bytes(detail, mode=mode)
    return send_file(
        out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/rentals/<int:rental_id>/kits/<int:rental_kit_id>", methods=["PATCH", "POST"])
def api_update_rental_kit_code(rental_id, rental_kit_id):
    data = request.get_json(silent=True) or {}
    new_code = str(data.get("kit_code") or "").strip()
    actor = get_current_actor()
    ts = now_iso()

    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê"}), 404

        rk = conn.execute(
            "SELECT * FROM rental_kits WHERE id = ? AND rental_id = ?",
            (rental_kit_id, rental_id),
        ).fetchone()
        if not rk:
            return jsonify({"error": "Không tìm thấy bộ robot trong phiếu"}), 404

        old_code = str(rk["kit_code"] or "").strip()
        if new_code and new_code != old_code:
            dup_err, dup_conflict = validate_unique_kit_codes(
                conn,
                [(rk["kit_id"], rk["kit_name"], new_code)],
                exclude_rk_id=rental_kit_id,
                current_rental_id=rental_id,
            )
            if dup_err:
                return jsonify({"error": dup_err, "conflict": dup_conflict}), 400

        if new_code != old_code:
            conn.execute(
                "UPDATE rental_kits SET kit_code = ? WHERE id = ?",
                (new_code, rental_kit_id),
            )

            record_change_log(
                conn,
                rental_id=rental_id,
                action="UPDATE_KIT_CODE",
                actor=actor,
                summary=f"Nhập mã bộ '{rk['kit_name']}': '{old_code or 'chưa có'}' -> '{new_code or 'để trống'}'",
                details=json.dumps({"rental_kit_id": rental_kit_id, "old_code": old_code, "new_code": new_code}, ensure_ascii=False),
                created_at=ts,
            )

        detail = build_rental_detail(conn, row, catalog_by_id)
    return jsonify({"success": True, "kit_code": new_code, "rental": detail})


@app.route("/api/rentals/<int:rental_id>/entries", methods=["PATCH", "POST"])
def api_save_entries(rental_id):
    data = request.get_json(force=True) or {}
    entries = data.get("entries") or []
    ts = now_iso()

    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê (hoặc phiếu đã được chuyển vào mục đã xoá)"}), 404
        if row["status"] == "completed":
            return jsonify({"error": "Phiếu kiểm kê này đã hoàn tất kiểm kê ban đầu, không thể chỉnh sửa số lượng linh kiện. Vui lòng sử dụng chức năng Trả bổ sung nếu khách trả thêm hàng."}), 400
        rk_rows = conn.execute(
            "SELECT id, kit_id FROM rental_kits WHERE rental_id = ? ORDER BY id", (rental_id,)
        ).fetchall()
        rk_to_kit = {r["id"]: r["kit_id"] for r in rk_rows}
        kit_to_first_rk = {}
        for r in rk_rows:
            if r["kit_id"] not in kit_to_first_rk:
                kit_to_first_rk[r["kit_id"]] = r["id"]

        item_lookup = {}
        for kid in set(rk_to_kit.values()):
            cat_kit = catalog_by_id.get(kid)
            if cat_kit:
                for it in cat_kit["items"]:\
                    item_lookup[(kid, it["id"])] = it

        # Track previous values for change log
        old_entries_rows = conn.execute(
            "SELECT rental_kit_id, item_id, actual_input, custom_qty, item_note FROM inspection_entries WHERE rental_id = ?",
            (rental_id,),
        ).fetchall()
        old_map = {
            (r["rental_kit_id"], r["item_id"]): (
                "" if r["actual_input"] is None else str(r["actual_input"]).strip(),
                r["custom_qty"],
                str(r["item_note"] or "").strip(),
            )
            for r in old_entries_rows
        }
        changes_list = []

        for e in entries:
            item_id = e.get("item_id")
            rk_raw = e.get("rental_kit_id")
            rk_id = None
            if rk_raw not in (None, ""):
                try:
                    rk_id = int(rk_raw)
                except (TypeError, ValueError):
                    rk_id = None
            if rk_id not in rk_to_kit:
                rk_id = kit_to_first_rk.get(e.get("kit_id"))
            if not item_id or rk_id not in rk_to_kit:
                continue

            kit_id = rk_to_kit[rk_id]
            actual_input = e.get("actual_input")
            if actual_input is not None:
                actual_input = str(actual_input).strip()
            custom_qty = e.get("custom_qty")
            if custom_qty in ("", None):
                custom_qty_val = None
            else:
                try:
                    custom_qty_val = float(str(custom_qty).replace(",", "."))
                except ValueError:
                    custom_qty_val = None
            item_note = (e.get("item_note") or "").strip()

            # Validate shortage does not exceed expected kit quantity (when kit quantity exists)
            if actual_input and (kit_id, item_id) in item_lookup:
                cat_item = item_lookup[(kit_id, item_id)]
                expected_qty = (
                    custom_qty_val
                    if custom_qty_val is not None
                    else (cat_item["qty"] if cat_item["qty"] is not None else None)
                )
                if expected_qty is not None:
                    try:
                        val_num = float(actual_input.replace(",", "."))
                    except ValueError:
                        val_num = None
                    if val_num is not None and val_num < 0 and abs(val_num) > expected_qty:
                        return (
                            jsonify(
                                {
                                    "error": f"Số lượng thiếu ({abs(val_num):g}) của '{cat_item['name']}' không được lớn hơn số lượng trong bộ ({expected_qty:g})."
                                }
                            ),
                            400,
                        )

            clean_actual = "" if actual_input is None else str(actual_input).strip()

            # Check if this entry changed
            old_tuple = old_map.get((rk_id, item_id))
            cat_item = item_lookup.get((kit_id, item_id), {})
            item_name = cat_item.get("name") or str(item_id)
            if old_tuple is None:
                if clean_actual or custom_qty_val is not None or item_note:
                    changes_list.append({
                        "item_id": item_id,
                        "item_name": item_name,
                        "old_val": "Mặc định (Đủ)",
                        "new_val": f"{clean_actual} món" if clean_actual else "Đủ",
                        "note": item_note,
                    })
            else:
                old_actual, old_custom, old_note = old_tuple
                if old_actual != clean_actual or old_custom != custom_qty_val or old_note != item_note:
                    changes_list.append({
                        "item_id": item_id,
                        "item_name": item_name,
                        "old_val": f"{old_actual} món" if old_actual else "Đủ",
                        "new_val": f"{clean_actual} món" if clean_actual else "Đủ",
                        "note": item_note,
                    })

            conn.execute(
                """
                INSERT INTO inspection_entries
                    (rental_id, rental_kit_id, kit_id, item_id, custom_qty, actual_input, item_note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rental_kit_id, item_id) DO UPDATE SET
                    custom_qty = excluded.custom_qty,
                    actual_input = excluded.actual_input,
                    item_note = excluded.item_note,
                    updated_at = excluded.updated_at
                """,
                (rental_id, rk_id, kit_id, item_id, custom_qty_val, clean_actual, item_note, ts),
            )

        new_status = row["status"]
        if new_status == "pending":
            new_status = "inspecting"
        conn.execute(
            "UPDATE rentals SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, ts, rental_id),
        )

        if changes_list:
            actor = get_current_actor()
            is_completed = (row["status"] == "completed")
            action = "EDIT_COMPLETED" if is_completed else "INSPECT_ITEMS"
            if len(changes_list) == 1:
                c = changes_list[0]
                summary = f"Sửa linh kiện '{c['item_name']}': {c['old_val']} → {c['new_val']}"
            else:
                names = ", ".join(c["item_name"] for c in changes_list[:3])
                if len(changes_list) > 3:
                    names += f" và {len(changes_list) - 3} mục khác"
                summary = f"Cập nhật số lượng {len(changes_list)} linh kiện ({names})"

            if is_completed:
                summary = f"[Sửa phiếu hoàn tất] {summary}"

            record_change_log(
                conn,
                rental_id,
                action=action,
                actor=actor,
                summary=summary,
                details=json.dumps(changes_list, ensure_ascii=False),
                created_at=ts,
            )

        updated_row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        detail = build_rental_detail(conn, updated_row, catalog_by_id)

    return jsonify({"ok": True, "summary": detail["summary"], "status": detail["status"], "kits": detail["kits"], "rental": detail})


@app.route("/api/rentals/<int:rental_id>/status", methods=["POST"])
def api_set_status(rental_id):
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if status not in ("pending", "inspecting", "completed"):
        return jsonify({"error": "Trạng thái không hợp lệ"}), 400
    ts = now_iso()
    completed_at = ts if status == "completed" else None

    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê (hoặc phiếu đã được chuyển vào mục đã xoá)"}), 404
        conn.execute(
            "UPDATE rentals SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (status, completed_at, ts, rental_id),
        )
        updated_row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        detail = build_rental_detail(conn, updated_row, catalog_by_id)

        actor = get_current_actor()
        if status == "completed":
            sum_info = detail.get("summary") or {}
            comp = int(sum_info.get("total_compensation", 0))
            short_cnt = sum_info.get("shortage_items_count", 0)

            # Freeze Lần 1 initial shortages if not yet saved and there are shortages
            if not row["initial_inspection_json"] and detail.get("shortage_items"):
                init_shortages = [
                    {
                        "rental_kit_id": it["rental_kit_id"],
                        "kit_name": it["kit_name"],
                        "kit_code": it.get("kit_code") or "",
                        "kit_display_label": it.get("kit_display_label") or it["kit_name"],
                        "item_id": it["id"],
                        "item_name": it["name"],
                        "item_code": it.get("code") or "",
                        "unit": it.get("unit") or "cái",
                        "images": it.get("images") or [],
                        "expected_qty": it.get("expected_qty"),
                        "missing_qty": it["missing_qty"],
                        "unit_price": it.get("unit_price", 0),
                        "compensation": it.get("compensation", 0),
                        "pair_billing_note": it.get("pair_billing_note") or "",
                        "pair_charge_role": it.get("pair_charge_role") or "none",
                    }
                    for it in detail.get("shortage_items", [])
                ]
                init_snapshot = {
                    "round_number": 1,
                    "round_name": "Kiểm kê ban đầu khi nhận hàng trả",
                    "actor": actor or "Người kiểm kê",
                    "created_at": ts,
                    "shortages": init_shortages,
                    "total_missing_qty": detail["summary"]["total_missing_qty"],
                    "total_compensation": detail["summary"]["total_compensation"],
                }
                conn.execute(
                    "UPDATE rentals SET initial_inspection_json = ? WHERE id = ?",
                    (json.dumps(init_snapshot, ensure_ascii=False), rental_id),
                )

            record_change_log(
                conn,
                rental_id,
                action="COMPLETE_RENTAL",
                actor=actor,
                summary=f"Hoàn tất kiểm kê & ghi nhận (Thiếu {short_cnt} linh kiện, tổng tiền bù {comp:,} ₫)",
                details=json.dumps(
                    {
                        "status": "completed",
                        "total_compensation": comp,
                        "shortage_items_count": short_cnt,
                    },
                    ensure_ascii=False,
                ),
                created_at=ts,
            )
        elif status == "inspecting":
            record_change_log(
                conn,
                rental_id,
                action="REOPEN_INSPECTION",
                actor=actor,
                summary="Mở lại kiểm kê cho phiếu",
                details=json.dumps({"status": "inspecting"}, ensure_ascii=False),
                created_at=ts,
            )

    return jsonify({"rental": detail})


@app.route("/api/rentals/<int:rental_id>/logs", methods=["GET"])
def api_get_rental_logs(rental_id):
    """Lấy danh sách nhật ký ghi nhận các lần thay đổi dữ liệu của phiếu."""
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, code, team_name, status, created_at FROM rentals WHERE id = ?", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê"}), 404
        logs = get_change_logs(conn, rental_id)
    return jsonify({
        "ok": True,
        "rental_id": rental_id,
        "code": row["code"],
        "team_name": row["team_name"],
        "logs": logs,
    })


@app.route("/api/rentals/<int:rental_id>/supplementary-preview", methods=["GET"])
def api_supplementary_preview(rental_id):
    """
    Chuẩn bị dữ liệu cho đợt trả hàng bổ sung:
    Kiểm tra trạng thái gần nhất của phiếu và chỉ trả về danh sách các linh kiện đang thiếu.
    """
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê"}), 404
        if row["status"] != "completed":
            return jsonify({"error": "Phiếu này chưa hoàn tất kiểm kê ban đầu, không thể thực hiện trả bổ sung."}), 400
        detail = build_rental_detail(conn, row, catalog_by_id)

        rounds = detail.get("supplementary_rounds") or []
        next_round_number = len(rounds) + 2
        supp_dot = len(rounds) + 1
        next_round_name = f"Trả bổ sung lần {supp_dot}"

        missing_items = []
        for it in detail.get("shortage_items", []):
            missing_items.append({
                "rental_kit_id": it["rental_kit_id"],
                "kit_name": it["kit_name"],
                "kit_code": it.get("kit_code") or "",
                "kit_display_label": it.get("kit_display_label") or it["kit_name"],
                "item_id": it["id"],
                "item_name": it["name"],
                "item_code": it.get("code") or "",
                "image_url": it.get("image_url") or "",
                "expected_qty": it["expected_qty"],
                "missing_qty": it["missing_qty"],
                "unit_price": it.get("unit_price", 0),
                "compensation": it.get("compensation", 0),
                "pair_billing_note": it.get("pair_billing_note") or "",
                "pair_charge_role": it.get("pair_charge_role") or "none",
            })

        rounds_history = []
        initial_insp = detail.get("initial_inspection") or {}
        rounds_history.append(initial_insp)
        for r in rounds:
            rounds_history.append(r)

    return jsonify({
        "ok": True,
        "rental_id": detail["id"],
        "ticket_code": detail["code"],
        "team_name": detail["team_name"],
        "returner_name": detail["returner_name"],
        "phone": detail["phone"],
        "status": detail["status"],
        "next_round_number": next_round_number,
        "next_round_name": next_round_name,
        "initial_inspection": initial_insp,
        "missing_items": missing_items,
        "total_missing_qty": detail["summary"]["total_missing_qty"],
        "total_compensation": detail["summary"]["total_compensation"],
        "rounds_history": rounds_history,
        "supplementary_rounds": rounds,
    })


@app.route("/api/rentals/<int:rental_id>/supplementary-return", methods=["POST"])
def api_supplementary_return(rental_id):
    """
    Tiếp nhận trả hàng bổ sung cho các linh kiện đang thiếu:
    - Cập nhật số lượng kiểm kê
    - Lưu snapshot đợt trả (ngày giờ, người trả, danh sách linh kiện đã trả, danh sách linh kiện còn thiếu lại)
    - Ghi log thay đổi
    """
    data = request.get_json(silent=True) or {}
    returned_items = data.get("returned_items") or []
    note = str(data.get("note") or "").strip()
    actor = str(data.get("actor") or "").strip() or get_current_actor()
    ts = now_iso()

    if not returned_items:
        return jsonify({"error": "Vui lòng nhập số lượng linh kiện được trả bổ sung."}), 400

    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê"}), 404
        if row["status"] != "completed":
            return jsonify({"error": "Phiếu này chưa hoàn tất kiểm kê ban đầu, không thể thực hiện trả bổ sung."}), 400

        cur_detail = build_rental_detail(conn, row, catalog_by_id)
        shortage_map = {
            (it["rental_kit_id"], it["id"]): it
            for it in cur_detail.get("shortage_items", [])
        }

        if not shortage_map:
            return jsonify({"error": "Phiếu này hiện không có linh kiện nào bị thiếu để trả bổ sung."}), 400

        # Lưu cố định (Freeze snapshot) danh sách gốc thiếu ở đợt ban đầu nếu chưa lưu
        if not row["initial_inspection_json"] and cur_detail.get("shortage_items"):
            init_shortages = [
                {
                    "rental_kit_id": it["rental_kit_id"],
                    "kit_name": it["kit_name"],
                    "kit_code": it.get("kit_code") or "",
                    "kit_display_label": it.get("kit_display_label") or it["kit_name"],
                    "item_id": it["id"],
                    "item_name": it["name"],
                    "item_code": it.get("code") or "",
                    "unit": it.get("unit") or "cái",
                    "images": it.get("images") or [],
                    "expected_qty": it.get("expected_qty"),
                    "missing_qty": it["missing_qty"],
                    "unit_price": it.get("unit_price", 0),
                    "compensation": it.get("compensation", 0),
                    "pair_billing_note": it.get("pair_billing_note") or "",
                    "pair_charge_role": it.get("pair_charge_role") or "none",
                }
                for it in cur_detail.get("shortage_items", [])
            ]
            init_snapshot = {
                "round_number": 1,
                "round_name": "Kiểm kê ban đầu khi nhận hàng trả",
                "actor": "Người kiểm kê",
                "created_at": row["completed_at"] or row["created_at"] or ts,
                "shortages": init_shortages,
                "total_missing_qty": cur_detail["summary"]["total_missing_qty"],
                "total_compensation": cur_detail["summary"]["total_compensation"],
            }
            conn.execute(
                "UPDATE rentals SET initial_inspection_json = ? WHERE id = ?",
                (json.dumps(init_snapshot, ensure_ascii=False), rental_id),
            )

        valid_returns = []
        total_returned_qty = 0
        for ret in returned_items:
            rk_id = ret.get("rental_kit_id")
            item_id = ret.get("item_id")
            qty_raw = ret.get("returned_qty")
            try:
                returned_qty = float(str(qty_raw).replace(",", "."))
            except (ValueError, TypeError):
                returned_qty = 0

            if returned_qty <= 0:
                continue

            target = shortage_map.get((rk_id, item_id))
            if not target:
                return jsonify({
                    "error": f"Linh kiện mã '{item_id}' không nằm trong danh sách đang thiếu của phiếu này."
                }), 400

            max_missing = target["missing_qty"]
            if returned_qty > max_missing + 1e-6:
                return jsonify({
                    "error": f"Số lượng trả ({returned_qty:g}) của '{target['name']}' vượt quá số lượng đang thiếu ({max_missing:g})."
                }), 400

            valid_returns.append({
                "rental_kit_id": rk_id,
                "item_id": item_id,
                "kit_name": target["kit_name"],
                "kit_code": target.get("kit_code") or "",
                "item_name": target["name"],
                "returned_qty": returned_qty,
                "unit_price": target.get("unit_price", 0),
                "previous_missing_qty": max_missing,
                "target": target,
            })
            total_returned_qty += returned_qty

        if not valid_returns:
            return jsonify({"error": "Chưa có số lượng trả hợp lệ nào (> 0) được nhập."}), 400

        # Update inspection_entries in database
        for vr in valid_returns:
            target = vr["target"]
            rk_id = vr["rental_kit_id"]
            item_id = vr["item_id"]
            returned_qty = vr["returned_qty"]
            cur_missing = target["missing_qty"]
            expected_qty = target.get("expected_qty")
            raw_input = target.get("actual_input")

            new_missing = max(0.0, cur_missing - returned_qty)
            if raw_input is not None and str(raw_input).strip().startswith("-"):
                if new_missing > 0:
                    new_actual = f"-{new_missing:g}"
                else:
                    new_actual = f"{expected_qty:g}" if expected_qty is not None else "0"
            else:
                try:
                    val_num = float(str(raw_input).replace(",", "."))
                except (ValueError, TypeError):
                    val_num = (expected_qty - cur_missing) if expected_qty is not None else 0
                new_actual_num = val_num + returned_qty
                if expected_qty is not None and new_actual_num > expected_qty:
                    new_actual_num = expected_qty
                new_actual = f"{new_actual_num:g}"

            conn.execute(
                """
                UPDATE inspection_entries
                SET actual_input = ?, updated_at = ?
                WHERE rental_kit_id = ? AND item_id = ?
                """,
                (new_actual, ts, rk_id, item_id),
            )

        # Re-compute rental detail
        updated_row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        new_detail = build_rental_detail(conn, updated_row, catalog_by_id)

        remaining_shortages_snapshot = []
        for it in new_detail.get("shortage_items", []):
            remaining_shortages_snapshot.append({
                "rental_kit_id": it["rental_kit_id"],
                "kit_name": it["kit_name"],
                "kit_code": it.get("kit_code") or "",
                "kit_display_label": it.get("kit_display_label") or it["kit_name"],
                "item_id": it["id"],
                "item_name": it["name"],
                "item_code": it.get("code") or "",
                "unit": it.get("unit") or "cái",
                "images": it.get("images") or [],
                "expected_qty": it.get("expected_qty"),
                "missing_qty": it["missing_qty"],
                "unit_price": it.get("unit_price", 0),
                "compensation": it.get("compensation", 0),
                "pair_billing_note": it.get("pair_billing_note") or "",
                "pair_charge_role": it.get("pair_charge_role") or "none",
            })

        returned_items_snapshot = [
            {
                "rental_kit_id": vr["rental_kit_id"],
                "kit_name": vr["kit_name"],
                "kit_code": vr["kit_code"],
                "kit_display_label": vr["target"].get("kit_display_label") or vr["kit_name"],
                "item_id": vr["item_id"],
                "item_name": vr["item_name"],
                "item_code": vr["target"].get("code") or "",
                "unit": vr["target"].get("unit") or "cái",
                "images": vr["target"].get("images") or [],
                "returned_qty": vr["returned_qty"],
                "unit_price": vr["unit_price"],
            }
            for vr in valid_returns
        ]

        prev_rounds = cur_detail.get("supplementary_rounds") or []
        round_number = len(prev_rounds) + 2
        supp_dot = len(prev_rounds) + 1
        round_name = f"Trả bổ sung lần {supp_dot}"

        rem_missing_qty = new_detail["summary"]["total_missing_qty"]
        new_total_comp = new_detail["summary"]["total_compensation"]

        conn.execute(
            """
            INSERT INTO supplementary_returns
                (rental_id, round_number, round_name, actor, returned_items_json,
                 remaining_shortages_json, total_returned_qty, remaining_missing_qty,
                 new_total_compensation, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rental_id,
                round_number,
                round_name,
                actor,
                json.dumps(returned_items_snapshot, ensure_ascii=False),
                json.dumps(remaining_shortages_snapshot, ensure_ascii=False),
                total_returned_qty,
                rem_missing_qty,
                new_total_comp,
                note,
                ts,
            ),
        )

        items_desc = ", ".join(f"{vr['item_name']} (+{vr['returned_qty']:g})" for vr in valid_returns[:3])
        if len(valid_returns) > 3:
            items_desc += f" và {len(valid_returns) - 3} món khác"
        rem_status_desc = "Đã đủ 100%" if rem_missing_qty == 0 else f"Còn thiếu {rem_missing_qty:g} món, tiền bù {int(new_total_comp):,} ₫"
        log_summary = f"[{round_name}] Nhận trả thêm {total_returned_qty:g} linh kiện ({items_desc}) → {rem_status_desc}"

        record_change_log(
            conn,
            rental_id,
            action="SUPPLEMENTARY_RETURN",
            actor=actor,
            summary=log_summary,
            details=json.dumps({
                "round_number": round_number,
                "round_name": round_name,
                "actor": actor,
                "returned_items": returned_items_snapshot,
                "remaining_shortages": remaining_shortages_snapshot,
                "note": note,
            }, ensure_ascii=False),
            created_at=ts,
        )

        conn.execute("UPDATE rentals SET updated_at = ? WHERE id = ?", (ts, rental_id))

        final_row = conn.execute("SELECT * FROM rentals WHERE id = ?", (rental_id,)).fetchone()
        final_detail = build_rental_detail(conn, final_row, catalog_by_id)

    return jsonify({
        "ok": True,
        "round_number": round_number,
        "round_name": round_name,
        "actor": actor,
        "created_at": ts,
        "total_returned_qty": total_returned_qty,
        "remaining_missing_qty": rem_missing_qty,
        "new_total_compensation": new_total_comp,
        "rental": final_detail,
        "message": f"Đã ghi nhận thành công '{round_name}' lúc {ts}!",
    })


def safe_excel_val(val):
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return val
    s = str(val)
    if s.startswith(("=", "+", "-", "@")):
        try:
            float(s)
            return val
        except ValueError:
            return "'" + s
    return val


def build_excel_workbook_bytes(detail, mode="shortage"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Bao cao thieu" if mode == "shortage" else "Bien ban kiem ke"

    # Styling
    thin = Side(border_style="thin", color="CBD5E1")
    border_all = Border(left=thin, right=thin, top=thin, bottom=thin)
    title_font = Font(name="Calibri", size=15, bold=True, color="0F172A")
    meta_label_font = Font(name="Calibri", size=11, bold=True, color="334155")
    meta_val_font = Font(name="Calibri", size=11, color="0F172A")
    header_fill = PatternFill("solid", fgColor="1E3A8A")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    shortage_fill = PatternFill("solid", fgColor="FEF2F2")
    total_fill = PatternFill("solid", fgColor="EFF6FF")
    total_font = Font(name="Calibri", size=12, bold=True, color="B91C1C")

    title_text = (
        "BÁO CÁO THỐNG KÊ LINH KIỆN THIẾU & TIỀN BÙ"
        if mode == "shortage"
        else "BIÊN BẢN KIỂM KÊ LINH KIỆN ROBOT CHO THUÊ"
    )
    ws.merge_cells("A1:L1")
    ws["A1"] = title_text
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    kits_str = ", ".join(k["display_label"] for k in detail["kits"])
    meta_rows = [
        ("Mã phiếu:", safe_excel_val(detail["code"]), "Đội thuê:", safe_excel_val(detail["team_name"])),
        ("Người trả hàng:", safe_excel_val(detail["returner_name"]), "Số điện thoại:", safe_excel_val(detail["phone"])),
        ("Các bộ đã thuê:", safe_excel_val(kits_str), "Ngày xuất báo cáo:", datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]
    if detail.get("email") or detail.get("note"):
        meta_rows.append(
            ("Gmail:", safe_excel_val(detail.get("email") or "-"), "Ghi chú:", safe_excel_val(detail.get("note") or ""))
        )

    cur_row = 3
    for lbl1, val1, lbl2, val2 in meta_rows:
        ws.cell(cur_row, 1, lbl1).font = meta_label_font
        ws.merge_cells(start_row=cur_row, start_column=2, end_row=cur_row, end_column=5)
        meta_val_cell = ws.cell(cur_row, 2, val1)
        meta_val_cell.font = meta_val_font
        meta_val_cell.alignment = Alignment(vertical="center", wrap_text=True)
        if lbl2:
            ws.cell(cur_row, 6, lbl2).font = meta_label_font
            ws.merge_cells(start_row=cur_row, start_column=7, end_row=cur_row, end_column=12)
            ws.cell(cur_row, 7, val2).font = meta_val_font
        ws.row_dimensions[cur_row].height = 30 if lbl1 == "Các bộ đã thuê:" and len(str(val1)) > 55 else 20
        cur_row += 1

    cur_row += 1
    headers = [
        "STT",
        "Bộ robot",
        "Mã LK",
        "Tên hàng / Linh kiện",
        "Hình ảnh",
        "ĐVT",
        "SL quy định",
        "SL thực",
        "SL thiếu",
        "Đơn giá (VNĐ)",
        "Thành tiền bù (VNĐ)",
        "Thời gian kiểm",
    ]
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(cur_row, col_idx, h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border_all
    ws.row_dimensions[cur_row].height = 28

    col_widths = [6, 24, 16, 36, 12, 8, 13, 13, 12, 16, 20, 20]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    if mode == "shortage":
        export_items = detail["shortage_items"]
    else:
        export_items = [it for kd in detail["kit_details"] for it in kd["items"]]

    data_start_row = cur_row + 1
    for idx, it in enumerate(export_items, start=1):
        r = cur_row + idx
        role = it.get("pair_charge_role") or "none"
        pair_note = (
            (it.get("pair_billing_note") or "").strip()
            if role in ("primary", "included_in_pair", "solo_in_pair", "flat_set")
            else ""
        )
        ws.row_dimensions[r].height = 56 if pair_note else 48

        expected_val = it["expected_qty"] if it["expected_qty"] is not None else ""
        if it["status"] == "default_full":
            actual_val = expected_val if expected_val != "" else "Đủ"
        elif it["effective_actual"] is not None:
            actual_val = it["effective_actual"]
        else:
            actual_val = it["actual_input"]

        name_display = it["name"]
        if it.get("section_name"):
            name_display = f"[{it['section_name']}] {name_display}"
        if pair_note:
            name_display = f"{name_display}\n({pair_note})"

        time_val = it.get("updated_at") or ""
        if time_val:
            try:
                dt_obj = datetime.strptime(time_val.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                time_display = dt_obj.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                time_display = time_val
        else:
            time_display = "-"

        row_vals = [
            idx,
            safe_excel_val(it.get("kit_display_label") or it["kit_name"]),
            safe_excel_val(it["code"] or "-"),
            safe_excel_val(name_display),
            "",
            safe_excel_val(it["unit"]),
            expected_val,
            safe_excel_val(actual_val),
            it["missing_qty"],
            it["unit_price"],
            None,
            safe_excel_val(time_display),
        ]
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(r, c_idx, val)
            cell.border = border_all
            cell.alignment = Alignment(
                horizontal="center" if c_idx in (1, 3, 5, 6, 7, 8, 9, 12) else ("right" if c_idx in (10, 11) else "left"),
                vertical="center",
                wrap_text=True,
            )
            if it["missing_qty"] > 0:
                cell.fill = shortage_fill

        # Formula for Thành tiền bù = SL thiếu * Đơn giá (with paired-set deduplication & x1 Card flat-set billing)
        if role == "included_in_pair":
            comp_cell = ws.cell(r, 11, 0)
            comp_cell.font = Font(name="Calibri", size=10.5, italic=True, color="64748B")
        elif role == "flat_set" or it.get("is_flat_set_price"):
            comp_cell = ws.cell(r, 11, f"=IF(I{r}>0,J{r},0)")
            if it["missing_qty"] > 0:
                comp_cell.font = Font(name="Calibri", size=11, bold=True, color="B91C1C")
        else:
            comp_cell = ws.cell(r, 11, f"=I{r}*J{r}")
            if it["missing_qty"] > 0:
                comp_cell.font = Font(name="Calibri", size=11, bold=True, color="B91C1C")
        comp_cell.border = border_all
        comp_cell.alignment = Alignment(horizontal="right", vertical="center")
        if it["missing_qty"] > 0:
            comp_cell.fill = shortage_fill
        ws.cell(r, 10).number_format = "#,##0"
        comp_cell.number_format = "#,##0"

        # Embed first image if available
        if it.get("images"):
            rel_img = it["images"][0].lstrip("/")
            img_path = BASE_DIR / rel_img
            if img_path.exists():
                try:
                    with PILImage.open(img_path) as pim:
                        pim = pim.convert("RGBA")
                        pim.thumbnail((56, 56))
                        buf = io.BytesIO()
                        pim.save(buf, format="PNG")
                        buf.seek(0)
                        xl_img = XLImage(buf)
                        xl_img.width = pim.width
                        xl_img.height = pim.height
                        ws.add_image(xl_img, f"E{r}")
                except Exception:
                    pass

    data_end_row = cur_row + len(export_items)
    total_row = data_end_row + 1
    ws.row_dimensions[total_row].height = 28
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=8)
    ws.cell(total_row, 1, "TỔNG CỘNG TIỀN BÙ").font = total_font
    ws.cell(total_row, 1).alignment = Alignment(horizontal="right", vertical="center")

    for c_idx in range(1, 13):
        ws.cell(total_row, c_idx).border = border_all
        ws.cell(total_row, c_idx).fill = total_fill

    if export_items:
        ws.cell(total_row, 9, f"=SUM(I{data_start_row}:I{data_end_row})").font = total_font
        ws.cell(total_row, 9).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(total_row, 11, f"=SUM(K{data_start_row}:K{data_end_row})").font = total_font
        ws.cell(total_row, 11).number_format = "#,##0"
        ws.cell(total_row, 11).alignment = Alignment(horizontal="right", vertical="center")
    else:
        ws.cell(total_row, 9, 0).font = total_font
        ws.cell(total_row, 11, 0).font = total_font
    ws.cell(total_row, 12, "").border = border_all

    has_pair_dedup = detail.get("summary", {}).get("paired_dedup_count", 0) > 0
    has_flat_set = detail.get("summary", {}).get("flat_set_count", 0) > 0
    note_row = total_row + 1
    if has_pair_dedup or has_flat_set:
        note_parts = []
        if has_pair_dedup:
            note_parts.append(
                "* Ghi chú linh kiện theo bộ: Các chi tiết cùng 1 bộ (như cánh trái/phải, bánh/lốp, ổ bi...) chỉ tính bù 1 bộ nếu mất một phần hoặc mất cả cặp trong cùng bộ robot (dòng phụ ghi 0 đ để không tính trùng)."
            )
        if has_flat_set:
            note_parts.append(
                "* Ghi chú bộ có Card: Đơn giá là tiền cả 1 bộ Card, nên thiếu 1 hay thiếu toàn bộ số lượng card đều chỉ tính tiền ×1."
            )
        ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=12)
        ncell = ws.cell(note_row, 1, " ".join(note_parts))
        ncell.font = Font(name="Calibri", size=10, italic=True, color="92400E")
        ncell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[note_row].height = 34 if (has_pair_dedup and has_flat_set) else 24
        note_row += 1

    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=12)
    vcell = ws.cell(
        note_row,
        1,
        "* Lưu ý mở trên điện thoại: Vui lòng ưu tiên mở file Excel này bằng Microsoft Excel hoặc Google Sheets (Google Trang tính) để hiển thị đúng công thức (=SUM, =IF) và hình ảnh linh kiện.",
    )
    vcell.font = Font(name="Calibri", size=10, italic=True, color="1E3A8A")
    vcell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[note_row].height = 24

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    safe_team = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in detail["team_name"])
    filename = f"BaoCao_{mode}_{detail['code']}_{safe_team}.xlsx"
    return out, filename


@app.route("/api/rentals/<int:rental_id>/email", methods=["PATCH"])
def api_update_rental_email(rental_id):
    return (
        jsonify(
            {
                "error": "Thông tin phiếu không được phép chỉnh sửa sau khi lập. Nếu nhập sai, vui lòng xoá phiếu cũ và lập phiếu mới."
            }
        ),
        403,
    )


@app.route("/api/rentals/<int:rental_id>/export/excel", methods=["GET"])
def api_export_excel(rental_id):
    mode = request.args.get("mode", "shortage")
    _, catalog_by_id = load_catalog()
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ? AND deleted_at IS NULL", (rental_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Không tìm thấy phiếu kiểm kê (hoặc phiếu đã được chuyển vào mục đã xoá)"}), 404
        detail = build_rental_detail(conn, row, catalog_by_id)

    out, filename = build_excel_workbook_bytes(detail, mode=mode)
    return send_file(
        out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/crm/preview", methods=["POST"])
def api_crm_preview():
    use_sample = (
        request.args.get("use_sample") in ("1", "true", "yes")
        or (request.is_json and request.get_json(silent=True) and request.get_json().get("use_sample"))
        or request.form.get("use_sample") in ("1", "true", "yes")
    )

    file_bytes = None
    if use_sample:
        if not SAMPLE_CRM_PATH.exists():
            return jsonify({"error": f"Không tìm thấy file mẫu CRM tại {SAMPLE_CRM_PATH.name}"}), 404
        file_bytes = SAMPLE_CRM_PATH.read_bytes()
    elif "file" in request.files:
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Vui lòng chọn file Excel CRM (.xlsx)"}), 400
        file_bytes = f.read()
    else:
        return jsonify({"error": "Vui lòng tải lên file Excel CRM hoặc chọn nạp từ file mẫu"}), 400

    catalog, _ = load_catalog()
    with get_conn(DB_PATH) as conn:
        existing_orders = get_existing_crm_order_nos(conn)

    try:
        preview_data = parse_crm_file(file_bytes, catalog, existing_crm_order_nos=existing_orders)
    except Exception as exc:
        return jsonify({"error": f"Lỗi đọc file Excel CRM: {str(exc)}"}), 400

    return jsonify({"success": True, "data": preview_data})


@app.route("/api/crm/import", methods=["POST"])
def api_crm_import():
    actor = get_current_actor()
    catalog, _ = load_catalog()

    orders_to_process = None
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if body.get("orders") and isinstance(body["orders"], list):
            orders_to_process = body["orders"]

    if orders_to_process is None:
        use_sample = (
            request.args.get("use_sample") in ("1", "true", "yes")
            or (request.is_json and request.get_json(silent=True) and request.get_json().get("use_sample"))
            or request.form.get("use_sample") in ("1", "true", "yes")
        )
        file_bytes = None
        if use_sample:
            if not SAMPLE_CRM_PATH.exists():
                return jsonify({"error": f"Không tìm thấy file mẫu CRM tại {SAMPLE_CRM_PATH.name}"}), 404
            file_bytes = SAMPLE_CRM_PATH.read_bytes()
        elif "file" in request.files:
            f = request.files["file"]
            if not f.filename:
                return jsonify({"error": "Vui lòng chọn file Excel CRM (.xlsx)"}), 400
            file_bytes = f.read()
        else:
            return jsonify({"error": "Không có dữ liệu đơn hàng để nạp"}), 400

        with get_conn(DB_PATH) as conn:
            existing_orders = get_existing_crm_order_nos(conn)

        try:
            parsed = parse_crm_file(file_bytes, catalog, existing_crm_order_nos=existing_orders)
            orders_to_process = parsed["orders"]
        except Exception as exc:
            return jsonify({"error": f"Lỗi đọc file Excel CRM: {str(exc)}"}), 400

    with get_conn(DB_PATH) as conn:
        result = import_crm_orders(conn, catalog, orders_to_process, actor=actor)

        skip_parts = []
        if result["duplicate_skipped_count"] > 0:
            skip_parts.append(f"bỏ qua {result['duplicate_skipped_count']} đơn đã tồn tại")
        if result["no_kits_skipped_count"] > 0:
            skip_parts.append(f"bỏ qua {result['no_kits_skipped_count']} đơn rỗng")
        skip_text = f" ({', '.join(skip_parts)})" if skip_parts else ""

    return jsonify(
        {
            "success": True,
            "imported_count": result["imported_count"],
            "duplicate_skipped_count": result["duplicate_skipped_count"],
            "no_kits_skipped_count": result["no_kits_skipped_count"],
            "imported_tickets": result["imported_tickets"],
            "message": f"Đã nạp thành công {result['imported_count']} đơn hàng mới vào danh sách kiểm kê!{skip_text}",
        }
    )


if __name__ == "__main__":
    # Pre-warm catalog on startup
    load_catalog()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)

