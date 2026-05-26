(function () {
  const DEFAULT_BASE_URL = "http://127.0.0.1:8766/api";

  function normalizeBaseUrl(value) {
    const text = String(value || "").trim();
    return text ? text.replace(/\/+$/, "") : DEFAULT_BASE_URL;
  }

  function currentBaseUrl() {
    try {
      return normalizeBaseUrl(window.localStorage.getItem("controlCenterApiBase"));
    } catch (error) {
      return DEFAULT_BASE_URL;
    }
  }

  function buildUrl(path, params) {
    const cleanPath = String(path || "").startsWith("/") ? path : "/" + path;
    const url = new URL(currentBaseUrl() + cleanPath);
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        return;
      }
      url.searchParams.set(key, String(value));
    });
    return url.toString();
  }

  async function request(method, path, body, params) {
    const options = {
      method,
      headers: { Accept: "application/json" },
    };
    if (body !== undefined && body !== null) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(buildUrl(path, params), options);
    } catch (error) {
      const wrapped = new Error("控制中心未连接，请先运行 python -m control_center serve --host 127.0.0.1 --port 8766");
      wrapped.cause = error;
      throw wrapped;
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error("控制中心返回了无法解析的数据");
    }

    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "控制中心请求失败");
    }
    return payload.data;
  }

  async function requestForm(method, path, formData, params) {
    const options = {
      method,
      headers: { Accept: "application/json" },
      body: formData,
    };

    let response;
    try {
      response = await fetch(buildUrl(path, params), options);
    } catch (error) {
      const wrapped = new Error("控制中心未连接，请先运行 python -m control_center serve --host 127.0.0.1 --port 8766");
      wrapped.cause = error;
      throw wrapped;
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error("控制中心返回了无法解析的数据");
    }

    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "控制中心请求失败");
    }
    return payload.data;
  }
  window.ControlCenterAPI = {
    get baseUrl() {
      return currentBaseUrl();
    },
    setBaseUrl(value) {
      const normalized = normalizeBaseUrl(value);
      window.localStorage.setItem("controlCenterApiBase", normalized);
      return normalized;
    },
    clearBaseUrl() {
      window.localStorage.removeItem("controlCenterApiBase");
      return DEFAULT_BASE_URL;
    },
    buildUrl,
    request,
    requestForm,
    get(path, params) {
      return request("GET", path, null, params);
    },
    post(path, body, params) {
      return request("POST", path, body || {}, params);
    },
    health() {
      return request("GET", "/health");
    },
    summary() {
      return request("GET", "/summary");
    },
    routes() {
      return request("GET", "/routes");
    },
    videos(params) {
      return request("GET", "/videos", null, params);
    },
    importVideo(body) {
      return request("POST", "/videos/import", body || {});
    },
    scanVideoFolder(body) {
      return request("POST", "/videos/scan-folder", body || {});
    },
    uploadVideo(formData) {
      return requestForm("POST", "/videos/upload", formData);
    },
    chooseVideoFolder(body) {
      return request("POST", "/videos/choose-folder", body || {});
    },
    video(videoId) {
      return request("GET", `/videos/${encodeURIComponent(videoId)}`);
    },
    captions(params) {
      return request("GET", "/captions", null, params);
    },
    products(params) {
      return request("GET", "/products", null, params);
    },
    product(productId) {
      return request("GET", `/products/${encodeURIComponent(productId)}`);
    },
    importCaptions(formData) {
      return requestForm("POST", "/captions/import", formData);
    },
    captionBindings(params) {
      return request("GET", "/caption-bindings", null, params);
    },
    phones(params) {
      return request("GET", "/phones", null, params);
    },
    phone(phoneId) {
      return request("GET", `/phones/${encodeURIComponent(phoneId)}`);
    },
    tasks(params) {
      return request("GET", "/tasks", null, params);
    },
    task(taskId) {
      return request("GET", `/tasks/${encodeURIComponent(taskId)}`);
    },
    importTasksCsv(formData) {
      return requestForm("POST", "/tasks/import-csv", formData);
    },
    queuePreview(params) {
      return request("GET", "/queue/preview", null, params);
    },
    runQueueDryRun(body) {
      return request("POST", "/queue/run-once", Object.assign({ dry_run: true }, body || {}));
    },
    runQueue(body) {
      return request("POST", "/queue/run-once", body || {});
    },
    queueWorkersStatus(params) {
      return request("GET", "/queue/workers/status", null, params);
    },
    startQueueWorkers(body) {
      return request("POST", "/queue/workers/start", body || {});
    },
    stopQueueWorkers(body) {
      return request("POST", "/queue/workers/stop", body || {});
    },
    recoverStaleQueueWorkers(body) {
      return request("POST", "/queue/workers/recover-stale", body || {});
    },
    logs(params) {
      return request("GET", "/logs", null, params);
    },
    log(logId, params) {
      return request("GET", `/logs/${encodeURIComponent(logId)}`, null, params);
    },
    failures(params) {
      return request("GET", "/logs/failures", null, params);
    },
    latestLog(params) {
      return request("GET", "/logs/latest", null, params);
    },
    adbStatus() {
      return request("GET", "/adb/status");
    },
    adbDevices(params) {
      return request("GET", "/adb/devices", null, params);
    },
    refreshUsbPhones() {
      return request("POST", "/phones/usb-refresh", {});
    },
    usbMonitor() {
      return request("GET", "/phones/usb-monitor");
    },
    ocrSettings() {
      return request("GET", "/settings/ocr");
    },
    saveOcrSettings(body) {
      return request("POST", "/settings/ocr", body || {});
    },
    uploadConfig() {
      return request("GET", "/upload/config");
    },
    captionPresets() {
      return request("GET", "/upload/caption-presets");
    },
  };
})();

