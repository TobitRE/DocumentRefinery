(function () {
  const knownOptionKeys = [
    "max_num_pages",
    "max_file_size",
    "exports",
    "do_ocr",
    "do_table_structure",
    "generate_parsed_pages",
    "generate_picture_images",
    "images_scale",
    "ocr_options",
    "ocr",
    "ocr_engine",
    "ocr_languages",
    "force_full_page_ocr",
  ];

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function setText(selectorOrNode, text, root = document) {
    const node = typeof selectorOrNode === "string" ? qs(selectorOrNode, root) : selectorOrNode;
    if (node) node.textContent = text;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    }[char]));
  }

  function statusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["ok", "success", "succeeded", "delivered", "clean"].includes(normalized)) return "status status-ok";
    if (["warn", "warning", "queued", "pending"].includes(normalized)) return "status status-warn";
    if (["running", "retrying", "converting", "exporting", "scanning"].includes(normalized)) return "status status-running";
    if (["fail", "failed", "error", "quarantined", "canceled"].includes(normalized)) return "status status-fail";
    return "status status-neutral";
  }

  function badge(value) {
    const node = document.createElement("span");
    node.className = statusClass(value);
    node.textContent = value || "unknown";
    return node;
  }

  function formatBytes(value) {
    if (value === null || value === undefined || value === "") return "-";
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    if (num < 1024) return `${num} B`;
    if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
    if (num < 1024 * 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(1)} MB`;
    return `${(num / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }

  function formatJson(payload) {
    if (payload === null || payload === undefined || payload === "") return "{}";
    return JSON.stringify(payload, null, 2);
  }

  function readJson(textarea) {
    const raw = (textarea?.value || "").trim();
    if (!raw) return {};
    return JSON.parse(raw);
  }

  function writeJson(textarea, payload) {
    if (!textarea) return;
    textarea.value = Object.keys(payload).length ? formatJson(payload) : "";
  }

  function parseList(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function getApiKey(root = document) {
    return (qs("#apiKeyInput", root)?.value || "").trim();
  }

  function getCookie(name) {
    return document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(`${name}=`))
      ?.slice(name.length + 1) || "";
  }

  function hasDashboardContext(root = document) {
    return Boolean(qs("[data-dashboard-context]", root) || qs("[data-dashboard-context]", document));
  }

  function getDashboardKeyId(root = document) {
    return (
      qs("[data-dashboard-key-select]", root)?.value ||
      qs("[data-dashboard-key-select]", document)?.value ||
      localStorage.getItem("drDashboardApiKeyId") ||
      ""
    );
  }

  function errorMessageFromPayload(payload, fallback) {
    if (!payload || typeof payload !== "object") return fallback;
    if (payload.message) return payload.message;
    if (payload.error_code) return payload.error_code;
    if (payload.details) return `${fallback}: ${formatJson(payload.details)}`;
    return fallback;
  }

  async function apiFetch(path, options = {}, root = document) {
    const key = getApiKey(root);
    if (!key) throw new Error("Add an API key for this page.");
    const headers = options.headers ? { ...options.headers } : {};
    headers.Authorization = `Api-Key ${key}`;
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        message = payload.message || payload.error_code || message;
      } catch (err) {
        // Keep the generic message.
      }
      throw new Error(message);
    }
    return response.json();
  }

  async function dashboardFetch(path, options = {}, root = document) {
    const method = (options.method || "GET").toUpperCase();
    const apiKeyId = getDashboardKeyId(root);
    if (!apiKeyId) throw new Error("Select a tenant context key first.");

    const headers = options.headers ? { ...options.headers } : {};
    let body = options.body;
    let url = new URL(path, window.location.origin);
    if (method === "GET") {
      url.searchParams.set("api_key_id", apiKeyId);
    } else if (body instanceof FormData) {
      if (!body.has("api_key_id")) body.append("api_key_id", apiKeyId);
      headers["X-CSRFToken"] = decodeURIComponent(getCookie("csrftoken"));
    } else {
      let payload = {};
      if (typeof body === "string" && body.trim()) {
        payload = JSON.parse(body);
      } else if (body && typeof body === "object") {
        payload = body;
      }
      payload.api_key_id = payload.api_key_id || apiKeyId;
      body = JSON.stringify(payload);
      headers["Content-Type"] = headers["Content-Type"] || "application/json";
      headers["X-CSRFToken"] = decodeURIComponent(getCookie("csrftoken"));
    }

    const response = await fetch(url.toString(), { ...options, method, headers, body });
    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      const text = await response.text();
      payload = text ? { message: text } : {};
    }
    if (!response.ok) {
      throw new Error(errorMessageFromPayload(payload, `Request failed (${response.status})`));
    }
    return payload;
  }

  function renderDashboardKeyScopes(target, key) {
    if (!target) return;
    target.innerHTML = "";
    (key?.scopes || []).forEach((scope) => {
      const item = document.createElement("span");
      item.className = "status status-neutral";
      item.textContent = scope;
      target.appendChild(item);
    });
  }

  function renderDashboardBillingStatus(target, key) {
    if (!target) return;
    target.innerHTML = "";
    if (!key) return;
    const count = Number(key.dashboard_billable_actions_30d || 0);
    const node = document.createElement("span");
    node.className = count > 0 ? "status status-warn" : "status status-neutral";
    if (count > 0) {
      const last = key.dashboard_billable_last_at
        ? ` Last: ${new Date(key.dashboard_billable_last_at).toLocaleString()}`
        : "";
      node.textContent = `${count} potentially billable dashboard action${count === 1 ? "" : "s"} in the last 30 days.${last}`;
    } else {
      node.textContent = "No potentially billable dashboard actions recorded in the last 30 days.";
    }
    target.appendChild(node);
  }

  function syncTenantActionAvailability(selectedKey) {
    const actionGroups = new Map();
    qsa("[data-action-tenant-id]").forEach((btn) => {
      if (!btn.dataset.actionOriginalLabel) {
        btn.dataset.actionOriginalLabel = btn.textContent.trim();
      }
      btn.textContent = btn.dataset.actionOriginalLabel;
      const requiredTenantId = btn.dataset.actionTenantId;
      const requiredTenantName = btn.dataset.actionTenantName || `tenant #${requiredTenantId}`;
      const requiredScope = btn.dataset.actionScope;
      const selectedTenantId = String(selectedKey?.tenant_id || "");
      const noContext = !selectedTenantId;
      const tenantMismatch = requiredTenantId && String(requiredTenantId) !== selectedTenantId;
      const missingScope = requiredScope && !(selectedKey?.scopes || []).includes(requiredScope);
      let reason = "";
      let shortReason = "";
      if (noContext) {
        reason = "Select a tenant context key to enable this action.";
        shortReason = "Select tenant context.";
      } else if (tenantMismatch) {
        reason = `Select tenant context ${requiredTenantName} to enable this action.`;
        shortReason = `Use ${requiredTenantName} context.`;
      } else if (missingScope) {
        reason = `Selected key needs ${requiredScope}.`;
        shortReason = `Needs ${requiredScope}.`;
      }

      btn.disabled = Boolean(reason);
      if (reason) {
        btn.title = reason;
        delete btn.dataset.actionDisabledReason;
        btn.dataset.actionDisabledReasonFull = reason;
        btn.dataset.actionDisabledReasonShort = shortReason;
      } else {
        btn.removeAttribute("title");
        delete btn.dataset.actionDisabledReason;
        delete btn.dataset.actionDisabledReasonFull;
        delete btn.dataset.actionDisabledReasonShort;
      }

      const group = btn.closest("[data-action-reason-group]");
      if (group) {
        const buttons = actionGroups.get(group) || [];
        buttons.push(btn);
        actionGroups.set(group, buttons);
      }
    });

    actionGroups.forEach((buttons, group) => {
      const reasonTarget = qs(".dr-action-disabled-reason[data-action-disabled-reason]", group);
      if (!reasonTarget) return;
      const reasons = Array.from(
        new Set(buttons.map((btn) => btn.dataset.actionDisabledReasonShort).filter(Boolean))
      );
      reasonTarget.textContent = reasons[0] || "";
      if (reasons.length) {
        reasonTarget.classList.remove("dr-hidden");
      } else {
        reasonTarget.classList.add("dr-hidden");
      }
    });
  }

  async function loadDashboardContext(root = document) {
    const contexts = qsa("[data-dashboard-context]", root);
    if (!contexts.length) return;
    let payload;
    try {
      const response = await fetch("/dashboard/api/context");
      payload = await response.json();
      if (!response.ok) throw new Error(errorMessageFromPayload(payload, "Could not load keys."));
    } catch (err) {
      contexts.forEach((context) => {
        setText("[data-dashboard-context-status]", err.message, context);
      });
      return;
    }

    const storedId = localStorage.getItem("drDashboardApiKeyId");
    const keys = payload.keys || [];
    const storedStillValid = keys.some((key) => String(key.id) === String(storedId));
    const selectedId = storedStillValid ? storedId : payload.default_key_id;
    contexts.forEach((context) => {
      const select = qs("[data-dashboard-key-select]", context);
      if (select) {
        select.innerHTML = "";
        if (!keys.length) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "No active API keys";
          select.appendChild(option);
        }
        keys.forEach((key) => {
          const option = document.createElement("option");
          option.value = key.id;
          option.textContent = `${key.is_dashboard_test_key ? "Test context - " : ""}${key.name} / ${key.tenant_name} (${key.prefix})`;
          option.selected = String(key.id) === String(selectedId);
          select.appendChild(option);
        });
        if (select.value) localStorage.setItem("drDashboardApiKeyId", select.value);
      }
      const selectedKey = keys.find((key) => String(key.id) === String(select?.value));
      setText(
        "[data-dashboard-context-status]",
        selectedKey
          ? `Using ${selectedKey.name} for tenant ${selectedKey.tenant_name}.`
          : "Create an active API key before using dashboard actions.",
        context
      );
      renderDashboardBillingStatus(
        qs("[data-dashboard-billing-status]", context),
        selectedKey
      );
      renderDashboardKeyScopes(qs("[data-dashboard-context-scopes]", context), selectedKey);
      syncTenantActionAvailability(selectedKey);
    });
    document.dispatchEvent(new CustomEvent("dr:dashboard-context-ready"));
  }

  function initDashboardContext(root = document) {
    qsa("[data-dashboard-context]", root).forEach((context) => {
      qs("[data-dashboard-context-refresh]", context)?.addEventListener("click", () => {
        loadDashboardContext(root);
      });
      qs("[data-dashboard-key-select]", context)?.addEventListener("change", (event) => {
        localStorage.setItem("drDashboardApiKeyId", event.target.value);
        loadDashboardContext(root);
      });
    });
    loadDashboardContext(root);
  }

  function initApiKeyPanel(root = document) {
    const panel = qs("[data-api-key-panel]", root);
    if (!panel) return;
    const input = qs("#apiKeyInput", panel);
    const status = qs("[data-api-key-status]", panel) || qs("#statusLine", panel) || qs("#statusLine", root);
    const saveBtn = qs("[data-api-key-use]", panel) || qs("#saveKeyBtn", panel);
    const clearBtn = qs("[data-api-key-clear]", panel) || qs("#clearKeyBtn", panel);
    const refreshBtn = qs("[data-api-refresh]", panel) || qs("#refreshBtn", panel);

    if (saveBtn) {
      saveBtn.addEventListener("click", () => {
        if (!input?.value.trim()) {
          if (status) {
            status.textContent = "Paste an API key before using this page.";
            status.className = "error";
          }
          return;
        }
        if (status) {
          status.textContent = "API key ready for this page.";
          status.className = "muted";
        }
        document.dispatchEvent(new CustomEvent("dr:api-key-ready"));
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        if (input) input.value = "";
        if (status) {
          status.textContent = "API key cleared from this page.";
          status.className = "muted";
        }
      });
    }

    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("dr:refresh-requested"));
      });
    }
  }

  function optionWasTouched(input) {
    return input?.dataset.optionTouched === "true";
  }

  function optionShouldBeIncluded(input, onlyTouched) {
    return Boolean(input) && (!onlyTouched || optionWasTouched(input));
  }

  function clearOptionTouched(root) {
    qsa("[data-option-key], [data-export-option]", root).forEach((input) => {
      delete input.dataset.optionTouched;
    });
  }

  function resetDoclingOptionsControls(root) {
    qsa("[data-docling-options-controls]", root).forEach((controls) => {
      const status = qs("[data-options-status]", controls);
      const resolveOutput = qs("[data-options-resolve-output]", controls);
      if (status) {
        status.textContent = "Ready.";
        status.className = "muted";
      }
      if (resolveOutput) resolveOutput.textContent = "{}";
      clearOptionTouched(controls);
    });
  }

  function markOptionTouched(input) {
    if (input) input.dataset.optionTouched = "true";
  }

  function readStructuredOptions(root, { onlyTouched = false } = {}) {
    const payload = {};
    const maxPagesInput = qs('[data-option-key="max_num_pages"]', root);
    const maxSizeInput = qs('[data-option-key="max_file_size"]', root);
    const imagesScaleInput = qs('[data-option-key="images_scale"]', root);
    const maxPages = maxPagesInput?.value;
    const maxSize = maxSizeInput?.value;
    const imagesScale = imagesScaleInput?.value;

    if (optionShouldBeIncluded(maxPagesInput, onlyTouched) && maxPages !== "") {
      payload.max_num_pages = Number.parseInt(maxPages, 10);
    }
    if (optionShouldBeIncluded(maxSizeInput, onlyTouched) && maxSize !== "") {
      payload.max_file_size = Number.parseInt(maxSize, 10);
    }
    if (optionShouldBeIncluded(imagesScaleInput, onlyTouched) && imagesScale !== "") {
      payload.images_scale = Number.parseFloat(imagesScale);
    }

    qsa("[data-option-boolean]", root).forEach((input) => {
      if (optionShouldBeIncluded(input, onlyTouched)) {
        payload[input.dataset.optionKey] = Boolean(input.checked);
      }
    });

    const ocrOptions = {};
    const ocrEngineInput = qs('[data-option-key="ocr_engine"]', root);
    const languagesInput = qs('[data-option-key="ocr_languages"]', root);
    const forceFullPageInput = qs('[data-option-key="force_full_page_ocr"]', root);
    if (optionShouldBeIncluded(ocrEngineInput, onlyTouched)) ocrOptions.kind = ocrEngineInput.value || "auto";
    if (optionShouldBeIncluded(languagesInput, onlyTouched)) {
      ocrOptions.lang = parseList(languagesInput.value || "");
    }
    if (optionShouldBeIncluded(forceFullPageInput, onlyTouched)) {
      ocrOptions.force_full_page_ocr = Boolean(forceFullPageInput.checked);
    }
    if (Object.keys(ocrOptions).length) payload.ocr_options = ocrOptions;

    const exportInputs = qsa("[data-export-option]", root);
    const exportsTouched = exportInputs.some((input) => optionWasTouched(input));
    if (!onlyTouched || exportsTouched) {
      const exports = exportInputs.filter((item) => item.checked).map((item) => item.value);
      if (exports.length || exportsTouched) payload.exports = exports;
    }
    return payload;
  }

  function applyStructuredOptions(root, textarea, { onlyTouched = false } = {}) {
    let existing = {};
    const status = qs("[data-options-status]", root);
    try {
      existing = readJson(textarea);
    } catch (err) {
      if (status) {
        status.textContent = `JSON fallback is invalid: ${err.message}`;
        status.className = "error";
      }
      return;
    }
    const merged = { ...existing };
    const structured = readStructuredOptions(root, { onlyTouched });
    if (onlyTouched && structured.ocr_options && typeof merged.ocr_options === "object" && merged.ocr_options) {
      structured.ocr_options = { ...merged.ocr_options, ...structured.ocr_options };
    }
    if (!onlyTouched) knownOptionKeys.forEach((key) => delete merged[key]);
    Object.assign(merged, structured);
    writeJson(textarea, merged);
    if (status) {
      status.textContent = onlyTouched
        ? "Touched structured controls merged into JSON fallback."
        : "Structured controls applied to JSON fallback.";
      status.className = "muted";
    }
    return merged;
  }

  function readEffectiveDoclingOptions(page, textareaSelector) {
    const textarea = qs(textareaSelector, page);
    const controls =
      textarea && qs(`[data-docling-options-controls][data-json-target="${textarea.id}"]`, page);
    if (!controls) return readJson(textarea);
    const merged = applyStructuredOptions(controls, textarea, { onlyTouched: true });
    if (merged === undefined) {
      throw new Error("Fix the JSON fallback before submitting.");
    }
    return merged;
  }

  function loadStructuredControls(root, textarea) {
    let payload = {};
    const status = qs("[data-options-status]", root);
    try {
      payload = readJson(textarea);
    } catch (err) {
      if (status) {
        status.textContent = `JSON fallback is invalid: ${err.message}`;
        status.className = "error";
      }
      return;
    }

    const ocrOptions = payload.ocr_options || {};
    const values = {
      max_num_pages: payload.max_num_pages,
      max_file_size: payload.max_file_size,
      images_scale: payload.images_scale,
      ocr_engine: payload.ocr_engine || ocrOptions.kind || "auto",
      ocr_languages: Array.isArray(payload.ocr_languages)
        ? payload.ocr_languages.join(", ")
        : Array.isArray(ocrOptions.lang)
          ? ocrOptions.lang.join(", ")
          : "",
    };

    Object.entries(values).forEach(([key, value]) => {
      const input = qs(`[data-option-key="${key}"]`, root);
      if (input && value !== undefined && value !== null) input.value = value;
    });

    const booleans = {
      do_ocr: payload.do_ocr ?? payload.ocr,
      do_table_structure: payload.do_table_structure,
      generate_parsed_pages: payload.generate_parsed_pages,
      generate_picture_images: payload.generate_picture_images,
      force_full_page_ocr: payload.force_full_page_ocr ?? ocrOptions.force_full_page_ocr,
    };
    Object.entries(booleans).forEach(([key, value]) => {
      const input = qs(`[data-option-key="${key}"]`, root);
      if (input && value !== undefined && value !== null) input.checked = Boolean(value);
    });

    const selectedExports = new Set(Array.isArray(payload.exports) ? payload.exports : []);
    qsa("[data-export-option]", root).forEach((input) => {
      input.checked = selectedExports.has(input.value);
    });

    if (status) {
      status.textContent = "Structured controls loaded from JSON fallback.";
      status.className = "muted";
    }
    clearOptionTouched(root);
  }

  function initDoclingOptionsControls(root = document) {
    qsa("[data-docling-options-controls]", root).forEach((controls) => {
      const targetId = controls.dataset.jsonTarget;
      const textarea = targetId ? qs(`#${targetId}`, root) : qs("textarea", controls);
      if (!textarea) return;
      const applyBtn = qs("[data-options-apply]", controls);
      const loadBtn = qs("[data-options-load]", controls);
      const resolveBtn = qs("[data-options-resolve]", controls);
      const resolveOutput = qs("[data-options-resolve-output]", controls);

      qsa("[data-option-key], [data-export-option]", controls).forEach((input) => {
        input.addEventListener("input", () => markOptionTouched(input));
        input.addEventListener("change", () => markOptionTouched(input));
      });
      const form = controls.closest("form");
      if (form && form.dataset.doclingOptionsSubmitBound !== "true") {
        form.dataset.doclingOptionsSubmitBound = "true";
        form.addEventListener("submit", (event) => {
          const merged = applyStructuredOptions(controls, textarea, { onlyTouched: true });
          if (merged === undefined) event.preventDefault();
        });
      }

      if (applyBtn) applyBtn.addEventListener("click", () => applyStructuredOptions(controls, textarea));
      if (loadBtn) loadBtn.addEventListener("click", () => loadStructuredControls(controls, textarea));
      if (resolveBtn) {
        resolveBtn.addEventListener("click", async () => {
          const status = qs("[data-options-status]", controls);
          try {
            applyStructuredOptions(controls, textarea);
            const profile = qs("[data-options-profile]", controls)?.value || "";
            const requestOptions = {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ profile, options_json: readJson(textarea) }),
            };
            const payload = hasDashboardContext(document)
              ? await dashboardFetch("/dashboard/api/docling/options/resolve/", requestOptions, document)
              : await apiFetch("/v1/docling/options/resolve/", requestOptions, document);
            if (resolveOutput) resolveOutput.textContent = formatJson(payload);
            if (status) {
              status.textContent = "Effective options resolved by backend.";
              status.className = "muted";
            }
          } catch (err) {
            if (status) {
              status.textContent = err.message;
              status.className = "error";
            }
          }
        });
      }
      loadStructuredControls(controls, textarea);
    });
  }

  function renderPanel(node, payload) {
    if (!node) return;
    node.textContent = typeof payload === "string" ? payload : formatJson(payload);
  }

  function documentFromUploadPayload(payload) {
    return payload?.document || payload;
  }

  function actionRootFor(node) {
    return (
      node?.closest("[data-upload-page]") ||
      node?.closest("[data-job-detail-page]") ||
      node?.closest("[data-comparison-page]") ||
      document
    );
  }

  function profileForAction(root, trigger) {
    const source = trigger?.dataset.ingestProfileSource;
    if (source) return qs(source, root)?.value.trim() || "";
    if (trigger?.dataset.ingestProfile !== undefined) return trigger.dataset.ingestProfile || "";
    return qs("#uploadProfile", root)?.value.trim() || "";
  }

  function optionsForAction(root, trigger) {
    const source = trigger?.dataset.ingestOptionsSource;
    if (source) return readEffectiveDoclingOptions(root, source);
    if (qs("#uploadOptionsJson", root)) return readEffectiveDoclingOptions(root, "#uploadOptionsJson");
    return {};
  }

  function setActionStatus(root, message, className = "muted") {
    const status = qs("[data-job-action-status]", root) || qs("[data-upload-status]", root);
    if (status) {
      status.textContent = message;
      status.className = className;
    }
  }

  function renderActionResult(root, payload) {
    renderPanel(qs("[data-job-action-result]", root) || qs("[data-upload-result]", root), payload);
  }

  async function runDocumentIngest(documentUuid, { root = document, trigger = null, mode = "create_new" } = {}) {
    const profile = profileForAction(root, trigger);
    const options = optionsForAction(root, trigger);
    const payload = { mode, options_json: options };
    if (profile) payload.profile = profile;
    const result = await dashboardFetch(
      `/dashboard/api/documents/${documentUuid}/ingest/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      root
    );
    setActionStatus(root, `Job #${result.job_id} queued for document #${result.document?.id}.`);
    renderActionResult(root, result);
    return result;
  }

  async function retryDashboardJob(jobId, root = document) {
    const result = await dashboardFetch(
      `/dashboard/api/jobs/${jobId}/retry/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      },
      root
    );
    setActionStatus(root, `Job #${jobId} retry queued.`);
    renderActionResult(root, result);
    return result;
  }

  function renderDocumentsTable(page, documents) {
    const table = qs("[data-documents-table]", page);
    if (!table) return;
    table.innerHTML = "";
    if (!documents.length) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="6" class="text-muted">No documents found for the selected key.</td>';
      table.appendChild(row);
      return;
    }
    documents.forEach((item) => {
      const row = document.createElement("tr");
      const latest = item.latest_job;
      const documentId = escapeHtml(item.id);
      const filename = escapeHtml(item.original_filename || "-");
      const uuid = escapeHtml(item.uuid);
      const status = escapeHtml(item.status);
      const origin = item.created_via === "DASHBOARD" ? "Dashboard" : "API";
      const latestHtml = latest
        ? `<a href="/dashboard/jobs/${encodeURIComponent(latest.id)}/">#${escapeHtml(latest.id)}</a> <span class="${statusClass(latest.status)}">${escapeHtml(latest.status)}</span>`
        : '<span class="muted">-</span>';
      row.innerHTML = `
        <td>
          <span class="mono">#${documentId}</span><br />
          <span class="dr-table-filename" title="${filename}">${filename}</span>
          <span class="muted mono dr-table-uuid" title="${uuid}">${uuid}</span>
        </td>
        <td><span class="${statusClass(item.status)}">${status}</span></td>
        <td><span class="${item.created_via === "DASHBOARD" ? "status status-warn" : "status status-neutral"}">${origin}</span></td>
        <td>${latestHtml}</td>
        <td class="mono">${escapeHtml(item.job_count || 0)}</td>
        <td>
          <div class="actions dr-row-actions">
            <button type="button" class="ghost" data-document-ingest-uuid="${uuid}" data-ingest-mode="create_new">Run new job</button>
            <button type="button" class="ghost" data-document-ingest-uuid="${uuid}" data-ingest-mode="reuse_existing">Reuse if same</button>
          </div>
        </td>
      `;
      table.appendChild(row);
    });
  }

  async function loadDocuments(page) {
    if (!qs("[data-documents-table]", page)) return;
    try {
      const payload = await dashboardFetch("/dashboard/api/documents/", {}, page);
      renderDocumentsTable(page, payload.documents || []);
    } catch (err) {
      const table = qs("[data-documents-table]", page);
      if (table) {
        table.innerHTML = `<tr><td colspan="6" class="text-muted">${err.message}</td></tr>`;
      }
    }
  }

  function initUploadPage(root = document) {
    const page = qs("[data-upload-page]", root);
    if (!page) return;
    const status = qs("[data-upload-status]", page);
    const uploadBtn = qs("[data-upload-submit]", page);
    const clearBtn = qs("[data-upload-clear]", page);
    const result = qs("[data-upload-result]", page);

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        qsa("input, textarea, select", page).forEach((input) => {
          if (input.id === "apiKeyInput") return;
          if (input.type === "checkbox") input.checked = input.id === "uploadIngest";
          else if (input.tagName === "SELECT") input.selectedIndex = 0;
          else input.value = "";
        });
        resetDoclingOptionsControls(page);
        if (status) {
          status.textContent = "No upload yet.";
          status.className = "muted";
        }
        renderPanel(result, "No upload yet.");
      });
    }

    if (uploadBtn) {
      uploadBtn.addEventListener("click", async () => {
        try {
          const fileInput = qs("#uploadFile", page);
          const files = Array.from(fileInput?.files || []);
          if (!files.length) throw new Error("Select a PDF before uploading.");
          const externalUuid = qs("#uploadExternalUuid", page)?.value.trim();
          const profile = qs("#uploadProfile", page)?.value.trim();
          const options = readEffectiveDoclingOptions(page, "#uploadOptionsJson");
          if (files.length > 1 && externalUuid) {
            throw new Error("External UUID can only be used with a single uploaded file.");
          }
          const results = [];
          for (const file of files) {
            const form = new FormData();
            form.append("file", file);
            form.append("ingest", "false");
            form.append("duplicate_policy", "return_existing");
            if (externalUuid) form.append("external_uuid", externalUuid);
            if (profile) form.append("profile", profile);
            if (Object.keys(options).length) form.append("options_json", JSON.stringify(options));
            const uploadPayload = await dashboardFetch(
              "/dashboard/api/documents/",
              { method: "POST", body: form },
              page
            );
            const itemResult = { upload: uploadPayload };
            const documentPayload = documentFromUploadPayload(uploadPayload);
            if (qs("#uploadIngest", page)?.checked && documentPayload?.uuid) {
              itemResult.ingest = await runDocumentIngest(
                documentPayload.uuid,
                { root: page, trigger: uploadBtn, mode: "create_new" }
              );
            }
            results.push(itemResult);
          }
          renderPanel(result, files.length === 1 ? results[0] : { uploads: results });
          if (status) {
            status.textContent = "Upload workflow completed.";
            status.className = "muted";
          }
          loadDocuments(page);
        } catch (err) {
          if (status) {
            status.textContent = err.message;
            status.className = "error";
          }
        }
      });
    }

    qs("[data-documents-refresh]", page)?.addEventListener("click", () => loadDocuments(page));
    document.addEventListener("dr:dashboard-context-ready", () => loadDocuments(page));
  }

  function initJobDetailPage(root = document) {
    const page = qs("[data-job-detail-page]", root);
    if (!page) return;
    const output = qs("[data-artifact-preview-output]", page);
    const status = qs("[data-artifact-preview-status]", page);
    qsa("[data-artifact-preview]", page).forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const payload = await dashboardFetch(
            `/dashboard/api/artifacts/${btn.dataset.artifactPreview}/preview/`,
            {},
            page
          );
          renderPanel(output, payload);
          if (status) {
            status.textContent = "Artifact preview loaded.";
            status.className = "muted";
          }
        } catch (err) {
          if (status) {
            status.textContent = err.message;
            status.className = "error";
          }
        }
      });
    });
  }

  function initJobActions(root = document) {
    root.addEventListener("click", async (event) => {
      const ingestBtn = event.target.closest("[data-document-ingest-uuid]");
      if (ingestBtn && root.contains(ingestBtn)) {
        const page = actionRootFor(ingestBtn);
        try {
          ingestBtn.disabled = true;
          setActionStatus(page, "Submitting document job...");
          await runDocumentIngest(ingestBtn.dataset.documentIngestUuid, {
            root: page,
            trigger: ingestBtn,
            mode: ingestBtn.dataset.ingestMode || "create_new",
          });
        } catch (err) {
          setActionStatus(page, err.message, "error");
        } finally {
          ingestBtn.disabled = false;
        }
        return;
      }

      const retryBtn = event.target.closest("[data-job-retry]");
      if (retryBtn && root.contains(retryBtn)) {
        const page = actionRootFor(retryBtn);
        try {
          retryBtn.disabled = true;
          setActionStatus(page, "Submitting retry...");
          await retryDashboardJob(retryBtn.dataset.jobRetry, page);
        } catch (err) {
          setActionStatus(page, err.message, "error");
        } finally {
          retryBtn.disabled = false;
        }
      }
    });
  }

  function initComparisonPage(root = document) {
    const page = qs("[data-comparison-page]", root);
    if (!page) return;
    const status = qs("[data-comparison-status]", page);
    const result = qs("[data-comparison-result]", page);

    const selectedProfiles = () => qsa("[data-compare-profile]:checked", page).map((item) => item.value);

    qs("[data-comparison-run]", page)?.addEventListener("click", async () => {
      try {
        const documentId = qs("#compareDocumentId", page)?.value.trim();
        if (!documentId) throw new Error("Enter a document id.");
        const profiles = selectedProfiles();
        if (!profiles.length) throw new Error("Select at least one profile.");
        const options = readEffectiveDoclingOptions(page, "#compareOptionsJson");
        const payload = await dashboardFetch(
          `/dashboard/api/documents/${documentId}/compare/`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profiles, options_json: options }),
          },
          page
        );
        renderPanel(result, payload);
        if (status) {
          status.textContent = "Comparison queued.";
          status.className = "muted";
        }
      } catch (err) {
        if (status) {
          status.textContent = err.message;
          status.className = "error";
        }
      }
    });

    qs("[data-comparison-fetch]", page)?.addEventListener("click", async () => {
      try {
        const comparisonId = qs("#compareIdInput", page)?.value.trim();
        if (!comparisonId) throw new Error("Enter a comparison id.");
        const payload = await dashboardFetch(
          `/dashboard/api/jobs/?comparison_id=${encodeURIComponent(comparisonId)}`,
          {},
          page
        );
        renderPanel(result, payload);
        if (status) {
          status.textContent = "Comparison jobs loaded.";
          status.className = "muted";
        }
      } catch (err) {
        if (status) {
          status.textContent = err.message;
          status.className = "error";
        }
      }
    });
  }

  function initCopyButtons(root = document) {
    qsa("[data-copy-target]", root).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const target = qs(btn.dataset.copyTarget, root);
        const text = target?.textContent?.trim() || target?.value || "";
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          btn.textContent = "Copied";
        } catch (err) {
          btn.textContent = "Copy failed";
        }
      });
    });
  }

  function init() {
    initDashboardContext();
    initApiKeyPanel();
    initDoclingOptionsControls();
    initUploadPage();
    initJobDetailPage();
    initJobActions();
    initComparisonPage();
    initCopyButtons();
  }

  const dashboardApi = {
    apiFetch,
    badge,
    dashboardFetch,
    formatBytes,
    formatJson,
    readJson,
    renderPanel,
    setText,
    statusClass,
  };
  document.documentElement.dataset.drDashboardReady = "true";
  try {
    if (Object.isExtensible(window)) {
      window.DocumentRefineryDashboard = dashboardApi;
    }
  } catch (err) {
    // Some embedded browser contexts prevent adding properties to window.
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
