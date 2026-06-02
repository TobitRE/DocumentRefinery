# Docling Admin Dashboard Planning Context

## Projektkontext

- Projekt: DocumentRefinery
- Django soll auf dem aktuellen LTS-Zweig bleiben.
- Redis bleibt auf Version 7.
- Docling soll auf die aktuelle 2.96.x-Reihe angehoben und sauber bedienbar werden.
- Das Dashboard soll auf dem Tabler Admin Template basieren: <https://tabler.io/admin-template>.
- Tabler ist ein Bootstrap-5-basiertes, responsives Admin-Template mit fertigen Layouts, Formularen, Tabellen, Tabs, Badges, Cards, Dark Mode und Icon-Unterstuetzung.

## Bestehende Funktionen

Das bestehende Projekt hat bereits:

- Profile: `fast_text`, `ocr_only`, `structured`, `full_vlm`
- Dashboard-Upload und Profilvergleich
- Artefakte: `docling_json`, `markdown`, `text`, `doctags`, `chunks_json`, `figures_zip`
- rohe `docling_options_json` Defaults pro Tenant/API-Key
- Operations-Ansicht fuer Queue, Worker, Systemstatus und Fehlermeldungen

## Zu pruefende Dateien

- `document_refinery/documents/profiles.py`
- `document_refinery/authn/options.py`
- `document_refinery/documents/tasks.py`
- `document_refinery/documents/models.py`
- `document_refinery/documents/serializers.py`
- `document_refinery/documents/views.py`
- `document_refinery/dashboard/web_views.py`
- `document_refinery/dashboard/templates/dashboard/base.html`
- `document_refinery/dashboard/templates/dashboard/upload.html`
- `document_refinery/dashboard/templates/dashboard/jobs.html`
- `document_refinery/dashboard/templates/dashboard/job_detail.html`
- `document_refinery/dashboard/templates/dashboard/profile_comparison.html`
- `document_refinery/dashboard/templates/dashboard/profiles.html`
- `document_refinery/dashboard/templates/dashboard/runtime.html`
- `document_refinery/dashboard/templates/dashboard/operations.html`
- `document_refinery/dashboard/templates/dashboard/api_key_new.html`
- `document_refinery/dashboard/templates/dashboard/api_key_detail.html`
- `document_refinery/core/views.py`

## Planungsfragen

### Ist-Zustand

- Welche Docling-Optionen werden aktuell wirklich backendseitig genutzt?
- Welche Profile gibt es und was aktivieren sie tatsaechlich?
- Welche Artefakte werden erzeugt und welche sind echte Docling-Ausgaben?
- Welche Informationen sind im Admin/Dashboard schon sichtbar?
- Welche Informationen sind nur ueber JSON oder gar nicht sichtbar?

### Zielbild fuer das Tabler-Dashboard

- Welche Seiten sollen entstehen?
- Welche bestehenden Seiten sollen ersetzt oder erweitert werden?
- Welche Tabler-Komponenten sollen verwendet werden?
- Empfohlene Navigation:
  - Overview
  - Upload & Jobs
  - Profile Comparison
  - Docling Profiles
  - Runtime Diagnostics
  - API Keys / Tenant Defaults
  - Webhooks
- Layout soll operativ, ruhig und fuer wiederholte Arbeit optimiert sein.
- Kein Marketing-Landing-Page-Charakter.

### Konfigurationsmodell

- Sollen die heutigen festen Profile beibehalten, erweitert oder in ein modelliertes Profil-System ueberfuehrt werden?
- Wie sollen Tenant-/API-Key-Defaults funktionieren?
  - JSON-Fallback behalten
  - strukturierte Controls ergaenzen
  - effektive Optionen vor Jobstart anzeigen
- Optionen, die sicher angeboten werden koennen:
  - OCR an/aus
  - OCR Engine: `auto`, `rapidocr`, `easyocr`, `tesseract`, `tesseract_cli`, `mac`
  - OCR-Sprachen
  - full-page OCR
  - Tabellenstruktur
  - parsed pages/layout
  - picture images
  - image scale
  - exports: `markdown`, `text`, `doctags`, `docling_json`, `chunks_json`, `figures_zip`
- Optionen, die nur angeboten werden sollen, wenn das Backend sie wirklich implementiert:
  - echte VLM-Pipeline-Auswahl
  - TableFormer/VLM Tabellenmodi
  - picture description
  - picture classification
  - ASR/Audio
  - weitere Input-Formate wie DOCX, PPTX, XLSX, HTML

### Docling 2.96.x Feature-Abgleich

- Welche neuen Docling-Funktionen sind fuer DocumentRefinery relevant?
- Welche sind reine Backend-/Runtime-Themen?
- Welche gehoeren in die UI?
- Welche gehoeren nur in Diagnostics?
- Welche sollten bewusst nicht angeboten werden?

### Runtime Diagnostics

Die Planung soll eine Admin-Ansicht vorsehen, die mindestens zeigt:

- `docling` Version
- `docling-core` Version, falls verfuegbar
- `docling-parse` Version, falls verfuegbar
- `DOCLING_DEVICE`
- `DOCLING_NUM_THREADS`
- `HF_HOME`
- Modelcache vorhanden und beschreibbar
- FFmpeg vorhanden
- verfuegbare OCR-Backends soweit pruefbar
- Celery Worker Concurrency
- Redis/Broker Status

