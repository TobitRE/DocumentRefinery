# Docling Admin Dashboard Implementation Plan

Stand: 2026-06-02

Diese Datei ist ein Planungsartefakt fuer den spaeteren Umbau des
DocumentRefinery Admin-/Dashboard-Interfaces. Sie beschreibt keine bereits
ausgefuehrte Implementierung. In dieser Planungsphase werden keine Migrationen,
keine Asset-Installation und keine Codeaenderungen vorgenommen.

## 1. Ist-Zustand

### Abhaengigkeiten und Runtime-Ziel

- `requirements.txt` definiert als Ziel `Django>=5.2.14,<5.3`, `redis>=7,<8`
  und `docling==2.96.1`.
- `deploy/DOCLING_2_96_UPGRADE.md` bestaetigt: Django bleibt auf `5.2.x` LTS,
  Redis bleibt auf der Python-Client-Linie `7.x`, Docling-Ziel ist `2.96.1`.
- Die lokale Analyseumgebung zeigte noch abweichende installierte Pakete
  (`docling 2.72.0`, `Django 6.0.1`). Der spaetere Implementierungsauftrag muss
  deshalb zuerst einen Dependency-Sanity-Gate gegen `requirements.txt` einplanen,
  bevor Dashboard-Funktionen gegen Docling 2.96.x verifiziert werden.
- `document_refinery/config/settings.py` enthaelt Docling-relevante Runtime-Werte:
  `DATA_ROOT`, `HF_HOME`, `DOCLING_DEVICE`, `DOCLING_NUM_THREADS`,
  `UPLOAD_MAX_SIZE_MB`, `MAX_PAGES`, Redis/Celery-Konfiguration,
  ClamAV-Konfiguration und `CELERY_WORKER_CONCURRENCY`.

### Aktuelle Profile

Die festen Profile stehen in `document_refinery/documents/profiles.py`:

- `fast_text`: setzt `do_ocr=False`, `do_table_structure=False`,
  `do_picture_description=False`, `do_picture_classification=False`; Exporte:
  `text`, `markdown`, `doctags`.
- `ocr_only`: setzt `do_ocr=True`, `do_table_structure=False`,
  `force_full_page_ocr=True` mit `ocr_options.kind=auto` und leerer `lang`-Liste;
  Exporte: `text`, `markdown`, `doctags`.
- `structured`: setzt `do_ocr=True`, `do_table_structure=True`,
  `generate_parsed_pages=True`; Exporte: `text`, `markdown`, `doctags`,
  `chunks_json`.
- `full_vlm`: setzt `do_ocr=True`, `do_table_structure=True`,
  `generate_picture_images=True`, `images_scale=2.0`; Exporte: `text`,
  `markdown`, `doctags`, `chunks_json`, `figures_zip`.

Wichtig: `full_vlm` aktiviert aktuell keine echte VLM-Pipeline und keine Picture
Description oder Classification; beide Flags stehen im Code auf `False`. Das
Dashboard darf dieses Profil daher nicht als echte VLM-Funktion darstellen. Es
soll als Legacy-/Kompatibilitaetsprofil fuer strukturierte Ausgabe plus
Figure-Images beschrieben werden, bis eine echte VLM-Pipeline backendseitig
implementiert und abgesichert ist.

### Aktuelle Docling-Optionsnutzung

Aktuell werden Optionen an mehreren Stellen verarbeitet:

- `document_refinery/authn/options.py` validiert nur einen kleinen JSON-Subset:
  `max_num_pages`, `max_file_size`, `exports`, `ocr`, `ocr_languages`.
- `document_refinery/documents/views.py` loest Defaults in dieser Reihenfolge auf:
  Request-`options_json`, API-Key-`docling_options_json`,
  Tenant-`docling_options_json`, `settings.DOC_DEFAULT_OPTIONS`.
- `apply_profile_to_options()` ueberschreibt im aufgeloesten JSON nur `exports`.
- `docling_convert_task()` nutzt aus `job.options_json` nur `max_num_pages` und
  `max_file_size`; die eigentlichen `PdfPipelineOptions` werden ausschliesslich
  aus dem Profil gebaut.
- `export_artifacts_task()` nutzt aus `job.options_json` `exports`.
- Die validierten Keys `ocr` und `ocr_languages` sind aktuell reserviert, aber
  nicht an den Converter angebunden.
- Unbekannte JSON-Keys werden validierungsseitig akzeptiert, sind aber aktuell
  wirkungslos, sofern sie nicht explizit im Pipeline-Building verwendet werden.

### Aktuelle Datenmodelle und Artefakte

Betroffene Modelle in `document_refinery/documents/models.py`:

- `Document`: PDF-Metadaten, Speicherpfade, `page_count` ist vorhanden, wird aber
  im aktuellen Pipeline-Code nicht sichtbar befuellt.
- `IngestionJob`: Profil, `comparison_id`, `options_json`, `docling_version`,
  Status, Stage, Laufzeiten (`scan_ms`, `convert_ms`, `export_ms`, `chunk_ms`),
  Fehlerdetails, Worker-/Celery-Felder.
- `Artifact`: Artefakt-Typ, Speicherpfad, Checksum, Groesse, Content-Type.

Aktuelle Artefakte:

- `docling_json`: echte serialisierte DoclingDocument-Ausgabe aus
  `export_to_dict()`.
- `markdown`: echte Docling-Ausgabe aus `export_to_markdown()`.
- `text`: echte Docling-Ausgabe aus `export_to_text()`.
- `doctags`: echte Docling-Ausgabe aus `export_to_doctags()`.
- `chunks_json`: aktuell kein echtes Chunking; es speichert nur
  `{"format": "doctags", "content": "<...>"}`.
- `figures_zip`: ZIP aus den in DoclingDocument vorhandenen Picture-Image-URIs;
  kann leer sein, wenn keine Bilder exportiert wurden.

### Aktuelle API- und Dashboard-Oberflaeche

API:

- `POST /v1/documents/`: Upload, optional `ingest`, optional `profile`,
  optional `options_json`.
