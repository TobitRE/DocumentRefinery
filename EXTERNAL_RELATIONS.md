# External Relations and Test Gaps

This document lists external systems the project depends on and the current testing strategy.
It helps identify weak points when debugging production issues.

## External Dependencies

- **ClamAV (clamd)**
  - Used in `documents.tasks.scan_pdf_task` via `clamav_client`.
  - **Tests**: Mocked responses in pipeline tests.
  - **Gap**: No integration tests against a live ClamAV daemon.

- **Docling (conversion + exports)**
  - Used in `documents.tasks.docling_convert_task` and `export_artifacts_task`.
  - **Tests**: Unit tests patch converter behavior and validate export logic.
  - **Gap**: No end-to-end test against real Docling for OCR or PDF edge cases.

- **Celery Broker (Redis)**
  - Used for async task execution and webhook retry scheduling.
  - **Tests**: Tasks executed directly (`.apply`) without broker.
  - **Gap**: Retry scheduling via broker not tested in CI.

- **Filesystem / Storage**
  - Used for upload, quarantine, clean files, and artifacts.
  - **Tests**: Temporary filesystem used for unit tests.
  - **Gap**: No integration tests with real storage volumes or permission failures.

- **Nginx X-Accel-Redirect**
  - Artifact download can use `X-Accel-Redirect` for internal files.
  - **Tests**: Header presence is tested; no real nginx integration.

- **System Telemetry (/proc, nvidia-smi)**
  - Dashboard uses `/proc/*` and `nvidia-smi` for system metrics.
  - **Tests**: Helper functions are mocked.
  - **Gap**: No tests on real hardware metrics and GPU drivers.

- **HTTP Webhook Delivery**
  - Uses outbound HTTP requests via `urllib`.
  - **Tests**: Request/response flows are mocked.
  - **Gap**: No integration tests for real TLS/network failures.

## Debugging Notes

- If ingestion fails unexpectedly, check ClamAV availability and Docling errors first.
- Webhook delivery issues are often broker-related (retry scheduling) or network-related.
- Artifact download errors may involve filesystem permissions or nginx config when X-Accel is enabled.
