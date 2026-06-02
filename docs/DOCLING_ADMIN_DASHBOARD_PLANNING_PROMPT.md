# Docling Admin Dashboard Planning Prompt

## Ziel

Erstelle einen belastbaren Umsetzungsplan fuer den Umbau des DocumentRefinery Admin-/Dashboard-Interfaces auf Docling 2.96.x. Es sollen noch keine Codeaenderungen umgesetzt werden. Ergebnis dieser Phase ist ausschliesslich ein technischer Plan mit Architekturentscheidung, UI-Struktur, Backend-Anpassungen, Migrationsbedarf, Teststrategie und offenen Risiken.

Das Dashboard soll auf dem Tabler Admin Template basieren: <https://tabler.io/admin-template>. Tabler ist ein Bootstrap-5-basiertes, responsives Admin-Template mit fertigen Layouts, Formularen, Tabellen, Tabs, Badges, Cards, Dark Mode und Icon-Unterstuetzung.

## Kontext

- Projekt: DocumentRefinery
- Django soll auf dem aktuellen LTS-Zweig bleiben.
- Redis bleibt auf Version 7.
- Docling soll auf die aktuelle 2.96.x-Reihe angehoben und sauber bedienbar werden.
- Das bestehende Projekt hat bereits:
  - Profile: `fast_text`, `ocr_only`, `structured`, `full_vlm`
  - Dashboard-Upload und Profilvergleich
  - Artefakte: `docling_json`, `markdown`, `text`, `doctags`, `chunks_json`, `figures_zip`
  - rohe `docling_options_json` Defaults pro Tenant/API-Key
  - Operations-Ansicht fuer Queue, Worker, Systemstatus und Fehlermeldungen

## Planungsauftrag

Analysiere die bestehende Implementierung und erstelle einen Plan, wie das Admin-/Dashboard-Interface so umgebaut werden sollte, dass neue und relevante Docling-Funktionen sauber sichtbar, konfigurierbar und diagnostizierbar werden. Es duerfen in dieser Phase keine Dateien ausser dem Plan geaendert werden.

## Zu Pruefende Dateien

- `document_refinery/documents/profiles.py`
- `document_refinery/authn/options.py`
- `document_refinery/documents/tasks.py`
- `document_refinery/documents/models.py`
- `document_refinery/documents/serializers.py`
- `document_refinery/documents/views.py`
- `document_refinery/dashboard/web_views.py`
- `document_refinery/dashboard/templates/dashboard/base.html`
- `document_refinery/dashboard/templates/dashboard/index.html`
- `document_refinery/dashboard/templates/dashboard/operations.html`
- `document_refinery/dashboard/templates/dashboard/api_key_new.html`
- `document_refinery/dashboard/templates/dashboard/api_key_detail.html`
- `document_refinery/core/views.py`

## Gewuenschtes Planungsergebnis

Der Plan soll folgende Abschnitte enthalten:

1. **Ist-Zustand**
   - Welche Docling-Optionen werden aktuell wirklich backendseitig genutzt?
   - Welche Profile gibt es und was aktivieren sie tatsaechlich?
   - Welche Artefakte werden erzeugt und welche sind echte Docling-Ausgaben?
   - Welche Informationen sind im Admin/Dashboard schon sichtbar?
   - Welche Informationen sind nur ueber JSON oder gar nicht sichtbar?

2. **Zielbild fuer das Tabler-Dashboard**
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

3. **Konfigurationsmodell**
   - Vorschlag, ob die heutigen festen Profile beibehalten, erweitert oder in ein modelliertes Profil-System ueberfuehrt werden sollen.
   - Entscheidungsvorschlag fuer Tenant-/API-Key-Defaults:
     - JSON-Fallback behalten
     - strukturierte Controls ergaenzen
     - effektive Optionen vor Jobstart anzeigen
   - Konkrete Liste der Optionen, die sicher angeboten werden koennen:
     - OCR an/aus
     - OCR Engine: `auto`, `rapidocr`, `easyocr`, `tesseract`, `tesseract_cli`, `mac`
     - OCR-Sprachen
     - full-page OCR
     - Tabellenstruktur
     - parsed pages/layout
     - picture images
     - image scale
     - exports: `markdown`, `text`, `doctags`, `docling_json`, `chunks_json`, `figures_zip`
   - Liste der Optionen, die nur angeboten werden sollen, wenn das Backend sie wirklich implementiert:
     - echte VLM-Pipeline-Auswahl
     - TableFormer/VLM Tabellenmodi
     - picture description
     - picture classification
     - ASR/Audio
     - weitere Input-Formate wie DOCX, PPTX, XLSX, HTML

