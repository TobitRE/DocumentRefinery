# Docling 2.96 Upgrade

Diese Anleitung beschreibt das Server-Upgrade von `docling==2.72.0` auf `docling==2.96.1`.
Redis bleibt auf der Python-Client-Linie `7.x`; Django bleibt auf `5.2.x` LTS.

## Zielversionen

Die relevanten Constraints stehen in `requirements.txt`:

```txt
Django>=5.2.14,<5.3
redis>=7,<8
docling==2.96.1
```

## Vor dem Update

1. Backup erstellen:

   ```sh
   ./deploy/update_document_refinery.sh --backup
   ```

2. `.env` prüfen. Diese Werte sollten gesetzt sein:

   ```env
   HF_HOME=/var/lib/docling_service/hf_cache
   DOCLING_CACHE_DIR=/var/lib/docling_service/docling_cache
   DOCLING_ARTIFACTS_PATH=/var/lib/docling_service/docling_artifacts
   DOCLING_DEVICE=cpu
   DOCLING_NUM_THREADS=2
   CELERY_WORKER_CONCURRENCY=1
   ```

   `HF_HOME`, `DOCLING_CACHE_DIR` und `DOCLING_ARTIFACTS_PATH` muessen fuer den
   Celery-Service-User beschreibbar sein und unter `DATA_ROOT` liegen. Das venv und
   `site-packages` sollen nicht beschreibbar sein. Docling-Basismodelle und
   RapidOCR-Modelle werden in `DOCLING_ARTIFACTS_PATH` vorab abgelegt, damit der
   erste Job keine Modelle zur Laufzeit nach `site-packages` oder `$HOME` herunterlaedt.

## Update ausführen

```sh
./deploy/update_document_refinery.sh
```

Das Update-Script führt nach `pip install -r requirements.txt` automatisch aus:

```sh
venv/bin/python -m pip check
venv/bin/python deploy/docling_model_warmup.py --env-file .env
venv/bin/python deploy/docling_runtime_check.py --check-models
venv/bin/python deploy/docling_runtime_check.py --smoke --profile ocr_only
venv/bin/python deploy/docling_runtime_check.py --smoke --profile structured
```

## Diagnose und Smoke-Test

Für eine reine Analyse:

```sh
venv/bin/python deploy/docling_runtime_check.py
```

Für RapidOCR-Modellartefakte:

```sh
venv/bin/python deploy/docling_model_warmup.py --env-file .env --check-only
venv/bin/python deploy/docling_runtime_check.py --check-models
```

Für einen echten einseitigen Docling-PDF-Smoke:

```sh
venv/bin/python deploy/docling_runtime_check.py --smoke
```

Für maschinenlesbare Ausgabe:

```sh
venv/bin/python deploy/docling_runtime_check.py --json
```

Die gleiche Runtime-Sicht ist im Staff-Dashboard verfuegbar:

- `/dashboard/runtime/`: staff-only Runtime Diagnostics mit manueller Smoke-Aktion.
- `/v1/dashboard/runtime`: API-Key-Endpoint mit Scope `dashboard:read`.

Die Dashboard-Smoke-Aktion nutzt ein kleines internes PDF, eine Sperre, ein Rate-Limit und einen
Timeout. Sie prueft keine echten VLM- oder Chunking-Pipelines.

Wenn der Smoke beim Modell-Download scheitert, zuerst prüfen:

```sh
echo "$HF_HOME"
test -w "$HF_HOME" && echo writable
echo "$DOCLING_CACHE_DIR"
test -w "$DOCLING_CACHE_DIR" && echo cache-writable
echo "$DOCLING_ARTIFACTS_PATH"
test -w "$DOCLING_ARTIFACTS_PATH" && echo artifacts-writable
```

RapidOCR darf nicht versuchen, nach
`venv/lib/python*/site-packages/rapidocr/models/` zu schreiben. Wenn dort ein
`Read-only file system`-Fehler erscheint, ist der Artefaktpfad nicht gesetzt, nicht
beschreibbar, oder der OCR-Warmup wurde nicht erfolgreich ausgefuehrt.

