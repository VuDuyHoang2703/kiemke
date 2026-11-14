(() => {
  "use strict";

  const state = {
    view: "checkin",
    catalog: { kits: [] },
    kitsById: {},
    rentals: [],
    selectedKitInstances: {}, // { kit_id: [{ rental_kit_id: null|number, kit_code: string }] }
    lastCreatedId: null,
    teamSearch: "",
    teamFilter: "all",
    activeRental: null,
    activeKitTab: "all", // numeric rental_kit_id or "all"
    inspSearch: "",
    inspFilter: "all",
    inspSection: "all",
    dirtyEntries: new Map(), // `${rental_kit_id}__${item_id}` -> entry payload
    completedEditPending: false,
    saveTimer: null,
    savingPromise: null,
    checkinMode: "crm",
    crmPreviewData: null,
    crmSearch: "",
    crmFilter: "all",
  };

  // --- Utilities ---
  const $ = (id) => document.getElementById(id);

  function isValidEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(email || "").trim());
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeText(str) {
    return String(str ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/đ/g, "d")
      .replace(/Đ/g, "D")
      .toLowerCase();
  }

  function formatMoney(amount, allowDecimals = false) {
    const num = Number(amount || 0);
    if (!Number.isFinite(num)) return "0 ₫";
    const opts = allowDecimals
      ? { minimumFractionDigits: 0, maximumFractionDigits: 2 }
      : { minimumFractionDigits: 0, maximumFractionDigits: 0 };
    const val = allowDecimals ? num : Math.round(num);
    return val.toLocaleString("vi-VN", opts) + " ₫";
  }

  function formatQty(val) {
    if (val === null || val === undefined || val === "") return "-";
    const num = Number(val);
    if (!Number.isFinite(num)) return "-";
    return Number.isInteger(num) ? String(num) : num.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
  }

  function formatDateTime(iso) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T"));
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("vi-VN", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatDateShort(iso) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T"));
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("vi-VN", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  }

  function formatLogTimestamp(iso) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T"));
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    const day = pad(d.getDate());
    const month = pad(d.getMonth() + 1);
    const year = d.getFullYear();
    const hours = pad(d.getHours());
    const minutes = pad(d.getMinutes());
    const seconds = pad(d.getSeconds());
    return `${hours}:${minutes}:${seconds} ${day}/${month}/${year}`;
  }

  function numberToVietnameseWords(number) {
    const n = Math.round(Number(number || 0));
    if (n === 0) return "Không đồng";
    const digits = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"];
    const units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ"];

    function readTriple(num, full) {
      const tram = Math.floor(num / 100);
      const chuc = Math.floor((num % 100) / 10);
      const donvi = num % 10;
      let res = "";
      if (tram > 0 || full) {
        res += digits[tram] + " trăm ";
      }
      if (chuc > 1) {
        res += digits[chuc] + " mươi ";
        if (donvi === 1) res += "mốt ";
        else if (donvi === 5) res += "lăm ";
        else if (donvi > 0) res += digits[donvi] + " ";
      } else if (chuc === 1) {
        res += "mười ";
        if (donvi === 5) res += "lăm ";
        else if (donvi > 0) res += digits[donvi] + " ";
      } else if (donvi > 0) {
        if (tram > 0 || full) res += "lẻ ";
        res += digits[donvi] + " ";
      }
      return res.trim();
    }

    let remaining = Math.abs(n);
    const groups = [];
    while (remaining > 0) {
      groups.push(remaining % 1000);
      remaining = Math.floor(remaining / 1000);
    }

    let parts = [];
    for (let i = groups.length - 1; i >= 0; i--) {
      const g = groups[i];
      if (g > 0) {
        const full = i < groups.length - 1;
        parts.push(readTriple(g, full) + (units[i] ? " " + units[i] : ""));
      }
    }
    const text = parts.join(" ").replace(/\s+/g, " ").trim();
    return text.charAt(0).toUpperCase() + text.slice(1) + " đồng";
  }

  function showToast(msg, type = "info") {
    const box = $("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${
      type === "success"
        ? "toast-success"
        : type === "warning"
        ? "toast-warning"
        : type === "error"
        ? "toast-error"
        : ""
    }`;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }

  function hasCheckinDraft() {
    const hasFields = ["input-returner", "input-team", "input-phone", "input-email", "input-note"].some(
      (id) => ($(id)?.value || "").trim() !== ""
    );
    const hasKits = Object.values(state.selectedKitInstances).some((arr) => arr && arr.length > 0);
    return hasFields || hasKits;
  }

  function getStatusLabel(status) {
    if (status === "completed") return "Đã hoàn tất";
    if (status === "inspecting") return "Đang kiểm kê";
    return "Chờ kiểm kê";
  }

  function updateTopbarStatusUI() {
    const draftDot = $("nav-checkin-draft-dot");
    if (draftDot) {
      draftDot.hidden = !(state.view !== "checkin" && hasCheckinDraft());
    }
    const pill = $("nav-uninspected-pill") || $("active-ticket-pill");
    if (!pill) return;

    let uninspectedReturnedCount = 0;
    if (state.rentals && Array.isArray(state.rentals)) {
      for (const r of state.rentals) {
        const retInfo = getReturnStatusInfo(r);
        if (retInfo.isReturned && r.status === "pending") {
          uninspectedReturnedCount++;
        }
      }
    }

    const txtEl = $("nav-uninspected-text");
    if (uninspectedReturnedCount > 0) {
      const msg = `Đã trả chưa kiểm: ${uninspectedReturnedCount} đơn`;
      if (txtEl) txtEl.textContent = msg;
      else pill.textContent = msg;
      pill.className = "nav-uninspected-pill";
      pill.title = `Có ${uninspectedReturnedCount} đơn đã bàn giao thiết bị về kho nhưng chưa kiểm kê. Bấm để lọc danh sách.`;
      pill.hidden = false;
    } else {
      const msg = `0 đơn đã trả chưa kiểm`;
      if (txtEl) txtEl.textContent = msg;
      else pill.textContent = msg;
      pill.className = "nav-uninspected-pill pill-empty";
      pill.title = `Hiện không có đơn nào đã trả thiết bị mà chưa kiểm kê.`;
      pill.hidden = false;
    }
  }

  function openStep1Confirm({ title, message, okLabel = "Xác nhận", onConfirm }) {
    const modal = $("step1-confirm-modal");
    const okBtn = $("step1-confirm-ok");
    const cancelBtn = $("step1-confirm-cancel");
    const closeBtn = $("step1-confirm-close");
    $("step1-confirm-title").textContent = title || "Xác nhận thao tác";
    $("step1-confirm-msg").textContent = message || "";
    okBtn.textContent = okLabel;

    const close = () => {
      modal.hidden = true;
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      closeBtn.onclick = null;
      modal.onclick = null;
    };
    cancelBtn.onclick = close;
    closeBtn.onclick = close;
    modal.onclick = (ev) => {
      if (ev.target === modal) close();
    };
    okBtn.onclick = () => {
      close();
      onConfirm?.();
    };
    modal.hidden = false;
  }

  function confirmUnsavedCompletedEdit(onConfirm) {
    const modal = $("unsaved-edit-modal");
    if (!modal) {
      const ok = window.confirm("Chỉnh sửa chưa lưu, bạn có muốn thoát ra không?");
      if (ok) {
        state.dirtyEntries.clear();
        state.completedEditPending = false;
        setSaveIndicator("hidden");
        onConfirm?.();
      }
      return;
    }

    const okBtn = $("unsaved-edit-ok");
    const cancelBtn = $("unsaved-edit-cancel");
    const closeBtn = $("unsaved-edit-close");

    const close = () => {
      modal.hidden = true;
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      closeBtn.onclick = null;
      modal.onclick = null;
    };

    cancelBtn.onclick = close;
    closeBtn.onclick = close;
    modal.onclick = (ev) => {
      if (ev.target === modal) close();
    };

    okBtn.onclick = () => {
      close();
      state.dirtyEntries.clear();
      state.completedEditPending = false;
      setSaveIndicator("hidden");
      onConfirm?.();
    };

    modal.hidden = false;
  }

  function focusKitCodeInput(kitId, instIdx = 0) {
    const grid = $("kit-selector-grid");
    if (!grid) return;
    const target =
      grid.querySelector(`.kit-code-input[data-kit-id="${CSS.escape(String(kitId))}"][data-inst-idx="${instIdx}"]`) ||
      grid.querySelector(`.kit-card[data-kit-id="${CSS.escape(String(kitId))}"] .kit-code-input`);
    if (target) target.focus();
  }

  function setSaveIndicator(mode) {
    const el = $("save-indicator");
    if (mode === "hidden") {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    if (mode === "dirty-completed") {
      el.textContent = "Đang sửa phiếu (Chưa lưu)";
      el.classList.add("saving");
    } else if (mode === "saving") {
      el.textContent = "Đang lưu...";
      el.classList.add("saving");
    } else {
      el.textContent = "Đã lưu tự động";
      el.classList.remove("saving");
    }
  }

  function getCurrentStaffName() {
    return (
      (window.CURRENT_USER && (window.CURRENT_USER.username || window.CURRENT_USER.name || window.CURRENT_USER.email)) ||
      localStorage.getItem("username") ||
      localStorage.getItem("current_user") ||
      localStorage.getItem("auth_username") ||
      localStorage.getItem("inventory_staff_name") ||
      "Tài khoản người dùng"
    );
  }

  function setCurrentStaffName(name) {
    const clean = (name || "").trim() || "Tài khoản người dùng";
    localStorage.setItem("inventory_staff_name", clean);
    localStorage.setItem("username", clean);
    const el = $("log-current-staff-display");
    if (el) el.textContent = clean;
    return clean;
  }

  async function api(url, options = {}) {
    const staffName = getCurrentStaffName();
    const headers = {
      "Content-Type": "application/json",
      "X-User-Name": encodeURIComponent(staffName),
      ...(options.headers || {}),
    };
    const resp = await fetch(url, {
      ...options,
      headers,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.error || `Lỗi máy chủ (${resp.status})`);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // --- Core Calculation (mirrors database.py) ---
  function computeItemStatus(expectedQty, actualInput, unitPrice, isFlatSetPrice = false) {
    const price = Number(unitPrice || 0);
    const raw = actualInput === null || actualInput === undefined ? "" : String(actualInput).trim();

    if (raw === "") {
      return {
        status: "default_full",
        status_label: "Đủ (mặc định)",
        actual_num: null,
        effective_actual: expectedQty,
        missing_qty: 0,
        surplus_qty: 0,
        compensation: 0,
        exceeds_expected: false,
      };
    }

    const parsed = Number(raw.replace(",", "."));
    if (!Number.isFinite(parsed)) {
      return {
        status: "invalid",
        status_label: "Nhập sai số",
        actual_num: null,
        effective_actual: expectedQty,
        missing_qty: 0,
        surplus_qty: 0,
        compensation: 0,
        exceeds_expected: false,
      };
    }

    if (parsed < 0) {
      let missing = Math.round(Math.abs(parsed) * 10000) / 10000;
      if (expectedQty !== null && expectedQty !== undefined && missing > expectedQty) {
        missing = expectedQty;
      }
      const eff = expectedQty !== null && expectedQty !== undefined
        ? Math.round((expectedQty - missing) * 10000) / 10000
        : null;
      return {
        status: missing > 0 ? "shortage" : "sufficient",
        status_label: missing > 0 ? `Thiếu ${formatQty(missing)}` : "Đủ",
        actual_num: parsed,
        effective_actual: eff,
        missing_qty: missing,
        surplus_qty: 0,
        compensation:
          isFlatSetPrice && missing > 0
            ? Math.round(price * 100) / 100
            : Math.round(missing * price * 100) / 100,
        exceeds_expected: false,
      };
    }

    if (expectedQty === null || expectedQty === undefined) {
      return {
        status: "sufficient",
        status_label: "Đủ",
        actual_num: parsed,
        effective_actual: parsed,
        missing_qty: 0,
        surplus_qty: 0,
        compensation: 0,
        exceeds_expected: false,
      };
    }

    if (parsed < expectedQty) {
      const missing = Math.round((expectedQty - parsed) * 10000) / 10000;
      return {
        status: "shortage",
        status_label: `Thiếu ${formatQty(missing)}`,
        actual_num: parsed,
        effective_actual: parsed,
        missing_qty: missing,
        surplus_qty: 0,
        compensation: isFlatSetPrice
          ? Math.round(price * 100) / 100
          : Math.round(missing * price * 100) / 100,
        exceeds_expected: false,
      };
    } else if (parsed > expectedQty) {
      const surplus = Math.round((parsed - expectedQty) * 10000) / 10000;
      return {
        status: "surplus",
        status_label: `Dư +${formatQty(surplus)}`,
        actual_num: parsed,
        effective_actual: parsed,
        missing_qty: 0,
        surplus_qty: surplus,
        compensation: 0,
        exceeds_expected: false,
      };
    }

    return {
      status: "sufficient",
      status_label: "Đủ",
      actual_num: parsed,
      effective_actual: parsed,
      missing_qty: 0,
      surplus_qty: 0,
      compensation: 0,
      exceeds_expected: false,
    };
  }

  function applyPairGroupBilling(items) {
    const pairGroups = new Map();
    for (const it of items) {
      const calcRaw = computeItemStatus(it.expected_qty, it.actual_input, it.unit_price, false);
      const calcBill = computeItemStatus(
        it.expected_qty,
        it.actual_input,
        it.unit_price,
        Boolean(it.is_flat_set_price)
      );
      Object.assign(it, calcBill);
      const isShort = it.status === "shortage" && it.missing_qty > 0;
      it.raw_compensation = calcRaw.compensation;
      it.pair_charge_role = "none";
      it.pair_charge_qty = isShort ? it.missing_qty : 0;
      it.pair_group_missing_qty = isShort ? it.missing_qty : 0;
      it.pair_primary_name = "";
      it.pair_billing_note = "";
      if (isShort && it.is_flat_set_price) {
        it.pair_charge_role = "flat_set";
        it.pair_charge_qty = 1;
        it.pair_group_missing_qty = 1;
        const unitStr = it.unit || "Cái";
        it.pair_billing_note =
          it.expected_qty !== null && it.expected_qty !== undefined
            ? `Tiền cả 1 bộ Card (${formatQty(it.expected_qty)} ${unitStr}): thiếu ${formatQty(it.missing_qty)} hay toàn bộ chỉ tính ×1`
            : `Tiền cả 1 bộ Card: thiếu ${formatQty(it.missing_qty)} hay toàn bộ chỉ tính ×1`;
      }
      if (it.pair_group) {
        if (!pairGroups.has(it.pair_group)) pairGroups.set(it.pair_group, []);
        pairGroups.get(it.pair_group).push(it);
      }
    }

    for (const members of pairGroups.values()) {
      if (members.length < 2) continue;
      const shortages = members.filter((m) => m.status === "shortage" && m.missing_qty > 0);
      if (shortages.length === 0) continue;

      const groupMissingSets = Math.max(...shortages.map((m) => m.missing_qty));
      const getItemRowIdx = (item) => {
        const val = item.row_num ?? item.stt ?? item.item_id ?? 0;
        const num = Number(val);
        return isNaN(num) ? 0 : num;
      };

      let primary = shortages[0];
      for (const m of shortages) {
        if (
          m.missing_qty > primary.missing_qty ||
          (m.missing_qty === primary.missing_qty && getItemRowIdx(m) < getItemRowIdx(primary))
        ) {
          primary = m;
        }
      }

      const setPrice = Number(primary.unit_price ?? members[0].unit_price ?? 0);
      const groupComp = Math.round(groupMissingSets * setPrice * 100) / 100;
      const allNames = members.map((m) => m.name).join(" + ");

      if (shortages.length === 1) {
        const solo = shortages[0];
        solo.compensation = groupComp;
        solo.pair_charge_qty = groupMissingSets;
        solo.pair_group_missing_qty = groupMissingSets;
        solo.pair_charge_role = "solo_in_pair";
        solo.pair_billing_note = `Theo bộ (${allNames}): mất ${formatQty(solo.missing_qty)} chi tiết tính bù ${formatQty(groupMissingSets)} bộ`;
        for (const m of members) {
          if (m !== solo) {
            m.pair_charge_role = "partner_shortage";
            m.pair_group_missing_qty = groupMissingSets;
            m.pair_primary_name = solo.name;
            m.pair_billing_note = `Cùng bộ với '${solo.name}' (đã tính ${formatQty(groupMissingSets)} bộ ở dòng kia)`;
          }
        }
      } else {
        const missDesc = shortages.map((m) => `${m.name} (-${formatQty(m.missing_qty)})`).join(" + ");
        const unitPhrase = groupMissingSets === 1 ? "1 bộ" : `${formatQty(groupMissingSets)} bộ`;
        for (const m of members) {
          m.pair_group_missing_qty = groupMissingSets;
          m.pair_primary_name = primary.name;
          if (m === primary) {
            m.compensation = groupComp;
            m.pair_charge_qty = groupMissingSets;
            m.pair_charge_role = "primary";
            m.pair_billing_note = `Tính trọn ${unitPhrase} (${missDesc})`;
          } else if (m.status === "shortage" && m.missing_qty > 0) {
            m.compensation = 0;
            m.pair_charge_qty = 0;
            m.pair_charge_role = "included_in_pair";
            m.pair_billing_note = `Đã tính chung ${unitPhrase} với '${primary.name}' (0 đ)`;
          } else {
            m.pair_charge_role = "partner_shortage";
            m.pair_billing_note = `Cùng bộ đã tính chung với '${primary.name}'`;
          }
        }
      }
    }
  }

  function formatPairNoteHtml(it) {
    if (it.pair_billing_note) {
      const cls =
        it.pair_charge_role === "included_in_pair"
          ? "pair-billing-note pair-billing-note-included"
          : it.pair_charge_role === "flat_set"
          ? "pair-billing-note pair-billing-note-card"
          : "pair-billing-note";
      return `<div class="${cls}">${escapeHtml(it.pair_billing_note)}</div>`;
    }
    if (it.is_flat_set_price) {
      return `<div class="pair-tag-link pair-tag-link-card">Tiền cả 1 bộ Card: thiếu 1 hay toàn bộ số lượng cũng chỉ tính ×1</div>`;
    }
    if (it.is_paired_price) {
      const partners = (it.pair_members || []).filter((n) => n !== it.name);
      if (partners.length > 0) {
        return `<div class="pair-tag-link">Đi chung bộ với: <strong>${escapeHtml(partners.join(" + "))}</strong></div>`;
      }
    }
    return "";
  }

  function formatCompCellHtml(it) {
    if (it.pair_charge_role === "included_in_pair") {
      return `<strong class="muted">0 ₫</strong><div class="pair-included-badge">Tính chung bộ</div>`;
    }
    if (it.pair_charge_role === "flat_set") {
      return `<strong class="text-red">${formatMoney(it.compensation)}</strong><div class="pair-primary-badge pair-card-badge">Cả bộ Card (×1)</div>`;
    }
    if (it.pair_charge_role === "primary") {
      return `<strong class="text-red">${formatMoney(it.compensation)}</strong><div class="pair-primary-badge">Trọn ${escapeHtml(
        formatQty(it.pair_charge_qty)
      )} bộ</div>`;
    }
    if (it.pair_charge_role === "solo_in_pair") {
      return `<strong class="text-red">${formatMoney(it.compensation)}</strong><div class="pair-primary-badge">Tính theo bộ</div>`;
    }
    return `<strong class="${it.compensation > 0 ? "text-red" : "muted"}">${
      it.compensation > 0 ? formatMoney(it.compensation) : "0 ₫"
    }</strong>`;
  }

  // --- Navigation ---
  async function switchView(viewName, { viaNav = false, silent = false, skipReportReload = false } = {}) {
    const prevView = state.view;

    if (state.completedEditPending && !["inspect", "report"].includes(viewName)) {
      confirmUnsavedCompletedEdit(() => {
        switchView(viewName, { viaNav, silent, skipReportReload });
      });
      return;
    }

    if (prevView === "inspect" && viewName !== "inspect") {
      if (state.activeRental?.status !== "completed") {
        await flushPendingEntries();
        setSaveIndicator("hidden");
      }
    }
    state.view = viewName;
    document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
    $(`view-${viewName}`).classList.add("active");
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === viewName);
    });

    if (viewName === "checkin") {
      loadRentals()
        .then(() => {
          if (state.view === "checkin") refreshKitCodeConflicts(false);
        })
        .catch(() => {});
    } else if (viewName === "teams") {
      await loadRentals();
    } else if (viewName === "inspect") {
      renderInspectionWorkspace();
    } else if (viewName === "report") {
      if (state.activeRental && !skipReportReload) {
        if (state.activeRental.status === "completed" && state.completedEditPending) {
          renderReport();
        } else {
          await openReportForRental(state.activeRental.id, false);
        }
      } else {
        renderReport();
      }
    }

    updateTopbarStatusUI();

    if (!silent && prevView !== viewName) {
      if (prevView === "checkin" && viewName !== "checkin" && hasCheckinDraft()) {
        showToast("Đang giữ thông tin tiếp nhận chưa gửi ở mục Tiếp nhận trả hàng.", "info");
      }
      if (
        prevView === "inspect" &&
        !["inspect", "report"].includes(viewName) &&
        state.activeRental &&
        state.activeRental.status !== "completed"
      ) {
        showToast(
          `Đã lưu tạm phiếu ${state.activeRental.code} (${state.activeRental.team_name}) — ${getStatusLabel(
            state.activeRental.status
          )} (chưa khoá).`,
          "info"
        );
      }
      if (viewName === "inspect" && viaNav) {
        if (!state.activeRental) {
          showToast("Chưa chọn đội để kiểm kê — hãy chọn một phiếu trong Danh sách đội thuê.", "warning");
        } else {
          const sum = state.activeRental.summary || {};
          showToast(
            `Đang mở phiếu ${state.activeRental.code} (${state.activeRental.team_name}) — ${getStatusLabel(
              state.activeRental.status
            )} (${sum.checked_count || 0}/${sum.total_items || 0} mục).`,
            "info"
          );
        }
      } else if (viewName === "report") {
        if (!state.activeRental && viaNav) {
          showToast("Chưa chọn phiếu để xem báo cáo — hãy chọn trong Danh sách đội thuê.", "warning");
        } else if (state.activeRental?.status === "pending") {
          showToast(
            `Cảnh báo: Phiếu ${state.activeRental.code} đang CHỜ KIỂM KÊ (chưa nhập SL mục nào — mặc định đủ 100%).`,
            "warning"
          );
        } else if (state.activeRental && viaNav) {
          showToast(
            `Đang xem báo cáo phiếu ${state.activeRental.code} (${state.activeRental.team_name}) — ${getStatusLabel(
              state.activeRental.status
            )}.`,
            state.activeRental.status === "completed" ? "success" : "info"
          );
        }
      }
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // --- Data Loaders ---
  async function loadCatalog(reload = false) {
    const data = reload
      ? (await api("/api/catalog/reload", { method: "POST" })).catalog
      : await api("/api/catalog");
    state.catalog = data;
    state.kitsById = {};
    for (const k of data.kits || []) {
      state.kitsById[k.id] = k;
    }
    renderKitSelector();
  }

  async function loadRentals() {
    const data = await api("/api/rentals");
    state.rentals = data.rentals || [];
    $("teams-count-badge").textContent = String(state.rentals.length);
    if (state.activeRental && !state.rentals.some((r) => r.id === state.activeRental.id)) {
      state.activeRental = null;
      state.dirtyEntries.clear();
    }
    updateTopbarStatusUI();
    renderTeamsList();
  }

  // --- VIEW 1: Checkin / Edit Team ---
  function buildExistingKitCodeMap() {
    const map = new Map();
    for (const r of state.rentals || []) {
      for (const k of r.kits || []) {
        const clean = String(k.kit_code || "").trim();
        if (!clean) continue;
        const key = `${k.kit_id}::${clean.toLowerCase()}`;
        if (!map.has(key)) {
          map.set(key, {
            rental_id: r.id,
            ticket_code: r.code,
            team_name: r.team_name,
            returner_name: r.returner_name,
            kit_name: k.kit_name,
            kit_code: clean,
          });
        }
      }
    }
    return map;
  }

  function analyzeKitCodeConflicts() {
    const existingMap = buildExistingKitCodeMap();
    const selfCountByKey = new Map();
    for (const [kit_id, instances] of Object.entries(state.selectedKitInstances)) {
      for (const inst of instances) {
        const clean = String(inst.kit_code || "").trim();
        if (!clean) continue;
        const key = `${kit_id}::${clean.toLowerCase()}`;
        selfCountByKey.set(key, (selfCountByKey.get(key) || 0) + 1);
      }
    }

    const perInstanceError = new Map();
    let firstConflict = null;

    for (const [kit_id, instances] of Object.entries(state.selectedKitInstances)) {
      const kitName = state.kitsById[kit_id]?.name || kit_id;
      instances.forEach((inst, idx) => {
        const clean = String(inst.kit_code || "").trim();
        if (!clean) return;
        const key = `${kit_id}::${clean.toLowerCase()}`;
        const instKey = `${kit_id}::${idx}`;

        if ((selfCountByKey.get(key) || 0) > 1) {
          const hint = `Trùng mã "${clean}" trên cùng bộ ${kitName}`;
          const fullMessage = `Cùng một bộ "${kitName}" không thể có 2 mã giống nhau ("${clean}"). Khác tên bộ (vd: ALPHA A và ALPHA B) mới được trùng mã.`;
          perInstanceError.set(instKey, hint);
          if (!firstConflict) firstConflict = { kit_id, idx, hint, fullMessage };
        } else if (existingMap.has(key)) {
          const c = existingMap.get(key);
          const hint = `Đã tồn tại ở phiếu ${c.ticket_code} (Đội: ${c.team_name})`;
          const fullMessage = `Bộ "${kitName}" có mã "${clean}" đã tồn tại ở phiếu ${c.ticket_code} (Đội: ${c.team_name} · Người trả: ${c.returner_name}). Cùng một tên bộ không thể trùng mã!`;
          perInstanceError.set(instKey, hint);
          if (!firstConflict) firstConflict = { kit_id, idx, hint, fullMessage };
        }
      });
    }

    return {
      hasConflict: perInstanceError.size > 0,
      firstConflict,
      perInstanceError,
    };
  }

  function refreshKitCodeConflicts(markEmpty = false) {
    const result = analyzeKitCodeConflicts();
    const grid = $("kit-selector-grid");
    if (!grid) return result;

    let hasVisibleErr = false;
    grid.querySelectorAll(".kit-code-item").forEach((itemEl) => {
      const inp = itemEl.querySelector(".kit-code-input");
      const hintEl = itemEl.querySelector(".kit-code-error-hint");
      if (!inp) return;
      const kitId = inp.dataset.kitId;
      const idx = Number(inp.dataset.instIdx);
      const conflictHint = result.perInstanceError.get(`${kitId}::${idx}`);
      const isBlank = !inp.value.trim();

      if (conflictHint) {
        inp.classList.add("input-error");
        if (hintEl) {
          hintEl.textContent = conflictHint;
          hintEl.hidden = false;
        }
        hasVisibleErr = true;
      } else if (isBlank && markEmpty) {
        inp.classList.add("input-error");
        if (hintEl) {
          hintEl.textContent = "Bắt buộc nhập mã bộ";
          hintEl.hidden = false;
        }
        hasVisibleErr = true;
      } else {
        inp.classList.remove("input-error");
        if (hintEl) {
          hintEl.textContent = "";
          hintEl.hidden = true;
        }
      }
    });

    const errEl = $("checkin-validation-msg");
    if (errEl) {
      if (result.firstConflict && (errEl.textContent.includes("mã") || markEmpty || result.hasConflict)) {
        errEl.textContent = result.firstConflict.fullMessage;
      } else if (!hasVisibleErr && (errEl.textContent.includes("mã") || errEl.textContent.includes("phiếu"))) {
        errEl.textContent = "";
      }
    }

    return result;
  }

  function renderKitSelector() {
    const grid = $("kit-selector-grid");
    if (!grid) return;
    grid.innerHTML = "";

    for (const kit of state.catalog.kits || []) {
      const instances = state.selectedKitInstances[kit.id] || [];
      const selectedCount = instances.length;
      const isSelected = selectedCount > 0;

      const card = document.createElement("div");
      card.className = `kit-card ${isSelected ? "selected" : ""}`;
      card.dataset.kitId = kit.id;

      const piecesText = kit.total_pieces > 0
        ? `${kit.total_items} loại LK · ${kit.total_pieces} chi tiết`
        : `${kit.total_items} loại linh kiện`;

      const codesHtml = isSelected
        ? `<div class="kit-codes-list" onclick="event.stopPropagation()">
            ${instances
              .map(
                (inst, idx) => `
                  <div class="kit-code-item">
                    <div class="kit-code-row">
                      <span class="kit-code-badge">${selectedCount > 1 ? `Bộ #${idx + 1}` : "Mã bộ"} <span class="req">*</span></span>
                      <input type="text" class="kit-code-input" required data-kit-id="${escapeHtml(kit.id)}" data-inst-idx="${idx}" value="${escapeHtml(
                  inst.kit_code || ""
                )}" placeholder="${
                  selectedCount > 1 ? `Bắt buộc nhập mã bộ #${idx + 1}` : "Bắt buộc nhập mã bộ (vd: A-01)"
                }" autocomplete="off" />
                    </div>
                    <div class="kit-code-error-hint" hidden></div>
                  </div>
                `
              )
              .join("")}
          </div>`
        : "";

      card.innerHTML = `
        <div class="kit-card-top">
          ${
            kit.preview_image
              ? `<img class="kit-thumb" src="${escapeHtml(kit.preview_image)}" alt="${escapeHtml(kit.name)}" />`
              : `<div class="kit-thumb no-img-box">Robot</div>`
          }
          <div class="kit-info">
            <h4>${escapeHtml(kit.name)}</h4>
            <p>${escapeHtml(piecesText)}</p>
          </div>
          <input type="checkbox" class="kit-check" ${isSelected ? "checked" : ""} aria-label="Chọn ${escapeHtml(kit.name)}" />
        </div>
        <div class="kit-card-bottom">
          <span class="muted">${escapeHtml(kit.subtitle)}</span>
          ${
            isSelected
              ? `<div class="stepper" onclick="event.stopPropagation()">
                  <button type="button" class="step-dec" title="Giảm">−</button>
                  <span>${selectedCount} bộ</span>
                  <button type="button" class="step-inc" title="Tăng">+</button>
                </div>`
              : `<span class="text-sm muted">Nhấn để chọn</span>`
          }
        </div>
        ${codesHtml}
      `;

      card.addEventListener("click", (ev) => {
        if (ev.target.closest(".stepper, .kit-codes-list, .kit-check")) return;
        if (state.selectedKitInstances[kit.id]?.length > 0) {
          const inputs = Array.from(card.querySelectorAll(".kit-code-input"));
          const target = inputs.find((inp) => !inp.value.trim()) || inputs[0];
          if (target) target.focus();
          return;
        }
        state.selectedKitInstances[kit.id] = [{ rental_kit_id: null, kit_code: "" }];
        renderKitSelector();
        focusKitCodeInput(kit.id, 0);
      });

      const checkEl = card.querySelector(".kit-check");
      if (checkEl) {
        checkEl.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const currentList = state.selectedKitInstances[kit.id] || [];
          if (currentList.length === 0) {
            state.selectedKitInstances[kit.id] = [{ rental_kit_id: null, kit_code: "" }];
            renderKitSelector();
            focusKitCodeInput(kit.id, 0);
            return;
          }
          const enteredCodes = currentList.map((i) => String(i.kit_code || "").trim()).filter(Boolean);
          if (enteredCodes.length === 0) {
            delete state.selectedKitInstances[kit.id];
            renderKitSelector();
            return;
          }
          ev.preventDefault();
          checkEl.checked = true;
          openStep1Confirm({
            title: `Bỏ chọn bộ ${kit.name}?`,
            message: `Bộ "${kit.name}" đang có ${enteredCodes.length} mã bộ đã nhập (${enteredCodes.join(
              ", "
            )}). Bạn có chắc muốn bỏ chọn và xoá các mã này khỏi phiếu tiếp nhận không?`,
            okLabel: "Bỏ chọn bộ này",
            onConfirm: () => {
              delete state.selectedKitInstances[kit.id];
              renderKitSelector();
            },
          });
        });
      }

      const decBtn = card.querySelector(".step-dec");
      const incBtn = card.querySelector(".step-inc");
      if (decBtn) {
        decBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          const list = state.selectedKitInstances[kit.id] || [];
          if (list.length === 0) return;
          const lastInst = list[list.length - 1];
          const enteredCode = String(lastInst?.kit_code || "").trim();
          const applyDecrease = () => {
            const cur = state.selectedKitInstances[kit.id] || [];
            if (cur.length > 1) {
              cur.pop();
            } else {
              delete state.selectedKitInstances[kit.id];
            }
            renderKitSelector();
          };
          if (!enteredCode) {
            applyDecrease();
            return;
          }
          const msg =
            list.length > 1
              ? `Bộ #${list.length} (${kit.name}) đang có mã bộ "${enteredCode}". Bạn có chắc muốn xoá bộ này khỏi phiếu?`
              : `Bộ "${kit.name}" đang có mã bộ "${enteredCode}". Giảm về 0 sẽ bỏ chọn hoàn toàn bộ này.`;
          openStep1Confirm({
            title: list.length > 1 ? `Xoá Bộ #${list.length} (${kit.name})?` : `Bỏ chọn bộ ${kit.name}?`,
            message: msg,
            okLabel: list.length > 1 ? "Xoá bộ này" : "Bỏ chọn bộ",
            onConfirm: applyDecrease,
          });
        });
      }
      if (incBtn) {
        incBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!state.selectedKitInstances[kit.id]) {
            state.selectedKitInstances[kit.id] = [];
          }
          state.selectedKitInstances[kit.id].push({ rental_kit_id: null, kit_code: "" });
          const newIdx = state.selectedKitInstances[kit.id].length - 1;
          renderKitSelector();
          focusKitCodeInput(kit.id, newIdx);
        });
      }

      card.querySelectorAll(".kit-code-input").forEach((inp) => {
        inp.addEventListener("click", (e) => e.stopPropagation());
        inp.addEventListener("input", (e) => {
          const idx = Number(inp.dataset.instIdx);
          if (state.selectedKitInstances[kit.id]?.[idx]) {
            state.selectedKitInstances[kit.id][idx].kit_code = e.target.value;
          }
          refreshKitCodeConflicts(false);
        });
      });

      grid.appendChild(card);
    }

    const totalSets = Object.values(state.selectedKitInstances).reduce((sum, arr) => sum + arr.length, 0);
    const totalTypes = Object.values(state.selectedKitInstances).filter((arr) => arr.length > 0).length;
    $("selected-kits-summary").textContent =
      totalSets > 0 ? `Đã chọn: ${totalSets} bộ (${totalTypes} loại)` : "Đã chọn: 0 bộ";
    refreshKitCodeConflicts(false);
    updateTopbarStatusUI();
  }

  function clearCheckinFieldErrors() {
    ["input-returner", "input-team", "input-phone", "input-email"].forEach((id) => {
      $(id)?.classList.remove("input-error");
    });
    $("kit-selector-grid")?.querySelectorAll(".kit-code-input.input-error").forEach((el) => {
      el.classList.remove("input-error");
    });
  }

  function resetCheckinForm() {
    state.selectedKitInstances = {};
    if ($("checkin-title")) $("checkin-title").textContent = "Tiếp nhận trả hàng từ CRM Excel";
    if ($("btn-checkin-submit")?.querySelector("span")) {
      $("btn-checkin-submit").querySelector("span").textContent = "Tiếp tục";
    }
    ["input-returner", "input-team", "input-phone", "input-email", "input-note"].forEach((id) => {
      const el = $(id);
      if (el) el.value = "";
    });
    if ($("checkin-validation-msg")) $("checkin-validation-msg").textContent = "";
    clearCheckinFieldErrors();
    renderKitSelector();
  }

  async function handleCheckinSubmit(e) {
    e.preventDefault();
    clearCheckinFieldErrors();
    const returner_name = $("input-returner").value.trim();
    const team_name = $("input-team").value.trim();
    const phone = $("input-phone").value.trim();
    const email = $("input-email").value.trim();
    const note = $("input-note").value.trim();
    const kits = [];
    for (const [kit_id, instances] of Object.entries(state.selectedKitInstances)) {
      for (const inst of instances) {
        kits.push({
          rental_kit_id: inst.rental_kit_id ?? null,
          kit_id,
          kit_code: (inst.kit_code || "").trim(),
        });
      }
    }

    const errEl = $("checkin-validation-msg");
    if (!returner_name) {
      $("input-returner").classList.add("input-error");
      errEl.textContent = "Vui lòng nhập tên người trả hàng.";
      $("input-returner").focus();
      return;
    }
    if (!team_name) {
      $("input-team").classList.add("input-error");
      errEl.textContent = "Vui lòng nhập tên đội / thuộc đội nào.";
      $("input-team").focus();
      return;
    }
    if (!phone) {
      $("input-phone").classList.add("input-error");
      errEl.textContent = "Vui lòng nhập số điện thoại liên hệ.";
      $("input-phone").focus();
      return;
    }
    if (email && !isValidEmail(email)) {
      $("input-email").classList.add("input-error");
      errEl.textContent = "Địa chỉ Gmail/Email không đúng định dạng (ví dụ: ten@gmail.com).";
      $("input-email").focus();
      return;
    }
    if (kits.length === 0) {
      errEl.textContent = "Vui lòng chọn ít nhất 1 bộ robot đã thuê.";
      showToast("Vui lòng chọn ít nhất 1 bộ robot đã thuê.", "error");
      return;
    }

    const codeInputs = Array.from($("kit-selector-grid").querySelectorAll(".kit-code-input"));
    const emptyInputs = codeInputs.filter((inp) => !inp.value.trim());
    if (emptyInputs.length > 0 || kits.some((k) => !k.kit_code)) {
      refreshKitCodeConflicts(true);
      const msg =
        emptyInputs.length > 1
          ? `Vui lòng nhập mã cho tất cả các bộ robot đã chọn (còn ${emptyInputs.length} bộ đang để trống mã).`
          : "Vui lòng nhập mã cho từng bộ robot đã chọn trước khi tiếp tục.";
      errEl.textContent = msg;
      showToast(msg, "error");
      if (emptyInputs[0]) {
        emptyInputs[0].scrollIntoView({ behavior: "smooth", block: "center" });
        emptyInputs[0].focus();
      }
      return;
    }

    try {
      await loadRentals();
    } catch {
      // Fall back to cached rentals + authoritative backend check
    }
    const conflictInfo = refreshKitCodeConflicts(false);
    if (conflictInfo.hasConflict && conflictInfo.firstConflict) {
      const c = conflictInfo.firstConflict;
      errEl.textContent = c.fullMessage;
      showToast(c.fullMessage, "error");
      const targetInp = $("kit-selector-grid").querySelector(
        `.kit-code-input[data-kit-id="${c.kit_id}"][data-inst-idx="${c.idx}"]`
      );
      if (targetInp) {
        targetInp.scrollIntoView({ behavior: "smooth", block: "center" });
        targetInp.focus();
      }
      return;
    }
    errEl.textContent = "";

    const submitBtn = $("btn-checkin-submit");
    submitBtn.disabled = true;
    try {
      const res = await api("/api/rentals", {
        method: "POST",
        body: JSON.stringify({ returner_name, team_name, phone, email, note, kits }),
      });
      state.lastCreatedId = res.rental.id;
      state.activeRental = null;
      state.dirtyEntries.clear();
      showToast(`Đã tiếp nhận đội "${team_name}" vào danh sách kiểm kê!`, "success");
      resetCheckinForm();
      await switchView("teams", { silent: true });
    } catch (err) {
      if (err.data?.conflict) {
        await loadRentals().catch(() => {});
        refreshKitCodeConflicts(false);
      }
      errEl.textContent = err.message;
      showToast(err.message, "error");
    } finally {
      submitBtn.disabled = false;
    }
  }

  // --- CRM Import Functions ---
  async function loadCrmPreview(file = null, useSample = false) {
    const btnBrowse = $("btn-browse-crm-file");
    const dropZone = $("crm-drop-zone");

    if (btnBrowse) btnBrowse.disabled = true;
    showToast("Đang đọc và phân tích dữ liệu đơn hàng CRM...", "info");

    try {
      let res;
      if (useSample) {
        res = await fetch("/api/crm/preview?use_sample=1", { method: "POST" });
      } else if (file) {
        const formData = new FormData();
        formData.append("file", file);
        res = await fetch("/api/crm/preview", { method: "POST", body: formData });
      } else {
        throw new Error("Không có file để phân tích");
      }

      const json = await res.json();
      if (!res.ok || !json.success) {
        throw new Error(json.error || "Lỗi khi đọc file Excel CRM");
      }

      state.crmPreviewData = json.data;
      if (dropZone) dropZone.classList.remove("drag-over");

      const nameEl = $("crm-file-chosen-name");
      if (nameEl) {
        if (file) {
          nameEl.textContent = `File: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        } else {
          nameEl.textContent = `File mẫu: CRM_Saleorder_12.11.2026_13.57.52_512.xlsx (100 đơn hàng)`;
        }
        nameEl.hidden = false;
      }

      const data = json.data;
      if ($("crm-kpi-total")) $("crm-kpi-total").textContent = String(data.total_orders);
      if ($("crm-kpi-new")) $("crm-kpi-new").textContent = String(data.new_count);
      if ($("crm-kpi-dup")) $("crm-kpi-dup").textContent = String(data.duplicate_count);
      if ($("crm-kpi-nokits")) $("crm-kpi-nokits").textContent = String(data.loose_count || data.no_kits_count || 0);

      const updCount = data.update_return_count || 0;
      const updateCard = $("crm-kpi-card-update");
      const updateFilterBtn = $("crm-filter-update");
      if (updateCard) {
        updateCard.hidden = updCount === 0;
        if ($("crm-kpi-update")) $("crm-kpi-update").textContent = String(updCount);
      }
      if (updateFilterBtn) {
        updateFilterBtn.hidden = updCount === 0;
        updateFilterBtn.textContent = `Cập nhật Đã trả (${updCount})`;
      }

      const totalActionable = data.new_count + updCount;
      const confirmBtn = $("btn-crm-confirm-import");
      const confirmText = $("crm-confirm-import-text");
      if (confirmBtn && confirmText) {
        if (totalActionable > 0) {
          confirmBtn.disabled = false;
          let label = `Tiến hành nạp (${data.new_count} đơn mới`;
          if (updCount > 0) label += ` + cập nhật ${updCount} đơn sang Đã trả`;
          label += `) vào hệ thống`;
          confirmText.textContent = label;
        } else {
          confirmBtn.disabled = true;
          confirmText.textContent = `Không có đơn mới hay cập nhật (tất cả ${data.duplicate_count} đơn đã có trong hệ thống)`;
        }
      }

      if ($("crm-preview-container")) $("crm-preview-container").hidden = false;
      renderCrmPreviewTable();

      if (totalActionable > 0) {
        let msg = `Tìm thấy ${data.new_count} đơn mới`;
        if (updCount > 0) msg += ` và ${updCount} đơn được cập nhật trạng thái "Đã trả" theo CRM mới`;
        showToast(msg + "!", "success");
      } else {
        showToast(
          `Tất cả ${data.duplicate_count} đơn trong file đều đã có trong hệ thống kiểm kê và không có thay đổi trạng thái.`,
          "info"
        );
      }
    } catch (err) {
      showToast(err.message || "Lỗi đọc file CRM", "error");
    } finally {
      if (btnBrowse) btnBrowse.disabled = false;
    }
  }

  function formatGroupedKits(kits) {
    if (!kits || kits.length === 0) return '<span class="muted text-sm">—</span>';

    const groups = new Map();
    for (const k of kits) {
      // Clean kit name: remove any existing '(Bộ X/Y)' if present in kit_name
      let baseName = (k.kit_name || k.display_name || k.display_label || "Bộ linh kiện")
        .replace(/\s*\(Bộ\s*\d+\/\d+\)/gi, "")
        .trim();

      const key = k.kit_id || baseName;
      if (!groups.has(key)) {
        groups.set(key, {
          name: baseName,
          count: 0,
          missingCount: 0,
          compensation: 0,
          kitCodes: [],
        });
      }
      const g = groups.get(key);
      g.count += 1;
      g.missingCount += Number(k.missing_items_count || 0);
      g.compensation += Number(k.compensation || 0);
      if (k.kit_code && String(k.kit_code).trim()) {
        g.kitCodes.push(String(k.kit_code).trim());
      }
    }

    return Array.from(groups.values())
      .map((g) => {
        const missText = g.missingCount > 0 ? ` · Thiếu ${g.missingCount}` : "";
        const isWarn = g.missingCount > 0;
        let tooltip = `${g.name} (Số lượng: ${g.count} bộ)`;
        if (g.missingCount > 0) {
          tooltip += ` — Thiếu ${g.missingCount} món (${formatMoney(g.compensation)})`;
        }
        if (g.kitCodes.length > 0) {
          tooltip += ` · Mã bộ: ${g.kitCodes.join(", ")}`;
        }
        return `<span class="kit-tag ${isWarn ? "kit-tag-warn" : ""}" title="${escapeHtml(
          tooltip
        )}">${escapeHtml(g.name)} <strong class="kit-qty-badge">(SL: ${g.count})</strong>${escapeHtml(missText)}</span>`;
      })
      .join("");
  }

  function renderCrmPreviewTable() {
    const tbody = $("crm-preview-tbody");
    if (!tbody || !state.crmPreviewData) return;

    const q = normalizeText(state.crmSearch.trim());
    const f = state.crmFilter;

    const filtered = (state.crmPreviewData.orders || []).filter((o) => {
      if (f === "new" && o.status !== "new") return false;
      if (f === "update_return" && o.status !== "update_return") return false;
      if (f === "duplicate" && o.status !== "duplicate") return false;
      if (f === "no_kits" && !o.is_loose && o.status !== "no_kits") return false;
      if (f === "loose" && !o.is_loose) return false;

      if (q) {
        const kitsText = (o.kits || []).map((k) => `${k.kit_name} ${k.kit_code || ""}`).join(" ");
        const hay = normalizeText(
          `${o.order_no} ${o.customer || ""} ${o.team_name || ""} ${o.returner_name || ""} ${o.phone || ""} ${o.address || ""} ${kitsText}`
        );
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    tbody.innerHTML = "";
    if (filtered.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center muted" style="padding: 24px;">Không tìm thấy đơn hàng nào phù hợp với bộ lọc.</td></tr>`;
      return;
    }

    for (const o of filtered) {
      const tr = document.createElement("tr");

      let badgeHtml = "";
      if (o.status === "update_return") {
        badgeHtml = `<span class="pill-new" style="background:#dcfce7; color:#15803d; border:1px solid #bbf7d0;">Cập nhật: Đã trả</span>`;
      } else if (o.status === "new") {
        if (o.is_loose) {
          badgeHtml = `<span class="pill-new" style="background:#fef3c7; color:#92400e; border:1px solid #fde68a;">Phụ kiện lẻ (Mới)</span>`;
        } else {
          badgeHtml = `<span class="pill-new">Mới (${o.kits.length} bộ)</span>`;
        }
      } else if (o.status === "duplicate") {
        if (o.is_loose) {
          badgeHtml = `<span class="pill-dup" title="Đã tồn tại trong hệ thống (sẽ bỏ qua)">Đã tồn tại (Phụ kiện)</span>`;
        } else {
          badgeHtml = `<span class="pill-dup" title="Đã tồn tại trong hệ thống (sẽ bỏ qua)">Đã tồn tại</span>`;
        }
      } else {
        badgeHtml = `<span class="pill-nokits">Đơn rỗng</span>`;
      }

      const kitsHtml = formatGroupedKits(o.kits || []);

      const extraText = (o.extra_parts && o.extra_parts.length > 0)
        ? o.extra_parts.join(" · ")
        : "";

      let dueHtml = `<span class="muted text-sm">—</span>`;
      if (o.due_date) {
        const rawDue = String(o.due_date).trim().slice(0, 10);
        let isOvd = false;
        if (/^\d{4}-\d{2}-\d{2}$/.test(rawDue)) {
          const [y, m, d] = rawDue.split("-").map(Number);
          isOvd = new Date(y, m - 1, d, 23, 59, 59, 999).getTime() < Date.now();
        }
        if (isOvd && !o.is_already_returned) {
          dueHtml = `<div><span class="badge-due badge-overdue" style="font-size:11px;">Quá hạn (${escapeHtml(o.due_date)})</span></div>`;
        } else {
          dueHtml = `<div><span class="badge-due">Hạn: ${escapeHtml(o.due_date)}</span></div>`;
        }
      }

      tr.innerHTML = `
        <td>${badgeHtml}</td>
        <td><span class="pill-crm">${escapeHtml(o.order_no)}</span></td>
        <td>
          <strong>${escapeHtml(o.customer || o.team_name)}</strong>
          ${o.address ? `<div class="text-sm muted" style="margin-top:2px">${escapeHtml(o.address)}</div>` : ""}
        </td>
        <td>
          <div><strong>${escapeHtml(o.returner_name)}</strong></div>
          <div class="text-sm muted">${escapeHtml(o.phone)}</div>
        </td>
        <td>${dueHtml}</td>
        <td><div class="kit-tags">${kitsHtml || '<span class="muted text-sm">—</span>'}</div></td>
        <td>
          ${extraText ? `<div class="text-sm" style="color:#d97706; font-weight:500;">Phụ kiện: ${escapeHtml(extraText)}</div>` : ""}
          ${o.note && !extraText ? `<div class="text-sm muted">${escapeHtml(o.note)}</div>` : ""}
          ${!extraText && !o.note ? `<span class="muted text-sm">—</span>` : ""}
        </td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function executeCrmImport() {
    const newCount = state.crmPreviewData?.new_count || 0;
    const updCount = state.crmPreviewData?.update_return_count || 0;
    if (!state.crmPreviewData || (newCount === 0 && updCount === 0)) {
      showToast("Không có đơn hàng mới hoặc cập nhật nào để nạp.", "warning");
      return;
    }

    const btn = $("btn-crm-confirm-import");
    const originalText = $("crm-confirm-import-text")?.textContent || "";
    if (btn) btn.disabled = true;
    if ($("crm-confirm-import-text")) $("crm-confirm-import-text").textContent = "Đang nạp dữ liệu...";

    try {
      const res = await fetch("/api/crm/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          orders: state.crmPreviewData.orders,
          changed_by: "Quản trị viên (CRM Import)",
        }),
      });

      const json = await res.json();
      if (!res.ok || !json.success) {
        throw new Error(json.error || "Lỗi khi nạp dữ liệu CRM");
      }

      showToast(json.message || `Đã nạp thành công ${json.imported_count} đơn hàng mới!`, "success");

      // Reset CRM preview state
      state.crmPreviewData = null;
      if ($("crm-preview-container")) $("crm-preview-container").hidden = true;
      if ($("crm-file-chosen-name")) $("crm-file-chosen-name").hidden = true;
      if ($("crm-file-input")) $("crm-file-input").value = "";

      // Refresh rentals and switch to teams view so user sees newly imported orders immediately
      await loadRentals();
      await switchView("teams");
    } catch (err) {
      showToast(err.message || "Lỗi khi nạp đơn hàng", "error");
    } finally {
      if (btn) btn.disabled = false;
      if ($("crm-confirm-import-text")) $("crm-confirm-import-text").textContent = originalText;
    }
  }

  // --- VIEW 2: Teams List ---
  function getReturnStatusInfo(rental) {
    const rawReturn = String(rental.return_status || "Đã trả").trim();
    const isReturned = (
      rawReturn === "Đã trả" ||
      rawReturn === "Đã trả đúng hạn" ||
      rawReturn === "Đã trả toàn bộ" ||
      rawReturn === "Đã trả hết"
    );

    let dueTime = NaN;
    if (rental.due_date) {
      const rawDue = String(rental.due_date).trim().slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(rawDue)) {
        const [y, m, d] = rawDue.split("-").map(Number);
        dueTime = new Date(y, m - 1, d, 23, 59, 59, 999).getTime();
      } else {
        const parsed = new Date(rental.due_date);
        if (!isNaN(parsed.getTime())) {
          parsed.setHours(23, 59, 59, 999);
          dueTime = parsed.getTime();
        }
      }
    }

    const now = new Date();
    const hasDue = !isNaN(dueTime);
    const isPastDue = hasDue && (now.getTime() > dueTime);
    let overdueDays = 0;
    if (isPastDue) {
      const diffMs = now.getTime() - dueTime;
      overdueDays = Math.max(1, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
    }

    // Phân định 3 trạng thái trả hàng liên kết với hạn trả:
    // 1. Đã trả (Đã trả đúng hạn): Khách đã mang đồ về kho -> KHÔNG BAO GIỜ bị tính quá hạn
    // 2. Chưa trả (Còn hạn): Chưa trả nhưng còn trong thời hạn
    // 3. Chưa trả (Hết hạn): Chưa trả và đã quá hạn trả hàng
    if (isReturned) {
      return {
        key: "returned",
        label: "Đã trả",
        shortLabel: "Đã trả",
        fullLabel: "Đã trả thiết bị về kho",
        badgeClass: "badge-returned",
        isReturned: true,
        isOverdue: false,
        days: 0,
      };
    } else if (isPastDue) {
      return {
        key: "unreturned_overdue",
        label: "Chưa trả (Hết hạn)",
        shortLabel: "Chưa trả (Hết hạn)",
        fullLabel: `Chưa trả (Quá hạn ${overdueDays} ngày)`,
        badgeClass: "badge-unreturned-overdue",
        isReturned: false,
        isOverdue: true,
        days: overdueDays,
      };
    } else {
      return {
        key: "unreturned_ontime",
        label: "Chưa trả (Còn hạn)",
        shortLabel: "Chưa trả (Còn hạn)",
        fullLabel: "Chưa trả (Còn trong hạn)",
        badgeClass: "badge-unreturned-ontime",
        isReturned: false,
        isOverdue: false,
        days: 0,
      };
    }
  }

  function getOverdueInfo(rental) {
    const retInfo = getReturnStatusInfo(rental);
    return { isOverdue: retInfo.isOverdue, days: retInfo.days };
  }

  function getRentalStatusBadge(rental) {
    const sum = rental.summary || {};
    const retInfo = getReturnStatusInfo(rental);
    const retBadge = `<span class="badge-return ${retInfo.badgeClass}" title="Tình trạng thiết bị: ${escapeHtml(retInfo.fullLabel)}">${escapeHtml(retInfo.label)}</span>`;

    let inspectBadge = "";
    if (rental.status === "completed") {
      if (sum.shortage_items_count > 0) {
        inspectBadge = `<span class="status-pill status-shortage" title="Đã kiểm kê: Phát hiện thiếu linh kiện">Thiếu (${sum.shortage_items_count})</span>`;
      } else {
        inspectBadge = `<span class="status-pill status-completed" title="Đã kiểm kê: Đủ 100% linh kiện">Đủ</span>`;
      }
    } else if (rental.status === "inspecting") {
      const missText = sum.shortage_items_count > 0 ? ` · Thiếu ${sum.shortage_items_count}` : "";
      inspectBadge = `<span class="status-pill status-inspecting" title="Đang trong quá trình kiểm kê">Đang kiểm (${sum.checked_count}/${sum.total_items})${missText}</span>`;
    } else {
      inspectBadge = `<span class="status-pill status-pending" title="Chưa tiến hành kiểm kê linh kiện">Chưa kiểm</span>`;
    }

    return `
      <div class="status-cell-wrap">
        <div class="status-item"><span class="status-type-label">Trả hàng:</span>${retBadge}</div>
        <div class="status-item"><span class="status-type-label">Kiểm kê:</span>${inspectBadge}</div>
      </div>
    `;
  }

  function renderTeamsList() {
    const q = normalizeText(state.teamSearch.trim());
    const f = state.teamFilter;

    // Update KPIs from all rentals
    let activeCount = 0;
    let doneCount = 0;
    let overdueCount = 0;
    let totalCompAll = 0;
    let uninspectedReturnedCount = 0;
    for (const r of state.rentals) {
      if (r.status === "completed") doneCount++;
      else activeCount++;
      totalCompAll += Number(r.summary?.total_compensation || 0);
      const retInfo = getReturnStatusInfo(r);
      if (retInfo.isOverdue) overdueCount++;
      if (retInfo.isReturned && r.status === "pending") uninspectedReturnedCount++;
    }
    $("kpi-total-teams").textContent = String(state.rentals.length);
    $("kpi-active-teams").textContent = String(activeCount);
    $("kpi-done-teams").textContent = String(doneCount);
    $("kpi-all-comp").textContent = formatMoney(totalCompAll);
    const kpiOverdueEl = $("kpi-overdue-teams");
    if (kpiOverdueEl) kpiOverdueEl.textContent = String(overdueCount);
    const filterOverdueSpan = $("kpi-filter-overdue");
    if (filterOverdueSpan) filterOverdueSpan.textContent = String(overdueCount);
    const filterUninspectedSpan = $("kpi-filter-returned-uninspected");
    if (filterUninspectedSpan) filterUninspectedSpan.textContent = String(uninspectedReturnedCount);

    const filtered = state.rentals.filter((r) => {
      const retInfo = getReturnStatusInfo(r);
      if (f === "overdue" && !retInfo.isOverdue) return false;
      if (f === "unreturned_overdue" && retInfo.key !== "unreturned_overdue") return false;
      if (f === "unreturned_ontime" && retInfo.key !== "unreturned_ontime") return false;
      if (f === "returned" && retInfo.key !== "returned") return false;
      if (f === "returned_uninspected" && (!retInfo.isReturned || r.status !== "pending")) return false;
      if (f === "pending" && r.status !== "pending") return false;
      if (f === "inspecting" && r.status !== "inspecting") return false;
      if (f === "sufficient" && (r.status !== "completed" || (r.summary?.shortage_items_count || 0) > 0)) return false;
      if (f === "has_shortage" && (r.status !== "completed" || (r.summary?.shortage_items_count || 0) === 0)) return false;
      if (q) {
        const kitsText = (r.kits || []).map((k) => `${k.display_label} ${k.kit_code || ""}`).join(" ");
        const hay = normalizeText(
          `${r.code} ${r.crm_order_no || ""} ${r.team_name} ${r.returner_name} ${r.phone} ${r.email || ""} ${r.due_date || ""} ${r.delivery_address || ""} ${r.note || ""} ${kitsText}`
        );
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const tbody = $("teams-tbody");
    tbody.innerHTML = "";
    $("teams-empty").hidden = filtered.length > 0;

    for (const r of filtered) {
      const tr = document.createElement("tr");
      const retInfo = getReturnStatusInfo(r);
      if (retInfo.isOverdue) {
        tr.classList.add("row-overdue");
      }
      if (state.lastCreatedId === r.id) {
        tr.classList.add("highlight-row");
      }

      const kitsHtml = formatGroupedKits(r.kits || []);

      const comp = Number(r.summary?.total_compensation || 0);
      const isPending = r.status === "pending";
      const isCompleted = r.status === "completed";
      const hasShortage = Number(r.summary?.shortage_items_count || 0) > 0;
      const suppBtnHtml = (isCompleted && hasShortage)
        ? `<button class="btn btn-warning btn-sm act-supplementary" title="Khách mang trả thêm linh kiện bị thiếu">Trả bổ sung</button>`
        : "";

      let actionsHtml = "";
      if (isCompleted) {
        actionsHtml = `
          ${suppBtnHtml}
          <button class="btn btn-primary btn-sm act-report">Xem báo cáo</button>
        `;
      } else if (retInfo.isReturned) {
        actionsHtml = `
          <button class="btn btn-primary btn-sm act-inspect">Kiểm kê</button>
        `;
      } else {
        actionsHtml = `
          <button class="btn btn-success btn-sm act-receive-return" data-id="${r.id}" title="Khách mang hàng đến trả: Bấm để xác nhận đã nhận thiết bị về kho và bắt đầu kiểm kê">Nhận hàng trả</button>
        `;
      }

      const compHtml = isPending
        ? `<span class="muted" title="Chưa kiểm kê">—</span>`
        : `<strong class="${comp > 0 ? "text-red" : "muted"}">${formatMoney(comp)}</strong>`;

      const dueHtml = r.due_date
        ? retInfo.isOverdue
          ? `<div>
               <span class="badge-due badge-overdue" title="Đã quá hạn ${retInfo.days} ngày so với hạn trả hàng!">
                 Quá hạn ${retInfo.days} ngày (${escapeHtml(r.due_date)})
               </span>
             </div>`
          : `<div><span class="badge-due">Hạn: ${escapeHtml(r.due_date)}</span></div>`
        : `<span class="muted text-sm">—</span>`;

      let teamSubHtml = "";
      if (r.is_edited) {
        teamSubHtml += `<div style="margin-top:4px;"><button type="button" class="btn-xs-pill btn-orig-info act-view-orig" data-id="${r.id}" title="Xem thông tin gốc trước khi sửa">Đã sửa (Xem gốc)</button></div>`;
      }
      if (r.has_added_items) {
        teamSubHtml += `<div style="margin-top:4px;"><button type="button" class="btn-xs-pill btn-added-items act-view-added" data-id="${r.id}" title="Xem các bộ / linh kiện được thêm vào">Đã thêm đồ (So sánh)</button></div>`;
      }

      tr.innerHTML = `
        <td class="col-code">
          <div title="${r.crm_order_no ? `CRM: ${escapeHtml(r.crm_order_no)}` : ''}"><span class="code-pill">${escapeHtml(r.code)}</span></div>
          <div class="text-sm muted" style="margin-top:3px">${escapeHtml(formatDateTime(r.created_at))}</div>
        </td>
        <td class="col-team">
          <strong style="color:var(--text); font-size:13.5px;">${escapeHtml(r.team_name)}</strong>
          ${teamSubHtml}
        </td>
        <td class="col-contact">
          <div><strong>${escapeHtml(r.returner_name)}</strong></div>
          <div class="text-sm muted">${escapeHtml(r.phone)}</div>
          ${
            r.email
              ? `<div class="text-sm muted gmail-inline" title="${escapeHtml(r.email)}">${escapeHtml(
                  r.email
                )}</div>`
              : ""
          }
        </td>
        <td class="col-due">
          ${dueHtml}
          ${
            r.delivery_address
              ? `<div class="delivery-addr" title="${escapeHtml(r.delivery_address)}">${escapeHtml(r.delivery_address)}</div>`
              : ""
          }
        </td>
        <td class="col-kits"><div class="kit-tags">${kitsHtml}</div></td>
        <td class="col-status">${getRentalStatusBadge(r)}</td>
        <td class="col-comp num">${compHtml}</td>
        <td class="col-actions actions-col">
          <div class="btn-row" style="justify-content:flex-end">${actionsHtml}</div>
        </td>
      `;

      const inspectBtn = tr.querySelector(".act-inspect");
      if (inspectBtn) {
        inspectBtn.addEventListener("click", () => openInspectForRental(r.id));
      }
      const receiveReturnBtn = tr.querySelector(".act-receive-return");
      if (receiveReturnBtn) {
        receiveReturnBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          handleReceiveReturn(r.id);
        });
      }
      const reportBtn = tr.querySelector(".act-report");
      if (reportBtn) {
        reportBtn.addEventListener("click", () => openReportForRental(r.id, true));
      }
      const suppBtn = tr.querySelector(".act-supplementary");
      if (suppBtn) {
        suppBtn.addEventListener("click", () => openSupplementaryModal(r.id));
      }
      const editBtn = tr.querySelector(".act-edit-info");
      if (editBtn) {
        editBtn.addEventListener("click", () => openEditRentalInfoModal(r.id));
      }
      const addBtn = tr.querySelector(".act-add-items");
      if (addBtn) {
        addBtn.addEventListener("click", () => openAddRentalItemsModal(r.id));
      }
      const viewOrigBtn = tr.querySelector(".act-view-orig");
      if (viewOrigBtn) {
        viewOrigBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          openViewOriginalInfoModal(r.id);
        });
      }
      const viewAddedBtn = tr.querySelector(".act-view-added");
      if (viewAddedBtn) {
        viewAddedBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          openCompareAddedKitsModal(r.id);
        });
      }

      tbody.appendChild(tr);
    }

    if (state.lastCreatedId) {
      setTimeout(() => {
        state.lastCreatedId = null;
      }, 2200);
    }
  }


  function isLooseKit(kd) {
    if (!kd) return false;
    return (
      kd.kit_id === "phu_kien_le" ||
      kd.kit_id === "phu_kien_di_kem" ||
      kd.is_loose_order === true ||
      (typeof kd.kit_name === "string" && kd.kit_name.toLowerCase().includes("phụ kiện"))
    );
  }

  async function handleReceiveReturn(rentalId) {
    const r = state.rentals.find((x) => x.id === rentalId) || state.activeRental;
    const codeName = r ? r.code : `đơn #${rentalId}`;
    if (!confirm(`Xác nhận khách đã mang bàn giao trả lại thiết bị cho đơn ${codeName}?\n\nSau khi nhận hàng, hệ thống sẽ chuyển trạng thái sang "Đã trả" và mở màn hình kiểm kê ngay.`)) {
      return;
    }
    try {
      const res = await api(`/api/rentals/${rentalId}/receive-return`, {
        method: "POST",
        body: { actor: getCurrentStaffName() },
      });
      showToast(`Đã nhận hàng trả cho đơn ${res.rental.code}! Bắt đầu kiểm kê.`, "success");
      const idx = state.rentals.findIndex((x) => x.id === rentalId);
      if (idx !== -1) {
        state.rentals[idx] = res.rental;
      }
      state.activeRental = res.rental;
      renderTeamsList();
      updateTopbarStatusUI();
      openInspectForRental(rentalId);
    } catch (err) {
      showToast(err.message || "Lỗi khi ghi nhận nhận hàng", "error");
    }
  }

  // --- VIEW 3: Inspection Workspace ---
  async function openInspectForRental(rentalId) {
    if (state.completedEditPending && state.activeRental && state.activeRental.id !== rentalId) {
      confirmUnsavedCompletedEdit(() => {
        openInspectForRental(rentalId);
      });
      return;
    }
    try {
      if (state.activeRental?.status !== "completed") {
        await flushPendingEntries();
      }
      const data = await api(`/api/rentals/${rentalId}`);
      state.activeRental = data.rental;
      state.dirtyEntries.clear();
      state.completedEditPending = false;
      state.activeKitTab =
        data.rental.kit_details.length > 0 ? data.rental.kit_details[0].rental_kit_id : "all";
      state.inspSearch = "";
      state.inspFilter = "all";
      state.inspSection = "all";
      $("insp-search").value = "";
      document.querySelectorAll("#insp-filter-pills .pill-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.filter === "all");
      });
      await switchView("inspect");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function recalculateActiveRentalStats() {
    const rental = state.activeRental;
    if (!rental) return;

    let totalItems = 0;
    let checkedCount = 0;
    let missingTypes = 0;
    let totalMissingQty = 0;
    let totalBilledQty = 0;
    let pairedDedupCount = 0;
    let flatSetCount = 0;
    let totalComp = 0;
    const shortageItems = [];

    for (const kd of rental.kit_details) {
      applyPairGroupBilling(kd.items);
      let kdMissingCount = 0;
      let kdMissingQty = 0;
      let kdComp = 0;

      for (const it of kd.items) {
        totalItems++;
        if (it.actual_input !== null && String(it.actual_input).trim() !== "") {
          checkedCount++;
        }
        if (it.status === "shortage") {
          missingTypes++;
          totalMissingQty += it.missing_qty;
          totalBilledQty += Number(it.pair_charge_qty ?? it.missing_qty);
          if (it.pair_charge_role === "included_in_pair") {
            pairedDedupCount++;
          } else if (it.pair_charge_role === "flat_set") {
            flatSetCount++;
          }
          totalComp += it.compensation;
          kdMissingCount++;
          kdMissingQty += it.missing_qty;
          kdComp += it.compensation;
          shortageItems.push(it);
        }
      }
      kd.missing_items_count = kdMissingCount;
      kd.missing_qty = Math.round(kdMissingQty * 10000) / 10000;
      kd.compensation = Math.round(kdComp * 100) / 100;
    }

    rental.shortage_items = shortageItems;
    rental.summary = {
      total_items: totalItems,
      checked_count: checkedCount,
      shortage_items_count: missingTypes,
      total_missing_qty: Math.round(totalMissingQty * 10000) / 10000,
      total_billed_qty: Math.round(totalBilledQty * 10000) / 10000,
      paired_dedup_count: pairedDedupCount,
      flat_set_count: flatSetCount,
      total_compensation: Math.round(totalComp),
      total_compensation_exact: Math.round(totalComp * 100) / 100,
    };

    // Update sticky bar & kit tab badges without re-rendering table
    $("sticky-total").textContent = String(totalItems);
    $("sticky-checked").textContent = String(checkedCount);
    $("sticky-shortage").textContent =
      pairedDedupCount > 0 || flatSetCount > 0
        ? `${missingTypes} món (${formatQty(rental.summary.total_missing_qty)} CT · tính ${formatQty(
            rental.summary.total_billed_qty
          )} bộ)`
        : `${missingTypes} món (${formatQty(rental.summary.total_missing_qty)} cái)`;
    $("sticky-comp").textContent = formatMoney(rental.summary.total_compensation);

    document.querySelectorAll("#insp-kit-tabs .kit-tab").forEach((tab) => {
      const rkAttr = tab.dataset.rkId;
      const badge = tab.querySelector(".tab-miss");
      if (!badge) return;
      const miss =
        rkAttr === "all"
          ? missingTypes
          : rental.kit_details.find((k) => String(k.rental_kit_id) === rkAttr)?.missing_items_count || 0;
      badge.textContent = `Thiếu ${miss}`;
      badge.hidden = miss === 0;
    });
  }

  function scheduleSaveTimer() {
    setSaveIndicator("saving");
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => {
      flushPendingEntries();
    }, 350);
  }

  function updateCompletedEditNoticeUI() {
    const isCompleted = state.activeRental?.status === "completed";
    const hasDirty = state.completedEditPending && state.dirtyEntries.size > 0;
    const notice = $("insp-completed-notice");
    const noticeText = $("insp-completed-notice-text");
    const actions = $("insp-completed-actions");
    if (notice) {
      notice.hidden = !isCompleted;
      if (noticeText) {
        if (hasDirty) {
          noticeText.innerHTML = `<strong>Đang có ${state.dirtyEntries.size} thay đổi chưa lưu trên phiếu đã hoàn tất</strong>. Bạn có thể bấm <strong>"Lưu chỉnh sửa"</strong> ngay tại đây hoặc bấm <strong>"Thống kê &amp; Lưu chỉnh sửa"</strong> để đối soát lại trước khi lưu.`;
        } else {
          noticeText.innerHTML = `<strong>Phiếu đã hoàn tất kiểm kê</strong> — Đang mở ở chế độ <strong>cho phép chỉnh sửa số lượng</strong>. Sau khi chỉnh xong, vui lòng bấm <strong>"Lưu chỉnh sửa"</strong> hoặc <strong>"Thống kê &amp; Báo cáo"</strong> để ghi nhận nhật ký ngày giờ.`;
        }
      }
      if (actions) {
        actions.hidden = !hasDirty;
      }
    }

    const btnGoto = $("btn-goto-report");
    const btnSticky = $("btn-sticky-report");
    const btnText = hasDirty ? "Thống kê & Lưu chỉnh sửa" : "Thống kê & Báo cáo";
    if (btnGoto) {
      const span = btnGoto.querySelector("span");
      if (span) span.textContent = btnText;
    }
    if (btnSticky) {
      const span = btnSticky.querySelector("span");
      if (span) span.textContent = btnText;
    }
  }

  function queueEntrySave(item) {
    if (!state.activeRental) return;
    const key = `${item.rental_kit_id}__${item.id}`;
    state.dirtyEntries.set(key, {
      rental_kit_id: item.rental_kit_id,
      item_id: item.id,
      kit_id: item.kit_id,
      actual_input: item.actual_input,
      custom_qty: item.custom_qty,
      item_note: item.item_note || "",
    });

    if (state.activeRental.status === "completed") {
      state.completedEditPending = true;
      setSaveIndicator("dirty-completed");
      updateCompletedEditNoticeUI();
      return;
    }

    scheduleSaveTimer();
  }

  async function flushPendingEntries() {
    clearTimeout(state.saveTimer);
    if (state.savingPromise) {
      await state.savingPromise;
    }
    if (!state.activeRental || state.dirtyEntries.size === 0) {
      return;
    }
    if (state.activeRental.status === "completed") {
      return;
    }

    const rentalId = state.activeRental.id;
    const entriesSnapshot = Array.from(state.dirtyEntries.entries());
    state.dirtyEntries.clear();

    const entriesPayload = entriesSnapshot.map(([, v]) => v);
    state.savingPromise = api(`/api/rentals/${rentalId}/entries`, {
      method: "PATCH",
      body: JSON.stringify({ entries: entriesPayload }),
    })
      .then((res) => {
        if (state.activeRental && state.activeRental.id === rentalId) {
          state.activeRental.status = res.status;
          if (res.summary) state.activeRental.summary = res.summary;
          if (res.kits) state.activeRental.kits = res.kits;
          updateTopbarStatusUI();
          const sp = $("insp-status-pill");
          if (sp) {
            sp.className = `status-pill status-${res.status}`;
            sp.textContent =
              res.status === "completed"
                ? "Đã hoàn tất"
                : res.status === "inspecting"
                ? "Đang kiểm kê"
                : "Chờ kiểm kê";
          }
        }
        setSaveIndicator("saved");
      })
      .catch((err) => {
        if (err.status !== 400 && err.status !== 404) {
          for (const [k, v] of entriesSnapshot) {
            if (!state.dirtyEntries.has(k)) {
              state.dirtyEntries.set(k, v);
            }
          }
        }
        setSaveIndicator("hidden");
        showToast("Lỗi lưu dữ liệu: " + err.message, "error");
      })
      .finally(() => {
        state.savingPromise = null;
      });

    await state.savingPromise;
  }

  function renderInspectionWorkspace() {
    const rental = state.activeRental;
    if (!rental) {
      setSaveIndicator("hidden");
      $("inspect-no-team").hidden = false;
      $("inspect-locked-box").hidden = true;
      $("inspect-workspace").hidden = true;
      return;
    }
    $("inspect-no-team").hidden = true;

    if (rental.status === "completed") {
      $("inspect-locked-box").hidden = false;
      $("inspect-workspace").hidden = true;
      $("inspect-no-team").hidden = true;
      return;
    }

    $("inspect-locked-box").hidden = true;
    $("inspect-workspace").hidden = false;

    const completedNotice = $("insp-completed-notice");
    if (completedNotice) {
      completedNotice.hidden = rental.status !== "completed";
    }

    const inspSuppBtn = $("btn-inspect-supplementary");
    if (inspSuppBtn) {
      inspSuppBtn.hidden = !(rental.status === "completed" && Number(rental.summary?.shortage_items_count || 0) > 0);
    }

    const validTab =
      (state.activeKitTab === "all" && rental.kit_details.length > 0) ||
      rental.kit_details.some((k) => k.rental_kit_id === state.activeKitTab);
    if (!validTab) {
      state.activeKitTab =
        rental.kit_details.length > 0 ? rental.kit_details[0].rental_kit_id : "all";
    }

    $("insp-code").textContent = rental.code;
    $("insp-team-name").textContent = rental.team_name;
    $("insp-returner").textContent = rental.returner_name;
    $("insp-phone").textContent = rental.phone;
    $("insp-email-wrap").hidden = !rental.email;
    $("insp-email").textContent = rental.email || "";
    $("insp-note-wrap").hidden = !rental.note;
    $("insp-note").textContent = rental.note || "";

    const retInfo = getReturnStatusInfo(rental);
    const unreturnedNotice = $("insp-unreturned-notice");
    if (unreturnedNotice) {
      unreturnedNotice.hidden = retInfo.isReturned || rental.status === "completed";
      const btnReceiveInsp = $("btn-insp-receive-return");
      if (btnReceiveInsp) {
        btnReceiveInsp.onclick = () => handleReceiveReturn(rental.id);
      }
    }

    const returnBadge = $("insp-badge-return");
    if (returnBadge) {
      returnBadge.textContent = retInfo.fullLabel || retInfo.label;
      returnBadge.className = `badge-return ${retInfo.badgeClass}`;
      returnBadge.title = `Tình trạng trả thiết bị: ${retInfo.fullLabel}`;
    }

    const btnViewOrig = $("insp-btn-view-orig");
    if (btnViewOrig) {
      btnViewOrig.hidden = !rental.is_edited;
      btnViewOrig.onclick = () => openViewOriginalInfoModal(rental.id);
    }

    const btnViewAdded = $("insp-btn-view-added");
    if (btnViewAdded) {
      btnViewAdded.hidden = !rental.has_added_items;
      btnViewAdded.onclick = () => openCompareAddedKitsModal(rental.id);
    }

    const btnEditInfo = $("btn-inspect-edit-info");
    if (btnEditInfo) {
      btnEditInfo.onclick = () => openEditRentalInfoModal(rental.id);
    }

    const btnAddItems = $("btn-inspect-add-items");
    if (btnAddItems) {
      btnAddItems.onclick = () => openAddRentalItemsModal(rental.id);
    }

    const sp = $("insp-status-pill");
    sp.className = `status-pill status-${rental.status}`;
    sp.textContent =
      rental.status === "completed"
        ? (rental.summary.shortage_items_count > 0 ? `Đã kiểm · Thiếu ${rental.summary.shortage_items_count} món` : "Đã kiểm · Đủ 100%")
        : rental.status === "inspecting"
        ? "Đang kiểm kê"
        : "Chờ kiểm kê";

    // Render Per-Instance Kit Tabs
    const tabsBox = $("insp-kit-tabs");
    tabsBox.innerHTML = "";
    if (rental.kit_details.length > 1) {
      const allBtn = document.createElement("button");
      allBtn.className = `kit-tab ${state.activeKitTab === "all" ? "active" : ""}`;
      allBtn.dataset.rkId = "all";
      allBtn.innerHTML = `<span>Tất cả (${rental.kit_details.length} bộ)</span><span class="tab-miss" ${
        rental.summary.shortage_items_count === 0 ? "hidden" : ""
      }>Thiếu ${rental.summary.shortage_items_count}</span>`;
      allBtn.addEventListener("click", () => {
        state.activeKitTab = "all";
        state.inspSection = "all";
        renderInspectionWorkspace();
      });
      tabsBox.appendChild(allBtn);
    }

    for (const kd of rental.kit_details) {
      const btn = document.createElement("button");
      btn.className = `kit-tab ${state.activeKitTab === kd.rental_kit_id ? "active" : ""}`;
      btn.dataset.rkId = String(kd.rental_kit_id);
      const addedTag = kd.is_added_after ? ' <em style="font-size:10px; color:var(--green); font-weight:700;">[Thêm]</em>' : '';
      btn.innerHTML = `<span class="tab-label">${escapeHtml(kd.display_label)}${addedTag}</span><span class="tab-miss" ${kd.missing_items_count === 0 ? "hidden" : ""}>Thiếu ${
        kd.missing_items_count
      }</span>`;
      btn.addEventListener("click", () => {
        state.activeKitTab = kd.rental_kit_id;
        state.inspSection = "all";
        renderInspectionWorkspace();
      });
      tabsBox.appendChild(btn);
    }

    // Kit Code bar when inspecting a specific kit instance (only for robot kits, loose accessories don't have kit codes)
    const codeBar = $("insp-kit-code-bar");
    const activeKd =
      state.activeKitTab === "all"
        ? null
        : rental.kit_details.find((k) => k.rental_kit_id === state.activeKitTab);
    if (activeKd && !isLooseKit(activeKd)) {
      codeBar.hidden = false;
      const instDesc =
        activeKd.same_type_total > 1
          ? `${activeKd.kit_name} — Bộ ${activeKd.instance_index}`
          : activeKd.kit_name;
      $("insp-kit-code-label").textContent = `Mã bộ (${instDesc}):`;
      const input = $("insp-kit-code-input");
      if (input) {
        input.value = activeKd.kit_code || "";
      }
    } else {
      codeBar.hidden = true;
    }

    // Section dropdown (deduplicated across identical kit types)
    const secSelect = $("insp-section-select");
    const activeKds = activeKd ? [activeKd] : rental.kit_details;
    const rawSections = activeKds.flatMap((k) =>
      (k.sections || []).map((s) => ({ ...s, kit_id: k.kit_id, kit_name: k.kit_name }))
    );
    const seenSecIds = new Set();
    const allSections = [];
    for (const sec of rawSections) {
      if (!seenSecIds.has(sec.id)) {
        seenSecIds.add(sec.id);
        allSections.push(sec);
      }
    }
    const distinctKitTypesWithSections = new Set(allSections.map((s) => s.kit_id)).size;

    if (allSections.length > 0) {
      secSelect.hidden = false;
      secSelect.innerHTML = `<option value="all">Tất cả nhóm / cụm (${allSections.length})</option>`;
      for (const sec of allSections) {
        const opt = document.createElement("option");
        opt.value = sec.id;
        opt.textContent =
          distinctKitTypesWithSections > 1 ? `${sec.kit_name} — ${sec.name}` : sec.name;
        if (state.inspSection === sec.id) opt.selected = true;
        secSelect.appendChild(opt);
      }
    } else {
      secSelect.hidden = true;
      state.inspSection = "all";
    }

    renderInspectionTable();
    recalculateActiveRentalStats();
  }

  let kitCodeSaveTimer = null;

  async function saveActiveKitCode(silent = false) {
    if (!state.activeRental || state.activeKitTab === "all") return;
    const activeKd = state.activeRental.kit_details?.find((k) => k.rental_kit_id === state.activeKitTab);
    if (!activeKd || isLooseKit(activeKd)) return;

    const input = $("insp-kit-code-input");
    const newCode = (input ? input.value : "").trim();
    if (silent && newCode === (activeKd.kit_code || "").trim()) {
      return;
    }

    const btn = $("btn-save-kit-code");
    const statusEl = $("kit-code-save-status");
    if (btn) btn.disabled = true;

    try {
      const res = await fetch(`/api/rentals/${state.activeRental.id}/kits/${activeKd.rental_kit_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kit_code: newCode, actor: "Người kiểm kê" }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Lỗi lưu mã bộ");
      }
      activeKd.kit_code = newCode;
      state.activeRental = data.rental;
      const idx = state.rentals.findIndex((r) => r.id === state.activeRental.id);
      if (idx !== -1) {
        state.rentals[idx] = data.rental;
      }

      if (statusEl) {
        statusEl.textContent = newCode ? "Đã tự động lưu" : "Đã xóa mã";
        statusEl.style.color = "#059669";
        statusEl.hidden = false;
        clearTimeout(statusEl._timer);
        statusEl._timer = setTimeout(() => {
          statusEl.hidden = true;
        }, 2200);
      }

      if (!silent) {
        showToast(newCode ? `Đã lưu mã bộ "${newCode}"!` : "Đã cập nhật mã bộ!", "success");
      }

      // Update active tab display label
      const activeTabBtn = document.querySelector(`#insp-kit-tabs .kit-tab[data-rk-id="${activeKd.rental_kit_id}"]`);
      if (activeTabBtn) {
        const lbl = activeTabBtn.querySelector(".tab-label");
        const freshKd = data.rental.kit_details.find((k) => k.rental_kit_id === activeKd.rental_kit_id);
        if (lbl && freshKd) {
          lbl.textContent = freshKd.display_label;
        }
      }
    } catch (err) {
      if (!silent) {
        showToast(err.message || "Lỗi lưu mã bộ", "error");
      } else if (statusEl) {
        statusEl.textContent = err.message;
        statusEl.style.color = "#dc2626";
        statusEl.hidden = false;
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function handleKitCodeInputChange() {
    clearTimeout(kitCodeSaveTimer);
    kitCodeSaveTimer = setTimeout(() => {
      saveActiveKitCode(true);
    }, 450);
  }

  async function proceedToReportValidation(onProceed) {
    const rental = state.activeRental;
    if (!rental) return;

    // 1. Flush any pending actual quantity inputs
    await flushPendingEntries();

    // 2. Flush kit code input if actively inspecting a single robot kit
    if (state.activeKitTab !== "all") {
      const curTab = rental.kit_details?.find((k) => k.rental_kit_id === state.activeKitTab);
      if (curTab && !isLooseKit(curTab)) {
        await saveActiveKitCode(true);
      }
    }

    if (!rental.kit_details || rental.kit_details.length === 0) {
      onProceed();
      return;
    }

    // 3. CHECK A: Every ROBOT kit MUST have a kit code (loose accessories kits are exempted)
    const missingCodeKits = rental.kit_details.filter(
      (kd) => !isLooseKit(kd) && !(kd.kit_code || "").trim()
    );

    if (missingCodeKits.length > 0) {
      $("missing-kit-code-count").textContent = String(missingCodeKits.length);
      const ul = $("missing-kit-code-list");
      ul.innerHTML = "";
      for (const kd of missingCodeKits) {
        const li = document.createElement("li");
        li.textContent = kd.display_label;
        ul.appendChild(li);
      }
      $("modal-missing-kit-code").hidden = false;

      $("btn-goto-enter-kit-code").onclick = () => {
        $("modal-missing-kit-code").hidden = true;
        // Navigate directly to the first kit that is missing a code
        state.activeKitTab = missingCodeKits[0].rental_kit_id;
        state.inspSection = "all";
        renderInspectionWorkspace();
        setTimeout(() => {
          const input = $("insp-kit-code-input");
          if (input) {
            input.focus();
            input.select();
          }
        }, 120);
      };
      return;
    }

    // 4. CHECK B: If an entire kit has 0 items entered, confirm if it's 100% complete
    // Only check during initial inspection (skip if order is already completed)
    if (rental.status !== "completed") {
      const unenteredKits = rental.kit_details.filter(
        (kd) => (kd.checked_count || 0) === 0
      );

      if (unenteredKits.length > 0) {
        $("unentered-kits-count").textContent = String(unenteredKits.length);
        const ul = $("unentered-kits-list");
        ul.innerHTML = "";
        for (const kd of unenteredKits) {
          const li = document.createElement("li");
          li.textContent = kd.display_label;
          ul.appendChild(li);
        }
        $("modal-unentered-kits").hidden = false;

        $("btn-unentered-check-again").onclick = () => {
          $("modal-unentered-kits").hidden = true;
          state.activeKitTab = unenteredKits[0].rental_kit_id;
          state.inspSection = "all";
          renderInspectionWorkspace();
        };

        $("btn-unentered-confirm-sufficient").onclick = () => {
          $("modal-unentered-kits").hidden = true;
          onProceed();
        };
        return;
      }
    }

    // All validations passed
    onProceed();
  }

  function renderInspectionTable() {
    const rental = state.activeRental;
    if (!rental) return;

    const q = normalizeText(state.inspSearch.trim());
    const f = state.inspFilter;
    const secFilter = state.inspSection;

    const kds =
      state.activeKitTab === "all"
        ? rental.kit_details
        : rental.kit_details.filter((k) => k.rental_kit_id === state.activeKitTab);

    const hasAnyCodes = kds.some((kd) =>
      (kd.items || []).some((it) => Boolean((it.code || "").trim()))
    );

    const thCode = $("th-code");
    if (thCode) {
      thCode.hidden = !hasAnyCodes;
    }

    const tbody = $("insp-tbody");
    tbody.innerHTML = "";

    const itemsByCompositeKey = new Map();
    for (const kd of kds) {
      for (const it of kd.items) {
        itemsByCompositeKey.set(`${it.rental_kit_id}__${it.id}`, it);
      }
    }

    function refreshRowCompAndPairDOM(rowEl, itemObj) {
      if (!rowEl || !itemObj) return;
      const compCell = rowEl.querySelector(".comp-cell");
      if (compCell) compCell.innerHTML = formatCompCellHtml(itemObj);
      const pairSlot = rowEl.querySelector(".pair-note-slot");
      if (pairSlot) pairSlot.innerHTML = formatPairNoteHtml(itemObj);
    }

    let visibleCount = 0;

    for (const kd of kds) {
      const secLookup = {};
      for (const s of kd.sections || []) {
        secLookup[s.id] = s;
      }

      const filteredItems = kd.items.filter((it) => {
        if (secFilter !== "all" && it.section_id !== secFilter) return false;
        if (f === "shortage" && it.status !== "shortage") return false;
        if (f === "entered" && (it.actual_input === "" || it.actual_input === null)) return false;
        if (f === "blank" && it.actual_input !== "" && it.actual_input !== null) return false;
        if (q) {
          const hay = normalizeText(
            `${it.code} ${it.name} ${it.section_name || ""} ${kd.display_label} ${kd.kit_code || ""}`
          );
          if (!hay.includes(q)) return false;
        }
        return true;
      });

      if (filteredItems.length === 0) continue;

      // Show read-only per-instance kit banner when viewing 'all'
      if (state.activeKitTab === "all" && kds.length > 1) {
        const kitRow = document.createElement("tr");
        kitRow.className = "kit-divider-row";
        const codeDisplay = !isLooseKit(kd) && kd.kit_code
          ? `<div class="inline-kit-code-wrap"><span>Mã bộ:</span><strong class="code-pill">${escapeHtml(kd.kit_code)}</strong></div>`
          : "";
        kitRow.innerHTML = `
          <td colspan="${hasAnyCodes ? 10 : 9}">
            <div class="kit-divider-inner">
              <div>
                <strong class="kit-divider-title">Bộ: ${escapeHtml(kd.display_label)}</strong>
              </div>
              ${codeDisplay}
            </div>
          </td>
        `;
        tbody.appendChild(kitRow);
      }

      let lastSectionId = "__NONE__";
      for (const it of filteredItems) {
        visibleCount++;
        if (it.section_id && it.section_id !== lastSectionId) {
          lastSectionId = it.section_id;
          const secInfo = secLookup[it.section_id];
          const secRow = document.createElement("tr");
          secRow.className = "section-divider-row";
          secRow.innerHTML = `
            <td colspan="${hasAnyCodes ? 10 : 9}">
              <div class="section-divider-inner">
                ${
                  secInfo?.image
                    ? `<img class="section-thumb" src="${escapeHtml(secInfo.image)}" alt="${escapeHtml(secInfo.name)}" />`
                    : ""
                }
                <div>
                  <strong>Cụm / Nhóm: ${escapeHtml(it.section_name)}</strong>
                  <span class="muted text-sm" style="margin-left:8px">(Nhấp vào ảnh để phóng to cụm lắp ráp)</span>
                </div>
              </div>
            </td>
          `;
          const st = secRow.querySelector(".section-thumb");
          if (st) {
            st.addEventListener("click", () => {
              openImageModal([secInfo.image], it.section_name, "Ảnh cụm linh kiện", null);
            });
          }
          tbody.appendChild(secRow);
        }

        const tr = document.createElement("tr");
        tr.dataset.rkId = String(it.rental_kit_id);
        tr.dataset.itemId = it.id;
        if (it.pair_group) tr.dataset.pairGroup = it.pair_group;
        if (it.status === "shortage") tr.classList.add("row-shortage");
        else if (it.status === "surplus") tr.classList.add("row-surplus");
        if (it.is_paired_price) {
          tr.classList.add("row-pair");
          if (it.pair_index === 0) tr.classList.add("row-pair-start");
          if (it.pair_index === Number(it.pair_size || 2) - 1) tr.classList.add("row-pair-end");
        }
        if (it.is_flat_set_price) {
          tr.classList.add("row-card-set");
        }

        const imgsHtml =
          it.images && it.images.length > 0
            ? `<div class="part-imgs">${it.images
                .map(
                  (src) =>
                    `<img class="part-thumb" loading="lazy" src="${escapeHtml(src)}" alt="${escapeHtml(it.name)}" />`
                )
                .join("")}</div>`
            : `<div class="no-img-box">Không ảnh</div>`;

        const expectedCellHtml =
          it.base_qty === null || it.base_qty === undefined
            ? `<input type="number" min="0" step="any" class="custom-qty-input" value="${
                it.custom_qty !== null && it.custom_qty !== undefined ? escapeHtml(it.custom_qty) : ""
              }" placeholder="SL thuê" title="Bộ này không cố định SL trong Excel — có thể nhập SL thuê hoặc nhập số âm ở ô SL thực" />`
            : `<strong>${formatQty(it.expected_qty)}</strong>`;

        const inputClass =
          it.status === "shortage"
            ? "input-shortage"
            : it.status === "sufficient"
            ? "input-sufficient"
            : it.status === "surplus"
            ? "input-surplus"
            : "";

        const codeCellHtml = hasAnyCodes
          ? `<td>${it.code ? `<span class="code-pill">${escapeHtml(it.code)}</span>` : `<span class="muted">-</span>`}</td>`
          : "";

        tr.innerHTML = `
          <td class="tabular muted">${escapeHtml(it.stt)}</td>
          ${codeCellHtml}
          <td>
            <div class="item-name-main">${escapeHtml(it.name)}</div>
            <div class="item-sub">
              <span class="unit-tag">${escapeHtml(it.unit)}</span>
              ${
                it.is_paired_price
                  ? `<span class="pair-tag" title="${escapeHtml(it.pair_label || "")}">Theo bộ (${formatQty(it.pair_size || 2)} chi tiết)</span>`
                  : ""
              }
              ${
                it.is_flat_set_price
                  ? `<span class="pair-tag pair-tag-card" title="Đơn giá là tiền cả 1 bộ Card: thiếu 1 hay toàn bộ số lượng card cũng chỉ tính ×1">Trọn bộ Card (×1)</span>`
                  : ""
              }
            </div>
            <div class="pair-note-slot">${formatPairNoteHtml(it)}</div>
          </td>
          <td>${imgsHtml}</td>
          <td class="num tabular">${formatMoney(it.unit_price, true)}</td>
          <td class="num tabular expected-cell">${expectedCellHtml}</td>
          <td style="text-align:center">
            <div class="actual-input-wrap">
              <input type="text" inputmode="decimal" class="actual-input ${inputClass}" value="${escapeHtml(
          it.actual_input
        )}" placeholder="" aria-label="Số lượng thực ${escapeHtml(it.name)}" />
              <button type="button" tabindex="-1" class="clear-actual-btn" title="Xoá về mặc định đủ" ${
                it.actual_input ? "" : "hidden"
              }>&times;</button>
            </div>
          </td>
          <td class="status-cell">
            <span class="status-pill status-${it.status}">${escapeHtml(it.status_label)}</span>
            ${
              it.exceeds_expected
                ? `<div class="text-sm text-amber" style="margin-top:2px">Vượt SL bộ (${formatQty(it.expected_qty)})</div>`
                : ""
            }
          </td>
          <td class="num tabular comp-cell">${formatCompCellHtml(it)}</td>
          <td class="tabular log-time-cell">
            ${
              it._pendingEdit
                ? '<span class="log-time-badge badge-pending-edit" title="Đã sửa số lượng - Hãy bấm Thống kê & Báo cáo rồi bấm Lưu chỉnh sửa">Chờ lưu...</span>'
                : it.updated_at
                ? `<span class="log-time-badge">${escapeHtml(formatLogTimestamp(it.updated_at))}</span>`
                : '<span class="muted text-sm">—</span>'
            }
          </td>
        `;

        // Image zoom handlers
        tr.querySelectorAll(".part-thumb").forEach((imgEl) => {
          imgEl.addEventListener("click", () => {
            openImageModal(it.images, it.name, it.code, it.unit_price);
          });
        });

        const actualEl = tr.querySelector(".actual-input");
        const clearBtn = tr.querySelector(".clear-actual-btn");
        const customQtyEl = tr.querySelector(".custom-qty-input");

        function updateRowUI() {
          const calc = computeItemStatus(
            it.expected_qty,
            it.actual_input,
            it.unit_price,
            Boolean(it.is_flat_set_price)
          );
          Object.assign(it, calc);

          const isCompletedRental = state.activeRental?.status === "completed";
          if (isCompletedRental) {
            it._pendingEdit = true;
            const timeCell = tr.querySelector(".log-time-cell");
            if (timeCell) {
              timeCell.innerHTML = `<span class="log-time-badge badge-pending-edit" title="Đã sửa số lượng - Hãy bấm 'Thống kê & Báo cáo' rồi bấm 'Lưu chỉnh sửa' để ghi nhận">Chờ lưu...</span>`;
            }
          } else {
            const now = new Date();
            const pad = (n) => String(n).padStart(2, "0");
            const nowStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(
              now.getHours()
            )}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
            it.updated_at = nowStr;

            const timeCell = tr.querySelector(".log-time-cell");
            if (timeCell) {
              timeCell.innerHTML = `<span class="log-time-badge just-updated">${escapeHtml(
                formatLogTimestamp(nowStr)
              )}</span>`;
            }
          }

          tr.classList.toggle("row-shortage", it.status === "shortage");
          tr.classList.toggle("row-surplus", it.status === "surplus");

          actualEl.classList.remove("input-shortage", "input-sufficient", "input-surplus");
          if (it.status === "shortage") actualEl.classList.add("input-shortage");
          else if (it.status === "sufficient") actualEl.classList.add("input-sufficient");
          else if (it.status === "surplus") actualEl.classList.add("input-surplus");

          clearBtn.hidden = !it.actual_input;

          const statusCell = tr.querySelector(".status-cell");
          statusCell.innerHTML = `
            <span class="status-pill status-${it.status}">${escapeHtml(it.status_label)}</span>
            ${
              it.exceeds_expected
                ? `<div class="text-sm text-amber" style="margin-top:2px">Vượt SL bộ (${formatQty(it.expected_qty)})</div>`
                : ""
            }
          `;

          // Reconcile paired items & rental totals BEFORE refreshing comp cells
          recalculateActiveRentalStats();
          if (it.pair_group) {
            tbody
              .querySelectorAll(`tr[data-rk-id="${CSS.escape(String(it.rental_kit_id))}"][data-pair-group="${CSS.escape(it.pair_group)}"]`)
              .forEach((sibRow) => {
                const sibKey = `${sibRow.dataset.rkId}__${sibRow.dataset.itemId}`;
                refreshRowCompAndPairDOM(sibRow, itemsByCompositeKey.get(sibKey));
              });
          } else {
            refreshRowCompAndPairDOM(tr, it);
          }
          queueEntrySave(it);
        }

        function enforceShortageLimit() {
          if (it.expected_qty === null || it.expected_qty === undefined) return;
          const raw = actualEl.value.trim();
          if (raw === "" || raw === "-") return;
          const num = Number(raw.replace(",", "."));
          if (Number.isFinite(num) && num < 0 && Math.abs(num) > it.expected_qty) {
            const clamped = it.expected_qty > 0 ? `-${it.expected_qty}` : "0";
            actualEl.value = clamped;
            it.actual_input = clamped;
            showToast(
              `Số lượng thiếu không được lớn hơn số lượng hiện có trong bộ (${formatQty(it.expected_qty)})!`,
              "error"
            );
          }
        }

        actualEl.addEventListener("input", () => {
          it.actual_input = actualEl.value.trim();
          enforceShortageLimit();
          updateRowUI();
        });

        actualEl.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === "ArrowDown") {
            ev.preventDefault();
            const inputs = Array.from(tbody.querySelectorAll(".actual-input"));
            const idx = inputs.indexOf(actualEl);
            if (idx >= 0 && idx + 1 < inputs.length) {
              inputs[idx + 1].focus();
              inputs[idx + 1].select();
            }
          } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            const inputs = Array.from(tbody.querySelectorAll(".actual-input"));
            const idx = inputs.indexOf(actualEl);
            if (idx > 0) {
              inputs[idx - 1].focus();
              inputs[idx - 1].select();
            }
          }
        });

        clearBtn.addEventListener("click", () => {
          actualEl.value = "";
          it.actual_input = "";
          updateRowUI();
          actualEl.focus();
        });

        if (customQtyEl) {
          customQtyEl.addEventListener("input", () => {
            const rawQ = customQtyEl.value.trim();
            if (rawQ === "") {
              it.custom_qty = null;
              it.expected_qty = null;
            } else {
              const numQ = Number(rawQ);
              it.custom_qty = Number.isFinite(numQ) && numQ >= 0 ? numQ : null;
              it.expected_qty = it.custom_qty;
            }
            enforceShortageLimit();
            updateRowUI();
          });
        }

        tbody.appendChild(tr);
      }
    }

    if (visibleCount === 0) {
      const emptyRow = document.createElement("tr");
      emptyRow.innerHTML = `<td colspan="${hasAnyCodes ? 10 : 9}" class="empty-state">Không có linh kiện nào khớp với bộ lọc hiện tại.</td>`;
      tbody.appendChild(emptyRow);
    }
  }

  // --- VIEW 4: Statistics & Report ---
  async function openReportForRental(rentalId, switchToView = true) {
    if (state.completedEditPending && state.activeRental && state.activeRental.id !== rentalId) {
      confirmUnsavedCompletedEdit(() => {
        openReportForRental(rentalId, switchToView);
      });
      return;
    }
    try {
      if (
        state.activeRental &&
        state.activeRental.id === rentalId &&
        state.activeRental.status === "completed" &&
        state.completedEditPending
      ) {
        recalculateActiveRentalStats();
        if (switchToView) {
          await switchView("report", { skipReportReload: true });
        } else {
          renderReport();
          updateTopbarStatusUI();
        }
        return;
      }

      await flushPendingEntries();
      const prevRentalId = state.activeRental?.id;
      const data = await api(`/api/rentals/${rentalId}`);
      state.activeRental = data.rental;
      if (prevRentalId !== data.rental.id) {
        state.activeKitTab =
          data.rental.kit_details.length > 0 ? data.rental.kit_details[0].rental_kit_id : "all";
      }
      if (switchToView) {
        await switchView("report", { skipReportReload: true });
      } else {
        renderReport();
        updateTopbarStatusUI();
      }
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function renderReport() {
    const rental = state.activeRental;
    if (!rental) {
      $("report-no-team").hidden = false;
      $("report-content").hidden = true;
      return;
    }
    $("report-no-team").hidden = true;
    $("report-content").hidden = false;

    // Recalculate stats to ensure latest in-memory edits are 100% computed
    if (rental.kit_details) {
      recalculateActiveRentalStats();
    }

    const kitsStr = (rental.kits || []).map((k) => k.display_label).join(", ");

    $("rep-code").textContent = rental.code;
    $("rep-date").textContent = formatDateTime(rental.updated_at || rental.created_at);
    $("rep-team").textContent = rental.team_name;
    $("rep-returner").textContent = rental.returner_name;
    $("rep-phone").textContent = rental.phone;
    $("rep-email").textContent = rental.email || "Chưa cung cấp";
    $("rep-kits").textContent = kitsStr;
    $("rep-sig-returner").textContent = rental.returner_name;

    $("rep-note-row").hidden = !rental.note;
    $("rep-note").textContent = rental.note || "";

    const sp = $("rep-status-pill");
    sp.className = `status-pill status-${rental.status}`;
    sp.textContent =
      rental.status === "completed"
        ? "Đã hoàn tất"
        : rental.status === "inspecting"
        ? "Đang kiểm kê"
        : "Chờ kiểm kê";

    const backInspectBtn = $("btn-report-back-inspect");
    const toggleBtn = $("btn-toggle-complete");
    if (rental.status === "completed") {
      backInspectBtn.hidden = true; // Không cho phép quay lại chỉnh sửa số lượng sau khi đã hoàn tất
      toggleBtn.textContent = "Đã hoàn tất kiểm kê";
      toggleBtn.disabled = true;
      toggleBtn.className = "btn btn-outline";
    } else {
      backInspectBtn.hidden = false;
      backInspectBtn.textContent = "Quay lại kiểm kê";
      toggleBtn.textContent = "Hoàn tất kiểm kê & Ghi nhận";
      toggleBtn.disabled = false;
      toggleBtn.className = "btn btn-success";
    }

    const shortages = rental.shortage_items || [];
    const totalComp = Number(rental.summary?.total_compensation || 0);
    const totalQty = Number(rental.summary?.total_missing_qty || 0);

    const isCompletedDirty = Boolean(rental.status === "completed" && state.completedEditPending && state.dirtyEntries.size > 0);
    const repEditBanner = $("rep-completed-edit-banner");
    const tbSaveBtn = $("btn-toolbar-save-edit");
    if (repEditBanner) {
      repEditBanner.hidden = !isCompletedDirty;
      const descEl = repEditBanner.querySelector(".pending-edit-desc");
      if (descEl && isCompletedDirty) {
        descEl.innerHTML = `Bạn vừa sửa số lượng kiểm kê (${state.dirtyEntries.size} linh kiện). Số liệu và tổng tiền bù bên dưới đã được <strong>tính lại ngay lập tức: ${formatMoney(totalComp)}</strong>. Hãy đối soát lại rồi bấm <strong>"Lưu chỉnh sửa"</strong> để cập nhật chính thức vào hệ thống và ghi log ngày giờ.`;
      }
    }
    if (tbSaveBtn) tbSaveBtn.hidden = !isCompletedDirty;

    $("rep-missing-types").textContent = String(shortages.length);
    $("rep-missing-pieces").textContent = formatQty(totalQty);
    $("rep-total-comp").textContent = formatMoney(totalComp);

    const isCompleted = rental.status === "completed";
    const hasShortage = shortages.length > 0;
    const canDoSupplementary = isCompleted && hasShortage;

    const btnRepSupp = $("btn-report-supplementary");
    if (btnRepSupp) {
      btnRepSupp.hidden = !canDoSupplementary;
    }

    const suppRounds = rental.supplementary_rounds || [];
    const initInsp = rental.initial_inspection || {};
    const initShortages = initInsp.shortages || (suppRounds.length === 0 ? shortages : []);
    const initTotalMissing = initInsp.total_missing_qty ?? (suppRounds.length === 0 ? rental.summary?.total_missing_qty : 0);
    const initTotalComp = initInsp.total_compensation ?? (suppRounds.length === 0 ? rental.summary?.total_compensation : 0);
    const initTime = initInsp.created_at || rental.completed_at || rental.created_at;
    const initActor = initInsp.actor || "Người kiểm kê";

    const historyToolbar = $("rep-history-toolbar");
    const roundsCountText = $("rep-rounds-count-text");
    const btnSuppHistory = $("btn-supp-from-history");
    const roundsContainer = $("rep-rounds-container");

    const totalRoundsCount = 1 + suppRounds.length;
    if (historyToolbar) {
      historyToolbar.hidden = false;
      if (roundsCountText) {
        roundsCountText.textContent = suppRounds.length === 0
          ? "Kiểm kê ban đầu khi nhận hàng trả"
          : `${totalRoundsCount} đợt (Ban đầu + ${suppRounds.length} đợt trả bổ sung)`;
      }
      if (btnSuppHistory) btnSuppHistory.hidden = !canDoSupplementary;
    }

    const hasAnyInitialShortage = (initShortages && initShortages.length > 0);
    $("rep-pending-warning").hidden = rental.status !== "pending";
    $("rep-full-ok-banner").hidden = hasAnyInitialShortage || rental.status === "pending";
    $("rep-pair-rule-note").hidden = !shortages.some((it) =>
      ["primary", "included_in_pair", "solo_in_pair", "flat_set"].includes(it.pair_charge_role)
    );

    // Build dedicated table cards for each round
    let roundsHtml = "";

    // 1. BẢNG ĐỢT BAN ĐẦU: KIỂM KÊ BAN ĐẦU KHI NHẬN HÀNG TRẢ (KHÔNG GHI LẦN 1)
    if (hasAnyInitialShortage) {
      roundsHtml += renderSingleRoundTableCard({
        roundBadgeText: "Ban đầu",
        roundBadgeClass: "pill-blue",
        roundTitle: "BẢNG KIỂM KÊ BAN ĐẦU KHI NHẬN HÀNG TRẢ (DANH SÁCH GỐC)",
        timeStr: formatDateTime(initTime),
        actorName: escapeHtml(initActor),
        noteText: rental.note ? `Ghi chú ban đầu: ${rental.note}` : "",
        returnedItems: [],
        shortageItems: initShortages,
        totalMissingQty: initTotalMissing,
        totalCompensation: initTotalComp,
        footLabel: "TỔNG TIỀN BÙ BAN ĐẦU:",
        isInitial: true,
      });
    }

    // 2. BẢNG CHO CÁC LẦN TIẾP THEO: TRẢ BỔ SUNG LẦN 1, LẦN 2, LẦN 3...
    suppRounds.forEach((round, rIdx) => {
      const retItems = round.returned_items || [];
      const remItems = round.remaining_shortages || [];
      const suppDot = rIdx + 1;
      const roundTitleText = `TRẢ BỔ SUNG LẦN ${suppDot} — BẢNG ĐỐI SOÁT CÒN THIẾU`;

      roundsHtml += renderSingleRoundTableCard({
        roundBadgeText: `Trả bổ sung lần ${suppDot}`,
        roundBadgeClass: (round.remaining_missing_qty === 0) ? "pill-green" : "pill-amber",
        roundTitle: roundTitleText,
        timeStr: formatDateTime(round.created_at),
        actorName: escapeHtml(round.actor),
        noteText: round.note || "",
        returnedItems: retItems,
        shortageItems: remItems,
        totalMissingQty: round.remaining_missing_qty,
        totalCompensation: round.new_total_compensation,
        footLabel: `TỔNG CÒN THIẾU LẠI SAU TRẢ BỔ SUNG LẦN ${suppDot}:`,
        isInitial: false,
      });
    });

    if (roundsContainer) {
      roundsContainer.innerHTML = roundsHtml;

      // Attach click-to-zoom on all item images across all round tables
      roundsContainer.querySelectorAll(".part-thumb").forEach((imgEl) => {
        imgEl.addEventListener("click", () => {
          const src = imgEl.getAttribute("src");
          const name = imgEl.getAttribute("data-name") || "Linh kiện";
          const code = imgEl.getAttribute("data-code") || "";
          const price = parseFloat(imgEl.getAttribute("data-price")) || 0;
          openImageModal([src], name, code, price);
        });
      });
    }

    $("rep-amount-words").innerHTML = `<strong>Bằng chữ (tiền bù hiện tại):</strong> ${escapeHtml(
      numberToVietnameseWords(totalComp)
    )}.`;
  }

  function renderSingleRoundTableCard({
    roundBadgeText,
    roundBadgeClass,
    roundTitle,
    timeStr,
    actorName,
    noteText,
    returnedItems = [],
    shortageItems = [],
    totalMissingQty = 0,
    totalCompensation = 0,
    footLabel = "TỔNG CỘNG:",
    isInitial = false,
  }) {
    // If returned items exist for this round
    let returnedBoxHtml = "";
    if (returnedItems.length > 0) {
      const hasRetCodes = returnedItems.some((it) => Boolean((it.item_code || it.code || "").trim()));
      const retRows = returnedItems.map((it, idx) => {
        const imgSrc = (it.images && it.images.length > 0) ? it.images[0] : "";
        const imgTag = imgSrc
          ? `<img class="part-thumb" src="${escapeHtml(imgSrc)}" data-name="${escapeHtml(it.item_name)}" data-code="${escapeHtml(it.item_code || '')}" data-price="${Number(it.unit_price || 0)}" alt="" style="width:34px; height:34px; object-fit:contain; cursor:pointer;" />`
          : '<span class="muted text-sm">-</span>';
        const codeTd = hasRetCodes
          ? `<td>${it.item_code ? `<span class="code-pill">${escapeHtml(it.item_code)}</span>` : '<span class="muted">-</span>'}</td>`
          : "";
        return `
          <tr>
            <td class="tabular muted" style="width:36px; text-align:center;">${idx + 1}</td>
            <td><span class="kit-tag">${escapeHtml(it.kit_display_label || it.kit_name)}</span></td>
            ${codeTd}
            <td><strong>${escapeHtml(it.item_name)}</strong></td>
            <td style="text-align:center;">${imgTag}</td>
            <td style="text-align:center;"><strong class="text-green">+${formatQty(it.returned_qty)} ${escapeHtml(it.unit || "cái")}</strong></td>
          </tr>
        `;
      }).join("");

      returnedBoxHtml = `
        <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:10px 14px; margin-bottom:14px;">
          <div style="font-size:13px; font-weight:700; color:#166534; margin-bottom:6px;">
            <span>Linh kiện đã nhận bàn giao trả thêm trong đợt này:</span>
          </div>
          <div class="table-responsive">
            <table class="data-table" style="background:#fff; border-radius:6px; overflow:hidden;">
              <thead>
                <tr style="background:#f8fafc;">
                  <th style="width:36px; text-align:center;">STT</th>
                  <th>Bộ robot</th>
                  ${hasRetCodes ? "<th>Mã LK</th>" : ""}
                  <th>Tên linh kiện nhận trả</th>
                  <th style="width:50px; text-align:center;">Ảnh</th>
                  <th style="text-align:center; width:140px;">Số lượng trả thêm</th>
                </tr>
              </thead>
              <tbody>${retRows}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // Missing table for this round
    let tableHtml = "";
    if (shortageItems.length === 0) {
      tableHtml = `
        <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:8px; padding:16px; text-align:center; color:#065f46;">
          <div style="font-size:14.5px; font-weight:700;">KHÁCH HÀNG ĐÃ TRẢ ĐỦ TOÀN BỘ 100% LINH KIỆN — KHÔNG CÒN NỢ MÓN NÀO!</div>
          <div style="font-size:13px; margin-top:4px;">Tổng tiền phải thanh toán sau đợt này: <strong>0 ₫</strong></div>
        </div>
      `;
    } else {
      const hasShortageCodes = shortageItems.some((it) => Boolean((it.item_code || it.code || "").trim()));
      const rows = shortageItems.map((it, idx) => {
        const imgSrc = (it.images && it.images.length > 0) ? it.images[0] : "";
        const imgTag = imgSrc
          ? `<img class="part-thumb" src="${escapeHtml(imgSrc)}" data-name="${escapeHtml(it.item_name || it.name)}" data-code="${escapeHtml(it.item_code || it.code || '')}" data-price="${Number(it.unit_price || 0)}" alt="" style="width:40px; height:40px; object-fit:contain; cursor:pointer;" />`
          : '<span class="muted text-sm">-</span>';

        const role = it.pair_charge_role || "none";
        let compCellHtml = "";
        if (role === "included_in_pair") {
          compCellHtml = `<strong class="muted">0 ₫</strong><div class="pair-included-badge">Tính chung bộ</div>`;
        } else if (role === "flat_set") {
          compCellHtml = `<strong class="text-red">${formatMoney(it.compensation)}</strong><div class="pair-primary-badge pair-card-badge">Cả bộ Card (×1)</div>`;
        } else if (role === "primary") {
          compCellHtml = `<strong class="text-red">${formatMoney(it.compensation)}</strong><div class="pair-primary-badge">Trọn bộ</div>`;
        } else {
          compCellHtml = `<strong class="text-red">${formatMoney(it.compensation)}</strong>`;
        }

        const noteHtml = it.pair_billing_note ? `<div class="pair-billing-note">${escapeHtml(it.pair_billing_note)}</div>` : "";
        const codeTd = hasShortageCodes
          ? `<td>${(it.item_code || it.code) ? `<span class="code-pill">${escapeHtml(it.item_code || it.code)}</span>` : '<span class="muted">-</span>'}</td>`
          : "";

        return `
          <tr>
            <td class="tabular muted" style="width:46px; text-align:center;">${idx + 1}</td>
            <td><span class="kit-tag">${escapeHtml(it.kit_display_label || it.kit_name)}</span></td>
            ${codeTd}
            <td>
              <strong>${escapeHtml(it.item_name || it.name)}</strong>
              ${noteHtml}
            </td>
            <td style="text-align:center;">${imgTag}</td>
            <td>${escapeHtml(it.unit || "cái")}</td>
            <td class="num tabular"><strong class="text-red">${formatQty(it.missing_qty)}</strong></td>
            <td class="num tabular">${formatMoney(it.unit_price, true)}</td>
            <td class="num tabular">${compCellHtml}</td>
          </tr>
        `;
      }).join("");

      tableHtml = `
        <div class="table-responsive">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th style="width:46px; text-align:center;">STT</th>
                <th>Bộ robot</th>
                ${hasShortageCodes ? "<th>Mã LK</th>" : ""}
                <th>Tên hàng / Linh kiện</th>
                <th style="width:70px; text-align:center;">Hình ảnh</th>
                <th style="width:55px;">ĐVT</th>
                <th class="num" style="width:90px;">SL thiếu</th>
                <th class="num" style="width:115px;">Đơn giá</th>
                <th class="num" style="width:130px;">Thành tiền bù</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
            <tfoot>
              <tr class="total-row">
                <td colspan="${hasShortageCodes ? 6 : 5}" class="num"><strong>${footLabel}</strong></td>
                <td class="num"><strong class="text-red">${formatQty(totalMissingQty)}</strong></td>
                <td></td>
                <td class="num total-money"><strong class="text-red">${formatMoney(totalCompensation)}</strong></td>
              </tr>
            </tfoot>
          </table>
        </div>
      `;
    }

    return `
      <div class="card round-report-card" style="border: 1.5px solid var(--border); border-radius: 10px; overflow: hidden; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.03);">
        <div class="card-header" style="background:#f8fafc; border-bottom: 1.5px solid var(--border); padding: 12px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span class="pill ${roundBadgeClass}" style="font-weight:700; font-size:12.5px;">${roundBadgeText}</span>
            <strong style="font-size:14px; color:var(--text);">${roundTitle}</strong>
          </div>
          <div style="font-size:12.5px; color:var(--text-muted); display:flex; gap:14px; align-items:center; flex-wrap:wrap;">
            <span>Thời gian: <strong style="color:var(--text);">${timeStr}</strong></span>
            <span>Người tiếp nhận: <strong style="color:var(--text);">${actorName}</strong></span>
          </div>
        </div>
        <div class="card-body" style="padding:16px;">
          ${noteText ? `<div style="font-size:13px; color:#475569; margin-bottom:12px; background:#f1f5f9; padding:8px 12px; border-radius:6px;"><strong>Ghi chú:</strong> <em>"${escapeHtml(noteText)}"</em></div>` : ""}
          ${returnedBoxHtml}
          ${tableHtml}
        </div>
      </div>
    `;
  }

  async function saveCompletedRentalEdits() {
    if (!state.activeRental || !state.completedEditPending || state.dirtyEntries.size === 0) {
      showToast("Không có thay đổi nào cần lưu.", "info");
      return;
    }

    const rentalId = state.activeRental.id;
    const entriesPayload = Array.from(state.dirtyEntries.values());
    const saveBtn = $("btn-save-completed-edit");
    const tbSaveBtn = $("btn-toolbar-save-edit");
    const inspSaveBtn = $("btn-inspect-save-completed-edit");
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Đang lưu..."; }
    if (tbSaveBtn) { tbSaveBtn.disabled = true; tbSaveBtn.textContent = "Đang lưu..."; }
    if (inspSaveBtn) { inspSaveBtn.disabled = true; inspSaveBtn.textContent = "Đang lưu..."; }

    try {
      const res = await api(`/api/rentals/${rentalId}/entries`, {
        method: "PATCH",
        body: JSON.stringify({ entries: entriesPayload }),
      });

      state.dirtyEntries.clear();
      state.completedEditPending = false;
      state.activeRental = res.rental;

      if (state.activeRental.kit_details) {
        for (const kd of state.activeRental.kit_details) {
          for (const it of kd.items) {
            delete it._pendingEdit;
          }
        }
      }

      setSaveIndicator("saved");
      updateCompletedEditNoticeUI();
      if (state.currentView === "inspect") {
        renderInspectionWorkspace();
      } else {
        renderReport();
      }
      updateTopbarStatusUI();
      await loadRentals();
      showToast("Đã lưu thành công các chỉnh sửa và cập nhật nhật ký thời gian kiểm kê!", "success");
    } catch (err) {
      showToast("Lỗi khi lưu chỉnh sửa: " + err.message, "error");
    } finally {
      const saveBtnIconHtml = `<span>Lưu chỉnh sửa</span>`;
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = saveBtnIconHtml;
      }
      if (tbSaveBtn) {
        tbSaveBtn.disabled = false;
        tbSaveBtn.innerHTML = saveBtnIconHtml;
      }
      if (inspSaveBtn) {
        inspSaveBtn.disabled = false;
        inspSaveBtn.innerHTML = saveBtnIconHtml;
      }
    }
  }

  async function cancelCompletedRentalEdits() {
    if (!state.activeRental || !state.completedEditPending) return;
    confirmUnsavedCompletedEdit(async () => {
      updateCompletedEditNoticeUI();
      if (state.currentView === "inspect") {
        await openInspectForRental(state.activeRental.id);
      } else {
        await openReportForRental(state.activeRental.id, false);
      }
      showToast("Đã hủy bỏ các thay đổi chưa lưu.", "info");
    });
  }

  function buildReportSummaryText(rental) {
    if (!rental) return "";
    const kitsStr = (rental.kits || []).map((k) => k.display_label).join(", ");
    const shortages = rental.shortage_items || [];
    const contactLine = rental.email
      ? `Người trả: ${rental.returner_name} | SĐT: ${rental.phone} | Gmail: ${rental.email}`
      : `Người trả: ${rental.returner_name} | SĐT: ${rental.phone}`;
    const lines = [
      `BÁO CÁO KIỂM KÊ HÀNG THUÊ - ${rental.team_name} (${rental.code})`,
      contactLine,
      `Bộ robot đã thuê: ${kitsStr}`,
    ];
    if (rental.note) {
      lines.push(`Ghi chú: ${rental.note}`);
    }
    lines.push(`----------------------------------------`);

    if (shortages.length === 0) {
      lines.push("Xác nhận: Đã trả ĐỦ 100% linh kiện, không phát sinh tiền bù.");
    } else {
      lines.push(`Danh sách linh kiện thiếu (${shortages.length} loại):`);
      shortages.forEach((it, idx) => {
        const codePart = it.code ? `[${it.code}] ` : "";
        const kitLabel = it.kit_display_label || it.kit_name;
        const role = it.pair_charge_role || "none";
        if (role === "included_in_pair") {
          lines.push(
            `${idx + 1}. ${codePart}${it.name} (${kitLabel}): Thiếu ${formatQty(it.missing_qty)} ${it.unit} → Đã tính chung bộ với ${it.pair_primary_name} = 0 ₫`
          );
        } else if (role === "primary" || role === "solo_in_pair") {
          const chargeQty = it.pair_charge_qty ?? it.missing_qty;
          lines.push(
            `${idx + 1}. ${codePart}${it.name} (${kitLabel}): Thiếu ${formatQty(it.missing_qty)} ${it.unit} [Tính ${formatQty(chargeQty)} bộ × ${formatMoney(it.unit_price, true)}] = ${formatMoney(it.compensation)}`
          );
        } else if (role === "flat_set") {
          lines.push(
            `${idx + 1}. ${codePart}${it.name} (${kitLabel}): Thiếu ${formatQty(it.missing_qty)} ${it.unit} [Cả bộ Card ×1 × ${formatMoney(it.unit_price, true)}] = ${formatMoney(it.compensation)}`
          );
        } else {
          lines.push(
            `${idx + 1}. ${codePart}${it.name} (${kitLabel}): Thiếu ${formatQty(it.missing_qty)} ${it.unit} × ${formatMoney(it.unit_price, true)} = ${formatMoney(it.compensation)}`
          );
        }
      });
      if ((rental.summary?.paired_dedup_count || 0) > 0) {
        lines.push("* Lưu ý: Linh kiện theo bộ (cánh trái/phải, khung & bánh...) chỉ tính bù 1 bộ nếu mất một phần hoặc cả cặp trong cùng bộ robot.");
      }
      if ((rental.summary?.flat_set_count || 0) > 0) {
        lines.push("* Lưu ý: Các bộ có Card chỉ tính tiền ×1 (tiền cả 1 bộ Card), dù thiếu 1 hay toàn bộ số lượng card.");
      }
      lines.push(`----------------------------------------`);
      lines.push(`TỔNG SỐ LƯỢNG THIẾU: ${formatQty(rental.summary.total_missing_qty)}`);
      lines.push(`TỔNG TIỀN BÙ: ${formatMoney(rental.summary.total_compensation)}`);
      lines.push(`(${numberToVietnameseWords(rental.summary.total_compensation)})`);
    }
    lines.push(`----------------------------------------`);
    lines.push(
      `* Lưu ý xem file Excel (.xlsx) trên điện thoại: Vui lòng ưu tiên mở bằng ứng dụng Microsoft Excel hoặc Google Trang tính (Google Sheets) để hiển thị đúng công thức (=SUM, =IF) & hình ảnh linh kiện.`
    );
    return lines.join("\n");
  }

  async function copyReportSummary() {
    const rental = state.activeRental;
    if (!rental) return;
    try {
      await navigator.clipboard.writeText(buildReportSummaryText(rental));
      showToast("Đã sao chép báo cáo để gửi khách hàng!", "success");
    } catch {
      showToast("Không thể truy cập clipboard tự động.", "error");
    }
  }

  function buildAuditHistorySummaryText(rental) {
    if (!rental) return "";
    const lines = [
      `=== TRÍCH LỤC LỊCH SỬ KIỂM KÊ & CÁC LẦN TRẢ HÀNG ===`,
      `Mã phiếu: ${rental.code}`,
      `Khách hàng / Đội: ${rental.team_name}`,
      `Người liên hệ: ${rental.returner_name} - SĐT: ${rental.phone}`,
      `----------------------------------------------------`,
    ];

    const initInsp = rental.initial_inspection || {};
    const initShortages = initInsp.shortages || rental.shortage_items || [];
    const initTime = initInsp.created_at || rental.completed_at || rental.created_at;

    lines.push(`KIỂM KÊ BAN ĐẦU KHI NHẬN HÀNG TRẢ`);
    lines.push(`- Thời gian: ${formatDateTime(initTime)}`);
    lines.push(`- Người kiểm kê: ${initInsp.actor || "Người kiểm kê"}`);
    if (initShortages.length === 0) {
      lines.push(`- Tình trạng: Khách bàn giao đủ 100% linh kiện ban đầu.`);
    } else {
      lines.push(`- Danh sách GỐC linh kiện thiếu (${initShortages.length} loại, tổng ${formatQty(initInsp.total_missing_qty || rental.summary?.total_missing_qty)} món):`);
      initShortages.forEach((it, idx) => {
        const kName = it.kit_display_label || it.kit_name;
        lines.push(`  ${idx + 1}. [${kName}] ${it.item_name || it.name}: Thiếu ${formatQty(it.missing_qty)}${it.unit_price ? ` (Bù: ${formatMoney(it.compensation)})` : ''}`);
      });
      lines.push(`=> Tổng tiền bù ban đầu: ${formatMoney(initInsp.total_compensation || rental.summary?.total_compensation)}`);
    }

    const suppRounds = rental.supplementary_rounds || [];
    suppRounds.forEach((round, rIdx) => {
      lines.push(``);
      lines.push(`TRẢ BỔ SUNG LẦN ${rIdx + 1}`);
      lines.push(`- Thời gian tiếp nhận: ${formatDateTime(round.created_at)}`);
      lines.push(`- Người tiếp nhận: ${round.actor}`);
      if (round.note) lines.push(`- Ghi chú: "${round.note}"`);

      const retItems = round.returned_items || [];
      if (retItems.length > 0) {
        lines.push(`- Đã nhận trả thêm (+${formatQty(round.total_returned_qty)} món):`);
        retItems.forEach((it) => {
          lines.push(`  + [${it.kit_name}] ${it.item_name}: Đã nhận thêm +${formatQty(it.returned_qty)}`);
        });
      }

      if (round.remaining_missing_qty === 0) {
        lines.push(`=> KẾT QUẢ: ĐÃ TRẢ ĐỦ TOÀN BỘ 100% LINH KIỆN - TIỀN BÙ VỀ 0 ₫.`);
      } else {
        const remItems = round.remaining_shortages || [];
        lines.push(`=> SAU ĐỢT NÀY VẪN CÒN THIẾU (${formatQty(round.remaining_missing_qty)} món · Tiền bù còn lại: ${formatMoney(round.new_total_compensation)}):`);
        remItems.forEach((it) => {
          lines.push(`  - [${it.kit_name}] ${it.item_name}: Vẫn còn thiếu ${formatQty(it.missing_qty)}${it.compensation ? ` (${formatMoney(it.compensation)})` : ''}`);
        });
      }
    });

    lines.push(`----------------------------------------------------`);
    lines.push(`Trích xuất lúc: ${new Date().toLocaleString("vi-VN")}`);
    lines.push(`Hệ thống kiểm kê hàng thuê tự động.`);
    return lines.join("\n");
  }

  async function copyAuditHistorySummary() {
    const rental = state.activeRental;
    if (!rental) return;
    try {
      await navigator.clipboard.writeText(buildAuditHistorySummaryText(rental));
      showToast("Đã sao chép trích lục toàn bộ các lần trả hàng (sẵn sàng dán gửi khách đối soát)!", "success");
    } catch {
      showToast("Không thể truy cập clipboard tự động.", "error");
    }
  }

  function openGmailModal() {
    const rental = state.activeRental;
    if (!rental) return;
    const summaryText = buildReportSummaryText(rental);
    const subject = `[Biên bản kiểm kê ${rental.code}] Đội ${rental.team_name} - Thống kê linh kiện trả hàng`;
    $("gmail-modal-sub").textContent = `Phiếu ${rental.code} · Đội ${rental.team_name}`;
    $("gmail-to-input").value = rental.email || "";
    $("gmail-to-input").classList.remove("input-error");
    $("gmail-subject-preview").textContent = subject;
    $("gmail-summary-preview").textContent = summaryText;
    $("gmail-modal").hidden = false;
  }

  // --- Image Zoom Modal ---
  function openImageModal(images, title, code, unitPrice) {
    $("modal-img-title").textContent = title || "Chi tiết linh kiện";
    const codeEl = $("modal-img-code");
    if (code) {
      codeEl.hidden = false;
      codeEl.textContent = code;
    } else {
      codeEl.hidden = true;
    }
    const body = $("modal-img-body");
    body.innerHTML = (images || [])
      .map((src) => `<img src="${escapeHtml(src)}" alt="${escapeHtml(title)}" />`)
      .join("");
    const meta = $("modal-img-meta");
    meta.innerHTML = unitPrice
      ? `<span>Đơn giá: <strong>${formatMoney(unitPrice, true)}</strong></span>`
      : `<span>Hình ảnh từ file Excel gốc</span>`;
    $("img-modal").hidden = false;
  }

  // --- Audit & Change Logs Modal ---
  async function openChangeLogsModal(rentalId) {
    const rId = rentalId || (state.activeRental ? state.activeRental.id : null);
    if (!rId) {
      showToast("Chưa chọn phiếu để xem nhật ký thay đổi.", "warning");
      return;
    }
    try {
      const res = await api(`/api/rentals/${rId}/logs`);
      const logs = res.logs || [];
      $("log-modal-code").textContent = res.code || (state.activeRental ? state.activeRental.code : "PKK");
      $("log-modal-count").textContent = `${logs.length} lượt ghi nhận`;
      const staffEl = $("log-current-staff-display");
      if (staffEl) staffEl.textContent = getCurrentStaffName();

      const tbody = $("log-modal-tbody");
      tbody.innerHTML = "";
      $("log-modal-empty").hidden = logs.length > 0;

      for (const log of logs) {
        const tr = document.createElement("tr");
        let badgeClass = "log-action-update";
        let actionLabel = "Cập nhật";
        if (log.action === "CREATE_RENTAL") {
          badgeClass = "log-action-create";
          actionLabel = "Tiếp nhận";
        } else if (log.action === "INSPECT_ITEMS") {
          badgeClass = "log-action-inspect";
          actionLabel = "Kiểm đếm";
        } else if (log.action === "COMPLETE_RENTAL") {
          badgeClass = "log-action-complete";
          actionLabel = "Hoàn tất";
        } else if (log.action === "EDIT_COMPLETED") {
          badgeClass = "log-action-edit";
          actionLabel = "Sửa đã chốt";
        } else if (log.action === "REOPEN_INSPECTION") {
          badgeClass = "log-action-reopen";
          actionLabel = "Mở lại";
        } else if (log.action === "SUPPLEMENTARY_RETURN") {
          badgeClass = "log-action-inspect";
          actionLabel = "Trả bổ sung";
        }

        let detailsHtml = escapeHtml(log.summary || "");
        if (log.details) {
          try {
            const parsed = JSON.parse(log.details);
            if (Array.isArray(parsed) && parsed.length > 0) {
              const diffLines = parsed.map(
                (d) => `• <strong>${escapeHtml(d.item_name || d.item_id)}</strong>: ${escapeHtml(d.old_val)} → <strong style="color:var(--primary,#2563eb)">${escapeHtml(d.new_val)}</strong>${d.note ? ` (<em>${escapeHtml(d.note)}</em>)` : ""}`
              ).join("<br/>");
              detailsHtml = `<div>${escapeHtml(log.summary || "")}</div><div class="text-sm muted" style="margin-top:4px; line-height:1.45">${diffLines}</div>`;
            }
          } catch {
            // keep summary
          }
        }

        tr.innerHTML = `
          <td style="white-space:nowrap; font-size:12.5px; color:var(--text-muted);">${escapeHtml(formatLogTimestamp(log.created_at))}</td>
          <td><strong style="color:var(--text);">${escapeHtml(log.actor || "Người kiểm kê")}</strong></td>
          <td><span class="log-action-badge ${badgeClass}">${escapeHtml(actionLabel)}</span></td>
          <td>${detailsHtml}</td>
        `;
        tbody.appendChild(tr);
      }

      $("change-logs-modal").hidden = false;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // --- Supplementary Returns Flow (Trả hàng bổ sung) ---
  async function openSupplementaryModal(rentalId) {
    try {
      showToast("Đang kiểm tra trạng thái và danh sách linh kiện thiếu...", "info");
      const res = await api(`/api/rentals/${rentalId}/supplementary-preview`);
      if (!res.ok) throw new Error(res.error || "Không thể tải thông tin");

      if (!res.missing_items || res.missing_items.length === 0) {
        showToast("Đơn hàng này hiện không còn linh kiện nào bị thiếu!", "success");
        return;
      }

      state.suppModalData = res;
      $("supp-modal-round-tag").textContent = res.next_round_name;
      $("supp-modal-code").textContent = res.ticket_code;
      $("supp-modal-team").textContent = res.team_name;
      $("supp-missing-count").textContent = String(res.missing_items.length);

      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      const liveTimeStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
      $("supp-live-time").textContent = liveTimeStr;

      const savedStaff = getCurrentStaffName();
      $("supp-actor-input").value = savedStaff;
      $("supp-note-input").value = "";

      renderSupplementaryTable(res.missing_items);
      updateSupplementarySummary();

      $("modal-supplementary").hidden = false;
    } catch (err) {
      showToast(err.message || "Lỗi tải thông tin trả bổ sung", "error");
    }
  }

  function renderSupplementaryTable(missingItems) {
    const tbody = $("supp-tbody");
    tbody.innerHTML = "";

    missingItems.forEach((it) => {
      const tr = document.createElement("tr");
      tr.dataset.rkId = String(it.rental_kit_id);
      tr.dataset.itemId = String(it.item_id);
      tr.dataset.maxMissing = String(it.missing_qty);
      tr.dataset.unitPrice = String(it.unit_price || 0);

      const imgHtml = it.image_url
        ? `<img class="part-thumb" src="${escapeHtml(it.image_url)}" alt="${escapeHtml(it.item_name)}" style="width:36px; height:36px; object-fit:contain;" />`
        : "";

      tr.innerHTML = `
        <td>
          <span class="kit-tag">${escapeHtml(it.kit_display_label || it.kit_name)}</span>
        </td>
        <td>
          <div style="display:flex; align-items:center; gap:8px;">
            ${imgHtml}
            <div>
              <strong>${escapeHtml(it.item_name)}</strong>
              ${it.item_code ? `<span class="code-pill" style="margin-left:4px;">${escapeHtml(it.item_code)}</span>` : ""}
              ${it.pair_billing_note ? `<div class="text-sm muted">${escapeHtml(it.pair_billing_note)}</div>` : ""}
            </div>
          </div>
        </td>
        <td class="num"><strong class="text-red">${formatQty(it.missing_qty)}</strong></td>
        <td style="text-align:center;">
          <div style="display:flex; align-items:center; justify-content:center; gap:6px;">
            <input type="number" min="0" max="${it.missing_qty}" step="any" class="supp-input-qty" value="0" />
            <button type="button" class="btn btn-xs btn-outline act-supp-full" title="Khách trả đủ số lượng đang thiếu">Trả đủ</button>
          </div>
        </td>
        <td class="num"><strong class="supp-cell-remaining text-red">${formatQty(it.missing_qty)}</strong></td>
        <td class="num">${formatMoney(it.unit_price)}</td>
      `;

      const input = tr.querySelector(".supp-input-qty");
      const fullBtn = tr.querySelector(".act-supp-full");
      const remEl = tr.querySelector(".supp-cell-remaining");

      const onQtyChange = () => {
        let val = parseFloat(input.value.replace(",", ".")) || 0;
        if (val < 0) val = 0;
        if (val > it.missing_qty) {
          val = it.missing_qty;
          input.value = String(val);
        }
        if (val > 0) {
          input.classList.add("has-val");
        } else {
          input.classList.remove("has-val");
        }

        const rem = Math.max(0, it.missing_qty - val);
        remEl.textContent = formatQty(rem);
        if (rem === 0) {
          remEl.className = "supp-cell-remaining text-green";
        } else {
          remEl.className = "supp-cell-remaining text-red";
        }

        updateSupplementarySummary();
      };

      input.addEventListener("input", onQtyChange);
      input.addEventListener("change", onQtyChange);

      fullBtn.addEventListener("click", () => {
        input.value = String(it.missing_qty);
        onQtyChange();
      });

      tbody.appendChild(tr);
    });
  }

  function updateSupplementarySummary() {
    const rows = $("supp-tbody").querySelectorAll("tr");
    let totalReturned = 0;
    let totalRemaining = 0;
    let estimatedComp = 0;

    rows.forEach((tr) => {
      const maxMissing = parseFloat(tr.dataset.maxMissing) || 0;
      const unitPrice = parseFloat(tr.dataset.unitPrice) || 0;
      const input = tr.querySelector(".supp-input-qty");
      const retVal = parseFloat(input?.value?.replace(",", ".")) || 0;

      const rem = Math.max(0, maxMissing - retVal);
      totalReturned += retVal;
      totalRemaining += rem;
      estimatedComp += (rem * unitPrice);
    });

    $("supp-sum-returned").textContent = `${formatQty(totalReturned)} món`;
    $("supp-sum-remaining").textContent = `${formatQty(totalRemaining)} món`;
    $("supp-sum-comp").textContent = formatMoney(estimatedComp);

    const submitBtn = $("btn-submit-supplementary");
    if (submitBtn) {
      submitBtn.disabled = (totalReturned <= 0);
    }
  }

  async function submitSupplementaryReturn() {
    if (!state.suppModalData) return;
    const rentalId = state.suppModalData.rental_id;
    const actor = $("supp-actor-input").value.trim() || "Người kiểm kê";
    const note = $("supp-note-input").value.trim();

    setCurrentStaffName(actor);

    const rows = $("supp-tbody").querySelectorAll("tr");
    const returnedItems = [];

    rows.forEach((tr) => {
      const rkId = parseInt(tr.dataset.rkId, 10);
      const itemId = tr.dataset.itemId;
      const input = tr.querySelector(".supp-input-qty");
      const val = parseFloat(input?.value?.replace(",", ".")) || 0;
      if (val > 0) {
        returnedItems.push({
          rental_kit_id: rkId,
          item_id: itemId,
          returned_qty: val,
        });
      }
    });

    if (returnedItems.length === 0) {
      showToast("Vui lòng nhập số lượng linh kiện trả bổ sung (> 0).", "error");
      return;
    }

    const submitBtn = $("btn-submit-supplementary");
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span>Đang lưu...</span>`;

    try {
      const res = await api(`/api/rentals/${rentalId}/supplementary-return`, {
        method: "POST",
        body: JSON.stringify({
          actor,
          note,
          returned_items: returnedItems,
        }),
      });

      $("modal-supplementary").hidden = true;
      showToast(res.message || "Đã lưu đợt trả bổ sung thành công!", "success");

      state.activeRental = res.rental;
      await loadRentals();

      if (state.view === "teams") {
        renderTeamsList();
      } else if (state.view === "report") {
        renderReport();
        updateTopbarStatusUI();
      } else if (state.view === "inspect") {
        renderInspectionWorkspace();
        updateTopbarStatusUI();
      }
    } catch (err) {
      showToast(err.message || "Lỗi lưu trả bổ sung", "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `<span id="supp-submit-btn-text">Lưu đợt trả bổ sung</span>`;
    }
  }

  // --- Event Bindings ---
  function bindEvents() {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view, { viaNav: true }));
    });

    const uninspPill = $("nav-uninspected-pill") || $("active-ticket-pill");
    uninspPill?.addEventListener("click", () => {
      switchView("teams", { viaNav: true });
      const targetBtn = document.querySelector("#team-filter-pills [data-filter='returned_uninspected']");
      if (targetBtn) {
        targetBtn.click();
      } else {
        state.teamFilter = "returned_uninspected";
        renderTeamsList();
      }
    });

    document.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.goto, { viaNav: true }));
    });

    // CRM File Import bindings
    $("btn-browse-crm-file")?.addEventListener("click", () => {
      $("crm-file-input")?.click();
    });

    $("crm-file-input")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (file) loadCrmPreview(file, false);
    });

    const dropZone = $("crm-drop-zone");
    if (dropZone) {
      dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("drag-over");
      });
      dropZone.addEventListener("dragleave", (e) => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
      });
      dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        const file = e.dataTransfer.files?.[0];
        if (file) loadCrmPreview(file, false);
      });
    }

    $("btn-crm-cancel")?.addEventListener("click", () => {
      state.crmPreviewData = null;
      if ($("crm-preview-container")) $("crm-preview-container").hidden = true;
      if ($("crm-file-chosen-name")) $("crm-file-chosen-name").hidden = true;
      if ($("crm-file-input")) $("crm-file-input").value = "";
      showToast("Đã hủy bỏ bản xem trước CRM.", "info");
    });

    $("btn-crm-confirm-import")?.addEventListener("click", executeCrmImport);

    $("crm-preview-search")?.addEventListener("input", (e) => {
      state.crmSearch = e.target.value;
      renderCrmPreviewTable();
    });

    document.querySelectorAll("#crm-preview-filters .pill-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.crmFilter = btn.dataset.filter;
        document.querySelectorAll("#crm-preview-filters .pill-btn").forEach((b) => {
          b.classList.toggle("active", b === btn);
        });
        renderCrmPreviewTable();
      });
    });

    $("checkin-form")?.addEventListener("submit", handleCheckinSubmit);
    ["input-returner", "input-team", "input-phone", "input-email", "input-note"].forEach((id) => {
      $(id)?.addEventListener("input", () => {
        $(id)?.classList.remove("input-error");
        updateTopbarStatusUI();
      });
    });
    $("btn-reset-checkin")?.addEventListener("click", () => {
      resetCheckinForm();
    });

    $("btn-locked-goto-report")?.addEventListener("click", () => {
      if (state.activeRental) openReportForRental(state.activeRental.id, true);
    });
    $("btn-locked-goto-teams")?.addEventListener("click", () => switchView("teams"));

    $("btn-new-team")?.addEventListener("click", () => {
      switchView("checkin");
    });

    $("btn-save-kit-code")?.addEventListener("click", () => saveActiveKitCode(false));
    $("insp-kit-code-input")?.addEventListener("input", handleKitCodeInputChange);
    $("insp-kit-code-input")?.addEventListener("blur", () => saveActiveKitCode(true));
    $("insp-kit-code-input")?.addEventListener("change", () => saveActiveKitCode(true));
    $("insp-kit-code-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveActiveKitCode(false);
      }
    });

    $("btn-close-missing-kit-code")?.addEventListener("click", () => {
      $("modal-missing-kit-code").hidden = true;
    });
    $("modal-missing-kit-code")?.addEventListener("click", (e) => {
      if (e.target === $("modal-missing-kit-code")) $("modal-missing-kit-code").hidden = true;
    });

    $("btn-close-unentered-kits")?.addEventListener("click", () => {
      $("modal-unentered-kits").hidden = true;
    });
    $("modal-unentered-kits")?.addEventListener("click", (e) => {
      if (e.target === $("modal-unentered-kits")) $("modal-unentered-kits").hidden = true;
    });

    $("team-search").addEventListener("input", (e) => {
      state.teamSearch = e.target.value;
      renderTeamsList();
    });

    document.querySelectorAll("#team-filter-pills .pill-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.teamFilter = btn.dataset.filter;
        document.querySelectorAll("#team-filter-pills .pill-btn").forEach((b) => {
          b.classList.toggle("active", b === btn);
        });
        renderTeamsList();
      });
    });

    $("insp-search").addEventListener("input", (e) => {
      state.inspSearch = e.target.value;
      renderInspectionTable();
    });

    $("insp-section-select").addEventListener("change", (e) => {
      state.inspSection = e.target.value;
      renderInspectionTable();
    });

    document.querySelectorAll("#insp-filter-pills .pill-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.inspFilter = btn.dataset.filter;
        document.querySelectorAll("#insp-filter-pills .pill-btn").forEach((b) => {
          b.classList.toggle("active", b === btn);
        });
        renderInspectionTable();
      });
    });

    $("btn-back-teams").addEventListener("click", () => switchView("teams"));
    $("btn-sticky-teams").addEventListener("click", () => switchView("teams"));
    $("btn-goto-report").addEventListener("click", () => {
      if (state.activeRental) {
        if (state.activeRental.status === "completed" && state.completedEditPending) {
          proceedToReportValidation(() => {
            recalculateActiveRentalStats();
            switchView("report", { skipReportReload: true });
          });
        } else if (state.activeRental.status !== "completed") {
          proceedToReportValidation(() => {
            openReportForRental(state.activeRental.id, true);
          });
        } else {
          openReportForRental(state.activeRental.id, true);
        }
      }
    });
    $("btn-sticky-report").addEventListener("click", () => {
      if (state.activeRental) {
        if (state.activeRental.status === "completed" && state.completedEditPending) {
          proceedToReportValidation(() => {
            recalculateActiveRentalStats();
            switchView("report", { skipReportReload: true });
          });
        } else if (state.activeRental.status !== "completed") {
          proceedToReportValidation(() => {
            openReportForRental(state.activeRental.id, true);
          });
        } else {
          openReportForRental(state.activeRental.id, true);
        }
      }
    });

    $("btn-report-back-inspect").addEventListener("click", () => switchView("inspect"));
    $("btn-report-back-teams").addEventListener("click", () => switchView("teams"));
    $("btn-send-gmail").addEventListener("click", openGmailModal);
    $("btn-copy-summary").addEventListener("click", copyReportSummary);
    $("btn-copy-history-audit")?.addEventListener("click", copyAuditHistorySummary);
    $("btn-print-report").addEventListener("click", () => window.print());
    $("btn-view-change-logs").addEventListener("click", () => openChangeLogsModal());
    const inspLogsBtn = $("btn-inspect-view-logs");
    if (inspLogsBtn) inspLogsBtn.addEventListener("click", () => openChangeLogsModal());

    const closeChangeLogsModal = () => {
      $("change-logs-modal").hidden = true;
    };
    $("log-modal-close").addEventListener("click", closeChangeLogsModal);
    $("btn-log-modal-close").addEventListener("click", closeChangeLogsModal);
    $("change-logs-modal").addEventListener("click", (e) => {
      if (e.target === $("change-logs-modal")) closeChangeLogsModal();
    });

    $("btn-change-staff-name").addEventListener("click", () => {
      const current = getCurrentStaffName();
      const entered = prompt("Nhập tên người kiểm kê / người thực hiện thao tác:", current);
      if (entered !== null) {
        const updated = setCurrentStaffName(entered);
        showToast(`Đã đổi tên người thực hiện thành: "${updated}"`, "success");
      }
    });

    $("gmail-to-input").addEventListener("input", () => {
      $("gmail-to-input").classList.remove("input-error");
    });
    $("gmail-modal-close").addEventListener("click", () => {
      $("gmail-modal").hidden = true;
    });
    $("gmail-modal").addEventListener("click", (e) => {
      if (e.target === $("gmail-modal")) $("gmail-modal").hidden = true;
    });

    $("btn-gmail-download-excel").addEventListener("click", () => {
      const rental = state.activeRental;
      if (!rental) return;
      const mode = (rental.shortage_items || []).length > 0 ? "shortage" : "full";
      window.location.href = `/api/rentals/${rental.id}/export/excel?mode=${mode}`;
    });

    $("btn-gmail-web-compose").addEventListener("click", async () => {
      const rental = state.activeRental;
      if (!rental) return;
      const toEmail = $("gmail-to-input").value.trim();
      if (toEmail && !isValidEmail(toEmail)) {
        $("gmail-to-input").classList.add("input-error");
        $("gmail-to-input").focus();
        showToast("Địa chỉ Gmail người nhận không đúng định dạng.", "error");
        return;
      }

      const subject = `[Biên bản kiểm kê ${rental.code}] Đội ${rental.team_name} - Thống kê linh kiện trả hàng`;
      const fullBody = buildReportSummaryText(rental);
      const buildUrl = (bodyText) =>
        `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(toEmail)}&su=${encodeURIComponent(
          subject
        )}&body=${encodeURIComponent(bodyText)}`;

      let composeUrl = buildUrl(fullBody);
      let clippedToClipboard = false;
      if (composeUrl.length > 7500) {
        try {
          await navigator.clipboard.writeText(fullBody);
          clippedToClipboard = true;
        } catch {
          // Ignore clipboard denial
        }
        const shortBody =
          fullBody.slice(0, 1500) +
          "\n...\n[Danh sách quá dài cho liên kết trình duyệt — toàn bộ nội dung đã được copy sẵn vào Clipboard, bạn chỉ cần Ctrl+V vào email hoặc đính kèm file Excel.]";
        composeUrl = buildUrl(shortBody);
      }

      window.open(composeUrl, "_blank", "noopener");
      showToast(
        clippedToClipboard
          ? "Đã mở Gmail soạn sẵn & copy đầy đủ nội dung vào Clipboard!"
          : "Đã mở trang soạn Gmail. Có thể bấm 'Tải file Excel để tự đính kèm' nếu cần gửi kèm bảng ảnh.",
        "success"
      );
    });

    $("btn-export-excel-shortage").addEventListener("click", async () => {
      if (!state.activeRental) return;
      if (state.completedEditPending) {
        const autoSave = window.confirm(
          "Bạn có các chỉnh sửa mới chưa được lưu vào hệ thống. Bạn có muốn lưu chỉnh sửa ngay để file Excel xuất ra có đầy đủ số lượng và giá mới nhất không?"
        );
        if (!autoSave) return;
        await saveCompletedRentalEdits();
      }
      window.location.href = `/api/rentals/${state.activeRental.id}/export/excel?mode=shortage`;
    });
    $("btn-export-excel-full").addEventListener("click", async () => {
      if (!state.activeRental) return;
      if (state.completedEditPending) {
        const autoSave = window.confirm(
          "Bạn có các chỉnh sửa mới chưa được lưu vào hệ thống. Bạn có muốn lưu chỉnh sửa ngay để file Excel xuất ra có đầy đủ số lượng và giá mới nhất không?"
        );
        if (!autoSave) return;
        await saveCompletedRentalEdits();
      }
      window.location.href = `/api/rentals/${state.activeRental.id}/export/excel?mode=full`;
    });

    const closeCompleteModal = () => {
      $("complete-modal").hidden = true;
    };
    $("complete-cancel-btn").addEventListener("click", closeCompleteModal);
    $("complete-modal-close").addEventListener("click", closeCompleteModal);
    $("complete-modal").addEventListener("click", (e) => {
      if (e.target === $("complete-modal")) closeCompleteModal();
    });

    $("btn-toggle-complete").addEventListener("click", () => {
      const rental = state.activeRental;
      if (!rental || rental.status === "completed") return;
      const comp = Number(rental.summary?.total_compensation || 0);
      $("complete-modal-msg").textContent = `Xác nhận hoàn tất kiểm kê phiếu ${rental.code} (Đội: ${rental.team_name}) · Tổng tiền bù ghi nhận: ${formatMoney(comp)}.`;
      const okBtn = $("complete-confirm-btn");
      okBtn.disabled = false;
      okBtn.textContent = "Hoàn tất kiểm kê & Ghi nhận";
      $("complete-modal").hidden = false;
    });

    $("complete-confirm-btn").addEventListener("click", async () => {
      const rental = state.activeRental;
      if (!rental || rental.status === "completed") return;
      const okBtn = $("complete-confirm-btn");
      okBtn.disabled = true;
      okBtn.textContent = "Đang ghi nhận...";
      try {
        await flushPendingEntries();
        const res = await api(`/api/rentals/${rental.id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: "completed" }),
        });
        state.activeRental = res.rental;
        updateTopbarStatusUI();
        closeCompleteModal();
        renderReport();
        await loadRentals();
        showToast("Đã hoàn tất kiểm kê và ghi nhận thành công!", "success");
      } catch (err) {
        showToast(err.message, "error");
        okBtn.disabled = false;
        okBtn.textContent = "Hoàn tất kiểm kê & Ghi nhận";
      }
    });

    $("btn-save-completed-edit")?.addEventListener("click", saveCompletedRentalEdits);
    $("btn-toolbar-save-edit")?.addEventListener("click", saveCompletedRentalEdits);
    $("btn-inspect-save-completed-edit")?.addEventListener("click", saveCompletedRentalEdits);
    $("btn-cancel-completed-edit")?.addEventListener("click", cancelCompletedRentalEdits);
    $("btn-inspect-cancel-completed-edit")?.addEventListener("click", cancelCompletedRentalEdits);

    $("btn-reload-excel").addEventListener("click", async () => {
      const btn = $("btn-reload-excel");
      btn.disabled = true;
      try {
        await loadCatalog(true);
        if (state.activeRental) {
          const data = await api(`/api/rentals/${state.activeRental.id}`);
          state.activeRental = data.rental;
          if (state.view === "inspect") renderInspectionWorkspace();
          if (state.view === "report") renderReport();
          updateTopbarStatusUI();
        }
        if (state.view === "teams") await loadRentals();
        showToast("Đã đồng bộ dữ liệu mới nhất từ KIEM KE HANG THUE.xlsx!", "success");
      } catch (err) {
        showToast("Lỗi đồng bộ Excel: " + err.message, "error");
      } finally {
        btn.disabled = false;
      }
    });

    $("modal-img-close").addEventListener("click", () => {
      $("img-modal").hidden = true;
    });
    $("img-modal").addEventListener("click", (e) => {
      if (e.target === $("img-modal")) $("img-modal").hidden = true;
    });

    // Supplementary Modal bindings
    $("btn-inspect-supplementary")?.addEventListener("click", () => {
      if (state.activeRental) openSupplementaryModal(state.activeRental.id);
    });
    $("btn-report-supplementary")?.addEventListener("click", () => {
      if (state.activeRental) openSupplementaryModal(state.activeRental.id);
    });
    $("btn-supp-from-history")?.addEventListener("click", () => {
      if (state.activeRental) openSupplementaryModal(state.activeRental.id);
    });

    const closeSupplementaryModal = () => {
      $("modal-supplementary").hidden = true;
    };
    $("btn-close-supplementary-modal")?.addEventListener("click", closeSupplementaryModal);
    $("btn-cancel-supplementary")?.addEventListener("click", closeSupplementaryModal);
    $("modal-supplementary")?.addEventListener("click", (e) => {
      if (e.target === $("modal-supplementary")) closeSupplementaryModal();
    });
    $("btn-submit-supplementary")?.addEventListener("click", submitSupplementaryReturn);

    // Edit Rental Info Modal Handlers
    const closeEditInfoModal = () => {
      $("modal-edit-rental-info").hidden = true;
    };
    $("btn-close-edit-info")?.addEventListener("click", closeEditInfoModal);
    $("btn-cancel-edit-info")?.addEventListener("click", closeEditInfoModal);
    $("modal-edit-rental-info")?.addEventListener("click", (e) => {
      if (e.target === $("modal-edit-rental-info")) closeEditInfoModal();
    });
    $("btn-submit-edit-info")?.addEventListener("click", submitEditRentalInfo);

    // View Original Info Modal Handlers
    const closeOrigInfoModal = () => {
      $("modal-view-original-info").hidden = true;
    };
    $("btn-close-orig-info")?.addEventListener("click", closeOrigInfoModal);
    $("btn-close-orig-info-foot")?.addEventListener("click", closeOrigInfoModal);
    $("modal-view-original-info")?.addEventListener("click", (e) => {
      if (e.target === $("modal-view-original-info")) closeOrigInfoModal();
    });

    // Add Items Modal Handlers
    const closeAddItemsModal = () => {
      $("modal-add-rental-items").hidden = true;
    };
    $("btn-close-add-items")?.addEventListener("click", closeAddItemsModal);
    $("btn-cancel-add-items")?.addEventListener("click", closeAddItemsModal);
    $("modal-add-rental-items")?.addEventListener("click", (e) => {
      if (e.target === $("modal-add-rental-items")) closeAddItemsModal();
    });
    $("btn-submit-add-items")?.addEventListener("click", submitAddRentalItems);

    document.querySelectorAll(".tab-add-type").forEach((tabBtn) => {
      tabBtn.addEventListener("click", () => {
        document.querySelectorAll(".tab-add-type").forEach((b) => b.classList.remove("active"));
        tabBtn.classList.add("active");
        const targetId = tabBtn.dataset.tab;
        document.querySelectorAll(".tab-add-pane").forEach((pane) => {
          pane.hidden = pane.id !== targetId;
        });
      });
    });

    $("add-items-search")?.addEventListener("input", () => {
      renderAddItemsCatalogTable();
    });
    $("add-items-filter-kit")?.addEventListener("change", () => {
      renderAddItemsCatalogTable();
    });
    $("btn-clear-item-selection")?.addEventListener("click", () => {
      addItemsSelectedComponents.clear();
      renderAddItemsCatalogTable();
      renderAddItemsSelectedPanel();
      updateAddItemsSelectionSummary();
    });

    // Compare Added Kits Modal Handlers
    const closeCompareKitsModal = () => {
      $("modal-compare-added-kits").hidden = true;
    };
    $("btn-close-compare-kits")?.addEventListener("click", closeCompareKitsModal);
    $("btn-close-compare-kits-foot")?.addEventListener("click", closeCompareKitsModal);
    $("modal-compare-added-kits")?.addEventListener("click", (e) => {
      if (e.target === $("modal-compare-added-kits")) closeCompareKitsModal();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        $("img-modal").hidden = true;
        $("complete-modal").hidden = true;
        $("gmail-modal").hidden = true;
        $("step1-confirm-modal").hidden = true;
        $("unsaved-edit-modal").hidden = true;
        $("change-logs-modal").hidden = true;
        $("modal-missing-kit-code").hidden = true;
        $("modal-unentered-kits").hidden = true;
        $("modal-supplementary").hidden = true;
        $("modal-edit-rental-info").hidden = true;
        $("modal-view-original-info").hidden = true;
        $("modal-add-rental-items").hidden = true;
        $("modal-compare-added-kits").hidden = true;
      }
    });

    window.addEventListener("beforeunload", (e) => {
      if (state.completedEditPending && state.dirtyEntries.size > 0) {
        e.preventDefault();
        e.returnValue = "Chỉnh sửa chưa lưu, bạn có muốn thoát ra không?";
        return e.returnValue;
      }
      if (!state.activeRental || state.activeRental.status === "completed") return;
      if (state.dirtyEntries.size > 0) {
        const payload = JSON.stringify({ entries: Array.from(state.dirtyEntries.values()) });
        navigator.sendBeacon?.(
          `/api/rentals/${state.activeRental.id}/entries`,
          new Blob([payload], { type: "application/json" })
        );
      }
    });
  }

  // --- Modal Functions: Edit Info, View Original, Add Items, Compare Added ---
  async function openEditRentalInfoModal(rentalId) {
    try {
      const data = await api(`/api/rentals/${rentalId}`);
      const r = data.rental;
      $("edit-info-rental-id").value = r.id;
      $("edit-info-ticket-code").textContent = r.code;
      $("edit-info-team").value = r.team_name || "";
      $("edit-info-returner").value = r.returner_name || "";
      $("edit-info-phone").value = r.phone || "";
      $("edit-info-email").value = r.email || "";
      $("edit-info-due-date").value = r.due_date ? String(r.due_date).slice(0, 10) : "";
      $("edit-info-return-status").value = r.return_status || "Đã trả";
      $("edit-info-address").value = r.delivery_address || "";
      $("edit-info-note").value = r.note || "";
      $("edit-info-actor").value = getCurrentStaffName();

      const btnViewOrig = $("btn-edit-info-view-orig");
      if (btnViewOrig) {
        btnViewOrig.hidden = !r.is_edited;
        btnViewOrig.onclick = () => {
          $("modal-edit-rental-info").hidden = true;
          openViewOriginalInfoModal(r.id);
        };
      }

      $("modal-edit-rental-info").hidden = false;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function submitEditRentalInfo() {
    const rentalId = $("edit-info-rental-id").value;
    if (!rentalId) return;

    const payload = {
      team_name: $("edit-info-team").value.trim(),
      returner_name: $("edit-info-returner").value.trim(),
      phone: $("edit-info-phone").value.trim(),
      email: $("edit-info-email").value.trim(),
      due_date: $("edit-info-due-date").value.trim(),
      return_status: $("edit-info-return-status").value,
      delivery_address: $("edit-info-address").value.trim(),
      note: $("edit-info-note").value.trim(),
      actor: $("edit-info-actor").value.trim() || getCurrentStaffName(),
    };

    if (!payload.team_name || !payload.returner_name || !payload.phone) {
      showToast("Vui lòng điền đủ Tên đội, Người nhận và SĐT", "error");
      return;
    }

    try {
      const res = await api(`/api/rentals/${rentalId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      $("modal-edit-rental-info").hidden = true;
      showToast("Đã cập nhật thông tin đơn thuê thành công!", "success");
      await loadRentals();
      if (state.activeRental && state.activeRental.id === Number(rentalId)) {
        state.activeRental = res.rental;
        renderInspectionWorkspace();
      }
    } catch (err) {
      showToast("Lỗi cập nhật: " + err.message, "error");
    }
  }

  async function openViewOriginalInfoModal(rentalId) {
    try {
      const data = await api(`/api/rentals/${rentalId}`);
      const r = data.rental;
      $("orig-info-ticket-code").textContent = r.code;

      const orig = r.original_info || {};
      const fields = [
        { key: "team_name", label: "Khách hàng / Đội thuê", orig: orig.team_name, curr: r.team_name },
        { key: "returner_name", label: "Người nhận / Người trả", orig: orig.returner_name, curr: r.returner_name },
        { key: "phone", label: "Số điện thoại", orig: orig.phone, curr: r.phone },
        { key: "email", label: "Email", orig: orig.email, curr: r.email },
        { key: "due_date", label: "Hạn trả hàng", orig: orig.due_date, curr: r.due_date },
        { key: "delivery_address", label: "Địa chỉ giao hàng", orig: orig.delivery_address, curr: r.delivery_address },
        { key: "return_status", label: "Tình trạng trả thiết bị", orig: orig.return_status, curr: r.return_status },
        { key: "note", label: "Ghi chú", orig: orig.note, curr: r.note },
      ];

      const tbody = $("orig-info-tbody");
      tbody.innerHTML = "";
      for (const f of fields) {
        const oVal = String(f.orig || "").trim();
        const cVal = String(f.curr || "").trim();
        const isChanged = oVal !== cVal;

        const tr = document.createElement("tr");
        if (isChanged) tr.classList.add("diff-row-changed");
        tr.innerHTML = `
          <td><strong>${escapeHtml(f.label)}</strong></td>
          <td style="color:var(--text);">${escapeHtml(oVal || "—")}</td>
          <td><strong style="color:${isChanged ? 'var(--blue)' : 'var(--text)'};">${escapeHtml(cVal || "—")}</strong></td>
          <td style="text-align:center;">
            ${isChanged ? '<span class="diff-tag-changed">Đã sửa</span>' : '<span class="diff-tag-same">Giữ nguyên</span>'}
          </td>
        `;
        tbody.appendChild(tr);
      }

      // Render logs
      const logsBox = $("orig-info-logs");
      const editLogs = (r.change_logs || []).filter((l) => l.action === "EDIT_INFO");
      if (editLogs.length === 0) {
        logsBox.innerHTML = `<span class="muted">Chưa ghi nhận lượt chỉnh sửa nào.</span>`;
      } else {
        logsBox.innerHTML = editLogs
          .map(
            (l) => `
          <div style="padding:4px 0; border-bottom:1px solid var(--border);">
            <span class="muted">${escapeHtml(l.created_at)}</span> · 
            <strong>${escapeHtml(l.actor)}</strong>: 
            <span>${escapeHtml(l.summary)}</span>
          </div>
        `
          )
          .join("");
      }

      $("modal-view-original-info").hidden = false;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  let addItemsCurrentRentalId = null;
  const addItemsSelectedKits = new Map(); // kit_id -> qty
  const addItemsSelectedComponents = new Map(); // key `${source_kit_id}:${item_id}` -> { source_kit_id, item, qty }
  let allCatalogComponentsList = [];

  function buildAllCatalogComponentsList() {
    if (allCatalogComponentsList.length > 0) return allCatalogComponentsList;
    const list = [];
    for (const kit of (state.catalog?.kits || [])) {
      for (const item of (kit.items || [])) {
        list.push({
          ...item,
          source_kit_id: kit.id,
          source_kit_name: kit.name,
        });
      }
    }
    allCatalogComponentsList = list;
    return allCatalogComponentsList;
  }

  function updateAddItemsSelectionSummary() {
    const kitCountEl = $("tab-kit-selected-count");
    if (kitCountEl) {
      let totalKitQty = 0;
      addItemsSelectedKits.forEach((q) => {
        const num = parseInt(q);
        if (!isNaN(num) && num > 0) totalKitQty += num;
      });
      kitCountEl.textContent = String(totalKitQty);
      kitCountEl.hidden = totalKitQty === 0;
    }

    const itemBadgeEl = $("tab-item-selected-count");
    const count = addItemsSelectedComponents.size;
    let totalCompQty = 0;
    addItemsSelectedComponents.forEach((val) => {
      const num = parseInt(val.qty);
      if (!isNaN(num) && num > 0) totalCompQty += num;
    });

    if (itemBadgeEl) {
      itemBadgeEl.textContent = String(count);
      itemBadgeEl.hidden = count === 0;
    }

    const selCountEl = $("add-items-sel-count");
    if (selCountEl) selCountEl.textContent = String(count);
    const selQtyEl = $("add-items-sel-total-qty");
    if (selQtyEl) selQtyEl.textContent = String(totalCompQty);
  }

  function renderAddKitsTab() {
    const container = $("add-kits-list");
    if (!container) return;
    container.innerHTML = "";

    const kits = state.catalog?.kits || [];
    for (const kit of kits) {
      const card = document.createElement("div");
      const isSelected = addItemsSelectedKits.has(kit.id);
      const currentQty = addItemsSelectedKits.get(kit.id) || "";
      card.className = `kit-card-select ${isSelected ? "selected" : ""}`;
      card.dataset.kitId = kit.id;

      const thumbImgHtml = kit.preview_image
        ? `<div class="kit-card-thumb"><img src="${escapeHtml(kit.preview_image)}" class="kit-card-thumb-img" alt="${escapeHtml(kit.name)}" onerror="this.parentElement.innerHTML='<span class=\\'muted text-xs\\'>Không ảnh</span>'" /></div>`
        : `<div class="kit-card-thumb"><span class="muted text-xs">Không ảnh</span></div>`;

      card.innerHTML = `
        <div class="kit-card-left">
          <input type="checkbox" class="kit-card-checkbox" ${isSelected ? "checked" : ""} />
          ${thumbImgHtml}
          <div class="kit-card-info">
            <span class="kit-card-name" title="${escapeHtml(kit.name)}">${escapeHtml(kit.name)}</span>
            <span class="kit-card-items-count">${kit.total_items || (kit.items || []).length} linh kiện / bộ</span>
          </div>
        </div>
        <div class="kit-card-qty-wrap">
          <span class="kit-card-qty-label">SL:</span>
          <input type="number" class="kit-card-qty-input" min="1" step="1" placeholder="SL" value="${isSelected ? currentQty : ""}" ${isSelected ? "" : "disabled"} />
        </div>
      `;

      const chk = card.querySelector(".kit-card-checkbox");
      const qtyInput = card.querySelector(".kit-card-qty-input");
      const thumbEl = card.querySelector(".kit-card-thumb-img");

      if (thumbEl && kit.preview_image) {
        thumbEl.addEventListener("click", (e) => {
          e.stopPropagation();
          previewImageModal(kit.preview_image, kit.name);
        });
      }

      const toggleCard = (checked) => {
        chk.checked = checked;
        qtyInput.disabled = !checked;
        card.classList.toggle("selected", checked);
        if (checked) {
          qtyInput.value = "";
          qtyInput.placeholder = "SL";
          addItemsSelectedKits.set(kit.id, "");
          qtyInput.focus();
        } else {
          qtyInput.value = "";
          addItemsSelectedKits.delete(kit.id);
        }
        updateAddItemsSelectionSummary();
      };

      chk.addEventListener("change", (e) => {
        toggleCard(e.target.checked);
      });

      card.addEventListener("click", (e) => {
        if (e.target === chk || e.target === qtyInput || e.target === thumbEl) return;
        toggleCard(!chk.checked);
      });

      qtyInput.addEventListener("input", (e) => {
        const val = e.target.value.trim();
        if (addItemsSelectedKits.has(kit.id)) {
          addItemsSelectedKits.set(kit.id, val);
        }
        updateAddItemsSelectionSummary();
      });

      container.appendChild(card);
    }
  }

  function renderAddItemsSelectedPanel() {
    const panel = $("add-items-selected-panel");
    const container = $("add-items-selected-chips");
    if (!panel || !container) return;

    if (addItemsSelectedComponents.size === 0) {
      panel.style.display = "none";
      container.innerHTML = "";
      return;
    }

    panel.style.display = "block";
    container.innerHTML = "";

    addItemsSelectedComponents.forEach((val, key) => {
      const it = val.item;
      const row = document.createElement("div");
      row.className = "selected-chip-row";
      row.dataset.key = key;

      const imgSrc = (it.images && it.images[0]) ? it.images[0] : "";
      const thumbHtml = imgSrc
        ? `<img src="${escapeHtml(imgSrc)}" class="selected-chip-thumb" alt="${escapeHtml(it.name)}" onerror="this.style.display='none'" />`
        : "";

      const codeHtml = it.code
        ? `<span class="item-code-badge">${escapeHtml(it.code)}</span>`
        : "";

      row.innerHTML = `
        <div class="selected-chip-left">
          ${thumbHtml}
          <div class="selected-chip-info">
            <div class="selected-chip-name" title="${escapeHtml(it.name)}">${escapeHtml(it.name)} ${codeHtml}</div>
            <div class="text-sm muted">${escapeHtml(it.source_kit_name)} · Đơn giá: ${formatMoney(it.unit_price || 0)}</div>
          </div>
        </div>
        <div class="selected-chip-right">
          <span style="font-size:12px; font-weight:600; color:#475569;">SL:</span>
          <input type="number" min="1" step="1" class="selected-chip-qty" placeholder="SL" value="${val.qty || ""}" />
          <button type="button" class="btn-remove-chip" title="Bỏ chọn linh kiện này">Xóa</button>
        </div>
      `;

      const chipQtyInput = row.querySelector(".selected-chip-qty");
      const btnRemove = row.querySelector(".btn-remove-chip");

      chipQtyInput.addEventListener("input", (e) => {
        const newQty = e.target.value.trim();
        val.qty = newQty;
        addItemsSelectedComponents.set(key, val);

        // Sync with table row if visible
        const tableRow = $(`add-items-tbody`)?.querySelector(`tr[data-key="${key}"]`);
        if (tableRow) {
          const tableQtyInput = tableRow.querySelector(".item-qty-input");
          if (tableQtyInput) tableQtyInput.value = newQty;
        }
        updateAddItemsSelectionSummary();
      });

      btnRemove.addEventListener("click", () => {
        addItemsSelectedComponents.delete(key);
        renderAddItemsSelectedPanel();

        // Sync with table row if visible
        const tableRow = $(`add-items-tbody`)?.querySelector(`tr[data-key="${key}"]`);
        if (tableRow) {
          const chk = tableRow.querySelector(".item-row-chk");
          const tableQtyInput = tableRow.querySelector(".item-qty-input");
          if (chk) chk.checked = false;
          if (tableQtyInput) {
            tableQtyInput.disabled = true;
            tableQtyInput.value = "";
          }
          tableRow.classList.remove("row-selected");
        }
        updateAddItemsSelectionSummary();
      });

      container.appendChild(row);
    });
  }

  function renderAddItemsCatalogTable() {
    const tbody = $("add-items-tbody");
    if (!tbody) return;
    const all = buildAllCatalogComponentsList();

    const q = normalizeText(($("add-items-search")?.value || "").trim());
    const kitFilter = $("add-items-filter-kit")?.value || "";

    const filtered = all.filter((it) => {
      if (kitFilter && it.source_kit_id !== kitFilter) return false;
      if (q) {
        const hay = normalizeText(`${it.name} ${it.code || ""} ${it.source_kit_name}`);
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    tbody.innerHTML = "";
    const emptyEl = $("add-items-empty");
    if (emptyEl) emptyEl.hidden = filtered.length > 0;

    const limit = Math.min(filtered.length, 300);
    for (let i = 0; i < limit; i++) {
      const it = filtered[i];
      const key = `${it.source_kit_id}:${it.id}`;
      const isSelected = addItemsSelectedComponents.has(key);
      const savedData = addItemsSelectedComponents.get(key);
      const currentQty = savedData ? (savedData.qty || "") : "";

      const tr = document.createElement("tr");
      tr.dataset.key = key;
      if (isSelected) tr.classList.add("row-selected");

      const imgSrc = (it.images && it.images[0]) ? it.images[0] : "";
      const thumbHtml = imgSrc
        ? `<div class="item-thumb-box"><img src="${escapeHtml(imgSrc)}" class="item-thumb-img" alt="${escapeHtml(it.name)}" onerror="this.parentElement.innerHTML='<span class=\\'item-thumb-placeholder\\'>Không ảnh</span>'" /></div>`
        : `<div class="item-thumb-box"><span class="item-thumb-placeholder">Không ảnh</span></div>`;

      const codeHtml = it.code
        ? `<span class="item-code-badge" title="Mã linh kiện: ${escapeHtml(it.code)}">${escapeHtml(it.code)}</span>`
        : "";

      tr.innerHTML = `
        <td style="text-align:center;">
          <input type="checkbox" class="item-row-chk" ${isSelected ? "checked" : ""} style="width:17px; height:17px; cursor:pointer; accent-color:var(--primary);" />
        </td>
        <td style="text-align:center;">${thumbHtml}</td>
        <td>
          <div style="font-weight:600; color:var(--text);">${escapeHtml(it.name)} ${codeHtml}</div>
          <div class="text-sm muted" style="margin-top:2px;">Đơn vị: ${escapeHtml(it.unit || 'cái')}</div>
        </td>
        <td><span class="badge-tag">${escapeHtml(it.source_kit_name)}</span></td>
        <td style="text-align:right; font-weight:600; color:var(--text);">${formatMoney(it.unit_price || 0)}</td>
        <td style="text-align:center;">
          <input type="number" class="item-qty-input" min="1" step="1" placeholder="SL" value="${isSelected ? currentQty : ""}" ${isSelected ? "" : "disabled"} />
        </td>
      `;

      const chk = tr.querySelector(".item-row-chk");
      const qtyInput = tr.querySelector(".item-qty-input");
      const imgEl = tr.querySelector(".item-thumb-img");

      if (imgEl && imgSrc) {
        imgEl.addEventListener("click", (e) => {
          e.stopPropagation();
          previewImageModal(imgSrc, it.name);
        });
      }

      const toggleItem = (checked) => {
        chk.checked = checked;
        qtyInput.disabled = !checked;
        tr.classList.toggle("row-selected", checked);
        if (checked) {
          qtyInput.value = "";
          qtyInput.placeholder = "SL";
          addItemsSelectedComponents.set(key, { source_kit_id: it.source_kit_id, item: it, qty: "" });
          qtyInput.focus();
        } else {
          qtyInput.value = "";
          addItemsSelectedComponents.delete(key);
        }
        renderAddItemsSelectedPanel();
        updateAddItemsSelectionSummary();
      };

      chk.addEventListener("change", (e) => {
        toggleItem(e.target.checked);
      });

      tr.addEventListener("click", (e) => {
        if (e.target === chk || e.target === qtyInput || e.target === imgEl) return;
        toggleItem(!chk.checked);
      });

      qtyInput.addEventListener("input", (e) => {
        const val = e.target.value.trim();
        if (addItemsSelectedComponents.has(key)) {
          const existing = addItemsSelectedComponents.get(key);
          existing.qty = val;
          addItemsSelectedComponents.set(key, existing);
        }
        renderAddItemsSelectedPanel();
        updateAddItemsSelectionSummary();
      });

      tbody.appendChild(tr);
    }
  }

  async function openAddRentalItemsModal(rentalId) {
    addItemsCurrentRentalId = rentalId;
    try {
      const data = await api(`/api/rentals/${rentalId}`);
      const r = data.rental;
      $("add-item-ticket-code").textContent = r.code;

      // Reset selection maps
      addItemsSelectedKits.clear();
      addItemsSelectedComponents.clear();

      // Render Tab 1: Kits
      renderAddKitsTab();

      // Setup Tab 2: Filters and components
      const all = buildAllCatalogComponentsList();
      const kitFilterSelect = $("add-items-filter-kit");
      if (kitFilterSelect) {
        kitFilterSelect.innerHTML = `<option value="">Tất cả các bộ (${all.length} linh kiện)</option>` +
          (state.catalog?.kits || []).map((k) => `<option value="${escapeHtml(k.id)}">${escapeHtml(k.name)} (${(k.items || []).length} LK)</option>`).join("");
      }

      if ($("add-items-search")) $("add-items-search").value = "";
      if (kitFilterSelect) kitFilterSelect.value = "";

      renderAddItemsCatalogTable();
      renderAddItemsSelectedPanel();
      updateAddItemsSelectionSummary();

      // Reset Tab 3
      $("add-custom-name").value = "";
      $("add-custom-qty").value = "";
      $("add-custom-price").value = "";
      $("add-custom-unit").value = "cái";
      $("add-custom-note").value = "";

      // Reset active tab to Tab 1
      document.querySelectorAll(".tab-add-type").forEach((t) => t.classList.toggle("active", t.dataset.tab === "tab-add-kit"));
      $("tab-add-kit").hidden = false;
      $("tab-add-cat-item").hidden = true;
      $("tab-add-custom").hidden = true;

      if ($("add-item-actor")) {
        $("add-item-actor").value = getCurrentStaffName();
      }

      $("modal-add-rental-items").hidden = false;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function submitAddRentalItems() {
    if (!addItemsCurrentRentalId) return;
    const activeTab = document.querySelector(".tab-add-type.active")?.dataset.tab;
    const actor = $("add-item-actor")?.value.trim() || getCurrentStaffName();

    let payload = { actor };
    if (activeTab === "tab-add-kit") {
      if (addItemsSelectedKits.size === 0) {
        showToast("Vui lòng tích chọn ít nhất 1 bộ kit cần thêm", "warning");
        return;
      }
      const kits = [];
      for (const [kit_id, qtyStr] of addItemsSelectedKits.entries()) {
        const qtyNum = parseInt(qtyStr);
        if (isNaN(qtyNum) || qtyNum <= 0) {
          const matchedKit = (state.catalog?.kits || []).find((k) => k.id === kit_id);
          showToast(`Vui lòng nhập số lượng cho bộ ${matchedKit ? matchedKit.name : kit_id}`, "warning");
          return;
        }
        kits.push({ kit_id, qty: qtyNum });
      }
      payload.item_type = "batch_kits";
      payload.kits = kits;
    } else if (activeTab === "tab-add-cat-item") {
      if (addItemsSelectedComponents.size === 0) {
        showToast("Vui lòng tích chọn ít nhất 1 linh kiện lẻ cần thêm", "warning");
        return;
      }
      const items = [];
      for (const val of addItemsSelectedComponents.values()) {
        const qtyNum = parseInt(val.qty);
        if (isNaN(qtyNum) || qtyNum <= 0) {
          showToast(`Vui lòng nhập số lượng cho linh kiện ${val.item.name}`, "warning");
          return;
        }
        items.push({
          source_kit_id: val.source_kit_id,
          item_id: val.item.id,
          qty: qtyNum,
        });
      }
      payload.item_type = "batch_catalog_items";
      payload.items = items;
    } else if (activeTab === "tab-add-custom") {
      payload.item_type = "custom_item";
      payload.name = $("add-custom-name").value.trim();
      const customQty = parseInt($("add-custom-qty").value);
      if (!payload.name) {
        showToast("Vui lòng nhập tên sản phẩm / linh kiện", "error");
        return;
      }
      if (isNaN(customQty) || customQty <= 0) {
        showToast("Vui lòng nhập số lượng hợp lệ cho sản phẩm", "warning");
        return;
      }
      payload.qty = customQty;
      payload.unit_price = Number($("add-custom-price").value || 0);
      payload.unit = $("add-custom-unit").value.trim() || "cái";
      payload.note = $("add-custom-note").value.trim();
    }

    try {
      const res = await api(`/api/rentals/${addItemsCurrentRentalId}/add-items`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("modal-add-rental-items").hidden = true;
      showToast("Đã thêm đồ trả vào đơn thuê thành công!", "success");
      await loadRentals();
      if (state.activeRental && state.activeRental.id === Number(addItemsCurrentRentalId)) {
        state.activeRental = res.rental;
        renderInspectionWorkspace();
      }
    } catch (err) {
      showToast("Lỗi thêm đồ trả: " + err.message, "error");
    }
  }

  async function openCompareAddedKitsModal(rentalId) {
    try {
      const data = await api(`/api/rentals/${rentalId}`);
      const r = data.rental;
      $("compare-kits-ticket-code").textContent = r.code;

      const origBox = $("compare-orig-kits-list");
      const origList = r.original_kits_summary || [];
      if (origList.length === 0) {
        origBox.innerHTML = `<span class="muted text-sm">Không có dữ liệu thiết bị gốc ban đầu.</span>`;
      } else {
        origBox.innerHTML = origList
          .map(
            (k) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; border-bottom:1px solid #e2e8f0; background:#ffffff; border-radius:8px; margin-bottom:6px;">
            <div>
              <strong style="color:var(--text); font-size:13.5px;">${escapeHtml(k.name || k.kit_name || k.display_label)}</strong>
              ${k.unit_price ? `<div class="muted text-xs" style="margin-top:2px;">Đơn giá: ${formatMoney(k.unit_price)}</div>` : ''}
            </div>
            <div>
              <span class="badge-tag" style="font-weight:700; font-size:12.5px; padding:3px 12px; background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; border-radius:999px;">
                Số lượng: ${k.qty || 1} ${escapeHtml(k.unit || 'bộ')}
              </span>
            </div>
          </div>
        `
          )
          .join("");
      }

      const addedBox = $("compare-added-kits-list");
      const addedList = r.added_kits_summary || [];
      if (addedList.length === 0) {
        addedBox.innerHTML = `<span class="muted text-sm">Chưa có linh kiện hoặc bộ nào được thêm bổ sung vào đơn này.</span>`;
      } else {
        addedBox.innerHTML = addedList
          .map(
            (it) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; border-bottom:1px solid #e2e8f0; background:#ffffff; border-radius:8px; margin-bottom:6px;">
            <div>
              <strong style="color:var(--green); font-size:13.5px;">${escapeHtml(it.name || it.item_name || it.kit_name || it.display_label)}</strong>
              ${it.unit_price ? `<div class="muted text-xs" style="margin-top:2px;">Đơn giá: ${formatMoney(it.unit_price)}</div>` : ''}
            </div>
            <div>
              <span class="badge-return badge-returned" style="font-weight:700; font-size:12.5px; padding:3px 12px; border-radius:999px;">
                Số lượng: ${it.qty || 1} ${escapeHtml(it.unit || 'món')}
              </span>
            </div>
          </div>
        `
          )
          .join("");
      }

      $("btn-compare-open-add-modal").onclick = () => {
        $("modal-compare-added-kits").hidden = true;
        openAddRentalItemsModal(r.id);
      };

      $("modal-compare-added-kits").hidden = false;
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // --- Boot ---
  async function init() {
    bindEvents();
    try {
      await Promise.all([loadCatalog(false), loadRentals()]);
      if (state.rentals && state.rentals.length > 0) {
        await switchView("teams");
      }
    } catch (err) {
      showToast("Lỗi khởi tạo dữ liệu: " + err.message, "error");
    }
  }

  init();
})();

