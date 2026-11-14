import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

RETENTION_DAYS = 90


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_days_iso(dt_str: str, days: int = RETENTION_DAYS) -> str:
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        dt = datetime.now()
    return (dt + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def compute_days_remaining(expires_at: str) -> int:
    if not expires_at:
        return 0
    try:
        exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        diff_sec = (exp_dt - datetime.now()).total_seconds()
        if diff_sec <= 0:
            return 0
        return min(RETENTION_DAYS, math.ceil(diff_sec / 86400))
    except ValueError:
        return 0


def purge_expired_deleted_rentals(conn):
    now = now_iso()
    conn.execute(
        """
        DELETE FROM rentals
        WHERE deleted_at IS NOT NULL
          AND deleted_expires_at IS NOT NULL
          AND deleted_expires_at != ''
          AND deleted_expires_at <= ?
        """,
        (now,),
    )


def get_conn(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rentals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                returner_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT DEFAULT '',
                note TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                deleted_at TEXT DEFAULT NULL,
                delete_reason TEXT DEFAULT '',
                deleted_expires_at TEXT DEFAULT NULL
            );
            """
        )

        # Ensure optional columns exist on pre-existing databases
        rental_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(rentals)").fetchall()
        }
        if "email" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN email TEXT DEFAULT ''")
        if "deleted_at" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN deleted_at TEXT DEFAULT NULL")
        if "delete_reason" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN delete_reason TEXT DEFAULT ''")
        if "deleted_expires_at" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN deleted_expires_at TEXT DEFAULT NULL")
        if "crm_order_no" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN crm_order_no TEXT DEFAULT NULL")
        if "due_date" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN due_date TEXT DEFAULT ''")
        if "delivery_address" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN delivery_address TEXT DEFAULT ''")
        if "initial_inspection_json" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN initial_inspection_json TEXT DEFAULT NULL")
        if "return_status" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN return_status TEXT DEFAULT 'Đã trả'")
        if "original_info_json" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN original_info_json TEXT DEFAULT NULL")
        if "original_kits_json" not in rental_cols:
            conn.execute("ALTER TABLE rentals ADD COLUMN original_kits_json TEXT DEFAULT NULL")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_rentals_crm_order_no ON rentals(crm_order_no)")

        purge_expired_deleted_rentals(conn)

        # Migration: populate original_info_json for existing rentals
        rows_to_init = conn.execute("SELECT * FROM rentals WHERE original_info_json IS NULL").fetchall()
        for r in rows_to_init:
            ret_stat = "Đã trả"
            if "return_status" in r.keys() and r["return_status"]:
                ret_stat = r["return_status"]
            orig_info = {
                "team_name": r["team_name"],
                "returner_name": r["returner_name"],
                "phone": r["phone"],
                "email": r["email"] or "",
                "due_date": r["due_date"] or "",
                "delivery_address": r["delivery_address"] or "",
                "note": r["note"] or "",
                "return_status": ret_stat,
            }
            conn.execute(
                "UPDATE rentals SET original_info_json = ?, return_status = COALESCE(return_status, 'Đã trả') WHERE id = ?",
                (json.dumps(orig_info, ensure_ascii=False), r["id"]),
            )

        # Migration: Reset 51 CRM auto-completed rentals back to pending inspection
        # ("Đã trả toàn bộ" = đã trả thiết bị, không đồng nghĩa là đã kiểm kê)
        conn.execute(
            """
            UPDATE rentals
            SET status = 'pending',
                completed_at = NULL,
                initial_inspection_json = NULL,
                return_status = 'Đã trả'
            WHERE status = 'completed' AND initial_inspection_json LIKE '%Hệ thống (CRM - Đã trả toàn bộ)%'
            """
        )

        # Check if rental_kits already uses the per-instance schema (has kit_code & instance_index)
        rk_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(rental_kits)").fetchall()
        }
        ie_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(inspection_entries)").fetchall()
        }

        if not rk_cols:
            conn.executescript(
                """
                CREATE TABLE rental_kits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                    kit_id TEXT NOT NULL,
                    kit_name TEXT NOT NULL,
                    instance_index INTEGER NOT NULL DEFAULT 1,
                    kit_code TEXT DEFAULT '',
                    kit_count INTEGER NOT NULL DEFAULT 1,
                    custom_items_json TEXT DEFAULT NULL
                );

                CREATE TABLE inspection_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                    rental_kit_id INTEGER NOT NULL REFERENCES rental_kits(id) ON DELETE CASCADE,
                    kit_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    custom_qty REAL,
                    actual_input TEXT,
                    item_note TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(rental_kit_id, item_id)
                );
                """
            )
        elif "kit_code" not in rk_cols or "rental_kit_id" not in ie_cols:
            # Migrate legacy grouped rental_kits / inspection_entries cleanly without data loss
            conn.execute("PRAGMA foreign_keys = OFF")
            old_rks = conn.execute("SELECT * FROM rental_kits ORDER BY id").fetchall()
            old_entries = conn.execute("SELECT * FROM inspection_entries ORDER BY id").fetchall()

            conn.executescript(
                """
                DROP TABLE IF EXISTS rental_kits_new;
                DROP TABLE IF EXISTS inspection_entries_new;

                CREATE TABLE rental_kits_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                    kit_id TEXT NOT NULL,
                    kit_name TEXT NOT NULL,
                    instance_index INTEGER NOT NULL DEFAULT 1,
                    kit_code TEXT DEFAULT '',
                    kit_count INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE inspection_entries_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                    rental_kit_id INTEGER NOT NULL REFERENCES rental_kits_new(id) ON DELETE CASCADE,
                    kit_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    custom_qty REAL,
                    actual_input TEXT,
                    item_note TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(rental_kit_id, item_id)
                );
                """
            )

            first_rk_map = {}
            per_rental_kit_idx = {}
            for rk in old_rks:
                cnt = max(1, int(rk["kit_count"] or 1))
                for _ in range(cnt):
                    key = (rk["rental_id"], rk["kit_id"])
                    idx = per_rental_kit_idx.get(key, 0) + 1
                    per_rental_kit_idx[key] = idx
                    cur = conn.execute(
                        """
                        INSERT INTO rental_kits_new (rental_id, kit_id, kit_name, instance_index, kit_code, kit_count)
                        VALUES (?, ?, ?, ?, '', 1)
                        """,
                        (rk["rental_id"], rk["kit_id"], rk["kit_name"], idx),
                    )
                    if key not in first_rk_map:
                        first_rk_map[key] = cur.lastrowid

            for e in old_entries:
                key = (e["rental_id"], e["kit_id"])
                rk_id = first_rk_map.get(key)
                if rk_id is None:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO inspection_entries_new
                        (rental_id, rental_kit_id, kit_id, item_id, custom_qty, actual_input, item_note, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        e["rental_id"],
                        rk_id,
                        e["kit_id"],
                        e["item_id"],
                        e["custom_qty"],
                        e["actual_input"],
                        e["item_note"],
                        e["updated_at"],
                    ),
                )

            conn.executescript(
                """
                DROP TABLE inspection_entries;
                DROP TABLE rental_kits;
                ALTER TABLE rental_kits_new RENAME TO rental_kits;
                ALTER TABLE inspection_entries_new RENAME TO inspection_entries;
                """
            )
            conn.execute("PRAGMA foreign_keys = ON")

        rk_cols_curr = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(rental_kits)").fetchall()
        }
        if "custom_items_json" not in rk_cols_curr:
            conn.execute("ALTER TABLE rental_kits ADD COLUMN custom_items_json TEXT DEFAULT NULL")
        if "is_added_after" not in rk_cols_curr:
            conn.execute("ALTER TABLE rental_kits ADD COLUMN is_added_after INTEGER DEFAULT 0")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS deletion_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rental_id INTEGER,
                ticket_code TEXT NOT NULL,
                team_name TEXT NOT NULL,
                returner_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT DEFAULT '',
                status TEXT NOT NULL,
                kits_summary TEXT NOT NULL,
                total_missing_qty REAL DEFAULT 0,
                total_compensation REAL DEFAULT 0,
                reason TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rental_change_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'Người kiểm kê',
                actor_role TEXT DEFAULT 'staff',
                summary TEXT NOT NULL,
                details TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_change_logs_rental ON rental_change_logs(rental_id, id DESC);

            CREATE TABLE IF NOT EXISTS supplementary_returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
                round_number INTEGER NOT NULL,
                round_name TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'Người kiểm kê',
                returned_items_json TEXT NOT NULL,
                remaining_shortages_json TEXT NOT NULL,
                total_returned_qty REAL DEFAULT 0,
                remaining_missing_qty REAL DEFAULT 0,
                new_total_compensation REAL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_supp_returns_rental ON supplementary_returns(rental_id, round_number);
            """
        )


