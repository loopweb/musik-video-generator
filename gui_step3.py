# Musik Video Generator v2.9

from __future__ import annotations

import os
import sys
import threading
import subprocess
from pathlib import Path

# ── Splash Screen (startet sofort, vor den schweren Imports)
def _show_splash():
    import tkinter as tk
    from PIL import Image, ImageTk

    def resource_path_early(rel: str) -> str:
        import sys
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        return str(base / rel)

    splash = tk.Tk()
    splash.overrideredirect(True)           # kein Fensterrahmen
    splash.attributes("-topmost", True)

    # Grafik laden
    img_path = resource_path_early("splash.png")
    try:
        pil_img = Image.open(img_path)
    except Exception:
        # Fallback falls Datei fehlt
        splash.destroy()
        return

    sw, sh = pil_img.size
    # Fenster zentrieren
    screen_w = splash.winfo_screenwidth()
    screen_h = splash.winfo_screenheight()
    x = (screen_w - sw) // 2
    y = (screen_h - sh) // 2
    splash.geometry(f"{sw}x{sh}+{x}+{y}")

    tk_img = ImageTk.PhotoImage(pil_img)
    lbl = tk.Label(splash, image=tk_img, bd=0)
    lbl.pack()

    splash.update()

    # Hauptprogramm in Hintergrundthread laden
    ready = threading.Event()
    load_error: list = []

    def load_main():
        try:
            # Schwere Imports triggern
            import numpy
            import librosa
            import customtkinter
        except Exception as e:
            load_error.append(e)
        finally:
            ready.set()

    threading.Thread(target=load_main, daemon=True).start()

    # Warten bis geladen — min. 2 Sekunden, max. 30 Sekunden als Sicherheitsnetz
    import time
    start = time.time()
    MAX_WAIT = 30.0
    while (not ready.is_set() or (time.time() - start) < 2.0) and (time.time() - start) < MAX_WAIT:
        splash.update()
        time.sleep(0.02)

    splash.destroy()

    if load_error:
        import tkinter.messagebox as mb
        mb.showerror(
            "Fehler beim Start",
            f"Eine benötigte Bibliothek konnte nicht geladen werden:\n{load_error[0]}"
        )
        sys.exit(1)
    elif not ready.is_set():
        import tkinter.messagebox as mb
        mb.showerror(
            "Fehler beim Start",
            "Das Laden der Bibliotheken hat zu lange gedauert (Timeout)."
        )
        sys.exit(1)

_show_splash()

# ── Ab hier normale Imports
import hashlib
import random
import shutil
import tempfile
from typing import Callable, Optional, Tuple

import numpy as np
import librosa

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image


# --------------------- Windows: keine Konsolenfenster ---------------------
CREATE_NO_WINDOW = 0
if os.name == "nt":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def run_hidden(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, creationflags=CREATE_NO_WINDOW)


def check_output_hidden(cmd: list[str]) -> bytes:
    return subprocess.check_output(cmd, creationflags=CREATE_NO_WINDOW)


