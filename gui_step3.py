# Musik Video Generator v4.1

from __future__ import annotations

APP_VERSION = "4.1"

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
        if msg == "start":
            for _n in ("mvg_start.log", "mvg_ffmpeg.log"):
                try:
                    if (base / _n).exists():
                        os.replace(base / _n, base / _n.replace(".log", "_vorher.log"))
                except Exception:
                    pass
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


_CHILDREN: set = set()                 # laufende ffmpeg-Prozesse (werden beim Beenden mit beendet)
_CHILD_LOCK = threading.Lock()
STALL_SEC = 300                        # ffmpeg ohne jeden Fortschritt so lange -> gilt als hängend, wird beendet
_LOG_LOCK = threading.Lock()


def _log_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def _ffmpeg_log():
    """Ausgaben von ffmpeg landen in mvg_ffmpeg.log (statt ins Leere – ohne Konsole ein bekannter Hänger-Grund)."""
    try:
        p = _log_dir() / "mvg_ffmpeg.log"
        if p.exists() and p.stat().st_size > 5_000_000:
            p.write_text("", encoding="utf-8")
        return open(p, "a", encoding="utf-8", errors="replace")
    except Exception:
        return None


def _short_cmd(cmd: list) -> str:
    parts = []
    for a in cmd[1:]:
        a = str(a)
        parts.append(Path(a).name if ("/" in a or "\\" in a) and len(a) > 40 else (a if len(a) <= 60 else a[:57] + "…"))
    return "ffmpeg " + " ".join(parts)


def run_hidden(cmd: list[str], timeout: Optional[float] = None,
               progress_cb: Optional[Callable[[float], None]] = None) -> subprocess.CompletedProcess:
    """ffmpeg ohne Konsole starten. stdin geschlossen, Ausgaben ins Log, Wächter gegen Hänger:
    tut sich STALL_SEC lang nichts (Zieldatei wächst nicht, kein Fortschritt), wird der Prozess beendet."""
    import time as _t
    cmd = [str(c) for c in cmd]
    is_ff = Path(cmd[0]).name.lower().startswith("ffmpeg")
    if is_ff:
        extra = ["-nostdin"] + (["-progress", "pipe:1", "-nostats"] if progress_cb else ["-nostats"])
        cmd = cmd[:1] + [e for e in extra if e not in cmd] + cmd[1:]
    log = _ffmpeg_log()
    if log is not None:
        with _LOG_LOCK:
            log.write(f"\n[{_t.strftime('%H:%M:%S')}] {_short_cmd(cmd) if is_ff else ' '.join(cmd)}\n")
            log.flush()
    out_path = Path(cmd[-1]) if is_ff and not cmd[-1].startswith(("-", "pipe:")) else None
    state = {"last": _t.time(), "size": -1}
    proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if progress_cb else subprocess.DEVNULL,
                            stderr=log if log is not None else subprocess.DEVNULL)
    with _CHILD_LOCK:
        _CHILDREN.add(proc)

    def read_progress():
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        sec = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    state["last"] = _t.time()
                    try:
                        progress_cb(sec)
                    except Exception:
                        pass
        except Exception:
            pass

    reader = None
    if progress_cb:
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
    start = _t.time()
    try:
        while True:
            try:
                rc = proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            now = _t.time()
            if timeout is not None and now - start > timeout:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
            if out_path is not None:
                try:
                    sz = out_path.stat().st_size
                except OSError:
                    sz = -1
                if sz != state["size"]:
                    state["size"], state["last"] = sz, now
            if now - state["last"] > STALL_SEC:
                proc.kill()
                proc.wait()
                _trace(f"{_t.strftime('%H:%M:%S')} ffmpeg hing und wurde beendet: {_short_cmd(cmd)}")
                raise RuntimeError(tr("ffmpeg reagiert seit {n} Minuten nicht mehr und wurde abgebrochen. Bitte über ⚙ → Problem melden einen Fehlerbericht erstellen.").format(n=max(1, STALL_SEC // 60)))
        if reader is not None:
            reader.join(timeout=2)
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return subprocess.CompletedProcess(cmd, rc)
    finally:
        with _CHILD_LOCK:
            _CHILDREN.discard(proc)
        if log is not None:
            try:
                log.close()
            except Exception:
                pass


def check_output_hidden(cmd: list[str], timeout: Optional[float] = 300) -> bytes:
    return subprocess.check_output(cmd, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=timeout)


def kill_child_processes() -> None:
    """Beim Beenden: noch laufende ffmpeg-Prozesse mit beenden (sonst laufen sie unsichtbar weiter)."""
    with _CHILD_LOCK:
        procs = list(_CHILDREN)
    for p_ in procs:
        try:
            p_.kill()
        except Exception:
            pass


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

# HELP_SECTIONS (Kurzanleitung) steht im GUI-Teil weiter unten.
HELP_SECTIONS: dict = {}

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
DEFAULT_CLIPS_DIR = BASE_DIR / "clips"      # Clip-Pool: ein einziger Ordner für immer wieder genutzte Clips/Fotos
POOL_DIR = DEFAULT_CLIPS_DIR
INTRO_DIR = BASE_DIR / "intro"
OUTRO_DIR = BASE_DIR / "outro"
VORSPANN_DIR = BASE_DIR / "pre-intro"
PHOTOS_DIR = BASE_DIR / "photos"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache" / "normalized"

for d in (DEFAULT_CLIPS_DIR, INTRO_DIR, OUTRO_DIR, VORSPANN_DIR, OUTPUT_DIR, CACHE_DIR):   # photos/ entfällt (Fotos aus jedem Ordner)
    d.mkdir(parents=True, exist_ok=True)

# Persistente Segmente für den Timeline-Editor
SEGMENTS_DIR = BASE_DIR / "cache" / "segments"
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

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

VIDEO_OVERHANG_SEC = 0.08

MIN_PHOTO_SEC = 2.0
MAX_CONSEC_PHOTOS = 2

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
        try:
            if float(ffprobe_json(out).get("format", {}).get("duration") or 0) > 0:
                return out
        except Exception:
            pass
        try:
            out.unlink()                       # unvollständig (z. B. nach einem Abbruch) -> neu vorbereiten
        except Exception:
            pass

    if status_cb:
        status_cb(tr("Clip wird vorbereitet: ") + original.name)
    _trace(time.strftime("%H:%M:%S") + " vorbereiten: " + original.name)

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
        str(out.with_name(out.stem + ".part.mp4"))
    ]
    tmp = Path(cmd[-1])
    dur = 0.0
    try:
        dur = float(ffprobe_json(original).get("format", {}).get("duration") or 0)
    except Exception:
        pass
    last = [-1]

    def prog(sec: float):
        if status_cb and dur > 0:
            pct = max(0, min(99, int(sec * 100 / dur)))
            if pct != last[0]:
                last[0] = pct
                status_cb(tr("Clip wird vorbereitet: ") + original.name + f" · {pct} %")

    try:
        run_hidden(cmd, progress_cb=prog)
        os.replace(tmp, out)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    _trace(time.strftime("%H:%M:%S") + " vorbereitet: " + original.name)
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
    """Öffnet die PDF-Anleitung in der eingestellten Sprache. Liegt eine PDF neben der EXE, wird diese genommen;
    sonst die in die EXE eingebaute (sie wird dafür einmal nach cache/manual kopiert)."""
    names = {"en": "MVG_Manual_EN.pdf", "es": "MVG_Manual_ES.pdf"}
    name = names.get(LANG, "MVG_Bedienungsanleitung.pdf")
    pdf = BASE_DIR / name
    if not pdf.exists() and (BASE_DIR / "manual" / name).exists():
        pdf = BASE_DIR / "manual" / name
    if not pdf.exists():
        inner = Path(resource_path(name))
        if inner.exists():
            try:
                dst_dir = BASE_DIR / "manual"
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / name
                if not dst.exists() or dst.stat().st_size != inner.stat().st_size:
                    shutil.copyfile(inner, dst)
                pdf = dst
            except Exception:
                pdf = inner
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


# =====================================================================================
#            Render-Engine v4: erst PLANEN (Clipfolge), dann AUSFÜHREN (rendern)
# -------------------------------------------------------------------------------------
#  plan_video()      Beat-Analyse, Schnittpunkte, Intro/Outro/Pre-Intro-Wahl und Clip-Zuordnung.
#                    Dauert nur Sekunden – der Nutzer sieht das Ergebnis als Kacheln (Schritt 4)
#                    und kann Clips verschieben oder tauschen, bevor irgendetwas gerendert wird.
#  execute_plan()    rendert den Plan. Jedes Segment wird unter einem Schlüssel aus Quelle,
#                    Startpunkt, Bildanzahl und Format zwischengespeichert. Nach Änderungen
#                    werden deshalb nur geänderte Segmente neu gerendert; Verschieben braucht
#                    nur noch das Zusammenfügen.
#
#  Unverändert aus v3.x übernommen (wichtig für Bild/Ton-Synchronität):
#   - Pre-Intro und Intro: Länge wird aus dem tatsächlich gerenderten Segment gemessen
#   - Intro + Clips = exakt Songlänge, Pre-Intro und Outro (je mit eigenem Ton) kommen dazu
#   - Outro-Ton-Delay = Pre-Intro-Länge + Musiklänge
#   - alle Segmente stumm (-an) für concat mit -c copy; Apostrophe via ffmpeg_concat_escape()
#   - stream_loop steht vor -ss
# =====================================================================================

def render_clip_segment(
    kind: str,
    src: Path,
    out_seg: Path,
    seg_len: float,
    vf: str,
    frames: Optional[int] = None,
    start: float = 0.0,
) -> None:
    """Rendert EIN stummes Segment (Foto oder Video) im Zielformat, ab `start` Sekunden im Clip."""
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
        # Clip loopen falls er ab dem Startpunkt nicht mehr lang genug ist (stream_loop VOR -ss)
        loop_flag = ["-stream_loop", "-1"] if clip_dur < start + seg_len + 0.1 else []
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


_DUR_CACHE: dict = {}
_BEAT_CACHE: dict = {}


def media_duration(p) -> float:
    """Dauer eines Videos (gecacht nach Pfad, Größe und Änderungszeit)."""
    p = Path(p)
    try:
        st = p.stat()
        key = (str(p), st.st_size, int(st.st_mtime))
    except Exception:
        key = (str(p), 0, 0)
    if key not in _DUR_CACHE:
        try:
            _DUR_CACHE[key] = float(ffprobe_duration(p))
        except Exception:
            _DUR_CACHE[key] = 0.0
    return _DUR_CACHE[key]


def detect_beats_cached(audio_path: str):
    p = Path(audio_path)
    try:
        st = p.stat()
        key = (str(p), st.st_size, int(st.st_mtime))
    except Exception:
        key = (str(p), 0, 0)
    if key not in _BEAT_CACHE:
        _BEAT_CACHE[key] = detect_beats(audio_path)
    beats, dur = _BEAT_CACHE[key]
    return list(beats), dur


def _speed_target(speed: str, is_shorts: bool) -> float:
    if speed == "gentle":
        t = CUT_SECONDS_GENTLE
    elif speed == "slow":
        t = CUT_SECONDS_SLOW
    elif speed == "normal":
        t = CUT_SECONDS_NORMAL
    elif speed == "random":
        t = random.uniform(CUT_SECONDS_FAST, CUT_SECONDS_GENTLE)   # wird pro Schnitt neu gewürfelt
    else:
        t = CUT_SECONDS_FAST
    if is_shorts and speed in ("gentle", "slow"):
        t = min(t, 1.60)
    return t


def _random_offset(kind: str, src: str, dur: float) -> float:
    if kind != "video":
        return 0.0
    clip_dur = media_duration(src)
    max_start = max(0.0, clip_dur - dur - 0.05)
    return random.uniform(0.0, max_start) if max_start > 0 else 0.0


def _tmp_for(p: Path) -> Path:
    """Eindeutiger Zwischenname: zwei Vorgänge (oder ein hängengebliebener alter) kommen sich nie in die Quere."""
    import uuid
    return p.with_name(f"{p.stem}.{os.getpid()}{uuid.uuid4().hex[:6]}.part.mp4")


def _safe_replace(tmp: Path, dst: Path) -> None:
    """Fertiges Segment an seinen Platz. Unter Windows kann eine Datei kurz gesperrt sein -> mehrmals versuchen."""
    import time as _t
    last = None
    for _ in range(12):
        try:
            os.replace(tmp, dst)
            return
        except OSError as e:
            last = e
            _t.sleep(0.4)
    if dst.exists() and dst.stat().st_size > 1000:      # gleiches Segment liegt schon fertig da
        try:
            tmp.unlink()
        except Exception:
            pass
        return
    raise RuntimeError(tr("Eine Datei im Cache wird von einem anderen Programm benutzt. Bitte im Task-Manager alle „ffmpeg“-Prozesse beenden (oder den Computer neu starten) und erneut versuchen.") + f"\n{last}")


# ── Ganzer Clip (keine Schnittlänge gewählt) und Loop (genau ein Clip) ─────────────────────
FULL_PHOTO_SEC = 4.0          # Fotos im Modus „ganzer Clip“
LOOP_XFADE = 0.25             # Überblendung am Loop-Übergang (Sekunden)
LOOP_SEAM_MAX = 3.0           # Loop nur, wenn der Übergang höchstens so „unruhig“ ist wie 3 normale Bildwechsel
END_FADE_SEC = 1.0            # weiches Ende ohne Outro: Bild und Ton blenden in der letzten Sekunde aus
BOOMERANG_MAX = 6.0           # längste Vorwärts-Strecke beim Boomerang (Sekunden)


def plan_mode(media: list, speed: str) -> str:
    """beat = feste Schnittlänge, full = ganze Clips, loop = genau ein Video (läuft als Loop)."""
    kinds = [m["kind"] for m in media if Path(m["path"]).exists()]
    if kinds.count("video") == 1 and "photo" not in kinds:
        return "loop"
    if not speed or speed == "full":
        return "full"
    return "beat"


def _beat_period(beats: list) -> float:
    if len(beats) >= 3:
        import numpy as _np
        d = _np.diff(_np.asarray(beats, dtype=float))
        d = d[(d > 0.2) & (d < 2.0)]
        if len(d):
            return float(_np.median(d))
    return 0.5


def _snap_full_end(t: float, natural_end: float, beats: list, music_dur: float):
    """Ende eines ganzen Clips: letzter Beat vor dem natürlichen Ende (Clip läuft fast ganz)."""
    if natural_end >= music_dur - 0.05:
        return music_dur, True
    cands = [b for b in beats if t + 0.5 <= b <= natural_end]
    return (cands[-1] if cands else natural_end), False


def _full_layout(seq: list, plan_media: list, start: float, music_dur: float, beats: list, is_shorts: bool) -> list:
    """Ganze Clips nacheinander ab `start`. seq = gewünschte Reihenfolge [(kind, path)];
    reicht sie nicht bis zum Songende, wird mit weiteren Clips aus der Auswahl aufgefüllt."""
    vids = [Path(m["path"]) for m in plan_media if m["kind"] == "video" and Path(m["path"]).exists()]
    phs = [Path(m["path"]) for m in plan_media if m["kind"] == "photo" and Path(m["path"]).exists()]
    pool: list = []
    slots: list = []
    t = start
    k = 0
    last = None
    guard = 0
    while t < music_dur - 0.05 and guard < 5000:
        guard += 1
        if k < len(seq):
            kind, path = seq[k]
        else:
            if not pool:
                pool = list(shuffle_items_once(vids, phs))
            pick = next((x for x in pool if (x[0], str(x[1])) != last), pool[0])
            pool.remove(pick)
            kind, path = pick
        k += 1
        d = media_duration(path) if kind == "video" else FULL_PHOTO_SEC
        if d <= 0.1:
            d = FULL_PHOTO_SEC
        end, is_last = _snap_full_end(t, t + d, beats, music_dur)
        if end - t < 0.05:
            end = min(music_dur, t + d)
            is_last = end >= music_dur - 0.05
        dur = end - t
        if is_last and not is_shorts:
            dur += VIDEO_OVERHANG_SEC
        slots.append({"start": t, "dur": dur, "kind": kind, "src": str(path), "offset": 0.0})
        t = end
        last = (kind, str(path))
    return slots


def _relayout_full(plan: dict, seq: list) -> None:
    plan["slots"] = _full_layout(seq, plan["media"], plan.get("intro_len", 0.0), plan["music_dur"],
                                 plan.get("beats", []), plan["is_shorts"])


_LOOP_CACHE: dict = {}


def _gray_frames(src: str, fps: int = 10, max_sec: float = 40.0, w: int = 48, h: int = 27):
    import numpy as _np
    cmd = [ffmpeg_cmd(), "-v", "error", "-t", f"{max_sec:.2f}", "-i", str(src),
           "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "pipe:1"]
    data = check_output_hidden(cmd)
    arr = _np.frombuffer(data, dtype=_np.uint8)
    n = len(arr) // (w * h)
    return arr[:n * w * h].reshape(n, h, w).astype(_np.float32)


def analyze_loop(src: str, beat_period: float) -> dict:
    """Sucht im Clip die beste Loop-Stelle: zwei Bilder, die sich möglichst ähnlich sehen, mit einer
    Loop-Länge von ganzen Beats. Ist kein sauberer Übergang möglich, wird es ein Boomerang
    (vorwärts, dann rückwärts). Ergebnis: {"type", "a", "b", "frames", "xfade"}."""
    import numpy as _np
    D = media_duration(src)
    key = (_file_sig(Path(src)), round(beat_period, 3))
    if key in _LOOP_CACHE:
        return dict(_LOOP_CACHE[key])
    P = max(0.25, beat_period)
    X = LOOP_XFADE
    res = None
    try:
        fps = 10
        arr = _gray_frames(src, fps=fps)
        n = len(arr)
        if n >= 20:
            cons = _np.abs(arr[1:] - arr[:-1]).mean(axis=(1, 2))
            m = float(_np.median(cons)) + 0.5
            min_len = max(1.5, 2 * P)
            best = None
            for i in range(0, max(1, int(n * 0.4))):
                j0 = i + int(min_len * fps)
                j1 = n - int((X + 0.1) * fps)          # hinter j braucht es noch die Überblendung
                if j0 >= j1:
                    break
                d = _np.abs(arr[j0:j1] - arr[i]).mean(axis=(1, 2)) / m
                units = (_np.arange(j0, j1) - i) / fps
                k = _np.maximum(1, _np.round(units / P))
                beat_err = _np.abs(units - k * P) / P
                score = d + 0.6 * beat_err - 0.15 * _np.minimum(units, 12.0) / 12.0
                jj = int(_np.argmin(score))
                if best is None or score[jj] < best[0]:
                    best = (float(score[jj]), i, j0 + jj, float(d[jj]))
            if best is not None and best[3] <= LOOP_SEAM_MAX:
                _s, i, j, _ratio = best
                a = i / fps
                unit = (j - i) / fps
                k = max(1, int(round(unit / P)))
                unit_b = k * P
                if a + unit_b + X > D - 0.05:          # Beat-Länge passt nicht mehr in den Clip
                    unit_b = unit
                U = max(int(round(unit_b * FPS)), int(FPS))
                res = {"type": "loop", "a": a, "b": a + U / FPS + X, "frames": U, "xfade": X}
    except Exception:
        res = None
    if res is None:                                  # Boomerang: vorwärts und zurück, Wendepunkte auf dem Beat
        avail = max(0.4, min(D - 0.05, BOOMERANG_MAX))
        mcount = int(avail // P)
        lf = mcount * P if mcount >= 1 and mcount * P >= 1.0 else avail
        nf = max(4, int(round(lf * FPS)))
        res = {"type": "boomerang", "a": 0.0, "b": nf / FPS, "frames": 2 * nf - 2, "xfade": 0.0}
    _LOOP_CACHE[key] = dict(res)
    return res


def _loop_layout(plan: dict) -> None:
    """Slots für den Loop-Modus: lauter gleich lange Durchläufe bis zum Songende."""
    start = plan.get("intro_len", 0.0)
    music_dur = plan["music_dur"]
    src = plan["loop_src"]
    info = plan.get("loop")
    slots = []
    if info is None:                                 # Clip ist länger als der Song: einfach durchlaufen lassen
        dur = music_dur - start + (0.0 if plan["is_shorts"] else VIDEO_OVERHANG_SEC)
        slots.append({"start": start, "dur": dur, "kind": "video", "src": src, "offset": 0.0})
    else:
        L = info["frames"] / FPS
        t = start
        while t < music_dur - 0.05:
            end = min(t + L, music_dur)
            dur = end - t
            if end >= music_dur - 0.05 and not plan["is_shorts"]:
                dur += VIDEO_OVERHANG_SEC
            slots.append({"start": t, "dur": dur, "kind": "loop", "src": src, "offset": 0.0})
            t = end
    plan["slots"] = slots


def render_loop_unit(src: Path, info: dict, vf: str, out_seg: Path) -> None:
    """Rendert EINEN nahtlosen Loop-Durchlauf (genau info['frames'] Bilder, stumm, Zielformat)."""
    U = int(info["frames"])
    tmp_dir = Path(tempfile.mkdtemp(prefix="mvgloop_"))
    try:
        enc = ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
               "-r", str(FPS), "-fps_mode", "cfr"]
        if info["type"] == "loop":
            # Durchlauf = Clip ab a+X für U Bilder; die letzten X Bilder werden in den Anfang (ab a) überblendet.
            # In einfachen Einzelschritten (ohne xfade), damit es mit jeder ffmpeg-Version zuverlässig läuft.
            Xf = max(1, min(int(round(info["xfade"] * FPS)), U // 3))
            a = info["a"]
            p1 = tmp_dir / "p1.mp4"
            p2 = tmp_dir / "p2.mp4"
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-ss", f"{a + Xf / FPS:.4f}", "-i", str(src),
                        "-vf", vf] + enc + ["-frames:v", str(U - Xf), str(p1)], timeout=900)
            expr = f"A*(1-(N+1)/{Xf + 1})+B*((N+1)/{Xf + 1})"
            fc = (f"[0:v]{vf},format=yuv420p,trim=end_frame={Xf},setpts=PTS-STARTPTS[t];"
                  f"[1:v]{vf},format=yuv420p,trim=end_frame={Xf},setpts=PTS-STARTPTS[h];"
                  f"[t][h]blend=all_expr='{expr}',format=yuv420p[v]")
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner",
                        "-ss", f"{a + U / FPS:.4f}", "-t", f"{Xf / FPS + 0.5:.4f}", "-i", str(src),
                        "-ss", f"{a:.4f}", "-t", f"{Xf / FPS + 0.5:.4f}", "-i", str(src),
                        "-filter_complex", fc, "-map", "[v]"] + enc + ["-frames:v", str(Xf), str(p2)], timeout=900)
            lst = tmp_dir / "list.txt"
            lst.write_text(f"file '{ffmpeg_concat_escape(p1.as_posix())}'\nfile '{ffmpeg_concat_escape(p2.as_posix())}'\n",
                           encoding="utf-8")
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out_seg)], timeout=900)
        else:
            nf = (U + 2) // 2
            fwd = tmp_dir / "fwd.mp4"
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(src), "-vf", vf, "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "veryfast",
                        "-fps_mode", "cfr", "-frames:v", str(nf), str(fwd)], timeout=900)
            parts = [fwd]
            chunk = 30
            starts = list(range(0, nf, chunk))
            for s0 in reversed(starts):                  # rückwärts in kleinen Stücken (wenig Speicher)
                e0 = min(nf, s0 + chunk)
                pc = tmp_dir / f"r{s0:05d}.mp4"
                run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(fwd), "-vf",
                            f"trim=start_frame={s0}:end_frame={e0},setpts=PTS-STARTPTS,reverse", "-an",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "veryfast",
                            "-fps_mode", "cfr", str(pc)], timeout=900)
                parts.append(pc)
            lst = tmp_dir / "list.txt"
            lst.write_text("".join(f"file '{ffmpeg_concat_escape(p_.as_posix())}'\n" for p_ in parts), encoding="utf-8")
            allf = tmp_dir / "all.mp4"
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(allf)], timeout=900)
            # doppelte Wendebilder entfernen: letztes Vorwärtsbild und letztes Rückwärtsbild
            sel = f"select='not(eq(n\\,{nf})+eq(n\\,{2 * nf - 1}))',setpts=N/{FPS}/TB"
            run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(allf), "-vf", sel, "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                        "-r", str(FPS), "-fps_mode", "cfr", "-frames:v", str(U), str(out_seg)], timeout=900)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def render_loop_cut(unit: Path, frames: int, unit_frames: int, out_seg: Path, align_end: bool = False) -> None:
    """Loop-Durchlauf mit abweichender Länge: der erste Abschnitt nimmt das ENDE eines Durchlaufs
    (damit er nahtlos in den nächsten übergeht), der letzte den Anfang.
    Endliche Wiederholungszahl – nie „-stream_loop -1“, damit nichts endlos laufen kann."""
    frames, unit_frames = int(frames), max(1, int(unit_frames))
    enc = ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
           "-r", str(FPS), "-fps_mode", "cfr", "-frames:v", str(frames), str(out_seg)]
    if align_end and frames < unit_frames:
        run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(unit),
                    "-vf", f"trim=start_frame={unit_frames - frames},setpts=PTS-STARTPTS"] + enc, timeout=900)
    else:
        reps = frames // unit_frames
        run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-stream_loop", str(reps), "-i", str(unit)] + enc, timeout=900)


def plan_video(
    audio_path: str,
    media: list,
    fmt: str,
    use_intro: bool,
    use_outro: bool,
    use_vorspann: bool,
    speed: str,
    status_cb: Optional[Callable[[str], None]] = None,
    stage_cb: Optional[Callable[..., None]] = None,
) -> dict:
    """Plant ein Video (ohne zu rendern). media: [{"kind": "video"|"photo", "path": Path|str}].
    stage_cb(name, *info) meldet den Beginn jedes Arbeitsschritts (beats, short, cuts, extras, assign)."""
    def stage(*a):
        if stage_cb:
            try:
                stage_cb(*a)
            except Exception:
                pass

    is_shorts = fmt != "normal"
    shorts_limited = fmt == "shorts58"
    # Der 58s-Short ist immer "pur": ohne Pre-Intro, Intro und Outro.
    if shorts_limited:
        use_intro = use_outro = use_vorspann = False

    items = [(m["kind"], Path(m["path"])) for m in media if Path(m["path"]).exists()]
    ext_vid = [p for k, p in items if k == "video"]
    ext_ph = [p for k, p in items if k == "photo"]
    if not ext_vid and not ext_ph:
        raise RuntimeError(tr("Keine Clips oder Fotos ausgewählt."))

    if status_cb:
        status_cb(tr("Analysiere Musik…"))
    stage("beats")
    beat_times, music_dur_full = detect_beats_cached(audio_path)
    music_dur = min(music_dur_full, SHORTS_MAX_SEC) if shorts_limited else music_dur_full
    music_start = 0.0
    if shorts_limited:
        stage("short", music_dur_full > SHORTS_MAX_SEC + 10)
    if shorts_limited and music_dur_full > SHORTS_MAX_SEC + 10:
        if status_cb:
            status_cb(tr("Suche stärksten Abschnitt für den Short…"))
        music_start, _why = choose_short_start(audio_path, music_dur_full)
        beat_times = [t - music_start for t in beat_times
                      if music_start <= t < music_start + music_dur]
        if not beat_times or beat_times[0] > 0.01:
            beat_times = [0.0] + beat_times

    mode = plan_mode(media, speed)
    stage("cuts")
    if mode == "beat":
        target_seconds = _speed_target(speed, is_shorts)
        cut_points = build_cut_points(beat_times, music_dur, target_seconds, speed=speed)
    else:
        target_seconds = 1.0
        cut_points = []

    # Intro: Länge wird auf ganze Bilder gerundet geplant; beim Rendern wird sie am fertigen
    # Segment nachgemessen und das erste Clip-Segment gleicht eine Abweichung aus.
    stage("extras")
    intro_src: Optional[Path] = None
    intro_len = 0.0
    if use_intro:
        cands = list_video_files(INTRO_DIR)
        if cands:
            intro_src = random.choice(cands)
            intro_len = min(media_duration(intro_src), music_dur)
            intro_len = round(intro_len * FPS) / FPS
            if intro_len < 0.20:
                intro_src, intro_len = None, 0.0
    outro_src = random.choice(list_video_files(OUTRO_DIR)) if use_outro and list_video_files(OUTRO_DIR) else None
    vorspann_src = random.choice(list_video_files(VORSPANN_DIR)) if use_vorspann and list_video_files(VORSPANN_DIR) else None

    loop_info = None
    loop_src = None
    if mode == "beat":
        start_offset = intro_len
        cut_points2: list[float] = []
        if start_offset < music_dur - 0.05:
            # erstes Clip-Segment nach dem Intro nicht auffällig kurz
            min_first_seg = max(0.20, float(target_seconds) * 0.75)
            cut_points2 = [t for t in cut_points if t >= start_offset + min_first_seg]
            cut_points2 = [start_offset] + cut_points2
            if cut_points2[-1] < music_dur:
                cut_points2.append(music_dur)

        stage("assign", len(items), max(0, len(cut_points2) - 1))
        all_items = shuffle_items_once(ext_vid, ext_ph)
        remaining = all_items[:]
        last_item = None
        consec_photos = 0
        slots: list[dict] = []
        total = max(0, len(cut_points2) - 1)
        for i in range(total):
            seg_len = cut_points2[i + 1] - cut_points2[i]
            if seg_len <= 0.05:
                continue
            is_last = (i == total - 1)
            seg_len_out = seg_len + (VIDEO_OVERHANG_SEC if is_last and not is_shorts else 0.0)
            if not remaining:
                all_items = shuffle_items_once(ext_vid, ext_ph)
                remaining = all_items[:]
            (kind, path), consec_photos = pick_item_no_repeat(remaining, all_items, last_item, consec_photos, seg_len_out)
            last_item = (kind, path)
            slots.append({
                "start": cut_points2[i],
                "dur": seg_len_out,
                "kind": kind,
                "src": str(path),
                "offset": _random_offset(kind, str(path), seg_len_out),
            })
    elif mode == "full":
        stage("assign", len(items), 0)
        slots = _full_layout([], [{"kind": k, "path": str(p)} for k, p in items], intro_len, music_dur,
                             beat_times, is_shorts)
    else:
        stage("loop")
        loop_src = str(ext_vid[0])
        if media_duration(loop_src) < music_dur - intro_len + 0.05:
            loop_info = analyze_loop(loop_src, _beat_period(beat_times))
        tmp = {"intro_len": intro_len, "music_dur": music_dur, "loop_src": loop_src, "loop": loop_info,
               "is_shorts": is_shorts}
        _loop_layout(tmp)
        slots = tmp["slots"]

    if status_cb:
        status_cb(tr("Clipfolge geplant: {n} Schnitte").format(n=len(slots)))

    return {
        "fmt": fmt,
        "audio": str(audio_path),
        "is_shorts": is_shorts,
        "shorts_limited": shorts_limited,
        "out_w": OUT_W_SHORTS if is_shorts else OUT_W_NORMAL,
        "out_h": OUT_H_SHORTS if is_shorts else OUT_H_NORMAL,
        "music_dur": music_dur,
        "music_start": music_start,
        "intro_src": str(intro_src) if intro_src else None,
        "intro_len": intro_len,
        "outro_src": str(outro_src) if outro_src else None,
        "vorspann_src": str(vorspann_src) if vorspann_src else None,
        "slots": slots,
        "media": [{"kind": k, "path": str(p)} for k, p in items],
        "version": 0,
        "mode": mode,
        "beats": list(beat_times),
        "loop": loop_info,
        "loop_src": loop_src,
    }


def plan_swap(plan: dict, i: int, j: int) -> bool:
    """Verschieben: tauscht die Clips zweier Plätze. Die Längen bleiben bei den Plätzen (Beat-Raster)."""
    s = plan["slots"]
    if i == j or not (0 <= i < len(s)) or not (0 <= j < len(s)):
        return False
    if plan.get("mode") == "loop":
        return False
    if plan.get("mode") == "full":           # ganze Clips: Reihenfolge tauschen, die Schnitte wandern mit
        seq = [(x["kind"], x["src"]) for x in s]
        seq[i], seq[j] = seq[j], seq[i]
        _relayout_full(plan, seq)
        plan["version"] += 1
        return True
    for k in ("kind", "src", "offset"):
        s[i][k], s[j][k] = s[j][k], s[i][k]
    for idx in (i, j):
        sl = s[idx]
        if sl["kind"] == "video":       # Startpunkt an die neue Länge anpassen
            max_start = max(0.0, media_duration(sl["src"]) - sl["dur"] - 0.05)
            sl["offset"] = min(sl["offset"], max_start)
    plan["version"] += 1
    return True


def plan_replace(plan: dict, i: int) -> bool:
    """Clip tauschen: zufälliger anderer Clip aus der Auswahl, gleiche Länge (Platz bleibt)."""
    s = plan["slots"]
    if not (0 <= i < len(s)):
        return False
    sl = s[i]
    if plan.get("mode") == "loop":
        return False
    media = [m for m in plan["media"] if Path(m["path"]).exists()]
    if plan.get("mode") == "full":           # ganzer Clip: anderer Clip, die folgenden Schnitte passen sich an
        cands = [m for m in media if m["path"] != sl["src"]]
        if not cands:
            return False
        used = {x["src"] for x in s}
        m = random.choice([c for c in cands if c["path"] not in used] or cands)
        seq = [(x["kind"], x["src"]) for x in s]
        seq[i] = (m["kind"], m["path"])
        _relayout_full(plan, seq)
        plan["version"] += 1
        return True
    videos_exist = any(m["kind"] == "video" for m in media)
    cands = [m for m in media if m["path"] != sl["src"]
             and not (m["kind"] == "photo" and videos_exist and sl["dur"] < MIN_PHOTO_SEC)]
    if not cands:
        return False
    used = {x["src"] for x in s}
    fresh = [m for m in cands if m["path"] not in used]
    m = random.choice(fresh or cands)
    sl["kind"] = m["kind"]
    sl["src"] = m["path"]
    sl["offset"] = _random_offset(m["kind"], m["path"], sl["dur"])
    plan["version"] += 1
    return True


def _seg_key(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8", errors="ignore")).hexdigest()[:20]


def _file_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        return str(p)


def execute_plan(
    plan: dict,
    out_path: Path,
    title_overlay: bool = False,
    title_text: str = "",
    status_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
    seg_cb: Optional[Callable[[int, str], None]] = None,
    seg_dir: Optional[Path] = None,
    prune: bool = False,
    phase_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Rendert einen Plan. seg_cb(index, zustand) meldet den Fortschritt je Kachel
    (Index in plan_tiles(plan); zustand "run" oder "done").
    seg_dir: Segment-Zwischenspeicher (gleiche Segmente werden wiederverwendet).
    prune:   danach alle Segmente im seg_dir löschen, die dieser Plan nicht mehr braucht.
    """
    is_shorts = plan["is_shorts"]
    shorts_limited = plan["shorts_limited"]
    out_w, out_h = plan["out_w"], plan["out_h"]
    music_dur = plan["music_dur"]
    music_start = plan["music_start"]
    audio_path = plan["audio"]
    if shorts_limited:
        title_overlay = False   # kein Titel im 58s-Short
    mode = "shorts" if is_shorts else "normal"
    vf = build_vf_base(out_w, out_h, mode)
    res = f"{out_w}x{out_h}|{mode}"

    temp_dir = Path(tempfile.mkdtemp(prefix="beatcut_"))
    if seg_dir is None:
        seg_dir = temp_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    prepared: dict = {}

    def prep(p: Path) -> Path:
        if p not in prepared:
            prepared[p] = normalize_to_cache_video_only(p, status_cb=status_cb) if needs_normalize(p) else p
            if prepared[p] != p:
                try:
                    os.utime(prepared[p], None)          # „zuletzt benutzt“ für das automatische Aufräumen
                except Exception:
                    pass
        return prepared[p]

    used_files: set = set()
    segments_files: list[Path] = []
    tile = 0
    vorspann_len = 0.0
    intro_len = 0.0
    vorspann_src = Path(plan["vorspann_src"]) if plan.get("vorspann_src") else None
    intro_src = Path(plan["intro_src"]) if plan.get("intro_src") else None
    outro_src = Path(plan["outro_src"]) if plan.get("outro_src") else None
    n_slots = len(plan["slots"])
    n_work = n_slots + sum(1 for x in (vorspann_src, intro_src, outro_src) if x is not None and x.exists())
    done_work = [0]

    def step_done():
        done_work[0] += 1
        if progress_cb and n_work:
            progress_cb(0.03 + 0.85 * done_work[0] / n_work)

    import time as _tm
    _trace(_tm.strftime("%H:%M:%S") + f" Render: {out_path.name} · {len(plan['slots'])} Abschnitte · Modus {plan.get('mode')}")

    def phase(name):
        _trace(_tm.strftime("%H:%M:%S") + " Schritt: " + str(name))
        if phase_cb:
            try:
                phase_cb(name)
            except Exception:
                pass

    def cb(i, state):
        if state == "run":
            _trace(_tm.strftime("%H:%M:%S") + f" Abschnitt {i + 1} beginnt")
        if seg_cb:
            try:
                seg_cb(i, state)
            except Exception:
                pass

    try:
        if progress_cb:
            progress_cb(0.02)

        # ── Pre-Intro (eigener Ton, läuft VOR allem, stumm gerendert)
        if vorspann_src is not None and vorspann_src.exists():
            phase("pre")
            cb(tile, "run")
            out_seg = seg_dir / f"p_{_seg_key('pre', _file_sig(vorspann_src), res)}.mp4"
            if not out_seg.exists():
                if status_cb:
                    status_cb(tr("Rendere Pre-Intro…"))
                tmp = _tmp_for(out_seg)
                run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                            "-i", str(prep(vorspann_src)), "-vf", vf, "-an",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                            "-fps_mode", "cfr", str(tmp)])
                _safe_replace(tmp, out_seg)
            # Länge aus dem tatsächlich gerenderten Segment (CFR kann einzelne Frames verschieben)
            vorspann_len = ffprobe_duration(out_seg)
            segments_files.append(out_seg)
            used_files.add(out_seg.name)
            cb(tile, "done")
            tile += 1
            step_done()

        # ── Intro (stumm unter der Musik)
        if intro_src is not None and intro_src.exists() and plan["intro_len"] > 0:
            phase("intro")
            cb(tile, "run")
            out_seg = seg_dir / f"i_{_seg_key('intro', _file_sig(intro_src), round(plan['intro_len'] * FPS), res)}.mp4"
            if not out_seg.exists():
                if status_cb:
                    status_cb(tr("Rendere Intro…"))
                tmp = _tmp_for(out_seg)
                run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                            "-i", str(prep(intro_src)), "-t", f"{plan['intro_len']:.4f}", "-vf", vf, "-an",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                            "-fps_mode", "cfr", str(tmp)])
                _safe_replace(tmp, out_seg)
            # WICHTIG: tatsächliche Länge messen – das erste Clip-Segment gleicht die Abweichung aus
            intro_len = ffprobe_duration(out_seg)
            segments_files.append(out_seg)
            used_files.add(out_seg.name)
            cb(tile, "done")
            tile += 1
            step_done()

        # ── Clips: Grenzen auf ganze Bilder gerundet (kumulativ, damit sich nichts aufsummiert)
        if status_cb:
            status_cb(tr("Rendere Segmente…"))
        phase("clips")
        for i, sl in enumerate(plan["slots"]):
            start_t = intro_len if (i == 0 and intro_len > 0) else sl["start"]
            end_t = sl["start"] + sl["dur"]
            frames = max(1, int(round(end_t * FPS)) - int(round(start_t * FPS)))
            seg_len = frames / FPS
            src = Path(sl["src"])
            cb(tile, "run")
            if sl["kind"] == "loop" and plan.get("loop"):
                info = plan["loop"]
                ukey = _seg_key("loopunit", _file_sig(src), info["type"], round(info["a"], 3), round(info["b"], 3),
                                info["frames"], res)
                unit = seg_dir / f"l_{ukey}.mp4"
                if not unit.exists():
                    if status_cb:
                        status_cb(tr("Bereite Loop vor…"))
                    tmpu = _tmp_for(unit)
                    render_loop_unit(prep(src), info, vf, tmpu)
                    if not tmpu.exists() or tmpu.stat().st_size < 1000:
                        raise RuntimeError(tr("Der Loop-Durchlauf konnte nicht erstellt werden."))
                    _safe_replace(tmpu, unit)
                used_files.add(unit.name)
                if frames == int(info["frames"]):
                    out_seg = unit
                else:
                    first_loop = (i == 0)
                    out_seg = seg_dir / f"l_{_seg_key(ukey, frames, 'end' if first_loop else 'start')}.mp4"
                    if not out_seg.exists():
                        tmpc = _tmp_for(out_seg)
                        render_loop_cut(unit, frames, int(info["frames"]), tmpc, align_end=first_loop)
                        _safe_replace(tmpc, out_seg)
                segments_files.append(out_seg)
                used_files.add(out_seg.name)
                cb(tile, "done")
                tile += 1
                step_done()
                continue
            out_seg = seg_dir / f"c_{_seg_key(sl['kind'], _file_sig(src), round(sl['offset'], 3), frames, res)}.mp4"
            if not out_seg.exists():
                tmp = _tmp_for(out_seg)
                if sl["kind"] == "photo":
                    render_clip_segment("photo", src, tmp, seg_len, vf, frames=frames)
                else:
                    render_clip_segment("video", prep(src), tmp, seg_len, vf, frames=frames, start=sl["offset"])
                _safe_replace(tmp, out_seg)
            segments_files.append(out_seg)
            used_files.add(out_seg.name)
            cb(tile, "done")
            tile += 1
            step_done()

        # ── Outro (eigener Ton nach der Musik, Segment stumm)
        if outro_src is not None and outro_src.exists():
            phase("outro")
            cb(tile, "run")
            out_seg = seg_dir / f"o_{_seg_key('outro', _file_sig(outro_src), res)}.mp4"
            if not out_seg.exists():
                if status_cb:
                    status_cb(tr("Hänge Outro an…"))
                tmp = _tmp_for(out_seg)
                run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                            "-i", str(outro_src), "-vf", vf, "-an",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                            "-fps_mode", "cfr", str(tmp)])
                _safe_replace(tmp, out_seg)
            segments_files.append(out_seg)
            used_files.add(out_seg.name)
            cb(tile, "done")
            tile += 1
            step_done()

        if progress_cb:
            progress_cb(0.89)
        if status_cb:
            status_cb(tr("Klebe Segmente…"))
        phase("concat")

        # Weiches Ende: ohne Outro blenden Bild und Ton zum Schluss aus (der 58-s-Short hat seine eigene Blende).
        # Nur die letzten Stücke werden neu kodiert – als Kopie im Temp-Ordner, der Segment-Cache bleibt unverändert.
        soft_end = 0.0
        if not shorts_limited and not (outro_src is not None and outro_src.exists()) and segments_files:
            # so viele Schluss-Stücke nehmen, dass die Blende eine volle Sekunde lang sein kann
            tail, tail_len = [], 0.0
            for seg in reversed(segments_files):
                tail.insert(0, seg)
                tail_len += media_duration(seg)
                if tail_len >= END_FADE_SEC + 0.1 or len(tail) >= 6:
                    break
            d = min(END_FADE_SEC, max(tail_len * 0.5, tail_len - 0.1))
            if d >= 0.2:
                faded = temp_dir / "last_fade.mp4"
                tail_list = temp_dir / "tail.txt"
                with open(tail_list, "w", encoding="utf-8") as f:
                    for seg in tail:
                        f.write(f"file '{ffmpeg_concat_escape(seg.as_posix())}'\n")
                run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(tail_list),
                            "-vf", f"fade=t=out:st={max(0.0, tail_len - d):.4f}:d={d:.4f}", "-an",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                            "-r", str(FPS), "-fps_mode", "cfr", str(faded)], timeout=900)
                if faded.exists() and faded.stat().st_size > 1000:
                    segments_files[len(segments_files) - len(tail):] = [faded]
                    soft_end = min(d, music_dur * 0.5)

        video_only = temp_dir / "video_only.mp4"
        concat_list = temp_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for s in segments_files:
                f.write(f"file '{ffmpeg_concat_escape(s.as_posix())}'\n")
        run_hidden([ffmpeg_cmd(), "-y", "-hide_banner", "-f", "concat", "-safe", "0",
                    "-i", str(concat_list), "-c", "copy", str(video_only)])

        if status_cb:
            status_cb(tr("Muxe Audio…"))
        phase("mux")
        if progress_cb:
            progress_cb(0.93)

        if shorts_limited:
            fade_start, fade_d = compute_fade(music_dur)
            a_chain = (
                f"atrim={music_start:.4f}:{music_start + music_dur:.4f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={SHORTS_FADE_IN_SEC:.2f},"
                f"afade=t=out:st={fade_start:.4f}:d={fade_d:.4f}"
            )
        else:
            a_chain = f"atrim=0:{music_dur:.4f},asetpts=PTS-STARTPTS"
            if soft_end > 0:
                a_chain += f",afade=t=out:st={music_dur - soft_end:.4f}:d={soft_end:.4f}"

        # Pre-Intro-Ton läuft VOR der Musik -> Musik um vorspann_len verzögern
        vorspann_audio_path = None
        music_delay_ms = 0
        if vorspann_src is not None and vorspann_src.exists() and has_audio_stream(vorspann_src):
            vorspann_audio_path = vorspann_src
            music_delay_ms = round(vorspann_len * 1000)

        # Outro-Ton NACH der Musik: Delay = Pre-Intro-Länge + Musiklänge
        outro_audio_path = None
        outro_delay_ms = 0
        if outro_src is not None and outro_src.exists() and has_audio_stream(outro_src):
            outro_audio_path = outro_src
            outro_delay_ms = music_delay_ms + round(music_dur * 1000)

        inputs = [str(video_only), str(audio_path)]
        next_idx = 2
        outro_idx = vorspann_idx = None
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
            audio_parts.append(f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[aout]")
        else:
            audio_parts = [mus_chain.replace("[mus]", "[aout]")]
        a_filter = ";".join(p for p in audio_parts if p)

        if title_overlay and title_text:
            # Titel startet nach Pre-Intro + Intro
            txt_filter = build_title_overlay_filter(title_text, out_w, out_h, intro_offset=vorspann_len + intro_len)
            filter_complex = f"[0:v]{txt_filter}[vt];{a_filter}"
            video_map = "[vt]"
            vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", "-fps_mode", "cfr"]
        elif shorts_limited:
            _fs, _fd = compute_fade(music_dur)
            filter_complex = (f"[0:v]fade=t=in:st=0:d={SHORTS_FADE_IN_SEC:.2f},"
                              f"fade=t=out:st={_fs:.4f}:d={_fd:.4f}[vt];{a_filter}")
            video_map = "[vt]"
            vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", "-fps_mode", "cfr"]
        else:
            filter_complex = a_filter
            video_map = "0:v:0"
            vcodec_args = ["-c:v", "copy"]

        tmp_out = temp_dir / "final.mp4"
        cmd_mux = [ffmpeg_cmd(), "-y", "-hide_banner"]
        for inp in inputs:
            cmd_mux += ["-i", inp]
        cmd_mux += ["-filter_complex", filter_complex, "-map", video_map, "-map", "[aout]"]
        cmd_mux += vcodec_args
        cmd_mux += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp_out)]
        run_hidden(cmd_mux)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_out, out_path)       # erst am Ende überschreiben: bei Fehlern bleibt das alte Video

        if prune:
            for f in seg_dir.glob("*.mp4"):
                if f.name not in used_files:
                    try:
                        f.unlink()
                    except Exception:
                        pass

        if progress_cb:
            progress_cb(1.0)
        phase("done")
        if status_cb:
            status_cb(tr("Fertig: ") + out_path.name)

        return {
            "out_path": str(out_path),
            "vorspann_len": vorspann_len,
            "intro_len": intro_len,
            "music_dur": music_dur,
            "outro_len": ffprobe_duration(segments_files[-1]) if outro_src is not None and outro_src.exists() else 0.0,
            "total": ffprobe_duration(out_path),
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def plan_tiles(plan: dict) -> list[dict]:
    """Kacheln für die Anzeige: Pre-Intro, Intro, Clips, Outro – mit geplanten Zeiten im Video."""
    tiles = []
    t0 = 0.0
    if plan.get("vorspann_src") and Path(plan["vorspann_src"]).exists():
        d = media_duration(plan["vorspann_src"])
        tiles.append({"type": "pre", "src": plan["vorspann_src"], "kind": "video", "offset": 0.0, "t": 0.0, "dur": d})
        t0 = d
    if plan.get("intro_src") and plan.get("intro_len", 0) > 0 and Path(plan["intro_src"]).exists():
        tiles.append({"type": "intro", "src": plan["intro_src"], "kind": "video", "offset": 0.0, "t": t0, "dur": plan["intro_len"]})
    for i, sl in enumerate(plan["slots"]):
        tiles.append({"type": "clip", "slot": i, "src": sl["src"], "kind": sl["kind"], "offset": sl["offset"],
                      "t": t0 + sl["start"], "dur": sl["dur"]})
    if plan.get("outro_src") and Path(plan["outro_src"]).exists():
        tiles.append({"type": "outro", "src": plan["outro_src"], "kind": "video", "offset": 0.0,
                      "t": t0 + plan["music_dur"], "dur": media_duration(plan["outro_src"])})
    return tiles


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
                           check=True, timeout=180, creationflags=0x08000000, stdin=subprocess.DEVNULL)
            data = tmp_zip.read_bytes()
        finally:
            try:
                tmp_zip.unlink()
            except Exception:
                pass
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        target.write_bytes(zf.read("deno.exe"))
    return target


def render_url_short(source: Path, target: Path, start: float, update: Callable[..., None]) -> None:
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
    update(tr("Short wird erstellt …"), stage="render")
    run_hidden(cmd)


def pick_url_short_start(source: Path, duration: float, update: Callable[..., None]) -> float:
    """Stärksten Abschnitt im Ton suchen; sonst die Mitte des Videos."""
    fallback = max(0.0, (duration - URL_SHORT_SEC) / 2)
    if duration > 1800:
        return fallback
    update(tr("Musik wird analysiert …"), stage="music")
    wav = Path(tempfile.gettempdir()) / f"mvg_urlshort_{os.getpid()}.wav"
    try:
        subprocess.run([ffmpeg_cmd(), "-y", "-v", "error", "-i", str(source), "-vn",
                        "-ac", "1", "-ar", "11025", str(wav)],
                       check=True, creationflags=CREATE_NO_WINDOW, timeout=180, stdin=subprocess.DEVNULL)
        start, why = choose_short_start(str(wav), duration, win=URL_SHORT_SEC)
        return fallback if why.startswith("Songanfang") else start
    except Exception:
        return fallback
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def generate_short_from_url(url: str, update: Callable[..., None]) -> Path:
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in YT_HOSTS:
        raise ValueError(tr("In der Zwischenablage ist kein gültiger YouTube-Link."))
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        raise RuntimeError(tr("yt-dlp ist nicht installiert (pip install yt-dlp)."))

    update(tr("YouTube-Werkzeug wird geprüft …"), stage="tool")
    deno = _ensure_deno()
    out_dir = OUTPUT_DIR / "Shorts"      # wird erst beim Speichern angelegt

    def progress(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = f" {int(d.get('downloaded_bytes', 0) * 100 / total)}%" if total else ""
            update(tr("Video wird geladen …") + pct, stage="load", frac=(d.get("downloaded_bytes", 0) / total) if total else None)

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
        update(tr("Video wird geprüft …"), stage="check")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("is_live") or info.get("_type") == "playlist":
                raise ValueError(tr("Bitte ein einzelnes, abgeschlossenes Video verwenden."))
            dur = float(info.get("duration") or 0)
            if dur < URL_SHORT_SEC:
                raise ValueError(tr("Das Video muss mindestens {n} Sekunden lang sein.").format(n=URL_SHORT_SEC))
            update(str(info.get("title") or ""), stage="title")
            ydl.download([url])
        srcs = [q for q in Path(tmp).iterdir() if q.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if len(srcs) != 1:
            raise RuntimeError(tr("Die heruntergeladene Videodatei wurde nicht gefunden."))
        start = pick_url_short_start(srcs[0], dur, update)
        target = out_dir / f"Short_{info.get('id', 'video')}.mp4"
        tmp_target = Path(tmp) / target.name
        render_url_short(srcs[0], tmp_target, start, update)
        if not tmp_target.exists() or tmp_target.stat().st_size < 1000:
            raise RuntimeError(tr("Der Short konnte nicht erstellt werden."))
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_target), str(target))
    return target


# ── Programm-Icon (blauer Play-Button, 16–256 px) fest eingebaut, damit Logo und Fenster-Icon
#    auch ohne icon.ico neben der EXE funktionieren
_ICON_ICO_B64 = (
    "AAABAAcAEBAAAAAAIAAhAQAAdgAAABgYAAAAACAAnwEAAJcBAAAgIAAAAAAgAPoBAAA2AwAAMDAAAAAAIADSAgAAMAUAAEBAAAAA"
    "ACAA6AMAAAIIAACAgAAAAAAgACQIAADqCwAAAAAAAAAAIAB3FAAADhQAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAAEAgGAAAAH/P/"
    "YQAAAOhJREFUeJzF0zFqAlEQxvH/uM9ddRW0SpEDBBU8gpV4D8FKciGbNGk9gGcIAQsvEEsVTDpZ9e1Y+EDMPhWVkOnfj29m3kh7"
    "tFYeqNwjj/8WCARE7gQU+NkoG+ugW4BUoRBAv5HnORZWiWL1AF0FxAFRIAxahrdOyGsrT9HAKvEvK5NAXAvfiVIJhX7T8N6N6NUN"
    "PiIDqENEDmkAnkrCS80/b+NLAEpsICfwMU8ZTrdMlinlMDuIDIDATuFzoYy/doxnFgWqkWA9Pci5r5xYsAqxOW3negJXBXPcil64"
    "lrOAKt6p/67/P6Y9hZ9H5q8NB7UAAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAAAGAAAABgIBgAAAOB3PfgAAAFmSURBVHic"
    "7ZSxSgNBEIa/ubtsJBpCCtEiiGXAwkbBQtKk8znsbX0FOwWrFJZW1unzAIKKCHZBEYRYSBKi5u52LDbRKptEDCg6sGyx7P/t/DOz"
    "Ujl7UWYYwSzF/wG/CCBAIG7/doACsYVOH1KFcErKSIAAViEbwmHFsLsWYgJ4fnNjMyko8h1ahVwkrC8Km0sZdlYjTm8T6s2Udgx5"
    "4zJUz6iOtcgq9GK3r+SF/Y0MtaqhWgroJfCagHiy8QLcPUXEFdmqW+ViwMG24ahiKBeFfjoa4gUoICIf3TMUsQNLtpYDSvMBsR3d"
    "Yd4aCKAoOhBVXHEVqDdTTm4S7rvKQuYTOhVgmIaqswjg6slyfJlw3rJkQ8hFo8UnAlgV5iJotpXadUzjwZJYKBiw+MW9AAXCALqx"
    "steIuetYHntKwQgmdEM3SYy3CLhoWUwIxayQqr/vvwTIZZzopK+eGjDOZ1/8jO/6bwPeAaj9f4CW7Ix1AAAAAElFTkSuQmCCiVBO"
    "Rw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABwUlEQVR4nO3WzWoUQRDA8X/19MyaXSO5BIIo+AIechIU9KAH38Xn8AX0"
    "ZkCMF4P4BKIHBcGLiCBmNfGgaEAFTVR04/RUeeiMg184vVlZlCkYmMPQ/avqrmLk+LXPxhTDTXPzDtABOsBYABGQaQK+VFAZZBNS"
    "JAHU4MBeYZDDu21D2D3Et/lIiFn3PZw7UTAKxtLDwO0N5UMJ+4oG+FcA9eIzOQxyWOg7zh4tuP9GufI4cOulgsDAgwKWAGkNADAT"
    "1CLGgMV5x+J8wZ0N5dJqyb3XyowXelms2OQBGAY4iYC65Mf2O44s9LjxvOLio8D6e2U2b3c5Ervg+0WdxEcNvIPThzIunCw4czhv"
    "vWISQH4iNJBq52jmesKpgxnZTpX+VIekI/hVqEVAJjAKsPIksLIWKJVviIkB6jtQbyw02V9/VrE8DKxvGns8+BabJwOgKXU9gIZv"
    "lfMPAndfKb0szoS6S9pEEsCJMFcITuDppnJ5WHHzRUWpzTBq235JACNmvLVtXF0LjAIsrwY+lnEw5X68KQggKX/FZvApxPe+j62X"
    "mvGPkXQEIjBbxMuntvvNkwEwfql/F//eH1EH6AD/HeArViunHtMowjEAAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAAAMAAA"
    "ADAIBgAAAFcC+YcAAAKZSURBVHic7Zk9axRBGMd/z8zunblLjE1AothIUgQisZaI4ssH0E4LsUhjo5/B75DqmoiNaIIWgljZqoVY"
    "BNS8CAoBxUCC5sjp7c5jMXdcEGKS27lbAvtvdtgddp/fzvOfZ2ZXzs9vK4dYJu8AsqoAyFsFQN4qAPJWAZC3CoC8VQDkrSjUjYx0"
    "2q6P69sgAApsJ/4oQCXy7X6AhAFQuD0RMTYsPF5OePNdKVsP4lrXe6VMAAIkCsfKcGPcMlQSzo1ann9OePEl5d26csRC2XqIXnBk"
    "NrEqlIwHceqDvT4WMXuxxN0zEScHhXrTX7ey9/0OqjCzkAhGvJEdkCqUrXBrImLucomZCUs1gl9N3z0kSBAA1Y5hBR+g4kEqsTAz"
    "GfPgSpmb45bEwc8/vp8JABIGAMX9c24niFM4XhXunY2ZvVDi2mkPUm/6flkUrA7s5lABRDomnhoxTI0YpkcNcx8SPm1qppQKMgKC"
    "IHsEIW2PtNJt+oSldqnMncnI148uIcIAHODhRvyoNFPfvnrKUrGQuu7SKVgK7efhbT9YgdjC0oajtpiwlUBkuit44Tywh5z6N24F"
    "NhrKw48JCyspDQcDtvtqHQTAqe5aZZ128v93Co+WEp6upqzVlUrUWm5kKNE9G4H2G23P9c9WU+ZXEpY2lcjAUNwxdBYFA9AdR9VO"
    "4MubjifLKQsrKSUL1bhT5EIoCIARweCDsgIIrG0ptcUmr9YcjQSOln3f0EvsQB7ws4gVqDeV2mLCy6+O9UYrz+Pe7Q0yASgQG1jf"
    "Vu6/bTI2bHj9LeX9D6Uaw2CgPP+fJNQPjkbil8wlAwOtmaUfO8tgJq7EvpiFNOh+FAygnxv5nTr0n1UKgLxVAOStAiBvFQB5qwDI"
    "W4ce4C+GYeuAjx6/jgAAAABJRU5ErkJggolQTkcNChoKAAAADUlIRFIAAABAAAAAQAgGAAAAqmlx3gAAA69JREFUeJztms9rXFUU"
    "xz/n3vfeZDo1lQnWWn+ShRQGBRetgljqIvg/uHPtyq0b/wFxoSAuxEUpiNCNNC6iVEJ140KlFINpwUycpNJWGpqOaTrz7r0u3ntN"
    "VLTtOHdOoO8LsxjmMe97Pu/ce849M3L89K3AAyyjbUBbNQBtA9qqAWgb0FYNQNuAtmoA2ga0VQPQNqCtGoC2AW3VALQNaKsGoG1A"
    "WzWA2DcQwAiIxL7TaIoKQIA8QH8IA1eCiHnDERQNQBX8I02Ye9IwOy30h+ABu4dAJDG+VCgCzSy8fTTj6EHDjduBU8s5py85NgeB"
    "ZiKkBpzyTDoKAADnod2A2WkhAAcawpvPp5x43HJu3THfdVzZglZaLA2vBCIOgDK/E7Pz1gME6MwYOjOGV5+wfPxTzvdXPVtDaKbl"
    "dRMGES0DAiBGMCUMoagEPhSfHWkb3n0l4+frns8u5XzR9YhAs3QUJgQiGgAAymB3qwLiQwHkSNvwzosZLz3qONN1/HDNI0DDFlkT"
    "G0Q0AEIR/L8F8HcQrz1jmXvacrbn+OjCkJVNmEpKEBEhRM8Ad5dLdoMwAnNPWTozwkLXs7juWNoI7EuK0hmjYkRthAL3/vR2gzjc"
    "MrzRSfjgRMbrz1p8gM1BnDSImgFVG3w/MlIsGw9MZ8JbL6S8/JjhbM/zVc/hyu8d194QdwkwWscnApadDfTYIcuxQ5bZA8KHF3JE"
    "/rm5jqq4Z4H/2e9K+Rq4IuDjhw3TKeR+fK30ngYQyjKa2SLg+a7nxgCsGV8GRF8Co6gqn9X+sbjmOLPi+O6KJxnzKWpPAaiqhi3n"
    "B+t9z8mlnPmuZxhgX1I++TEWhKgAnA/37LXqA6zAb38EPr2Y8/Wa4+oWtBLIIh2Y1DOgCsoIXN8OfLnq+HzFsbwRaKWwPy3PD5G6"
    "QTUAIQCys87PrTneP5+zejPQsPBwowg89rxg4gCqJseWgV/c8Hyz7jm1nLPt4KFsMoFXigzgrx3LnXUOrN30fLKUs3jZ0x8Ux+Cp"
    "ZPIToqjzgMxCK5U7DEy5sy+seua7jl4/0EyKqVAIOlOhOADKUraxHfhl0/Nc2+ADfHvZ8d6PQ3r94mnvVwy8ksT6q6wAwwAHm0Kn"
    "XUyEz//uue1gyu5MhrQVdQmkAtduBRZ+DRgpAs+s/iR4t6JuggFIBNK0fB+xno+q6GXwv8Zie0H1j6PaBrRVA9A2oK0agLYBbdUA"
    "tA1oqwagbUBbNQBtA9qqAWgb0FYNQNuAth54AH8Ctc1MtElPknwAAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAAAgAAAAIAI"
    "BgAAAMM+YcsAAAfrSURBVHic7Zzdj1VXFcB/e59zmaEwJTNiCQRIG6tArbamRhOb0D7oiy8+GR/64osvbXzxyf/BP8FoX9RoQkKt"
    "ERFS25hiY6utVQuBlvksw8gwMHNn7nzce/bePuxzuAeKSIELZ89av2Qy4ebOcOeu3157rf1xzeEjawFFLPZBvwDlwaICCEcFEI4K"
    "IBwVQDgqgHBUAOGoAMJRAYSjAghHBRCOCiAcFUA4KoBwVADhqADCUQGEowIIRwUQjgogHBVAOCqAcFQA4agAwlEBhKMCCEcFEI4K"
    "IBwVQDgqgHBUAOGoAMJRAYSjAghHBRCOCiAcFUA4KoBwVADhbBoBDGBM/K7cPskLYMovBxQeAmDVgtsmeQF8gBBgSwbDOZgAXdfP"
    "CMqtyR/0C7gbQoDtLXhyp+XQqGHIGmZWPO/Pez7uRDlaNj5PPw/35iQpgCEGN7fwzf0ZLxzMGRsyhABFCEy3PS+fLnh7PrBRQG4g"
    "K3OdVxOuI8kpwBgoAjw8BIf3ZuzaashNnAYeyg0HxzJ+/NUt/OBQzv6ROA90HXR9v2ZQIklmgIrhDB5uxfTuiTaHcoSPDhu+dyDn"
    "a7ssr4wXnF0MXF4PLKwBBjKjUwMkKkAIcRRvyWJKN8TKv174+RAfe3zU8tJTLTo9OLfg+fVHBe9fCXRdrA8yUwok1IQkBaio0j6U"
    "UtRyu60Fdmtu2JrDzr0Zj41afnGmx+sXPEvd+JzMxHpCYkZIWoDM9gW4GVVGqI/u3dsMLz7V4tk9nmOTjn9d9rR7sUbIbXx+EGRB"
    "0gIYe3slXbUwVGWEbS3DN/ZkfPmzlpm2581Zz6k5z8RyoPBxagAZ00LSAsCnS9lVRgjlD25vGQ59JuPxUcu39gdePlNw6qJntYjS"
    "5OaTGWSzkbwAdzJpGyDUApsbw6M7DD96usWBHY4TMwWzq9DpRQFyG/+fzehB8gLc6eisdwzV1LBj2PDdL+Q8vy/jn/OeP0w7zlz1"
    "rJUZYTPWB0kLEFf+yn/cxepOfWrIM9iz3bBne8bX91jemvUcPV9wbingfCw8DZunY0hWgCre9yD+1/3OKhsYA6NDhm8/lvHEmOFX"
    "Zwv+PBs7Btg8rWOyAgADeefr2cCH+MCjOyw/fLrFoTHHyRnH7GqgvQEb5WJSJU6KpCtAOeS7VZQG8OvrIoxsMXzncznP7c2YW/H8"
    "ZS5wfKpgbhWsLVvNBLNBugKUDPoNr4tgDIwNG8aGMz4/Cs88YvjlWcd7l33cdSxFSGlaSF4Ac5/e6Xp9gInF4lceydg3YjnyYcHx"
    "KcfCejyZlFnI6EvT5M4heQHuJzdrHXduNXz/iRYHRy0nZxwTbc+ldVjvlSLY8qhaaOY2dLoClKOq9YAOANb3GYZzeH5fxjO7LIsb"
    "gddmPL+fdFxai61jqyZB00jyQEiTqHYdQ1ko7huxvHAg58Uv5ewfAQw4HwvFJqYAFeAeUM8GLsT64NndGc/tztmexce8p5GVYdIC"
    "NG1A2fKkkQ/QyuCLY4aRVlmoNjD4kLIApplp9drKZHlusYnzfp10i8CGUdUB1QmjThf+fsnTLmicpHVUgHtANcirhmRpI/DquOON"
    "C461LhgLNsQ1gqahAtwF1ai3JhZ6i+uBc4ueV8cd7833D5ZkNnYCTawDVIA7oAq8MTHAKxuB12Ycx6cdM8uBdjceOMlMLLJcQ9cA"
    "QAX4VFSBx/TX/KfanqMfFfzpgmdhIz6vVRanIcT7Ck0NPqgAt019ng/AlfXAm7OO3553TK2U9wyy/rW16gcaHHsgYQEC0KuuBg+o"
    "zL424omBLzzMLHs+WAicmCk4czWw3ovtaGbLm8oDeSWDI1kBYLBv9o2VfacHJ6YKjp4vmF0zrBUBQ1z1M6H8GvBrGgRJCzAoqmtl"
    "AMu9wNxK4Nik4+THjivrYE2gVTsyXuWg1IIPKsB1VPcFrIHVHvxj3nFs0jPe9iysBVZdvIlUzfP14i7F4IMKAFzf1hkDU0uB33xY"
    "8PoFR6db3jwuL4qkOM/fCtEC1ANZtXVvXXT87HTBuasBR+0GcdnSbTZEClBfwQPoOVh38M6c46ene0y3Y3E3VI54vRq2iajO/FsD"
    "nV5gfNHz7nzgo6XABwuOy2uxn4e4vLvZSVaAGy+G3Iqb9fMTS55XzjvevuS4Up7xh5jyN2Oq/18kKwABMgzm/3wWXH3pFuJO3bGJ"
    "guPTjqkOdIs4x2cCbgLfjGQFCMTRuq38C278hJAbd+ra3cD4Vc/RCc/f/uPolDt1Q1m//Wvymv2gSFYAAGsMW6yhWnqv4lfv5xfL"
    "nbpTFxyTK/FDojz9e33SRvyNJC3Aci/Q7np2DtvrdgMsgIHJJc/PTxf8dS5e8fb0L21ID3xFsmcCrYGLncAfpx3zG/12rfBx3f6d"
    "OcdP3u3xxqxn1cUNm6q619j3STIDBKIA6wX8btwxsQxPjhoeymI/P9EO/HvBcbET64JrO3Ua+U9gDh9ZS/ptqYq/3ILxQAa9on8+"
    "P8Ubu/eTJDNAnaq967pShkqIhl/KbArJC1DFOKt/UIOO+tsmeQEqUrqT3ySS7QKUe4MKIBwVQDgqgHBUAOGoAMJRAYSjAghHBRCO"
    "CiAcFUA4KoBwVADhqADCUQGEowIIRwUQjgogHBVAOCqAcFQA4agAwlEBhKMCCEcFEI4KIBwVQDgqgHBUAOGoAMJRAYSjAghHBRCO"
    "CiAcFUA4KoBwVADh/Bd9dBb9y71eMQAAAABJRU5ErkJggolQTkcNChoKAAAADUlIRFIAAAEAAAABAAgGAAAAXHKoZgAAFD5JREFU"
    "eJzt3elzHMd5x/Fv9+4Ci4vgTSuSLNlO4jj2q7hSlapU5S/On+FyKnYptkWKokRKvC8Ai73v3X7yomd2B0tQPExiAPTvUwUSApZD"
    "EEL/pqePp91//ffQEJEk+bK/ABEpjwJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJA"
    "JGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQp"
    "AEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJ"
    "mAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoA"
    "kYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgA4pVzZX4AkoVr2FyCRy1q8"
    "Y9n4rfB5s6P/LfIhKABOiRBiA1+8WRYG7ujvIh+SAqBkzoEzsKx1zwvvQ2z0HvCFj6k3IB+KAqBEBliIjbziYb0CtQpUPTjnsADT"
    "uTGaw3gew8G7o2Eg8vdQAJTMExv97prj8gZcrTt2alDxjskcOmNjfxjYGxr9qcOAShYA+aOCegPyvhQAJbDsF+9gqwbXNx2/2PV8"
    "vgPX646tKlS9YxKgPTYOho6n/cCzHjzrG90pTAu9AV+8rsg7UACcMO9gNo/v16vwD9uO3131/P6654sdz3Y1e+53EIDJPL41RoE7"
    "zcD/PA981wq0p3FU0BF7BEZ8fTEFFAjyJgqAEgRiw92qwZe7nt9d8fz2sufS+usf7j/b9lxYj+9v1eBhDw5HRmcC07AMDQeLYBB5"
    "EwVACQzw3thd93xxwfHFBc927Q1N1jk+2/Zsfub4xQXPrYZxs5H1BsZxLKDi4gxCccowHycQOY4CoCQO2KjBlbrjYnZnn4WjC4Ly"
    "dpuvCah5x41Nx+V12KkFdtfg0jo86hp7Q2M4hYnBPECweJEKy2uKrFIAnJBig87n9qsO1vPpPx8brcue54srAle79LUK/OKi59qm"
    "418uee62An/ZD9zvBNqTOGU4CoaZw7O8Zt4TUIdAcgqAEuQDd/l8voXs44WGftxNO1gcP/BAvQL1DcfVDcfVOmzW4JMtR2tsDGbQ"
    "mhjNIRyMjP4M5tnf+bqlxpImBcAJWizx9VDN7vrOLe/Mb+qp+5UGnLu25fn3quNfL0N/GgOgMTDuNgNfHQQe9QJDc/HPWwyQfMZA"
    "IZA2BUBJjvQA3uHP5Y8SZllwuHitS3XHpXr2CoPW0LhSh/Ua7DbhQc9ojWAwjyHgXFx9qB5B2hQAJXC2nLarurjqD7LewVsM2OWb"
    "g+x1030OduuOX9c817c9X+4G/vh8zs1G4HnfmFoMCQ/4wioijROkRwFQIk9c9199z6oMizt3NjZQDBDnYLPm2KzBds2DGZsV+LFj"
    "7I+gNTJGAYbT5evzVYWaNUiHAqAMbtngqj52xWE5+v/Ol8uXA7+mR7C77vi3655PtzyPe8bdduDbQ+NhN9AYGrMQL5AvLc4DQPsM"
    "zj8FQFlc7H5/qH3+xWsU1w8EiyFzse65WIefbRvXt+BK3fi0A896RmcMnSkMZ0Z/HtcjQGFloZxbCoCT4oCw3OvvXX6rdYvBvA/V"
    "2IrrByorF91dd/xzxfPJFvx+7NkbGM/7gfsdeNAJPO4Z3RnMss1GeZqoN3A+KQBKllcA+hiKo/sh+0u8g3rVUa/CtQ3HlxeMvSF8"
    "sg2fbsPDrvGkbzzuGa0xTOfLXkqxR6AwOB8UAKdAsVF9jC73auMtqnjHjU3P9hr840VPe2R81wz84Xng9mGgacuvq9ibUC2C80EB"
    "kIjijEGx8ToHFReLkOzUYq9gZy32GDYq8EPXaAyNzjQuMc6XKy8GDEv8N8nfTwFQto/5DHCM4jQhHN+Ar216/vNT+HTL87eDwK1G"
    "4Mdu4HAcy5NVnFv0CBYLk9Cuw7NIAZCgV2YMbDlOYMQlypfrngvXYtGSCzW4vgnPh0ZzBN1prFQ0nsc9BnnR0mKpMjkbFAAlONIA"
    "bTlAV4ZiAZHVBUDVCvxy13Nlw/GbkacxNJ734cdO4E7TeNoP9GfxmaDiCpWJNDZwZigAShayt9NgdWWhA+o1qNccN7Yc4ynsD41P"
    "tuHqhnG/43g+CDTHsXjpNCzHFTQ2cDYoAEqU3/0XAXBKWk2+snD1y1mvwc+8Y7fu+aeL8GIA91qBbw8D3x7O2RvGgiQVz9HahOoR"
    "nFoKgJKdph5A0ZFdh/nHXHws2KnEmYKfbcG1uufyOlxYM+614xqCTlaUJP8zOsfg9FIAlKRYISh/57S1k8WuQ17/td3YdmyveX6+"
    "47jTDPzpZaxevB+WJc5WVyNqDcHpoQCQNzqy+i97ZAm23M68u+7YXY+9gqqH7Rrcaxt7A6M9jWXN54XraA3B6aEAOCmrt7ts8vy4"
    "0ffTKp8xyKf9XqlMtOn5j0/g023HrYZxuxG424kLiYYhu+vnJcx9vI56A+VSAJQslDwN+D5emcYk/htqHi6uey6sGZs14+IaXN80"
    "Xg6M9tQYzqAzhu7E6M3igKFnuR1aTp4CoCTOxUYzM2MWjIDjLLYDV+gR5LxzfL7juLzu+M0VozeG5jhwMIwHmtxrB35ox/MMnIs/"
    "hMGpGlEZFAAlO4s9gFXHrR9Y87BWd1yqx8+Op3A4gs/6cGMDdtfgYcc4GMblxVNbViM6I09E54ICoExu5fcz7nXrBwDWa54bFbhQ"
    "h8+2Hb/cddxqBL7aj5WJutNlL2IxNgCL7sAZz8hTSwEgH9Tq+oHFFKKLz/pbHrZqjkv1eNZhvWZcbMCDjtEcG71pLEaSDzaelQHS"
    "s0oBUKZ8FuCc3d6K6wfy/15Vrzp+ueu5XIcvdxxfvYy7Dh/1AoNZ/BNVd7Ty8XEzBh+rhkIqFADy0bxu12HegNcrjhubsFP11Bxs"
    "VmFnDZ71oZXtNpzMl68/bv3A4mwFUxi8DwVAyU64HEBp3OKXV0832lxz/Paq58qG57PtwHfNwPftWKuwPTFmECsXF0qT5YVJKj5+"
    "/+ZzBcD7UACU7CwtBPoQVrvzIds8tFVz/Ooi7NTiY8HVTeN539EcxwHCg1HsFfSnMA3xAtXK8iwEXxg5VBC8PQVASSxfSusd1azC"
    "Tko/uHnwrfYGrm46ttY8n1+A0Qz608DeAO60Yq3C+51AZ5JNNxrMLe41qGTnGsznafSoPhQFQMlWF9GkZvXocu9ib2Crlr+iQncM"
    "lzcCl9bjPoMHHaMxskWNQl/YsOR9di2lwFtRAJRMP6tHqxIdZ2cdfn3Jc3XDcWPD8deDwM2DOU+HYFnZ8nlYbj2uOAghGyc4wX/H"
    "WaQAkFPhlarFhferHjaqcQHRZsXjXNxPMAhGa2LM863HBq6iRv8uFAByqhypVnxM5eJLdcevdj3Pe0Z7HJjNoV1YSx2y6iqmu/9b"
    "OYv7T+SccytvgTjyP89G/C+tw893PJ9ve7aryx9iT/lFVs8aBYCcevncf15ZqF51XNuAa5uwVc0+bz991oEcTwFQktXpL3m94vcq"
    "v7tv1OKMQK2y/CHW9/PdKQBOSuGn0zlwPntzmgV4W1b4Pd9Gbce9QN6aAqAk6gG8veKuwnwvQHcCrfGy+nD+Onk3CgA5tfKGHyxO"
    "8+Xd/9HMeNE3nvWN3sxiNSH1pN6LpgHl1FndRlxxUKnE98dzeNSNJcWe9AK9yTIYQuHPKAzejgJATo3j9vvn3X6yz/3QCny1Z9xu"
    "xl5Af7q8+zuWXdpsv5C8gQJAToW8Aa/ujDSLXf7uFJ71jL/sBf52EHjUix+bhazEeL6UWCXG34kCQEq3evxYUWcSawbePjTuNgMP"
    "Osb+KDb+uWUVg7LtwI7lHgB5OwoAKU3xrp+3+/Ec+jOjNTLa43ga8Y+dwK2G8agb6E6ygUGO7gLMt1OHwnXlzRQAcuJWi4ZWs9Y6"
    "nRtPOsbdduBuK47yN7O7fXNkDObL8wbzFu6Jm4BCWJYdU+N/ewoAOTHF0f1iV98MOuPAk67xzaFx+zCWBNsbGMOZ4XBUsm5+rXK0"
    "fkBxhaDu/O9OASAf1Wox0JBV8CkWQTkYBG43Ajcbge9axvO+0ZzEgqABFx8R8u6+Hb128Wh1Nf53pwCQj2q12Edl0d2H3tTY6xs/"
    "tAM3DwLftwLPBrEUmDlYq0BeGOi4KcL84/L+FAAlOe9LgYtd8tWSZ2bwrB+r/95qBO63jf2h0ZnCYJbd1TWddyIUACXJT9Q9b17X"
    "6Kchzud3JnEBz712fNa/cxgPDZ2zLOyZjw/YOfz+nDYKgJNSGLSKa9uNmRnB7Nz0BMxiQy4WOp0GYzCFw6HxuGd814qVfZ8P4sh+"
    "dxLDwdyyms95OynpNFMAlOQ89ABWl+zmR31DbNTNofG0H9gfwv7AeNQz7jQDz/qB/szFsujZs/7q9+OMf2vODAVASc76GMCRufys"
    "tVazhfjB4HE38M1BfMZ/0jfa2fN9e2yM5255MAgwQ2v5y6IAkHdSXHm3mMvPfg8GvYnxbGDcPAj8dT8+4++PjJE5qh4qBjUPaxyt"
    "/ivlUADIW8vv2PDqIZ1m8LIfuNM0bjcDd5qBJz3jcAxzc/HObsu1AMden+X1lQknQwEgb1Rcs18pfHw0M1oT6E9i1/5pL/BNw/i+"
    "HXg6MIaz+Lp6ZXmd183nF/8uOTkKAPlJeYOd2/IZH6A7Nu53ArcP4XEvZGv2jYMhtCfGcLqcEZihQh2nlQJAjrX6rO9dtmZ/Ehft"
    "POgY3zcD3x7C036gO7U4cJdtxlks9y0sA5bTRwEgx8q76UfX7BtfNwJfNwL3WoG9IXTHca5/ks3lHxkgVKs/9RQAsrDYTruyWac/"
    "NZ714LvDwP/tB75pBp72A9PgqDlYL+7SW1xI7f8sUAAkrjgNl7+fd+EhDu593wz878vAN43A8z50p8bc3GJAcKaWfmYpABJ17Hw+"
    "LEbrzOBwFFfuffUy8Ke9OU96xnTuWKvARmV5bp/u+meXAiAxq9Nwq/P5ucOhcesw8Idnc241jL0BhBBfPLejZbjk7FIAJOR1lXdn"
    "AYYzYzqHcYDOGB52YvXdrxuBZ/24QWe9sly2+1Nz+XJ2KAASUGywq1t1h1Njf2A87RuNEbQmxsHQeNSNlXkOh/EC5mCqbv65owA4"
    "5467689DXKxzODJeZkdsPerFRTzNidEeG42RMZnFsFirxEU9WrN//igAzqnFbjuLjXhxuk6AF/1syW4r8LgXOBxDawKjOYxnxjTE"
    "xwKzbG8/uuufVwqAkhzZSPOBrnmk6i5H5/NHM6MzhpeDwL228fWBca8VeDkKjOZuMbAHscFX/HKQT3f+80sBcMKW028O7z7MsbZH"
    "BuUKtbHzufxZgB/bsfLu3XbgcQ9eDKE7MYYzt9yd5woHbKi+fhIUACXJB+MqPr69r1ee8QutdjyPjfxx1/jbQeDWQeB+N9CaOKZZ"
    "977qX53SK5bylvNNAXBSVibNnYt36LVKfMtrBa5O0f2UfH/+cUU4+5PAoy7cbhrfHsY6fAcDozt1zPJ9+W75pb3my5RzTgFQoqqP"
    "c+u1d+xrL3rshf35+Zl63YnRn8D+IHC/A1834rbd1iQOAHoHa/5oDT475tqSBgVASRbjALjlYFvhc69THN3P9+fPQtye+0M78KgT"
    "5/EbY6M5gsYoHsAxtWUvwxeuJWlTAJSkeIptsVjGTzXK1dH9eYDWOPCwY9w6jNN6TzrG4djozpZltr2LdfgWz/hq+ZJRAJyUrLUX"
    "zwUYz43RPM67V70jBHDZ7Xl1mrA42JffwV8OAn9+EZfsPugazXHs/k9DXLVn2evzHX4iqxQAJQjAJMStti8HgdaO51o97rJ7G8NZ"
    "PF3n5kHgjy9iAc7WBPKjsfPn/OIdXzd9OY4C4IQcma4zmM4d+0Pj+5ZxZcPYrMLWW/zfGEyMu63An/fiRp2H3Xim3iwUVvstfhH5"
    "aQqAEuSPAa0x3G0F6lUwHJ9vO7YqyylCsteZxWW6/WlcxnurEQPgYccY23I6Md+frzl8eVsKgBOWz/U7YDCFR11jNDP2hnN+vuO5"
    "sQE71eIIfzwuuzGOU3vPss07L/vGaJ41+MJ8vhq+vAsFQAnyAbm5QXcS1+k3x8bTHlzPAiDvAcwNhjNojmB/FGiOs3r72TRgcRmw"
    "Gr+8KwVAyeYBQjDmAYbTwIt+tjAom8IzH3sNk2yn3iQUVv+V+HXL+aAAKJGH5QYci8U58vl7y4/K9uA9+EKxzorTLj35MBQAJSpu"
    "vlk9abe4J6C45t+I5blEPgQFwCnhs/58lVcX7WiXnnwsCoBTolib/3UBIPKhKQBOITV4OSkaSBZJmAJAJGEKAJGEKQBEEqYAEEmY"
    "AkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCR"
    "hCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYA"
    "EEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRh"
    "CgCRhCkARBKmABBJmAJAJGEKAJGEKQBEEqYAEEmYAkAkYQoAkYQpAEQSpgAQSZgCQCRhCgCRhCkARBKmABBJmAJAJGEKAJGEKQBE"
    "Evb/o3e+wCTejckAAAAASUVORK5CYII="
)


# =====================================================================================
#                                   GUI
#   Assistent in 4 Schritten: Start · Bildmaterial · Extras · Fertig (Clip-Editor)
# =====================================================================================
APPEARANCE_MODES = {"system": "System", "light": "Light", "dark": "Dark"}
_appearance = load_settings().get("appearance", "system")
if _appearance not in APPEARANCE_MODES:
    _appearance = "system"
ctk.set_appearance_mode(APPEARANCE_MODES[_appearance])
ctk.set_default_color_theme("blue")
appearance_var = None


def set_appearance(key: str):
    """Darstellung System / Hell / Dunkel – sofort umschalten und merken."""
    if key not in APPEARANCE_MODES:
        return
    save_settings({"appearance": key})
    ctk.set_appearance_mode(APPEARANCE_MODES[key])
    try:
        appearance_var.set(key)
    except Exception:
        pass

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    _HAS_DND = False

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

MOBILE_URL = "https://github.com/loopweb/mvg-mobil"
RELEASES_URL = "https://github.com/loopweb/musik-video-generator/releases"

# ── Farben: MVG-Blau als Akzent (wie bisher und wie MVG Mobil). (hell, dunkel)
ACCENT       = "#1f6aa5"
ACCENT_HOVER = "#185889"
C_BG         = ("#eef2f6", "#18191c")
C_CARD       = ("#ffffff", "#232529")
C_SOFT       = ("#f5f7fa", "#2b2e33")
C_SOFT_HOVER = ("#e6ebf1", "#353940")
C_BORDER     = ("#d6dee7", "#3a3e45")
C_TEXT       = ("#1b2533", "#e9ecf0")
C_MUTED      = ("#5d6a7a", "#9aa4b0")
C_ACTIVE     = ("#e4eef8", "#1c3247")      # gewählte Karte
C_TODO       = ("#a7b1bd", "#4d535c")      # offener Schritt
C_WARN_BG    = ("#fff4dc", "#3b3220")
C_WARN_TX    = ("#8a5a00", "#e8c06a")
C_DANGER_TX  = ("#b3261e", "#f08a80")
C_LOCK       = ("#c0851f", "#e0a53f")
C_LOCK_PRE   = ("#7a4fc0", "#b090e0")
C_OK         = ("#1f7a4d", "#5fd39a")

WIN_W, WIN_H = 1100, 740
MIN_W, MIN_H = 1000, 680

SEL_THUMB_W, SEL_THUMB_H = 128, 72


APP_SLOGAN = "Simply cut to the beat."      # Slogan (in allen Sprachen gleich)


def F(size: int, bold: bool = False) -> "ctk.CTkFont":
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def _open_url(url: str):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def fmt_time(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


# ===================== Zustand =====================
music_file: Optional[str] = None
music_dur: Optional[float] = None
selection: list[dict] = []            # [{"kind": "video"|"photo", "path": Path}]
current_step: int = 1                 # 0 = Willkommen, 1..5
render_busy: bool = False
render_failed: Optional[str] = None
_url_short_busy = False
_lang_dialog_open = False





# ===================== Hauptfenster =====================
def _boot_root(dnd: bool = False):
    r = None
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
                root_old = getattr(tk, "_default_root", None)
                if root_old is not None:
                    root_old.destroy()
            except Exception:
                pass
            r = None
    if r is None:
        r = ctk.CTk()
    r.title("MVG " + APP_VERSION)
    r.update_idletasks()
    sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
    w, h = min(WIN_W, sw - 40), min(WIN_H, sh - 80)
    r._mvg_size = (w, h)
    r.minsize(min(MIN_W, w), min(MIN_H, h))
    r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
    _apply_icon(r)
    try:
        r.report_callback_exception = lambda et, ev, tb: _log_error(
            "".join(__import__("traceback").format_exception(et, ev, tb)), show=True)
    except Exception:
        pass
    return r


def open_language_dialog(first_start: bool = False):
    """Sprachauswahl. Beim ersten Start als eigenes Fenster, danach (⚙ → Sprache) als derselbe Bildschirm
    über dem Hauptfenster – der Wechsel klappt ohne Neustart."""
    global _lang_dialog_open
    if first_start:
        _first_start_language_screen()
        return
    if render_busy:
        ui_status(tr("Während des Renderns nicht möglich"))
        return
    if _lang_dialog_open:
        return
    _lang_dialog_open = True
    app.update_idletasks()
    sc = ctk.ScalingTracker.get_window_scaling(app) or 1.0
    w, h = max(600, int(app.winfo_width() / sc)), max(500, int(app.winfo_height() / sc))
    bg = _language_background(w, h, footnote=False)
    try:                                   # Hinweis direkt ins Bild (kein Kasten um den Text)
        from PIL import ImageDraw
        d = ImageDraw.Draw(bg)
        t_ = tr("Esc oder ✕ – ohne Änderung zurück")
        fnt = _load_font(13, False)
        bb = d.textbbox((0, 0), t_, font=fnt)
        d.text((w // 2 - (bb[2] - bb[0]) // 2, int(h * 0.9)), t_, font=fnt, fill=(140, 150, 165))
    except Exception:
        pass
    ov = ctk.CTkFrame(app, fg_color="#101318", corner_radius=0)
    ov.place(x=0, y=0, relwidth=1, relheight=1)
    bg_img = ctk.CTkImage(light_image=bg, dark_image=bg, size=(w, h))
    ctk.CTkLabel(ov, image=bg_img, text="").place(x=0, y=0, relwidth=1, relheight=1)
    ov.lift()

    def close(_e=None):
        global _lang_dialog_open
        _lang_dialog_open = False
        try:
            app.unbind("<Escape>")
        except Exception:
            pass
        ov.destroy()

    def pick(code):
        global LANG
        close()
        if code != LANG:
            save_settings({"lang": code})
            LANG = code
            rebuild_ui()

    _language_cards(ov, bg, w, h, pick, current=LANG)
    # Zurück ohne Änderung: Button oben rechts oder Esc
    xb = ctk.CTkButton(ov, text="✕", width=40, height=40, corner_radius=20, font=F(16, True),
                       fg_color="#232833", hover_color="#323a48", text_color="#e9ecf0",
                       bg_color=_bg_at(bg, w - 44, 44), command=close)
    xb.place(x=w - 64, y=24)
    app.bind("<Escape>", close)


def _bg_at(img, x: int, y: int) -> str:
    """Farbe des Hintergrundbilds an einer Stelle (für die Ecken abgerundeter Elemente)."""
    try:
        px = img.getpixel((max(0, min(img.width - 1, x)), max(0, min(img.height - 1, y))))
        return "#%02x%02x%02x" % tuple(px[:3])
    except Exception:
        return "#101318"


def _load_font(size: int, bold: bool = True):
    """TrueType-Schrift für ins Bild gezeichnete Texte (Windows: Segoe UI / Arial)."""
    from PIL import ImageFont
    win = os.environ.get("WINDIR", "C:\\Windows")
    names = (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"])
    cands = [Path(win) / "Fonts" / n for n in names] + [
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]
    for c in cands:
        if c.exists():
            try:
                return ImageFont.truetype(str(c), size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _language_background(w: int, h: int, footnote: bool = True):
    """Hintergrund der Sprachauswahl: Splash-Bild abgedunkelt und weichgezeichnet, darauf Icon, Titel, Slogan."""
    from PIL import ImageDraw, ImageEnhance, ImageFilter, ImageOps
    sp = _find_asset("splash.png")
    img = None
    if sp is not None:
        try:
            src = Image.open(sp).convert("RGB")
            src = src.crop((0, 0, src.width, int(src.height * 0.7)))     # ohne Ladebalken/Slogan des Splash
            img = ImageOps.fit(src, (w, h))
            img = img.filter(ImageFilter.GaussianBlur(radius=max(8, w // 70)))
            img = ImageEnhance.Brightness(img).enhance(0.32)
        except Exception:
            img = None
    if img is None:                                     # Ersatz: dunkler Verlauf
        img = Image.new("RGB", (w, h), "#101318")
        d = ImageDraw.Draw(img)
        for y in range(h):
            k = y / max(1, h - 1)
            d.line([(0, y), (w, y)], fill=(int(16 + 14 * k), int(19 + 24 * k), int(24 + 40 * k)))
    d = ImageDraw.Draw(img)
    cx = w // 2
    ip = _icon_path()
    if ip is not None:
        try:
            ic = Image.open(ip)
            if ic.format == "ICO" and ic.info.get("sizes"):
                ic.size = max(ic.info["sizes"])
            ic = ic.convert("RGBA").resize((84, 84), Image.LANCZOS)
            img.paste(ic, (cx - 42, int(h * 0.09)), ic)
        except Exception:
            pass

    def center_text(y, text, font, fill):
        bb = d.textbbox((0, 0), text, font=font)
        d.text((cx - (bb[2] - bb[0]) // 2, y), text, font=font, fill=fill)

    center_text(int(h * 0.09) + 100, "Musik Video Generator " + APP_VERSION, _load_font(34), (240, 244, 248))
    center_text(int(h * 0.09) + 148, APP_SLOGAN, _load_font(17, False), (120, 170, 220))
    center_text(int(h * 0.47), "Sprache wählen  ·  Choose your language  ·  Elige tu idioma", _load_font(18, False), (205, 212, 222))
    if footnote:
        center_text(int(h * 0.90), "Später jederzeit in den Einstellungen änderbar  ·  Can be changed later in the settings  ·  Se puede cambiar luego en los ajustes",
                    _load_font(12, False), (140, 150, 165))
    return img


def _language_cards(parent, bg, w: int, h: int, pick, current: Optional[str] = None):
    """Die drei Sprachkarten auf dem Hintergrundbild (erster Start und ⚙ → Sprache)."""
    cards = (("de", "DE", "Deutsch", "Willkommen"),
             ("en", "EN", "English", "Welcome"),
             ("es", "ES", "Español", "Bienvenido"))
    cw, ch = 220, 150
    gap = 30
    total = 3 * cw + 2 * gap
    x0 = (w - total) // 2
    y0 = int(h * 0.56)

    CARD_BG, CARD_HOVER = "#191d24", "#1d3044"
    BORDER, BORDER_HOVER = "#3a4150", ACCENT
    for n, (code, badge, name, hello) in enumerate(cards):
        x = x0 + n * (cw + gap)
        # Ecken der abgerundeten Karte in der Farbe des Hintergrunds darunter
        pts = [(x + 2, y0 + 2), (x + cw - 3, y0 + 2), (x + 2, y0 + ch - 3), (x + cw - 3, y0 + ch - 3)]
        pxs = [bg.getpixel((min(w - 1, a), min(h - 1, b_))) for a, b_ in pts]
        corner = "#%02x%02x%02x" % tuple(sum(p_[k] for p_ in pxs) // 4 for k in range(3))
        f = ctk.CTkFrame(parent, width=cw, height=ch, corner_radius=16, fg_color=CARD_BG, bg_color=corner,
                         border_width=2, border_color=BORDER)
        f.place(x=x, y=y0)
        f.pack_propagate(False)
        is_cur = code == current
        if is_cur:
            f.configure(border_color=ACCENT)
        b = ctk.CTkLabel(f, text=badge, width=44, height=26, corner_radius=8, fg_color=ACCENT, text_color="white",
                         font=F(13, True))
        b.pack(pady=(18, 8))
        l1 = ctk.CTkLabel(f, text=name, font=F(20, True), text_color="#eef2f6")
        l1.pack()
        f._mvg_cur = is_cur
        l2 = ctk.CTkLabel(f, text=("✓ " + {"de": "Aktuell", "en": "Current", "es": "Actual"}[code]) if is_cur else hello,
                          font=F(13, is_cur), text_color="#7fb3de" if is_cur else "#93a1b2")
        l2.pack(pady=(2, 0))

        def enter(_e=None, fr=f, ws=(f, l1, l2)):
            fr.configure(border_color=BORDER_HOVER, fg_color=CARD_HOVER)
            for x_ in ws[1:]:
                x_.configure(fg_color=CARD_HOVER)

        def leave(_e=None, fr=f, ws=(f, l1, l2), b=b):
            try:
                px_, py_ = fr.winfo_pointerxy()
                if fr.winfo_containing(px_, py_) in (fr, *ws, b) or str(fr.winfo_containing(px_, py_)).startswith(str(fr)):
                    return
            except Exception:
                pass
            fr.configure(border_color=ACCENT if fr._mvg_cur else BORDER, fg_color=CARD_BG)
            for x_ in ws[1:]:
                x_.configure(fg_color=CARD_BG)

        for wdg in (f, b, l1, l2):
            wdg.bind("<Button-1>", lambda e, c=code: pick(c))
            wdg.bind("<Enter>", enter)
            wdg.bind("<Leave>", leave)
            try:
                wdg.configure(cursor="hand2")
            except Exception:
                pass
        l1.configure(fg_color=CARD_BG)
        l2.configure(fg_color=CARD_BG)



def _first_start_language_screen():
    """Erster Start: großes Fenster mit Splash-Hintergrund und drei Sprachkarten."""
    root = _boot_root()
    w, h = getattr(root, "_mvg_size", (WIN_W, WIN_H))
    root.resizable(False, False)
    bg = _language_background(w, h)
    bg_img = ctk.CTkImage(light_image=bg, dark_image=bg, size=(w, h))
    ctk.CTkLabel(root, image=bg_img, text="").place(x=0, y=0, relwidth=1, relheight=1)

    def pick(code):
        global LANG
        save_settings({"lang": code})
        LANG = code
        root.destroy()

    _language_cards(root, bg, w, h, pick)

    def on_close():
        root.destroy()
        os._exit(0)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ===================== Klick-Hilfe: Bereiche registrieren =====================
HELP_REG: list = []          # [(widget, key)]
_click_help = {"active": False}


def H(widget, key: str):
    """Meldet ein Widget für die Klick-Hilfe an und gibt es zurück."""
    HELP_REG.append((widget, key))
    return widget


def _help_target_at(x_root: int, y_root: int, host=None):
    """Kleinster angemeldeter Bereich unter dem Mauszeiger -> Hilfe-Schlüssel.
    Mit `host` zählen nur Bereiche in diesem Fenster (Klick-Hilfe im Thumbnail-Editor / Pool)."""
    best, best_area = None, None
    alive = []
    for w, key in HELP_REG:
        try:
            if not w.winfo_exists():
                continue
        except Exception:
            continue
        alive.append((w, key))
        try:
            if not w.winfo_viewable():
                continue
            if host is not None and w.winfo_toplevel() is not host:
                continue
            x0, y0 = w.winfo_rootx(), w.winfo_rooty()
            ww, hh = w.winfo_width(), w.winfo_height()
            if ww <= 1 or hh <= 1:
                continue
            if x0 <= x_root < x0 + ww and y0 <= y_root < y0 + hh:
                area = ww * hh
                if best_area is None or area < best_area:
                    best, best_area = key, area
        except Exception:
            pass
    HELP_REG[:] = alive
    return best


# ===================== Drag & Drop von Dateien =====================
def _dnd_register_tree(w):
    """Registriert ein Widget samt aller Unter-Widgets als Ablagefläche für Dateien."""
    if not _HAS_DND or not getattr(app, "TkdndVersion", None):
        return
    stack = [w]
    while stack:
        c = stack.pop()
        if isinstance(c, (tk.Toplevel, ctk.CTkToplevel)) and c is not app:
            continue
        try:
            if not getattr(c, "_mvg_dnd", False):
                c.drop_target_register(DND_FILES)
                c.dnd_bind("<<Drop>>", _on_drop)
                c._mvg_dnd = True
        except Exception:
            pass
        try:
            stack.extend(c.winfo_children())
        except Exception:
            pass


def _on_drop(event):
    try:
        paths = list(app.tk.splitlist(event.data))
    except Exception:
        paths = [event.data]
    handle_dropped_paths(paths)
    return getattr(event, "action", None)


def _media_kind(p: Path) -> Optional[str]:
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def add_media(paths, quiet: bool = False) -> int:
    """Fügt Videos/Fotos der gemeinsamen Auswahl hinzu (Ordner: alle Dateien darin). Gibt die Anzahl neuer Einträge zurück."""
    have = {str(it["path"].resolve()).lower() for it in selection}
    added = 0
    files: list[Path] = []
    for p in paths:
        pp = Path(str(p).strip().strip("{}"))
        if pp.is_dir():
            files.extend(sorted(list_video_files(pp) + list_image_files(pp), key=lambda x: x.name.lower()))
        elif pp.is_file():
            files.append(pp)
    for f in files:
        kind = _media_kind(f)
        if kind not in ("video", "photo"):
            continue
        key = str(f.resolve()).lower()
        if key in have:
            continue
        have.add(key)
        selection.append({"kind": kind, "path": f})
        added += 1
    if added:
        queue_sel_thumbs()
    if not quiet:
        n_v = sum(1 for it in selection if it["kind"] == "video")
        n_p = len(selection) - n_v
        ui_status(tr("{n} hinzugefügt · Auswahl: {v} Clips, {p} Fotos").format(n=added, v=n_v, p=n_p) if added
                  else tr("Nichts Neues hinzugefügt (schon in der Auswahl oder kein Video/Foto)"))
    return added


def set_music(path: str):
    global music_file, music_dur
    music_file = path
    music_dur = None
    try:
        music_dur = ffprobe_duration(Path(path))
    except Exception:
        music_dur = None


def handle_dropped_paths(paths: list[str]):
    if render_busy:
        ui_status(tr("Während des Renderns nicht möglich"))
        return
    audio = [p for p in paths if Path(p).is_file() and Path(p).suffix.lower() in AUDIO_EXTS]
    media = [p for p in paths if p not in audio]
    if audio:
        set_music(audio[-1])
        ui_status(tr("Musik: ") + Path(audio[-1]).name)
    if media:
        add_media(media)
    if current_step in (1, 2, 3):
        refresh_page()
    elif current_step in (4, 5):
        ui_status(tr("Hinzugefügt – die Clipfolge wird beim nächsten „Video erstellen“ neu geplant."))
    update_stepbar()


# ===================== Vorschaubilder der Auswahl =====================
SEL_THUMB_PIL: dict[str, "Image.Image"] = {}
SEL_THUMB_IMG: dict[str, "ctk.CTkImage"] = {}
_sel_thumb_queue: list[Path] = []
_sel_thumb_running = False
_sel_tile_labels: dict[str, object] = {}


import queue as _queue
from concurrent.futures import ThreadPoolExecutor as _Pool

_ui_queue: "_queue.Queue" = _queue.Queue()
THUMB_DIR = BASE_DIR / "cache" / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)
_thumb_pool = _Pool(max_workers=4)          # Vorschaubilder parallel erzeugen


def ui_call(fn):
    """Aus einem Hintergrund-Thread: fn im Hauptthread ausführen (Tk ist nicht thread-sicher)."""
    _ui_queue.put(fn)


def _pump_ui():
    try:
        for _ in range(200):
            fn = _ui_queue.get_nowait()
            try:
                fn()
            except Exception:
                pass
    except _queue.Empty:
        pass
    try:
        app.after(40, _pump_ui)
    except Exception:
        pass


def _disk_thumb(p: Path, tag: str, w: int, h: int, make) -> Optional["Image.Image"]:
    """Vorschaubild aus cache/thumbs laden oder einmalig erzeugen und dort ablegen.
    Schlüssel: Datei (Pfad, Größe, Änderungszeit) + Stelle + Größe -> bei geänderter Datei neu."""
    try:
        st = p.stat()
        key = hashlib.md5(f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}|{tag}|{w}x{h}".encode("utf-8", "ignore")).hexdigest()
        fp = THUMB_DIR / f"{key}.jpg"
        if fp.exists():
            try:
                os.utime(fp, None)                  # „zuletzt benutzt“ für das automatische Aufräumen
            except Exception:
                pass
            return Image.open(fp).convert("RGB")
    except Exception:
        fp = None
    img = make()
    if img is not None and fp is not None:
        try:
            img.save(fp, "JPEG", quality=85)
        except Exception:
            pass
    return img


MANUAL_DIR = BASE_DIR / "manual"                 # die drei PDF-Anleitungen (aus der EXE hierher kopiert)
LOGO_DIR = BASE_DIR / "logo"                     # eigene Logos für den Thumbnail-Editor
MANUAL_FILES = ("MVG_Bedienungsanleitung.pdf", "MVG_Manual_EN.pdf", "MVG_Manual_ES.pdf")


def setup_user_folders() -> None:
    """Beim Start: Ordner manual/ mit den eingebauten PDF-Anleitungen und logo/ mit dem MVG-Logo anlegen.
    Vorhandene Dateien des Nutzers werden nie überschrieben – nur fehlende oder veraltete Anleitungen ersetzt."""
    try:
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        for name in MANUAL_FILES:
            inner = Path(resource_path(name))
            dst = MANUAL_DIR / name
            if inner.exists() and inner.resolve() != dst.resolve():
                if not dst.exists() or dst.stat().st_size != inner.stat().st_size:
                    shutil.copyfile(inner, dst)
    except Exception:
        pass
    try:
        LOGO_DIR.mkdir(parents=True, exist_ok=True)
        if not any(LOGO_DIR.iterdir()):
            ip = _icon_path()
            if ip is not None:
                ic = Image.open(ip)
                if ic.format == "ICO" and ic.info.get("sizes"):
                    ic.size = max(ic.info["sizes"])
                ic.convert("RGBA").save(LOGO_DIR / "MVG_Logo.png")
    except Exception:
        pass


CACHE_LIMITS = {"normalized": (60, 5 * 1024 ** 3), "thumbs": (30, 300 * 1024 ** 2)}   # (Tage unbenutzt, Höchstgröße)


def cleanup_cache() -> None:
    """Automatisches Aufräumen beim Start (im Hintergrund):
    - cache/segments: alte Abschnitte (nach einem Neustart ohnehin nicht mehr nutzbar)
    - cache/normalized: länger als 60 Tage unbenutzt, und höchstens 5 GB (am längsten Unbenutzte zuerst)
    - cache/thumbs: länger als 30 Tage unbenutzt, und höchstens 300 MB"""
    import time as _t
    for f in SEGMENTS_DIR.glob("*.mp4"):
        try:
            f.unlink()
        except Exception:
            pass
    for f in CACHE_DIR.glob("*.part.mp4"):              # Reste eines abgebrochenen Vorbereitens
        try:
            f.unlink()
        except Exception:
            pass
    now = _t.time()
    for folder, (days, limit) in ((CACHE_DIR, CACHE_LIMITS["normalized"]), (THUMB_DIR, CACHE_LIMITS["thumbs"])):
        try:
            files = []
            for f in folder.glob("*"):
                if f.is_file():
                    st = f.stat()
                    files.append([f, st.st_mtime, st.st_size])
            keep = []
            for f, mt, sz in files:
                if now - mt > days * 86400:
                    try:
                        f.unlink()
                        continue
                    except Exception:
                        pass
                keep.append((f, mt, sz))
            total = sum(x[2] for x in keep)
            for f, mt, sz in sorted(keep, key=lambda x: x[1]):      # älteste zuerst
                if total <= limit:
                    break
                try:
                    f.unlink()
                    total -= sz
                except Exception:
                    pass
        except Exception:
            pass


def clear_segments() -> None:
    for f in SEGMENTS_DIR.glob("*.mp4"):
        try:
            f.unlink()
        except Exception:
            pass


def _make_thumb_pil(p: Path, w: int, h: int) -> Optional["Image.Image"]:
    return _disk_thumb(Path(p), "t0.5", w, h, lambda: _make_thumb_pil_raw(Path(p), w, h))


def _make_thumb_pil_raw(p: Path, w: int, h: int) -> Optional["Image.Image"]:
    import io
    from PIL import ImageOps
    try:
        if p.suffix.lower() in IMAGE_EXTS:
            img = Image.open(p)
            img = ImageOps.exif_transpose(img).convert("RGB")
            return ImageOps.fit(img, (w, h))
        for ss in ("0.5", "0"):
            cmd = [ffmpeg_cmd(), "-y", "-hide_banner", "-loglevel", "error", "-ss", ss, "-i", str(p),
                   "-frames:v", "1",
                   "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                   "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
            try:
                data = check_output_hidden(cmd)
            except Exception:
                data = b""
            if data:
                return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        pass
    return None


def sel_thumb_image(p: Path) -> Optional["ctk.CTkImage"]:
    k = str(p)
    if k in SEL_THUMB_IMG:
        return SEL_THUMB_IMG[k]
    pil = SEL_THUMB_PIL.get(k)
    if pil is None:
        return None
    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(SEL_THUMB_W, SEL_THUMB_H))
    SEL_THUMB_IMG[k] = img
    return img


def queue_sel_thumbs():
    """Lädt fehlende Vorschaubilder der Auswahl nacheinander im Hintergrund."""
    global _sel_thumb_running
    for it in selection:
        k = str(it["path"])
        if k not in SEL_THUMB_PIL and it["path"] not in _sel_thumb_queue:
            _sel_thumb_queue.append(it["path"])
    if _sel_thumb_running or not _sel_thumb_queue:
        return
    _sel_thumb_running = True

    def worker():
        global _sel_thumb_running
        while _sel_thumb_queue:
            p = _sel_thumb_queue.pop(0)
            pil = _make_thumb_pil(p, SEL_THUMB_W, SEL_THUMB_H)
            if pil is not None:
                SEL_THUMB_PIL[str(p)] = pil
                ui_call(lambda pp=p: _sel_thumb_ready(pp))
        _sel_thumb_running = False

    threading.Thread(target=worker, daemon=True).start()


def _sel_thumb_ready(p: Path):
    lbl = _sel_tile_labels.get(str(p))
    if lbl is None:
        return
    try:
        if lbl.winfo_exists():
            lbl.configure(image=sel_thumb_image(p), text="")
    except Exception:
        pass


# ===================== Grundgerüst =====================
shell: dict = {}
status_var = None


def ui_status(text: str):
    try:
        status_var.set(text)
    except Exception:
        pass


def ui_progress(val: float):
    bar = shell.get("render_bar")
    try:
        if bar is not None and bar.winfo_exists():
            bar.set(clamp(float(val), 0.0, 1.0))
    except Exception:
        pass


def btn_primary(parent, text, command, width=180, height=40, **kw):
    return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                         fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                         font=F(13, True), corner_radius=10, **kw)


def btn_secondary(parent, text, command, width=150, height=36, **kw):
    kw.setdefault("text_color", C_TEXT)
    return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                         fg_color=C_SOFT, hover_color=C_SOFT_HOVER, border_width=1, border_color=C_BORDER,
                         font=F(12, True), corner_radius=10, **kw)


def btn_link(parent, text, command, **kw):
    return ctk.CTkButton(parent, text=text, command=command, width=10, height=24,
                         fg_color="transparent", hover_color=C_SOFT_HOVER, text_color=ACCENT,
                         font=F(12), corner_radius=6, **kw)


def help_button(parent, host):
    """„?“ für Unterfenster: startet die Klick-Hilfe in genau diesem Fenster."""
    b = ctk.CTkButton(parent, text="?", width=36, height=34, font=F(15, True), fg_color=C_SOFT,
                      hover_color=C_SOFT_HOVER, text_color=C_TEXT, border_width=1, border_color=C_BORDER,
                      corner_radius=10, command=lambda: start_click_help(host))
    H(b, "qhelp_win")
    return b


def card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=14, border_width=1, border_color=C_BORDER, **kw)


def heading(parent, title: str, sub: str):
    box = ctk.CTkFrame(parent, fg_color="transparent")
    box.pack(fill="x", padx=4, pady=(4, 12))
    ctk.CTkLabel(box, text=title, font=F(24, True), text_color=C_TEXT, anchor="w").pack(fill="x")
    ctk.CTkLabel(box, text=sub, font=F(13), text_color=C_MUTED, anchor="w", justify="left",
                 wraplength=980).pack(fill="x", pady=(2, 0))
    return box


def build_shell():
    """Kopfzeile, Schrittleiste, Inhaltsbereich und Fußleiste (ohne Seiteninhalt)."""
    global status_var
    for w in app.winfo_children():
        if isinstance(w, (tk.Toplevel, ctk.CTkToplevel)):
            continue
        w.destroy()
    HELP_REG.clear()
    shell.clear()
    app.configure(fg_color=C_BG)
    if status_var is None:
        status_var = ctk.StringVar(value="")

    # ── Kopfzeile
    head = ctk.CTkFrame(app, fg_color=C_CARD, corner_radius=0, height=64)
    head.pack(fill="x")
    H(head, "header")
    head.pack_propagate(False)
    logo = _logo_widget(head)
    logo.pack(side="left", padx=(24, 12))
    tbox = ctk.CTkFrame(head, fg_color="transparent")
    tbox.pack(side="left")
    ctk.CTkLabel(tbox, text=tr("Musik Video Generator"), font=F(18, True), text_color=C_TEXT,
                 anchor="w").pack(fill="x")
    ctk.CTkLabel(tbox, text=APP_SLOGAN, font=F(12),                # Version steht in der Titelleiste
                 text_color=("#2f78b7", "#7fb3de"), anchor="w").pack(fill="x")
    gear = ctk.CTkButton(head, text="⚙", width=40, height=36, font=F(18), fg_color=C_SOFT,
                         hover_color=C_SOFT_HOVER, text_color=C_TEXT, border_width=1, border_color=C_BORDER,
                         corner_radius=10)
    gear.configure(command=lambda: show_settings_menu(gear))
    gear.pack(side="right", padx=(6, 24))
    H(gear, "help")
    qbtn = ctk.CTkButton(head, text="?", width=40, height=36, font=F(16, True), fg_color=C_SOFT,
                         hover_color=C_SOFT_HOVER, text_color=C_TEXT, border_width=1, border_color=C_BORDER,
                         corner_radius=10, command=lambda: start_click_help())
    qbtn.pack(side="right")
    H(qbtn, "qhelp")
    shell["head"], shell["qbtn"], shell["update_btn"] = head, qbtn, None
    _place_update_button()
    shell["head_sep"] = ctk.CTkFrame(app, fg_color=C_BORDER, height=1, corner_radius=0)
    shell["head_sep"].pack(fill="x")

    # ── Schrittleiste
    bar = card(app, height=58)
    bar.pack(fill="x", padx=24, pady=(14, 0))
    bar.pack_propagate(False)
    H(bar, "steps")
    shell["steps"] = []
    for i, name in enumerate(("Start", "Bildmaterial", "Extras", "Schnitt", "Fertig"), start=1):
        bar.grid_columnconfigure(i - 1, weight=1, uniform="steps")
        cell = ctk.CTkFrame(bar, fg_color="transparent")
        cell.grid(row=0, column=i - 1, sticky="w", padx=(24, 0), pady=14)
        dot = ctk.CTkLabel(cell, text=str(i), width=28, height=28, corner_radius=14, fg_color=C_TODO,
                           text_color="white", font=F(12, True))
        dot.pack(side="left")
        lbl = ctk.CTkLabel(cell, text=f"{i}  {tr(name)}", font=F(13), text_color=C_MUTED)
        lbl.pack(side="left", padx=(10, 0))
        for w in (cell, dot, lbl):
            w.bind("<Button-1>", lambda e, s=i: _step_clicked(s))
        shell["steps"].append((cell, dot, lbl))

    # ── Fußleiste
    foot_wrap = ctk.CTkFrame(app, fg_color=C_CARD, corner_radius=0)
    foot_wrap.pack(side="bottom", fill="x")
    ctk.CTkFrame(foot_wrap, fg_color=C_BORDER, height=1, corner_radius=0).pack(fill="x")
    foot = ctk.CTkFrame(foot_wrap, fg_color="transparent", height=62)
    foot.pack(fill="x", padx=24)
    foot.pack_propagate(False)
    shell["foot_left"] = ctk.CTkFrame(foot, fg_color="transparent")
    shell["foot_left"].pack(side="left", pady=12)
    shell["foot_right"] = ctk.CTkFrame(foot, fg_color="transparent")
    shell["foot_right"].pack(side="right", pady=12)
    st = ctk.CTkLabel(foot, textvariable=status_var, font=F(12), text_color=C_MUTED, anchor="w")
    st.pack(side="left", fill="x", expand=True, padx=18)
    shell["foot_status"] = st
    H(st, "status")

    # ── Inhalt
    content = ctk.CTkFrame(app, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=(14, 12))
    shell["content"] = content


_LOGO_IMG = None


def _find_asset(name: str) -> Optional[Path]:
    """Sucht eine Datei im EXE-Bundle und im Programmordner."""
    for base in (Path(resource_path("")), BASE_DIR):
        p = base / name
        if p.exists():
            return p
    return None


_ICON_PATH: Optional[Path] = None


def _icon_path() -> Optional[Path]:
    """icon.ico aus Bundle/Programmordner, sonst das eingebaute Icon (einmalig in cache/ abgelegt)."""
    global _ICON_PATH
    if _ICON_PATH is not None:
        return _ICON_PATH
    p = _find_asset("icon.ico")
    if p is None:
        try:
            import base64
            p = BASE_DIR / "cache" / "mvg_icon.ico"
            data = base64.b64decode("".join(_ICON_ICO_B64))
            if not p.exists() or p.read_bytes() != data:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        except Exception:
            p = None
    _ICON_PATH = p
    return p


def _apply_icon(win):
    """Setzt das MVG-Icon in die Titelzeile (CustomTkinter würde sonst sein eigenes Icon setzen)."""
    p = _icon_path()
    if p is None or os.name != "nt":
        return
    try:
        win.iconbitmap(str(p))
    except Exception:
        pass


# Auch alle Unterfenster (Pool-Auswahl, Hilfe, Sprache …) bekommen das MVG-Icon
_orig_toplevel_init = ctk.CTkToplevel.__init__


def _toplevel_init_with_icon(self, *a, **k):
    _orig_toplevel_init(self, *a, **k)
    _apply_icon(self)


ctk.CTkToplevel.__init__ = _toplevel_init_with_icon


def _logo_widget(parent):
    """Original-MVG-Logo, falls als Bilddatei vorhanden; sonst ein „M“ in MVG-Blau."""
    global _LOGO_IMG
    if _LOGO_IMG is None:
        # Logo im Kopf = das Programm-Icon (icon.ico); größte enthaltene Auflösung wird genommen
        for p in (_find_asset("logo.png"), _icon_path()):
            if p is None:
                continue
            try:
                pil = Image.open(p)
                if pil.format == "ICO" and pil.info.get("sizes"):
                    pil.size = max(pil.info["sizes"])
                pil = pil.convert("RGBA")
                _LOGO_IMG = ctk.CTkImage(light_image=pil, dark_image=pil, size=(40, 40))
                break
            except Exception:
                _LOGO_IMG = None
    if _LOGO_IMG is not None:
        return ctk.CTkLabel(parent, image=_LOGO_IMG, text="")
    return ctk.CTkLabel(parent, text="M", width=40, height=40, corner_radius=10, fg_color=ACCENT,
                        text_color="white", font=F(20, True))


def set_footer(left: list = (), right: list = ()):
    """Ersetzt die Buttons der Fußleiste. Elemente: (Text, Befehl, Art, aktiv[, Hilfe-Schlüssel])."""
    for side, items in (("foot_left", left), ("foot_right", right)):
        fr = shell[side]
        for w in fr.winfo_children():
            # nicht sofort zerstören: der Aufruf kommt oft aus dem Klick genau dieses Buttons
            # (CTk läuft danach noch 100 ms Klick-Animation auf ihm)
            w.pack_forget()
            w.after(300, w.destroy)
        for item in items:
            text, cmd, kind, enabled = item[:4]
            key = item[4] if len(item) > 4 else None
            if kind == "primary":
                b = btn_primary(fr, text, cmd, width=190, height=38)
            elif kind == "danger":
                b = btn_secondary(fr, text, cmd, width=150, height=38, text_color=C_DANGER_TX)
            else:
                b = btn_secondary(fr, text, cmd, width=150, height=38)
            if not enabled:
                b.configure(state="disabled")
                if kind == "primary":
                    b.configure(fg_color=C_TODO, text_color_disabled=("#f2f4f7", "#9aa4b0"))
            b.pack(side="left", padx=(8 if side == "foot_right" else 0, 0 if side == "foot_right" else 8))
            if key:
                H(b, key)
            b.after(60, lambda bb=b: _sync_hover(bb))


def _sync_hover(b):
    """Neu erzeugter Button direkt unter dem Mauszeiger: Windows meldet dann kein <Enter>,
    und CTk würde den ersten Klick ignorieren. Zustand hier nachziehen."""
    try:
        if not b.winfo_exists():
            return
        w = b.winfo_containing(*b.winfo_pointerxy())
        while w is not None:
            if w is b:
                b._on_enter()
                return
            w = w.master
    except Exception:
        pass


def show_status_in_footer(show: bool):
    st = shell.get("foot_status")
    if st is None:
        return
    if show:
        st.pack(side="left", fill="x", expand=True, padx=18)
    else:
        st.pack_forget()


# ===================== Navigation =====================
def can_reach(step: int) -> bool:
    if render_busy or plan_busy:
        return False
    if step <= 1:
        return True
    if step == 2:
        return bool(music_file)
    if step in (3, 4):
        return bool(music_file) and bool(selection)
    if step == 5:
        return plan_is_rendered()
    return False


def _step_clicked(step: int):
    if current_step == 0 or render_busy or step == current_step:
        return
    if can_reach(step):
        if step == 4:
            enter_step4()
        else:
            go(step)


def update_stepbar():
    if not shell.get("steps"):
        return
    cur = current_step
    for i, (cell, dot, lbl) in enumerate(shell["steps"], start=1):
        done = cur > i
        active = (cur == i)
        if active:
            dot.configure(fg_color=ACCENT, text=str(i))
            lbl.configure(text_color=C_TEXT, font=F(13, True))
        elif done:
            dot.configure(fg_color=ACCENT, text="✓")
            lbl.configure(text_color=C_MUTED, font=F(13))
        else:
            dot.configure(fg_color=C_TODO, text=str(i))
            lbl.configure(text_color=C_MUTED, font=F(13))
        reachable = current_step != 0 and i != cur and can_reach(i)
        try:
            for w in (cell, dot, lbl):
                w.configure(cursor="hand2" if reachable else "")
        except Exception:
            pass


def go(step: int):
    global current_step
    current_step = step
    if step in (4, 5):
        _sync_formats()
    ui_status("")                 # alte Meldungen gehören zur vorigen Seite
    refresh_page()


def refresh_page():
    content = shell["content"]
    for w in content.winfo_children():
        w.destroy()
    _sel_tile_labels.clear()
    show_status_in_footer(not (current_step == 4 and render_busy))
    builder = {0: build_welcome, 1: build_step1, 2: build_step2, 3: build_step3,
               4: build_step4, 5: build_step5}.get(current_step, build_step1)
    builder(content)
    # Seite als Ganzes für die Klick-Hilfe: greift überall dort, wo kein genauerer Bereich liegt
    HELP_REG[:] = [(w, k) for (w, k) in HELP_REG if w is not content]
    H(content, {0: "page_welcome", 1: "page_start", 2: "page_media", 3: "page_extras",
                4: "page_render", 5: "page_done"}.get(current_step, "page_start"))
    update_stepbar()
    _dnd_register_tree(app)


def rebuild_ui():
    """Komplettes Neuzeichnen (z. B. nach Sprachwechsel). Der Zustand bleibt erhalten."""
    ui_status("")
    build_shell()
    refresh_page()


# ===================== Willkommen (einmalig) =====================
def build_welcome(parent):
    heading(parent, tr("Willkommen beim Musik Video Generator {v}").format(v=APP_VERSION),
            tr("Aus deiner Musik und deinen Clips entsteht in fünf Schritten ein Video – die Schnitte liegen automatisch auf dem Beat."))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="both", expand=True)
    row.grid_columnconfigure((0, 1), weight=1, uniform="w")
    row.grid_rowconfigure(0, weight=1)

    c1 = card(row)
    c1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    ctk.CTkLabel(c1, text=tr("So geht's"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=24, pady=(20, 10))
    for n, (t, d) in enumerate((
            ("Start", "Musik wählen und Formate festlegen"),
            ("Bildmaterial", "Clips und Fotos auswählen – aus dem Pool, aus Ordnern oder einfach ins Fenster ziehen"),
            ("Extras", "Optional Pre-Intro, Intro, Outro, Titel und Schnittgeschwindigkeit"),
            ("Schnitt", "Clipfolge prüfen, Clips verschieben oder tauschen, Video erstellen"),
            ("Fertig", "Video ansehen, Thumbnail speichern, Clips ändern, hochladen")), start=1):
        r = ctk.CTkFrame(c1, fg_color="transparent")
        r.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(r, text=str(n), width=28, height=28, corner_radius=14, fg_color=ACCENT,
                     text_color="white", font=F(12, True)).pack(side="left", anchor="n")
        tb = ctk.CTkFrame(r, fg_color="transparent")
        tb.pack(side="left", fill="x", expand=True, padx=(12, 0))
        ctk.CTkLabel(tb, text=tr(t), font=F(13, True), text_color=C_TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(tb, text=tr(d), font=F(12), text_color=C_MUTED, anchor="w", justify="left",
                     wraplength=380).pack(fill="x")

    c2 = card(row)
    c2.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    ctk.CTkLabel(c2, text=tr("Dein Clip-Pool"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=24, pady=(20, 10))
    ctk.CTkLabel(c2, text=tr("Das Programm wird ohne Clips ausgeliefert. Lege Videos und Fotos, die du immer wieder verwendest, in den Pool-Ordner. In Schritt 2 wählst du daraus per Klick die passenden aus.\n\nMaterial, das nur zu einem Song passt, musst du nicht in den Pool legen: Ordner wählen oder Dateien einfach ins Fenster ziehen."),
                 font=F(13), text_color=C_TEXT, anchor="w", justify="left", wraplength=420).pack(fill="x", padx=24)
    n_pool = len(pool_files())                     # inklusive Unterordner (Kategorien)
    ctk.CTkLabel(c2, text=tr("Im Pool: {n} Dateien").format(n=n_pool), font=F(12, True),
                 text_color=ACCENT if n_pool else C_WARN_TX, anchor="w").pack(fill="x", padx=24, pady=(14, 6))
    ctk.CTkLabel(c2, text=str(POOL_DIR), font=F(11), text_color=C_MUTED, anchor="w", justify="left",
                 wraplength=420).pack(fill="x", padx=24)
    btn_secondary(c2, tr("Pool-Ordner öffnen"), lambda: open_path_portable(POOL_DIR), width=190).pack(anchor="w", padx=24, pady=(14, 20))

    def start():
        save_settings({"welcome_v4": True})
        go(1)

    set_footer(right=[(tr("Los geht's") + "  →", start, "primary", True, "start_welcome")])


# ===================== Schritt 1: Start =====================
FORMAT_DEFAULTS = {"normal": True, "shorts": False, "shorts58": True}
format_vars: dict = {}
speed_var = intro_var = outro_var = vorspann_var = title_var = None


def init_vars():
    global speed_var, intro_var, outro_var, vorspann_var, title_var, appearance_var
    appearance_var = tk.StringVar(value=_appearance)
    for k, v in FORMAT_DEFAULTS.items():
        format_vars[k] = ctk.BooleanVar(value=v)
    speed_var = ctk.StringVar(value="normal")
    intro_var = ctk.BooleanVar(value=True)
    outro_var = ctk.BooleanVar(value=True)
    vorspann_var = ctk.BooleanVar(value=False)
    title_var = ctk.BooleanVar(value=False)


def selected_formats() -> list[str]:
    return [k for k in FORMAT_ORDER if bool(format_vars[k].get())]


def loop_selected() -> bool:
    """Genau ein Video und keine Fotos ausgewählt -> der Clip läuft als Loop durch den ganzen Song."""
    kinds = [it["kind"] for it in selection]
    return kinds.count("video") == 1 and "photo" not in kinds


def speed_label() -> str:
    if loop_selected():
        return tr("Loop (ein Clip)")
    if not speed_var.get():
        return tr("Ganzer Clip")
    return tr(dict(SPEED_KEYS).get(speed_var.get(), "Normal"))


def _last_dir(kind: str) -> Optional[str]:
    """Zuletzt benutzter Ordner für Musik / Videos / Fotos (Dateiauswahl öffnet sich dort)."""
    d = (load_settings().get("last_dirs") or {}).get(kind)
    try:
        return d if d and Path(d).is_dir() else None
    except Exception:
        return None


def _remember_dir(kind: str, path) -> None:
    try:
        p = Path(path)
        d = p if p.is_dir() else p.parent
        dirs = dict(load_settings().get("last_dirs") or {})
        dirs[kind] = str(d)
        save_settings({"last_dirs": dirs})
    except Exception:
        pass


def choose_music():
    if render_busy:
        return
    f = filedialog.askopenfilename(title=tr("Musik auswählen"), initialdir=_last_dir("music"),
                                   filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg")])
    if f:
        _remember_dir("music", f)
        set_music(f)
        ui_status(tr("Musik: ") + Path(f).name)
        refresh_page()


def build_step1(parent):
    heading(parent, tr("Dein Song gibt den Takt vor."),
            tr("Wähle zuerst deine Musik und die Videoformate, die du haben möchtest."))
    c = card(parent)
    c.pack(fill="both", expand=True)

    ctk.CTkLabel(c, text=tr("1. Musikdatei"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=28, pady=(22, 0))
    ctk.CTkLabel(c, text=tr("MP3 oder WAV · auch per Ziehen ins Fenster"), font=F(12), text_color=C_MUTED, anchor="w").pack(fill="x", padx=28)
    zone = ctk.CTkFrame(c, fg_color=C_SOFT, corner_radius=12, border_width=1, border_color=C_BORDER)
    zone.pack(fill="x", padx=28, pady=(10, 0))
    H(zone, "music")
    ctk.CTkLabel(zone, text="♫", width=52, height=52, corner_radius=26, fg_color=C_ACTIVE,
                 text_color=ACCENT, font=F(24, True)).pack(side="left", padx=22, pady=18)
    tb = ctk.CTkFrame(zone, fg_color="transparent")
    tb.pack(side="left", fill="x", expand=True, pady=14)
    if music_file:
        ctk.CTkLabel(tb, text=Path(music_file).name, font=F(15, True), text_color=C_TEXT, anchor="w").pack(fill="x")
        info = tr("Länge: {t}").format(t=fmt_time(music_dur)) if music_dur else ""
        ctk.CTkLabel(tb, text=info + ("   ·   " if info else "") + str(Path(music_file).parent), font=F(12),
                     text_color=C_MUTED, anchor="w").pack(fill="x")
        b = btn_secondary(zone, tr("Andere Musik wählen"), choose_music, width=190)
    else:
        ctk.CTkLabel(tb, text=tr("Noch keine Datei ausgewählt"), font=F(15, True), text_color=C_TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(tb, text=tr("Ziehe deine Audiodatei hierher oder wähle sie aus."), font=F(12),
                     text_color=C_MUTED, anchor="w").pack(fill="x")
        b = btn_primary(zone, tr("Musikdatei wählen"), choose_music, width=190)
    b.pack(side="right", padx=22)
    H(b, "music")

    ctk.CTkLabel(c, text=tr("2. Ausgabeformate"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=28, pady=(24, 0))
    ctk.CTkLabel(c, text=tr("Mehrfachauswahl möglich · jedes Format wird als eigene Datei erstellt."), font=F(12),
                 text_color=C_MUTED, anchor="w").pack(fill="x", padx=28)
    row = ctk.CTkFrame(c, fg_color="transparent")
    row.pack(fill="x", padx=28, pady=(10, 0))
    row.grid_columnconfigure((0, 1, 2), weight=1, uniform="fmt")
    specs = (("normal", "Normal  16:9", "Querformat · volle Länge · mit Extras", "fmt_normal"),
             ("shorts", "Normal  9:16", "Hochformat · volle Länge · mit Extras", "fmt_shorts"),
             ("shorts58", "Short  9:16 · 58s", "Stärkster Abschnitt · ohne Extras", "fmt_short58"))
    for col, (key, title, sub, hkey) in enumerate(specs):
        on = bool(format_vars[key].get())
        f = ctk.CTkFrame(row, fg_color=C_ACTIVE if on else C_SOFT, corner_radius=12, border_width=1,
                         border_color=ACCENT if on else C_BORDER)
        f.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 2 else 6))
        H(f, hkey)
        cb = ctk.CTkCheckBox(f, text=tr(title), variable=format_vars[key], font=F(13, True), text_color=C_TEXT,
                             fg_color=ACCENT, hover_color=ACCENT_HOVER, checkbox_width=20, checkbox_height=20,
                             command=lambda k=key: _toggle_format(k, from_checkbox=True))
        cb.pack(anchor="w", padx=16, pady=(14, 2))
        sl = ctk.CTkLabel(f, text=tr(sub), font=F(11), text_color=C_MUTED, anchor="w")
        sl.pack(fill="x", padx=(46, 12), pady=(0, 14))
        for w in (f, sl):
            w.bind("<Button-1>", lambda e, k=key: _toggle_format(k))
            w.configure(cursor="hand2")

    if music_file:
        names = [get_output_path_from_music(music_file, k).name for k in selected_formats()]
        txt = tr("Wird gespeichert als: ") + "  +  ".join(names) + tr("  (Ordner output/)")
    else:
        txt = tr("Der Dateiname des Videos entsteht aus dem Namen der Musikdatei.")
    ctk.CTkLabel(c, text=txt, font=F(12), text_color=C_MUTED, anchor="w", justify="left",
                 wraplength=960).pack(fill="x", padx=28, pady=(14, 20))

    set_footer(right=[(tr("Weiter") + "  →", lambda: go(2), "primary", bool(music_file), "next")])
    if not music_file:
        ui_status(tr("Bitte zuerst eine Musikdatei wählen."))


def _toggle_format(key: str, from_checkbox: bool = False):
    if not from_checkbox:
        format_vars[key].set(not bool(format_vars[key].get()))
    if not selected_formats():
        format_vars[key].set(True)
        ui_status(tr("Mindestens ein Format muss aktiv sein"))
    else:
        ui_status(tr("Formate: ") + " + ".join(FORMAT_LABELS[k] for k in selected_formats()))
    refresh_page()


# ===================== Schritt 2: Bildmaterial =====================
def _is_in_pool(p: Path) -> bool:
    """Liegt die Datei im Pool-Ordner oder in einem seiner Unterordner (eine Ebene)?"""
    try:
        par = p.resolve().parent
        root = POOL_DIR.resolve()
        return par == root or par.parent == root
    except Exception:
        return False


def _natural_key(p: Path):
    """clip_2 vor clip_10 (natürliche Sortierung)."""
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name.lower())]


def _pool_dir_files(d: Path) -> list[Path]:
    try:
        return sorted(list_video_files(d) + list_image_files(d), key=_natural_key)
    except Exception:
        return []


def pool_categories() -> list[tuple[str, list[Path]]]:
    """Optionale Kategorien: Unterordner im Pool (eine Ebene), die Videos oder Fotos enthalten.
    Ohne Unterordner ist die Liste leer – dann gibt es im Auswahlfenster keine Reiter."""
    cats = []
    try:
        subs = sorted((d for d in POOL_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")),
                      key=_natural_key)
    except Exception:
        subs = []
    for d in subs:
        f = _pool_dir_files(d)
        if f:
            cats.append((d.name, f))
    return cats


def pool_files() -> list[Path]:
    """Alle Dateien im Pool: direkt im Ordner clips/ und in seinen Unterordnern."""
    out = _pool_dir_files(POOL_DIR)
    for _name, f in pool_categories():
        out += f
    return out


def choose_media_folder(kind: str):
    d = filedialog.askdirectory(title=tr("Ordner mit Videos wählen") if kind == "video" else tr("Ordner mit Fotos wählen"),
                                initialdir=_last_dir(kind))
    if not d:
        return
    _remember_dir(kind, Path(d).parent if Path(d).parent.is_dir() else d)   # nächstes Mal: eine Ebene höher
    files = list_video_files(Path(d)) if kind == "video" else list_image_files(Path(d))
    if not files:
        ui_status(tr("In diesem Ordner wurden keine Videos gefunden.") if kind == "video"
                  else tr("In diesem Ordner wurden keine Fotos gefunden."))
        return
    add_media(files)
    refresh_page()


def choose_media_files(kind: str):
    if kind == "video":
        fs = filedialog.askopenfilenames(title=tr("Videos wählen"), initialdir=_last_dir("video"),
                                         filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm")])
    else:
        fs = filedialog.askopenfilenames(title=tr("Fotos wählen"), initialdir=_last_dir("photo"),
                                         filetypes=[("Foto", "*.jpg *.jpeg *.png *.webp")])
    if fs:
        _remember_dir(kind, fs[0])
        add_media(list(fs))
        refresh_page()


def remove_from_selection(p: Path):
    selection[:] = [it for it in selection if it["path"] != p]
    _refresh_step2_dynamic()


def clear_selection():
    if not selection:
        return
    if not messagebox.askyesno(tr("Auswahl leeren"), tr("Alle {n} Einträge aus der Auswahl entfernen?\n(Die Dateien selbst bleiben erhalten.)").format(n=len(selection))):
        return
    selection.clear()
    _refresh_step2_dynamic()


def build_step2(parent):
    heading(parent, tr("Woher sollen die Bilder kommen?"),
            tr("Kombiniere beliebig viele Quellen. Du kannst Dateien und Ordner auch einfach ins Fenster ziehen."))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    row.grid_columnconfigure((0, 1, 2), weight=1, uniform="src")
    shell["src_cards"] = {}

    def src_card(col, key, title, desc, hkey, buttons):
        f = card(row)
        f.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 2 else 6))
        H(f, hkey)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 0))
        dot = ctk.CTkLabel(top, text="+", width=34, height=34, corner_radius=17, fg_color=C_SOFT,
                           text_color=C_MUTED, font=F(16, True))
        dot.pack(side="left")
        ctk.CTkLabel(top, text=title, font=F(15, True), text_color=C_TEXT, anchor="w").pack(side="left", padx=(12, 0))
        ctk.CTkLabel(f, text=desc, font=F(12), text_color=C_MUTED, anchor="w", justify="left",
                     wraplength=280).pack(fill="x", padx=20, pady=(8, 0))
        info = ctk.CTkLabel(f, text="", font=F(12, True), text_color=ACCENT, anchor="w")
        info.pack(fill="x", padx=20, pady=(8, 0))
        br = ctk.CTkFrame(f, fg_color="transparent")
        br.pack(fill="x", padx=20, pady=(10, 16))
        for n, (txt, cmd, primary, bkey) in enumerate(buttons):
            b = (btn_primary(br, txt, cmd, width=130, height=34) if primary
                 else btn_secondary(br, txt, cmd, width=120, height=34))
            b.pack(side="left", padx=(0 if n == 0 else 8, 0))
            H(b, bkey)
        shell["src_cards"][key] = (f, dot, info)

    src_card(0, "pool", tr("Clip-Pool"), tr("Deine Clips und Fotos für jedes Video (Ordner clips/)"), "src_pool",
             [(tr("Clips auswählen"), open_pool_selector, True, "src_pool_btn"),
              (tr("Ordner öffnen"), lambda: open_path_portable(POOL_DIR), False, "src_pool_open")])
    src_card(1, "video", tr("Eigene Videos"), tr("Clips aus einem beliebigen Ordner auf deinem Computer"), "src_videos",
             [(tr("Ordner wählen"), lambda: choose_media_folder("video"), False, "src_folder"),
              (tr("Dateien wählen"), lambda: choose_media_files("video"), False, "src_files")])
    src_card(2, "photo", tr("Eigene Fotos"), tr("Fotos als stehende Bilder zwischen den Clips"), "src_photos",
             [(tr("Ordner wählen"), lambda: choose_media_folder("photo"), False, "src_folder"),
              (tr("Dateien wählen"), lambda: choose_media_files("photo"), False, "src_files")])

    # ── Gemeinsame Auswahl
    sel = card(parent)
    sel.pack(fill="both", expand=True, pady=(12, 0))
    H(sel, "selection")
    hd = ctk.CTkFrame(sel, fg_color="transparent")
    hd.pack(fill="x", padx=20, pady=(14, 6))
    ctk.CTkLabel(hd, text=tr("Deine Auswahl"), font=F(15, True), text_color=C_TEXT).pack(side="left")
    cnt = ctk.CTkLabel(hd, text="", font=F(12), text_color=C_MUTED)
    cnt.pack(side="left", padx=12)
    lh = ctk.CTkLabel(hd, text="", font=F(12, True), text_color=ACCENT)
    lh.pack(side="left", padx=(4, 0))
    shell["sel_loop_hint"] = lh
    H(lh, "loop_mode")
    clr = btn_secondary(hd, tr("Alle entfernen"), clear_selection, width=130, height=30, text_color=C_DANGER_TX)
    clr.pack(side="right")
    H(clr, "sel_clear")
    grid_holder = ctk.CTkScrollableFrame(sel, fg_color="transparent")
    grid_holder.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    shell["sel_count"] = cnt
    shell["sel_clear"] = clr
    shell["sel_grid"] = grid_holder
    _refresh_step2_dynamic(first=True)


def _refresh_step2_dynamic(first: bool = False):
    """Aktualisiert Karten-Infos und das Raster der Auswahl, ohne die ganze Seite neu aufzubauen."""
    if current_step != 2 or "sel_grid" not in shell:
        return
    try:
        if not shell["sel_grid"].winfo_exists():
            return
    except Exception:
        return
    n_v = sum(1 for it in selection if it["kind"] == "video")
    n_p = len(selection) - n_v
    pool_sel = sum(1 for it in selection if _is_in_pool(it["path"]))
    n_pool_files = len(pool_files())
    ext_v = sum(1 for it in selection if it["kind"] == "video" and not _is_in_pool(it["path"]))
    ext_p = sum(1 for it in selection if it["kind"] == "photo" and not _is_in_pool(it["path"]))
    infos = {
        "pool": (pool_sel, tr("{n} im Pool · {s} ausgewählt").format(n=n_pool_files, s=pool_sel) if n_pool_files
                 else tr("Pool-Ordner ist noch leer")),
        "video": (ext_v, tr("{n} Videos ausgewählt").format(n=ext_v) if ext_v else ""),
        "photo": (ext_p, tr("{n} Fotos ausgewählt").format(n=ext_p) if ext_p else ""),
    }
    for key, (f, dot, info) in shell.get("src_cards", {}).items():
        n, txt = infos[key]
        on = n > 0
        f.configure(fg_color=C_ACTIVE if on else C_CARD, border_color=ACCENT if on else C_BORDER)
        dot.configure(text="✓" if on else "+", fg_color=ACCENT if on else C_SOFT,
                      text_color="white" if on else C_MUTED)
        warn = (key == "pool" and not n_pool_files)
        info.configure(text=txt, text_color=C_WARN_TX if warn else ACCENT)

    shell["sel_count"].configure(text=tr("{v} Clips · {p} Fotos").format(v=n_v, p=n_p))
    try:
        shell["sel_loop_hint"].configure(text=("↻ " + tr("Nur ein Clip – er läuft als Loop durch den ganzen Song.")) if loop_selected() else "")
    except Exception:
        pass
    shell["sel_clear"].configure(state="normal" if selection else "disabled")

    grid = shell["sel_grid"]
    for w in grid.winfo_children():
        w.destroy()
    _sel_tile_labels.clear()
    if not selection:
        empty = ctk.CTkFrame(grid, fg_color=C_SOFT, corner_radius=12, border_width=1, border_color=C_BORDER, height=150)
        empty.pack(fill="both", expand=True, padx=8, pady=8)
        empty.pack_propagate(False)
        ctk.CTkLabel(empty, text="⇣", font=F(28, True), text_color=C_MUTED).pack(pady=(26, 0))
        ctk.CTkLabel(empty, text=tr("Noch nichts ausgewählt"), font=F(14, True), text_color=C_TEXT).pack()
        ctk.CTkLabel(empty, text=tr("Wähle oben eine Quelle – oder ziehe Videos, Fotos oder ganze Ordner hierher."),
                     font=F(12), text_color=C_MUTED).pack(pady=(2, 0))
    else:
        try:
            avail = max(600, shell["content"].winfo_width() - 60)
        except Exception:
            avail = 1000
        cols = max(4, avail // (SEL_THUMB_W + 18))
        for i, it in enumerate(selection):
            p = it["path"]
            tile = ctk.CTkFrame(grid, fg_color=C_SOFT, corner_radius=10, border_width=1, border_color=C_BORDER)
            tile.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="n")
            img = sel_thumb_image(p)
            lbl = ctk.CTkLabel(tile, text="" if img else ("🎬" if it["kind"] == "video" else "🖼"), image=img,
                               width=SEL_THUMB_W, height=SEL_THUMB_H, fg_color=C_BORDER, corner_radius=6,
                               font=F(20))
            lbl.pack(padx=4, pady=(4, 2))
            _sel_tile_labels[str(p)] = lbl
            name = p.stem if len(p.stem) <= 18 else p.stem[:17] + "…"
            badge = ("◆ " if _is_in_pool(p) else "") + ("🖼 " if it["kind"] == "photo" else "") + name
            ctk.CTkLabel(tile, text=badge, font=F(10), text_color=C_MUTED, width=SEL_THUMB_W, anchor="w").pack(padx=6, pady=(0, 4))
            x = ctk.CTkButton(tile, text="✕", width=22, height=22, corner_radius=11, font=F(11, True),
                              fg_color=("#ffffff", "#1b1d21"), hover_color=("#fde3e1", "#4a2523"),
                              text_color=C_DANGER_TX, command=lambda pp=p: remove_from_selection(pp))
            x.place(relx=1.0, x=-8, y=8, anchor="ne")
            H(x, "sel_remove")
            H(tile, "selection")
        queue_sel_thumbs()

    set_footer(left=[("←  " + tr("Zurück"), lambda: go(1), "secondary", True, "back")],
               right=[(tr("Weiter") + "  →", lambda: go(3), "primary", bool(selection), "next")])
    update_stepbar()
    if not first:
        _dnd_register_tree(grid)


# ── Pool-Auswahl (Vorschaubilder anklicken)
POOL_THUMB_W, POOL_THUMB_H = 160, 90
POOL_THUMB_CACHE: dict[str, "ctk.CTkImage"] = {}


def open_pool_selector():
    files = pool_files()
    if not files:
        messagebox.showinfo(tr("Clip-Pool"),
                            tr("Der Pool-Ordner ist noch leer.\n\nLege Videos und Fotos, die du immer wieder verwendest, in diesen Ordner:\n{path}\n\nDanach kannst du sie hier per Klick auswählen.").format(path=POOL_DIR))
        open_path_portable(POOL_DIR)
        return

    win = ctk.CTkToplevel(app)
    win.title(tr("Clips aus dem Pool auswählen"))
    w_, h_ = 830, 600
    try:
        app.update_idletasks()
        win.geometry(f"{w_}x{h_}+{app.winfo_x() + (app.winfo_width() - w_) // 2}+{app.winfo_y() + 40}")
    except Exception:
        win.geometry(f"{w_}x{h_}")
    win.transient(app)
    win.grab_set()
    win.configure(fg_color=C_BG)

    chosen_now = {str(it["path"].resolve()).lower() for it in selection if _is_in_pool(it["path"])}
    sel = {p: ctk.BooleanVar(value=str(p.resolve()).lower() in chosen_now) for p in files}
    btns: dict = {}
    alive = [True]

    hd = ctk.CTkFrame(win, fg_color="transparent")
    hd.pack(fill="x", padx=18, pady=(14, 6))
    ctk.CTkLabel(hd, text=tr("Klicke Clips an, die ins Video sollen"), font=F(15, True), text_color=C_TEXT).pack(side="left")
    help_button(hd, win).pack(side="right")
    cnt = ctk.CTkLabel(hd, text="", font=F(12), text_color=C_MUTED)
    cnt.pack(side="right", padx=(0, 12))
    H(cnt, "pw_count")

    # Optionale Reiter: nur wenn es Unterordner gibt (sonst sieht das Fenster aus wie immer)
    cats = pool_categories()
    tab_files: dict = {}
    tab_names: list = []
    tab_btns: dict = {}
    current_tab = [None]
    if cats:
        t_all = tr("Alle")
        tab_names.append(t_all)
        tab_files[t_all] = files
        for name, f in cats:
            tab_names.append(name)
            tab_files[name] = f
        top_files = _pool_dir_files(POOL_DIR)
        if top_files:
            t_more = tr("Weitere")
            while t_more in tab_files:
                t_more += " "
            tab_names.append(t_more)
            tab_files[t_more] = top_files
        last = load_settings().get("pool_tab")
        current_tab[0] = last if last in tab_files else t_all
        # Reiter als Leiste; bei vielen Unterordnern einfach in weitere Zeilen umbrechen
        tabs_box = ctk.CTkFrame(win, fg_color="transparent")
        tabs_box.pack(fill="x", padx=18, pady=(0, 8))
        H(tabs_box, "pool_tabs")
        fnt = F(12, True)
        avail = w_ - 36
        row_fr, used = None, avail + 1
        for name in tab_names:
            bw = max(70, fnt.measure(name + "  · 999") + 24)
            if used + bw > avail:
                row_fr = ctk.CTkFrame(tabs_box, fg_color="transparent")
                row_fr.pack(fill="x", pady=(0, 6))
                used = 0
            b_ = ctk.CTkButton(row_fr, text=name, width=bw, height=30, corner_radius=15, font=fnt,
                               fg_color=C_SOFT, hover_color=C_SOFT_HOVER, text_color=C_TEXT,
                               border_width=1, border_color=C_BORDER,
                               command=lambda v=name: show_tab(v))
            b_.pack(side="left", padx=(0, 6))
            H(b_, "pool_tabs")
            tab_btns[name] = b_
            used += bw

    scroll = ctk.CTkScrollableFrame(win, fg_color=C_CARD, corner_radius=12)
    scroll.pack(fill="both", expand=True, padx=18, pady=(0, 8))
    H(scroll, "pw_grid")

    def upd():
        cnt.configure(text=tr("{n} / {total} ausgewählt").format(n=sum(1 for v in sel.values() if v.get()), total=len(files)))
        for name, b_ in tab_btns.items():          # Reiter: gewählte Clips je Kategorie, aktiver Reiter blau
            k = sum(1 for p in tab_files[name] if sel[p].get())
            act = name == current_tab[0]
            try:
                b_.configure(text=f"{name}  · {k}" if k else name,
                             fg_color=ACCENT if act else C_SOFT, hover_color=ACCENT_HOVER if act else C_SOFT_HOVER,
                             text_color="white" if act else C_TEXT, border_color=ACCENT if act else C_BORDER)
            except Exception:
                pass

    def border(p):
        b = btns.get(p)
        if b is not None:
            on = sel[p].get()
            b.configure(border_color=ACCENT if on else C_BORDER, border_width=3 if on else 1,
                        fg_color=C_ACTIVE if on else C_CARD)

    def toggle(p):
        sel[p].set(not sel[p].get())
        border(p)
        upd()

    cols = 4
    pics: dict = {}
    for idx, p in enumerate(files):
        cell = ctk.CTkFrame(scroll, fg_color="transparent", corner_radius=10, border_width=1, border_color=C_BORDER)
        if current_tab[0] is None:
            cell.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)
        img = POOL_THUMB_CACHE.get(str(p))
        pic = ctk.CTkLabel(cell, text="" if img else "⏳", image=img, width=POOL_THUMB_W, height=POOL_THUMB_H,
                           fg_color=C_BORDER, corner_radius=6, text_color=C_MUTED)
        pic.pack(padx=4, pady=(4, 2))
        nm = p.stem if len(p.stem) <= 22 else p.stem[:21] + "…"
        lb = ctk.CTkLabel(cell, text=("🖼 " if p.suffix.lower() in IMAGE_EXTS else "") + nm, font=F(10),
                          text_color=C_MUTED, height=16)
        lb.pack(pady=(0, 4))
        for wdg in (cell, pic, lb):
            wdg.bind("<Button-1>", lambda e, pp=p: toggle(pp))
            try:
                wdg.configure(cursor="hand2")
            except Exception:
                pass
        btns[p] = cell
        pics[p] = pic
        border(p)

    def put(pp, pl):
        if not alive[0] or pl is None:
            return
        img = ctk.CTkImage(light_image=pl, dark_image=pl, size=(POOL_THUMB_W, POOL_THUMB_H))
        POOL_THUMB_CACHE[str(pp)] = img
        try:
            pics[pp].configure(image=img, text="")
        except Exception:
            pass

    started: set = set()

    def job(pp):
        if not alive[0]:
            return
        pl = _make_thumb_pil(pp, POOL_THUMB_W, POOL_THUMB_H)
        ui_call(lambda: put(pp, pl))

    def load_thumbs(lst):
        for p in lst:                             # Reihenfolge wie angezeigt
            if str(p) not in POOL_THUMB_CACHE and p not in started:
                started.add(p)
                _thumb_pool.submit(job, p)

    def visible_files():
        return tab_files[current_tab[0]] if current_tab[0] is not None else files

    def show_tab(name):
        """Reiter wechseln: nur die Zellen dieser Kategorie zeigen, nur deren Vorschaubilder laden."""
        if name not in tab_files:
            return
        current_tab[0] = name
        try:
            s_ = load_settings()
            s_["pool_tab"] = name
            save_settings(s_)
        except Exception:
            pass
        for c in btns.values():
            c.grid_remove()
        for i, p in enumerate(tab_files[name]):
            btns[p].grid(row=i // cols, column=i % cols, padx=5, pady=5)
        try:
            scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass
        load_thumbs(tab_files[name])
        upd()

    if current_tab[0] is not None:
        show_tab(current_tab[0])
    else:
        load_thumbs(files)

    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=18, pady=(0, 14))

    def all_(v):
        for p in visible_files():                 # mit Reitern: nur die gerade sichtbare Kategorie
            sel[p].set(v)
            border(p)
        upd()

    H(btn_secondary(foot, tr("Alle"), lambda: all_(True), width=90), "pw_all").pack(side="left")
    H(btn_secondary(foot, tr("Keine"), lambda: all_(False), width=90), "pw_all").pack(side="left", padx=8)

    def close():
        alive[0] = False
        win.destroy()

    def ok():
        keep = [it for it in selection if not _is_in_pool(it["path"])]
        pool_old = [it for it in selection if _is_in_pool(it["path"])]
        chosen = {p for p in files if sel[p].get()}
        # Reihenfolge: bereits gewählte Pool-Einträge bleiben, neue kommen hinten dazu
        new_pool = [it for it in pool_old if it["path"] in chosen]
        have = {it["path"] for it in new_pool}
        for p in files:
            if p in chosen and p not in have:
                new_pool.append({"kind": "photo" if p.suffix.lower() in IMAGE_EXTS else "video", "path": p})
        selection[:] = new_pool + keep
        queue_sel_thumbs()
        ui_status(tr("Pool: {n} ausgewählt").format(n=len(new_pool)))
        close()
        _refresh_step2_dynamic()

    H(btn_primary(foot, tr("Übernehmen"), ok, width=150, height=36), "pw_apply").pack(side="right")
    H(btn_secondary(foot, tr("Abbrechen"), close, width=120), "pw_apply").pack(side="right", padx=8)
    win.protocol("WM_DELETE_WINDOW", close)
    upd()


# ===================== Schritt 3: Extras =====================
SPEED_KEYS = [("gentle", "Sanft"), ("slow", "Langsam"), ("normal", "Normal"), ("fast", "Schnell"), ("random", "Random")]
_extra_folder_sig = {"v": None}


def _extras_signature():
    return tuple(bool(list_video_files(f)) for f in (VORSPANN_DIR, INTRO_DIR, OUTRO_DIR))


def _open_extra_folder(folder: Path):
    """Ordner öffnen; kommt der Nutzer ins Fenster zurück, wird Schritt 3 bei Änderungen neu aufgebaut."""
    _extra_folder_sig["v"] = _extras_signature()
    open_path_portable(folder)


def _on_app_focus(_e=None):
    if current_step == 3 and _extra_folder_sig["v"] is not None and _extras_signature() != _extra_folder_sig["v"]:
        _extra_folder_sig["v"] = None
        refresh_page()


def _folder_hint(folder: Path) -> tuple[str, bool]:
    n = len(list_video_files(folder))
    if n == 0:
        return tr("Ordner leer"), False
    if n == 1:
        return tr("1 Datei"), True
    return tr("{n} Dateien, zufällig").format(n=n), True


def build_step3(parent):
    heading(parent, tr("Noch etwas ergänzen?"),
            tr("Alle Extras sind optional. Ohne Änderungen kannst du direkt starten."))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="both", expand=True)
    row.grid_columnconfigure(0, weight=2, uniform="s3")     # feste Aufteilung 2:1 – Texte verschieben nichts
    row.grid_columnconfigure(1, weight=1, uniform="s3")
    row.grid_rowconfigure(0, weight=1)

    left = card(row)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    ctk.CTkLabel(left, text=tr("Optionale Videoelemente"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=24, pady=(16, 0))
    ctk.CTkLabel(left, text=tr("Gilt für 16:9 und 9:16 in voller Länge – der 58-Sekunden-Short bleibt immer ohne."),
                 font=F(12), text_color=C_MUTED, anchor="w").pack(fill="x", padx=24, pady=(0, 6))

    def opt_row(var, title, desc, folder: Optional[Path], hkey):
        r = ctk.CTkFrame(left, fg_color=C_SOFT, corner_radius=10, border_width=1, border_color=C_BORDER)
        r.pack(fill="x", padx=24, pady=3)
        H(r, hkey)
        usable = folder is None or bool(list_video_files(folder))
        if usable:
            sw = ctk.CTkSwitch(r, text="", variable=var, width=46, progress_color=ACCENT,
                               command=lambda: _refresh_step3_summary())
        else:
            # leerer Ordner: Option nicht wählbar (Einstellung selbst bleibt gespeichert)
            sw = ctk.CTkSwitch(r, text="", variable=ctk.BooleanVar(value=False), width=46, state="disabled")
        sw.pack(side="right", padx=12)
        H(sw, hkey)
        box = ctk.CTkFrame(r, fg_color="transparent")
        box.pack(side="left", fill="x", expand=True, padx=14, pady=7)
        l1 = ctk.CTkFrame(box, fg_color="transparent")
        l1.pack(fill="x")
        ctk.CTkLabel(l1, text=title, font=F(13, True), text_color=C_TEXT if usable else C_MUTED,
                     anchor="w", height=20).pack(side="left")
        if folder is not None:
            hint, ok = _folder_hint(folder)
            ctk.CTkLabel(l1, text="   " + hint, font=F(11, not ok),
                         text_color=C_MUTED if ok else C_WARN_TX, anchor="w", height=20).pack(side="left")
            lk = btn_link(l1, tr("Ordner öffnen"), lambda f=folder: _open_extra_folder(f))
            lk.configure(height=20, font=F(11))
            lk.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(box, text=desc if usable else tr("Lege ein Video in den Ordner {name}/, dann ist die Option wählbar.").format(name=folder.name),
                     font=F(11), text_color=C_MUTED, anchor="w", height=16).pack(fill="x")

    opt_row(vorspann_var, tr("Pre-Intro"), tr("Eigenes Video mit Ton vor der Musik"), VORSPANN_DIR, "sw_pre")
    opt_row(intro_var, tr("Intro"), tr("Stummer Clip am Anfang, Musik läuft schon"), INTRO_DIR, "sw_intro")
    opt_row(outro_var, tr("Outro"), tr("Eigenes Video mit Ton nach der Musik"), OUTRO_DIR, "sw_outro")
    ttl = get_title_from_music(music_file) if music_file else ""
    opt_row(title_var, tr("Titel"), tr("Musiktitel unten links einblenden") + (f" („{ttl[:40]}“)" if ttl else ""), None, "sw_title")

    sp = ctk.CTkFrame(left, fg_color=C_SOFT, corner_radius=10, border_width=1, border_color=C_BORDER)
    sp.pack(fill="x", padx=24, pady=(8, 14))
    H(sp, "speed")
    ctk.CTkLabel(sp, text=tr("Schnittgeschwindigkeit"), font=F(13, True), text_color=C_TEXT, anchor="w", height=20).pack(fill="x", padx=14, pady=(7, 0))
    desc = ctk.CTkLabel(sp, text="", font=F(11), text_color=C_MUTED, anchor="nw", justify="left",
                        height=30, wraplength=600)        # immer Platz für zwei Zeilen: nichts springt
    desc.pack(fill="x", padx=14)
    labels = [tr(l) for _, l in SPEED_KEYS]
    back = {tr(l): k for k, l in SPEED_KEYS}
    seg = ctk.CTkSegmentedButton(sp, values=labels, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                 font=F(12), height=32)
    loop_on = loop_selected()

    def upd_desc():
        if loop_on:
            desc.configure(text=tr("Nur ein Clip ausgewählt: Er läuft als Loop durch den ganzen Song – die Schnittgeschwindigkeit spielt keine Rolle."),
                           text_color=ACCENT)
        elif not speed_var.get():
            desc.configure(text=tr("Nichts gewählt: Jeder Clip läuft ganz – geschnitten wird am Clip-Ende, auf dem Beat."),
                           text_color=ACCENT)
        else:
            desc.configure(text=tr("Alle Schnitte liegen auf dem Beat – die Geschwindigkeit bestimmt nur, wie oft geschnitten wird. Nochmal klicken = ganzer Clip."),
                           text_color=C_MUTED)

    def pick_speed(v):
        if speed_var.get() and back[v] == speed_var.get():     # zweiter Klick: Auswahl aufheben = ganzer Clip
            speed_var.set("")
            seg.set("")
        else:
            seg.set(v)
            speed_var.set(back[v])
        upd_desc()
        _refresh_step3_summary()

    for v_, b_ in seg._buttons_dict.items():
        b_.configure(command=lambda v=v_: pick_speed(v))
    if speed_var.get():
        seg.set(tr(dict(SPEED_KEYS).get(speed_var.get(), "Normal")))
    if loop_on:
        seg.configure(state="disabled")
    upd_desc()
    seg.pack(fill="x", padx=14, pady=(6, 9))
    H(seg, "speed")

    right = card(row)
    right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    H(right, "summary")
    ctk.CTkLabel(right, text=tr("Zusammenfassung"), font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=24, pady=(18, 8))
    summ = ctk.CTkLabel(right, text="", font=F(12), text_color=C_TEXT, anchor="w", justify="left", wraplength=300)
    summ.pack(fill="x", padx=24)
    shell["summary"] = summ
    ctk.CTkFrame(right, fg_color=C_BORDER, height=1).pack(fill="x", padx=24, pady=14)
    ctk.CTkLabel(right, text=tr("Im nächsten Schritt"), font=F(13, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=24)
    ctk.CTkLabel(right, text=tr("Du siehst, wie dein Video aus den Clips zusammengesetzt wird, und kannst Clips verschieben oder tauschen – bevor gerendert wird."),
                 font=F(12), text_color=C_MUTED, anchor="w", justify="left", wraplength=300).pack(fill="x", padx=24, pady=(4, 18))
    _refresh_step3_summary()

    set_footer(left=[("←  " + tr("Zurück"), lambda: go(2), "secondary", True, "back")],
               right=[(tr("Weiter") + "  →", enter_step4, "primary", True, "to_render")])


def _refresh_step3_summary():
    lbl = shell.get("summary")
    try:
        if lbl is None or not lbl.winfo_exists():
            return
    except Exception:
        return
    n_v = sum(1 for it in selection if it["kind"] == "video")
    n_p = len(selection) - n_v
    extras = [tr(n) for n, v, f in (("Pre-Intro", vorspann_var, VORSPANN_DIR), ("Intro", intro_var, INTRO_DIR),
                                     ("Outro", outro_var, OUTRO_DIR), ("Titel", title_var, None))
              if v.get() and (f is None or list_video_files(f))]
    lines = [
        tr("Musik: ") + (Path(music_file).name if music_file else "–") + (f"  ({fmt_time(music_dur)})" if music_dur else ""),
        tr("Formate: ") + " + ".join(FORMAT_LABELS[k] for k in selected_formats()),
        tr("Bildmaterial: ") + tr("{v} Clips · {p} Fotos").format(v=n_v, p=n_p),
        tr("Extras: ") + (", ".join(extras) if extras else tr("keine")),
        tr("Schnitt: ") + speed_label(),
    ]
    lbl.configure(text="\n".join("•  " + l for l in lines))


# ===================== Schritt 4: Schnitt (Clipfolge planen, sortieren, rendern) =====================
plan: Optional[dict] = None              # aktuelle Clipfolge des Hauptvideos (siehe plan_video)
plan_sig = None                          # Einstellungen, aus denen der Plan entstand
plan_rendered_version: int = -1          # plan["version"], die zuletzt gerendert wurde (-1 = noch nie)
plan_busy: bool = False                  # Planung läuft
plan_error: Optional[str] = None
tile_state: dict = {}                    # Kachel-Index -> "run" | "done"
render_rows: list = []                   # weitere Formate: [{"fmt", "name", "state"}]
last_outputs: list = []                  # [(fmt, Path)] des letzten Renders
last_failed_formats: list = []
_drag = {"src": None, "hl": None}

TILE_W, TILE_H = 150, 84                 # Querformat-Kacheln
TILE_PW, TILE_PH = 72, 128               # Hochformat-Kacheln
TILE_PIL: dict = {}                      # Schlüssel -> PIL-Bild
TILE_IMG: dict = {}                      # (Schlüssel, gedimmt) -> CTkImage
_tile_queue: list = []
_tile_running = False


def main_format() -> str:
    f = selected_formats()
    return f[0] if f else "normal"


def current_plan_signature():
    return (
        music_file,
        tuple((it["kind"], str(it["path"])) for it in selection),
        main_format(),
        bool(intro_var.get()), bool(outro_var.get()), bool(vorspann_var.get()),
        tuple(sorted(p.name for p in list_video_files(INTRO_DIR))),
        tuple(sorted(p.name for p in list_video_files(OUTRO_DIR))),
        tuple(sorted(p.name for p in list_video_files(VORSPANN_DIR))),
        speed_var.get(),
    )


def plan_dirty() -> bool:
    """Gibt es nach einem Render noch nicht übernommene Änderungen an der Clipfolge?"""
    return plan is not None and plan_rendered_version >= 0 and plan["version"] != plan_rendered_version


def plan_is_rendered() -> bool:
    return plan is not None and plan_rendered_version >= 0 and bool(last_outputs) and last_outputs[0][1].exists()


def _sync_formats():
    """Nach dem Rendern abgewählte Formate nicht mehr anzeigen (die Dateien bleiben im Output-Ordner)."""
    global last_outputs
    sel = selected_formats()
    last_outputs = [o for o in last_outputs if o[0] in sel]


def missing_formats() -> list:
    """Nach dem Rendern neu angekreuzte Formate, die es noch nicht gibt."""
    if plan is None or plan_rendered_version < 0:
        return []
    have = {f for f, _p in last_outputs}
    return [k for k in selected_formats() if k not in have]


def enter_step4():
    """Zu Schritt 4. Haben sich Musik, Bildmaterial, Extras oder Schnitt geändert, wird neu geplant –
    vorher wird gefragt, falls dabei eine Sortierung oder ein fertiges Video verloren geht."""
    global plan_error
    if render_busy:
        return
    if not (music_file and selection):
        return
    sig = current_plan_signature()
    if plan is not None and plan_sig != sig:
        if plan["version"] > 0 or plan_rendered_version >= 0:
            if not messagebox.askyesno(
                    tr("Clipfolge neu planen?"),
                    tr("Du hast Musik, Bildmaterial, Extras, Format oder Schnitt geändert. Dafür wird die Clipfolge neu geplant – deine Sortierung geht dabei verloren.\n\nFortfahren?")):
                return
        _forget_plan()
    plan_error = None
    go(4)
    if plan is None and not plan_busy:
        start_planning()


def _forget_plan():
    global plan, plan_sig, plan_rendered_version
    plan = None
    plan_sig = None
    plan_rendered_version = -1
    tile_state.clear()


def _plan_prog_rows(fmt: str, use_intro: bool, use_outro: bool, use_vorspann: bool, speed: str) -> list:
    rows = [_prog_row("beats", "Musik analysieren", tr("Tempo und Beats deines Songs erkennen"))]
    if fmt == "shorts58":
        rows.append(_prog_row("short", "Besten Abschnitt für den Short finden",
                              tr("Der stärkste 58-Sekunden-Teil des Songs")))
    mode = plan_mode([{"kind": it["kind"], "path": str(it["path"])} for it in selection], speed)
    if mode == "beat":
        sp = dict(SPEED_KEYS).get(speed, speed)
        rows.append(_prog_row("cuts", "Schnitte auf den Beat legen", tr("Schnittgeschwindigkeit: ") + tr(sp)))
    elif mode == "full":
        rows.append(_prog_row("cuts", "Schnitte auf den Beat legen", tr("Ganzer Clip – Schnitt am Clip-Ende, auf dem Beat")))
    if fmt != "shorts58":
        ex = [n for on, n in ((use_vorspann, tr("Pre-Intro")), (use_intro, tr("Intro")), (use_outro, tr("Outro"))) if on]
        if ex:
            rows.append(_prog_row("extras", "Extras auswählen", ", ".join(ex)))
    if mode == "loop":
        rows.append(_prog_row("loop", "Loop vorbereiten", tr("Nahtlose Stelle im Clip suchen, im Takt")))
    else:
        rows.append(_prog_row("assign", "Clips und Fotos verteilen", tr("Jeder Schnitt bekommt einen Clip oder ein Foto")))
    rows.append(_prog_row("thumbs", "Vorschaubilder vorbereiten", tr("Für die Übersicht der Clipfolge")))
    return rows


def _plan_stage(name, *info):
    """Rückmeldung aus plan_video (läuft im Hauptthread über ui_call)."""
    m = plan_prog
    if _prog_find(m, name) is None:
        return
    detail = ""
    if name == "short" and info and not info[0]:
        detail = tr("nicht nötig")
    elif name == "assign" and len(info) >= 2 and info[1]:
        detail = tr("{n} Schnitte").format(n=info[1])
    _prog_start(m, name, detail=detail)


def start_planning():
    global plan_busy, plan_error
    plan_busy = True
    plan_error = None
    sig = current_plan_signature()
    args = dict(audio_path=music_file,
                media=[{"kind": it["kind"], "path": str(it["path"])} for it in selection],
                fmt=main_format(),
                use_intro=bool(intro_var.get()), use_outro=bool(outro_var.get()),
                use_vorspann=bool(vorspann_var.get()), speed=speed_var.get())
    plan_prog.clear()
    plan_prog.update({"rows": _plan_prog_rows(args["fmt"], args["use_intro"], args["use_outro"],
                                              args["use_vorspann"], args["speed"]),
                      "complete": False, "fired": False, "on_complete": None})
    refresh_page()

    def finished(p):
        plan_prog["on_complete"] = lambda: _planning_done(p, sig, None)
        plan_prog["complete"] = True
        _prog_finish(plan_prog)
        sl = shell.get("plan_steps")
        try:
            alive = sl is not None and sl.winfo_exists()
        except Exception:
            alive = False
        if not alive and not plan_prog["fired"]:          # keine Liste sichtbar -> sofort weiter
            plan_prog["fired"] = True
            _planning_done(p, sig, None)

    def worker():
        try:
            p = plan_video(status_cb=lambda t: ui_call(lambda: ui_status(t)),
                           stage_cb=lambda *a: ui_call(lambda: _plan_stage(*a)), **args)
            tiles = plan_tiles(p)
            n = len(tiles)
            ui_call(lambda: _prog_start(plan_prog, "thumbs", frac=0.0, detail=f"0 / {n}"))
            for k, t in enumerate(tiles):         # Vorschaubilder gleich mitladen
                _tile_pil(t, p["is_shorts"])
                r = _prog_find(plan_prog, "thumbs")
                if r is not None:
                    r["frac"] = (k + 1) / max(1, n)
                    r["detail"] = f"{k + 1} / {n}"
            ui_call(lambda: finished(p))
        except Exception as e:
            msg = str(e)

            def fail():
                plan_prog["fired"] = True
                _planning_done(None, sig, msg)
            ui_call(fail)

    threading.Thread(target=worker, daemon=True).start()


def _planning_done(p, sig, err):
    global plan, plan_sig, plan_busy, plan_error, plan_rendered_version
    plan_busy = False
    if err:
        plan_error = err
        ui_status(tr("Fehler: ") + err)
    else:
        plan, plan_sig, plan_rendered_version = p, sig, -1
        tile_state.clear()
        ui_status(tr("Clipfolge geplant: {n} Schnitte").format(n=len(p["slots"])))
    if current_step == 4:
        refresh_page()


# ── Vorschaubilder der Kacheln (Bild aus dem Originalclip an der geplanten Stelle)
def _tile_key(t: dict, portrait: bool) -> str:
    at = t["offset"] + min(0.5, t["dur"] / 2) if t["kind"] == "video" else 0.0
    return f"{t['src']}|{at:.2f}|{'p' if portrait else 'l'}"


def _frame_pil(src: str, at: float, w: int, h: int, kind: str) -> Optional["Image.Image"]:
    if w <= 400:                                   # nur kleine Vorschaubilder cachen (nicht die Thumbnail-Kandidaten)
        return _disk_thumb(Path(src), f"f{at:.2f}", w, h, lambda: _frame_pil_raw(src, at, w, h, kind))
    return _frame_pil_raw(src, at, w, h, kind)


def _frame_pil_raw(src: str, at: float, w: int, h: int, kind: str) -> Optional["Image.Image"]:
    import io
    from PIL import ImageOps
    p = Path(src)
    try:
        if kind == "photo" or p.suffix.lower() in IMAGE_EXTS:
            img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            return ImageOps.fit(img, (w, h))
        for ss in (at, 0.0):
            cmd = [ffmpeg_cmd(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", str(p),
                   "-frames:v", "1", "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                   "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
            try:
                data = check_output_hidden(cmd)
            except Exception:
                data = b""
            if data:
                return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        pass
    return None


def _tile_pil(t: dict, portrait: bool):
    k = _tile_key(t, portrait)
    if k not in TILE_PIL:
        w, h = (TILE_PW, TILE_PH) if portrait else (TILE_W, TILE_H)
        at = t["offset"] + min(0.5, t["dur"] / 2) if t["kind"] == "video" else 0.0
        TILE_PIL[k] = _frame_pil(t["src"], at, w, h, t["kind"])
    return TILE_PIL[k]


def _tile_image(t: dict, portrait: bool, dim: bool):
    k = _tile_key(t, portrait)
    pil = TILE_PIL.get(k)
    if pil is None:
        return None
    ik = (k, dim)
    if ik not in TILE_IMG:
        from PIL import ImageEnhance
        im = ImageEnhance.Brightness(ImageEnhance.Color(pil).enhance(0.25)).enhance(0.55) if dim else pil
        w, h = (TILE_PW, TILE_PH) if portrait else (TILE_W, TILE_H)
        TILE_IMG[ik] = ctk.CTkImage(light_image=im, dark_image=im, size=(w, h))
    return TILE_IMG[ik]


def _load_missing_tile_thumbs():
    """Fehlende Vorschaubilder (z. B. nach „Clip tauschen“) im Hintergrund nachladen."""
    global _tile_running
    if plan is None:
        return
    portrait = plan["is_shorts"]
    for t in plan_tiles(plan):
        if _tile_key(t, portrait) not in TILE_PIL and t not in _tile_queue:
            _tile_queue.append(t)
    if _tile_running or not _tile_queue:
        return
    _tile_running = True

    def worker():
        global _tile_running
        while _tile_queue:
            t = _tile_queue.pop(0)
            _tile_pil(t, portrait)
        _tile_running = False
        ui_call(lambda: (_refresh_tile_images() if current_step == 4 else None))

    threading.Thread(target=worker, daemon=True).start()


import math
import time


# ── Fortschritt als Liste (Planen und Rendern) und Laufband beim Rendern
def _col(c):
    """Farbtupel (hell, dunkel) -> aktuelle Farbe (für tk.Canvas, das keine Tupel kennt)."""
    if isinstance(c, (tuple, list)):
        return c[1] if ctk.get_appearance_mode() == "Dark" else c[0]
    return c


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _prog_row(key: str, title: str, sub: str = "") -> dict:
    return {"key": key, "title": title, "sub": sub, "state": "wait", "frac": None, "detail": ""}


def _prog_find(model: dict, key: str) -> Optional[dict]:
    for r in model["rows"]:
        if r["key"] == key:
            return r
    return None


def _prog_start(model: dict, key: str, frac: Optional[float] = None, detail: str = ""):
    """Schritt `key` beginnt: alle Schritte davor gelten als erledigt."""
    for r in model["rows"]:
        if r["key"] == key:
            r["state"] = "run"
            r["frac"] = frac
            r["detail"] = detail
            return
        if r["state"] in ("wait", "run"):
            r["state"] = "done"


def _prog_finish(model: dict, keys=None):
    for r in model["rows"]:
        if (keys is None or r["key"] in keys) and r["state"] in ("wait", "run"):
            r["state"] = "done"


class StepList:
    """Arbeitsschritte untereinander, jeder mit kleinem Balken. Die Anzeige läuft dem tatsächlichen
    Stand gleichmäßig hinterher (jeder Schritt ist kurz sichtbar), damit nichts nur aufblitzt."""
    MIN_ACTIVE = 0.45

    def __init__(self, parent, model: dict, columns: int = 1, compact: bool = False):
        self.model = model
        self.compact = compact
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.vis = []
        rows = model["rows"]
        per_col = -(-len(rows) // columns)
        for c_ in range(columns):
            self.frame.grid_columnconfigure(c_, weight=1, uniform="sl")
        for i, r in enumerate(rows):
            col, row = divmod(i, per_col)
            line = ctk.CTkFrame(self.frame, fg_color="transparent")
            line.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 18, 0),
                      pady=(3 if compact else 7))
            sz = 24 if compact else 30
            icon = ctk.CTkLabel(line, text=str(i + 1), width=sz, height=sz, corner_radius=sz // 2,
                                fg_color=C_TODO, text_color="white", font=F(11 if compact else 12, True))
            icon.pack(side="left", anchor="n" if not compact else "center")
            txt = ctk.CTkFrame(line, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True, padx=(10, 10))
            tl = ctk.CTkLabel(txt, text=tr(r["title"]), font=F(12 if compact else 13, True), text_color=C_MUTED,
                              anchor="w", height=18 if compact else 20)
            tl.pack(fill="x")
            sl = None
            if not compact and r["sub"]:
                sl = ctk.CTkLabel(txt, text=r["sub"], font=F(11), text_color=C_MUTED, anchor="w", height=16)
                sl.pack(fill="x")
            right = ctk.CTkFrame(line, fg_color="transparent")
            right.pack(side="right")
            det = ctk.CTkLabel(right, text="", font=F(11), text_color=C_MUTED, anchor="e",
                               width=90 if compact else 130, height=16)
            bar = ctk.CTkProgressBar(right, width=120 if compact else 220, height=7 if compact else 8,
                                     progress_color=C_BORDER, fg_color=C_BORDER, mode="determinate")
            bar.set(0)
            if compact:
                det.pack(side="right", padx=(8, 0))
                bar.pack(side="right")
            else:
                bar.pack(anchor="e", pady=(6, 3))
                det.pack(anchor="e")
            done = r["state"] in ("done", "fail")
            v = {"icon": icon, "title": tl, "sub": sl, "bar": bar, "det": det, "done": False,
                 "since": None, "disp": 0.0, "mode": "det", "look": "wait", "n": i + 1}
            self.vis.append(v)
            if done:
                self._set_done(i)
        self._tick()

    # Aussehen
    def _set_active(self, i):
        v = self.vis[i]
        if v["look"] != "run":
            v["look"] = "run"
            v["icon"].configure(fg_color=ACCENT, text=str(v["n"]))
            v["title"].configure(text_color=C_TEXT)
            v["bar"].configure(progress_color=ACCENT)

    def _set_done(self, i):
        v = self.vis[i]
        r = self.model["rows"][i]
        v["done"] = True
        if v["mode"] == "ind":
            v["bar"].stop()
            v["bar"].configure(mode="determinate")
            v["mode"] = "det"
        if r["state"] == "fail":
            v["look"] = "fail"
            v["icon"].configure(fg_color=C_DANGER_TX, text="✕")
            v["title"].configure(text_color=C_DANGER_TX)
            v["bar"].configure(progress_color=C_DANGER_TX)
        else:
            v["look"] = "done"
            v["icon"].configure(fg_color=C_OK, text="✓")
            v["title"].configure(text_color=C_TEXT)
            v["bar"].configure(progress_color=C_OK)
        v["bar"].set(1.0)
        v["det"].configure(text=r["detail"] or tr("fertig"))

    def _tick(self):
        try:
            if not self.frame.winfo_exists():
                return
        except Exception:
            return
        now = time.time()
        rows = self.model["rows"]
        k = next((i for i, v in enumerate(self.vis) if not v["done"]), None)
        if k is None:
            if self.model.get("complete") and not self.model.get("fired"):
                self.model["fired"] = True
                fn = self.model.get("on_complete")
                if fn:
                    self.frame.after(350, fn)
        else:
            r, v = rows[k], self.vis[k]
            if r["state"] != "wait":
                if v["since"] is None:
                    v["since"] = now
                self._set_active(k)
                if r["state"] == "fail":
                    self._set_done(k)
                else:
                    finishing = r["state"] == "done" and now - v["since"] >= self.MIN_ACTIVE
                    target = 1.0 if finishing else r["frac"]
                    bar = v["bar"]
                    if target is None:
                        if v["mode"] != "ind":
                            bar.configure(mode="indeterminate", indeterminate_speed=1.2)
                            bar.start()
                            v["mode"] = "ind"
                    else:
                        if v["mode"] != "det":
                            bar.stop()
                            bar.configure(mode="determinate")
                            v["mode"] = "det"
                            v["disp"] = 0.0
                        d = target - v["disp"]
                        v["disp"] += d * 0.25 if abs(d) > 0.004 else d
                        bar.set(max(0.0, min(1.0, v["disp"])))
                    v["det"].configure(text=r["detail"])
                    # sanftes Pulsieren der Nummer
                    ph = (math.sin(now * 5.0) + 1) / 2
                    try:
                        v["icon"].configure(fg_color=_mix(_col(ACCENT), "#7fb3de", ph * 0.6))
                    except Exception:
                        pass
                    if finishing and v["disp"] >= 0.995:
                        self._set_done(k)
        self.frame.after(40, self._tick)


BAND_W, BAND_H = 176, 99          # Kacheln im Laufband (Querformat)
BAND_PW, BAND_PH = 70, 124        # Hochformat


class RenderBand:
    """Laufband beim Rendern: eine Reihe Kacheln; der Abschnitt, der gerade entsteht, steht im linken
    Drittel, fertige laufen nach links hinaus, die nächsten kommen von rechts. Darunter ein dünner
    Streifen mit dem ganzen Video."""

    def __init__(self, parent, plan_: dict, states: Optional[dict] = None):
        from PIL import ImageTk  # noqa: F401  (vorab laden)
        self.states = tile_state if states is None else states
        s = ctk.ScalingTracker.get_widget_scaling(parent)
        self.s = s
        self.portrait = plan_["is_shorts"]
        bw, bh = (BAND_PW, BAND_PH) if self.portrait else (BAND_W, BAND_H)
        self.tw, self.th = int(bw * s), int(bh * s)
        self.hdr = int(22 * s)
        self.pad = int(10 * s)
        self.pitch = self.tw + int(16 * s)
        self.tiles = plan_tiles(plan_)
        self.total = sum(t["dur"] for t in self.tiles) or 1.0
        self.bg = _col(C_CARD)
        self.fam = F(10).cget("family")
        hgt = self.pad + self.hdr + self.th + self.pad
        self.cv = tk.Canvas(parent, height=hgt, highlightthickness=0, bd=0, bg=self.bg)
        self.strip = tk.Canvas(parent, height=int(30 * s), highlightthickness=0, bd=0, bg=self.bg)
        self.photos: dict = {}
        self.items: list = []
        self.off = 0.0
        self.drawn = 0.0
        self.width = 0
        self.looks: dict = {}
        self.cv.bind("<Configure>", lambda e: self._layout())
        self.strip.bind("<Configure>", lambda e: self._layout_strip())
        H(self.cv, "render_band")
        H(self.strip, "render_band")
        self._tick()

    # Hilfen
    def _font(self, px, bold=False):
        return (self.fam, -int(px * self.s), "bold" if bold else "normal")

    def _current(self) -> int:
        for i in range(len(self.tiles)):
            if self.states.get(i) == "run":
                return i
        for i in range(len(self.tiles)):
            if self.states.get(i) != "done":
                return i
        return max(0, len(self.tiles) - 1)

    def _photo(self, i, dim):
        from PIL import ImageTk, ImageEnhance
        t = self.tiles[i]
        pil = TILE_PIL.get(_tile_key(t, self.portrait))
        if pil is None:
            return None
        k = (_tile_key(t, self.portrait), dim)
        if k not in self.photos:
            im = pil.resize((self.tw, self.th), Image.LANCZOS)
            if dim:
                im = ImageEnhance.Brightness(ImageEnhance.Color(im).enhance(0.25)).enhance(0.6)
            self.photos[k] = ImageTk.PhotoImage(im)
        return self.photos[k]

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.cv.create_polygon(pts, smooth=True, **kw)

    def _name(self, i):
        t = self.tiles[i]
        if t["type"] == "clip":
            n = sum(1 for x in self.tiles[:i + 1] if x["type"] == "clip")
            nm = (tr("Durchlauf") if t["kind"] == "loop" else tr("Clip")) + f" {n}"
        else:
            nm = {"pre": tr("Pre-Intro"), "intro": tr("Intro"), "outro": tr("Outro")}[t["type"]]
        return nm if self.portrait else f"{nm}  ·  {fmt_time(t['t'])}"

    # Aufbau
    def _layout(self):
        w = self.cv.winfo_width()
        if w < 50 or w == self.width:
            return
        self.width = w
        self.cv.delete("all")
        self.items = []
        self.looks = {}
        self.anchor = int(w * 0.30) - self.tw // 2
        cur = self._current()
        self.off = self.drawn = cur * self.pitch
        y0 = self.pad
        r = int(8 * self.s)
        for i in range(len(self.tiles)):
            x = self.anchor + i * self.pitch - self.drawn
            tg = ("band", f"t{i}")
            fr = self._rrect(x - 4, y0 - 2, x + self.tw + 4, y0 + self.hdr + self.th + 4, r,
                             fill=_col(C_SOFT), outline=_col(C_BORDER), width=2, tags=tg)
            hd = self.cv.create_text(x + 2, y0 + self.hdr // 2, text=self._name(i), anchor="w",
                                     font=self._font(11, True), fill=_col(C_MUTED), tags=tg)
            ph = self.cv.create_rectangle(x, y0 + self.hdr, x + self.tw, y0 + self.hdr + self.th,
                                          fill=_col(C_BORDER), outline="", tags=tg)
            im = self.cv.create_image(x, y0 + self.hdr, anchor="nw", tags=tg)
            bx, by = x + self.tw - int(14 * self.s), y0 + self.hdr + int(14 * self.s)
            rr = int(10 * self.s)
            bd = self.cv.create_oval(bx - rr, by - rr, bx + rr, by + rr, fill=_col(C_OK), outline="white",
                                     width=2, state="hidden", tags=tg)
            bt = self.cv.create_text(bx, by, text="✓", fill="white", font=self._font(12, True),
                                     state="hidden", tags=tg)
            self.items.append({"fr": fr, "hd": hd, "ph": ph, "im": im, "bd": bd, "bt": bt})
            self._apply(i, force=True)
        # weiche Ränder links und rechts
        fw = int(70 * self.s)
        hgt = int(self.cv.cget("height"))
        for side in ("l", "r"):
            img = self._fade(fw, hgt, side)
            self.photos[("fade", side, w)] = img
            self.cv.create_image(0 if side == "l" else w - fw, 0, anchor="nw", image=img, tags=("fade",))
        self.cv.tag_raise("fade")

    def _fade(self, fw, hgt, side):
        from PIL import ImageTk
        rgb = tuple(int(self.bg[i:i + 2], 16) for i in (1, 3, 5))
        im = Image.new("RGBA", (fw, hgt))
        px = im.load()
        for x in range(fw):
            a = 1 - x / fw if side == "l" else x / fw
            a = int(255 * (a ** 1.6))
            for y in range(hgt):
                px[x, y] = rgb + (a,)
        return ImageTk.PhotoImage(im)

    def _layout_strip(self):
        st = self.strip
        st.delete("all")
        w = st.winfo_width()
        if w < 50:
            return
        self.blocks = []
        x0, x1 = 2, w - 2
        y0, y1 = 2, 2 + int(8 * self.s)
        x = x0
        for t in self.tiles:
            bw_ = (x1 - x0) * t["dur"] / self.total
            b = st.create_rectangle(x, y0, x + bw_, y1, fill=_col(C_BORDER), outline=self.bg, width=1)
            self.blocks.append(b)
            x += bw_
        ty = y1 + int(10 * self.s)
        st.create_text(x0, ty, text="0:00", anchor="w", font=self._font(10), fill=_col(C_MUTED))
        st.create_text(x1, ty, text=fmt_time(self.total), anchor="e", font=self._font(10), fill=_col(C_MUTED))
        self.strip_txt = st.create_text((x0 + x1) // 2, ty, text="", font=self._font(10, True), fill=_col(C_MUTED))
        self._update_strip()

    def _update_strip(self):
        if not getattr(self, "blocks", None):
            return
        done_n = 0
        for i, b in enumerate(self.blocks):
            stt = self.states.get(i)
            col = _col(C_OK) if stt == "done" else (_col(ACCENT) if stt == "run" else _col(C_BORDER))
            self.strip.itemconfigure(b, fill=col)
            done_n += stt == "done"
        self.strip.itemconfigure(self.strip_txt, text=tr("{n} von {total} Abschnitten fertig").format(
            n=done_n, total=len(self.blocks)))

    def _apply(self, i, force=False):
        """Aussehen der Kachel i an ihren Zustand anpassen."""
        if i >= len(self.items):
            return
        stt = self.states.get(i) or "wait"
        if not force and self.looks.get(i) == stt:
            return
        self.looks[i] = stt
        it = self.items[i]
        cv = self.cv
        img = self._photo(i, dim=(stt == "wait"))
        if img is not None:
            cv.itemconfigure(it["im"], image=img, state="normal")
        else:
            cv.itemconfigure(it["im"], state="hidden")
        if stt == "done":
            cv.itemconfigure(it["fr"], outline=_col(C_OK), width=2)
            cv.itemconfigure(it["hd"], fill=_col(C_OK), text="✓ " + self._name(i))
            cv.itemconfigure(it["bd"], state="normal")
            cv.itemconfigure(it["bt"], state="normal")
        elif stt == "run":
            cv.itemconfigure(it["fr"], outline=_col(ACCENT), width=3)
            cv.itemconfigure(it["hd"], fill=_col(ACCENT), text="▶ " + self._name(i))
            cv.itemconfigure(it["bd"], state="hidden")
            cv.itemconfigure(it["bt"], state="hidden")
        else:
            cv.itemconfigure(it["fr"], outline=_col(C_BORDER), width=2)
            cv.itemconfigure(it["hd"], fill=_col(C_MUTED), text=self._name(i))
            cv.itemconfigure(it["bd"], state="hidden")
            cv.itemconfigure(it["bt"], state="hidden")

    def state_changed(self, i):
        self._apply(i)
        self._update_strip()

    def refresh_images(self):
        for i in range(len(self.items)):
            self._apply(i, force=True)

    def _tick(self):
        try:
            if not self.cv.winfo_exists():
                return
        except Exception:
            return
        if self.items:
            cur = self._current()
            target = cur * self.pitch
            d = target - self.off
            if abs(d) > 0.5:
                step = d * 0.12
                mn = 1.5 * self.s
                if abs(step) < mn:
                    step = mn if d > 0 else -mn
                    if abs(step) > abs(d):
                        step = d
                self.off += step
            else:
                self.off = target
            dx = self.off - self.drawn
            if abs(dx) >= 0.5:
                self.cv.move("band", -round(dx), 0)
                self.drawn += round(dx)
            # Rahmen der laufenden Kachel pulsiert
            if self.states.get(cur) == "run" and cur < len(self.items):
                ph = (math.sin(time.time() * 5.0) + 1) / 2
                self.cv.itemconfigure(self.items[cur]["fr"], outline=_mix(_col(ACCENT), "#8cc0ea", ph * 0.7))
        self.cv.after(30, self._tick)


def _pct_text(val: float, update: bool = False) -> str:
    """„37 %  ·  noch ca. 2 Min.“ – Restzeit aus der bisherigen Dauer geschätzt (geglättet)."""
    txt = f"{int(round(val * 100))} %"
    t0 = shell.get("_render_t0")
    if update and t0 and val >= 0.04:
        el = time.time() - t0
        if el >= 6:
            rest = el * (1.0 - val) / max(val, 1e-3)
            old = shell.get("_eta")
            shell["_eta"] = rest if old is None else 0.7 * old + 0.3 * rest
    eta = shell.get("_eta")
    if eta is not None and val < 0.995:
        if eta < 60:
            txt += "  ·  " + tr("noch unter 1 Min.")
        else:
            txt += "  ·  " + tr("noch ca. {n} Min.").format(n=int(math.ceil(eta / 60.0)))
    return txt


render_prog: dict = {"rows": [], "complete": False, "fired": True, "on_complete": None, "clip_idx": []}
plan_prog: dict = {"rows": [], "complete": False, "fired": True, "on_complete": None}


# ── Seite
def build_step4(parent):
    if plan_busy or (plan is None and not plan_error):
        heading(parent, tr("Clipfolge wird geplant …"),
                tr("Die Musik wird analysiert und die Schnitte werden auf den Beat gelegt. Das dauert nur einen Moment."))
        c = card(parent)
        c.pack(fill="x")
        if plan_prog.get("rows"):
            sl = StepList(c, plan_prog)
            sl.frame.pack(fill="x", padx=28, pady=(18, 18))
            shell["plan_steps"] = sl.frame
            H(sl.frame, "plan_steps")
        else:
            pb = ctk.CTkProgressBar(c, height=10, progress_color=ACCENT, mode="indeterminate")
            pb.pack(fill="x", padx=28, pady=(24, 8))
            pb.start()
            ctk.CTkLabel(c, textvariable=status_var, font=F(12), text_color=C_MUTED, anchor="w").pack(fill="x", padx=28, pady=(0, 22))
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", not plan_busy, "back")])
        return
    if plan is None:
        heading(parent, tr("Das hat leider nicht geklappt"), tr("Die Clipfolge konnte nicht geplant werden."))
        c = card(parent)
        c.pack(fill="x")
        ctk.CTkLabel(c, text=plan_error or "", font=F(12), text_color=C_DANGER_TX, anchor="w", justify="left",
                     wraplength=960).pack(fill="x", padx=28, pady=22)
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[(tr("Problem melden"), create_problem_report, "secondary", True, "report"),
                          (tr("Erneut versuchen"), start_planning, "primary", True, "retry")])
        return

    rendered = plan_rendered_version >= 0
    dirty = plan_dirty()
    if render_busy:
        heading(parent, tr("Dein Video wird erstellt …"),
                tr("Das Band zeigt, welcher Abschnitt gerade entsteht – fertige laufen nach links hinaus. Ein Klang meldet, wenn alles fertig ist."))
    elif render_failed:
        heading(parent, tr("Das hat leider nicht geklappt"), tr("Beim Erstellen des Videos ist ein Fehler aufgetreten."))
    elif plan.get("mode") == "loop":
        heading(parent, tr("So wird dein Video aufgebaut"),
                tr("Ein einziger Clip läuft als Loop durch den ganzen Song – nahtlos und im Takt."))
    elif rendered:
        heading(parent, tr("Clips ändern"),
                tr("Verschiebe oder tausche Clips. „Änderungen übernehmen“ rendert nur, was sich geändert hat."))
    else:
        heading(parent, tr("So wird dein Video aufgebaut"),
                tr("Ziehe Clips an eine neue Position. „Clip tauschen“ nimmt zufällig einen anderen Clip gleicher Länge. Dann „Video erstellen“."))

    c = card(parent)
    c.pack(fill="both", expand=True)
    top = ctk.CTkFrame(c, fg_color="transparent")
    top.pack(fill="x", padx=20, pady=(12, 4))
    mf = plan["fmt"]
    others = [FORMAT_LABELS[k] for k in selected_formats()[1:]]
    if plan.get("mode") == "loop":
        info = tr("Loop für: ") + FORMAT_LABELS[mf] + f"  ·  {fmt_time(plan['music_dur'])}"
    else:
        info = tr("Clipfolge für: ") + FORMAT_LABELS[mf] + f"  ·  {len(plan['slots'])} " + tr("Schnitte") + \
            f"  ·  {fmt_time(plan['music_dur'])}"
    if others and not rendered:
        info += "   ·   " + tr("automatisch dazu: ") + ", ".join(others)
    lbl = ctk.CTkLabel(top, text=info, font=F(12, True), text_color=C_TEXT, anchor="w")
    lbl.pack(side="left")
    H(lbl, "plan_grid")
    chip = ctk.CTkLabel(top, text=tr("Änderungen vorgemerkt"), font=F(11, True), fg_color=C_WARN_BG,
                        text_color=C_WARN_TX, corner_radius=8, height=26, padx=10)
    shell["chip"] = chip
    if dirty and not render_busy:
        chip.pack(side="right")
    elif rendered and not render_busy and missing_formats():
        chip.configure(text=tr("Noch nicht erstellt: ") + ", ".join(FORMAT_LABELS[k] for k in missing_formats()))
        chip.pack(side="right")
        H(chip, "render_missing")

    if plan.get("mode") == "loop" and not render_busy and not render_failed:
        _build_loop_card(c)
        _step4_footer()
        return

    if render_busy:
        pct = ctk.CTkLabel(top, text=_pct_text(shell.get("_last_progress", 0.0)), font=F(13, True),
                           text_color=ACCENT)
        pct.pack(side="right")
        shell["render_pct"] = pct
        H(pct, "render_progress")
        xp = render_prog.get("extra_plan")
        if xp is not None:                 # weiteres Format (z. B. Short) wird gerade erstellt
            lbl.configure(text=tr("Wird automatisch dazu erstellt: ") + FORMAT_LABELS[xp["fmt"]] +
                          f"  ·  {len(xp['slots'])} " + tr("Schnitte") + f"  ·  {fmt_time(xp['music_dur'])}")
            band = RenderBand(c, xp, render_prog["extra_state"])
        else:
            band = RenderBand(c, plan)
        band.cv.pack(fill="x", padx=14, pady=(8, 0))
        band.strip.pack(fill="x", padx=26, pady=(6, 6))
        shell["render_band"] = band
        box = ctk.CTkFrame(c, fg_color=C_SOFT, corner_radius=10)
        box.pack(fill="x", padx=20, pady=(4, 14))
        H(box, "render_progress")
        n_rows = len(render_prog.get("rows", []))
        sl = StepList(box, render_prog, columns=2 if n_rows > 3 else 1, compact=True)
        sl.frame.pack(fill="x", padx=14, pady=10)
        H(sl.frame, "render_progress")
        _step4_footer()
        return
    shell["render_band"] = None

    if render_failed:
        box = ctk.CTkFrame(c, fg_color=C_SOFT, corner_radius=10)
        box.pack(fill="x", padx=20, pady=(4, 6))
        H(box, "render_progress")
        bar = ctk.CTkProgressBar(box, height=12, progress_color=ACCENT)
        bar.set(shell.get("_last_progress", 0.0))
        bar.pack(fill="x", padx=14, pady=(10, 4))
        shell["render_bar"] = bar
        ctk.CTkLabel(box, textvariable=status_var, font=F(11), text_color=C_MUTED, anchor="w").pack(fill="x", padx=14)
        # Hinweis: ein leerer CTkFrame ist standardmäßig 200 px hoch -> nur anlegen, wenn es mehrere Formate gibt
        rows = ctk.CTkFrame(box, fg_color="transparent", height=1)
        if len(render_rows) > 1:
            rows.pack(fill="x", padx=14, pady=(2, 8))
        else:
            ctk.CTkFrame(box, fg_color="transparent", height=8).pack()
        shell["render_rows"] = rows
        _refresh_render_rows()
        if render_failed:
            ctk.CTkLabel(box, text=render_failed, font=F(12), text_color=C_DANGER_TX, anchor="w", justify="left",
                         wraplength=940).pack(fill="x", padx=14, pady=(0, 10))

    grid_scroll = ctk.CTkScrollableFrame(c, fg_color="transparent")
    grid_scroll.pack(fill="both", expand=True, padx=10, pady=(2, 10))
    shell["tile_grid"] = grid_scroll
    H(grid_scroll, "plan_grid")
    _build_tiles()
    _load_missing_tile_thumbs()

    _step4_footer()


def _build_loop_card(c):
    """Schritt 4 im Loop-Modus: ein Clip, der durch den ganzen Song läuft."""
    shell["tile_widgets"] = []
    info = plan.get("loop")
    src = Path(plan.get("loop_src") or plan["slots"][0]["src"])
    body = ctk.CTkFrame(c, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=20, pady=(10, 16))
    H(body, "loop_card")
    portrait = plan["is_shorts"]
    pw, ph = (180, 320) if portrait else (480, 270)
    pic = ctk.CTkLabel(body, text="⏳", width=pw, height=ph, fg_color=C_BORDER, corner_radius=10)
    pic.pack(side="left", anchor="n")
    H(pic, "loop_card")

    def show(pil):
        try:
            if pil is not None and pic.winfo_exists():
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(pw, ph))
                pic.configure(image=img, text="")
                pic._mvg_img = img
        except Exception:
            pass

    def load():
        at = (info["a"] + 0.5) if info else 0.5
        pil = _frame_pil_raw(str(src), at, pw, ph, "video")
        ui_call(lambda: show(pil))
    threading.Thread(target=load, daemon=True).start()

    txt = ctk.CTkFrame(body, fg_color="transparent")
    txt.pack(side="left", fill="both", expand=True, padx=(24, 0))
    ctk.CTkLabel(txt, text="↻  " + src.name, font=F(16, True), text_color=C_TEXT, anchor="w").pack(fill="x")
    if info is None:
        kind = tr("Kein Loop nötig")
        expl = tr("Der Clip ist länger als der Song. Er läuft einfach von Anfang an und endet mit der Musik.")
        rows = [(tr("Länge im Video"), fmt_time(plan["music_dur"] - plan.get("intro_len", 0.0)))]
    else:
        L = info["frames"] / FPS
        n_rep = sum(1 for sl in plan["slots"] if sl["kind"] == "loop")
        if info["type"] == "loop":
            kind = tr("Nahtloser Loop")
            expl = tr("Das Programm hat im Clip zwei Stellen gefunden, die sich sehr ähnlich sehen, und loopt genau dazwischen. Am Übergang wird kurz überblendet, und die Länge eines Durchlaufs ist auf den Beat abgestimmt – so fällt die Wiederholung kaum auf.")
        else:
            kind = tr("Boomerang (vor und zurück)")
            expl = tr("Anfang und Ende des Clips sehen zu verschieden aus für einen unsichtbaren Übergang. Deshalb läuft der Clip vorwärts und dann rückwärts – so gibt es keinen Sprung. Die Wendepunkte liegen auf dem Beat.")
        rows = [(tr("Art"), kind), (tr("Ein Durchlauf"), f"{L:.1f} s".replace(".", "," if LANG == "de" else ".")),
                (tr("Wiederholungen"), f"{n_rep}×"), (tr("Länge im Video"), fmt_time(plan["music_dur"] - plan.get("intro_len", 0.0)))]
    grid = ctk.CTkFrame(txt, fg_color="transparent")
    grid.pack(fill="x", pady=(12, 10))
    for r_, (k_, v_) in enumerate(rows):
        ctk.CTkLabel(grid, text=k_, font=F(12), text_color=C_MUTED, anchor="w").grid(row=r_, column=0, sticky="w", pady=2)
        ctk.CTkLabel(grid, text=v_, font=F(12, True), text_color=C_TEXT, anchor="w").grid(row=r_, column=1, sticky="w",
                                                                                          padx=(16, 0), pady=2)
    ctk.CTkLabel(txt, text=expl, font=F(12), text_color=C_MUTED, anchor="w", justify="left",
                 wraplength=420 if not portrait else 640).pack(fill="x")
    ctk.CTkLabel(txt, text=tr("Möchtest du lieber geschnittene Clips? Wähle in Schritt 2 weitere Clips oder Fotos dazu."),
                 font=F(11), text_color=C_MUTED, anchor="w", justify="left",
                 wraplength=420 if not portrait else 640).pack(fill="x", pady=(10, 0))


def _step4_footer():
    rendered = plan_rendered_version >= 0
    dirty = plan_dirty()
    if render_busy:
        set_footer()
    elif render_failed:
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[(tr("Problem melden"), create_problem_report, "secondary", True, "report"),
                          ("▶  " + tr("Erneut versuchen"), do_render, "primary", True, "retry")])
    elif rendered and not dirty and missing_formats():
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[(tr("Zur Fertig-Seite") + "  →", lambda: go(5), "secondary", plan_is_rendered(), "to_done"),
                          ("▶  " + tr("Fehlende Formate erstellen"), do_render, "primary", True, "render_missing")])
    elif rendered and not dirty:
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[(tr("Zur Fertig-Seite") + "  →", lambda: go(5), "primary", plan_is_rendered(), "to_done")])
    elif rendered:
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[(tr("Verwerfen"), discard_plan_changes, "secondary", True, "discard"),
                          ("✔  " + tr("Änderungen übernehmen"), do_render, "primary", True, "apply")])
    else:
        set_footer(left=[("←  " + tr("Zurück"), lambda: go(3), "secondary", True, "back")],
                   right=[("▶  " + tr("Video erstellen"), do_render, "primary", True, "render")])


_rendered_slots_backup: Optional[list] = None   # Stand der Clipfolge beim letzten Render (für „Verwerfen“)


def discard_plan_changes():
    if plan is None or _rendered_slots_backup is None:
        return
    import copy
    plan["slots"] = copy.deepcopy(_rendered_slots_backup)
    plan["version"] = plan_rendered_version
    # zurück zur Fertig-Seite – „Verwerfen“ beendet das Ändern (erst nach dem Klick-Ereignis umbauen)

    def back():
        if plan_is_rendered():
            go(5)
        else:
            _after_plan_edit()
        ui_status(tr("Änderungen verworfen – das Video ist unverändert."))
    app.after(10, back)


def _tile_border(t: dict, i: int):
    st = tile_state.get(i)
    if st == "run":
        return ACCENT
    if st == "done" and render_busy:
        return C_OK
    if t["type"] in ("intro", "outro"):
        return C_LOCK
    if t["type"] == "pre":
        return C_LOCK_PRE
    return C_BORDER


def _build_tiles():
    grid = shell.get("tile_grid")
    try:
        if grid is None or not grid.winfo_exists() or plan is None:
            return
    except Exception:
        return
    for w in grid.winfo_children():
        w.destroy()
    portrait = plan["is_shorts"]
    tiles = plan_tiles(plan)
    try:
        avail = max(600, shell["content"].winfo_width() - 70)
    except Exception:
        avail = 1000
    tw = TILE_PW if portrait else TILE_W
    cols = max(4, avail // (tw + 22))
    shell["tile_widgets"] = []
    clip_no = 0
    for i, t in enumerate(tiles):
        r, cpos = divmod(i, cols)
        fixed = t["type"] != "clip"
        st = tile_state.get(i)
        frm = ctk.CTkFrame(grid, corner_radius=10, border_width=2, border_color=_tile_border(t, i), fg_color=C_SOFT)
        frm.grid(row=r, column=cpos, padx=4, pady=4, sticky="n")
        if t["type"] == "clip":
            clip_no += 1
            name = tr("Clip") + f" {clip_no}"
        else:
            name = "🔒 " + {"pre": tr("Pre-Intro"), "intro": tr("Intro"), "outro": tr("Outro")}[t["type"]]
        mark = "✓ " if (render_busy and st == "done") else ""
        hdr_txt = f"{mark}{name}" if portrait else f"{mark}{name}  ·  {fmt_time(t['t'])}–{fmt_time(t['t'] + t['dur'])}"
        hdr = ctk.CTkLabel(frm, text=hdr_txt, font=F(10, True), height=18, anchor="w",
                           text_color=(C_LOCK_PRE if t["type"] == "pre" else C_LOCK) if fixed else C_TEXT)
        hdr.pack(fill="x", padx=7, pady=(4, 1))
        dim = render_busy and st != "done"
        img = _tile_image(t, portrait, dim)
        pic = ctk.CTkLabel(frm, text="" if img else "⏳", image=img,
                           width=TILE_PW if portrait else TILE_W, height=TILE_PH if portrait else TILE_H,
                           fg_color=C_BORDER, corner_radius=4)
        pic.pack(padx=6, pady=(0, 2))
        if fixed:
            lk = ctk.CTkLabel(frm, text=tr("Gesperrt"), font=F(10, True), fg_color=C_WARN_BG, text_color=C_WARN_TX,
                              corner_radius=6, height=22)
            lk.pack(fill="x", padx=6, pady=(2, 6))
            for w in (frm, hdr, pic, lk):
                H(w, "plan_lock")
        else:
            rb = ctk.CTkButton(frm, text="↻" if portrait else "↻ " + tr("Clip tauschen"), height=22,
                               width=(TILE_PW if portrait else TILE_W), font=F(10, True),
                               fg_color=C_CARD, hover_color=C_ACTIVE, text_color=ACCENT, border_width=1,
                               border_color=C_BORDER, corner_radius=6,
                               state="disabled" if render_busy else "normal",
                               command=lambda s=t["slot"]: swap_random(s))
            rb.pack(fill="x", padx=6, pady=(2, 6))
            H(rb, "plan_swap")
            for w in (frm, hdr, pic):
                H(w, "plan_grid")
                if not render_busy:
                    w.bind("<ButtonPress-1>", lambda e, k=i: _tile_press(k))
                    w.bind("<B1-Motion>", _tile_motion)
                    w.bind("<ButtonRelease-1>", _tile_release)
        shell["tile_widgets"].append((frm, hdr, pic, t))


def _set_tile_state(i: int, state: str):
    """Live-Anzeige beim Rendern: Kachel i läuft gerade / ist fertig."""
    tile_state[i] = state
    idx = render_prog.get("clip_idx") or []
    r = _prog_find(render_prog, "clips")
    if r is not None and idx and r["state"] == "run":
        done_n = sum(1 for k in idx if tile_state.get(k) == "done")
        r["frac"] = done_n / len(idx)
        r["detail"] = f"{done_n} / {len(idx)}"
    band = shell.get("render_band")
    if band is not None:
        try:
            band.state_changed(i)
        except Exception:
            pass
    tw = shell.get("tile_widgets") or []
    if i >= len(tw) or plan is None:
        return
    frm, hdr, pic, t = tw[i]
    try:
        if not frm.winfo_exists():
            return
        frm.configure(border_color=_tile_border(t, i))
        if state == "done":
            pic.configure(image=_tile_image(t, plan["is_shorts"], False))
            txt = hdr.cget("text")
            if not txt.startswith("✓"):
                hdr.configure(text="✓ " + txt)
    except Exception:
        pass


def swap_random(slot: int):
    if render_busy or plan is None:
        return
    if plan_replace(plan, slot):
        ui_status(tr("Clip getauscht"))
        _after_plan_edit()
    else:
        ui_status(tr("Keine alternativen Clips verfügbar."))


def _after_plan_edit():
    """Nach Verschieben/Tauschen: nur Bilder der Kacheln und die Bedienelemente aktualisieren –
    das Raster bleibt stehen (kein Flackern)."""
    _refresh_tile_images()
    _load_missing_tile_thumbs()
    chip = shell.get("chip")
    try:
        if chip is not None and chip.winfo_exists():
            if plan_dirty():
                chip.configure(text=tr("Änderungen vorgemerkt"))
                chip.pack(side="right")
            elif missing_formats():
                chip.configure(text=tr("Noch nicht erstellt: ") + ", ".join(FORMAT_LABELS[k] for k in missing_formats()))
                chip.pack(side="right")
            else:
                chip.pack_forget()
    except Exception:
        pass
    _step4_footer()


def _refresh_tile_images():
    """Bild und Rahmen jeder vorhandenen Kachel an den aktuellen Plan anpassen (ohne Neuaufbau)."""
    if plan is None:
        return
    band = shell.get("render_band")
    if band is not None:
        try:
            band.refresh_images()
        except Exception:
            pass
    tw = shell.get("tile_widgets") or []
    tiles = plan_tiles(plan)
    if len(tiles) != len(tw):
        _build_tiles()
        return
    portrait = plan["is_shorts"]
    for i, (frm, hdr, pic, _old) in enumerate(tw):
        t = tiles[i]
        tw[i] = (frm, hdr, pic, t)
        try:
            if not frm.winfo_exists():
                continue
            img = _tile_image(t, portrait, render_busy and tile_state.get(i) != "done")
            if img is not None:
                pic.configure(image=img, text="")
            else:
                pic.configure(image=None, text="⏳")
            frm.configure(border_color=_tile_border(t, i), border_width=2)
        except Exception:
            pass


# ── Verschieben per Ziehen
def _tile_index_of(widget) -> Optional[int]:
    tw = shell.get("tile_widgets") or []
    w = widget
    for _ in range(6):
        if w is None:
            return None
        for i, (frm, hdr, pic, t) in enumerate(tw):
            if w is frm or w is hdr or w is pic:
                return i
        try:
            w = w.master
        except Exception:
            return None
    return None


def _tile_movable(i: Optional[int]) -> bool:
    tw = shell.get("tile_widgets") or []
    return i is not None and 0 <= i < len(tw) and tw[i][3]["type"] == "clip"


def _tile_press(i: int):
    _drag["src"] = i
    _drag["hl"] = None
    try:
        app.configure(cursor="fleur")
    except Exception:
        pass


def _tile_motion(event):
    if _drag["src"] is None:
        return
    tgt = _tile_index_of(event.widget.winfo_containing(event.x_root, event.y_root))
    if tgt == _drag["src"] or not _tile_movable(tgt):
        tgt = None
    if tgt != _drag["hl"]:
        tw = shell.get("tile_widgets") or []
        if _drag["hl"] is not None and _drag["hl"] < len(tw):
            frm, _h, _p, t = tw[_drag["hl"]]
            frm.configure(border_color=_tile_border(t, _drag["hl"]), border_width=2)
        if tgt is not None:
            tw[tgt][0].configure(border_color=ACCENT, border_width=3)
        _drag["hl"] = tgt


def _tile_release(event):
    src = _drag["src"]
    _drag["src"] = None
    _drag["hl"] = None
    try:
        app.configure(cursor="")
    except Exception:
        pass
    if src is None:
        return
    tgt = _tile_index_of(event.widget.winfo_containing(event.x_root, event.y_root))
    tw = shell.get("tile_widgets") or []
    if tgt is None or tgt == src or not _tile_movable(tgt) or not _tile_movable(src):
        for i, (frm, _h, _p, t) in enumerate(tw):
            try:
                frm.configure(border_color=_tile_border(t, i), border_width=2)
            except Exception:
                pass
        return
    if plan_swap(plan, tw[src][3]["slot"], tw[tgt][3]["slot"]):
        ui_status(tr("Clips verschoben"))
        _after_plan_edit()


def _global_release(event):
    """Sicherheitsnetz: Ziehen außerhalb einer Kachel losgelassen."""
    if _drag["src"] is not None:
        _tile_release(event)


# ── Rendern
def do_render():
    """„Video erstellen“ bzw. „Änderungen übernehmen“: rendert den Plan (nur Geändertes neu)."""
    global render_busy, render_failed, render_rows
    if render_busy or plan is None:
        return
    if plan_sig != current_plan_signature():
        enter_step4()                     # Einstellungen geändert -> erst neu planen
        return
    _sync_formats()
    fmts = selected_formats()
    first = plan_rendered_version < 0     # erster Render dieser Clipfolge: alle Formate
    if first:
        todo = list(fmts)
        has_main = True
    else:                                 # danach: Hauptvideo nur bei Änderungen, dazu neu angekreuzte Formate
        miss = missing_formats()
        has_main = plan_dirty() or fmts[0] in miss
        todo = ([fmts[0]] if has_main else []) + [k for k in miss if k != fmts[0]]
        if not todo:
            return
    jobs = [(k, get_output_path_from_music(music_file, k)) for k in todo]
    import copy
    main_plan = copy.deepcopy(plan)
    version = plan["version"]
    use_title = bool(title_var.get())
    title_text = get_title_from_music(music_file) if use_title else ""
    settings = dict(use_intro=bool(intro_var.get()), use_outro=bool(outro_var.get()),
                    use_vorspann=bool(vorspann_var.get()), speed=speed_var.get())
    media = [{"kind": it["kind"], "path": str(it["path"])} for it in selection]

    render_busy = True
    render_failed = None
    tile_state.clear()
    render_rows = [{"fmt": k, "name": p.name, "state": "wait"} for k, p in jobs]
    shell["_last_progress"] = 0.0
    shell["_render_t0"] = time.time()
    shell["_eta"] = None
    # Liste der Arbeitsschritte
    mp = main_plan
    rows = []
    if has_main and mp.get("vorspann_src") and Path(mp["vorspann_src"]).exists():
        rows.append(_prog_row("pre", "Pre-Intro"))
    if has_main:
        if mp.get("intro_src") and Path(mp["intro_src"]).exists() and mp["intro_len"] > 0:
            rows.append(_prog_row("intro", "Intro"))
        rows.append(_prog_row("clips", "Loop-Durchläufe" if mp.get("mode") == "loop" else "Clips rendern"))
        if mp.get("outro_src") and Path(mp["outro_src"]).exists():
            rows.append(_prog_row("outro", "Outro"))
        rows.append(_prog_row("concat", "Abschnitte zusammenfügen"))
        rows.append(_prog_row("mux", "Musik und Titel einfügen" if (use_title and not mp["shorts_limited"]) else "Musik anlegen"))
    main_keys = [r["key"] for r in rows]
    for idx, (k, _p) in enumerate(jobs):
        if not (has_main and idx == 0):
            rows.append(_prog_row(f"fmt{idx}", FORMAT_LABELS[k]))
    render_prog.clear()
    render_prog.update({"rows": rows, "complete": False, "fired": True, "on_complete": None,
                        "extra_plan": None, "extra_state": {},
                        "clip_idx": [i for i, t in enumerate(plan_tiles(mp)) if t["type"] == "clip"]})
    refresh_page()
    ui_status(tr("Starte…"))
    n = len(jobs)

    def prog(p, idx):
        val = (idx + p) / n
        shell["_last_progress"] = val
        ui_progress(val)
        lbl = shell.get("render_pct")
        try:
            if lbl is not None and lbl.winfo_exists():
                lbl.configure(text=_pct_text(val, update=True))
        except Exception:
            pass
        if not (has_main and idx == 0):
            r = _prog_find(render_prog, f"fmt{idx}")
            if r is not None and r["state"] == "run":
                r["frac"] = max(0.0, min(1.0, p))
                r["detail"] = f"{int(round(p * 100))} %"

    def phase(name):
        if name == "done":
            _prog_finish(render_prog, main_keys)
        elif name == "clips":
            k = len(render_prog["clip_idx"])
            _prog_start(render_prog, "clips", frac=0.0, detail=f"0 / {k}")
        elif name in main_keys:
            _prog_start(render_prog, name)

    def show_extra(p):
        render_prog["extra_plan"] = p
        render_prog["extra_state"] = {}
        if render_busy and current_step == 4:
            refresh_page()

    def extra_images():
        band = shell.get("render_band")
        if band is not None:
            try:
                band.refresh_images()
            except Exception:
                pass

    def extra_seg(i, state):
        render_prog["extra_state"][i] = state
        band = shell.get("render_band")
        if band is not None:
            try:
                band.state_changed(i)
            except Exception:
                pass

    def set_row(idx, state):
        render_rows[idx]["state"] = state
        _refresh_render_rows()
        if has_main and idx == 0:
            if state == "done":
                _prog_finish(render_prog, main_keys)
            elif state == "fail":
                for r in render_prog["rows"]:
                    if r["key"] in main_keys and r["state"] == "run":
                        r["state"] = "fail"
            return
        key = f"fmt{idx}"
        if state == "run":
            _prog_start(render_prog, key, frac=0.0, detail="0 %")
        elif state == "done":
            r = _prog_find(render_prog, key)
            if r is not None:
                r["state"], r["detail"] = "done", tr("fertig")
        elif state == "fail":
            r = _prog_find(render_prog, key)
            if r is not None:
                r["state"], r["detail"] = "fail", tr("Fehler")

    def worker():
        failed = []
        outputs = []
        try:
            for idx, (fmt, out) in enumerate(jobs):
                prefix = f"[{idx + 1}/{n} · {FORMAT_LABELS[fmt]}]  " if n > 1 else ""
                app.after(0, set_row, idx, "run")
                try:
                    if has_main and idx == 0:
                        p = main_plan
                        execute_plan(p, out, title_overlay=use_title, title_text=title_text,
                                     status_cb=lambda t, pr=prefix: app.after(0, _render_status, t, pr),
                                     progress_cb=lambda v, i=idx: app.after(0, prog, v, i),
                                     seg_cb=lambda i, s: app.after(0, _set_tile_state, i, s),
                                     seg_dir=SEGMENTS_DIR, prune=True,
                                     phase_cb=lambda nm: app.after(0, phase, nm))
                    else:
                        p = plan_video(music_file, media, fmt, status_cb=lambda t, pr=prefix: app.after(0, ui_status, pr + t),
                                       **settings)
                        app.after(0, show_extra, p)
                        for t in plan_tiles(p):     # Bilder fürs Laufband
                            _tile_pil(t, p["is_shorts"])
                        app.after(0, extra_images)
                        execute_plan(p, out, title_overlay=use_title, title_text=title_text,
                                     status_cb=lambda t, pr=prefix: app.after(0, _render_status, t, pr),
                                     progress_cb=lambda v, i=idx: app.after(0, prog, v, i),
                                     seg_cb=lambda i, s: app.after(0, extra_seg, i, s))
                    outputs.append((fmt, out))
                    app.after(0, set_row, idx, "done")
                except Exception as e:
                    app.after(0, set_row, idx, "fail")
                    if has_main and idx == 0:
                        raise
                    failed.append(f"{FORMAT_LABELS[fmt]}: {e}")
            app.after(0, play_done_sound)
            app.after(0, lambda: _render_done(outputs, failed, version, main_plan, first, has_main))
        except Exception as e:
            msg = str(e)
            app.after(0, lambda: _render_error(msg))

    threading.Thread(target=worker, daemon=True).start()


def _render_status(t: str, prefix: str = ""):
    """Statuszeile; beim Vorbereiten eines Clips steht der Fortschritt auch in der Zeile „Clips rendern“."""
    ui_status(prefix + t)
    head = tr("Clip wird vorbereitet: ")
    if t.startswith(head):
        r = _prog_find(render_prog, "clips")
        if r is not None and r["state"] in ("wait", "run"):
            pct = t.rsplit("·", 1)[1].strip() if "·" in t else ""
            r["detail"] = (tr("vorbereiten") + " " + pct).strip()


def _render_done(outputs, failed, version, rendered_plan, first, has_main=True):
    global render_busy, plan_rendered_version, last_outputs, last_failed_formats, _rendered_slots_backup
    import copy
    render_busy = False
    if has_main:
        plan_rendered_version = version
        _rendered_slots_backup = copy.deepcopy(rendered_plan["slots"])
    if first:
        last_outputs = outputs
        last_failed_formats = failed
    else:                                     # neue Dateien zu den vorhandenen, Reihenfolge wie die Formate
        d = dict(last_outputs)
        d.update(dict(outputs))
        last_outputs = [(k, d[k]) for k in FORMAT_ORDER if k in d]
        last_failed_formats = failed
    _sync_formats()
    if has_main:
        thumb_state.update({"cands": [], "idx": 0, "saved": None, "busy": False, "style": None, "base": None, "final": None})
    go(5)
    names = " + ".join(p.name for _f, p in outputs)
    ui_status(tr("Fertig: ") + names + ((tr("  —  Fehler bei ") + "; ".join(failed)) if failed else ""))
    if has_main:
        start_thumb_candidates(rendered_plan)


def _render_error(msg: str):
    global render_busy, render_failed
    render_busy = False
    render_failed = msg
    ui_status(tr("Fehler: ") + msg)
    refresh_page()


def _refresh_render_rows():
    rows = shell.get("render_rows")
    try:
        if rows is None or not rows.winfo_exists():
            return
    except Exception:
        return
    for w in rows.winfo_children():
        w.destroy()
    if len(render_rows) <= 1:
        return
    icon = {"wait": ("○", C_MUTED), "run": ("●", ACCENT), "done": ("✓", C_OK), "fail": ("✕", C_DANGER_TX)}
    state_txt = {"wait": "wartet", "run": "läuft …", "done": "fertig", "fail": "Fehler"}
    for job in render_rows:
        r = ctk.CTkFrame(rows, fg_color="transparent")
        r.pack(side="left", padx=(0, 22))
        ic, col = icon[job["state"]]
        ctk.CTkLabel(r, text=ic, font=F(13, True), text_color=col, width=16).pack(side="left")
        ctk.CTkLabel(r, text=f"{FORMAT_LABELS[job['fmt']]} – {tr(state_txt[job['state']])}", font=F(11, True),
                     text_color=col).pack(side="left", padx=(4, 0))


# ===================== Schritt 5: Fertig =====================
thumb_state: dict = {"cands": [], "idx": 0, "saved": None, "busy": False, "style": None, "base": None, "final": None}
PREVIEW_L = (480, 270)
PREVIEW_P = (180, 320)


def thumbnail_path() -> Optional[Path]:
    if not last_outputs or last_outputs[0][0] != "normal":
        return None
    out = last_outputs[0][1]
    return out.with_name(out.stem + "_Thumbnail.jpg")


def _score_frame(img) -> float:
    """Bevorzugt farbige, helle, klar gezeichnete Bilder – keine Schwarzbilder, kein Bildrauschen."""
    from PIL import ImageFilter, ImageStat
    small = img.convert("RGB").resize((160, 90)).filter(ImageFilter.GaussianBlur(1))
    g = small.convert("L")
    mean = ImageStat.Stat(g).mean[0]
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    sat = ImageStat.Stat(small.convert("HSV").split()[1]).mean[0] / 255.0
    bright = 1.0 - min(1.0, abs(mean - 130) / 130)
    detail = min(1.0, edges / 25.0)
    return (0.55 * sat + 0.45 * detail) * (0.3 + bright)


def start_thumb_candidates(rendered_plan: dict):
    """Standbilder aus dem fertigen Hauptvideo holen, nach Qualität sortieren, bestes als Thumbnail speichern."""
    if not last_outputs:
        return
    fmt, out = last_outputs[0]
    portrait = fmt != "normal"
    thumb_state.update({"cands": [], "idx": 0, "saved": None, "busy": True, "style": None, "base": None, "final": None})
    pre = media_duration(rendered_plan["vorspann_src"]) if rendered_plan.get("vorspann_src") else 0.0
    slots = rendered_plan["slots"]
    times = [pre + s["start"] + s["dur"] / 2 for s in slots]
    if len(times) > 14:
        step = len(times) / 14
        times = [times[int(i * step)] for i in range(14)]
    size = (1280, 720) if not portrait else (360, 640)

    def worker():
        cands = []
        for t in times:
            img = _frame_pil(str(out), t, size[0], size[1], "video")
            if img is not None:
                cands.append((_score_frame(img), img))
        cands.sort(key=lambda x: -x[0])
        imgs = [c[1] for c in cands]
        app.after(0, lambda: _thumbs_ready(imgs))

    threading.Thread(target=worker, daemon=True).start()


def _thumbs_ready(imgs):
    thumb_state["cands"] = imgs
    thumb_state["idx"] = 0
    thumb_state["busy"] = False
    st_ = load_settings()
    tpl = _thumb_template()
    if st_.get("thumb_auto") and tpl:             # Vorlage automatisch anwenden (Titel = Songname)
        style = dict(THUMB_DEFAULT)
        style.update(tpl)
        style["text1"] = get_title_from_music(music_file) if music_file else ""
        thumb_state["style"] = style
    _save_thumbnail()
    if current_step == 5:
        _refresh_thumb_view()


def _save_thumbnail():
    p = thumbnail_path()
    base = _thumb_current_base()
    if p is None or base is None:
        thumb_state["saved"] = None
        return
    try:
        img = compose_thumb(base, thumb_state["style"]) if thumb_state.get("style") else base.convert("RGB")
        thumb_state["final"] = img
        q = 92
        while True:                                # YouTube erlaubt höchstens 2 MB
            img.save(p, "JPEG", quality=q, optimize=True)
            if p.stat().st_size <= 1_950_000 or q <= 60:
                break
            q -= 8
        thumb_state["saved"] = p
    except Exception as e:
        thumb_state["saved"] = None
        ui_status(tr("Thumbnail konnte nicht gespeichert werden: ") + str(e))


def next_thumbnail():
    if len(thumb_state["cands"]) < 2:
        return
    thumb_state["idx"] = (thumb_state["idx"] + 1) % len(thumb_state["cands"])
    thumb_state["base"] = None                     # Text/Stil bleiben, nur das Bild wechselt
    _save_thumbnail()
    _refresh_thumb_view()
    if thumb_state["saved"]:
        ui_status(tr("Thumbnail gespeichert: ") + thumb_state["saved"].name)


def _refresh_thumb_view():
    """Vorschaubild + Hinweis auf der Fertig-Seite aktualisieren, ohne die Seite neu aufzubauen."""
    lbl = shell.get("thumb_label")
    try:
        if lbl is None or not lbl.winfo_exists():
            return
    except Exception:
        return
    pw, ph = shell.get("thumb_size", PREVIEW_L)
    if thumb_state["cands"]:
        pil = (thumb_state.get("final") or thumb_state["cands"][thumb_state["idx"]]).copy()
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(pw, ph))
        shell["_thumb_img"] = img                 # Referenz halten
        lbl.configure(image=img, text="")
    info = shell.get("thumb_info")
    nb = shell.get("thumb_next")
    if info is not None:
        if thumb_state["busy"] or not thumb_state["cands"]:
            txt = tr("YouTube-Thumbnail wird erstellt …")
        elif thumb_state["saved"]:
            txt = tr("Gespeichert: ") + thumb_state["saved"].name
        else:
            txt = tr("Thumbnail konnte nicht gespeichert werden: ") + "–"
        info.configure(text=txt)
        nb.configure(state="normal" if len(thumb_state["cands"]) > 1 else "disabled")
        eb = shell.get("thumb_edit")
        if eb is not None:
            try:
                eb.configure(state="normal" if thumb_state["cands"] else "disabled")
            except Exception:
                pass


# ── Thumbnail-Werkzeugpalette („✎ Bearbeiten“): Bild, Text, Logo, Vorlage
THUMB_FONTS = [            # (Name, Windows-Datei, Ersatz unter Linux zum Testen)
    ("Impact", "impact.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
    ("Arial Black", "ariblk.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("Segoe UI Black", "seguibl.ttf", "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"),
    ("Bahnschrift", "bahnschrift.ttf", "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf"),
    ("Trebuchet", "trebucbd.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("Georgia", "georgiab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ("Segoe Script", "segoescb.ttf", "/usr/share/fonts/truetype/freefont/FreeSerifBoldItalic.ttf"),
]
THUMB_COLORS = ["#ffffff", "#ffd400", "#ff8a00", "#ff2d55", "#1f8fe5", "#29d17f", "#000000"]
THUMB_DEFAULT = {"bright": 1.0, "contrast": 1.0, "sat": 1.0, "dark": 0.35,
                 "text1": "", "text2": "", "font": "Impact", "size": 120, "upper": True,
                 "color": "#ffffff", "stroke": "#000000", "stroke_w": 6, "shadow": True, "pos": "bc",
                 "logo": "", "logo_corner": "tr", "logo_size": 14,
                 "tx": 0.5, "ty": 0.8, "lx": 0.9, "ly": 0.12}       # freie Position (Mitte, Anteil der Bildgröße)
_FONT_CACHE: dict = {}


def _thumb_font_path(name: str) -> Optional[str]:
    win = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    for n, f, alt in THUMB_FONTS:
        if n == name:
            for c in (win / f, Path(alt)):
                if c.exists():
                    return str(c)
    return None


def thumb_fonts_available() -> list:
    return [n for n, _f, _a in THUMB_FONTS if _thumb_font_path(n)]


def _thumb_font(name: str, px: int):
    from PIL import ImageFont
    key = (name, px)
    if key not in _FONT_CACHE:
        p = _thumb_font_path(name)
        try:
            f = ImageFont.truetype(p, px) if p else _load_font(px, True)
            if p and p.lower().endswith("bahnschrift.ttf"):
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
        except Exception:
            f = _load_font(px, True)
        _FONT_CACHE[key] = f
    return _FONT_CACHE[key]


def compose_thumb(base, style: dict, size=(1280, 720), info: Optional[dict] = None):
    """Setzt das Thumbnail aus Grundbild und Stil zusammen (Bild-Regler, Abdunkeln, Text, Logo).
    info (optional) bekommt die Rahmen von Text und Logo – für das Verschieben mit der Maus."""
    from PIL import ImageEnhance, ImageDraw, ImageFilter, ImageOps
    st = dict(THUMB_DEFAULT)
    st.update(style or {})
    W, Hh = size
    k = W / 1280.0
    img = ImageOps.fit(base.convert("RGB"), (W, Hh))
    if st["bright"] != 1.0:
        img = ImageEnhance.Brightness(img).enhance(st["bright"])
    if st["contrast"] != 1.0:
        img = ImageEnhance.Contrast(img).enhance(st["contrast"])
    if st["sat"] != 1.0:
        img = ImageEnhance.Color(img).enhance(st["sat"])
    img = img.convert("RGBA")
    lines = [t for t in (st["text1"].strip(), st["text2"].strip()) if t]
    free = st["pos"] == "free"
    if free:                                          # frei verschoben: Verlauf richtet sich nach der Höhe
        vpos = "t" if st["ty"] < 0.34 else ("b" if st["ty"] > 0.66 else "m")
        hpos = "c"
    else:
        vpos, hpos = st["pos"][0], st["pos"][1]      # t/m/b und l/c/r
    # weicher Verlauf hinter dem Text
    if st["dark"] > 0 and lines:
        grad = Image.new("L", (1, 256))
        for y in range(256):
            if vpos == "t":
                a = 1 - y / 255
            elif vpos == "b":
                a = y / 255
            else:
                a = 1 - abs(y - 127.5) / 127.5
            grad.putpixel((0, y), int(255 * st["dark"] * (a ** 1.4)))
        mask = grad.resize((W, Hh))
        black = Image.new("RGBA", (W, Hh), (0, 0, 0, 255))
        black.putalpha(mask)
        img = Image.alpha_composite(img, black)
    # Text
    if lines:
        texts = [l.upper() if st["upper"] else l for l in lines]
        s1 = max(10, int(st["size"] * k))
        sizes = [s1] + ([max(8, int(s1 * 0.5))] if len(texts) > 1 else [])
        maxw = W * 0.9
        meas = ImageDraw.Draw(img)
        fonts = []
        for t_, sz in zip(texts, sizes):
            f = _thumb_font(st["font"], sz)
            while sz > 10 and meas.textlength(t_, font=f) > maxw:     # zu breit -> kleiner
                sz = int(sz * 0.92)
                f = _thumb_font(st["font"], sz)
            fonts.append(f)
        sw = int(st["stroke_w"] * k * (sizes[0] / max(1, s1)))
        boxes = [meas.textbbox((0, 0), t_, font=f, stroke_width=sw) for t_, f in zip(texts, fonts)]
        heights = [b[3] - b[1] for b in boxes]
        widths = [b[2] - b[0] for b in boxes]
        gap = int(10 * k)
        total_h = sum(heights) + gap * (len(texts) - 1)
        margin = int(W * 0.05)
        if free:
            y = int(st["ty"] * Hh - total_h / 2)
            y = max(0, min(Hh - total_h, y))
        else:
            y = margin if vpos == "t" else (Hh - margin - total_h if vpos == "b" else (Hh - total_h) // 2)
        y_top = y
        x_min, x_max = W, 0
        layer = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        ds = ImageDraw.Draw(shadow)
        for t_, f, b, h_, w_ in zip(texts, fonts, boxes, heights, widths):
            if free:
                x = int(st["tx"] * W - w_ / 2)
                x = max(0, min(W - w_, x))
            else:
                x = margin if hpos == "l" else (W - margin - w_ if hpos == "r" else (W - w_) // 2)
            x_min, x_max = min(x_min, x), max(x_max, x + w_)
            if st["shadow"]:
                off = int(6 * k)
                ds.text((x - b[0] + off, y - b[1] + off), t_, font=f, fill=(0, 0, 0, 170),
                        stroke_width=sw, stroke_fill=(0, 0, 0, 170))
            d.text((x - b[0], y - b[1]), t_, font=f, fill=st["color"],
                   stroke_width=sw, stroke_fill=st["stroke"])
            y += h_ + gap
        if st["shadow"]:
            img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(max(1, int(5 * k)))))
        img = Image.alpha_composite(img, layer)
        if info is not None:
            info["text"] = (x_min, y_top, x_max, y_top + total_h)
    # Logo
    if st["logo"] and Path(st["logo"]).exists():
        try:
            lg = Image.open(st["logo"]).convert("RGBA")
            lw = max(8, int(W * st["logo_size"] / 100))
            lh = max(1, int(lg.height * lw / lg.width))
            lg = lg.resize((lw, lh), Image.LANCZOS)
            m = int(W * 0.03)
            c = st["logo_corner"]
            if c == "free":
                lx = max(0, min(W - lw, int(st["lx"] * W - lw / 2)))
                ly = max(0, min(Hh - lh, int(st["ly"] * Hh - lh / 2)))
            else:
                lx = m if c[1] == "l" else W - m - lw
                ly = m if c[0] == "t" else Hh - m - lh
            img.alpha_composite(lg, (lx, ly))
            if info is not None:
                info["logo"] = (lx, ly, lx + lw, ly + lh)
        except Exception:
            pass
    return img.convert("RGB")


def _thumb_current_base():
    if thumb_state.get("base") is not None:
        return thumb_state["base"]
    if thumb_state["cands"]:
        return thumb_state["cands"][thumb_state["idx"]]
    return None


def _thumb_template() -> Optional[dict]:
    t = load_settings().get("thumb_template")
    return dict(t) if isinstance(t, dict) else None


def open_thumb_editor():
    """Werkzeugpalette: großes Vorschaubild links, Werkzeuge rechts. „Übernehmen“ speichert das Thumbnail."""
    base0 = _thumb_current_base()
    out = last_outputs[0][1] if last_outputs else None
    if base0 is None or out is None or not out.exists():
        ui_status(tr("YouTube-Thumbnail wird erstellt …"))
        return
    title = get_title_from_music(music_file) if music_file else out.stem
    if thumb_state.get("style"):
        style = dict(thumb_state["style"])
    else:
        style = dict(THUMB_DEFAULT)
        tpl = _thumb_template()
        if tpl:
            style.update(tpl)
        style["text1"] = title
    fonts = thumb_fonts_available() or ["Impact"]
    if style["font"] not in fonts:
        style["font"] = fonts[0]
    cur = {"base": base0, "style": style, "job": None}
    vid_dur = media_duration(out)

    win = ctk.CTkToplevel(app)
    win.title(tr("Thumbnail bearbeiten"))
    W_, H_ = 1180, 700
    try:
        app.update_idletasks()
        win.geometry(f"{W_}x{H_}+{app.winfo_x() + max(0, (app.winfo_width() - W_) // 2)}+{app.winfo_y() + 20}")
    except Exception:
        win.geometry(f"{W_}x{H_}")
    win.transient(app)
    win.grab_set()
    win.configure(fg_color=C_BG)
    win.grid_columnconfigure(0, weight=1)
    win.grid_columnconfigure(1, weight=0)
    win.grid_rowconfigure(0, weight=1)

    # ── links: Vorschau + Bildwahl
    left = card(win)
    left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=(16, 8))
    ctk.CTkLabel(left, text=tr("Vorschau"), font=F(15, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=20, pady=(14, 6))
    PW, PH = 704, 396
    prev = ctk.CTkLabel(left, text="", width=PW, height=PH, fg_color=C_BORDER, corner_radius=8)
    prev.pack(padx=20)
    H(prev, "te_preview")
    ctk.CTkLabel(left, text=tr("Tipp: Text und Logo lassen sich in der Vorschau mit der Maus verschieben."),
                 font=F(11), text_color=C_MUTED, anchor="w").pack(fill="x", padx=20, pady=(4, 0))
    fr = ctk.CTkFrame(left, fg_color="transparent")
    fr.pack(fill="x", padx=20, pady=(14, 4))
    H(fr, "te_frame")
    ctk.CTkLabel(fr, text=tr("Bild aus dem Video"), font=F(13, True), text_color=C_TEXT).pack(side="left")
    tlab = ctk.CTkLabel(fr, text="", font=F(12), text_color=C_MUTED)
    tlab.pack(side="right")
    slider = ctk.CTkSlider(left, from_=0, to=max(0.1, vid_dur - 0.1), number_of_steps=max(10, int(vid_dur * 4)),
                           progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER)
    slider.set(0)
    slider.pack(fill="x", padx=20)
    H(slider, "te_frame")
    sug = ctk.CTkFrame(left, fg_color="transparent")
    sug.pack(fill="x", padx=20, pady=(8, 14))
    ctk.CTkLabel(sug, text=tr("Oder einen Vorschlag des Programms:"), font=F(12), text_color=C_MUTED).pack(side="left")

    def render_preview():
        cur["job"] = None
        try:
            boxes = {}
            img = compose_thumb(cur["base"], cur["style"], (PW, PH), boxes)
            cur["boxes"] = boxes
            ci = ctk.CTkImage(light_image=img, dark_image=img, size=(PW, PH))
            prev.configure(image=ci)
            prev._mvg_img = ci
        except Exception as e:
            ui_status(str(e))

    def schedule():
        if cur["job"] is None:
            cur["job"] = win.after(60, render_preview)

    def set_base(pil):
        if pil is not None:
            cur["base"] = pil
            schedule()

    # Text und Logo mit der Maus verschieben
    drag = {"what": None, "dx": 0.0, "dy": 0.0}

    def _frac(e):
        w_, h_ = max(1, prev.winfo_width()), max(1, prev.winfo_height())
        return e.x / w_, e.y / h_

    def _hit(fx, fy):
        px, py = fx * PW, fy * PH
        for what in ("logo", "text"):
            b_ = cur.get("boxes", {}).get(what)
            if b_ and b_[0] - 6 <= px <= b_[2] + 6 and b_[1] - 6 <= py <= b_[3] + 6:
                return what, b_
        return None, None

    def on_press(e):
        fx, fy = _frac(e)
        what, b_ = _hit(fx, fy)
        drag["what"] = what
        if what:
            cx, cy = (b_[0] + b_[2]) / 2 / PW, (b_[1] + b_[3]) / 2 / PH
            drag["dx"], drag["dy"] = fx - cx, fy - cy

    def on_motion(e):
        what = drag["what"]
        if not what:
            return
        fx, fy = _frac(e)
        nx, ny = min(1.0, max(0.0, fx - drag["dx"])), min(1.0, max(0.0, fy - drag["dy"]))
        if what == "text":
            cur["style"]["tx"], cur["style"]["ty"] = round(nx, 4), round(ny, 4)
            if cur["style"]["pos"] != "free":
                cur["style"]["pos"] = "free"
                mark_pos()
        else:
            cur["style"]["lx"], cur["style"]["ly"] = round(nx, 4), round(ny, 4)
            if cur["style"]["logo_corner"] != "free":
                cur["style"]["logo_corner"] = "free"
                try:
                    cm.set(tr("frei verschoben"))
                except Exception:
                    pass
        schedule()

    def on_release(_e):
        drag["what"] = None

    def on_hover(e):
        what, _b = _hit(*_frac(e))
        try:
            prev.configure(cursor="fleur" if what else "")
        except Exception:
            pass
    for ev, fn in (("<ButtonPress-1>", on_press), ("<B1-Motion>", on_motion), ("<ButtonRelease-1>", on_release),
                   ("<Motion>", on_hover)):
        prev.bind(ev, fn)

    frame_req = {"t": None, "busy": False}

    def load_frame():
        t = frame_req["t"]
        frame_req["busy"] = True
        pil = _frame_pil_raw(str(out), t, 1280, 720, "video")

        def done():
            frame_req["busy"] = False
            set_base(pil)
            if frame_req["t"] != t:          # Regler wurde weiterbewegt
                threading.Thread(target=load_frame, daemon=True).start()
        ui_call(done)

    def on_slider(v):
        frame_req["t"] = float(v)
        tlab.configure(text=fmt_time(float(v)) + f".{int((float(v) % 1) * 10)}")
        if not frame_req["busy"]:
            threading.Thread(target=load_frame, daemon=True).start()
    slider.configure(command=on_slider)

    def next_suggestion():
        if thumb_state["cands"]:
            thumb_state["idx"] = (thumb_state["idx"] + 1) % len(thumb_state["cands"])
            set_base(thumb_state["cands"][thumb_state["idx"]])
    bsug = btn_secondary(sug, "↻  " + tr("Anderes Bild"), next_suggestion, width=140, height=30)
    bsug.pack(side="left", padx=(10, 0))
    H(bsug, "te_frame")

    # ── rechts: Werkzeuge
    tools = ctk.CTkScrollableFrame(win, width=380, fg_color=C_CARD, corner_radius=14)
    tools.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=(16, 8))

    def section(title, key):
        ctk.CTkLabel(tools, text=title, font=F(14, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=12, pady=(14, 4))
        f = ctk.CTkFrame(tools, fg_color=C_SOFT, corner_radius=10)
        f.pack(fill="x", padx=8)
        H(f, key)
        return f

    def slider_row(parent, label, key, lo, hi, fmt=None, hkey=None):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=10, pady=(6, 0))
        lab = ctk.CTkLabel(r, text=label, font=F(12), text_color=C_TEXT, anchor="w", width=120)
        lab.pack(side="left")
        val = ctk.CTkLabel(r, text="", font=F(11), text_color=C_MUTED, width=44)
        val.pack(side="right")
        s_ = ctk.CTkSlider(r, from_=lo, to=hi, progress_color=ACCENT, button_color=ACCENT,
                           button_hover_color=ACCENT_HOVER, width=150)
        s_.set(cur["style"][key])
        s_.pack(side="right", padx=(6, 6))

        def ch(v, key=key):
            cur["style"][key] = round(float(v), 2) if hi <= 5 else int(float(v))
            val.configure(text=(fmt or "{:.0f}").format(cur["style"][key] * (100 if hi <= 5 else 1)))
            schedule()
        s_.configure(command=ch)
        val.configure(text=(fmt or "{:.0f}").format(cur["style"][key] * (100 if hi <= 5 else 1)))
        if hkey:
            for w_ in (r, lab, s_):
                H(w_, hkey)
        return s_

    def swatches(parent, key, hkey):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=10, pady=(6, 0))
        btns = []

        def pick(c):
            cur["style"][key] = c
            mark()
            schedule()

        def custom():
            from tkinter import colorchooser
            res = colorchooser.askcolor(color=cur["style"][key], parent=win)
            if res and res[1]:
                pick(res[1])

        def mark():
            for c_, b_ in btns:
                b_.configure(border_width=3 if c_.lower() == cur["style"][key].lower() else 1,
                             border_color=ACCENT if c_.lower() == cur["style"][key].lower() else C_BORDER)
        for c in THUMB_COLORS:
            b = ctk.CTkButton(r, text="", width=28, height=28, corner_radius=14, fg_color=c, hover_color=c,
                              border_width=1, border_color=C_BORDER, command=lambda c=c: pick(c))
            b.pack(side="left", padx=(0, 5))
            H(b, hkey)
            btns.append((c, b))
        bc = btn_secondary(r, "…", custom, width=34, height=28)
        bc.pack(side="left")
        H(bc, hkey)
        mark()

    # Bild
    sb = section(tr("Bild"), "te_image")
    slider_row(sb, tr("Helligkeit"), "bright", 0.5, 1.5, "{:.0f} %", "te_image")
    slider_row(sb, tr("Kontrast"), "contrast", 0.5, 1.5, "{:.0f} %", "te_image")
    slider_row(sb, tr("Farbsättigung"), "sat", 0.0, 2.0, "{:.0f} %", "te_image")
    slider_row(sb, tr("Abdunkeln hinter Text"), "dark", 0.0, 0.9, "{:.0f} %", "te_dark")
    ctk.CTkFrame(sb, height=8, fg_color="transparent").pack()

    # Text
    st_ = section(tr("Text"), "te_text")
    for key, ph in (("text1", tr("Zeile 1 (Titel)")), ("text2", tr("Zeile 2 (optional, z. B. Künstler)"))):
        e = ctk.CTkEntry(st_, placeholder_text=ph, font=F(13), height=34)
        if cur["style"][key]:
            e.insert(0, cur["style"][key])
        e.pack(fill="x", padx=10, pady=(8, 0))
        e.bind("<KeyRelease>", lambda _e, k_=key, w_=e: (cur["style"].__setitem__(k_, w_.get()), schedule()))
        H(e, "te_text")
    r = ctk.CTkFrame(st_, fg_color="transparent")
    r.pack(fill="x", padx=10, pady=(8, 0))
    ctk.CTkLabel(r, text=tr("Schrift"), font=F(12), text_color=C_TEXT, width=120, anchor="w").pack(side="left")
    fm = ctk.CTkOptionMenu(r, values=fonts, font=F(12), width=190, fg_color=ACCENT, button_color=ACCENT_HOVER,
                           button_hover_color=ACCENT_HOVER,
                           command=lambda v: (cur["style"].__setitem__("font", v), schedule()))
    fm.set(cur["style"]["font"])
    fm.pack(side="right")
    H(fm, "te_font")
    slider_row(st_, tr("Größe"), "size", 40, 200, None, "te_font")
    up = ctk.CTkSwitch(st_, text=tr("GROSSBUCHSTABEN"), font=F(12), progress_color=ACCENT,
                       command=lambda: (cur["style"].__setitem__("upper", bool(up.get())), schedule()))
    if cur["style"]["upper"]:
        up.select()
    up.pack(anchor="w", padx=10, pady=(8, 0))
    H(up, "te_font")
    ctk.CTkLabel(st_, text=tr("Schriftfarbe"), font=F(12), text_color=C_TEXT, anchor="w").pack(fill="x", padx=10, pady=(10, 0))
    swatches(st_, "color", "te_color")
    ctk.CTkLabel(st_, text=tr("Kontur"), font=F(12), text_color=C_TEXT, anchor="w").pack(fill="x", padx=10, pady=(10, 0))
    swatches(st_, "stroke", "te_color")
    slider_row(st_, tr("Konturstärke"), "stroke_w", 0, 16, None, "te_color")
    sh = ctk.CTkSwitch(st_, text=tr("Schatten"), font=F(12), progress_color=ACCENT,
                       command=lambda: (cur["style"].__setitem__("shadow", bool(sh.get())), schedule()))
    if cur["style"]["shadow"]:
        sh.select()
    sh.pack(anchor="w", padx=10, pady=(8, 0))
    H(sh, "te_color")
    ctk.CTkLabel(st_, text=tr("Position"), font=F(12), text_color=C_TEXT, anchor="w").pack(fill="x", padx=10, pady=(10, 0))
    pg = ctk.CTkFrame(st_, fg_color="transparent")
    pg.pack(anchor="w", padx=10, pady=(4, 10))
    H(pg, "te_pos")
    pos_btns = {}

    def mark_pos():
        for k_, b_ in pos_btns.items():
            on = k_ == cur["style"]["pos"]
            b_.configure(fg_color=ACCENT if on else C_CARD, text_color="white" if on else C_MUTED)

    def set_pos(p_):
        cur["style"]["pos"] = p_
        mark_pos()
        schedule()
    for ri, v_ in enumerate("tmb"):
        for ci, h_ in enumerate("lcr"):
            b = ctk.CTkButton(pg, text="●", width=40, height=28, corner_radius=6, font=F(10),
                              fg_color=C_CARD, hover_color=C_SOFT_HOVER, text_color=C_MUTED, border_width=1,
                              border_color=C_BORDER, command=lambda p_=v_ + h_: set_pos(p_))
            b.grid(row=ri, column=ci, padx=2, pady=2)
            H(b, "te_pos")
            pos_btns[v_ + h_] = b
    mark_pos()

    # Logo
    sl_ = section(tr("Logo"), "te_logo")
    lr = ctk.CTkFrame(sl_, fg_color="transparent")
    lr.pack(fill="x", padx=10, pady=(8, 0))
    lname = ctk.CTkLabel(lr, text="", font=F(11), text_color=C_MUTED, anchor="w")

    def upd_logo_name():
        lname.configure(text=Path(cur["style"]["logo"]).name if cur["style"]["logo"] else tr("kein Logo"))

    def choose_logo():
        f = filedialog.askopenfilename(parent=win, title=tr("Logo wählen (PNG mit transparentem Hintergrund)"),
                                       initialdir=str(LOGO_DIR) if LOGO_DIR.exists() else None,
                                       filetypes=[("PNG", "*.png"), (tr("Bilder"), "*.png *.jpg *.jpeg *.webp")])
        if f:
            cur["style"]["logo"] = f
            upd_logo_name()
            schedule()

    def remove_logo():
        cur["style"]["logo"] = ""
        upd_logo_name()
        schedule()
    b1 = btn_secondary(lr, tr("Logo wählen …"), choose_logo, width=120, height=30)
    b1.pack(side="left")
    b2 = btn_secondary(lr, tr("Entfernen"), remove_logo, width=90, height=30)
    b2.pack(side="left", padx=6)
    for w_ in (b1, b2):
        H(w_, "te_logo")
    lname.pack(fill="x", padx=10, pady=(4, 0))
    upd_logo_name()
    cr = ctk.CTkFrame(sl_, fg_color="transparent")
    cr.pack(fill="x", padx=10, pady=(6, 0))
    ctk.CTkLabel(cr, text=tr("Ecke"), font=F(12), text_color=C_TEXT, width=120, anchor="w").pack(side="left")
    corners = {tr("oben links"): "tl", tr("oben rechts"): "tr", tr("unten links"): "bl", tr("unten rechts"): "br"}
    cm = ctk.CTkOptionMenu(cr, values=list(corners), font=F(12), width=190, fg_color=ACCENT, button_color=ACCENT_HOVER,
                           button_hover_color=ACCENT_HOVER,
                           command=lambda v: (cur["style"].__setitem__("logo_corner", corners[v]), schedule()))
    cm.set(tr("frei verschoben") if cur["style"]["logo_corner"] == "free"
           else next((k_ for k_, v_ in corners.items() if v_ == cur["style"]["logo_corner"]), tr("oben rechts")))
    cm.pack(side="right")
    H(cm, "te_logo")
    slider_row(sl_, tr("Logo-Größe"), "logo_size", 5, 35, "{:.0f} %", "te_logo")
    ctk.CTkFrame(sl_, height=8, fg_color="transparent").pack()

    # Vorlage
    sv = section(tr("Vorlage"), "te_template")

    def save_tpl():
        tpl = {k_: v_ for k_, v_ in cur["style"].items() if k_ != "text1"}
        s_ = load_settings()
        s_["thumb_template"] = tpl
        save_settings(s_)
        ui_status(tr("Vorlage gespeichert – sie wird beim nächsten Bearbeiten vorausgefüllt."))
        tinfo.configure(text=tr("Vorlage gespeichert."))

    def load_tpl():
        tpl = _thumb_template()
        if not tpl:
            tinfo.configure(text=tr("Noch keine Vorlage gespeichert."))
            return
        keep = cur["style"]["text1"]
        cur["style"].update(tpl)
        cur["style"]["text1"] = keep
        win.destroy()
        thumb_state["style"] = dict(cur["style"])
        thumb_state["base"] = cur["base"]
        open_thumb_editor()

    vr = ctk.CTkFrame(sv, fg_color="transparent")
    vr.pack(fill="x", padx=10, pady=(8, 0))
    bs = btn_secondary(vr, tr("Als Vorlage speichern"), save_tpl, width=170, height=30)
    bs.pack(side="left")
    bl = btn_secondary(vr, tr("Vorlage laden"), load_tpl, width=130, height=30)
    bl.pack(side="left", padx=6)
    for w_ in (bs, bl):
        H(w_, "te_template")
    auto_var = ctk.BooleanVar(value=bool(load_settings().get("thumb_auto")))

    def set_auto():
        s_ = load_settings()
        s_["thumb_auto"] = bool(auto_var.get())
        save_settings(s_)
    au = ctk.CTkSwitch(sv, text=tr("Vorlage automatisch auf neue Thumbnails anwenden"), variable=auto_var,
                       font=F(12), progress_color=ACCENT, command=set_auto)
    au.pack(anchor="w", padx=10, pady=(10, 0))
    H(au, "te_template")
    tinfo = ctk.CTkLabel(sv, text="", font=F(11), text_color=C_MUTED, anchor="w")
    tinfo.pack(fill="x", padx=10, pady=(4, 10))

    # ── unten: Knöpfe
    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))

    def reset():
        keep = cur["style"]["text1"]
        cur["style"].clear()
        cur["style"].update(THUMB_DEFAULT)
        cur["style"]["text1"] = keep
        cur["style"]["font"] = fonts[0]
        win.destroy()
        thumb_state["style"] = dict(cur["style"])
        thumb_state["base"] = cur["base"]
        open_thumb_editor()

    def no_text():
        win.destroy()
        thumb_state["style"] = None
        thumb_state["base"] = cur["base"]
        _save_thumbnail()
        _refresh_thumb_view()

    def apply():
        thumb_state["style"] = dict(cur["style"])
        thumb_state["base"] = cur["base"]
        win.destroy()
        _save_thumbnail()
        _refresh_thumb_view()
        if thumb_state["saved"]:
            ui_status(tr("Thumbnail gespeichert: ") + thumb_state["saved"].name)

    help_button(foot, win).pack(side="left", padx=(0, 8))
    br = btn_secondary(foot, tr("Zurücksetzen"), reset, width=130)
    br.pack(side="left")
    H(br, "te_buttons")
    bn = btn_secondary(foot, tr("Ohne Text"), no_text, width=120)
    bn.pack(side="left", padx=8)
    H(bn, "te_buttons")
    ba = btn_primary(foot, "✔  " + tr("Übernehmen"), apply, width=170, height=38)
    ba.pack(side="right")
    H(ba, "te_buttons")
    bc = btn_secondary(foot, tr("Abbrechen"), win.destroy, width=120)
    bc.pack(side="right", padx=8)
    H(bc, "te_buttons")
    win.bind("<Escape>", lambda _e: win.destroy())
    render_preview()


def build_step5(parent):
    if not last_outputs:
        go(4)
        return
    fmt, out = last_outputs[0]
    portrait = fmt != "normal"
    heading(parent, "✓  " + tr("Dein Video ist fertig!"),
            tr("Sieh es dir an, lade es hoch – oder ändere einzelne Clips."))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="both", expand=True)
    row.grid_columnconfigure(0, weight=3)
    row.grid_columnconfigure(1, weight=2)
    row.grid_rowconfigure(0, weight=1)

    left = card(row)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    H(left, "done_thumb" if not portrait else "done_preview")
    ctk.CTkLabel(left, text=tr("Vorschau") if portrait else tr("Thumbnail-Generator"), font=F(15, True),
                 text_color=C_TEXT, anchor="w").pack(fill="x", padx=22, pady=(16, 0))
    ctk.CTkLabel(left, text=tr("Standbild aus deinem Video – bei Shorts nimmt YouTube das Bild direkt aus dem Video.") if portrait
                 else tr("YouTube-Thumbnail (1280×720) aus deinem Video – wird automatisch im Output-Ordner gespeichert."),
                 font=F(11), text_color=C_MUTED, anchor="w", justify="left", wraplength=540).pack(fill="x", padx=22)
    pw, ph = PREVIEW_P if portrait else PREVIEW_L
    prev = ctk.CTkLabel(left, text="⏳", width=pw, height=ph, corner_radius=10, fg_color=C_BORDER, font=F(20))
    prev.pack(pady=(10, 8))
    shell["thumb_label"] = prev
    shell["thumb_size"] = (pw, ph)
    if not portrait:
        tl = ctk.CTkFrame(left, fg_color="transparent")
        tl.pack(pady=(0, 14))
        info = ctk.CTkLabel(tl, text="", font=F(11), text_color=C_MUTED)
        info.pack(side="left")
        nb = btn_secondary(tl, "↻  " + tr("Anderes Bild"), next_thumbnail, width=140, height=30)
        nb.pack(side="left", padx=(12, 0))
        H(nb, "done_thumb")
        teb = btn_secondary(tl, "✎  " + tr("Bearbeiten"), open_thumb_editor, width=130, height=30)
        teb.pack(side="left", padx=(8, 0))
        H(teb, "thumb_edit")
        shell["thumb_edit"] = teb
        shell["thumb_info"] = info
        shell["thumb_next"] = nb
    _refresh_thumb_view()

    right = card(row)
    right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    ctk.CTkLabel(right, text=tr("Deine Dateien"), font=F(15, True), text_color=C_TEXT, anchor="w").pack(fill="x", padx=22, pady=(18, 6))
    files = ctk.CTkFrame(right, fg_color="transparent")
    files.pack(fill="x", padx=22)
    H(files, "done_files")
    for f, p in last_outputs:
        r = ctk.CTkFrame(files, fg_color="transparent")
        r.pack(fill="x", pady=2)
        try:
            d = fmt_time(media_duration(p))
        except Exception:
            d = ""
        ctk.CTkLabel(r, text="✓", font=F(13, True), text_color=C_OK, width=18).pack(side="left")
        ctk.CTkLabel(r, text=f"{FORMAT_LABELS[f]}  ·  {d}", font=F(12, True), text_color=C_TEXT, width=120,
                     anchor="w").pack(side="left", padx=(4, 0))
        nm = p.name if len(p.name) <= 34 else p.name[:33] + "…"
        ctk.CTkLabel(r, text=nm, font=F(11), text_color=C_MUTED, anchor="w").pack(side="left")
    for fl in last_failed_formats:
        ctk.CTkLabel(files, text="✕ " + fl, font=F(11), text_color=C_DANGER_TX, anchor="w", justify="left",
                     wraplength=330).pack(fill="x", pady=2)

    is_loop = plan is not None and plan.get("mode") == "loop"
    ctk.CTkFrame(right, fg_color=C_BORDER, height=1).pack(fill="x", padx=22, pady=14)
    ed = ctk.CTkFrame(right, fg_color="transparent")
    if not is_loop:                     # beim Loop gibt es nichts zu verschieben oder zu tauschen
        ed.pack(fill="x", padx=22)
    H(ed, "done_edit")
    ctk.CTkLabel(ed, text=tr("Gefällt dir ein Clip nicht?"), font=F(13, True), text_color=C_TEXT, anchor="w").pack(fill="x")
    ctk.CTkLabel(ed, text=tr("Verschieben oder tauschen – gerendert wird danach nur, was sich geändert hat."),
                 font=F(11), text_color=C_MUTED, anchor="w", justify="left", wraplength=330).pack(fill="x", pady=(2, 8))
    eb = btn_secondary(ed, "✏  " + tr("Clips ändern"), lambda: go(4), width=170)
    eb.pack(anchor="w")
    H(eb, "done_edit")

    set_footer(left=[("＋  " + tr("Neues Video"), new_video, "secondary", True, "new_video")],
               right=[(tr("Output-Ordner"), open_output_folder, "secondary", True, "outfolder"),
                      (tr("YouTube Upload"), open_youtube_upload, "danger", True, "youtube"),
                      ("▶  " + tr("Video ansehen"), open_last_video, "primary", True, "watch")])


def new_video():
    """Zurück zu Schritt 1: Formate, Extras, Schnitt und Auswahl bleiben, Musik und Clipfolge werden geleert."""
    global music_file, music_dur, last_outputs, last_failed_formats
    if render_busy:
        return
    if plan_dirty() and not messagebox.askyesno(
            tr("Neues Video"), tr("Es gibt noch nicht übernommene Änderungen. Verwerfen und ein neues Video beginnen?")):
        return
    music_file = None
    music_dur = None
    _forget_plan()
    last_outputs = []
    last_failed_formats = []
    thumb_state.update({"cands": [], "idx": 0, "saved": None, "busy": False, "style": None, "base": None, "final": None})
    threading.Thread(target=clear_segments, daemon=True).start()   # Abschnitte des alten Videos braucht keiner mehr
    go(1)
    ui_status(tr("Neues Video: Einstellungen und Auswahl wurden behalten."))


def open_last_video():
    if last_outputs and last_outputs[0][1].exists():
        open_path_portable(last_outputs[0][1])
    else:
        ui_status(tr("kein Video vorhanden"))


def open_output_folder():
    open_path_portable(OUTPUT_DIR)


def open_youtube_upload(explicit_path: Optional[Path] = None):
    _open_url("https://www.youtube.com/upload")


# ===================== ⚙-Menü =====================
def show_settings_menu(widget=None):
    menu = tk.Menu(app, tearoff=0)
    busy_state = "disabled" if render_busy else "normal"
    menu.add_command(label=tr("Klick-Hilfe"), command=lambda: start_click_help())
    menu.add_command(label=tr("Kurzanleitung"), command=open_help)
    menu.add_command(label=tr("PDF-Anleitung"), command=open_pdf_manual)
    menu.add_separator()
    menu.add_command(label=tr("Short per URL (Zwischenablage)") + ("  …" if _url_short_busy else ""),
                     command=on_short_url_click)
    menu.add_command(label=tr("Pool-Ordner öffnen"), command=lambda: open_path_portable(POOL_DIR))
    menu.add_command(label=tr("Output-Ordner"), command=open_output_folder)
    menu.add_separator()
    menu.add_command(label=tr("Sprache / Language"), command=lambda: open_language_dialog(False), state=busy_state)
    look = tk.Menu(menu, tearoff=0)
    for key, label in (("system", "System"), ("light", "Hell"), ("dark", "Dunkel")):
        look.add_radiobutton(label=tr(label), value=key, variable=appearance_var,
                             command=lambda k=key: set_appearance(k))
    menu.add_cascade(label=tr("Darstellung"), menu=look)
    menu.add_command(label=tr("Cache leeren") + f"  ({_mb(_cache_size())})", command=clear_cache_clicked,
                     state="disabled" if (render_busy or plan_busy) else "normal")
    menu.add_command(label=tr("Einstellungen zurücksetzen"), command=on_reset_clicked, state=busy_state)
    menu.add_separator()
    menu.add_command(label=tr("Problem melden …"), command=create_problem_report)
    menu.add_command(label=tr("Mobile Version (GitHub)"), command=lambda: _open_url(MOBILE_URL))
    menu.add_command(label=tr("Über MVG …") + ("   ⬆" if UPDATE_INFO["state"] == "new" else ""),
                     command=open_about_dialog)
    try:
        if widget is not None:
            x = widget.winfo_rootx() + widget.winfo_width() - 260
            y = widget.winfo_rooty() + widget.winfo_height() + 4
        else:
            x, y = app.winfo_pointerx(), app.winfo_pointery()
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


# ===================== Über MVG · Lizenzen · Update-Hinweis =====================
REPO_URL = "https://github.com/loopweb/musik-video-generator"
HOME_URL = "https://loopweb.github.io/musik-video-generator/"
ISSUES_URL = REPO_URL + "/issues"
DONATE_URL = "https://www.paypal.com/paypalme/loopweb"
ORIGIN_URL = "https://donmiguel.nicepage.io/"
UPDATE_API = "https://api.github.com/repos/loopweb/musik-video-generator/releases/latest"
APP_COPYRIGHT = "© 2026 Michael Beyer (loopweb)"
UPDATE_INFO: dict = {"state": "unknown", "latest": "", "url": RELEASES_URL}   # unknown | checking | current | new | offline


def _ver_tuple(v: str) -> tuple:
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", v or "")[:4]) or (0,)


def check_for_update(on_done=None) -> None:
    """Fragt still bei GitHub nach der neuesten Version. Ohne Internet passiert einfach nichts."""
    if UPDATE_INFO["state"] == "checking":
        return
    UPDATE_INFO["state"] = "checking"

    def worker():
        import ssl
        import urllib.error
        import urllib.request
        contexts = [None]
        try:
            import certifi
            contexts.append(ssl.create_default_context(cafile=certifi.where()))
        except Exception:
            pass
        data = None
        for ctx in contexts:
            try:
                req = urllib.request.Request(UPDATE_API, headers={"User-Agent": "MVG/" + APP_VERSION,
                                                                  "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    data = _json.loads(r.read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as e:
                data = {} if e.code == 404 else None      # 404: noch kein Release veröffentlicht -> gilt als aktuell
                if data is not None:
                    break
            except Exception:
                data = None
        if data is None:
            UPDATE_INFO["state"] = "offline"
        else:
            tag = str(data.get("tag_name") or data.get("name") or "").strip()
            UPDATE_INFO["latest"] = tag.lstrip("vV")
            UPDATE_INFO["url"] = data.get("html_url") or RELEASES_URL
            UPDATE_INFO["state"] = "new" if tag and _ver_tuple(tag) > _ver_tuple(APP_VERSION) else "current"
        try:
            app.after(0, _update_ready, on_done)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _update_ready(on_done=None):
    _place_update_button()
    if on_done is not None:
        try:
            on_done()
        except Exception:
            pass


def _place_update_button():
    """Kleiner Hinweis oben in der Kopfzeile, wenn es eine neuere Version gibt."""
    head = shell.get("head")
    if UPDATE_INFO["state"] != "new" or head is None or shell.get("update_btn") is not None:
        return
    try:
        if not head.winfo_exists():
            return
        b = ctk.CTkButton(head, text="⬆  " + tr("Version {v} verfügbar").format(v=UPDATE_INFO["latest"]),
                          height=32, font=F(12, True), fg_color=C_WARN_BG, hover_color=C_SOFT_HOVER,
                          text_color=C_WARN_TX, corner_radius=16, command=lambda: _open_url(UPDATE_INFO["url"]))
        b.pack(side="right", padx=(0, 10))        # links neben „?“
        H(b, "update")
        shell["update_btn"] = b
    except Exception:
        pass


def _update_line() -> str:
    st = UPDATE_INFO["state"]
    if st == "new":
        return tr("Neue Version {v} verfügbar").format(v=UPDATE_INFO["latest"])
    if st == "current":
        return tr("Version {v} – aktuell").format(v=APP_VERSION)
    if st == "offline":
        return tr("Version {v} – keine Verbindung zur Update-Prüfung").format(v=APP_VERSION)
    return tr("Version {v} – wird geprüft …").format(v=APP_VERSION)


def _link_label(parent, text, url, size=12):
    lb = ctk.CTkLabel(parent, text=text, font=F(size), text_color=("#2f78b7", "#7fb3de"), cursor="hand2")
    lb.bind("<Button-1>", lambda _e: _open_url(url))
    return lb


def open_about_dialog():
    win = ctk.CTkToplevel(app)
    win.title(tr("Über MVG"))
    win.resizable(False, False)
    win.transient(app)
    win.configure(fg_color=C_BG)
    c = card(win)
    c.pack(fill="both", expand=True, padx=16, pady=16)
    top = ctk.CTkFrame(c, fg_color="transparent")
    top.pack(fill="x", padx=24, pady=(20, 6))
    try:
        _logo_widget(top).pack(side="left", padx=(0, 14))
    except Exception:
        pass
    tb = ctk.CTkFrame(top, fg_color="transparent")
    tb.pack(side="left")
    ctk.CTkLabel(tb, text=tr("Musik Video Generator") + " " + APP_VERSION, font=F(18, True), text_color=C_TEXT, anchor="w").pack(fill="x")
    ctk.CTkLabel(tb, text=APP_SLOGAN, font=F(12), text_color=("#2f78b7", "#7fb3de"), anchor="w").pack(fill="x")

    vrow = ctk.CTkFrame(c, fg_color=C_SOFT, corner_radius=10)
    vrow.pack(fill="x", padx=24, pady=(10, 4))
    vlab = ctk.CTkLabel(vrow, text=_update_line(), font=F(12, True), text_color=C_TEXT, anchor="w")
    vlab.pack(side="left", padx=14, pady=10)
    vbtn = btn_primary(vrow, tr("Herunterladen"), lambda: _open_url(UPDATE_INFO["url"]), width=130, height=30)

    def refresh_v():
        try:
            if not win.winfo_exists():
                return
            vlab.configure(text=_update_line(), text_color=C_WARN_TX if UPDATE_INFO["state"] == "new" else C_TEXT)
            if UPDATE_INFO["state"] == "new":
                vbtn.pack(side="right", padx=10, pady=8)
        except Exception:
            pass

    if UPDATE_INFO["state"] in ("unknown", "offline"):
        check_for_update(on_done=refresh_v)
    refresh_v()

    ctk.CTkLabel(c, text=APP_COPYRIGHT + "  ·  " + tr("MIT-Lizenz"), font=F(12), text_color=C_TEXT,
                 anchor="w").pack(fill="x", padx=24, pady=(10, 0))
    org = ctk.CTkFrame(c, fg_color="transparent")
    org.pack(fill="x", padx=24, pady=(2, 0))
    ctk.CTkLabel(org, text=tr("Ursprünglich entstanden für die Musikvideos von"), font=F(12),
                 text_color=C_MUTED).pack(side="left")
    _link_label(org, "Don Miguel de Cabarete", ORIGIN_URL).pack(side="left", padx=(4, 0))

    links = ctk.CTkFrame(c, fg_color="transparent")
    links.pack(fill="x", padx=21, pady=(16, 0))
    for i, (txt, url) in enumerate(((tr("Homepage"), HOME_URL), ("GitHub", REPO_URL),
                                    (tr("Fehler melden"), ISSUES_URL))):
        links.grid_columnconfigure(i, weight=1, uniform="lk")
        btn_secondary(links, txt, lambda u=url: _open_url(u), width=150).grid(row=0, column=i, sticky="ew", padx=3)
    row2 = ctk.CTkFrame(c, fg_color="transparent")
    row2.pack(fill="x", padx=21, pady=(6, 0))
    row2.grid_columnconfigure(0, weight=1, uniform="r2")
    row2.grid_columnconfigure(1, weight=1, uniform="r2")
    btn_secondary(row2, "☕  " + tr("MVG unterstützen"), lambda: _open_url(DONATE_URL), width=150).grid(
        row=0, column=0, sticky="ew", padx=3)
    btn_secondary(row2, tr("Lizenzen"), lambda: open_license_window(win), width=150).grid(
        row=0, column=1, sticky="ew", padx=3)
    ctk.CTkLabel(c, text=tr("MVG ist kostenlos. Wenn es dir gefällt, freue ich mich über einen Kaffee."),
                 font=F(11), text_color=C_MUTED, anchor="w", wraplength=470, justify="left").pack(fill="x", padx=24, pady=(8, 0))
    btn_primary(c, tr("Schließen"), win.destroy, width=130, height=34).pack(pady=(16, 20))
    win.bind("<Escape>", lambda _e: win.destroy())
    win.update_idletasks()
    w_, h_ = 560, win.winfo_reqheight()
    try:
        win.geometry(f"{w_}x{h_}+{app.winfo_rootx() + (app.winfo_width() - w_) // 2}+{app.winfo_rooty() + 80}")
    except Exception:
        win.geometry(f"{w_}x{h_}")
    try:
        win.grab_set()
    except Exception:
        pass


def _ffmpeg_info() -> tuple:
    """(Versionszeile, Lizenz) der mitgelieferten bzw. gefundenen FFmpeg-Version."""
    try:
        out = check_output_hidden([ffmpeg_cmd(), "-hide_banner", "-version"], timeout=10).decode("utf-8", "replace")
        first = out.splitlines()[0].split(" Copyright")[0].strip() if out else "FFmpeg"
        if "--enable-gpl" in out:
            lic = "GNU GPL v3" if "--enable-version3" in out else "GNU GPL v2+"
        else:
            lic = "GNU LGPL v3" if "--enable-version3" in out else "GNU LGPL v2.1+"
        return first, lic
    except Exception:
        return "FFmpeg", "GNU LGPL / GPL"


MIT_TEXT = """MIT License

Copyright (c) 2026 Michael Beyer (loopweb)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def license_text() -> str:
    ff_ver, ff_lic = _ffmpeg_info()
    parts = [
        "Musik Video Generator " + APP_VERSION,
        "",
        MIT_TEXT,
        "",
        "─" * 60,
        tr("Verwendete Programme und Bibliotheken"),
        "─" * 60,
        tr("MVG enthält bzw. nutzt die folgende freie Software. Sie gehört ihren jeweiligen Urhebern und steht unter deren eigenen Lizenzen:"),
        "",
        "• FFmpeg – " + ff_ver,
        "  " + tr("Lizenz") + ": " + ff_lic + "  ·  https://ffmpeg.org",
        "  " + tr("FFmpeg ist ein eigenständiges Programm, das MVG aufruft. Quellcode und Lizenztext: https://ffmpeg.org/download.html und https://ffmpeg.org/legal.html"),
        "• Python – PSF License  ·  https://www.python.org",
        "• Tcl/Tk (tkinter) – Tcl/Tk License (BSD)  ·  https://www.tcl.tk",
        "• CustomTkinter – MIT  ·  https://github.com/TomSchimansky/CustomTkinter",
        "• tkinterdnd2 – MIT  ·  https://github.com/Eliav2/tkinterdnd2",
        "• librosa – ISC  ·  https://librosa.org",
        "• NumPy – BSD-3-Clause  ·  https://numpy.org",
        "• Pillow – MIT-CMU (HPND)  ·  https://python-pillow.org",
        "• yt-dlp – Unlicense  ·  https://github.com/yt-dlp/yt-dlp",
        "• PyInstaller – GPL v2 " + tr("mit Ausnahme für erzeugte Programme") + "  ·  https://pyinstaller.org",
        "  " + tr("sowie deren Abhängigkeiten (z. B. SciPy, soundfile, numba), jeweils unter ihren eigenen freien Lizenzen."),
        "",
        tr("Die vollständigen Lizenztexte findest du auf den genannten Seiten."),
    ]
    return "\n".join(parts)


def open_license_window(parent=None):
    host = parent if parent is not None else app
    win = ctk.CTkToplevel(host)
    win.title(tr("Lizenzen"))
    win.transient(host)
    win.configure(fg_color=C_BG)
    w_, h_ = 680, 560
    try:
        win.geometry(f"{w_}x{h_}+{host.winfo_rootx() + (host.winfo_width() - w_) // 2}+{host.winfo_rooty() + 20}")
    except Exception:
        win.geometry(f"{w_}x{h_}")
    box = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=12), wrap="word", fg_color=C_CARD,
                         text_color=C_TEXT, border_width=1, border_color=C_BORDER, corner_radius=10)
    box.pack(fill="both", expand=True, padx=16, pady=(16, 8))
    box.insert("1.0", license_text())
    box.configure(state="disabled")
    btn_primary(win, tr("Schließen"), win.destroy, width=130, height=34).pack(pady=(0, 14))
    win.bind("<Escape>", lambda _e: win.destroy())
    try:
        win.grab_set()
    except Exception:
        pass


# ===================== Problem melden · Cache leeren =====================
def _desktop_dir() -> Path:
    """Desktop des Benutzers (auch wenn er von OneDrive umgeleitet ist); sonst der Output-Ordner."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and Path(buf.value).is_dir():
                return Path(buf.value)
        except Exception:
            pass
    d = Path.home() / "Desktop"
    return d if d.is_dir() else OUTPUT_DIR


def _mb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def _report_info() -> str:
    import platform
    import time as _t
    lines = ["Musik Video Generator " + APP_VERSION, "Erstellt: " + _t.strftime("%Y-%m-%d %H:%M:%S"), ""]
    try:
        lines.append("System: " + platform.platform() + " · " + platform.machine())
    except Exception:
        pass
    lines.append(f"Prozessorkerne: {os.cpu_count()}")
    if os.name == "nt":
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            lines.append(f"Arbeitsspeicher: {_mb(ms.ullTotalPhys)} (frei {_mb(ms.ullAvailPhys)})")
        except Exception:
            pass
    try:
        du = shutil.disk_usage(BASE_DIR)
        lines.append(f"Festplatte (Programmordner): frei {_mb(du.free)} von {_mb(du.total)}")
    except Exception:
        pass
    lines.append("Programmordner: " + str(BASE_DIR))
    try:
        ff, lic = _ffmpeg_info()
        lines.append("FFmpeg: " + ff + " (" + ffmpeg_cmd() + ")")
    except Exception:
        pass
    lines.append(f"Sprache: {LANG} · Darstellung: {ctk.get_appearance_mode()}")
    try:
        lines.append(f"Bildschirm: {app.winfo_screenwidth()}×{app.winfo_screenheight()} · Fenster: {app.winfo_width()}×{app.winfo_height()}")
    except Exception:
        pass
    lines.append(f"Cache: {_mb(_cache_size())}")
    lines += ["", f"Schritt: {current_step} · Render läuft: {render_busy} · Planung läuft: {plan_busy}"]
    if render_failed:
        lines.append("Letzter Renderfehler: " + str(render_failed))
    if plan_error:
        lines.append("Letzter Planungsfehler: " + str(plan_error))
    try:
        fm = [k for k, v in format_vars.items() if v.get()]
        lines.append(f"Formate: {', '.join(fm)} · Schnitt: {speed_var.get() or 'ganzer Clip'}")
    except Exception:
        pass
    if music_file:
        lines.append(f"Musik: {Path(music_file).name} · {fmt_time(music_dur)}")
    lines += ["", f"Auswahl ({len(selection)} Einträge):"]
    for it in selection[:40]:
        p = Path(it["path"])
        desc = f"  {it['kind']}: {p.name}"
        try:
            desc += f" · {_mb(p.stat().st_size)}"
            info = _json.loads(check_output_hidden([ffprobe_cmd(), "-v", "error", "-print_format", "json",
                                                   "-show_streams", "-show_format", str(p)], timeout=20)
                              .decode("utf-8", "replace"))
            v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
            desc += (f" · {v.get('codec_name', '?')} {v.get('width', '?')}×{v.get('height', '?')}"
                     f" · {v.get('avg_frame_rate', '?')} fps · {v.get('pix_fmt', '?')}"
                     f" · {float(info.get('format', {}).get('duration') or 0):.1f} s")
        except Exception as e:
            desc += f" · (nicht lesbar: {e.__class__.__name__})"
        lines.append(desc)
    if len(selection) > 40:
        lines.append(f"  … und {len(selection) - 40} weitere")
    return "\n".join(lines) + "\n"


def create_problem_report():
    """Packt Protokolle und technische Angaben in eine ZIP-Datei auf dem Desktop."""
    ui_status(tr("Fehlerbericht wird erstellt …"))

    def worker():
        import time as _t
        import zipfile
        try:
            info = _report_info()
            target = _desktop_dir() / f"MVG_{tr('Fehlerbericht').replace(' ', '_')}_{_t.strftime('%Y-%m-%d_%H%M')}.zip"
            base = _log_dir()
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("info.txt", info)
                for n_ in ("mvg_error.log", "mvg_start.log", "mvg_start_vorher.log", "mvg_ffmpeg.log",
                           "mvg_ffmpeg_vorher.log", "mvg_settings.json"):
                    f = base / n_
                    if f.exists():
                        z.write(f, n_)
            app.after(0, _report_done, target, None)
        except Exception as e:
            app.after(0, _report_done, None, str(e))

    threading.Thread(target=worker, daemon=True).start()


def _report_done(target, err):
    if err or target is None:
        ui_status(tr("Fehlerbericht konnte nicht erstellt werden"))
        messagebox.showerror(tr("Problem melden"), tr("Der Fehlerbericht konnte nicht erstellt werden.") + "\n\n" + str(err))
        return
    ui_status(tr("Fehlerbericht gespeichert: ") + target.name)
    w = ctk.CTkToplevel(app)
    w.title(tr("Problem melden"))
    w.transient(app)
    w.resizable(False, False)
    w.configure(fg_color=C_BG)
    c = card(w)
    c.pack(fill="both", expand=True, padx=14, pady=14)
    ctk.CTkLabel(c, text="✓  " + tr("Fehlerbericht erstellt"), font=F(16, True), text_color=C_OK,
                 anchor="w").pack(fill="x", padx=22, pady=(18, 4))
    ctk.CTkLabel(c, text=tr("Die Datei liegt auf deinem Desktop:") + "\n" + target.name + "\n\n" +
                 tr("Schick sie an den Entwickler – oder melde den Fehler auf GitHub."),
                 font=F(12), text_color=C_TEXT, anchor="w", justify="left", wraplength=440).pack(fill="x", padx=22, pady=(4, 0))
    bt = ctk.CTkFrame(c, fg_color="transparent")
    bt.pack(fill="x", padx=22, pady=(18, 18))

    def github():
        from urllib.parse import quote
        body = tr("Was ist passiert?") + "\n\n\n" + tr("MVG-Version: ") + APP_VERSION + "\n\n" + \
            tr("(Bitte die ZIP-Datei vom Desktop hier hineinziehen.)")
        _open_url(ISSUES_URL + "/new?title=" + quote(tr("Fehlerbericht")) + "&body=" + quote(body))
        open_path_portable(target.parent)

    btn_primary(bt, "OK", w.destroy, width=90, height=34).pack(side="right")
    btn_secondary(bt, tr("Auf GitHub melden"), github, width=160).pack(side="right", padx=8)
    w.bind("<Escape>", lambda _e: w.destroy())
    try:
        w.update_idletasks()
        w_, h_ = 500, w.winfo_reqheight()
        w.geometry(f"{w_}x{h_}+{app.winfo_rootx() + (app.winfo_width() - w_) // 2}+{app.winfo_rooty() + 120}")
        w.grab_set()
    except Exception:
        pass


def _cache_size() -> int:
    total = 0
    for folder in (CACHE_DIR, SEGMENTS_DIR, THUMB_DIR):
        try:
            for f in folder.glob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except Exception:
            pass
    return total


def clear_cache_clicked():
    if render_busy or plan_busy:
        ui_status(tr("Während des Renderns nicht möglich"))
        return
    size = _cache_size()
    if size <= 0:
        messagebox.showinfo(tr("Cache leeren"), tr("Der Cache ist bereits leer."))
        return
    if not messagebox.askokcancel(tr("Cache leeren"), tr(
            "Der Cache belegt {size}. Darin liegen nur Zwischendateien, die das Programm jederzeit neu erstellen kann.\n\n"
            "Deine Videos, Clips, Fotos und Einstellungen bleiben erhalten. Das nächste Rendern dauert etwas länger.\n\nJetzt leeren?").format(size=_mb(size))):
        return
    freed = 0
    for folder in (CACHE_DIR, SEGMENTS_DIR, THUMB_DIR):
        for f in list(folder.glob("*")):
            try:
                if f.is_file():
                    sz = f.stat().st_size
                    f.unlink()
                    freed += sz
            except Exception:
                pass
    try:
        POOL_THUMB_CACHE.clear()
    except Exception:
        pass
    ui_status(tr("Cache geleert: {size} frei").format(size=_mb(freed)))


def on_reset_clicked():
    """Alles auf Anfang: Musik, Auswahl, Formate, Extras, Schnitt, Editor. Cache optional leeren."""
    global music_file, music_dur, last_outputs, last_failed_formats, render_failed
    if render_busy or plan_busy:
        ui_status(tr("Reset nicht möglich während Render läuft"))
        return
    cache_files = list(CACHE_DIR.glob("*.mp4"))
    cache_count = len(cache_files)
    cache_mb = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)
    if cache_count > 0:
        msg = tr("Projekt zurücksetzen?\n\nCache enthält {n} Datei(en) ({mb:.1f} MB).\nCache ebenfalls leeren?").format(n=cache_count, mb=cache_mb)
        result = messagebox.askyesnocancel(tr("Einstellungen zurücksetzen"), msg)
        if result is None:
            return
        clear_cache = result
    else:
        if not messagebox.askokcancel(tr("Einstellungen zurücksetzen"), tr("Projekt zurücksetzen?")):
            return
        clear_cache = False
    if clear_cache:
        for f in cache_files:
            try:
                f.unlink()
            except Exception:
                pass

    music_file = None
    music_dur = None
    _forget_plan()
    last_outputs = []
    last_failed_formats = []
    thumb_state.update({"cands": [], "idx": 0, "saved": None, "busy": False, "style": None, "base": None, "final": None})
    selection.clear()
    for k, v in FORMAT_DEFAULTS.items():
        format_vars[k].set(v)
    speed_var.set("normal")
    intro_var.set(True)
    outro_var.set(True)
    vorspann_var.set(False)
    title_var.set(False)
    render_failed = None
    for f in SEGMENTS_DIR.glob("*.mp4"):
        try:
            f.unlink()
        except Exception:
            pass
    go(1)
    ui_status(tr("Cache geleert ({n} Dateien)").format(n=cache_count) if clear_cache else tr("bereit"))


# ===================== Short per URL =====================
_url_short_state: dict = {"win": None, "model": None, "url": "", "title": "", "result": None, "error": None}


def _valid_youtube_url(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        p_ = urlsplit(url)
        return p_.scheme in ("http", "https") and p_.hostname in YT_HOSTS
    except Exception:
        return False


def on_short_url_click():
    """YouTube-Link aus der Zwischenablage -> 45-s-Short (9:16) in output/Shorts, mit Fortschrittsfenster."""
    global _url_short_busy
    if _url_short_busy:                      # läuft schon: Fenster wieder zeigen
        open_url_short_window()
        return
    try:
        url = app.clipboard_get().strip()
    except Exception:
        url = ""
    if not url:
        messagebox.showinfo(tr("Short per URL"), tr("Zwischenablage leer – bitte zuerst einen YouTube-Link kopieren"))
        return
    if not _valid_youtube_url(url):
        messagebox.showinfo(tr("Short per URL"), tr("In der Zwischenablage ist kein gültiger YouTube-Link.") + "\n\n" +
                            tr("Kopiere den Link eines YouTube-Videos (z. B. aus der Adresszeile des Browsers) und wähle dann noch einmal „Short per URL“."))
        return
    _url_short_busy = True
    rows = [_prog_row("link", "Link prüfen"), _prog_row("tool", "YouTube-Werkzeug vorbereiten"),
            _prog_row("check", "Video-Infos abrufen"), _prog_row("load", "Video laden"),
            _prog_row("music", "Stärksten Abschnitt suchen"), _prog_row("render", "Short erstellen (45 s · 9:16)")]
    model = {"rows": rows, "complete": False, "fired": True, "on_complete": None}
    _url_short_state.update({"model": model, "url": url, "title": "", "result": None, "error": None})
    _prog_start(model, "link")
    open_url_short_window()

    def upd(t: str, stage: Optional[str] = None, frac: Optional[float] = None):
        def apply():
            if stage == "title":
                _url_short_state["title"] = t
                _url_short_refresh()
                return
            if stage:
                r = _prog_find(model, stage)
                if r is not None:
                    if r["state"] != "run":
                        for r0 in model["rows"]:
                            if r0["state"] == "run":
                                r0["detail"] = ""          # erledigte Schritte zeigen „fertig“
                        _prog_start(model, stage, frac=frac)
                    else:
                        r["frac"] = frac
                    r["detail"] = f"{int(frac * 100)} %" if frac is not None else ""
            ui_status(tr("Short per URL") + ": " + t)
        app.after(0, apply)

    def worker():
        global _url_short_busy
        try:
            path = generate_short_from_url(url, upd)

            def ok():
                _prog_finish(model)
                _url_short_state["result"] = path
                ui_status(tr("Short fertig: {name}  (Ordner output/Shorts)").format(name=path.name))
                play_done_sound()
                _url_short_refresh()
            app.after(0, ok)
        except Exception as e:
            msg = str(e) or e.__class__.__name__

            def fail():
                bad = next((r for r in model["rows"] if r["state"] == "run"), None) or \
                    next((r for r in model["rows"] if r["state"] == "wait"), None)
                if bad is not None:
                    bad["state"] = "fail"
                    bad["detail"] = tr("Fehler")
                _url_short_state["error"] = msg
                ui_status(tr("Short per URL fehlgeschlagen"))
                _url_short_refresh()
            app.after(0, fail)
        finally:
            _url_short_busy = False

    threading.Thread(target=worker, daemon=True).start()


def _url_short_refresh():
    """Fenster neu aufbauen (Titel, Ergebnis), falls es offen ist."""
    w = _url_short_state.get("win")
    try:
        if w is not None and w.winfo_exists():
            _build_url_short_content(w)
    except Exception:
        pass


def open_url_short_window():
    w = _url_short_state.get("win")
    try:
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
    except Exception:
        pass
    if _url_short_state.get("model") is None:
        return
    w = ctk.CTkToplevel(app)
    w.title(tr("Short per URL"))
    w.transient(app)
    w.configure(fg_color=C_BG)
    w.resizable(False, False)
    _url_short_state["win"] = w
    _build_url_short_content(w)
    w.bind("<Escape>", lambda _e: w.destroy())
    try:
        w.update_idletasks()
        w_, h_ = 600, max(w.winfo_reqheight(), 300)
        w.geometry(f"{w_}x{h_}+{app.winfo_rootx() + app.winfo_width() - w_ - 40}+{app.winfo_rooty() + 90}")
    except Exception:
        pass


def _build_url_short_content(w):
    st = _url_short_state
    for ch in w.winfo_children():
        if type(ch).__name__ in ("CTkFrame",):
            ch.destroy()
    c = card(w)
    c.pack(fill="both", expand=True, padx=14, pady=14)
    hd = ctk.CTkFrame(c, fg_color="transparent")
    hd.pack(fill="x", padx=20, pady=(16, 2))
    ctk.CTkLabel(hd, text="▶  " + tr("Short per URL"), font=F(16, True), text_color=C_TEXT).pack(side="left")
    help_button(hd, w).pack(side="right")
    info = ctk.CTkFrame(c, fg_color="transparent")
    info.pack(fill="x", padx=20)
    H(info, "us_link")
    u = st["url"]
    u = u if len(u) <= 70 else u[:67] + "…"
    if st["title"]:
        ctk.CTkLabel(info, text=st["title"], font=F(13, True), text_color=C_TEXT, anchor="w", justify="left",
                     wraplength=520).pack(fill="x")
    ctk.CTkLabel(info, text=u, font=F(11), text_color=C_MUTED, anchor="w").pack(fill="x")
    box = ctk.CTkFrame(c, fg_color=C_SOFT, corner_radius=10)
    box.pack(fill="x", padx=20, pady=(10, 0))
    H(box, "us_steps")
    sl = StepList(box, st["model"], compact=True)
    sl.frame.pack(fill="x", padx=14, pady=10)
    res = ctk.CTkFrame(c, fg_color="transparent")
    res.pack(fill="x", padx=20, pady=(10, 16))
    H(res, "us_result")
    if st["result"] is not None:
        ctk.CTkLabel(res, text="✓  " + tr("Fertig: ") + st["result"].name, font=F(13, True), text_color=C_OK,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(res, text=tr("Gespeichert im Ordner output/Shorts"), font=F(11), text_color=C_MUTED,
                     anchor="w").pack(fill="x")
    elif st["error"]:
        ctk.CTkLabel(res, text="✕  " + st["error"], font=F(12, True), text_color=C_DANGER_TX, anchor="w",
                     justify="left", wraplength=540).pack(fill="x")
    else:
        ctk.CTkLabel(res, text=tr("Du kannst dieses Fenster schließen und weiterarbeiten – der Short entsteht im Hintergrund."),
                     font=F(11), text_color=C_MUTED, anchor="w", justify="left", wraplength=540).pack(fill="x")
    bt = ctk.CTkFrame(res, fg_color="transparent")
    bt.pack(fill="x", pady=(12, 0))
    btn_primary(bt, tr("Schließen"), w.destroy, width=120, height=34).pack(side="right")
    if st["result"] is not None:
        p_ = st["result"]
        btn_secondary(bt, tr("Ordner öffnen"), lambda: open_path_portable(p_.parent), width=140).pack(side="right", padx=8)
    try:
        w.update_idletasks()
        w.geometry(f"600x{max(w.winfo_reqheight(), 300)}")
    except Exception:
        pass


# ===================== Schließen =====================
def on_closing():
    if render_busy:
        if not messagebox.askyesno(tr("Beenden"), tr("Rendern läuft noch. Wirklich beenden?")):
            return
    elif plan_dirty():
        if not messagebox.askokcancel(tr("Beenden"), tr("Es gibt noch nicht übernommene Änderungen an der Clipfolge. Wirklich beenden?")):
            return
    kill_child_processes()
    app.destroy()
    os._exit(0)


# ===================== Klick-Hilfe =====================
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
 "sw_title": ("Titel", "Blendet den Musiktitel unten links im Video ein (Schrift Arial Bold mit leichtem Schatten).\n\nWoher der Titel kommt: Er wird automatisch aus dem Dateinamen deiner Musik gebildet. Aus „Mein Song.mp3“ wird also „Mein Song“. Sonderzeichen, die in Windows-Dateinamen nicht erlaubt sind, wie Doppelpunkte, werden entfernt. Willst du einen anderen Titel, benenne die Musikdatei um.\n\nWann er erscheint: nach dem Intro, mit weichem Einblenden, und nach einigen Sekunden blendet er wieder aus. Er bleibt also nicht das ganze Video über stehen.\n\nIm 58-Sekunden-Short gibt es nie einen Titel."),
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
 "sw_title": ("Title", "Shows the music title at the bottom left of the video (Arial Bold with a light shadow).\n\nWhere the title comes from: it is created automatically from the file name of your music. “My Song.mp3” becomes “My Song”. Characters that are not allowed in Windows file names, such as colons, are removed. If you want a different title, rename the music file.\n\nWhen it appears: after the intro, fading in softly, and after a few seconds it fades out again. So it does not stay on screen for the whole video.\n\nThe 58-second short never has a title."),
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
 "sw_title": ("Título", "Muestra el título de la música abajo a la izquierda en el vídeo (letra Arial Bold con una sombra ligera).\n\nDe dónde sale el título: se crea automáticamente a partir del nombre del archivo de tu música. «Mi canción.mp3» se convierte en «Mi canción». Los caracteres que Windows no permite en nombres de archivo, como los dos puntos, se eliminan. Si quieres otro título, cambia el nombre del archivo de música.\n\nCuándo aparece: después del intro, entrando suavemente, y tras unos segundos vuelve a desvanecerse. Es decir, no se queda en pantalla durante todo el vídeo.\n\nEl short de 58 segundos nunca lleva título."),
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


def show_help_topic(key: str, host=None):
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
    HOST = host if host is not None else app
    win = ctk.CTkToplevel(HOST)
    _click_help["topic"] = win
    win.title(title)
    win.resizable(False, False)
    win.transient(HOST)
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
    x = HOST.winfo_rootx() + max(0, (HOST.winfo_width() - w_) // 2)
    y = HOST.winfo_rooty() + max(0, (HOST.winfo_height() - h_) // 3)
    win.geometry(f"{w_}x{h_}+{x}+{y}")
    try:
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
    except Exception:
        pass


def start_click_help(host=None):
    """Klick-Hilfe: unsichtbare Fläche fängt Klicks ab; Klick auf Bereich -> Erklärung."""
    if _click_help["active"]:
        return
    L = LANG or "de"
    M = MENU.get(L, MENU["de"])
    _click_help["active"] = True
    HOST = host if host is not None else app
    BAN_H = 40

    banner = ctk.CTkFrame(HOST, fg_color="#1f6aa5", corner_radius=0, height=BAN_H)
    banner.place(x=0, y=0, relwidth=1)
    banner.pack_propagate(False)
    lbl_var = ctk.StringVar(value=M["banner"])
    ctk.CTkLabel(banner, textvariable=lbl_var, text_color="white",
                 font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(side="left", padx=14)
    overlay = tk.Toplevel(HOST)
    overlay.overrideredirect(True)
    try:
        overlay.attributes("-alpha", 0.01)
    except Exception:
        pass
    overlay.configure(bg="black", cursor="question_arrow")

    def place_overlay():
        try:
            HOST.update_idletasks()
            overlay.geometry(f"{HOST.winfo_width()}x{max(1, HOST.winfo_height() - BAN_H)}"
                             f"+{HOST.winfo_rootx()}+{HOST.winfo_rooty() + BAN_H}")
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
        key = _help_target_at(e.x_root, e.y_root, host)
        if key:
            title = HELP_TOPICS.get(L, HELP_TOPICS["de"]).get(key, ("", ""))[0]
            lbl_var.set(f"❓ {title}")
        else:
            lbl_var.set(M["banner"])

    def on_click(e):
        key = _help_target_at(e.x_root, e.y_root, host)
        if key:
            finish()              # Modus endet automatisch, dann erscheint die Erklärung
            show_help_topic(key, host)
        else:
            lbl_var.set(M["none"])

    overlay.bind("<Motion>", on_move)
    overlay.bind("<Button-1>", on_click)
    overlay.bind("<Escape>", finish)
    HOST.bind("<Escape>", finish, add="+")

    def keep_aligned():
        if _click_help["active"]:
            place_overlay()
            HOST.after(250, keep_aligned)

    place_overlay()
    banner.lift()
    overlay.lift()
    try:
        overlay.focus_force()
    except Exception:
        pass
    HOST.after(250, keep_aligned)


# =====================================================================================
#                      Texte v4.0: Klick-Hilfe, Kurzanleitung
# =====================================================================================
_HELP_V4 = {
"de": {
 "music": ("Musikdatei", "Hier wählst du die Musik, aus der dein Video entsteht. Klicke auf „Musikdatei wählen“ oder ziehe die Datei einfach ins Programmfenster – das klappt in jedem Schritt. Unterstützt werden MP3, WAV, FLAC und OGG.\n\nWas daraus wird: Das Programm erkennt den Beat und legt alle Bildwechsel genau darauf. Das fertige Video ist so lang wie dein Song (plus Pre-Intro und Outro, falls aktiv). Auch der Dateiname kommt von hier: „MeinSong.mp3“ wird zu „MeinSong.mp4“, und derselbe Name wird optional als Titel eingeblendet.\n\nWichtig: Ohne Musik geht es nicht weiter – der Button „Weiter“ bleibt so lange grau. Wählst du später eine andere Musik, ändern sich auch die Namen der neuen Videos."),
 "help": ("Einstellungen (⚙)", "Das Zahnrad öffnet das Menü mit allem, was nicht direkt zum Videobau gehört:\n\n• Klick-Hilfe: danach einen Bereich anklicken und genau diese Art Erklärung lesen. Der Modus endet nach einer Erklärung von selbst (oder mit Esc).\n• Kurzanleitung: die vier Schritte im Überblick.\n• PDF-Anleitung: das ausführliche Handbuch in deiner Sprache (muss neben der EXE liegen).\n• Short per URL: 45-Sekunden-Short aus einem YouTube-Link in der Zwischenablage.\n• Pool-Ordner öffnen / Output-Ordner: öffnet die Ordner im Explorer.\n• Sprache / Language: Deutsch, English oder Español – wird sofort umgestellt, ohne Neustart. Deine Auswahl bleibt erhalten.\n• Einstellungen zurücksetzen: alles auf Anfang (Musik, Auswahl, Formate, Extras, Schnitt). Der Cache kann dabei optional geleert werden. Deine Videos und Clips bleiben immer erhalten.\n• Neueste Version / Mobile Version: öffnet die GitHub-Seiten für Updates und MVG Mobil."),
 "qhelp": ("Klick-Hilfe (?)", "Startet die Klick-Hilfe direkt, ohne Umweg über das Zahnrad.\n\nSo funktioniert sie: Oben erscheint ein blauer Balken. Fahre mit der Maus über das Fenster – der Balken zeigt, wozu es an dieser Stelle eine Erklärung gibt. Ein Klick öffnet die ausführliche Erklärung, danach ist die Klick-Hilfe automatisch wieder aus. Solange sie aktiv ist, wird nichts ausgelöst: Du kannst gefahrlos auf jeden Button klicken.\n\nBeenden ohne Erklärung: mit Esc oder dem Button rechts im blauen Balken."),
 "steps": ("Schrittleiste", "Zeigt, wo du gerade bist: 1 Start, 2 Bildmaterial, 3 Extras, 4 Fertig. Erledigte Schritte bekommen einen Haken, der aktuelle Schritt ist fett.\n\nDu kannst auf einen Schritt klicken, um direkt dorthin zu springen – zum Beispiel aus Schritt 4 zurück zu den Extras, um etwas zu ändern und neu zu rendern. Ein Schritt ist erst erreichbar, wenn das Nötige davor erledigt ist: Schritt 2 braucht Musik, Schritt 3 zusätzlich mindestens einen Clip oder ein Foto, Schritt 4 ein fertig gerendertes Video.\n\nAlle Einstellungen bleiben beim Hin- und Herspringen erhalten. Während ein Video gerendert wird, ist die Leiste gesperrt."),
 "fmt_normal": ("Normal 16:9", "Das klassische Querformat für YouTube, Fernseher und Computer-Bildschirme. Das Video ist so lang wie deine Musik.\n\nIn diesem Format (und in 9:16 in voller Länge) werden die Extras aus Schritt 3 verwendet: Pre-Intro, Intro, Outro und Titel.\n\nDateiname: Musiktitel.mp4 im Ordner output/.\n\nDas erste gewählte Format ist das Hauptvideo: Nur dieses bearbeitest du nach dem Rendern in Schritt 4 (Clips verschieben und ersetzen). Ist 16:9 angekreuzt, ist es immer das Hauptvideo."),
 "fmt_shorts": ("Normal 9:16", "Hochformat (wie auf dem Handy gehalten) in der vollen Länge deiner Musik. Gedacht für Fälle, in denen das ganze Lied am Handy gezeigt werden soll, etwa als Story oder als langes Hochformat-Video.\n\nPre-Intro, Intro, Outro und Titel werden verwendet, wenn sie in Schritt 3 eingeschaltet sind.\n\nDateiname: Musiktitel_9x16.mp4 im Ordner output/.\n\nIst 16:9 nicht angekreuzt, wird dieses Format zum Hauptvideo, das du in Schritt 4 bearbeitest. Möchtest du nur eine kurze Fassung für Reels, Shorts oder TikTok, nimm stattdessen den 58-Sekunden-Short."),
 "src_pool": ("Clip-Pool", "Der Pool ist ein einziger Ordner (clips/ im Programmordner) für Videos und Fotos, die du immer wieder verwendest – zum Beispiel deine Standard-Clips für jeden Song.\n\nWichtig: Das Programm wird ohne Clips ausgeliefert. Den Ordner füllst du selbst: „Ordner öffnen“ öffnet ihn im Explorer, dort einfach Dateien hineinkopieren. Unterordner werden nicht berücksichtigt.\n\nMit „Clips auswählen“ suchst du dir aus dem Pool per Klick die Clips für dieses Video aus. Die Karte zeigt an, wie viele Dateien im Pool liegen und wie viele davon gerade ausgewählt sind; ist etwas ausgewählt, wird die Karte blau und bekommt einen Haken."),
 "src_pool_btn": ("Clips auswählen", "Öffnet ein Fenster mit Vorschaubildern aller Videos und Fotos im Pool-Ordner. Ein Klick wählt einen Clip aus oder wieder ab (blauer Rahmen = ausgewählt). „Alle“ und „Keine“ gehen schneller.\n\nMit „Übernehmen“ landen die gewählten Pool-Clips in deiner Auswahl unten; abgewählte Pool-Clips werden daraus entfernt. Clips aus anderen Quellen (eigene Ordner, hineingezogene Dateien) bleiben davon unberührt. „Abbrechen“ lässt alles, wie es war.\n\nIst der Pool leer, bekommst du einen Hinweis und der Pool-Ordner öffnet sich, damit du ihn füllen kannst. Das Erzeugen der Vorschaubilder kann bei vielen Clips einen Moment dauern."),
 "src_pool_open": ("Pool-Ordner öffnen", "Öffnet den Pool-Ordner (clips/ im Programmordner) im Windows-Explorer. Dort legst du Videos und Fotos ab, die du für viele Videos verwenden möchtest.\n\nNach dem Kopieren musst du nichts weiter tun: Beim nächsten Klick auf „Clips auswählen“ erscheinen die neuen Dateien automatisch. Unterstützt werden MP4, MOV, MKV, AVI und WEBM sowie JPG, PNG und WEBP. Unterordner werden nicht durchsucht."),
 "src_videos": ("Eigene Videos", "Für Material, das nicht im Pool liegt – zum Beispiel Clips, die nur zu diesem einen Song passen.\n\n• Ordner wählen: übernimmt alle Videos aus einem Ordner in deine Auswahl.\n• Dateien wählen: einzelne Videos auswählen (mit Strg oder Umschalt mehrere auf einmal).\n• Oder: Dateien bzw. ganze Ordner einfach ins Programmfenster ziehen.\n\nDie Karte zeigt, wie viele eigene Videos gerade in der Auswahl sind. Unterstützt werden MP4, MOV, MKV, AVI und WEBM."),
 "src_photos": ("Eigene Fotos", "Fotos (JPG, PNG, WEBP) werden wie Clips verwendet: Jedes Foto steht für die Dauer eines Schnitts im Bild.\n\n• Ordner wählen: übernimmt alle Fotos aus einem Ordner.\n• Dateien wählen: einzelne Fotos auswählen.\n• Oder: Fotos einfach ins Fenster ziehen.\n\nDamit das Video lebendig bleibt, gelten zwei Regeln, sobald auch Videoclips dabei sind: Ein Foto wird nur für Schnitte von mindestens 2 Sekunden genommen, und es kommen höchstens zwei Fotos direkt hintereinander. Ein Video nur aus Fotos ist ebenfalls möglich."),
 "src_folder": ("Ordner wählen", "Öffnet einen Windows-Dialog, in dem du einen Ordner auswählst. Alle passenden Dateien darin (Videos bzw. Fotos) werden deiner Auswahl hinzugefügt; Unterordner werden nicht durchsucht.\n\nWichtig zu wissen: Der Windows-Ordnerdialog zeigt nur Ordner an, keine Dateien. Ein Ordner voller Videos sieht darin deshalb leer aus – das ist normal. Einfach den Ordner anklicken und mit „Ordner auswählen“ bestätigen. Was gefunden wurde, siehst du danach in „Deine Auswahl“.\n\nDateien, die schon in der Auswahl sind, werden kein zweites Mal hinzugefügt."),
 "src_files": ("Dateien wählen", "Öffnet einen Windows-Dialog, in dem du einzelne Dateien auswählst. Mit gedrückter Strg-Taste wählst du mehrere einzeln aus, mit Umschalt einen ganzen Bereich, mit Strg+A alle Dateien im Ordner.\n\nDie gewählten Dateien kommen zusätzlich zu dem, was schon in deiner Auswahl ist. Doppelte Einträge werden automatisch übersprungen."),
 "selection": ("Deine Auswahl", "Hier siehst du alles, was in dein Video kommt – egal aus welcher Quelle: Pool-Clips (mit ◆ markiert), Videos aus eigenen Ordnern, Fotos (🖼) und hineingezogene Dateien.\n\nAlle Einträge sind gleichberechtigt: Das Programm verteilt sie zufällig auf die Schnitte, möglichst ohne denselben Clip zweimal hintereinander. Gibt es mehr Schnitte als Clips, werden Clips wiederholt.\n\nHinzufügen kannst du über die drei Karten oben oder indem du Videos, Fotos oder ganze Ordner aus dem Explorer ins Fenster ziehst. Mit dem ✕ auf einem Vorschaubild entfernst du einen einzelnen Eintrag, mit „Alle entfernen“ leerst du die Auswahl. Die Dateien selbst werden dabei nie gelöscht.\n\nTipp: Je mehr unterschiedliches Material, desto abwechslungsreicher das Video."),
 "sel_remove": ("Aus der Auswahl entfernen (✕)", "Nimmt diesen einen Clip bzw. dieses Foto aus der Auswahl für das Video. Die Datei selbst bleibt auf deinem Computer unverändert erhalten – es wird nichts gelöscht.\n\nVersehentlich entfernt? Einfach wieder hinzufügen: über die passende Karte oben oder per Ziehen ins Fenster."),
 "sel_clear": ("Alle entfernen", "Leert die komplette Auswahl, nach einer Sicherheitsabfrage. Die Dateien selbst bleiben erhalten, auch die im Pool-Ordner.\n\nPraktisch, wenn du für einen neuen Song ganz anderes Material verwenden möchtest."),
 "speed": ("Schnittgeschwindigkeit", "Bestimmt, wie schnell die Clips wechseln. Alle Schnitte werden genau auf den Beat der Musik gelegt, die Geschwindigkeit bestimmt nur, wie viele Schläge zwischen zwei Schnitten liegen.\n\n• Sanft: etwa alle 3,5 Sekunden, ruhig und atmosphärisch\n• Langsam: etwa alle 2,2 Sekunden, cineastisch\n• Normal: etwa alle 1,7 Sekunden, ausgewogen (Standard)\n• Schnell: etwa alle 0,95 Sekunden, energetisch\n• Random: jeder Schnitt zufällig zwischen 0,95 und 3,5 Sekunden\n\nTipp: Für Balladen passen Sanft oder Langsam, für Bachata und Merengue meist Normal, für Reggaetón und Tanzmusik Schnell. In den Hochformaten werden Sanft und Langsam etwas gestrafft, damit es am Handy nicht zu träge wirkt."),
 "summary": ("Zusammenfassung", "Zeigt auf einen Blick, was beim Klick auf „Video erstellen“ passiert: welche Musik, welche Formate, wie viel Bildmaterial, welche Extras und welche Schnittgeschwindigkeit.\n\nStimmt etwas nicht, springst du über die Schrittleiste oben oder „Zurück“ direkt an die richtige Stelle. Alles bleibt dabei erhalten."),
 "render": ("Video erstellen", "Startet das Rendern. Das Programm analysiert die Musik, legt die Schnitte auf den Beat, rendert alle Abschnitte und fügt Bild und Ton zusammen. Für jedes gewählte Format entsteht eine eigene Datei im Ordner output/.\n\nWährenddessen siehst du für jedes Format, ob es wartet, läuft oder fertig ist, dazu Fortschrittsbalken und Status. Die Bedienung ist so lange gesperrt. Mehrere Formate dauern entsprechend länger. Ein Klang meldet das Ende, danach geht es automatisch zu Schritt 4.\n\nSchlägt das Hauptvideo fehl, siehst du die Ursache und kannst zurück zu den Extras oder es erneut versuchen. Schlägt nur ein weiteres Format fehl (z. B. der Short), wird das in Schritt 4 angezeigt."),
 "render_page": ("Fortschritt", "Hier siehst du, was gerade gerendert wird. Jede Zeile ist ein Format: ○ wartet, ● läuft, ✓ fertig, ✕ Fehler. Der Balken zählt über alle Formate hinweg, die Zeile darunter zeigt den aktuellen Arbeitsschritt (z. B. „Rendere Segmente…“).\n\nBeim ersten Render mit neuen Clips kann „Normalisiere …“ erscheinen: Clips in ungewöhnlichen Formaten werden dabei einmalig vorbereitet und im Cache gespeichert. Weitere Renders mit denselben Clips gehen dann schneller.\n\nSchließt du das Programm jetzt, wird das Video nicht fertig."),
 "new_video": ("Neues Video", "Beginnt ein neues Video bei Schritt 1. Formate, Extras, Schnittgeschwindigkeit und deine Clip-Auswahl bleiben erhalten – du musst nur die neue Musik wählen. So machst du schnell mehrere Videos mit demselben Material.\n\nDas fertige Video bleibt im Ordner output/ erhalten. Der Clip-Editor wird geleert; nicht übernommene Änderungen gehen nach einer Rückfrage verloren.\n\nMöchtest du wirklich alles auf Anfang setzen (auch Auswahl und Formate), nutze im ⚙-Menü „Einstellungen zurücksetzen“."),
 "watch": ("Video abspielen", "Öffnet das gerade erstellte Hauptvideo im Standard-Videoplayer deines Computers.\n\nNach „Änderungen übernehmen“ zeigt es die neue Fassung. Weitere Formate wie den Short findest du über „Output-Ordner“."),
 "ed_apply": ("Änderungen übernehmen", "Speichert deine Änderungen im Video: die neue Reihenfolge und alle ersetzten Clips.\n\nDas geht schnell, weil nur der Zusammenfüge-Schritt neu läuft, nicht das ganze Video. Während des Speicherns zeigt die Statuszeile den Fortschritt – bitte so lange nichts schließen. Danach ist das Video im Ordner output/ aktualisiert und „Video abspielen“ zeigt die neue Fassung.\n\nDer Button ist erst aktiv, wenn du etwas geändert hast. Solange du nicht übernimmst, bleibt das Video unverändert; der gelbe Hinweis „Änderungen vorgemerkt“ erinnert dich daran."),
 "ed_discard": ("Verwerfen", "Macht alle noch nicht übernommenen Änderungen rückgängig: Ersetzte Clips kommen zurück, die Reihenfolge springt auf den zuletzt gespeicherten Stand.\n\nDas Video selbst wird dabei nicht angefasst – es bleibt genau so, wie es zuletzt gespeichert wurde. Bereits übernommene Änderungen bleiben erhalten."),
 "ed_grid": ("Clip-Übersicht", "Jede Kachel ist ein Abschnitt deines Videos, in der Reihenfolge, in der er vorkommt (von links nach rechts, Zeile für Zeile). Oben auf der Kachel stehen Nummer und Zeitbereich im Video.\n\n• Verschieben: Kachel mit gedrückter linker Maustaste auf eine andere ziehen. Beide tauschen die Plätze; das Ziel leuchtet beim Ziehen blau auf.\n• Ersetzen: „Clip ersetzen“ tauscht den Clip gegen einen zufälligen anderen aus deiner Auswahl, genau gleich lang. So bleibt das Video exakt so lang wie dein Song und alle Schnitte bleiben auf dem Beat.\n• Gesperrte Kacheln (Schloss): Pre-Intro, Intro und Outro bleiben an ihrem Platz.\n\nDu kannst beliebig oft ändern. Erst „Änderungen übernehmen“ speichert ins Video, „Verwerfen“ macht alles Offene rückgängig."),
 "ed_remove": ("Clip ersetzen", "Ersetzt diesen Clip durch einen anderen aus deiner Auswahl. Der Ersatz wird zufällig gewählt und ist bildgenau so lang wie der alte Clip, damit das Video so lang bleibt wie der Song und alle Schnitte auf dem Beat bleiben. Bevorzugt werden Clips, die noch nicht im Video vorkommen.\n\nEs geht schnell, weil nur dieser eine Abschnitt neu gerendert wird. Die Statuszeile zeigt „Ersetze Clip …“ und danach „Clip ersetzt“.\n\nGefällt dir der neue Clip nicht, klicke einfach noch einmal – dann kommt ein anderer. „Verwerfen“ holt alle ersetzten Clips zurück, „Änderungen übernehmen“ speichert sie. Gibt es keinen passenden Ersatz (z. B. nur ein Clip in der Auswahl), meldet die Statuszeile das."),
 "ed_lock": ("Gesperrter Abschnitt", "Das Schloss markiert Pre-Intro, Intro und Outro. Diese Abschnitte sind fest: Pre-Intro und Intro bleiben vorn, das Outro bleibt am Ende.\n\nDer Grund: Pre-Intro und Outro haben ihren eigenen Ton, der vor bzw. nach der Musik läuft. Das Intro läuft am Songanfang, und der Titel wird erst danach eingeblendet. Würden diese Abschnitte verrutschen, passten Ton, Titel und Bild nicht mehr zusammen.\n\nAlle anderen Clips (ohne Schloss) kannst du frei verschieben und ersetzen."),
 "ed_status": ("Statuszeile (Editor)", "Zeigt im Clip-Editor den aktuellen Stand, zum Beispiel „Ersetze Clip … (wird neu gerendert)“, „Clip ersetzt — mit „Übernehmen“ speichern“ oder den Fortschritt beim Speichern (der dünne Balken darüber).\n\nBei Problemen, etwa wenn ein Clip nicht ersetzt werden konnte, steht hier die Ursache."),
 "status": ("Statuszeile", "Zeigt unten in der Fußleiste, was das Programm gerade getan hat oder was noch fehlt – zum Beispiel „Bitte zuerst eine Musikdatei wählen.“, „5 hinzugefügt · Auswahl: 12 Clips, 3 Fotos“ oder „Fertig: Song.mp4 + Song_Short.mp4“.\n\nDas ist die erste Stelle, auf die du schauen solltest, wenn etwas nicht wie erwartet reagiert."),
},
"en": {
 "music": ("Music file", "Here you choose the music your video is made from. Click “Choose music file” or simply drag the file into the program window – that works in every step. MP3, WAV, FLAC and OGG are supported.\n\nWhat happens: the program detects the beat and places every cut exactly on it. The finished video is as long as your song (plus pre-intro and outro, if active). The file name also comes from here: “MySong.mp3” becomes “MySong.mp4”, and the same name can be shown as a title.\n\nImportant: without music you cannot continue – the “Next” button stays grey until then. If you choose different music later, the names of the new videos change too."),
 "help": ("Settings (⚙)", "The gear opens the menu with everything that is not directly part of building the video:\n\n• Click help: then click an area and read exactly this kind of explanation. The mode ends by itself after one explanation (or with Esc).\n• Quick guide: the four steps at a glance.\n• PDF manual: the detailed manual in your language (must be next to the EXE).\n• Short from URL: a 45-second short from a YouTube link on the clipboard.\n• Open pool folder / Output folder: opens the folders in Explorer.\n• Language / Sprache: Deutsch, English or Español – switches immediately, no restart. Your selection is kept.\n• Reset settings: everything back to the start (music, selection, formats, extras, cutting). The cache can optionally be emptied. Your videos and clips are always kept.\n• Latest version / Mobile version: opens the GitHub pages for updates and MVG Mobile."),
 "qhelp": ("Click help (?)", "Starts click help directly, without going through the gear.\n\nHow it works: a blue bar appears at the top. Move the mouse over the window – the bar shows what can be explained at that spot. A click opens the detailed explanation, then click help switches off by itself. While it is active nothing is triggered, so you can safely click any button.\n\nTo end without an explanation: press Esc or use the button on the right of the blue bar."),
 "steps": ("Step bar", "Shows where you are: 1 Start, 2 Footage, 3 Extras, 4 Done. Completed steps get a tick, the current step is bold.\n\nYou can click a step to jump straight there – for example from step 4 back to the extras to change something and render again. A step can only be reached once what it needs is done: step 2 needs music, step 3 additionally at least one clip or photo, step 4 a finished video.\n\nAll settings are kept while you jump back and forth. While a video is rendering, the bar is locked."),
 "fmt_normal": ("Normal 16:9", "The classic landscape format for YouTube, TVs and computer screens. The video is as long as your music.\n\nIn this format (and in full-length 9:16) the extras from step 3 are used: pre-intro, intro, outro and title.\n\nFile name: SongTitle.mp4 in the output/ folder.\n\nThe first chosen format is the main video: only this one is edited after rendering in step 4 (moving and replacing clips). If 16:9 is ticked, it is always the main video."),
 "fmt_shorts": ("Normal 9:16", "Portrait format (as you hold a phone) at the full length of your music. Meant for when the whole song should be shown on a phone, for example as a story or a long portrait video.\n\nPre-intro, intro, outro and title are used if they are switched on in step 3.\n\nFile name: SongTitle_9x16.mp4 in the output/ folder.\n\nIf 16:9 is not ticked, this format becomes the main video that you edit in step 4. If you only want a short version for Reels, Shorts or TikTok, use the 58-second short instead."),
 "src_pool": ("Clip pool", "The pool is a single folder (clips/ in the program folder) for videos and photos you use again and again – for example your standard clips for every song.\n\nImportant: the program ships without clips. You fill the folder yourself: “Open folder” opens it in Explorer, then simply copy files into it. Subfolders are ignored.\n\nWith “Select clips” you pick the clips for this video from the pool with a click. The card shows how many files are in the pool and how many of them are currently selected; when something is selected, the card turns blue and gets a tick."),
 "src_pool_btn": ("Select clips", "Opens a window with thumbnails of all videos and photos in the pool folder. One click selects or deselects a clip (blue frame = selected). “All” and “None” are quicker.\n\nWith “Apply” the chosen pool clips go into your selection below; deselected pool clips are removed from it. Clips from other sources (your own folders, dropped files) are not affected. “Cancel” leaves everything as it was.\n\nIf the pool is empty, you get a notice and the pool folder opens so you can fill it. Creating the thumbnails can take a moment with many clips."),
 "src_pool_open": ("Open pool folder", "Opens the pool folder (clips/ in the program folder) in Windows Explorer. Put videos and photos there that you want to use for many videos.\n\nAfter copying you do not need to do anything else: the next time you click “Select clips”, the new files appear automatically. MP4, MOV, MKV, AVI and WEBM as well as JPG, PNG and WEBP are supported. Subfolders are not searched."),
 "src_videos": ("Your videos", "For material that is not in the pool – for example clips that only fit this one song.\n\n• Choose folder: adds all videos from a folder to your selection.\n• Choose files: pick individual videos (several at once with Ctrl or Shift).\n• Or: simply drag files or whole folders into the program window.\n\nThe card shows how many of your own videos are currently in the selection. MP4, MOV, MKV, AVI and WEBM are supported."),
 "src_photos": ("Your photos", "Photos (JPG, PNG, WEBP) are used like clips: each photo stays on screen for the length of one cut.\n\n• Choose folder: adds all photos from a folder.\n• Choose files: pick individual photos.\n• Or: simply drag photos into the window.\n\nTo keep the video lively, two rules apply as soon as video clips are included too: a photo is only used for cuts of at least 2 seconds, and at most two photos come directly after each other. A video made only of photos is possible as well."),
 "src_folder": ("Choose folder", "Opens a Windows dialog in which you choose a folder. All matching files in it (videos or photos) are added to your selection; subfolders are not searched.\n\nGood to know: the Windows folder dialog shows only folders, not files. A folder full of videos therefore looks empty in it – that is normal. Just click the folder and confirm with “Select folder”. You see what was found afterwards in “Your selection”.\n\nFiles that are already in the selection are not added a second time."),
 "src_files": ("Choose files", "Opens a Windows dialog in which you choose individual files. Hold Ctrl to pick several one by one, Shift for a whole range, Ctrl+A for all files in the folder.\n\nThe chosen files are added to what is already in your selection. Duplicates are skipped automatically."),
 "selection": ("Your selection", "Here you see everything that goes into your video – whatever the source: pool clips (marked ◆), videos from your own folders, photos (🖼) and dropped files.\n\nAll entries are equal: the program distributes them randomly over the cuts, avoiding the same clip twice in a row where possible. If there are more cuts than clips, clips are repeated.\n\nYou add material with the three cards above or by dragging videos, photos or whole folders from Explorer into the window. The ✕ on a thumbnail removes a single entry, “Remove all” empties the selection. The files themselves are never deleted.\n\nTip: the more varied the material, the more varied the video."),
 "sel_remove": ("Remove from selection (✕)", "Takes this one clip or photo out of the selection for the video. The file itself stays unchanged on your computer – nothing is deleted.\n\nRemoved by mistake? Simply add it again: with the matching card above or by dragging it into the window."),
 "sel_clear": ("Remove all", "Empties the whole selection, after a confirmation. The files themselves are kept, including those in the pool folder.\n\nHandy when you want to use completely different material for a new song."),
 "speed": ("Cutting speed", "Determines how fast the clips change. All cuts are placed exactly on the beat; the speed only decides how many beats lie between two cuts.\n\n• Gentle: about every 3.5 seconds, calm and atmospheric\n• Slow: about every 2.2 seconds, cinematic\n• Normal: about every 1.7 seconds, balanced (default)\n• Fast: about every 0.95 seconds, energetic\n• Random: every cut random between 0.95 and 3.5 seconds\n\nTip: Gentle or Slow suit ballads, Normal usually suits bachata and merengue, Fast suits reggaeton and dance music. In the portrait formats Gentle and Slow are tightened a little so it does not feel sluggish on a phone."),
 "summary": ("Summary", "Shows at a glance what happens when you click “Create video”: which music, which formats, how much footage, which extras and which cutting speed.\n\nIf something is not right, jump to the right place via the step bar at the top or “Back”. Everything is kept."),
 "render": ("Create video", "Starts rendering. The program analyses the music, places the cuts on the beat, renders all sections and joins picture and sound. For every chosen format a separate file is created in the output/ folder.\n\nMeanwhile you see for each format whether it is waiting, running or finished, plus a progress bar and status. The controls are locked until then. Several formats take correspondingly longer. A sound signals the end, then you go to step 4 automatically.\n\nIf the main video fails, you see the cause and can go back to the extras or try again. If only an additional format fails (e.g. the short), this is shown in step 4."),
 "render_page": ("Progress", "Here you see what is being rendered. Each row is one format: ○ waiting, ● running, ✓ finished, ✕ error. The bar counts across all formats, the line below shows the current work step (e.g. “Rendering segments…”).\n\nOn the first render with new clips you may see “Normalizing …”: clips in unusual formats are prepared once and stored in the cache. Further renders with the same clips are faster.\n\nIf you close the program now, the video will not be finished."),
 "new_video": ("New video", "Starts a new video at step 1. Formats, extras, cutting speed and your clip selection are kept – you only need to choose the new music. This way you quickly make several videos with the same material.\n\nThe finished video stays in the output/ folder. The clip editor is cleared; changes not yet applied are lost after a confirmation.\n\nIf you really want to reset everything (including selection and formats), use “Reset settings” in the ⚙ menu."),
 "watch": ("Play video", "Opens the main video you just created in your computer's default video player.\n\nAfter “Apply changes” it shows the new version. Other formats such as the short are in the “Output folder”."),
 "ed_apply": ("Apply changes", "Saves your changes in the video: the new order and all replaced clips.\n\nThis is quick because only the joining step runs again, not the whole video. While saving, the status line shows the progress – please do not close anything until it is done. Afterwards the video in the output/ folder is updated and “Play video” shows the new version.\n\nThe button only becomes active once you have changed something. As long as you do not apply, the video stays unchanged; the yellow “Changes pending” notice reminds you."),
 "ed_discard": ("Discard", "Undoes all changes not yet applied: replaced clips come back and the order returns to the last saved state.\n\nThe video itself is not touched – it stays exactly as it was last saved. Changes you already applied are kept."),
 "ed_grid": ("Clip overview", "Every tile is one section of your video, in the order in which it appears (left to right, row by row). At the top of the tile you see its number and its time range in the video.\n\n• Move: drag a tile with the left mouse button held down onto another one. The two swap places; the target lights up blue while you drag.\n• Replace: “Replace clip” swaps the clip for a random other one from your selection, exactly as long. This keeps the video exactly as long as your song and all cuts stay on the beat.\n• Locked tiles (padlock): pre-intro, intro and outro stay in place.\n\nYou can change as often as you like. Only “Apply changes” saves into the video, “Discard” undoes everything pending."),
 "ed_remove": ("Replace clip", "Replaces this clip with another one from your selection. The replacement is chosen at random and is frame-exactly as long as the old clip, so the video stays as long as the song and all cuts stay on the beat. Clips that do not yet appear in the video are preferred.\n\nIt is quick because only this one section is rendered again. The status line shows “Replacing clip …” and then “Clip replaced”.\n\nIf you do not like the new clip, just click again – you get a different one. “Discard” brings back all replaced clips, “Apply changes” saves them. If there is no suitable replacement (e.g. only one clip in the selection), the status line says so."),
 "ed_lock": ("Locked section", "The padlock marks pre-intro, intro and outro. These sections are fixed: pre-intro and intro stay at the front, the outro stays at the end.\n\nThe reason: pre-intro and outro have their own sound, which plays before or after the music. The intro plays at the start of the song, and the title only appears after it. If these sections moved, sound, title and picture would no longer fit together.\n\nAll other clips (without a padlock) you can freely move and replace."),
 "ed_status": ("Status line (editor)", "Shows the current state in the clip editor, for example “Replacing clip … (re-rendering)”, “Clip replaced — save with “Apply”” or the progress while saving (the thin bar above it).\n\nWhen there are problems, for example if a clip could not be replaced, the cause is shown here."),
 "status": ("Status line", "Shows at the bottom of the window what the program just did or what is still missing – for example “Please choose a music file first.”, “5 added · Selection: 12 clips, 3 photos” or “Done: Song.mp4 + Song_Short.mp4”.\n\nThis is the first place to look when something does not react as expected."),
},
"es": {
 "music": ("Archivo de música", "Aquí eliges la música a partir de la cual se crea tu vídeo. Pulsa «Elegir archivo de música» o simplemente arrastra el archivo a la ventana del programa; funciona en cualquier paso. Se admiten MP3, WAV, FLAC y OGG.\n\nQué ocurre: el programa detecta el ritmo y coloca todos los cortes exactamente sobre él. El vídeo terminado dura lo mismo que tu canción (más pre-intro y outro, si están activos). El nombre del archivo también sale de aquí: «MiCancion.mp3» se convierte en «MiCancion.mp4», y ese mismo nombre puede mostrarse como título.\n\nImportante: sin música no se puede continuar; el botón «Siguiente» queda gris hasta entonces. Si más tarde eliges otra música, también cambian los nombres de los vídeos nuevos."),
 "help": ("Ajustes (⚙)", "El engranaje abre el menú con todo lo que no forma parte directamente de la creación del vídeo:\n\n• Ayuda con clic: después haz clic en una zona y lee justo este tipo de explicación. El modo termina solo tras una explicación (o con Esc).\n• Guía rápida: los cuatro pasos de un vistazo.\n• Manual PDF: el manual detallado en tu idioma (debe estar junto al EXE).\n• Short desde URL: un short de 45 segundos a partir de un enlace de YouTube en el portapapeles.\n• Abrir carpeta del grupo / Carpeta de salida: abre las carpetas en el Explorador.\n• Idioma / Language: Deutsch, English o Español; se cambia al instante, sin reiniciar. Tu selección se conserva.\n• Restablecer ajustes: todo vuelve al principio (música, selección, formatos, extras, corte). Opcionalmente se puede vaciar la caché. Tus vídeos y clips siempre se conservan.\n• Última versión / Versión móvil: abre las páginas de GitHub para actualizaciones y MVG Móvil."),
 "qhelp": ("Ayuda con clic (?)", "Inicia la ayuda con clic directamente, sin pasar por el engranaje.\n\nCómo funciona: arriba aparece una barra azul. Mueve el ratón por la ventana; la barra indica qué se puede explicar en ese punto. Un clic abre la explicación detallada y después la ayuda con clic se apaga sola. Mientras está activa no se ejecuta nada, así que puedes hacer clic sin peligro en cualquier botón.\n\nPara terminar sin explicación: pulsa Esc o el botón a la derecha de la barra azul."),
 "steps": ("Barra de pasos", "Muestra dónde estás: 1 Inicio, 2 Material, 3 Extras, 4 Listo. Los pasos completados llevan una marca, el paso actual aparece en negrita.\n\nPuedes hacer clic en un paso para saltar directamente a él; por ejemplo, desde el paso 4 volver a los extras para cambiar algo y renderizar de nuevo. Un paso solo es accesible cuando lo necesario está hecho: el paso 2 necesita música, el paso 3 además al menos un clip o una foto, el paso 4 un vídeo terminado.\n\nTodos los ajustes se conservan al ir y volver. Mientras se renderiza un vídeo, la barra está bloqueada."),
 "fmt_normal": ("Normal 16:9", "El clásico formato horizontal para YouTube, televisores y pantallas de ordenador. El vídeo dura lo mismo que tu música.\n\nEn este formato (y en el 9:16 de duración completa) se usan los extras del paso 3: pre-intro, intro, outro y título.\n\nNombre del archivo: TituloDeLaCancion.mp4 en la carpeta output/.\n\nEl primer formato elegido es el vídeo principal: solo este se edita después de renderizar en el paso 4 (mover y reemplazar clips). Si 16:9 está marcado, siempre es el vídeo principal."),
 "fmt_shorts": ("Normal 9:16", "Formato vertical (como sujetas el teléfono) con la duración completa de tu música. Pensado para cuando toda la canción debe verse en el teléfono, por ejemplo como historia o como vídeo vertical largo.\n\nPre-intro, intro, outro y título se usan si están activados en el paso 3.\n\nNombre del archivo: TituloDeLaCancion_9x16.mp4 en la carpeta output/.\n\nSi 16:9 no está marcado, este formato pasa a ser el vídeo principal que editas en el paso 4. Si solo quieres una versión corta para Reels, Shorts o TikTok, usa en su lugar el short de 58 segundos."),
 "src_pool": ("Grupo de clips", "El grupo es una sola carpeta (clips/ dentro de la carpeta del programa) para vídeos y fotos que usas una y otra vez; por ejemplo, tus clips habituales para cada canción.\n\nImportante: el programa se entrega sin clips. La carpeta la llenas tú: «Abrir carpeta» la abre en el Explorador y allí copias los archivos. Las subcarpetas no se tienen en cuenta.\n\nCon «Seleccionar clips» eliges con un clic los clips del grupo para este vídeo. La tarjeta muestra cuántos archivos hay en el grupo y cuántos están seleccionados; cuando hay algo seleccionado, la tarjeta se vuelve azul y lleva una marca."),
 "src_pool_btn": ("Seleccionar clips", "Abre una ventana con miniaturas de todos los vídeos y fotos de la carpeta del grupo. Un clic selecciona o deselecciona un clip (marco azul = seleccionado). «Todos» y «Ninguno» van más rápido.\n\nCon «Aplicar» los clips elegidos del grupo pasan a tu selección de abajo; los clips del grupo deseleccionados se quitan de ella. Los clips de otras fuentes (tus carpetas, archivos arrastrados) no se ven afectados. «Cancelar» lo deja todo como estaba.\n\nSi el grupo está vacío, recibes un aviso y se abre la carpeta del grupo para que la llenes. Crear las miniaturas puede tardar un momento si hay muchos clips."),
 "src_pool_open": ("Abrir carpeta del grupo", "Abre la carpeta del grupo (clips/ dentro de la carpeta del programa) en el Explorador de Windows. Ahí guardas los vídeos y fotos que quieres usar en muchos vídeos.\n\nDespués de copiarlos no tienes que hacer nada más: la próxima vez que pulses «Seleccionar clips», los archivos nuevos aparecen automáticamente. Se admiten MP4, MOV, MKV, AVI y WEBM, así como JPG, PNG y WEBP. Las subcarpetas no se examinan."),
 "src_videos": ("Tus vídeos", "Para material que no está en el grupo; por ejemplo, clips que solo encajan con esta canción.\n\n• Elegir carpeta: añade a tu selección todos los vídeos de una carpeta.\n• Elegir archivos: elige vídeos sueltos (varios a la vez con Ctrl o Mayús).\n• O bien: arrastra archivos o carpetas enteras a la ventana del programa.\n\nLa tarjeta muestra cuántos vídeos propios hay ahora en la selección. Se admiten MP4, MOV, MKV, AVI y WEBM."),
 "src_photos": ("Tus fotos", "Las fotos (JPG, PNG, WEBP) se usan como clips: cada foto permanece en pantalla durante un corte.\n\n• Elegir carpeta: añade todas las fotos de una carpeta.\n• Elegir archivos: elige fotos sueltas.\n• O bien: arrastra las fotos a la ventana.\n\nPara que el vídeo siga siendo dinámico, en cuanto también hay clips de vídeo se aplican dos reglas: una foto solo se usa en cortes de al menos 2 segundos y como máximo van dos fotos seguidas. También es posible un vídeo hecho solo de fotos."),
 "src_folder": ("Elegir carpeta", "Abre un diálogo de Windows en el que eliges una carpeta. Todos los archivos adecuados que contiene (vídeos o fotos) se añaden a tu selección; las subcarpetas no se examinan.\n\nBueno saber: el diálogo de carpetas de Windows muestra solo carpetas, no archivos. Por eso una carpeta llena de vídeos parece vacía en él; es normal. Haz clic en la carpeta y confirma con «Seleccionar carpeta». Lo encontrado lo ves después en «Tu selección».\n\nLos archivos que ya están en la selección no se añaden por segunda vez."),
 "src_files": ("Elegir archivos", "Abre un diálogo de Windows en el que eliges archivos sueltos. Con Ctrl pulsado eliges varios uno a uno, con Mayús un rango entero, con Ctrl+A todos los archivos de la carpeta.\n\nLos archivos elegidos se suman a lo que ya hay en tu selección. Los duplicados se omiten automáticamente."),
 "selection": ("Tu selección", "Aquí ves todo lo que entra en tu vídeo, venga de donde venga: clips del grupo (marcados con ◆), vídeos de tus carpetas, fotos (🖼) y archivos arrastrados.\n\nTodas las entradas tienen el mismo peso: el programa las reparte al azar entre los cortes, evitando en lo posible el mismo clip dos veces seguidas. Si hay más cortes que clips, los clips se repiten.\n\nAñades material con las tres tarjetas de arriba o arrastrando vídeos, fotos o carpetas enteras desde el Explorador a la ventana. La ✕ de una miniatura quita una sola entrada, «Quitar todo» vacía la selección. Los archivos nunca se borran.\n\nConsejo: cuanto más variado el material, más variado el vídeo."),
 "sel_remove": ("Quitar de la selección (✕)", "Saca este clip o esta foto de la selección para el vídeo. El archivo sigue intacto en tu ordenador; no se borra nada.\n\n¿Lo quitaste por error? Vuelve a añadirlo: con la tarjeta correspondiente de arriba o arrastrándolo a la ventana."),
 "sel_clear": ("Quitar todo", "Vacía toda la selección, tras una confirmación. Los archivos se conservan, también los de la carpeta del grupo.\n\nPráctico cuando quieres usar un material completamente distinto para una canción nueva."),
 "speed": ("Velocidad de corte", "Determina la rapidez con que cambian los clips. Todos los cortes se colocan exactamente sobre el ritmo; la velocidad solo decide cuántos tiempos hay entre dos cortes.\n\n• Suave: aprox. cada 3,5 segundos, tranquilo y atmosférico\n• Lento: aprox. cada 2,2 segundos, cinematográfico\n• Normal: aprox. cada 1,7 segundos, equilibrado (por defecto)\n• Rápido: aprox. cada 0,95 segundos, enérgico\n• Aleatorio: cada corte al azar entre 0,95 y 3,5 segundos\n\nConsejo: Suave o Lento van bien con baladas, Normal suele ir bien con bachata y merengue, Rápido con reguetón y música de baile. En los formatos verticales, Suave y Lento se aceleran un poco para que no resulte lento en el teléfono."),
 "summary": ("Resumen", "Muestra de un vistazo qué ocurre al pulsar «Crear vídeo»: qué música, qué formatos, cuánto material, qué extras y qué velocidad de corte.\n\nSi algo no está bien, salta al lugar correcto con la barra de pasos de arriba o con «Atrás». Todo se conserva."),
 "render": ("Crear vídeo", "Inicia el renderizado. El programa analiza la música, coloca los cortes sobre el ritmo, renderiza todas las secciones y une imagen y sonido. Por cada formato elegido se crea un archivo aparte en la carpeta output/.\n\nMientras tanto ves para cada formato si espera, está en curso o ha terminado, además de una barra de progreso y el estado. Los controles quedan bloqueados hasta entonces. Varios formatos tardan proporcionalmente más. Un sonido avisa del final y después pasas automáticamente al paso 4.\n\nSi falla el vídeo principal, ves la causa y puedes volver a los extras o intentarlo de nuevo. Si solo falla un formato adicional (p. ej. el short), se indica en el paso 4."),
 "render_page": ("Progreso", "Aquí ves qué se está renderizando. Cada fila es un formato: ○ espera, ● en curso, ✓ terminado, ✕ error. La barra cuenta todos los formatos juntos; la línea de abajo muestra el paso de trabajo actual (p. ej. «Renderizando segmentos…»).\n\nEn el primer renderizado con clips nuevos puede aparecer «Normalizando …»: los clips en formatos poco habituales se preparan una sola vez y se guardan en la caché. Los siguientes renderizados con los mismos clips son más rápidos.\n\nSi cierras ahora el programa, el vídeo no se terminará."),
 "new_video": ("Nuevo vídeo", "Empieza un vídeo nuevo en el paso 1. Formatos, extras, velocidad de corte y tu selección de clips se conservan; solo tienes que elegir la música nueva. Así haces rápidamente varios vídeos con el mismo material.\n\nEl vídeo terminado se queda en la carpeta output/. El editor de clips se vacía; los cambios aún no aplicados se pierden tras una confirmación.\n\nSi de verdad quieres empezar de cero (también selección y formatos), usa «Restablecer ajustes» en el menú ⚙."),
 "watch": ("Reproducir vídeo", "Abre el vídeo principal que acabas de crear en el reproductor predeterminado de tu ordenador.\n\nDespués de «Aplicar cambios» muestra la versión nueva. Los demás formatos, como el short, están en la «Carpeta de salida»."),
 "ed_apply": ("Aplicar cambios", "Guarda tus cambios en el vídeo: el nuevo orden y todos los clips reemplazados.\n\nEs rápido porque solo se repite el paso de unir, no todo el vídeo. Mientras se guarda, la línea de estado muestra el progreso; por favor, no cierres nada hasta que termine. Después el vídeo de la carpeta output/ queda actualizado y «Reproducir vídeo» muestra la versión nueva.\n\nEl botón solo se activa cuando has cambiado algo. Mientras no apliques, el vídeo no cambia; el aviso amarillo «Cambios pendientes» te lo recuerda."),
 "ed_discard": ("Descartar", "Deshace todos los cambios aún no aplicados: los clips reemplazados vuelven y el orden regresa al último estado guardado.\n\nEl vídeo en sí no se toca; queda exactamente como se guardó por última vez. Los cambios ya aplicados se conservan."),
 "ed_grid": ("Vista de clips", "Cada casilla es una sección de tu vídeo, en el orden en que aparece (de izquierda a derecha, fila por fila). Arriba en la casilla ves su número y su intervalo de tiempo en el vídeo.\n\n• Mover: arrastra una casilla con el botón izquierdo pulsado sobre otra. Las dos intercambian su sitio; el destino se ilumina en azul mientras arrastras.\n• Reemplazar: «Reemplazar clip» cambia el clip por otro al azar de tu selección, exactamente igual de largo. Así el vídeo dura exactamente lo mismo que tu canción y todos los cortes siguen sobre el ritmo.\n• Casillas bloqueadas (candado): pre-intro, intro y outro se quedan en su sitio.\n\nPuedes cambiar tantas veces como quieras. Solo «Aplicar cambios» guarda en el vídeo; «Descartar» deshace todo lo pendiente."),
 "ed_remove": ("Reemplazar clip", "Reemplaza este clip por otro de tu selección. El reemplazo se elige al azar y dura exactamente lo mismo, fotograma a fotograma, que el clip anterior, de modo que el vídeo siga durando lo mismo que la canción y todos los cortes sigan sobre el ritmo. Se prefieren clips que aún no aparecen en el vídeo.\n\nEs rápido porque solo se renderiza de nuevo esta sección. La línea de estado muestra «Reemplazando clip …» y después «Clip reemplazado».\n\nSi no te gusta el clip nuevo, vuelve a pulsar y obtendrás otro. «Descartar» recupera todos los clips reemplazados, «Aplicar cambios» los guarda. Si no hay un reemplazo adecuado (p. ej. solo un clip en la selección), la línea de estado lo indica."),
 "ed_lock": ("Sección bloqueada", "El candado marca pre-intro, intro y outro. Estas secciones son fijas: pre-intro e intro se quedan delante, el outro se queda al final.\n\nEl motivo: pre-intro y outro tienen su propio sonido, que suena antes o después de la música. El intro suena al principio de la canción y el título aparece justo después. Si estas secciones se movieran, sonido, título e imagen dejarían de encajar.\n\nTodos los demás clips (sin candado) puedes moverlos y reemplazarlos libremente."),
 "ed_status": ("Línea de estado (editor)", "Muestra el estado actual en el editor de clips, por ejemplo «Reemplazando clip … (renderizando de nuevo)», «Clip reemplazado — guarda con «Aplicar»» o el progreso al guardar (la barra fina de encima).\n\nSi hay problemas, por ejemplo si no se pudo reemplazar un clip, aquí aparece la causa."),
 "status": ("Línea de estado", "Muestra abajo en la ventana lo que el programa acaba de hacer o lo que aún falta; por ejemplo «Primero elige un archivo de música.», «5 añadidos · Selección: 12 clips, 3 fotos» o «Listo: Cancion.mp4 + Cancion_Short.mp4».\n\nEs el primer sitio donde mirar cuando algo no reacciona como esperas."),
},
}

for _L, _topics in _HELP_V4.items():
    HELP_TOPICS.setdefault(_L, {}).update(_topics)
    for _old in ("reset", "pool", "mood", "pool_count", "manual", "sw_pool", "sw_ext", "sw_photos",
                 "btn_ext", "btn_photos", "count", "sources", "edit", "ed_cancel", "fmt"):
        HELP_TOPICS[_L].pop(_old, None)

HELP_SECTIONS = {
 "de": [
  ["1. Start", "Musik wählen (Button oder ins Fenster ziehen) und die Formate ankreuzen. Standard: 16:9-Video plus 58-s-Short."],
  ["2. Bildmaterial", "Clips und Fotos sammeln: aus dem Pool (Ordner clips/), aus eigenen Ordnern oder per Ziehen ins Fenster. Alles landet in „Deine Auswahl“."],
  ["3. Extras", "Optional Pre-Intro, Intro, Outro und Titel, dazu die Schnittgeschwindigkeit. Dann „Video erstellen“."],
  ["4. Fertig", "Video abspielen, Clips verschieben oder ersetzen und mit „Änderungen übernehmen“ speichern. „Neues Video“ startet mit denselben Einstellungen neu."],
  ["Pool füllen", "Das Programm kommt ohne Clips. Videos und Fotos, die du oft nutzt, in den Ordner clips/ legen (⚙ → Pool-Ordner öffnen)."],
  ["Short per URL", "YouTube-Link kopieren, dann ⚙ → Short per URL – 45-s-Short in output/Shorts."],
  ["Hilfe", "„?“ oben rechts: Bereich anklicken und Erklärung lesen. Im ⚙: Kurzanleitung, PDF, Sprache, Updates, Mobile Version."],
 ],
 "en": [
  ["1. Start", "Choose music (button or drag it into the window) and tick the formats. Default: 16:9 video plus 58 s short."],
  ["2. Footage", "Collect clips and photos: from the pool (folder clips/), from your own folders or by dragging them into the window. Everything ends up in “Your selection”."],
  ["3. Extras", "Optional pre-intro, intro, outro and title, plus the cutting speed. Then “Create video”."],
  ["4. Done", "Play the video, move or replace clips and save with “Apply changes”. “New video” starts again with the same settings."],
  ["Fill the pool", "The program ships without clips. Put videos and photos you use often into the clips/ folder (⚙ → Open pool folder)."],
  ["Short from URL", "Copy a YouTube link, then ⚙ → Short from URL – 45 s short in output/Shorts."],
  ["Help", "“?” at the top right: click an area and read the explanation. In ⚙: quick guide, PDF, language, updates, mobile version."],
 ],
 "es": [
  ["1. Inicio", "Elige la música (botón o arrástrala a la ventana) y marca los formatos. Por defecto: vídeo 16:9 y short de 58 s."],
  ["2. Material", "Reúne clips y fotos: del grupo (carpeta clips/), de tus carpetas o arrastrándolos a la ventana. Todo acaba en «Tu selección»."],
  ["3. Extras", "Opcional: pre-intro, intro, outro y título, además de la velocidad de corte. Luego «Crear vídeo»."],
  ["4. Listo", "Reproduce el vídeo, mueve o reemplaza clips y guarda con «Aplicar cambios». «Nuevo vídeo» empieza de nuevo con los mismos ajustes."],
  ["Llenar el grupo", "El programa se entrega sin clips. Pon los vídeos y fotos que usas a menudo en la carpeta clips/ (⚙ → Abrir carpeta del grupo)."],
  ["Short desde URL", "Copia un enlace de YouTube y luego ⚙ → Short desde URL – short de 45 s en output/Shorts."],
  ["Ayuda", "«?» arriba a la derecha: haz clic en una zona y lee la explicación. En ⚙: guía rápida, PDF, idioma, actualizaciones, versión móvil."],
 ],
}

# Pre-Intro/Intro/Outro: bei leerem Ordner ist der Schalter jetzt gesperrt
for _L, _old, _new in (
        ("de", "Ist der Ordner leer, passiert nichts.", "Ist der Ordner leer, ist der Schalter gesperrt, bis ein Video darin liegt."),
        ("en", "If the folder is empty, nothing happens.", "If the folder is empty, the switch is locked until a video is in it."),
        ("es", "Si la carpeta está vacía, no pasa nada.", "Si la carpeta está vacía, el interruptor queda bloqueado hasta que haya un vídeo en ella.")):
    for _k in ("sw_pre", "sw_intro", "sw_outro"):
        _t, _b = HELP_TOPICS[_L][_k]
        HELP_TOPICS[_L][_k] = (_t, _b.replace(_old, _new))


# ── v4.0 (5 Schritte): Clipfolge vor dem Rendern, Fertig-Seite mit Thumbnail
_HELP_V41 = {
"de": {
 "steps": ("Schrittleiste", "Zeigt, wo du gerade bist: 1 Start, 2 Bildmaterial, 3 Extras, 4 Schnitt, 5 Fertig. Erledigte Schritte bekommen einen Haken, der aktuelle Schritt ist fett.\n\nDu kannst auf einen Schritt klicken, um direkt dorthin zu springen. Ein Schritt ist erst erreichbar, wenn das Nötige davor erledigt ist: Schritt 2 braucht Musik, Schritt 3 und 4 zusätzlich mindestens einen Clip oder ein Foto, Schritt 5 ein fertiges Video.\n\nAlle Einstellungen bleiben beim Hin- und Herspringen erhalten. Änderst du nach dem Planen etwas an Musik, Bildmaterial, Extras, Hauptformat oder Schnitt, fragt das Programm vor dem Neuplanen nach – damit deine Sortierung nicht versehentlich verloren geht. Während gerendert wird, ist die Leiste gesperrt."),
 "fmt_normal": ("Normal 16:9", "Das klassische Querformat für YouTube, Fernseher und Computer-Bildschirme. Das Video ist so lang wie deine Musik.\n\nIn diesem Format (und in 9:16 in voller Länge) werden die Extras aus Schritt 3 verwendet: Pre-Intro, Intro, Outro und Titel.\n\nDateiname: Musiktitel.mp4 im Ordner output/. Dazu speichert das Programm automatisch ein YouTube-Thumbnail (Musiktitel_Thumbnail.jpg, 1280×720).\n\nDas erste angekreuzte Format ist das Hauptvideo: Seine Clipfolge siehst und sortierst du in Schritt 4. Ist 16:9 angekreuzt, ist es immer das Hauptvideo; die anderen Formate werden automatisch zusammengestellt."),
 "fmt_shorts": ("Normal 9:16", "Hochformat (wie auf dem Handy gehalten) in der vollen Länge deiner Musik. Gedacht für Fälle, in denen das ganze Lied am Handy gezeigt werden soll, etwa als Story oder als langes Hochformat-Video.\n\nPre-Intro, Intro, Outro und Titel werden verwendet, wenn sie in Schritt 3 eingeschaltet sind.\n\nDateiname: Musiktitel_9x16.mp4 im Ordner output/.\n\nIst 16:9 nicht angekreuzt, wird dieses Format zum Hauptvideo, dessen Clipfolge du in Schritt 4 sortierst. Zusammen mit 16:9 wird es automatisch zusammengestellt."),
 "fmt_short58": ("Short 9:16 · 58 s", "Ein kurzes Hochformat-Video von genau 58 Sekunden für Instagram Reels, YouTube Shorts und TikTok.\n\nWie der Ausschnitt entsteht: Das Programm sucht automatisch den stärksten Abschnitt deines Songs (meist den Refrain) und schneidet genau dieses Stück heraus. Bild und Ton werden am Anfang weich ein- und am Ende ausgeblendet. Der Short bleibt immer ohne Pre-Intro, Intro, Outro und Titel. Ist der Song kürzer als 58 Sekunden, wird er in voller Länge genommen.\n\nSortieren: Wählst du den Short allein, siehst und sortierst du seine Clipfolge in Schritt 4. Zusammen mit 16:9 oder 9:16 wird er automatisch zusammengestellt.\n\nDateiname: Musiktitel_Short.mp4 im Ordner output/."),
 "summary": ("Zusammenfassung", "Zeigt auf einen Blick, womit dein Video gebaut wird: Musik, Formate, Bildmaterial, Extras und Schnittgeschwindigkeit.\n\nMit „Weiter“ plant das Programm daraus die Clipfolge und zeigt sie dir in Schritt 4 – gerendert wird erst, wenn du dort „Video erstellen“ klickst. Stimmt etwas nicht, springst du über die Schrittleiste oder „Zurück“ an die richtige Stelle."),
 "to_render": ("Weiter zu Schritt 4", "Plant aus deinen Einstellungen die Clipfolge: Die Musik wird analysiert, die Schnitte werden auf den Beat gelegt und jedem Schnitt wird ein Clip oder Foto aus deiner Auswahl zugeordnet. Das dauert nur ein paar Sekunden.\n\nGerendert wird dabei noch nichts. Das passiert erst in Schritt 4 mit „Video erstellen“."),
 "plan_grid": ("Clipfolge", "Jede Kachel ist ein Abschnitt deines Videos, in der Reihenfolge, in der er vorkommt (von links nach rechts, Zeile für Zeile). Oben steht die Zeit im fertigen Video. Das Bild zeigt den Clip an der Stelle, an der er verwendet wird (noch nicht auf das Format zugeschnitten).\n\n• Verschieben: Kachel mit gedrückter linker Maustaste auf eine andere ziehen. Die beiden Clips tauschen die Plätze; die Längen der Plätze bleiben, damit alle Schnitte auf dem Beat bleiben.\n• Tauschen: „Clip tauschen“ setzt einen zufälligen anderen Clip aus deiner Auswahl an diese Stelle.\n• Gesperrt (Schloss): Pre-Intro, Intro und Outro bleiben an ihrem Platz.\n\nVor dem ersten Rendern kostet das Sortieren keine Zeit. Nach dem Rendern wird mit „Änderungen übernehmen“ nur neu gerendert, was sich geändert hat."),
 "plan_swap": ("Clip tauschen", "Setzt einen zufällig gewählten anderen Clip oder ein anderes Foto aus deiner Auswahl an diese Stelle. Die Länge des Abschnitts bleibt gleich, damit das Video so lang bleibt wie der Song und alle Schnitte auf dem Beat bleiben. Bevorzugt werden Clips, die noch nicht im Video vorkommen.\n\nGefällt dir der neue Clip nicht, klicke einfach noch einmal. Fotos werden nur für Abschnitte von mindestens 2 Sekunden genommen, wenn auch Videos in der Auswahl sind.\n\nVor dem ersten Rendern ist das sofort erledigt. Nach dem Rendern wird beim Übernehmen nur dieser eine Abschnitt neu gerendert."),
 "plan_lock": ("Gesperrter Abschnitt", "Das Schloss markiert Pre-Intro, Intro und Outro. Diese Abschnitte sind fest: Pre-Intro und Intro bleiben vorn, das Outro bleibt am Ende.\n\nDer Grund: Pre-Intro und Outro haben ihren eigenen Ton, der vor bzw. nach der Musik läuft. Das Intro läuft am Songanfang, und der Titel wird erst danach eingeblendet. Würden diese Abschnitte verrutschen, passten Ton, Titel und Bild nicht mehr zusammen.\n\nWelches Video als Pre-Intro, Intro oder Outro genommen wird, bestimmst du über die Ordner pre-intro/, intro/ und outro/ (bei mehreren Dateien zufällig)."),
 "render": ("Video erstellen", "Rendert dein Video genau in der Clipfolge, die du siehst. Die Kacheln werden dabei gesperrt und färben sich nacheinander ein, sobald ihr Abschnitt fertig ist – so siehst du, wie das Video zusammengesetzt wird. Darüber laufen Fortschrittsbalken und Status.\n\nFür jedes angekreuzte Format entsteht eine eigene Datei im Ordner output/. Das Hauptvideo kommt zuerst, weitere Formate (z. B. der Short) werden danach automatisch zusammengestellt. Ein Klang meldet das Ende, dann geht es zur Fertig-Seite.\n\nSchlägt etwas fehl, siehst du hier die Ursache und kannst es erneut versuchen. Ein bestehendes Video wird erst überschrieben, wenn das neue komplett fertig ist."),
 "apply": ("Änderungen übernehmen", "Speichert deine Änderungen an der Clipfolge ins fertige Video. Gerendert wird nur, was sich geändert hat: Bei verschobenen Clips werden die vorhandenen Abschnitte nur neu zusammengefügt, bei getauschten Clips wird nur der neue Abschnitt gerendert. Das geht deutlich schneller als ein komplettes Video.\n\nNeu erstellt wird dabei nur das Hauptvideo. Weitere Formate wie der Short bleiben unverändert.\n\nDanach geht es wieder zur Fertig-Seite, das Thumbnail wird aus dem neuen Video erstellt."),
 "discard": ("Verwerfen", "Macht alle noch nicht übernommenen Änderungen rückgängig: Die Clipfolge springt auf den Stand des fertigen Videos zurück. Das Video selbst wird dabei nicht angefasst."),
 "to_done": ("Zur Fertig-Seite", "Es gibt keine offenen Änderungen – das Video entspricht genau der angezeigten Clipfolge. Mit diesem Button geht es zurück zur Fertig-Seite mit Vorschau, Thumbnail und Upload."),
 "render_progress": ("Fortschritt", "Zeigt, was gerade gerendert wird: Der Balken zählt über alle Formate hinweg, die Zeile darunter nennt den aktuellen Arbeitsschritt. Bei mehreren Formaten siehst du für jedes, ob es wartet, läuft oder fertig ist.\n\nBeim ersten Render mit neuen Clips kann „Normalisiere …“ erscheinen: Clips in ungewöhnlichen Formaten werden einmalig vorbereitet und im Cache gespeichert. Danach geht es schneller.\n\nSchließt du das Programm jetzt, wird das Video nicht fertig."),
 "done_preview": ("Vorschau", "Ein Standbild aus deinem fertigen Video. Ein Klick darauf (oder auf ▶) öffnet das Video im Standard-Videoplayer deines Computers – mit Ton.\n\nBeim Querformat (16:9) ist dieses Bild zugleich dein YouTube-Thumbnail."),
 "done_thumb": ("YouTube-Thumbnail", "Beim 16:9-Video speichert das Programm automatisch ein Thumbnail in YouTube-Größe (1280×720, JPG) neben das Video: Musiktitel_Thumbnail.jpg im Ordner output/. Gewählt wird ein möglichst helles, scharfes Standbild aus einem deiner Clips – kein Pre-Intro, Intro oder Outro.\n\n„↻ Anderes Bild“ nimmt das nächstbeste Standbild und überschreibt die gespeicherte Datei. Klick dich einfach durch, bis dir eins gefällt.\n\nBei reinem Hochformat (9:16 oder nur Short) wird kein Thumbnail gespeichert, weil YouTube bei Shorts das Bild aus dem Video nimmt. Dann siehst du hier nur ein Vorschaubild."),
 "done_files": ("Deine Dateien", "Alle Videos, die gerade entstanden sind, mit Format und Länge. Sie liegen im Ordner output/ – „Output-Ordner“ öffnet ihn.\n\nKonnte ein weiteres Format nicht erstellt werden (z. B. der Short), steht das hier mit der Ursache."),
 "done_edit": ("Clips ändern", "Bringt dich zurück zu Schritt 4 mit der Clipfolge deines fertigen Videos. Dort verschiebst oder tauschst du Clips und klickst „Änderungen übernehmen“ – gerendert wird dann nur, was sich geändert hat.\n\nAlternativ geht das jederzeit über die Schrittleiste oben (Schritt 4)."),
 "new_video": ("Neues Video", "Beginnt ein neues Video bei Schritt 1. Formate, Extras, Schnittgeschwindigkeit und deine Clip-Auswahl bleiben erhalten – du wählst nur die neue Musik. So machst du schnell mehrere Videos mit demselben Material.\n\nDas fertige Video und das Thumbnail bleiben im Ordner output/ erhalten.\n\nMöchtest du wirklich alles auf Anfang setzen (auch Auswahl und Formate), nutze im ⚙-Menü „Einstellungen zurücksetzen“."),
 "watch": ("Video ansehen", "Öffnet das fertige Hauptvideo im Standard-Videoplayer deines Computers, mit Ton. Weitere Formate wie den Short findest du über „Output-Ordner“."),
},
"en": {
 "steps": ("Step bar", "Shows where you are: 1 Start, 2 Footage, 3 Extras, 4 Edit, 5 Done. Completed steps get a tick, the current step is bold.\n\nYou can click a step to jump straight there. A step can only be reached once what it needs is done: step 2 needs music, steps 3 and 4 additionally at least one clip or photo, step 5 a finished video.\n\nAll settings are kept while you jump back and forth. If you change music, footage, extras, main format or cutting after planning, the program asks before re-planning – so your arrangement is not lost by accident. While rendering, the bar is locked."),
 "fmt_normal": ("Normal 16:9", "The classic landscape format for YouTube, TVs and computer screens. The video is as long as your music.\n\nIn this format (and in full-length 9:16) the extras from step 3 are used: pre-intro, intro, outro and title.\n\nFile name: SongTitle.mp4 in the output/ folder. The program also saves a YouTube thumbnail automatically (SongTitle_Thumbnail.jpg, 1280×720).\n\nThe first ticked format is the main video: you see and arrange its clip order in step 4. If 16:9 is ticked, it is always the main video; the other formats are put together automatically."),
 "fmt_shorts": ("Normal 9:16", "Portrait format (as you hold a phone) at the full length of your music. Meant for when the whole song should be shown on a phone, for example as a story or a long portrait video.\n\nPre-intro, intro, outro and title are used if they are switched on in step 3.\n\nFile name: SongTitle_9x16.mp4 in the output/ folder.\n\nIf 16:9 is not ticked, this format becomes the main video whose clip order you arrange in step 4. Together with 16:9 it is put together automatically."),
 "fmt_short58": ("Short 9:16 · 58 s", "A short portrait video of exactly 58 seconds for Instagram Reels, YouTube Shorts and TikTok.\n\nHow the excerpt is made: the program automatically finds the strongest section of your song (usually the chorus) and cuts exactly that piece. Picture and sound fade in softly at the start and out at the end. The short never has pre-intro, intro, outro or title. If the song is shorter than 58 seconds, it is used in full.\n\nArranging: if you choose the short alone, you see and arrange its clip order in step 4. Together with 16:9 or 9:16 it is put together automatically.\n\nFile name: SongTitle_Short.mp4 in the output/ folder."),
 "summary": ("Summary", "Shows at a glance what your video is built from: music, formats, footage, extras and cutting speed.\n\nWith “Next” the program plans the clip order from this and shows it to you in step 4 – rendering only starts when you click “Create video” there. If something is not right, jump to the right place via the step bar or “Back”."),
 "to_render": ("Next to step 4", "Plans the clip order from your settings: the music is analysed, the cuts are placed on the beat and every cut gets a clip or photo from your selection. This only takes a few seconds.\n\nNothing is rendered yet. That only happens in step 4 with “Create video”."),
 "plan_grid": ("Clip order", "Every tile is one section of your video, in the order in which it appears (left to right, row by row). At the top you see the time in the finished video. The picture shows the clip at the spot where it is used (not yet cropped to the format).\n\n• Move: drag a tile with the left mouse button held down onto another one. The two clips swap places; the lengths of the places stay, so all cuts stay on the beat.\n• Replace: “Replace clip” puts a random other clip from your selection at this spot.\n• Locked (padlock): pre-intro, intro and outro stay in place.\n\nBefore the first render, arranging costs no time. After rendering, “Apply changes” only re-renders what has changed."),
 "plan_swap": ("Replace clip", "Puts a randomly chosen other clip or photo from your selection at this spot. The length of the section stays the same, so the video stays as long as the song and all cuts stay on the beat. Clips that do not yet appear in the video are preferred.\n\nIf you do not like the new clip, just click again. Photos are only used for sections of at least 2 seconds when there are also videos in the selection.\n\nBefore the first render this is instant. After rendering, only this one section is rendered again when you apply."),
 "plan_lock": ("Locked section", "The padlock marks pre-intro, intro and outro. These sections are fixed: pre-intro and intro stay at the front, the outro stays at the end.\n\nThe reason: pre-intro and outro have their own sound, which plays before or after the music. The intro plays at the start of the song, and the title only appears after it. If these sections moved, sound, title and picture would no longer fit together.\n\nWhich video is used as pre-intro, intro or outro is decided by the folders pre-intro/, intro/ and outro/ (random if there are several files)."),
 "render": ("Create video", "Renders your video in exactly the clip order you see. The tiles are locked and light up one after another as soon as their section is finished – so you see how the video is put together. Above them run the progress bar and status.\n\nFor every ticked format a separate file is created in the output/ folder. The main video comes first, other formats (e.g. the short) are put together automatically afterwards. A sound signals the end, then you go to the Done page.\n\nIf something fails, you see the cause here and can try again. An existing video is only overwritten once the new one is completely finished."),
 "apply": ("Apply changes", "Saves your changes to the clip order into the finished video. Only what has changed is rendered: with moved clips the existing sections are only joined again, with replaced clips only the new section is rendered. This is much faster than a complete video.\n\nOnly the main video is created again. Other formats such as the short stay unchanged.\n\nAfterwards you go back to the Done page; the thumbnail is made from the new video."),
 "discard": ("Discard", "Undoes all changes not yet applied: the clip order returns to the state of the finished video. The video itself is not touched."),
 "to_done": ("To the Done page", "There are no pending changes – the video matches the displayed clip order exactly. This button takes you back to the Done page with preview, thumbnail and upload."),
 "render_progress": ("Progress", "Shows what is being rendered: the bar counts across all formats, the line below names the current work step. With several formats you see for each one whether it is waiting, running or finished.\n\nOn the first render with new clips you may see “Normalizing …”: clips in unusual formats are prepared once and stored in the cache. After that it is faster.\n\nIf you close the program now, the video will not be finished."),
 "done_preview": ("Preview", "A still image from your finished video. A click on it (or on ▶) opens the video in your computer's default video player – with sound.\n\nFor landscape (16:9) this image is also your YouTube thumbnail."),
 "done_thumb": ("YouTube thumbnail", "For the 16:9 video the program automatically saves a thumbnail in YouTube size (1280×720, JPG) next to the video: SongTitle_Thumbnail.jpg in the output/ folder. It picks a bright, sharp still from one of your clips – never pre-intro, intro or outro.\n\n“↻ Other picture” takes the next best still and overwrites the saved file. Just click through until you like one.\n\nFor portrait only (9:16 or only the short) no thumbnail is saved, because YouTube takes the picture from the video for Shorts. Then you only see a preview here."),
 "done_files": ("Your files", "All videos that were just created, with format and length. They are in the output/ folder – “Output folder” opens it.\n\nIf an additional format could not be created (e.g. the short), this is shown here with the cause."),
 "done_edit": ("Change clips", "Takes you back to step 4 with the clip order of your finished video. There you move or replace clips and click “Apply changes” – only what has changed is rendered.\n\nYou can also get there any time via the step bar at the top (step 4)."),
 "new_video": ("New video", "Starts a new video at step 1. Formats, extras, cutting speed and your clip selection are kept – you only choose the new music. This way you quickly make several videos with the same material.\n\nThe finished video and the thumbnail stay in the output/ folder.\n\nIf you really want to reset everything (including selection and formats), use “Reset settings” in the ⚙ menu."),
 "watch": ("Watch video", "Opens the finished main video in your computer's default video player, with sound. Other formats such as the short are in the “Output folder”."),
},
"es": {
 "steps": ("Barra de pasos", "Muestra dónde estás: 1 Inicio, 2 Material, 3 Extras, 4 Montaje, 5 Listo. Los pasos completados llevan una marca, el paso actual aparece en negrita.\n\nPuedes hacer clic en un paso para saltar directamente a él. Un paso solo es accesible cuando lo necesario está hecho: el paso 2 necesita música, los pasos 3 y 4 además al menos un clip o una foto, el paso 5 un vídeo terminado.\n\nTodos los ajustes se conservan al ir y volver. Si después de planificar cambias música, material, extras, formato principal o corte, el programa pregunta antes de volver a planificar, para que tu orden no se pierda por error. Mientras se renderiza, la barra está bloqueada."),
 "fmt_normal": ("Normal 16:9", "El clásico formato horizontal para YouTube, televisores y pantallas de ordenador. El vídeo dura lo mismo que tu música.\n\nEn este formato (y en el 9:16 de duración completa) se usan los extras del paso 3: pre-intro, intro, outro y título.\n\nNombre del archivo: TituloDeLaCancion.mp4 en la carpeta output/. Además el programa guarda automáticamente una miniatura para YouTube (TituloDeLaCancion_Thumbnail.jpg, 1280×720).\n\nEl primer formato marcado es el vídeo principal: su orden de clips lo ves y ordenas en el paso 4. Si 16:9 está marcado, siempre es el vídeo principal; los demás formatos se montan automáticamente."),
 "fmt_shorts": ("Normal 9:16", "Formato vertical (como sujetas el teléfono) con la duración completa de tu música. Pensado para cuando toda la canción debe verse en el teléfono, por ejemplo como historia o como vídeo vertical largo.\n\nPre-intro, intro, outro y título se usan si están activados en el paso 3.\n\nNombre del archivo: TituloDeLaCancion_9x16.mp4 en la carpeta output/.\n\nSi 16:9 no está marcado, este formato pasa a ser el vídeo principal cuyo orden de clips ordenas en el paso 4. Junto con 16:9 se monta automáticamente."),
 "fmt_short58": ("Short 9:16 · 58 s", "Un vídeo vertical corto de exactamente 58 segundos para Instagram Reels, YouTube Shorts y TikTok.\n\nCómo se crea el fragmento: el programa busca automáticamente la sección más fuerte de tu canción (normalmente el estribillo) y corta justo ese trozo. Imagen y sonido entran suavemente al principio y se atenúan al final. El short nunca lleva pre-intro, intro, outro ni título. Si la canción dura menos de 58 segundos, se usa completa.\n\nOrdenar: si eliges solo el short, ves y ordenas su orden de clips en el paso 4. Junto con 16:9 o 9:16 se monta automáticamente.\n\nNombre del archivo: TituloDeLaCancion_Short.mp4 en la carpeta output/."),
 "summary": ("Resumen", "Muestra de un vistazo con qué se construye tu vídeo: música, formatos, material, extras y velocidad de corte.\n\nCon «Siguiente» el programa planifica el orden de clips y te lo muestra en el paso 4; solo se renderiza cuando allí pulsas «Crear vídeo». Si algo no está bien, salta al lugar correcto con la barra de pasos o con «Atrás»."),
 "to_render": ("Siguiente al paso 4", "Planifica el orden de clips a partir de tus ajustes: se analiza la música, los cortes se colocan sobre el ritmo y a cada corte se le asigna un clip o una foto de tu selección. Solo tarda unos segundos.\n\nTodavía no se renderiza nada. Eso ocurre en el paso 4 con «Crear vídeo»."),
 "plan_grid": ("Orden de clips", "Cada casilla es una sección de tu vídeo, en el orden en que aparece (de izquierda a derecha, fila por fila). Arriba ves el tiempo en el vídeo terminado. La imagen muestra el clip en el punto donde se usa (aún sin recortar al formato).\n\n• Mover: arrastra una casilla con el botón izquierdo pulsado sobre otra. Los dos clips intercambian su sitio; la duración de los sitios se mantiene, así todos los cortes siguen sobre el ritmo.\n• Reemplazar: «Reemplazar clip» pone en este sitio otro clip al azar de tu selección.\n• Bloqueado (candado): pre-intro, intro y outro se quedan en su sitio.\n\nAntes del primer renderizado, ordenar no cuesta tiempo. Después, «Aplicar cambios» solo vuelve a renderizar lo que ha cambiado."),
 "plan_swap": ("Reemplazar clip", "Pone en este sitio otro clip u otra foto de tu selección, elegido al azar. La duración de la sección se mantiene, de modo que el vídeo siga durando lo mismo que la canción y todos los cortes sigan sobre el ritmo. Se prefieren clips que aún no aparecen en el vídeo.\n\nSi no te gusta el clip nuevo, vuelve a pulsar. Las fotos solo se usan en secciones de al menos 2 segundos cuando también hay vídeos en la selección.\n\nAntes del primer renderizado es instantáneo. Después, al aplicar, solo se renderiza de nuevo esta sección."),
 "plan_lock": ("Sección bloqueada", "El candado marca pre-intro, intro y outro. Estas secciones son fijas: pre-intro e intro se quedan delante, el outro se queda al final.\n\nEl motivo: pre-intro y outro tienen su propio sonido, que suena antes o después de la música. El intro suena al principio de la canción y el título aparece justo después. Si estas secciones se movieran, sonido, título e imagen dejarían de encajar.\n\nQué vídeo se usa como pre-intro, intro u outro lo decides con las carpetas pre-intro/, intro/ y outro/ (al azar si hay varios archivos)."),
 "render": ("Crear vídeo", "Renderiza tu vídeo exactamente en el orden de clips que ves. Las casillas se bloquean y se iluminan una tras otra en cuanto su sección está lista; así ves cómo se monta el vídeo. Encima corren la barra de progreso y el estado.\n\nPor cada formato marcado se crea un archivo aparte en la carpeta output/. El vídeo principal va primero; los demás formatos (p. ej. el short) se montan después automáticamente. Un sonido avisa del final y luego pasas a la página Listo.\n\nSi algo falla, aquí ves la causa y puedes intentarlo de nuevo. Un vídeo existente solo se sobrescribe cuando el nuevo está completamente terminado."),
 "apply": ("Aplicar cambios", "Guarda tus cambios del orden de clips en el vídeo terminado. Solo se renderiza lo que ha cambiado: con clips movidos solo se vuelven a unir las secciones existentes; con clips reemplazados solo se renderiza la sección nueva. Es mucho más rápido que un vídeo completo.\n\nSolo se vuelve a crear el vídeo principal. Los demás formatos, como el short, no cambian.\n\nDespués vuelves a la página Listo; la miniatura se crea a partir del vídeo nuevo."),
 "discard": ("Descartar", "Deshace todos los cambios aún no aplicados: el orden de clips vuelve al estado del vídeo terminado. El vídeo en sí no se toca."),
 "to_done": ("A la página Listo", "No hay cambios pendientes: el vídeo coincide exactamente con el orden de clips mostrado. Este botón te lleva de vuelta a la página Listo con vista previa, miniatura y subida."),
 "render_progress": ("Progreso", "Muestra qué se está renderizando: la barra cuenta todos los formatos juntos y la línea de abajo indica el paso de trabajo actual. Con varios formatos ves para cada uno si espera, está en curso o ha terminado.\n\nEn el primer renderizado con clips nuevos puede aparecer «Normalizando …»: los clips en formatos poco habituales se preparan una sola vez y se guardan en la caché. Después va más rápido.\n\nSi cierras ahora el programa, el vídeo no se terminará."),
 "done_preview": ("Vista previa", "Una imagen fija de tu vídeo terminado. Un clic en ella (o en ▶) abre el vídeo en el reproductor predeterminado de tu ordenador, con sonido.\n\nEn formato horizontal (16:9) esta imagen es también tu miniatura de YouTube."),
 "done_thumb": ("Miniatura de YouTube", "Para el vídeo 16:9 el programa guarda automáticamente una miniatura en tamaño YouTube (1280×720, JPG) junto al vídeo: TituloDeLaCancion_Thumbnail.jpg en la carpeta output/. Elige una imagen fija clara y nítida de uno de tus clips, nunca del pre-intro, intro u outro.\n\n«↻ Otra imagen» toma la siguiente mejor imagen y sobrescribe el archivo guardado. Ve probando hasta que te guste una.\n\nCon solo formato vertical (9:16 o solo el short) no se guarda miniatura, porque YouTube toma la imagen del vídeo en los Shorts. Entonces aquí solo ves una vista previa."),
 "done_files": ("Tus archivos", "Todos los vídeos que se acaban de crear, con formato y duración. Están en la carpeta output/; «Carpeta de salida» la abre.\n\nSi no se pudo crear un formato adicional (p. ej. el short), aquí se indica con la causa."),
 "done_edit": ("Cambiar clips", "Te lleva de vuelta al paso 4 con el orden de clips de tu vídeo terminado. Allí mueves o reemplazas clips y pulsas «Aplicar cambios»; solo se renderiza lo que ha cambiado.\n\nTambién puedes llegar en cualquier momento con la barra de pasos de arriba (paso 4)."),
 "new_video": ("Nuevo vídeo", "Empieza un vídeo nuevo en el paso 1. Formatos, extras, velocidad de corte y tu selección de clips se conservan; solo eliges la música nueva. Así haces rápidamente varios vídeos con el mismo material.\n\nEl vídeo terminado y la miniatura se quedan en la carpeta output/.\n\nSi de verdad quieres empezar de cero (también selección y formatos), usa «Restablecer ajustes» en el menú ⚙."),
 "watch": ("Ver vídeo", "Abre el vídeo principal terminado en el reproductor predeterminado de tu ordenador, con sonido. Los demás formatos, como el short, están en la «Carpeta de salida»."),
},
}
for _L, _topics in _HELP_V41.items():
    HELP_TOPICS[_L].update(_topics)
    for _old in ("ed_apply", "ed_discard", "ed_grid", "ed_remove", "ed_lock", "ed_status", "render_page"):
        HELP_TOPICS[_L].pop(_old, None)

HELP_SECTIONS = {
 "de": [
  ["1. Start", "Musik wählen (Button oder ins Fenster ziehen) und die Formate ankreuzen. Standard: 16:9-Video plus 58-s-Short."],
  ["2. Bildmaterial", "Clips und Fotos sammeln: aus dem Pool (Ordner clips/), aus eigenen Ordnern oder per Ziehen ins Fenster."],
  ["3. Extras", "Optional Pre-Intro, Intro, Outro und Titel, dazu die Schnittgeschwindigkeit."],
  ["4. Schnitt", "Die geplante Clipfolge ansehen, Clips verschieben oder tauschen, dann „Video erstellen“. Ein Laufband zeigt live, welcher Abschnitt gerade entsteht."],
  ["5. Fertig", "Video ansehen, Thumbnail wählen (wird automatisch gespeichert), hochladen. „Clips ändern“ rendert nur das Geänderte neu."],
  ["Pool füllen", "Das Programm kommt ohne Clips. Videos und Fotos, die du oft nutzt, in den Ordner clips/ legen (⚙ → Pool-Ordner öffnen)."],
  ["Hilfe", "„?“ oben rechts: Bereich anklicken und Erklärung lesen. Im ⚙: Kurzanleitung, PDF, Short per URL, Sprache, Updates, Mobile Version."],
 ],
 "en": [
  ["1. Start", "Choose music (button or drag it into the window) and tick the formats. Default: 16:9 video plus 58 s short."],
  ["2. Footage", "Collect clips and photos: from the pool (folder clips/), from your own folders or by dragging them into the window."],
  ["3. Extras", "Optional pre-intro, intro, outro and title, plus the cutting speed."],
  ["4. Edit", "Look at the planned clip order, move or replace clips, then “Create video”. A band shows live which section is being created."],
  ["5. Done", "Watch the video, pick a thumbnail (saved automatically), upload. “Change clips” only re-renders what changed."],
  ["Fill the pool", "The program ships without clips. Put videos and photos you use often into the clips/ folder (⚙ → Open pool folder)."],
  ["Help", "“?” at the top right: click an area and read the explanation. In ⚙: quick guide, PDF, short from URL, language, updates, mobile version."],
 ],
 "es": [
  ["1. Inicio", "Elige la música (botón o arrástrala a la ventana) y marca los formatos. Por defecto: vídeo 16:9 y short de 58 s."],
  ["2. Material", "Reúne clips y fotos: del grupo (carpeta clips/), de tus carpetas o arrastrándolos a la ventana."],
  ["3. Extras", "Opcional: pre-intro, intro, outro y título, además de la velocidad de corte."],
  ["4. Montaje", "Mira el orden de clips planificado, mueve o reemplaza clips y luego «Crear vídeo». Una cinta muestra en directo qué sección se está creando."],
  ["5. Listo", "Ver el vídeo, elegir la miniatura (se guarda sola), subirlo. «Cambiar clips» solo vuelve a renderizar lo cambiado."],
  ["Llenar el grupo", "El programa se entrega sin clips. Pon los vídeos y fotos que usas a menudo en la carpeta clips/ (⚙ → Abrir carpeta del grupo)."],
  ["Ayuda", "«?» arriba a la derecha: haz clic en una zona y lee la explicación. En ⚙: guía rápida, PDF, short desde URL, idioma, actualizaciones, versión móvil."],
 ],
}

# Fertig-Seite: Vorschau/Thumbnail-Generator ohne Abspielen; ⚙-Menü mit Darstellung
_HELP_V42 = {
 "de": {
  "done_preview": ("Vorschau", "Ein Standbild aus deinem fertigen Video. Bei Hochformat (9:16 oder nur Short) wird es nicht gespeichert, denn bei Shorts nimmt YouTube das Bild direkt aus dem Video.\n\nAbgespielt wird das Video über „▶ Video ansehen“ unten rechts – im Standard-Videoplayer deines Computers, mit Ton."),
  "done_thumb": ("Thumbnail-Generator", "Beim 16:9-Video erstellt das Programm automatisch ein Thumbnail in YouTube-Größe (1280×720, JPG) und speichert es neben dem Video: Musiktitel_Thumbnail.jpg im Ordner output/. Gewählt wird ein möglichst farbiges, helles und klares Standbild aus einem deiner Clips – kein Pre-Intro, Intro oder Outro.\n\n„↻ Anderes Bild“ nimmt das nächste Standbild und überschreibt die gespeicherte Datei sofort. Klick dich einfach durch, bis dir eins gefällt – es gibt nichts extra zu speichern.\n\nDas Thumbnail lädst du bei YouTube beim Hochladen unter „Thumbnail“ hoch."),
  "help": ("Einstellungen (⚙)", "Das Zahnrad öffnet das Menü mit allem, was nicht direkt zum Videobau gehört:\n\n• Klick-Hilfe: danach einen Bereich anklicken und genau diese Art Erklärung lesen. Der Modus endet nach einer Erklärung von selbst (oder mit Esc).\n• Kurzanleitung: die fünf Schritte im Überblick.\n• PDF-Anleitung: das ausführliche Handbuch in deiner Sprache (muss neben der EXE liegen).\n• Short per URL: 45-Sekunden-Short aus einem YouTube-Link in der Zwischenablage.\n• Pool-Ordner öffnen / Output-Ordner: öffnet die Ordner im Explorer.\n• Sprache / Language: Deutsch, English oder Español – wird sofort umgestellt, ohne Neustart.\n• Darstellung: System (wie Windows), Hell oder Dunkel – wird sofort umgestellt und gemerkt.\n• Einstellungen zurücksetzen: alles auf Anfang (Musik, Auswahl, Formate, Extras, Schnitt). Der Cache kann dabei optional geleert werden. Deine Videos und Clips bleiben immer erhalten.\n• Neueste Version / Mobile Version: öffnet die GitHub-Seiten für Updates und MVG Mobil."),
 },
 "en": {
  "done_preview": ("Preview", "A still image from your finished video. For portrait (9:16 or only the short) it is not saved, because YouTube takes the picture directly from the video for Shorts.\n\nThe video is played with “▶ Watch video” at the bottom right – in your computer's default video player, with sound."),
  "done_thumb": ("Thumbnail generator", "For the 16:9 video the program automatically creates a thumbnail in YouTube size (1280×720, JPG) and saves it next to the video: SongTitle_Thumbnail.jpg in the output/ folder. It picks a colourful, bright and clear still from one of your clips – never pre-intro, intro or outro.\n\n“↻ Other picture” takes the next still and overwrites the saved file immediately. Just click through until you like one – there is nothing extra to save.\n\nYou upload the thumbnail on YouTube under “Thumbnail” while uploading the video."),
  "help": ("Settings (⚙)", "The gear opens the menu with everything that is not directly part of building the video:\n\n• Click help: then click an area and read exactly this kind of explanation. The mode ends by itself after one explanation (or with Esc).\n• Quick guide: the five steps at a glance.\n• PDF manual: the detailed manual in your language (must be next to the EXE).\n• Short from URL: a 45-second short from a YouTube link on the clipboard.\n• Open pool folder / Output folder: opens the folders in Explorer.\n• Language / Sprache: Deutsch, English or Español – switches immediately, no restart.\n• Appearance: System (like Windows), Light or Dark – switches immediately and is remembered.\n• Reset settings: everything back to the start (music, selection, formats, extras, cutting). The cache can optionally be emptied. Your videos and clips are always kept.\n• Latest version / Mobile version: opens the GitHub pages for updates and MVG Mobile."),
 },
 "es": {
  "done_preview": ("Vista previa", "Una imagen fija de tu vídeo terminado. En formato vertical (9:16 o solo el short) no se guarda, porque en los Shorts YouTube toma la imagen directamente del vídeo.\n\nEl vídeo se reproduce con «▶ Ver vídeo» abajo a la derecha, en el reproductor predeterminado de tu ordenador y con sonido."),
  "done_thumb": ("Generador de miniaturas", "Para el vídeo 16:9 el programa crea automáticamente una miniatura en tamaño YouTube (1280×720, JPG) y la guarda junto al vídeo: TituloDeLaCancion_Thumbnail.jpg en la carpeta output/. Elige una imagen fija colorida, clara y nítida de uno de tus clips, nunca del pre-intro, intro u outro.\n\n«↻ Otra imagen» toma la siguiente imagen y sobrescribe al instante el archivo guardado. Ve probando hasta que te guste una; no hay que guardar nada más.\n\nLa miniatura la subes en YouTube en «Miniatura» al subir el vídeo."),
  "help": ("Ajustes (⚙)", "El engranaje abre el menú con todo lo que no forma parte directamente de la creación del vídeo:\n\n• Ayuda con clic: después haz clic en una zona y lee justo este tipo de explicación. El modo termina solo tras una explicación (o con Esc).\n• Guía rápida: los cinco pasos de un vistazo.\n• Manual PDF: el manual detallado en tu idioma (debe estar junto al EXE).\n• Short desde URL: un short de 45 segundos a partir de un enlace de YouTube en el portapapeles.\n• Abrir carpeta del grupo / Carpeta de salida: abre las carpetas en el Explorador.\n• Idioma / Language: Deutsch, English o Español; se cambia al instante, sin reiniciar.\n• Apariencia: Sistema (como Windows), Claro u Oscuro; se cambia al instante y se recuerda.\n• Restablecer ajustes: todo vuelve al principio (música, selección, formatos, extras, corte). Opcionalmente se puede vaciar la caché. Tus vídeos y clips siempre se conservan.\n• Última versión / Versión móvil: abre las páginas de GitHub para actualizaciones y MVG Móvil."),
 },
}
for _L, _topics in _HELP_V42.items():
    HELP_TOPICS[_L].update(_topics)

# Klick-Hilfe komplett: Seiten als Ganzes, Kopfzeile, Zurück/Weiter
_HELP_V43 = {
 "de": {
  "header": ("Musik Video Generator", "Oben links siehst du Programmname und Slogan („Simply cut to the beat.“). Die Versionsnummer steht in der Titelleiste des Fensters. Rechts liegen die beiden Hilfe-Knöpfe:\n\n• „?“ startet die Klick-Hilfe: Danach klickst du auf einen beliebigen Bereich und bekommst genau diese Art Erklärung.\n• „⚙“ öffnet das Menü mit Kurzanleitung, PDF-Anleitung, Short per URL, Ordnern, Sprache, Darstellung (Hell/Dunkel), Zurücksetzen und den Links zu Updates und zur Mobile Version."),
  "page_welcome": ("Willkommen", "Diese Seite erscheint nur beim allerersten Start. Links siehst du die Schritte im Überblick, rechts den wichtigsten Hinweis: Das Programm wird ohne Clips ausgeliefert.\n\nLege Videos und Fotos, die du für viele Videos verwendest, in den Pool-Ordner (clips/ im Programmordner). „Pool-Ordner öffnen“ öffnet ihn direkt im Explorer. Material, das nur zu einem Song passt, musst du nicht in den Pool legen – das wählst du später in Schritt 2 aus einem beliebigen Ordner oder ziehst es einfach ins Fenster.\n\nMit „Los geht's“ startest du bei Schritt 1."),
  "start_welcome": ("Los geht's", "Schließt die Willkommensseite und startet bei Schritt 1. Die Seite erscheint danach nicht mehr. Den Pool-Ordner erreichst du jederzeit über das ⚙-Menü oder in Schritt 2."),
  "page_start": ("Schritt 1 – Start", "Hier legst du fest, was entstehen soll:\n\n1. Musikdatei: Button „Musikdatei wählen“ oder Datei einfach ins Fenster ziehen. Das Video bekommt den Namen der Musikdatei.\n2. Ausgabeformate: 16:9, 9:16 in voller Länge und/oder der 58-Sekunden-Short. Mehrfachauswahl ist möglich, jedes Format wird eine eigene Datei.\n\nUnten siehst du, unter welchen Namen die Videos im Ordner output/ gespeichert werden. „Weiter“ wird aktiv, sobald eine Musikdatei gewählt ist."),
  "page_media": ("Schritt 2 – Bildmaterial", "Hier sammelst du alle Clips und Fotos für das Video. Oben sind die drei Quellen: Clip-Pool, eigene Videos und eigene Fotos. Du kannst sie beliebig kombinieren. Alles Gewählte landet unten in „Deine Auswahl“.\n\nDer schnellste Weg: Videos, Fotos oder ganze Ordner aus dem Explorer direkt ins Programmfenster ziehen.\n\nWelcher Clip an welche Stelle kommt, entscheidet sich erst in Schritt 4 – dort kannst du die Reihenfolge noch ändern. „Weiter“ wird aktiv, sobald mindestens ein Clip oder Foto ausgewählt ist."),
  "page_extras": ("Schritt 3 – Extras", "Hier legst du fest, was zusätzlich ins Video kommt, und wie schnell geschnitten wird.\n\n• Pre-Intro: eigenes Video mit Ton vor der Musik (verlängert das Video)\n• Intro: stummer Clip am Songanfang (gehört zur Songlänge)\n• Outro: eigenes Video mit Ton nach der Musik (verlängert das Video)\n• Titel: der Musiktitel unten links\n• Schnittgeschwindigkeit: von Sanft bis Schnell oder Random\n\nIst ein Ordner leer, ist der Schalter gesperrt. Rechts siehst du die Zusammenfassung. „Weiter“ plant die Clipfolge – gerendert wird noch nicht."),
  "page_render": ("Schritt 4 – Schnitt", "Hier siehst du, wie dein Video aus den Clips zusammengesetzt wird – jede Kachel ist ein Abschnitt, in der Reihenfolge im Video. Du kannst Clips verschieben (Kachel auf eine andere ziehen) oder mit „Clip tauschen“ austauschen. Pre-Intro, Intro und Outro sind gesperrt.\n\n„Video erstellen“ rendert genau diese Clipfolge. Die Kacheln färben sich dabei nacheinander ein, sobald ihr Abschnitt fertig ist.\n\nNach dem Rendern kommst du über „Clips ändern“ hierher zurück. Dann speichert „Änderungen übernehmen“ deine Änderungen – gerendert wird nur, was sich geändert hat."),
  "page_done": ("Schritt 5 – Fertig", "Dein Video ist fertig. Links ist der Thumbnail-Generator (beim 16:9-Video): Das YouTube-Thumbnail wird automatisch im Ordner output/ gespeichert, „↻ Anderes Bild“ wählt ein anderes. Rechts stehen alle erstellten Dateien.\n\n• „▶ Video ansehen“ öffnet das Video im Player\n• „Output-Ordner“ öffnet den Ordner mit allen Dateien\n• „YouTube Upload“ öffnet die Upload-Seite im Browser\n• „✏ Clips ändern“ führt zurück zu Schritt 4\n• „＋ Neues Video“ beginnt mit neuer Musik und behält deine Einstellungen"),
  "back": ("Zurück", "Geht einen Schritt zurück. Alle Einstellungen und deine Auswahl bleiben dabei erhalten – du kannst jederzeit wieder nach vorn.\n\nÄnderst du nach dem Planen etwas Grundlegendes (Musik, Bildmaterial, Extras, Hauptformat oder Schnitt), wird die Clipfolge beim nächsten Mal neu geplant. Hast du schon sortiert oder gerendert, fragt das Programm vorher nach."),
  "next": ("Weiter", "Geht zum nächsten Schritt. Der Button ist erst aktiv, wenn alles Nötige erledigt ist: in Schritt 1 eine Musikdatei, in Schritt 2 mindestens ein Clip oder Foto. Fehlt noch etwas, steht der Hinweis unten in der Statuszeile."),
  "retry": ("Erneut versuchen", "Startet den fehlgeschlagenen Vorgang noch einmal. Die Ursache steht direkt darüber in rot. Häufige Gründe: Eine Datei aus der Auswahl wurde verschoben oder gelöscht, ein Clip ist beschädigt, oder der Datenträger ist voll. Behebe die Ursache und versuche es erneut – oder geh mit „Zurück“ zu den vorigen Schritten. Details stehen außerdem in mvg_error.log im Programmordner."),
 },
 "en": {
  "header": ("Music Video Generator", "At the top left you see the program name and slogan (“Simply cut to the beat.”). The version number is shown in the window's title bar. On the right are the two help buttons:\n\n• “?” starts click help: then you click any area and get exactly this kind of explanation.\n• “⚙” opens the menu with quick guide, PDF manual, short from URL, folders, language, appearance (light/dark), reset and the links to updates and the mobile version."),
  "page_welcome": ("Welcome", "This page only appears on the very first start. On the left you see the steps at a glance, on the right the most important note: the program ships without clips.\n\nPut videos and photos you use for many videos into the pool folder (clips/ in the program folder). “Open pool folder” opens it directly in Explorer. Material that only fits one song does not need to go into the pool – you pick it later in step 2 from any folder or simply drag it into the window.\n\n“Let's go” starts at step 1."),
  "start_welcome": ("Let's go", "Closes the welcome page and starts at step 1. The page does not appear again. You can reach the pool folder any time via the ⚙ menu or in step 2."),
  "page_start": ("Step 1 – Start", "Here you decide what will be created:\n\n1. Music file: the “Choose music file” button or simply drag the file into the window. The video gets the name of the music file.\n2. Output formats: 16:9, full-length 9:16 and/or the 58-second short. Several can be chosen, each format becomes its own file.\n\nAt the bottom you see the names under which the videos are saved in the output/ folder. “Next” becomes active once a music file is chosen."),
  "page_media": ("Step 2 – Footage", "Here you collect all clips and photos for the video. At the top are the three sources: clip pool, your videos and your photos. You can combine them freely. Everything you choose ends up below in “Your selection”.\n\nThe quickest way: drag videos, photos or whole folders from Explorer straight into the program window.\n\nWhich clip goes where is only decided in step 4 – there you can still change the order. “Next” becomes active once at least one clip or photo is selected."),
  "page_extras": ("Step 3 – Extras", "Here you decide what else goes into the video and how fast it is cut.\n\n• Pre-intro: your own video with sound before the music (makes the video longer)\n• Intro: silent clip at the start of the song (part of the song length)\n• Outro: your own video with sound after the music (makes the video longer)\n• Title: the song title at the bottom left\n• Cutting speed: from Gentle to Fast or Random\n\nIf a folder is empty, its switch is locked. On the right you see the summary. “Next” plans the clip order – nothing is rendered yet."),
  "page_render": ("Step 4 – Edit", "Here you see how your video is put together from the clips – every tile is one section, in the order of the video. You can move clips (drag a tile onto another one) or swap them with “Replace clip”. Pre-intro, intro and outro are locked.\n\n“Create video” renders exactly this clip order. The tiles light up one after another as soon as their section is finished.\n\nAfter rendering you come back here with “Change clips”. Then “Apply changes” saves your changes – only what has changed is rendered."),
  "page_done": ("Step 5 – Done", "Your video is finished. On the left is the thumbnail generator (for the 16:9 video): the YouTube thumbnail is saved automatically in the output/ folder, “↻ Other picture” picks another one. On the right are all created files.\n\n• “▶ Watch video” opens the video in the player\n• “Output folder” opens the folder with all files\n• “YouTube upload” opens the upload page in the browser\n• “✏ Change clips” takes you back to step 4\n• “＋ New video” starts with new music and keeps your settings"),
  "back": ("Back", "Goes back one step. All settings and your selection are kept – you can move forward again at any time.\n\nIf you change something fundamental after planning (music, footage, extras, main format or cutting), the clip order is re-planned next time. If you have already arranged or rendered, the program asks first."),
  "next": ("Next", "Goes to the next step. The button only becomes active once everything needed is done: in step 1 a music file, in step 2 at least one clip or photo. If something is missing, the hint is shown in the status line at the bottom."),
  "retry": ("Try again", "Starts the failed process again. The cause is shown directly above in red. Common reasons: a file from the selection was moved or deleted, a clip is damaged, or the disk is full. Fix the cause and try again – or go back to earlier steps with “Back”. Details are also in mvg_error.log in the program folder."),
 },
 "es": {
  "header": ("Generador de vídeos musicales", "Arriba a la izquierda ves el nombre del programa y el eslogan («Simply cut to the beat.»). El número de versión aparece en la barra de título de la ventana. A la derecha están los dos botones de ayuda:\n\n• «?» inicia la ayuda con clic: después haces clic en cualquier zona y recibes justo este tipo de explicación.\n• «⚙» abre el menú con guía rápida, manual PDF, short desde URL, carpetas, idioma, apariencia (claro/oscuro), restablecer y los enlaces a actualizaciones y a la versión móvil."),
  "page_welcome": ("Bienvenida", "Esta página solo aparece en el primerísimo inicio. A la izquierda ves los pasos de un vistazo y a la derecha el aviso más importante: el programa se entrega sin clips.\n\nPon los vídeos y fotos que usas en muchos vídeos en la carpeta del grupo (clips/ dentro de la carpeta del programa). «Abrir carpeta del grupo» la abre directamente en el Explorador. El material que solo encaja con una canción no tiene que ir al grupo: lo eliges después en el paso 2 desde cualquier carpeta o simplemente lo arrastras a la ventana.\n\n«Empezar» te lleva al paso 1."),
  "start_welcome": ("Empezar", "Cierra la página de bienvenida y empieza en el paso 1. La página no vuelve a aparecer. La carpeta del grupo la tienes siempre en el menú ⚙ o en el paso 2."),
  "page_start": ("Paso 1 – Inicio", "Aquí decides qué se va a crear:\n\n1. Archivo de música: botón «Elegir archivo de música» o arrastra el archivo a la ventana. El vídeo recibe el nombre del archivo de música.\n2. Formatos de salida: 16:9, 9:16 de duración completa y/o el short de 58 segundos. Puedes elegir varios; cada formato es un archivo aparte.\n\nAbajo ves con qué nombres se guardan los vídeos en la carpeta output/. «Siguiente» se activa en cuanto hay un archivo de música elegido."),
  "page_media": ("Paso 2 – Material", "Aquí reúnes todos los clips y fotos para el vídeo. Arriba están las tres fuentes: grupo de clips, tus vídeos y tus fotos. Puedes combinarlas libremente. Todo lo elegido acaba abajo en «Tu selección».\n\nLo más rápido: arrastra vídeos, fotos o carpetas enteras desde el Explorador directamente a la ventana del programa.\n\nQué clip va en qué lugar se decide en el paso 4, donde aún puedes cambiar el orden. «Siguiente» se activa en cuanto hay al menos un clip o una foto seleccionados."),
  "page_extras": ("Paso 3 – Extras", "Aquí decides qué más entra en el vídeo y lo rápido que se corta.\n\n• Pre-intro: vídeo propio con sonido antes de la música (alarga el vídeo)\n• Intro: clip mudo al principio de la canción (forma parte de su duración)\n• Outro: vídeo propio con sonido después de la música (alarga el vídeo)\n• Título: el título de la canción abajo a la izquierda\n• Velocidad de corte: de Suave a Rápido o Aleatorio\n\nSi una carpeta está vacía, su interruptor está bloqueado. A la derecha ves el resumen. «Siguiente» planifica el orden de clips; todavía no se renderiza."),
  "page_render": ("Paso 4 – Montaje", "Aquí ves cómo se monta tu vídeo con los clips: cada casilla es una sección, en el orden del vídeo. Puedes mover clips (arrastrar una casilla sobre otra) o cambiarlos con «Reemplazar clip». Pre-intro, intro y outro están bloqueados.\n\n«Crear vídeo» renderiza exactamente este orden de clips. Las casillas se iluminan una tras otra en cuanto su sección está lista.\n\nDespués de renderizar vuelves aquí con «Cambiar clips». Entonces «Aplicar cambios» guarda tus cambios y solo se renderiza lo que ha cambiado."),
  "page_done": ("Paso 5 – Listo", "Tu vídeo está terminado. A la izquierda está el generador de miniaturas (en el vídeo 16:9): la miniatura de YouTube se guarda automáticamente en la carpeta output/ y «↻ Otra imagen» elige otra. A la derecha están todos los archivos creados.\n\n• «▶ Ver vídeo» abre el vídeo en el reproductor\n• «Carpeta de salida» abre la carpeta con todos los archivos\n• «Subir a YouTube» abre la página de subida en el navegador\n• «✏ Cambiar clips» te lleva de vuelta al paso 4\n• «＋ Nuevo vídeo» empieza con música nueva y conserva tus ajustes"),
  "back": ("Atrás", "Retrocede un paso. Todos los ajustes y tu selección se conservan; puedes volver a avanzar cuando quieras.\n\nSi después de planificar cambias algo básico (música, material, extras, formato principal o corte), el orden de clips se vuelve a planificar la próxima vez. Si ya has ordenado o renderizado, el programa pregunta antes."),
  "next": ("Siguiente", "Pasa al siguiente paso. El botón solo se activa cuando todo lo necesario está hecho: en el paso 1 un archivo de música, en el paso 2 al menos un clip o una foto. Si falta algo, el aviso aparece abajo en la línea de estado."),
  "retry": ("Intentar de nuevo", "Vuelve a iniciar el proceso que falló. La causa aparece justo encima en rojo. Motivos frecuentes: un archivo de la selección se movió o se borró, un clip está dañado o el disco está lleno. Corrige la causa e inténtalo de nuevo, o vuelve a los pasos anteriores con «Atrás». Los detalles también están en mvg_error.log en la carpeta del programa."),
 },
}
for _L, _topics in _HELP_V43.items():
    HELP_TOPICS[_L].update(_topics)

# Pool-Kategorien (optionale Unterordner) und „Verwerfen“ führt zurück zur Fertig-Seite
_HELP_V44 = {
 "de": {
  "src_pool": ("Clip-Pool", "Der Pool ist dein Ordner clips/ im Programmordner für Videos und Fotos, die du immer wieder verwendest – zum Beispiel deine Standard-Clips für jeden Song.\n\nWichtig: Das Programm wird ohne Clips ausgeliefert. Den Ordner füllst du selbst: „Ordner öffnen“ öffnet ihn im Explorer, dort einfach Dateien hineinkopieren.\n\nOrdnung nach Wunsch: Du kannst im Pool-Ordner Unterordner anlegen, zum Beispiel „Strand“, „Stadt“ oder „Nacht“. Jeder Unterordner wird im Auswahlfenster zu einem eigenen Reiter. Ohne Unterordner gibt es keine Reiter – dann sieht alles aus wie gewohnt. Berücksichtigt wird nur eine Ebene (Unterordner in Unterordnern nicht).\n\nMit „Clips auswählen“ suchst du dir aus dem Pool per Klick die Clips für dieses Video aus. Die Karte zeigt an, wie viele Dateien im Pool liegen (alle Unterordner zusammen) und wie viele davon gerade ausgewählt sind; ist etwas ausgewählt, wird die Karte blau und bekommt einen Haken."),
  "src_pool_btn": ("Clips auswählen", "Öffnet ein Fenster mit Vorschaubildern der Videos und Fotos im Pool-Ordner. Ein Klick wählt einen Clip aus oder wieder ab (blauer Rahmen = ausgewählt).\n\nHast du im Pool Unterordner angelegt, erscheinen oben Reiter: „Alle“ zeigt alles, jeder weitere Reiter nur den Inhalt eines Unterordners. „Weitere“ zeigt Dateien, die direkt im Pool-Ordner liegen. Die Auswahl gilt über alle Reiter hinweg – du kannst also aus mehreren Kategorien Clips sammeln. „Alle“ und „Keine“ unten wirken nur auf den gerade sichtbaren Reiter. Das Programm merkt sich, welchen Reiter du zuletzt benutzt hast, und lädt die Vorschaubilder nur für den sichtbaren Reiter – das geht bei vielen Clips schneller.\n\nMit „Übernehmen“ landen die gewählten Pool-Clips in deiner Auswahl unten; abgewählte Pool-Clips werden daraus entfernt. Clips aus anderen Quellen (eigene Ordner, hineingezogene Dateien) bleiben davon unberührt. „Abbrechen“ lässt alles, wie es war.\n\nIst der Pool leer, bekommst du einen Hinweis und der Pool-Ordner öffnet sich, damit du ihn füllen kannst. Vorschaubilder werden einmal erzeugt und dann gespeichert; beim ersten Öffnen kann es bei vielen Clips einen Moment dauern."),
  "src_pool_open": ("Pool-Ordner öffnen", "Öffnet den Pool-Ordner (clips/ im Programmordner) im Windows-Explorer. Dort legst du Videos und Fotos ab, die du für viele Videos verwenden möchtest.\n\nNach dem Kopieren musst du nichts weiter tun: Beim nächsten Klick auf „Clips auswählen“ erscheinen die neuen Dateien automatisch. Unterstützt werden MP4, MOV, MKV, AVI und WEBM sowie JPG, PNG und WEBP.\n\nTipp: Lege Unterordner an (z. B. „Strand“, „Stadt“), dann bekommst du im Auswahlfenster für jeden Unterordner einen eigenen Reiter. Unterordner in Unterordnern werden nicht durchsucht, leere Unterordner bekommen keinen Reiter."),
  "pool_tabs": ("Kategorien", "Jeder Reiter steht für einen Unterordner in deinem Pool-Ordner. „Alle“ zeigt sämtliche Pool-Dateien, „Weitere“ die Dateien, die direkt im Pool-Ordner liegen (nicht in einem Unterordner).\n\nDeine Auswahl bleibt beim Wechseln erhalten – du kannst in mehreren Reitern Clips anklicken und alles zusammen übernehmen. Die Zahl oben rechts zählt alle ausgewählten Clips, egal in welchem Reiter.\n\nBei vielen Unterordnern brechen die Reiter einfach in eine weitere Zeile um. Hinter dem Namen steht, wie viele Clips du in diesem Reiter ausgewählt hast (z. B. „Strand · 3“); der aktive Reiter ist blau. Neue Kategorien legst du an, indem du im Pool-Ordner einen Unterordner erstellst und Dateien hineinlegst."),
  "discard": ("Verwerfen", "Macht alle noch nicht übernommenen Änderungen rückgängig: Die Clipfolge springt auf den Stand des fertigen Videos zurück und du landest wieder auf der Fertig-Seite. Das Video selbst wird dabei nicht angefasst."),
 },
 "en": {
  "src_pool": ("Clip pool", "The pool is your clips/ folder in the program folder for videos and photos you use again and again – for example your standard clips for every song.\n\nImportant: the program ships without clips. You fill the folder yourself: “Open folder” opens it in Explorer, then simply copy files into it.\n\nOrganise it your way: you can create subfolders in the pool folder, for example “Beach”, “City” or “Night”. Each subfolder becomes its own tab in the selection window. Without subfolders there are no tabs – everything looks as usual. Only one level is used (subfolders inside subfolders are not).\n\nWith “Select clips” you pick the clips for this video from the pool with a click. The card shows how many files are in the pool (all subfolders together) and how many of them are currently selected; when something is selected, the card turns blue and gets a tick."),
  "src_pool_btn": ("Select clips", "Opens a window with thumbnails of the videos and photos in the pool folder. One click selects or deselects a clip (blue frame = selected).\n\nIf you have created subfolders in the pool, tabs appear at the top: “All” shows everything, every other tab only the contents of one subfolder. “More” shows files that lie directly in the pool folder. The selection works across all tabs – so you can collect clips from several categories. “All” and “None” at the bottom only affect the tab you are looking at. The program remembers which tab you used last and only loads thumbnails for the visible tab – this is faster with many clips.\n\nWith “Apply” the chosen pool clips go into your selection below; deselected pool clips are removed from it. Clips from other sources (your own folders, dropped files) are not affected. “Cancel” leaves everything as it was.\n\nIf the pool is empty, you get a notice and the pool folder opens so you can fill it. Thumbnails are created once and then stored; the first time, it can take a moment with many clips."),
  "src_pool_open": ("Open pool folder", "Opens the pool folder (clips/ in the program folder) in Windows Explorer. Put videos and photos there that you want to use for many videos.\n\nAfter copying you do not need to do anything else: the next time you click “Select clips”, the new files appear automatically. MP4, MOV, MKV, AVI and WEBM as well as JPG, PNG and WEBP are supported.\n\nTip: create subfolders (e.g. “Beach”, “City”) and you get a separate tab for each subfolder in the selection window. Subfolders inside subfolders are not searched, empty subfolders get no tab."),
  "pool_tabs": ("Categories", "Each tab stands for a subfolder in your pool folder. “All” shows every pool file, “More” the files that lie directly in the pool folder (not in a subfolder).\n\nYour selection is kept when you switch – you can click clips in several tabs and apply everything together. The number at the top right counts all selected clips, no matter which tab they are in.\n\nWith many subfolders the tabs simply wrap onto another line. After the name you see how many clips you have selected in that tab (e.g. “Beach · 3”); the active tab is blue. You create new categories by making a subfolder in the pool folder and putting files into it."),
  "discard": ("Discard", "Undoes all changes not yet applied: the clip order returns to the state of the finished video and you are taken back to the Done page. The video itself is not touched."),
 },
 "es": {
  "src_pool": ("Grupo de clips", "El grupo es tu carpeta clips/ dentro de la carpeta del programa para vídeos y fotos que usas una y otra vez; por ejemplo, tus clips habituales para cada canción.\n\nImportante: el programa se entrega sin clips. La carpeta la llenas tú: «Abrir carpeta» la abre en el Explorador y allí copias los archivos.\n\nOrdénalo a tu gusto: puedes crear subcarpetas en la carpeta del grupo, por ejemplo «Playa», «Ciudad» o «Noche». Cada subcarpeta se convierte en una pestaña propia en la ventana de selección. Sin subcarpetas no hay pestañas y todo se ve como siempre. Solo se tiene en cuenta un nivel (las subcarpetas dentro de subcarpetas no).\n\nCon «Seleccionar clips» eliges con un clic los clips del grupo para este vídeo. La tarjeta muestra cuántos archivos hay en el grupo (todas las subcarpetas juntas) y cuántos están seleccionados; cuando hay algo seleccionado, la tarjeta se vuelve azul y lleva una marca."),
  "src_pool_btn": ("Seleccionar clips", "Abre una ventana con miniaturas de los vídeos y fotos de la carpeta del grupo. Un clic selecciona o deselecciona un clip (marco azul = seleccionado).\n\nSi has creado subcarpetas en el grupo, arriba aparecen pestañas: «Todos» lo muestra todo y cada una de las demás solo el contenido de una subcarpeta. «Otros» muestra los archivos que están directamente en la carpeta del grupo. La selección vale para todas las pestañas, así que puedes reunir clips de varias categorías. «Todos» y «Ninguno» de abajo solo afectan a la pestaña visible. El programa recuerda qué pestaña usaste por última vez y solo carga las miniaturas de la pestaña visible, lo que va más rápido con muchos clips.\n\nCon «Aplicar» los clips elegidos del grupo pasan a tu selección de abajo; los clips del grupo deseleccionados se quitan de ella. Los clips de otras fuentes (tus carpetas, archivos arrastrados) no se ven afectados. «Cancelar» lo deja todo como estaba.\n\nSi el grupo está vacío, recibes un aviso y se abre la carpeta del grupo para que la llenes. Las miniaturas se crean una vez y luego se guardan; la primera vez puede tardar un momento si hay muchos clips."),
  "src_pool_open": ("Abrir carpeta del grupo", "Abre la carpeta del grupo (clips/ dentro de la carpeta del programa) en el Explorador de Windows. Ahí guardas los vídeos y fotos que quieres usar en muchos vídeos.\n\nDespués de copiarlos no tienes que hacer nada más: la próxima vez que pulses «Seleccionar clips», los archivos nuevos aparecen automáticamente. Se admiten MP4, MOV, MKV, AVI y WEBM, así como JPG, PNG y WEBP.\n\nConsejo: crea subcarpetas (p. ej. «Playa», «Ciudad») y en la ventana de selección tendrás una pestaña para cada una. Las subcarpetas dentro de subcarpetas no se examinan y las subcarpetas vacías no reciben pestaña."),
  "pool_tabs": ("Categorías", "Cada pestaña corresponde a una subcarpeta de tu carpeta del grupo. «Todos» muestra todos los archivos del grupo y «Otros» los que están directamente en la carpeta del grupo (no en una subcarpeta).\n\nTu selección se mantiene al cambiar de pestaña: puedes marcar clips en varias pestañas y aplicarlo todo junto. El número de arriba a la derecha cuenta todos los clips seleccionados, estén en la pestaña que estén.\n\nCon muchas subcarpetas, las pestañas simplemente pasan a otra línea. Detrás del nombre ves cuántos clips has seleccionado en esa pestaña (p. ej. «Playa · 3»); la pestaña activa es azul. Para crear categorías nuevas, crea una subcarpeta en la carpeta del grupo y pon archivos dentro."),
  "discard": ("Descartar", "Deshace todos los cambios aún no aplicados: el orden de clips vuelve al estado del vídeo terminado y regresas a la página Listo. El vídeo en sí no se toca."),
 },
}
for _L, _topics in _HELP_V44.items():
    HELP_TOPICS[_L].update(_topics)

# Kurzanleitung umfasst fünf Schritte
for _L, _old, _new in (("de", "die vier Schritte im Überblick", "die fünf Schritte im Überblick"),
                       ("en", "the four steps at a glance", "the five steps at a glance"),
                       ("es", "los cuatro pasos de un vistazo", "los cinco pasos de un vistazo")):
    if "help" in HELP_TOPICS[_L]:
        _t, _b = HELP_TOPICS[_L]["help"]
        HELP_TOPICS[_L]["help"] = (_t, _b.replace(_old, _new))

# Fortschrittslisten (Planen/Rendern) und Laufband
_HELP_V45 = {
 "de": {
  "plan_steps": ("Clipfolge wird geplant", "Hier siehst du Schritt für Schritt, was das Programm gerade vorbereitet:\n\n1. Musik analysieren – Tempo und Beats deines Songs werden erkannt. Dieser Schritt meldet keinen Zwischenstand, deshalb läuft der Balken hin und her, bis er fertig ist. Beim zweiten Mal mit demselben Song geht es schneller.\n2. Besten Abschnitt für den Short finden – nur beim Short 9:16 · 58 s, wenn der Song länger ist.\n3. Schnitte auf den Beat legen – nach der eingestellten Schnittgeschwindigkeit.\n4. Extras auswählen – nur wenn Pre-Intro, Intro oder Outro eingeschaltet sind.\n5. Clips und Fotos verteilen – jeder Schnitt bekommt einen Clip oder ein Foto.\n6. Vorschaubilder vorbereiten – mit Zähler, damit die Übersicht danach gleich vollständig ist.\n\nDie aktuelle Nummer ist blau, erledigte Schritte bekommen einen grünen Haken. Danach erscheint automatisch die Clipfolge zum Sortieren. Während des Planens ist „Zurück“ gesperrt."),
  "render_band": ("Laufband", "Das Band zeigt dein Video Abschnitt für Abschnitt, während es entsteht. Der Abschnitt, der gerade gerendert wird, steht im linken Drittel – blau umrandet, mit ▶ vor dem Namen. Fertige Abschnitte bekommen einen grünen Haken und laufen nach links aus dem Bild, die nächsten kommen blass von rechts nach.\n\nDer Streifen darunter ist das ganze Video auf einen Blick: Jeder Block ist ein Abschnitt (breit = lang), grün = fertig, blau = läuft gerade. Darunter steht, wie viele Abschnitte schon fertig sind.\n\nAbschnitte, die schon aus einem früheren Durchgang vorliegen (z. B. bei „Änderungen übernehmen“), sind sofort fertig – dann läuft das Band einfach schneller durch.\n\nWird ein weiteres Format automatisch mit erstellt (z. B. der Short), wechselt das Band danach zu dessen Abschnitten."),
  "render_progress": ("Fortschritt", "Die Liste zeigt die Arbeitsschritte beim Rendern – jeder mit eigenem Balken. Je nach Einstellung: Pre-Intro, Intro, Clips rendern (mit Zähler, z. B. 8 / 22), Outro, Abschnitte zusammenfügen und Musik anlegen bzw. Musik und Titel einfügen. Weitere Formate, die automatisch mit erstellt werden (z. B. der Short), haben eine eigene Zeile mit Prozentangabe.\n\nOben rechts steht der Gesamtfortschritt über alle Formate in Prozent. Blau = läuft, grüner Haken = fertig, rotes ✕ = Fehler.\n\nBeim ersten Render mit neuen Clips können Clips in ungewöhnlichen Formaten einmalig vorbereitet werden; danach geht es schneller. Schließt du das Programm jetzt, wird das Video nicht fertig."),
  "render": ("Video erstellen", "Rendert dein Video genau in der Clipfolge, die du siehst. Statt des Rasters erscheint dabei ein Laufband: Der Abschnitt, der gerade entsteht, steht vorn, fertige laufen nach links hinaus. Darunter zeigt eine Liste jeden Arbeitsschritt mit eigenem Balken.\n\nFür jedes angekreuzte Format entsteht eine eigene Datei im Ordner output/. Das Hauptvideo kommt zuerst, weitere Formate (z. B. der Short) werden danach automatisch zusammengestellt. Ein Klang meldet das Ende, dann geht es zur Fertig-Seite.\n\nSchlägt etwas fehl, siehst du hier die Ursache und kannst es erneut versuchen. Ein bestehendes Video wird erst überschrieben, wenn das neue komplett fertig ist."),
  "page_render": ("Schritt 4 – Schnitt", "Hier siehst du, wie dein Video aus den Clips zusammengesetzt wird – jede Kachel ist ein Abschnitt, in der Reihenfolge im Video. Du kannst Clips verschieben (Kachel auf eine andere ziehen) oder mit „Clip tauschen“ austauschen. Pre-Intro, Intro und Outro sind gesperrt.\n\n„Video erstellen“ rendert genau diese Clipfolge. Währenddessen läuft ein Band mit den Abschnitten durchs Bild, darunter stehen die Arbeitsschritte als Liste.\n\nNach dem Rendern kommst du über „Clips ändern“ hierher zurück. Dann speichert „Änderungen übernehmen“ deine Änderungen – gerendert wird nur, was sich geändert hat."),
 },
 "en": {
  "plan_steps": ("Planning the clip order", "Here you see step by step what the program is preparing right now:\n\n1. Analyse music – the tempo and beats of your song are detected. This step reports no intermediate progress, so the bar moves back and forth until it is done. The second time with the same song it is faster.\n2. Find the best part for the short – only for the Short 9:16 · 58 s, if the song is longer.\n3. Place cuts on the beat – according to the chosen cutting speed.\n4. Choose extras – only if pre-intro, intro or outro are switched on.\n5. Distribute clips and photos – every cut gets a clip or a photo.\n6. Prepare thumbnails – with a counter, so the overview is complete straight away.\n\nThe current number is blue, finished steps get a green tick. Then the clip order for sorting appears automatically. “Back” is locked while planning."),
  "render_band": ("Band", "The band shows your video section by section while it is being created. The section being rendered right now stands in the left third – framed in blue, with ▶ in front of its name. Finished sections get a green tick and run out of the picture to the left, the next ones come in pale from the right.\n\nThe strip below is the whole video at a glance: every block is one section (wide = long), green = finished, blue = running. Below it you see how many sections are already done.\n\nSections that already exist from an earlier run (e.g. with “Apply changes”) are finished immediately – then the band simply runs through faster.\n\nIf another format is created automatically as well (e.g. the short), the band then switches to its sections."),
  "render_progress": ("Progress", "The list shows the work steps while rendering – each with its own bar. Depending on your settings: pre-intro, intro, render clips (with a counter, e.g. 8 / 22), outro, join sections and add music or add music and title. Other formats created automatically as well (e.g. the short) have their own line with a percentage.\n\nAt the top right you see the overall progress across all formats in percent. Blue = running, green tick = done, red ✕ = error.\n\nOn the first render with new clips, clips in unusual formats may be prepared once; after that it is faster. If you close the program now, the video will not be finished."),
  "render": ("Create video", "Renders your video in exactly the clip order you see. Instead of the grid a band appears: the section being created stands at the front, finished ones run out to the left. Below it a list shows every work step with its own bar.\n\nFor every ticked format a separate file is created in the output/ folder. The main video comes first, other formats (e.g. the short) are put together automatically afterwards. A sound signals the end, then you go to the Done page.\n\nIf something fails, you see the cause here and can try again. An existing video is only overwritten once the new one is completely finished."),
  "page_render": ("Step 4 – Edit", "Here you see how your video is put together from the clips – every tile is one section, in the order of the video. You can move clips (drag a tile onto another one) or swap them with “Replace clip”. Pre-intro, intro and outro are locked.\n\n“Create video” renders exactly this clip order. Meanwhile a band with the sections runs through the picture, with the work steps listed below it.\n\nAfter rendering you come back here with “Change clips”. Then “Apply changes” saves your changes – only what has changed is rendered."),
 },
 "es": {
  "plan_steps": ("Planificando el orden de clips", "Aquí ves paso a paso lo que el programa está preparando:\n\n1. Analizar la música: se detectan el tempo y los beats de tu canción. Este paso no informa de un progreso intermedio, por eso la barra va y viene hasta que termina. La segunda vez con la misma canción va más rápido.\n2. Buscar la mejor parte para el short: solo con el Short 9:16 · 58 s, si la canción es más larga.\n3. Poner los cortes en el beat: según la velocidad de corte elegida.\n4. Elegir extras: solo si están activados pre-intro, intro u outro.\n5. Repartir clips y fotos: cada corte recibe un clip o una foto.\n6. Preparar miniaturas: con contador, para que la vista general esté completa enseguida.\n\nEl número actual es azul y los pasos terminados reciben una marca verde. Después aparece automáticamente el orden de clips para ordenar. Mientras se planifica, «Atrás» está bloqueado."),
  "render_band": ("Cinta", "La cinta muestra tu vídeo sección a sección mientras se crea. La sección que se está renderizando está en el tercio izquierdo, con marco azul y ▶ delante del nombre. Las secciones terminadas reciben una marca verde y salen de la imagen por la izquierda; las siguientes entran pálidas por la derecha.\n\nLa franja de abajo es el vídeo entero de un vistazo: cada bloque es una sección (ancho = largo), verde = terminada, azul = en curso. Debajo pone cuántas secciones están ya listas.\n\nLas secciones que ya existen de una pasada anterior (p. ej. con «Aplicar cambios») están listas al instante; entonces la cinta simplemente pasa más rápido.\n\nSi se crea automáticamente otro formato (p. ej. el short), la cinta cambia después a sus secciones."),
  "render_progress": ("Progreso", "La lista muestra los pasos de trabajo al renderizar, cada uno con su propia barra. Según los ajustes: pre-intro, intro, renderizar clips (con contador, p. ej. 8 / 22), outro, unir secciones y añadir música o añadir música y título. Los demás formatos que se crean automáticamente (p. ej. el short) tienen su propia línea con porcentaje.\n\nArriba a la derecha ves el progreso total de todos los formatos en porcentaje. Azul = en curso, marca verde = listo, ✕ rojo = error.\n\nEn el primer renderizado con clips nuevos, los clips en formatos poco habituales pueden prepararse una vez; después va más rápido. Si cierras ahora el programa, el vídeo no se terminará."),
  "render": ("Crear vídeo", "Renderiza tu vídeo exactamente en el orden de clips que ves. En lugar de la cuadrícula aparece una cinta: la sección que se está creando va delante y las terminadas salen por la izquierda. Debajo, una lista muestra cada paso de trabajo con su propia barra.\n\nPor cada formato marcado se crea un archivo aparte en la carpeta output/. El vídeo principal va primero; los demás formatos (p. ej. el short) se montan después automáticamente. Un sonido avisa del final y luego pasas a la página Listo.\n\nSi algo falla, aquí ves la causa y puedes intentarlo de nuevo. Un vídeo existente solo se sobrescribe cuando el nuevo está completamente terminado."),
  "page_render": ("Paso 4 – Montaje", "Aquí ves cómo se monta tu vídeo con los clips: cada casilla es una sección, en el orden del vídeo. Puedes mover clips (arrastrar una casilla sobre otra) o cambiarlos con «Reemplazar clip». Pre-intro, intro y outro están bloqueados.\n\n«Crear vídeo» renderiza exactamente este orden de clips. Mientras tanto, una cinta con las secciones recorre la imagen y debajo aparecen los pasos de trabajo en forma de lista.\n\nDespués de renderizar vuelves aquí con «Cambiar clips». Entonces «Aplicar cambios» guarda tus cambios y solo se renderiza lo que ha cambiado."),
 },
}
for _L, _topics in _HELP_V45.items():
    HELP_TOPICS[_L].update(_topics)

# Formate nach dem Rendern geändert
_HELP_V46 = {
 "de": {"render_missing": ("Fehlende Formate erstellen", "Du hast nach dem Rendern in Schritt 1 ein weiteres Format angekreuzt, zum Beispiel den Short. Dieses Format gibt es noch nicht – oben steht „Noch nicht erstellt: …“.\n\n„▶ Fehlende Formate erstellen“ erzeugt nur diese fehlenden Dateien. Das Hauptvideo wird dabei nicht neu gerendert, deine Clipfolge bleibt, wie sie ist. Hast du zusätzlich Clips geändert, erledigt „Änderungen übernehmen“ beides in einem Durchgang.\n\nUmgekehrt gilt: Wählst du ein Format nach dem Rendern ab, wird es auf der Fertig-Seite nicht mehr angezeigt. Die Datei bleibt aber im Ordner output/ – gelöscht wird nichts.")},
 "en": {"render_missing": ("Create missing formats", "After rendering you ticked another format in step 1, for example the short. This format does not exist yet – at the top it says “Not created yet: …”.\n\n“▶ Create missing formats” creates only these missing files. The main video is not rendered again and your clip order stays as it is. If you also changed clips, “Apply changes” does both in one go.\n\nThe other way round: if you untick a format after rendering, it is no longer shown on the Done page. The file stays in the output/ folder, though – nothing is deleted.")},
 "es": {"render_missing": ("Crear formatos que faltan", "Después de renderizar has marcado otro formato en el paso 1, por ejemplo el short. Ese formato aún no existe; arriba pone «Aún sin crear: …».\n\n«▶ Crear formatos que faltan» crea solo esos archivos. El vídeo principal no se vuelve a renderizar y tu orden de clips se mantiene. Si además cambiaste clips, «Aplicar cambios» hace las dos cosas de una vez.\n\nAl revés: si desmarcas un formato después de renderizar, ya no aparece en la página Listo. El archivo sigue en la carpeta output/; no se borra nada.")},
}
for _L, _topics in _HELP_V46.items():
    HELP_TOPICS[_L].update(_topics)

# Ganzer Clip, Loop/Boomerang und Thumbnail-Werkzeugpalette
_HELP_V49 = {
 "de": {
  "speed": ("Schnittgeschwindigkeit", "Bestimmt, wie schnell die Clips wechseln. Alle Schnitte werden genau auf den Beat der Musik gelegt, die Geschwindigkeit bestimmt nur, wie viele Schläge zwischen zwei Schnitten liegen.\n\n• Sanft: etwa alle 3,5 Sekunden, ruhig und atmosphärisch\n• Langsam: etwa alle 2,2 Sekunden, cineastisch\n• Normal: etwa alle 1,7 Sekunden, ausgewogen (Standard)\n• Schnell: etwa alle 0,95 Sekunden, energetisch\n• Random: jeder Schnitt zufällig zwischen 0,95 und 3,5 Sekunden\n\nGanzer Clip: Klickst du die markierte Geschwindigkeit noch einmal an, ist nichts mehr markiert. Dann läuft jeder Clip komplett von Anfang bis Ende – egal wie lang er ist. Geschnitten wird auf dem letzten Beat vor dem Clip-Ende, dabei fallen höchstens Bruchteile einer Sekunde weg. Fotos stehen in diesem Modus 4 Sekunden. Ein erneuter Klick auf eine Geschwindigkeit schaltet zurück.\n\nNur ein Clip: Liegt in der Auswahl genau ein Video (ohne Fotos), läuft es als Loop durch den ganzen Song. Die Geschwindigkeit ist dann ausgegraut, weil sie keine Rolle spielt.\n\nTipp: Für Balladen passen Sanft oder Langsam, für Bachata und Merengue meist Normal, für Reggaetón und Tanzmusik Schnell. In den Hochformaten werden Sanft und Langsam etwas gestrafft, damit es am Handy nicht zu träge wirkt."),
  "page_extras": ("Schritt 3 – Extras", "Hier legst du fest, was zusätzlich ins Video kommt, und wie schnell geschnitten wird.\n\n• Pre-Intro: eigenes Video mit Ton vor der Musik (verlängert das Video)\n• Intro: stummer Clip am Songanfang (gehört zur Songlänge)\n• Outro: eigenes Video mit Ton nach der Musik (verlängert das Video)\n• Titel: der Musiktitel unten links\n• Schnittgeschwindigkeit: von Sanft bis Schnell oder Random – oder nichts markiert für „ganzer Clip“\n\nIst ein Ordner leer, ist der Schalter gesperrt. Rechts siehst du die Zusammenfassung. „Weiter“ plant die Clipfolge – gerendert wird noch nicht."),
  "selection": ("Deine Auswahl", "Hier siehst du alles, was in dein Video kommt – egal aus welcher Quelle: Pool-Clips (mit ◆ markiert), Videos aus eigenen Ordnern, Fotos (🖼) und hineingezogene Dateien.\n\nAlle Einträge sind gleichberechtigt: Das Programm verteilt sie zufällig auf die Schnitte, möglichst ohne denselben Clip zweimal hintereinander. Gibt es mehr Schnitte als Clips, werden Clips wiederholt.\n\nNur ein Clip: Liegt hier genau ein Video und kein Foto, erscheint der Hinweis „↻ Nur ein Clip“. Dann wird das Video automatisch zum Loop, der durch den ganzen Song läuft.\n\nHinzufügen kannst du über die drei Karten oben oder indem du Videos, Fotos oder ganze Ordner aus dem Explorer ins Fenster ziehst. Mit dem ✕ auf einem Vorschaubild entfernst du einen einzelnen Eintrag, mit „Alle entfernen“ leerst du die Auswahl. Die Dateien selbst werden dabei nie gelöscht.\n\nTipp: Je mehr unterschiedliches Material, desto abwechslungsreicher das Video."),
  "loop_mode": ("Nur ein Clip – Loop", "Du hast genau ein Video ausgewählt (und keine Fotos). Dieses Video läuft dann als Loop durch den ganzen Song – die Schnittgeschwindigkeit wird dafür ignoriert.\n\nDas Programm entscheidet automatisch, wie geloopt wird:\n• Nahtloser Loop: Es sucht im Clip zwei Stellen, die sich sehr ähnlich sehen, und loopt genau dazwischen. Am Übergang wird kurz überblendet, und die Länge eines Durchlaufs ist auf den Beat abgestimmt.\n• Boomerang: Sehen Anfang und Ende zu verschieden aus, läuft der Clip vorwärts und dann rückwärts. So gibt es keinen Sprung; die Wendepunkte liegen auf dem Beat.\n\nIst der Clip länger als der Song, wird gar nicht geloopt – er läuft einfach und endet mit der Musik.\n\nMöchtest du lieber geschnittene Clips, nimm einfach weitere Clips oder Fotos dazu."),
  "loop_card": ("Loop", "Diese Karte zeigt, wie dein einziger Clip durch den Song läuft:\n\n• Art: Nahtloser Loop oder Boomerang (vor und zurück) – das Programm wählt automatisch, was bei diesem Clip besser aussieht\n• Ein Durchlauf: so lange dauert eine Wiederholung; die Länge ist auf den Beat abgestimmt\n• Wiederholungen: so oft läuft der Durchlauf im Video\n• Länge im Video: die Zeit, die der Loop füllt (Songlänge ohne Intro)\n\nPre-Intro, Intro, Outro und Titel funktionieren wie gewohnt. Gerendert wird nur ein einziger Durchlauf, die Wiederholungen werden danach ohne Neuberechnung aneinandergehängt – auch ein langer Song ist schnell fertig.\n\n„Video erstellen“ startet das Rendern."),
  "plan_grid": ("Clipfolge", "Jede Kachel ist ein Abschnitt deines Videos, in der Reihenfolge, in der er vorkommt (von links nach rechts, Zeile für Zeile). Oben steht die Zeit im fertigen Video. Das Bild zeigt den Clip an der Stelle, an der er verwendet wird (noch nicht auf das Format zugeschnitten).\n\n• Verschieben: Kachel mit gedrückter linker Maustaste auf eine andere ziehen. Die beiden Clips tauschen die Plätze; die Längen der Plätze bleiben, damit alle Schnitte auf dem Beat bleiben.\n• Tauschen: „Clip tauschen“ setzt einen zufälligen anderen Clip aus deiner Auswahl an diese Stelle.\n• Gesperrt (Schloss): Pre-Intro, Intro und Outro bleiben an ihrem Platz.\n\nIm Modus „ganzer Clip“ (keine Schnittgeschwindigkeit markiert) behält jeder Clip seine eigene Länge. Verschiebst oder tauschst du einen Clip, wandern die Schnitte danach mit – die Zeiten der folgenden Kacheln ändern sich, am Ende kann ein Clip dazukommen oder wegfallen.\n\nVor dem ersten Rendern kostet das Sortieren keine Zeit. Nach dem Rendern wird mit „Änderungen übernehmen“ nur neu gerendert, was sich geändert hat."),
  "plan_swap": ("Clip tauschen", "Setzt einen zufällig gewählten anderen Clip oder ein anderes Foto aus deiner Auswahl an diese Stelle. Die Länge des Abschnitts bleibt gleich, damit das Video so lang bleibt wie der Song und alle Schnitte auf dem Beat bleiben. Bevorzugt werden Clips, die noch nicht im Video vorkommen.\n\nIm Modus „ganzer Clip“ läuft auch der neue Clip ganz; die folgenden Schnitte passen sich an seine Länge an.\n\nGefällt dir der neue Clip nicht, klicke einfach noch einmal. Fotos werden nur für Abschnitte von mindestens 2 Sekunden genommen, wenn auch Videos in der Auswahl sind.\n\nVor dem ersten Rendern ist das sofort erledigt. Nach dem Rendern wird beim Übernehmen nur neu gerendert, was sich geändert hat."),
  "done_thumb": ("Thumbnail-Generator", "Beim 16:9-Video erstellt das Programm automatisch ein Thumbnail in YouTube-Größe (1280×720, JPG) und speichert es neben dem Video: Musiktitel_Thumbnail.jpg im Ordner output/. Gewählt wird ein möglichst farbiges, helles und klares Standbild aus einem deiner Clips – kein Pre-Intro, Intro oder Outro.\n\n„↻ Anderes Bild“ nimmt das nächste Standbild und überschreibt die gespeicherte Datei sofort. Hast du das Thumbnail bearbeitet, bleiben Text und Stil dabei erhalten – nur das Bild wechselt.\n\n„✎ Bearbeiten“ öffnet die Werkzeugpalette: Bild frei aus dem Video wählen, Text mit Schrift, Farbe, Kontur und Schatten, eigenes Logo und Vorlagen für deinen Kanal-Look.\n\nDas Thumbnail lädst du bei YouTube beim Hochladen unter „Thumbnail“ hoch."),
  "thumb_edit": ("Thumbnail bearbeiten", "Öffnet die Werkzeugpalette für dein YouTube-Thumbnail. Links siehst du die Vorschau, rechts die Werkzeuge:\n\n• Bild: Helligkeit, Kontrast, Farbsättigung und ein weicher Verlauf, der den Hintergrund hinter dem Text abdunkelt\n• Text: zwei Zeilen (Titel ist vorausgefüllt), Schrift, Größe, Großbuchstaben, Schriftfarbe, Kontur und Schatten, Position\n• Logo: eigenes PNG in eine Ecke legen\n• Vorlage: deinen Stil speichern und bei jedem Video wiederverwenden\n\nUnter der Vorschau wählst du mit dem Schieberegler jedes beliebige Bild aus dem fertigen Video.\n\nSolange du nichts bearbeitest, bleibt alles wie bisher: Das Thumbnail entsteht automatisch, ohne Text."),
  "te_preview": ("Vorschau", "So sieht dein Thumbnail aus – jede Änderung rechts erscheint sofort. Gespeichert wird erst mit „Übernehmen“, dann in voller Größe (1280×720) als JPG im Ordner output/. Die Datei bleibt automatisch unter YouTubes Grenze von 2 MB."),
  "te_frame": ("Bild aus dem Video", "Mit dem Schieberegler wählst du jedes beliebige Bild aus deinem fertigen Video – einfach ziehen, die Vorschau folgt. Die Zeit daneben zeigt die Stelle im Video.\n\n„↻ Anderes Bild“ springt zum nächsten Vorschlag des Programms. Die Vorschläge sind nach Qualität sortiert: farbig, hell, klar gezeichnet."),
  "te_image": ("Bild", "Regler für das Grundbild:\n\n• Helligkeit: dunkler oder heller (100 % = unverändert)\n• Kontrast: flacher oder knackiger\n• Farbsättigung: von Schwarz-Weiß (0 %) bis kräftig bunt (200 %)\n\nThumbnails wirken bei YouTube oft besser, wenn Kontrast und Sättigung etwas höher sind als im Video."),
  "te_dark": ("Abdunkeln hinter Text", "Legt einen weichen dunklen Verlauf hinter den Text – oben, in der Mitte oder unten, je nach Textposition. So bleibt die Schrift auf jedem Bild lesbar, auch auf hellem Himmel oder Sand. 0 % schaltet den Verlauf aus. Ohne Text wird nichts abgedunkelt."),
  "te_text": ("Text", "Zeile 1 ist mit dem Songtitel vorausgefüllt, du kannst sie frei ändern oder leeren. Zeile 2 ist optional, zum Beispiel für deinen Künstlernamen oder „Official Video“; sie erscheint halb so groß unter Zeile 1.\n\nIst ein Text zu breit, wird er automatisch verkleinert, damit er ins Bild passt. Bleiben beide Zeilen leer, hat das Thumbnail keinen Text."),
  "te_font": ("Schrift und Größe", "Zur Auswahl stehen kräftige, gut lesbare Schriften, die auf jedem Windows 10/11 vorhanden sind: Impact, Arial Black, Segoe UI Black, Bahnschrift, Trebuchet, Georgia und Segoe Script. Fehlt eine Schrift auf deinem Computer, erscheint sie nicht in der Liste.\n\n„Größe“ gilt für Zeile 1, Zeile 2 ist immer halb so groß. „GROSSBUCHSTABEN“ schreibt alles groß – bei YouTube-Thumbnails meist die beste Wahl."),
  "te_color": ("Farbe, Kontur, Schatten", "• Schriftfarbe: Klick auf einen Farbkreis; „…“ öffnet die freie Farbwahl\n• Kontur: Farbe des Rands um jeden Buchstaben – eine dunkle Kontur macht hellen Text auf jedem Hintergrund lesbar\n• Konturstärke: 0 schaltet die Kontur aus\n• Schatten: ein weicher Schatten hinter dem Text für mehr Tiefe\n\nBewährt: weiße oder gelbe Schrift mit schwarzer Kontur."),
  "te_pos": ("Position", "Die neun Felder stehen für die Stellen im Bild: oben, Mitte, unten – jeweils links, mittig, rechts. Ein Klick setzt den Text dorthin, das aktive Feld ist blau. Der dunkle Verlauf passt sich automatisch an.\n\nTipp: YouTube blendet unten rechts die Videolänge ein – Text deshalb lieber nicht unten rechts platzieren."),
  "te_logo": ("Logo", "Legt dein eigenes Logo in eine Ecke des Thumbnails – ideal für einen einheitlichen Kanal-Look. Am besten eignet sich ein PNG mit transparentem Hintergrund.\n\n• „Logo wählen …“ öffnet die Dateiauswahl\n• „Entfernen“ nimmt das Logo wieder heraus\n• Ecke: oben links/rechts, unten links/rechts\n• Logo-Größe: Breite in Prozent der Bildbreite"),
  "te_template": ("Vorlage", "Speichert deinen Stil – Schrift, Größe, Farben, Kontur, Schatten, Position, Bild-Regler, Zeile 2 und Logo – als Vorlage. Nur der Titel (Zeile 1) wird nicht gespeichert, er kommt immer vom jeweiligen Song.\n\n• „Als Vorlage speichern“: merkt sich den aktuellen Stil\n• „Vorlage laden“: übernimmt die gespeicherte Vorlage in den Editor\n• Automatisch anwenden: Ist der Schalter an, bekommt jedes neue Video sofort ein Thumbnail im Stil deiner Vorlage – ohne dass du den Editor öffnen musst. Standardmäßig ist er aus.\n\nBeim nächsten Öffnen des Editors ist die Vorlage automatisch vorausgefüllt."),
  "te_buttons": ("Übernehmen, Abbrechen …", "• „Übernehmen“ speichert das Thumbnail (1280×720, JPG) im Ordner output/ und zeigt es auf der Fertig-Seite\n• „Abbrechen“ (oder Esc) schließt den Editor, nichts wird geändert\n• „Zurücksetzen“ stellt alle Werkzeuge auf den Anfang zurück, der Titel bleibt\n• „Ohne Text“ übernimmt nur das gewählte Bild, ganz ohne Text und Logo"),
  "page_done": ("Schritt 5 – Fertig", "Dein Video ist fertig. Links ist der Thumbnail-Generator (beim 16:9-Video): Das YouTube-Thumbnail wird automatisch im Ordner output/ gespeichert, „↻ Anderes Bild“ wählt ein anderes, „✎ Bearbeiten“ öffnet die Werkzeugpalette mit Text, Schriften, Farben und Logo. Rechts stehen alle erstellten Dateien.\n\n• „▶ Video ansehen“ öffnet das Video im Player\n• „Output-Ordner“ öffnet den Ordner mit allen Dateien\n• „YouTube Upload“ öffnet die Upload-Seite im Browser\n• „✏ Clips ändern“ führt zurück zu Schritt 4 (nicht beim Loop mit nur einem Clip)\n• „＋ Neues Video“ beginnt mit neuer Musik und behält deine Einstellungen"),
 },
 "en": {
  "speed": ("Cutting speed", "Decides how fast the clips change. All cuts are placed exactly on the beat of the music; the speed only decides how many beats lie between two cuts.\n\n• Gentle: about every 3.5 seconds, calm and atmospheric\n• Slow: about every 2.2 seconds, cinematic\n• Normal: about every 1.7 seconds, balanced (default)\n• Fast: about every 0.95 seconds, energetic\n• Random: every cut randomly between 0.95 and 3.5 seconds\n\nWhole clip: if you click the selected speed again, nothing is selected any more. Then every clip plays completely from start to end – no matter how long it is. The cut lands on the last beat before the end of the clip, so at most a fraction of a second is lost. Photos stay for 4 seconds in this mode. Clicking a speed again switches back.\n\nOnly one clip: if the selection contains exactly one video (and no photos), it loops through the whole song. The speed is then greyed out because it does not matter.\n\nTip: for ballads Gentle or Slow fit, for bachata and merengue usually Normal, for reggaetón and dance music Fast. In the portrait formats Gentle and Slow are tightened a little so that it does not feel sluggish on a phone."),
  "page_extras": ("Step 3 – Extras", "Here you decide what else goes into the video and how fast it is cut.\n\n• Pre-intro: your own video with sound before the music (makes the video longer)\n• Intro: silent clip at the start of the song (part of the song length)\n• Outro: your own video with sound after the music (makes the video longer)\n• Title: the music title at the bottom left\n• Cutting speed: from Gentle to Fast or Random – or nothing selected for “whole clip”\n\nIf a folder is empty, the switch is locked. On the right you see the summary. “Next” plans the clip order – nothing is rendered yet."),
  "selection": ("Your selection", "Here you see everything that goes into your video – no matter which source: pool clips (marked ◆), videos from your own folders, photos (🖼) and dropped files.\n\nAll entries are equal: the program distributes them randomly over the cuts, avoiding the same clip twice in a row where possible. If there are more cuts than clips, clips are repeated.\n\nOnly one clip: if there is exactly one video and no photo here, the note “↻ Only one clip” appears. The video then automatically becomes a loop that runs through the whole song.\n\nYou can add material with the three cards above or by dragging videos, photos or whole folders from Explorer into the window. The ✕ on a thumbnail removes a single entry, “Remove all” empties the selection. The files themselves are never deleted.\n\nTip: the more varied the material, the more varied the video."),
  "loop_mode": ("Only one clip – loop", "You have selected exactly one video (and no photos). This video then loops through the whole song – the cutting speed is ignored.\n\nThe program decides automatically how to loop:\n• Seamless loop: it looks for two points in the clip that look very similar and loops exactly between them. The transition gets a short crossfade, and the length of one pass is matched to the beat.\n• Boomerang: if the start and end look too different, the clip runs forwards and then backwards. That way there is no jump; the turning points land on the beat.\n\nIf the clip is longer than the song, it does not loop at all – it simply plays and ends with the music.\n\nIf you prefer cut clips, just add more clips or photos."),
  "loop_card": ("Loop", "This card shows how your single clip runs through the song:\n\n• Type: seamless loop or boomerang (forwards and back) – the program automatically chooses what looks better for this clip\n• One pass: how long one repetition lasts; the length is matched to the beat\n• Repetitions: how often the pass runs in the video\n• Length in the video: the time the loop fills (song length without intro)\n\nPre-intro, intro, outro and title work as usual. Only a single pass is rendered; the repetitions are then joined without re-encoding – even a long song is finished quickly.\n\n“Create video” starts rendering."),
  "plan_grid": ("Clip order", "Every tile is one section of your video, in the order in which it appears (left to right, row by row). At the top is the time in the finished video. The picture shows the clip at the point where it is used (not yet cropped to the format).\n\n• Move: drag a tile onto another one with the left mouse button held down. The two clips swap places; the lengths of the places stay so that all cuts stay on the beat.\n• Replace: “Replace clip” puts a random other clip from your selection in this place.\n• Locked (padlock): pre-intro, intro and outro stay where they are.\n\nIn “whole clip” mode (no cutting speed selected) every clip keeps its own length. If you move or replace a clip, the following cuts move with it – the times of the following tiles change, and at the end a clip may be added or dropped.\n\nBefore the first render sorting costs no time. After rendering, “Apply changes” only renders again what has changed."),
  "plan_swap": ("Replace clip", "Puts a randomly chosen other clip or photo from your selection in this place. The length of the section stays the same, so the video stays as long as the song and all cuts stay on the beat. Clips that do not yet appear in the video are preferred.\n\nIn “whole clip” mode the new clip also plays in full; the following cuts adjust to its length.\n\nIf you do not like the new clip, just click again. Photos are only used for sections of at least 2 seconds when there are also videos in the selection.\n\nBefore the first render this happens instantly. After rendering, only what has changed is rendered again when you apply."),
  "done_thumb": ("Thumbnail generator", "For the 16:9 video the program automatically creates a thumbnail in YouTube size (1280×720, JPG) and saves it next to the video: SongTitle_Thumbnail.jpg in the output/ folder. It picks a colourful, bright and clear still from one of your clips – no pre-intro, intro or outro.\n\n“↻ Other picture” takes the next still and overwrites the saved file immediately. If you have edited the thumbnail, text and style are kept – only the picture changes.\n\n“✎ Edit” opens the tool palette: choose any frame from the video, text with font, colour, outline and shadow, your own logo and templates for your channel look.\n\nYou upload the thumbnail on YouTube under “Thumbnail” when uploading."),
  "thumb_edit": ("Edit thumbnail", "Opens the tool palette for your YouTube thumbnail. On the left you see the preview, on the right the tools:\n\n• Image: brightness, contrast, saturation and a soft gradient that darkens the background behind the text\n• Text: two lines (the title is pre-filled), font, size, capital letters, text colour, outline and shadow, position\n• Logo: put your own PNG in a corner\n• Template: save your style and reuse it for every video\n\nBelow the preview, the slider lets you pick any frame of the finished video.\n\nAs long as you do not edit anything, everything stays as before: the thumbnail is created automatically, without text."),
  "te_preview": ("Preview", "This is how your thumbnail looks – every change on the right appears immediately. It is only saved with “Apply”, then in full size (1280×720) as a JPG in the output/ folder. The file automatically stays below YouTube's 2 MB limit."),
  "te_frame": ("Frame from the video", "With the slider you can pick any frame of your finished video – just drag, the preview follows. The time next to it shows the position in the video.\n\n“↻ Other picture” jumps to the next suggestion of the program. The suggestions are sorted by quality: colourful, bright, clearly drawn."),
  "te_image": ("Image", "Sliders for the base picture:\n\n• Brightness: darker or brighter (100 % = unchanged)\n• Contrast: flatter or punchier\n• Saturation: from black and white (0 %) to strongly colourful (200 %)\n\nThumbnails often work better on YouTube when contrast and saturation are a little higher than in the video."),
  "te_dark": ("Darken behind text", "Puts a soft dark gradient behind the text – at the top, in the middle or at the bottom, depending on the text position. That way the text stays readable on any picture, even on a bright sky or sand. 0 % switches the gradient off. Without text nothing is darkened."),
  "te_text": ("Text", "Line 1 is pre-filled with the song title; you can change it freely or clear it. Line 2 is optional, for example for your artist name or “Official Video”; it appears half the size below line 1.\n\nIf a text is too wide, it is reduced automatically so that it fits. If both lines are empty, the thumbnail has no text."),
  "te_font": ("Font and size", "You can choose strong, easy-to-read fonts that exist on every Windows 10/11: Impact, Arial Black, Segoe UI Black, Bahnschrift, Trebuchet, Georgia and Segoe Script. If a font is missing on your computer, it does not appear in the list.\n\n“Size” applies to line 1; line 2 is always half as big. “CAPITAL LETTERS” writes everything in capitals – usually the best choice for YouTube thumbnails."),
  "te_color": ("Colour, outline, shadow", "• Text colour: click a colour circle; “…” opens the free colour picker\n• Outline: colour of the edge around each letter – a dark outline makes light text readable on any background\n• Outline width: 0 switches the outline off\n• Shadow: a soft shadow behind the text for more depth\n\nProven: white or yellow text with a black outline."),
  "te_pos": ("Position", "The nine fields stand for the places in the picture: top, middle, bottom – each left, centre, right. One click puts the text there; the active field is blue. The dark gradient adjusts automatically.\n\nTip: YouTube shows the video length at the bottom right – so better not place text at the bottom right."),
  "te_logo": ("Logo", "Puts your own logo in a corner of the thumbnail – ideal for a consistent channel look. A PNG with a transparent background works best.\n\n• “Choose logo …” opens the file dialog\n• “Remove” takes the logo out again\n• Corner: top left/right, bottom left/right\n• Logo size: width in percent of the picture width"),
  "te_template": ("Template", "Saves your style – font, size, colours, outline, shadow, position, image sliders, line 2 and logo – as a template. Only the title (line 1) is not saved; it always comes from the current song.\n\n• “Save as template”: remembers the current style\n• “Load template”: puts the saved template into the editor\n• Apply automatically: if the switch is on, every new video immediately gets a thumbnail in the style of your template – without opening the editor. It is off by default.\n\nThe next time you open the editor, the template is pre-filled automatically."),
  "te_buttons": ("Apply, Cancel …", "• “Apply” saves the thumbnail (1280×720, JPG) in the output/ folder and shows it on the Done page\n• “Cancel” (or Esc) closes the editor, nothing is changed\n• “Reset” sets all tools back to the start; the title stays\n• “No text” takes only the chosen picture, without any text or logo"),
  "page_done": ("Step 5 – Done", "Your video is finished. On the left is the thumbnail generator (for the 16:9 video): the YouTube thumbnail is saved automatically in the output/ folder, “↻ Other picture” picks another one, “✎ Edit” opens the tool palette with text, fonts, colours and logo. On the right are all created files.\n\n• “▶ Watch video” opens the video in the player\n• “Output folder” opens the folder with all files\n• “YouTube upload” opens the upload page in the browser\n• “✏ Change clips” takes you back to step 4 (not for a loop with only one clip)\n• “＋ New video” starts with new music and keeps your settings"),
 },
 "es": {
  "speed": ("Velocidad de corte", "Decide lo rápido que cambian los clips. Todos los cortes se colocan exactamente en el beat de la música; la velocidad solo decide cuántos golpes hay entre dos cortes.\n\n• Suave: cada 3,5 segundos aprox., tranquilo y atmosférico\n• Lento: cada 2,2 segundos aprox., cinematográfico\n• Normal: cada 1,7 segundos aprox., equilibrado (predeterminado)\n• Rápido: cada 0,95 segundos aprox., enérgico\n• Aleatorio: cada corte al azar entre 0,95 y 3,5 segundos\n\nClip entero: si vuelves a pulsar la velocidad marcada, ya no queda nada marcado. Entonces cada clip se reproduce completo de principio a fin, dure lo que dure. El corte cae en el último beat antes del final del clip, así se pierde como mucho una fracción de segundo. En este modo las fotos duran 4 segundos. Al pulsar de nuevo una velocidad se vuelve al modo normal.\n\nSolo un clip: si en la selección hay exactamente un vídeo (y ninguna foto), se repite en loop durante toda la canción. La velocidad aparece entonces en gris porque no importa.\n\nConsejo: para baladas van bien Suave o Lento, para bachata y merengue casi siempre Normal, para reggaetón y música de baile Rápido. En los formatos verticales, Suave y Lento se aceleran un poco para que en el móvil no resulte lento."),
  "page_extras": ("Paso 3 – Extras", "Aquí decides qué más entra en el vídeo y lo rápido que se corta.\n\n• Pre-intro: vídeo propio con sonido antes de la música (alarga el vídeo)\n• Intro: clip mudo al principio de la canción (forma parte de su duración)\n• Outro: vídeo propio con sonido después de la música (alarga el vídeo)\n• Título: el título de la música abajo a la izquierda\n• Velocidad de corte: de Suave a Rápido o Aleatorio, o nada marcado para «clip entero»\n\nSi una carpeta está vacía, el interruptor está bloqueado. A la derecha ves el resumen. «Siguiente» planifica el orden de clips; todavía no se renderiza nada."),
  "selection": ("Tu selección", "Aquí ves todo lo que entra en tu vídeo, venga de donde venga: clips del grupo (marcados con ◆), vídeos de tus carpetas, fotos (🖼) y archivos arrastrados.\n\nTodas las entradas valen lo mismo: el programa las reparte al azar entre los cortes, evitando en lo posible el mismo clip dos veces seguidas. Si hay más cortes que clips, los clips se repiten.\n\nSolo un clip: si aquí hay exactamente un vídeo y ninguna foto, aparece el aviso «↻ Solo un clip». El vídeo se convierte entonces automáticamente en un loop que dura toda la canción.\n\nPuedes añadir material con las tres tarjetas de arriba o arrastrando vídeos, fotos o carpetas enteras desde el Explorador a la ventana. La ✕ de una miniatura quita una entrada; «Quitar todo» vacía la selección. Los archivos nunca se borran.\n\nConsejo: cuanto más variado el material, más variado el vídeo."),
  "loop_mode": ("Solo un clip: loop", "Has elegido exactamente un vídeo (y ninguna foto). Ese vídeo se repite entonces en loop durante toda la canción; la velocidad de corte se ignora.\n\nEl programa decide automáticamente cómo hacer el loop:\n• Loop sin cortes: busca dos puntos del clip muy parecidos y hace el loop justo entre ellos. En la transición hay un breve fundido y la duración de cada pasada está ajustada al beat.\n• Bumerán: si el principio y el final son demasiado distintos, el clip va hacia delante y luego hacia atrás. Así no hay salto; los puntos de giro caen en el beat.\n\nSi el clip es más largo que la canción, no se hace loop: simplemente se reproduce y termina con la música.\n\nSi prefieres clips cortados, añade más clips o fotos."),
  "loop_card": ("Loop", "Esta tarjeta muestra cómo recorre la canción tu único clip:\n\n• Tipo: loop sin cortes o bumerán (adelante y atrás); el programa elige automáticamente lo que mejor queda con este clip\n• Una pasada: lo que dura una repetición; la duración está ajustada al beat\n• Repeticiones: cuántas veces se repite la pasada en el vídeo\n• Duración en el vídeo: el tiempo que llena el loop (duración de la canción sin intro)\n\nPre-intro, intro, outro y título funcionan como siempre. Solo se renderiza una pasada; las repeticiones se unen después sin volver a codificar, así incluso una canción larga se termina rápido.\n\n«Crear vídeo» empieza el renderizado."),
  "plan_grid": ("Orden de clips", "Cada casilla es una sección de tu vídeo, en el orden en que aparece (de izquierda a derecha, fila a fila). Arriba está el tiempo en el vídeo terminado. La imagen muestra el clip en el punto donde se usa (aún sin recortar al formato).\n\n• Mover: arrastra una casilla sobre otra con el botón izquierdo pulsado. Los dos clips cambian de sitio; las duraciones de los sitios se mantienen para que todos los cortes sigan en el beat.\n• Reemplazar: «Reemplazar clip» pone en ese sitio otro clip al azar de tu selección.\n• Bloqueado (candado): pre-intro, intro y outro se quedan en su sitio.\n\nEn el modo «clip entero» (sin velocidad de corte marcada) cada clip conserva su propia duración. Si mueves o reemplazas un clip, los cortes siguientes se desplazan con él: cambian los tiempos de las casillas siguientes y al final puede añadirse o desaparecer un clip.\n\nAntes del primer renderizado, ordenar no cuesta tiempo. Después, «Aplicar cambios» solo vuelve a renderizar lo que ha cambiado."),
  "plan_swap": ("Reemplazar clip", "Pone en este sitio otro clip u otra foto de tu selección elegidos al azar. La duración de la sección se mantiene, así el vídeo dura lo mismo que la canción y todos los cortes siguen en el beat. Se prefieren clips que aún no aparecen en el vídeo.\n\nEn el modo «clip entero» el clip nuevo también se reproduce entero; los cortes siguientes se ajustan a su duración.\n\nSi no te gusta el clip nuevo, vuelve a pulsar. Las fotos solo se usan en secciones de al menos 2 segundos cuando también hay vídeos en la selección.\n\nAntes del primer renderizado esto es instantáneo. Después, al aplicar solo se vuelve a renderizar lo que ha cambiado."),
  "done_thumb": ("Generador de miniaturas", "Para el vídeo 16:9 el programa crea automáticamente una miniatura en tamaño YouTube (1280×720, JPG) y la guarda junto al vídeo: TituloDeLaCancion_Thumbnail.jpg en la carpeta output/. Elige una imagen fija lo más colorida, clara y nítida posible de uno de tus clips, nunca del pre-intro, intro u outro.\n\n«↻ Otra imagen» toma la siguiente imagen y sobrescribe el archivo guardado al instante. Si has editado la miniatura, el texto y el estilo se mantienen; solo cambia la imagen.\n\n«✎ Editar» abre la paleta de herramientas: elegir cualquier imagen del vídeo, texto con fuente, color, contorno y sombra, tu propio logo y plantillas para el estilo de tu canal.\n\nLa miniatura la subes en YouTube en «Miniatura» al publicar el vídeo."),
  "thumb_edit": ("Editar miniatura", "Abre la paleta de herramientas para tu miniatura de YouTube. A la izquierda ves la vista previa, a la derecha las herramientas:\n\n• Imagen: brillo, contraste, saturación y un degradado suave que oscurece el fondo tras el texto\n• Texto: dos líneas (el título ya viene puesto), fuente, tamaño, mayúsculas, color del texto, contorno y sombra, posición\n• Logo: poner tu propio PNG en una esquina\n• Plantilla: guardar tu estilo y reutilizarlo en cada vídeo\n\nDebajo de la vista previa, el control deslizante te deja elegir cualquier imagen del vídeo terminado.\n\nMientras no edites nada, todo sigue igual: la miniatura se crea automáticamente, sin texto."),
  "te_preview": ("Vista previa", "Así queda tu miniatura: cada cambio de la derecha aparece al instante. Solo se guarda con «Aplicar», entonces a tamaño completo (1280×720) como JPG en la carpeta output/. El archivo queda automáticamente por debajo del límite de 2 MB de YouTube."),
  "te_frame": ("Imagen del vídeo", "Con el control deslizante eliges cualquier imagen de tu vídeo terminado: arrastra y la vista previa te sigue. El tiempo de al lado indica el punto del vídeo.\n\n«↻ Otra imagen» salta a la siguiente propuesta del programa. Las propuestas están ordenadas por calidad: coloridas, claras y nítidas."),
  "te_image": ("Imagen", "Controles para la imagen base:\n\n• Brillo: más oscura o más clara (100 % = sin cambios)\n• Contraste: más plana o con más fuerza\n• Saturación: de blanco y negro (0 %) a muy colorida (200 %)\n\nEn YouTube las miniaturas suelen funcionar mejor con algo más de contraste y saturación que el vídeo."),
  "te_dark": ("Oscurecer tras el texto", "Pone un degradado oscuro y suave detrás del texto: arriba, en el centro o abajo, según la posición del texto. Así el texto se lee en cualquier imagen, incluso sobre un cielo claro o arena. 0 % desactiva el degradado. Sin texto no se oscurece nada."),
  "te_text": ("Texto", "La línea 1 viene con el título de la canción; puedes cambiarla libremente o borrarla. La línea 2 es opcional, por ejemplo para tu nombre artístico u «Official Video»; aparece a la mitad de tamaño debajo de la línea 1.\n\nSi un texto es demasiado ancho, se reduce automáticamente para que quepa. Si las dos líneas quedan vacías, la miniatura no lleva texto."),
  "te_font": ("Fuente y tamaño", "Puedes elegir fuentes potentes y fáciles de leer que vienen en todo Windows 10/11: Impact, Arial Black, Segoe UI Black, Bahnschrift, Trebuchet, Georgia y Segoe Script. Si a tu ordenador le falta una fuente, no aparece en la lista.\n\n«Tamaño» vale para la línea 1; la línea 2 siempre es la mitad. «MAYÚSCULAS» lo escribe todo en mayúsculas, casi siempre la mejor opción para miniaturas de YouTube."),
  "te_color": ("Color, contorno, sombra", "• Color del texto: pulsa un círculo de color; «…» abre el selector de color libre\n• Contorno: color del borde de cada letra; un contorno oscuro hace legible el texto claro sobre cualquier fondo\n• Grosor del contorno: 0 lo desactiva\n• Sombra: una sombra suave detrás del texto para dar profundidad\n\nUna combinación probada: texto blanco o amarillo con contorno negro."),
  "te_pos": ("Posición", "Las nueve casillas son los lugares de la imagen: arriba, centro, abajo; cada uno a la izquierda, en el centro o a la derecha. Un clic pone ahí el texto; la casilla activa es azul. El degradado oscuro se adapta solo.\n\nConsejo: YouTube muestra la duración del vídeo abajo a la derecha, así que mejor no pongas texto ahí."),
  "te_logo": ("Logo", "Pone tu propio logo en una esquina de la miniatura, ideal para que tu canal tenga un estilo uniforme. Lo mejor es un PNG con fondo transparente.\n\n• «Elegir logo …» abre el selector de archivos\n• «Quitar» lo vuelve a quitar\n• Esquina: arriba izquierda/derecha, abajo izquierda/derecha\n• Tamaño del logo: ancho en porcentaje del ancho de la imagen"),
  "te_template": ("Plantilla", "Guarda tu estilo (fuente, tamaño, colores, contorno, sombra, posición, controles de imagen, línea 2 y logo) como plantilla. Solo el título (línea 1) no se guarda: siempre viene de la canción actual.\n\n• «Guardar como plantilla»: recuerda el estilo actual\n• «Cargar plantilla»: pone la plantilla guardada en el editor\n• Aplicar automáticamente: si el interruptor está activado, cada vídeo nuevo recibe al instante una miniatura con el estilo de tu plantilla, sin abrir el editor. Está desactivado por defecto.\n\nLa próxima vez que abras el editor, la plantilla aparece automáticamente."),
  "te_buttons": ("Aplicar, Cancelar …", "• «Aplicar» guarda la miniatura (1280×720, JPG) en la carpeta output/ y la muestra en la página Listo\n• «Cancelar» (o Esc) cierra el editor sin cambiar nada\n• «Restablecer» devuelve todas las herramientas al inicio; el título se mantiene\n• «Sin texto» usa solo la imagen elegida, sin texto ni logo"),
  "page_done": ("Paso 5 – Listo", "Tu vídeo está terminado. A la izquierda está el generador de miniaturas (en el vídeo 16:9): la miniatura de YouTube se guarda automáticamente en la carpeta output/, «↻ Otra imagen» elige otra y «✎ Editar» abre la paleta de herramientas con texto, fuentes, colores y logo. A la derecha están todos los archivos creados.\n\n• «▶ Ver vídeo» abre el vídeo en el reproductor\n• «Carpeta de salida» abre la carpeta con todos los archivos\n• «Subir a YouTube» abre la página de subida en el navegador\n• «✏ Cambiar clips» te lleva de vuelta al paso 4 (no en un loop con un solo clip)\n• «＋ Nuevo vídeo» empieza con música nueva y conserva tus ajustes"),
 },
}
for _L, _topics in _HELP_V49.items():
    HELP_TOPICS[_L].update(_topics)

# Text/Logo frei verschieben, automatisches Aufräumen des Caches
_HELP_V50 = {
 "de": {
  "te_preview": ("Vorschau", "So sieht dein Thumbnail aus – jede Änderung rechts erscheint sofort.\n\nText und Logo kannst du direkt hier mit der Maus verschieben: Fährst du darüber, wird der Mauszeiger zum Kreuz. Dann gedrückt halten und an die gewünschte Stelle ziehen. Der dunkle Verlauf folgt der Höhe des Textes.\n\nGespeichert wird erst mit „Übernehmen“, dann in voller Größe (1280×720) als JPG im Ordner output/. Die Datei bleibt automatisch unter YouTubes Grenze von 2 MB."),
  "te_pos": ("Position", "Die neun Felder sind Schnellwahlen für die typischen Stellen: oben, Mitte, unten – jeweils links, mittig, rechts. Ein Klick setzt den Text dorthin, das aktive Feld ist blau.\n\nFrei platzieren: Zieh den Text in der Vorschau einfach mit der Maus an jede beliebige Stelle. Dann ist keines der Felder markiert; ein Klick auf ein Feld springt wieder zur Schnellwahl. Der dunkle Verlauf passt sich automatisch an.\n\nTipp: YouTube blendet unten rechts die Videolänge ein – Text deshalb lieber nicht unten rechts platzieren."),
  "te_logo": ("Logo", "Legt dein eigenes Logo auf das Thumbnail – ideal für einen einheitlichen Kanal-Look. Am besten eignet sich ein PNG mit transparentem Hintergrund.\n\n• „Logo wählen …“ öffnet die Dateiauswahl\n• „Entfernen“ nimmt das Logo wieder heraus\n• Ecke: oben links/rechts, unten links/rechts – oder das Logo in der Vorschau mit der Maus an jede Stelle ziehen („frei verschoben“)\n• Logo-Größe: Breite in Prozent der Bildbreite"),
  "te_template": ("Vorlage", "Speichert deinen Stil – Schrift, Größe, Farben, Kontur, Schatten, Position (auch frei verschoben), Bild-Regler, Zeile 2 und Logo – als Vorlage. Nur der Titel (Zeile 1) wird nicht gespeichert, er kommt immer vom jeweiligen Song.\n\n• „Als Vorlage speichern“: merkt sich den aktuellen Stil\n• „Vorlage laden“: übernimmt die gespeicherte Vorlage in den Editor\n• Automatisch anwenden: Ist der Schalter an, bekommt jedes neue Video sofort ein Thumbnail im Stil deiner Vorlage – ohne dass du den Editor öffnen musst. Standardmäßig ist er aus.\n\nBeim nächsten Öffnen des Editors ist die Vorlage automatisch vorausgefüllt."),
 },
 "en": {
  "te_preview": ("Preview", "This is how your thumbnail looks – every change on the right appears immediately.\n\nYou can move the text and logo right here with the mouse: when you hover over them, the pointer turns into a cross. Then hold the button down and drag to the place you want. The dark gradient follows the height of the text.\n\nIt is only saved with “Apply”, then in full size (1280×720) as a JPG in the output/ folder. The file automatically stays below YouTube's 2 MB limit."),
  "te_pos": ("Position", "The nine fields are quick choices for the typical places: top, middle, bottom – each left, centre, right. One click puts the text there; the active field is blue.\n\nPlace freely: simply drag the text with the mouse in the preview to any place. Then none of the fields is marked; clicking a field jumps back to the quick choice. The dark gradient adjusts automatically.\n\nTip: YouTube shows the video length at the bottom right – so better not place text at the bottom right."),
  "te_logo": ("Logo", "Puts your own logo on the thumbnail – ideal for a consistent channel look. A PNG with a transparent background works best.\n\n• “Choose logo …” opens the file dialog\n• “Remove” takes the logo out again\n• Corner: top left/right, bottom left/right – or drag the logo with the mouse in the preview to any place (“moved freely”)\n• Logo size: width in percent of the picture width"),
  "te_template": ("Template", "Saves your style – font, size, colours, outline, shadow, position (also moved freely), image sliders, line 2 and logo – as a template. Only the title (line 1) is not saved; it always comes from the current song.\n\n• “Save as template”: remembers the current style\n• “Load template”: puts the saved template into the editor\n• Apply automatically: if the switch is on, every new video immediately gets a thumbnail in the style of your template – without opening the editor. It is off by default.\n\nThe next time you open the editor, the template is pre-filled automatically."),
 },
 "es": {
  "te_preview": ("Vista previa", "Así queda tu miniatura: cada cambio de la derecha aparece al instante.\n\nEl texto y el logo los puedes mover aquí mismo con el ratón: al pasar por encima, el puntero se convierte en una cruz. Mantén pulsado y arrastra hasta el lugar que quieras. El degradado oscuro sigue la altura del texto.\n\nSolo se guarda con «Aplicar», entonces a tamaño completo (1280×720) como JPG en la carpeta output/. El archivo queda automáticamente por debajo del límite de 2 MB de YouTube."),
  "te_pos": ("Posición", "Las nueve casillas son accesos rápidos a los lugares típicos: arriba, centro, abajo; cada uno a la izquierda, en el centro o a la derecha. Un clic pone ahí el texto; la casilla activa es azul.\n\nColocar libremente: arrastra el texto con el ratón en la vista previa a cualquier lugar. Entonces no queda marcada ninguna casilla; un clic en una casilla vuelve al acceso rápido. El degradado oscuro se adapta solo.\n\nConsejo: YouTube muestra la duración del vídeo abajo a la derecha, así que mejor no pongas texto ahí."),
  "te_logo": ("Logo", "Pone tu propio logo en la miniatura, ideal para que tu canal tenga un estilo uniforme. Lo mejor es un PNG con fondo transparente.\n\n• «Elegir logo …» abre el selector de archivos\n• «Quitar» lo vuelve a quitar\n• Esquina: arriba izquierda/derecha, abajo izquierda/derecha, o arrastra el logo con el ratón en la vista previa a cualquier lugar («movido libremente»)\n• Tamaño del logo: ancho en porcentaje del ancho de la imagen"),
  "te_template": ("Plantilla", "Guarda tu estilo (fuente, tamaño, colores, contorno, sombra, posición, también si la moviste libremente, controles de imagen, línea 2 y logo) como plantilla. Solo el título (línea 1) no se guarda: siempre viene de la canción actual.\n\n• «Guardar como plantilla»: recuerda el estilo actual\n• «Cargar plantilla»: pone la plantilla guardada en el editor\n• Aplicar automáticamente: si el interruptor está activado, cada vídeo nuevo recibe al instante una miniatura con el estilo de tu plantilla, sin abrir el editor. Está desactivado por defecto.\n\nLa próxima vez que abras el editor, la plantilla aparece automáticamente."),
 },
}
for _L, _topics in _HELP_V50.items():
    HELP_TOPICS[_L].update(_topics)

# PDF-Anleitungen sind in die EXE eingebaut
for _L, _pairs in (("de", (("(die PDF muss neben der EXE liegen)", "(in die EXE eingebaut – eine PDF neben der EXE hat Vorrang)"),
                           ("(muss neben der EXE liegen)", "(in die EXE eingebaut – eine PDF neben der EXE hat Vorrang)"))),
                   ("en", (("(the PDF must be next to the EXE)", "(built into the EXE – a PDF next to the EXE takes priority)"),
                           ("(must be next to the EXE)", "(built into the EXE – a PDF next to the EXE takes priority)"))),
                   ("es", (("(el PDF debe estar junto al EXE)", "(incluido en el EXE; un PDF junto al EXE tiene prioridad)"),
                           ("(debe estar junto al EXE)", "(incluido en el EXE; un PDF junto al EXE tiene prioridad)")))):
    for _k in list(HELP_TOPICS[_L]):
        _t, _b = HELP_TOPICS[_L][_k]
        for _o, _n in _pairs:
            _b = _b.replace(_o, _n)
        HELP_TOPICS[_L][_k] = (_t, _b)

# Ordner logo/ für eigene Logos
_HELP_V51 = {
 "de": {"te_logo": ("Logo", "Legt dein eigenes Logo auf das Thumbnail – ideal für einen einheitlichen Kanal-Look. Am besten eignet sich ein PNG mit transparentem Hintergrund.\n\nFür deine Logos gibt es im Programmordner den Ordner logo/. Dort liegt zu Beginn das MVG-Logo als Beispiel; leg deine eigenen Logos einfach dazu. „Logo wählen …“ öffnet die Dateiauswahl direkt in diesem Ordner – du kannst aber auch jedes andere Bild auf deinem Computer nehmen.\n\n• „Entfernen“ nimmt das Logo wieder heraus\n• Ecke: oben links/rechts, unten links/rechts – oder das Logo in der Vorschau mit der Maus an jede Stelle ziehen („frei verschoben“)\n• Logo-Größe: Breite in Prozent der Bildbreite")},
 "en": {"te_logo": ("Logo", "Puts your own logo on the thumbnail – ideal for a consistent channel look. A PNG with a transparent background works best.\n\nFor your logos there is the logo/ folder in the program folder. At first it contains the MVG logo as an example; simply add your own logos. “Choose logo …” opens the file dialog directly in this folder – but you can also take any other picture on your computer.\n\n• “Remove” takes the logo out again\n• Corner: top left/right, bottom left/right – or drag the logo with the mouse in the preview to any place (“moved freely”)\n• Logo size: width in percent of the picture width")},
 "es": {"te_logo": ("Logo", "Pone tu propio logo en la miniatura, ideal para que tu canal tenga un estilo uniforme. Lo mejor es un PNG con fondo transparente.\n\nPara tus logos existe la carpeta logo/ dentro de la carpeta del programa. Al principio contiene el logo de MVG como ejemplo; añade ahí tus propios logos. «Elegir logo …» abre el selector de archivos directamente en esa carpeta, aunque también puedes usar cualquier otra imagen de tu ordenador.\n\n• «Quitar» lo vuelve a quitar\n• Esquina: arriba izquierda/derecha, abajo izquierda/derecha, o arrastra el logo con el ratón en la vista previa a cualquier lugar («movido libremente»)\n• Tamaño del logo: ancho en porcentaje del ancho de la imagen")},
}
for _L, _topics in _HELP_V51.items():
    HELP_TOPICS[_L].update(_topics)

# Über MVG, Update-Hinweis, Klick-Hilfe in Unterfenstern, Restzeit, Ordner merken, weiches Ende
_HELP_V52 = {
 "de": {
  "help": ("Einstellungen (⚙)", "Das Zahnrad öffnet das Menü mit allem, was nicht direkt zum Videobau gehört:\n\n• Klick-Hilfe: danach einen Bereich anklicken und genau diese Art Erklärung lesen. Der Modus endet nach einer Erklärung von selbst (oder mit Esc).\n• Kurzanleitung: die fünf Schritte im Überblick.\n• PDF-Anleitung: das ausführliche Handbuch in deiner Sprache (in die EXE eingebaut – eine PDF neben der EXE hat Vorrang).\n• Short per URL: 45-Sekunden-Short aus einem YouTube-Link in der Zwischenablage.\n• Pool-Ordner öffnen / Output-Ordner: öffnet die Ordner im Explorer.\n• Sprache / Language: Deutsch, English oder Español – wird sofort umgestellt, ohne Neustart.\n• Darstellung: System (wie Windows), Hell oder Dunkel – wird sofort umgestellt und gemerkt.\n• Einstellungen zurücksetzen: alles auf Anfang (Musik, Auswahl, Formate, Extras, Schnitt). Der Cache kann dabei optional geleert werden. Deine Videos und Clips bleiben immer erhalten.\n• Mobile Version: öffnet die GitHub-Seite von MVG Mobil.\n• Über MVG: Version und Update-Prüfung, Copyright und Lizenz, Links zu Homepage, GitHub und „Fehler melden“, die Möglichkeit, MVG mit einem Kaffee zu unterstützen, und die Lizenzen der verwendeten Programme. Gibt es eine neue Version, steht hinter dem Eintrag ein ⬆."),
  "update": ("Neue Version verfügbar", "Beim Start fragt MVG still bei GitHub nach, ob es eine neuere Version gibt. Ist das der Fall, erscheint dieser Hinweis oben in der Kopfzeile.\n\nEin Klick öffnet die Download-Seite der neuen Version im Browser. Installiert wird nichts automatisch – du entscheidest selbst, ob und wann du aktualisierst. Deine Einstellungen, Clips und Videos bleiben beim Austausch der EXE erhalten.\n\nOhne Internetverbindung passiert einfach nichts; das Programm funktioniert ganz normal weiter. Übertragen wird bei der Prüfung nur die Anfrage nach der neuesten Versionsnummer – keine Daten über dich oder deine Videos."),
  "qhelp_win": ("Klick-Hilfe (?)", "Startet die Klick-Hilfe in diesem Fenster. Oben erscheint ein blauer Balken; fahre mit der Maus über das Fenster – der Balken zeigt, wozu es an dieser Stelle eine Erklärung gibt. Ein Klick öffnet die ausführliche Erklärung, danach ist die Klick-Hilfe automatisch wieder aus.\n\nSolange sie aktiv ist, wird nichts ausgelöst: Du kannst gefahrlos auf jeden Button klicken. Beenden ohne Erklärung: mit Esc oder dem Button rechts im blauen Balken."),
  "pw_count": ("Ausgewählt", "Zeigt, wie viele Clips und Fotos du gerade ausgewählt hast – von wie vielen im Pool insgesamt. Gezählt wird über alle Reiter hinweg, also auch die Auswahl in Kategorien, die du gerade nicht siehst."),
  "pw_grid": ("Clips im Pool", "Jede Kachel ist ein Video oder Foto aus deinem Pool-Ordner (Fotos haben ein 🖼 vor dem Namen). Ein Klick wählt sie aus, ein zweiter Klick wieder ab. Ausgewählt erkennst du am blauen Rahmen.\n\nDie Vorschaubilder werden beim ersten Öffnen erzeugt und danach gespeichert – bei vielen Clips kann das beim ersten Mal einen Moment dauern. Die Reihenfolge im Video entsteht später: Bereits gewählte Pool-Clips behalten ihren Platz, neu gewählte kommen hinten dazu."),
  "pw_all": ("Alle / Keine", "„Alle“ wählt alle Clips aus, die gerade zu sehen sind, „Keine“ wählt sie alle ab. Mit Reitern wirken die beiden Buttons nur auf den aktiven Reiter – die Auswahl in anderen Kategorien bleibt unverändert."),
  "pw_apply": ("Übernehmen / Abbrechen", "„Übernehmen“ legt deine Auswahl in die Clip-Liste von Schritt 2: Neu gewählte Pool-Clips kommen dazu, abgewählte werden entfernt. Clips aus anderen Quellen (eigene Ordner, hineingezogene Dateien) bleiben davon unberührt.\n\n„Abbrechen“ (oder das Fenster schließen) lässt alles so, wie es vorher war."),
 },
 "en": {
  "help": ("Settings (⚙)", "The gear opens the menu with everything that is not directly part of building the video:\n\n• Click help: then click an area and read exactly this kind of explanation. The mode ends by itself after one explanation (or with Esc).\n• Quick guide: the five steps at a glance.\n• PDF manual: the detailed manual in your language (built into the EXE – a PDF next to the EXE takes priority).\n• Short from URL: a 45-second short from a YouTube link on the clipboard.\n• Open pool folder / Output folder: opens the folders in Explorer.\n• Language / Sprache: Deutsch, English or Español – switches immediately, no restart.\n• Appearance: System (like Windows), Light or Dark – switches immediately and is remembered.\n• Reset settings: everything back to the start (music, selection, formats, extras, cutting). The cache can optionally be emptied. Your videos and clips are always kept.\n• Mobile version: opens the GitHub page of MVG Mobile.\n• About MVG: version and update check, copyright and license, links to the homepage, GitHub and “Report a bug”, a way to support MVG with a coffee, and the licenses of the programs used. If there is a new version, the entry shows a ⬆."),
  "update": ("New version available", "At start-up MVG quietly asks GitHub whether there is a newer version. If so, this notice appears at the top in the header.\n\nA click opens the download page of the new version in your browser. Nothing is installed automatically – you decide whether and when to update. Your settings, clips and videos are kept when you replace the EXE.\n\nWithout an internet connection nothing happens; the program keeps working as normal. The check only sends the request for the latest version number – no data about you or your videos."),
  "qhelp_win": ("Click help (?)", "Starts click help in this window. A blue bar appears at the top; move the mouse over the window – the bar shows what can be explained at that spot. A click opens the detailed explanation, then click help switches off by itself.\n\nWhile it is active nothing is triggered, so you can safely click any button. To end without an explanation: press Esc or use the button on the right of the blue bar."),
  "pw_count": ("Selected", "Shows how many clips and photos you have selected – out of how many in the pool. It counts across all tabs, including the selection in categories you are not looking at right now."),
  "pw_grid": ("Clips in the pool", "Each tile is a video or photo from your pool folder (photos have a 🖼 before the name). One click selects it, a second click deselects it. You can tell it is selected by the blue frame.\n\nThe thumbnails are created the first time you open the window and then stored – with many clips this can take a moment the first time. The order in the video comes later: pool clips already chosen keep their place, newly chosen ones are added at the end."),
  "pw_all": ("All / None", "“All” selects every clip you can currently see, “None” deselects them all. With tabs, the two buttons only affect the active tab – the selection in other categories stays as it is."),
  "pw_apply": ("Apply / Cancel", "“Apply” puts your selection into the clip list of step 2: newly chosen pool clips are added, deselected ones are removed. Clips from other sources (your own folders, dropped files) are not affected.\n\n“Cancel” (or closing the window) leaves everything as it was."),
 },
 "es": {
  "help": ("Ajustes (⚙)", "El engranaje abre el menú con todo lo que no forma parte directamente de la creación del vídeo:\n\n• Ayuda con clic: después haz clic en una zona y lee justo este tipo de explicación. El modo termina solo tras una explicación (o con Esc).\n• Guía rápida: los cinco pasos de un vistazo.\n• Manual PDF: el manual detallado en tu idioma (incluido en el EXE; un PDF junto al EXE tiene prioridad).\n• Short desde URL: un short de 45 segundos a partir de un enlace de YouTube en el portapapeles.\n• Abrir carpeta del grupo / Carpeta de salida: abre las carpetas en el Explorador.\n• Idioma / Language: Deutsch, English o Español; se cambia al instante, sin reiniciar.\n• Apariencia: Sistema (como Windows), Claro u Oscuro; se cambia al instante y se recuerda.\n• Restablecer ajustes: todo vuelve al principio (música, selección, formatos, extras, corte). Opcionalmente se puede vaciar la caché. Tus vídeos y clips siempre se conservan.\n• Versión móvil: abre la página de GitHub de MVG Móvil.\n• Acerca de MVG: versión y comprobación de actualizaciones, copyright y licencia, enlaces a la página web, GitHub e «Informar de un error», la posibilidad de apoyar MVG con un café y las licencias de los programas utilizados. Si hay una versión nueva, la entrada muestra un ⬆."),
  "update": ("Nueva versión disponible", "Al arrancar, MVG pregunta discretamente a GitHub si hay una versión más reciente. Si la hay, aparece este aviso arriba en la cabecera.\n\nUn clic abre en el navegador la página de descarga de la nueva versión. No se instala nada automáticamente: tú decides si actualizas y cuándo. Tus ajustes, clips y vídeos se conservan al sustituir el EXE.\n\nSin conexión a Internet no pasa nada; el programa sigue funcionando con normalidad. La comprobación solo envía la consulta del número de la última versión, ningún dato sobre ti ni sobre tus vídeos."),
  "qhelp_win": ("Ayuda con clic (?)", "Inicia la ayuda con clic en esta ventana. Arriba aparece una barra azul; mueve el ratón por la ventana y la barra muestra qué se puede explicar en ese punto. Un clic abre la explicación detallada y después la ayuda con clic se desactiva sola.\n\nMientras está activa no se ejecuta nada, así que puedes hacer clic en cualquier botón sin riesgo. Para salir sin explicación: pulsa Esc o el botón de la derecha de la barra azul."),
  "pw_count": ("Seleccionados", "Muestra cuántos clips y fotos tienes seleccionados, de cuántos hay en total en el grupo. Cuenta todas las pestañas, también la selección en categorías que no estás viendo ahora."),
  "pw_grid": ("Clips del grupo", "Cada mosaico es un vídeo o una foto de tu carpeta del grupo (las fotos llevan un 🖼 delante del nombre). Un clic lo selecciona y otro lo deselecciona. Lo reconoces como seleccionado por el marco azul.\n\nLas miniaturas se crean la primera vez que abres la ventana y luego se guardan; con muchos clips puede tardar un momento la primera vez. El orden en el vídeo se decide después: los clips del grupo ya elegidos mantienen su lugar y los nuevos se añaden al final."),
  "pw_all": ("Todos / Ninguno", "«Todos» selecciona todos los clips que se ven ahora y «Ninguno» los deselecciona todos. Con pestañas, los dos botones solo afectan a la pestaña activa; la selección en las demás categorías no cambia."),
  "pw_apply": ("Aplicar / Cancelar", "«Aplicar» pone tu selección en la lista de clips del paso 2: los clips del grupo elegidos se añaden y los deseleccionados se quitan. Los clips de otras fuentes (tus carpetas, archivos arrastrados) no se ven afectados.\n\n«Cancelar» (o cerrar la ventana) lo deja todo como estaba."),
 },
}
for _L, _topics in _HELP_V52.items():
    HELP_TOPICS[_L].update(_topics)

# Ergänzungen an bestehenden Erklärungen
for _L, _adds in (
    ("de", {"render_progress": "\n\nRestzeit: Neben den Prozent steht nach einigen Sekunden eine Schätzung wie „noch ca. 2 Min.“. Sie wird aus dem bisherigen Tempo berechnet und während des Renderns laufend genauer.",
            "music": "\n\nDie Dateiauswahl öffnet sich immer in dem Ordner, aus dem du zuletzt Musik gewählt hast.",
            "src_folder": "\n\nDer Dialog öffnet sich dort, wo du zuletzt Videos bzw. Fotos ausgewählt hast – praktisch, wenn deine Clips in mehreren Ordnern nebeneinander liegen.",
            "src_files": "\n\nDer Dialog öffnet sich in dem Ordner, aus dem du zuletzt Videos bzw. Fotos gewählt hast.",
            "sw_outro": "\n\nOhne Outro endet das Video weich: In der letzten Sekunde blenden Bild und Ton sanft aus, statt hart abzubrechen."}),
    ("en", {"render_progress": "\n\nTime left: after a few seconds an estimate such as “approx. 2 min left” appears next to the percentage. It is calculated from the speed so far and becomes more accurate while rendering.",
            "music": "\n\nThe file dialog always opens in the folder you last chose music from.",
            "src_folder": "\n\nThe dialog opens where you last chose videos or photos – handy when your clips are in several folders side by side.",
            "src_files": "\n\nThe dialog opens in the folder you last chose videos or photos from.",
            "sw_outro": "\n\nWithout an outro the video ends softly: in the last second, picture and sound fade out gently instead of stopping abruptly."}),
    ("es", {"render_progress": "\n\nTiempo restante: tras unos segundos aparece junto al porcentaje una estimación como «faltan aprox. 2 min». Se calcula a partir del ritmo hasta ese momento y se vuelve más precisa durante el renderizado.",
            "music": "\n\nEl selector de archivos se abre siempre en la carpeta de la que elegiste música la última vez.",
            "src_folder": "\n\nEl diálogo se abre donde elegiste vídeos o fotos la última vez; práctico si tus clips están en varias carpetas contiguas.",
            "src_files": "\n\nEl diálogo se abre en la carpeta de la que elegiste vídeos o fotos la última vez.",
            "sw_outro": "\n\nSin outro, el vídeo termina suavemente: en el último segundo, la imagen y el sonido se desvanecen en lugar de cortarse de golpe."})):
    for _k, _a in _adds.items():
        if _k in HELP_TOPICS[_L]:
            _t, _b = HELP_TOPICS[_L][_k]
            HELP_TOPICS[_L][_k] = (_t, _b + _a)

# Short per URL: Fortschrittsfenster
_HELP_V53 = {
 "de": {
  "us_link": ("Video", "Zeigt, aus welchem Video der Short entsteht: den Link aus deiner Zwischenablage und – sobald das Programm ihn von YouTube abgerufen hat – den Titel des Videos. So siehst du sofort, ob du den richtigen Link kopiert hattest."),
  "us_steps": ("Arbeitsschritte", "Der Short entsteht in sechs Schritten, jeder mit eigenem Balken; erledigte bekommen einen grünen Haken:\n\n• Link prüfen: Ist es ein YouTube-Link?\n• YouTube-Werkzeug vorbereiten: Beim allerersten Mal wird eine kleine Zusatzkomponente geladen (Internet nötig), danach geht dieser Schritt sofort.\n• Video-Infos abrufen: Länge und Titel; Livestreams, Playlists und Videos unter 45 Sekunden werden abgelehnt.\n• Video laden: mit Prozentanzeige.\n• Stärksten Abschnitt suchen: Das Programm hört sich den Ton an und wählt die stärksten 45 Sekunden.\n• Short erstellen: Hochformat 9:16 mit Originalton, weich ein- und ausgeblendet.\n\nSchlägt ein Schritt fehl, wird er rot markiert und die Ursache steht darunter."),
  "us_result": ("Ergebnis", "Nach dem letzten Schritt steht hier der Name der fertigen Datei. Gespeichert wird sie im Ordner output/Shorts; „Ordner öffnen“ zeigt sie im Explorer, und ein Klang meldet das Ende.\n\nDu musst nicht warten: Das Fenster lässt sich jederzeit schließen, der Short entsteht im Hintergrund weiter, und die Statuszeile unten zeigt den Stand. Ein erneuter Klick auf ⚙ → Short per URL holt das Fenster zurück, solange er läuft.\n\nBitte nur Videos verwenden, für die du die Rechte hast."),
 },
 "en": {
  "us_link": ("Video", "Shows which video the short is made from: the link from your clipboard and – as soon as the program has fetched it from YouTube – the video title. So you can see straight away whether you copied the right link."),
  "us_steps": ("Work steps", "The short is made in six steps, each with its own bar; finished ones get a green tick:\n\n• Check link: is it a YouTube link?\n• Prepare YouTube tool: the very first time a small add-on is downloaded (internet needed); after that this step is instant.\n• Fetch video info: length and title; live streams, playlists and videos under 45 seconds are rejected.\n• Download video: with percentage.\n• Find the strongest section: the program listens to the sound and picks the strongest 45 seconds.\n• Create short: portrait 9:16 with the original sound, softly faded in and out.\n\nIf a step fails, it is marked red and the reason is shown below."),
  "us_result": ("Result", "After the last step the name of the finished file appears here. It is saved in the output/Shorts folder; “Open folder” shows it in Explorer, and a sound signals the end.\n\nYou do not have to wait: the window can be closed at any time, the short keeps being made in the background and the status line at the bottom shows the progress. Clicking ⚙ → Short from URL again brings the window back while it is running.\n\nPlease only use videos you have the rights to."),
 },
 "es": {
  "us_link": ("Vídeo", "Muestra de qué vídeo se crea el short: el enlace de tu portapapeles y, en cuanto el programa lo ha consultado en YouTube, el título del vídeo. Así ves enseguida si copiaste el enlace correcto."),
  "us_steps": ("Pasos de trabajo", "El short se crea en seis pasos, cada uno con su propia barra; los terminados reciben una marca verde:\n\n• Comprobar enlace: ¿es un enlace de YouTube?\n• Preparar herramienta de YouTube: la primerísima vez se descarga un pequeño complemento (hace falta Internet); después este paso es inmediato.\n• Obtener datos del vídeo: duración y título; se rechazan directos, listas de reproducción y vídeos de menos de 45 segundos.\n• Descargar vídeo: con porcentaje.\n• Buscar la sección más fuerte: el programa escucha el sonido y elige los 45 segundos más fuertes.\n• Crear short: vertical 9:16 con el sonido original, con fundido suave de entrada y salida.\n\nSi un paso falla, se marca en rojo y debajo aparece el motivo."),
  "us_result": ("Resultado", "Tras el último paso aparece aquí el nombre del archivo terminado. Se guarda en la carpeta output/Shorts; «Abrir carpeta» lo muestra en el Explorador y un sonido avisa del final.\n\nNo tienes que esperar: puedes cerrar la ventana cuando quieras, el short se sigue creando en segundo plano y la línea de estado de abajo muestra el avance. Mientras se está creando, al pulsar de nuevo ⚙ → Short desde URL vuelve a aparecer la ventana.\n\nUsa solo vídeos sobre los que tengas derechos."),
 },
}
for _L, _topics in _HELP_V53.items():
    HELP_TOPICS[_L].update(_topics)

# 4.1: Cache leeren, Problem melden, Vorbereiten großer Clips
for _L, (_reset, _mobile, _cache, _report) in {
    "de": ("• Einstellungen zurücksetzen:", "• Mobile Version:",
           "• Cache leeren: löscht die Zwischendateien des Programms (die Größe steht dahinter). Normalerweise nicht nötig – das Programm räumt selbst auf. Hilfreich, wenn die Festplatte voll ist oder etwas nicht stimmt. Videos, Clips und Einstellungen bleiben erhalten.\n",
           "• Problem melden: legt einen Fehlerbericht als ZIP-Datei auf deinen Desktop, den du dem Entwickler schicken kannst.\n"),
    "en": ("• Reset settings:", "• Mobile version:",
           "• Empty cache: deletes the program's temporary files (the size is shown next to it). Normally not needed – the program tidies up by itself. Useful when the disk is full or something seems wrong. Videos, clips and settings are kept.\n",
           "• Report a problem: puts an error report as a ZIP file on your desktop that you can send to the developer.\n"),
    "es": ("• Restablecer ajustes:", "• Versión móvil:",
           "• Vaciar caché: borra los archivos intermedios del programa (al lado se ve el tamaño). Normalmente no hace falta: el programa limpia solo. Útil si el disco está lleno o algo no va bien. Vídeos, clips y ajustes se conservan.\n",
           "• Informar de un problema: guarda en tu escritorio un informe de error en un archivo ZIP que puedes enviar al desarrollador.\n"),
}.items():
    _t, _b = HELP_TOPICS[_L]["help"]
    if _reset in _b:
        _b = _b.replace(_reset, _cache + _reset, 1)
    if _mobile in _b:
        _b = _b.replace(_mobile, _report + _mobile, 1)
    HELP_TOPICS[_L]["help"] = (_t, _b)

_HELP_V54 = {
 "de": {"report": ("Problem melden", "Erstellt einen Fehlerbericht: eine ZIP-Datei auf deinem Desktop mit den Protokollen des Programms und technischen Angaben (Windows-Version, Arbeitsspeicher, freier Platz, FFmpeg-Version, Namen und Formate deiner ausgewählten Clips).\n\nKennst du den Entwickler, schick ihm die Datei direkt, zum Beispiel per WhatsApp. Sonst öffnet „Auf GitHub melden“ die Seite für Fehlermeldungen und den Ordner mit der Datei: kurz beschreiben, was passiert ist, und die ZIP-Datei in das Textfeld ziehen (kostenloses GitHub-Konto nötig). Damit lässt sich meist schnell erkennen, woran es lag.\n\nEnthalten sind keine Videos, Fotos oder Musik. Der Bericht enthält auch die Protokolle der vorigen Sitzung – du kannst ihn also auch nach einem Neustart noch erstellen.")},
 "en": {"report": ("Report a problem", "Creates an error report: a ZIP file on your desktop with the program's logs and technical details (Windows version, memory, free space, FFmpeg version, names and formats of your selected clips).\n\nIf you know the developer, send him the file directly, for example by WhatsApp. Otherwise “Report on GitHub” opens the bug report page and the folder with the file: briefly describe what happened and drag the ZIP file into the text field (free GitHub account needed). It usually shows quickly what went wrong.\n\nNo videos, photos or music are included. The report also contains the logs of the previous session – so you can still create it after a restart.")},
 "es": {"report": ("Informar de un problema", "Crea un informe de error: un archivo ZIP en tu escritorio con los registros del programa y datos técnicos (versión de Windows, memoria, espacio libre, versión de FFmpeg, nombres y formatos de los clips seleccionados).\n\nSi conoces al desarrollador, envíale el archivo directamente, por ejemplo por WhatsApp. Si no, «Informar en GitHub» abre la página de informes de error y la carpeta con el archivo: describe brevemente qué ha pasado y arrastra el archivo ZIP al campo de texto (hace falta una cuenta gratuita de GitHub). Normalmente permite ver enseguida qué ha fallado.\n\nNo incluye vídeos, fotos ni música. El informe también contiene los registros de la sesión anterior, así que puedes crearlo incluso después de reiniciar.")},
}
for _L, _topics in _HELP_V54.items():
    HELP_TOPICS[_L].update(_topics)
for _L, _a in (("de", "\n\nClip wird vorbereitet: Videos in besonderen Formaten (z. B. 4K oder HEVC vom Handy) bereitet das Programm beim ersten Mal einmalig vor. Das kann bei großen Clips einige Minuten dauern; in der Zeile „Clips rendern“ und unten in der Statuszeile siehst du den Fortschritt in Prozent. Beim nächsten Video geht es dann sofort."),
               ("en", "\n\nPreparing clip: videos in special formats (e.g. 4K or HEVC from a phone) are prepared once the first time. With large clips this can take a few minutes; the “Render clips” row and the status line show the progress in percent. The next video then goes straight away."),
               ("es", "\n\nPreparando clip: los vídeos en formatos especiales (p. ej. 4K o HEVC del móvil) se preparan una sola vez la primera vez. Con clips grandes puede tardar unos minutos; en la fila «Renderizar clips» y en la línea de estado ves el avance en porcentaje. En el siguiente vídeo ya va al instante.")):
    _t, _b = HELP_TOPICS[_L]["render_progress"]
    HELP_TOPICS[_L]["render_progress"] = (_t, _b + _a)


# ── Übersetzungen der v4.0-Oberfläche: deutscher Text -> (English, Español)
_TR_V4 = {
 "Start": ("Start", "Inicio"),
 "Bildmaterial": ("Footage", "Material"),
 "Extras": ("Extras", "Extras"),
 "Fertig": ("Done", "Listo"),
 "Weiter": ("Next", "Siguiente"),
 "Zurück": ("Back", "Atrás"),
 "Fehler": ("Error", "Error"),
 "wartet": ("waiting", "en espera"),
 "läuft …": ("running …", "en curso …"),
 "fertig": ("done", "listo"),
 "keine": ("none", "ninguno"),
 "Clip": ("Clip", "Clip"),
 "Gesperrt": ("Locked", "Bloqueado"),
 "Klick-Hilfe": ("Click help", "Ayuda con clic"),
 "PDF-Anleitung": ("PDF manual", "Manual PDF"),
 "Sprache / Language": ("Language / Sprache", "Idioma / Language"),
 "Neueste Version (GitHub)": ("Latest version (GitHub)", "Última versión (GitHub)"),
 "Mobile Version (GitHub)": ("Mobile version (GitHub)", "Versión móvil (GitHub)"),
 "Einstellungen zurücksetzen": ("Reset settings", "Restablecer ajustes"),
 "Pool-Ordner öffnen": ("Open pool folder", "Abrir carpeta del grupo"),
 "Ordner öffnen": ("Open folder", "Abrir carpeta"),
 "Musik: ": ("Music: ", "Música: "),
 "Rendern läuft noch. Wirklich beenden?": ("Rendering is still running. Really quit?", "El renderizado sigue en curso. ¿Salir de todos modos?"),
 "Während des Renderns nicht möglich": ("Not possible while rendering", "No es posible durante el renderizado"),
 "Ziehen und Ablegen ist nicht verfügbar – bitte die Buttons verwenden.": ("Drag and drop is not available – please use the buttons.", "Arrastrar y soltar no está disponible – usa los botones."),
 # Willkommen
 "Willkommen beim Musik Video Generator {v}": ("Welcome to Music Video Generator {v}", "Bienvenido al Generador de vídeos musicales {v}"),
 "Aus deiner Musik und deinen Clips entsteht in vier Schritten ein Video – die Schnitte liegen automatisch auf dem Beat.": ("Your music and clips become a video in four steps – the cuts land on the beat automatically.", "Tu música y tus clips se convierten en un vídeo en cuatro pasos; los cortes caen solos sobre el ritmo."),
 "So geht's": ("How it works", "Así funciona"),
 "Musik wählen und Formate festlegen": ("Choose music and set the formats", "Elige la música y define los formatos"),
 "Clips und Fotos auswählen – aus dem Pool, aus Ordnern oder einfach ins Fenster ziehen": ("Select clips and photos – from the pool, from folders or simply drag them into the window", "Elige clips y fotos: del grupo, de carpetas o simplemente arrastrándolos a la ventana"),
 "Optional Pre-Intro, Intro, Outro, Titel und Schnittgeschwindigkeit": ("Optional pre-intro, intro, outro, title and cutting speed", "Opcional: pre-intro, intro, outro, título y velocidad de corte"),
 "Video ansehen, Clips verschieben oder ersetzen, hochladen": ("Watch the video, move or replace clips, upload", "Ver el vídeo, mover o reemplazar clips, subirlo"),
 "Dein Clip-Pool": ("Your clip pool", "Tu grupo de clips"),
 "Das Programm wird ohne Clips ausgeliefert. Lege Videos und Fotos, die du immer wieder verwendest, in den Pool-Ordner. In Schritt 2 wählst du daraus per Klick die passenden aus.\n\nMaterial, das nur zu einem Song passt, musst du nicht in den Pool legen: Ordner wählen oder Dateien einfach ins Fenster ziehen.": (
    "The program ships without clips. Put videos and photos you use again and again into the pool folder. In step 2 you pick the right ones from it with a click.\n\nMaterial that only fits one song does not need to go into the pool: choose a folder or simply drag the files into the window.",
    "El programa se entrega sin clips. Pon en la carpeta del grupo los vídeos y fotos que usas una y otra vez. En el paso 2 eliges con un clic los adecuados.\n\nEl material que solo encaja con una canción no tiene que ir al grupo: elige una carpeta o arrastra los archivos a la ventana."),
 "Im Pool: {n} Dateien": ("In the pool: {n} files", "En el grupo: {n} archivos"),
 "Los geht's": ("Let's go", "Empezar"),
 # Schritt 1
 "Was möchtest du erstellen?": ("What would you like to create?", "¿Qué quieres crear?"),
 "Wähle zuerst deine Musik und die Videoformate, die du haben möchtest.": ("First choose your music and the video formats you want.", "Primero elige tu música y los formatos de vídeo que quieres."),
 "1. Musikdatei": ("1. Music file", "1. Archivo de música"),
 "MP3 oder WAV · auch per Ziehen ins Fenster": ("MP3 or WAV · also by dragging into the window", "MP3 o WAV · también arrastrando a la ventana"),
 "Länge: {t}": ("Length: {t}", "Duración: {t}"),
 "Andere Musik wählen": ("Choose other music", "Elegir otra música"),
 "Noch keine Datei ausgewählt": ("No file selected yet", "Aún no hay archivo"),
 "Ziehe deine Audiodatei hierher oder wähle sie aus.": ("Drag your audio file here or choose it.", "Arrastra aquí tu archivo de audio o elígelo."),
 "Musikdatei wählen": ("Choose music file", "Elegir archivo de música"),
 "2. Ausgabeformate": ("2. Output formats", "2. Formatos de salida"),
 "Mehrfachauswahl möglich · jedes Format wird als eigene Datei erstellt.": ("Multiple choice possible · each format is created as its own file.", "Puedes elegir varios · cada formato se crea como archivo propio."),
 "Querformat · volle Länge · mit Extras": ("Landscape · full length · with extras", "Horizontal · duración completa · con extras"),
 "Hochformat · volle Länge · mit Extras": ("Portrait · full length · with extras", "Vertical · duración completa · con extras"),
 "Stärkster Abschnitt · ohne Extras": ("Strongest section · no extras", "Sección más fuerte · sin extras"),
 "Wird gespeichert als: ": ("Will be saved as: ", "Se guardará como: "),
 "  (Ordner output/)": ("  (folder output/)", "  (carpeta output/)"),
 "Der Dateiname des Videos entsteht aus dem Namen der Musikdatei.": ("The video's file name comes from the name of the music file.", "El nombre del vídeo se crea a partir del nombre del archivo de música."),
 "Bitte zuerst eine Musikdatei wählen.": ("Please choose a music file first.", "Primero elige un archivo de música."),
 # Schritt 2
 "Woher sollen die Bilder kommen?": ("Where should the pictures come from?", "¿De dónde salen las imágenes?"),
 "Kombiniere beliebig viele Quellen. Du kannst Dateien und Ordner auch einfach ins Fenster ziehen.": ("Combine as many sources as you like. You can also simply drag files and folders into the window.", "Combina tantas fuentes como quieras. También puedes arrastrar archivos y carpetas a la ventana."),
 "Clip-Pool": ("Clip pool", "Grupo de clips"),
 "Deine Clips und Fotos für jedes Video (Ordner clips/)": ("Your clips and photos for every video (folder clips/)", "Tus clips y fotos para cada vídeo (carpeta clips/)"),
 "Clips auswählen": ("Select clips", "Seleccionar clips"),
 "Eigene Videos": ("Your videos", "Tus vídeos"),
 "Clips aus einem beliebigen Ordner auf deinem Computer": ("Clips from any folder on your computer", "Clips de cualquier carpeta de tu ordenador"),
 "Ordner wählen": ("Choose folder", "Elegir carpeta"),
 "Dateien wählen": ("Choose files", "Elegir archivos"),
 "Eigene Fotos": ("Your photos", "Tus fotos"),
 "Fotos als stehende Bilder zwischen den Clips": ("Photos as still images between the clips", "Fotos como imágenes fijas entre los clips"),
 "Deine Auswahl": ("Your selection", "Tu selección"),
 "Alle entfernen": ("Remove all", "Quitar todo"),
 "Auswahl leeren": ("Clear selection", "Vaciar selección"),
 "Alle {n} Einträge aus der Auswahl entfernen?\n(Die Dateien selbst bleiben erhalten.)": ("Remove all {n} entries from the selection?\n(The files themselves are kept.)", "¿Quitar las {n} entradas de la selección?\n(Los archivos se conservan.)"),
 "Noch nichts ausgewählt": ("Nothing selected yet", "Aún no hay nada seleccionado"),
 "Wähle oben eine Quelle – oder ziehe Videos, Fotos oder ganze Ordner hierher.": ("Choose a source above – or drag videos, photos or whole folders here.", "Elige una fuente arriba, o arrastra aquí vídeos, fotos o carpetas enteras."),
 "{n} im Pool · {s} ausgewählt": ("{n} in the pool · {s} selected", "{n} en el grupo · {s} seleccionados"),
 "Pool-Ordner ist noch leer": ("Pool folder is still empty", "La carpeta del grupo aún está vacía"),
 "{n} Videos ausgewählt": ("{n} videos selected", "{n} vídeos seleccionados"),
 "{n} Fotos ausgewählt": ("{n} photos selected", "{n} fotos seleccionadas"),
 "{v} Clips · {p} Fotos": ("{v} clips · {p} photos", "{v} clips · {p} fotos"),
 "{n} hinzugefügt · Auswahl: {v} Clips, {p} Fotos": ("{n} added · Selection: {v} clips, {p} photos", "{n} añadidos · Selección: {v} clips, {p} fotos"),
 "Nichts Neues hinzugefügt (schon in der Auswahl oder kein Video/Foto)": ("Nothing new added (already in the selection or not a video/photo)", "No se añadió nada nuevo (ya está en la selección o no es vídeo/foto)"),
 "Ordner mit Videos wählen": ("Choose a folder with videos", "Elegir carpeta con vídeos"),
 "Ordner mit Fotos wählen": ("Choose a folder with photos", "Elegir carpeta con fotos"),
 "In diesem Ordner wurden keine Videos gefunden.": ("No videos were found in this folder.", "No se encontraron vídeos en esta carpeta."),
 "In diesem Ordner wurden keine Fotos gefunden.": ("No photos were found in this folder.", "No se encontraron fotos en esta carpeta."),
 "Videos wählen": ("Choose videos", "Elegir vídeos"),
 "Fotos wählen": ("Choose photos", "Elegir fotos"),
 "Zur Auswahl hinzugefügt – gilt beim nächsten Video (Schritt 2)": ("Added to the selection – applies to the next video (step 2)", "Añadido a la selección – vale para el próximo vídeo (paso 2)"),
 "Clips aus dem Pool auswählen": ("Select clips from the pool", "Seleccionar clips del grupo"),
 "Klicke Clips an, die ins Video sollen": ("Click the clips that should go into the video", "Haz clic en los clips que deben ir al vídeo"),
 "Der Pool-Ordner ist noch leer.\n\nLege Videos und Fotos, die du immer wieder verwendest, in diesen Ordner:\n{path}\n\nDanach kannst du sie hier per Klick auswählen.": (
    "The pool folder is still empty.\n\nPut videos and photos you use again and again into this folder:\n{path}\n\nAfterwards you can select them here with a click.",
    "La carpeta del grupo aún está vacía.\n\nPon en esta carpeta los vídeos y fotos que usas una y otra vez:\n{path}\n\nDespués podrás elegirlos aquí con un clic."),
 "Übernehmen": ("Apply", "Aplicar"),
 "Pool: {n} ausgewählt": ("Pool: {n} selected", "Grupo: {n} seleccionados"),
 # Schritt 3
 "Noch etwas ergänzen?": ("Anything to add?", "¿Algo más?"),
 "Alle Extras sind optional. Ohne Änderungen kannst du direkt starten.": ("All extras are optional. You can start right away without changes.", "Todos los extras son opcionales. Puedes empezar directamente sin cambios."),
 "Optionale Videoelemente": ("Optional video elements", "Elementos de vídeo opcionales"),
 "Gilt für 16:9 und 9:16 in voller Länge – der 58-Sekunden-Short bleibt immer ohne.": ("Applies to full-length 16:9 and 9:16 – the 58-second short always stays without.", "Vale para 16:9 y 9:16 de duración completa; el short de 58 segundos siempre va sin ellos."),
 "Eigenes Video mit Ton vor der Musik": ("Your own video with sound before the music", "Vídeo propio con sonido antes de la música"),
 "Stummer Clip am Anfang, Musik läuft schon": ("Silent clip at the start, music already playing", "Clip mudo al principio, la música ya suena"),
 "Eigenes Video mit Ton nach der Musik": ("Your own video with sound after the music", "Vídeo propio con sonido después de la música"),
 "Musiktitel unten links einblenden": ("Show the song title bottom left", "Mostrar el título abajo a la izquierda"),
 "Ordner leer": ("Folder empty", "Carpeta vacía"),
 "1 Datei": ("1 file", "1 archivo"),
 "{n} Dateien, zufällig": ("{n} files, random", "{n} archivos, al azar"),
 "Schnitt: ": ("Cutting: ", "Corte: "),
 "Zusammenfassung": ("Summary", "Resumen"),
 "Nach dem Rendern": ("After rendering", "Después de renderizar"),
 "Im nächsten Schritt siehst du alle Clips deines Videos. Du kannst sie verschieben oder durch andere Clips gleicher Länge ersetzen – ohne neu zu rendern.": ("In the next step you see all clips of your video. You can move them or replace them with other clips of the same length – without rendering again.", "En el siguiente paso ves todos los clips de tu vídeo. Puedes moverlos o reemplazarlos por otros de la misma duración, sin volver a renderizar."),
 "Bildmaterial: ": ("Footage: ", "Material: "),
 "Extras: ": ("Extras: ", "Extras: "),
 "Video erstellen": ("Create video", "Crear vídeo"),
 # Rendern
 "Keine Clips oder Fotos ausgewählt.": ("No clips or photos selected.", "No hay clips ni fotos seleccionados."),
 "Hinweis: {n} Dateien der Auswahl existieren nicht mehr und werden übersprungen.": ("Note: {n} files of the selection no longer exist and are skipped.", "Aviso: {n} archivos de la selección ya no existen y se omiten."),
 "Neu rendern": ("Render again", "Renderizar de nuevo"),
 "Im Clip-Editor gibt es noch nicht übernommene Änderungen. Trotzdem neu rendern?": ("The clip editor has changes that were not applied. Render again anyway?", "En el editor de clips hay cambios sin aplicar. ¿Renderizar de todos modos?"),
 "Dein Video wird erstellt …": ("Your video is being created …", "Se está creando tu vídeo …"),
 "Das dauert je nach Songlänge und Anzahl der Formate ein paar Minuten. Ein Klang meldet, wenn alles fertig ist.": ("Depending on song length and number of formats this takes a few minutes. A sound tells you when everything is done.", "Según la duración de la canción y el número de formatos tarda unos minutos. Un sonido avisa cuando todo está listo."),
 "Das hat leider nicht geklappt": ("Unfortunately that did not work", "Lamentablemente no ha funcionado"),
 "Beim Erstellen des Videos ist ein Fehler aufgetreten.": ("An error occurred while creating the video.", "Se produjo un error al crear el vídeo."),
 "Zurück zu den Extras": ("Back to the extras", "Volver a los extras"),
 "Erneut versuchen": ("Try again", "Intentar de nuevo"),
 # Schritt 4
 "Dein Video ist fertig! Willst du Clips ändern?": ("Your video is ready! Want to change clips?", "¡Tu vídeo está listo! ¿Quieres cambiar clips?"),
 "Ziehe Clips an eine neue Position. „Clip ersetzen“ wählt einen anderen Clip gleicher Länge. Erst „Änderungen übernehmen“ speichert ins Video.": ("Drag clips to a new position. “Replace clip” picks another clip of the same length. Only “Apply changes” saves into the video.", "Arrastra clips a otra posición. «Reemplazar clip» elige otro clip de la misma duración. Solo «Aplicar cambios» guarda en el vídeo."),
 "Bearbeitet wird: ": ("Editing: ", "Se edita: "),
 "Bleibt unverändert: ": ("Stays unchanged: ", "No cambia: "),
 "Nicht erstellt: ": ("Not created: ", "No creado: "),
 "Clip-Übersicht": ("Clip overview", "Vista de clips"),
 "Änderungen übernehmen": ("Apply changes", "Aplicar cambios"),
 "Verwerfen": ("Discard", "Descartar"),
 "Änderungen vorgemerkt": ("Changes pending", "Cambios pendientes"),
 "Clips verschieben oder ersetzen, dann „Änderungen übernehmen“ klicken": ("Move or replace clips, then click “Apply changes”", "Mueve o reemplaza clips y luego pulsa «Aplicar cambios»"),
 "Clip ersetzen": ("Replace clip", "Reemplazar clip"),
 "Änderungen verworfen – das Video ist unverändert.": ("Changes discarded – the video is unchanged.", "Cambios descartados – el vídeo no ha cambiado."),
 "Neues Video": ("New video", "Nuevo vídeo"),
 "Es gibt noch nicht übernommene Änderungen. Verwerfen und ein neues Video beginnen?": ("There are changes that were not applied. Discard them and start a new video?", "Hay cambios sin aplicar. ¿Descartarlos y empezar un vídeo nuevo?"),
 "Neues Video: Einstellungen und Auswahl wurden behalten.": ("New video: settings and selection were kept.", "Nuevo vídeo: se conservaron los ajustes y la selección."),
 "Video abspielen": ("Play video", "Reproducir vídeo"),
 "YouTube Upload": ("YouTube upload", "Subir a YouTube"),
 "Es gibt noch nicht übernommene Änderungen im Clip-Editor. Wirklich beenden?": ("There are unapplied changes in the clip editor. Really quit?", "Hay cambios sin aplicar en el editor de clips. ¿Salir de verdad?"),
 "Bitte kurz warten …": ("Please wait a moment …", "Espera un momento …"),
 "Verarbeite neue Reihenfolge…": ("Processing the new order…", "Procesando el nuevo orden…"),
 "Klebe Segmente zusammen…": ("Joining segments…", "Uniendo segmentos…"),
 "Mische Audio…": ("Mixing audio…", "Mezclando el audio…"),
 "Segment fehlt: ": ("Segment missing: ", "Falta el segmento: "),
 "Version": ("Version", "Versión"),
 "Schnittgeschwindigkeit": ("Cutting speed", "Velocidad de corte"),
 "Alle Schnitte liegen auf dem Beat – die Geschwindigkeit bestimmt nur, wie oft geschnitten wird.": ("All cuts land on the beat – the speed only decides how often a cut happens.", "Todos los cortes caen sobre el ritmo; la velocidad solo decide con qué frecuencia se corta."),
 "Lege ein Video in den Ordner {name}/, dann ist die Option wählbar.": ("Put a video into the folder {name}/ to make this option available.", "Pon un vídeo en la carpeta {name}/ para poder activar esta opción."),
}
for _de, (_en, _es) in _TR_V4.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V41 = {
 "Rendern": ("Render", "Renderizar"),
 "Anderes Bild": ("Other picture", "Otra imagen"),
 "Clip getauscht": ("Clip replaced", "Clip reemplazado"),
 "Clip tauschen": ("Replace clip", "Reemplazar clip"),
 "Clipfolge für: ": ("Clip order for: ", "Orden de clips para: "),
 "Clipfolge geplant: {n} Schnitte": ("Clip order planned: {n} cuts", "Orden de clips planificado: {n} cortes"),
 "Clipfolge neu planen?": ("Re-plan the clip order?", "¿Volver a planificar el orden de clips?"),
 "Clipfolge wird geplant …": ("Planning the clip order …", "Planificando el orden de clips …"),
 "Clips verschoben": ("Clips moved", "Clips movidos"),
 "Clips ändern": ("Change clips", "Cambiar clips"),
 "Dein Video ist fertig!": ("Your video is ready!", "¡Tu vídeo está listo!"),
 "Deine Dateien": ("Your files", "Tus archivos"),
 "Die Clipfolge konnte nicht geplant werden.": ("The clip order could not be planned.", "No se pudo planificar el orden de clips."),
 "Die Kacheln färben sich, sobald ihr Abschnitt fertig ist. Ein Klang meldet, wenn alles fertig ist.": ("The tiles light up as soon as their section is finished. A sound tells you when everything is done.", "Las casillas se iluminan en cuanto su sección está lista. Un sonido avisa cuando todo ha terminado."),
 "Die Musik wird analysiert und die Schnitte werden auf den Beat gelegt. Das dauert nur einen Moment.": ("The music is analysed and the cuts are placed on the beat. This only takes a moment.", "Se analiza la música y los cortes se colocan sobre el ritmo. Solo tarda un momento."),
 "Du hast Musik, Bildmaterial, Extras, Format oder Schnitt geändert. Dafür wird die Clipfolge neu geplant – deine Sortierung geht dabei verloren.\n\nFortfahren?": ("You changed music, footage, extras, format or cutting. The clip order will be re-planned for this – your arrangement will be lost.\n\nContinue?", "Has cambiado música, material, extras, formato o corte. Para ello se vuelve a planificar el orden de clips y tu orden se pierde.\n\n¿Continuar?"),
 "Du siehst, wie dein Video aus den Clips zusammengesetzt wird, und kannst Clips verschieben oder tauschen – bevor gerendert wird.": ("You see how your video is put together from the clips and can move or replace clips – before anything is rendered.", "Ves cómo se monta tu vídeo con los clips y puedes moverlos o reemplazarlos, antes de renderizar."),
 "Es gibt noch nicht übernommene Änderungen an der Clipfolge. Wirklich beenden?": ("There are unapplied changes to the clip order. Really quit?", "Hay cambios sin aplicar en el orden de clips. ¿Salir de verdad?"),
 "Gefällt dir ein Clip nicht?": ("Don't like a clip?", "¿No te gusta un clip?"),
 "Hinzugefügt – die Clipfolge wird beim nächsten „Video erstellen“ neu geplant.": ("Added – the clip order will be re-planned at the next “Create video”.", "Añadido – el orden de clips se volverá a planificar en el próximo «Crear vídeo»."),
 "Im nächsten Schritt": ("In the next step", "En el siguiente paso"),
 "Schnitte": ("cuts", "cortes"),
 "Sieh es dir an, lade es hoch – oder ändere einzelne Clips.": ("Watch it, upload it – or change individual clips.", "Míralo, súbelo o cambia clips concretos."),
 "So wird dein Video aufgebaut": ("This is how your video is built", "Así se monta tu vídeo"),
 "Thumbnail gespeichert: ": ("Thumbnail saved: ", "Miniatura guardada: "),
 "Thumbnail konnte nicht gespeichert werden: ": ("Thumbnail could not be saved: ", "No se pudo guardar la miniatura: "),
 "Verschiebe oder tausche Clips. „Änderungen übernehmen“ rendert nur, was sich geändert hat.": ("Move or replace clips. “Apply changes” only renders what has changed.", "Mueve o reemplaza clips. «Aplicar cambios» solo renderiza lo que ha cambiado."),
 "Verschieben oder tauschen – gerendert wird danach nur, was sich geändert hat.": ("Move or replace – afterwards only what has changed is rendered.", "Mover o reemplazar: después solo se renderiza lo que ha cambiado."),
 "Video ansehen": ("Watch video", "Ver vídeo"),
 "Vorschau aus dem Video (Hochformat – YouTube nimmt bei Shorts das Bild aus dem Video)": ("Preview from the video (portrait – for Shorts YouTube takes the picture from the video)", "Vista previa del vídeo (vertical – en los Shorts YouTube toma la imagen del vídeo)"),
 "YouTube-Thumbnail gespeichert: ": ("YouTube thumbnail saved: ", "Miniatura de YouTube guardada: "),
 "YouTube-Thumbnail wird erstellt …": ("Creating YouTube thumbnail …", "Creando la miniatura de YouTube …"),
 "Ziehe Clips an eine neue Position. „Clip tauschen“ nimmt zufällig einen anderen Clip gleicher Länge. Dann „Video erstellen“.": ("Drag clips to a new position. “Replace clip” takes a random other clip of the same length. Then “Create video”.", "Arrastra clips a otra posición. «Reemplazar clip» toma al azar otro clip de la misma duración. Luego «Crear vídeo»."),
 "Zur Fertig-Seite": ("To the Done page", "A la página Listo"),
 "automatisch dazu: ": ("automatically also: ", "además, automático: "),
}
for _de, (_en, _es) in _TR_V41.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V42 = {
 "Darstellung": ("Appearance", "Apariencia"),
 "System": ("System", "Sistema"),
 "Hell": ("Light", "Claro"),
 "Dunkel": ("Dark", "Oscuro"),
 "Vorschau": ("Preview", "Vista previa"),
 "Thumbnail-Generator": ("Thumbnail generator", "Generador de miniaturas"),
 "Gespeichert: ": ("Saved: ", "Guardada: "),
 "Standbild aus deinem Video – bei Shorts nimmt YouTube das Bild direkt aus dem Video.": ("Still from your video – for Shorts YouTube takes the picture directly from the video.", "Imagen fija de tu vídeo: en los Shorts YouTube toma la imagen directamente del vídeo."),
 "YouTube-Thumbnail (1280×720) aus deinem Video – wird automatisch im Output-Ordner gespeichert.": ("YouTube thumbnail (1280×720) from your video – saved automatically in the output folder.", "Miniatura de YouTube (1280×720) de tu vídeo: se guarda automáticamente en la carpeta de salida."),
}
for _de, (_en, _es) in _TR_V42.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V44 = {
 "Weitere": ("More", "Otros"),
 "Kategorie:": ("Category:", "Categoría:"),
}
for _de, (_en, _es) in _TR_V44.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V45 = {
 "Das Band zeigt, welcher Abschnitt gerade entsteht – fertige laufen nach links hinaus. Ein Klang meldet, wenn alles fertig ist.": ("The band shows which section is being created right now – finished ones run out to the left. A sound tells you when everything is done.", "La cinta muestra qué sección se está creando ahora; las terminadas salen por la izquierda. Un sonido avisa cuando todo está listo."),
 "Der stärkste 58-Sekunden-Teil des Songs": ("The strongest 58-second part of the song", "La parte de 58 segundos más fuerte de la canción"),
 "Für die Übersicht der Clipfolge": ("For the overview of the clip order", "Para la vista general del orden de clips"),
 "Jeder Schnitt bekommt einen Clip oder ein Foto": ("Every cut gets a clip or a photo", "Cada corte recibe un clip o una foto"),
 "Schnittgeschwindigkeit: ": ("Cutting speed: ", "Velocidad de corte: "),
 "Tempo und Beats deines Songs erkennen": ("Detect the tempo and beats of your song", "Detectar el tempo y los beats de tu canción"),
 "nicht nötig": ("not needed", "no hace falta"),
 "{n} Schnitte": ("{n} cuts", "{n} cortes"),
 "{n} von {total} Abschnitten fertig": ("{n} of {total} sections done", "{n} de {total} secciones listas"),
 "Musik analysieren": ("Analyse music", "Analizar la música"),
 "Besten Abschnitt für den Short finden": ("Find the best part for the short", "Buscar la mejor parte para el short"),
 "Schnitte auf den Beat legen": ("Place cuts on the beat", "Poner los cortes en el beat"),
 "Extras auswählen": ("Choose extras", "Elegir extras"),
 "Clips und Fotos verteilen": ("Distribute clips and photos", "Repartir clips y fotos"),
 "Vorschaubilder vorbereiten": ("Prepare thumbnails", "Preparar miniaturas"),
 "Clips rendern": ("Render clips", "Renderizar clips"),
 "Abschnitte zusammenfügen": ("Join sections", "Unir secciones"),
 "Musik und Titel einfügen": ("Add music and title", "Añadir música y título"),
 "Musik anlegen": ("Add music", "Añadir música"),
 "Aus deiner Musik und deinen Clips entsteht in fünf Schritten ein Video – die Schnitte liegen automatisch auf dem Beat.": ("Your music and clips become a video in five steps – the cuts land on the beat automatically.", "Tu música y tus clips se convierten en un vídeo en cinco pasos; los cortes caen solos sobre el beat."),
 "Clipfolge prüfen, Clips verschieben oder tauschen, Video erstellen": ("Check the clip order, move or swap clips, create the video", "Revisar el orden de clips, mover o cambiar clips, crear el vídeo"),
 "Video ansehen, Thumbnail speichern, Clips ändern, hochladen": ("Watch the video, save the thumbnail, change clips, upload", "Ver el vídeo, guardar la miniatura, cambiar clips, subir"),
 "Schnitt": ("Edit", "Montaje"),
 "Dein Song gibt den Takt vor.": ("Your song sets the beat.", "Tu canción marca el ritmo."),
 "Fehlende Formate erstellen": ("Create missing formats", "Crear formatos que faltan"),
 "Noch nicht erstellt: ": ("Not created yet: ", "Aún sin crear: "),
 "Esc oder ✕ – ohne Änderung zurück": ("Esc or ✕ – back without changes", "Esc o ✕ – volver sin cambios"),
 "Wird automatisch dazu erstellt: ": ("Created automatically as well: ", "Se crea automáticamente también: "),
}
for _de, (_en, _es) in _TR_V45.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V47 = {
 "Dein Song gibt den Takt vor.": ("Your song sets the beat.", "Tu canción marca el ritmo."),
 "Alle Schnitte liegen auf dem Beat – die Geschwindigkeit bestimmt nur, wie oft geschnitten wird. Nochmal klicken = ganzer Clip.": ("All cuts land on the beat – the speed only decides how often to cut. Click again = whole clip.", "Todos los cortes caen en el beat; la velocidad solo decide cada cuánto se corta. Otro clic = clip entero."),
 "Anfang und Ende des Clips sehen zu verschieden aus für einen unsichtbaren Übergang. Deshalb läuft der Clip vorwärts und dann rückwärts – so gibt es keinen Sprung. Die Wendepunkte liegen auf dem Beat.": ("The start and end of the clip look too different for an invisible transition. So the clip runs forwards and then backwards – there is no jump. The turning points land on the beat.", "El principio y el final del clip son demasiado distintos para una transición invisible. Por eso el clip va hacia delante y luego hacia atrás: así no hay salto. Los puntos de giro caen en el beat."),
 "Art": ("Type", "Tipo"),
 "Bereite Loop vor…": ("Preparing loop…", "Preparando el loop…"),
 "Boomerang (vor und zurück)": ("Boomerang (forwards and back)", "Bumerán (adelante y atrás)"),
 "Das Programm hat im Clip zwei Stellen gefunden, die sich sehr ähnlich sehen, und loopt genau dazwischen. Am Übergang wird kurz überblendet, und die Länge eines Durchlaufs ist auf den Beat abgestimmt – so fällt die Wiederholung kaum auf.": ("The program found two points in the clip that look very similar and loops exactly between them. The transition gets a short crossfade, and the length of one pass is matched to the beat – so the repetition is hardly noticeable.", "El programa encontró dos puntos del clip muy parecidos y hace el loop justo entre ellos. En la transición hay un breve fundido y la duración de cada pasada está ajustada al beat, así la repetición apenas se nota."),
 "Der Clip ist länger als der Song. Er läuft einfach von Anfang an und endet mit der Musik.": ("The clip is longer than the song. It simply plays from the start and ends with the music.", "El clip es más largo que la canción. Simplemente se reproduce desde el principio y termina con la música."),
 "Durchlauf": ("Pass", "Pasada"),
 "Ein Durchlauf": ("One pass", "Una pasada"),
 "Ein einziger Clip läuft als Loop durch den ganzen Song – nahtlos und im Takt.": ("A single clip loops through the whole song – seamlessly and on the beat.", "Un único clip se repite en loop durante toda la canción, sin cortes y al ritmo."),
 "Ganzer Clip": ("Whole clip", "Clip entero"),
 "Ganzer Clip – Schnitt am Clip-Ende, auf dem Beat": ("Whole clip – cut at the end of the clip, on the beat", "Clip entero: corte al final del clip, en el beat"),
 "Kein Loop nötig": ("No loop needed", "No hace falta loop"),
 "Loop (ein Clip)": ("Loop (one clip)", "Loop (un clip)"),
 "Länge im Video": ("Length in the video", "Duración en el vídeo"),
 "Möchtest du lieber geschnittene Clips? Wähle in Schritt 2 weitere Clips oder Fotos dazu.": ("Would you rather have cut clips? Add more clips or photos in step 2.", "¿Prefieres clips cortados? Añade más clips o fotos en el paso 2."),
 "Nahtlose Stelle im Clip suchen, im Takt": ("Find a seamless point in the clip, on the beat", "Buscar un punto sin cortes en el clip, al ritmo"),
 "Nahtloser Loop": ("Seamless loop", "Loop sin cortes"),
 "Nichts gewählt: Jeder Clip läuft ganz – geschnitten wird am Clip-Ende, auf dem Beat.": ("Nothing selected: every clip plays in full – the cut is at the end of the clip, on the beat.", "Nada seleccionado: cada clip se reproduce entero; el corte va al final del clip, en el beat."),
 "Nur ein Clip ausgewählt: Er läuft als Loop durch den ganzen Song – die Schnittgeschwindigkeit spielt keine Rolle.": ("Only one clip selected: it loops through the whole song – the cutting speed does not matter.", "Solo hay un clip: se repite en loop durante toda la canción; la velocidad de corte no importa."),
 "Nur ein Clip – er läuft als Loop durch den ganzen Song.": ("Only one clip – it loops through the whole song.", "Solo un clip: se repite en loop durante toda la canción."),
 "Wiederholungen": ("Repetitions", "Repeticiones"),
 "Loop für: ": ("Loop for: ", "Loop para: "),
 "Loop vorbereiten": ("Prepare loop", "Preparar el loop"),
 "Loop-Durchläufe": ("Loop passes", "Pasadas del loop"),
 "Eine Datei im Cache wird von einem anderen Programm benutzt. Bitte im Task-Manager alle „ffmpeg“-Prozesse beenden (oder den Computer neu starten) und erneut versuchen.": ("A file in the cache is being used by another program. Please end all “ffmpeg” processes in Task Manager (or restart the computer) and try again.", "Otro programa está usando un archivo de la caché. Cierra todos los procesos «ffmpeg» en el Administrador de tareas (o reinicia el ordenador) e inténtalo de nuevo."),
 "frei verschoben": ("moved freely", "movido libremente"),
 "Tipp: Text und Logo lassen sich in der Vorschau mit der Maus verschieben.": ("Tip: drag the text and logo with the mouse in the preview.", "Consejo: arrastra el texto y el logo con el ratón en la vista previa."),
 "Der Loop-Durchlauf konnte nicht erstellt werden.": ("The loop pass could not be created.", "No se pudo crear la pasada del loop."),
}
for _de, (_en, _es) in _TR_V47.items():
    TRANSLATIONS["en"][_de] = _en
    TRANSLATIONS["es"][_de] = _es

_TR_V48 = {
 "Abdunkeln hinter Text": ("Darken behind text", "Oscurecer tras el texto"),
 "Als Vorlage speichern": ("Save as template", "Guardar como plantilla"),
 "Bearbeiten": ("Edit", "Editar"),
 "Bild": ("Image", "Imagen"),
 "Bild aus dem Video": ("Frame from the video", "Imagen del vídeo"),
 "Bilder": ("Images", "Imágenes"),
 "Ecke": ("Corner", "Esquina"),
 "Entfernen": ("Remove", "Quitar"),
 "Farbsättigung": ("Saturation", "Saturación"),
 "GROSSBUCHSTABEN": ("CAPITAL LETTERS", "MAYÚSCULAS"),
 "Größe": ("Size", "Tamaño"),
 "Helligkeit": ("Brightness", "Brillo"),
 "Kontrast": ("Contrast", "Contraste"),
 "Kontur": ("Outline", "Contorno"),
 "Konturstärke": ("Outline width", "Grosor del contorno"),
 "Logo": ("Logo", "Logo"),
 "Logo wählen (PNG mit transparentem Hintergrund)": ("Choose logo (PNG with transparent background)", "Elegir logo (PNG con fondo transparente)"),
 "Logo wählen …": ("Choose logo …", "Elegir logo …"),
 "Logo-Größe": ("Logo size", "Tamaño del logo"),
 "Noch keine Vorlage gespeichert.": ("No template saved yet.", "Aún no hay plantilla guardada."),
 "Oder einen Vorschlag des Programms:": ("Or one of the program's suggestions:", "O una propuesta del programa:"),
 "Ohne Text": ("No text", "Sin texto"),
 "Position": ("Position", "Posición"),
 "Schatten": ("Shadow", "Sombra"),
 "Schrift": ("Font", "Fuente"),
 "Schriftfarbe": ("Text colour", "Color del texto"),
 "Text": ("Text", "Texto"),
 "Thumbnail bearbeiten": ("Edit thumbnail", "Editar miniatura"),
 "Vorlage": ("Template", "Plantilla"),
 "Vorlage automatisch auf neue Thumbnails anwenden": ("Apply template automatically to new thumbnails", "Aplicar la plantilla automáticamente a las nuevas miniaturas"),
 "Vorlage gespeichert – sie wird beim nächsten Bearbeiten vorausgefüllt.": ("Template saved – it will be pre-filled next time you edit.", "Plantilla guardada: se rellenará la próxima vez que edites."),
 "Vorlage gespeichert.": ("Template saved.", "Plantilla guardada."),
 "Vorlage laden": ("Load template", "Cargar plantilla"),
 "Zeile 1 (Titel)": ("Line 1 (title)", "Línea 1 (título)"),
 "Zeile 2 (optional, z. B. Künstler)": ("Line 2 (optional, e.g. artist)", "Línea 2 (opcional, p. ej. artista)"),
 "Zurücksetzen": ("Reset", "Restablecer"),
 "kein Logo": ("no logo", "sin logo"),
 "oben links": ("top left", "arriba a la izquierda"),
 "oben rechts": ("top right", "arriba a la derecha"),
 "unten links": ("bottom left", "abajo a la izquierda"),
 "unten rechts": ("bottom right", "abajo a la derecha"),
}
for _de, (_en, _es) in _TR_V48.items():
    TRANSLATIONS["en"].setdefault(_de, _en)
    TRANSLATIONS["es"].setdefault(_de, _es)

# ── Über MVG, Lizenzen, Update-Hinweis, Restzeit
_TR_V49 = {
 "Über MVG": ("About MVG", "Acerca de MVG"),
 "Über MVG …": ("About MVG …", "Acerca de MVG …"),
 "Homepage": ("Homepage", "Página web"),
 "Fehler melden": ("Report a bug", "Informar de un error"),
 "Herunterladen": ("Download", "Descargar"),
 "Lizenz": ("License", "Licencia"),
 "Lizenzen": ("Licenses", "Licencias"),
 "MIT-Lizenz": ("MIT License", "Licencia MIT"),
 "MVG unterstützen": ("Support MVG", "Apoyar MVG"),
 "MVG ist kostenlos. Wenn es dir gefällt, freue ich mich über einen Kaffee.": (
    "MVG is free. If you like it, I'd be happy about a coffee.",
    "MVG es gratis. Si te gusta, me alegrará que me invites a un café."),
 "Ursprünglich entstanden für die Musikvideos von": ("Originally created for the music videos of", "Creado originalmente para los videoclips de"),
 "Neue Version {v} verfügbar": ("New version {v} available", "Nueva versión {v} disponible"),
 "Version {v} verfügbar": ("Version {v} available", "Versión {v} disponible"),
 "Version {v} – aktuell": ("Version {v} – up to date", "Versión {v} – actualizada"),
 "Version {v} – keine Verbindung zur Update-Prüfung": ("Version {v} – no connection for the update check", "Versión {v} – sin conexión para buscar actualizaciones"),
 "Version {v} – wird geprüft …": ("Version {v} – checking …", "Versión {v} – comprobando …"),
 "Verwendete Programme und Bibliotheken": ("Programs and libraries used", "Programas y bibliotecas utilizados"),
 "MVG enthält bzw. nutzt die folgende freie Software. Sie gehört ihren jeweiligen Urhebern und steht unter deren eigenen Lizenzen:": (
    "MVG contains or uses the following free software. It belongs to its respective authors and is subject to their own licenses:",
    "MVG contiene o utiliza el siguiente software libre. Pertenece a sus respectivos autores y está sujeto a sus propias licencias:"),
 "FFmpeg ist ein eigenständiges Programm, das MVG aufruft. Quellcode und Lizenztext: https://ffmpeg.org/download.html und https://ffmpeg.org/legal.html": (
    "FFmpeg is a separate program that MVG calls. Source code and license text: https://ffmpeg.org/download.html and https://ffmpeg.org/legal.html",
    "FFmpeg es un programa independiente al que MVG llama. Código fuente y texto de la licencia: https://ffmpeg.org/download.html y https://ffmpeg.org/legal.html"),
 "mit Ausnahme für erzeugte Programme": ("with exception for generated programs", "con excepción para los programas generados"),
 "sowie deren Abhängigkeiten (z. B. SciPy, soundfile, numba), jeweils unter ihren eigenen freien Lizenzen.": (
    "plus their dependencies (e.g. SciPy, soundfile, numba), each under its own free license.",
    "y sus dependencias (p. ej. SciPy, soundfile, numba), cada una bajo su propia licencia libre."),
 "Die vollständigen Lizenztexte findest du auf den genannten Seiten.": ("You will find the full license texts on the pages listed.", "Encontrarás los textos completos de las licencias en las páginas indicadas."),
 "noch ca. {n} Min.": ("approx. {n} min left", "faltan aprox. {n} min"),
 "noch unter 1 Min.": ("less than 1 min left", "falta menos de 1 min"),
}
for _de, (_en, _es) in _TR_V49.items():
    TRANSLATIONS["en"].setdefault(_de, _en)
    TRANSLATIONS["es"].setdefault(_de, _es)

# ── Short per URL: Fortschrittsfenster
_TR_V50 = {
 "Link prüfen": ("Check link", "Comprobar enlace"),
 "YouTube-Werkzeug vorbereiten": ("Prepare YouTube tool", "Preparar herramienta de YouTube"),
 "Video-Infos abrufen": ("Fetch video info", "Obtener datos del vídeo"),
 "Video laden": ("Download video", "Descargar vídeo"),
 "Stärksten Abschnitt suchen": ("Find the strongest section", "Buscar la sección más fuerte"),
 "Short erstellen (45 s · 9:16)": ("Create short (45 s · 9:16)", "Crear short (45 s · 9:16)"),
 "Fertig: ": ("Done: ", "Listo: "),
 "Der Short konnte nicht erstellt werden.": ("The short could not be created.", "No se pudo crear el short."),
 "Ordner öffnen": ("Open folder", "Abrir carpeta"),
 "Gespeichert im Ordner output/Shorts": ("Saved in the output/Shorts folder", "Guardado en la carpeta output/Shorts"),
 "Du kannst dieses Fenster schließen und weiterarbeiten – der Short entsteht im Hintergrund.": (
    "You can close this window and keep working – the short is created in the background.",
    "Puedes cerrar esta ventana y seguir trabajando: el short se crea en segundo plano."),
 "Kopiere den Link eines YouTube-Videos (z. B. aus der Adresszeile des Browsers) und wähle dann noch einmal „Short per URL“.": (
    "Copy the link of a YouTube video (e.g. from the browser's address bar) and then choose “Short from URL” again.",
    "Copia el enlace de un vídeo de YouTube (p. ej. de la barra de direcciones del navegador) y elige de nuevo «Short desde URL»."),
}
for _de, (_en, _es) in _TR_V50.items():
    TRANSLATIONS["en"].setdefault(_de, _en)
    TRANSLATIONS["es"].setdefault(_de, _es)

# ── 4.1: Problem melden, Cache leeren, Vorbereiten, Hänger-Wächter
_TR_V51 = {
 "Cache geleert: {size} frei": ("Cache emptied: {size} freed", "Caché vaciada: {size} liberados"),
 "Cache leeren": ("Empty cache", "Vaciar caché"),
 "Clip wird vorbereitet: ": ("Preparing clip: ", "Preparando clip: "),
 "Der Cache belegt {size}. Darin liegen nur Zwischendateien, die das Programm jederzeit neu erstellen kann.\n\nDeine Videos, Clips, Fotos und Einstellungen bleiben erhalten. Das nächste Rendern dauert etwas länger.\n\nJetzt leeren?": (
    "The cache takes up {size}. It only holds temporary files the program can recreate at any time.\n\nYour videos, clips, photos and settings are kept. The next render takes a little longer.\n\nEmpty it now?",
    "La caché ocupa {size}. Solo contiene archivos intermedios que el programa puede volver a crear en cualquier momento.\n\nTus vídeos, clips, fotos y ajustes se conservan. El próximo renderizado tardará un poco más.\n\n¿Vaciarla ahora?"),
 "Der Cache ist bereits leer.": ("The cache is already empty.", "La caché ya está vacía."),
 "Der Fehlerbericht konnte nicht erstellt werden.": ("The error report could not be created.", "No se pudo crear el informe de error."),
 "Der Fehlerbericht liegt auf deinem Desktop:": ("The error report is on your desktop:", "El informe de error está en tu escritorio:"),
 "Enthalten sind nur Protokolle, Dateinamen und technische Angaben – keine Videos, Fotos oder Musik.": (
    "It only contains logs, file names and technical details – no videos, photos or music.",
    "Solo contiene registros, nombres de archivo y datos técnicos; ningún vídeo, foto ni música."),
 "Fehlerbericht gespeichert: ": ("Error report saved: ", "Informe de error guardado: "),
 "Fehlerbericht konnte nicht erstellt werden": ("Error report could not be created", "No se pudo crear el informe de error"),
 "Fehlerbericht wird erstellt …": ("Creating error report …", "Creando informe de error …"),
 "Ordner jetzt öffnen?": ("Open the folder now?", "¿Abrir la carpeta ahora?"),
 "Problem melden": ("Report a problem", "Informar de un problema"),
 "Problem melden …": ("Report a problem …", "Informar de un problema …"),
 "Schick diese Datei an den Entwickler – zum Beispiel per E-Mail oder WhatsApp, oder hänge sie an eine Fehlermeldung auf GitHub an.": (
    "Send this file to the developer – for example by e-mail or WhatsApp, or attach it to a bug report on GitHub.",
    "Envía este archivo al desarrollador, por ejemplo por correo o WhatsApp, o adjúntalo a un informe de error en GitHub."),
 "ffmpeg reagiert seit {n} Minuten nicht mehr und wurde abgebrochen. Bitte über ⚙ → Problem melden einen Fehlerbericht erstellen.": (
    "ffmpeg has not responded for {n} minutes and was stopped. Please create an error report via ⚙ → Report a problem.",
    "ffmpeg no responde desde hace {n} minutos y se ha detenido. Crea un informe de error en ⚙ → Informar de un problema."),
 "vorbereiten": ("preparing", "preparando"),
 "Fehlerbericht erstellt": ("Error report created", "Informe de error creado"),
 "Die Datei liegt auf deinem Desktop:": ("The file is on your desktop:", "El archivo está en tu escritorio:"),
 "Schick sie an den Entwickler – oder melde den Fehler auf GitHub.": ("Send it to the developer – or report the problem on GitHub.", "Envíaselo al desarrollador o informa del problema en GitHub."),
 "Fehlerbericht": ("Error report", "Informe de error"),
 "Auf GitHub melden": ("Report on GitHub", "Informar en GitHub"),
 "Was ist passiert?": ("What happened?", "¿Qué ha pasado?"),
 "MVG-Version: ": ("MVG version: ", "Versión de MVG: "),
 "(Bitte die ZIP-Datei vom Desktop hier hineinziehen.)": ("(Please drag the ZIP file from your desktop into this field.)", "(Arrastra aquí el archivo ZIP de tu escritorio.)"),
 "Wenn du den Entwickler kennst, schick ihm die Datei einfach direkt – zum Beispiel per WhatsApp oder E-Mail.": (
    "If you know the developer, simply send him the file directly – for example by WhatsApp or e-mail.",
    "Si conoces al desarrollador, envíale el archivo directamente, por ejemplo por WhatsApp o correo."),
 "Sonst: „Auf GitHub melden“ öffnet die Seite für Fehlermeldungen und den Ordner mit der Datei. Beschreib kurz, was passiert ist, und zieh die ZIP-Datei in das Textfeld (dafür brauchst du ein kostenloses GitHub-Konto).": (
    "Otherwise: “Report on GitHub” opens the bug report page and the folder with the file. Briefly describe what happened and drag the ZIP file into the text field (you need a free GitHub account for this).",
    "Si no: «Informar en GitHub» abre la página de informes de error y la carpeta con el archivo. Describe brevemente qué ha pasado y arrastra el archivo ZIP al campo de texto (necesitas una cuenta gratuita de GitHub)."),
}
for _de, (_en, _es) in _TR_V51.items():
    TRANSLATIONS["en"].setdefault(_de, _en)
    TRANSLATIONS["es"].setdefault(_de, _es)


# =====================================================================================
#                                     Start
# =====================================================================================
_trace("vor Sprachabfrage")
if not LANG:
    open_language_dialog(first_start=True)
    if not LANG:
        LANG = "de"
_trace("Sprache ok: " + LANG)

# Cache automatisch aufräumen (Hintergrund): alte Segmente, lange unbenutzte Vorbereitungen und Vorschaubilder
threading.Thread(target=cleanup_cache, daemon=True).start()
setup_user_folders()          # manual/ (PDF-Anleitungen) und logo/ (MVG-Logo als Vorlage)

app = _boot_root(dnd=True)
_trace("Hauptfenster erzeugt (Drag&Drop: %s)" % bool(getattr(app, "TkdndVersion", None)))
init_vars()
build_shell()
current_step = 1 if load_settings().get("welcome_v4") else 0
refresh_page()
if current_step == 1:
    ui_status(tr("bereit"))
if not getattr(app, "TkdndVersion", None):
    ui_status(tr("Ziehen und Ablegen ist nicht verfügbar – bitte die Buttons verwenden."))

app.protocol("WM_DELETE_WINDOW", on_closing)
app.bind("<ButtonRelease-1>", _global_release, add="+")
app.bind("<FocusIn>", _on_app_focus, add="+")
app.after(40, _pump_ui)
app.after(1500, check_for_update)       # still nach neuer Version fragen (ohne Internet: nichts)

_trace("mainloop")
app.mainloop()
