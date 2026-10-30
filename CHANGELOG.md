# Changelog — Musik Video Generator

## v4.2

**Neu**

- **Vollautomatik:** ⚙ → „⚡ Vollautomatik“ nimmt zur gewählten Musik 10 zufällige Clips aus dem Pool, plant, rendert und zeigt die Fertig-Seite. Ohne Musik oder bei leerem Pool erscheint ein kurzer Hinweis; eine vorhandene Auswahl wird nach Rückfrage ersetzt.
- **Pre-Intro, Intro und Outro auswählen:** Liegen mehrere Videos im jeweiligen Ordner, erscheint in Schritt 3 neben dem Schalter ein Auswahlfeld – „Zufällig“ (wie bisher) oder ein bestimmtes Video. Die Wahl wird gemerkt; fehlt das Video später, gilt wieder „Zufällig“. Die Zusammenfassung zeigt das gewählte Video in Klammern.

**Verbessert**

- **Protokolle im Ordner `logs/`:** Fehler-, Ablauf- und FFmpeg-Protokoll liegen nicht mehr lose neben der EXE; alte Protokolle werden beim ersten Start dorthin verschoben. Vom Fehlerprotokoll bleiben nur die jüngsten Einträge (höchstens ca. 1 MB).
- **Homepage:** neues Beispielvideo; auf dem Handy führt der Knopf oben rechts zu MVG Mobil statt zum Windows-Download.

## v4.1

**Neu**

- **Problem melden:** ⚙ → „Problem melden …“ (und ein Knopf direkt bei Fehlermeldungen) legt einen Fehlerbericht als ZIP-Datei auf den Desktop: Protokolle der aktuellen und der vorigen Sitzung, Windows-Version, Arbeitsspeicher, freier Platz, FFmpeg-Version sowie Namen und Formate der ausgewählten Clips. Keine Videos, Fotos oder Musik. Wer den Entwickler kennt, schickt die Datei direkt; sonst öffnet „Auf GitHub melden“ die Issues-Seite (mit vorausgefülltem Text) und den Ordner mit der Datei.
- **Cache leeren** als eigener Eintrag im ⚙-Menü, mit Anzeige der Größe. Bisher war das nur über „Einstellungen zurücksetzen“ erreichbar.
- **Fortschritt beim Vorbereiten:** Clips in besonderen Formaten (z. B. 4K/HEVC vom Handy) zeigen beim ersten Rendern „vorbereiten 35 %“, statt scheinbar stillzustehen.

**Verbessert**

- **Schutz gegen Hänger:** Jeder ffmpeg-Schritt wird überwacht. Tut sich 5 Minuten lang nichts, wird er mit einer klaren Meldung abgebrochen. ffmpeg bekommt kein offenes Eingabe- und Ausgabefenster mehr (`-nostdin`, Ausgaben ins Protokoll).
- **Protokolle:** `mvg_start.log` hält jeden Render-Schritt mit Uhrzeit fest, `mvg_ffmpeg.log` die Ausgaben von ffmpeg. Die Protokolle der vorigen Sitzung bleiben als `…_vorher.log` erhalten.

**Fehlerbehebung**

- Ein abgebrochenes Vorbereiten eines Clips konnte eine unvollständige Datei im Cache hinterlassen, die beim nächsten Rendern Probleme machte. Vorbereitet wird jetzt in eine Zwischendatei; vorhandene unvollständige Dateien werden erkannt und neu erstellt.

## v4.0

Die Bedienung wurde komplett neu aufgebaut. Statt eines großen Fensters mit vielen Optionen führt das Programm jetzt in fünf Schritten zum Video: **1 Start → 2 Bildmaterial → 3 Extras → 4 Schnitt → 5 Fertig**. Die Schrittleiste oben zeigt jederzeit, wo man steht; erledigte Schritte lassen sich direkt anklicken.

**Neu**