- `POST /v1/documents/{id}/compare/`: Vergleich ueber mehrere Profile.
- `GET /v1/jobs/`, `GET /v1/jobs/{id}/`, `POST /v1/jobs/{id}/retry/`,
  `POST /v1/jobs/{id}/cancel/`.
- `GET /v1/artifacts/?job_id=...`, `GET /v1/artifacts/{id}/`.
- `GET /v1/dashboard/summary`, `GET /v1/dashboard/workers`,
  `GET /v1/dashboard/reports/usage`.
- Interne Endpunkte: `/healthz`, `/readyz`, `/metrics`.

Dashboard:

- `document_refinery/dashboard/templates/dashboard/base.html` ist ein
  handgeschriebenes Layout mit inline CSS, eigener Farbwelt und Navigation.
- `/dashboard/` rendert `operations.html`: Queue-Status, Worker, Systemsignale,
  Metrics, Recent Failures/Finished.
- `/dashboard/tools/` rendert `index.html`: API-Key-Eingabe, PDF-Upload,
  Jobstatus, Artefakte, Profilvergleich und Diff in einer grossen Seite.
- API-Key-Seiten haben rohe `docling_options_json`-Textareas und MIME-Typ-Felder.
- Webhook-Seiten sind vorhanden.

## 2. Zielbild mit Tabler

Das Dashboard wird ein operatives Admin-Interface auf Basis des Tabler Admin
Templates. Tabler wird als Bootstrap-5-basiertes Admin-Template mit responsiven
Layouts, Tabellen, Formularen, Tabs, Badges, Cards, Dark Mode und Icon-Support
genutzt. Ziel ist kein Marketing-Landing-Page-Charakter, sondern eine ruhige,
dichte Arbeitsoberflaeche fuer wiederholte Admin- und Tenant-Operationen.

### Zielnavigation

Geplante Seiten und Aufgaben:

- `Overview`: Queue, Durchsatz, Fehler, Worker, wichtigste Runtime-Warnungen.
- `Upload & Jobs`: PDF-Upload, Jobliste, Filter, Jobdetails, Retry/Cancel.
- `Profile Comparison`: Vergleichslaeufe, Profilmatrix, Artefakt-Diff.
- `Docling Profiles`: read-only Profilkatalog, effektive Defaults, Capability
  Badges, bewusst nicht aktive Features.
- `Runtime Diagnostics`: Docling-/System-/Worker-/Cache-/OCR-/FFmpeg-Checks.
- `API Keys / Tenant Defaults`: Scopes, Upload-Grenzen, strukturierte Docling
  Defaults plus JSON-Fallback.
- `Webhooks`: Endpoints und Delivery Logs.

### Routenplanung

Bestehende Routen bleiben kompatibel, koennen aber intern neu aufgeteilt werden:

- `/dashboard/` bleibt Operations/Overview.
- `/dashboard/tools/` bleibt als Einstieg oder Redirect, wird aber in der UI in
  `Upload & Jobs` und `Profile Comparison` aufgeteilt.
- Neu geplant:
  - `/dashboard/jobs/`
  - `/dashboard/jobs/<id>/`
  - `/dashboard/compare/`
  - `/dashboard/profiles/`
  - `/dashboard/runtime/`

### Tabler-Komponenten

Geplante Komponenten:

- Sidebar oder Tabler-Topnav mit aktiven Navigationszustanden.
- Tabler Cards nur fuer echte Panel-/Item-Gruppen, nicht als dekoratives
  Seitenlayout.
- Badges fuer Status, Stage, Profile, Artefakt-Typen und Runtime-Warnungen.
- Tables/Data grids fuer Joblisten, Profile, Artefakte, Webhook Deliveries.
- Tabs fuer Jobdetails: `Overview`, `Artifacts`, `Effective Options`, `Errors`,
  `Runtime`.
- Forms, Switches, Selects, Segmented Controls und Range-/Number-Inputs fuer
  strukturierte Docling-Optionen.
- Alerts fuer nicht unterstuetzte oder runtime-blockierte Features.
- Dark Mode kann ueber Tabler vorbereitet werden, ist aber nicht Kern der ersten
  Umsetzung.

### Asset-Entscheidung

Keine CDN-Abhaengigkeit ohne bewusste Entscheidung. Fuer diese Django-App ist der
bevorzugte Implementierungsweg:

1. Tabler Free/MIT verwenden.
2. Kompiliertes Tabler-CSS/JS und Icons als vendored static assets unter
   `document_refinery/dashboard/static/vendor/tabler/<version>/` ablegen.
3. Ueber Django `staticfiles` und bestehendes `collectstatic` deployen.
4. npm nur einfuehren, wenn spaeter ein echter Asset-Build mit Sass/Tree-Shaking
   benoetigt wird.

## 3. Konfigurationsmodell fuer Docling-Profile und Defaults

### Zielprinzipien

- JSON-Fallback bleibt erhalten.
- Strukturierte Controls duerfen nur Optionen anbieten, die der Backend-Code
  validiert und an Docling weitergibt.
- Optionsschema-Entscheidung: bestehende JSON-Defaults duerfen unbekannte Keys
  weiter enthalten, werden aber mit Warnungen angezeigt; strukturierte Controls
  schreiben nur strikt validierte Keys.
- Effektive Optionen muessen vor Jobstart sichtbar sein.
- Profile bleiben zunaechst als feste Presets erhalten; ein dynamisches
  Datenbank-Profilmodell wird nicht in der ersten Umbauphase eingefuehrt.
- Profilnamen bleiben API-kompatibel. `full_vlm` bleibt als Name erhalten, wird
  aber im UI korrekt als nicht-VLM-faehiges Legacy-Profil erklaert.

### Geplante Optionsebenen

Die spaetere Implementierung soll eine zentrale Resolver-Funktion einplanen:

1. System-Defaults aus `settings.DOC_DEFAULT_OPTIONS`.
2. Tenant-Defaults aus `Tenant.docling_options_json`.
3. API-Key-Defaults aus `APIKey.docling_options_json`.
4. Request-Optionen aus `options_json`.
5. Profil-Preset als gezielte Overrides und Export-Vorgaben.

