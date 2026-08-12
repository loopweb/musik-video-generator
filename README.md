# Musik Video Generator (MVG)

Ein Windows-Desktop-Tool, das automatisch Musikvideos erstellt: Musikdatei auswählen, Clips aus einem internen Pool oder externen Ordnern werden per FFmpeg beat-synchron zusammengeschnitten – fertig.

![Format](https://img.shields.io/badge/Format-16%3A9%20%7C%209%3A16%20%7C%20Shorts-1f6aa5)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

## Download

Fertige Windows-EXE (portable, kein Python nötig):
**[→ Releases](../../releases)**

## Funktionen

- **Beat-synchrones Schneiden** – Musik wird analysiert, Schnitte automatisch auf den Beat gelegt
- **Drei Formate** – 16:9, 9:16, oder kurzes Shorts-Format
- **Fünf Schnittgeschwindigkeiten** – von sanft bis energetisch, oder zufällig gemischt
- **Interner Clip-Pool** – eigene Clips nach Stimmung sortiert (traurig, neutral, glücklich, Animation)
- **Externe Clips & Fotos** – zusätzliches oder alternatives Bildmaterial einbinden
- **Pre-Intro, Intro, Outro** – optionale Bausteine, Pre-Intro und Outro mit eigenem Ton
- **Titel-Overlay** – automatisch aus dem Musikdateinamen generiert
- **Clip-Editor** – Reihenfolge der Clips nach dem Rendern per Drag & Drop anpassen, ohne komplett neu zu rendern
- **YouTube-Shortcut** – Upload-Seite direkt aus dem Programm öffnen

## Installation & Nutzung

### Für Anwender

1. Aktuelles Release als ZIP herunterladen und entpacken
2. `MVG.exe` starten – benötigte Unterordner werden automatisch angelegt
3. Eigene Videoclips in `clips/traurig/`, `clips/neutral/`, `clips/gluecklich/` und `clips/animations/` legen (das Programm wird ohne Clips ausgeliefert)
4. Musik laden, Optionen wählen, Rendern

Ausführliche Anleitung: `MVG_Bedienungsanleitung.pdf` (liegt neben der EXE, oder direkt im Programm über den `?`-Button erreichbar)

### Für Entwickler

## Zusätzlich benötigte Datein

- **icon.ico
- **splash.png
- **Sound/done.wav
- **ffmpeg/

```bash
git clone https://github.com/<user>/<repo>.git
cd <repo>
pip install -r requirements.txt
```

**FFmpeg wird benötigt**, ist aber nicht im Repo enthalten (Lizenz/Größe). Lade FFmpeg von [ffmpeg.org](https://ffmpeg.org/download.html) herunter und lege `ffmpeg.exe` / `ffprobe.exe` in einen Ordner `ffmpeg/` im Projektverzeichnis.

Programm starten:

```bash
python gui_step3.py
```

### EXE selbst bauen

```bash
build_exe.bat
```

Voraussetzung: Python 3.12, PyInstaller (in `requirements.txt` enthalten), sowie `ffmpeg/`, `Sound/`, `splash.png` und `icon.ico` im Projektordner. Die fertige EXE liegt danach in `dist/`.

## Ordnerstruktur

| Ordner | Inhalt |
|---|---|
| `clips/traurig/` `clips/neutral/` `clips/gluecklich/` `clips/animations/` | Eigene Videoclips nach Stimmung (selbst befüllen) |
| `pre-intro/` | Video mit eigenem Ton, läuft vor der Musik (optional) |
| `intro/` | Stummes Intro unter der Musik (optional) |
| `outro/` | Video mit eigenem Ton, läuft nach der Musik (optional) |
| `photos/` | Externe Fotos (optional) |
| `output/` | Fertige Videos |
| `cache/` | Normalisierungs- und Segment-Cache (automatisch verwaltet) |

Alle Ordner werden beim ersten Programmstart automatisch angelegt.

## Tech-Stack

Python 3.12 · CustomTkinter · librosa · FFmpeg · PyInstaller

## Lizenz

MIT – siehe [LICENSE](LICENSE).

FFmpeg ist nicht Teil dieses Quellcode-Repos (siehe `.gitignore`) und muss für die lokale Entwicklung separat bezogen werden. Die fertige EXE in den [Releases](../../releases) enthält FFmpeg eingebettet – die Weitergabe der kompilierten FFmpeg-Binärdateien zusammen mit dieser Anwendung ist im Rahmen der [FFmpeg-Lizenz](https://ffmpeg.org/legal.html) (LGPL/GPL, je nach Build) zulässig.