4. **Docling 2.96.x Feature-Abgleich**
   - Welche neuen Docling-Funktionen sind fuer DocumentRefinery relevant?
   - Welche sind reine Backend-/Runtime-Themen?
   - Welche gehoeren in die UI?
   - Welche gehoeren nur in Diagnostics?
   - Welche sollten bewusst nicht angeboten werden?

5. **Runtime Diagnostics**
   - Plan fuer eine Admin-Ansicht, die mindestens zeigt:
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
   - Plan fuer serverseitige Diagnose-Endpunkte oder Management Commands.

6. **Job- und Artefaktansicht**
   - Plan, wie pro Job sichtbar werden:
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
   - Plan fuer Artefaktvorschau:
     - Text/Markdown/DocTags als Preview
     - Docling JSON strukturiert oder formatiert
     - Figures ZIP nur Download und Metadaten
     - Chunks JSON nur dann als echte Chunks anzeigen, wenn echtes Chunking implementiert ist

7. **Input-Formate**
   - Pruefe, ob das System PDF-only bleiben soll.
   - Wenn neue Docling-Formate geplant werden, benoetigt der Plan explizit:
     - Upload-MIME-Validierung
     - Dateiendungen und Storage-Pfade
     - Security-Pruefung
     - Tests
     - UI-Kennzeichnung pro Format
   - Ohne explizite Backend-Erweiterung bleibt die UI PDF-only.

8. **Backend-Aenderungsplan**
   - Liste der benoetigten Backend-Aenderungen, aber keine Umsetzung:
     - Profile aus Backend in Frontend ausliefern
     - Optionsschema zentralisieren
     - Validierung erweitern
     - JobSerializer um Diagnosefelder ergaenzen
     - Operations-Endpoint um Docling Runtime erweitern
     - ggf. neues Endpoint fuer Profile/Capabilities
     - ggf. echtes Chunking klaeren

9. **Frontend-Aenderungsplan mit Tabler**
   - Plan fuer Umstieg von bestehenden Templates auf Tabler:
     - `base.html` auf Tabler Layout, CSS und JS vorbereiten
     - Sidebar oder Topnav gemaess Tabler
     - bestehende Karten/Formulare/Tables auf Tabler-Komponenten mappen
     - Badges fuer Status/Profile/Artefakte
     - Tabs fuer Job Details, Artefakte, Optionen, Errors
     - Tabellen fuer Joblisten und Profilvergleiche
     - Forms fuer strukturierte Docling-Optionen
   - Keine CDN-Abhaengigkeit ohne bewusste Entscheidung.
   - Klaere, ob Tabler als vendored static asset, npm dependency oder lokales static bundle eingebunden werden soll.

10. **Teststrategie**
    - Unit-Tests fuer Optionsvalidierung.
    - Tests fuer Profile und effektive Optionen.
    - Serializer/API-Tests fuer neue Felder.
    - Dashboard-Tests fuer neue Seiten und Formularfelder.
    - Pipeline-Smoke mit kleinem PDF.
    - Optional Browser-Smoke fuer Tabler UI.

11. **Risiken und Entscheidungen**
    - Liste aller offenen Entscheidungen:
      - Tabler Free vs Pro
      - Asset-Einbindung
      - PDF-only vs weitere Formate
      - echte VLM-Unterstuetzung ja/nein
      - echtes Chunking ja/nein
      - wie viel Optionsfreiheit fuer Tenant/API-Key erlaubt wird
      - wie Runtime Checks abgesichert werden
    - Liste der erwarteten Migrationsrisiken.

12. **Umsetzungsreihenfolge**
    - Empfohlene Reihenfolge in kleinen, reviewbaren Schritten.
    - Jeder Schritt soll klar testbar sein.
    - Keine Big-Bang-Migration.

## Akzeptanzkriterien fuer den Plan

- Der Plan trennt klar zwischen Planung und Implementierung.
- Der Plan nennt konkrete Dateien und betroffene Datenfluesse.
- Der Plan bietet keine UI-Funktionen an, die backendseitig nicht realistisch abgedeckt werden.
- Der Plan beruecksichtigt Tabler als Admin-Template.
- Der Plan haelt Django LTS und Redis 7 unveraendert.
- Der Plan beschreibt Tests und Risiken.
- Der Plan ist so konkret, dass danach ein separater Implementierungsauftrag erstellt werden kann.