# --------------------- Pfade (EXE/Bundle) ---------------------
def resource_path(rel_path: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return str(base / rel_path)


def app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# --------------------- Sprache / Übersetzung ---------------------
import json as _json

SETTINGS_FILE = app_dir() / "mvg_settings.json"
LANGS = {"de": "Deutsch", "en": "English", "es": "Español"}


def load_settings() -> dict:
    try:
        return _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        cur = load_settings()
        cur.update(data)
        SETTINGS_FILE.write_text(_json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


_SETTINGS = load_settings()
LANG = _SETTINGS.get("lang", "")
if LANG not in LANGS:
    LANG = ""          # noch nicht gewählt -> Auswahl beim ersten Start (de bis dahin)


def tr(text: str) -> str:
    """Übersetzt einen deutschen Programmtext in die gewählte Sprache (Fallback: Deutsch)."""
    if LANG in ("", "de"):
        return text
    return TRANSLATIONS.get(LANG, {}).get(text, text)


TRANSLATIONS: dict = {"en": {}, "es": {}}   # wird weiter unten gefüllt


TRANSLATIONS = {
 "en": {
  "  —  Fehler bei ": "  —  errors in ",
  "(keine Musik gewählt)": "(no music selected)",
  "Abbrechen": "Cancel",
  "Alle": "All",
  "Analysiere Musik…": "Analyzing music…",
  "Animation": "Animation",
  "Anzahl:": "Count:",
  "Automatisch": "Automatic",
  "Beenden": "Quit",
  "Bitte ein einzelnes, abgeschlossenes Video verwenden.": "Please use a single, finished video.",
  "Bitte warten — Clip wird gerade ersetzt …": "Please wait — a clip is being replaced …",
  "Cache geleert ({n} Dateien)": "Cache cleared ({n} files)",
  "Clip ersetzt — mit „Übernehmen“ speichern": "Clip replaced — save with “Apply”",
  "Clip konnte nicht ersetzt werden: ": "Clip could not be replaced: ",
  "Clips (intern): {path}": "Clips (internal): {path}",
  "Clips auswählen  ({moods})": "Select clips  ({moods})",
  "Clips per Drag & Drop umsortieren oder mit „✕ Entfernen“ ersetzen – dann Übernehmen": "Reorder clips by drag & drop or replace them with “✕ Remove” – then Apply",
  "Clips verschieben oder entfernen, dann Übernehmen klicken": "Move or remove clips, then click Apply",
  "Clips: {path}": "Clips: {path}",
  "Clips: {v} | Fotos: {p}": "Clips: {v} | Photos: {p}",
  "Das Video muss mindestens {n} Sekunden lang sein.": "The video must be at least {n} seconds long.",
  "Die heruntergeladene Videodatei wurde nicht gefunden.": "The downloaded video file was not found.",
  "Du befindest dich im Editor. Wirklich beenden?": "You are in the editor. Really quit?",
  "Ersetze Clip … (wird neu gerendert)": "Replacing clip … (re-rendering)",
  "Externe Clips": "External clips",
  "Externe Clips wählen…": "Choose external clips…",
  "Externe Fotos": "External photos",
  "Externe Fotos wählen…": "Choose external photos…",
  "Externer Clip-Ordner": "External clip folder",
  "Externer Clip-Ordner gesetzt": "External clip folder set",
  "Fehler: ": "Error: ",
  "Fertig: ": "Done: ",
  "Format": "Format",
  "Formate: ": "Formats: ",
  "Foto-Ordner": "Photo folder",
  "Foto-Ordner gesetzt": "Photo folder set",
  "Fotos: {path}": "Photos: {path}",
  "Glücklich": "Happy",
  "Hänge Outro an…": "Appending outro…",
  "In der Zwischenablage ist kein gültiger YouTube-Link.": "The clipboard does not contain a valid YouTube link.",
  "Interner Clip-Pool": "Internal clip pool",
  "Interner Pool": "Internal pool",
  "Intro": "Intro",
  "Keine": "None",
  "Keine Ersatz-Clips verfügbar": "No replacement clips available",
  "Keine Medien gefunden. (Extern/Intern prüfen)": "No media found. (Check external/internal)",
  "Keine Medien zum Auswählen vorhanden.": "No media available to choose from.",
  "Keine Quelle aktiv": "No source active",
  "Keine alternativen Clips verfügbar.": "No alternative clips available.",
  "Keine internen Clips gefunden": "No internal clips found",
  "Klebe Segmente…": "Joining segments…",
  "Kurzanleitung": "Quick guide",
  "Langsam": "Slow",
  "Manuelle Auswahl": "Manual selection",
  "Manuelle Auswahl: alle Clips": "Manual selection: all clips",
  "Manuelle Auswahl: {n} Clips": "Manual selection: {n} clips",
  "Manuelle Clip-Auswahl  —  Stimmung: {moods}": "Manual clip selection  —  Mood: {moods}",
  "Mindestens ein Format muss aktiv sein": "At least one format must be active",
  "Musik Video Generator": "Music Video Generator",
  "Musik Video Generator  v2.9": "Music Video Generator  v2.9",
  "Musik auswählen": "Choose music",
  "Musik wird analysiert …": "Analyzing music …",
  "Musik wählen": "Choose music",
  "Muxe Audio…": "Mixing audio…",
  "Neutral": "Neutral",
  "Normal": "Normal",
  "Normal  16:9": "Normal  16:9",
  "Normal  9:16": "Normal  9:16",
  "Normalisiere: ": "Normalizing: ",
  "OK": "OK",
  "Output-Ordner": "Output folder",
  "Output: ": "Output: ",
  "Output: (Musik auswählen)": "Output: (choose music)",
  "Outro": "Outro",
  "PDF-Anleitung nicht neben dem Programm gefunden": "PDF manual not found next to the program",
  "PDF-Anleitung öffnen": "Open PDF manual",
  "Pool: AUS": "Pool: OFF",
  "Pool: alle / 10 / 20 / 30": "Pool: all / 10 / 20 / 30",
  "Pool: keine Clips in der gewählten Stimmung": "Pool: no clips in the selected mood",
  "Pre-Intro": "Pre-intro",
  "Projekt zurücksetzen?": "Reset project?",
  "Projekt zurücksetzen?\n\nCache enthält {n} Datei(en) ({mb:.1f} MB).\nCache ebenfalls leeren?": "Reset project?\n\nThe cache contains {n} file(s) ({mb:.1f} MB).\nClear the cache as well?",
  "Projekt-Reset": "Project reset",
  "Quelle: (nichts gewählt)": "Source: (nothing selected)",
  "Quelle: MIX (Extern bevorzugt)": "Source: MIX (external preferred)",
  "Quelle: NUR EXTERN": "Source: EXTERNAL ONLY",
  "Quelle: NUR INTERN": "Source: INTERNAL ONLY",
  "Quellen & Optionen": "Sources & options",
  "Random": "Random",
  "Rendere Intro…": "Rendering intro…",
  "Rendere Pre-Intro…": "Rendering pre-intro…",
  "Rendere Segmente…": "Rendering segments…",
  "Reset": "Reset",
  "Reset nicht möglich während Render läuft": "Reset not possible while rendering",
  "Sanft": "Gentle",
  "Schließen": "Close",
  "Schnell": "Fast",
  "Schnitt": "Cutting",
  "Short  9:16 · 58s": "Short  9:16 · 58s",
  "Short fertig: {name}  (Ordner output/Shorts)": "Short ready: {name}  (folder output/Shorts)",
  "Short per URL": "Short from URL",
  "Short per URL (Zwischenablage)": "Short from URL (clipboard)",
  "Short per URL fehlgeschlagen": "Short from URL failed",
  "Short wird erstellt …": "Creating short …",
  "Short: ab {t}": "Short: from {t}",
  "Starte…": "Starting…",
  "Stimmung: ": "Mood: ",
  "Stimmung: alle": "Mood: all",
  "Suche stärksten Abschnitt für den Short…": "Finding the strongest section for the short…",
  "Titel": "Title",
  "Traurig": "Sad",
  "Video wird geladen …": "Downloading video …",
  "Video wird geprüft …": "Checking video …",
  "Wähle deine Stimmung": "Choose your mood",
  "YouTube": "YouTube",
  "YouTube-Werkzeug wird geprüft …": "Checking YouTube tool …",
  "Ziehen = neue Position · Entfernen = zufälliger Ersatzclip gleicher Länge (Intro/Outro bleiben)": "Drag = new position · Remove = random replacement clip of the same length (intro/outro stay)",
  "Zwischenablage leer – bitte zuerst einen YouTube-Link kopieren": "Clipboard empty – please copy a YouTube link first",
  "bereit": "ready",
  "bitte Musik auswählen": "please choose music",
  "kein Video vorhanden": "no video available",
  "keine externen Clips gefunden": "no external clips found",
  "keine externen Fotos gefunden": "no external photos found",
  "yt-dlp ist nicht installiert (pip install yt-dlp).": "yt-dlp is not installed (pip install yt-dlp).",
  "{n} / {total} ausgewählt": "{n} / {total} selected",
  "⏳  Vorschaubilder werden geladen…": "⏳  Loading thumbnails…",
  "▶  Render": "▶  Render",
  "▶  Video anschauen": "▶  Watch video",
  "✏  Clips verschieben": "✏  Edit clips",
  "✔  Übernehmen": "✔  Apply",
  "✕  Abbrechen": "✕  Cancel",
  "✕  Entfernen": "✕  Remove",
  "✕  Zurück": "✕  Back",
  "🔒 Outro": "🔒 Outro",
  "🔒 Pre-Intro": "🔒 Pre-intro",
 },
 "es": {
  "  —  Fehler bei ": "  —  errores en ",
  "(keine Musik gewählt)": "(ninguna música seleccionada)",
  "Abbrechen": "Cancelar",
  "Alle": "Todos",
  "Analysiere Musik…": "Analizando la música…",
  "Animation": "Animación",
  "Anzahl:": "Cantidad:",
  "Automatisch": "Automático",
  "Beenden": "Salir",
  "Bitte ein einzelnes, abgeschlossenes Video verwenden.": "Usa un solo vídeo ya terminado.",
  "Bitte warten — Clip wird gerade ersetzt …": "Espera — se está reemplazando un clip …",
  "Cache geleert ({n} Dateien)": "Caché vaciada ({n} archivos)",
  "Clip ersetzt — mit „Übernehmen“ speichern": "Clip reemplazado — guarda con «Aplicar»",
  "Clip konnte nicht ersetzt werden: ": "No se pudo reemplazar el clip: ",
  "Clips (intern): {path}": "Clips (internos): {path}",
  "Clips auswählen  ({moods})": "Seleccionar clips  ({moods})",
  "Clips per Drag & Drop umsortieren oder mit „✕ Entfernen“ ersetzen – dann Übernehmen": "Reordena los clips arrastrando o reemplázalos con «✕ Quitar» – luego Aplicar",
  "Clips verschieben oder entfernen, dann Übernehmen klicken": "Mueve o quita clips y luego pulsa Aplicar",
  "Clips: {path}": "Clips: {path}",
  "Clips: {v} | Fotos: {p}": "Clips: {v} | Fotos: {p}",
  "Das Video muss mindestens {n} Sekunden lang sein.": "El vídeo debe durar al menos {n} segundos.",
  "Die heruntergeladene Videodatei wurde nicht gefunden.": "No se encontró el archivo de vídeo descargado.",
  "Du befindest dich im Editor. Wirklich beenden?": "Estás en el editor. ¿Salir de verdad?",
  "Ersetze Clip … (wird neu gerendert)": "Reemplazando clip … (renderizando de nuevo)",
  "Externe Clips": "Clips externos",
  "Externe Clips wählen…": "Elegir clips externos…",
  "Externe Fotos": "Fotos externas",
  "Externe Fotos wählen…": "Elegir fotos externas…",
  "Externer Clip-Ordner": "Carpeta de clips externos",
  "Externer Clip-Ordner gesetzt": "Carpeta de clips externos definida",
  "Fehler: ": "Error: ",
  "Fertig: ": "Listo: ",
  "Format": "Formato",
  "Formate: ": "Formatos: ",
  "Foto-Ordner": "Carpeta de fotos",
  "Foto-Ordner gesetzt": "Carpeta de fotos definida",
  "Fotos: {path}": "Fotos: {path}",
  "Glücklich": "Alegre",
  "Hänge Outro an…": "Añadiendo el outro…",
  "In der Zwischenablage ist kein gültiger YouTube-Link.": "El portapapeles no contiene un enlace de YouTube válido.",
  "Interner Clip-Pool": "Grupo de clips interno",
  "Interner Pool": "Grupo interno",
  "Intro": "Intro",
  "Keine": "Ninguno",
  "Keine Ersatz-Clips verfügbar": "No hay clips de reemplazo",
  "Keine Medien gefunden. (Extern/Intern prüfen)": "No se encontró material. (Revisa externo/interno)",
  "Keine Medien zum Auswählen vorhanden.": "No hay material para elegir.",
  "Keine Quelle aktiv": "Ninguna fuente activa",
  "Keine alternativen Clips verfügbar.": "No hay clips alternativos.",
  "Keine internen Clips gefunden": "No se encontraron clips internos",
  "Klebe Segmente…": "Uniendo segmentos…",
  "Kurzanleitung": "Guía rápida",
  "Langsam": "Lento",
  "Manuelle Auswahl": "Selección manual",
  "Manuelle Auswahl: alle Clips": "Selección manual: todos los clips",
  "Manuelle Auswahl: {n} Clips": "Selección manual: {n} clips",
  "Manuelle Clip-Auswahl  —  Stimmung: {moods}": "Selección manual de clips  —  Ánimo: {moods}",
  "Mindestens ein Format muss aktiv sein": "Debe haber al menos un formato activo",
  "Musik Video Generator": "Generador de vídeos musicales",
  "Musik Video Generator  v2.9": "Generador de vídeos musicales  v2.9",
  "Musik auswählen": "Elegir música",
  "Musik wird analysiert …": "Analizando la música …",
  "Musik wählen": "Elegir música",
  "Muxe Audio…": "Mezclando el audio…",
  "Neutral": "Neutral",
  "Normal": "Normal",
  "Normal  16:9": "Normal  16:9",
  "Normal  9:16": "Normal  9:16",
  "Normalisiere: ": "Normalizando: ",
  "OK": "Aceptar",
  "Output-Ordner": "Carpeta de salida",
  "Output: ": "Salida: ",
  "Output: (Musik auswählen)": "Salida: (elige música)",
  "Outro": "Outro",
  "PDF-Anleitung nicht neben dem Programm gefunden": "No se encontró el manual PDF junto al programa",
  "PDF-Anleitung öffnen": "Abrir manual PDF",
  "Pool: AUS": "Grupo: NO",
  "Pool: alle / 10 / 20 / 30": "Grupo: todos / 10 / 20 / 30",
  "Pool: keine Clips in der gewählten Stimmung": "Grupo: no hay clips en el ánimo elegido",
  "Pre-Intro": "Pre-intro",
  "Projekt zurücksetzen?": "¿Restablecer el proyecto?",
  "Projekt zurücksetzen?\n\nCache enthält {n} Datei(en) ({mb:.1f} MB).\nCache ebenfalls leeren?": "¿Restablecer el proyecto?\n\nLa caché contiene {n} archivo(s) ({mb:.1f} MB).\n¿Vaciar también la caché?",
  "Projekt-Reset": "Restablecer proyecto",
  "Quelle: (nichts gewählt)": "Fuente: (nada seleccionado)",
  "Quelle: MIX (Extern bevorzugt)": "Fuente: MIXTA (prioridad externa)",
  "Quelle: NUR EXTERN": "Fuente: SOLO EXTERNA",
  "Quelle: NUR INTERN": "Fuente: SOLO INTERNA",
  "Quellen & Optionen": "Fuentes y opciones",
  "Random": "Aleatorio",
  "Rendere Intro…": "Renderizando el intro…",
  "Rendere Pre-Intro…": "Renderizando el pre-intro…",
  "Rendere Segmente…": "Renderizando segmentos…",
  "Reset": "Reiniciar",
  "Reset nicht möglich während Render läuft": "No se puede reiniciar durante el renderizado",
  "Sanft": "Suave",
  "Schließen": "Cerrar",
  "Schnell": "Rápido",
  "Schnitt": "Corte",
  "Short  9:16 · 58s": "Short  9:16 · 58 s",
  "Short fertig: {name}  (Ordner output/Shorts)": "Short listo: {name}  (carpeta output/Shorts)",
  "Short per URL": "Short desde URL",
  "Short per URL (Zwischenablage)": "Short desde URL (portapapeles)",
  "Short per URL fehlgeschlagen": "Falló el short desde URL",
  "Short wird erstellt …": "Creando el short …",
  "Short: ab {t}": "Short: desde {t}",
  "Starte…": "Iniciando…",
  "Stimmung: ": "Ánimo: ",
  "Stimmung: alle": "Ánimo: todos",
  "Suche stärksten Abschnitt für den Short…": "Buscando la sección más fuerte para el short…",
  "Titel": "Título",
  "Traurig": "Triste",
  "Video wird geladen …": "Descargando el vídeo …",
  "Video wird geprüft …": "Comprobando el vídeo …",
  "Wähle deine Stimmung": "Elige tu ánimo",
  "YouTube": "YouTube",
  "YouTube-Werkzeug wird geprüft …": "Comprobando la herramienta de YouTube …",
  "Ziehen = neue Position · Entfernen = zufälliger Ersatzclip gleicher Länge (Intro/Outro bleiben)": "Arrastrar = nueva posición · Quitar = clip de reemplazo aleatorio de la misma duración (intro/outro se mantienen)",
  "Zwischenablage leer – bitte zuerst einen YouTube-Link kopieren": "Portapapeles vacío – copia primero un enlace de YouTube",
  "bereit": "listo",
  "bitte Musik auswählen": "elige primero una música",
  "kein Video vorhanden": "no hay vídeo",
  "keine externen Clips gefunden": "no se encontraron clips externos",
  "keine externen Fotos gefunden": "no se encontraron fotos externas",
  "yt-dlp ist nicht installiert (pip install yt-dlp).": "yt-dlp no está instalado (pip install yt-dlp).",
  "{n} / {total} ausgewählt": "{n} / {total} seleccionados",
  "⏳  Vorschaubilder werden geladen…": "⏳  Cargando miniaturas…",
  "▶  Render": "▶  Renderizar",
  "▶  Video anschauen": "▶  Ver vídeo",
  "✏  Clips verschieben": "✏  Editar clips",
  "✔  Übernehmen": "✔  Aplicar",
  "✕  Abbrechen": "✕  Cancelar",
  "✕  Entfernen": "✕  Quitar",
  "✕  Zurück": "✕  Atrás",
  "🔒 Outro": "🔒 Outro",
  "🔒 Pre-Intro": "🔒 Pre-intro",
 },
}

HELP_SECTIONS = {
 "de": [
  ["1. Vorbereiten", "Die Clip-Ordner mit eigenen Videos füllen – das Programm wird ohne Clips ausgeliefert."],
  ["2. Musik & Format", "Musikdatei wählen, dann Format(e) und Schnitt. Standard: 16:9-Video plus 58-s-Short."],
  ["3. Clip-Quelle", "Interner Pool mit Stimmung, eigene Clips/Fotos oder beides gemischt."],
  ["4. Extras", "Pre-Intro, Intro, Outro und Titel sind optional und standardmäßig aus."],
  ["5. Rendern", "Auf „Render“ klicken. Ein Klang meldet das Ende, das Video liegt in output/."],
  ["6. Nachbearbeiten", "„Clips verschieben“: Clips umsortieren oder mit „✕ Entfernen“ ersetzen."],
  ["7. Short per URL", "YouTube-Link kopieren, „Short per URL“ klicken – 45-s-Short in output/Shorts."],
  ["8. Hilfe", "Über das „?“: Klick-Hilfe (Bereich anklicken, Erklärung lesen), PDF, Sprache."]
 ],
 "en": [
  ["1. Prepare", "Fill the clip folders with your own videos – the program ships without clips."],
  ["2. Music & format", "Choose a music file, then format(s) and cutting. Default: 16:9 video plus 58 s short."],
  ["3. Clip source", "Internal pool with mood, your own clips/photos, or both mixed."],
  ["4. Extras", "Pre-intro, intro, outro and title are optional and off by default."],
  ["5. Render", "Click “Render”. A sound signals the end, the video is in output/."],
  ["6. Refine", "“Edit clips”: reorder clips or replace them with “✕ Remove”."],
  ["7. Short from URL", "Copy a YouTube link, click “Short from URL” – 45 s short in output/Shorts."],
  ["8. Help", "Via “?”: click help (click an area, read the explanation), PDF, language."]
 ],
 "es": [
  ["1. Preparar", "Llena las carpetas de clips con tus vídeos – el programa se entrega sin clips."],
  ["2. Música y formato", "Elige un archivo de música, luego formato(s) y corte. Por defecto: vídeo 16:9 y short de 58 s."],
  ["3. Fuente de clips", "Grupo interno con ánimo, tus clips/fotos, o ambos mezclados."],
  ["4. Extras", "Pre-intro, intro, outro y título son opcionales y están apagados por defecto."],
  ["5. Renderizar", "Pulsa «Renderizar». Un sonido avisa del final, el vídeo está en output/."],
  ["6. Retocar", "«Editar clips»: reordena clips o reemplázalos con «✕ Quitar»."],
  ["7. Short desde URL", "Copia un enlace de YouTube y pulsa «Short desde URL» – short de 45 s en output/Shorts."],
  ["8. Ayuda", "Con «?»: ayuda con clic (haz clic en una zona y lee la explicación), PDF, idioma."]
 ]
}

# --------------------- Done Sound ---------------------
try:
    import winsound
except Exception:
    winsound = None

DONE_WAV = resource_path("Sound/done.wav")


def play_done_sound():
    if winsound is None:
        return
    try:
        if os.path.exists(DONE_WAV):
            winsound.PlaySound(DONE_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep()
    except Exception:
        try:
            winsound.MessageBeep()
        except Exception:
            pass


# --------------------- FFmpeg ---------------------
FFMPEG_BUNDLED = resource_path("ffmpeg/ffmpeg.exe")
FFPROBE_BUNDLED = resource_path("ffmpeg/ffprobe.exe")


def ffmpeg_cmd() -> str:
    return FFMPEG_BUNDLED if os.path.exists(FFMPEG_BUNDLED) else "ffmpeg"


def ffprobe_cmd() -> str:
    return FFPROBE_BUNDLED if os.path.exists(FFPROBE_BUNDLED) else "ffprobe"


# --------------------- Projektordner ---------------------
BASE_DIR = app_dir()
DEFAULT_CLIPS_DIR = BASE_DIR / "clips"
INTRO_DIR = BASE_DIR / "intro"
OUTRO_DIR = BASE_DIR / "outro"
VORSPANN_DIR = BASE_DIR / "pre-intro"
PHOTOS_DIR = BASE_DIR / "photos"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache" / "normalized"

ANIMATIONS_DIR = DEFAULT_CLIPS_DIR / "animations"
for d in (DEFAULT_CLIPS_DIR, INTRO_DIR, OUTRO_DIR, VORSPANN_DIR, PHOTOS_DIR, OUTPUT_DIR, CACHE_DIR, ANIMATIONS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Persistente Segmente für den Timeline-Editor
SEGMENTS_DIR = BASE_DIR / "cache" / "segments"
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

# Timeline-Editor State
timeline_seg_meta:   list[dict]  = []
timeline_audio_path: Optional[str] = None
in_editor_mode:      bool        = False

# Drag & Drop
TLINE_THUMB_W    = 150
TLINE_THUMB_H    = 84
TLINE_COLS       = 5
TLINE_THUMB_CACHE: dict[str, "ctk.CTkImage"] = {}
_drag_source_idx:    Optional[int]      = None
_drag_highlight_idx: Optional[int]      = None
_timeline_order:     list[int]          = []
_tline_buttons:      list               = []
_tline_changed:      bool               = False


# --------------------- Stimmungen ---------------------
MOODS = ["Traurig", "Neutral", "Glücklich", "Animation"]
MOOD_DIR_MAP = {"Traurig": "traurig", "Neutral": "neutral", "Glücklich": "gluecklich", "Animation": "animations"}
for folder in MOOD_DIR_MAP.values():
    (DEFAULT_CLIPS_DIR / folder).mkdir(parents=True, exist_ok=True)


# --------------------- Settings ---------------------
FPS = 30

OUT_W_NORMAL, OUT_H_NORMAL = 1920, 1080
OUT_W_SHORTS, OUT_H_SHORTS = 1080, 1920
SHORTS_MAX_SEC = 58.0
SHORTS_FADE_IN_SEC = 0.6   # weiches Einblenden (Bild + Ton) beim Short
SHORTS_FADE_SEC = 2.0

CUT_SECONDS_GENTLE = 3.50
CUT_SECONDS_SLOW   = 2.20
CUT_SECONDS_NORMAL = 1.70
CUT_SECONDS_FAST   = 0.95

POOL_CHOICES = [10, 20, 30]
POOL_LABELS = [tr("Automatisch")] + [f"{x} Clips" for x in POOL_CHOICES]

VIDEO_OVERHANG_SEC = 0.08

MIN_PHOTO_SEC = 2.0
MAX_CONSEC_PHOTOS = 2

MIX_EXTERN_WEIGHT = 0.70

# Titel-Overlay Einstellungen
TITLE_FONT_SIZE = 52          # Schriftgröße (pt) — fett & größer
TITLE_FADE_IN_SEC = 1.2       # Fade-in Dauer
TITLE_DISPLAY_SEC = 5.0       # wie lange der Titel sichtbar bleibt
TITLE_MARGIN_X = 60           # Abstand vom linken Rand
TITLE_MARGIN_Y = 70           # Abstand vom unteren Rand


# --------------------- Utilities ---------------------
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# Formate: Schlüssel -> (Anzeigename, Dateiname-Zusatz)
FORMAT_ORDER = ["normal", "shorts", "shorts58"]
FORMAT_LABELS = {
    "normal": "16:9",
    "shorts": "9:16",
    "shorts58": "Short 58s",
}
FORMAT_SUFFIX = {
    "normal": "",
    "shorts": "_9x16",
    "shorts58": "_Short",
}


def get_output_path_from_music(music_path: str, fmt: str = "normal") -> Path:
    stem = Path(music_path).stem
    bad = '<>:"/\\|?*'
    for ch in bad:
        stem = stem.replace(ch, "_")
    stem = stem.rstrip(".").strip()
    if not stem:
        stem = "output"
    return OUTPUT_DIR / f"{stem}{FORMAT_SUFFIX.get(fmt, '')}.mp4"


def get_title_from_music(music_path: str) -> str:
    """Musikdateiname ohne Endung als Titel."""
    return Path(music_path).stem


def list_video_files(folder: Path) -> list[Path]:
    exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    if not folder.exists():
        return []
    return [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]


def list_image_files(folder: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    if not folder.exists():
        return []
    return [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]


def count_video_files(folder: Path) -> int:
    return len(list_video_files(folder))


def count_image_files(folder: Path) -> int:
    return len(list_image_files(folder))


def ffprobe_duration(path: Path) -> float:
    cmd = [
        ffprobe_cmd(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    out = check_output_hidden(cmd).decode("utf-8").strip()
    return float(out)


def ffprobe_json(path: Path) -> dict:
    cmd = [
        ffprobe_cmd(), "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path)
    ]
    out = check_output_hidden(cmd).decode("utf-8", errors="replace")
    import json
    return json.loads(out)


def has_audio_stream(path: Path) -> bool:
    try:
        info = ffprobe_json(path)
        streams = info.get("streams", [])
        return any(s.get("codec_type") == "audio" for s in streams)
    except Exception:
        return False


def detect_beats(audio_path: str) -> Tuple[list[float], float]:
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    dur = float(librosa.get_duration(y=y, sr=sr))

    if len(beat_times) < 8:
        if tempo <= 0:
            tempo = 120.0
        step = 60.0 / float(tempo)
        beat_times = np.arange(0, dur, step).tolist()

    if not beat_times or beat_times[0] > 0.01:
        beat_times = [0.0] + beat_times

    return beat_times, dur


def build_cut_points(beat_times: list[float], music_dur: float, target_seconds: float, speed: str = "") -> list[float]:
    cuts = [0.0]
    t = 0.0

    while t < music_dur:
        if speed == "random":
            step = random.uniform(CUT_SECONDS_FAST, CUT_SECONDS_GENTLE)
        else:
            step = max(0.25, float(target_seconds))
        t += step
        if t >= music_dur:
            break
        nearest = min(beat_times, key=lambda b: abs(b - t))
        if nearest - cuts[-1] >= 0.20:
            cuts.append(nearest)

    if cuts[-1] < music_dur:
        cuts.append(music_dur)

    return cuts


def _rational_to_float(s: str) -> Optional[float]:
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            a = float(a.strip())
            b = float(b.strip())
            if b == 0:
                return None
            return a / b
        return float(s)
    except Exception:
        return None


def needs_normalize(video_path: Path) -> bool:
    try:
        info = ffprobe_json(video_path)
        streams = info.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not v:
            return True

        codec = (v.get("codec_name") or "").lower()
        pix = (v.get("pix_fmt") or "").lower()
        avg = _rational_to_float(v.get("avg_frame_rate") or "")
        rfr = _rational_to_float(v.get("r_frame_rate") or "")

        if codec != "h264":
            return True
        if pix != "yuv420p":
            return True

        def is_30(x: Optional[float]) -> bool:
            if x is None:
                return False
            return abs(x - 30.0) < 0.25 or abs(x - 29.97) < 0.25

        if not is_30(avg) and not is_30(rfr):
            return True

        return False
    except Exception:
        return True


def ffmpeg_concat_escape(path_str: str) -> str:
    """Escaped einen Pfad für die FFmpeg concat-Demuxer-Liste (Apostrophe im Pfad)."""
    return path_str.replace("'", "'\\''")


def cache_key_for_file(p: Path) -> str:
    st = p.stat()
    raw = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8", errors="ignore")
    return hashlib.md5(raw).hexdigest()


def normalize_to_cache_video_only(original: Path, status_cb: Optional[Callable[[str], None]] = None) -> Path:
    key = cache_key_for_file(original)
    out = CACHE_DIR / f"{key}.mp4"
    if out.exists():
        return out

    if status_cb:
        status_cb(tr("Normalisiere: ") + original.name)

    vf = f"setpts=PTS-STARTPTS,fps={FPS},setsar=1"

    cmd = [
        ffmpeg_cmd(), "-y",
        "-hide_banner",
        "-fflags", "+genpts",
        "-i", str(original),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "20",
        "-preset", "veryfast",
        "-fps_mode", "cfr",
        str(out)
    ]
    run_hidden(cmd)
    return out


def build_vf_base(out_w: int, out_h: int, mode: str) -> str:
    if mode == "shorts":
        return (
            "setpts=PTS-STARTPTS,"
            f"fps={FPS},"
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},"
            "setsar=1"
        )
    return (
        "setpts=PTS-STARTPTS,"
        f"fps={FPS},"
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1"
    )


def compute_fade(music_dur: float) -> Tuple[float, float]:
    d = float(music_dur)
    fade_d = float(SHORTS_FADE_SEC)
    if d <= 3.0:
        fade_d = max(0.5, d * 0.25)
    fade_d = min(fade_d, max(0.25, d * 0.5))
    fade_start = max(0.0, d - fade_d)
    return fade_start, fade_d


def pick_no_repeat(items: list[Path], last: Optional[Path]) -> Path:
    if not items:
        raise RuntimeError(tr("Keine Medien zum Auswählen vorhanden."))
    if len(items) == 1:
        return items[0]
    x = random.choice(items)
    tries = 0
    while last is not None and x == last and tries < 10:
        x = random.choice(items)
        tries += 1
    return x


def clips_from_moods(moods: list[str]) -> list[Path]:
    files: list[Path] = []
    for m in moods:
        folder = DEFAULT_CLIPS_DIR / MOOD_DIR_MAP[m]
        files.extend(list_video_files(folder))
    uniq = list(dict.fromkeys([p.resolve() for p in files]).keys())
    return [Path(p) for p in uniq]


def shuffle_items_once(ext_vid: list[Path], ext_ph: list[Path]) -> list[Tuple[str, Path]]:
    items: list[Tuple[str, Path]] = [("video", p) for p in ext_vid] + [("photo", p) for p in ext_ph]
    random.shuffle(items)
    return items


def pick_item_no_repeat(
    remaining: list[Tuple[str, Path]],
    all_items: list[Tuple[str, Path]],
    last_item: Optional[Tuple[str, Path]],
    consec_photos: int,
    seg_len: float,
) -> Tuple[Tuple[str, Path], int]:
    videos_exist = any(k == "video" for k, _ in all_items)

    def ok(item: Tuple[str, Path]) -> bool:
        kind, _p = item
        if last_item is not None and item == last_item and len(all_items) > 1:
            return False
        if videos_exist:
            if kind == "photo" and consec_photos >= MAX_CONSEC_PHOTOS:
                return False
            if kind == "photo" and seg_len < MIN_PHOTO_SEC:
                return False
        return True

    if remaining:
        for _ in range(min(24, max(1, len(remaining)))):
            idx = random.randrange(len(remaining))
            cand = remaining[idx]
            if ok(cand):
                remaining.pop(idx)
                new_consec = consec_photos + 1 if cand[0] == "photo" else 0
                return cand, new_consec

    acceptable = [it for it in all_items if ok(it)]
    cand = random.choice(acceptable) if acceptable else random.choice(all_items)

    try:
        if cand in remaining:
            remaining.remove(cand)
    except Exception:
        pass

    new_consec = consec_photos + 1 if cand[0] == "photo" else 0
    return cand, new_consec


def open_help():
    """Kurzanleitung – aufgeräumt, ohne Buttons (PDF/Sprache über das ?-Menü)."""
    win = ctk.CTkToplevel(app)
    win.title(tr("Kurzanleitung"))
    win_w, win_h = 540, 620
    app.update_idletasks()
    x = app.winfo_x() + (app.winfo_width() - win_w) // 2
    y = app.winfo_y() + (app.winfo_height() - win_h) // 2
    win.geometry(f"{win_w}x{win_h}+{max(0, x)}+{max(0, y)}")
    win.resizable(False, False)
    win.transient(app)

    ctk.CTkLabel(win, text=tr("Kurzanleitung"),
                 font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(18, 8))
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=26, pady=(0, 16))
    for _title, _text in HELP_SECTIONS.get(LANG or "de", HELP_SECTIONS["de"]):
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(0, 9))
        ctk.CTkLabel(row, text=_title, font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w", justify="left").pack(fill="x")
        ctk.CTkLabel(row, text=_text, font=ctk.CTkFont(size=12), anchor="w", justify="left",
                     wraplength=480, text_color=("gray25", "gray78")).pack(fill="x")


def open_pdf_manual():
    """Öffnet die ausführliche PDF-Anleitung, falls neben der EXE vorhanden."""
    names = {"en": "MVG_Manual_EN.pdf", "es": "MVG_Manual_ES.pdf"}
    pdf = BASE_DIR / names.get(LANG, "MVG_Bedienungsanleitung.pdf")
    if not pdf.exists():
        pdf = BASE_DIR / "MVG_Bedienungsanleitung.pdf"
    if pdf.exists():
        open_path_portable(pdf)
    else:
        ui_status(tr("PDF-Anleitung nicht neben dem Programm gefunden"))


def open_path_portable(p: Path):
    try:
        if os.name == "nt":
            os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception:
        pass


def build_title_overlay_filter(
    title: str,
    out_w: int,
    out_h: int,
    textfile_path=None,   # nicht mehr verwendet, bleibt fuer Kompatibilitaet
    font_size: int = TITLE_FONT_SIZE,
    fade_in: float = TITLE_FADE_IN_SEC,
    display: float = TITLE_DISPLAY_SEC,
    margin_x: int = TITLE_MARGIN_X,
    margin_y: int = TITLE_MARGIN_Y,
    intro_offset: float = 0.0,
) -> str:
    """
    Titel-Overlay per drawtext.
    Sonderzeichen werden bereinigt damit FFmpeg keinen Fehler wirft.
    intro_offset = Laenge des Intros — Titel startet DANACH.
    """
    import re
    # Nur sichere Zeichen behalten: Buchstaben, Zahlen, Leerzeichen, - _
    safe = re.sub(r"[^\w \-]", " ", title).replace("_", " ").strip()
    # FFmpeg drawtext: Leerzeichen und Sonderzeichen escapen
    safe = safe.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")

    # Schriftgröße automatisch anpassen: je länger der Titel, desto kleiner
    # Basis: 52pt bei ~20 Zeichen, linear runter bis 24pt bei ~60 Zeichen
    char_count = len(safe)
    # Schätzung: jedes Zeichen braucht ca. font_size * 0.55 Pixel Breite
    max_text_width = out_w - margin_x - 40  # verfügbare Breite
    estimated_w = char_count * font_size * 0.55
    if estimated_w > max_text_width:
        font_size = max(24, int(max_text_width / (char_count * 0.55)))

    y_pos = out_h - margin_y - font_size
    t_rel = f"(t-{intro_offset:.2f})"

    alpha_expr = (
        f"if(lt(t,{intro_offset:.2f}),0,"
        f"if(lt({t_rel},{fade_in:.2f}),"
        f"{t_rel}/{fade_in:.2f},"
        f"if(lt({t_rel},{display:.2f}),1,0)))"
    )

    # Schrift: Arial Bold (auf jedem Windows vorhanden, alle Akzentzeichen), sonst Fallbacks
    font_part = ""
    win_dir = os.environ.get("WINDIR", "C:\\Windows")
    for fname in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf", "segoeui.ttf"):
        fpath = Path(win_dir) / "Fonts" / fname
        if fpath.exists():
            ff = fpath.as_posix().replace(":", "\\:")
            font_part = f":fontfile='{ff}'"
            break

    return (
        f"drawtext=text='{safe}'"
        f"{font_part}"
        f":fontsize={font_size}"
        f":fontcolor=white"
        f":alpha='{alpha_expr}'"
        f":x={margin_x}"
        f":y={y_pos}"
        f":shadowcolor=black@0.6"
        f":shadowx=2:shadowy=2"
    )


def choose_short_start(audio_path: str, duration: float, win: float = SHORTS_MAX_SEC) -> Tuple[float, str]:
    """
    Sucht den "stärksten" Abschnitt (wiederkehrende Harmonien + Energie) für den Short.
    Gibt (Startsekunde, Beschreibung) zurück. Bei unklarem Ergebnis: Start bei 0.
    Idee nach audio_selection.py des Shorts-Tools.
    """
    RATE, STEP = 11025, 0.5
    if duration <= win + 10:
        return 0.0, "Songanfang (Song kurz)"
    try:
        samples, _ = librosa.load(audio_path, sr=RATE, mono=True)
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size < RATE * (win + 10) or not np.isfinite(samples).all():
            return 0.0, "Songanfang"
        size, hop = 2048, 512
        frames = np.lib.stride_tricks.sliding_window_view(samples, size)[::hop]
        spectrum = np.abs(np.fft.rfft(frames * np.hanning(size), axis=1))
        freqs = np.fft.rfftfreq(size, 1 / RATE)
        valid = (freqs >= 80) & (freqs <= 4000)
        pitch = np.round(69 + 12 * np.log2(freqs[valid] / 440)).astype(int) % 12
        chroma = np.stack([spectrum[:, valid][:, pitch == k].sum(axis=1) for k in range(12)], axis=1)
        rms = np.sqrt((frames ** 2).mean(axis=1))
        if np.max(rms) < 0.001:
            return 0.0, "Songanfang"
        bins = (np.arange(len(frames)) * hop / RATE / STEP).astype(int)
        n = int(bins[-1]) + 1
        counts = np.maximum(np.bincount(bins, minlength=n), 1)
        feats = np.stack([np.bincount(bins, weights=chroma[:, k], minlength=n) / counts for k in range(12)], axis=1)
        energy = np.bincount(bins, weights=rms, minlength=n) / counts
        feats /= np.maximum(feats.sum(axis=1, keepdims=True), 1e-8)
        feats -= feats.mean(axis=0, keepdims=True)
        width = 16
        tpl = np.lib.stride_tricks.sliding_window_view(feats, width, axis=0)[::2].reshape(-1, 12 * width)
        tpl = tpl / np.maximum(np.linalg.norm(tpl, axis=1)[:, None], 1e-8)
        times = np.arange(len(tpl)) * 2 * STEP
        repeat = np.zeros(len(tpl))
        for off in range(0, len(tpl), 128):
            sim = tpl[off:off + 128] @ tpl.T
            sim[np.abs(times[off:off + 128, None] - times[None, :]) < 25] = -1
            repeat[off:off + 128] = np.maximum(0, sim.max(axis=1))
        if repeat.max(initial=0) < 0.8:
            return 0.0, "Songanfang (kein klares Muster)"
        cands = np.arange(5, max(6, int(duration - win) + 1), dtype=float)
        peak = np.percentile(energy, 90)
        scores = []
        for st in cands:
            e = energy[int(st / STEP):int((st + win) / STEP)]
            r = repeat[(times >= st) & (times <= st + win * 0.8)]
            if not len(e) or not len(r):
                scores.append(-1.0)
                continue
            act = np.mean(e > peak * 0.2)
            strength = min(1.0, np.mean(e) / max(peak, 1e-8))
            rec = np.mean(np.maximum(0, r - 0.65) / 0.35)
            scores.append((0.7 * rec + 0.3 * strength) * act)
        best = int(np.argmax(scores))
        if scores[best] < 0.35:
            return 0.0, "Songanfang (Analyse nicht eindeutig)"
        return float(min(max(0.0, cands[best]), duration - win)), "stärkster Abschnitt"
    except Exception:
        return 0.0, "Songanfang"


# --------------------- Render Engine ---------------------
def render_clip_segment(
    kind: str,
    src: Path,
    out_seg: Path,
    seg_len: float,
    vf: str,
    frames: Optional[int] = None,
) -> None:
    """
    Rendert EIN stummes Segment (Foto oder Video) im Zielformat.
    Wird vom Haupt-Render und vom Clip-Ersetzen im Editor gemeinsam genutzt.
    frames: optional exakte Bildanzahl (Ersatz-Segmente müssen bildgenau gleich lang sein).
    """
    frames_args = ["-frames:v", str(int(frames))] if frames else []

    if kind == "photo":
        cmd = [
            ffmpeg_cmd(), "-y", "-hide_banner",
            "-loop", "1",
            "-i", str(src),
            "-t", f"{seg_len:.4f}",
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-preset", "veryfast",
            "-r", str(FPS), "-fps_mode", "cfr",
        ] + frames_args + [str(out_seg)]
    else:
        clip_dur = ffprobe_duration(src)
        max_start = max(0.0, clip_dur - seg_len - 0.05)
        start = random.uniform(0.0, max_start) if max_start > 0 else 0.0

        # Clip loopen falls er kürzer als das Segment ist
        loop_flag = ["-stream_loop", "-1"] if clip_dur < seg_len + 0.1 else []
        cmd = [
            ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
        ] + loop_flag + [
            "-ss", f"{start:.4f}",
            "-i", str(src),
            "-t", f"{seg_len:.4f}",
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-preset", "veryfast",
            "-fps_mode", "cfr",
        ] + frames_args + [str(out_seg)]
    run_hidden(cmd)


def build_replacement_segment(
    old_seg: Path,
    old_src: Optional[str],
    used_srcs: set,
    pool: list,
    vf: str,
    out_seg: Path,
) -> dict:
    """
    Ersetzt ein Clip-Segment durch einen neuen Zufallsclip GLEICHER Länge (bildgenau),
    damit das Video weiterhin exakt so lang bleibt wie der Song.
    pool: Liste von {"kind": "video"|"photo", "path": str} — die Quellen des Renders.
    Gibt die neuen Meta-Felder zurück: path, label, src, kind.
    """
    old_dur = ffprobe_duration(old_seg)
    frames = max(1, int(round(old_dur * FPS)))
    seg_len = frames / FPS

    existing = [c for c in pool if Path(c["path"]).exists()]
    videos_exist = any(c["kind"] == "video" for c in existing)
    cands = [
        c for c in existing
        if c["path"] != old_src
        and not (c["kind"] == "photo" and videos_exist and seg_len < MIN_PHOTO_SEC)
    ]
    if not cands:
        raise RuntimeError(tr("Keine alternativen Clips verfügbar."))
    fresh = [c for c in cands if c["path"] not in used_srcs]
    choice = random.choice(fresh or cands)

    src = Path(choice["path"])
    if choice["kind"] == "photo":
        render_clip_segment("photo", src, out_seg, seg_len, vf, frames=frames)
    else:
        prepared = normalize_to_cache_video_only(src) if needs_normalize(src) else src
        render_clip_segment("video", prepared, out_seg, seg_len, vf, frames=frames)

    return {
        "path": str(out_seg),
        "label": src.stem,
        "src": choice["path"],
        "kind": choice["kind"],
    }


def render_video(
    audio_path: str,
    clips_folder: Path,
    photos_folder: Path,
    out_path: Path,
    use_intro: bool,
    use_outro: bool,
    use_vorspann: bool,
    speed: str,
    pool_value: int,
    pool_on: bool,
    external_clips_on: bool,
    external_photos_on: bool,
    is_shorts: bool,          # True = 9:16 Format
    shorts_limited: bool,     # True = zusätzlich auf 58s begrenzen
    moods_selected: Optional[list[str]] = None,
    title_overlay: bool = False,
    title_text: str = "",
    status_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
    persist_segments: bool = True,
) -> Tuple[int, int, int, list[dict], dict]:

    # Der 58s-Short ist immer "pur": ohne Pre-Intro, Intro und Outro.
    # (Das Outro würde sonst hinten angehängt und die 58 Sekunden sprengen,
    #  ein Intro würde einen großen Teil der Zeit verbrauchen.)
    if shorts_limited:
        use_intro = False
        use_outro = False
        use_vorspann = False
        title_overlay = False   # kein Titel im 58s-Short

    ext_vid: list[Path] = list_video_files(clips_folder) if external_clips_on else []
    ext_ph: list[Path] = list_image_files(photos_folder) if external_photos_on else []

    internal_vid_all: list[Path] = []
    if pool_on:
        moods = moods_selected or list(MOODS)
        internal_vid_all = clips_from_moods(moods)
        # Manuelle Auswahl filtern falls gesetzt
        if manual_clip_selection is not None and len(manual_clip_selection) > 0:
            sel_resolved = {p.resolve() for p in manual_clip_selection}
            internal_vid_all = [p for p in internal_vid_all if p.resolve() in sel_resolved]
            if not internal_vid_all:
                internal_vid_all = clips_from_moods(moods)  # Fallback: alle

    pool_vid: list[Path] = []
    if pool_on and internal_vid_all:
        if pool_value >= 10**8:
            pool_vid = list(internal_vid_all)
        else:
            n = min(max(1, int(pool_value)), len(internal_vid_all))
            pool_vid = random.sample(list(internal_vid_all), n) if len(internal_vid_all) > n else list(internal_vid_all)

    extern_enabled = bool(ext_vid) or bool(ext_ph)
    pool_enabled = bool(pool_on and pool_vid)

    if extern_enabled and not pool_enabled:
        source_mode = "extern_only"
    elif pool_enabled and not extern_enabled:
        source_mode = "intern_only"
    elif extern_enabled and pool_enabled:
        source_mode = "mix"
    else:
        raise RuntimeError(tr("Keine Medien gefunden. (Extern/Intern prüfen)"))

    if status_cb:
        status_cb(tr("Analysiere Musik…"))
    if progress_cb:
        progress_cb(0.02)

    beat_times, music_dur_full = detect_beats(audio_path)
    music_dur = min(music_dur_full, SHORTS_MAX_SEC) if shorts_limited else music_dur_full
    music_start = 0.0
    if shorts_limited and music_dur_full > SHORTS_MAX_SEC + 10:
        if status_cb:
            status_cb(tr("Suche stärksten Abschnitt für den Short…"))
        music_start, _why = choose_short_start(audio_path, music_dur_full)
        beat_times = [t - music_start for t in beat_times
                      if music_start <= t < music_start + music_dur]
        if status_cb:
            status_cb(tr("Short: ab {t}").format(t=f"{int(music_start)//60}:{int(music_start)%60:02d}"))

    if speed == "gentle":
        target_seconds = CUT_SECONDS_GENTLE
    elif speed == "slow":
        target_seconds = CUT_SECONDS_SLOW
    elif speed == "normal":
        target_seconds = CUT_SECONDS_NORMAL
    elif speed == "random":
        target_seconds = random.uniform(CUT_SECONDS_FAST, CUT_SECONDS_GENTLE)  # wird pro Schnitt neu gewuerfelt
    else:
        target_seconds = CUT_SECONDS_FAST
    if is_shorts and speed in ("gentle", "slow"):
        target_seconds = min(target_seconds, 1.60)

    cut_points = build_cut_points(beat_times, music_dur, target_seconds, speed=speed)

    out_w = OUT_W_SHORTS if is_shorts else OUT_W_NORMAL
    out_h = OUT_H_SHORTS if is_shorts else OUT_H_NORMAL

    # Intro
    intro_src: Optional[Path] = None
    intro_len = 0.0
    if use_intro:
        intro_candidates = list_video_files(INTRO_DIR)
        if intro_candidates:
            intro_src = random.choice(intro_candidates)   # random statt immer [0]
            intro_len = min(ffprobe_duration(intro_src), music_dur)
            if intro_len < 0.20:
                intro_src = None
                intro_len = 0.0

    # Outro
    outro_src: Optional[Path] = None
    if use_outro:
        outro_candidates = list_video_files(OUTRO_DIR)
        if outro_candidates:
            outro_src = random.choice(outro_candidates)   # random statt immer [0]

    # Vorspann — komplett unabhängig von Musik/Intro/Cuts, läuft VOR allem anderen
    # mit eigenem Ton. Beeinflusst weder start_offset noch cut_points.
    vorspann_src: Optional[Path] = None
    if use_vorspann:
        vorspann_candidates = list_video_files(VORSPANN_DIR)
        if vorspann_candidates:
            vorspann_src = random.choice(vorspann_candidates)

    if progress_cb:
        progress_cb(0.05)

    temp_dir = Path(tempfile.mkdtemp(prefix="beatcut_"))

    # Segmente persistent speichern für Timeline-Editor (nur beim Hauptvideo).
    # Zusatzformate (z.B. der Short) rendern in einen temporären Ordner, damit sie
    # die Segmente des Hauptvideos nicht überschreiben.
    if persist_segments:
        for f in SEGMENTS_DIR.glob("seg*.mp4"):
            try: f.unlink()
            except Exception: pass
        seg_dir = SEGMENTS_DIR
    else:
        seg_dir = temp_dir / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)

    prepared_video_only: dict[Path, Path] = {}

    def prepared_path_video_only(p: Path) -> Path:
        if p in prepared_video_only:
            return prepared_video_only[p]
        pp = normalize_to_cache_video_only(p, status_cb=status_cb) if needs_normalize(p) else p
        prepared_video_only[p] = pp
        return pp

    segments_files: list[Path] = []
    seg_durations: list[float] = []
    seg_labels: list[str] = []
    seg_src_info: list[Optional[dict]] = []   # pro Segment: {"kind","src"} oder None (Intro/Outro/Pre-Intro)
    used_videos: set[Path] = set()
    used_photos: set[Path] = set()

    all_external = shuffle_items_once(ext_vid, ext_ph) if extern_enabled else []
    remaining_external = all_external[:]

    def refill_external():
        nonlocal all_external, remaining_external
        all_external = shuffle_items_once(ext_vid, ext_ph)
        remaining_external = all_external[:]

    last_item: Optional[Tuple[str, Path]] = None
    last_vid: Optional[Path] = None
    consec_photos = 0

    try:
        seg_index = 1

        # Vorspann — läuft ganz am Anfang, VOR dem Intro. Segment wird stumm gerendert
        # (analog zum Outro), der eigene Ton wird separat beim finalen Mux vor die Musik gelegt.
        vorspann_len = 0.0
        if vorspann_src is not None and vorspann_src.exists():
            if status_cb:
                status_cb(tr("Rendere Pre-Intro…"))
            src_vorspann = prepared_path_video_only(vorspann_src)
            out_seg = seg_dir / f"seg{seg_index:04d}.mp4"
            seg_index += 1

            vf_vorspann = build_vf_base(out_w, out_h, "shorts" if is_shorts else "normal")

            cmd_vorspann = [
                ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                "-i", str(src_vorspann),
                "-vf", vf_vorspann,
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "18", "-preset", "veryfast",
                "-fps_mode", "cfr",
                str(out_seg)
            ]
            run_hidden(cmd_vorspann)
            # WICHTIG: Länge aus dem tatsächlich gerenderten Segment messen, nicht aus der
            # Originalquelle — die CFR-Konvertierung kann die Länge um einzelne Frames
            # verschieben. Sonst laufen Musik-Delay (adelay) und Video-Segmentlänge auseinander.
            vorspann_len = ffprobe_duration(out_seg)
            segments_files.append(out_seg)
            seg_durations.append(float(vorspann_len))
            seg_labels.append(f"Vorspann: {vorspann_src.stem}")
            seg_src_info.append(None)

        # Intro
        if intro_src is not None and intro_len > 0.0:
            if status_cb:
                status_cb(tr("Rendere Intro…"))
            src_intro = prepared_path_video_only(intro_src)
            out_seg = seg_dir / f"seg{seg_index:04d}.mp4"
            seg_index += 1

            vf_intro = build_vf_base(out_w, out_h, "shorts" if is_shorts else "normal")

            cmd_intro = [
                ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                "-i", str(src_intro),
                "-t", f"{intro_len:.4f}",
                "-vf", vf_intro,
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "18", "-preset", "veryfast",
                "-fps_mode", "cfr",
                str(out_seg)
            ]
            run_hidden(cmd_intro)
            # WICHTIG: Länge aus dem tatsächlich gerenderten Segment messen, nicht aus dem
            # angeforderten -t Wert — die CFR-Konvertierung rundet auf ganze Frames, dadurch
            # kann das Segment länger/kürzer als intro_len werden. Sonst wird das Gesamtvideo
            # länger als die Musik (Cut-Punkte basieren auf dem alten, falschen intro_len).
            intro_len = ffprobe_duration(out_seg)
            segments_files.append(out_seg)
            seg_durations.append(float(intro_len))
            seg_labels.append(f"Intro: {intro_src.stem}")
            seg_src_info.append(None)

        # Cut-Punkte für die Musik-Clips werden ERST JETZT berechnet — mit der tatsächlich
        # gerenderten Intro-Länge, nicht der angeforderten. So bleibt die Gesamtlänge korrekt.
        start_offset = intro_len
        cut_points2: list[float] = []
        if start_offset < music_dur - 0.05:
            # Das erste Clip-Segment nach dem Intro darf nicht auffällig kurz sein. Ohne diese
            # Prüfung fällt der nächste Beat-Cut-Punkt oft zufällig knapp hinter start_offset
            # (z.B. nur 0.3–1.3s entfernt), was wie ein Fehler wirkt. Deshalb: alle Cut-Punkte
            # verwerfen, die näher als eine normale Segmentlänge an start_offset liegen.
            min_first_seg = max(0.20, float(target_seconds) * 0.75)
            cut_points2 = [t for t in cut_points if t >= start_offset + min_first_seg]
            cut_points2 = [start_offset] + cut_points2
            if cut_points2[-1] < music_dur:
                cut_points2.append(music_dur)

        cuts_total = (1 if intro_src else 0) + max(0, len(cut_points2) - 1)

        if status_cb:
            fmt = "Shorts 9:16 · 58s" if shorts_limited else ("Shorts 9:16" if is_shorts else "Normal 16:9")
            src_txt = f"Extern(V:{len(ext_vid)}|F:{len(ext_ph)}) Intern({len(pool_vid)}) Mode:{source_mode}"
            status_cb(f"{fmt} | {src_txt} | Cuts: {cuts_total}")

        if status_cb:
            status_cb(tr("Rendere Segmente…"))

        total_cuts = max(0, len(cut_points2) - 1)
        if source_mode in ("extern_only", "mix") and extern_enabled and not remaining_external:
            refill_external()

        for i in range(total_cuts):
            seg_len = cut_points2[i + 1] - cut_points2[i]
            if seg_len <= 0.05:
                continue

            is_last = (i == total_cuts - 1)
            seg_len_out = seg_len + (VIDEO_OVERHANG_SEC if is_last and (not is_shorts) else 0.0)

            out_seg = seg_dir / f"seg{seg_index:04d}.mp4"
            seg_index += 1

            vf = build_vf_base(out_w, out_h, "shorts" if is_shorts else "normal")

            chosen_kind: str
            chosen_path: Path

            if source_mode == "extern_only":
                if not remaining_external:
                    refill_external()
                (kind, path), consec_photos = pick_item_no_repeat(
                    remaining_external, all_external, last_item, consec_photos, seg_len_out
                )
                chosen_kind, chosen_path = kind, path
                last_item = (chosen_kind, chosen_path)

            elif source_mode == "intern_only":
                chosen_kind, chosen_path = "int", pick_no_repeat(pool_vid, last_vid)
                last_vid = chosen_path
                consec_photos = 0
                last_item = (chosen_kind, chosen_path)

            else:
                pick_extern = random.random() < MIX_EXTERN_WEIGHT
                if pick_extern:
                    if not remaining_external:
                        refill_external()
                    (kind, path), consec_photos = pick_item_no_repeat(
                        remaining_external, all_external,
                        last_item if (last_item and last_item[0] != "int") else None,
                        consec_photos, seg_len_out
                    )
                    chosen_kind, chosen_path = kind, path
                    last_item = (chosen_kind, chosen_path)
                else:
                    chosen_kind, chosen_path = "int", pick_no_repeat(pool_vid, last_vid)
                    last_vid = chosen_path
                    consec_photos = 0
                    last_item = (chosen_kind, chosen_path)

            # Segment rendern (gemeinsame Funktion, auch für Clip-Ersetzen im Editor)
            if chosen_kind == "photo":
                img = chosen_path
                used_photos.add(img)
                render_clip_segment("photo", img, out_seg, seg_len_out, vf)
                seg_labels.append(img.stem)
                seg_src_info.append({"kind": "photo", "src": str(img)})
            else:
                clip = chosen_path
                last_vid = clip
                used_videos.add(clip)
                src = prepared_path_video_only(clip)
                render_clip_segment("video", src, out_seg, seg_len_out, vf)
                seg_labels.append(clip.stem)
                seg_src_info.append({"kind": "video", "src": str(clip)})

            segments_files.append(out_seg)
            seg_durations.append(float(seg_len_out))

            if progress_cb and total_cuts > 0:
                progress_cb(0.05 + 0.80 * ((i + 1) / total_cuts))

        # ----- Outro als Segment speichern -----
        # WICHTIG: Segment wird OHNE Ton gerendert (wie alle anderen Segmente),
        # damit der spätere concat mit "-c copy" ein einheitliches Stream-Layout hat.
        # Der Outro-Ton selbst wird separat beim finalen Audio-Mux aus outro_src gemischt.
        if outro_src is not None and outro_src.exists():
            if status_cb:
                status_cb(tr("Hänge Outro an…"))

            outro_seg = seg_dir / f"seg{seg_index:04d}.mp4"
            seg_index += 1
            vf_outro = build_vf_base(out_w, out_h, "shorts" if is_shorts else "normal")

            cmd_outro_prep = [
                ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                "-i", str(outro_src),
                "-vf", vf_outro,
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "18", "-preset", "veryfast",
                "-fps_mode", "cfr",
                str(outro_seg)
            ]
            run_hidden(cmd_outro_prep)
            segments_files.append(outro_seg)
            seg_durations.append(ffprobe_duration(outro_seg))
            seg_labels.append(f"Outro: {outro_src.stem}")
            seg_src_info.append(None)

        if progress_cb:
            progress_cb(0.88)
        if status_cb:
            status_cb(tr("Klebe Segmente…"))

        # Zusammensetzen via concat
        video_only = temp_dir / "video_only.mp4"
        concat_list = temp_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for s in segments_files:
                f.write(f"file '{ffmpeg_concat_escape(s.as_posix())}'\n")

        cmd_concat = [
            ffmpeg_cmd(), "-y", "-hide_banner",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(video_only)
        ]
        run_hidden(cmd_concat)

        if status_cb:
            status_cb(tr("Muxe Audio…"))
        if progress_cb:
            progress_cb(0.92)

        if shorts_limited:
            fade_start, fade_d = compute_fade(music_dur)
            a_chain = (
                f"atrim={music_start:.4f}:{music_start + music_dur:.4f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={SHORTS_FADE_IN_SEC:.2f},"
                f"afade=t=out:st={fade_start:.4f}:d={fade_d:.4f}"
            )
        else:
            a_chain = f"atrim=0:{music_dur:.4f},asetpts=PTS-STARTPTS"

        # Vorspann-Audio: läuft VOR der Musik. Die Musik wird um vorspann_len verzögert.
        vorspann_audio_path = None
        music_delay_ms = 0
        if vorspann_src is not None and vorspann_src.exists() and has_audio_stream(vorspann_src):
            vorspann_audio_path = vorspann_src
            music_delay_ms = round(vorspann_len * 1000)

        # Outro-Audio: nur wenn Outro am Ende liegt und eigenen Ton hat.
        # Es wird um (Pre-Intro-Länge + Musiklänge) verzögert, damit es NACH der Musik startet —
        # nicht nur um music_dur, sonst stimmt der Zeitpunkt nicht wenn ein Pre-Intro aktiv ist.
        outro_audio_path = None
        outro_delay_ms = 0
        if outro_src is not None and outro_src.exists() and has_audio_stream(outro_src):
            outro_audio_path = outro_src
            outro_delay_ms = music_delay_ms + round(music_dur * 1000)

        # ----- Filter-Complex dynamisch zusammenbauen -----
        # Inputs: 0=video_only, 1=musik, [2]=outro (falls vorhanden), [2 oder 3]=vorspann
        inputs = [str(video_only), str(audio_path)]
        next_idx = 2
        outro_idx = None
        vorspann_idx = None
        if outro_audio_path is not None:
            inputs.append(str(outro_audio_path))
            outro_idx = next_idx
            next_idx += 1
        if vorspann_audio_path is not None:
            inputs.append(str(vorspann_audio_path))
            vorspann_idx = next_idx
            next_idx += 1

        # Musik-Kette: ggf. um Vorspann-Länge verzögert
        if music_delay_ms > 0:
            mus_chain = f"[1:a]{a_chain},adelay={music_delay_ms}|{music_delay_ms}[mus]"
        else:
            mus_chain = f"[1:a]{a_chain}[mus]"

        audio_parts = [mus_chain]
        mix_labels = ["mus"]
        if outro_idx is not None:
            audio_parts.append(f"[{outro_idx}:a]adelay={outro_delay_ms}|{outro_delay_ms}[outro]")
            mix_labels.append("outro")
        if vorspann_idx is not None:
            audio_parts.append(f"[{vorspann_idx}:a]anull[vorspann]")
            mix_labels.append("vorspann")

        if len(mix_labels) > 1:
            mix_inputs = "".join(f"[{lbl}]" for lbl in mix_labels)
            audio_parts.append(f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[aout]")
        else:
            # Einfacher Fall: nur Musik, kein Outro/Vorspann-Ton -> [mus] direkt als [aout]
            audio_parts = [mus_chain.replace("[mus]", "[aout]")]

        a_filter = ";".join(p for p in audio_parts if p)

        use_title = bool(title_overlay and title_text)
        if use_title:
            txt_filter = build_title_overlay_filter(
                title_text, out_w, out_h, intro_offset=intro_len
            )
            filter_complex = f"[0:v]{txt_filter}[vt];{a_filter}"
            video_map = "[vt]"
            vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "18", "-preset", "veryfast", "-fps_mode", "cfr"]
        elif shorts_limited:
            # Short = Ausschnitt aus der Songmitte -> weiches Ein- und Ausblenden (Bild)
            _fs, _fd = compute_fade(music_dur)
            filter_complex = (
                f"[0:v]fade=t=in:st=0:d={SHORTS_FADE_IN_SEC:.2f},"
                f"fade=t=out:st={_fs:.4f}:d={_fd:.4f}[vt];{a_filter}"
            )
            video_map = "[vt]"
            vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "18", "-preset", "veryfast", "-fps_mode", "cfr"]
        else:
            filter_complex = a_filter
            video_map = "0:v:0"
            vcodec_args = ["-c:v", "copy"]

        cmd_mux = [ffmpeg_cmd(), "-y", "-hide_banner"]
        for inp in inputs:
            cmd_mux += ["-i", inp]
        cmd_mux += ["-filter_complex", filter_complex, "-map", video_map, "-map", "[aout]"]
        cmd_mux += vcodec_args
        cmd_mux += ["-c:a", "aac", "-b:a", "192k", str(out_path)]
        run_hidden(cmd_mux)

        if progress_cb:
            progress_cb(1.0)
        if status_cb:
            status_cb(tr("Fertig: ") + out_path.name)

        seg_meta = []
        for i, p in enumerate(segments_files):
            info = seg_src_info[i] if i < len(seg_src_info) else None
            entry = {
                "path":  str(p),
                "label": seg_labels[i] if i < len(seg_labels) else p.stem,
            }
            if info:                      # nur normale Clips sind ersetzbar
                entry["kind"] = info["kind"]
                entry["src"] = info["src"]
            seg_meta.append(entry)

        # Quellen-Pool für "Clip ersetzen" im Editor (Duplikate entfernt, Reihenfolge stabil)
        replace_pool: list[dict] = []
        _seen: set[str] = set()
        for _p in list(ext_vid) + list(pool_vid):
            if str(_p) not in _seen:
                _seen.add(str(_p)); replace_pool.append({"kind": "video", "path": str(_p)})
        for _p in ext_ph:
            if str(_p) not in _seen:
                _seen.add(str(_p)); replace_pool.append({"kind": "photo", "path": str(_p)})
        # Titel-Info + Outro/Vorspann-Quelle mitgeben, damit Re-Concat alles wiederholen kann
        render_info = {
            "title_overlay": title_overlay,
            "title_text":    title_text,
            "intro_len":     intro_len,
            "out_w":         out_w,
            "out_h":         out_h,
            "shorts_limited":shorts_limited,
            "music_dur":     music_dur,
            "music_start":   music_start,
            "outro_src":     str(outro_src) if outro_src is not None else None,
            "vorspann_src":  str(vorspann_src) if vorspann_src is not None else None,
            "vorspann_len":  vorspann_len,
            "is_shorts":     is_shorts,
            "replace_pool":  replace_pool,
        }
        return (len(used_videos), len(used_photos), cuts_total, seg_meta, render_info)

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


# --------------------- GUI ---------------------
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

def open_language_dialog(first_start: bool = False):
    """Sprachauswahl. Beim ersten Start vor dem Hauptfenster, sonst aus der Hilfe."""
    global LANG
    if first_start:
        root = ctk.CTk()
        root.title("Language / Sprache / Idioma")
    else:
        root = ctk.CTkToplevel(app)
        root.title("Language / Sprache / Idioma")
        root.grab_set()
    _w, _h = 360, 250
    try:
        root.update_idletasks()
        if first_start:
            _x = (root.winfo_screenwidth() - _w) // 2
            _y = (root.winfo_screenheight() - _h) // 2
        else:
            _x = app.winfo_x() + (app.winfo_width() - _w) // 2
            _y = app.winfo_y() + (app.winfo_height() - _h) // 2
        root.geometry(f"{_w}x{_h}+{max(0, _x)}+{max(0, _y)}")
    except Exception:
        root.geometry(f"{_w}x{_h}")
    root.resizable(False, False)
    chosen = {"v": LANG or ""}

    ctk.CTkLabel(root, text="Language · Sprache · Idioma",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 12))

    def pick(code):
        global LANG
        chosen["v"] = code
        save_settings({"lang": code})
        if first_start:
            LANG = code
            root.destroy()
        else:
            root.destroy()
            messagebox.showinfo(
                "Language / Sprache / Idioma",
                "Bitte das Programm neu starten.\nPlease restart the program.\nReinicia el programa."
            )

    for code, name in LANGS.items():
        ctk.CTkButton(root, text=name, width=200, height=36, command=lambda c=code: pick(c)).pack(pady=5)

    if first_start:
        def on_close():
            if not chosen["v"]:
                pick("de")
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()



if not LANG:
    open_language_dialog(first_start=True)
    if not LANG:
        LANG = 'de'

app = ctk.CTk()
app.title(tr("Musik Video Generator  v2.9"))
app.geometry("960x720")
app.minsize(960, 720)

# Fenster zentrieren
app.update_idletasks()
sw = app.winfo_screenwidth()
sh = app.winfo_screenheight()
x = (sw - 960) // 2
y = (sh - 720) // 2
app.geometry(f"960x720+{x}+{y}")

music_file: Optional[str] = None
last_output_path: Optional[Path] = None

music_var = ctk.StringVar(value=tr("(keine Musik gewählt)"))
clips_var = ctk.StringVar(value=str(DEFAULT_CLIPS_DIR))
photos_var = ctk.StringVar(value=str(PHOTOS_DIR))

# Formate sind mehrfach wählbar. Standard: normales Video + 58s-Short.
FORMAT_DEFAULTS = {"normal": True, "shorts": False, "shorts58": True}
format_vars = {k: ctk.BooleanVar(value=v) for k, v in FORMAT_DEFAULTS.items()}
speed_var = ctk.StringVar(value="normal")

intro_var = ctk.BooleanVar(value=True)
outro_var = ctk.BooleanVar(value=True)    # ← default ON
vorspann_var = ctk.BooleanVar(value=False)  # ← default AUS
title_var = ctk.BooleanVar(value=False)  # default AUS

pool_on_var = ctk.BooleanVar(value=False)   # default AUS
external_var = ctk.BooleanVar(value=False)
photos_mode_var = ctk.BooleanVar(value=False)

pool_value_var = ctk.StringVar(value=tr("Automatisch"))
pool_hint_var = ctk.StringVar(value="Pool: 10 / 20 / 30 / ALLE")

status_var = ctk.StringVar(value=tr("bereit"))
out_name_var = ctk.StringVar(value=tr("Output: (Musik auswählen)"))
count_var = ctk.StringVar(value="0")

clips_path_status_var = ctk.StringVar(value="")
photos_path_status_var = ctk.StringVar(value="")

mood_vars = {m: ctk.BooleanVar(value=(m == "Neutral")) for m in MOODS}  # Neutral default AN, Animation AUS
manual_clip_selection: Optional[list[Path]] = None  # None = alle verwenden
manual_clip_moods: Optional[list[str]] = None  # Stimmung bei der letzten Auswahl
BTN_DEFAULT_FG = None


def ui_status(text: str):
    status_var.set(text)
    try:
        status_box.configure(state="normal")
        status_box.delete("1.0", "end")
        status_box.insert("1.0", text)
        status_box.configure(state="disabled")
    except Exception:
        pass  # status_box noch nicht initialisiert


def ui_progress(val: float):
    try:
        if in_editor_mode:
            outer._progressbar_ed.set(clamp(float(val), 0.0, 1.0))  # type: ignore[attr-defined]
        else:
            progressbar.set(clamp(float(val), 0.0, 1.0))
    except Exception:
        pass


def selected_formats() -> list[str]:
    """Gewählte Formate in fester Reihenfolge (normal, shorts, shorts58)."""
    return [k for k in FORMAT_ORDER if bool(format_vars[k].get())]


def selected_moods() -> list[str]:
    chosen = [m for m, v in mood_vars.items() if bool(v.get())]
    return chosen if chosen else list(MOODS)


def parse_pool_value() -> int:
    v = str(pool_value_var.get()).strip().upper()
    if v == "ALLE":
        return 10**9
    if v in ("AUTOMATISCH", tr("Automatisch").upper()):
        return 10**9
    try:
        return int(v.split()[0])
    except Exception:
        return 20


def get_counts() -> tuple[int, int]:
    photos_folder = Path(photos_var.get())
    clips_folder = Path(clips_var.get())

    v_ext = count_video_files(clips_folder) if bool(external_var.get()) and clips_folder.exists() else 0
    p_ext = count_image_files(photos_folder) if bool(photos_mode_var.get()) and photos_folder.exists() else 0
    v_int = len(clips_from_moods(selected_moods())) if bool(pool_on_var.get()) else 0

    if (bool(external_var.get()) or bool(photos_mode_var.get())) and bool(pool_on_var.get()):
        return (v_ext + v_int, p_ext)
    if bool(external_var.get()) or bool(photos_mode_var.get()):
        return (v_ext, p_ext)
    return (v_int, 0)


def refresh_count():
    v, p = get_counts()
    if bool(photos_mode_var.get()):
        count_var.set(tr("Clips: {v} | Fotos: {p}").format(v=v, p=p))
    else:
        count_var.set(str(v))


def update_output_label():
    if music_file:
        names = [get_output_path_from_music(music_file, k).name for k in selected_formats()]
        out_name_var.set(tr("Output: ") + "  +  ".join(names))
    else:
        out_name_var.set(tr("Output: (Musik auswählen)"))


def update_source_status():
    ext_on = bool(external_var.get()) or bool(photos_mode_var.get())
    int_on = bool(pool_on_var.get())

    if ext_on and not int_on:
        ui_status(tr("Quelle: NUR EXTERN"))
    elif int_on and not ext_on:
        ui_status(tr("Quelle: NUR INTERN"))
    elif int_on and ext_on:
        ui_status(tr("Quelle: MIX (Extern bevorzugt)"))
    else:
        ui_status(tr("Quelle: (nichts gewählt)"))


def update_path_status_lines():
    if bool(external_var.get()):
        clips_path_status_var.set(tr("Clips: {path}").format(path=clips_var.get()))
    else:
        clips_path_status_var.set(tr("Clips (intern): {path}").format(path=DEFAULT_CLIPS_DIR))

    if bool(photos_mode_var.get()):
        photos_path_status_var.set(tr("Fotos: {path}").format(path=photos_var.get()))
    else:
        photos_path_status_var.set("")


def set_pool_controls_state():
    if not bool(pool_on_var.get()):
        pool_hint_var.set(tr("Pool: AUS"))
        opt_pool.configure(state="disabled")
        try:
            btn_manual.configure(state="disabled")
        except Exception:
            pass
    else:
        pool_hint_var.set(tr("Pool: alle / 10 / 20 / 30"))
        opt_pool.configure(state="normal")
        try:
            btn_manual.configure(state="normal")
        except Exception:
            pass


def set_mood_controls_state():
    enabled = bool(pool_on_var.get())
    state = "normal" if enabled else "disabled"
    cb_tr.configure(state=state)
    cb_neu.configure(state=state)
    cb_glu.configure(state=state)
    try:
        cb_ani.configure(state=state)
    except Exception:
        pass


def update_active_button_colors():
    global BTN_DEFAULT_FG
    if BTN_DEFAULT_FG is None:
        return
    active = "#1f6aa5"
    is_std = (not bool(external_var.get()) and not bool(photos_mode_var.get()))
    btn_external.configure(fg_color=active if bool(external_var.get()) else BTN_DEFAULT_FG)
    btn_photos.configure(fg_color=active if bool(photos_mode_var.get()) else BTN_DEFAULT_FG)


# --------------------- Handlers ---------------------
def _refresh_all():
    refresh_count()
    set_pool_controls_state()
    set_mood_controls_state()
    update_active_button_colors()
    update_output_label()
    update_source_status()
    update_path_status_lines()


def on_pool_toggle():
    _refresh_all()


def on_moods_changed():
    refresh_count()
    chosen = [m for m, v in mood_vars.items() if bool(v.get())]
    ui_status(tr("Stimmung: alle") if not chosen else tr("Stimmung: ") + " + ".join(tr(c) for c in chosen))


def choose_music():
    global music_file
    selected = filedialog.askopenfilename(title=tr("Musik auswählen"), filetypes=[("Audio", "*.mp3 *.wav")])
    if selected:
        music_file = selected
        music_var.set(music_file)
        update_output_label()
        ui_status(tr("bereit"))


def choose_external_clips_folder():
    external_var.set(True)
    on_external_toggle()
    selected = filedialog.askdirectory(title=tr("Externer Clip-Ordner"))
    if selected:
        clips_var.set(str(Path(selected)))
        refresh_count()
        update_path_status_lines()
        ui_status(tr("Externer Clip-Ordner gesetzt"))
    else:
        external_var.set(False)
        on_external_toggle()


def choose_photos_folder():
    photos_mode_var.set(True)
    on_photos_toggle()
    selected = filedialog.askdirectory(title=tr("Foto-Ordner"))
    if selected:
        photos_var.set(str(Path(selected)))
        refresh_count()
        update_path_status_lines()
        ui_status(tr("Foto-Ordner gesetzt"))
    else:
        photos_mode_var.set(False)
        on_photos_toggle()


# --------------------- Short per URL (YouTube-Link aus der Zwischenablage) ---------------------
URL_SHORT_SEC = 45
URL_SHORT_FILTER = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")
_url_short_busy = False


def _ensure_deno() -> Optional[Path]:
    """yt-dlp braucht für YouTube eine JS-Laufzeit (Deno). Einmalig automatisch laden."""
    tools = BASE_DIR / "tools"
    target = tools / "deno.exe"
    if target.exists() or os.name != "nt":
        return target if target.exists() else None
    import io, zipfile, urllib.request
    tools.mkdir(exist_ok=True)
    url = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
    import ssl
    data = None
    contexts = [None]                      # 1) Windows-Standard
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))   # 2) certifi
    except Exception:
        pass
    for ctx in contexts:
        try:
            with urllib.request.urlopen(url, timeout=120, context=ctx) as resp:
                data = resp.read()
            break
        except Exception:
            data = None
    if data is None:                       # 3) curl.exe von Windows (nutzt den Windows-Zertifikatsspeicher)
        tmp_zip = tools / "deno_download.zip"
        try:
            subprocess.run(["curl.exe", "-L", "--fail", "-s", "-o", str(tmp_zip), url],
                           check=True, timeout=180, creationflags=0x08000000)
            data = tmp_zip.read_bytes()
        finally:
            try:
                tmp_zip.unlink()
            except Exception:
                pass
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        target.write_bytes(zf.read("deno.exe"))
    return target