Das Ergebnis ist `effective_options`. Diese Optionen werden in `IngestionJob` als
Job-spezifischer Snapshot gespeichert. Das bestehende `options_json` kann dafuer
weiter genutzt werden; falls eine getrennte Darstellung noetig wird, ist ein
spaeteres Feld `effective_options_json` zu planen. Wichtig ist, dass der Job
nach Start nicht mehr von geaenderten Tenant-/API-Key-Defaults abhaengt.

### Strukturierte Controls fuer erste Umsetzung

Sicher planbar, sobald die Backendvalidierung und Pipeline-Abbildung umgesetzt
sind:

- Limits:
  - `max_num_pages`
  - `max_file_size`
- OCR:
  - `do_ocr`
  - OCR Engine: `auto`, `rapidocr`, `easyocr`, `tesseract`, `tesseract_cli`,
    `mac`
  - OCR-Sprachen als Liste
  - `force_full_page_ocr`
- Struktur:
  - `do_table_structure`
  - `generate_parsed_pages`
- Bilder:
  - `generate_picture_images`
  - `images_scale`
- Exporte:
  - `docling_json` ist immer erzeugt und daher nicht als abschaltbarer Export
    behandeln.
  - `markdown`, `text`, `doctags`, `chunks_json`, `figures_zip` als waehlebare
    Exporte, mit korrekter Kennzeichnung von `chunks_json`.

### Nicht als Controls anbieten, bis Backend fertig ist

- Echte VLM-Pipeline-Auswahl.
- Picture Description und Picture Classification.
- TableFormer-/Vision-Table-Modi.
- Chart Extraction, Code Enrichment, Formula Enrichment.
- ASR/Audio/Video.
- DOCX, PPTX, XLSX, HTML, Bilder, Markdown, CSV, XML, LaTeX als Uploadformate.
- Remote Services oder externe Plugin-Ausfuehrung.
- Freie Auswahl von Docling-Backends oder beliebigen Modellpfaden.

### Geplante Dateien fuer das Konfigurationsmodell

- `document_refinery/documents/profiles.py`: Profildefinitionen um Metadaten,
  Labels, Beschreibung, Risiko-/Kostenhinweise, Capability Flags und UI-Hints
  erweitern.
- `document_refinery/authn/options.py`: bestehende Validierung beibehalten, aber
  strukturierte Docling-Optionsvalidierung aus einer zentralen Schemafunktion
  aufrufen.
- Neu geplant: `document_refinery/documents/docling_options.py`
  - `DOC_OPTION_SCHEMA`
  - `resolve_effective_options(api_key, request_options, profile)`
  - `validate_effective_options(options)`
  - `build_pdf_pipeline_options(effective_options)`
  - `profile_catalog()`
  - `capabilities_payload()`
- `document_refinery/documents/views.py`: Upload, Ingest und Compare auf den
  zentralen Resolver umstellen.
- `document_refinery/documents/serializers.py`: neue Serializer fuer
  Capabilities, Profile und Options-Resolve.

## 4. Docling 2.96.x Feature-Abgleich

### Externe Faktenbasis

- Docling PyPI fuehrt am 2026-06-02 `2.96.1` als neueste 2.96.x-Version
  (Release 2026-06-01) und `2.96.0` (Release 2026-05-28). Das Repository wird
  auf `docling==2.96.1` gepinnt.
- Docling 2.96.0 changelog-relevant fuer dieses Projekt: threaded
  `docling-parse` PDF backend (v6) und ein Fix fuer JSON Transformers model type.
- Docling 2.96.1 ist fuer Diagnostics relevant: verbesserte Missing-FFmpeg-Fehler
  fuer ASR und ein DOCX-Fix. Diese Fixes werden uebernommen; sie schalten aber
  ohne Backend-Erweiterung keine ASR- oder DOCX-UI frei.
- Docling unterstuetzt deutlich mehr Formate und Features als DocumentRefinery
  aktuell absichert. Diese Features duerfen nicht automatisch im Dashboard
  angeboten werden.

### Bereits implementiert

- PDF-Upload und PDF-Signaturpruefung.
- PDF-Konvertierung via `DocumentConverter`.
- Profilbasierte `PdfPipelineOptions` fuer vier feste Profile.
- OCR-Engine-Klassen sind im Profilcode abbildbar: `auto`, `rapidocr`,
  `easyocr`, `tesseract`, `tesseract_cli`, `mac`.
- Exporte fuer JSON, Markdown, Text, DocTags, Figure ZIP.
- Laufzeitmessung fuer Scan, Convert und Export.
- `partial_success` wird als Fehler behandelt.
- `docling_version` wird pro Job gespeichert.
- Deploy-Diagnostik in `deploy/docling_runtime_check.py`.

### Geplante Erweiterungen

- Zentraler Optionsresolver mit effektiven Optionen.
- Strukturierte Controls fuer sicher abbildbare `PdfPipelineOptions`.
- Runtime Diagnostics fuer `docling`, `docling-core`, `docling-parse`,
  `DOCLING_DEVICE`, `DOCLING_NUM_THREADS`, `HF_HOME`, Modelcache, FFmpeg,
  OCR-Backends, Celery Worker Concurrency und Redis/Broker.
- Jobdetails mit Docling-Versionen, effektiven Optionen, Stage-Laufzeiten,
  Result-Metriken und Artefakten.
- Echter Chunking-Pfad ist als spaetere Erweiterung beschlossen, aber nicht Teil
  des ersten Tabler-/Optionsschema-Umbaus.

### Bewusst nicht angebotene Docling-Funktionen

- Multi-format Upload ist beschlossen, bleibt aber aus der ersten Umsetzung
  ausgeschlossen, bis MIME-, Storage-, Security- und Conversion-Backend
  implementiert sind.
- ASR/Audio/Video bleibt ausgeschlossen; FFmpeg wird nur diagnostisch angezeigt.
- VLM-Pipeline bleibt ausgeschlossen, solange keine Ressourcensteuerung,
  Modellverwaltung, Laufzeitbegrenzung und Exportsemantik implementiert sind.
