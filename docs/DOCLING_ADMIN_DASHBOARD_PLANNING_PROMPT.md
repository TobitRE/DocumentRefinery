# Docling Admin Dashboard Planning Prompt

## Aufgabe

Erstelle einen konkreten Umsetzungsplan fuer den Umbau des DocumentRefinery Admin-/Dashboard-Interfaces fuer Docling 2.96.x.

Nutze als Grundlage die Zusatzinformationen in:

- `docs/DOCLING_ADMIN_DASHBOARD_PLANNING_CONTEXT.md`

## Ziel

Schreibe als Ergebnis eine neue Markdown-Datei:

- `docs/DOCLING_ADMIN_DASHBOARD_IMPLEMENTATION_PLAN.md`

Diese Datei soll der eigentliche Umsetzungsplan fuer die spaetere Implementierung sein. In dieser Planungsphase sollen keine Codeaenderungen vorgenommen werden.

## Muss-Anforderungen

- Django bleibt auf dem aktuellen LTS-Zweig.
- Redis bleibt auf Version 7.
- Das Dashboard soll auf dem Tabler Admin Template basieren: <https://tabler.io/admin-template>.
- Die Planung muss zwischen bereits implementierten Funktionen, geplanten Erweiterungen und bewusst nicht angebotenen Docling-Funktionen unterscheiden.
- Keine UI-Funktion soll geplant werden, wenn sie backendseitig nicht realistisch abgesichert werden kann.
- Das bestehende JSON-Fallback fuer Docling-Optionen soll erhalten bleiben, aber strukturierte Admin-/Dashboard-Controls sollen geplant werden.
- Der Plan soll konkrete Dateien, Datenfluesse, Tests, Risiken und eine schrittweise Umsetzungsreihenfolge nennen.

## Erwarteter Inhalt der neuen Umsetzungsdatei

Die Datei `docs/DOCLING_ADMIN_DASHBOARD_IMPLEMENTATION_PLAN.md` soll mindestens diese Abschnitte enthalten:

1. Ist-Zustand
2. Zielbild mit Tabler
3. Konfigurationsmodell fuer Docling-Profile und Defaults
4. Docling 2.96.x Feature-Abgleich
5. Runtime Diagnostics
6. Job- und Artefaktansicht
7. Input-Formate und Sicherheitsgrenzen
8. Backend-Aenderungsplan
9. Frontend-Aenderungsplan mit Tabler
10. Teststrategie
11. Risiken und offene Entscheidungen
12. Umsetzungsreihenfolge

## Arbeitsweise

- Lies zuerst die Kontextdatei vollstaendig.
- Pruefe danach die dort genannten Projektdateien.
- Erstelle dann nur die neue Umsetzungsplan-Datei.
- Keine Implementierung, keine Migrationen, keine Asset-Installation und keine Tests ausfuehren, ausser wenn sie zur reinen Analyse noetig sind.

