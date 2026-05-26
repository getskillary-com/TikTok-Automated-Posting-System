(function () {
  const NAV_ITEMS = [
    { page: "overview", label: "总览", icon: "dashboard" },
    { page: "video_library", label: "视频库", icon: "video_library" },
    { page: "copywriting_library", label: "文案库", icon: "description" },
    { page: "product_library", label: "商品库", icon: "inventory_2" },
    { page: "mobile_library", label: "手机库", icon: "smartphone" },
    { page: "publish_task", label: "发布任务", icon: "assignment" },
    { page: "execution_queue", label: "执行队列", icon: "reorder" },
    { page: "audit_logs", label: "运行日志", icon: "history_edu" },
    { page: "system_settings", label: "系统设置", icon: "settings_suggest" },
  ];

  const PAGE_TITLES = {
    overview: "总览",
    video_library: "视频库",
    copywriting_library: "文案库",
    product_library: "商品库",
    mobile_library: "手机库",
    publish_task: "发布任务",
    execution_queue: "执行队列",
    audit_logs: "运行日志",
    system_settings: "系统设置",
  };

  const VALID_PAGES = new Set(NAV_ITEMS.map((item) => item.page));

  function normalizePage(page) {
    const raw = String(page || "").trim().replace(/^\/+/, "").split(/[?&/#]/)[0];
    return VALID_PAGES.has(raw) ? raw : "";
  }

  function pageFromHash() {
    const rawHash = decodeURIComponent(location.hash || "").replace(/^#\/?/, "");
    return normalizePage(rawHash);
  }

  function pageFromPath() {
    const parts = location.pathname.replace(/\\/g, "/").split("/").filter(Boolean);
    const stitchIndex = parts.lastIndexOf("stitch_");
    if (stitchIndex >= 0 && parts[stitchIndex + 1]) {
      return normalizePage(parts[stitchIndex + 1]);
    }
    return normalizePage(parts[parts.length - 2]);
  }

  function currentPage() {
    return pageFromHash() || pageFromPath() || "overview";
  }
  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function injectStyles() {
    if (document.getElementById("gc-layout-normalizer-styles")) {
      return;
    }
    const style = document.createElement("style");
    style.id = "gc-layout-normalizer-styles";
    style.textContent = `
      :root {
        --gc-sidebar-width: 240px;
        --gc-header-height: 56px;
        --gc-page-bg: #f5f7fc;
        --gc-border: #dfe4ee;
        --gc-text: #172033;
        --gc-muted: #667085;
        --gc-primary: #005daa;
      }
      html.light,
      body.gc-shell-ready {
        background: var(--gc-page-bg) !important;
      }
      body.gc-shell-ready {
        color: var(--gc-text) !important;
        min-height: 100vh !important;
        overflow: auto !important;
      }
      body.gc-shell-ready > nav:not(.gc-shell-sidebar),
      body.gc-shell-ready > aside:not(.gc-shell-sidebar):not([data-gc-layer]),
      body.gc-shell-ready > header:not(.gc-shell-header),
      body.gc-shell-ready > div:not(.gc-shell-header):not(.gc-toast-region):not(.fixed) > header:first-child,
      body.gc-shell-ready > main > header:first-child {
        display: none !important;
      }
      body.gc-shell-ready > div:not(.gc-toast-region):not(.fixed):not([data-gc-layer]) {
        box-sizing: border-box !important;
        margin-left: var(--gc-sidebar-width) !important;
        min-height: 100vh !important;
        overflow: visible !important;
        padding-top: var(--gc-header-height) !important;
        width: calc(100% - var(--gc-sidebar-width)) !important;
      }
      body.gc-shell-ready > main {
        box-sizing: border-box !important;
        margin-left: var(--gc-sidebar-width) !important;
        min-height: 100vh !important;
        padding: calc(var(--gc-header-height) + 24px) 24px 24px !important;
        width: calc(100% - var(--gc-sidebar-width)) !important;
      }
      body.gc-shell-ready main {
        background: var(--gc-page-bg) !important;
        box-sizing: border-box !important;
        display: block !important;
        height: auto !important;
        margin-left: 0 !important;
        margin-top: 0 !important;
        max-width: none !important;
        min-height: calc(100vh - var(--gc-header-height)) !important;
        overflow: auto !important;
        padding: 24px !important;
        width: 100% !important;
      }
      body.gc-shell-ready > main {
        flex: 0 0 calc(100% - var(--gc-sidebar-width)) !important;
        margin-left: var(--gc-sidebar-width) !important;
        margin-top: 0 !important;
        padding: calc(var(--gc-header-height) + 24px) 24px 24px !important;
        width: calc(100% - var(--gc-sidebar-width)) !important;
      }
      .gc-shell-sidebar {
        background: #ffffff;
        border-right: 1px solid var(--gc-border);
        bottom: 0;
        display: flex;
        flex-direction: column;
        gap: 10px;
        left: 0;
        padding: 16px 10px;
        position: fixed;
        top: 0;
        width: var(--gc-sidebar-width);
        z-index: 1200;
      }
      .gc-shell-brand {
        border-bottom: 1px solid #edf1f7;
        padding: 10px 12px 16px;
      }
      .gc-shell-brand strong {
        color: #101828;
        display: block;
        font-size: 20px;
        font-weight: 800;
        line-height: 1.2;
      }
      .gc-shell-brand span {
        color: var(--gc-muted);
        display: block;
        font-size: 13px;
        margin-top: 8px;
      }
      .gc-shell-nav {
        display: flex;
        flex: 1;
        flex-direction: column;
        gap: 4px;
        overflow-y: auto;
        padding-top: 8px;
      }
      .gc-shell-nav a {
        align-items: center;
        border-radius: 6px;
        color: #526176;
        display: flex;
        font-size: 14px;
        font-weight: 700;
        gap: 12px;
        min-height: 40px;
        padding: 0 14px;
        text-decoration: none;
        transition: background 120ms ease, color 120ms ease;
      }
      .gc-shell-nav a:hover {
        background: #f3f7ff;
        color: var(--gc-primary);
      }
      .gc-shell-nav a[aria-current="page"] {
        background: #edf4ff;
        box-shadow: inset 3px 0 0 #2563eb;
        color: #155eef;
      }
      .gc-shell-nav .material-symbols-outlined {
        font-size: 21px;
        width: 22px;
      }
      .gc-shell-footer {
        border-top: 1px solid #edf1f7;
        display: grid;
        gap: 8px;
        padding: 12px 8px 0;
      }
      .gc-shell-primary-action {
        align-items: center;
        background: #0067b1;
        border-radius: 6px;
        color: #ffffff;
        display: flex;
        font-size: 14px;
        font-weight: 800;
        gap: 8px;
        justify-content: center;
        min-height: 40px;
        text-decoration: none;
      }
      .gc-shell-primary-action:hover {
        background: #00569a;
      }
      .gc-shell-header {
        align-items: center;
        background: rgba(255, 255, 255, 0.92);
        border-bottom: 1px solid var(--gc-border);
        display: flex;
        gap: 16px;
        height: var(--gc-header-height);
        justify-content: space-between;
        left: var(--gc-sidebar-width);
        padding: 0 22px;
        position: fixed;
        right: 0;
        top: 0;
        z-index: 1100;
      }
      .gc-shell-crumb {
        align-items: center;
        color: var(--gc-muted);
        display: flex;
        flex-shrink: 0;
        font-size: 14px;
        font-weight: 700;
        gap: 10px;
        min-width: 190px;
      }
      .gc-shell-crumb strong {
        color: #101828;
        font-size: 15px;
      }
      .gc-shell-search {
        flex: 1;
        max-width: 420px;
        min-width: 180px;
        position: relative;
      }
      .gc-shell-search .material-symbols-outlined {
        color: #98a2b3;
        font-size: 18px;
        left: 12px;
        position: absolute;
        top: 50%;
        transform: translateY(-50%);
      }
      .gc-shell-search input {
        background: #f1f5fb;
        border: 1px solid transparent;
        border-radius: 6px;
        color: #344054;
        font-size: 13px;
        height: 34px;
        outline: none;
        padding: 0 12px 0 38px;
        width: 100%;
      }
      .gc-shell-search input:focus {
        background: #ffffff;
        border-color: #84adff;
      }
      .gc-shell-actions {
        align-items: center;
        display: flex;
        flex-shrink: 0;
        gap: 10px;
      }
      .gc-shell-adb {
        align-items: center;
        background: #ecfdf3;
        border-radius: 999px;
        color: #067647;
        display: inline-flex;
        font-size: 13px;
        font-weight: 800;
        gap: 7px;
        min-height: 28px;
        padding: 0 11px;
        white-space: nowrap;
      }
      .gc-shell-adb::before {
        background: #12b76a;
        border-radius: 999px;
        content: "";
        height: 8px;
        width: 8px;
      }
      .gc-shell-icon-button {
        align-items: center;
        border-radius: 6px;
        color: #667085;
        display: inline-flex;
        height: 32px;
        justify-content: center;
        text-decoration: none;
        width: 32px;
      }
      .gc-shell-icon-button:hover {
        background: #f2f4f7;
        color: #101828;
      }
      .gc-shell-avatar {
        align-items: center;
        background: #e7eef8;
        border: 1px solid #d7dfeb;
        border-radius: 999px;
        color: #344054;
        display: inline-flex;
        font-size: 13px;
        font-weight: 900;
        height: 32px;
        justify-content: center;
        width: 32px;
      }
      body.gc-shell-ready .gc-live-panel {
        border-radius: 8px !important;
        margin-bottom: 24px !important;
      }
      body.gc-shell-ready table {
        font-variant-numeric: tabular-nums;
      }
      @media (max-width: 900px) {
        :root { --gc-sidebar-width: 72px; }
        .gc-shell-brand strong,
        .gc-shell-brand span,
        .gc-shell-nav a span:not(.material-symbols-outlined),
        .gc-shell-primary-action span:not(.material-symbols-outlined) { display: none; }
        .gc-shell-sidebar { padding: 12px 8px; }
        .gc-shell-brand { padding: 8px 0 14px; }
        .gc-shell-nav a { justify-content: center; padding: 0; }
        .gc-shell-primary-action { min-width: 40px; }
        .gc-shell-crumb { min-width: auto; }
        .gc-shell-search { display: none; }
      }
    `;
    document.head.appendChild(style);
  }

  function navHref(page) {
    return `#/${page}`;
  }

  function renderSidebar(page) {
    const nav = NAV_ITEMS.map((item) => {
      const active = item.page === page ? ' aria-current="page"' : "";
      return `<a data-gc-shell-nav="${escapeHtml(item.page)}" href="${escapeHtml(navHref(item.page))}"${active}><span class="material-symbols-outlined">${escapeHtml(item.icon)}</span>${escapeHtml(item.label)}</a>`;
    }).join("");

    const sidebar = document.createElement("aside");
    sidebar.className = "gc-shell-sidebar";
    sidebar.innerHTML = `
      <div class="gc-shell-brand">
        <strong>群控发布系统</strong>
        <span>v2.4.0 稳定版</span>
      </div>
      <nav class="gc-shell-nav" aria-label="主导航">${nav}</nav>
      <div class="gc-shell-footer">
        <a class="gc-shell-primary-action" href="#/video_library" data-gc-shell-nav="video_library">
          <span class="material-symbols-outlined">video_call</span><span>视频入口</span>
        </a>
        <a class="gc-shell-primary-action" href="#/publish_task" data-gc-shell-nav="publish_task">
          <span class="material-symbols-outlined">rocket_launch</span><span>新建发布任务</span>
        </a>
      </div>
    `;
    return sidebar;
  }

  function renderHeader(page) {
    const title = PAGE_TITLES[page] || "工作台";
    const header = document.createElement("header");
    header.className = "gc-shell-header";
    header.innerHTML = `
      <div class="gc-shell-crumb"><span>群控发布系统</span><span>/</span><strong>${escapeHtml(title)}</strong></div>
      <label class="gc-shell-search">
        <span class="material-symbols-outlined">search</span>
        <input type="search" placeholder="搜索${escapeHtml(title)}..." />
      </label>
      <div class="gc-shell-actions">
        <span class="gc-shell-adb">ADB：运行中</span>
        <a class="gc-shell-icon-button" href="#" aria-label="通知"><span class="material-symbols-outlined">notifications</span></a>
        <a class="gc-shell-icon-button" href="#" aria-label="终端"><span class="material-symbols-outlined">terminal</span></a>
        <a class="gc-shell-icon-button" href="#" aria-label="同步"><span class="material-symbols-outlined">sync</span></a>
        <span class="gc-shell-avatar" aria-label="管理员">管</span>
      </div>
    `;
    return header;
  }

  function removeOldShellArtifacts() {
    document.querySelectorAll(".gc-shell-sidebar,.gc-shell-header").forEach((node) => node.remove());
  }

  function buildHash(page, params) {
    const target = normalizePage(page) || "overview";
    const search = new URLSearchParams(params || {});
    const query = search.toString();
    return `#/${target}${query ? "?" + query : ""}`;
  }

  function setPage(page) {
    const target = normalizePage(page) || "overview";
    removeOldShellArtifacts();
    document.body.classList.add("gc-shell-ready");
    document.body.dataset.gcCurrentPage = target;
    document.body.prepend(renderHeader(target));
    document.body.prepend(renderSidebar(target));
    document.title = `${PAGE_TITLES[target] || "工作台"} - 群控发布系统`;
    window.dispatchEvent(new CustomEvent("gc:page-change", { detail: { page: target } }));
  }

  function navigate(page, params) {
    const nextHash = buildHash(page, params);
    if (location.hash === nextHash) {
      setPage(page);
      return;
    }
    location.hash = nextHash;
  }

  function setupShellNavigation() {
    if (document.body.dataset.gcShellNavigationReady === "true") {
      return;
    }
    document.body.dataset.gcShellNavigationReady = "true";
    document.addEventListener("click", (event) => {
      const link = event.target.closest && event.target.closest("[data-gc-shell-nav]");
      if (!link) {
        return;
      }
      const target = link.getAttribute("data-gc-shell-nav");
      if (!normalizePage(target)) {
        return;
      }
      event.preventDefault();
      navigate(target);
    });
  }

  function boot() {
    injectStyles();
    setupShellNavigation();
    setPage(currentPage());
  }

  window.GroupControlShell = {
    getPage: currentPage,
    navigate,
    setPage,
    pages: NAV_ITEMS.slice(),
  };

  window.addEventListener("hashchange", () => setPage(currentPage()));

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
