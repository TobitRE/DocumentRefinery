# Docling Admin Dashboard Implementation Prompt

## Aufgabe

Setze den Umbau des DocumentRefinery Admin-/Dashboard-Interfaces fuer
Docling 2.96.1 gemaess folgendem Planungsdokument um:

- `docs/DOCLING_ADMIN_DASHBOARD_IMPLEMENTATION_PLAN.md`

Der Plan ist die verbindliche fachliche Grundlage. Lies ihn zuerst vollstaendig,
bevor du Code aenderst. Pruefe danach die dort genannten Projektdateien und
implementiere die Umstellung schrittweise.

## Ziel

Nach Abschluss soll DocumentRefinery ein backendseitig abgesichertes,
Tabler-basiertes Admin-/Dashboard-Interface fuer den bestehenden PDF-first
Docling-Flow haben.

Die Umsetzung soll insbesondere liefern:

- Zentrales Docling-Optionsschema mit JSON-Fallback.
- Effektive Docling-Optionen aus System-, Tenant-, API-Key-, Request- und
  Profil-Ebene.
- Profilkatalog und Capabilities aus dem Backend statt hardcodierter UI-Logik.
- Runtime Diagnostics fuer Docling, Worker, Redis/Broker, Cache, OCR-Backends
  und relevante Runtime-Pfade.
- Manuell ausloesbarer staff-only Runtime-Smoke mit internem Test-PDF.
- Erweiterte Job- und Artefaktansichten inklusive Metriken, effektiven Optionen,
  Runtime-Informationen und sicherer Artefaktvorschau.
- Tabler Free/MIT als vendored compiled static bundle ohne CDN.
- Tabler-basiertes Dashboard mit Overview, Upload/Jobs, Jobdetails,
  Profilkatalog, Vergleichsansicht, Runtime Diagnostics und strukturierten
  API-Key-Defaults.

## Harte Rahmenbedingungen

- Django bleibt auf dem aktuellen LTS-Zweig gemaess `requirements.txt`:
  `Django>=5.2.14,<5.3`.
- Redis bleibt auf Version 7 gemaess `requirements.txt`: `redis>=7,<8`.
- Docling bleibt exakt auf `docling==2.96.1` gepinnt.
- Tabler wird als Free/MIT-Version verwendet.
- Tabler-Assets werden als kompiliertes vendored static bundle im Repository
  abgelegt. Keine Runtime-CDN-Abhaengigkeit.
- Das bestehende JSON-Fallback fuer Docling-Optionen bleibt erhalten.
- Strukturierte UI-Controls duerfen nur Keys schreiben, die backendseitig
  validiert und real an Docling weitergegeben werden.
- Bestehende unbekannte JSON-Keys duerfen kompatibel erhalten bleiben, muessen
  aber als Warnung sichtbar werden.
- Keine Dashboard-Funktion anbieten, wenn sie backendseitig nicht realistisch
  abgesichert ist.
- Die erste Umsetzung bleibt PDF-first. Multi-format Upload wird nicht in dieser
  Umsetzung freigeschaltet.
- `chunks_json` darf nicht als echtes Chunking dargestellt werden, solange kein
  echter Chunking-Pfad implementiert ist.
- `full_vlm` darf nicht als echte VLM-Funktion dargestellt werden. Es ist ein
  Legacy-/Kompatibilitaetsprofil fuer strukturierte Ausgabe plus Figure-Images.
- Echte Chunking-Integration, echte VLM-Unterstuetzung und Multi-format Upload
  bleiben spaetere separate Umsetzungsauftraege.

## Erwartete Backend-Umsetzung

Implementiere die Backend-Phasen aus dem Plan in dieser Reihenfolge:

1. Dependency- und Runtime-Sanity
2. Optionsschema und Profilkatalog
3. Backend-Capabilities und Options-Resolve API
4. Runtime Diagnostics Backend
5. Jobdetails, Metriken und Artefaktpreview

Konkret erwartete Dateien und Aenderungen:

- `document_refinery/documents/docling_options.py`
  - Neues zentrales Optionsschema.
  - Validierung strukturierter Optionen.
  - Legacy-Mapping fuer bestehende Keys wie `ocr` und `ocr_languages`, soweit
    kompatibel.
  - Warnungen fuer unbekannte JSON-Fallback-Keys.
  - Resolver fuer effektive Optionen:
    `resolve_effective_options(api_key, request_options, profile)`.
  - Builder fuer `PdfPipelineOptions` aus effektiven Optionen.
  - Capabilities- und Schema-Payloads fuer UI/API.
- `document_refinery/documents/profiles.py`
  - Profile um Labels, Beschreibungen, Ressourcenhinweise, Feature-Status,
    Warnungen und Capability-Metadaten erweitern.
  - `full_vlm` korrekt als nicht echte VLM-Funktion kennzeichnen.
