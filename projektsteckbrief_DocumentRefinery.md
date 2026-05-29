# Projektsteckbrief: DocumentRefinery

Projektname: DocumentRefinery

1. Management Summary (Worum geht es & Ziel)

DocumentRefinery ist eine mandantenfaehige Plattform zur sicheren, standardisierten und asynchronen Verarbeitung von PDF-Dokumenten. Das System nimmt Dokumente per API entgegen, prueft sie vor der Verarbeitung auf Malware, konvertiert sie mit Docling in strukturierte Ausgabeformate und stellt die Ergebnisse fuer nachgelagerte Systeme bereit. Das geloeste Kernproblem ist die Abloesung individueller Einzelloesungen durch einen zentralen, wiederverwendbaren Dokument-Processing-Service. Das uebergeordnete Ziel ist der Aufbau einer belastbaren Plattform fuer dokumentbasierte Automatisierung, Qualitaetssicherung und spaetere Produktisierung.

2. Aktueller Stand (Status Quo)

Stand 10. April 2026 wirkt das Projekt wie ein fortgeschrittener MVP bzw. eine fruehe Beta, nicht mehr wie ein reines Konzept.

- Der Kern-Workflow ist implementiert: Upload, Quarantaene, MIME-/Groessen-/Duplikatspruefung, Virenscan via ClamAV, asynchrone Verarbeitung via Celery, Docling-Konvertierung, Artefakt-Export und Finalisierung.
- Multi-Tenancy ist auf Datenebene umgesetzt; API-Keys mit Scopes, Throttling und erlaubten Upload-MIME-Types sind vorhanden.
- Mehrere Verarbeitungsprofile sind produktiv im Code angelegt: `fast_text`, `ocr_only`, `structured`, `full_vlm`.
- Artefakt-Ausgaben sind bereits umgesetzt, darunter `docling_json`, `markdown`, `text`, `doctags`, `chunks_json` und `figures_zip`.
- Vergleichslaeufe ueber mehrere Profile, Job-Cancel/Retry, Webhook-CRUD inklusive Retry/Backoff sowie Dashboard- und Monitoring-Endpunkte sind vorhanden.
- Die operative Oberflaeche wurde zuletzt klar ausgebaut: Operations-Sicht, Tenant-Tools, API-Key-Verwaltung, Webhook-Management und Systemstatus.
- Die lokale Test-Suite lief im aktuellen Stand erfolgreich durch: 105 Tests, Laufzeit rund 4,2 Sekunden.
- Der zuletzt dokumentierte lokale Coverage-Wert liegt bei 93 %; schwaechere Bereiche liegen vor allem in operativen UI-/Admin-Pfaden.
- Der letzte commitierte Stand ist vom 9. Februar 2026; zusaetzlich existieren aktuell lokale, noch nicht commitierte Aenderungen auf dem Branch `codex/verbessere-design-und-usability`.
- Die Dokumentation ist nicht vollstaendig synchron zum Ist-Zustand: README und Task-Liste signalisieren teils noch Planungsstatus, obwohl grosse Teile bereits implementiert sind.

3. Naechste Schritte & Meilensteine

Im vorliegenden Datenstand sind keine verbindlichen Deadlines dokumentiert. Aus dem tatsaechlichen Projektstand ergeben sich jedoch folgende unmittelbare naechste Schritte:

- Die aktuell lokalen UI-/Usability- und Robustheitsaenderungen sollten bereinigt, committed und in einen klaren Release-Stand ueberfuehrt werden.
- README, Task-Liste, API-Guide und Naming sollten auf einen konsistenten Produktstand gebracht werden, damit Scope, Reifegrad und Roadmap eindeutig kommunizierbar sind.
- Der API-Vertrag sollte finalisiert werden, insbesondere bei Docling-Optionen, die heute validiert und gespeichert werden, aber nur teilweise wirksam sind.
- CI sollte eingefuehrt werden, mindestens fuer Tests, Linting und einen reproduzierbaren Qualitaets-Check pro Aenderung.
- Ein naechster Produktisierungs-Meilenstein waere eine belastbare Betriebsreife: Retention/Cleanup, Observability, echte Integrations- und End-to-End-Tests gegen Redis/Celery, ClamAV, Docling und Nginx/X-Accel.
- Ein nachgelagerter Meilenstein waere die Kommerzialisierungsfaehigkeit: Usage-/Kostenreporting, SLOs, Quotas und klare Produktgrenzen zwischen v0, v1 und spaeteren Ausbaustufen.

4. Risiken & Blocker

