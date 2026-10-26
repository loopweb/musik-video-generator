# Musik Video Generator v3.0

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

# ── Fehlerprotokoll: Startfehler sichtbar machen (EXE ist "windowed" und würde sonst still scheitern)
def _log_error(text: str, show: bool = False):
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        with open(base / "mvg_error.log", "a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(f"--- {_dt.datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}\n")
    except Exception:
        pass
    if show:
        try:
            import tkinter.messagebox as _mb
            _mb.showerror("Musik Video Generator", text[-1500:] + "\n\n(mvg_error.log)")
        except Exception:
            pass


def _trace(msg: str):
    """Startphasen mitschreiben (mvg_start.log, wird bei jedem Start neu begonnen)."""
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        mode = "w" if msg == "start" else "a"
        with open(base / "mvg_start.log", mode, encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _excepthook(et, ev, tb):
    import traceback
    _log_error("".join(traceback.format_exception(et, ev, tb)), show=True)


sys.excepthook = _excepthook
_trace("start")


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
  "Musik Video Generator  v3.0": "Music Video Generator  v3.0",
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
  "Musik Video Generator  v3.0": "Generador de vídeos musicales  v3.0",
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
  ["8. Hilfe", "Über das „⚙“: Klick-Hilfe (Bereich anklicken, Erklärung lesen), PDF, Sprache, Modus, Updates, Mobile Version."]
 ],
 "en": [
  ["1. Prepare", "Fill the clip folders with your own videos – the program ships without clips."],
  ["2. Music & format", "Choose a music file, then format(s) and cutting. Default: 16:9 video plus 58 s short."],
  ["3. Clip source", "Internal pool with mood, your own clips/photos, or both mixed."],
  ["4. Extras", "Pre-intro, intro, outro and title are optional and off by default."],
  ["5. Render", "Click “Render”. A sound signals the end, the video is in output/."],
  ["6. Refine", "“Edit clips”: reorder clips or replace them with “✕ Remove”."],
  ["7. Short from URL", "Copy a YouTube link, click “Short from URL” – 45 s short in output/Shorts."],
  ["8. Help", "Via “⚙”: click help (click an area, read the explanation), PDF, language, mode, updates, mobile version."]
 ],
 "es": [
  ["1. Preparar", "Llena las carpetas de clips con tus vídeos – el programa se entrega sin clips."],
  ["2. Música y formato", "Elige un archivo de música, luego formato(s) y corte. Por defecto: vídeo 16:9 y short de 58 s."],
  ["3. Fuente de clips", "Grupo interno con ánimo, tus clips/fotos, o ambos mezclados."],
  ["4. Extras", "Pre-intro, intro, outro y título son opcionales y están apagados por defecto."],
  ["5. Renderizar", "Pulsa «Renderizar». Un sonido avisa del final, el vídeo está en output/."],
  ["6. Retocar", "«Editar clips»: reordena clips o reemplázalos con «✕ Quitar»."],
  ["7. Short desde URL", "Copia un enlace de YouTube y pulsa «Short desde URL» – short de 45 s en output/Shorts."],
  ["8. Ayuda", "Con «⚙»: ayuda con clic (haz clic en una zona y lee la explicación), PDF, idioma, modo, actualizaciones, versión móvil."]
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
    """Kurzanleitung – aufgeräumt, ohne Buttons (PDF/Sprache über das ⚙-Menü)."""
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
        msg = tr("PDF-Anleitung nicht neben dem Programm gefunden")
        try:
            ui_status(msg)                 # Fortgeschritten-Modus: Statuszeile
        except NameError:
            messagebox.showinfo("MVG", msg)   # Einfach-Modus: keine Statuszeile


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
    intro_override: Optional[Path] = None,
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
        intro_candidates = [intro_override] if intro_override else list_video_files(INTRO_DIR)
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

def _boot_root(dnd: bool = False):
    """Das eine große Hauptfenster – wird von Sprach-/Modusabfrage und danach vom Programm genutzt."""
    if True:
        r = None
        # Drag & Drop nur, wenn es gebraucht wird (Einfach-Modus bzw. erster Start)
        if _HAS_DND and dnd:
            try:
                class _DndCTk(ctk.CTk, TkinterDnD.DnDWrapper):
                    def __init__(self, *a, **k):
                        super().__init__(*a, **k)
                        self.TkdndVersion = TkinterDnD._require(self)
                r = _DndCTk()
            except Exception as e:
                _log_error("Drag&Drop nicht verfügbar: " + repr(e))
                try:
                    import tkinter as _tk
                    root_old = getattr(_tk, "_default_root", None)
                    if root_old is not None:
                        root_old.destroy()
                except Exception:
                    pass
                r = None
        if r is None:
            r = ctk.CTk()
        r.title(tr("Musik Video Generator  v3.0"))
        r.geometry("960x720")
        r.minsize(960, 720)
        r.update_idletasks()
        r.geometry(f"960x720+{max(0, (r.winfo_screenwidth() - 960) // 2)}+{max(0, (r.winfo_screenheight() - 720) // 2)}")
        try:
            r.report_callback_exception = lambda et, ev, tb: _log_error("".join(__import__("traceback").format_exception(et, ev, tb)), show=True)
        except Exception:
            pass
    return r


def open_language_dialog(first_start: bool = False):
    """Sprachauswahl. Beim ersten Start im großen Hauptfenster, sonst als kleines Fenster aus dem Menü."""
    global LANG
    if first_start:
        root = _boot_root()
    else:
        root = ctk.CTkToplevel(app)
        root.title("Language / Sprache / Idioma")
        root.grab_set()
    if first_start:
        box = ctk.CTkFrame(root, fg_color="transparent")
        box.place(relx=0.5, rely=0.5, anchor="center")
    else:
        _w, _h = 360, 250
        try:
            root.update_idletasks()
            _x = app.winfo_x() + (app.winfo_width() - _w) // 2
            _y = app.winfo_y() + (app.winfo_height() - _h) // 2
            root.geometry(f"{_w}x{_h}+{max(0, _x)}+{max(0, _y)}")
        except Exception:
            root.geometry(f"{_w}x{_h}")
        root.resizable(False, False)
        box = root

    ctk.CTkLabel(box, text="Language · Sprache · Idioma",
                 font=ctk.CTkFont(size=22 if first_start else 16, weight="bold")).pack(pady=(20, 14))

    def pick(code):
        global LANG
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
        ctk.CTkButton(box, text=name, width=260 if first_start else 200, height=44 if first_start else 36,
                      command=lambda c=code: pick(c)).pack(pady=6)

    if first_start:
        def on_close():
            root.destroy()
            os._exit(0)
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()



# ===================== MODUS: EINFACH / FORTGESCHRITTEN =====================
MODE = ""   # "simple" | "advanced" (wird beim ersten Start abgefragt)

TRANSLATIONS.setdefault("en", {}).update({
    "Modus wählen": "Choose mode",
    "Wie möchtest du das Programm nutzen?": "How would you like to use the program?",
    "Einfach\nnur Musik und Videos hineinziehen": "Simple\njust drop in music and videos",
    "Fortgeschritten\nalle Einstellungen": "Advanced\nall settings",
    "Der Modus kann jederzeit im ⚙-Menü geändert werden.": "You can change the mode any time in the ⚙ menu.",
    "Modus wechseln": "Change mode",
    "Musik hierher ziehen\n(oder klicken zum Auswählen)": "Drop your music here\n(or click to choose)",
    "Videos hierher ziehen\n(oder klicken zum Auswählen)": "Drop your videos here\n(or click to choose)",
    "Musik: ": "Music: ",
    "Videos: ": "Videos: ",
    " Videos": " videos",
    "Ordner öffnen": "Open folder",
    "Bitte zuerst Musik und Videos hinzufügen.": "Please add music and videos first.",
    "Videos werden vorbereitet …": "Preparing videos …",
    "Ziehen und Ablegen ist nicht verfügbar – bitte klicken.": "Drag and drop is not available – please click.",
    "Zurücksetzen": "Reset",
    "Hilfe, Anleitung & Fortgeschritten-Modus: ⚙ oben rechts": "Help, manual & advanced mode: ⚙ top right",
    "Klick-Hilfe": "Click help",
    "Neueste Version (GitHub)": "Latest version (GitHub)",
    "Mobile Version – MVG Mobil (GitHub)": "Mobile version – MVG Mobile (GitHub)",
    "📱 Auch fürs Handy: MVG Mobil": "📱 Also for your phone: MVG Mobile",
    "PDF-Anleitung": "PDF manual",
    "Sprache / Language": "Language / Sprache",
    "Intro: ": "Intro: ",
    "Intro hierher ziehen (optional)\n(oder klicken zum Auswählen)": "Drop an intro here (optional)\n(or click to choose)",
    "Intro wählen": "Choose intro",
    "Bitte das Programm neu starten.": "Please restart the program.",
    "Videos wählen": "Choose videos",
    "Rendern läuft noch. Wirklich beenden?": "Rendering is still running. Really quit?",
})
TRANSLATIONS.setdefault("es", {}).update({
    "Modus wählen": "Elegir modo",
    "Wie möchtest du das Programm nutzen?": "¿Cómo quieres usar el programa?",
    "Einfach\nnur Musik und Videos hineinziehen": "Sencillo\nsolo arrastra música y vídeos",
    "Fortgeschritten\nalle Einstellungen": "Avanzado\ntodos los ajustes",
    "Der Modus kann jederzeit im ⚙-Menü geändert werden.": "Puedes cambiar el modo en cualquier momento en el menú ⚙.",
    "Modus wechseln": "Cambiar modo",
    "Musik hierher ziehen\n(oder klicken zum Auswählen)": "Arrastra aquí la música\n(o haz clic para elegir)",
    "Videos hierher ziehen\n(oder klicken zum Auswählen)": "Arrastra aquí los vídeos\n(o haz clic para elegir)",
    "Musik: ": "Música: ",
    "Videos: ": "Vídeos: ",
    " Videos": " vídeos",
    "Ordner öffnen": "Abrir carpeta",
    "Bitte zuerst Musik und Videos hinzufügen.": "Primero añade música y vídeos.",
    "Videos werden vorbereitet …": "Preparando vídeos …",
    "Ziehen und Ablegen ist nicht verfügbar – bitte klicken.": "Arrastrar y soltar no está disponible – haz clic.",
    "Zurücksetzen": "Restablecer",
    "Hilfe, Anleitung & Fortgeschritten-Modus: ⚙ oben rechts": "Ayuda, manual y modo avanzado: ⚙ arriba a la derecha",
    "Klick-Hilfe": "Ayuda con clic",
    "Neueste Version (GitHub)": "Última versión (GitHub)",
    "Mobile Version – MVG Mobil (GitHub)": "Versión móvil – MVG Móvil (GitHub)",
    "📱 Auch fürs Handy: MVG Mobil": "📱 También para el móvil: MVG Móvil",
    "PDF-Anleitung": "Manual PDF",
    "Sprache / Language": "Idioma / Language",
    "Intro: ": "Intro: ",
    "Intro hierher ziehen (optional)\n(oder klicken zum Auswählen)": "Arrastra aquí una intro (opcional)\n(o haz clic para elegir)",
    "Intro wählen": "Elegir intro",
    "Bitte das Programm neu starten.": "Reinicia el programa.",
    "Videos wählen": "Elegir vídeos",
    "Rendern läuft noch. Wirklich beenden?": "El renderizado sigue en curso. ¿Salir de todos modos?",
})

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    _HAS_DND = False

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def open_mode_dialog(first_start: bool = False):
    """Einfach oder Fortgeschritten. Beim ersten Start im großen Hauptfenster, sonst aus dem ⚙-Menü."""
    global MODE
    if first_start:
        root = _boot_root()
        box = ctk.CTkFrame(root, fg_color="transparent")
        box.place(relx=0.5, rely=0.5, anchor="center")
        wl, bw, bh = 520, 420, 76
    else:
        root = ctk.CTkToplevel(app)
        root.grab_set()
        root.title(tr("Modus wählen"))
        w, h = 420, 330
        try:
            root.update_idletasks()
            x = app.winfo_x() + (app.winfo_width() - w) // 2
            y = app.winfo_y() + (app.winfo_height() - h) // 2
            root.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        except Exception:
            root.geometry(f"{w}x{h}")
        root.resizable(False, False)
        box = root
        wl, bw, bh = 370, 320, 64

    ctk.CTkLabel(box, text=tr("Wie möchtest du das Programm nutzen?"),
                 font=ctk.CTkFont(size=22 if first_start else 16, weight="bold"),
                 wraplength=wl).pack(pady=(22, 16))

    def pick(m):
        global MODE
        save_settings({"mode": m})
        if first_start:
            MODE = m
            root.destroy()
        else:
            root.destroy()
            messagebox.showinfo(tr("Modus wählen"), tr("Bitte das Programm neu starten."))

    for m, key in (("simple", "Einfach\nnur Musik und Videos hineinziehen"),
                   ("advanced", "Fortgeschritten\nalle Einstellungen")):
        ctk.CTkButton(box, text=tr(key), width=bw, height=bh,
                      command=lambda mm=m: pick(mm)).pack(pady=7)
    ctk.CTkLabel(box, text=tr("Der Modus kann jederzeit im ⚙-Menü geändert werden."),
                 font=ctk.CTkFont(size=11), text_color="gray", wraplength=wl).pack(pady=(12, 0))

    if first_start:
        def on_close():
            root.destroy()
            os._exit(0)
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()


def _split_drop(win, data: str) -> list[str]:
    try:
        return list(win.tk.splitlist(data))
    except Exception:
        return [data]


MOBILE_URL = "https://github.com/loopweb/mvg-mobil"
RELEASES_URL = "https://github.com/loopweb/musik-video-generator/releases"


def _open_url(url: str):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def show_settings_menu(widget=None, simple: bool = False):
    """⚙-Menü: Einstellungen, Hilfe, Updates, Mobile Version."""
    menu = tk.Menu(app, tearoff=0)
    menu.add_command(label=tr("Sprache / Language"), command=lambda: open_language_dialog(False))
    menu.add_command(label=tr("Modus wechseln"), command=lambda: open_mode_dialog(False))
    menu.add_separator()
    if not simple:
        menu.add_command(label=tr("Klick-Hilfe"), command=lambda: start_click_help())
        menu.add_command(label=tr("Kurzanleitung"), command=open_help)
    menu.add_command(label=tr("PDF-Anleitung"), command=open_pdf_manual)
    menu.add_separator()
    menu.add_command(label=tr("Neueste Version (GitHub)"), command=lambda: _open_url(RELEASES_URL))
    menu.add_command(label=tr("Mobile Version – MVG Mobil (GitHub)"), command=lambda: _open_url(MOBILE_URL))
    try:
        if widget is not None:
            x, y = widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height()
        else:
            x, y = app.winfo_pointerx(), app.winfo_pointery()
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def run_simple_mode():
    """Einfacher Modus im großen Hauptfenster: Musik + Videos (+ optional Intro) hineinziehen, Render."""
    global app

    app = _boot_root(dnd=True)
    win = app

    st = {"music": None, "videos": [], "intro": None, "busy": False}

    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=30, pady=(22, 0))
    ctk.CTkLabel(top, text="Musik Video Generator",
                 font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

    btn_q = ctk.CTkButton(top, text="⚙", width=40, height=40, command=lambda: show_settings_menu(btn_q, simple=True))
    btn_q.pack(side="right")

    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=60)

    def make_zone(height):
        z = ctk.CTkFrame(body, height=height, corner_radius=14, border_width=2,
                         border_color=("gray60", "gray40"))
        z.pack(fill="x", pady=(16, 0))
        z.pack_propagate(False)
        lbl = ctk.CTkLabel(z, text="", font=ctk.CTkFont(size=16), justify="center")
        lbl.pack(expand=True)
        return z, lbl

    def refresh():
        if st["music"]:
            music_lbl.configure(text=tr("Musik: ") + Path(st["music"]).name)
        else:
            music_lbl.configure(text=tr("Musik hierher ziehen\n(oder klicken zum Auswählen)"))
        if st["videos"]:
            vid_lbl.configure(text=tr("Videos: ") + f"{len(st['videos'])}" + tr(" Videos"))
        else:
            vid_lbl.configure(text=tr("Videos hierher ziehen\n(oder klicken zum Auswählen)"))
        if st["intro"]:
            intro_lbl.configure(text=tr("Intro: ") + Path(st["intro"]).name)
        else:
            intro_lbl.configure(text=tr("Intro hierher ziehen (optional)\n(oder klicken zum Auswählen)"))
        btn_render.configure(state="normal" if (st["music"] and st["videos"] and not st["busy"]) else "disabled")

    def add_files(paths, kind="videos"):
        for p in paths:
            pp = Path(str(p).strip("{}"))
            ext = pp.suffix.lower()
            if pp.is_dir():
                add_files([str(c) for c in sorted(pp.iterdir())], kind)
            elif not pp.is_file():
                continue
            elif ext in AUDIO_EXTS:
                st["music"] = str(pp)
            elif ext in VIDEO_EXTS:
                if kind == "intro":
                    st["intro"] = str(pp)
                elif str(pp) not in st["videos"]:
                    st["videos"].append(str(pp))
        refresh()

    def pick_music():
        if st["busy"]:
            return
        f = filedialog.askopenfilename(title=tr("Musik wählen"),
                                       filetypes=[("Audio", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac")])
        if f:
            add_files([f])

    def pick_videos():
        if st["busy"]:
            return
        fs = filedialog.askopenfilenames(title=tr("Videos wählen"),
                                         filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm")])
        if fs:
            add_files(list(fs), "videos")

    def pick_intro():
        if st["busy"]:
            return
        f = filedialog.askopenfilename(title=tr("Intro wählen"),
                                       filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm")])
        if f:
            add_files([f], "intro")

    zone_m, music_lbl = make_zone(120)
    zone_i, intro_lbl = make_zone(90)
    zone_v, vid_lbl = make_zone(120)

    for z, lbl, click, kind in ((zone_m, music_lbl, pick_music, "videos"),
                                (zone_v, vid_lbl, pick_videos, "videos"),
                                (zone_i, intro_lbl, pick_intro, "intro")):
        for wdg in (z, lbl):
            wdg.bind("<Button-1>", lambda e, c=click: c())
            try:
                wdg.configure(cursor="hand2")
            except Exception:
                pass
            if _HAS_DND:
                try:
                    wdg.drop_target_register(DND_FILES)
                    wdg.dnd_bind("<<Drop>>", lambda e, k=kind: (None if st["busy"] else add_files(_split_drop(win, e.data), k)))
                except Exception:
                    pass

    status = ctk.CTkLabel(body, text="" if _HAS_DND else tr("Ziehen und Ablegen ist nicht verfügbar – bitte klicken."),
                          wraplength=700, justify="center")
    status.pack(pady=(20, 6))
    bar = ctk.CTkProgressBar(body, width=700)
    bar.set(0)
    bar.pack(pady=(0, 12))

    def ui_status(t):
        status.configure(text=t)

    def ui_progress(p):
        bar.set(max(0.0, min(1.0, p)))

    def set_busy(b):
        st["busy"] = b
        refresh()

    def open_out():
        try:
            OUTPUT_DIR.mkdir(exist_ok=True)
            os.startfile(str(OUTPUT_DIR))
        except Exception:
            pass

    def do_simple_render():
        if st["busy"]:
            return
        if not (st["music"] and st["videos"]):
            ui_status(tr("Bitte zuerst Musik und Videos hinzufügen."))
            return
        music = st["music"]
        vids = list(st["videos"])
        intro = Path(st["intro"]) if st["intro"] else None
        set_busy(True)
        ui_progress(0.0)
        ui_status(tr("Videos werden vorbereitet …"))

        def worker():
            try:
                folder = BASE_DIR / "simple_clips"
                if folder.exists():
                    shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True, exist_ok=True)
                for i, v in enumerate(vids):
                    dst = folder / f"{i:03d}_{Path(v).name}"
                    try:
                        os.link(v, dst)
                    except Exception:
                        shutil.copy2(v, dst)
                empty_photos = BASE_DIR / "simple_photos"
                empty_photos.mkdir(exist_ok=True)

                OUTPUT_DIR.mkdir(exist_ok=True)
                jobs = [(k, get_output_path_from_music(music, k)) for k in ("normal", "shorts58")]
                done, failed = [], []
                n = len(jobs)
                for idx, (fmt, fout) in enumerate(jobs):
                    prefix = f"[{idx + 1}/{n} · {FORMAT_LABELS[fmt]}]  "
                    try:
                        render_video(
                            audio_path=music, clips_folder=folder, photos_folder=empty_photos,
                            out_path=fout, use_intro=bool(intro and fmt == "normal"),
                            use_outro=False, use_vorspann=False,
                            speed="normal", pool_value=0, pool_on=False,
                            external_clips_on=True, external_photos_on=False,
                            is_shorts=(fmt != "normal"), shorts_limited=(fmt == "shorts58"),
                            moods_selected=None, title_overlay=False, title_text="",
                            status_cb=lambda t, p=prefix: win.after(0, ui_status, p + t),
                            progress_cb=lambda p, i=idx: win.after(0, ui_progress, (i + p) / n),
                            persist_segments=False,
                            intro_override=intro if fmt == "normal" else None,
                        )
                        done.append(fout.name)
                    except Exception as e:
                        if idx == 0:
                            raise
                        failed.append(f"{FORMAT_LABELS[fmt]}: {e}")
                win.after(0, play_done_sound)
                msg = tr("Fertig: ") + " + ".join(done)
                if failed:
                    msg += tr("  —  Fehler bei ") + "; ".join(failed)
                win.after(0, ui_status, msg)
                win.after(0, ui_progress, 1.0)
                win.after(0, lambda: btn_open.pack(pady=(0, 10)))
            except Exception as e:
                win.after(0, ui_status, tr("Fehler: ") + str(e))
            finally:
                shutil.rmtree(BASE_DIR / "simple_clips", ignore_errors=True)   # temporäre Kopien aufräumen
                shutil.rmtree(BASE_DIR / "simple_photos", ignore_errors=True)
                win.after(0, lambda: set_busy(False))

        btn_open.pack_forget()
        threading.Thread(target=worker, daemon=True).start()

    btn_render = ctk.CTkButton(body, text="▶  Render", width=700, height=56,
                               font=ctk.CTkFont(size=18, weight="bold"),
                               state="disabled", command=do_simple_render)
    btn_render.pack(pady=(4, 10))
    btn_open = ctk.CTkButton(body, text=tr("Ordner öffnen"), width=200, command=open_out)

    def reset():
        if st["busy"]:
            return
        st["music"] = None
        st["videos"] = []
        st["intro"] = None
        ui_progress(0.0)
        ui_status("")
        btn_open.pack_forget()
        refresh()

    ctk.CTkButton(body, text=tr("Zurücksetzen"), width=120, fg_color="transparent", border_width=1,
                  text_color=("gray20", "gray80"), command=reset).pack(pady=(0, 14))

    ctk.CTkLabel(body, text=tr("Hilfe, Anleitung & Fortgeschritten-Modus: ⚙ oben rechts"), font=ctk.CTkFont(size=12), text_color="gray",
                 wraplength=700, justify="center").pack(pady=(0, 6))

    def on_close():
        if st["busy"] and not messagebox.askyesno(tr("Beenden"), tr("Rendern läuft noch. Wirklich beenden?")):
            return
        win.destroy()
        os._exit(0)

    win.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    win.mainloop()
    sys.exit(0)


_trace("vor Sprachabfrage")
_first_start = not LANG
if not LANG:
    open_language_dialog(first_start=True)
    if not LANG:
        LANG = 'de'

_trace("Sprache ok")
MODE = load_settings().get("mode", "")
if MODE not in ("simple", "advanced"):
    if _first_start:
        open_mode_dialog(first_start=True)
    if MODE not in ("simple", "advanced"):
        MODE = "advanced"          # bestehende Nutzer: gewohntes Fenster
_trace("Modus: " + MODE)
if MODE == "simple":
    run_simple_mode()

app = _boot_root(dnd=False)
_trace("Hauptfenster erzeugt")
app.title(tr("Musik Video Generator  v3.0"))
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
    out_dir = OUTPUT_DIR / "Shorts"      # wird erst beim Speichern angelegt

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
        out_dir.mkdir(parents=True, exist_ok=True)
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

btn_help = ctk.CTkButton(sec_music, text="⚙", width=36, command=lambda: open_help_menu())
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
        sec_top, text="⚙", width=36, command=lambda: open_help_menu()
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
    btn_help = ctk.CTkButton(sec_music, text="⚙", width=36, command=lambda: open_help_menu())
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
 "music": ("Musik wählen", "Hier wählst du die Musikdatei (MP3 oder WAV), aus der dein Video entstehen soll. Klicke auf den Button, suche die Datei im Windows-Dialog und bestätige. Der Dateiname erscheint danach neben dem Button.\n\nWas daraus wird: Das Programm analysiert den Beat der Musik und legt alle Bildwechsel genau darauf. Das fertige Video ist so lang wie dein Song. Auch der Dateiname des Videos kommt von hier: „MeinSong.mp3“ wird zu „MeinSong.mp4“. Derselbe Name wird optional als Titel im Video eingeblendet.\n\nWichtig: Ohne Musik kann nicht gerendert werden. Fehlt sie, meldet die Statuszeile „bitte Musik auswählen“. Wählst du später eine andere Datei, ändern sich auch die Namen der neuen Videos."),
 "reset": ("Reset", "Setzt das Programm auf den Anfangszustand zurück: gewählte Musik, Formate, Schnittgeschwindigkeit, Pool, Stimmung und alle Schalter.\n\nIst ein Zwischenspeicher (Cache) vorhanden, fragt dich das Programm, ob er auch geleert werden soll. Der Cache enthält bereits vorbereitete Clips und macht wiederholtes Rendern schneller, kann aber viel Speicherplatz belegen. Leerst du ihn, dauert der nächste Render etwas länger, sonst passiert nichts.\n\nDeine fertigen Videos im Ordner output/ und deine eigenen Clips bleiben immer erhalten. Während ein Render läuft, ist Reset gesperrt."),
 "help": ("Einstellungen (⚙)", "Das Zahnrad öffnet das Menü mit allem, was nicht direkt zum Videobau gehört:\n\n• Sprache / Language: Deutsch, English oder Español. Nach dem Wechsel das Programm einmal neu starten.\n• Modus wechseln: Einfach (nur Musik und Videos hineinziehen) oder Fortgeschritten (alle Einstellungen). Ebenfalls mit Neustart.\n• Klick-Hilfe: Du klickst danach auf einen beliebigen Bereich und bekommst genau diese Erklärung. Der Modus beendet sich nach einer Erklärung von selbst.\n• Kurzanleitung: eine kurze Übersicht der acht Schritte.\n• PDF-Anleitung: das ausführliche Handbuch in deiner Sprache (die PDF muss neben der EXE liegen).\n• Neueste Version (GitHub): öffnet die Download-Seite für Updates.\n• Mobile Version (MVG Mobil): öffnet die Seite der Handy-Variante."),
 "fmt": ("Format", "Hier legst du fest, welche Videoformate beim Render entstehen. Du kannst mehrere gleichzeitig ankreuzen: Jedes Format wird in einem eigenen Durchgang gebaut und als eigene Datei im Ordner output/ gespeichert. Mindestens ein Format muss aktiv bleiben, sonst meldet die Statuszeile einen Hinweis.\n\nStandard sind 16:9 und der 58-Sekunden-Short, also das klassische Video plus die Fassung für Instagram, YouTube Shorts und TikTok in einem Rutsch.\n\nBedenke: Jedes zusätzliche Format verlängert die Renderzeit. Der Fortschrittsbalken zählt über alle Formate hinweg, die Statuszeile zeigt, welches gerade dran ist."),
 "fmt_normal": ("Normal 16:9", "Das klassische Querformat für YouTube, Fernseher und Computer-Bildschirme. Das Video ist so lang wie deine Musik.\n\nNur in diesem Format (und im 9:16-Vollformat) sind Pre-Intro, Intro, Outro und Titel möglich, sofern du sie unter „Quellen & Optionen“ eingeschaltet hast.\n\nDateiname: Musiktitel.mp4 im Ordner output/.\n\nDieses Video ist auch das Hauptvideo: Der Clip-Editor („Clips verschieben“) bearbeitet nach dem Rendern immer das erste gewählte Format, normalerweise also dieses."),
 "fmt_shorts": ("Normal 9:16", "Hochformat (wie auf dem Handy gehalten) in der vollen Länge deiner Musik. Gedacht für Fälle, in denen das ganze Lied am Handy gezeigt werden soll, etwa als Story oder als langes Hochformat-Video.\n\nPre-Intro, Intro, Outro und Titel sind möglich, wenn sie eingeschaltet sind.\n\nDateiname: Musiktitel_9x16.mp4 im Ordner output/.\n\nMöchtest du nur eine kurze Fassung für Reels, Shorts oder TikTok, nimm stattdessen den 58-Sekunden-Short."),
 "fmt_short58": ("Short 9:16 · 58 s", "Ein kurzes Hochformat-Video von genau 58 Sekunden für Instagram Reels, YouTube Shorts und TikTok.\n\nWie der Ausschnitt entsteht: Das Programm sucht automatisch den stärksten Abschnitt deines Songs (meist den Refrain) und schneidet genau dieses Stück heraus. Weil dadurch ein Teil aus der Mitte fehlt, werden Bild und Ton am Anfang weich eingeblendet und am Ende wieder ausgeblendet.\n\nDer Short entsteht immer ohne Pre-Intro, Intro, Outro und Titel, damit er exakt 58 Sekunden lang bleibt. Ist der Song kürzer als 58 Sekunden, wird er in voller Länge genommen.\n\nDateiname: Musiktitel_Short.mp4 im Ordner output/."),
 "speed": ("Schnitt", "Bestimmt, wie schnell die Clips wechseln. Alle Schnitte werden genau auf den Beat der Musik gelegt, die Geschwindigkeit bestimmt nur, wie viele Schläge zwischen zwei Schnitten liegen.\n\n• Sanft: etwa alle 3,5 Sekunden, ruhig und atmosphärisch\n• Langsam: etwa alle 2,2 Sekunden, cineastisch\n• Normal: etwa alle 1,7 Sekunden, ausgewogen (Standard)\n• Schnell: etwa alle 0,95 Sekunden, energetisch\n• Random: jeder Schnitt zufällig zwischen 0,95 und 3,5 Sekunden, abwechslungsreich und unvorhersehbar\n\nTipp: Für Balladen passen Sanft oder Langsam, für Bachata und Merengue meist Normal, für Reggaetón und Tanzmusik Schnell. Mehr Schnitte bedeuten mehr benötigte Clips und eine etwas längere Renderzeit."),
 "pool": ("Interner Clip-Pool", "Der interne Pool sind deine eigenen Videoclips, sortiert in den Stimmungs-Ordnern im Programmordner: clips/traurig, clips/neutral, clips/gluecklich und clips/animations.\n\nGanz wichtig: Das Programm wird ohne Clips ausgeliefert. Diese Ordner musst du selbst mit Videos füllen. Sind sie leer und keine andere Quelle aktiv, gibt es nichts zu schneiden, und die Statuszeile meldet, dass keine Clips gefunden wurden.\n\nIn diesem Bereich bestimmst du, welche Stimmung verwendet wird, wie viele Clips daraus und ob du einzelne Clips selbst auswählst. Ganz ausschalten kannst du den Pool mit dem Schalter „Interner Pool“ unter „Quellen & Optionen“, zum Beispiel wenn du nur eigene Clips aus einem anderen Ordner nutzen willst."),
 "mood": ("Wähle deine Stimmung", "Hier wählst du, aus welchen Stimmungs-Ordnern die Clips genommen werden: Traurig, Neutral, Glücklich und/oder Animation. Du kannst beliebig viele kombinieren; dann wird aus allen angehakten Ordnern gemischt.\n\nIst keine Stimmung angehakt, verwendet das Programm alle Ordner.\n\nTipp: Passe die Stimmung zur Musik, damit Bild und Ton zusammenwirken: ruhige, melancholische Lieder zu „Traurig“, Tanzmusik zu „Glücklich“. „Animation“ ist für animierte Clips, die du im Ordner clips/animations/ ablegst.\n\nWie viele Clips tatsächlich zur Verfügung stehen, zeigt die Zeile „Anzahl:“. Steht dort 0, ist der gewählte Ordner leer."),
 "pool_count": ("Anzahl Clips im Pool", "Legt fest, wie viele Clips aus dem Pool für dieses Video verwendet werden.\n\n• Automatisch: alle verfügbaren Clips der gewählten Stimmung\n• 10, 20 oder 30 Clips: eine zufällige Auswahl dieser Größe\n\nWas das bewirkt: Eine kleinere Auswahl gibt dem Video einen einheitlicheren Look, weil sich dieselben Clips öfter wiederholen. Eine größere Auswahl bringt mehr Abwechslung.\n\nHast du weniger Clips im Ordner als eingestellt, nimmt das Programm einfach alle, die da sind. Bei sehr kurzen Schnitten (Schnell oder Random) wiederholen sich Clips früher, das ist normal."),
 "manual": ("Manuelle Auswahl", "Öffnet ein Fenster mit Vorschaubildern aller Clips der gewählten Stimmung. Mit einem Klick wählst du einen Clip aus oder wieder ab (blauer Rahmen = ausgewählt). Mit „Alle“ und „Keine“ gehst du schneller vor.\n\nNur die ausgewählten Clips werden danach für das Video verwendet. „OK“ wird erst aktiv, wenn mindestens ein Clip gewählt ist; mit „Abbrechen“ bleibt alles wie vorher.\n\nWann das sinnvoll ist: wenn bestimmte Clips auf keinen Fall vorkommen sollen oder nur eine Handvoll zum Song passt. Das Erstellen der Vorschaubilder kann bei vielen Clips einen Moment dauern."),
 "sw_pre": ("Pre-Intro", "Ein eigenes Video mit eigenem Ton, das VOR deiner Musik läuft, zum Beispiel eine Begrüßung, ein Kanal-Jingle oder ein Logo-Clip.\n\nSo nutzt du es: Lege die Videodatei in den Ordner pre-intro/ im Programmordner und schalte „Pre-Intro“ ein. Liegen mehrere Dateien im Ordner, wird beim Render zufällig eine gewählt.\n\nWas du beachten musst: Das fertige Video wird dadurch länger als dein Song, weil das Pre-Intro vorangestellt wird. Der Ton des Pre-Intros wird nicht von der Musik überdeckt, die Musik beginnt erst danach. Im Clip-Editor bleibt das Pre-Intro immer ganz vorn.\n\nStandard: aus. Im 58-Sekunden-Short wird das Pre-Intro nie verwendet. Ist der Ordner leer, passiert nichts."),
 "sw_intro": ("Intro", "Ein stummes Video am Anfang, unter dem die Musik schon läuft, zum Beispiel ein animierter Vorspann oder ein Titelbild.\n\nSo nutzt du es: Lege die Datei in den Ordner intro/ und schalte „Intro“ ein. Bei mehreren Dateien wird zufällig eine gewählt. Der Ton des Intro-Videos wird nicht verwendet; es läuft stumm unter deiner Musik.\n\nWas du beachten musst: Das Intro zählt zur Songlänge. Das Video wird dadurch nicht länger, nur der Anfang besteht aus dem Intro. Danach folgen die normalen Clips, bis die Musik zu Ende ist. Ist das Intro länger als der Song, wird es gekürzt.\n\nIm 58-Sekunden-Short wird das Intro nie verwendet. Ist der Ordner leer, passiert nichts."),
 "sw_outro": ("Outro", "Ein eigenes Video mit eigenem Ton, das NACH der Musik angehängt wird, zum Beispiel ein Abspann, ein Hinweis zum Abonnieren oder dein Logo.\n\nSo nutzt du es: Lege die Datei in den Ordner outro/ und schalte „Outro“ ein. Bei mehreren Dateien wird zufällig eine gewählt.\n\nWas du beachten musst: Das fertige Video wird dadurch länger als der Song, weil das Outro hinten angehängt wird. Es hat seinen eigenen Ton, die Musik endet vorher. Im Clip-Editor bleibt das Outro immer am Ende und lässt sich nicht verschieben.\n\nIm 58-Sekunden-Short wird das Outro nie verwendet. Ist der Ordner leer, passiert nichts."),
 "sw_title": ("Titel", "Blendet den Musiktitel unten links im Video ein (Schrift Arial Bold mit leichtem Schatten).\n\nWoher der Titel kommt: Er wird automatisch aus dem Dateinamen deiner Musik gebildet. Aus „Don Miguel - Fuego.mp3“ wird also „Don Miguel - Fuego“. Sonderzeichen, die in Windows-Dateinamen nicht erlaubt sind, wie Doppelpunkte, werden entfernt. Willst du einen anderen Titel, benenne die Musikdatei um.\n\nWann er erscheint: nach dem Intro, mit weichem Einblenden, und nach einigen Sekunden blendet er wieder aus. Er bleibt also nicht das ganze Video über stehen.\n\nIm 58-Sekunden-Short gibt es nie einen Titel."),
 "sw_pool": ("Interner Pool (Schalter)", "Schaltet die Clips aus den Stimmungs-Ordnern ein oder aus.\n\n• Nur der Pool an: Das Video besteht aus deinen Clips der gewählten Stimmung.\n• Pool aus: Es werden nur externe Clips und Fotos verwendet. Dann muss mindestens eine dieser Quellen aktiv sein, sonst meldet die Statuszeile „Keine Quelle aktiv“.\n• Pool und externe Clips beide an: das ist der Mix-Modus. Externe Clips werden bevorzugt, der Pool füllt auf.\n\nTipp: Der Mix-Modus eignet sich gut, wenn du für einen Song ein paar passende Spezial-Clips hast und den Rest mit deinem Standardmaterial auffüllen möchtest."),
 "sw_ext": ("Externe Clips", "Verwendet Videos aus einem Ordner deiner Wahl, zum Beispiel Material, das nur zu diesem einen Song passt. Den Ordner suchst du mit „Externe Clips wählen…“ aus. Wie viele Videos das Programm darin gefunden hat, zeigt die Zeile „Anzahl:“.\n\nWie sie verwendet werden: Sind interner Pool und externe Clips zusammen aktiv, werden die externen Clips bevorzugt (etwa 70 %), der Pool füllt auf. Gibt es nur externe Clips, werden sie bei Bedarf wiederholt, bis die Musik zu Ende ist. Unterstützt werden MP4, MOV, MKV, AVI und WEBM.\n\nHinweis: Wird der Ordner gewählt, aber nichts gefunden, meldet die Statuszeile „keine externen Clips gefunden“."),
 "sw_photos": ("Externe Fotos", "Verwendet Fotos (JPG, PNG, WEBP) aus einem Ordner deiner Wahl als stehende Bilder im Video. Den Ordner suchst du mit „Externe Fotos wählen…“ aus.\n\nWie sie verwendet werden: Jedes Foto steht für die Dauer eines Schnitts im Bild, wie ein Videoclip. Fotos und Videos lassen sich mischen. Sind genug Videoclips vorhanden, werden Fotos bei sehr kurzen Schnitten nicht genutzt, damit das Video lebendig bleibt.\n\nHinweis: Wird der Ordner gewählt, aber nichts gefunden, meldet die Statuszeile „keine externen Fotos gefunden“."),
 "short_url": ("Short per URL", "Erstellt einen 45-Sekunden-Short im Hochformat aus einem YouTube-Video, ganz ohne eigene Musik oder Clips.\n\nSo geht es:\n1. Öffne das Video in deinem Browser und kopiere den Link aus der Adressleiste (Strg+C).\n2. Klicke diesen Button. Du musst den Link nirgends einfügen: Das Programm liest ihn direkt aus der Zwischenablage. Deshalb siehst du nach dem Klick kein Eingabefeld, nur die Statuszeile mit dem Fortschritt.\n\nWas passiert: Das Programm lädt das Video, wählt automatisch den stärksten Abschnitt, schneidet 45 Sekunden im Format 9:16 mit dem Originalton heraus und blendet Bild und Ton weich ein und aus. Das Ergebnis liegt im Ordner output/Shorts.\n\nBeim ersten Mal wird eine kleine Zusatzkomponente (deno.exe) heruntergeladen, dafür ist Internet nötig. Das Video muss mindestens 45 Sekunden lang sein. Ist kein gültiger YouTube-Link in der Zwischenablage, sagt dir das die Meldung. Bitte nur Videos verwenden, für die du die Rechte hast."),
 "btn_ext": ("Externe Clips wählen…", "Öffnet einen Windows-Dialog, in dem du den Ordner mit deinen eigenen Videoclips wählst, und schaltet damit „Externe Clips“ ein. Das Programm verwendet danach alle Videos in diesem Ordner.\n\nWichtig zu wissen: Der Windows-Ordnerdialog zeigt nur Ordner an, keine Dateien. Ein Ordner voller Videos sieht darin deshalb leer aus. Das ist normal. Klicke einfach den gewünschten Ordner an und bestätige mit „Ordner auswählen“. Wie viele Videos gefunden wurden, zeigt dir danach die Zeile „Anzahl:“.\n\nBrichst du die Auswahl ab, wird die Option „Externe Clips“ wieder ausgeschaltet. Unterordner werden nicht durchsucht."),
 "btn_photos": ("Externe Fotos wählen…", "Öffnet einen Windows-Dialog, in dem du den Ordner mit deinen eigenen Fotos wählst, und schaltet damit „Externe Fotos“ ein. Das Programm verwendet danach alle Fotos (JPG, PNG, WEBP) in diesem Ordner.\n\nWichtig zu wissen: Der Windows-Ordnerdialog zeigt nur Ordner an, keine Dateien. Ein Ordner voller Fotos sieht darin deshalb leer aus. Das ist normal. Klicke einfach den gewünschten Ordner an und bestätige mit „Ordner auswählen“. Wie viele Fotos gefunden wurden, zeigt dir danach die Zeile „Anzahl:“.\n\nBrichst du die Auswahl ab, wird „Externe Fotos“ wieder ausgeschaltet. Unterordner werden nicht durchsucht."),
 "count": ("Anzahl:", "Zeigt, wie viele Clips (und bei aktiven Fotos auch, wie viele Fotos) mit den aktuellen Einstellungen zur Verfügung stehen. Die Zahl ändert sich, sobald du Stimmung, Pool oder Ordner änderst.\n\nSo liest du sie: Steht hier 0, gibt es nichts zu schneiden. Dann fehlen Clips im gewählten Stimmungs-Ordner, der externe Ordner ist leer oder die passende Quelle ist ausgeschaltet.\n\nDas ist auch die beste Kontrolle nach „Externe Clips wählen…“: Siehst du dort eine passende Zahl, hat das Programm deinen Ordner richtig erkannt, auch wenn der Windows-Dialog ihn leer angezeigt hat."),
 "sources": ("Quellen & Optionen", "Hier legst du fest, woher das Bildmaterial kommt und welche Extras dazukommen.\n\n• Extras: Pre-Intro, Intro, Outro und Titel\n• Quellen: interner Pool, externe Clips und externe Fotos, einzeln oder gemischt\n• Short per URL: ein Short aus einem YouTube-Link\n\nJeder Schalter hat eine eigene Erklärung: Starte die Klick-Hilfe erneut und klicke direkt auf den Schalter.\n\nMindestens eine Bildquelle muss aktiv sein, sonst kann nicht gerendert werden. Die Extras sind nur für die Formate Normal 16:9 und Normal 9:16 gedacht; der 58-Sekunden-Short bleibt immer pur."),
 "render": ("Render", "Startet die Erstellung deines Videos. Das ist der Hauptknopf, alles andere sind Vorbereitungen.\n\nWas passiert: Das Programm analysiert die Musik, legt die Schnitte auf den Beat, rendert alle Segmente einzeln und fügt dann Bild und Ton zusammen. Für jedes gewählte Format entsteht eine Datei im Ordner output/.\n\nWährend des Renders: Der Fortschrittsbalken und die Statuszeile zeigen den Stand, die meisten Bedienelemente sind gesperrt. Mehrere Formate dauern entsprechend länger. Am Ende meldet ein Klang, dass das Video fertig ist.\n\nDanach kannst du es mit „Video anschauen“ ansehen oder mit „Clips verschieben“ nachbearbeiten. Meldet die Statuszeile vorab etwas wie „bitte Musik auswählen“ oder „Keine Quelle aktiv“, fehlt noch eine Voraussetzung."),
 "edit": ("Clips verschieben", "Wird erst nach einem fertigen Render aktiv und öffnet den Clip-Editor. Dort siehst du alle Clips deines Videos als Vorschaubilder, in der Reihenfolge, in der sie vorkommen.\n\nWas du dort tun kannst: Clips per Ziehen neu anordnen und unpassende Clips mit „✕ Entfernen“ durch andere ersetzen. Das Video muss dafür nicht komplett neu gerendert werden, es läuft nur der Zusammenfüge-Schritt neu, deshalb geht es schnell.\n\nBearbeitet wird das erste gewählte Format, normalerweise das 16:9-Video. Die anderen Formate (zum Beispiel der Short) bleiben unverändert. Pre-Intro und Outro sind gesperrt und bleiben am Anfang beziehungsweise Ende."),
 "status": ("Statuszeile", "Zeigt in Textform, was das Programm gerade tut oder zuletzt getan hat. Das ist die erste Stelle, auf die du schauen solltest, wenn etwas nicht klappt.\n\nBeispiele: „bereit“, „Rendere Segmente…“, „Fertig: Song.mp4 + Song_Short.mp4“. Bei Problemen steht hier die Ursache, etwa „bitte Musik auswählen“, „Mindestens ein Format muss aktiv sein“, „Keine Quelle aktiv“ oder „keine externen Clips gefunden“.\n\nBei mehreren Formaten steht vorn, welches gerade bearbeitet wird, zum Beispiel „[2/2 · Short 58s]“. Bei einem Fehler erscheint die Meldung hier und das Programm gibt die Bedienelemente wieder frei."),
 "watch": ("Video anschauen", "Öffnet das zuletzt fertiggestellte Video im Standard-Videoplayer deines Computers, ohne dass du erst den Ordner suchen musst.\n\nGibt es noch kein Video, zeigt die Statuszeile „kein Video vorhanden“. Ältere Videos findest du über den Button „Output-Ordner“.\n\nMit mehreren Formaten wird das Hauptvideo geöffnet, normalerweise die 16:9-Fassung. Die Shorts findest du im Output-Ordner."),
 "outfolder": ("Output-Ordner", "Öffnet den Ordner output/ im Windows-Explorer. Dort liegen alle fertigen Videos.\n\nSo findest du, was du suchst: Das normale Video heißt Musiktitel.mp4, die 9:16-Fassung Musiktitel_9x16.mp4 und der 58-Sekunden-Short Musiktitel_Short.mp4. Shorts, die du per URL erstellt hast, liegen im Unterordner Shorts und heißen Short_… mit der Video-Kennung.\n\nVon hier aus kannst du Videos kopieren, umbenennen oder hochladen. Das Programm löscht im Output-Ordner nie etwas von selbst. Beim Deinstallieren kannst du ihn vorher sichern."),
 "youtube": ("YouTube", "Öffnet die Upload-Seite von YouTube in deinem Browser, damit du dein fertiges Video direkt hochladen kannst.\n\nWas du noch selbst tun musst: Auf der YouTube-Seite wählst du das Video aus. Am einfachsten geht das, wenn du vorher über „Output-Ordner“ den Ordner mit den fertigen Videos öffnest und die Datei von dort in die Seite ziehst.\n\nDu musst bei YouTube angemeldet sein. Das Programm selbst lädt nichts hoch und liest keine Zugangsdaten."),
 "ed_cancel": ("Abbrechen / Zurück", "Verlässt den Clip-Editor und kehrt zum Hauptfenster zurück, ohne etwas zu speichern.\n\nAlle Änderungen, die du noch nicht mit „Übernehmen“ gespeichert hast, werden verworfen: Entfernte Clips kommen zurück, die ursprüngliche Reihenfolge wird wiederhergestellt. Dein Video bleibt genau so, wie es nach dem Render war.\n\nTipp: Möchtest du nur einen einzelnen Schritt rückgängig machen, klicke bei einem ersetzten Clip noch einmal auf „✕ Entfernen“, dann bekommst du einen anderen Ersatz. Mit „Abbrechen“ gehen alle offenen Änderungen verloren."),
 "ed_apply": ("Übernehmen", "Speichert deine Änderungen im Video: die neue Reihenfolge der Clips und alle ersetzten Clips.\n\nDas geht schnell, weil nur der Zusammenfüge-Schritt neu läuft, nicht das ganze Video. Während des Speicherns zeigt die Statuszeile den Fortschritt, bitte so lange nichts schließen. Danach ist das Video im Ordner output/ aktualisiert.\n\nDer Button ist erst aktiv, wenn du etwas geändert hast. Solange du „Übernehmen“ nicht klickst, bleibt das fertige Video unverändert und alle Änderungen sind nur vorgemerkt."),
 "ed_grid": ("Clip-Übersicht", "Jede Kachel ist ein Clip deines Videos, in der Reihenfolge, in der er vorkommt (von links nach rechts, Zeile für Zeile).\n\n• Anordnen: Kachel mit gedrückter linker Maustaste auf eine andere Kachel ziehen. Beide tauschen die Plätze; das Ziel leuchtet beim Ziehen farbig auf.\n• Entfernen: Mit „✕ Entfernen“ unter der Kachel wird der Clip durch einen zufälligen anderen Clip genau gleicher Länge ersetzt, am liebsten durch einen, der noch nicht im Video vorkommt. Dadurch bleibt das Video exakt so lang wie dein Song und die Musik bleibt im Takt.\n• Gesperrte Kacheln (Schloss) sind Pre-Intro und Outro. Sie lassen sich nicht verschieben.\n\nDu kannst beliebig oft ändern. Erst „Übernehmen“ macht die Änderungen endgültig, „Abbrechen“ verwirft alles."),
 "ed_remove": ("✕ Entfernen", "Ersetzt diesen Clip durch einen anderen. Der Ersatz wird zufällig gewählt und ist genau so lang wie der alte Clip, damit das Video so lang bleibt wie der Song und alle Schnitte weiter auf dem Beat liegen. Das Programm bevorzugt Clips, die noch nicht im Video vorkommen.\n\nSchnell geht das, weil nur dieser eine Ausschnitt neu gerendert wird. Die Statuszeile zeigt „Ersetze Clip … (wird neu gerendert)“ und danach „Clip ersetzt — mit „Übernehmen“ speichern“.\n\nGefällt dir der neue Clip nicht, klicke noch einmal auf „✕ Entfernen“, dann wird wieder ein anderer gewählt. Mit „Abbrechen“ stellst du alle ersetzten Clips wieder her, mit „Übernehmen“ speicherst du sie. Gibt es keinen passenden Ersatz, meldet die Statuszeile, dass es nicht geklappt hat."),
 "ed_lock": ("Gesperrter Clip", "Das Schloss markiert Pre-Intro und Outro. Diese beiden sind fest: Das Pre-Intro bleibt immer am Anfang, das Outro immer am Ende.\n\nSie lassen sich weder verschieben noch entfernen. Der Grund: Beide haben ihren eigenen Ton, der vor beziehungsweise nach der Musik abläuft. Würden sie mitten ins Video rutschen, wäre der Ton durcheinander.\n\nAlle anderen Clips (ohne Schloss) kannst du frei anordnen und ersetzen."),
 "ed_status": ("Statuszeile", "Zeigt im Clip-Editor den aktuellen Stand, zum Beispiel „Clip ersetzt — mit „Übernehmen“ speichern“, „Ersetze Clip … (wird neu gerendert)“ oder den Fortschritt beim Speichern.\n\nSo erkennst du, ob noch etwas offen ist: Steht dort der Hinweis „mit „Übernehmen“ speichern“, sind Änderungen vorgemerkt, aber noch nicht im Video. Erst nach „Übernehmen“ sind sie gespeichert.\n\nBei Problemen, etwa wenn ein Clip nicht ersetzt werden konnte, steht hier die Ursache."),
},
"en": {
 "music": ("Choose music", "Here you choose the music file (MP3 or WAV) your video will be made from. Click the button, find the file in the Windows dialog and confirm. The file name then appears next to the button.\n\nWhat happens: the program analyses the beat of the music and places every cut exactly on it. The finished video is as long as your song. The video's file name also comes from here: “MySong.mp3” becomes “MySong.mp4”. The same name can optionally be shown as a title in the video.\n\nImportant: without music nothing can be rendered. If it is missing, the status line says “please choose music”. If you choose another file later, the names of the new videos change too."),
 "reset": ("Reset", "Sets the program back to its initial state: chosen music, formats, cutting speed, pool, mood and all switches.\n\nIf a cache is present, the program asks whether it should be emptied as well. The cache holds clips that have already been prepared and makes repeated rendering faster, but it can take up a lot of disk space. If you empty it, the next render takes a little longer, nothing else happens.\n\nYour finished videos in the output/ folder and your own clips are always kept. While a render is running, Reset is locked."),
 "help": ("Settings (⚙)", "The gear opens the menu with everything that does not belong directly to building the video:\n\n• Language / Sprache: Deutsch, English or Español. Restart the program once after changing it.\n• Change mode: Simple (just drop in music and videos) or Advanced (all settings). Also needs a restart.\n• Click help: afterwards you click any area and get exactly this kind of explanation. The mode ends by itself after one explanation.\n• Quick guide: a short overview of the eight steps.\n• PDF manual: the detailed manual in your language (the PDF must be next to the EXE).\n• Latest version (GitHub): opens the download page for updates.\n• Mobile version (MVG Mobile): opens the GitHub page of the phone version."),
 "fmt": ("Format", "Here you decide which video formats are created when rendering. You can tick several at once: each format is built in its own pass and saved as its own file in the output/ folder. At least one format must stay active, otherwise the status line shows a notice (“At least one format must be active”).\n\nThe default is 16:9 plus the 58-second short, i.e. the classic video and the version for Instagram, YouTube Shorts and TikTok in one go.\n\nKeep in mind: every additional format makes rendering longer. The progress bar counts across all formats, the status line shows which one is being worked on."),
 "fmt_normal": ("Normal 16:9", "The classic landscape format for YouTube, TVs and computer screens. The video is as long as your music.\n\nOnly in this format (and in full-length 9:16) are pre-intro, intro, outro and title possible, provided you switched them on under “Sources & options”.\n\nFile name: SongTitle.mp4 in the output/ folder.\n\nThis video is also the main video: after rendering, the clip editor (“Edit clips”) always works on the first chosen format, normally this one."),
 "fmt_shorts": ("Normal 9:16", "Portrait format (as you hold a phone) at the full length of your music. Meant for cases where the whole song should be shown on a phone, for example as a story or a long portrait video.\n\nPre-intro, intro, outro and title are possible if they are switched on.\n\nFile name: SongTitle_9x16.mp4 in the output/ folder.\n\nIf you only want a short version for Reels, Shorts or TikTok, use the 58-second short instead."),
 "fmt_short58": ("Short 9:16 · 58 s", "A short portrait video of exactly 58 seconds for Instagram Reels, YouTube Shorts and TikTok.\n\nHow the excerpt is made: the program automatically finds the strongest section of your song (usually the chorus) and cuts exactly that piece. Because part of the middle is missing as a result, picture and sound fade in softly at the start and fade out at the end.\n\nThe short is always created without pre-intro, intro, outro and title so that it stays exactly 58 seconds long. If the song is shorter than 58 seconds, it is used in full.\n\nFile name: SongTitle_Short.mp4 in the output/ folder."),
 "speed": ("Cutting", "Determines how fast the clips change. All cuts are placed exactly on the beat of the music; the speed only decides how many beats lie between two cuts.\n\n• Gentle: about every 3.5 seconds, calm and atmospheric\n• Slow: about every 2.2 seconds, cinematic\n• Normal: about every 1.7 seconds, balanced (default)\n• Fast: about every 0.95 seconds, energetic\n• Random: every cut random between 0.95 and 3.5 seconds, varied and unpredictable\n\nTip: Gentle or Slow suit ballads, Normal usually suits bachata and merengue, Fast suits reggaeton and dance music. More cuts mean more clips are needed and a slightly longer render time."),
 "pool": ("Internal clip pool", "The internal pool is your own video clips, sorted in the mood folders in the program folder: clips/traurig, clips/neutral, clips/gluecklich and clips/animations.\n\nVery important: the program ships without clips. You have to fill these folders with videos yourself. If they are empty and no other source is active, there is nothing to cut, and the status line reports that no clips were found.\n\nIn this area you decide which mood is used, how many clips from it, and whether you pick individual clips yourself. You can switch the pool off completely with the “Internal pool” switch under “Sources & options”, for example if you only want to use your own clips from another folder."),
 "mood": ("Choose your mood", "Here you choose which mood folders the clips are taken from: Sad, Neutral, Happy and/or Animation. You can combine as many as you like; clips are then mixed from all ticked folders.\n\nIf no mood is ticked, the program uses all folders.\n\nTip: match the mood to the music so that picture and sound work together: calm, melancholic songs with “Sad”, dance music with “Happy”. “Animation” is for animated clips that you put in the folder clips/animations/.\n\nHow many clips are actually available is shown in the “Count:” line. If it says 0, the chosen folder is empty."),
 "pool_count": ("Number of clips in the pool", "Sets how many clips from the pool are used for this video.\n\n• Automatic: all available clips of the chosen mood\n• 10, 20 or 30 clips: a random selection of that size\n\nWhat this does: a smaller selection gives the video a more uniform look because the same clips repeat more often. A larger selection brings more variety.\n\nIf you have fewer clips in the folder than set here, the program simply takes all that are there. With very short cuts (Fast or Random) clips repeat sooner, which is normal."),
 "manual": ("Manual selection", "Opens a window with thumbnails of all clips of the chosen mood. One click selects or deselects a clip (blue frame = selected). “All” and “None” make it quicker.\n\nOnly the selected clips are then used for the video. “OK” only becomes active once at least one clip is chosen; with “Cancel” everything stays as before.\n\nWhen this is useful: if certain clips must not appear under any circumstances or only a handful fit the song. Creating the thumbnails can take a moment when there are many clips."),
 "sw_pre": ("Pre-intro", "Your own video with its own sound that plays BEFORE your music, for example a greeting, a channel jingle or a logo clip.\n\nHow to use it: put the video file in the pre-intro/ folder in the program folder and switch “Pre-intro” on. If several files are in the folder, one is chosen at random when rendering.\n\nWhat to keep in mind: the finished video becomes longer than your song because the pre-intro is placed in front. The pre-intro's sound is not covered by the music; the music only starts afterwards. In the clip editor the pre-intro always stays at the very front.\n\nDefault: off. In the 58-second short the pre-intro is never used. If the folder is empty, nothing happens."),
 "sw_intro": ("Intro", "A silent video at the start, under which the music is already playing, for example an animated opening or a title image.\n\nHow to use it: put the file in the intro/ folder and switch “Intro” on. If there are several files, one is chosen at random. The sound of the intro video is not used; it plays silently under your music.\n\nWhat to keep in mind: the intro counts towards the song length. The video does not get longer, only its beginning consists of the intro. Then the normal clips follow until the music ends. If the intro is longer than the song, it is shortened.\n\nIn the 58-second short the intro is never used. If the folder is empty, nothing happens."),
 "sw_outro": ("Outro", "Your own video with its own sound that is appended AFTER the music, for example an end card, a reminder to subscribe or your logo.\n\nHow to use it: put the file in the outro/ folder and switch “Outro” on. If there are several files, one is chosen at random.\n\nWhat to keep in mind: the finished video becomes longer than the song because the outro is attached at the end. It has its own sound; the music ends before. In the clip editor the outro always stays at the end and cannot be moved.\n\nIn the 58-second short the outro is never used. If the folder is empty, nothing happens."),
 "sw_title": ("Title", "Shows the music title at the bottom left of the video (Arial Bold with a light shadow).\n\nWhere the title comes from: it is created automatically from the file name of your music. “Don Miguel - Fuego.mp3” becomes “Don Miguel - Fuego”. Characters that are not allowed in Windows file names, such as colons, are removed. If you want a different title, rename the music file.\n\nWhen it appears: after the intro, fading in softly, and after a few seconds it fades out again. So it does not stay on screen for the whole video.\n\nThe 58-second short never has a title."),
 "sw_pool": ("Internal pool (switch)", "Switches the clips from the mood folders on or off.\n\n• Only the pool on: the video consists of your clips of the chosen mood.\n• Pool off: only external clips and photos are used. At least one of those sources must then be active, otherwise the status line says “No source active”.\n• Pool and external clips both on: this is mix mode. External clips are preferred, the pool fills up.\n\nTip: mix mode is good when you have a few special clips for one song and want to fill the rest with your standard material."),
 "sw_ext": ("External clips", "Uses videos from a folder of your choice, for example material that fits only this one song. You pick the folder with “Choose external clips…”. How many videos the program found in it is shown in the “Count:” line.\n\nHow they are used: if the internal pool and external clips are both active, external clips are preferred (about 70 %) and the pool fills up. With only external clips, they are repeated when needed until the music ends. MP4, MOV, MKV, AVI and WEBM are supported.\n\nNote: if the folder is chosen but nothing is found, the status line says “no external clips found”."),
 "sw_photos": ("External photos", "Uses photos (JPG, PNG, WEBP) from a folder of your choice as still images in the video. You pick the folder with “Choose external photos…”.\n\nHow they are used: each photo stays on screen for the length of one cut, like a video clip. Photos and videos can be mixed. If enough video clips are available, photos are not used for very short cuts so the video stays lively.\n\nNote: if the folder is chosen but nothing is found, the status line says “no external photos found”."),
 "short_url": ("Short from URL", "Creates a 45-second portrait short from a YouTube video, without any music or clips of your own.\n\nHow it works:\n1. Open the video in your browser and copy the link from the address bar (Ctrl+C).\n2. Click this button. You do not have to paste the link anywhere: the program reads it straight from the clipboard. That is why you see no input field after the click, only the status line with the progress.\n\nWhat happens: the program downloads the video, automatically picks the strongest section, cuts 45 seconds in 9:16 format with the original sound and fades picture and sound in and out softly. The result is in the folder output/Shorts.\n\nThe first time, a small extra component (deno.exe) is downloaded, so internet is required. The video must be at least 45 seconds long. If there is no valid YouTube link on the clipboard, a message tells you. Please only use videos you have the rights to."),
 "btn_ext": ("Choose external clips…", "Opens a Windows dialog in which you choose the folder with your own video clips, and thereby switches “External clips” on. The program then uses all videos in that folder.\n\nGood to know: the Windows folder dialog shows only folders, not files. A folder full of videos therefore looks empty in it. That is normal. Just click the folder you want and confirm with “Select folder”. How many videos were found is shown afterwards in the “Count:” line.\n\nIf you cancel the selection, the “External clips” option is switched off again. Subfolders are not searched."),
 "btn_photos": ("Choose external photos…", "Opens a Windows dialog in which you choose the folder with your own photos, and thereby switches “External photos” on. The program then uses all photos (JPG, PNG, WEBP) in that folder.\n\nGood to know: the Windows folder dialog shows only folders, not files. A folder full of photos therefore looks empty in it. That is normal. Just click the folder you want and confirm with “Select folder”. How many photos were found is shown afterwards in the “Count:” line.\n\nIf you cancel the selection, “External photos” is switched off again. Subfolders are not searched."),
 "count": ("Count:", "Shows how many clips (and, with photos active, how many photos) are available with the current settings. The number changes as soon as you change mood, pool or folders.\n\nHow to read it: if it says 0, there is nothing to cut. Then clips are missing in the chosen mood folder, the external folder is empty or the matching source is switched off.\n\nIt is also the best check after “Choose external clips…”: if you see a sensible number there, the program has recognised your folder correctly, even if the Windows dialog showed it as empty."),
 "sources": ("Sources & options", "Here you decide where the picture material comes from and which extras are added.\n\n• Extras: pre-intro, intro, outro and title\n• Sources: internal pool, external clips and external photos, individually or mixed\n• Short from URL: a short from a YouTube link\n\nEvery switch has its own explanation: start click help again and click the switch directly.\n\nAt least one picture source must be active, otherwise nothing can be rendered. The extras are only meant for the formats Normal 16:9 and Normal 9:16; the 58-second short always stays plain."),
 "render": ("Render", "Starts the creation of your video. This is the main button, everything else is preparation.\n\nWhat happens: the program analyses the music, places the cuts on the beat, renders all segments one by one and then joins picture and sound. For every chosen format a file is created in the output/ folder.\n\nWhile rendering: the progress bar and the status line show the progress, and most controls are locked. Several formats take correspondingly longer. At the end a sound tells you the video is finished.\n\nAfterwards you can watch it with “Watch video” or refine it with “Edit clips”. If the status line reports something like “please choose music” or “No source active” beforehand, a requirement is still missing."),
 "edit": ("Edit clips", "Only becomes active after a finished render and opens the clip editor. There you see all clips of your video as thumbnails, in the order in which they appear.\n\nWhat you can do there: rearrange clips by dragging and replace unsuitable clips with “✕ Remove”. The video does not have to be fully re-rendered for this; only the joining step runs again, so it is quick.\n\nThe first chosen format is edited, normally the 16:9 video. The other formats (for example the short) stay unchanged. Pre-intro and outro are locked and stay at the start and end respectively."),
 "status": ("Status line", "Shows in text form what the program is doing right now or did last. This is the first place to look when something does not work.\n\nExamples: “ready”, “Rendering segments…”, “Done: Song.mp4 + Song_Short.mp4”. When there are problems, the cause is shown here, for example “please choose music”, “At least one format must be active”, “No source active” or “no external clips found”.\n\nWith several formats, the front shows which one is being processed, for example “[2/2 · Short 58s]”. On an error the message appears here and the program releases the controls again."),
 "watch": ("Watch video", "Opens the most recently finished video in your computer's default video player, without you having to look for the folder first.\n\nIf there is no video yet, the status line says “no video available”. You find older videos with the “Output folder” button.\n\nWith several formats the main video is opened, normally the 16:9 version. You find the shorts in the output folder."),
 "outfolder": ("Output folder", "Opens the output/ folder in Windows Explorer. All finished videos are there.\n\nHow to find what you are looking for: the normal video is called SongTitle.mp4, the 9:16 version SongTitle_9x16.mp4 and the 58-second short SongTitle_Short.mp4. Shorts you created from a URL are in the subfolder Shorts and are called Short_… followed by the video ID.\n\nFrom here you can copy, rename or upload videos. The program never deletes anything in the output folder by itself. Before uninstalling you can back it up."),
 "youtube": ("YouTube", "Opens the YouTube upload page in your browser so you can upload your finished video directly.\n\nWhat you still have to do yourself: on the YouTube page you choose the video. The easiest way is to first open the folder with the finished videos via “Output folder” and drag the file from there onto the page.\n\nYou must be signed in to YouTube. The program itself uploads nothing and reads no login data."),
 "ed_cancel": ("Cancel / Back", "Leaves the clip editor and returns to the main window without saving anything.\n\nAll changes you have not yet saved with “Apply” are discarded: removed clips come back, the original order is restored. Your video stays exactly as it was after the render.\n\nTip: if you only want to undo a single step, click “✕ Remove” again on a replaced clip to get a different replacement. “Cancel” loses all open changes."),
 "ed_apply": ("Apply", "Saves your changes in the video: the new order of the clips and all replaced clips.\n\nThis is quick because only the joining step runs again, not the whole video. While saving, the status line shows the progress; please do not close anything until it is done. Afterwards the video in the output/ folder is updated.\n\nThe button only becomes active once you have changed something. As long as you do not click “Apply”, the finished video stays unchanged and all changes are only noted down."),
 "ed_grid": ("Clip overview", "Every tile is one clip of your video, in the order in which it appears (left to right, row by row).\n\n• Rearranging: drag a tile with the left mouse button held down onto another tile. The two swap places; the target lights up in colour while you drag.\n• Removing: “✕ Remove” under the tile replaces the clip with a random other clip of exactly the same length, preferably one that does not yet appear in the video. This keeps the video exactly as long as your song and the music stays in time.\n• Locked tiles (padlock) are the pre-intro and the outro. They cannot be moved.\n\nYou can change as often as you like. Only “Apply” makes the changes final, “Cancel” discards everything."),
 "ed_remove": ("✕ Remove", "Replaces this clip with another one. The replacement is chosen at random and is exactly as long as the old clip, so the video stays as long as the song and all cuts stay on the beat. The program prefers clips that do not yet appear in the video.\n\nIt is quick because only this one excerpt is rendered again. The status line shows “Replacing clip … (re-rendering)” and then “Clip replaced — save with “Apply””.\n\nIf you do not like the new clip, click “✕ Remove” again and another one is chosen. With “Cancel” you restore all replaced clips, with “Apply” you save them. If there is no suitable replacement, the status line says it did not work."),
 "ed_lock": ("Locked clip", "The padlock marks the pre-intro and the outro. These two are fixed: the pre-intro always stays at the start, the outro always at the end.\n\nThey can neither be moved nor removed. The reason: both have their own sound, which plays before or after the music. If they slipped into the middle of the video, the sound would be in a mess.\n\nAll other clips (without a padlock) you can freely rearrange and replace."),
 "ed_status": ("Status line", "Shows the current state in the clip editor, for example “Clip replaced — save with “Apply””, “Replacing clip … (re-rendering)” or the progress while saving.\n\nHow to tell whether something is still open: if it says “save with “Apply””, changes are noted but not yet in the video. They are only saved after “Apply”.\n\nWhen there are problems, for example if a clip could not be replaced, the cause is shown here."),
},
"es": {
 "music": ("Elegir música", "Aquí eliges el archivo de música (MP3 o WAV) a partir del cual se creará tu vídeo. Pulsa el botón, busca el archivo en el diálogo de Windows y confirma. El nombre del archivo aparece después junto al botón.\n\nQué ocurre: el programa analiza el ritmo de la música y coloca todos los cortes exactamente sobre él. El vídeo terminado dura lo mismo que tu canción. El nombre del vídeo también sale de aquí: «MiCancion.mp3» se convierte en «MiCancion.mp4». Ese mismo nombre puede mostrarse opcionalmente como título en el vídeo.\n\nImportante: sin música no se puede renderizar. Si falta, la línea de estado dice «elige primero una música». Si más tarde eliges otro archivo, también cambian los nombres de los vídeos nuevos."),
 "reset": ("Reset", "Devuelve el programa a su estado inicial: música elegida, formatos, velocidad de corte, grupo, ánimo y todos los interruptores.\n\nSi hay una memoria caché, el programa pregunta si también debe vaciarse. La caché contiene clips ya preparados y hace más rápido renderizar de nuevo, pero puede ocupar mucho espacio en disco. Si la vacías, el siguiente renderizado tarda un poco más; no ocurre nada más.\n\nTus vídeos terminados en la carpeta output/ y tus propios clips siempre se conservan. Mientras se renderiza, Reset está bloqueado."),
 "help": ("Ajustes (⚙)", "El engranaje abre el menú con todo lo que no forma parte directamente de la creación del vídeo:\n\n• Idioma / Language: Deutsch, English o Español. Después de cambiarlo, reinicia el programa una vez.\n• Cambiar modo: Sencillo (solo arrastra música y vídeos) o Avanzado (todos los ajustes). También requiere reiniciar.\n• Ayuda con clic: después haces clic en cualquier zona y recibes justo este tipo de explicación. El modo termina solo tras una explicación.\n• Guía rápida: un breve resumen de los ocho pasos.\n• Manual PDF: el manual detallado en tu idioma (el PDF debe estar junto al EXE).\n• Última versión (GitHub): abre la página de descargas para actualizaciones.\n• Versión móvil (MVG Móvil): abre la página de GitHub de la versión para el teléfono."),
 "fmt": ("Formato", "Aquí decides qué formatos de vídeo se crean al renderizar. Puedes marcar varios a la vez: cada formato se construye en su propia pasada y se guarda como archivo aparte en la carpeta output/. Al menos un formato debe seguir activo; si no, la línea de estado muestra un aviso («Debe haber al menos un formato activo»).\n\nPor defecto son 16:9 y el short de 58 segundos, es decir, el vídeo clásico y la versión para Instagram, YouTube Shorts y TikTok de una sola vez.\n\nTen en cuenta: cada formato adicional alarga el tiempo de renderizado. La barra de progreso cuenta todos los formatos juntos; la línea de estado indica cuál se está procesando."),
 "fmt_normal": ("Normal 16:9", "El clásico formato horizontal para YouTube, televisores y pantallas de ordenador. El vídeo dura lo mismo que tu música.\n\nSolo en este formato (y en el 9:16 de duración completa) son posibles pre-intro, intro, outro y título, siempre que los hayas activado en «Fuentes y opciones».\n\nNombre del archivo: TituloDeLaCancion.mp4 en la carpeta output/.\n\nEste vídeo es también el vídeo principal: después de renderizar, el editor de clips («Editar clips») trabaja siempre con el primer formato elegido, normalmente este."),
 "fmt_shorts": ("Normal 9:16", "Formato vertical (como sujetas el teléfono) con la duración completa de tu música. Pensado para cuando toda la canción debe verse en el teléfono, por ejemplo como historia o como vídeo vertical largo.\n\nPre-intro, intro, outro y título son posibles si están activados.\n\nNombre del archivo: TituloDeLaCancion_9x16.mp4 en la carpeta output/.\n\nSi solo quieres una versión corta para Reels, Shorts o TikTok, usa en su lugar el short de 58 segundos."),
 "fmt_short58": ("Short 9:16 · 58 s", "Un vídeo vertical corto de exactamente 58 segundos para Instagram Reels, YouTube Shorts y TikTok.\n\nCómo se crea el fragmento: el programa busca automáticamente la sección más fuerte de tu canción (normalmente el estribillo) y corta justo ese trozo. Como así falta una parte del medio, imagen y sonido entran suavemente al principio y se atenúan al final.\n\nEl short se crea siempre sin pre-intro, intro, outro ni título, para que dure exactamente 58 segundos. Si la canción dura menos de 58 segundos, se usa completa.\n\nNombre del archivo: TituloDeLaCancion_Short.mp4 en la carpeta output/."),
 "speed": ("Corte", "Determina la rapidez con que cambian los clips. Todos los cortes se colocan exactamente sobre el ritmo de la música; la velocidad solo decide cuántos tiempos hay entre dos cortes.\n\n• Suave: aprox. cada 3,5 segundos, tranquilo y atmosférico\n• Lento: aprox. cada 2,2 segundos, cinematográfico\n• Normal: aprox. cada 1,7 segundos, equilibrado (por defecto)\n• Rápido: aprox. cada 0,95 segundos, enérgico\n• Aleatorio: cada corte al azar entre 0,95 y 3,5 segundos, variado e impredecible\n\nConsejo: Suave o Lento van bien con baladas, Normal suele ir bien con bachata y merengue, Rápido con reguetón y música de baile. Más cortes significan que hacen falta más clips y un tiempo de renderizado algo mayor."),
 "pool": ("Grupo de clips interno", "El grupo interno son tus propios clips de vídeo, ordenados en las carpetas de ánimo dentro de la carpeta del programa: clips/traurig, clips/neutral, clips/gluecklich y clips/animations.\n\nMuy importante: el programa se entrega sin clips. Tienes que llenar estas carpetas con vídeos tú mismo. Si están vacías y no hay otra fuente activa, no hay nada que cortar y la línea de estado indica que no se encontraron clips.\n\nEn esta zona decides qué ánimo se usa, cuántos clips de él y si eliges clips concretos tú mismo. Puedes apagar el grupo por completo con el interruptor «Grupo interno» en «Fuentes y opciones», por ejemplo si solo quieres usar tus propios clips de otra carpeta."),
 "mood": ("Elige tu ánimo", "Aquí eliges de qué carpetas de ánimo se toman los clips: Triste, Neutral, Alegre y/o Animación. Puedes combinar todas las que quieras; entonces se mezclan clips de todas las carpetas marcadas.\n\nSi no hay ningún ánimo marcado, el programa usa todas las carpetas.\n\nConsejo: adapta el ánimo a la música para que imagen y sonido se refuercen: canciones tranquilas y melancólicas con «Triste», música de baile con «Alegre». «Animación» es para clips animados que dejas en la carpeta clips/animations/.\n\nCuántos clips hay realmente disponibles lo muestra la línea «Cantidad:». Si pone 0, la carpeta elegida está vacía."),
 "pool_count": ("Cantidad de clips en el grupo", "Define cuántos clips del grupo se usan para este vídeo.\n\n• Automático: todos los clips disponibles del ánimo elegido\n• 10, 20 o 30 clips: una selección aleatoria de ese tamaño\n\nQué efecto tiene: una selección más pequeña da al vídeo un aspecto más uniforme porque los mismos clips se repiten más a menudo. Una selección mayor aporta más variedad.\n\nSi tienes menos clips en la carpeta de los que has indicado, el programa simplemente usa todos los que hay. Con cortes muy cortos (Rápido o Aleatorio) los clips se repiten antes; es normal."),
 "manual": ("Selección manual", "Abre una ventana con miniaturas de todos los clips del ánimo elegido. Un clic selecciona o deselecciona un clip (marco azul = seleccionado). Con «Todos» y «Ninguno» vas más rápido.\n\nDespués solo se usan los clips seleccionados para el vídeo. «OK» solo se activa cuando hay al menos un clip elegido; con «Cancelar» todo queda como antes.\n\nCuándo es útil: si ciertos clips no deben aparecer bajo ningún concepto o solo unos pocos encajan con la canción. Crear las miniaturas puede tardar un momento si hay muchos clips."),
 "sw_pre": ("Pre-intro", "Tu propio vídeo con su propio sonido que se reproduce ANTES de tu música, por ejemplo un saludo, una cortinilla del canal o un clip con tu logo.\n\nCómo usarlo: pon el archivo de vídeo en la carpeta pre-intro/ dentro de la carpeta del programa y activa «Pre-intro». Si hay varios archivos en la carpeta, al renderizar se elige uno al azar.\n\nQué debes tener en cuenta: el vídeo terminado dura más que tu canción porque el pre-intro se coloca delante. El sonido del pre-intro no queda tapado por la música; la música empieza después. En el editor de clips el pre-intro se queda siempre al principio del todo.\n\nPor defecto: desactivado. En el short de 58 segundos el pre-intro nunca se usa. Si la carpeta está vacía, no pasa nada."),
 "sw_intro": ("Intro", "Un vídeo mudo al principio, bajo el cual la música ya está sonando, por ejemplo una animación de apertura o una imagen de título.\n\nCómo usarlo: pon el archivo en la carpeta intro/ y activa «Intro». Si hay varios archivos, se elige uno al azar. El sonido del vídeo de intro no se usa; se reproduce mudo bajo tu música.\n\nQué debes tener en cuenta: el intro cuenta como parte de la duración de la canción. El vídeo no se alarga; solo su comienzo consiste en el intro. Después siguen los clips normales hasta que acaba la música. Si el intro es más largo que la canción, se acorta.\n\nEn el short de 58 segundos el intro nunca se usa. Si la carpeta está vacía, no pasa nada."),
 "sw_outro": ("Outro", "Tu propio vídeo con su propio sonido que se añade DESPUÉS de la música, por ejemplo una pantalla final, un aviso para suscribirse o tu logo.\n\nCómo usarlo: pon el archivo en la carpeta outro/ y activa «Outro». Si hay varios archivos, se elige uno al azar.\n\nQué debes tener en cuenta: el vídeo terminado dura más que la canción porque el outro se añade al final. Tiene su propio sonido; la música termina antes. En el editor de clips el outro se queda siempre al final y no se puede mover.\n\nEn el short de 58 segundos el outro nunca se usa. Si la carpeta está vacía, no pasa nada."),
 "sw_title": ("Título", "Muestra el título de la música abajo a la izquierda en el vídeo (letra Arial Bold con una sombra ligera).\n\nDe dónde sale el título: se crea automáticamente a partir del nombre del archivo de tu música. «Don Miguel - Fuego.mp3» se convierte en «Don Miguel - Fuego». Los caracteres que Windows no permite en nombres de archivo, como los dos puntos, se eliminan. Si quieres otro título, cambia el nombre del archivo de música.\n\nCuándo aparece: después del intro, entrando suavemente, y tras unos segundos vuelve a desvanecerse. Es decir, no se queda en pantalla durante todo el vídeo.\n\nEl short de 58 segundos nunca lleva título."),
 "sw_pool": ("Grupo interno (interruptor)", "Activa o desactiva los clips de las carpetas de ánimo.\n\n• Solo el grupo activado: el vídeo se compone de tus clips del ánimo elegido.\n• Grupo desactivado: solo se usan clips y fotos externos. Entonces al menos una de esas fuentes debe estar activa; si no, la línea de estado dice «Ninguna fuente activa».\n• Grupo y clips externos activados a la vez: es el modo mixto. Se prefieren los clips externos y el grupo completa.\n\nConsejo: el modo mixto va bien cuando tienes unos pocos clips especiales para una canción y quieres completar el resto con tu material habitual."),
 "sw_ext": ("Clips externos", "Usa vídeos de una carpeta que elijas, por ejemplo material que solo encaja con esta canción. La carpeta se elige con «Elegir clips externos…». Cuántos vídeos ha encontrado el programa en ella lo muestra la línea «Cantidad:».\n\nCómo se usan: si el grupo interno y los clips externos están activos a la vez, se prefieren los externos (aprox. 70 %) y el grupo completa. Con solo clips externos, se repiten cuando hace falta hasta que acaba la música. Se admiten MP4, MOV, MKV, AVI y WEBM.\n\nNota: si se elige la carpeta pero no se encuentra nada, la línea de estado dice «no se encontraron clips externos»."),
 "sw_photos": ("Fotos externas", "Usa fotos (JPG, PNG, WEBP) de una carpeta que elijas como imágenes fijas en el vídeo. La carpeta se elige con «Elegir fotos externas…».\n\nCómo se usan: cada foto permanece en pantalla durante un corte, igual que un clip de vídeo. Fotos y vídeos se pueden mezclar. Si hay suficientes clips de vídeo, las fotos no se usan en cortes muy cortos para que el vídeo siga siendo dinámico.\n\nNota: si se elige la carpeta pero no se encuentra nada, la línea de estado dice «no se encontraron fotos externas»."),
 "short_url": ("Short desde URL", "Crea un short vertical de 45 segundos a partir de un vídeo de YouTube, sin música ni clips propios.\n\nCómo funciona:\n1. Abre el vídeo en tu navegador y copia el enlace de la barra de direcciones (Ctrl+C).\n2. Pulsa este botón. No tienes que pegar el enlace en ningún sitio: el programa lo lee directamente del portapapeles. Por eso, tras el clic no ves ningún campo de entrada, solo la línea de estado con el progreso.\n\nQué ocurre: el programa descarga el vídeo, elige automáticamente la sección más fuerte, corta 45 segundos en formato 9:16 con el sonido original y hace entrar y salir imagen y sonido suavemente. El resultado queda en la carpeta output/Shorts.\n\nLa primera vez se descarga un pequeño componente adicional (deno.exe), por lo que hace falta internet. El vídeo debe durar al menos 45 segundos. Si no hay un enlace de YouTube válido en el portapapeles, un mensaje te lo indica. Usa solo vídeos sobre los que tengas derechos."),
 "btn_ext": ("Elegir clips externos…", "Abre un diálogo de Windows en el que eliges la carpeta con tus propios clips de vídeo y, con ello, activa «Clips externos». Después el programa usa todos los vídeos de esa carpeta.\n\nBueno saber: el diálogo de carpetas de Windows muestra solo carpetas, no archivos. Por eso una carpeta llena de vídeos parece vacía en él. Es normal. Haz clic en la carpeta que quieras y confirma con «Seleccionar carpeta». Cuántos vídeos se han encontrado lo muestra después la línea «Cantidad:».\n\nSi cancelas la selección, la opción «Clips externos» se vuelve a desactivar. Las subcarpetas no se examinan."),
 "btn_photos": ("Elegir fotos externas…", "Abre un diálogo de Windows en el que eliges la carpeta con tus propias fotos y, con ello, activa «Fotos externas». Después el programa usa todas las fotos (JPG, PNG, WEBP) de esa carpeta.\n\nBueno saber: el diálogo de carpetas de Windows muestra solo carpetas, no archivos. Por eso una carpeta llena de fotos parece vacía en él. Es normal. Haz clic en la carpeta que quieras y confirma con «Seleccionar carpeta». Cuántas fotos se han encontrado lo muestra después la línea «Cantidad:».\n\nSi cancelas la selección, «Fotos externas» se vuelve a desactivar. Las subcarpetas no se examinan."),
 "count": ("Cantidad:", "Muestra cuántos clips (y, con fotos activas, cuántas fotos) hay disponibles con los ajustes actuales. El número cambia en cuanto modificas el ánimo, el grupo o las carpetas.\n\nCómo leerlo: si pone 0, no hay nada que cortar. Entonces faltan clips en la carpeta de ánimo elegida, la carpeta externa está vacía o la fuente correspondiente está apagada.\n\nTambién es la mejor comprobación después de «Elegir clips externos…»: si ves ahí un número razonable, el programa ha reconocido bien tu carpeta, aunque el diálogo de Windows la mostrara vacía."),
 "sources": ("Fuentes y opciones", "Aquí decides de dónde sale el material de imagen y qué extras se añaden.\n\n• Extras: pre-intro, intro, outro y título\n• Fuentes: grupo interno, clips externos y fotos externas, por separado o mezclados\n• Short desde URL: un short a partir de un enlace de YouTube\n\nCada interruptor tiene su propia explicación: inicia otra vez la ayuda con clic y haz clic directamente en el interruptor.\n\nAl menos una fuente de imagen debe estar activa; si no, no se puede renderizar. Los extras están pensados solo para los formatos Normal 16:9 y Normal 9:16; el short de 58 segundos siempre queda limpio."),
 "render": ("Renderizar", "Inicia la creación de tu vídeo. Es el botón principal; todo lo demás es preparación.\n\nQué ocurre: el programa analiza la música, coloca los cortes sobre el ritmo, renderiza todos los segmentos uno a uno y luego une imagen y sonido. Por cada formato elegido se crea un archivo en la carpeta output/.\n\nDurante el renderizado: la barra de progreso y la línea de estado muestran el avance, y la mayoría de los controles están bloqueados. Varios formatos tardan proporcionalmente más. Al final un sonido avisa de que el vídeo está listo.\n\nDespués puedes verlo con «Ver vídeo» o retocarlo con «Editar clips». Si antes la línea de estado indica algo como «elige primero una música» o «Ninguna fuente activa», aún falta un requisito."),
 "edit": ("Editar clips", "Solo se activa después de un renderizado terminado y abre el editor de clips. Allí ves todos los clips de tu vídeo como miniaturas, en el orden en que aparecen.\n\nQué puedes hacer allí: reordenar clips arrastrándolos y reemplazar clips que no encajan con «✕ Quitar». Para ello no hace falta renderizar de nuevo todo el vídeo; solo se repite el paso de unir, por eso es rápido.\n\nSe edita el primer formato elegido, normalmente el vídeo 16:9. Los demás formatos (por ejemplo el short) no cambian. Pre-intro y outro están bloqueados y se quedan al principio y al final respectivamente."),
 "status": ("Línea de estado", "Muestra en forma de texto lo que el programa está haciendo o hizo por última vez. Es el primer sitio donde mirar cuando algo no funciona.\n\nEjemplos: «listo», «Renderizando segmentos…», «Listo: Cancion.mp4 + Cancion_Short.mp4». Cuando hay problemas, aquí aparece la causa, por ejemplo «elige primero una música», «Debe haber al menos un formato activo», «Ninguna fuente activa» o «no se encontraron clips externos».\n\nCon varios formatos, al principio se indica cuál se está procesando, por ejemplo «[2/2 · Short 58s]». Si hay un error, el mensaje aparece aquí y el programa vuelve a liberar los controles."),
 "watch": ("Ver vídeo", "Abre el último vídeo terminado en el reproductor de vídeo predeterminado de tu ordenador, sin que tengas que buscar antes la carpeta.\n\nSi aún no hay ningún vídeo, la línea de estado dice «no hay vídeo». Los vídeos anteriores los encuentras con el botón «Carpeta de salida».\n\nCon varios formatos se abre el vídeo principal, normalmente la versión 16:9. Los shorts los encuentras en la carpeta de salida."),
 "outfolder": ("Carpeta de salida", "Abre la carpeta output/ en el Explorador de Windows. Ahí están todos los vídeos terminados.\n\nCómo encontrar lo que buscas: el vídeo normal se llama TituloDeLaCancion.mp4, la versión 9:16 TituloDeLaCancion_9x16.mp4 y el short de 58 segundos TituloDeLaCancion_Short.mp4. Los shorts que creaste desde una URL están en la subcarpeta Shorts y se llaman Short_… seguido del identificador del vídeo.\n\nDesde aquí puedes copiar, renombrar o subir vídeos. El programa nunca borra nada por su cuenta en la carpeta de salida. Antes de desinstalar puedes hacer una copia de seguridad."),
 "youtube": ("YouTube", "Abre la página de subida de YouTube en tu navegador para que puedas subir directamente tu vídeo terminado.\n\nLo que aún tienes que hacer tú: en la página de YouTube eliges el vídeo. Lo más fácil es abrir antes con «Carpeta de salida» la carpeta con los vídeos terminados y arrastrar el archivo desde allí a la página.\n\nTienes que haber iniciado sesión en YouTube. El programa en sí no sube nada ni lee datos de acceso."),
 "ed_cancel": ("Cancelar / Atrás", "Sale del editor de clips y vuelve a la ventana principal sin guardar nada.\n\nTodos los cambios que aún no hayas guardado con «Aplicar» se descartan: los clips quitados vuelven y se restablece el orden original. Tu vídeo queda exactamente como estaba tras el renderizado.\n\nConsejo: si solo quieres deshacer un paso, pulsa otra vez «✕ Quitar» en un clip reemplazado para obtener otro reemplazo. Con «Cancelar» se pierden todos los cambios abiertos."),
 "ed_apply": ("Aplicar", "Guarda tus cambios en el vídeo: el nuevo orden de los clips y todos los clips reemplazados.\n\nEs rápido porque solo se repite el paso de unir, no todo el vídeo. Mientras se guarda, la línea de estado muestra el progreso; por favor, no cierres nada hasta que termine. Después el vídeo de la carpeta output/ queda actualizado.\n\nEl botón solo se activa cuando has cambiado algo. Mientras no pulses «Aplicar», el vídeo terminado no cambia y todos los cambios están solo anotados."),
 "ed_grid": ("Vista de clips", "Cada casilla es un clip de tu vídeo, en el orden en que aparece (de izquierda a derecha, fila por fila).\n\n• Reordenar: arrastra una casilla con el botón izquierdo del ratón pulsado sobre otra casilla. Las dos intercambian su sitio; el destino se ilumina de color mientras arrastras.\n• Quitar: «✕ Quitar» bajo la casilla reemplaza el clip por otro clip aleatorio de exactamente la misma duración, preferiblemente uno que aún no aparezca en el vídeo. Así el vídeo dura exactamente lo mismo que tu canción y la música sigue a tiempo.\n• Las casillas bloqueadas (candado) son el pre-intro y el outro. No se pueden mover.\n\nPuedes cambiar tantas veces como quieras. Solo «Aplicar» hace definitivos los cambios; «Cancelar» lo descarta todo."),
 "ed_remove": ("✕ Quitar", "Reemplaza este clip por otro. El reemplazo se elige al azar y dura exactamente lo mismo que el clip anterior, de modo que el vídeo siga durando lo mismo que la canción y todos los cortes sigan sobre el ritmo. El programa prefiere clips que aún no aparezcan en el vídeo.\n\nEs rápido porque solo se renderiza de nuevo este fragmento. La línea de estado muestra «Reemplazando clip … (renderizando de nuevo)» y después «Clip reemplazado — guarda con «Aplicar»».\n\nSi no te gusta el clip nuevo, pulsa otra vez «✕ Quitar» y se elige otro. Con «Cancelar» restableces todos los clips reemplazados, con «Aplicar» los guardas. Si no hay un reemplazo adecuado, la línea de estado dice que no ha funcionado."),
 "ed_lock": ("Clip bloqueado", "El candado marca el pre-intro y el outro. Estos dos son fijos: el pre-intro se queda siempre al principio, el outro siempre al final.\n\nNo se pueden mover ni quitar. El motivo: ambos tienen su propio sonido, que se reproduce antes o después de la música. Si se colaran en medio del vídeo, el sonido quedaría desordenado.\n\nTodos los demás clips (sin candado) puedes reordenarlos y reemplazarlos libremente."),
 "ed_status": ("Línea de estado", "Muestra el estado actual en el editor de clips, por ejemplo «Clip reemplazado — guarda con «Aplicar»», «Reemplazando clip … (renderizando de nuevo)» o el progreso al guardar.\n\nCómo saber si queda algo pendiente: si pone «guarda con «Aplicar»», hay cambios anotados que aún no están en el vídeo. Solo se guardan tras «Aplicar».\n\nSi hay problemas, por ejemplo si no se pudo reemplazar un clip, aquí aparece la causa."),
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


# --------------------- Klick-Hilfe ("⚙") ---------------------
_click_help = {"active": False}


def _help_text_map() -> dict:
    """Beschriftung (in aktueller Sprache) -> Hilfe-Schlüssel."""
    pairs = [
        ("Musik wählen", "music"), ("Reset", "reset"), ("⚙", "help"),
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
    body_lbl = ctk.CTkLabel(win, text=body, font=ctk.CTkFont(size=13), anchor="w", justify="left",
                            wraplength=480)
    body_lbl.pack(fill="x", padx=22, pady=(0, 10))
    ctk.CTkButton(win, text=MENU.get(L, MENU["de"])["close"], width=120, command=win.destroy).pack(pady=(4, 16))
    win.update_idletasks()
    w_, h_ = 530, win.winfo_reqheight()
    try:
        max_h = max(300, win.winfo_screenheight() - 140)
    except Exception:
        max_h = 700
    if isinstance(h_, int) and h_ > max_h:
        # zu lang für den Bildschirm: Text in scrollbaren Bereich legen
        body_lbl.pack_forget()
        sc = ctk.CTkScrollableFrame(win, width=480, height=max_h - 150, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=14, pady=(0, 6), before=win.winfo_children()[-1])
        ctk.CTkLabel(sc, text=body, font=ctk.CTkFont(size=13), anchor="w", justify="left",
                     wraplength=450).pack(fill="x")
        win.update_idletasks()
        h_ = min(win.winfo_reqheight(), max_h)
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
    """⚙-Menü im Hauptfenster und im Clip-Editor."""
    show_settings_menu(btn or (btn_help if not in_editor_mode else None), simple=False)


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

_trace("mainloop")
app.mainloop()