- `document_refinery/authn/options.py`
  - Bestehende Validierung an das zentrale Optionsschema anbinden.
- `document_refinery/documents/views.py`
  - Upload, Ingest und Compare auf denselben Optionsresolver umstellen.
  - Neue Endpunkte fuer Profile, Capabilities und Options-Resolve.
  - Neuer sicherer Artifact-Preview-Endpoint mit Tenant-Scoping und
    Groessenlimit.
- `document_refinery/documents/serializers.py`
  - Serializer fuer Options-Resolve, Jobdetails, Runtime-/Metrikfelder und
    Artefaktpreview ergaenzen.
- `document_refinery/documents/models.py`
  - Job-Metadaten fuer Runtime-Versionen und Result-Metriken ergaenzen, sofern
    nicht sauber in bestehenden Feldern darstellbar.
- `document_refinery/documents/tasks.py`
  - `PdfPipelineOptions` aus effektiven Job-Optionen bauen.
  - Docling-, docling-core- und docling-parse-Versionen pro Job speichern.
  - Result-Metriken in `export_artifacts_task()` erzeugen.
  - `Document.page_count` befuellen, wenn Docling die Seitenzahl sicher liefert.
- `document_refinery/dashboard/runtime.py`
  - Shared Runtime Check Service fuer API, Staff-Seite und optional
    Management-/Deploy-Checks.
- `document_refinery/dashboard/views.py`
  - `GET /v1/dashboard/runtime` fuer API-Keys mit `dashboard:read`.
- `document_refinery/dashboard/web_views.py`
  - Staff-only Runtime-Seite und staff-only Smoke-Aktion.
- `document_refinery/dashboard/urls.py`
  - Runtime API route.
- `document_refinery/dashboard/web_urls.py`
  - Neue Dashboard-Webrouten fuer Runtime, Jobs, Profile und Compare.
- `deploy/docling_runtime_check.py`
  - Bestehenden Deploy-Check erhalten und, wenn sinnvoll, an Shared Runtime
    Checks angleichen.

## Erwartete Frontend-Umsetzung

Implementiere die Frontend-Phasen aus dem Plan in dieser Reihenfolge:

1. Tabler Basislayout
2. Operations und Runtime UI
3. Upload, Jobs und Artefakte
4. Profile Comparison und Profile Catalog
5. API Keys / Tenant Defaults

Konkret erwartete Dateien und Aenderungen:

- `document_refinery/dashboard/static/vendor/tabler/<version>/...`
  - Vendored Tabler Free/MIT CSS/JS und benoetigte Assets.
- `document_refinery/dashboard/static/dashboard/app.css`
  - Projektweite Dashboard-Anpassungen, keine grossen Inline-Styles.
- `document_refinery/dashboard/static/dashboard/app.js`
  - Gemeinsame Dashboard-Helfer, API-Aufrufe, Statusrendering.
- `document_refinery/dashboard/templates/dashboard/base.html`
  - Tabler-Shell, Navigation, Page Header, statische Assets.
- `document_refinery/dashboard/templates/dashboard/operations.html`
  - Overview/Operations auf Tabler-Komponenten migrieren.
- Neue oder aufgeteilte Templates:
  - `jobs.html`
  - `job_detail.html`
  - `upload.html`
  - `profile_comparison.html`
  - `profiles.html`
  - `runtime.html`
- Bestehende API-Key-Templates:
  - Strukturierte Docling-Defaults oberhalb des JSON-Fallbacks.
  - PDF-first MIME-Grenzen beibehalten.
  - Unbekannte JSON-Keys als Warnung darstellen, nicht still verstecken.

Frontend-Regeln:

- Keine UI fuer VLM, echte Chunking-Integration oder Multi-format Upload in der
  ersten Umsetzung.
- Profile, Capabilities und Optionsschema aus Backend-Endpunkten laden.
- Effektive Optionen vor Jobstart anzeigen.
- Artefaktvorschau nur ueber den sicheren Preview-Endpoint laden.
- Markdown/Text/DocTags/JSON nicht als ausfuehrbares HTML rendern.
- `figures_zip` nur als Download und sichere ZIP-Metadaten anzeigen.
- Tabler Cards nur fuer echte Panels oder wiederholte Items nutzen.
- Kein Marketing-/Landing-Page-Layout bauen; das Dashboard bleibt eine dichte,
  operative Admin-Oberflaeche.

## Tests und Verifikation

Fuehre nach der Implementierung die relevanten Tests aus. Mindestens:

```bash
venv/bin/python document_refinery/manage.py test
```

Fuehre ausserdem den Runtime-Check aus:

```bash
venv/bin/python deploy/docling_runtime_check.py --json
```

