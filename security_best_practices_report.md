# Security Best Practices Report

Date: 2026-02-07  
Reviewer: Codex (`security-best-practices` skill)  
Scope: `DocumentRefinery` repository (Django backend + dashboard frontend JS)

## Executive Summary

The review found **4 actionable security findings**:

- **1 High** (DOM XSS in staff dashboard rendering path)
- **2 Medium** (API key persistence in `localStorage`; traceback/info disclosure to API clients and webhook recipients)
- **1 Low** (non-constant-time token comparison for internal endpoints)

The highest-priority issue is a client-side injection path that can execute attacker-controlled HTML/JS in the dashboard context and expose API keys stored in the browser.

## High Severity Findings

### 1) [SBP-001] Dashboard DOM XSS via unsafe `innerHTML` on API-sourced fields

- Rule ID: `JS-XSS-001`
- Severity: **High**
- Location:
  - `document_refinery/dashboard/templates/dashboard/index.html:1043`
  - `document_refinery/dashboard/templates/dashboard/index.html:1044`
  - `document_refinery/dashboard/templates/dashboard/index.html:1436`
  - `document_refinery/documents/serializers.py:78`
  - `document_refinery/documents/tasks.py:339`
  - `document_refinery/documents/tasks.py:377`
- Evidence:
  - Dashboard builds HTML with interpolated API values:
    - `const error = job.error_code ? \`${job.error_code}: ${job.error_message || ""}\` : "—";`
    - `jobStatusPanel.innerHTML = \` ... ${error} ... \`;`
  - `job` is fetched from `/v1/jobs/<id>/` and serializer exposes `error_message`.
  - `error_message` is populated from exception text (`str(exc)`) in task failures.
- Impact:
  - If `job.error_message` contains HTML/JS payload (directly or via downstream exception text), script can execute in the dashboard origin, enabling theft of API keys and unauthorized API actions.
- Fix:
  - Replace `innerHTML` rendering with safe DOM APIs (`textContent`, `createElement`, `appendChild`) for dynamic fields.
  - If rich HTML is required, sanitize with a strict allowlist sanitizer before insertion.
- Mitigation:
  - Add a restrictive CSP (preferably response header) to reduce XSS blast radius.
- False positive notes:
  - If all job error text is guaranteed trusted and non-user-influenced this risk drops, but current code paths ingest exception strings from processing of uploaded documents.

## Medium Severity Findings

### 2) [SBP-002] API keys persisted in browser `localStorage`

- Rule ID: `JS-STORAGE-001` (frontend sensitive data handling)
- Severity: **Medium**
- Location:
  - `document_refinery/dashboard/templates/dashboard/index.html:372`
  - `document_refinery/dashboard/templates/dashboard/index.html:397`
- Evidence:
  - `localStorage.getItem("docrefinery_api_key")`
  - `localStorage.setItem("docrefinery_api_key", apiKeyInput.value.trim())`
- Impact:
  - Any successful XSS (including third-party script compromise or future frontend bug) can trivially exfiltrate long-lived API keys.
- Fix:
  - Do not persist API keys in `localStorage` by default.
  - Prefer in-memory storage for the active tab/session; if persistence is required, gate it behind explicit opt-in with strong warning and short key TTL/rotation policy.
- Mitigation:
  - Tight CSP and elimination of `innerHTML` sinks reduce likelihood of token theft.
- False positive notes:
  - Risk is lower on hardened, internal-only admin workstations, but not eliminated.

### 3) [SBP-003] Internal traceback details exposed to tenant-facing APIs and webhooks

- Rule ID: `DJANGO-INFO-001` (error information exposure)
- Severity: **Medium**
- Location:
  - `document_refinery/documents/tasks.py:203`
  - `document_refinery/documents/tasks.py:339`
  - `document_refinery/documents/tasks.py:377`
  - `document_refinery/documents/tasks.py:478`
  - `document_refinery/documents/serializers.py:79`
  - `document_refinery/dashboard/views.py:80`
- Evidence:
  - `_traceback_details()` captures traceback text.
  - Failure paths store traceback in `job.error_details_json`.
  - `JobSerializer` exposes `error_details_json`.
  - Dashboard summary includes `error_details_json` in API payload.
  - Webhook payload includes `error_details`.
- Impact:
  - Reveals internal stack traces, file paths, and implementation details to API consumers and third-party webhook endpoints, aiding targeted exploitation and reconnaissance.
- Fix:
  - Keep full tracebacks server-side only (logs/observability).
  - Expose sanitized error codes/messages externally; optionally include an opaque correlation ID for support.
- Mitigation:
  - Redact sensitive fields before webhook/API serialization.
- False positive notes:
  - If all API consumers and webhook destinations are fully trusted internal systems, severity may be lower.

## Low Severity Findings

### 4) [SBP-004] Internal token comparison is not constant-time

- Rule ID: `DJANGO-AUTH-CT-001`
- Severity: **Low**
- Location:
  - `document_refinery/core/views.py:17`
- Evidence:
  - `if not provided or provided != token:`
- Impact:
  - Standard string comparison can leak timing information about token prefix matches in edge cases.
- Fix:
  - Use `hmac.compare_digest(provided, token)` after basic presence checks.
- Mitigation:
  - Maintain network-layer restrictions and rate limiting for internal endpoints.
- False positive notes:
  - Practical exploitability is often low on internal networks with limited exposure.

## Additional Observations (Configuration Hygiene)

- Local run of `venv/bin/python document_refinery/manage.py check --deploy` reports expected hardening warnings (HSTS/SSL redirect/secure cookies/weak local `SECRET_KEY` in current env).  
- These are environment-dependent and may be acceptable in local/dev, but production deployment should enforce secure values.
