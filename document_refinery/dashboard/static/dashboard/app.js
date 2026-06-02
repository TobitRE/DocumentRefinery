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
            const payload = await apiFetch(
              "/v1/docling/options/resolve/",
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ profile, options_json: readJson(textarea) }),
              },
              document
            );
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
            form.append("ingest", qs("#uploadIngest", page)?.checked ? "true" : "false");
            if (externalUuid) form.append("external_uuid", externalUuid);
            if (profile) form.append("profile", profile);
            if (Object.keys(options).length) form.append("options_json", JSON.stringify(options));
            results.push(await apiFetch("/v1/documents/", { method: "POST", body: form }, page));
          }
          renderPanel(result, files.length === 1 ? results[0] : { uploads: results });
          if (status) {
            status.textContent = "Upload request completed.";
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
  }

  function initJobDetailPage(root = document) {
    const page = qs("[data-job-detail-page]", root);
    if (!page) return;
    const output = qs("[data-artifact-preview-output]", page);
    const status = qs("[data-artifact-preview-status]", page);
    qsa("[data-artifact-preview]", page).forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const payload = await apiFetch(`/v1/artifacts/${btn.dataset.artifactPreview}/preview/`, {}, page);
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
        const payload = await apiFetch(
          `/v1/documents/${documentId}/compare/`,
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
        const payload = await apiFetch(`/v1/jobs/?comparison_id=${encodeURIComponent(comparisonId)}`, {}, page);
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
    initApiKeyPanel();
    initDoclingOptionsControls();
    initUploadPage();
    initJobDetailPage();
    initComparisonPage();
    initCopyButtons();
  }

  const dashboardApi = {
    apiFetch,
    badge,
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