- Remote Services bleiben ausgeschlossen, weil sie andere Datenschutz- und
  Credential-Grenzen haetten.
- Externe Plugin-Ausfuehrung bleibt ausgeschlossen.
- Chart/Code/Formula Enrichment bleibt ausgeschlossen, bis klare Artefakte,
  Runtime-Kosten und Tests definiert sind.

## 5. Runtime Diagnostics

### Ziel

Eine staff-only Dashboard-Ansicht und ein API-Key-geschuetzter
`dashboard:read`-Endpoint zeigen schnelle, sichere Runtime-Checks. Diese Checks
duerfen keine langen Modell-Downloads, keine beliebigen Shell-Kommandos und
keine usergesteuerten Pfade ausfuehren.

### Geplante Checks

- Package-Versionen:
  - `docling`
  - `docling-core`
  - `docling-parse`
  - `Django`
  - `redis`
- Environment:
  - `DOCLING_DEVICE`
  - `DOCLING_NUM_THREADS`
  - `HF_HOME`
  - `DATA_ROOT`
  - `CELERY_WORKER_CONCURRENCY`
- Filesystem:
  - `HF_HOME` existiert
  - `HF_HOME` beschreibbar
  - `DATA_ROOT` existiert
  - Disk usage fuer `/` und `DATA_ROOT`
- Tools:
  - FFmpeg via `shutil.which("ffmpeg")` und optional `ffmpeg -version` mit
    kurzem Timeout.
  - Tesseract CLI via `shutil.which("tesseract")`.
- OCR-Backends soweit pruefbar:
  - RapidOCR: Import-/Package-Check, kein Modell-Download.
  - EasyOCR: Import-/Package-Check, kein Modell-Download.
  - Tesseract: Package/CLI-Check.
  - Tesseract CLI: CLI-Check.
  - mac OCR: nur auf macOS als theoretisch verfuegbar markieren.
- Celery:
  - Broker-Verbindung wie bisher.
  - Worker online.
  - Worker Concurrency aus `inspect.stats()`.
  - aktive Tasks.
- Redis/Broker:
  - bestehender `current_app.connection().ensure_connection(max_retries=1)`.

### Endpoint vs Management Command

Geplanter Kompromiss:

- Neue Shared-Funktion in `document_refinery/dashboard/runtime.py` fuer schnelle
  Checks.
- Neuer API-Endpoint `GET /v1/dashboard/runtime`.
- Neue Staff-Seite `/dashboard/runtime/`.
- Neues oder angepasstes Management Command, das dieselbe Shared-Funktion nutzt.
- `deploy/docling_runtime_check.py` bleibt fuer Server-Upgrades und optionalen
  Smoke erhalten, soll aber perspektivisch die gemeinsame Checklogik verwenden.
- Runtime Smoke ist beschlossen: Die Dashboard-Seite soll eine manuell
  ausgeloeste, staff-only Smoke-Aktion fuer ein kleines Test-PDF anbieten.
  Automatische Smoke-Conversions bei normalen Page Loads bleiben ausgeschlossen.

### Sicherheit

- Diagnosewerte duerfen keine Secrets anzeigen.
- Pfade werden nur als konfigurierte Runtime-Pfade angezeigt, nicht aus
  Request-Parametern gelesen.
- Teure Checks werden gecacht, z. B. 5 bis 30 Sekunden je nach Check.
- Smoke-Checks mit PDF-Konvertierung brauchen CSRF-geschuetzte Staff-Aktion,
  Rate-Limit/Lock gegen parallele Laeufe, kurzes Timeout, kleines eingebettetes
  PDF, klares Ergebnisprotokoll und keine usergesteuerten Eingabedateien.

## 6. Job- und Artefaktansicht

### Geplante Jobdetails

Pro Job sollen sichtbar werden:

- ID, UUID, external UUID, tenantbezogene Metadaten nur im erlaubten Kontext.
- Dokumentname, MIME-Typ, Groesse, SHA256.
- Status, Stage, Retry-Zustand, Celery Task ID, Worker Hostname.
- Profil und Profilbeschreibung.
- Docling-Versionen:
  - `docling_version` aus bestehendem Feld.
  - geplant: `docling_core_version` und `docling_parse_version`, entweder als
    neue Felder oder als `runtime_json`.
- Effektive Optionen als formatierte JSON-Ansicht plus strukturierte
  Zusammenfassung.
- Laufzeiten:
  - `scan_ms`
  - `convert_ms`
  - `export_ms`
  - `chunk_ms`
  - `duration_ms`
- Fehler:
  - `error_code`
  - `error_message`
  - sichere, begrenzte Anzeige von `error_details_json` fuer Staff oder wenn
    `API_INCLUDE_ERROR_DETAILS` greift.
- Result-Metriken:
  - Seitenzahl
  - Tabellenanzahl
  - Bilderanzahl
  - Textumfang

### Result-Metriken

Die Metriken sollen nicht teuer bei jedem Page Load aus Artefakten rekonstruiert
werden. Geplant:

- Neues JSON-Feld auf `IngestionJob`, z. B. `result_metrics_json`, oder ein
  eigenes kleines Modell nur wenn spaeter historische Queries noetig werden.
- Befuellung in `export_artifacts_task()` nach dem Laden des `DoclingDocument`.
- `Document.page_count` ebenfalls setzen, wenn Docling die Seitenzahl sicher
  liefert.
- Fallback: Wenn alte Jobs keine Metriken haben, zeigt die UI `unknown` und kann
  optional eine read-only Rekonstruktion aus `docling_json` anbieten.

### Artefaktvorschau

Geplante Artefakt-Tabs:

- Text/Markdown/DocTags: Vorschau mit Groessenlimit, monospace, Download-Link.
- Docling JSON: formatierte JSON-Vorschau mit Trunkierung und Download-Link.
- Figures ZIP: kein Inline-Entpacken grosser Daten; nur Download, Groesse,
  Checksum und optional sichere ZIP-Metadaten.
- Chunks JSON: solange kein echtes Chunking implementiert ist, klar als
  `DocTags compatibility payload` kennzeichnen.

