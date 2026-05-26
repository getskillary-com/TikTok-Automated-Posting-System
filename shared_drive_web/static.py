from __future__ import annotations


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>群控任务提交</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f4f6f8;
      --panel: #ffffff;
      --panel-soft: #f9fafb;
      --text: #182230;
      --muted: #667085;
      --line: #d9e1ec;
      --line-strong: #b8c4d4;
      --accent: #0f766e;
      --accent-dark: #0b5f59;
      --blue: #2563eb;
      --blue-soft: #eff6ff;
      --amber: #b54708;
      --amber-soft: #fff7ed;
      --green: #067647;
      --green-soft: #ecfdf3;
      --red: #b42318;
      --red-soft: #fef3f2;
      --shadow: 0 12px 30px rgba(16, 24, 40, 0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--page);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }

    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
    }

    .sidebar {
      background: #111827;
      color: #f9fafb;
      padding: 22px 18px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 18px;
      font-weight: 700;
    }

    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #0f766e;
      color: #fff;
      font-weight: 800;
    }

    .nav {
      display: grid;
      gap: 6px;
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 40px;
      padding: 9px 10px;
      border-radius: 6px;
      color: #d0d5dd;
      text-decoration: none;
      font-weight: 600;
    }

    .nav-item.active { background: #1f2937; color: #fff; }
    .nav-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: currentColor;
      opacity: 0.85;
    }

    .sidebar-footer {
      margin-top: auto;
      color: #98a2b3;
      font-size: 12px;
      line-height: 1.6;
    }

    .workspace {
      padding: 22px 24px 28px;
      min-width: 0;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 18px;
    }

    h1, h2, h3, p { margin: 0; }

    h1 {
      font-size: 26px;
      line-height: 1.25;
      font-weight: 750;
    }

    .subtitle {
      margin-top: 6px;
      color: var(--muted);
      line-height: 1.6;
    }

    .toolbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }

    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: var(--shadow);
    }

    .stat-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }

    .stat-value {
      margin-top: 8px;
      font-size: 28px;
      line-height: 1;
      font-weight: 760;
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(360px, 500px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .panel-header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }

    .panel-title {
      display: grid;
      gap: 4px;
    }

    .panel-title h2 {
      font-size: 17px;
      font-weight: 750;
    }

    .panel-title span {
      color: var(--muted);
      font-size: 12px;
    }

    .panel-body { padding: 18px; }

    form {
      display: grid;
      gap: 14px;
    }

    label {
      display: grid;
      gap: 7px;
      color: var(--text);
      font-weight: 650;
    }

    label span {
      color: var(--muted);
      font-weight: 500;
      font-size: 12px;
      line-height: 1.4;
    }

    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
      min-height: 40px;
      padding: 9px 10px;
      outline: none;
    }

    textarea {
      resize: vertical;
      min-height: 118px;
      line-height: 1.55;
    }

    input:focus, select:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
    }

    input[type="file"] {
      padding: 8px;
      background: var(--panel-soft);
    }

    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .task-mode {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 12px;
      display: grid;
      gap: 6px;
    }

    .task-mode strong { font-size: 15px; }
    .task-mode span { color: var(--muted); font-size: 12px; line-height: 1.5; }

    .hidden { display: none !important; }

    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 2px;
    }

    button, .button {
      min-height: 40px;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 9px 14px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }

    button:hover, .button:hover { background: var(--accent-dark); }
    button:disabled {
      cursor: not-allowed;
      background: #cbd5e1;
      color: #667085;
    }

    .secondary {
      background: #fff;
      color: var(--text);
      border-color: var(--line-strong);
    }

    .secondary:hover { background: #f8fafc; }

    .status-line {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .status-line.ok { color: var(--green); }
    .status-line.error { color: var(--red); }
    .status-line.warn { color: var(--amber); }

    .table-wrap {
      overflow: auto;
      max-height: calc(100vh - 310px);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 860px;
      font-size: 13px;
    }

    th, td {
      padding: 10px 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      word-break: break-word;
    }

    th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f8fafc;
      color: var(--muted);
      font-weight: 750;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 72px;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #eef2f6;
      color: #475467;
      font-size: 12px;
      font-weight: 700;
    }

    .badge.online_idle, .badge.accepted, .badge.published {
      background: var(--green-soft);
      color: var(--green);
    }

    .badge.pending, .badge.ready, .badge.running, .badge.processing {
      background: var(--amber-soft);
      color: var(--amber);
    }

    .badge.failed, .badge.rejected, .badge.invalid, .badge.duplicate, .badge.duplicate_batch, .badge.offline, .badge.error {
      background: var(--red-soft);
      color: var(--red);
    }

    .phone-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      display: grid;
      gap: 8px;
    }

    .phone-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .phone-manager {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }

    .phone-account-row {
      display: grid;
      grid-template-columns: minmax(140px, 1fr) 118px 82px;
      gap: 8px;
      align-items: end;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }

    .phone-account-row button {
      min-height: 40px;
      padding-inline: 10px;
    }

    .technical-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      margin-top: 6px;
    }

    .time-context {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px 10px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }

    .time-context strong {
      min-width: 72px;
      color: var(--text);
      font-size: 15px;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
    }

    details {
      margin-top: 16px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }

    summary {
      cursor: pointer;
      color: var(--text);
      font-weight: 750;
    }

    .empty {
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 1100px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar {
        position: static;
        padding: 14px 18px;
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
      }
      .nav, .sidebar-footer { display: none; }
      .grid { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
    }

    @media (max-width: 680px) {
      .workspace { padding: 16px 12px 22px; }
      .topbar { flex-direction: column; }
      .toolbar { justify-content: flex-start; }
      .stats { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .phone-account-row { grid-template-columns: 1fr; }
      .panel-header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">GC</div>
        <div>群控提交</div>
      </div>
      <nav class="nav" aria-label="主导航">
        <a class="nav-item active" href="#"><span class="nav-dot"></span>任务提交</a>
        <a class="nav-item" href="/accounts"><span class="nav-dot"></span>账号管理</a>
        <a class="nav-item" href="#batchPanel"><span class="nav-dot"></span>发布记录</a>
        <a class="nav-item" href="#advancedImport"><span class="nav-dot"></span>批量导入</a>
      </nav>
      <div class="sidebar-footer">
        <div id="healthText">系统连接中</div>
        <div>状态以群控数据库为准</div>
      </div>
    </aside>

    <main class="workspace">
      <div class="topbar">
        <div>
          <h1>群控任务提交</h1>
          <p class="subtitle">选择手机后自动切换营销号或橱窗号字段，提交后生成批次并进入发布队列。</p>
        </div>
        <div class="toolbar">
          <a class="button secondary" href="/templates/task_template.xlsx">任务模板</a>
          <a class="button secondary" href="/templates/phones.xlsx">手机清单</a>
          <button class="secondary" type="button" id="syncPhonesBtn">同步手机名称</button>
          <button class="secondary" type="button" id="refreshBtn">刷新</button>
        </div>
      </div>

      <section class="stats" aria-label="系统概览">
        <div class="stat">
          <div class="stat-label">在线空闲手机</div>
          <div class="stat-value" id="onlineCount">0</div>
        </div>
        <div class="stat">
          <div class="stat-label">待发布任务</div>
          <div class="stat-value" id="pendingCount">0</div>
        </div>
        <div class="stat">
          <div class="stat-label">运行中任务</div>
          <div class="stat-value" id="runningCount">0</div>
        </div>
        <div class="stat">
          <div class="stat-label">失败任务</div>
          <div class="stat-value" id="failedCount">0</div>
        </div>
      </section>

      <div class="grid">
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title">
              <h2>任务提交区</h2>
              <span>一次提交一条素材任务</span>
            </div>
            <span class="badge" id="modeBadge">未选择</span>
          </div>
          <div class="panel-body">
            <form id="taskForm">
              <label>
                对应手机
                <select id="phoneSelect" name="phone_id" required>
                  <option value="">加载手机中</option>
                </select>
              </label>

              <div class="phone-card" id="phoneDetail">
                <strong>请选择手机</strong>
                <div class="phone-meta">系统会按手机账号类型展示对应字段。</div>
              </div>

              <div class="task-mode" id="taskMode">
                <strong>任务类型待确认</strong>
                <span>选择手机后自动判断使用 TikTok Studio 还是 TikTok App 工作流。</span>
              </div>

              <div class="row">
                <label>
                  <span id="timeLabel">预约/执行时间</span>
                  <input id="scheduledAt" name="scheduled_at" type="datetime-local" required>
                  <span id="timeHelp">营销号是 TikTok Studio 预约时间；橱窗号是 TikTok App 内置预约发布时间。</span>
                  <div class="time-context" aria-live="polite">
                    <span>巴西圣保罗时间</span>
                    <strong id="saoPauloClock">--:--:--</strong>
                    <span id="saoPauloDate">--</span>
                  </div>
                </label>
                <label>
                  视频
                  <input id="videoInput" name="video" type="file" accept=".mp4,.mov,.m4v,.webm,.3gp,.3gpp,.mkv,.avi" required>
                  <span id="selectedVideoName">未选择视频</span>
                </label>
              </div>

              <label>
                视频文案
                <textarea id="captionInput" name="caption" required placeholder="输入发布文案"></textarea>
              </label>

              <div id="productFields" class="hidden">
                <label>
                  商品搜索文案
                  <input id="productSearchTitle" name="product_search_title" type="text" placeholder="添加商品时输入到搜索框里的内容">
                  <span>添加商品时输入到搜索框里的内容。</span>
                </label>
                <label>
                  商品名（自定义商品名）
                  <input id="productPublishName" name="product_publish_name" type="text" placeholder="商品添加后弹出的自定义商品名">
                  <span>商品添加后弹出的自定义商品名。</span>
                </label>
              </div>

              <label>
                备注
                <input id="noteInput" name="note" type="text" placeholder="可选">
              </label>

              <div class="actions">
                <button id="submitBtn" type="submit" disabled>提交任务</button>
                <span class="status-line" id="submitMessage"></span>
              </div>
            </form>

            <details id="advancedImport">
              <summary>高级/批量导入</summary>
              <form id="batchForm" style="margin-top: 14px;">
                <div class="row">
                  <label>
                    批次编号
                    <input name="batch_id" type="text" placeholder="留空自动生成">
                  </label>
                  <label>
                    Excel 清单
                    <input name="tasks" type="file" accept=".xlsx" required>
                  </label>
                </div>
                <label>
                  视频文件
                  <input name="videos" type="file" accept=".mp4,.mov,.m4v,.webm,.3gp,.3gpp,.mkv,.avi" multiple required>
                </label>
                <div class="actions">
                  <button type="submit" class="secondary">上传 Excel 批次</button>
                  <span class="status-line" id="batchMessage"></span>
                </div>
              </form>
            </details>
          </div>
        </section>

        <section class="panel" id="batchPanel">
          <div class="panel-header">
            <div class="panel-title">
              <h2>批次状态</h2>
              <span>导入状态和发布状态实时从数据库读取</span>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>批次</th>
                  <th>手机</th>
                  <th>类型</th>
                  <th>视频</th>
                  <th>预约/执行时间</th>
                  <th>导入</th>
                  <th>发布</th>
                  <th>错误原因</th>
                  <th>更新时间</th>
                  <th>状态表</th>
                </tr>
              </thead>
              <tbody id="batchesBody">
                <tr><td colspan="10" class="empty">正在加载</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  </div>

  <script>
    const state = {
      phones: [],
      batches: [],
      selectedPhone: null,
      selectedVideoFile: null,
      phoneSync: null,
      refreshInFlight: false
    };

    const phoneSelect = document.getElementById("phoneSelect");
    const phoneDetail = document.getElementById("phoneDetail");
    const taskMode = document.getElementById("taskMode");
    const modeBadge = document.getElementById("modeBadge");
    const productFields = document.getElementById("productFields");
    const productSearchTitle = document.getElementById("productSearchTitle");
    const productPublishName = document.getElementById("productPublishName");
    const videoInput = document.getElementById("videoInput");
    const selectedVideoName = document.getElementById("selectedVideoName");
    const submitBtn = document.getElementById("submitBtn");
    const submitMessage = document.getElementById("submitMessage");
    const batchMessage = document.getElementById("batchMessage");
    const timeLabel = document.getElementById("timeLabel");
    const timeHelp = document.getElementById("timeHelp");
    const saoPauloClock = document.getElementById("saoPauloClock");
    const saoPauloDate = document.getElementById("saoPauloDate");
    const batchesBody = document.getElementById("batchesBody");
    const syncPhonesBtn = document.getElementById("syncPhonesBtn");
    const saoPauloTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "America/Sao_Paulo",
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
    const saoPauloDateFormatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "America/Sao_Paulo",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short"
    });

    function updateSaoPauloClock() {
      const now = new Date();
      try {
        saoPauloClock.textContent = saoPauloTimeFormatter.format(now);
        saoPauloDate.textContent = saoPauloDateFormatter.format(now);
      } catch {
        saoPauloClock.textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
        saoPauloDate.textContent = "本机时间";
      }
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char]));
    }

    function badge(value) {
      const clean = String(value || "-");
      return `<span class="badge ${escapeHtml(clean)}">${escapeHtml(clean)}</span>`;
    }

    function accountLabel(value) {
      if (value === "marketing") return "营销号";
      if (value === "showcase") return "橱窗号";
      return value || "未知";
    }

    function accountDisplayName(phone) {
      return String(phone?.account_name || "").trim() || "未设置账号名";
    }

    function phoneDisplayName(phone) {
      return String(phone?.device_name || "").trim() || "未命名设备";
    }

    function phoneExplorerName(phone) {
      return String(phone?.explorer_name || "").trim();
    }

    function phoneExplorerLine(phone) {
      const explorerName = phoneExplorerName(phone);
      return `资源管理器名称：${escapeHtml(explorerName || "未读取")}<br>`;
    }

    function phoneSelectLabel(phone) {
      return `${accountDisplayName(phone)} · ${accountLabel(phone.account_type)} · ${phone.current_status}`;
    }

    function phoneTableLabel(phone, phoneId) {
      if (!phone) return phoneId || "-";
      return accountDisplayName(phone);
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, options);
      const text = await response.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error(text || `HTTP ${response.status}`);
      }
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      return payload.data;
    }

    async function refreshAll() {
      if (state.refreshInFlight) return;
      state.refreshInFlight = true;
      try {
        const [health, phones, batches] = await Promise.all([
          requestJson("/api/health"),
          requestJson("/api/phones"),
          requestJson("/api/batches?limit=100")
        ]);
        state.phones = phones;
        state.batches = batches;
        state.phoneSync = health.phone_sync?.last_result || null;
        const syncText = state.phoneSync?.synced_at ? ` · 同步 ${state.phoneSync.mode} ${state.phoneSync.synced_at}` : "";
        document.getElementById("healthText").textContent = `${health.phone_count} 台手机 · ${health.timezone}${syncText}`;
        renderPhones();
        renderStats();
        renderBatches();
      } catch (error) {
        document.getElementById("healthText").textContent = error.message;
        setMessage(submitMessage, error.message, "error");
      } finally {
        state.refreshInFlight = false;
      }
    }

    function renderPhones() {
      const previous = phoneSelect.value;
      if (!state.phones.length) {
        phoneSelect.innerHTML = '<option value="">暂无手机</option>';
        state.selectedPhone = null;
        applyPhoneMode();
        return;
      }
      phoneSelect.innerHTML = '<option value="">请选择手机</option>' + state.phones.map(phone => {
        return `<option value="${escapeHtml(phone.phone_id)}">${escapeHtml(phoneSelectLabel(phone))}</option>`;
      }).join("");
      if (previous && state.phones.some(phone => phone.phone_id === previous)) {
        phoneSelect.value = previous;
      }
      state.selectedPhone = state.phones.find(phone => phone.phone_id === phoneSelect.value) || null;
      applyPhoneMode();
    }

    function applyPhoneMode() {
      const phone = state.selectedPhone;
      if (!phone) {
        phoneDetail.innerHTML = '<strong>请选择手机</strong><div class="phone-meta">系统会按手机账号类型展示对应字段。</div>';
        taskMode.innerHTML = '<strong>任务类型待确认</strong><span>选择手机后自动判断使用 TikTok Studio 还是 TikTok App 工作流。</span>';
        modeBadge.textContent = "未选择";
        modeBadge.className = "badge";
        productFields.classList.add("hidden");
        productSearchTitle.required = false;
        productPublishName.required = false;
        submitBtn.disabled = true;
        submitBtn.textContent = "提交任务";
        setMessage(submitMessage, "", "");
        return;
      }

      const queueable = ["online_idle", "running"].includes(phone.current_status);
      const type = phone.account_type || "marketing";
      const accountName = accountDisplayName(phone);
      phoneDetail.innerHTML = `
        <strong>${escapeHtml(accountName)}</strong>
        <div class="phone-meta">
          类型：${escapeHtml(accountLabel(type))} · 状态：${badge(phone.current_status)}
        </div>
      `;
      modeBadge.textContent = accountLabel(type);
      modeBadge.className = `badge ${escapeHtml(phone.current_status)}`;

      if (type === "showcase") {
        productFields.classList.remove("hidden");
        productSearchTitle.required = true;
        productPublishName.required = true;
        timeLabel.textContent = "预约发布时间";
        timeHelp.textContent = "橱窗号使用 TikTok App 内置预约发布时间，提交后 worker 会尽快领取并在 App 内设置预约。";
        taskMode.innerHTML = '<strong>橱窗号任务 · TikTok App</strong><span>提交后 publish_mode=scheduled，使用 TikTok App 内置预约发布时间；商品搜索文案用于添加商品搜索框，自定义商品名用于添加后的商品名。</span>';
        submitBtn.textContent = "提交橱窗号任务";
      } else {
        productFields.classList.add("hidden");
        productSearchTitle.required = false;
        productPublishName.required = false;
        productSearchTitle.value = "";
        productPublishName.value = "";
        timeLabel.textContent = "预约发布时间";
        timeHelp.textContent = "营销号使用 TikTok Studio 的预约发布时间。";
        taskMode.innerHTML = '<strong>营销号任务 · TikTok Studio</strong><span>提交后 publish_mode=scheduled，不接收任何商品字段。</span>';
        submitBtn.textContent = "提交营销号任务";
      }

      submitBtn.disabled = !queueable || !["marketing", "showcase"].includes(type);
      if (!queueable) {
        setMessage(submitMessage, `当前手机状态为 ${phone.current_status || "unknown"}，暂不能提交。`, "warn");
      } else if (!["marketing", "showcase"].includes(type)) {
        setMessage(submitMessage, `不支持的账号类型：${type || "unknown"}`, "error");
      } else if (phone.current_status === "running") {
        setMessage(submitMessage, "该手机正在执行，提交后会加入队列，当前任务完成后自动执行。", "warn");
      }
    }

    function renderStats() {
      document.getElementById("onlineCount").textContent = String(state.phones.filter(phone => phone.current_status === "online_idle").length);
      const itemRows = state.batches.flatMap(batch => batch.items || []);
      document.getElementById("pendingCount").textContent = String(itemRows.filter(row => ["pending", "ready"].includes(row.task_status)).length);
      document.getElementById("runningCount").textContent = String(itemRows.filter(row => row.task_status === "running").length);
      document.getElementById("failedCount").textContent = String(itemRows.filter(row => row.task_status === "failed" || ["rejected", "invalid", "duplicate"].includes(row.import_status)).length);
    }

    function renderBatchesLegacy() {
      if (!state.batches.length) {
        batchesBody.innerHTML = '<tr><td colspan="10" class="empty">暂无批次</td></tr>';
        return;
      }
      batchesBody.innerHTML = state.batches.map(batch => {
        const item = (batch.items || [])[0] || {};
        const phone = state.phones.find(entry => entry.phone_id === item.phone_id);
        const type = phone ? accountLabel(phone.account_type) : "-";
        const phoneLabel = phoneTableLabel(phone, item.phone_id || "");
        const failure = item.failure_reason || batch.error_message || "";
        return `
          <tr>
            <td>${escapeHtml(batch.batch_id)}</td>
            <td>${escapeHtml(phoneLabel)}</td>
            <td>${escapeHtml(type)}</td>
            <td>${escapeHtml(item.video_file || "-")}</td>
            <td>${escapeHtml(item.scheduled_at || "-")}</td>
            <td>${badge(item.import_status || batch.status)}</td>
            <td>${badge(item.task_status || "-")}</td>
            <td>${escapeHtml(failure || "-")}</td>
            <td>${escapeHtml(item.updated_at || batch.updated_at || "-")}</td>
            <td><a href="/status/${encodeURIComponent(batch.batch_id)}/status.xlsx">下载</a></td>
          </tr>
        `;
      }).join("");
    }

    function renderBatches() {
      if (!state.batches.length) {
        batchesBody.innerHTML = '<tr><td colspan="10" class="empty">No batches</td></tr>';
        return;
      }
      batchesBody.innerHTML = state.batches.flatMap(batch => {
        const rows = batch.items && batch.items.length ? batch.items : [{}];
        return rows.map((item, index) => {
          const phone = state.phones.find(entry => entry.phone_id === item.phone_id);
          const type = phone ? accountLabel(phone.account_type) : "-";
          const phoneLabel = phoneTableLabel(phone, item.phone_id || "");
          const failure = item.failure_reason || batch.error_message || "";
          return `
            <tr>
              <td>${index === 0 ? escapeHtml(batch.batch_id) : ""}</td>
              <td>${escapeHtml(phoneLabel)}</td>
              <td>${escapeHtml(type)}</td>
              <td>${escapeHtml(item.video_file || "-")}</td>
              <td>${escapeHtml(item.scheduled_at || "-")}</td>
              <td>${badge(item.import_status || batch.status)}</td>
              <td>${badge(item.task_status || "-")}</td>
              <td>${escapeHtml(failure || "-")}</td>
              <td>${escapeHtml(item.updated_at || batch.updated_at || "-")}</td>
              <td>${index === 0 ? `<a href="/status/${encodeURIComponent(batch.batch_id)}/status.xlsx">download</a>` : ""}</td>
            </tr>
          `;
        });
      }).join("");
    }

    function setMessage(element, text, tone) {
      element.textContent = text || "";
      element.className = tone ? `status-line ${tone}` : "status-line";
    }

    function renderSelectedVideo() {
      if (state.selectedVideoFile) {
        selectedVideoName.textContent = `已选择：${state.selectedVideoFile.name}`;
        videoInput.required = false;
      } else {
        selectedVideoName.textContent = "未选择视频";
        videoInput.required = true;
      }
    }

    function clearSelectedVideo() {
      state.selectedVideoFile = null;
      videoInput.value = "";
      renderSelectedVideo();
    }

    videoInput.addEventListener("change", () => {
      const nextFile = videoInput.files && videoInput.files.length ? videoInput.files[0] : null;
      if (nextFile) {
        state.selectedVideoFile = nextFile;
        setMessage(submitMessage, "", "");
      } else if (state.selectedVideoFile) {
        setMessage(submitMessage, `已保留原视频：${state.selectedVideoFile.name}`, "warn");
      }
      renderSelectedVideo();
    });

    phoneSelect.addEventListener("change", () => {
      setMessage(submitMessage, "", "");
      state.selectedPhone = state.phones.find(phone => phone.phone_id === phoneSelect.value) || null;
      applyPhoneMode();
    });

    document.getElementById("refreshBtn").addEventListener("click", refreshAll);

    syncPhonesBtn.addEventListener("click", async () => {
      setMessage(submitMessage, "正在同步手机名称", "");
      syncPhonesBtn.disabled = true;
      try {
        const result = await requestJson("/api/phones/sync", {method: "POST"});
        if (result.sync?.error) {
          throw new Error(result.sync.error);
        }
        state.phones = result.phones || [];
        state.phoneSync = result.sync || null;
        renderPhones();
        renderStats();
        renderBatches();
        const syncedAt = state.phoneSync?.synced_at || "";
        const mode = state.phoneSync?.mode || "";
        setMessage(submitMessage, `手机名称已同步 ${mode} ${syncedAt}`, "ok");
      } catch (error) {
        setMessage(submitMessage, error.message, "error");
      } finally {
        syncPhonesBtn.disabled = false;
      }
    });

    document.getElementById("taskForm").addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!state.selectedPhone) return;
      if (!state.selectedVideoFile) {
        renderSelectedVideo();
        setMessage(submitMessage, "请先选择视频", "error");
        return;
      }
      setMessage(submitMessage, "正在提交", "");
      submitBtn.disabled = true;
      try {
        const formData = new FormData(form);
        formData.set("video", state.selectedVideoFile, state.selectedVideoFile.name);
        if (state.selectedPhone.account_type !== "showcase") {
          formData.delete("product_search_title");
          formData.delete("product_publish_name");
        }
        const result = await requestJson("/api/tasks", {method: "POST", body: formData});
        setMessage(submitMessage, `已提交：${result.batch_id}`, "ok");
        const keepPhone = phoneSelect.value;
        form.reset();
        clearSelectedVideo();
        phoneSelect.value = keepPhone;
        state.selectedPhone = state.phones.find(phone => phone.phone_id === keepPhone) || null;
        applyPhoneMode();
        await refreshAll();
      } catch (error) {
        setMessage(submitMessage, error.message, "error");
      } finally {
        applyPhoneMode();
      }
    });

    document.getElementById("batchForm").addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.currentTarget;
      setMessage(batchMessage, "正在上传批次", "");
      try {
        const formData = new FormData(form);
        formData.set("auto_import", "1");
        const result = await requestJson("/api/upload", {method: "POST", body: formData});
        setMessage(batchMessage, `已导入：${result.batch_id}`, "ok");
        form.reset();
        await refreshAll();
      } catch (error) {
        setMessage(batchMessage, error.message, "error");
      }
    });

    updateSaoPauloClock();
    setInterval(updateSaoPauloClock, 1000);
    refreshAll();
    setInterval(refreshAll, 5000);
  </script>