Wenn der Smoke oder ein Job mit `ImportError: onnxruntime is not installed`
scheitert, ist die RapidOCR-Engine-Dependency im venv unvollstaendig. Nach dem
Pull muss `./deploy/update_document_refinery.sh` `onnxruntime>=1.20,<2` aus
`requirements.txt` installieren. Der Runtime-Check und der Model-Warmup brechen
danach ab, wenn das Backend weiterhin fehlt. Fuer die Standardkonfiguration gilt:

```sh
DOCLING_ALLOWED_OCR_ENGINES=auto,rapidocr
DOCLING_RAPIDOCR_BACKENDS=onnxruntime
```

`easyocr` ist nicht Teil der Standard-Produktionskonfiguration. Es wird vom
Dashboard und von der Optionsvalidierung blockiert, solange es nicht bewusst mit
`pip install easyocr` installiert und ueber `DOCLING_ALLOWED_OCR_ENGINES`
freigeschaltet wurde. Fuer den aktuellen Serverpfad ist RapidOCR mit
ONNX Runtime die abgesicherte OCR-Engine.

Wenn der Smoke auf Apple Silicon oder macOS mit MPS/Torch-Fehlern scheitert, sicherstellen:

```sh
export DOCLING_DEVICE=cpu
export DOCLING_NUM_THREADS=2
```

## Erwartete Verhaltensänderungen

- `partial_success` von Docling wird als fehlgeschlagene Konvertierung behandelt. Das verhindert,
  dass unvollständige Artefakte als erfolgreich veröffentlicht werden.
- `docling==2.96.1` enthält die 2.96.0-Änderungen und zusätzlich Fixes für
  aussagekräftigere FFmpeg-Fehler bei ASR sowie DOCX-Text-Erhalt. DocumentRefinery
  bleibt bis zur geplanten Multi-Format-Erweiterung trotzdem PDF-first.
- Exportfehler werden separat als `DOCLING_EXPORT_FAILED` gespeichert.
- `chunks_json` nutzt weiterhin DocTags-Inhalt, verwendet aber die aktuelle
  `export_to_doctags()`-API statt der deprecated Document-Token-Methode.
- Die Celery-Worker-Concurrency ist initial konservativ auf `1` gesetzt, weil Docling 2.96 intern
  mehrere Threads und Modellkomponenten verwendet.
- Das Dashboard bietet PDF-first Upload, strukturierte Docling-Controls und JSON-Fallback.
  Die Endpunkte `/v1/docling/profiles/`, `/v1/docling/capabilities/` und
  `/v1/docling/options/resolve/` liefern Profil-, Capability- und effektive Optionsdaten.
  Sie sind mit `dashboard:read` oder `documents:write` nutzbar.
- Artefaktvorschauen laufen ueber `/v1/artifacts/{id}/preview/` mit Tenant-Scoping und
  Groessenlimit. `figures_zip` wird nur als ZIP-Metadaten angezeigt; `chunks_json` bleibt ein
  DocTags-Kompatibilitaetspayload, bis echtes Chunking separat implementiert wird.

## Nach dem Update prüfen

```sh
venv/bin/python document_refinery/manage.py test
curl -H "X-Internal-Token: $INTERNAL_ENDPOINTS_TOKEN" http://localhost/healthz
curl -H "X-Internal-Token: $INTERNAL_ENDPOINTS_TOKEN" http://localhost/readyz
```

Danach einen kleinen PDF-Job mit `fast_text` und einen kleinen OCR-Job mit `ocr_only`
einreichen. OCR-Profile duerfen nach dem Update keine Modelle mehr in `site-packages`
herunterladen; die RapidOCR-Modelle muessen bereits unter `DOCLING_ARTIFACTS_PATH`
liegen.