Geplanter Backend-Schutz:

- Neuer Preview-Endpoint, z. B. `GET /v1/artifacts/{id}/preview`.
- Tenant-Scoping bleibt wie im bestehenden `ArtifactViewSet`.
- Maximalgroesse fuer Preview, z. B. 256 KB.
- Keine HTML-Ausfuehrung, keine unescaped Markdown-HTML-Injection.
- JSON-Vorschau nur nach parsebarer JSON-Struktur; sonst Text-Fallback.

## 7. Input-Formate und Sicherheitsgrenzen

### Erste Umsetzung bleibt PDF-first

Die erste Umsetzung bleibt PDF-first, weil das Backend aktuell PDF-spezifisch
ist. Multi-format Upload ist als spaetere Erweiterung beschlossen, aber die UI
darf neue Formate erst anbieten, wenn der Backend-Flow abgesichert ist:

- `DEFAULT_ALLOWED_UPLOAD_MIME_TYPES` erlaubt nur `application/pdf` und
  `application/x-pdf`.
- Upload validiert PDF-Signatur mit `%PDF-`.
- Storage-Pfade enden fest auf `.pdf`.
- `DocumentConverter` wird mit `InputFormat.PDF` und `PdfFormatOption`
  konfiguriert, wenn Profiloptionen genutzt werden.
- ClamAV- und Speicherpfade sind auf den aktuellen PDF-Flow abgestimmt.

### Beschlossene spaetere Multi-Format-Erweiterung

Neue Formate duerfen erst implementiert und in der UI angeboten werden, wenn der
Backend-Auftrag explizit diese Punkte enthaelt:

- MIME-Allowlist pro Format in `authn/options.py`.
- Dateiendungs- und Storage-Pfad-Mapping ohne feste `.pdf`-Annahme.
- Content-Sniffing oder formatbezogene Validierung.
- `DocumentConverter(allowed_formats=[...])` mit Format-spezifischen
  `FormatOption`-Klassen.
- Security Review fuer HTML, Office-Dateien, Bilder, Audio/Video und XML.
- Tests fuer Upload, Duplicate Detection, Malware-Scan, Conversion und Artefakte
  pro Format.
- UI-Kennzeichnung pro Format und klare Runtime-Hinweise.

Ohne diese Backend-Erweiterung zeigt das Dashboard keine DOCX/PPTX/XLSX/HTML/
Audio/Bild-Upload-Controls.

## 8. Backend-Aenderungsplan

### Phase Backend 1: Dependency- und Capability-Gate

Betroffene Dateien:

- `requirements.txt`
- `deploy/DOCLING_2_96_UPGRADE.md`
- `deploy/docling_runtime_check.py`
- optional `document_refinery/core/views.py`

Plan:

- Sicherstellen, dass Runtime und CI gegen `Django>=5.2.14,<5.3`,
  `redis>=7,<8` und Docling 2.96.x laufen.
- Docling bleibt fuer Produktion exakt auf `docling==2.96.1` gepinnt. Weitere
  2.96.x-Patches werden nur als expliziter Dependency-Schritt uebernommen.
- `deploy/docling_runtime_check.py --json` als maschinenlesbare Quelle fuer
  Runtime Diagnostics vorbereiten oder in Shared-Code ueberfuehren.
- `core/views.py` kann minimal bleiben; tiefe Diagnostics sollen nicht in
  `/healthz` landen.

### Phase Backend 2: Zentraler Optionsresolver

Betroffene Dateien:

- `document_refinery/documents/docling_options.py` neu
- `document_refinery/documents/profiles.py`
- `document_refinery/authn/options.py`
- `document_refinery/documents/views.py`
- `document_refinery/documents/tests/test_pipeline.py`
- `document_refinery/authn/tests/test_options.py`

Plan:

- Zentrales Schema fuer erlaubte strukturierte Optionen definieren.
- `exports` gegen bekannte Artefakte validieren.
- `ocr_options` normalisieren:
  - `kind`
  - `lang`
  - `force_full_page_ocr`
- Legacy-Keys `ocr` und `ocr_languages` entweder kompatibel mappen oder als
  deprecated anzeigen. Keine stillen Scheinoptionen mehr.
- `build_pdf_pipeline_options(effective_options)` aus effektiven Optionen bauen.
- Profile als Preset plus UI-Metadaten modellieren.
- Upload/Ingest/Compare nutzen denselben Resolver.
- Options-Resolve-Endpoint planen:
  - `POST /v1/docling/options/resolve`
  - Auth: API key
  - Scope: `documents:write` oder `dashboard:read` je nach Nutzung
  - Input: `profile`, `options_json`
  - Output: `effective_options`, `layers`, `warnings`, `profile`

### Phase Backend 3: Profile und Capabilities API

Betroffene Dateien:

- `document_refinery/documents/serializers.py`
- `document_refinery/documents/views.py`
- `document_refinery/documents/urls.py`
- optional `document_refinery/dashboard/views.py`

Plan:

- Neuer Endpoint `GET /v1/docling/profiles` oder
  `GET /v1/dashboard/docling/profiles`.
- Payload enthaelt:
  - Profilname
  - Label
  - Beschreibung
  - Pipeline-Flags
  - Default-Exports
  - Warnungen, z. B. `full_vlm` ist keine echte VLM-Pipeline
  - Ressourcenkategorie
  - Feature-Status: `implemented`, `planned`, `not_offered`
- Profile kommen aus dem Backend; Frontend hardcodet sie nicht mehr.

### Phase Backend 4: Jobdetails und Result-Metriken

Betroffene Dateien:

- `document_refinery/documents/models.py`
- `document_refinery/documents/tasks.py`
- `document_refinery/documents/serializers.py`
- `document_refinery/documents/views.py`
- Migration in `document_refinery/documents/migrations/`

Plan:

- Job-Metadaten erweitern:
  - optional `docling_core_version`
  - optional `docling_parse_version`
  - optional `runtime_json`
  - optional `result_metrics_json`
- `JobSerializer` um `docling_version`, effektive Optionen, Worker-Felder und
  Metriken erweitern.