- **Bildmaterial aus jeder Quelle in einer Auswahl:** Clip-Pool, beliebige Ordner, einzelne Dateien oder einfach per Drag & Drop ins Fenster (Videos, Fotos, ganze Ordner). Einzelne Einträge lassen sich wieder entfernen.
- **Clip-Pool vereinfacht:** ein einziger Ordner `clips/` statt Stimmungs-Ordnern. Das Auswahlfenster zeigt Vorschaubilder; ein Klick wählt einen Clip aus oder ab. Vorschaubilder werden parallel erzeugt und auf der Festplatte zwischengespeichert – auch bei vielen Clips öffnet sich das Fenster schnell.
- **Optionale Pool-Kategorien:** Unterordner in `clips/` erscheinen als Reiter („Alle“, je Unterordner, „Weitere“). Die Auswahl gilt über alle Reiter, der zuletzt benutzte Reiter wird gemerkt. Ohne Unterordner sieht alles aus wie gewohnt. Die alten Stimmungs-Ordner aus v3.0 werden so automatisch zu Reitern.
- **Schnitt – Clipfolge vor dem Rendern (Schritt 4):** Das Programm plant das Video und zeigt jeden Abschnitt als Kachel. Clips per Ziehen verschieben oder mit „Clip tauschen“ durch einen anderen Clip gleicher Länge ersetzen. Pre-Intro, Intro und Outro sind gesperrt.
- **Planen mit Schritt-Liste:** Musik analysieren, Schnitte setzen, Extras wählen, Clips verteilen, Vorschaubilder vorbereiten – jeder Schritt mit eigenem Balken und Haken.
- **Live-Ansicht beim Rendern:** Ein Laufband zeigt den Abschnitt, der gerade entsteht; fertige laufen nach links hinaus. Darunter ein Übersichtsstreifen des ganzen Videos und eine Liste aller Arbeitsschritte (auch für automatisch mit erstellte Formate wie den Short) sowie der Gesamtfortschritt in Prozent.
- **Nachträglich ändern:** Auf der Fertig-Seite führt „✏ Clips ändern“ zurück zur Clipfolge. „Änderungen übernehmen“ rendert nur, was sich geändert hat (Segment-Cache); „Verwerfen“ stellt den Stand des fertigen Videos wieder her und führt zurück zur Fertig-Seite.
- **Ganzer Clip:** Ein zweiter Klick auf die markierte Schnittgeschwindigkeit hebt die Markierung auf. Dann läuft jeder Clip komplett; geschnitten wird auf dem letzten Beat vor dem Clip-Ende. Fotos stehen 4 Sekunden. Beim Verschieben oder Tauschen in Schritt 4 wandern die folgenden Schnitte mit.
- **Loop aus einem Clip:** Liegt genau ein Video (ohne Fotos) in der Auswahl, läuft es automatisch als Loop durch den ganzen Song. Das Programm sucht die beste Loop-Stelle (ähnlichste Bilder, kurze Überblendung, Länge auf den Beat abgestimmt); ist kein sauberer Übergang möglich, wird es ein Boomerang (vor und zurück). Gerendert wird nur ein Durchlauf, die Wiederholungen werden ohne Neuberechnung angehängt. Schritt 4 zeigt dafür eine Loop-Karte.
- **Thumbnail-Werkzeugpalette:** „✎ Bearbeiten“ auf der Fertig-Seite öffnet ein Fenster mit Live-Vorschau: Bild per Schieberegler frei aus dem Video wählen, Helligkeit/Kontrast/Sättigung, Abdunkeln hinter dem Text, zwei Textzeilen mit Schrift, Größe, Farbe, Kontur, Schatten und Position, eigenes Logo sowie Vorlagen (optional automatisch auf jedes neue Thumbnail anwenden). Ohne Bearbeiten bleibt alles automatisch wie bisher. Text und Logo lassen sich in der Vorschau frei mit der Maus verschieben.
- **Fertig-Seite mit Thumbnail-Generator:** Aus dem eigenen Video wird automatisch ein YouTube-Thumbnail (1280×720, JPG, ohne Titel) gewählt und als `<Titel>_Thumbnail.jpg` im Output-Ordner gespeichert. „↻ Anderes Bild“ schlägt ein anderes vor. Bei reinem Hochformat gibt es nur ein Vorschaubild. Das Video öffnet sich wie bisher im Standard-Player.
- **Short-Logik:** Wird der 58-s-Short zusammen mit 16:9 oder 9:16 gewählt, entsteht er automatisch mit. Allein gewählt, lässt sich seine Clipfolge sortieren.
- **Formate nachträglich ändern:** Wird nach dem Rendern ein Format dazu angekreuzt, erstellt „▶ Fehlende Formate erstellen“ nur dieses (ohne das Hauptvideo neu zu rendern). Abgewählte Formate verschwinden von der Fertig-Seite, die Dateien bleiben im Output-Ordner.
- **Sprachwechsel ohne Neustart:** ⚙ → Sprache öffnet denselben Auswahlbildschirm wie beim ersten Start; die Auswahl bleibt erhalten.
- **Darstellung Hell / Dunkel / System** im ⚙-Menü.
- **Neuer erster Start:** ansprechender Sprachbildschirm, danach eine einmalige Willkommensseite mit Hinweis auf den Clip-Pool.
- **Klick-Hilfe vollständig neu:** jeder Bereich jeder Seite hat eine ausführliche Erklärung in Deutsch, English und Español.
- **Bedienungsanleitungen** (DE/EN/ES) komplett neu mit aktuellen Screenshots.
- **Programm-Icon** im Fenster, in der Titelleiste und in allen Unterfenstern; das Icon ist eingebettet und funktioniert auch ohne `icon.ico`.
- **Cache wird automatisch aufgeräumt:** Gerenderte Abschnitte beim Programmstart und bei „Neues Video“, vorbereitete Clips nach 60 Tagen ohne Nutzung (höchstens 5 GB), Vorschaubilder nach 30 Tagen (höchstens 300 MB).
- **PDF-Anleitungen in der EXE:** Die drei Anleitungen sind eingebaut und werden beim Start in den Ordner manual/ gelegt; ⚙ → PDF-Anleitung öffnet die passende Sprache. Liegt eine PDF neben der EXE, hat diese Vorrang.
- **Ordner logo/:** für eigene Logos im Thumbnail-Editor, zu Beginn mit dem MVG-Logo; „Logo wählen …“ öffnet direkt diesen Ordner.
- **Über MVG** (⚙-Menü): Version mit Update-Prüfung, Copyright und MIT-Lizenz, Links zu Homepage, GitHub und „Fehler melden“, „☕ MVG unterstützen“ sowie die Lizenzen aller verwendeten Programme (inkl. der eingebauten FFmpeg-Version).
- **Update-Hinweis:** Beim Start fragt MVG still bei GitHub nach; gibt es eine neuere Version, erscheint oben „⬆ Version … verfügbar“ mit Link zur Download-Seite. Ohne Internet passiert nichts, installiert wird nichts automatisch.
- **Restzeit beim Rendern:** neben den Prozent steht nach wenigen Sekunden „noch ca. … Min.“.
- **Weiches Ende:** Ohne Outro blenden Bild und Ton in der letzten Sekunde aus, statt hart abzubrechen.
- **Ordner merken:** Die Dateiauswahl für Musik, Videos und Fotos öffnet sich im zuletzt benutzten Ordner.
- **Klick-Hilfe in Unterfenstern:** Thumbnail-Editor und Pool-Fenster haben einen eigenen „?“-Button.
- **Short per URL mit Fortschrittsfenster:** zeigt sofort Link, Videotitel und alle Arbeitsschritte mit Balken; am Ende Dateiname und „Ordner öffnen“, bei Fehlern die Ursache im selben Fenster. Ungültige Links werden sofort gemeldet. Das Fenster kann geschlossen werden, der Short entsteht im Hintergrund weiter.