- Der groesste operative Blocker ist der unstete Auslieferungsstand: Der formale Git-Stand endet am 9. Februar 2026, waehrend die aktuelle Weiterentwicklung noch lokal im Worktree liegt. Das erschwert Release-Faehigkeit, Uebergaben und belastbares Reporting.
- Es gibt eine deutliche Dokumentationsdrift zwischen Task-Liste, README, Decisions und tatsaechlicher Implementierung. Das erhoeht das Risiko falscher Priorisierung und Missverstaendnisse bei Stakeholdern.
- Externe Kernabhaengigkeiten wie Redis/Celery, ClamAV, Docling und Nginx sind ueberwiegend nur mock-basiert getestet. Das reduziert das Risiko in der Logik, nicht aber im realen Betriebsverhalten.
- Teile der konfigurierbaren Docling-Optionen erzeugen derzeit hoehere Erwartung als tatsaechliche Wirkung. Das ist fachlich riskant, weil Mandanten- oder API-Key-Defaults dadurch missverstaendlich werden koennen.
- Es fehlt eine sichtbare CI-Pipeline. Damit ist die Qualitaet aktuell stark an lokale Disziplin gebunden.
- Die Bus-Factor-Situation ist kritisch: In der lokalen Historie ist nur ein sichtbarer Contributor erkennbar.
- Ein Security-Review vom 7. Februar 2026 liegt vor, aber sein Remediation-Status ist nicht zentral nachverfolgbar. Einzelne Punkte wurden inzwischen offenbar bereits verbessert, ein sauberer Re-Test fehlt jedoch.
- Fuer die Geschaeftsfuehrung ist eine Richtungsentscheidung sinnvoll: Soll DocumentRefinery primaer interner Shared Service bleiben oder als externe, produktisierte Plattform weiter aufgebaut werden?

5. Potenzial & Vision

DocumentRefinery hat das Potenzial, sich von einem technischen Extraktionsservice zu einer zentralen Dokumentenplattform der 7 Hills Neo GmbH zu entwickeln. Strategisch interessant ist vor allem die Kombination aus mandantenfaehiger API, Profilsteuerung, Qualitaetsvergleich, Webhooks und operativer Transparenz. Daraus kann mittelfristig ein standardisierter Infrastrukturbaustein fuer interne Produkte, Kundenloesungen und dokumentgetriebene Workflows entstehen. Langfristig eroeffnet das Chancen in Richtung Document Intelligence, Kostenmodellierung, qualitaetsbasierter Profilwahl, Integrationsprodukte und Managed Services.

6. Strategische Empfehlungen & Spin-offs

Verbesserungen:

- Einen klaren Release-Schnitt definieren: aktueller MVP-Stand committen, dokumentieren und als belastbaren Referenzstand kennzeichnen.
- Scope und Produktnarrativ vereinheitlichen: ein Name, ein Reifegrad, ein API-Vertrag, ein Roadmap-Dokument.
- CI und echte Integrations-Tests priorisieren, bevor weitere Funktionsbreite aufgebaut wird.
- Die wirksamen und nicht wirksamen Docling-Optionen transparent machen oder technisch nachziehen, um Integrationsrisiken zu vermeiden.
- Betriebsreife ausbauen: Retention, Cleanup, Monitoring, Pagination, Quotas und Failure-Recovery sauber abschliessen.

Weiterentwicklung:

- Das Produkt als zentrale Dokumentverarbeitungsplattform ausbauen, nicht nur als Upload-API.
- Profilvergleich und Qualitaetsbewertung als Differenzierungsmerkmal weiterentwickeln, etwa mit automatischer Profilempfehlung je Dokumenttyp.
- Usage-, Kosten- und Performance-Daten systematisch erfassen, um Pricing, SLOs und Kapazitaetsplanung vorzubereiten.
- Den Service gezielt an weitere interne oder kundenseitige Anwendungen anbinden, um Wiederverwendung und Standardisierung im Unternehmen zu erhoehen.

Abgeleitete Projekte:

- Ein `Document Intelligence Layer` fuer Klassifikation, Qualitaetsbewertung, Routing und Folgeprozesse auf Basis der erzeugten Artefakte.
- Ein zentrales Admin-/Ops-Cockpit fuer mehrere KI- oder Dokumentservices mit einheitlichem Monitoring, API-Key-Management und Audit-Funktion.
- Ein Reporting-Modul fuer Usage, Kosten, SLA/SLO und tenantbasierte Abrechnung.
- Ein Folgeprojekt fuer sichere, standardisierte Event-Integration, z. B. ueber Webhooks+, Retry-Policies, Signaturpruefung und Integrations-Templates fuer Drittsysteme.
- Ein Produktmodul fuer dokumentbasierte Vergleichs- und Benchmarking-Workflows, etwa fuer OCR-/Extraktionsqualitaet nach Dokumenttyp oder Kunde.