- In `docling_convert_task()` Runtime-Versionen speichern.
- In `export_artifacts_task()` Result-Metriken erzeugen.
- `chunks_json` nicht als echtes Chunking darstellen.

### Phase Backend 5: Artifact Preview

Betroffene Dateien:

- `document_refinery/documents/views.py`
- `document_refinery/documents/serializers.py`
- `document_refinery/documents/urls.py`
- `document_refinery/documents/tests/test_artifacts.py`

Plan:

- Preview-Endpoint mit Tenant-Scoping und Groessenlimit.
- JSON/Text/Markdown/DocTags-Vorschau.
- ZIP-Metadaten nur sicher und begrenzt.
- Download-Endpoint unveraendert lassen.

### Phase Backend 6: Runtime Diagnostics

Betroffene Dateien:

- `document_refinery/dashboard/runtime.py` neu
- `document_refinery/dashboard/views.py`
- `document_refinery/dashboard/web_views.py`
- `document_refinery/dashboard/urls.py`
- `document_refinery/dashboard/web_urls.py`
- `document_refinery/dashboard/tests.py`
- `deploy/docling_runtime_check.py`

Plan:

- Shared Runtime Check Service bauen.
- `GET /v1/dashboard/runtime` hinzufuegen.
- `/dashboard/runtime/` Staff-Seite hinzufuegen.
- Bestehendes `/dashboard/system` entweder um Docling-Runtime erweitern oder als
  System-Basischeck belassen und Runtime separat fuehren.
- Tests mit Mocks fuer Versionen, Env, `shutil.which`, Broker und Worker.

## 9. Frontend-Aenderungsplan mit Tabler

### Phase Frontend 1: Tabler Basislayout

Betroffene Dateien:

- `document_refinery/dashboard/templates/dashboard/base.html`
- neu: `document_refinery/dashboard/static/dashboard/app.css`
- neu: `document_refinery/dashboard/static/dashboard/app.js`
- neu: `document_refinery/dashboard/static/vendor/tabler/<version>/...`

Plan:

- Inline CSS aus `base.html` entfernen.
- Tabler CSS/JS ueber `{% static %}` einbinden.
- Shell mit Sidebar oder Topnav, Breadcrumbs und Page Header.
- Navigation auf geplante Seiten erweitern.
- Bestehende Routen behalten.
- Dark Mode Toggle nur vorbereiten, nicht als Kernfunktion erzwingen.

### Phase Frontend 2: Overview/Operations migrieren

Betroffene Dateien:

- `operations.html`
- `dashboard/views.py`
- `dashboard/web_views.py`

Plan:

- Aktuelle Stats in Tabler Stat Cards mappen.
- Stages und Worker als Badges/List Groups.
- Recent Failures und Recent Finished als Tabellen.
- Systemstatus nur als Kurzindikator; tiefe Docling-Runtime auf separater Seite.
- Retry-Aktion weiterhin nur fuer retry-faehige Jobs anzeigen.

### Phase Frontend 3: Upload & Jobs trennen

Betroffene Dateien:

- `index.html` aufteilen oder ersetzen
- neu: `jobs.html`, `job_detail.html`, `upload.html`
- `dashboard/web_urls.py`

Plan:

- Upload-Form bleibt in der ersten Umsetzung PDF-only.
- Profile aus Backend-Endpoint laden.
- Strukturierte Options-Controls nur anzeigen, wenn Backend-Capability
  `implemented` meldet.
- Effektive Optionen vor Jobstart ueber Options-Resolve anzeigen.
- Jobliste mit Filtern fuer Status, Stage, Profil, Datum, Document ID,
  Comparison ID.
- Jobdetails mit Tabs fuer Overview, Artifacts, Options, Errors, Runtime.

### Phase Frontend 4: Profile Comparison

Betroffene Dateien:

- neu oder ersetzt: `profile_comparison.html`
- `dashboard/static/dashboard/comparison.js`

Plan:

- Profilauswahl als Tabler Checkbox Cards oder Data Grid.
- Profilbeschreibung und Warn-Badges aus Capabilities API.
- Comparison ID und Jobs als Tabelle.
- Artefaktvergleich mit limitierten Previews.
- `figures_zip` im Vergleich nicht inline darstellen.
- `chunks_json` nur als DocTags-Kompatibilitaetsdaten anzeigen, bis echtes
  Chunking implementiert ist.

### Phase Frontend 5: API Keys / Tenant Defaults

Betroffene Dateien:

- `api_key_new.html`
- `api_key_detail.html`
- optional neue Tenant-Defaults-Seite

Plan:

- Bestehende JSON-Textarea bleibt als Advanced JSON.
- Strukturierte Controls werden oberhalb der JSON-Textarea hinzugefuegt.
- UI schreibt weiterhin ein JSON-Payload, aber nur ueber vom Backend validierte
  Keys.
- MIME-Controls bleiben in der ersten Umsetzung PDF-only.
- Sichtbare Hinweise:
  - JSON-Fallback wird angewendet, wenn strukturierte Controls nicht alle
    Optionen abdecken.
  - Unbekannte JSON-Keys koennen ignoriert oder kuenftig rejected werden; die
    Entscheidung muss im Backend-Schema getroffen werden.

### Phase Frontend 6: Runtime Diagnostics

Betroffene Dateien:

- neu: `runtime.html`
- `dashboard/static/dashboard/runtime.js`

Plan:

- Version Cards fuer Docling/Django/Redis.
- Environment Tabelle.
- Cache-/Filesystem Checks als Status-Badges.
- OCR/FFmpeg Matrix.
- Worker/Broker-Status.
- Warnungen priorisieren:
  - Version mismatch
  - `HF_HOME` fehlt/nicht beschreibbar
  - FFmpeg fehlt
  - keine Worker online
  - Broker down
- Staff-only Smoke-Aktion mit explizitem Button, Lock/Rate-Limit, kurzem Timeout
  und Ergebnisprotokoll. Die Aktion nutzt ein kleines internes Test-PDF und
  keine hochgeladenen Dateien.

## 10. Teststrategie

