(function () {
  const api = window.ControlCenterAPI;
  if (!api) return;

  const pages = {
    overview: "控制总览",
    video_library: "视频库",
    copywriting_library: "文案库",
    product_library: "商品库",
    mobile_library: "手机库",
    publish_task: "发布任务",
    execution_queue: "执行队列",
    audit_logs: "运行日志",
    system_settings: "系统设置",
  };
  const labels = {
    unused: "未使用",
    assigned: "已分配",
    publishing: "发布中",
    published: "已发布",
    failed: "失败",
    archived: "已归档",
    disabled: "已停用",
    removed: "已移除",
    used: "已使用",
    pending: "待执行",
    ready: "可执行",
    running: "执行中",
    cancelled: "已取消",
    paused: "暂缓",
    online_idle: "在线空闲",
    offline: "离线",
    error: "异常",
    paired: "已配对",
    unpaired: "未配对",
    pairing: "配对中",
    authorization_failed: "授权失败",
    authorized: "已授权",
    unauthorized: "未授权",
    unknown: "未知",
    usb: "USB",
    wireless: "无线",
    scheduled: "预约发布",
    immediate: "立即发布",
    timed: "定时点击",
    dry_run: "演练完成",
    no_task: "暂无任务",
    ok: "正常",
    active: "启用",
    marketing: "营销号",
    showcase: "橱窗号",
  };
  Object.assign(labels, {
    starting: "启动中",
    idle: "空闲",
    stopped: "已停止",
    stale: "已过期",
    completed: "已完成",
    no_phones: "无可用手机",
  });
  const TASK_STATUSES = new Set(["pending", "ready", "running", "published", "dry_run", "failed", "cancelled", "paused", "removed"]);
  const PUBLISH_MODES = new Set(["scheduled", "immediate", "timed"]);
  const filters = {};

  function pageName() {
    if (window.GroupControlShell && typeof window.GroupControlShell.getPage === "function") {
      return window.GroupControlShell.getPage();
    }
    const rawHash = decodeURIComponent(location.hash || "").replace(/^#\/?/, "");
    const page = rawHash.split(/[?&/#]/)[0];
    return pages[page] ? page : "overview";
  }

  function hashParams() {
    const rawHash = decodeURIComponent(location.hash || "").replace(/^#\/?/, "");
    const queryIndex = rawHash.indexOf("?");
    return new URLSearchParams(queryIndex >= 0 ? rawHash.slice(queryIndex + 1) : "");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value || ""));
    return String(value || "").replace(/["\\]/g, "\\$&");
  }

  function text(value, fallback) {
    if (value === undefined || value === null || value === "") return fallback || "-";
    if (Array.isArray(value)) return value.length ? value.join("、") : fallback || "-";
    return String(value);
  }

  function label(value) {
    const raw = String(value || "");
    return labels[raw] || raw || "-";
  }

  function compact(value, size) {
    const raw = text(value);
    const max = size || 18;
    return raw.length > max ? raw.slice(0, max) + "..." : raw;
  }

  function formatDate(value) {
    const raw = String(value || "");
    return raw ? raw.replace("T", " ").replace(/\.\d+/, "").replace(/\+\d\d:\d\d$/, "") : "-";
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!bytes) return "-";
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function formatDuration(value) {
    const seconds = Number(value || 0);
    if (!seconds) return "-";
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  function pill(value) {
    const raw = String(value || "unknown");
    const danger = ["failed", "error", "offline", "disabled", "removed", "unauthorized", "authorization_failed"].includes(raw);
    const warning = ["pending", "ready", "running", "assigned", "paused", "unknown", "unpaired", "pairing"].includes(raw);
    const tone = danger ? "danger" : warning ? "warning" : "ok";
    return `<span class="gc-pill is-${tone}">${escapeHtml(label(raw))}</span>`;
  }

  function taskStatus(row) {
    const direct = String((row && row.status) || "");
    const fallback = String((row && row.task_status) || "");
    if (TASK_STATUSES.has(direct)) return direct;
    if (TASK_STATUSES.has(fallback)) return fallback;
    return direct && !PUBLISH_MODES.has(direct) ? direct : "unknown";
  }

  function button(action, labelText, attrs, tone) {
    const extra = attrs ? " " + attrs : "";
    return `<button class="gc-btn ${tone ? "is-" + tone : ""}" type="button" data-action="${escapeHtml(action)}"${extra}>${escapeHtml(labelText)}</button>`;
  }

  function input(name, placeholder, value, type) {
    return `<input class="gc-input" name="${escapeHtml(name)}" type="${escapeHtml(type || "text")}" value="${escapeHtml(value || "")}" placeholder="${escapeHtml(placeholder || "")}" />`;
  }

  function select(name, options, selected) {
    const items = options
      .map((item) => {
        const value = typeof item === "string" ? item : item.value;
        const labelText = typeof item === "string" ? label(item) : item.label;
        const isSelected = String(value) === String(selected || "") ? " selected" : "";
        return `<option value="${escapeHtml(value)}"${isSelected}>${escapeHtml(labelText)}</option>`;
      })
      .join("");
    return `<select class="gc-input" name="${escapeHtml(name)}">${items}</select>`;
  }

  function field(labelText, control) {
    return `<label class="gc-field"><span>${escapeHtml(labelText)}</span>${control}</label>`;
  }

  function optionList(rows, key, labelBuilder, selected) {
    return (rows || [])
      .map((row) => {
        const value = String(row[key] || "");
        const isSelected = selected && value === String(selected) ? " selected" : "";
        return `<option value="${escapeHtml(value)}"${isSelected}>${escapeHtml(labelBuilder(row))}</option>`;
      })
      .join("");
  }

  function table(columns, rows, empty) {
    if (!rows || !rows.length) return `<div class="gc-empty">${escapeHtml(empty || "暂无数据")}</div>`;
    const head = columns.map((column) => `<th>${column.labelHtml || escapeHtml(column.label)}</th>`).join("");
    const body = rows
      .map((row) => `<tr>${columns.map((column) => `<td>${column.render ? column.render(row) : escapeHtml(text(row[column.key]))}</td>`).join("")}</tr>`)
      .join("");
    return `<div class="gc-table-wrap"><table class="gc-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function statGrid(items) {
    return `<div class="gc-grid">${items.map((item) => `<div class="gc-stat"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("")}</div>`;
  }

  function section(title, content, actions) {
    return `<section class="gc-section"><div class="gc-section-title"><strong>${escapeHtml(title)}</strong><div>${actions || ""}</div></div>${content}</section>`;
  }

  function formData(form) {
    return new FormData(form);
  }

  function formValue(form, name) {
    return String(formData(form).get(name) || "").trim();
  }

  function formBool(form, name) {
    return formData(form).get(name) === "on";
  }

  function folderNameFromPath(path) {
    const parts = String(path || "").replace(/\\/g, "/").split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }

  function parseTags(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function formEntries(form) {
    const data = new FormData(form);
    const result = {};
    data.forEach((value, key) => {
      result[key] = String(value || "").trim();
    });
    return result;
  }

  function filterForm(page, inner) {
    return `<form class="gc-form" data-form="filter" data-page="${escapeHtml(page)}">${inner}<button class="gc-btn is-primary" type="submit">应用筛选</button>${button("clear-filter", "重置", ` data-page="${escapeHtml(page)}"`)}</form>`;
  }

  function jsonPre(data) {
    return `<pre class="gc-code">${escapeHtml(JSON.stringify(data || {}, null, 2))}</pre>`;
  }

  function normalizeQueueCandidates(rows) {
    return (rows || []).map((row) => {
      if (!row || !row.task) return row || {};
      return Object.assign({}, row.task, { eligible: row.eligible, queue_reason: row.reason });
    });
  }

  function queueSelectAllHeader() {
    return `<label class="gc-queue-all"><input class="gc-select-box" data-queue-select-all type="checkbox" /> 全选</label>`;
  }

  function queueTaskSelector(row) {
    const taskId = String(row.task_id || "");
    return `<input class="gc-select-box" data-queue-task type="checkbox" value="${escapeHtml(taskId)}" aria-label="选择任务 ${escapeHtml(taskId)}" />`;
  }

  function selectedQueueTaskIds() {
    return Array.from(document.querySelectorAll("[data-queue-task]:checked"))
      .map((item) => String(item.value || "").trim())
      .filter(Boolean);
  }

  function updateQueueSelectionState() {
    const boxes = Array.from(document.querySelectorAll("[data-queue-task]"));
    const checkedCount = boxes.filter((box) => box.checked).length;
    const selectAll = document.querySelector("[data-queue-select-all]");
    if (selectAll) {
      selectAll.checked = boxes.length > 0 && checkedCount === boxes.length;
      selectAll.indeterminate = checkedCount > 0 && checkedCount < boxes.length;
    }
  }

  function bulkSelectAllHeader(kind) {
    return `<label class="gc-queue-all"><input class="gc-select-box" data-bulk-select-all="${escapeHtml(kind)}" type="checkbox" /> 全选</label>`;
  }

  function bulkSelector(kind, id, labelText) {
    return `<input class="gc-select-box" data-bulk-item="${escapeHtml(kind)}" type="checkbox" value="${escapeHtml(id)}" aria-label="选择${escapeHtml(labelText || id)}" />`;
  }

  function selectedBulkIds(kind) {
    return Array.from(document.querySelectorAll(`[data-bulk-item="${cssEscape(kind)}"]:checked`))
      .map((item) => String(item.value || "").trim())
      .filter(Boolean);
  }

  function updateBulkSelectionState(kind) {
    const kinds = kind ? [kind] : Array.from(new Set(Array.from(document.querySelectorAll("[data-bulk-item]")).map((item) => item.dataset.bulkItem)));
    kinds.forEach((itemKind) => {
      if (!itemKind) return;
      const boxes = Array.from(document.querySelectorAll(`[data-bulk-item="${cssEscape(itemKind)}"]`));
      const checkedCount = boxes.filter((box) => box.checked).length;
      const selectAllBoxes = Array.from(document.querySelectorAll(`[data-bulk-select-all="${cssEscape(itemKind)}"]`));
      selectAllBoxes.forEach((selectAll) => {
        selectAll.checked = boxes.length > 0 && checkedCount === boxes.length;
        selectAll.indeterminate = checkedCount > 0 && checkedCount < boxes.length;
      });
    });
  }

  function injectStyles() {
    if (document.getElementById("gc-ui-bridge-styles")) return;
    const style = document.createElement("style");
    style.id = "gc-ui-bridge-styles";
    style.textContent = `
      .gc-app { color: #18202f; display: flex; flex-direction: column; gap: 16px; }
      .gc-panel, .gc-section { background: #fff; border: 1px solid #dfe4ee; border-radius: 8px; box-shadow: 0 10px 30px rgba(15,23,42,.05); overflow: hidden; }
      .gc-head, .gc-section-title { align-items: center; border-bottom: 1px solid #e7ebf2; display: flex; gap: 12px; justify-content: space-between; padding: 14px 16px; }
      .gc-head h2 { color: #101828; font-size: 20px; font-weight: 850; margin: 0; }
      .gc-head span, .gc-muted { color: #667085; font-size: 12px; }
      .gc-body { display: flex; flex-direction: column; gap: 16px; padding: 16px; }
      .gc-actions, .gc-row-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
      .gc-row-actions { justify-content: flex-start; }
      .gc-btn { align-items: center; background: #f8fafc; border: 1px solid #cfd7e3; border-radius: 6px; color: #344054; cursor: pointer; display: inline-flex; font-size: 12px; font-weight: 800; min-height: 30px; padding: 6px 10px; }
      .gc-btn:hover { background: #eef4ff; border-color: #84adff; }
      .gc-btn:disabled { cursor: wait; opacity: .65; }
      .gc-btn.is-primary { background: #155eef; border-color: #155eef; color: #fff; }
      .gc-btn.is-danger { background: #d92d20; border-color: #d92d20; color: #fff; }
      .gc-btn.is-warning { background: #f79009; border-color: #f79009; color: #111827; }
      .gc-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
      .gc-stat { background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 8px; padding: 12px; }
      .gc-stat span { color: #667085; display: block; font-size: 12px; margin-bottom: 6px; }
      .gc-stat strong { color: #101828; display: block; font-size: 24px; line-height: 1; }
      .gc-form { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); padding: 14px 16px; }
      .gc-form.is-compact { padding: 0; }
      .gc-stack { display: flex; flex-direction: column; gap: 12px; }
      .gc-stack .gc-form { border: 1px solid #e4e7ec; border-radius: 8px; }
      .gc-span { grid-column: 1 / -1; }
      .gc-field { color: #46556c; display: flex; flex-direction: column; font-size: 12px; font-weight: 700; gap: 5px; }
      .gc-advanced { border: 1px solid #e4e7ec; border-radius: 8px; grid-column: 1 / -1; padding: 10px; }
      .gc-advanced summary { color: #344054; cursor: pointer; font-size: 12px; font-weight: 850; }
      .gc-advanced .gc-form { margin-top: 10px; }
      .gc-input-action { align-items: end; display: grid; gap: 8px; grid-template-columns: minmax(0, 1fr) auto; }
      .gc-input, .gc-textarea { background: #fff; border: 1px solid #cfd7e3; border-radius: 6px; color: #18202f; font-size: 12px; min-height: 34px; padding: 7px 9px; width: 100%; }
      .gc-textarea { min-height: 110px; resize: vertical; }
      .gc-input:focus, .gc-textarea:focus { border-color: #155eef; outline: 0; }
      .gc-check { align-items: center; background: #fff; border: 1px solid #cfd7e3; border-radius: 6px; display: flex; font-size: 12px; gap: 8px; min-height: 34px; padding: 7px 9px; }
      .gc-select-cell { text-align: center; width: 48px; }
      .gc-select-box { accent-color: #155eef; height: 16px; width: 16px; }
      .gc-queue-all { align-items: center; display: inline-flex; gap: 6px; }
      .gc-table-wrap { overflow-x: auto; }
      .gc-table { border-collapse: collapse; min-width: 100%; width: 100%; }
      .gc-table th { background: #f9fafb; border-bottom: 1px solid #e4e7ec; color: #667085; font-size: 12px; font-weight: 900; padding: 10px 12px; text-align: left; white-space: nowrap; }
      .gc-table td { border-bottom: 1px solid #f0f2f5; color: #344054; font-size: 12px; line-height: 1.45; max-width: 360px; padding: 10px 12px; vertical-align: top; }
      .gc-table tr:last-child td { border-bottom: 0; }
      .gc-pill { align-items: center; border-radius: 999px; display: inline-flex; font-size: 12px; font-weight: 850; min-height: 24px; padding: 4px 9px; white-space: nowrap; }
      .gc-pill.is-ok { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; }
      .gc-pill.is-warning { background: #fffaeb; border: 1px solid #fedf89; color: #b54708; }
      .gc-pill.is-danger { background: #fef3f2; border: 1px solid #fecdca; color: #b42318; }
      .gc-empty, .gc-error, .gc-note { border-radius: 8px; font-size: 13px; line-height: 1.5; padding: 14px; }
      .gc-empty { background: #f8fafc; border: 1px dashed #cfd7e3; color: #667085; }
      .gc-error { background: #fef3f2; border: 1px solid #fecdca; color: #b42318; }
      .gc-note { background: #eff8ff; border: 1px solid #b2ddff; color: #175cd3; }
      .gc-code { background: #101828; border-radius: 6px; color: #f2f4f7; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; line-height: 1.6; overflow: auto; padding: 12px; white-space: pre-wrap; }
      .gc-modal, .gc-drawer { background: rgba(15,23,42,.36); bottom: 0; left: 0; position: fixed; right: 0; top: 0; z-index: 1600; }
      .gc-modal[hidden], .gc-drawer[hidden] { display: none !important; }
      .gc-modal { align-items: center; display: flex; justify-content: center; padding: 20px; }
      .gc-dialog { background: #fff; border: 1px solid #dfe4ee; border-radius: 8px; box-shadow: 0 24px 80px rgba(15,23,42,.22); max-height: calc(100vh - 40px); overflow: hidden; width: min(780px, 100%); }
      .gc-dialog-head, .gc-drawer-head { align-items: center; border-bottom: 1px solid #edf1f7; display: flex; justify-content: space-between; padding: 14px 16px; }
      .gc-dialog-head h3, .gc-drawer-head h3 { font-size: 16px; font-weight: 900; margin: 0; }
      .gc-dialog-body, .gc-drawer-body { max-height: calc(100vh - 150px); overflow: auto; padding: 16px; }
      .gc-drawer { display: flex; justify-content: flex-end; }
      .gc-drawer-panel { background: #fff; border-left: 1px solid #dfe4ee; height: 100%; width: min(720px, 100%); }
      .gc-toast-region { bottom: 18px; display: flex; flex-direction: column; gap: 8px; position: fixed; right: 18px; width: min(380px, calc(100vw - 36px)); z-index: 2000; }
      .gc-toast { background: #101828; border-radius: 8px; color: #fff; font-size: 13px; line-height: 1.45; padding: 10px 12px; }
      .gc-qr-panel { align-items: center; display: grid; gap: 14px; grid-column: 1 / -1; justify-items: center; padding: 6px 0; }
      .gc-qr-panel img { border: 1px solid #d0d5dd; border-radius: 8px; height: min(320px, 70vw); image-rendering: pixelated; width: min(320px, 70vw); }
      .gc-qr-meta { color: #667085; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; text-align: center; }
      @media (max-width: 720px) { .gc-head, .gc-section-title { align-items: stretch; flex-direction: column; } .gc-actions { justify-content: flex-start; } .gc-grid { grid-template-columns: 1fr 1fr; } }
    `;
    document.head.appendChild(style);
  }

  function shell(title) {
    return `<section class="gc-panel"><div class="gc-head"><div><h2>${escapeHtml(title)}</h2><span>${escapeHtml(api.baseUrl)}</span></div><div class="gc-actions">${button("refresh", "刷新")}</div></div><div class="gc-body" data-gc-body>${loading()}</div></section>`;
  }

  function loading() {
    return `<div class="gc-empty">正在读取控制中心数据...</div>`;
  }

  function showToast(message, tone) {
    let region = document.querySelector(".gc-toast-region");
    if (!region) {
      region = document.createElement("div");
      region.className = "gc-toast-region";
      document.body.appendChild(region);
    }
    const item = document.createElement("div");
    item.className = "gc-toast";
    if (tone === "danger") item.style.background = "#b42318";
    if (tone === "ok") item.style.background = "#05603a";
    item.textContent = message;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 3200);
  }

  function ensureOverlays() {
    if (!document.querySelector("[data-gc-modal]")) {
      const modal = document.createElement("div");
      modal.className = "gc-modal";
      modal.hidden = true;
      modal.dataset.gcModal = "true";
      document.body.appendChild(modal);
    }
    if (!document.querySelector("[data-gc-drawer]")) {
      const drawer = document.createElement("div");
      drawer.className = "gc-drawer";
      drawer.hidden = true;
      drawer.dataset.gcDrawer = "true";
      document.body.appendChild(drawer);
    }
  }

  function openModal(title, body) {
    ensureOverlays();
    const modal = document.querySelector("[data-gc-modal]");
    modal.hidden = false;
    modal.innerHTML = `<div class="gc-dialog"><div class="gc-dialog-head"><h3>${escapeHtml(title)}</h3>${button("close-modal", "关闭")}</div><div class="gc-dialog-body">${body}</div></div>`;
    bindForms(modal, pageName());
  }

  function openDrawer(title, body) {
    ensureOverlays();
    const drawer = document.querySelector("[data-gc-drawer]");
    drawer.hidden = false;
    drawer.innerHTML = `<aside class="gc-drawer-panel"><div class="gc-drawer-head"><h3>${escapeHtml(title)}</h3>${button("close-drawer", "关闭")}</div><div class="gc-drawer-body">${body}</div></aside>`;
  }

  function closeOverlays() {
    document.querySelectorAll("[data-gc-modal],[data-gc-drawer]").forEach((node) => {
      node.hidden = true;
      node.innerHTML = "";
    });
  }

  async function renderOverview() {
    const [summary, queueRaw, failures, adb] = await Promise.all([
      api.summary(),
      api.queuePreview({ ignore_phone_status: false, allow_overdue: false, limit: 6 }),
      api.failures({ limit: 5 }),
      api.adbStatus(),
    ]);
    const queue = normalizeQueueCandidates(queueRaw);
    return [
      statGrid([
        { label: "视频", value: summary.videos || 0 },
        { label: "文案", value: summary.captions || 0 },
        { label: "商品", value: summary.products || 0 },
        { label: "手机", value: summary.phones || 0 },
        { label: "任务", value: summary.release_tasks || 0 },
        { label: "待执行", value: summary.pending_tasks || 0 },
        { label: "失败", value: summary.failed_tasks || 0 },
      ]),
      section(
        "即将执行",
        table(
          [
            { label: "任务", render: (row) => `<span class="gc-muted">${escapeHtml(compact(row.task_id))}</span>` },
            { label: "视频", render: (row) => escapeHtml(text(row.video_title || row.video_file_name)) },
            { label: "手机", render: (row) => escapeHtml(text(row.phone_name || row.phone_adb_serial)) },
            { label: "时间", render: (row) => escapeHtml(formatDate(row.scheduled_at)) },
            { label: "模式", render: (row) => escapeHtml(label(row.publish_mode)) },
            { label: "状态", render: (row) => pill(taskStatus(row)) },
          ],
          queue,
          "当前没有可预览的任务"
        )
      ),
      section(
        "最近失败",
        table(
          [
            { label: "日志", render: (row) => escapeHtml(compact(row.log_id || row.task_id)) },
            { label: "任务", render: (row) => escapeHtml(compact(row.task_id)) },
            { label: "原因", render: (row) => escapeHtml(text(row.failure_reason || row.result || row.error)) },
            { label: "时间", render: (row) => escapeHtml(formatDate(row.created_at || row.started_at || row.finished_at)) },
          ],
          failures,
          "还没有失败记录"
        )
      ),
      section("ADB 状态", table([{ label: "来源", render: () => escapeHtml(label(adb.source)) }, { label: "端口", render: () => escapeHtml(text(adb.server_port || "5037")) }, { label: "服务", render: () => pill(adb.service_status) }, { label: "工具", render: () => pill(adb.platform_tools_status) }, { label: "设备", render: () => escapeHtml(String((adb.devices || []).length)) }], [adb])),
    ].join("");
  }

  async function renderVideos() {
    const f = filters.video_library || {};
    const rows = await api.videos({ limit: f.limit || 200, search: f.search, status: f.status, tag: f.tag, batch: f.batch, include_removed: f.status === "removed" });
    const actions = [button("open-video-import", "导入视频", "", "primary"), button("open-video-scan", "扫描文件夹"), button("video-bulk-remove", "删除选中", "", "danger")].join("");
    const filter = filterForm(
      "video_library",
      `${input("search", "搜索编号、标题、文件名", f.search)}${select("status", ["", "unused", "assigned", "publishing", "published", "failed", "archived", "disabled", "removed"], f.status)}${input("tag", "标签", f.tag)}${input("batch", "批次", f.batch)}${input("limit", "条数", f.limit || "200", "number")}`
    );
    return section("筛选", filter) + section(
      "视频资产",
      table(
        [
          { label: "选择", labelHtml: bulkSelectAllHeader("video"), render: (row) => row.status === "removed" ? "" : bulkSelector("video", row.video_id, row.title || row.file_name || row.video_id) },
          { label: "编号", render: (row) => `<span class="gc-muted">${escapeHtml(compact(row.video_id))}</span>` },
          { label: "标题 / 文件", render: (row) => `${escapeHtml(text(row.title || row.file_name))}<div class="gc-muted">${escapeHtml(text(row.file_name || row.file_path))}</div>` },
          { label: "标签", render: (row) => escapeHtml(text(row.tags)) },
          { label: "批次", render: (row) => escapeHtml(text(row.batch_name)) },
          { label: "时长", render: (row) => escapeHtml(formatDuration(row.duration_seconds)) },
          { label: "大小", render: (row) => escapeHtml(formatBytes(row.file_size)) },
          { label: "状态", render: (row) => pill(row.status) },
          {
            label: "操作",
            render: (row) => videoRowActions(row),
          },
        ],
        rows,
        "视频库还没有视频"
      ),
      actions
    );
  }

  function videoRowActions(row) {
    const idAttr = ` data-id="${escapeHtml(row.video_id)}"`;
    const createTask = row.status === "removed" ? "" : button("task-from-video", "生成任务", idAttr, "primary");
    const remove = row.status === "removed" ? "" : button("video-remove", "删除", idAttr, "danger");
    return `<div class="gc-row-actions">${button("view-video", "详情", idAttr)}${button("open-video-status", "状态", `${idAttr} data-status="${escapeHtml(row.status)}"`)}${createTask}${remove}</div>`;
  }

  async function renderCaptions() {
    const f = filters.copywriting_library || {};
    const [rows, videos, bindings] = await Promise.all([api.captions({ limit: f.limit || 200, search: f.search, status: f.status, tag: f.tag, platform: f.platform, include_removed: f.status === "removed" }), api.videos({ limit: 200 }), api.captionBindings({})]);
    const actions = [button("open-caption-create", "新增文案", "", "primary"), button("open-caption-import", "批量导入"), button("open-caption-bind", "绑定视频"), button("caption-bulk-remove", "删除选中", "", "danger")].join("");
    const filter = filterForm(
      "copywriting_library",
      `${input("search", "搜索内容、编号、备注", f.search)}${select("status", ["", "unused", "assigned", "used", "disabled", "removed"], f.status)}${input("tag", "标签", f.tag)}${input("platform", "平台", f.platform)}${input("limit", "条数", f.limit || "200", "number")}`
    );
    const captionTable = table(
      [
        { label: "选择", labelHtml: bulkSelectAllHeader("caption"), render: (row) => row.status === "removed" ? "" : bulkSelector("caption", row.caption_id, row.caption_id) },
        { label: "编号", render: (row) => `<span class="gc-muted">${escapeHtml(compact(row.caption_id))}</span>` },
        { label: "内容", render: (row) => escapeHtml(compact(row.content, 96)) },
        { label: "平台", render: (row) => escapeHtml(text(row.platform)) },
        { label: "标签", render: (row) => escapeHtml(text(row.tags)) },
        { label: "状态", render: (row) => pill(row.status) },
        {
          label: "操作",
          render: (row) => captionRowActions(row),
        },
      ],
      rows,
      "文案库还没有内容"
    );
    const bindingTable = table(
      [
        { label: "绑定", render: (row) => escapeHtml(compact(row.binding_id)) },
        { label: "文案", render: (row) => escapeHtml(compact(row.caption_id)) },
        { label: "视频", render: (row) => escapeHtml(text(row.video_title || row.file_name || row.video_id)) },
        { label: "备注", render: (row) => escapeHtml(text(row.note)) },
      ],
      bindings,
      "还没有绑定关系"
    );
    window.__gcOptions = { captions: rows, videos };
    return section("筛选", filter) + section("文案列表", captionTable, actions) + section("绑定关系", bindingTable);
  }

  function captionRowActions(row) {
    const idAttr = ` data-id="${escapeHtml(row.caption_id)}"`;
    const remove = row.status === "removed" ? "" : button("caption-remove", "删除", idAttr, "danger");
    return `<div class="gc-row-actions">${button("view-caption", "详情", idAttr)}${button("open-caption-edit", "编辑", idAttr)}${button("open-caption-status", "状态", `${idAttr} data-status="${escapeHtml(row.status)}"`)}${remove}</div>`;
  }

  async function renderProducts() {
    const f = filters.product_library || {};
    const rows = await api.products({ limit: f.limit || 200, search: f.search, status: f.status, account_scope: f.account_scope, include_removed: f.status === "removed" });
    const actions = button("open-product-create", "新增商品", "", "primary");
    const filter = filterForm(
      "product_library",
      `${input("search", "搜索商品、标题、发布名称", f.search)}${select("status", ["", "active", "disabled", "removed"], f.status)}${input("account_scope", "账号范围", f.account_scope)}${input("limit", "条数", f.limit || "200", "number")}`
    );
    const productTable = table(
      [
        { label: "编号", render: (row) => `<span class="gc-muted">${escapeHtml(compact(row.product_id))}</span>` },
        { label: "商品", render: (row) => `${escapeHtml(text(row.title))}<div class="gc-muted">${escapeHtml(text(row.product_link))}</div>` },
        { label: "搜索标题", render: (row) => escapeHtml(text(row.search_title)) },
        { label: "发布名称", render: (row) => escapeHtml(text(row.publish_name)) },
        { label: "账号范围", render: (row) => escapeHtml(text(row.account_scope)) },
        { label: "状态", render: (row) => pill(row.status) },
        { label: "操作", render: (row) => productRowActions(row) },
      ],
      rows,
      "商品库还没有商品"
    );
    window.__gcOptions = Object.assign({}, window.__gcOptions || {}, { products: rows });
    return section("筛选", filter) + section("商品预设", productTable, actions);
  }

  function productRowActions(row) {
    const idAttr = ` data-id="${escapeHtml(row.product_id)}"`;
    const remove = row.status === "removed" ? "" : button("product-remove", "删除", idAttr, "danger");
    return `<div class="gc-row-actions">${button("view-product", "详情", idAttr)}${button("open-product-edit", "编辑", idAttr)}${button("open-product-status", "状态", `${idAttr} data-status="${escapeHtml(row.status)}"`)}${remove}</div>`;
  }

  async function renderPhones() {
    const f = filters.mobile_library || {};
    const [phones, adb, monitor] = await Promise.all([
      api.phones({ limit: f.limit || 200, search: f.search, status: f.status, pairing_status: f.pairing_status, connection_mode: f.connection_mode }),
      api.adbStatus(),
      api.usbMonitor().catch(() => ({})),
    ]);
    const actions = [button("open-phone-create", "手动添加", "", "primary"), button("open-adb-register", "USB 检测"), button("phone-usb-refresh", "检测已登记USB"), button("phone-health-all", "全量检查")].join("");
    const filter = filterForm(
      "mobile_library",
      `${input("search", "搜索手机、序列号、账号", f.search)}${select("status", ["", "online_idle", "running", "offline", "error", "disabled"], f.status)}${select("pairing_status", ["", "unpaired", "pairing", "paired", "authorization_failed"], f.pairing_status)}${select("connection_mode", ["", "usb"], f.connection_mode)}${input("limit", "条数", f.limit || "200", "number")}`
    );
    const adbBlock = table(
      [
        { label: "ADB 路径", render: () => `<span class="gc-muted">${escapeHtml(text(adb.adb_path))}</span>` },
        { label: "端口", render: () => escapeHtml(text(adb.server_port || "5037")) },
        { label: "服务", render: () => pill(adb.service_status) },
        { label: "工具", render: () => pill(adb.platform_tools_status) },
        { label: "设备", render: () => escapeHtml(String((adb.devices || []).length)) },
        { label: "自动检测", render: () => `${pill((monitor && monitor.status) || "unknown")}<div class="gc-muted">${escapeHtml(formatDate((monitor && monitor.last_checked_at) || ""))}</div>` },
      ],
      [adb]
    );
    const phoneTable = table(
      [
        { label: "编号", render: (row) => escapeHtml(compact(row.phone_id)) },
        { label: "名称", render: (row) => escapeHtml(text(row.device_name)) },
        { label: "序列号", render: (row) => `<span class="gc-muted">${escapeHtml(text(row.adb_serial))}</span>` },
        { label: "账号", render: (row) => escapeHtml(text(row.account_name)) },
        { label: "账号类型", render: (row) => escapeHtml(label(row.account_type || "marketing")) },
        { label: "连接", render: (row) => escapeHtml(label(row.connection_mode)) },
        { label: "授权", render: (row) => pill(row.authorization_status) },
        { label: "状态", render: (row) => pill(row.current_status) },
        {
          label: "操作",
          render: (row) => phoneRowActions(row),
        },
      ],
      phones,
      "手机库还没有登记手机"
    );
    return section("筛选", filter) + section("ADB 工具", adbBlock) + section("手机列表", phoneTable, actions);
  }

  function phoneRowActions(row) {
    const idAttr = ` data-id="${escapeHtml(row.phone_id)}"`;
    const toggle = row.current_status === "disabled" ? button("phone-enable", "启用", idAttr, "primary") : button("phone-disable", "停用", idAttr, "warning");
    const remove = row.current_status === "running" ? "" : button("phone-remove", "移除", idAttr, "danger");
    return `<div class="gc-row-actions">${button("view-phone", "详情", idAttr)}${button("open-phone-edit", "编辑", idAttr)}${button("phone-health", "检查", idAttr)}${button("phone-checks", "历史", idAttr)}${toggle}${remove}</div>`;
  }

  function phoneOptionLabel(row) {
    const name = compact(row.device_name || row.adb_serial, 34);
    const status = row.current_status || "unknown";
    const accountType = row.account_type || "marketing";
    return `${row.phone_id} - ${name} - ${label(status)} - ${label(accountType)}`;
  }

  function productOptionLabel(row) {
    return `${row.product_id} - ${compact(row.title || row.search_title, 42)} - ${compact(row.search_title, 30)}`;
  }

  function taskCanBeRemoved(row) {
    const status = String((row && row.status) || "");
    return status !== "running" && status !== "removed";
  }

  async function renderTasks() {
    const f = filters.publish_task || {};
    const [rawTasks, videos, captions, phones, bindings, products] = await Promise.all([
      api.tasks({ limit: f.limit || 200, search: f.search, status: f.status, phone_id: f.phone_id, video_id: f.video_id, date_from: f.date_from, date_to: f.date_to, include_removed: f.status === "removed" }),
      api.videos({ limit: 500 }),
      api.captions({ limit: 500 }),
      api.phones({ limit: 500 }),
      api.captionBindings({}),
      api.products({ limit: 500, status: "active" }),
    ]);
    const tasks = (rawTasks || []).filter((row) => f.status === "removed" || String(row.status || "") !== "removed");
    window.__gcOptions = { videos, captions, phones, bindings, products };
    const actions = [button("open-task-create", "新建任务", "", "primary"), button("open-task-from-binding", "从绑定创建"), button("open-task-import", "CSV 导入"), button("task-bulk-remove", "删除选中", "", "danger")].join("");
    const filter = filterForm(
      "publish_task",
      `${input("search", "搜索任务、视频、手机", f.search)}${select("status", ["", "pending", "ready", "running", "published", "dry_run", "failed", "cancelled", "paused", "removed"], f.status)}${input("phone_id", "手机编号", f.phone_id)}${input("video_id", "视频编号", f.video_id)}${input("date_from", "开始日期", f.date_from, "date")}${input("date_to", "结束日期", f.date_to, "date")}${input("limit", "条数", f.limit || "200", "number")}`
    );
    return section("筛选", filter) + section(
      "发布任务",
      table(
        [
          { label: "选择", labelHtml: bulkSelectAllHeader("task"), render: (row) => taskCanBeRemoved(row) ? bulkSelector("task", row.task_id, row.task_id) : "" },
          { label: "编号", render: (row) => escapeHtml(compact(row.task_id)) },
          { label: "视频", render: (row) => escapeHtml(text(row.video_title || row.video_file_name)) },
          { label: "文案", render: (row) => escapeHtml(compact(row.caption_preview || row.caption_content, 80)) },
          { label: "手机", render: (row) => escapeHtml(text(row.phone_name || row.phone_adb_serial)) },
          { label: "商品", render: (row) => escapeHtml(text(row.product_title || row.product_search_title || row.product_name)) },
          { label: "时间", render: (row) => escapeHtml(formatDate(row.scheduled_at)) },
          { label: "模式", render: (row) => escapeHtml(label(row.publish_mode)) },
          { label: "状态", render: (row) => pill(row.status) },
          {
            label: "操作",
            render: (row) => `<div class="gc-row-actions">${button("view-task", "详情", ` data-id="${escapeHtml(row.task_id)}"`)}${button("open-task-reschedule", "改时间", ` data-id="${escapeHtml(row.task_id)}"`)}${button("open-task-assign", "改手机", ` data-id="${escapeHtml(row.task_id)}"`)}${button("open-task-status", "状态", ` data-id="${escapeHtml(row.task_id)}" data-status="${escapeHtml(row.status)}"`)}${button("task-requeue", "重新入队", ` data-id="${escapeHtml(row.task_id)}" data-status="${escapeHtml(row.status)}"`)}${button("task-cancel", "取消", ` data-id="${escapeHtml(row.task_id)}"`, "danger")}</div>`,
          },
        ],
        tasks,
        "还没有发布任务"
      ),
      actions
    );
  }

  async function renderQueue() {
    const raw = await api.queuePreview({ ignore_phone_status: false, allow_overdue: false, limit: 200 });
    const rows = normalizeQueueCandidates(raw);
    const controls = `
      <form class="gc-form" data-form="queue-run">
        ${input("timezone", "时区", "Asia/Shanghai")}
        ${input("preparation_window_minutes", "准备窗口分钟", "60", "number")}
        ${input("timeout_seconds", "超时秒数", "7200", "number")}
        <label class="gc-check"><input name="ignore_phone_status" type="checkbox" /> 忽略手机空闲状态</label>
        <label class="gc-check"><input name="allow_overdue" type="checkbox" /> 允许过期任务</label>
        <label class="gc-check"><input name="pipeline_dry_run" type="checkbox" /> 流水线演练</label>
        <label class="gc-check"><input name="allow_publish" type="checkbox" /> 允许真实发布</label>
        <button class="gc-btn is-primary" type="submit">执行队列</button>
        ${button("queue-claim", "调试锁定")}
      </form>
      <div class="gc-note" data-result="queue"></div>
    `;
    const list = table(
      [
        { label: "选择", labelHtml: queueSelectAllHeader(), render: (row) => queueTaskSelector(row) },
        { label: "任务", render: (row) => escapeHtml(compact(row.task_id)) },
        { label: "视频", render: (row) => escapeHtml(text(row.video_title || row.video_file_name)) },
        { label: "手机", render: (row) => escapeHtml(text(row.phone_name || row.phone_adb_serial)) },
        { label: "计划时间", render: (row) => escapeHtml(formatDate(row.scheduled_at)) },
        { label: "模式", render: (row) => escapeHtml(label(row.publish_mode)) },
        { label: "状态", render: (row) => pill(taskStatus(row)) },
        { label: "可执行", render: (row) => (row.eligible ? pill("ok") : pill("failed")) },
        { label: "说明", render: (row) => escapeHtml(text(row.queue_reason)) },
      ],
      rows,
      "当前没有可执行队列任务"
    );
    return section("执行控制", controls) + section("队列预览", list);
  }

  async function renderLogs() {
    const f = filters.audit_logs || {};
    const [logs, failures] = await Promise.all([api.logs({ limit: f.limit || 200, search: f.search, result: f.result, task_id: f.task_id, phone_id: f.phone_id }), api.failures({ limit: 50 })]);
    const filter = filterForm(
      "audit_logs",
      `${input("search", "搜索日志、任务、手机、视频", f.search)}${select("result", ["", "running", "published", "failed", "dry_run"], f.result)}${input("task_id", "任务编号", f.task_id)}${input("phone_id", "手机编号", f.phone_id)}${input("limit", "条数", f.limit || "200", "number")}`
    );
    const logTable = table(
      [
        { label: "选择", labelHtml: bulkSelectAllHeader("log"), render: (row) => bulkSelector("log", row.log_id, row.log_id) },
        { label: "日志", render: (row) => escapeHtml(compact(row.log_id)) },
        { label: "任务", render: (row) => escapeHtml(compact(row.task_id)) },
        { label: "手机", render: (row) => escapeHtml(text(row.phone_name || row.phone_id)) },
        { label: "结果", render: (row) => pill(row.result || row.status) },
        { label: "时间", render: (row) => escapeHtml(formatDate(row.started_at || row.created_at || row.finished_at)) },
        {
          label: "操作",
          render: (row) => `<div class="gc-row-actions">${button("view-log", "详情", ` data-id="${escapeHtml(row.log_id)}"`)}${button("log-artifacts", "产物", ` data-id="${escapeHtml(row.log_id)}"`)}${button("log-tail", "输出", ` data-id="${escapeHtml(row.log_id)}"`)}${button("task-report", "任务报告", ` data-id="${escapeHtml(row.task_id)}"`)}</div>`,
        },
      ],
      logs,
      "还没有运行日志"
    );
    const failureTable = table(
      [
        { label: "选择", labelHtml: bulkSelectAllHeader("log"), render: (row) => bulkSelector("log", row.log_id, row.log_id) },
        { label: "日志", render: (row) => escapeHtml(compact(row.log_id)) },
        { label: "任务", render: (row) => escapeHtml(compact(row.task_id)) },
        { label: "原因", render: (row) => escapeHtml(text(row.failure_reason || row.task_failure_reason || row.result)) },
        { label: "时间", render: (row) => escapeHtml(formatDate(row.started_at || row.created_at || row.finished_at)) },
      ],
      failures,
      "还没有失败记录"
    );
    return section("筛选", filter) + section("最近日志", logTable, [button("latest-log", "最新日志"), button("log-bulk-remove", "删除选中", "", "danger")].join("")) + section("失败记录", failureTable);
  }

  async function renderSettings() {
    const settings = await api.ocrSettings();
    const usesLocalOcr = (settings.provider || "") === "paddle_ocr";
    const savedText = usesLocalOcr
      ? "本地 PaddleOCR 不需要 API 密钥"
      : settings.api_key_saved
      ? `已保存：${settings.api_key_masked || "********"}`
      : `未保存，当前可读取环境变量 ${settings.api_key_env || "-"}`;
    const ocrForm = `
      <form class="gc-form" data-form="save-ocr-settings">
        ${field("OCR 提供商", select("provider", [{ value: "paddle_ocr", label: "PaddleOCR" }, { value: "google_ocr", label: "Google Cloud Vision" }, { value: "openai", label: "OpenAI" }], settings.provider || "paddle_ocr"))}
        <label class="gc-field"><span>API 密钥</span><input class="gc-input" name="api_key" type="password" autocomplete="new-password" placeholder="${escapeHtml(savedText)}" /></label>
        <label class="gc-check"><input name="clear_api_key" type="checkbox" /> 清除已保存密钥</label>
        <button class="gc-btn is-primary" type="submit">保存 OCR 设置</button>
      </form>
      <div class="gc-note">密钥不会在页面明文显示；留空保存会保留当前密钥。</div>
    `;
    return section("OCR 设置", ocrForm);
  }

  function workerStatusView(status) {
    const rows = (status && Array.isArray(status.workers)) ? status.workers : [];
    const runningCount = rows.filter((row) => ["starting", "idle", "running"].includes(String(row.status || ""))).length;
    const stats = statGrid([
      { label: "Worker", value: label((status && status.status) || "idle") },
      { label: "线程", value: status && status.thread_alive ? "运行中" : "未运行" },
      { label: "活跃手机", value: runningCount },
      { label: "心跳记录", value: rows.length },
    ]);
    const tableHtml = table(
      [
        { label: "Worker", render: (row) => escapeHtml(compact(row.worker_id, 28)) },
        { label: "状态", render: (row) => pill(row.status) },
        { label: "手机", render: (row) => escapeHtml(text(row.phone_id || row.adb_serial)) },
        { label: "当前任务", render: (row) => escapeHtml(compact(row.current_task_id || "-")) },
        { label: "心跳", render: (row) => escapeHtml(formatDate(row.heartbeat_at)) },
        { label: "错误", render: (row) => escapeHtml(compact(row.last_error || "-", 80)) },
      ],
      rows,
      "暂无 Worker 心跳"
    );
    return stats + tableHtml;
  }

  async function renderQueue() {
    const [raw, workerStatus] = await Promise.all([
      api.queuePreview({ ignore_phone_status: false, allow_overdue: false, limit: 200 }),
      api.queueWorkersStatus({ limit: 100 }),
    ]);
    const rows = normalizeQueueCandidates(raw);
    const workerControls = `
      <form class="gc-form" data-form="queue-workers">
        ${input("phone_ids", "手机ID，多个用逗号分隔")}
        ${input("adb_serials", "ADB序列号，多个用逗号分隔")}
        ${select("account_type", [{ value: "", label: "全部账号" }, { value: "marketing", label: "营销号" }, { value: "showcase", label: "橱窗号" }], "")}
        ${input("max_workers", "最大并行手机数", "5", "number")}
        ${input("expected_worker_count", "准入期望手机数", "5", "number")}
        ${input("max_runs_per_worker", "每台最多任务数，0为不限", "0", "number")}
        ${input("post_run_cooldown_seconds", "单机任务间隔秒数", "90", "number")}
        ${input("idle_wake_interval_seconds", "空闲唤醒间隔秒数", "300", "number")}
        ${input("timeout_seconds", "单任务超时秒数", "7200", "number")}
        <label class="gc-check"><input name="idle_wake_enabled" type="checkbox" checked /> 空闲时定期唤醒/解锁</label>
        <label class="gc-check"><input name="steady_state_gate_enabled" type="checkbox" checked /> 启动前执行5台准入门禁</label>
        <label class="gc-check"><input name="require_task_per_phone" type="checkbox" /> 每台手机必须已有可领取任务</label>
        <label class="gc-check"><input name="stop_when_idle" type="checkbox" /> 空闲后停止</label>
        <label class="gc-check"><input name="stop_on_failure" type="checkbox" checked /> 失败后停止该手机</label>
        <label class="gc-check"><input name="ignore_phone_status" type="checkbox" /> 忽略手机空闲状态</label>
        <label class="gc-check"><input name="allow_overdue" type="checkbox" /> 允许过期任务</label>
        <label class="gc-check"><input name="pipeline_dry_run" type="checkbox" /> 流水线演练</label>
        <label class="gc-check"><input name="allow_publish" type="checkbox" /> 允许真实发布</label>
        <button class="gc-btn is-primary" type="submit">启动 Worker</button>
        ${button("queue-workers-status", "刷新状态")}
        ${button("queue-workers-stop", "停止 Worker", "", "danger")}
        ${button("queue-workers-recover", "恢复过期锁", "", "warning")}
      </form>
    `;
    const controls = `
      <form class="gc-form" data-form="queue-run">
        ${input("timezone", "时区", "Asia/Shanghai")}
        ${input("preparation_window_minutes", "准备窗口分钟", "60", "number")}
        ${input("timeout_seconds", "超时秒数", "7200", "number")}
        <label class="gc-check"><input name="ignore_phone_status" type="checkbox" /> 忽略手机空闲状态</label>
        <label class="gc-check"><input name="allow_overdue" type="checkbox" /> 允许过期任务</label>
        <label class="gc-check"><input name="pipeline_dry_run" type="checkbox" /> 流水线演练</label>
        <label class="gc-check"><input name="allow_publish" type="checkbox" /> 允许真实发布</label>
        <button class="gc-btn is-primary" type="submit">执行选中任务</button>
        ${button("queue-claim", "调试锁定")}
      </form>
      <div class="gc-note" data-result="queue"></div>
    `;
    const list = table(
      [
        { label: "选择", labelHtml: queueSelectAllHeader(), render: (row) => queueTaskSelector(row) },
        { label: "任务", render: (row) => escapeHtml(compact(row.task_id)) },
        { label: "视频", render: (row) => escapeHtml(text(row.video_title || row.video_file_name)) },
        { label: "手机", render: (row) => escapeHtml(text(row.phone_name || row.phone_adb_serial)) },
        { label: "计划时间", render: (row) => escapeHtml(formatDate(row.scheduled_at)) },
        { label: "模式", render: (row) => escapeHtml(label(row.publish_mode)) },
        { label: "状态", render: (row) => pill(taskStatus(row)) },
        { label: "可执行", render: (row) => (row.eligible ? pill("ok") : pill("failed")) },
        { label: "说明", render: (row) => escapeHtml(text(row.queue_reason)) },
      ],
      rows,
      "当前没有可执行队列任务"
    );
    return section("Worker 控制", workerControls) + section("Worker 状态", workerStatusView(workerStatus)) + section("选中任务执行", controls) + section("队列预览", list, button("queue-refresh", "刷新队列"));
  }

  const renderers = {
    overview: renderOverview,
    video_library: renderVideos,
    copywriting_library: renderCaptions,
    product_library: renderProducts,
    mobile_library: renderPhones,
    publish_task: renderTasks,
    execution_queue: renderQueue,
    audit_logs: renderLogs,
    system_settings: renderSettings,
  };

  async function renderPage(page) {
    injectStyles();
    ensureOverlays();
    const main = document.querySelector("main") || document.body;
    main.innerHTML = `<div class="gc-app">${shell(pages[page] || "控制中心")}</div>`;
    const body = main.querySelector("[data-gc-body]");
    try {
      body.innerHTML = await (renderers[page] || renderOverview)();
      bindForms(main, page);
      updateQueueSelectionState();
      updateBulkSelectionState();
    } catch (error) {
      body.innerHTML = `<div class="gc-error">${escapeHtml(error.message || String(error))}<span class="gc-code">cd "C:\\Local Disk\\Work\\group_control_system"\npython -m control_center serve --host 127.0.0.1 --port 8766</span></div>`;
    }
  }

  function reload() {
    renderPage(pageName());
  }

  async function runAction(message, action) {
    const result = await action();
    showToast(message || "操作完成", "ok");
    return result;
  }

  function bindForms(root, page) {
    root.querySelectorAll("form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const type = form.dataset.form;
        try {
          if (type === "filter") {
            filters[form.dataset.page] = formEntries(form);
            reload();
            return;
          }
          if (type === "video-import") await submitVideoImport(form);
          if (type === "video-scan") await submitVideoScan(form);
          if (type === "video-status") await submitSimplePost(`/videos/${formValue(form, "id")}/status`, { status: formValue(form, "status") });
          if (type === "caption-create") await submitSimplePost("/captions", { content: formValue(form, "content"), platform: formValue(form, "platform"), account_scope: formValue(form, "account_scope"), tags: parseTags(formValue(form, "tags")), note: formValue(form, "note") });
          if (type === "caption-import") await submitCaptionImport(form);
          if (type === "caption-edit") await submitSimplePost(`/captions/${formValue(form, "id")}/edit`, { content: formValue(form, "content"), platform: formValue(form, "platform"), account_scope: formValue(form, "account_scope"), tags: parseTags(formValue(form, "tags")), note: formValue(form, "note") });
          if (type === "caption-status") await submitSimplePost(`/captions/${formValue(form, "id")}/status`, { status: formValue(form, "status") });
          if (type === "caption-bind") await submitSimplePost("/captions/bind-video", { caption_id: formValue(form, "caption_id"), video_id: formValue(form, "video_id"), note: formValue(form, "note") });
          if (type === "product-create") await submitSimplePost("/products", productPayload(form));
          if (type === "product-edit") await submitSimplePost(`/products/${formValue(form, "id")}/edit`, productPayload(form));
          if (type === "product-status") await submitSimplePost(`/products/${formValue(form, "id")}/status`, { status: formValue(form, "status") });
          if (type === "phone-create") await submitSimplePost("/phones", phonePayload(form));
          if (type === "phone-edit") await submitSimplePost(`/phones/${formValue(form, "id")}/edit`, phonePayload(form));
          if (type === "usb-phone-register") {
            await submitUsbPhoneRegister(form);
            return;
          }
          if (type === "adb-register") {
            await submitAdbRegister(form);
            return;
          }
          if (type === "task-create") await submitTaskCreate(form);
          if (type === "task-from-binding") await submitTaskFromBinding(form);
          if (type === "task-import") await submitTaskImport(form);
          if (type === "task-reschedule") await submitSimplePost(`/tasks/${formValue(form, "id")}/reschedule`, { scheduled_at: formValue(form, "scheduled_at"), timezone: formValue(form, "timezone") || "Asia/Shanghai" });
          if (type === "task-assign") await submitSimplePost(`/tasks/${formValue(form, "id")}/assign-phone`, { phone_id: formValue(form, "phone_id") });
          if (type === "task-status") await submitSimplePost(`/tasks/${formValue(form, "id")}/status`, { status: formValue(form, "status"), failure_reason: formValue(form, "failure_reason"), run_dir: formValue(form, "run_dir") });
          if (type === "queue-run") {
            await submitQueueRun(form);
            return;
          }
          if (type === "queue-workers") {
            await submitQueueWorkers(form);
            return;
          }
          if (type === "save-ocr-settings") await submitSaveOcrSettings(form);
          if (type === "save-config") await submitSaveConfig(form);
          if (type === "save-presets") await submitSavePresets(form);
          closeOverlays();
          reload();
        } catch (error) {
          showToast(error.message || String(error), "danger");
        }
      });
    });
  }

  async function submitSimplePost(path, body) {
    await runAction("操作完成", () => api.post(path, body));
  }

  async function submitVideoImport(form) {
    const files = form.querySelector("[name='files']").files || [];
    if (files.length) {
      let count = 0;
      for (const file of files) {
        const data = new FormData();
        data.append("file", file, file.name);
        data.append("title", formValue(form, "title"));
        data.append("batch_name", formValue(form, "batch_name"));
        data.append("tags", formValue(form, "tags"));
        data.append("note", formValue(form, "note"));
        await api.uploadVideo(data);
        count += 1;
      }
      showToast(`已上传 ${count} 个视频`, "ok");
      return;
    }
    if (!formValue(form, "path")) throw new Error("请选择视频文件，或在高级选项里填写本机视频路径。");
    await runAction("视频已导入", () => api.importVideo({ path: formValue(form, "path"), title: formValue(form, "title"), batch_name: formValue(form, "batch_name"), tags: parseTags(formValue(form, "tags")), note: formValue(form, "note") }));
  }

  async function submitVideoScan(form) {
    const folder = formValue(form, "folder");
    if (!folder) throw new Error("请选择要扫描的视频文件夹。");
    await runAction("扫描完成", () => api.post("/videos/scan-folder", { folder, recursive: formBool(form, "recursive"), batch_name: formValue(form, "batch_name") || folderNameFromPath(folder), tags: parseTags(formValue(form, "tags")), note: formValue(form, "note") }));
  }

  async function chooseVideoScanFolder(buttonEl) {
    const form = buttonEl.closest("form");
    const inputEl = form && form.querySelector("[name='folder']");
    const result = await api.chooseVideoFolder({
      title: "选择视频文件夹",
      initial_dir: inputEl ? inputEl.value : "",
    });
    if (!result || !result.folder) {
      showToast("未选择文件夹");
      return;
    }
    if (inputEl) inputEl.value = result.folder;
    showToast("已选择文件夹", "ok");
  }

  async function submitCaptionImport(form) {
    const file = form.querySelector("[name='file']").files[0];
    if (file) {
      const data = new FormData(form);
      await runAction("文案已导入", () => api.requestForm("POST", "/captions/import", data));
      return;
    }
    await runAction("文案已导入", () => api.post("/captions/import", { path: formValue(form, "path"), platform: formValue(form, "platform"), account_scope: formValue(form, "account_scope"), tags: parseTags(formValue(form, "tags")), note: formValue(form, "note") }));
  }

  function productPayload(form) {
    return {
      title: formValue(form, "title"),
      search_title: formValue(form, "search_title"),
      publish_name: formValue(form, "publish_name"),
      product_link: formValue(form, "product_link"),
      account_scope: formValue(form, "account_scope"),
      note: formValue(form, "note"),
    };
  }

  async function submitTaskImport(form) {
    const file = form.querySelector("[name='file']").files[0];
    if (file) {
      const data = new FormData(form);
      await runAction("任务已导入", () => api.requestForm("POST", "/tasks/import-csv", data));
      return;
    }
    await runAction("任务已导入", () => api.post("/tasks/import-csv", { path: formValue(form, "path"), allow_reuse: formBool(form, "allow_reuse"), timezone: formValue(form, "timezone") || "Asia/Shanghai" }));
  }

  function phonePayload(form) {
    return {
      device_name: formValue(form, "device_name"),
      adb_serial: formValue(form, "adb_serial"),
      account_name: formValue(form, "account_name"),
      account_type: formValue(form, "account_type") || "marketing",
      connection_mode: "usb",
      app_package: formValue(form, "app_package"),
      remote_video_dir: formValue(form, "remote_video_dir") || "/sdcard/DCIM/Camera",
      note: formValue(form, "note"),
    };
  }

  async function submitAdbRegister(form) {
    const devices = await runAction("USB 设备检测完成", () => api.adbDevices({ register: false }));
    closeOverlays();
    reload();
    openDrawer("USB 检测结果", usbDeviceRegisterList(devices, form));
  }

  async function submitUsbPhoneRegister(form) {
    validateRequired(form, ["adb_serial"]);
    const result = await runAction("手机已登记", () => api.post("/phones", phonePayload(form)));
    closeOverlays();
    reload();
    openDrawer("登记结果", jsonPre(result));
  }

  async function disablePhone(phoneId) {
    await runAction("手机已停用", () => api.post(`/phones/${encodeURIComponent(phoneId)}/status`, { status: "disabled" }));
    reload();
  }

  async function enablePhone(phoneId) {
    const result = await runAction("手机已启用并完成检查", () => api.post(`/phones/${encodeURIComponent(phoneId)}/health`, {}));
    openDrawer("启用检查结果", jsonPre(result));
    reload();
  }

  async function removePhone(phoneId) {
    const ok = window.confirm("确认从手机库移除这台手机？历史任务和检查记录会保留，但它将不再出现在手机列表，也不会参与任务。");
    if (!ok) return;
    await runAction("手机已移除", () => api.post(`/phones/${encodeURIComponent(phoneId)}/remove`, {}));
    reload();
  }

  async function removeVideo(videoId) {
    const ok = window.confirm("确认从视频库删除这个视频？历史任务会保留引用，但它将不再出现在默认视频列表。");
    if (!ok) return;
    await runAction("视频已删除", () => api.post(`/videos/${encodeURIComponent(videoId)}/remove`, {}));
    reload();
  }

  async function removeCaption(captionId) {
    const ok = window.confirm("确认从文案库删除这条文案？历史任务和绑定记录会保留引用，但它将不再出现在默认文案列表。");
    if (!ok) return;
    await runAction("文案已删除", () => api.post(`/captions/${encodeURIComponent(captionId)}/remove`, {}));
    reload();
  }

  async function removeProduct(productId) {
    const ok = window.confirm("确认从商品库删除这个商品？历史任务会保留商品快照，但它将不再出现在默认商品列表。");
    if (!ok) return;
    await runAction("商品已删除", () => api.post(`/products/${encodeURIComponent(productId)}/remove`, {}));
    reload();
  }

  async function removeSelected(kind, labelText, path, payloadKey) {
    const ids = selectedBulkIds(kind);
    if (!ids.length) throw new Error(`请先勾选要删除的${labelText}。`);
    const ok = window.confirm(`确认批量删除 ${ids.length} 个${labelText}？`);
    if (!ok) return;
    const payload = {};
    payload[payloadKey] = ids;
    await runAction(`${labelText}已批量删除`, () => api.post(path, payload));
    reload();
  }

  function validateRequired(form, names) {
    const missing = names.filter((name) => !formValue(form, name));
    if (missing.length) {
      const namesText = missing.map((name) => ({ host: "手机 IP", pair_port: "配对端口", pair_code: "配对码", connect_port: "连接端口", service_name: "二维码服务名", password: "二维码密码" }[name] || name)).join("、");
      throw new Error(`请填写：${namesText}`);
    }
  }

  async function submitTaskCreate(form) {
    await runAction("任务已创建", () => api.post("/tasks", taskPayload(form)));
  }

  async function submitTaskFromBinding(form) {
    await runAction("任务已创建", () => api.post("/tasks/from-binding", Object.assign({ binding_id: formValue(form, "binding_id") }, taskPayload(form))));
  }

  function taskPayload(form) {
    return {
      video_id: formValue(form, "video_id"),
      caption_id: formValue(form, "caption_id"),
      phone_id: formValue(form, "phone_id"),
      scheduled_at: formValue(form, "scheduled_at"),
      publish_mode: formValue(form, "publish_mode") || "scheduled",
      product_id: formValue(form, "product_id"),
      product_link: formValue(form, "product_link"),
      product_name: formValue(form, "product_name"),
      product_search_title: formValue(form, "product_search_title"),
      product_publish_name: formValue(form, "product_publish_name"),
      max_retries: formValue(form, "max_retries") || "3",
      allow_reuse: formBool(form, "allow_reuse"),
      timezone: formValue(form, "timezone") || "Asia/Shanghai",
      note: formValue(form, "note"),
    };
  }

  async function submitQueueRun(form) {
    const taskIds = selectedQueueTaskIds();
    if (!taskIds.length) throw new Error("请先勾选要执行的队列任务。");
    const allowPublish = formBool(form, "allow_publish");
    const basePayload = {
      dry_run: !allowPublish && !formBool(form, "pipeline_dry_run"),
      pipeline_dry_run: formBool(form, "pipeline_dry_run"),
      allow_publish: allowPublish,
      timezone: formValue(form, "timezone") || "Asia/Shanghai",
      preparation_window_minutes: formValue(form, "preparation_window_minutes") || "60",
      ignore_phone_status: formBool(form, "ignore_phone_status"),
      allow_overdue: formBool(form, "allow_overdue"),
      timeout_seconds: formValue(form, "timeout_seconds") || "7200",
    };
    await runAction("队列执行已完成", async () => {
      const results = [];
      for (const taskId of taskIds) {
        try {
          const outcome = await api.runQueue(Object.assign({}, basePayload, { task_id: taskId }));
          results.push({ task_id: taskId, outcome });
        } catch (error) {
          results.push({ task_id: taskId, status: "request_failed", error: error.message || String(error) });
        }
      }
      return { status: "selected_tasks_completed", total: taskIds.length, results };
    });
    reload();
  }

  async function submitQueueWorkers(form) {
    const allowPublish = formBool(form, "allow_publish");
    const pipelineDryRun = formBool(form, "pipeline_dry_run");
    const payload = {
      phone_ids: formValue(form, "phone_ids"),
      adb_serials: formValue(form, "adb_serials"),
      account_type: formValue(form, "account_type"),
      max_workers: formValue(form, "max_workers") || "5",
      expected_worker_count: formValue(form, "expected_worker_count") || "5",
      max_runs_per_worker: formValue(form, "max_runs_per_worker") || "0",
      post_run_cooldown_seconds: formValue(form, "post_run_cooldown_seconds") || "90",
      idle_wake_enabled: formBool(form, "idle_wake_enabled"),
      idle_wake_interval_seconds: formValue(form, "idle_wake_interval_seconds") || "300",
      steady_state_gate_enabled: formBool(form, "steady_state_gate_enabled"),
      require_task_per_phone: formBool(form, "require_task_per_phone"),
      timeout_seconds: formValue(form, "timeout_seconds") || "7200",
      stop_when_idle: formBool(form, "stop_when_idle"),
      stop_on_failure: formBool(form, "stop_on_failure"),
      ignore_phone_status: formBool(form, "ignore_phone_status"),
      allow_overdue: formBool(form, "allow_overdue"),
      pipeline_dry_run: pipelineDryRun,
      dry_run: !allowPublish && !pipelineDryRun,
      allow_publish: allowPublish,
      timezone: "Asia/Shanghai",
    };
    const result = await runAction("Worker 已启动", () => api.startQueueWorkers(payload));
    openDrawer("Worker 启动结果", jsonPre(result));
    reload();
  }

  async function showQueueWorkersStatus() {
    await runAction("Worker 状态已刷新", () => api.queueWorkersStatus({ limit: 100 }));
    reload();
  }

  async function stopQueueWorkers() {
    await runAction("Worker 停止请求已发送", () => api.stopQueueWorkers({ wait_seconds: 5, limit: 100 }));
    reload();
  }

  async function recoverStaleQueueWorkers() {
    const ok = window.confirm("确认恢复过期 Worker 锁？运行中的任务会被释放回队列。");
    if (!ok) return;
    await runAction("过期 Worker 已恢复", () => api.recoverStaleQueueWorkers({ reason: "control center manual recovery" }));
    reload();
  }

  async function submitSaveOcrSettings(form) {
    await runAction("OCR 设置已保存", () => api.saveOcrSettings({
      provider: formValue(form, "provider") || "paddle_ocr",
      api_key: formValue(form, "api_key"),
      clear_api_key: formBool(form, "clear_api_key"),
    }));
  }

  async function submitSaveConfig(form) {
    const config = JSON.parse(formValue(form, "config_json") || "{}");
    config.adb = config.adb || {};
    config.pipeline = config.pipeline || {};
    config.pipeline.schedule = config.pipeline.schedule || {};
    config.adb.path = formValue(form, "adb_path");
    config.adb.serial = formValue(form, "adb_serial");
    config.adb.app_package = formValue(form, "app_package");
    config.adb.remote_video_dir = formValue(form, "remote_video_dir") || "/sdcard/DCIM/Camera";
    config.pipeline.schedule.timezone = formValue(form, "schedule_timezone") || "Asia/Shanghai";
    config.pipeline.targets = JSON.parse(formValue(form, "targets_json") || "{}");
    await runAction("配置已保存", () => api.post("/upload/config", { config }));
  }

  async function submitSavePresets(form) {
    const presets = {
      default: formValue(form, "default"),
      videos: JSON.parse(formValue(form, "videos_json") || "{}"),
      presets: JSON.parse(formValue(form, "presets_json") || "{}"),
    };
    await runAction("预设已保存", () => api.post("/upload/caption-presets", { presets }));
  }

  document.addEventListener("click", async (event) => {
    const buttonEl = event.target.closest && event.target.closest("[data-action]");
    if (!buttonEl) return;
    const action = buttonEl.dataset.action;
    if (["close-modal", "close-drawer"].includes(action)) {
      event.preventDefault();
      closeOverlays();
      return;
    }
    if (action === "refresh") {
      event.preventDefault();
      reload();
      return;
    }
    try {
      await handleAction(action, buttonEl);
    } catch (error) {
      if (error) showToast(error.message || String(error), "danger");
    }
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!target || !target.matches) return;
    if (target.matches("[data-queue-select-all]")) {
      document.querySelectorAll("[data-queue-task]").forEach((box) => {
        box.checked = target.checked;
      });
      updateQueueSelectionState();
      return;
    }
    if (target.matches("[data-queue-task]")) {
      updateQueueSelectionState();
      return;
    }
    if (target.matches("[data-bulk-select-all]")) {
      const kind = target.dataset.bulkSelectAll || "";
      document.querySelectorAll(`[data-bulk-item="${cssEscape(kind)}"]`).forEach((box) => {
        box.checked = target.checked;
      });
      updateBulkSelectionState(kind);
      return;
    }
    if (target.matches("[data-bulk-item]")) {
      updateBulkSelectionState(target.dataset.bulkItem || "");
    }
  });

  async function handleAction(action, el) {
    const id = el.dataset.id || "";
    if (action === "clear-filter") {
      filters[el.dataset.page] = {};
      reload();
      return;
    }
    if (action === "open-video-import") return openModal("导入视频", videoImportForm());
    if (action === "open-video-scan") return openModal("扫描文件夹", videoScanForm());
    if (action === "choose-video-folder") return chooseVideoScanFolder(el);
    if (action === "view-video") return openDrawer("视频详情", jsonPre(await api.video(id)));
    if (action === "open-video-status") return openModal("修改视频状态", statusForm("video-status", id, el.dataset.status, ["unused", "assigned", "publishing", "published", "failed", "archived", "disabled", "removed"]));
    if (action === "video-remove") return removeVideo(id);
    if (action === "video-bulk-remove") return removeSelected("video", "视频", "/videos/remove-batch", "video_ids");
    if (action === "task-from-video") return navigate("publish_task", { video_id: id });
    if (action === "open-caption-create") return openModal("新增文案", captionCreateForm());
    if (action === "open-caption-import") return openModal("批量导入文案", captionImportForm());
    if (action === "open-caption-bind") return openModal("绑定文案和视频", captionBindForm());
    if (action === "view-caption") return openDrawer("文案详情", jsonPre(await api.get(`/captions/${encodeURIComponent(id)}`)));
    if (action === "open-caption-edit") return openModal("编辑文案", captionEditForm(await api.get(`/captions/${encodeURIComponent(id)}`)));
    if (action === "open-caption-status") return openModal("修改文案状态", statusForm("caption-status", id, el.dataset.status, ["unused", "assigned", "used", "disabled", "removed"]));
    if (action === "caption-remove") return removeCaption(id);
    if (action === "caption-bulk-remove") return removeSelected("caption", "文案", "/captions/remove-batch", "caption_ids");
    if (action === "open-product-create") return openModal("新增商品", productForm("product-create", {}));
    if (action === "view-product") return openDrawer("商品详情", jsonPre(await api.product(id)));
    if (action === "open-product-edit") return openModal("编辑商品", productForm("product-edit", await api.product(id)));
    if (action === "open-product-status") return openModal("修改商品状态", statusForm("product-status", id, el.dataset.status, ["active", "disabled", "removed"]));
    if (action === "product-remove") return removeProduct(id);
    if (action === "open-phone-create") return openModal("手动添加手机信息", phoneForm("phone-create", {}));
    if (action === "view-phone") return openDrawer("手机详情", jsonPre(await api.phone(id)));
    if (action === "open-phone-edit") return openModal("编辑手机", phoneForm("phone-edit", await api.phone(id)));
    if (action === "open-adb-register") return openModal("USB 检测并登记", adbRegisterForm());
    if (action === "phone-health") return openDrawer("健康检查", jsonPre(await runAction("检查完成", () => api.post(`/phones/${encodeURIComponent(id)}/health`, {}))));
    if (action === "phone-health-all") return openDrawer("全量健康检查", jsonPre(await runAction("检查完成", () => api.post("/phones/health-all", {}))));
    if (action === "phone-usb-refresh") return openDrawer("已登记 USB 检测", jsonPre(await runAction("检测完成", () => api.refreshUsbPhones())));
    if (action === "phone-checks") return openDrawer("健康检查历史", jsonPre(await api.get(`/phones/${encodeURIComponent(id)}/checks`, { limit: 50 })));
    if (action === "phone-disable") return disablePhone(id);
    if (action === "phone-enable") return enablePhone(id);
    if (action === "phone-remove") return removePhone(id);
    if (action === "open-task-create") return openModal("新建发布任务", taskCreateForm());
    if (action === "open-task-from-binding") return openModal("从绑定创建任务", taskFromBindingForm());
    if (action === "open-task-import") return openModal("CSV 导入任务", taskImportForm());
    if (action === "view-task") return openDrawer("任务详情", jsonPre(await api.task(id)));
    if (action === "open-task-reschedule") return openModal("修改发布时间", taskRescheduleForm(id));
    if (action === "open-task-assign") return openModal("改派手机", taskAssignForm(id));
    if (action === "open-task-status") return openModal("修改任务状态", taskStatusForm(id, el.dataset.status));
    if (action === "task-cancel") return cancelTask(id);
    if (action === "task-requeue") return requeueTask(id, el.dataset.status || "");
    if (action === "task-bulk-remove") return removeSelected("task", "任务", "/tasks/remove-batch", "task_ids");
    if (action === "queue-refresh") {
      showToast("执行队列已刷新", "ok");
      reload();
      return;
    }
    if (action === "queue-claim") return queueClaim();
    if (action === "queue-workers-status") return showQueueWorkersStatus();
    if (action === "queue-workers-stop") return stopQueueWorkers();
    if (action === "queue-workers-recover") return recoverStaleQueueWorkers();
    if (action === "view-log") return openDrawer("日志详情", jsonPre(await api.log(id, { tail_chars: 2000 })));
    if (action === "log-artifacts") return showLogArtifacts(id);
    if (action === "log-tail") return openDrawer("输出尾部", jsonPre(await api.get(`/logs/${encodeURIComponent(id)}/tail`, { stream: "both", chars: 6000 })));
    if (action === "task-report") return openDrawer("任务报告", jsonPre(await api.get(`/logs/task/${encodeURIComponent(id)}`)));
    if (action === "latest-log") return openDrawer("最新日志", jsonPre(await api.latestLog({ tail_chars: 2000 })));
    if (action === "log-bulk-remove") return removeSelected("log", "日志", "/logs/remove-batch", "log_ids");
    if (action === "upload-doctor") return uploadTool("doctor", el);
    if (action === "upload-push") return uploadTool("push", el);
    if (action === "upload-run") return uploadTool("run", el);
  }

  function navigate(page, params) {
    if (window.GroupControlShell && typeof window.GroupControlShell.navigate === "function") window.GroupControlShell.navigate(page, params);
    else location.hash = `#/${page}`;
  }

  function videoImportForm() {
    return `<form class="gc-form" data-form="video-import"><label class="gc-field gc-span"><span>视频文件</span><input class="gc-input" name="files" type="file" multiple accept="video/*,.mp4,.mov,.m4v,.webm,.3gp,.3gpp,.mkv,.avi" /></label><button class="gc-btn is-primary" type="submit">导入</button><details class="gc-advanced"><summary>高级选项</summary><div class="gc-form is-compact">${input("path", "控制中心本机视频路径")}${input("title", "视频标题")}${input("batch_name", "批次")}${input("tags", "标签，逗号分隔")}${input("note", "备注")}</div></details></form>`;
  }

  function videoScanForm() {
    return `<form class="gc-form" data-form="video-scan"><div class="gc-input-action gc-span">${input("folder", "控制中心本机文件夹路径")}<button class="gc-btn" type="button" data-action="choose-video-folder">选择文件夹</button></div><label class="gc-check"><input name="recursive" type="checkbox" checked /> 递归扫描</label><button class="gc-btn is-primary" type="submit">扫描导入</button><details class="gc-advanced"><summary>高级选项</summary><div class="gc-form is-compact">${input("batch_name", "批次")}${input("tags", "标签，逗号分隔")}${input("note", "备注")}</div></details></form>`;
  }

  function statusForm(type, id, current, statuses) {
    return `<form class="gc-form" data-form="${escapeHtml(type)}"><input type="hidden" name="id" value="${escapeHtml(id)}" />${field("状态", select("status", statuses, current))}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function captionCreateForm() {
    return `<form class="gc-form" data-form="caption-create"><textarea class="gc-textarea gc-span" name="content" placeholder="文案内容" required></textarea>${input("platform", "平台")}${input("account_scope", "账号范围")}${input("tags", "标签，逗号分隔")}${input("note", "备注")}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function captionImportForm() {
    return `<form class="gc-form" data-form="caption-import"><input class="gc-input gc-span" name="file" type="file" accept=".txt,.json,.csv" />${input("path", "控制中心本机 TXT/JSON/CSV 路径")}${input("platform", "平台")}${input("account_scope", "账号范围")}${input("tags", "标签，逗号分隔")}${input("note", "备注")}<button class="gc-btn is-primary" type="submit">导入</button></form>`;
  }

  function captionEditForm(row) {
    return `<form class="gc-form" data-form="caption-edit"><input type="hidden" name="id" value="${escapeHtml(row.caption_id)}" /><textarea class="gc-textarea gc-span" name="content">${escapeHtml(row.content || "")}</textarea>${input("platform", "平台", row.platform)}${input("account_scope", "账号范围", row.account_scope)}${input("tags", "标签，逗号分隔", (row.tags || []).join(","))}${input("note", "备注", row.note)}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function captionBindForm() {
    const opts = window.__gcOptions || {};
    return `<form class="gc-form" data-form="caption-bind"><select class="gc-input" name="caption_id" required><option value="">选择文案</option>${optionList(opts.captions || [], "caption_id", (row) => `${row.caption_id} - ${compact(row.content, 42)}`)}</select><select class="gc-input" name="video_id" required><option value="">选择视频</option>${optionList(opts.videos || [], "video_id", (row) => `${row.video_id} - ${compact(row.title || row.file_name, 42)}`)}</select>${input("note", "备注")}<button class="gc-btn is-primary" type="submit">绑定</button></form>`;
  }

  function productForm(type, row) {
    return `<form class="gc-form" data-form="${escapeHtml(type)}"><input type="hidden" name="id" value="${escapeHtml(row.product_id || "")}" />${input("title", "商品显示名称", row.title)}${input("search_title", "搜索商品标题", row.search_title)}${input("publish_name", "发布页商品名称", row.publish_name)}${input("product_link", "商品链接，可选", row.product_link)}${input("account_scope", "账号范围，可选", row.account_scope)}${input("note", "备注", row.note)}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function phoneForm(type, row) {
    const manualNote = type === "phone-create" ? '<div class="gc-note gc-span">USB 调试推荐使用“USB 检测登记”自动识别。这里仅用于手动保存已经授权的 USB ADB 序列号。</div>' : "";
    return `<form class="gc-form" data-form="${escapeHtml(type)}">${manualNote}<input type="hidden" name="id" value="${escapeHtml(row.phone_id || "")}" /><input type="hidden" name="connection_mode" value="usb" />${input("device_name", "手机名称", row.device_name)}${input("adb_serial", "USB ADB 序列号", row.adb_serial)}${input("account_name", "账号", row.account_name)}${select("account_type", [{ value: "marketing", label: "营销号" }, { value: "showcase", label: "橱窗号" }], row.account_type || "marketing")}${input("app_package", "应用包名", row.app_package)}${input("remote_video_dir", "手机视频目录", row.remote_video_dir || "/sdcard/DCIM/Camera")}${input("note", "备注", row.note)}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function adbRegisterForm() {
    return `<form class="gc-form" data-form="adb-register"><div class="gc-note gc-span">请先用数据线连接手机，开启“USB 调试”，并在每台手机弹窗中允许这台电脑调试。点击后只检测当前 ADB 已识别的 USB 设备，不会自动添加；可在检测结果里逐台确认登记。</div>${select("account_type", [{ value: "marketing", label: "默认营销号" }, { value: "showcase", label: "默认橱窗号" }], "marketing")}${input("app_package", "默认应用包名")}${input("remote_video_dir", "默认手机视频目录", "/sdcard/DCIM/Camera")}<button class="gc-btn is-primary" type="submit">检测 USB 设备</button></form>`;
  }

  function usbAutoRegisterResult(devices) {
    if (!devices || !devices.length) {
      return `<div class="gc-empty">没有检测到 USB ADB 设备。请确认数据线可传输数据、USB 调试已开启，并在手机上允许调试授权。</div>`;
    }
    return table(
      [
        { label: "手机ID", render: (row) => escapeHtml(text(row.phone_id)) },
        { label: "序列号", render: (row) => `<span class="gc-code">${escapeHtml(text(row.serial))}</span>` },
        { label: "状态", render: (row) => pill(row.state === "device" ? "authorized" : row.state) },
        { label: "名称", render: (row) => escapeHtml(text(row.device_name)) },
        { label: "详情", render: (row) => `<span class="gc-muted">${escapeHtml(text(row.detail))}</span>` },
      ],
      devices,
      "没有检测到 USB ADB 设备"
    );
  }

  function usbDeviceRegisterList(devices, sourceForm) {
    const appPackage = formValue(sourceForm, "app_package");
    const accountType = formValue(sourceForm, "account_type") || "marketing";
    const remoteVideoDir = formValue(sourceForm, "remote_video_dir") || "/sdcard/DCIM/Camera";
    if (!devices || !devices.length) {
      return `<div class="gc-empty">没有检测到 USB ADB 设备。请确认数据线可传输数据、USB 调试已开启，并在手机上允许调试授权。</div>`;
    }
    return `<div class="gc-stack">${devices.map((device) => usbDeviceRegisterForm(device, appPackage, remoteVideoDir, accountType)).join("")}</div>`;
  }

  function usbDeviceRegisterForm(device, appPackage, remoteVideoDir, accountType) {
    const authorized = device.state === "device";
    const note = authorized ? "已授权，可登记。" : device.state === "unauthorized" ? "未授权，请在手机上允许 USB 调试后重新检测。" : `当前状态：${device.state || "unknown"}，暂不能登记。`;
    const disabled = authorized ? "" : " disabled";
    const nameValue = device.device_name || device.serial || "";
    return `
      <form class="gc-form" data-form="usb-phone-register">
        <div class="${authorized ? "gc-note" : "gc-error"} gc-span">
          <strong>${escapeHtml(device.serial || "-")}</strong><br />${escapeHtml(note)}
          ${device.detail ? `<br /><span class="gc-muted">${escapeHtml(device.detail)}</span>` : ""}
        </div>
        <input type="hidden" name="adb_serial" value="${escapeHtml(device.serial || "")}" />
        <input type="hidden" name="connection_mode" value="usb" />
        ${input("device_name", "手机名称", nameValue)}
        ${input("account_name", "账号")}
        ${select("account_type", [{ value: "marketing", label: "营销号" }, { value: "showcase", label: "橱窗号" }], accountType || "marketing")}
        ${input("app_package", "应用包名", appPackage)}
        ${input("remote_video_dir", "手机视频目录", remoteVideoDir)}
        ${input("note", "备注")}
        <button class="gc-btn is-primary" type="submit"${disabled}>登记这台手机</button>
      </form>`;
  }

  function taskCreateForm() {
    const opts = window.__gcOptions || {};
    const selectedVideo = hashParams().get("video_id") || "";
    return `<form class="gc-form" data-form="task-create"><select class="gc-input" name="video_id" required><option value="">选择视频</option>${optionList(opts.videos || [], "video_id", (row) => `${row.video_id} - ${compact(row.title || row.file_name, 42)}`, selectedVideo)}</select><select class="gc-input" name="caption_id"><option value="">不指定文案</option>${optionList(opts.captions || [], "caption_id", (row) => `${row.caption_id} - ${compact(row.content, 42)}`)}</select><select class="gc-input" name="phone_id" required><option value="">选择手机</option>${optionList(opts.phones || [], "phone_id", phoneOptionLabel)}</select><select class="gc-input" name="product_id"><option value="">不指定商品</option>${optionList(opts.products || [], "product_id", productOptionLabel)}</select>${input("scheduled_at", "发布时间", "", "datetime-local")}${field("发布模式", select("publish_mode", ["scheduled", "immediate", "timed"], "scheduled"))}${input("product_search_title", "搜索商品标题")}${input("product_publish_name", "发布页商品名称")}${input("product_link", "商品链接，可选")}${input("max_retries", "重试次数", "3", "number")}${input("timezone", "时区", "Asia/Shanghai")}<label class="gc-check"><input name="allow_reuse" type="checkbox" /> 允许复用视频</label>${input("note", "备注")}<button class="gc-btn is-primary" type="submit">创建</button></form>`;
  }

  function taskFromBindingForm() {
    const opts = window.__gcOptions || {};
    return `<form class="gc-form" data-form="task-from-binding"><select class="gc-input" name="binding_id" required><option value="">选择绑定关系</option>${optionList(opts.bindings || [], "binding_id", (row) => `${row.binding_id} - ${compact(row.video_title || row.file_name, 42)}`)}</select><select class="gc-input" name="phone_id" required><option value="">选择手机</option>${optionList(opts.phones || [], "phone_id", phoneOptionLabel)}</select><select class="gc-input" name="product_id"><option value="">不指定商品</option>${optionList(opts.products || [], "product_id", productOptionLabel)}</select>${input("scheduled_at", "发布时间", "", "datetime-local")}${field("发布模式", select("publish_mode", ["scheduled", "immediate", "timed"], "scheduled"))}${input("product_search_title", "搜索商品标题")}${input("product_publish_name", "发布页商品名称")}${input("product_link", "商品链接，可选")}${input("max_retries", "重试次数", "3", "number")}${input("timezone", "时区", "Asia/Shanghai")}<label class="gc-check"><input name="allow_reuse" type="checkbox" /> 允许复用视频</label>${input("note", "备注")}<button class="gc-btn is-primary" type="submit">创建</button></form>`;
  }

  function taskImportForm() {
    return `<form class="gc-form" data-form="task-import"><input class="gc-input gc-span" name="file" type="file" accept=".csv" />${input("path", "控制中心本机 CSV 路径")}<label class="gc-check"><input name="allow_reuse" type="checkbox" /> 允许复用视频</label>${input("timezone", "时区", "Asia/Shanghai")}<button class="gc-btn is-primary" type="submit">导入</button></form>`;
  }

  function taskRescheduleForm(id) {
    return `<form class="gc-form" data-form="task-reschedule"><input type="hidden" name="id" value="${escapeHtml(id)}" />${input("scheduled_at", "新发布时间", "", "datetime-local")}${input("timezone", "时区", "Asia/Shanghai")}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function taskAssignForm(id) {
    const opts = window.__gcOptions || {};
    return `<form class="gc-form" data-form="task-assign"><input type="hidden" name="id" value="${escapeHtml(id)}" /><select class="gc-input" name="phone_id" required><option value="">选择手机</option>${optionList(opts.phones || [], "phone_id", phoneOptionLabel)}</select><button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  function taskStatusForm(id, current) {
    return `<form class="gc-form" data-form="task-status"><input type="hidden" name="id" value="${escapeHtml(id)}" />${select("status", ["pending", "ready", "running", "published", "dry_run", "failed", "cancelled", "paused"], current)}${input("failure_reason", "失败原因")}${input("run_dir", "运行目录")}<button class="gc-btn is-primary" type="submit">保存</button></form>`;
  }

  async function cancelTask(id) {
    const reason = window.prompt("取消原因", "手动取消");
    if (reason === null) return;
    await runAction("任务已取消", () => api.post(`/tasks/${encodeURIComponent(id)}/cancel`, { reason }));
    reload();
  }

  async function requeueTask(id, status) {
    const forceRunning = status === "running";
    const ok = window.confirm(forceRunning ? "该任务仍标记为运行中，确认强制释放手机并重新入队？" : "确认将该任务重新放回待执行队列？");
    if (!ok) return;
    const resetRetryCount = status === "failed" ? window.confirm("是否清空重试次数？") : false;
    await runAction("任务已重新入队", () => api.post(`/tasks/${encodeURIComponent(id)}/requeue`, { force_running: forceRunning, reset_retry_count: resetRetryCount, reason: "手动重新入队" }));
    reload();
  }

  async function queueClaim() {
    const form = document.querySelector("[data-form='queue-run']");
    const taskIds = selectedQueueTaskIds();
    if (taskIds.length !== 1) throw new Error("调试锁定需要只勾选一个任务。");
    const result = await runAction("任务已锁定", () => api.post("/queue/claim-next", { task_id: taskIds[0], timezone: formValue(form, "timezone") || "Asia/Shanghai", preparation_window_minutes: formValue(form, "preparation_window_minutes") || "60", ignore_phone_status: formBool(form, "ignore_phone_status"), allow_overdue: formBool(form, "allow_overdue") }));
    openDrawer("锁定结果", jsonPre(result));
  }

  async function showLogArtifacts(id) {
    const data = await api.get(`/logs/${encodeURIComponent(id)}/artifacts`);
    const artifacts = data.artifacts || {};
    const files = artifacts.files || {};
    const links = Object.entries(files)
      .map(([kind, names]) => `<div><strong>${escapeHtml(kind)}</strong><div class="gc-row-actions">${(names || []).map((name) => button("open-artifact", name, ` data-id="${escapeHtml(id)}" data-kind="${escapeHtml(kind)}" data-name="${escapeHtml(name)}"`)).join("")}</div></div>`)
      .join("");
    openDrawer("日志产物", jsonPre(artifacts) + links);
  }

  document.addEventListener("click", async (event) => {
    const artifact = event.target.closest && event.target.closest("[data-action='open-artifact']");
    if (!artifact) return;
    event.preventDefault();
    const data = await api.get(`/logs/${encodeURIComponent(artifact.dataset.id)}/artifact`, { kind: artifact.dataset.kind, name: artifact.dataset.name });
    if (data.kind === "image") openDrawer(data.name, `<img alt="${escapeHtml(data.name)}" style="max-width:100%;" src="data:${escapeHtml(data.mime_type)};base64,${escapeHtml(data.base64)}" />`);
    else openDrawer(data.name, `<pre class="gc-code">${escapeHtml(data.content || "")}</pre>`);
  });

  async function uploadTool(kind, el) {
    const form = el.closest("form");
    let path = "/upload/doctor";
    const body = { timeout_seconds: formValue(form, "timeout_seconds") || "7200" };
    if (kind === "push") {
      path = "/upload/push";
      body.video = formValue(form, "video");
    }
    if (kind === "run") {
      path = "/upload/run";
      body.video = formValue(form, "video");
      body.dry_run = formBool(form, "dry_run");
      body.allow_publish = formBool(form, "allow_publish");
    }
    const result = await runAction("上传工具执行完成", () => api.post(path, body));
    openDrawer("上传工具输出", jsonPre(result));
  }

  function boot() {
    renderPage(pageName());
    window.addEventListener("gc:page-change", (event) => renderPage((event.detail && event.detail.page) || pageName()));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
