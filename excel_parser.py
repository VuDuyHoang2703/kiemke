import hashlib
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import openpyxl

EMU_PER_PT = 12700

KIT_METADATA = {
    "ALPHA A": {
        "id": "alpha_a",
        "name": "ALPHA A",
        "subtitle": "Bộ robot Alpha Set A",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["AlphaA", "alpha_a", "ALPHA A"],
    },
    "ALPHA B": {
        "id": "alpha_b",
        "name": "ALPHA B",
        "subtitle": "Bộ robot Alpha Set B",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["AlphaB", "alpha_b", "ALPHA B"],
    },
    "ALPHA C": {
        "id": "alpha_c",
        "name": "ALPHA C",
        "subtitle": "Bộ robot Alpha Set C",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["AlphaC", "alpha_c", "ALPHA C"],
    },
    "BETA (2)": {
        "id": "beta_2",
        "name": "BETA (2)",
        "subtitle": "Bộ robot BETA (2) tiêu chuẩn",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["Beta", "Beta2", "beta_2"],
    },
    "INTELL (E6)": {
        "id": "intell_e6",
        "name": "INTELL (E6)",
        "subtitle": "Bộ robot INTELL (E6)",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["Intell", "IntellE6", "intell_e6"],
    },
    "INNO (E7)": {
        "id": "inno_e7",
        "name": "INNO (E7)",
        "subtitle": "Bộ Innovation (E7) & linh kiện bổ sung",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["Inno", "InnoE7", "inno_e7"],
    },
    "MỞ RỘNG 2681": {
        "id": "mo_rong_2681",
        "name": "MỞ RỘNG 2681",
        "subtitle": "Bộ linh kiện mở rộng 2681",
        "has_codes": False,
        "has_fixed_qty": True,
        "cover_candidates": ["MoRong", "MoRong2681", "mo_rong_2681"],
    },
    "bộ nhiệm vụ 2671": {
        "id": "nhiem_vu_2671",
        "name": "Bộ nhiệm vụ 2671",
        "subtitle": "Bộ nhiệm vụ 2671 gồm 6 cụm mô-đun & túi linh kiện lẻ",
        "has_codes": True,
        "has_fixed_qty": True,
        "cover_candidates": ["NhiemVu", "BoNhiemVu", "NhiemVu2671", "BoNhiemVu2671", "2671"],
    },
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def resolve_kit_cover_image(image_dir: Path, sheet_name: str, kit_id: str = "") -> str | None:
    """Find a dedicated kit cover image in the Image/ folder."""
    if not image_dir.exists() or not image_dir.is_dir():
        return None

    files = [
        p for p in sorted(image_dir.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    if not files:
        return None

    by_stem_lower = {p.stem.lower(): p for p in files}
    by_norm = {_norm_key(p.stem): p for p in files}

    meta = KIT_METADATA.get(sheet_name, {})
    candidates = list(meta.get("cover_candidates", []))
    if sheet_name:
        candidates.append(sheet_name)
    if kit_id:
        candidates.append(kit_id)

    matched = None
    for cand in candidates:
        c_low = cand.lower()
        c_norm = _norm_key(cand)
        if c_low in by_stem_lower:
            matched = by_stem_lower[c_low]
            break
        if c_norm and c_norm in by_norm:
            matched = by_norm[c_norm]
            break

    if not matched:
        return None

    ver = int(matched.stat().st_mtime)
    return f"/kit-covers/{matched.name}?v={ver}"


def clean_num(val):
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        rounded = round(float(val), 4)
        if rounded == int(rounded):
            return int(rounded)
        return rounded
    s = str(val).strip().replace(",", "")
    try:
        f = float(s)
        rounded = round(f, 4)
        if rounded == int(rounded):
            return int(rounded)
        return rounded
    except ValueError:
        return None


def clean_text(val):
    if val is None:
        return ""
    s = str(val).replace("\r\n", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _build_rich_vm_lookup(zf: zipfile.ZipFile):
    """Build 1-based vm index -> '/static/kit_images/<file>' for Excel Place-in-Cell images."""
    needed = {
        "xl/metadata.xml",
        "xl/richData/rdrichvalue.xml",
        "xl/richData/richValueRel.xml",
        "xl/richData/_rels/richValueRel.xml.rels",
    }
    if not needed.issubset(set(zf.namelist())):
        return {}

    try:
        md_root = ET.fromstring(zf.read("xl/metadata.xml"))
        rvb_indices = []
        for fm in md_root.iter():
            if fm.tag.endswith("}futureMetadata") and fm.attrib.get("name") == "XLRICHVALUE":
                for bk in fm:
                    rvbs = [e.attrib.get("i") for e in bk.iter() if e.tag.endswith("}rvb")]
                    rvb_indices.append(int(rvbs[0]) if rvbs else None)

        vm_elem = next((e for e in md_root.iter() if e.tag.endswith("}valueMetadata")), None)
        if vm_elem is None:
            return {}

        vm_to_rv = {}
        for vm_idx, bk in enumerate(vm_elem, start=1):
            rc = next((e for e in bk if e.tag.endswith("}rc")), None)
            if rc is not None:
                fm_i = int(rc.attrib.get("v", -1))
                if 0 <= fm_i < len(rvb_indices) and rvb_indices[fm_i] is not None:
                    vm_to_rv[vm_idx] = rvb_indices[fm_i]

        rv_root = ET.fromstring(zf.read("xl/richData/rdrichvalue.xml"))
        rv_to_rel = []
        for rv in rv_root:
            v_el = next((e for e in rv if e.tag.endswith("}v")), None)
            rv_to_rel.append(int(v_el.text) if v_el is not None and v_el.text is not None else None)

        rels_map = {
            r.attrib["Id"]: r.attrib["Target"]
            for r in ET.fromstring(zf.read("xl/richData/_rels/richValueRel.xml.rels"))
        }
        rvrel_root = ET.fromstring(zf.read("xl/richData/richValueRel.xml"))
        rel_urls = []
        for el in rvrel_root:
            rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
            target = rels_map.get(rid, "")
            rel_urls.append(f"/static/kit_images/{os.path.basename(target)}" if target else None)

        vm_to_url = {}
        for vm_idx, rv_idx in vm_to_rv.items():
            if 0 <= rv_idx < len(rv_to_rel):
                rel_idx = rv_to_rel[rv_idx]
                if rel_idx is not None and 0 <= rel_idx < len(rel_urls) and rel_urls[rel_idx]:
                    vm_to_url[vm_idx] = rel_urls[rel_idx]
        return vm_to_url
    except Exception:
        return {}


def extract_sheet_images(zf: zipfile.ZipFile, media_dir: Path):
    """Map (sheet_name, row_num_1based) -> list of static image URLs."""
    wb_rels = {}
    for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
        wb_rels[rel.attrib["Id"]] = rel.attrib["Target"]

    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheet_paths = {}
    for elem in wb_root.iter():
        if elem.tag.endswith("}sheet"):
            rid = [v for k, v in elem.attrib.items() if k.endswith("}id")][0]
            target = wb_rels[rid]
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            sheet_paths[elem.attrib["name"]] = target

    # Extract all media files once into static/kit_images/
    media_dir.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        if name.startswith("xl/media/"):
            fname = os.path.basename(name)
            dest = media_dir / fname
            data = zf.read(name)
            if not dest.exists() or dest.stat().st_size != len(data):
                dest.write_bytes(data)

    sheet_row_images = {}
    for sheet_name, path in sheet_paths.items():
        root = ET.fromstring(zf.read(path))
        default_ht = 15.0
        for sf in root.iter():
            if sf.tag.endswith("}sheetFormatPr") and "defaultRowHeight" in sf.attrib:
                default_ht = float(sf.attrib["defaultRowHeight"])

        row_hts = {}
        max_r = 1
        for row in root.iter():
            if row.tag.endswith("}row"):
                r_num = int(row.attrib.get("r", 0))
                max_r = max(max_r, r_num)
                ht = float(row.attrib["ht"]) if "ht" in row.attrib else default_ht
                row_hts[r_num] = ht

        y_start = {}
        y_end = {}
        cur = 0.0
        for r in range(1, max_r + 20):
            h_emu = row_hts.get(r, default_ht) * EMU_PER_PT
            y_start[r] = cur
            cur += h_emu
            y_end[r] = cur

        sheet_filename = path.split("/")[-1]
        sheet_rels_path = f"xl/worksheets/_rels/{sheet_filename}.rels"
        drawing_path = None
        if sheet_rels_path in zf.namelist():
            for r in ET.fromstring(zf.read(sheet_rels_path)):
                if "drawing" in r.attrib["Type"]:
                    drawing_path = "xl/" + r.attrib["Target"].replace("../", "")

        row_map = {}
        if drawing_path and drawing_path in zf.namelist():
            d_filename = drawing_path.split("/")[-1]
            d_rels_path = f"xl/drawings/_rels/{d_filename}.rels"
            d_rels = {}
            if d_rels_path in zf.namelist():
                d_rels = {
                    r.attrib["Id"]: r.attrib["Target"]
                    for r in ET.fromstring(zf.read(d_rels_path))
                }
            d_root = ET.fromstring(zf.read(drawing_path))
            anchors_raw = []
            for child in d_root:
                tag = child.tag.split("}")[-1]
                if tag not in ("twoCellAnchor", "oneCellAnchor"):
                    continue
                from_el = [e for e in child if e.tag.endswith("}from")][0]
                to_el = [e for e in child if e.tag.endswith("}to")]
                fc = int([e for e in from_el if e.tag.endswith("}col")][0].text)
                fco = int([e for e in from_el if e.tag.endswith("}colOff")][0].text)
                fr = int([e for e in from_el if e.tag.endswith("}row")][0].text) + 1
                fro = int([e for e in from_el if e.tag.endswith("}rowOff")][0].text)
                embed = None
                for e in child.iter():
                    if e.tag.endswith("}blip"):
                        for k, v in e.attrib.items():
                            if k.endswith("}embed"):
                                embed = v
                img_target = d_rels.get(embed)
                if not img_target:
                    continue
                img_name = os.path.basename(img_target)
                y0 = y_start.get(fr, 0) + fro
                if to_el:
                    tr = int([e for e in to_el[0] if e.tag.endswith("}row")][0].text) + 1
                    tro = int([e for e in to_el[0] if e.tag.endswith("}rowOff")][0].text)
                    y1 = y_start.get(tr, 0) + tro
                else:
                    ext_el = [e for e in child if e.tag.endswith("}ext")]
                    cy = int(ext_el[0].attrib.get("cy", 0)) if ext_el else 0
                    tr = fr
                    y1 = y0 + cy

                if (y1 - y0) <= 1000:
                    continue

                if fr == tr:
                    best_r = fr
                else:
                    best_r = fr
                    best_ov = -1
                    for r in range(fr, tr + 1):
                        ry0 = y_start.get(r, 0)
                        ry1 = y_end.get(r, 0)
                        ov = min(y1, ry1) - max(y0, ry0)
                        if ov > best_ov:
                            best_ov = ov
                            best_r = r
                    if best_ov <= 0:
                        y_mid = (y0 + y1) / 2.0
                        for r in range(fr, tr + 1):
                            if y_start.get(r, 0) <= y_mid < y_end.get(r, 0):
                                best_r = r
                                break

                anchors_raw.append((best_r, fc, fco, f"/static/kit_images/{img_name}"))

            # Sort multiple images in the same row left-to-right
            anchors_raw.sort(key=lambda x: (x[0], x[1], x[2]))
            for r_num, _fc, _fco, url in anchors_raw:
                lst = row_map.setdefault(r_num, [])
                if url not in lst:
                    lst.append(url)

        # Also extract Excel Place-in-Cell (XLRICHVALUE) images referenced via cell vm="..."
        rich_vm_lookup = _build_rich_vm_lookup(zf)
        if rich_vm_lookup:
            for c_el in root.iter():
                if c_el.tag.endswith("}c") and "vm" in c_el.attrib:
                    ref = c_el.attrib.get("r", "")
                    m_row = re.search(r"(\d+)$", ref)
                    if not m_row:
                        continue
                    r_num = int(m_row.group(1))
                    try:
                        vm_idx = int(c_el.attrib["vm"])
                    except ValueError:
                        continue
                    url = rich_vm_lookup.get(vm_idx)
                    if url:
                        lst = row_map.setdefault(r_num, [])
                        if url not in lst:
                            lst.append(url)

        sheet_row_images[sheet_name] = row_map
    return sheet_row_images


def build_merged_lookup(ws):
    """Map (row, col) -> (top_val, min_row, max_row) for merged ranges."""
    lookup = {}
    for rng in ws.merged_cells.ranges:
        top_val = ws.cell(rng.min_row, rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                lookup[(r, c)] = (top_val, rng.min_row, rng.max_row)
    return lookup


def detect_columns(ws):
    """Detect column indices dynamically from row 1 headers."""
    cols = {
        "stt_col": None,
        "code_col": None,
        "name_col": None,
        "unit_col": None,
        "price_col": None,
        "qty_cols": [],
    }
    qty_priority = []
    for c in range(1, min(20, ws.max_column + 1)):
        h = clean_text(ws.cell(1, c).value).lower()
        if not h:
            continue
        if h == "stt":
            cols["stt_col"] = c
        elif h.startswith("mã"):
            cols["code_col"] = c
        elif h == "tên hàng":
            cols["name_col"] = c
        elif h == "đvt":
            cols["unit_col"] = c
        elif h == "đơn giá":
            cols["price_col"] = c
        elif h in ("số lượng trong bộ", "sl trong bộ", "sl"):
            qty_priority.append((0, c))
        elif h == "số lượng":
            qty_priority.append((1, c))
    qty_priority.sort()
    cols["qty_cols"] = [c for _, c in qty_priority]
    return cols


def parse_workbook(xlsx_path: Path, static_dir: Path, output_json: Path):
    raw_bytes = xlsx_path.read_bytes()
    zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    media_dir = static_dir / "kit_images"
    sheet_row_images = extract_sheet_images(zf, media_dir)

    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
    kits = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        meta = KIT_METADATA.get(
            sheet_name,
            {
                "id": re.sub(r"[^a-z0-9]+", "_", sheet_name.lower()).strip("_"),
                "name": sheet_name,
                "subtitle": sheet_name,
                "has_codes": False,
                "has_fixed_qty": True,
            },
        )
        kit_id = meta["id"]
        merged = build_merged_lookup(ws)
        row_imgs = sheet_row_images.get(sheet_name, {})
        cols = detect_columns(ws)
        name_col = cols["name_col"] or 2
        code_col = cols["code_col"]
        unit_col = cols["unit_col"]
        price_col = cols["price_col"]
        qty_cols = cols["qty_cols"]

        items = []
        sections = []
        current_section = None
        seq_stt = 0

        for r in range(2, ws.max_row + 1):
            col_a = clean_text(ws.cell(r, 1).value)
            code = clean_text(ws.cell(r, code_col).value) if code_col else ""
            name = clean_text(ws.cell(r, name_col).value)
            if name.startswith("***"):
                continue

            unit_raw = ws.cell(r, unit_col).value if unit_col else None
            price_raw = ws.cell(r, price_col).value if price_col else None
            qty = None
            for qc in qty_cols:
                q_val = clean_num(ws.cell(r, qc).value)
                if q_val is not None:
                    qty = q_val
                    break

            # Handle section headers:
            # 1) e.g. INNO (E7) rows 2, 5: name exists, unit/price/qty are all None
            # 2) e.g. bộ nhiệm vụ 2671 rows 2, 54, 68, ...: name is empty, code or col_a exists
            if not name and (code or col_a):
                sec_name = code or col_a
                sec_id = f"{kit_id}_sec_{len(sections) + 1}"
                current_section = {
                    "id": sec_id,
                    "name": sec_name,
                    "image": row_imgs.get(r, [None])[0],
                }
                sections.append(current_section)
                continue

            if not name:
                continue

            if unit_raw is None and price_raw is None and qty is None and not (
                (unit_col and (r, unit_col) in merged) or (price_col and (r, price_col) in merged)
            ):
                sec_id = f"{kit_id}_sec_{len(sections) + 1}"
                current_section = {
                    "id": sec_id,
                    "name": name,
                    "image": row_imgs.get(r, [None])[0],
                }
                sections.append(current_section)
                continue

            # Paired row fallback for missing qty inside merged price/unit range (e.g. INTELL R100)
            if qty is None and price_col and (r, price_col) in merged and qty_cols:
                top_r = merged[(r, price_col)][1]
                for qc in qty_cols:
                    q_val = clean_num(ws.cell(top_r, qc).value)
                    if q_val is not None:
                        qty = q_val
                        break

            # Handle merged or consecutive unmerged paired unit / price cells (linh kien theo bo)
            is_paired = False
            pair_group = None
            pair_label = ""
            if unit_col and (r, unit_col) in merged and unit_raw is None:
                unit_raw = merged[(r, unit_col)][0]

            in_merged_price = bool(price_col and (r, price_col) in merged)
            if in_merged_price:
                top_price, mr1, mr2 = merged[(r, price_col)]
                if mr2 > mr1:
                    is_paired = True
                    pair_group = f"{kit_id}__pair_{mr1}_{mr2}"
                if price_raw is None:
                    price_raw = top_price
            elif price_raw is None and items:
                # Consecutive unmerged component whose Đơn giá (and usually ĐVT) is blank,
                # sharing the set price defined on the immediately preceding component row.
                prev_it = items[-1]
                cur_sec_id = current_section["id"] if current_section else None
                if (
                    prev_it.get("section_id") == cur_sec_id
                    and prev_it.get("row_num") == r - 1
                    and float(prev_it.get("unit_price") or 0) > 0
                ):
                    is_paired = True
                    pair_group = prev_it.get("pair_group") or f"{kit_id}__pair_{prev_it['row_num']}_{r}"
                    prev_it["is_paired_price"] = True
                    prev_it["pair_group"] = pair_group
                    if not unit_col and prev_it.get("unit") == "Cái":
                        prev_it["unit"] = "Bộ"
                    if unit_raw is None:
                        unit_raw = prev_it["unit"]
                    price_raw = prev_it["unit_price"]
                    if qty is None:
                        qty = prev_it.get("qty")

            unit = clean_text(unit_raw) or "Cái"
            unit_price = clean_num(price_raw) or 0
            imgs = row_imgs.get(r, [])
            seq_stt += 1

            # Determine STT display (keep section-local STT for sheets with codes if present, else seq_stt)
            raw_stt = clean_num(ws.cell(r, 1).value) if code_col else seq_stt
            stt = int(raw_stt) if raw_stt is not None else seq_stt

            items.append(
                {
                    "id": f"{kit_id}__r{r}",
                    "kit_id": kit_id,
                    "row_num": r,
                    "stt": stt,
                    "seq": seq_stt,
                    "section_id": current_section["id"] if current_section else None,
                    "section_name": current_section["name"] if current_section else None,
                    "code": code,
                    "name": name,
                    "unit": unit,
                    "unit_price": unit_price,
                    "qty": qty,
                    "is_paired_price": is_paired,
                    "is_flat_set_price": bool(re.search(r"\bcard\b", name.lower())),
                    "pair_group": pair_group,
                    "pair_index": None,
                    "pair_size": 1,
                    "pair_members": [],
                    "pair_member_ids": [],
                    "pair_label": pair_label,
                    "images": imgs,
                }
            )

        # Post-process paired set metadata across each group
        pair_buckets = {}
        for it in items:
            pg = it.get("pair_group")
            if pg:
                pair_buckets.setdefault(pg, []).append(it)
        for pg, grp in pair_buckets.items():
            if len(grp) < 2:
                for it in grp:
                    it["is_paired_price"] = False
                    it["pair_group"] = None
                continue
            member_names = [g["name"] for g in grp]
            member_ids = [g["id"] for g in grp]
            p_label = f"Tính theo bộ ({' + '.join(member_names)})"
            for idx_g, it in enumerate(grp):
                it["is_paired_price"] = True
                it["pair_group"] = pg
                it["pair_index"] = idx_g
                it["pair_size"] = len(grp)
                it["pair_members"] = member_names
                it["pair_member_ids"] = member_ids
                it["pair_label"] = p_label

        # Prefer dedicated kit cover image in Image/ directory; fallback to section/item image
        image_dir = xlsx_path.parent / "Image"
        preview_img = resolve_kit_cover_image(image_dir, sheet_name, kit_id)
        if not preview_img:
            for sec in sections:
                if sec.get("image"):
                    preview_img = sec["image"]
                    break
        if not preview_img:
            for it in items:
                if it["images"]:
                    preview_img = it["images"][0]
                    break

        total_pieces = sum((it["qty"] or 0) for it in items)
        has_fixed_qty = any(it["qty"] is not None for it in items)
        kits.append(
            {
                "id": kit_id,
                "name": meta["name"],
                "sheet_name": sheet_name,
                "subtitle": meta["subtitle"],
                "has_codes": bool(code_col) or meta["has_codes"],
                "has_fixed_qty": has_fixed_qty,
                "total_items": len(items),
                "total_pieces": total_pieces,
                "preview_image": preview_img,
                "sections": sections,
                "items": items,
            }
        )

    catalog = {
        "catalog_version": 3,
        "source_file": xlsx_path.name,
        "kits": kits,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    xlsx = base / "data" / "KIEM KE HANG THUE.xlsx"
    if not xlsx.exists():
        xlsx = base / "KIEM KE HANG THUE.xlsx"
    cat = parse_workbook(
        xlsx,
        base / "static",
        base / "data" / "catalog.json",
    )
    for k in cat["kits"]:
        no_img = [it["name"] for it in k["items"] if not it["images"]]
        zero_price = [it["name"] for it in k["items"] if not it["unit_price"]]
        print(
            f"Kit {k['name']}: {k['total_items']} items, {k['total_pieces']} pieces, "
            f"{len(k['sections'])} sections, no_img={no_img}, zero_price={zero_price}"
        )