Keine Tests werden in dieser Planungsphase ausgefuehrt. Fuer die spaetere
Implementierung ist diese Testabdeckung geplant:

### Unit Tests

- `document_refinery/authn/tests/test_options.py`
  - strukturierte Optionstypen
  - erlaubte Exporte
  - Legacy-Mapping fuer `ocr`/`ocr_languages`
  - ungueltige OCR Engine
  - MIME in der ersten Umsetzung weiterhin PDF-only; spaetere Multi-Format-
    Erweiterung erhaelt eigene Format-Tests
- `document_refinery/documents/tests/test_pipeline.py`
  - alle Profile bauen valide `PdfPipelineOptions`
  - effektive Optionen werden in Pipeline-Optionen umgesetzt
  - Profil-Exports ueberschreiben nur definiert
  - `chunks_json` bleibt als Kompatibilitaetsartefakt markiert
- `document_refinery/dashboard/tests.py`
  - Runtime Check Service mit gemockten Versionen, Env, FFmpeg, OCR-Backends,
    Celery, Redis.

### API Tests

- Upload mit strukturierten Optionen erzeugt erwartetes `job.options_json`.
- Options-Resolve-Endpoint zeigt Layer und effektive Optionen.
- Compare verwendet denselben Resolver wie Upload/Ingest.
- JobSerializer enthaelt neue Felder nur im erlaubten Umfang.
- Artifact Preview begrenzt Groesse, scopt Tenant korrekt und escaped Inhalte.
- Runtime Endpoint braucht `dashboard:read`.

### Dashboard Tests

- Staff-only Zugriff bleibt fuer Web-Dashboard.
- Tabler-Basislayout rendert Navigation und aktive Seiten.
- API-Key-Seiten zeigen strukturierte Controls plus JSON-Fallback.
- Upload-Seite bleibt in der ersten Umsetzung PDF-only und zeigt keine neuen
  Format-Controls ohne Backend-Capability.
- Profile-Seite unterscheidet `implemented`, `planned`, `not_offered`.
- Runtime-Seite zeigt Warnungen bei gemocktem Version mismatch.
- Runtime-Seite zeigt die manuelle Smoke-Aktion nur fuer Staff an und rendert
  Ergebnis, Timeout und Fehlerstatus.

### Pipeline Smoke

- Kleines PDF mit `fast_text` nach Docling-2.96.x-Upgrade.
- Optional ein OCR-Profil, aber nur wenn `HF_HOME` beschreibbar ist und
  Modelcache-Verhalten geklaert wurde.
- Kein ASR/VLM-Smoke in der ersten Dashboard-Umsetzung.
- Dashboard-Runtime-Smoke nutzt ein kleines internes PDF, darf nicht parallel
  mehrfach laufen und muss bei Timeout sauber abbrechen.

### Browser Smoke

- Optional nach Frontend-Umbau:
  - `/dashboard/`
  - `/dashboard/jobs/`
  - `/dashboard/runtime/`
  - `/dashboard/api-keys/new/`
- Pruefung auf sichtbare Navigation, keine JS-Fehler, responsive Layouts.

## 11. Risiken, Entscheidungen und spaetere TODOs

### Risiken

- Lokale Runtime kann von `requirements.txt` abweichen. Das muss vor jeder
  Docling-2.96.x-Verifikation sichtbar gemacht werden.
- `settings.py` und Migration-Kommentare tragen Django-6.0.1-Historie, obwohl
  Ziel und Requirements Django 5.2 LTS sind. Die spaetere Umsetzung muss
  sicherstellen, dass keine Django-6-only APIs verwendet werden.
- Docling 2.96.x kann andere JSON-Strukturen, OCR-Ergebnisse oder Parser-
  Laufzeiten liefern als 2.72.x.
- Threaded docling-parse kann Runtime-Verhalten, CPU-Auslastung und Worker-
  Concurrency beeinflussen.
- OCR/VLM/Model-Features koennen Modell-Downloads, mehr RAM, laengere Laufzeiten
  und Cache-Rechteprobleme verursachen.
- `full_vlm` ist als Name irrefuehrend. Umbenennung waere ein API-Break; UI muss
  mit Labels und Warnungen arbeiten.
- Tabler-Umstellung betrifft viele Templates und JS, besonders `index.html`.
- Wenn strukturierte Controls und JSON-Fallback auseinanderlaufen, entstehen
  schwer nachvollziehbare effektive Optionen.
- Artefaktvorschau kann grosse Dateien laden; Preview braucht harte Limits.

### Festgelegte Entscheidungen

- Tabler: Free/MIT.
- Asset-Strategie: vendored compiled static bundle, kein CDN.
- Docling-Pin: `docling==2.96.1`.
- Optionsschema: bestehende JSON-Defaults bleiben kompatibel und zeigen
  Warnungen fuer unbekannte Keys; strukturierte Controls schreiben nur strikt
  validierte Keys.
- Echte Chunking-Integration: ja, aber spaeterer Auftrag.
- Echte VLM-Unterstuetzung: ja, aber spaeterer Auftrag.
- Multi-format Upload: ja, aber spaeterer Auftrag nach Backend-/Security-
  Absicherung.
- Runtime Smoke im Dashboard: ja, als manuelle staff-only Aktion mit Lock,
  Rate-Limit, Timeout und internem Test-PDF.

### TODO-Liste fuer spaeter

- Tenant-Defaults als eigene Seite oder zunaechst nur API-Key-Detail-Erweiterung
  entscheiden.
- Echte Chunking-Integration planen: Chunker-Auswahl, Output-Schema,
  Artefaktart, Preview, Tests und Abgrenzung zum bestehenden `chunks_json`
  Compatibility Payload.
- Echte VLM-Unterstuetzung planen: Pipeline-Auswahl, Modellkatalog, Cache-
  Vorwaermung, Ressourcenlimits, Timeouts, Kostenhinweise, Artefakte und Tests.
- Multi-format Upload planen: DOCX/PPTX/XLSX/HTML/Bild/Audio-Scope,
  MIME-/Extension-Mapping, Storage-Pfade, Security Review, Converter-
  `allowed_formats`, Format-spezifische Tests und UI-Kennzeichnung.