def record_change_log(conn, rental_id, action, actor="Người kiểm kê", summary="", details="", actor_role="staff", created_at=None):
    """
    Ghi nhận log thay đổi dữ liệu trong phiếu kiểm kê.
    Hỗ trợ tích hợp xác thực người dùng (Auth/SSO/User system) sau này:
    - actor: Tên người thực hiện (e.g. 'Nguyễn Văn A', 'admin', 'Nhân viên kho')
    - actor_role: Vai trò ('admin', 'staff', 'inspector')
    - action: Loại hành động ('CREATE_RENTAL', 'INSPECT_ITEMS', 'EDIT_COMPLETED', 'COMPLETE_RENTAL', 'UPDATE_INFO', v.v.)
    - summary: Tóm tắt dễ đọc cho người dùng
    - details: Chi tiết kỹ thuật hoặc chuỗi JSON các linh kiện/trường thay đổi
    - created_at: Thời gian ISO (mặc định lấy now_iso())
    """
    ts = created_at or now_iso()
    actor_str = str(actor or "Người kiểm kê").strip() or "Người kiểm kê"
    conn.execute(
        """
        INSERT INTO rental_change_logs
            (rental_id, action, actor, actor_role, summary, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (rental_id, action, actor_str, actor_role or "staff", summary or "", str(details or ""), ts),
    )


def get_change_logs(conn, rental_id):
    """Lấy danh sách nhật ký thay đổi của một phiếu theo thứ tự mới nhất trước."""
    try:
        rows = conn.execute(
            """
            SELECT id, rental_id, action, actor, actor_role, summary, details, created_at
            FROM rental_change_logs
            WHERE rental_id = ?
            ORDER BY id DESC
            """,
            (rental_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "rental_id": r["rental_id"],
                "action": r["action"],
                "actor": r["actor"],
                "actor_role": r["actor_role"],
                "summary": r["summary"],
                "details": r["details"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        return []


def get_supplementary_returns(conn, rental_id):
    """Lấy danh sách các đợt trả hàng bổ sung của phiếu theo thứ tự đợt 1, 2, 3..."""
    try:
        import json
        rows = conn.execute(
            """
            SELECT id, rental_id, round_number, round_name, actor,
                   returned_items_json, remaining_shortages_json,
                   total_returned_qty, remaining_missing_qty, new_total_compensation,
                   note, created_at
            FROM supplementary_returns
            WHERE rental_id = ?
            ORDER BY round_number ASC, id ASC
            """,
            (rental_id,),
        ).fetchall()
        result = []
        for r in rows:
            res = dict(r)
            try:
                res["returned_items"] = json.loads(res["returned_items_json"] or "[]")
            except Exception:
                res["returned_items"] = []
            try:
                res["remaining_shortages"] = json.loads(res["remaining_shortages_json"] or "[]")
            except Exception:
                res["remaining_shortages"] = []
            result.append(res)
        return result
    except Exception:
        return []


def compute_item_status(expected_qty, actual_input, unit_price, is_flat_set_price=False):
    """
    Core inventory evaluation rule:
    - Blank actual_input -> default sufficient (missing = 0)
    - Negative number (-k) -> shortage of k (capped at expected_qty if expected_qty is defined)
    - Non-negative number >= 0 -> compare with expected_qty:
        - if expected_qty is not None and val < expected_qty -> shortage (expected_qty - val)
        - if expected_qty is not None and val > expected_qty -> surplus (val - expected_qty), comp = 0
        - else -> sufficient (missing = 0)
    - If is_flat_set_price is True (e.g. Command Card set), any shortage >= 1 bills 1 x unit_price.
    """
    price = float(unit_price or 0)
    raw = "" if actual_input is None else str(actual_input).strip()

    if raw == "":
        return {
            "status": "default_full",
            "status_label": "Đủ (mặc định)",
            "actual_num": None,
            "effective_actual": expected_qty,
            "missing_qty": 0,
            "surplus_qty": 0,
            "compensation": 0.0,
            "exceeds_expected": False,
        }

    try:
        val = float(raw.replace(",", "."))
    except ValueError:
        return {
            "status": "invalid",
            "status_label": "Giá trị không hợp lệ",
            "actual_num": None,
            "effective_actual": expected_qty,
            "missing_qty": 0,
            "surplus_qty": 0,
            "compensation": 0.0,
            "exceeds_expected": False,
        }

    if val < 0:
        missing = round(abs(val), 4)
        if expected_qty is not None and missing > expected_qty:
            missing = round(float(expected_qty), 4)
        if missing == int(missing):
            missing = int(missing)
        effective_actual = None
        if expected_qty is not None:
            eff = round(expected_qty - missing, 4)
            effective_actual = int(eff) if eff == int(eff) else eff
        comp = round(price, 2) if (is_flat_set_price and missing > 0) else round(missing * price, 2)
        return {
            "status": "shortage" if missing > 0 else "sufficient",
            "status_label": f"Thiếu {missing:g}" if missing > 0 else "Đủ",
            "actual_num": val,
            "effective_actual": effective_actual,
            "missing_qty": missing,
            "surplus_qty": 0,
            "compensation": comp,
            "exceeds_expected": False,
        }

    if expected_qty is None:
        return {
            "status": "sufficient",
            "status_label": "Đủ",
            "actual_num": val,
            "effective_actual": val,
            "missing_qty": 0,
            "surplus_qty": 0,
            "compensation": 0.0,
            "exceeds_expected": False,
        }

    if val < expected_qty:
        missing = round(expected_qty - val, 4)
        if missing == int(missing):
            missing = int(missing)
        comp = round(price, 2) if is_flat_set_price else round(missing * price, 2)
        return {
            "status": "shortage",
            "status_label": f"Thiếu {missing:g}",
            "actual_num": val,
            "effective_actual": val,
            "missing_qty": missing,
            "surplus_qty": 0,
            "compensation": comp,
            "exceeds_expected": False,
        }
    elif val > expected_qty:
        surplus = round(val - expected_qty, 4)
        if surplus == int(surplus):
            surplus = int(surplus)
        return {
            "status": "surplus",
            "status_label": f"Dư +{surplus:g}",
            "actual_num": val,
            "effective_actual": val,
            "missing_qty": 0,
            "surplus_qty": surplus,
            "compensation": 0.0,
            "exceeds_expected": False,
        }
    else:
        return {
            "status": "sufficient",
            "status_label": "Đủ",
            "actual_num": val,
            "effective_actual": val,
            "missing_qty": 0,
            "surplus_qty": 0,
            "compensation": 0.0,
            "exceeds_expected": False,
        }


def generate_ticket_code(conn):
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"PKK-{today}-"
    row = conn.execute(
        "SELECT code FROM rentals WHERE code LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    if row:
        try:
            seq = int(row["code"].split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:03d}"


def apply_pair_group_billing(items_eval):
    """
    Reconcile compensation for:
    1) Flat-set Card items (e.g. Command Card packs): missing 1 or all cards bills 1 x unit_price.
    2) Paired/set components ('linh kien theo bo') within a single rental kit instance so losing
       any subset OR all parts of the same set only bills for max(missing_i) * set_price.
    """
    pair_groups = {}
    for it in items_eval:
        is_short = it["status"] == "shortage" and it["missing_qty"] > 0
        unit_price = float(it.get("unit_price") or 0)
        it["raw_compensation"] = round(it["missing_qty"] * unit_price, 2) if is_short else 0.0
        it["pair_charge_role"] = "none"
        it["pair_charge_qty"] = it["missing_qty"] if is_short else 0
        it["pair_group_missing_qty"] = it["missing_qty"] if is_short else 0
        it["pair_primary_name"] = ""
        it["pair_billing_note"] = ""
        if is_short and it.get("is_flat_set_price"):
            it["compensation"] = round(unit_price, 2)
            it["pair_charge_role"] = "flat_set"
            it["pair_charge_qty"] = 1
            it["pair_group_missing_qty"] = 1
            exp_q = it.get("expected_qty")
            unit_str = it.get("unit") or "Cái"
            if exp_q is not None:
                it["pair_billing_note"] = (
                    f"Tiền cả 1 bộ Card ({exp_q:g} {unit_str}): thiếu {it['missing_qty']:g} hay toàn bộ chỉ tính ×1"
                )
            else:
                it["pair_billing_note"] = (
                    f"Tiền cả 1 bộ Card: thiếu {it['missing_qty']:g} hay toàn bộ chỉ tính ×1"
                )
        pg = it.get("pair_group")
        if pg:
            pair_groups.setdefault(pg, []).append(it)

    for _pg, members in pair_groups.items():
        if len(members) < 2:
            continue
        shortages = [m for m in members if m["status"] == "shortage" and m["missing_qty"] > 0]
        if not shortages:
            continue

        group_missing_sets = max(m["missing_qty"] for m in shortages)
        if group_missing_sets == int(group_missing_sets):
            group_missing_sets = int(group_missing_sets)

        def _item_sort_key(m):
            row_idx = 0
            for k in ("row_num", "stt", "item_id"):
                v = m.get(k)
                if v is not None:
                    try:
                        row_idx = int(str(v).strip())
                        break
                    except (ValueError, TypeError):
                        pass
            return (m.get("missing_qty") or 0, -row_idx)

        primary = max(shortages, key=_item_sort_key)
        set_price = float(primary.get("unit_price") or members[0].get("unit_price") or 0)
        group_comp = round(group_missing_sets * set_price, 2)
        all_names = " + ".join(m["name"] for m in members)

        if len(shortages) == 1:
            solo = shortages[0]
            solo["compensation"] = group_comp
            solo["pair_charge_qty"] = group_missing_sets
            solo["pair_group_missing_qty"] = group_missing_sets
            solo["pair_charge_role"] = "solo_in_pair"
            solo["pair_billing_note"] = (
                f"Theo bộ ({all_names}): mất {solo['missing_qty']:g} chi tiết tính bù {group_missing_sets:g} bộ"
            )
            for m in members:
                if m is not solo:
                    m["pair_charge_role"] = "partner_shortage"
                    m["pair_group_missing_qty"] = group_missing_sets
                    m["pair_primary_name"] = solo["name"]
                    m["pair_billing_note"] = (
                        f"Cùng bộ với '{solo['name']}' (đã tính {group_missing_sets:g} bộ ở dòng kia)"
                    )
        else:
            miss_desc = " + ".join(f"{m['name']} (-{m['missing_qty']:g})" for m in shortages)
            unit_phrase = "1 bộ" if group_missing_sets == 1 else f"{group_missing_sets:g} bộ"
            for m in members:
                m["pair_group_missing_qty"] = group_missing_sets
                m["pair_primary_name"] = primary["name"]
                if m is primary:
                    m["compensation"] = group_comp
                    m["pair_charge_qty"] = group_missing_sets
                    m["pair_charge_role"] = "primary"
                    m["pair_billing_note"] = f"Tính trọn {unit_phrase} ({miss_desc})"
                elif m["status"] == "shortage" and m["missing_qty"] > 0:
                    m["compensation"] = 0.0
                    m["pair_charge_qty"] = 0
                    m["pair_charge_role"] = "included_in_pair"
                    m["pair_billing_note"] = (
                        f"Đã tính chung {unit_phrase} với '{primary['name']}' (0 đ)"
                    )
                else:
                    m["pair_charge_role"] = "partner_shortage"
                    m["pair_billing_note"] = (
                        f"Cùng bộ đã tính chung với '{primary['name']}'"
                    )


def build_rental_detail(conn, rental_row, catalog_by_id):
    rental = dict(rental_row)
    rental["is_deleted"] = bool(rental.get("deleted_at"))
    rental["days_remaining"] = (
        compute_days_remaining(rental.get("deleted_expires_at"))
        if rental.get("deleted_at")
        else None
    )
    rk_rows = conn.execute(
        "SELECT * FROM rental_kits WHERE rental_id = ? ORDER BY id", (rental["id"],)
    ).fetchall()
    kits_selected = [dict(r) for r in rk_rows]

    # Count how many instances of each kit_id this rental has
    kit_type_totals = {}
    for rk in kits_selected:
        kit_type_totals[rk["kit_id"]] = kit_type_totals.get(rk["kit_id"], 0) + 1

    entry_rows = conn.execute(
        "SELECT * FROM inspection_entries WHERE rental_id = ?", (rental["id"],)
    ).fetchall()
    entries_map = {(r["rental_kit_id"], r["item_id"]): dict(r) for r in entry_rows}

    total_items = 0
    checked_count = 0
    shortage_items = []
    kit_details = []
    total_missing_qty = 0
    total_billed_qty = 0
    paired_dedup_count = 0
    flat_set_count = 0
    total_compensation = 0.0

    for rk in kits_selected:
        rk_id = rk["id"]
        kit_id = rk["kit_id"]
        instance_index = int(rk["instance_index"] or 1)
        kit_code = (rk["kit_code"] or "").strip()
        same_type_total = kit_type_totals.get(kit_id, 1)

        cat_kit = catalog_by_id.get(kit_id)
        if not cat_kit:
            # Map legacy or variant IDs to standard catalog kit IDs
            if kit_id in ("beta_2", "beta_set", "beta_set_2"):
                cat_kit = catalog_by_id.get("beta")
            elif kit_id in ("alpha_set_a", "alpha_1", "alpha_a_set"):
                cat_kit = catalog_by_id.get("alpha_a")
            elif kit_id in ("alpha_set_b", "alpha_2", "alpha_b_set"):
                cat_kit = catalog_by_id.get("alpha_b")
            elif kit_id in ("alpha_set_c", "alpha_3", "alpha_c_set"):
                cat_kit = catalog_by_id.get("alpha_c")

        if not cat_kit and rk.get("custom_items_json"):
            try:
                items_parsed = json.loads(rk["custom_items_json"])
                cat_kit = {
                    "id": kit_id,
                    "name": rk["kit_name"],
                    "items": items_parsed,
                }
            except Exception:
                cat_kit = None
        elif cat_kit and rk.get("custom_items_json"):
            try:
                extra_items = json.loads(rk["custom_items_json"])
                cat_kit = {
                    **cat_kit,
                    "items": list(cat_kit["items"]) + extra_items,
                }
            except Exception:
                pass

        if not cat_kit:
            # Fallback for unmapped kit so order is NEVER displayed empty!
            cat_kit = {
                "id": kit_id,
                "name": rk.get("kit_name") or kit_id,
                "items": [{
                    "id": f"{kit_id}_item_1",
                    "code": rk.get("kit_code") or "",
                    "name": rk.get("kit_name") or kit_id,
                    "unit": "Bộ",
                    "qty": 1,
                    "unit_price": 0.0,
                    "images": [],
                }]
            }

        # Friendly display label for this individual kit instance
        inst_suffix = f" (Bộ {instance_index}/{same_type_total})" if same_type_total > 1 else ""
        if kit_id in ("phu_kien_le", "phu_kien_di_kem"):
            display_label = cat_kit["name"]
        elif kit_code:
            display_label = f"{cat_kit['name']}{inst_suffix} [{kit_code}]"
        else:
            display_label = f"{cat_kit['name']}{inst_suffix}"

        kit_items_eval = []
        kit_checked_count = 0

        for item in cat_kit["items"]:
            total_items += 1
            entry = entries_map.get((rk_id, item["id"]), {})
            custom_qty = entry.get("custom_qty")
            actual_input = entry.get("actual_input")
            item_note = entry.get("item_note", "")

            if custom_qty is not None:
                expected_qty = custom_qty
            elif item.get("qty") is not None:
                expected_qty = item["qty"]
            elif item.get("expected_qty") is not None:
                expected_qty = item["expected_qty"]
            else:
                expected_qty = None

            if actual_input is not None and str(actual_input).strip() != "":
                checked_count += 1
                kit_checked_count += 1

            calc = compute_item_status(
                expected_qty,
                actual_input,
                item.get("unit_price", 0),
                is_flat_set_price=bool(item.get("is_flat_set_price")),
            )

            enriched = {
                **item,
                "rental_kit_id": rk_id,
                "kit_name": cat_kit["name"],
                "kit_code": kit_code,
                "instance_index": instance_index,
                "same_type_total": same_type_total,
                "kit_display_label": display_label,
                "base_qty": item.get("qty", expected_qty),
                "expected_qty": expected_qty,
                "custom_qty": custom_qty,
                "actual_input": "" if actual_input is None else str(actual_input),
                "item_note": item_note,
                "updated_at": entry.get("updated_at") or "",
                **calc,
            }
            kit_items_eval.append(enriched)

        # Reconcile paired/set components within this kit instance before summing totals
        apply_pair_group_billing(kit_items_eval)

        kit_missing_count = 0
        kit_missing_qty = 0
        kit_comp = 0.0
        for enriched in kit_items_eval:
            if enriched["status"] == "shortage":
                kit_missing_count += 1
                kit_missing_qty += enriched["missing_qty"]
                total_billed_qty += enriched.get("pair_charge_qty", enriched["missing_qty"])
                if enriched.get("pair_charge_role") == "included_in_pair":
                    paired_dedup_count += 1
                elif enriched.get("pair_charge_role") == "flat_set":
                    flat_set_count += 1
                kit_comp += enriched["compensation"]
                shortage_items.append(enriched)

        total_missing_qty += kit_missing_qty
        total_compensation += kit_comp
        kit_details.append(
            {
                "rental_kit_id": rk_id,
                "kit_id": kit_id,
                "kit_name": cat_kit["name"],
                "kit_code": kit_code,
                "instance_index": instance_index,
                "same_type_total": same_type_total,
                "display_label": display_label,
                "has_codes": cat_kit.get("has_codes", True),
                "has_fixed_qty": cat_kit.get("has_fixed_qty", True),
                "sections": cat_kit.get("sections", []),
                "total_items": len(kit_items_eval),
                "checked_count": kit_checked_count,
                "missing_items_count": kit_missing_count,
                "missing_qty": round(kit_missing_qty, 4),
                "compensation": round(kit_comp, 2),
                "is_added_after": bool(rk.get("is_added_after")),
                "items": kit_items_eval,
            }
        )

    rental["kits"] = [
        {
            "rental_kit_id": kd["rental_kit_id"],
            "kit_id": kd["kit_id"],
            "kit_name": kd["kit_name"],
            "kit_code": kd["kit_code"],
            "instance_index": kd["instance_index"],
            "same_type_total": kd["same_type_total"],
            "display_label": kd["display_label"],
            "total_items": kd["total_items"],
            "checked_count": kd["checked_count"],
            "missing_items_count": kd["missing_items_count"],
            "compensation": kd["compensation"],
            "is_added_after": kd.get("is_added_after", False),
        }
        for kd in kit_details
    ]
    rental["kit_details"] = kit_details
    rental["shortage_items"] = shortage_items
    rental["summary"] = {
        "total_items": total_items,
        "checked_count": checked_count,
        "shortage_items_count": len(shortage_items),
        "total_missing_qty": round(total_missing_qty, 4),
        "total_billed_qty": round(total_billed_qty, 4),
        "paired_dedup_count": paired_dedup_count,
        "flat_set_count": flat_set_count,
        "total_compensation": round(total_compensation, 0),
        "total_compensation_exact": round(total_compensation, 2),
    }
    initial_insp = None
    if rental.get("initial_inspection_json"):
        try:
            initial_insp = json.loads(rental["initial_inspection_json"])
        except Exception:
            initial_insp = None

    supp_rounds = get_supplementary_returns(conn, rental["id"])

    if not initial_insp:
        initial_insp = {
            "round_number": 1,
            "round_name": "Kiểm kê ban đầu khi nhận hàng trả",
            "actor": rental.get("returner_name") or "Người kiểm kê",
            "created_at": rental.get("completed_at") or rental.get("created_at") or now_iso(),
            "shortages": [
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
                for it in shortage_items
            ],
            "total_missing_qty": round(total_missing_qty, 4),
            "total_compensation": round(total_compensation, 0),
        }

    rental["initial_inspection"] = initial_insp
    rental["change_logs"] = get_change_logs(conn, rental["id"])
    rental["supplementary_rounds"] = supp_rounds

    # 1. Parse original info and compute differences if edited
    orig_info = {}
    if rental.get("original_info_json"):
        try:
            orig_info = json.loads(rental["original_info_json"])
        except Exception:
            orig_info = {}
    if not orig_info:
        orig_info = {
            "team_name": rental["team_name"],
            "returner_name": rental["returner_name"],
            "phone": rental["phone"],
            "email": rental.get("email") or "",
            "due_date": rental.get("due_date") or "",
            "delivery_address": rental.get("delivery_address") or "",
            "note": rental.get("note") or "",
            "return_status": rental.get("return_status") or "Đã trả",
        }

    fields_meta = [
        ("team_name", "Khách hàng / Đội thuê"),
        ("returner_name", "Người nhận & SĐT / Người trả"),
        ("phone", "Số điện thoại"),
        ("email", "Email"),
        ("due_date", "Hạn trả"),
        ("delivery_address", "Địa chỉ giao hàng"),
        ("return_status", "Tình trạng trả thiết bị"),
        ("note", "Ghi chú"),
    ]
    info_diff = []
    for k, lbl in fields_meta:
        cur_v = str(rental.get(k) or "").strip()
        orig_v = str(orig_info.get(k) or "").strip()
        if cur_v != orig_v:
            info_diff.append({
                "field": k,
                "label": lbl,
                "old_val": orig_v,
                "new_val": cur_v,
            })

    rental["is_edited"] = len(info_diff) > 0
    rental["info_diff"] = info_diff
    rental["original_info"] = orig_info
    rental["return_status"] = rental.get("return_status") or "Đã trả"

    # 2. Compute original kits vs added kits/items
    added_kits_summary = []
    original_kits_summary = []
    for kd in kit_details:
        rk_raw = next((r for r in kits_selected if r["id"] == kd["rental_kit_id"]), {})
        is_added_kit = bool(rk_raw.get("is_added_after"))
        
        # Check custom items inside this kit (loose items)
        added_items = [it for it in kd["items"] if it.get("is_added_after")]
        orig_items = [it for it in kd["items"] if not it.get("is_added_after")]
        
        if is_added_kit:
            added_kits_summary.append({
                "type": "kit",
                "display_label": kd["display_label"],
                "kit_name": kd["kit_name"],
                "total_items": kd["total_items"],
                "is_added_after": True,
            })
        else:
            if added_items:
                for ait in added_items:
                    added_kits_summary.append({
                        "type": "item",
                        "display_label": f"{ait['name']} ({kd['display_label']})",
                        "item_name": ait["name"],
                        "qty": ait.get("expected_qty") or 1,
                        "unit": ait.get("unit") or "cái",
                        "unit_price": ait.get("unit_price") or 0,
                        "is_added_after": True,
                    })
            if orig_items or not added_items:
                original_kits_summary.append({
                    "type": "kit",
                    "display_label": kd["display_label"],
                    "kit_name": kd["kit_name"],
                    "total_items": len(orig_items) if added_items else kd["total_items"],
                })

    rental["has_added_items"] = len(added_kits_summary) > 0
    rental["added_kits_summary"] = added_kits_summary
    rental["original_kits_summary"] = original_kits_summary

    return rental