Wenn die lokale Umgebung nicht zu `requirements.txt` passt oder Docling 2.96.1
nicht installiert ist, dokumentiere den Blocker konkret und fuehre trotzdem alle
Tests aus, die ohne diesen Runtime-Abgleich sinnvoll sind.

Ergaenze oder aktualisiere Tests fuer:

- `document_refinery/authn/tests/test_options.py`
- `document_refinery/documents/tests/test_pipeline.py`
- Dokumenten-Upload mit strukturierten Optionen.
- Options-Resolve-Endpoint.
- Profile-/Capabilities-Endpoint.
- Compare-Flow mit zentralem Resolver.
- JobSerializer mit Runtime- und Metrikfeldern.
- Artifact Preview mit Tenant-Scoping, Groessenlimit, JSON/Text/ZIP-Verhalten.
- Runtime Diagnostics API und Staff-Seite mit Mocks.
- Staff-only Runtime-Smoke-Aktion.
- Dashboard-Rendering fuer Tabler-Basislayout und zentrale Seiten.

Nach dem Frontend-Umbau starte, falls noetig, einen lokalen Devserver und pruefe
die wichtigsten Dashboard-Seiten im Browser:

- `/dashboard/`
- `/dashboard/jobs/`
- `/dashboard/runtime/`
- `/dashboard/api-keys/new/`

Pruefe dabei mindestens:

- Navigation sichtbar und aktiv.
- Keine offensichtlichen JavaScript-Fehler.
- Keine UI-Angebote fuer nicht abgesicherte Docling-Funktionen.
- PDF-only Upload in der ersten Umsetzung.
- Effektive Optionen und Runtime-Warnungen sind sichtbar.

## Dokumentation

Aktualisiere nach der Umsetzung nur die Dokumentation, die durch neue Felder,
Endpunkte oder Bedienablaeufe wirklich betroffen ist. Kandidaten:

- `docs/API_INTEGRATION.md`
- `docs/ENDPOINTS.md`
- `deploy/DOCLING_2_96_UPGRADE.md`
- `docs/DOCLING_ADMIN_DASHBOARD_IMPLEMENTATION_PLAN.md`, falls die Umsetzung
  begruendet vom Plan abweicht.

Die spaeteren TODOs aus dem Plan bleiben als spaetere TODOs bestehen und sollen
nicht still in dieser Umsetzung erledigt werden:

- Echte Chunking-Integration.
- Echte VLM-Unterstuetzung.
- Multi-format Upload.
- Endgueltiger Tenant-Defaults-Seitenumfang, falls nicht im ersten UI-Schnitt
  noetig.

## Arbeitsweise

- Vor Aenderungen: `git status --short` pruefen.
- Wenn bereits uncommitted Aenderungen existieren, diese nicht zuruecksetzen und
  nicht ueberschreiben, ohne sie vorher zu verstehen.
- Keine destruktiven Git-Kommandos verwenden.
- Implementiere in kleinen, nachvollziehbaren Schritten entlang der
  Umsetzungsreihenfolge.
- Nach jeder groesseren Phase gezielte Tests ausfuehren.
- Bestehende API-Kompatibilitaet moeglichst erhalten.
- Keine neue Abstraktion einfuehren, wenn eine bestehende Projektstruktur
  ausreicht.
- Keine neue UI-Funktion hardcoden, die nicht aus Backend-Capabilities ableitbar
  ist.
- Halte die Umsetzung eng am bestehenden Django-/DRF-/Celery-Code.

## Abnahmekriterien

Die Umsetzung gilt erst als abgeschlossen, wenn:

- Die neue Tabler-basierte Dashboard-Struktur laeuft.
- Tabler lokal aus vendored static assets geladen wird.
- Upload/Ingest/Compare denselben Optionsresolver nutzen.
- Strukturierte Optionen backendseitig validiert und an Docling angebunden sind.
- JSON-Fallback weiter funktioniert und unbekannte Keys Warnungen erzeugen.
- Profile und Capabilities aus dem Backend kommen.
- `full_vlm`, `chunks_json`, Multi-format Upload und VLM korrekt abgegrenzt
  dargestellt werden.
- Runtime Diagnostics und manueller staff-only Smoke vorhanden sind.
- Jobdetails Runtime-/Metrikdaten und sichere Artefaktvorschau enthalten.
- Tests fuer die neuen Backend- und Dashboard-Flows vorhanden sind.
- `venv/bin/python document_refinery/manage.py test` erfolgreich ist oder ein
  klarer lokaler Umgebungsblocker dokumentiert wurde.
- `venv/bin/python deploy/docling_runtime_check.py --json` erfolgreich ist oder
  ein klarer lokaler Docling-/Runtime-Blocker dokumentiert wurde.
- Keine spaeteren Features versehentlich freigeschaltet wurden.