def render_url_short(source: Path, target: Path, start: float, update: Callable[[str], None]) -> None:
    """45 s im 9:16-Format mit Originalton aus der Quelldatei."""
    fs, fd = compute_fade(URL_SHORT_SEC)
    fin = SHORTS_FADE_IN_SEC
    vf = (f"{URL_SHORT_FILTER},fade=t=in:st=0:d={fin:.2f},"
          f"fade=t=out:st={fs:.4f}:d={fd:.4f}")
    af = f"afade=t=in:st=0:d={fin:.2f},afade=t=out:st={fs:.4f}:d={fd:.4f}"
    cmd = [ffmpeg_cmd(), "-hide_banner", "-y", "-ss", f"{start:.3f}", "-i", str(source),
           "-t", str(URL_SHORT_SEC), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-af", af,
           "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
    update(tr("Short wird erstellt …"))
    run_hidden(cmd)


def pick_url_short_start(source: Path, duration: float, update: Callable[[str], None]) -> float:
    """Stärksten Abschnitt im Ton suchen; sonst die Mitte des Videos."""
    fallback = max(0.0, (duration - URL_SHORT_SEC) / 2)
    if duration > 1800:
        return fallback
    update(tr("Musik wird analysiert …"))
    wav = Path(tempfile.gettempdir()) / f"mvg_urlshort_{os.getpid()}.wav"
    try:
        subprocess.run([ffmpeg_cmd(), "-y", "-v", "error", "-i", str(source), "-vn",
                        "-ac", "1", "-ar", "11025", str(wav)],
                       check=True, creationflags=CREATE_NO_WINDOW, timeout=180)
        start, why = choose_short_start(str(wav), duration, win=URL_SHORT_SEC)
        return fallback if why.startswith("Songanfang") else start
    except Exception:
        return fallback
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def generate_short_from_url(url: str, update: Callable[[str], None]) -> Path:
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in YT_HOSTS:
        raise ValueError(tr("In der Zwischenablage ist kein gültiger YouTube-Link."))
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        raise RuntimeError(tr("yt-dlp ist nicht installiert (pip install yt-dlp)."))

    update(tr("YouTube-Werkzeug wird geprüft …"))
    deno = _ensure_deno()
    out_dir = OUTPUT_DIR / "Shorts"
    out_dir.mkdir(parents=True, exist_ok=True)

    def progress(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = f" {int(d.get('downloaded_bytes', 0) * 100 / total)}%" if total else ""
            update(tr("Video wird geladen …") + pct)

    with tempfile.TemporaryDirectory(prefix="mvg-short-") as tmp:
        opts = {"noplaylist": True, "quiet": True, "no_warnings": True,
                "format": "bv*[height<=1920]+ba/b[height<=1920]",
                "outtmpl": str(Path(tmp) / "source.%(ext)s"),
                "merge_output_format": "mp4", "progress_hooks": [progress]}
        ff = Path(ffmpeg_cmd())
        if ff.is_absolute() and ff.exists():
            opts["ffmpeg_location"] = str(ff.parent)
        if deno:
            opts["js_runtimes"] = {"deno": {"path": str(deno)}}
        update(tr("Video wird geprüft …"))
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("is_live") or info.get("_type") == "playlist":
                raise ValueError(tr("Bitte ein einzelnes, abgeschlossenes Video verwenden."))
            dur = float(info.get("duration") or 0)
            if dur < URL_SHORT_SEC:
                raise ValueError(tr("Das Video muss mindestens {n} Sekunden lang sein.").format(n=URL_SHORT_SEC))
            ydl.download([url])
        srcs = [q for q in Path(tmp).iterdir() if q.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if len(srcs) != 1:
            raise RuntimeError(tr("Die heruntergeladene Videodatei wurde nicht gefunden."))
        start = pick_url_short_start(srcs[0], dur, update)
        target = out_dir / f"Short_{info.get('id', 'video')}.mp4"
        render_url_short(srcs[0], target, start, update)
    return target


def on_short_url_click():
    """Button 'Short per URL': YouTube-Link aus der Zwischenablage -> 45-s-Short (9:16)."""
    global _url_short_busy
    if _url_short_busy:
        return
    try:
        url = app.clipboard_get().strip()
    except Exception:
        url = ""
    if not url:
        ui_status(tr("Zwischenablage leer – bitte zuerst einen YouTube-Link kopieren"))
        return
    _url_short_busy = True
    try:
        btn_standard.configure(state="disabled")
    except Exception:
        pass

    def upd(t: str):
        app.after(0, ui_status, t)

    def worker():
        global _url_short_busy
        try:
            path = generate_short_from_url(url, upd)
            app.after(0, ui_status, tr("Short fertig: {name}  (Ordner output/Shorts)").format(name=path.name))
            app.after(0, lambda: open_path_portable(path.parent))
        except Exception as e:
            msg = str(e)
            app.after(0, ui_status, tr("Short per URL fehlgeschlagen"))
            app.after(0, lambda: messagebox.showerror(tr("Short per URL"), msg))
        finally:
            _url_short_busy = False
            try:
                app.after(0, lambda: btn_standard.configure(state="normal"))
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


def use_standard_clips():
    external_var.set(False)
    photos_mode_var.set(False)
    pool_on_var.set(True)
    clips_var.set(str(DEFAULT_CLIPS_DIR))
    _refresh_all()


def on_external_toggle():
    if not bool(external_var.get()):
        clips_var.set(str(DEFAULT_CLIPS_DIR))
    _refresh_all()


def on_photos_toggle():
    _refresh_all()


def on_outro_toggle():
    update_output_label()


def on_format_change(changed: Optional[str] = None):
    # Mindestens ein Format muss aktiv bleiben
    if not selected_formats():
        format_vars[changed or "normal"].set(True)
        update_output_label()
        ui_status(tr("Mindestens ein Format muss aktiv sein"))
        return
    update_output_label()
    ui_status(tr("Formate: ") + " + ".join(FORMAT_LABELS[k] for k in selected_formats()))


def open_output_folder():
    open_path_portable(OUTPUT_DIR)


def open_last_video():
    if last_output_path and last_output_path.exists():
        open_path_portable(last_output_path)
    else:
        ui_status(tr("kein Video vorhanden"))


def open_youtube_upload(explicit_path: Optional[Path] = None):
    """Öffnet die allgemeine YouTube Upload-Seite im Browser — unabhängig vom Programmstatus."""
    import webbrowser
    webbrowser.open("https://www.youtube.com/upload")


def disable_controls(disabled: bool):
    state = "disabled" if disabled else "normal"
    for w in [
        btn_music, btn_reset, btn_help,
        cb_fmt_normal, cb_fmt_shorts, cb_fmt_shorts58,
        rb_gentle, rb_normal, rb_random,
        rb_slow, rb_fast,
        opt_pool,
        sw_intro, sw_outro, sw_vorspann, sw_title,
        sw_pool, sw_external, sw_photos,
        btn_standard, btn_external, btn_photos,
        cb_tr, cb_neu, cb_glu, cb_ani,
        btn_manual,
        btn_render, btn_edit,
    ]:
        try:
            w.configure(state=state)
        except Exception:
            pass

    if not disabled:
        set_pool_controls_state()
        set_mood_controls_state()
        update_active_button_colors()
        update_path_status_lines()
        # btn_edit nur aktivieren wenn bereits gerendert wurde
        try:
            btn_edit.configure(state="normal" if timeline_seg_meta else "disabled")
        except Exception:
            pass


def on_reset_clicked():
    global music_file, last_output_path, manual_clip_selection, manual_clip_moods

    # Render läuft gerade — nicht im Editor-Modus möglich, aber sicherheitshalber prüfen
    try:
        if btn_render.cget("state") == "disabled":
            ui_status(tr("Reset nicht möglich während Render läuft"))
            return
    except Exception:
        pass  # Im Editor-Modus existiert btn_render nicht — Reset trotzdem erlauben

    # Cache-Größe ermitteln
    cache_files = list(CACHE_DIR.glob("*.mp4"))
    cache_count = len(cache_files)
    cache_mb = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)

    if cache_count > 0:
        msg = (
            tr("Projekt zurücksetzen?\n\nCache enthält {n} Datei(en) ({mb:.1f} MB).\nCache ebenfalls leeren?")
            .format(n=cache_count, mb=cache_mb)
        )
        result = messagebox.askyesnocancel(tr("Projekt-Reset"), msg)
        if result is None:       # Abbrechen
            return
        clear_cache = result     # Ja = True, Nein = False
    else:
        ok = messagebox.askokcancel(tr("Projekt-Reset"), tr("Projekt zurücksetzen?"))
        if not ok:
            return
        clear_cache = False

    if clear_cache:
        for f in cache_files:
            try:
                f.unlink()
            except Exception:
                pass
        ui_status(tr("Cache geleert ({n} Dateien)").format(n=cache_count))

    music_file = None
    last_output_path = None
    music_var.set(tr("(keine Musik gewählt)"))

    for _k, _v in FORMAT_DEFAULTS.items():
        format_vars[_k].set(_v)
    speed_var.set("normal")
    intro_var.set(True)
    outro_var.set(True)
    vorspann_var.set(False)
    title_var.set(False)
    pool_on_var.set(False)
    external_var.set(False)
    photos_mode_var.set(False)
    pool_value_var.set(tr("Automatisch"))
    clips_var.set(str(DEFAULT_CLIPS_DIR))
    photos_var.set(str(PHOTOS_DIR))

    for m, v in mood_vars.items():
        v.set(m == "Neutral")
    manual_clip_selection = None
    manual_clip_moods = None

    # Timeline-State auch zurücksetzen
    global timeline_seg_meta, timeline_audio_path, in_editor_mode
    global _timeline_order, _tline_changed, timeline_render_info
    timeline_seg_meta    = []
    timeline_audio_path  = None
    timeline_render_info = {}
    in_editor_mode       = False
    _timeline_order     = []
    _tline_changed      = False
    TLINE_THUMB_CACHE.clear()
    for f in SEGMENTS_DIR.glob("seg*.mp4"):
        try: f.unlink()
        except Exception: pass

    # Hauptfenster neu aufbauen (funktioniert aus beiden Modi)
    _rebuild_main_ui()


def do_render():
    global last_output_path

    if not music_file:
        ui_status(tr("bitte Musik auswählen"))
        return

    # Ein Auftrag pro gewähltem Format. Das erste (normal > 9:16 > Short) ist das
    # Hauptvideo: nur dafür bleiben die Segmente für den Clip-Editor erhalten.
    jobs = [(k, get_output_path_from_music(music_file, k)) for k in selected_formats()]
    if not jobs:
        ui_status(tr("Mindestens ein Format muss aktiv sein"))
        return
    out_path = jobs[0][1]
    last_output_path = out_path
    update_output_label()

    speed = speed_var.get()
    use_intro = bool(intro_var.get())
    use_outro = bool(outro_var.get())
    use_vorspann = bool(vorspann_var.get())
    use_title = bool(title_var.get())
    title_text = get_title_from_music(music_file) if use_title else ""
    pool_value = parse_pool_value()
    moods = selected_moods()

    clips_folder = Path(clips_var.get())
    photos_folder = Path(photos_var.get())

    pool_on = bool(pool_on_var.get())
    ext_clips_on = bool(external_var.get())
    ext_photos_on = bool(photos_mode_var.get())

    if ext_clips_on and (not clips_folder.exists() or count_video_files(clips_folder) < 1):
        ui_status(tr("keine externen Clips gefunden"))
        return
    if ext_photos_on and (not photos_folder.exists() or count_image_files(photos_folder) < 1):
        ui_status(tr("keine externen Fotos gefunden"))
        return
    if pool_on and len(clips_from_moods(moods)) < 1 and not (ext_clips_on or ext_photos_on):
        ui_status(tr("Pool: keine Clips in der gewählten Stimmung"))
        return
    if not pool_on and not (ext_clips_on or ext_photos_on):
        ui_status(tr("Keine Quelle aktiv"))
        return

    disable_controls(True)
    ui_progress(0.0)
    ui_status(tr("Starte…"))

    def worker():
        try:
            n = len(jobs)
            main_result = None
            failed: list[str] = []

            for idx, (fmt, fmt_out) in enumerate(jobs):
                prefix = f"[{idx + 1}/{n} · {FORMAT_LABELS[fmt]}]  " if n > 1 else ""

                def st(text, prefix=prefix):
                    app.after(0, ui_status, prefix + text)

                def pr(p, idx=idx):
                    # Gesamtfortschritt über alle Formate
                    app.after(0, ui_progress, (idx + p) / n)

                try:
                    result = render_video(
                        audio_path=music_file,
                        clips_folder=clips_folder,
                        photos_folder=photos_folder,
                        out_path=fmt_out,
                        use_intro=use_intro,
                        use_outro=use_outro,
                        use_vorspann=use_vorspann,
                        speed=speed,
                        pool_value=pool_value,
                        pool_on=pool_on,
                        external_clips_on=ext_clips_on,
                        external_photos_on=ext_photos_on,
                        is_shorts=(fmt != "normal"),
                        shorts_limited=(fmt == "shorts58"),
                        moods_selected=moods,
                        title_overlay=use_title,
                        title_text=title_text,
                        status_cb=st,
                        progress_cb=pr,
                        persist_segments=(idx == 0),
                    )
                except Exception as e:
                    if idx == 0:
                        raise          # Hauptvideo fehlgeschlagen -> Abbruch
                    failed.append(f"{FORMAT_LABELS[fmt]}: {e}")
                    continue

                if idx == 0:
                    main_result = result

            _nv, _np, _ct, seg_meta, render_info = main_result
            done_names = [fo.name for (_f, fo) in jobs if fo.exists()]
            app.after(0, play_done_sound)
            if failed:
                app.after(0, ui_status, tr("Fertig: ") + " + ".join(done_names) + tr("  —  Fehler bei ") + "; ".join(failed))
            else:
                app.after(0, ui_status, tr("Fertig: ") + " + ".join(done_names))
            app.after(0, lambda: _after_render(seg_meta, render_info, music_file, out_path))
        except Exception as e:
            app.after(0, ui_status, tr("Fehler: ") + str(e))
        finally:
            app.after(0, lambda: disable_controls(False))

    threading.Thread(target=worker, daemon=True).start()


# ===================== MANUELLE CLIP-AUSWAHL =====================
THUMB_W = 160
THUMB_H = 90
THUMB_COLS = 4
THUMB_CACHE: dict[Path, "ctk.CTkImage"] = {}


def extract_thumbnail(clip: Path) -> Optional["ctk.CTkImage"]:
    """Extrahiert ein Frame aus dem Video als CTkImage."""
    from PIL import Image
    import io

    if clip in THUMB_CACHE:
        return THUMB_CACHE[clip]
    try:
        cmd = [
            ffmpeg_cmd(), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "0.5",
            "-i", str(clip),
            "-frames:v", "1",
            "-vf", f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,crop={THUMB_W}:{THUMB_H}",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "pipe:1"
        ]
        data = check_output_hidden(cmd)
        img = Image.open(io.BytesIO(data))
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(THUMB_W, THUMB_H))
        THUMB_CACHE[clip] = ctk_img
        return ctk_img
    except Exception:
        return None


def open_clip_selector():
    global manual_clip_selection

    moods = selected_moods()
    all_clips = clips_from_moods(moods)

    if not all_clips:
        ui_status(tr("Keine internen Clips gefunden"))
        return

    moods_label = ", ".join(moods) if moods else "Alle"
    win = ctk.CTkToplevel(app)
    win.title(tr("Manuelle Clip-Auswahl  —  Stimmung: {moods}").format(moods=moods_label))
    win.geometry("760x560")
    win.resizable(True, True)
    win.grab_set()

    # Aktuelle Auswahl vorbelegen
    # Auswahl zurücksetzen wenn sich die Stimmung geändert hat
    if manual_clip_selection is not None and manual_clip_moods != moods:
        manual_clip_selection = None

    if manual_clip_selection is not None:
        sel_resolved = {p.resolve() for p in manual_clip_selection}
        selected = {p: ctk.BooleanVar(value=(p.resolve() in sel_resolved)) for p in all_clips}
    else:
        selected = {p: ctk.BooleanVar(value=False) for p in all_clips}

    thumb_buttons: dict[Path, ctk.CTkButton] = {}

    # Header
    header = ctk.CTkFrame(win, corner_radius=0, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(12, 6))
    ctk.CTkLabel(header, text=tr("Clips auswählen  ({moods})").format(moods=moods_label), font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
    count_lbl = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=11))
    count_lbl.pack(side="right")

    def update_count():
        n = sum(1 for v in selected.values() if v.get())
        count_lbl.configure(text=tr("{n} / {total} ausgewählt").format(n=n, total=len(all_clips)))
        try:
            refresh_ok_state()
        except Exception:
            pass  # refresh_ok_state noch nicht definiert

    # Scrollbarer Grid
    scroll = ctk.CTkScrollableFrame(win, corner_radius=8)
    scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    def toggle(clip: Path):
        selected[clip].set(not selected[clip].get())
        refresh_border(clip)
        update_count()

    def refresh_border(clip: Path):
        btn = thumb_buttons.get(clip)
        if btn is None:
            return
        if selected[clip].get():
            btn.configure(border_color="#1f6aa5", border_width=3)
        else:
            btn.configure(border_color="gray30", border_width=1)

    # Thumbnails laden (im Thread damit Fenster nicht einfriert)
    win_alive = [True]

    def on_win_close():
        win_alive[0] = False
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_win_close)

    def load_thumbs():
        for idx, clip in enumerate(all_clips):
            if not win_alive[0]:
                return
            row = idx // THUMB_COLS
            col = idx % THUMB_COLS
            img = extract_thumbnail(clip)

            def make_btn(c=clip, i=img, r=row, co=col):
                if not win_alive[0]:
                    return
                try:
                    btn = ctk.CTkButton(
                        scroll,
                        image=i,
                        text="",
                        width=THUMB_W + 6,
                        height=THUMB_H + 6,
                        corner_radius=6,
                        border_width=3 if selected[c].get() else 1,
                        border_color="#1f6aa5" if selected[c].get() else "gray30",
                        fg_color="transparent",
                        hover_color="gray20",
                        command=lambda cl=c: toggle(cl)
                    )
                    btn.grid(row=r, column=co, padx=6, pady=6)
                    thumb_buttons[c] = btn
                except Exception:
                    pass

            win.after(0, make_btn)

        def final_count():
            if win_alive[0]:
                update_count()
        win.after(0, final_count)

    threading.Thread(target=load_thumbs, daemon=True).start()
    update_count()

    # Alle / Keine Buttons
    btn_row = ctk.CTkFrame(win, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(0, 4))

    def select_all():
        for v in selected.values():
            v.set(True)
        for c in all_clips:
            refresh_border(c)
        update_count()

    def select_none():
        for v in selected.values():
            v.set(False)
        for c in all_clips:
            refresh_border(c)
        update_count()

    ctk.CTkButton(btn_row, text=tr("Alle"), width=100, command=select_all).pack(side="left", padx=(0, 8))
    ctk.CTkButton(btn_row, text=tr("Keine"), width=100, command=select_none).pack(side="left")

    # OK / Abbrechen
    def on_ok():
        global manual_clip_selection, manual_clip_moods
        win_alive[0] = False
        chosen = [p for p, v in selected.items() if v.get()]
        if not chosen:
            manual_clip_selection = None
            ui_status(tr("Manuelle Auswahl: alle Clips"))
        elif len(chosen) == len(all_clips):
            manual_clip_selection = None
            ui_status(tr("Manuelle Auswahl: alle Clips"))
        else:
            manual_clip_selection = chosen
            manual_clip_moods = moods
            ui_status(tr("Manuelle Auswahl: {n} Clips").format(n=len(chosen)))
        refresh_count()
        win.destroy()

    def on_cancel():
        win_alive[0] = False
        win.destroy()

    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=16, pady=(0, 12))
    btn_ok = ctk.CTkButton(foot, text=tr("OK"), width=130, command=on_ok, state="disabled")
    btn_ok.pack(side="right", padx=(8, 0))
    ctk.CTkButton(foot, text=tr("Abbrechen"), width=130, fg_color="gray40",
                  hover_color="gray30", command=on_cancel).pack(side="right")

    def refresh_ok_state():
        n = sum(1 for v in selected.values() if v.get())
        btn_ok.configure(state="normal" if n > 0 else "disabled")