**Entfernt**

- Einfach-Modus (die neue Schrittführung ersetzt ihn)
- Stimmungs-Ordner und Pool-Größe
- Separater Clip-Editor-Dialog (ersetzt durch Schritt 4)
- Ordner `photos/` (Fotos kommen aus jedem beliebigen Ordner)

**Fehlerbehebung**

- Titel erschien bei aktivem Pre-Intro zu früh – er startet jetzt nach Pre-Intro und Intro.
- Ein bestehendes Video wird erst überschrieben, wenn das neue vollständig fertig ist.
- Vorschaubilder im Pool erschienen teils erst nach einem Klick.
- Die Willkommensseite zählte Dateien in Pool-Unterordnern nicht mit.
- Loop-Render konnte unter Windows hängen bleiben; ffmpeg-Prozesse werden beim Beenden des Programms jetzt zuverlässig geschlossen, gesperrte Zwischendateien führen nicht mehr zu „WinError 32“.
- „Short per URL“ konnte in der EXE ohne Konsolenfenster beim Analysieren hängen bleiben.

## v3.0

**Neu**

- **Einfach-Modus:** Beim ersten Start wird nach der Sprache der Modus gewählt (Einfach / Fortgeschritten). Im Einfach-Modus genügt ein schlankes Fenster: Musik, optional ein Intro und die Videos hineinziehen (oder anklicken) und „Render“ drücken. Es entstehen ein 16:9-Video und ein 58-s-Short, ohne internen Pool. Bestehende Nutzer behalten das gewohnte Fenster. Drag & Drop benötigt `tkinterdnd2` (ohne: Klick-Auswahl). Eine Infozeile am Fensterrand verweist auf das ⚙-Menü (Anleitung, Einstellungen, Wechsel zur fortgeschrittenen Version). Die temporären Clip-Kopien (`simple_clips`) werden nach dem Rendern automatisch gelöscht.
- **Mehrsprachig: Deutsch, English, Español.** Die Sprache wird beim ersten Start gewählt und ist jederzeit im ⚙-Menü änderbar (danach Neustart). Gespeichert in `mvg_settings.json` neben dem Programm.
- **⚙-Menü (statt „?“):** Sprache und Modus wechseln, Hilfe (Klick-Hilfe, Kurzanleitung, PDF-Anleitung), „Neueste Version (GitHub)“ und „Mobile Version (GitHub)“.
- **Klick-Hilfe:** Alle 36 Erklärungen wurden ausführlich neu geschrieben (Deutsch, English, Español), mit Hinweisen auf Stolperstellen, etwa dass der Windows-Ordnerdialog nur Ordner zeigt oder dass „Short per URL“ den Link aus der Zwischenablage liest. Lange Texte erscheinen in einem scrollbaren Fenster. Auf einen beliebigen Knopf oder Bereich klicken und eine ausführliche Erklärung erhalten (36 Themen, drei Sprachen, Hauptfenster und Clip-Editor). Nach einer Erklärung beendet sich der Modus von selbst.
- **Kurzanleitung** aufgeräumt und ohne Buttons.
- **Bedienungsanleitung als PDF in drei Sprachen** (`MVG_Bedienungsanleitung.pdf`, `MVG_Manual_EN.pdf`, `MVG_Manual_ES.pdf`) mit neuen Abschnitten „Deinstallation“ und „Die Datei deno.exe“.
- **Fehlerprotokoll:** Startfehler werden in `mvg_error.log` neben der EXE geschrieben; `mvg_start.log` zeigt die Startphasen.

**Fehlerbehebung**

- Download der Hilfskomponente Deno schlug bei Zertifikatsproblemen fehl (`CERTIFICATE_VERIFY_FAILED`); jetzt mit Rückfallwegen.
- Einfach-Modus: „PDF-Anleitung“ im ⚙-Menü brach mit einem Fehler ab (`ui_status` nicht definiert); jetzt öffnet sie die PDF bzw. zeigt einen Hinweis.
- Der Ordner `output/Shorts` bleibt nicht mehr leer zurück, wenn „Short per URL“ scheitert.
- Startfehler `KeyError: 'Sad'` bei englischer/spanischer Oberfläche behoben.
- Meldung beim Fehlschlag von „Clip entfernen“ im Editor konnte einen Programmfehler auslösen – behoben.

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
