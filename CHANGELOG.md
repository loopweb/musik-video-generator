# Changelog — Musik Video Generator

## v2.9

- **Mehrsprachig: Deutsch, English, Español.** Beim ersten Start wird die Sprache gewählt; änderbar über „?“ → Language (danach Neustart). Die Auswahl steht in `mvg_settings.json` neben dem Programm.
- **Klick-Hilfe:** Das „?“ öffnet ein Menü (Klick-Hilfe, Kurzanleitung, PDF, Sprache). In der Klick-Hilfe klickt man auf einen beliebigen Knopf oder Bereich und bekommt eine ausführliche Erklärung (Hauptfenster und Clip-Editor); danach beendet sich der Modus von selbst.
- **Kurzanleitung** aufgeräumt und ohne Buttons; kein Assistent (Klick-Hilfe ersetzt ihn).
- **Klick-Hilfe:** Das „?“ öffnet ein Menü (Klick-Hilfe, Assistent, Kurzanleitung, PDF, Sprache). In der Klick-Hilfe klickt man auf einen beliebigen Knopf, Schalter oder Bereich – im Hauptfenster und im Clip-Editor – und bekommt eine ausführliche Erklärung (36 Themen, in allen drei Sprachen). Eine blaue Leiste zeigt beim Überfahren den Namen des Bereichs; Beenden mit Esc oder Button.
- **Bedienungsanleitung als PDF in drei Sprachen** (`MVG_Bedienungsanleitung.pdf`, `MVG_Manual_EN.pdf`, `MVG_Manual_ES.pdf`); der PDF-Button öffnet die PDF der gewählten Sprache.
- **Fehlerbehebung:** Startfehler `KeyError: 'Sad'` bei englischer/spanischer Oberfläche behoben (Stimmungsnamen wurden fälschlich übersetzt).
- **Fehlerbehebung:** Meldung beim Fehlschlag von „Clip entfernen“ im Editor konnte einen Programmfehler auslösen (Variable nicht mehr verfügbar) – behoben.

## v2.8

- **Weiches Ein- und Ausblenden beim 58-s-Short:** Da der Short ein Ausschnitt aus der Songmitte ist, wird Bild und Ton jetzt am Anfang kurz eingeblendet (0,6 s) und am Ende ausgeblendet.
- **Short aus dem stärksten Songabschnitt:** Der 58-s-Short nimmt nicht mehr die ersten 58 Sekunden, sondern den Abschnitt mit den meisten wiederkehrenden Harmonien und der höchsten Energie (meist der Refrain). Die Schnitte werden auf die Beats dieses Abschnitts gelegt. Bei unklarem Ergebnis oder kurzen Songs bleibt es beim Songanfang.
- **Auch der Short per URL blendet jetzt weich ein und aus** (Bild und Ton).
- **Neuer Button „Short per URL“** (ersetzt „Standard (intern)“): YouTube-Link in die Zwischenablage kopieren, Button klicken – es entsteht ein 45-Sekunden-Short im Hochformat mit Originalton und automatisch gewähltem stärkstem Abschnitt, gespeichert in `output/Shorts`. Benötigt `yt-dlp`; die nötige JS-Laufzeit (Deno) wird beim ersten Mal automatisch geladen.

## v2.7

**Neu**

- **Mehrfachauswahl bei den Formaten:** Die Formate (16:9, 9:16, Short 58 s) sind jetzt Checkboxen. Standard: 16:9-Video plus 58-Sekunden-Short, beides in einem Durchgang. Ausgabedateien: `Titel.mp4`, `Titel_9x16.mp4`, `Titel_Short.mp4`.
- **Der Short ist immer „pur“:** ohne Pre-Intro, Intro und Outro, damit er exakt 58 Sekunden lang bleibt.
- **Clips entfernen im Clip-Editor:** „✕ Entfernen“ ersetzt einen unpassenden Clip durch einen zufälligen anderen Clip exakt gleicher Länge (frame-genau), das Video bleibt so lang wie der Song. Bevorzugt werden Clips, die noch nicht verwendet wurden. Pre-Intro, Intro und Outro sind geschützt. „Abbrechen“ stellt alles wieder her.

**Fehlerbehebung**

- Der Clip-Editor merkt sich nach „Übernehmen“ die neue Reihenfolge.
- **Titel:** Im 58-s-Short wird kein Titel mehr eingeblendet; der Titel nutzt jetzt Arial Bold mit dezenterem Schatten. Alle Akzent- und Sonderbuchstaben (é, ñ, ç, ğ, å …) bleiben im Titel erhalten.

## v2.6

**Fehlerbehebung: Video- und Audiolänge stimmten nicht überein**

- **Zu kurzes erstes Clip-Segment nach dem Intro:** Der erste Schnitt nach dem Intro konnte durch Zufall sehr nah am Intro-Ende liegen und wirkte dadurch wie ein Fehler (teils nur 0,3–1 Sekunde statt der üblichen Segmentlänge). Cut-Punkte, die zu nah am Intro-Ende liegen, werden jetzt übersprungen.
- **Video länger als die Musik bei gleichzeitig aktivem Pre-Intro und Outro:** Der Outro-Ton wurde zeitlich falsch platziert, wenn zusätzlich ein Pre-Intro aktiv war — er startete zu früh, wodurch sich Bild- und Tonlänge um bis zu ~2 Sekunden unterscheiden konnten. Die Verzögerung des Outro-Tons berücksichtigt jetzt korrekt die Pre-Intro-Länge.

## v2.5

- **Intro verlängerte das Gesamtvideo über die Musiklänge hinaus:** Die für die Clip-Planung verwendete Intro-Länge stammte aus der Originaldatei statt aus dem tatsächlich gerenderten Segment. Kleine Rundungsunterschiede bei der Framerate-Konvertierung summierten sich dadurch zu einer hörbaren Überlänge. Die Segmentplanung nutzt jetzt die tatsächlich gerenderte Länge.

## v2.4

- Gleiches Problem wie in v2.5, zunächst für das **Pre-Intro** behoben (Längenmessung aus dem gerenderten Segment statt der Quelldatei).

## v2.3

- Schalter-Reihenfolge angepasst: **Pre-Intro → Intro → Outro → Titel**, passend zur zeitlichen Abfolge im Video.
- „Vorspann" in „Pre-Intro" umbenannt (Schalter, Statusmeldungen, Clip-Editor, Anleitung), inklusive Ordnerumbenennung `vorspann/` → `pre-intro/`.
- „Titel einblenden" zu „Titel" gekürzt.

## v2.2

- **Neues Feature: Pre-Intro.** Optionaler Clip mit eigenem Ton, der vor der Musik läuft (Standard: aus). Verhält sich unabhängig vom restlichen Video — beeinflusst weder Beat-Erkennung noch Cut-Punkte.
- Im Clip-Editor ist das Pre-Intro-Segment wie das Outro fixiert (nicht verschiebbar).

## v2.1

- Vier Bugs behoben, u. a.: Reset setzte die manuelle Clip-Auswahl nicht zurück; der Splash-Screen konnte bei einem fehlgeschlagenen Bibliotheks-Import endlos hängen bleiben; Dateinamen mit Apostroph konnten die FFmpeg-Zusammenführung zum Absturz bringen.

## v2.0

- Umbenennungen und Aufräumarbeiten im Hilfe-System: Kurzanleitung direkt im Programm, PDF-Anleitung liegt lose neben der EXE.
- „YouTube"-Button zum schnellen Hochladen ergänzt.

---

*Ältere Versionen (v1.x) siehe Commit-Historie.*