# ===================== LAYOUT =====================
outer = ctk.CTkFrame(app, corner_radius=16)
outer.pack(fill="both", expand=True, padx=16, pady=10)

# ── Titel
ctk.CTkLabel(
    outer, text=tr("Musik Video Generator"),
    font=ctk.CTkFont(size=22, weight="bold")
).pack(pady=(12, 6))

# ── Musik-Zeile
sec_music = ctk.CTkFrame(outer, corner_radius=12)
sec_music.pack(fill="x", padx=16, pady=(0, 8))
sec_music.grid_columnconfigure(1, weight=1)

btn_music = ctk.CTkButton(sec_music, text=tr("Musik wählen"), width=160, command=choose_music)
btn_music.grid(row=0, column=0, padx=12, pady=10)

ctk.CTkLabel(sec_music, textvariable=music_var, wraplength=580, justify="left").grid(
    row=0, column=1, sticky="w", padx=8, pady=10
)

btn_reset = ctk.CTkButton(sec_music, text=tr("Reset"), width=100, command=on_reset_clicked)
btn_reset.grid(row=0, column=2, padx=(0, 6), pady=10)

btn_help = ctk.CTkButton(sec_music, text="?", width=36, command=lambda: open_help_menu())
btn_help.grid(row=0, column=3, padx=(0, 12), pady=10)