- Runtime Smoke finalisieren: Endpoint- oder Form-POST-Design, Staff-Auth,
  CSRF, Locking, Timeout, Ergebnisprotokoll und Deploy-Abgleich mit
  `deploy/docling_runtime_check.py`.

## 12. Umsetzungsreihenfolge

### Schritt 0: Dependency- und Runtime-Sanity

- Runtime gegen `requirements.txt` abgleichen.
- Docling-2.96.1-Upgradepfad aus `deploy/DOCLING_2_96_UPGRADE.md` verifizieren.
- `deploy/docling_runtime_check.py --json` als spaetere Diagnostics-Basis
  bewerten.
- Keine UI-Funktionen fuer Docling 2.96.x freischalten, solange Runtime mismatch
  besteht.

### Schritt 1: Optionsschema und Profilkatalog

- `documents/docling_options.py` einfuehren.
- Bestehende Profile mit Metadaten und Capability-Status erweitern.
- Effektive Optionen zentral aufloesen.
- Validierung erweitern.
- Tests fuer Optionen und Profile schreiben.

### Schritt 2: Backend-Capabilities und Options-Resolve API

- Profile-/Capabilities-Endpoint einfuehren.
- Options-Resolve-Endpoint einfuehren.
- Upload/Ingest/Compare auf zentralen Resolver umstellen.
- JSON-Fallback beibehalten.
- Tests fuer API-Datenfluesse schreiben.

### Schritt 3: Runtime Diagnostics Backend

- Shared Runtime Check Service bauen.
- `/v1/dashboard/runtime` und `/dashboard/runtime/` vorbereiten.
- Manuelle staff-only Runtime-Smoke-Aktion mit internem Test-PDF vorbereiten.
- Bestehendes `/dashboard/system` nicht ueberfrachten; Runtime getrennt halten.
- Tests mit Mocks schreiben.

### Schritt 4: Jobdetails, Metriken und Artefaktpreview

- Serializer erweitern.
- Result-Metriken speichern oder sicher on demand rekonstruieren.
- Artifact Preview Endpoint einfuehren.
- Jobdetail-Datenfluss absichern.
- Tests fuer Tenant-Scoping, Limits und Preview.

### Schritt 5: Tabler Basislayout

- Tabler Free/MIT als vendored compiled static bundle einbinden.
- `base.html` auf Tabler umstellen.
- Gemeinsame CSS/JS-Dateien auslagern.
- Navigation fuer Zielseiten anlegen.
- Bestehende Seiten weiterhin erreichbar halten.

### Schritt 6: Operations und Runtime UI migrieren

- `/dashboard/` als Tabler Overview umsetzen.
- `/dashboard/runtime/` als neue Diagnostics-Seite umsetzen.
- Runtime-Smoke-Button mit Lock/Timeout und Ergebnisanzeige einbinden.
- Worker, Broker, Versions- und Cache-Warnungen sichtbar machen.

### Schritt 7: Upload, Jobs und Artefakte migrieren

- `/dashboard/tools/` in Upload/Jobs-Struktur ueberfuehren.
- Jobliste und Jobdetailseite mit Tabs bauen.
- Effektive Optionen vor Jobstart anzeigen.
- Artefaktvorschau einbinden.

### Schritt 8: Profile Comparison und Profile Catalog

- Profilkatalog anzeigen.
- Profile Comparison mit Backend-Profilen statt hardcodierten Namen.
- Vergleichsdiff ueber Preview-Endpunkte.
- `chunks_json` korrekt kennzeichnen.

### Schritt 9: API Keys / Tenant Defaults strukturieren

- Strukturierte Docling-Defaults in API-Key-Formulare integrieren.
- JSON-Fallback als Advanced-Bereich behalten.
- PDF-first MIME-Grenzen klar anzeigen; Multi-Format-Erweiterung nicht ohne
  Backend-Capability freischalten.
- Optional Tenant-Defaults als eigene Seite.

### Schritt 10: Abschlussverifikation

- Unit- und API-Tests.
- Kleiner PDF-Smoke nach Docling-2.96.1-Runtime.
- Optional Browser-Smoke fuer zentrale Dashboard-Seiten.
- Docs aktualisieren: `API_INTEGRATION.md`, `ENDPOINTS.md`,
  `deploy/DOCLING_2_96_UPGRADE.md`, falls Implementierung Felder oder Endpunkte
  aendert.

### Schritt 11: Spaetere beschlossene Erweiterungen planen

- Echte Chunking-Integration als separaten Implementierungsauftrag ausarbeiten.
- Echte VLM-Unterstuetzung als separaten Implementierungsauftrag ausarbeiten.
- Multi-format Upload als separaten Implementierungsauftrag ausarbeiten.
- Tenant-Defaults-Seitenumfang final entscheiden.

## Quellen und lokale Grundlage

Lokale Grundlage:

- `docs/DOCLING_ADMIN_DASHBOARD_PLANNING_CONTEXT.md`
- `requirements.txt`
- `deploy/DOCLING_2_96_UPGRADE.md`
- `deploy/docling_runtime_check.py`
- `document_refinery/documents/profiles.py`
- `document_refinery/authn/options.py`
- `document_refinery/documents/tasks.py`
- `document_refinery/documents/models.py`
- `document_refinery/documents/serializers.py`
- `document_refinery/documents/views.py`
- `document_refinery/dashboard/views.py`
- `document_refinery/dashboard/web_views.py`
- `document_refinery/dashboard/templates/dashboard/*.html`
- `document_refinery/core/views.py`

Externe Fakten, geprueft am 2026-06-02:

- Django Supported Versions: https://www.djangoproject.com/download/#supported-versions
- Tabler Admin Template: https://tabler.io/admin-template
- Docling PyPI Release History: https://pypi.org/project/docling/
- Docling Supported Formats: https://docling-project.github.io/docling/usage/supported_formats/
- Docling Pipeline Options: https://docling-project.github.io/docling/reference/pipeline_options/
- Docling Changelog: https://raw.githubusercontent.com/docling-project/docling/main/CHANGELOG.md
