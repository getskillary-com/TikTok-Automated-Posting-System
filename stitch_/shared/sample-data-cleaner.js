(function () {
  const CLEAN_MESSAGE = "样本数据已清理。当前页面只显示控制中心 API 返回的真实数据。";

  function pageName() {
    const parts = location.pathname.replace(/\\/g, "/").split("/").filter(Boolean);
    const index = parts.lastIndexOf("stitch_");
    if (index >= 0 && parts[index + 1]) return parts[index + 1];
    return parts[parts.length - 2] || "overview";
  }

  function injectStyles() {
    if (document.getElementById("gc-sample-cleaner-styles")) return;
    const style = document.createElement("style");
    style.id = "gc-sample-cleaner-styles";
    style.textContent = `
      .gc-sample-cleared {
        background: #ffffff;
        border: 1px dashed #cfd7e3;
        border-radius: 8px;
        color: #667085;
        font-size: 13px;
        line-height: 1.5;
        margin-top: 16px;
        padding: 16px;
      }
      [data-gc-sample-removed] { display: none !important; }
    `;
    document.head.appendChild(style);
  }

  function protectedNode(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return true;
    return Boolean(
      node.matches("[data-gc-live-panel]") ||
      node.matches("[data-video-library-app]") ||
      node.matches(".gc-app,.gc-modal,.gc-drawer") ||
      node.matches("[data-gc-sample-placeholder]") ||
      node.closest(".gc-shell-sidebar,.gc-shell-header,.gc-toast-region,.vl-modal,.vl-drawer,.gc-app,.gc-modal,.gc-drawer")
    );
  }

  function removeNode(node) {
    if (!node || protectedNode(node)) return;
    node.dataset.gcSampleRemoved = "true";
    node.remove();
  }

  function ensurePlaceholder(main) {
    if (!main || main.querySelector("[data-gc-sample-placeholder]")) return;
    const hasLivePanel = main.querySelector("[data-gc-live-panel]");
    const hasVideoApp = main.querySelector("[data-video-library-app]");
    const hasControlApp = main.querySelector(".gc-app");
    if (hasLivePanel || hasVideoApp || hasControlApp) return;
    const placeholder = document.createElement("section");
    placeholder.className = "gc-sample-cleared";
    placeholder.dataset.gcSamplePlaceholder = "true";
    placeholder.textContent = CLEAN_MESSAGE;
    main.appendChild(placeholder);
  }

  function clearMainSamples() {
    const main = document.querySelector("main");
    if (!main) return;
    Array.from(main.children).forEach((child) => {
      if (protectedNode(child)) return;
      removeNode(child);
    });
    ensurePlaceholder(main);
  }

  function clearPrototypeLayers() {
    document.querySelectorAll("[data-gc-layer]").forEach(removeNode);
    document.querySelectorAll("body > .fixed, body > aside.fixed, body > div.fixed").forEach((node) => {
      if (node.matches(".gc-shell-header,.gc-shell-sidebar,.vl-modal,.vl-drawer,.gc-modal,.gc-drawer") || node.closest(".gc-shell-header,.gc-shell-sidebar,.vl-modal,.vl-drawer,.gc-modal,.gc-drawer")) return;
      if (node.querySelector("h1,h2,h3,h4,button,.material-symbols-outlined")) removeNode(node);
    });
  }

  function markBody() {
    document.body.dataset.sampleDataCleaned = "true";
    document.body.classList.add("gc-samples-cleaned");
  }

  function clean() {
    injectStyles();
    clearPrototypeLayers();
    clearMainSamples();
    markBody();
  }

  function startObserver() {
    const observer = new MutationObserver(() => {
      window.clearTimeout(startObserver.timer);
      startObserver.timer = window.setTimeout(clean, 30);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    window.setTimeout(() => observer.disconnect(), 5000);
  }

  function boot() {
    clean();
    window.requestAnimationFrame(clean);
    window.setTimeout(clean, 120);
    window.setTimeout(clean, 450);
    startObserver();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