# ── Mittlere Zeile: Format | Schnitt | Clip-Pool
mid = ctk.CTkFrame(outer, corner_radius=0, fg_color="transparent")
mid.pack(fill="x", padx=16, pady=(0, 8))
mid.grid_columnconfigure(0, weight=0)
mid.grid_columnconfigure(1, weight=0)
mid.grid_columnconfigure(2, weight=1)

# Format
sec_fmt = ctk.CTkFrame(mid, corner_radius=12, width=230)
sec_fmt.grid(row=0, column=0, sticky="ns", padx=(0, 6), pady=0)
sec_fmt.pack_propagate(False)
sec_fmt.grid_propagate(False)

ctk.CTkLabel(sec_fmt, text=tr("Format"), font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 4)
)
cb_fmt_normal = ctk.CTkCheckBox(sec_fmt, text=tr("Normal  16:9"), variable=format_vars["normal"],
                                command=lambda: on_format_change("normal"))
cb_fmt_normal.pack(anchor="w", padx=12, pady=(0, 4))
cb_fmt_shorts = ctk.CTkCheckBox(sec_fmt, text=tr("Normal  9:16"), variable=format_vars["shorts"],
                                command=lambda: on_format_change("shorts"))
cb_fmt_shorts.pack(anchor="w", padx=12, pady=(0, 4))
cb_fmt_shorts58 = ctk.CTkCheckBox(sec_fmt, text=tr("Short  9:16 · 58s"), variable=format_vars["shorts58"],
                                  command=lambda: on_format_change("shorts58"))
cb_fmt_shorts58.pack(anchor="w", padx=12, pady=(0, 10))

# Schnitt
sec_speed = ctk.CTkFrame(mid, corner_radius=12, width=190)
sec_speed.grid(row=0, column=1, sticky="ns", padx=6, pady=0)
sec_speed.pack_propagate(False)
sec_speed.grid_propagate(False)

ctk.CTkLabel(sec_speed, text=tr("Schnitt"), font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 4)
)
rb_gentle = ctk.CTkRadioButton(sec_speed, text=tr("Sanft"), variable=speed_var, value="gentle")
rb_gentle.pack(anchor="w", padx=12, pady=(0, 4))
rb_slow = ctk.CTkRadioButton(sec_speed, text=tr("Langsam"), variable=speed_var, value="slow")
rb_slow.pack(anchor="w", padx=12, pady=(0, 4))
rb_normal = ctk.CTkRadioButton(sec_speed, text=tr("Normal"), variable=speed_var, value="normal")
rb_normal.pack(anchor="w", padx=12, pady=(0, 4))
rb_fast = ctk.CTkRadioButton(sec_speed, text=tr("Schnell"), variable=speed_var, value="fast")
rb_fast.pack(anchor="w", padx=12, pady=(0, 4))
rb_random = ctk.CTkRadioButton(sec_speed, text=tr("Random"), variable=speed_var, value="random")
rb_random.pack(anchor="w", padx=12, pady=(0, 10))

# Clip-Pool
sec_pool = ctk.CTkFrame(mid, corner_radius=12)
sec_pool.grid(row=0, column=2, sticky="nsew", padx=(6, 0), pady=0)
sec_pool.grid_columnconfigure(0, weight=1)

ctk.CTkLabel(sec_pool, text=tr("Interner Clip-Pool"), font=ctk.CTkFont(size=13, weight="bold")).grid(
    row=0, column=0, sticky="w", padx=12, pady=(10, 2)
)
ctk.CTkLabel(sec_pool, text=tr("Wähle deine Stimmung"),
             text_color=("gray40", "gray70"), font=ctk.CTkFont(size=11)).grid(
    row=1, column=0, sticky="w", padx=12, pady=(0, 4)
)

mood_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
mood_row.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 6))

cb_tr  = ctk.CTkCheckBox(mood_row, text=tr("Traurig"),   variable=mood_vars["Traurig"],   command=on_moods_changed)
cb_neu = ctk.CTkCheckBox(mood_row, text=tr("Neutral"),   variable=mood_vars["Neutral"],   command=on_moods_changed)
cb_glu = ctk.CTkCheckBox(mood_row, text=tr("Glücklich"), variable=mood_vars["Glücklich"], command=on_moods_changed)
cb_ani = ctk.CTkCheckBox(mood_row, text=tr("Animation"), variable=mood_vars["Animation"], command=on_moods_changed)
cb_tr.grid(row=0, column=0, padx=(0, 12))
cb_neu.grid(row=0, column=1, padx=(0, 12))
cb_glu.grid(row=0, column=2, padx=(0, 12))
cb_ani.grid(row=0, column=3)

ctk.CTkLabel(sec_pool, textvariable=pool_hint_var, font=ctk.CTkFont(size=11)).grid(
    row=3, column=0, sticky="w", padx=12, pady=(0, 2)
)
pool_btn_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
pool_btn_row.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 10))

opt_pool   = ctk.CTkOptionMenu(pool_btn_row, values=POOL_LABELS, variable=pool_value_var, width=140)
opt_pool.grid(row=0, column=0, padx=(0, 8))
btn_manual = ctk.CTkButton(pool_btn_row, text=tr("Manuelle Auswahl"), width=150, command=open_clip_selector)
btn_manual.grid(row=0, column=1)

# ── Clips & Quellen
sec_files = ctk.CTkFrame(outer, corner_radius=12)
sec_files.pack(fill="x", padx=16, pady=(0, 8))

ctk.CTkLabel(sec_files, text=tr("Quellen & Optionen"), font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 6)
)

# Schalter-Zeile
sw_row = ctk.CTkFrame(sec_files, fg_color="transparent")
sw_row.pack(anchor="w", padx=12, pady=(0, 8))

sw_vorspann = ctk.CTkSwitch(sw_row, text=tr("Pre-Intro"), variable=vorspann_var)
sw_vorspann.grid(row=0, column=0, padx=(0, 16))

sw_intro = ctk.CTkSwitch(sw_row, text=tr("Intro"), variable=intro_var)
sw_intro.grid(row=0, column=1, padx=(0, 16))

sw_outro = ctk.CTkSwitch(sw_row, text=tr("Outro"), variable=outro_var, command=on_outro_toggle)
sw_outro.grid(row=0, column=2, padx=(0, 16))

sw_title = ctk.CTkSwitch(sw_row, text=tr("Titel"), variable=title_var)
sw_title.grid(row=0, column=3, padx=(0, 16))

sw_pool = ctk.CTkSwitch(sw_row, text=tr("Interner Pool"), variable=pool_on_var, command=on_pool_toggle)
sw_pool.grid(row=0, column=4, padx=(0, 16))

sw_external = ctk.CTkSwitch(sw_row, text=tr("Externe Clips"), variable=external_var, command=on_external_toggle)
sw_external.grid(row=0, column=5, padx=(0, 16))

sw_photos = ctk.CTkSwitch(sw_row, text=tr("Externe Fotos"), variable=photos_mode_var, command=on_photos_toggle)
sw_photos.grid(row=0, column=6)

# Ordner-Buttons
btn_row = ctk.CTkFrame(sec_files, fg_color="transparent")
btn_row.pack(fill="x", padx=12, pady=(0, 4))
btn_row.grid_columnconfigure(0, weight=1)
btn_row.grid_columnconfigure(1, weight=1)
btn_row.grid_columnconfigure(2, weight=1)

btn_standard = ctk.CTkButton(btn_row, text=tr("Short per URL (Zwischenablage)"), command=on_short_url_click)
btn_standard.grid(row=0, column=0, sticky="ew", padx=(0, 6))
btn_external = ctk.CTkButton(btn_row, text=tr("Externe Clips wählen…"), command=choose_external_clips_folder)
btn_external.grid(row=0, column=1, sticky="ew", padx=(0, 6))
btn_photos = ctk.CTkButton(btn_row, text=tr("Externe Fotos wählen…"), command=choose_photos_folder)
btn_photos.grid(row=0, column=2, sticky="ew")

BTN_DEFAULT_FG = btn_standard.cget("fg_color")

# Pfad & Anzahl
path_frame = ctk.CTkFrame(sec_files, fg_color="transparent")
path_frame.pack(fill="x", padx=12, pady=(4, 8))

ctk.CTkLabel(path_frame, textvariable=clips_path_status_var,
             font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
ctk.CTkLabel(path_frame, textvariable=photos_path_status_var,
             font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")

count_line = ctk.CTkFrame(sec_files, fg_color="transparent")
count_line.pack(anchor="w", padx=12, pady=(0, 10))
ctk.CTkLabel(count_line, text=tr("Anzahl:")).grid(row=0, column=0, padx=(0, 8))
ctk.CTkLabel(count_line, textvariable=count_var).grid(row=0, column=1)

# ── Render + Clips verschieben nebeneinander
btn_row_render = ctk.CTkFrame(outer, fg_color="transparent")
btn_row_render.pack(pady=(4, 4))

btn_render = ctk.CTkButton(btn_row_render, text=tr("▶  Render"), width=240, height=42, command=do_render)
btn_render.grid(row=0, column=0, padx=(0, 8))

btn_edit = ctk.CTkButton(btn_row_render, text=tr("✏  Clips verschieben"), width=200, height=42,
                         state="disabled", command=lambda: _enter_editor())
btn_edit.grid(row=0, column=1)

ctk.CTkLabel(outer, textvariable=out_name_var, font=ctk.CTkFont(size=11)).pack(pady=(0, 6))

# ── Bottom: Fortschritt + Buttons
bottom = ctk.CTkFrame(outer, corner_radius=12)
bottom.pack(fill="x", padx=16, pady=(0, 10))
bottom.grid_columnconfigure(0, weight=1)

progressbar = ctk.CTkProgressBar(bottom, height=12)
progressbar.set(0.0)
progressbar.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 6))

status_box = ctk.CTkTextbox(bottom, height=28, wrap="none", activate_scrollbars=False,
                            font=ctk.CTkFont(size=12))
status_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
status_box.insert("1.0", tr("bereit"))
status_box.configure(state="disabled")
ctk.CTkButton(bottom, text=tr("▶  Video anschauen"), width=150, command=open_last_video).grid(
    row=1, column=1, sticky="e", padx=6, pady=(0, 10)
)
ctk.CTkButton(bottom, text=tr("Output-Ordner"), width=130, command=open_output_folder).grid(
    row=1, column=2, sticky="e", padx=6, pady=(0, 10)
)
ctk.CTkButton(bottom, text=tr("YouTube"), width=110, command=open_youtube_upload).grid(
    row=1, column=3, sticky="e", padx=12, pady=(0, 10)
)

# Init
refresh_count()
set_pool_controls_state()
set_mood_controls_state()
update_active_button_colors()
update_output_label()
update_source_status()
update_path_status_lines()


# ===================== NACH RENDER: EDIT-BUTTON =====================

timeline_render_info: dict = {}


def _after_render(seg_meta: list[dict], render_info: dict, audio_path: str, out_path: Path):
    """Wird nach erfolgreichem Render aufgerufen — Clips verschieben Button wird aktiv."""
    global timeline_seg_meta, timeline_audio_path, timeline_render_info
    timeline_seg_meta    = seg_meta
    timeline_audio_path  = audio_path
    timeline_render_info = render_info
    TLINE_THUMB_CACHE.clear()
    try:
        btn_edit.configure(state="normal")
    except Exception:
        pass


def _enter_editor():
    """Transformiert das Hauptfenster in den Timeline-Editor."""
    global in_editor_mode, _timeline_order, _tline_changed

    if not timeline_seg_meta:
        return

    in_editor_mode   = True
    _timeline_order  = list(range(len(timeline_seg_meta)))
    _tline_changed   = False

    out_path   = last_output_path
    audio_path = timeline_audio_path

    # Alle Widgets leeren
    for widget in outer.winfo_children():
        widget.destroy()

    # ── Titel (identisch)
    ctk.CTkLabel(
        outer, text=tr("Musik Video Generator"),
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(pady=(12, 6))

    # ── Obere Zeile: Abbrechen-Button + Hinweis + Reset + Hilfe
    sec_top = ctk.CTkFrame(outer, corner_radius=12)
    sec_top.pack(fill="x", padx=16, pady=(0, 8))
    sec_top.grid_columnconfigure(1, weight=1)

    btn_abbrechen = ctk.CTkButton(
        sec_top, text=tr("✕  Abbrechen"), width=160,
        fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
        command=_cancel_editor
    )
    btn_abbrechen.grid(row=0, column=0, padx=12, pady=10)
    outer._btn_abbrechen = btn_abbrechen  # type: ignore[attr-defined]

    ctk.CTkLabel(
        sec_top,
        text=tr("Clips per Drag & Drop umsortieren oder mit „✕ Entfernen“ ersetzen – dann Übernehmen"),
        font=ctk.CTkFont(size=12),
        text_color=("gray30", "gray75"),
        anchor="w",
    ).grid(row=0, column=1, sticky="w", padx=8, pady=10)

    ctk.CTkButton(
        sec_top, text=tr("Reset"), width=100, command=on_reset_clicked
    ).grid(row=0, column=2, padx=(0, 6), pady=10)

    ctk.CTkButton(
        sec_top, text="?", width=36, command=lambda: open_help_menu()
    ).grid(row=0, column=3, padx=(0, 12), pady=10)

    # ── Clip-Grid
    sec_grid = ctk.CTkFrame(outer, corner_radius=12)
    sec_grid.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    grid_scroll = ctk.CTkScrollableFrame(sec_grid, fg_color="transparent")
    grid_scroll.pack(fill="both", expand=True, padx=8, pady=8)

    grid_frame = ctk.CTkFrame(grid_scroll, fg_color="transparent")
    grid_frame.pack(fill="both", expand=True)
    outer._grid_frame = grid_frame  # type: ignore[attr-defined]

    # Sofort Ladehinweis zeigen
    ctk.CTkLabel(
        grid_frame,
        text=tr("⏳  Vorschaubilder werden geladen…"),
        font=ctk.CTkFont(size=18),
        text_color=("gray40", "gray65"),
    ).pack(pady=40)

    # Thumbnails laden, dann Grid aufbauen
    def load_then_build():
        for meta in timeline_seg_meta:
            extract_tline_thumbnail(meta["path"])
        app.after(0, lambda: _build_clip_grid(grid_frame, out_path))

    threading.Thread(target=load_then_build, daemon=True).start()

    # ── Übernehmen-Button (an Stelle des Render-Buttons)
    btn_übernehmen = ctk.CTkButton(
        outer, text=tr("✔  Übernehmen"), width=240, height=42,
        state="disabled",
        command=lambda: _do_reorder(audio_path, out_path)
    )
    btn_übernehmen.pack(pady=(4, 4))
    outer._btn_reorder = btn_übernehmen  # type: ignore[attr-defined]

    ctk.CTkLabel(
        outer,
        text=tr("Ziehen = neue Position · Entfernen = zufälliger Ersatzclip gleicher Länge (Intro/Outro bleiben)"),
        font=ctk.CTkFont(size=11),
        text_color=("gray40", "gray65"),
    ).pack(pady=(0, 4))

    # ── Bottom (identisch mit Hauptfenster)
    bottom_ed = ctk.CTkFrame(outer, corner_radius=12)
    bottom_ed.pack(fill="x", padx=16, pady=(0, 10))
    bottom_ed.grid_columnconfigure(0, weight=1)

    progressbar_ed = ctk.CTkProgressBar(bottom_ed, height=12)
    progressbar_ed.set(0.0)
    progressbar_ed.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 6))
    outer._progressbar_ed = progressbar_ed  # type: ignore[attr-defined]

    status_ed = ctk.CTkTextbox(bottom_ed, height=28, wrap="none",
                               activate_scrollbars=False, font=ctk.CTkFont(size=12))
    status_ed.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
    status_ed.insert("1.0", tr("Clips verschieben oder entfernen, dann Übernehmen klicken"))
    status_ed.configure(state="disabled")
    outer._status_ed = status_ed  # type: ignore[attr-defined]

    ctk.CTkButton(
        bottom_ed, text=tr("▶  Video anschauen"), width=150,
        command=lambda: open_path_portable(out_path)
    ).grid(row=1, column=1, sticky="e", padx=6, pady=(0, 10))

    ctk.CTkButton(
        bottom_ed, text=tr("Output-Ordner"), width=130,
        command=open_output_folder
    ).grid(row=1, column=2, sticky="e", padx=6, pady=(0, 10))

    ctk.CTkButton(
        bottom_ed, text=tr("YouTube"), width=110,
        command=lambda: open_youtube_upload(out_path)
    ).grid(row=1, column=3, sticky="e", padx=12, pady=(0, 10))


def _cancel_editor():
    """Zurück zum Hauptfenster — nicht übernommene Änderungen (Reihenfolge, ersetzte Clips)
    werden verworfen, Einstellungen bleiben."""
    global in_editor_mode, _timeline_order, _tline_changed
    if _reorder_busy or _replace_busy:
        _editor_status("Bitte kurz warten …")
        return
    _restore_replaced_clips()
    in_editor_mode   = False
    _timeline_order  = list(range(len(timeline_seg_meta)))
    _tline_changed   = False
    _rebuild_main_ui()


def _cancel_editor_keep_order():
    """Zurück nach "Übernehmen". Der übernommene Stand steckt bereits fest in der
    Segmentliste; nur danach noch Geändertes (nicht übernommen) wird verworfen."""
    _cancel_editor()


def _rebuild_main_ui():
    """Baut das Hauptfenster neu auf."""
    for widget in outer.winfo_children():
        widget.destroy()

    # Titel
    ctk.CTkLabel(
        outer, text=tr("Musik Video Generator"),
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(pady=(12, 6))

    # Musik-Zeile
    global btn_music, btn_reset, btn_help
    sec_music = ctk.CTkFrame(outer, corner_radius=12)
    sec_music.pack(fill="x", padx=16, pady=(0, 8))
    sec_music.grid_columnconfigure(1, weight=1)
    btn_music = ctk.CTkButton(sec_music, text=tr("Musik wählen"), width=160, command=choose_music)
    btn_music.grid(row=0, column=0, padx=12, pady=10)
    ctk.CTkLabel(sec_music, textvariable=music_var, wraplength=580, justify="left").grid(
        row=0, column=1, sticky="w", padx=8, pady=10)
    btn_reset = ctk.CTkButton(sec_music, text=tr("Reset"), width=100, command=on_reset_clicked)
    btn_reset.grid(row=0, column=2, padx=(0, 6), pady=10)
    btn_help = ctk.CTkButton(sec_music, text="?", width=36, command=lambda: open_help_menu())
    btn_help.grid(row=0, column=3, padx=(0, 12), pady=10)

    # Mittlere Sektion wiederherstellen — einfach neu initialisieren
    _init_mid_section()

    # Render + Clips verschieben nebeneinander
    global btn_render, btn_edit
    btn_row_render = ctk.CTkFrame(outer, fg_color="transparent")
    btn_row_render.pack(pady=(4, 4))
    btn_render = ctk.CTkButton(btn_row_render, text=tr("▶  Render"), width=240, height=42, command=do_render)
    btn_render.grid(row=0, column=0, padx=(0, 8))
    btn_edit = ctk.CTkButton(btn_row_render, text=tr("✏  Clips verschieben"), width=200, height=42,
                             state="normal" if timeline_seg_meta else "disabled",
                             command=_enter_editor)
    btn_edit.grid(row=0, column=1)
    ctk.CTkLabel(outer, textvariable=out_name_var, font=ctk.CTkFont(size=11)).pack(pady=(0, 6))

    # Bottom
    global progressbar, status_box
    bottom = ctk.CTkFrame(outer, corner_radius=12)
    bottom.pack(fill="x", padx=16, pady=(0, 10))
    bottom.grid_columnconfigure(0, weight=1)
    progressbar = ctk.CTkProgressBar(bottom, height=12)
    progressbar.set(0.0)
    progressbar.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 6))
    status_box = ctk.CTkTextbox(bottom, height=28, wrap="none",
                                activate_scrollbars=False, font=ctk.CTkFont(size=12))
    status_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
    status_box.insert("1.0", tr("bereit"))
    status_box.configure(state="disabled")
    ctk.CTkButton(bottom, text=tr("▶  Video anschauen"), width=150, command=open_last_video).grid(
        row=1, column=1, sticky="e", padx=6, pady=(0, 10))
    ctk.CTkButton(bottom, text=tr("Output-Ordner"), width=130, command=open_output_folder).grid(
        row=1, column=2, sticky="e", padx=6, pady=(0, 10))
    ctk.CTkButton(bottom, text=tr("YouTube"), width=110, command=open_youtube_upload).grid(
        row=1, column=3, sticky="e", padx=12, pady=(0, 10))

    refresh_count()
    set_pool_controls_state()
    set_mood_controls_state()
    update_active_button_colors()
    update_output_label()
    update_source_status()
    update_path_status_lines()