</body>
</html>
"""


ACCOUNT_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>账号管理 - 群控提交</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f4f6f8;
      --panel: #ffffff;
      --text: #182230;
      --muted: #667085;
      --line: #d9e1ec;
      --line-strong: #b8c4d4;
      --accent: #0f766e;
      --accent-dark: #0b5f59;
      --green: #067647;
      --green-soft: #ecfdf3;
      --amber: #b54708;
      --amber-soft: #fff7ed;
      --red: #b42318;
      --red-soft: #fef3f2;
      --shadow: 0 12px 30px rgba(16, 24, 40, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--page);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    .shell {
      max-width: 1120px;
      margin: 0 auto;
      padding: 24px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }
    h1, p { margin: 0; }
    h1 {
      font-size: 26px;
      line-height: 1.25;
      font-weight: 750;
    }
    .subtitle {
      margin-top: 6px;
      color: var(--muted);
      line-height: 1.6;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    button, .button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      min-height: 40px;
      padding: 9px 14px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    button.secondary, .button.secondary {
      background: #fff;
      color: var(--text);
      border: 1px solid var(--line-strong);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .panel-header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }
    .panel-title {
      display: grid;
      gap: 4px;
    }
    .panel-title strong {
      font-size: 17px;
    }
    .panel-title span {
      color: var(--muted);
      font-size: 12px;
    }
    .panel-body {
      padding: 16px 18px 18px;
      display: grid;
      gap: 10px;
    }
    .phone-row {
      display: grid;
      grid-template-columns: minmax(180px, 1.2fr) minmax(180px, 1.4fr) 132px 90px;
      gap: 10px;
      align-items: end;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    label {
      display: grid;
      gap: 7px;
      font-weight: 650;
    }
    label span, .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 500;
    }
    input, select {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      min-height: 40px;
      padding: 9px 10px;
      font: inherit;
      outline: none;
      background: #fff;
      color: var(--text);
    }
    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #eef2f6;
      color: #475467;
      font-size: 12px;
      font-weight: 700;
    }
    .badge.online_idle {
      background: var(--green-soft);
      color: var(--green);
    }
    .badge.running, .badge.pending {
      background: var(--amber-soft);
      color: var(--amber);
    }
    .badge.offline, .badge.disabled, .badge.removed {
      background: var(--red-soft);
      color: var(--red);
    }
    .status-line {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
      align-self: center;
    }
    .status-line.ok { color: var(--green); }
    .status-line.error { color: var(--red); }
    .empty {
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }
    @media (max-width: 760px) {
      .shell { padding: 16px 12px 22px; }
      .topbar { flex-direction: column; }
      .toolbar { justify-content: flex-start; }
      .panel-header { align-items: flex-start; flex-direction: column; }
      .phone-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <div>
        <h1>账号管理</h1>
        <p class="subtitle">维护提交页可见的账号名和账号类型。任务提交页只展示账号名、类型和状态。</p>
      </div>
      <div class="toolbar">
        <a class="button secondary" href="/">返回任务提交</a>
        <button class="secondary" type="button" id="syncBtn">同步手机名称</button>
        <button class="secondary" type="button" id="refreshBtn">刷新</button>
      </div>
    </div>

    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">
          <strong>手机账号列表</strong>
          <span>账号名建议保持唯一，例如：巴西营销01、巴西橱窗01。</span>
        </div>
        <span class="status-line" id="message"></span>
      </div>
      <div class="panel-body" id="phoneList">
        <div class="empty">正在加载</div>
      </div>
    </section>
  </main>

  <script>
    const state = { phones: [] };
    const phoneList = document.getElementById("phoneList");
    const message = document.getElementById("message");
    const syncBtn = document.getElementById("syncBtn");

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char]));
    }

    function setMessage(text, tone = "") {
      message.textContent = text || "";
      message.className = tone ? `status-line ${tone}` : "status-line";
    }

    function accountLabel(value) {
      if (value === "marketing") return "营销号";
      if (value === "showcase") return "橱窗号";
      return value || "未知";
    }

    function badge(value) {
      const clean = String(value || "-");
      return `<span class="badge ${escapeHtml(clean)}">${escapeHtml(clean)}</span>`;
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, options);
      const text = await response.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error(text || `HTTP ${response.status}`);
      }
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      return payload.data;
    }

    async function refreshPhones() {
      setMessage("正在读取手机");
      try {
        state.phones = await requestJson("/api/phones");
        renderPhones();
        setMessage(`已加载 ${state.phones.length} 台手机`, "ok");
      } catch (error) {
        setMessage(error.message, "error");
      }
    }

    function renderPhones() {
      if (!state.phones.length) {
        phoneList.innerHTML = '<div class="empty">暂无手机</div>';
        return;
      }
      phoneList.innerHTML = state.phones.map(phone => {
        const accountType = phone.account_type || "marketing";
        const systemName = phone.device_name || "未读取设备名";
        return `
          <div class="phone-row" data-phone-id="${escapeHtml(phone.phone_id || "")}">
            <label>
              识别参考
              <input value="${escapeHtml(systemName)}" disabled>
              <span>仅供群控电脑管理员识别，不在提交下拉框展示。</span>
            </label>
            <label>
              账号名
              <input class="account-name" type="text" value="${escapeHtml(phone.account_name || "")}" placeholder="例如：巴西营销01">
              <span>同事提交任务时看到的是这个名称。</span>
            </label>
            <label>
              类型
              <select class="account-type">
                <option value="marketing"${accountType === "marketing" ? " selected" : ""}>营销号</option>
                <option value="showcase"${accountType === "showcase" ? " selected" : ""}>橱窗号</option>
              </select>
              <span>${accountLabel(accountType)} · ${badge(phone.current_status)}</span>
            </label>
            <button class="save-phone" type="button">保存</button>
          </div>
        `;
      }).join("");
    }

    phoneList.addEventListener("click", async event => {
      const button = event.target.closest(".save-phone");
      if (!button) return;
      const row = button.closest("[data-phone-id]");
      if (!row) return;
      const phoneId = row.dataset.phoneId || "";
      const accountName = row.querySelector(".account-name")?.value || "";
      const accountType = row.querySelector(".account-type")?.value || "";
      button.disabled = true;
      setMessage("正在保存");
      try {
        const phone = await requestJson(`/api/phones/${encodeURIComponent(phoneId)}/account`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({account_name: accountName, account_type: accountType})
        });
        state.phones = state.phones.map(item => item.phone_id === phone.phone_id ? {...item, ...phone} : item);
        renderPhones();
        setMessage("已保存", "ok");
      } catch (error) {
        setMessage(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById("refreshBtn").addEventListener("click", refreshPhones);
    syncBtn.addEventListener("click", async () => {
      syncBtn.disabled = true;
      setMessage("正在同步手机名称");
      try {
        const result = await requestJson("/api/phones/sync", {method: "POST"});
        if (result.sync?.error) {
          throw new Error(result.sync.error);
        }
        state.phones = result.phones || [];
        renderPhones();
        setMessage("手机名称已同步", "ok");
      } catch (error) {
        setMessage(error.message, "error");
      } finally {
        syncBtn.disabled = false;
      }
    });

    refreshPhones();
  </script>
</body>
</html>
"""
