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
   DOCLING_DEVICE=cpu
   DOCLING_NUM_THREADS=2
   CELERY_WORKER_CONCURRENCY=1
   ```

   `HF_HOME` muss für den Celery-Service-User beschreibbar sein. Docling und RapidOCR laden
   Modellartefakte nach; bei `ProtectHome=read-only` darf der Cache nicht unter `$HOME` liegen.

## Update ausführen

```sh
./deploy/update_document_refinery.sh
```

Das Update-Script führt nach `pip install -r requirements.txt` automatisch aus:

```sh
venv/bin/python -m pip check
venv/bin/python deploy/docling_runtime_check.py
```

## Diagnose und Smoke-Test

Für eine reine Analyse:

```sh
venv/bin/python deploy/docling_runtime_check.py
```

Für einen echten einseitigen Docling-PDF-Smoke:

```sh
venv/bin/python deploy/docling_runtime_check.py --smoke
```

Für maschinenlesbare Ausgabe:

```sh
venv/bin/python deploy/docling_runtime_check.py --json
```

Wenn der Smoke beim Modell-Download scheitert, zuerst prüfen:

```sh
echo "$HF_HOME"
test -w "$HF_HOME" && echo writable
```

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

## Nach dem Update prüfen

```sh
venv/bin/python document_refinery/manage.py test
curl -H "X-Internal-Token: $INTERNAL_ENDPOINTS_TOKEN" http://localhost/healthz
curl -H "X-Internal-Token: $INTERNAL_ENDPOINTS_TOKEN" http://localhost/readyz
```

Danach einen kleinen PDF-Job mit `fast_text` einreichen. Für OCR/Profile mit OCR sollte der erste
Lauf zusätzliche RapidOCR-Modelle herunterladen; das ist nur dann sauber, wenn `HF_HOME` und die
site-packages-Schreibrechte für den Service-User passen oder die Modelle vorab vorgewärmt wurden.