Zusaetzlich soll geklaert werden, ob dafuer serverseitige Diagnose-Endpunkte oder Management Commands sinnvoller sind.

### Job- und Artefaktansicht

Pro Job sollen im Zielbild sichtbar werden:

- Docling-Version
- Profil
- effektive Optionen
- Laufzeiten pro Stage
- Status und Fehlerdetails
- Seitenzahl
- Tabellenanzahl
- Bilderanzahl
- Textumfang
- Artefakte

Artefaktvorschau:

- Text/Markdown/DocTags als Preview
- Docling JSON strukturiert oder formatiert
- Figures ZIP nur Download und Metadaten
- Chunks JSON nur dann als echte Chunks anzeigen, wenn echtes Chunking implementiert ist

### Input-Formate

- Pruefe, ob das System PDF-only bleiben soll.
- Wenn neue Docling-Formate geplant werden, benoetigt der Plan explizit:
  - Upload-MIME-Validierung
  - Dateiendungen und Storage-Pfade
  - Security-Pruefung
  - Tests
  - UI-Kennzeichnung pro Format
- Ohne explizite Backend-Erweiterung bleibt die UI PDF-only.

### Backend-Aenderungsplan

Moegliche Backend-Aenderungen, die geplant, aber nicht sofort umgesetzt werden sollen:

- Profile aus Backend in Frontend ausliefern
- Optionsschema zentralisieren
- Validierung erweitern
- JobSerializer um Diagnosefelder ergaenzen
- Operations-Endpoint um Docling Runtime erweitern
- ggf. neues Endpoint fuer Profile/Capabilities
- ggf. echtes Chunking klaeren

### Frontend-Aenderungsplan mit Tabler

Plan fuer Umstieg von bestehenden Templates auf Tabler:

- `base.html` auf Tabler Layout, CSS und JS vorbereiten
- Sidebar oder Topnav gemaess Tabler
- bestehende Karten/Formulare/Tables auf Tabler-Komponenten mappen
- Badges fuer Status/Profile/Artefakte
- Tabs fuer Job Details, Artefakte, Optionen, Errors
- Tabellen fuer Joblisten und Profilvergleiche
- Forms fuer strukturierte Docling-Optionen
- Keine CDN-Abhaengigkeit ohne bewusste Entscheidung
- Klaere, ob Tabler als vendored static asset, npm dependency oder lokales static bundle eingebunden werden soll

### Teststrategie

- Unit-Tests fuer Optionsvalidierung
- Tests fuer Profile und effektive Optionen
- Serializer/API-Tests fuer neue Felder
- Dashboard-Tests fuer neue Seiten und Formularfelder
- Pipeline-Smoke mit kleinem PDF
- Optional Browser-Smoke fuer Tabler UI

### Risiken und Entscheidungen

Festgelegte Entscheidungen:

- Tabler Free/MIT
- Tabler als vendored compiled static bundle, kein CDN
- Docling-Pin: `docling==2.96.1`
- Optionsschema: bestehende JSON-Defaults bleiben kompatibel und warnen bei unbekannten Keys; strukturierte Controls schreiben nur strikt validierte Keys
- Multi-format Upload ja, aber als spaeterer Backend-/Security-Auftrag
- echte VLM-Unterstuetzung ja, aber als spaeterer Auftrag
- echtes Chunking ja, aber als spaeterer Auftrag
- Runtime Smoke im Dashboard ja, als manuelle staff-only Aktion mit Lock, Rate-Limit, Timeout und internem Test-PDF

TODO-Liste fuer spaeter:

- Multi-format Upload: MIME-/Extension-Mapping, Storage-Pfade, Security Review, Formatoptionen, Tests und UI-Kennzeichnung planen
- echte VLM-Unterstuetzung: Modellkatalog, Ressourcenlimits, Timeouts, Cache-Vorwaermung, Artefakte und Tests planen
- echtes Chunking: Chunker-Auswahl, Output-Schema, Artefaktart, Preview und Tests planen
- Runtime Smoke: Endpoint-/Form-POST-Design, CSRF, Locking, Timeout und Ergebnisprotokoll finalisieren
- Tenant-Defaults-Seitenumfang final entscheiden

Erwartete Migrationsrisiken:

- Docling-Ausgaben koennen sich durch neue Parser-/OCR-Versionen strukturell aendern.
- VLM- und OCR-Funktionen koennen Modell-Downloads, mehr RAM und laengere Laufzeiten verursachen.
- Nicht alle neuen Docling-Funktionen passen automatisch zum aktuellen PDF-only-Service.
- Eine Tabler-Umstellung kann Templates, Styling und Dashboard-JavaScript betreffen.

## Akzeptanzkriterien fuer den zu erstellenden Umsetzungsplan

- Der Plan trennt klar zwischen Planung und Implementierung.
- Der Plan nennt konkrete Dateien und betroffene Datenfluesse.
- Der Plan bietet keine UI-Funktionen an, die backendseitig nicht realistisch abgedeckt werden.
- Der Plan beruecksichtigt Tabler als Admin-Template.
- Der Plan haelt Django LTS und Redis 7 unveraendert.
- Der Plan beschreibt Tests und Risiken.
- Der Plan ist so konkret, dass danach ein separater Implementierungsauftrag ausgefuehrt werden kann.