def _init_mid_section():
    """Baut Format/Schnitt/Pool + Quellen-Sektion auf."""
    global cb_fmt_normal, cb_fmt_shorts, cb_fmt_shorts58
    global rb_gentle, rb_normal, rb_random, rb_slow, rb_fast
    global opt_pool, sw_intro, sw_outro, sw_vorspann, sw_title
    global sw_pool, sw_external, sw_photos
    global btn_standard, btn_external, btn_photos
    global cb_tr, cb_neu, cb_glu, cb_ani, btn_manual
    global BTN_DEFAULT_FG

    mid = ctk.CTkFrame(outer, corner_radius=0, fg_color="transparent")
    mid.pack(fill="x", padx=16, pady=(0, 8))
    mid.grid_columnconfigure(0, weight=0)
    mid.grid_columnconfigure(1, weight=0)
    mid.grid_columnconfigure(2, weight=1)

    # Format
    sec_fmt = ctk.CTkFrame(mid, corner_radius=12, width=230)
    sec_fmt.grid(row=0, column=0, sticky="ns", padx=(0, 6))
    sec_fmt.pack_propagate(False)
    sec_fmt.grid_propagate(False)
    ctk.CTkLabel(sec_fmt, text=tr("Format"), font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))
    cb_fmt_normal = ctk.CTkCheckBox(sec_fmt, text=tr("Normal  16:9"), variable=format_vars["normal"], command=lambda: on_format_change("normal"))
    cb_fmt_normal.pack(anchor="w", padx=12, pady=(0, 4))
    cb_fmt_shorts = ctk.CTkCheckBox(sec_fmt, text=tr("Normal  9:16"), variable=format_vars["shorts"], command=lambda: on_format_change("shorts"))
    cb_fmt_shorts.pack(anchor="w", padx=12, pady=(0, 4))
    cb_fmt_shorts58 = ctk.CTkCheckBox(sec_fmt, text=tr("Short  9:16 · 58s"), variable=format_vars["shorts58"], command=lambda: on_format_change("shorts58"))
    cb_fmt_shorts58.pack(anchor="w", padx=12, pady=(0, 10))

    # Schnitt
    sec_speed = ctk.CTkFrame(mid, corner_radius=12, width=190)
    sec_speed.grid(row=0, column=1, sticky="ns", padx=6)
    sec_speed.pack_propagate(False)
    sec_speed.grid_propagate(False)
    ctk.CTkLabel(sec_speed, text=tr("Schnitt"), font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))
    rb_gentle = ctk.CTkRadioButton(sec_speed, text=tr("Sanft"),    variable=speed_var, value="gentle")
    rb_gentle.pack(anchor="w", padx=12, pady=(0, 4))
    rb_slow   = ctk.CTkRadioButton(sec_speed, text=tr("Langsam"),  variable=speed_var, value="slow")
    rb_slow.pack(anchor="w", padx=12, pady=(0, 4))
    rb_normal = ctk.CTkRadioButton(sec_speed, text=tr("Normal"),   variable=speed_var, value="normal")
    rb_normal.pack(anchor="w", padx=12, pady=(0, 4))
    rb_fast   = ctk.CTkRadioButton(sec_speed, text=tr("Schnell"),  variable=speed_var, value="fast")
    rb_fast.pack(anchor="w", padx=12, pady=(0, 4))
    rb_random = ctk.CTkRadioButton(sec_speed, text=tr("Random"),   variable=speed_var, value="random")
    rb_random.pack(anchor="w", padx=12, pady=(0, 10))

    # Clip-Pool
    sec_pool = ctk.CTkFrame(mid, corner_radius=12)
    sec_pool.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
    sec_pool.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(sec_pool, text=tr("Interner Clip-Pool"), font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(sec_pool, text=tr("Wähle deine Stimmung"), text_color=("gray40", "gray70"), font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
    mood_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
    mood_row.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 6))
    cb_tr  = ctk.CTkCheckBox(mood_row, text=tr("Traurig"),   variable=mood_vars["Traurig"],   command=on_moods_changed)
    cb_neu = ctk.CTkCheckBox(mood_row, text=tr("Neutral"),   variable=mood_vars["Neutral"],   command=on_moods_changed)
    cb_glu = ctk.CTkCheckBox(mood_row, text=tr("Glücklich"), variable=mood_vars["Glücklich"], command=on_moods_changed)
    cb_ani = ctk.CTkCheckBox(mood_row, text=tr("Animation"), variable=mood_vars["Animation"], command=on_moods_changed)
    cb_tr.grid(row=0, column=0, padx=(0, 12))
    cb_neu.grid(row=0, column=1, padx=(0, 12))
    cb_glu.grid(row=0, column=2, padx=(0, 12))
    cb_ani.grid(row=0, column=3)
    ctk.CTkLabel(sec_pool, textvariable=pool_hint_var, font=ctk.CTkFont(size=11)).grid(row=3, column=0, sticky="w", padx=12, pady=(0, 2))
    pool_btn_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
    pool_btn_row.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 10))
    opt_pool   = ctk.CTkOptionMenu(pool_btn_row, values=POOL_LABELS, variable=pool_value_var, width=140)
    opt_pool.grid(row=0, column=0, padx=(0, 8))
    btn_manual = ctk.CTkButton(pool_btn_row, text=tr("Manuelle Auswahl"), width=150, command=open_clip_selector)
    btn_manual.grid(row=0, column=1)

    # Quellen & Optionen
    sec_files = ctk.CTkFrame(outer, corner_radius=12)
    sec_files.pack(fill="x", padx=16, pady=(0, 8))
    ctk.CTkLabel(sec_files, text=tr("Quellen & Optionen"), font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 6))
    sw_row = ctk.CTkFrame(sec_files, fg_color="transparent")
    sw_row.pack(anchor="w", padx=12, pady=(0, 8))
    sw_vorspann = ctk.CTkSwitch(sw_row, text=tr("Pre-Intro"), variable=vorspann_var)
    sw_vorspann.grid(row=0, column=0, padx=(0, 16))
    sw_intro = ctk.CTkSwitch(sw_row, text=tr("Intro"), variable=intro_var)
    sw_intro.grid(row=0, column=1, padx=(0, 16))
    sw_outro = ctk.CTkSwitch(sw_row, text=tr("Outro"), variable=outro_var, command=on_outro_toggle)
    sw_outro.grid(row=0, column=2, padx=(0, 16))
    sw_title = ctk.CTkSwitch(sw_row, text=tr("Titel"), variable=title_var)
    sw_title.grid(row=0, column=3, padx=(0, 16))
    sw_pool = ctk.CTkSwitch(sw_row, text=tr("Interner Pool"), variable=pool_on_var, command=on_pool_toggle)
    sw_pool.grid(row=0, column=4, padx=(0, 16))
    sw_external = ctk.CTkSwitch(sw_row, text=tr("Externe Clips"), variable=external_var, command=on_external_toggle)
    sw_external.grid(row=0, column=5, padx=(0, 16))
    sw_photos = ctk.CTkSwitch(sw_row, text=tr("Externe Fotos"), variable=photos_mode_var, command=on_photos_toggle)
    sw_photos.grid(row=0, column=6)
    btn_row2 = ctk.CTkFrame(sec_files, fg_color="transparent")
    btn_row2.pack(fill="x", padx=12, pady=(0, 4))
    btn_row2.grid_columnconfigure(0, weight=1)
    btn_row2.grid_columnconfigure(1, weight=1)
    btn_row2.grid_columnconfigure(2, weight=1)
    btn_standard = ctk.CTkButton(btn_row2, text=tr("Short per URL (Zwischenablage)"), command=on_short_url_click)
    btn_standard.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    BTN_DEFAULT_FG = btn_standard.cget("fg_color")
    btn_external = ctk.CTkButton(btn_row2, text=tr("Externe Clips wählen…"), command=choose_external_clips_folder)
    btn_external.grid(row=0, column=1, sticky="ew", padx=(0, 6))
    btn_photos = ctk.CTkButton(btn_row2, text=tr("Externe Fotos wählen…"), command=choose_photos_folder)
    btn_photos.grid(row=0, column=2, sticky="ew")
    path_frame = ctk.CTkFrame(sec_files, fg_color="transparent")
    path_frame.pack(fill="x", padx=12, pady=(4, 8))
    ctk.CTkLabel(path_frame, textvariable=clips_path_status_var, font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
    ctk.CTkLabel(path_frame, textvariable=photos_path_status_var, font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
    count_line = ctk.CTkFrame(sec_files, fg_color="transparent")
    count_line.pack(anchor="w", padx=12, pady=(0, 10))
    ctk.CTkLabel(count_line, text=tr("Anzahl:")).grid(row=0, column=0, padx=(0, 8))
    ctk.CTkLabel(count_line, textvariable=count_var).grid(row=0, column=1)


# ===================== TIMELINE EDITOR FUNKTIONEN =====================

def extract_tline_thumbnail(seg_path: str) -> Optional["ctk.CTkImage"]:
    import io
    if seg_path in TLINE_THUMB_CACHE:
        return TLINE_THUMB_CACHE[seg_path]

    def _try_extract(ss: float) -> Optional[ctk.CTkImage]:
        try:
            cmd = [
                ffmpeg_cmd(), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{ss:.2f}", "-i", seg_path,
                "-frames:v", "1",
                "-vf", f"scale={TLINE_THUMB_W}:{TLINE_THUMB_H}:"
                       f"force_original_aspect_ratio=increase,"
                       f"crop={TLINE_THUMB_W}:{TLINE_THUMB_H}",
                "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
            ]
            data = check_output_hidden(cmd)
            if not data:
                return None
            img = Image.open(io.BytesIO(data))
            return ctk.CTkImage(light_image=img, dark_image=img,
                               size=(TLINE_THUMB_W, TLINE_THUMB_H))
        except Exception:
            return None

    result = _try_extract(0.1) or _try_extract(0.0)
    if result:
        TLINE_THUMB_CACHE[seg_path] = result
    return result


def _build_clip_grid(parent, out_path: Path):
    global _tline_buttons
    _tline_buttons = []

    for widget in parent.winfo_children():
        widget.destroy()

    cols = TLINE_COLS
    for display_pos, orig_idx in enumerate(_timeline_order):
        meta = timeline_seg_meta[orig_idx]
        row  = display_pos // cols
        col  = display_pos % cols

        is_outro = str(meta.get("label", "")).startswith("Outro:")
        is_vorspann = str(meta.get("label", "")).startswith("Vorspann:")

        border_color = ("gray55", "gray35")
        if is_outro:
            border_color = ("#c0851f", "#c0851f")
        elif is_vorspann:
            border_color = ("#7a4fc0", "#7a4fc0")

        frm = ctk.CTkFrame(
            parent, corner_radius=8,
            border_width=2,
            border_color=border_color,
            fg_color=("gray88", "gray18")
        )
        frm.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        img = TLINE_THUMB_CACHE.get(meta["path"])
        btn = ctk.CTkButton(
            frm, image=img,
            text="" if img else "⏳",
            width=TLINE_THUMB_W, height=TLINE_THUMB_H,
            corner_radius=4,
            fg_color="transparent",
            hover_color=("gray75", "gray30"),
        )
        btn.pack(padx=3, pady=(4, 0))

        # Outro-/Vorspann-Label / Sperrhinweis
        if is_outro:
            ctk.CTkLabel(
                frm, text=tr("🔒 Outro"),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=("#c0851f", "#e0a53f"),
            ).pack(pady=(0, 3))
        elif is_vorspann:
            ctk.CTkLabel(
                frm, text=tr("🔒 Pre-Intro"),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=("#7a4fc0", "#b090e0"),
            ).pack(pady=(0, 3))
        else:
            # Nur normale Clips sind verschiebbar
            idx_copy = display_pos
            btn.bind("<ButtonPress-1>",   lambda e, i=idx_copy: _drag_start(i))
            btn.bind("<B1-Motion>",        lambda e: _drag_motion(e))
            btn.bind("<ButtonRelease-1>",  lambda e: _drag_end_motion(e, out_path))
            frm.bind("<ButtonPress-1>",    lambda e, i=idx_copy: _drag_start(i))
            frm.bind("<B1-Motion>",         lambda e: _drag_motion(e))
            frm.bind("<ButtonRelease-1>",   lambda e: _drag_end_motion(e, out_path))

        # Clip entfernen (wird durch einen neuen, gleich langen Clip ersetzt).
        # Nur für echte Clips/Fotos — Intro, Pre-Intro und Outro bleiben geschützt.
        if meta.get("src") and not (is_outro or is_vorspann):
            ctk.CTkButton(
                frm, text=tr("✕  Entfernen"), width=TLINE_THUMB_W, height=22,
                font=ctk.CTkFont(size=11),
                fg_color=("#c0392b", "#8B0000"), hover_color=("#e74c3c", "#B22222"),
                corner_radius=4,
                command=lambda i=display_pos: _replace_clip(i, out_path),
            ).pack(padx=3, pady=(2, 5))

        _tline_buttons.append(btn)


# ---------------- Clip entfernen / ersetzen ----------------
_replace_busy: bool = False      # ein Ersatz-Clip wird gerade gerendert
_reorder_busy: bool = False      # "Übernehmen" läuft gerade
_replace_counter: int = 0


def _editor_status(text: str):
    """Schreibt in die Statuszeile des Editors."""
    try:
        sb = outer._status_ed  # type: ignore[attr-defined]
        sb.configure(state="normal")
        sb.delete("1.0", "end")
        sb.insert("1.0", text)
        sb.configure(state="disabled")
    except Exception:
        pass


def _replace_clip(display_pos: int, out_path: Path):
    """Entfernt einen Clip und füllt die Lücke mit einem neuen Clip gleicher Länge."""
    global _replace_busy, _replace_counter

    if _replace_busy or _reorder_busy:
        _editor_status("Bitte kurz warten …")
        return
    if display_pos < 0 or display_pos >= len(_timeline_order):
        return

    orig_idx = _timeline_order[display_pos]
    meta = timeline_seg_meta[orig_idx]
    if not meta.get("src"):
        return

    ri = timeline_render_info
    pool = ri.get("replace_pool") or []
    if not pool:
        _editor_status(tr("Keine Ersatz-Clips verfügbar"))
        return

    vf = build_vf_base(ri.get("out_w", 1920), ri.get("out_h", 1080),
                       "shorts" if ri.get("is_shorts") else "normal")
    used = {m.get("src") for m in timeline_seg_meta if m.get("src")}
    old_seg = Path(meta["path"])
    old_src = meta["src"]

    _replace_counter += 1
    new_seg = SEGMENTS_DIR / f"seg_r{_replace_counter:04d}.mp4"

    _replace_busy = True
    _editor_status(tr("Ersetze Clip … (wird neu gerendert)"))

    def worker():
        try:
            new_meta = build_replacement_segment(old_seg, old_src, used, pool, vf, new_seg)
            extract_tline_thumbnail(new_meta["path"])
            app.after(0, lambda: _apply_replacement(orig_idx, new_meta, out_path))
        except Exception as e:
            app.after(0, lambda err=e: _replacement_failed(err))

    threading.Thread(target=worker, daemon=True).start()


def _apply_replacement(orig_idx: int, new_meta: dict, out_path: Path):
    global _replace_busy, _tline_changed
    _replace_busy = False
    if not in_editor_mode or orig_idx >= len(timeline_seg_meta):
        try:
            Path(new_meta["path"]).unlink()
        except Exception:
            pass
        return

    meta = timeline_seg_meta[orig_idx]
    if "orig" not in meta:       # Ausgangszustand merken, damit "Abbrechen" ihn zurückholen kann
        meta["orig"] = {k: meta.get(k) for k in ("path", "label", "src", "kind")}
    meta.update(new_meta)

    _tline_changed = True
    try:
        outer._btn_reorder.configure(state="normal")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        _build_clip_grid(outer._grid_frame, out_path)  # type: ignore[attr-defined]
    except Exception:
        pass
    _editor_status(tr("Clip ersetzt — mit „Übernehmen“ speichern"))


def _replacement_failed(err: Exception):
    global _replace_busy
    _replace_busy = False
    _editor_status(tr("Clip konnte nicht ersetzt werden: ") + str(err))


def _restore_replaced_clips():
    """Macht alle noch nicht übernommenen Clip-Ersetzungen rückgängig."""
    for m in timeline_seg_meta:
        orig = m.pop("orig", None)
        if orig:
            m.update({k: v for k, v in orig.items() if v is not None})


_drag_ghost: Optional[ctk.CTkLabel] = None


def _destroy_ghost():
    global _drag_ghost
    if _drag_ghost is not None:
        try:
            _drag_ghost.place_forget()
            _drag_ghost.destroy()
        except Exception:
            pass
        _drag_ghost = None


def _drag_start(idx: int):
    global _drag_source_idx, _drag_ghost
    _drag_source_idx = idx
    _destroy_ghost()

    # Mauszeiger zu Verschiebe-Cursor
    try:
        app.configure(cursor="fleur")
    except Exception:
        pass

    # Quell-Frame grau markieren
    try:
        _tline_buttons[idx].master.configure(border_color="#888888", border_width=2)
    except Exception:
        pass

    # Schwebendes Label erstellen
    num = _timeline_order[idx] + 1 if idx < len(_timeline_order) else idx + 1
    ghost = ctk.CTkLabel(
        app,
        text=f"  {num}  ",
        font=ctk.CTkFont(size=13, weight="bold"),
        fg_color="#1f6aa5",
        text_color="white",
        corner_radius=8,
        width=44, height=32,
    )
    _drag_ghost = ghost


def _drag_motion(event):
    global _drag_highlight_idx

    if _drag_source_idx is None:
        return

    # Ghost positionieren
    if _drag_ghost is not None:
        try:
            _drag_ghost.place(
                x=event.x_root - app.winfo_rootx() - 22,
                y=event.y_root - app.winfo_rooty() - 40
            )
        except Exception:
            pass

    # Ziel-Frame ermitteln und blau hervorheben
    widget_under = event.widget.winfo_containing(event.x_root, event.y_root)
    new_idx = _find_btn_idx(widget_under)
    # Outro/Vorspann sind kein gültiges Ziel
    if new_idx == _drag_source_idx or (new_idx is not None and _is_fixed_pos(new_idx)):
        new_idx = None
    if new_idx != _drag_highlight_idx:
        if _drag_highlight_idx is not None:
            try:
                if _is_outro_pos(_drag_highlight_idx):
                    _tline_buttons[_drag_highlight_idx].master.configure(
                        border_color=("#c0851f", "#c0851f"), border_width=2)
                elif _is_vorspann_pos(_drag_highlight_idx):
                    _tline_buttons[_drag_highlight_idx].master.configure(
                        border_color=("#7a4fc0", "#7a4fc0"), border_width=2)
                else:
                    _tline_buttons[_drag_highlight_idx].master.configure(
                        border_color=("gray55", "gray35"), border_width=2)
            except Exception:
                pass
        _drag_highlight_idx = new_idx
        if new_idx is not None:
            try:
                _tline_buttons[new_idx].master.configure(
                    border_color="#1f6aa5", border_width=3)
            except Exception:
                pass


def _find_btn_idx(widget) -> Optional[int]:
    if widget is None:
        return None
    w = widget
    for _ in range(4):
        for i, btn in enumerate(_tline_buttons):
            if w is btn or w is btn.master:
                return i
        try:
            w = w.master
        except Exception:
            break
    return None


def _is_outro_pos(display_pos: int) -> bool:
    """Prüft ob die Anzeigeposition das Outro-Segment ist."""
    if display_pos < 0 or display_pos >= len(_timeline_order):
        return False
    orig_idx = _timeline_order[display_pos]
    return str(timeline_seg_meta[orig_idx].get("label", "")).startswith("Outro:")


def _is_vorspann_pos(display_pos: int) -> bool:
    """Prüft ob die Anzeigeposition das Vorspann-Segment ist."""
    if display_pos < 0 or display_pos >= len(_timeline_order):
        return False
    orig_idx = _timeline_order[display_pos]
    return str(timeline_seg_meta[orig_idx].get("label", "")).startswith("Vorspann:")


def _is_fixed_pos(display_pos: int) -> bool:
    """Prüft ob die Position fixiert ist (Outro oder Vorspann) — nicht verschiebbar."""
    return _is_outro_pos(display_pos) or _is_vorspann_pos(display_pos)


def _reset_grid_borders():
    """Setzt alle Rahmen zurück — Outro/Vorspann behalten ihre Farbe."""
    for i, btn in enumerate(_tline_buttons):
        try:
            if _is_outro_pos(i):
                btn.master.configure(border_color=("#c0851f", "#c0851f"), border_width=2)
            elif _is_vorspann_pos(i):
                btn.master.configure(border_color=("#7a4fc0", "#7a4fc0"), border_width=2)
            else:
                btn.master.configure(border_color=("gray55", "gray35"), border_width=2)
        except Exception:
            pass


def _drag_end_motion(event, out_path: Path):
    global _drag_source_idx, _drag_highlight_idx, _timeline_order, _tline_changed

    _destroy_ghost()

    # Cursor immer zurücksetzen
    try:
        app.configure(cursor="")
    except Exception:
        pass

    src = _drag_source_idx
    _drag_source_idx = None

    widget_under = event.widget.winfo_containing(event.x_root, event.y_root)
    tgt = _find_btn_idx(widget_under)
    _drag_highlight_idx = None

    _reset_grid_borders()

    if src is None or tgt is None or src == tgt:
        return

    # Outro/Vorspann dürfen weder gezogen noch überschrieben werden
    if _is_fixed_pos(src) or _is_fixed_pos(tgt):
        return

    _timeline_order[src], _timeline_order[tgt] = _timeline_order[tgt], _timeline_order[src]
    _tline_changed = True

    try:
        outer._btn_reorder.configure(state="normal")  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        parent = outer._grid_frame  # type: ignore[attr-defined]
        _build_clip_grid(parent, out_path)
    except Exception:
        pass


def _commit_applied_order(applied_order: list[int]):
    """Schreibt den soeben ins Video übernommenen Stand fest in die Segmentliste.
    Dadurch zeigt der Editor beim nächsten Öffnen die tatsächliche Reihenfolge des Videos."""
    global timeline_seg_meta, _timeline_order, _tline_changed
    current = list(_timeline_order)
    new_meta = [timeline_seg_meta[i] for i in applied_order]
    for m in new_meta:
        m.pop("orig", None)                   # Ersetzungen sind jetzt bestätigt
    pos = {old_idx: new_pos for new_pos, old_idx in enumerate(applied_order)}
    timeline_seg_meta = new_meta
    _timeline_order = [pos[j] for j in current]   # evtl. inzwischen Verschobenes bleibt sichtbar
    _tline_changed = (current != applied_order)


def _finish_reorder_ok(applied_order: list[int]):
    global _reorder_busy
    _reorder_busy = False
    _commit_applied_order(applied_order)
    try:
        outer._btn_reorder.configure(state="normal" if _tline_changed else "disabled")  # type: ignore[attr-defined]
    except Exception:
        pass


def _finish_reorder_fail():
    global _reorder_busy
    _reorder_busy = False
    try:
        outer._btn_reorder.configure(state="normal")  # type: ignore[attr-defined]
    except Exception:
        pass


def _do_reorder(audio_path: str, out_path: Path):
    global _reorder_busy
    if _replace_busy:
        _editor_status(tr("Bitte warten — Clip wird gerade ersetzt …"))
        return
    if _reorder_busy:
        return
    _reorder_busy = True

    try:
        outer._btn_reorder.configure(state="disabled")  # type: ignore[attr-defined]
    except Exception:
        pass

    ri = timeline_render_info
    applied_order = list(_timeline_order)   # eingefrorener Stand, der jetzt geschrieben wird

    def _set_status_ed(text: str):
        try:
            sb = outer._status_ed  # type: ignore[attr-defined]
            sb.configure(state="normal")
            sb.delete("1.0", "end")
            sb.insert("1.0", text)
            sb.configure(state="disabled")
        except Exception:
            ui_status(text)

    def _set_progress_ed(val: float):
        try:
            outer._progressbar_ed.set(clamp(float(val), 0.0, 1.0))  # type: ignore[attr-defined]
        except Exception:
            pass

    # Sofort Feedback geben
    _set_status_ed("Verarbeite neue Reihenfolge…")
    _set_progress_ed(0.05)

    def worker():
        try:
            ordered_segs = [Path(timeline_seg_meta[i]["path"]) for i in applied_order]
            missing = [s for s in ordered_segs if not s.exists()]
            if missing:
                raise RuntimeError(f"Segment fehlt: {missing[0].name}")

            app.after(0, _set_status_ed, "Klebe Segmente zusammen…")
            app.after(0, _set_progress_ed, 0.25)

            temp_dir    = Path(tempfile.mkdtemp(prefix="reorder_"))
            concat_list = temp_dir / "concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for s in ordered_segs:
                    f.write(f"file '{ffmpeg_concat_escape(s.as_posix())}'\n")

            video_only = temp_dir / "video_only.mp4"
            run_hidden([
                ffmpeg_cmd(), "-y", "-hide_banner",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy", str(video_only)
            ])

            app.after(0, _set_status_ed, "Mische Audio…")
            app.after(0, _set_progress_ed, 0.6)

            music_dur      = ri.get("music_dur", ffprobe_duration(Path(audio_path)))
            shorts_limited = ri.get("shorts_limited", False)

            if shorts_limited:
                fade_start, fade_d = compute_fade(music_dur)
                _ms = float(ri.get("music_start", 0.0))
                a_chain = (
                    f"atrim={_ms:.4f}:{_ms + music_dur:.4f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={SHORTS_FADE_IN_SEC:.2f},"
                    f"afade=t=out:st={fade_start:.4f}:d={fade_d:.4f}"
                )
            else:
                a_chain = f"atrim=0:{music_dur:.4f},asetpts=PTS-STARTPTS"

            # Vorspann-Audio nur wenn das Vorspann-Segment das ERSTE ist (nicht verschoben)
            # UND die Original-Vorspann-Quelle noch existiert und Ton hat.
            first_seg = timeline_seg_meta[applied_order[0]]
            first_label = first_seg.get("label", "")
            vorspann_audio_path = None
            music_delay_ms = 0
            if first_label.startswith("Vorspann:"):
                orig_vorspann = ri.get("vorspann_src")
                if orig_vorspann:
                    orig_vorspann_path = Path(orig_vorspann)
                    if orig_vorspann_path.exists() and has_audio_stream(orig_vorspann_path):
                        vorspann_audio_path = orig_vorspann_path
                        music_delay_ms = round(ri.get("vorspann_len", 0.0) * 1000)

            # Outro-Audio nur wenn das Outro-Segment das LETZTE ist (nicht verschoben)
            # UND die Original-Outro-Quelle noch existiert und Ton hat.
            # Delay = Pre-Intro-Länge + Musiklänge, damit der Zeitpunkt auch mit aktivem
            # Pre-Intro stimmt — nicht nur music_dur.
            last_seg = timeline_seg_meta[applied_order[-1]]
            last_label = last_seg.get("label", "")
            outro_audio_path = None
            outro_delay_ms = 0
            if last_label.startswith("Outro:"):
                orig_outro = ri.get("outro_src")
                if orig_outro:
                    orig_outro_path = Path(orig_outro)
                    if orig_outro_path.exists() and has_audio_stream(orig_outro_path):
                        outro_audio_path = orig_outro_path
                        outro_delay_ms = music_delay_ms + round(music_dur * 1000)

            temp_out = temp_dir / "final.mp4"

            # Video-Filter (Titel-Overlay) vorbereiten
            use_title = bool(ri.get("title_overlay") and ri.get("title_text"))
            if use_title:
                txt_filter = build_title_overlay_filter(
                    ri["title_text"],
                    ri.get("out_w", 1920),
                    ri.get("out_h", 1080),
                    intro_offset=ri.get("intro_len", 0.0)
                )

            # ----- Filter-Complex dynamisch zusammenbauen (wie im Haupt-Render) -----
            inputs = [str(video_only), str(audio_path)]
            next_idx = 2
            outro_idx = None
            vorspann_idx = None
            if outro_audio_path is not None:
                inputs.append(str(outro_audio_path))
                outro_idx = next_idx
                next_idx += 1
            if vorspann_audio_path is not None:
                inputs.append(str(vorspann_audio_path))
                vorspann_idx = next_idx
                next_idx += 1

            if music_delay_ms > 0:
                mus_chain = f"[1:a]{a_chain},adelay={music_delay_ms}|{music_delay_ms}[mus]"
            else:
                mus_chain = f"[1:a]{a_chain}[mus]"

            audio_parts = [mus_chain]
            mix_labels = ["mus"]
            if outro_idx is not None:
                audio_parts.append(f"[{outro_idx}:a]adelay={outro_delay_ms}|{outro_delay_ms}[outro]")
                mix_labels.append("outro")
            if vorspann_idx is not None:
                audio_parts.append(f"[{vorspann_idx}:a]anull[vorspann]")
                mix_labels.append("vorspann")

            if len(mix_labels) > 1:
                mix_inputs = "".join(f"[{lbl}]" for lbl in mix_labels)
                audio_parts.append(
                    f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[aout]"
                )
            else:
                audio_parts = [mus_chain.replace("[mus]", "[aout]")]

            a_filter = ";".join(p for p in audio_parts if p)

            if use_title:
                filter_complex = f"[0:v]{txt_filter}[vt];{a_filter}"
                video_map = "[vt]"
                vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                                "-crf", "18", "-preset", "veryfast", "-fps_mode", "cfr"]
            else:
                filter_complex = a_filter
                video_map = "0:v:0"
                vcodec_args = ["-c:v", "copy"]

            cmd = [ffmpeg_cmd(), "-y", "-hide_banner"]
            for inp in inputs:
                cmd += ["-i", inp]
            cmd += ["-filter_complex", filter_complex, "-map", video_map, "-map", "[aout]"]
            cmd += vcodec_args
            cmd += ["-c:a", "aac", "-b:a", "192k", str(temp_out)]
            run_hidden(cmd)

            shutil.copyfile(temp_out, out_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            app.after(0, _set_progress_ed, 1.0)
            app.after(0, play_done_sound)
            app.after(0, _set_status_ed, tr("Fertig: ") + out_path.name)
            app.after(0, lambda: _finish_reorder_ok(applied_order))
            # Nach Übernehmen: Abbrechen-Button zu "Zurück" ändern (kein Reset der Reihenfolge mehr)
            app.after(0, lambda: outer._btn_abbrechen.configure(  # type: ignore[attr-defined]
                text=tr("✕  Zurück"),
                command=_cancel_editor_keep_order
            ))
        except Exception as e:
            app.after(0, _set_progress_ed, 0.0)
            app.after(0, _set_status_ed, tr("Fehler: ") + str(e))
            app.after(0, _finish_reorder_fail)

    threading.Thread(target=worker, daemon=True).start()


# ── Fenster-Schließen Handler
def on_closing():
    if in_editor_mode:
        ok = messagebox.askokcancel(
            tr("Beenden"),
            tr("Du befindest dich im Editor. Wirklich beenden?")
        )
        if not ok:
            return
    app.destroy()

# Klick-Hilfe: Titel + ausführliche Erklärung je Bereich
HELP_TOPICS = {
"de": {
 "music": ("Musik wählen", "Hier wählst du die Musikdatei (MP3 oder WAV). Aus dieser Datei entsteht dein Video: Das Programm analysiert den Beat und legt alle Schnitte genau darauf. Die Länge des Videos entspricht der Länge der Musik.\n\nDer Name des fertigen Videos wird aus dem Dateinamen gebildet (z. B. „MeinSong.mp3“ → „MeinSong.mp4“). Auch der Titel, der optional im Video eingeblendet wird, kommt von dort.\n\nOhne Musik kann nicht gerendert werden."),
 "reset": ("Reset", "Setzt alle Einstellungen auf den Anfangszustand zurück: Musik, Formate, Schnitt, Pool, Stimmung und alle Schalter.\n\nIst ein Zwischenspeicher (Cache) vorhanden, fragt das Programm, ob er ebenfalls geleert werden soll. Der Cache beschleunigt wiederholtes Rendern, kann aber Speicherplatz belegen. Deine fertigen Videos im Ordner output/ bleiben immer erhalten.\n\nWährend ein Render läuft, ist Reset gesperrt."),
 "help": ("Hilfe (?)", "Öffnet ein kleines Menü:\n• Klick-Hilfe: Du klickst danach auf einen beliebigen Bereich und bekommst eine ausführliche Erklärung.\n• Kurzanleitung und PDF-Anleitung.\n• Sprache: Deutsch, English oder Español (danach Programm neu starten)."),
 "fmt": ("Format", "Hier legst du fest, welche Videoformate erzeugt werden. Du kannst mehrere gleichzeitig wählen – jedes Format wird als eigene Datei im Ordner output/ gespeichert. Mindestens ein Format muss aktiv bleiben.\n\nStandardmäßig sind 16:9 und der 58-Sekunden-Short aktiv. Mehr Formate bedeuten längere Renderzeit."),
 "fmt_normal": ("Normal 16:9", "Das klassische Querformat, ideal für YouTube und Computer-Bildschirme. Das Video hat die volle Länge deiner Musik und kann Pre-Intro, Intro, Outro und Titel enthalten.\n\nDateiname: Musiktitel.mp4\nDies ist das Format, das der Clip-Editor („Clips verschieben“) bearbeitet."),
 "fmt_shorts": ("Normal 9:16", "Hochformat in voller Länge deiner Musik – gedacht für Handy-Wiedergabe, wenn das ganze Lied gezeigt werden soll. Pre-Intro, Intro, Outro und Titel sind möglich.\n\nDateiname: Musiktitel_9x16.mp4"),
 "fmt_short58": ("Short 9:16 · 58 s", "Ein kurzes Hochformat-Video von genau 58 Sekunden für Instagram Reels, YouTube Shorts und TikTok.\n\nDas Programm sucht automatisch den stärksten Abschnitt deines Songs (meist den Refrain) und schneidet dieses Stück. Bild und Ton werden am Anfang weich eingeblendet und am Ende ausgeblendet. Der Short entsteht immer ohne Pre-Intro, Intro, Outro und Titel, damit er genau 58 Sekunden lang bleibt.\n\nDateiname: Musiktitel_Short.mp4"),
 "speed": ("Schnitt", "Bestimmt, wie schnell die Clips wechseln. Alle Schnitte werden auf den Beat der Musik gelegt.\n\n• Sanft: etwa alle 3,5 Sekunden – ruhig, atmosphärisch\n• Langsam: etwa alle 2,2 Sekunden – cineastisch\n• Normal: etwa alle 1,7 Sekunden – ausgewogen (Standard)\n• Schnell: etwa alle 0,95 Sekunden – energetisch\n• Random: jeder Schnitt zufällig zwischen 0,95 und 3,5 Sekunden\n\nFür Balladen passt Sanft oder Langsam, für Tanzmusik Schnell."),
 "pool": ("Interner Clip-Pool", "Der interne Pool nutzt deine eigenen Videoclips aus den Stimmungs-Ordnern im Programmordner (clips/traurig, clips/neutral, clips/gluecklich, clips/animations).\n\nWichtig: Das Programm wird ohne Clips ausgeliefert. Du musst diese Ordner selbst mit Videos füllen, sonst gibt es nichts zu schneiden.\n\nHier wählst du die Stimmung, wie viele Clips verwendet werden und ob du einzelne Clips selbst auswählst."),
 "mood": ("Stimmung", "Wähle, aus welchen Stimmungs-Ordnern die Clips kommen: Traurig, Neutral, Glücklich und/oder Animation. Du kannst mehrere kombinieren.\n\nIst keine Stimmung angehakt, werden alle Ordner verwendet. Passe die Stimmung zur Musik, damit Bild und Ton zusammenwirken."),
 "pool_count": ("Anzahl Clips im Pool", "Legt fest, wie viele Clips aus dem Pool verwendet werden.\n\n• Automatisch: alle verfügbaren Clips der gewählten Stimmung\n• 10 / 20 / 30 Clips: eine zufällige Auswahl dieser Größe\n\nEine kleinere Auswahl gibt dem Video einen einheitlicheren Look, eine größere mehr Abwechslung."),
 "manual": ("Manuelle Auswahl", "Öffnet ein Fenster mit Vorschaubildern aller Clips der gewählten Stimmung. Mit einem Klick wählst du Clips aus oder ab (blauer Rahmen = ausgewählt). Mit „Alle“ und „Keine“ gehst du schneller.\n\nNur die ausgewählten Clips werden verwendet. „OK“ wird aktiv, sobald mindestens ein Clip gewählt ist."),
 "sw_pre": ("Pre-Intro", "Ein eigenes Video mit eigenem Ton, das VOR der Musik läuft – zum Beispiel eine Begrüßung oder ein Logo-Clip. Die Videodatei legst du in den Ordner pre-intro/; liegen mehrere darin, wird zufällig eine gewählt.\n\nDas Video wird dadurch länger als dein Song, weil das Pre-Intro vorangestellt wird. Standard: aus.\nIm 58-s-Short wird das Pre-Intro nie verwendet."),
 "sw_intro": ("Intro", "Ein stummes Video am Anfang, unter dem die Musik schon läuft – zum Beispiel ein animierter Vorspann. Die Datei legst du in den Ordner intro/; bei mehreren wird zufällig eine gewählt.\n\nDas Intro zählt zur Songlänge: Danach folgen die normalen Clips, bis die Musik zu Ende ist. Im 58-s-Short wird das Intro nie verwendet."),
 "sw_outro": ("Outro", "Ein eigenes Video mit eigenem Ton, das NACH der Musik angehängt wird – zum Beispiel ein Abspann oder ein Hinweis zum Abonnieren. Die Datei legst du in den Ordner outro/; bei mehreren wird zufällig eine gewählt.\n\nDas Video wird dadurch länger als der Song. Im Clip-Editor bleibt das Outro immer am Ende. Im 58-s-Short wird das Outro nie verwendet."),
 "sw_title": ("Titel", "Blendet den Musiktitel unten links im Video ein (Schrift Arial Bold, mit leichtem Schatten). Der Titel stammt aus dem Dateinamen der Musik; Sonderzeichen wie Doppelpunkte werden entfernt.\n\nEr erscheint nach dem Intro, blendet weich ein und nach einigen Sekunden wieder aus. Im 58-s-Short gibt es nie einen Titel."),
 "sw_pool": ("Interner Pool (Schalter)", "Schaltet die Clips aus den Stimmungs-Ordnern ein oder aus. Ist er aus, werden nur deine eigenen Clips und Fotos verwendet. Sind interner Pool und eigene Clips beide an, ist das der Mix-Modus: eigene Clips werden bevorzugt, der Pool füllt auf."),
 "sw_ext": ("Externe Clips", "Verwendet Videos aus einem Ordner deiner Wahl, zum Beispiel Material, das nur zu diesem Song passt. Den Ordner wählst du mit „Externe Clips wählen…“.\n\nSind interner Pool und externe Clips zusammen aktiv, werden externe Clips bevorzugt (etwa 70 %) und interne füllen auf. Sind es nur externe Clips, werden sie bei Bedarf wiederholt."),
 "sw_photos": ("Externe Fotos", "Verwendet Fotos (JPG, PNG, WEBP) aus einem Ordner deiner Wahl als stehende Bilder im Video. Den Ordner wählst du mit „Externe Fotos wählen…“.\n\nFotos werden nicht für sehr kurze Schnitte genutzt, wenn genug Videoclips vorhanden sind."),
 "short_url": ("Short per URL", "Erstellt einen Short aus einem YouTube-Video: Kopiere zuerst den Link des Videos (Adressleiste im Browser → kopieren), dann klicke diesen Button.\n\nDas Programm lädt das Video, wählt automatisch den stärksten Abschnitt und speichert einen 45-Sekunden-Short im Hochformat 9:16 mit Originalton und weichem Ein- und Ausblenden im Ordner output/Shorts.\n\nBeim ersten Mal wird eine kleine Zusatzkomponente heruntergeladen (Internet nötig). Das Video muss mindestens 45 Sekunden lang sein. Bitte nur Videos verwenden, für die du die Rechte hast."),
 "btn_ext": ("Externe Clips wählen…", "Wählt den Ordner mit deinen eigenen Videoclips und aktiviert „Externe Clips“. Brichst du die Auswahl ab, wird die Option wieder ausgeschaltet."),
 "btn_photos": ("Externe Fotos wählen…", "Wählt den Ordner mit deinen eigenen Fotos und aktiviert „Externe Fotos“."),
 "count": ("Anzahl", "Zeigt, wie viele Clips (und bei aktiven Fotos wie viele Fotos) mit den aktuellen Einstellungen zur Verfügung stehen. Steht hier 0, gibt es nichts zu schneiden – dann fehlen Clips in den Ordnern oder die Quelle ist ausgeschaltet."),
 "sources": ("Quellen & Optionen", "Hier legst du fest, woher Bild und Zusatz-Inhalte kommen: Pre-Intro, Intro, Outro und Titel sowie interner Pool, externe Clips und externe Fotos. Fahre mit der Maus über einen einzelnen Schalter und klicke darauf, um ihn genauer erklärt zu bekommen."),
 "render": ("Render", "Startet die Erstellung deines Videos. Das Programm analysiert die Musik, setzt die Schnitte auf den Beat, rendert alle Segmente und fügt Bild und Ton zusammen. Für jedes gewählte Format entsteht eine Datei im Ordner output/.\n\nFortschrittsbalken und Statuszeile zeigen den Stand, ein Klang meldet das Ende. Danach kannst du das Video anschauen oder mit „Clips verschieben“ nachbearbeiten. Mehrere Formate dauern entsprechend länger."),
 "edit": ("Clips verschieben", "Wird nach dem Rendern aktiv und öffnet den Clip-Editor. Dort siehst du alle Clips deines Videos als Vorschaubilder und kannst sie neu anordnen oder unpassende Clips durch andere ersetzen – ohne das Video komplett neu zu rendern.\n\nBearbeitet wird das erste ausgewählte Format, normalerweise das 16:9-Video."),
 "status": ("Statuszeile", "Zeigt, was das Programm gerade tut oder getan hat – zum Beispiel „Rendere Segmente…“, „bereit“ oder Hinweise wie „Mindestens ein Format muss aktiv sein“. Bei Fehlern steht hier die Ursache."),
 "watch": ("Video anschauen", "Öffnet das zuletzt fertiggestellte Video im Standard-Videoplayer deines Computers. Gibt es noch kein Video, zeigt die Statuszeile einen Hinweis."),
 "outfolder": ("Output-Ordner", "Öffnet den Ordner output/ im Explorer. Dort liegen alle fertigen Videos (und Shorts per URL im Unterordner Shorts)."),
 "youtube": ("YouTube", "Öffnet die Upload-Seite von YouTube im Browser. Dein Video musst du dort noch auswählen und hochladen – am einfachsten über den Output-Ordner."),
 "ed_cancel": ("Abbrechen / Zurück", "Verlässt den Clip-Editor und kehrt zum Hauptfenster zurück. Alle Änderungen, die du noch nicht mit „Übernehmen“ gespeichert hast, werden verworfen – entfernte Clips kommen zurück, die Reihenfolge wird zurückgesetzt."),
 "ed_apply": ("Übernehmen", "Speichert deine Änderungen (neue Reihenfolge und ersetzte Clips) im Video. Das geht schnell, weil nur der Zusammenfüge-Schritt neu läuft. Der Button ist erst aktiv, wenn du etwas geändert hast. Während des Speicherns zeigt die Statuszeile den Fortschritt."),
 "ed_grid": ("Clip-Übersicht", "Jede Kachel ist ein Clip deines Videos, in der Reihenfolge, in der er vorkommt.\n\n• Anordnen: Kachel anklicken, gedrückt halten und auf eine andere Kachel ziehen – beide tauschen die Plätze.\n• Entfernen: Mit „✕ Entfernen“ unter der Kachel wird der Clip durch einen zufälligen anderen Clip genau gleicher Länge ersetzt, bevorzugt einen, der noch nicht im Video vorkommt. Das Video bleibt dadurch exakt so lang wie dein Song.\n• Gesperrte Kacheln (Schloss) sind Pre-Intro und Outro. Sie bleiben immer am Anfang bzw. Ende.\n\nErst „Übernehmen“ macht die Änderungen endgültig."),
 "ed_remove": ("✕ Entfernen", "Ersetzt diesen Clip durch einen zufälligen anderen Clip genau gleicher Länge, damit das Video so lang bleibt wie der Song. Das Programm bevorzugt Clips, die noch nicht im Video vorkommen, und rendert nur diesen einen Ausschnitt neu. Gefällt dir der neue Clip nicht, klicke noch einmal auf Entfernen.\n\nMit „Abbrechen“ stellst du alle ersetzten Clips wieder her, mit „Übernehmen“ speicherst du sie."),
 "ed_lock": ("Gesperrter Clip", "Pre-Intro und Outro sind fest: Das Pre-Intro bleibt immer am Anfang, das Outro immer am Ende. Sie lassen sich weder verschieben noch entfernen, damit ihr eigener Ton sauber vor und nach der Musik abläuft."),
 "ed_status": ("Statuszeile", "Zeigt im Editor den Stand: zum Beispiel „Clip ersetzt – mit Übernehmen speichern“, „Ersetze Clip…“ oder den Fortschritt beim Speichern."),
},
"en": {
 "music": ("Choose music", "Here you choose the music file (MP3 or WAV). Your video is built from it: the program analyzes the beat and places all cuts exactly on it. The video length equals the length of the music.\n\nThe name of the finished video comes from the file name (e.g. “MySong.mp3” → “MySong.mp4”). The optional on-screen title comes from there too.\n\nWithout music nothing can be rendered."),
 "reset": ("Reset", "Sets all settings back to their initial state: music, formats, cutting, pool, mood and all switches.\n\nIf a cache exists, the program asks whether to clear it as well. The cache speeds up repeated renders but can take up disk space. Your finished videos in the output/ folder are always kept.\n\nWhile a render is running, Reset is locked."),
 "help": ("Help (?)", "Opens a small menu:\n• Click help: afterwards you click any area and get a detailed explanation.\n• Quick guide and PDF manual.\n• Language: Deutsch, English or Español (restart the program afterwards)."),
 "fmt": ("Format", "Here you decide which video formats are created. You can choose several at once – each format is saved as its own file in the output/ folder. At least one format must stay active.\n\nBy default 16:9 and the 58-second short are active. More formats mean longer rendering."),
 "fmt_normal": ("Normal 16:9", "The classic landscape format, ideal for YouTube and computer screens. The video has the full length of your music and can contain pre-intro, intro, outro and title.\n\nFile name: Title.mp4\nThis is the format the clip editor (“Edit clips”) works on."),
 "fmt_shorts": ("Normal 9:16", "Portrait at the full length of your music – meant for phone playback when the whole song should be shown. Pre-intro, intro, outro and title are possible.\n\nFile name: Title_9x16.mp4"),
 "fmt_short58": ("Short 9:16 · 58 s", "A short portrait video of exactly 58 seconds for Instagram Reels, YouTube Shorts and TikTok.\n\nThe program automatically finds the strongest part of your song (usually the chorus) and cuts that piece. Picture and sound fade in softly at the start and out at the end. The short is always created without pre-intro, intro, outro and title so it stays exactly 58 seconds long.\n\nFile name: Title_Short.mp4"),
 "speed": ("Cutting", "Determines how fast the clips change. All cuts are placed on the beat of the music.\n\n• Gentle: about every 3.5 seconds – calm, atmospheric\n• Slow: about every 2.2 seconds – cinematic\n• Normal: about every 1.7 seconds – balanced (default)\n• Fast: about every 0.95 seconds – energetic\n• Random: every cut random between 0.95 and 3.5 seconds\n\nFor ballads Gentle or Slow fits, for dance music Fast."),
 "pool": ("Internal clip pool", "The internal pool uses your own video clips from the mood folders in the program folder (clips/traurig, clips/neutral, clips/gluecklich, clips/animations).\n\nImportant: the program ships without clips. You must fill these folders with videos yourself, otherwise there is nothing to cut.\n\nHere you choose the mood, how many clips are used and whether you pick single clips yourself."),
 "mood": ("Mood", "Choose which mood folders the clips come from: Sad, Neutral, Happy and/or Animation. You can combine several.\n\nIf no mood is ticked, all folders are used. Match the mood to the music so picture and sound work together."),
 "pool_count": ("Number of clips in the pool", "Sets how many clips from the pool are used.\n\n• Automatic: all available clips of the chosen mood\n• 10 / 20 / 30 clips: a random selection of that size\n\nA smaller selection gives the video a more uniform look, a larger one more variety."),
 "manual": ("Manual selection", "Opens a window with thumbnails of all clips of the chosen mood. A click selects or deselects a clip (blue frame = selected). “All” and “None” are quicker.\n\nOnly the selected clips are used. “OK” becomes active as soon as at least one clip is chosen."),
 "sw_pre": ("Pre-intro", "Your own video with its own sound that plays BEFORE the music – for example a greeting or a logo clip. Put the video file in the pre-intro/ folder; if there are several, one is picked at random.\n\nThe video becomes longer than your song because the pre-intro is placed in front. Default: off.\nThe pre-intro is never used in the 58 s short."),
 "sw_intro": ("Intro", "A silent video at the start with the music already playing underneath – for example an animated opener. Put the file in the intro/ folder; if there are several, one is picked at random.\n\nThe intro counts toward the song length: the normal clips follow until the music ends. The intro is never used in the 58 s short."),
 "sw_outro": ("Outro", "Your own video with its own sound that is appended AFTER the music – for example credits or a subscribe reminder. Put the file in the outro/ folder; if there are several, one is picked at random.\n\nThe video becomes longer than the song. In the clip editor the outro always stays at the end. The outro is never used in the 58 s short."),
 "sw_title": ("Title", "Shows the music title at the bottom left of the video (Arial Bold font, soft shadow). The title comes from the music file name; special characters such as colons are removed.\n\nIt appears after the intro, fades in and fades out again after a few seconds. The 58 s short never has a title."),
 "sw_pool": ("Internal pool (switch)", "Switches the clips from the mood folders on or off. If it is off, only your own clips and photos are used. If the internal pool and your own clips are both on, that is mix mode: your own clips are preferred and the pool fills up."),
 "sw_ext": ("External clips", "Uses videos from a folder of your choice, for example material that fits only this song. You choose the folder with “Choose external clips…”.\n\nIf the internal pool and external clips are both active, external clips are preferred (about 70 %) and internal ones fill up. With only external clips, they are repeated when needed."),
 "sw_photos": ("External photos", "Uses photos (JPG, PNG, WEBP) from a folder of your choice as still images in the video. You choose the folder with “Choose external photos…”.\n\nPhotos are not used for very short cuts when enough video clips are available."),
 "short_url": ("Short from URL", "Creates a short from a YouTube video: first copy the video link (address bar in the browser → copy), then click this button.\n\nThe program downloads the video, automatically picks the strongest section and saves a 45-second portrait 9:16 short with the original sound and soft fade in and out in the folder output/Shorts.\n\nThe first time a small extra component is downloaded (internet required). The video must be at least 45 seconds long. Please only use videos you have the rights to."),
 "btn_ext": ("Choose external clips…", "Chooses the folder with your own video clips and switches “External clips” on. If you cancel the selection, the option is switched off again."),
 "btn_photos": ("Choose external photos…", "Chooses the folder with your own photos and switches “External photos” on."),
 "count": ("Count", "Shows how many clips (and, with photos active, how many photos) are available with the current settings. If it says 0, there is nothing to cut – clips are missing in the folders or the source is switched off."),
 "sources": ("Sources & options", "Here you decide where picture and extra content come from: pre-intro, intro, outro and title as well as the internal pool, external clips and external photos. Move the mouse over a single switch and click it to get it explained in detail."),
 "render": ("Render", "Starts creating your video. The program analyzes the music, places the cuts on the beat, renders all segments and joins picture and sound. For every chosen format a file is created in the output/ folder.\n\nThe progress bar and status line show the state, a sound signals the end. Afterwards you can watch the video or refine it with “Edit clips”. Several formats take correspondingly longer."),
 "edit": ("Edit clips", "Becomes active after rendering and opens the clip editor. There you see all clips of your video as thumbnails and can rearrange them or replace unsuitable clips with others – without re-rendering the whole video.\n\nThe first selected format is edited, normally the 16:9 video."),
 "status": ("Status line", "Shows what the program is doing or has done – for example “Rendering segments…”, “ready” or hints such as “At least one format must be active”. For errors the cause appears here."),
 "watch": ("Watch video", "Opens the most recently finished video in your computer's default video player. If there is no video yet, the status line shows a hint."),
 "outfolder": ("Output folder", "Opens the output/ folder in Explorer. All finished videos are there (and shorts from URL in the Shorts subfolder)."),
 "youtube": ("YouTube", "Opens the YouTube upload page in the browser. You still have to select and upload your video there – the easiest way is via the output folder."),
 "ed_cancel": ("Cancel / Back", "Leaves the clip editor and returns to the main window. All changes you have not saved with “Apply” are discarded – removed clips come back and the order is reset."),
 "ed_apply": ("Apply", "Saves your changes (new order and replaced clips) in the video. This is fast because only the joining step runs again. The button is only active once you changed something. While saving, the status line shows the progress."),
 "ed_grid": ("Clip overview", "Each tile is a clip of your video, in the order in which it appears.\n\n• Rearrange: click a tile, hold, and drag it onto another tile – the two swap places.\n• Remove: “✕ Remove” under the tile replaces the clip with a random other clip of exactly the same length, preferably one not yet in the video. The video therefore stays exactly as long as your song.\n• Locked tiles (padlock) are pre-intro and outro. They always stay at the start or end.\n\nOnly “Apply” makes the changes final."),
 "ed_remove": ("✕ Remove", "Replaces this clip with a random other clip of exactly the same length so the video stays as long as the song. The program prefers clips not yet in the video and re-renders only this one section. If you do not like the new clip, click Remove again.\n\n“Cancel” restores all replaced clips, “Apply” saves them."),
 "ed_lock": ("Locked clip", "Pre-intro and outro are fixed: the pre-intro always stays at the start, the outro always at the end. They can be neither moved nor removed so that their own sound plays cleanly before and after the music."),
 "ed_status": ("Status line", "Shows the state in the editor: for example “Clip replaced – save with Apply”, “Replacing clip…” or the progress while saving."),
},
"es": {
 "music": ("Elegir música", "Aquí eliges el archivo de música (MP3 o WAV). Tu vídeo se crea a partir de él: el programa analiza el ritmo y coloca todos los cortes exactamente sobre él. La duración del vídeo es la de la música.\n\nEl nombre del vídeo terminado sale del nombre del archivo (p. ej. «MiCancion.mp3» → «MiCancion.mp4»). El título opcional que se muestra en el vídeo también sale de ahí.\n\nSin música no se puede renderizar."),
 "reset": ("Reiniciar", "Devuelve todos los ajustes al estado inicial: música, formatos, corte, grupo, ánimo y todos los interruptores.\n\nSi hay una caché, el programa pregunta si también debe vaciarse. La caché acelera renderizados repetidos pero puede ocupar espacio. Tus vídeos terminados en la carpeta output/ siempre se conservan.\n\nMientras se renderiza, Reiniciar está bloqueado."),
 "help": ("Ayuda (?)", "Abre un pequeño menú:\n• Ayuda con clic: después haces clic en cualquier zona y recibes una explicación detallada.\n• Guía rápida y manual PDF.\n• Idioma: Deutsch, English o Español (reinicia el programa después)."),
 "fmt": ("Formato", "Aquí decides qué formatos de vídeo se crean. Puedes elegir varios a la vez – cada formato se guarda como archivo propio en la carpeta output/. Al menos un formato debe seguir activo.\n\nPor defecto están activos 16:9 y el short de 58 segundos. Más formatos significan más tiempo de renderizado."),
 "fmt_normal": ("Normal 16:9", "El formato horizontal clásico, ideal para YouTube y pantallas de ordenador. El vídeo dura lo mismo que tu música y puede incluir pre-intro, intro, outro y título.\n\nNombre de archivo: Título.mp4\nEste es el formato que edita el editor de clips («Editar clips»)."),
 "fmt_shorts": ("Normal 9:16", "Vertical con la duración completa de tu música – pensado para verlo en el móvil cuando se quiere mostrar la canción entera. Son posibles pre-intro, intro, outro y título.\n\nNombre de archivo: Título_9x16.mp4"),
 "fmt_short58": ("Short 9:16 · 58 s", "Un vídeo vertical corto de exactamente 58 segundos para Instagram Reels, YouTube Shorts y TikTok.\n\nEl programa busca automáticamente la parte más fuerte de tu canción (normalmente el estribillo) y corta ese fragmento. Imagen y sonido entran con suavidad al principio y salen al final. El short se crea siempre sin pre-intro, intro, outro ni título para que dure exactamente 58 segundos.\n\nNombre de archivo: Título_Short.mp4"),
 "speed": ("Corte", "Determina qué tan rápido cambian los clips. Todos los cortes se colocan sobre el ritmo de la música.\n\n• Suave: aprox. cada 3,5 segundos – tranquilo, atmosférico\n• Lento: aprox. cada 2,2 segundos – cinematográfico\n• Normal: aprox. cada 1,7 segundos – equilibrado (por defecto)\n• Rápido: aprox. cada 0,95 segundos – enérgico\n• Aleatorio: cada corte al azar entre 0,95 y 3,5 segundos\n\nPara baladas va bien Suave o Lento, para música de baile Rápido."),
 "pool": ("Grupo de clips interno", "El grupo interno usa tus propios clips de vídeo de las carpetas de ánimo en la carpeta del programa (clips/traurig, clips/neutral, clips/gluecklich, clips/animations).\n\nImportante: el programa se entrega sin clips. Debes llenar estas carpetas con vídeos tú mismo, si no no hay nada que cortar.\n\nAquí eliges el ánimo, cuántos clips se usan y si eliges clips concretos tú mismo."),
 "mood": ("Ánimo", "Elige de qué carpetas de ánimo vienen los clips: Triste, Neutral, Alegre y/o Animación. Puedes combinar varias.\n\nSi no hay ninguna marcada, se usan todas las carpetas. Adapta el ánimo a la música para que imagen y sonido funcionen juntos."),
 "pool_count": ("Cantidad de clips del grupo", "Define cuántos clips del grupo se usan.\n\n• Automático: todos los clips disponibles del ánimo elegido\n• 10 / 20 / 30 clips: una selección aleatoria de ese tamaño\n\nUna selección menor da al vídeo un aspecto más uniforme, una mayor más variedad."),
 "manual": ("Selección manual", "Abre una ventana con miniaturas de todos los clips del ánimo elegido. Un clic selecciona o deselecciona un clip (marco azul = seleccionado). «Todos» y «Ninguno» van más rápido.\n\nSolo se usan los clips seleccionados. «Aceptar» se activa en cuanto hay al menos un clip elegido."),
 "sw_pre": ("Pre-intro", "Tu propio vídeo con su sonido, que se reproduce ANTES de la música – por ejemplo un saludo o un clip con logo. Coloca el archivo de vídeo en la carpeta pre-intro/; si hay varios, se elige uno al azar.\n\nEl vídeo resulta más largo que tu canción porque el pre-intro se antepone. Por defecto: apagado.\nEl pre-intro nunca se usa en el short de 58 s."),
 "sw_intro": ("Intro", "Un vídeo mudo al principio con la música ya sonando debajo – por ejemplo una apertura animada. Coloca el archivo en la carpeta intro/; si hay varios, se elige uno al azar.\n\nEl intro cuenta dentro de la duración de la canción: después siguen los clips normales hasta que acaba la música. El intro nunca se usa en el short de 58 s."),
 "sw_outro": ("Outro", "Tu propio vídeo con su sonido, que se añade DESPUÉS de la música – por ejemplo unos créditos o un aviso para suscribirse. Coloca el archivo en la carpeta outro/; si hay varios, se elige uno al azar.\n\nEl vídeo resulta más largo que la canción. En el editor de clips el outro siempre queda al final. El outro nunca se usa en el short de 58 s."),
 "sw_title": ("Título", "Muestra el título de la música abajo a la izquierda del vídeo (letra Arial Bold, con sombra suave). El título sale del nombre del archivo de música; los caracteres especiales como los dos puntos se eliminan.\n\nAparece tras el intro, entra con suavidad y sale de nuevo tras unos segundos. El short de 58 s nunca lleva título."),
 "sw_pool": ("Grupo interno (interruptor)", "Activa o desactiva los clips de las carpetas de ánimo. Si está apagado, solo se usan tus propios clips y fotos. Si el grupo interno y tus clips están activos a la vez, es el modo mixto: se prefieren tus clips y el grupo completa."),
 "sw_ext": ("Clips externos", "Usa vídeos de una carpeta que elijas, por ejemplo material que solo encaja con esta canción. La carpeta se elige con «Elegir clips externos…».\n\nSi el grupo interno y los clips externos están activos a la vez, se prefieren los externos (aprox. 70 %) y los internos completan. Con solo clips externos, se repiten si hace falta."),
 "sw_photos": ("Fotos externas", "Usa fotos (JPG, PNG, WEBP) de una carpeta que elijas como imágenes fijas en el vídeo. La carpeta se elige con «Elegir fotos externas…».\n\nLas fotos no se usan en cortes muy cortos si hay suficientes clips de vídeo."),
 "short_url": ("Short desde URL", "Crea un short a partir de un vídeo de YouTube: primero copia el enlace del vídeo (barra de direcciones del navegador → copiar) y luego pulsa este botón.\n\nEl programa descarga el vídeo, elige automáticamente la sección más fuerte y guarda en la carpeta output/Shorts un short vertical 9:16 de 45 segundos con el sonido original y entrada y salida suaves.\n\nLa primera vez se descarga un pequeño componente adicional (hace falta internet). El vídeo debe durar al menos 45 segundos. Usa solo vídeos sobre los que tengas derechos."),
 "btn_ext": ("Elegir clips externos…", "Elige la carpeta con tus propios clips de vídeo y activa «Clips externos». Si cancelas la selección, la opción se apaga de nuevo."),
 "btn_photos": ("Elegir fotos externas…", "Elige la carpeta con tus propias fotos y activa «Fotos externas»."),
 "count": ("Cantidad", "Muestra cuántos clips (y, con fotos activas, cuántas fotos) hay disponibles con los ajustes actuales. Si pone 0, no hay nada que cortar – faltan clips en las carpetas o la fuente está apagada."),
 "sources": ("Fuentes y opciones", "Aquí decides de dónde vienen la imagen y los contenidos adicionales: pre-intro, intro, outro y título, así como el grupo interno, los clips externos y las fotos externas. Pasa el ratón sobre un interruptor y haz clic para que te lo explique en detalle."),
 "render": ("Renderizar", "Inicia la creación de tu vídeo. El programa analiza la música, coloca los cortes sobre el ritmo, renderiza todos los segmentos y une imagen y sonido. Por cada formato elegido se crea un archivo en la carpeta output/.\n\nLa barra de progreso y la línea de estado muestran el avance y un sonido avisa del final. Después puedes ver el vídeo o retocarlo con «Editar clips». Con varios formatos tarda más."),
 "edit": ("Editar clips", "Se activa tras renderizar y abre el editor de clips. Allí ves todos los clips de tu vídeo como miniaturas y puedes reordenarlos o reemplazar los que no encajan por otros – sin renderizar todo el vídeo de nuevo.\n\nSe edita el primer formato elegido, normalmente el vídeo 16:9."),
 "status": ("Línea de estado", "Muestra lo que el programa está haciendo o ha hecho – por ejemplo «Renderizando segmentos…», «listo» o avisos como «Debe haber al menos un formato activo». En caso de error, aquí aparece la causa."),
 "watch": ("Ver vídeo", "Abre el último vídeo terminado en el reproductor de vídeo predeterminado de tu ordenador. Si aún no hay vídeo, la línea de estado muestra un aviso."),
 "outfolder": ("Carpeta de salida", "Abre la carpeta output/ en el Explorador. Allí están todos los vídeos terminados (y los shorts desde URL en la subcarpeta Shorts)."),
 "youtube": ("YouTube", "Abre la página de subida de YouTube en el navegador. Todavía tienes que elegir y subir allí tu vídeo – lo más fácil es desde la carpeta de salida."),
 "ed_cancel": ("Cancelar / Atrás", "Sale del editor de clips y vuelve a la ventana principal. Todos los cambios que no hayas guardado con «Aplicar» se descartan – los clips quitados vuelven y el orden se restablece."),
 "ed_apply": ("Aplicar", "Guarda tus cambios (nuevo orden y clips reemplazados) en el vídeo. Es rápido porque solo se repite el paso de unión. El botón solo se activa cuando has cambiado algo. Mientras se guarda, la línea de estado muestra el progreso."),
 "ed_grid": ("Vista de clips", "Cada casilla es un clip de tu vídeo, en el orden en que aparece.\n\n• Reordenar: haz clic en una casilla, mantén pulsado y arrástrala sobre otra – las dos intercambian el sitio.\n• Quitar: «✕ Quitar» bajo la casilla reemplaza el clip por otro clip aleatorio de exactamente la misma duración, preferiblemente uno que aún no esté en el vídeo. Así el vídeo dura exactamente lo mismo que tu canción.\n• Las casillas bloqueadas (candado) son pre-intro y outro. Siempre quedan al principio o al final.\n\nSolo «Aplicar» hace definitivos los cambios."),
 "ed_remove": ("✕ Quitar", "Reemplaza este clip por otro clip aleatorio de exactamente la misma duración para que el vídeo siga durando lo mismo que la canción. El programa prefiere clips que aún no estén en el vídeo y renderiza de nuevo solo este fragmento. Si no te gusta el clip nuevo, pulsa Quitar otra vez.\n\n«Cancelar» restaura todos los clips reemplazados, «Aplicar» los guarda."),
 "ed_lock": ("Clip bloqueado", "Pre-intro y outro son fijos: el pre-intro siempre queda al principio, el outro siempre al final. No se pueden mover ni quitar para que su propio sonido se reproduzca limpiamente antes y después de la música."),
 "ed_status": ("Línea de estado", "Muestra el estado en el editor: por ejemplo «Clip reemplazado – guarda con Aplicar», «Reemplazando clip…» o el progreso al guardar."),
},
}
MENU = {
 "de": {"click": "Klick-Hilfe – Bereich anklicken", "guide": "Kurzanleitung", "pdf": "PDF-Anleitung", "lang": "Sprache / Language",
        "banner": "❓ Klick-Hilfe: Klicke auf einen Bereich, den du erklärt haben möchtest.", "end": "Beenden (Esc)", "close": "Schließen", "none": "Hier gibt es keine Erklärung. Klicke auf einen Knopf, Schalter oder Bereich."},
 "en": {"click": "Click help – click an area", "guide": "Quick guide", "pdf": "PDF manual", "lang": "Language / Sprache",
        "banner": "❓ Click help: click the area you would like explained.", "end": "End (Esc)", "close": "Close", "none": "No explanation here. Click a button, switch or area."},
 "es": {"click": "Ayuda con clic – haz clic en una zona", "guide": "Guía rápida", "pdf": "Manual PDF", "lang": "Idioma / Language",
        "banner": "❓ Ayuda con clic: haz clic en la zona que quieras que te explique.", "end": "Terminar (Esc)", "close": "Cerrar", "none": "Aquí no hay explicación. Haz clic en un botón, interruptor o zona."},
}


# --------------------- Klick-Hilfe ("?") ---------------------
_click_help = {"active": False}


def _help_text_map() -> dict:
    """Beschriftung (in aktueller Sprache) -> Hilfe-Schlüssel."""
    pairs = [
        ("Musik wählen", "music"), ("Reset", "reset"), ("?", "help"),
        ("Normal  16:9", "fmt_normal"), ("Normal  9:16", "fmt_shorts"), ("Short  9:16 · 58s", "fmt_short58"),
        ("Format", "fmt"), ("Schnitt", "speed"),
        ("Sanft", "speed"), ("Langsam", "speed"), ("Normal", "speed"), ("Schnell", "speed"), ("Random", "speed"),
        ("Interner Clip-Pool", "pool"), ("Wähle deine Stimmung", "mood"),
        ("Traurig", "mood"), ("Neutral", "mood"), ("Glücklich", "mood"), ("Animation", "mood"),
        ("Manuelle Auswahl", "manual"),
        ("Quellen & Optionen", "sources"),
        ("Pre-Intro", "sw_pre"), ("Intro", "sw_intro"), ("Outro", "sw_outro"), ("Titel", "sw_title"),
        ("Interner Pool", "sw_pool"), ("Externe Clips", "sw_ext"), ("Externe Fotos", "sw_photos"),
        ("Short per URL (Zwischenablage)", "short_url"),
        ("Externe Clips wählen…", "btn_ext"), ("Externe Fotos wählen…", "btn_photos"),
        ("Anzahl:", "count"),
        ("▶  Render", "render"), ("✏  Clips verschieben", "edit"),
        ("▶  Video anschauen", "watch"), ("Output-Ordner", "outfolder"), ("YouTube", "youtube"),
        ("✕  Abbrechen", "ed_cancel"), ("✕  Zurück", "ed_cancel"), ("✔  Übernehmen", "ed_apply"),
        ("✕  Entfernen", "ed_remove"), ("🔒 Outro", "ed_lock"), ("🔒 Pre-Intro", "ed_lock"),
    ]
    return {tr(t): k for t, k in pairs}


def _help_target_at(x_root: int, y_root: int):
    """Findet den kleinsten bekannten Bereich unter dem Mauszeiger -> Hilfe-Schlüssel oder None."""
    tmap = _help_text_map()
    named = []
    g = globals()
    for name, key in (("sec_fmt", "fmt"), ("sec_speed", "speed"), ("sec_pool", "pool"), ("sec_files", "sources"),
                      ("opt_pool", "pool_count"), ("count_line", "count"), ("status_box", "status")):
        w = g.get(name)
        if w is not None:
            named.append((w, key))
    for attr, key in (("_status_ed", "ed_status"), ("_grid_frame", "ed_grid")):
        w = getattr(outer, attr, None)
        if w is not None:
            named.append((w, key))
    best = None
    best_area = None

    def consider(w, key):
        nonlocal best, best_area
        try:
            if not w.winfo_viewable():
                return
            x0, y0 = w.winfo_rootx(), w.winfo_rooty()
            ww, hh = w.winfo_width(), w.winfo_height()
            if ww <= 1 or hh <= 1:
                return
            if x0 <= x_root < x0 + ww and y0 <= y_root < y0 + hh:
                area = ww * hh
                if best_area is None or area < best_area:
                    best, best_area = key, area
        except Exception:
            pass

    for w, key in named:
        consider(w, key)

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ctk.CTkToplevel):
                continue
            try:
                t = c.cget("text")
            except Exception:
                t = None
            if t:
                key = tmap.get(str(t).strip())
                if key:
                    consider(c, key)
            walk(c)

    walk(app)
    return best


def show_help_topic(key: str):
    """Ausführliche Erklärung in einem kleinen Fenster."""
    L = LANG or "de"
    topics = HELP_TOPICS.get(L, HELP_TOPICS["de"])
    title, body = topics.get(key, HELP_TOPICS["de"].get(key, ("", "")))
    prev = _click_help.get("topic")
    try:
        if prev is not None and prev.winfo_exists():
            prev.destroy()
    except Exception:
        pass
    win = ctk.CTkToplevel(app)
    _click_help["topic"] = win
    win.title(title)
    win.resizable(False, False)
    win.transient(app)
    ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=18, weight="bold"), anchor="w",
                 justify="left", wraplength=480).pack(fill="x", padx=22, pady=(18, 6))
    ctk.CTkLabel(win, text=body, font=ctk.CTkFont(size=13), anchor="w", justify="left",
                 wraplength=480).pack(fill="x", padx=22, pady=(0, 10))
    ctk.CTkButton(win, text=MENU.get(L, MENU["de"])["close"], width=120, command=win.destroy).pack(pady=(4, 16))
    win.update_idletasks()
    w_, h_ = 530, win.winfo_reqheight()
    x = app.winfo_rootx() + max(0, (app.winfo_width() - w_) // 2)
    y = app.winfo_rooty() + max(0, (app.winfo_height() - h_) // 3)
    win.geometry(f"{w_}x{h_}+{x}+{y}")
    try:
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
    except Exception:
        pass


def start_click_help():
    """Klick-Hilfe: unsichtbare Fläche fängt Klicks ab; Klick auf Bereich -> Erklärung."""
    if _click_help["active"]:
        return
    L = LANG or "de"
    M = MENU.get(L, MENU["de"])
    _click_help["active"] = True
    BAN_H = 40

    banner = ctk.CTkFrame(app, fg_color="#1f6aa5", corner_radius=0, height=BAN_H)
    banner.place(x=0, y=0, relwidth=1)
    banner.pack_propagate(False)
    lbl_var = ctk.StringVar(value=M["banner"])
    ctk.CTkLabel(banner, textvariable=lbl_var, text_color="white",
                 font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(side="left", padx=14)
    overlay = tk.Toplevel(app)
    overlay.overrideredirect(True)
    try:
        overlay.attributes("-alpha", 0.01)
    except Exception:
        pass
    overlay.configure(bg="black", cursor="question_arrow")

    def place_overlay():
        try:
            app.update_idletasks()
            overlay.geometry(f"{app.winfo_width()}x{max(1, app.winfo_height() - BAN_H)}"
                             f"+{app.winfo_rootx()}+{app.winfo_rooty() + BAN_H}")
        except Exception:
            pass

    def finish(event=None):
        if not _click_help["active"]:
            return
        _click_help["active"] = False
        for w in (banner, overlay):
            try:
                w.destroy()
            except Exception:
                pass

    ctk.CTkButton(banner, text=M["end"], width=120, height=26, fg_color="white", text_color="#1f6aa5",
                  hover_color="#e8f0f7", command=finish).pack(side="right", padx=12)

    def on_move(e):
        key = _help_target_at(e.x_root, e.y_root)
        if key:
            title = HELP_TOPICS.get(L, HELP_TOPICS["de"]).get(key, ("", ""))[0]
            lbl_var.set(f"❓ {title}")
        else:
            lbl_var.set(M["banner"])

    def on_click(e):
        key = _help_target_at(e.x_root, e.y_root)
        if key:
            finish()              # Modus endet automatisch, dann erscheint die Erklärung
            show_help_topic(key)
        else:
            lbl_var.set(M["none"])

    overlay.bind("<Motion>", on_move)
    overlay.bind("<Button-1>", on_click)
    overlay.bind("<Escape>", finish)
    app.bind("<Escape>", finish, add="+")

    def keep_aligned():
        if _click_help["active"]:
            place_overlay()
            app.after(250, keep_aligned)

    place_overlay()
    banner.lift()
    overlay.lift()
    try:
        overlay.focus_force()
    except Exception:
        pass
    app.after(250, keep_aligned)


def open_help_menu(btn=None):
    """Menü des "?"-Buttons: Klick-Hilfe, Kurzanleitung, PDF, Sprache."""
    L = LANG or "de"
    M = MENU.get(L, MENU["de"])
    menu = tk.Menu(app, tearoff=0)
    menu.add_command(label=M["click"], command=start_click_help)
    menu.add_separator()
    menu.add_command(label=M["guide"], command=open_help)
    menu.add_command(label=M["pdf"], command=open_pdf_manual)
    menu.add_command(label=M["lang"], command=lambda: open_language_dialog(False))
    try:
        b = btn or (btn_help if not in_editor_mode else None)
        x = (b.winfo_rootx() if b else app.winfo_pointerx())
        y = (b.winfo_rooty() + b.winfo_height() if b else app.winfo_pointery())
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


app.protocol("WM_DELETE_WINDOW", on_closing)

# Sicherheitsnetz: Cursor zurücksetzen falls Drag außerhalb des Fensters losgelassen
def _global_release(event):
    global _drag_source_idx, _drag_highlight_idx
    if _drag_source_idx is not None:
        _destroy_ghost()
        try:
            app.configure(cursor="")
        except Exception:
            pass
        for btn in _tline_buttons:
            try:
                btn.master.configure(border_color=("gray55", "gray35"), border_width=2)
            except Exception:
                pass
        _drag_source_idx = None
        _drag_highlight_idx = None

app.bind("<ButtonRelease-1>", _global_release, add="+")

app.mainloop()
