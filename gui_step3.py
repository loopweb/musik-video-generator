# Musik Video Generator v2.3

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
SHORTS_FADE_SEC = 2.0

CUT_SECONDS_GENTLE = 3.50
CUT_SECONDS_SLOW   = 2.20
CUT_SECONDS_NORMAL = 1.70
CUT_SECONDS_FAST   = 0.95

POOL_CHOICES = [10, 20, 30]
POOL_LABELS = ["Automatisch"] + [f"{x} Clips" for x in POOL_CHOICES]

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


def get_output_path_from_music(music_path: str) -> Path:
    stem = Path(music_path).stem
    bad = '<>:"/\\|?*'
    for ch in bad:
        stem = stem.replace(ch, "_")
    stem = stem.rstrip(".").strip()
    if not stem:
        stem = "output"
    return OUTPUT_DIR / f"{stem}.mp4"


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
        status_cb(f"Normalisiere: {original.name}")

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
        raise RuntimeError("Keine Medien zum Auswählen vorhanden.")
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
    """Zeigt die Kurzanleitung direkt im Programm — kein PDF-Umweg nötig."""
    win = ctk.CTkToplevel(app)
    win.title("Kurzanleitung")

    win_w, win_h = 560, 600
    app.update_idletasks()
    x = app.winfo_x() + (app.winfo_width() - win_w) // 2
    y = app.winfo_y() + (app.winfo_height() - win_h) // 2
    win.geometry(f"{win_w}x{win_h}+{x}+{y}")

    win.resizable(False, True)
    win.grab_set()
    win.transient(app)

    ctk.CTkLabel(
        win, text="Kurzanleitung",
        font=ctk.CTkFont(size=20, weight="bold")
    ).pack(pady=(16, 10))

    scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
    scroll.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    def section(title: str, text: str):
        ctk.CTkLabel(
            scroll, text=title, font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w", justify="left"
        ).pack(fill="x", pady=(10, 2))
        ctk.CTkLabel(
            scroll, text=text, font=ctk.CTkFont(size=12),
            anchor="w", justify="left", wraplength=500
        ).pack(fill="x", pady=(0, 4))

    section(
        "1. Vor dem ersten Start",
        "Clip-Ordner mit eigenen Videos befüllen — das Programm wird "
        "ohne Clips ausgeliefert."
    )
    section(
        "2. Musik & Format",
        "Musikdatei wählen, Format und Schnittgeschwindigkeit festlegen."
    )
    section(
        "3. Clip-Quelle",
        "Internen Pool und Stimmung wählen, oder externe Clips/Fotos "
        "einbinden. Mix-Modus kombiniert beides. Optional: Pre-Intro "
        "mit eigenem Ton vor die Musik setzen."
    )
    section(
        "4. Rendern",
        "Auf 'Render' klicken. Ein Sound signalisiert das Ende."
    )
    section(
        "5. Clip-Editor (optional)",
        "Reihenfolge der Clips per Drag & Drop anpassen, dann "
        "'Übernehmen' — ohne kompletten Neu-Render."
    )
    section(
        "6. Fertiges Video",
        "Über 'Video anschauen', 'Output-Ordner' oder 'YouTube' direkt "
        "weiterverwenden."
    )

    ctk.CTkButton(win, text="PDF-Anleitung öffnen", width=180,
                  fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
                  command=open_pdf_manual).pack(pady=(4, 4))
    ctk.CTkButton(win, text="Schließen", width=140, command=win.destroy).pack(pady=(4, 16))


def open_pdf_manual():
    """Öffnet die ausführliche PDF-Anleitung, falls neben der EXE vorhanden."""
    pdf = BASE_DIR / "MVG_Bedienungsanleitung.pdf"
    if pdf.exists():
        open_path_portable(pdf)
    else:
        ui_status("PDF-Anleitung nicht neben dem Programm gefunden")


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
    safe = re.sub(r"[^A-Za-z0-9äöüÄÖÜßáéíóúàèìòùâêîôûãõñüïëÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÃÕÑÜÏË \-_]", " ", title).strip()
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

    return (
        f"drawtext=text='{safe}'"
        f":fontsize={font_size}"
        f":fontcolor=white"
        f":alpha='{alpha_expr}'"
        f":x={margin_x}"
        f":y={y_pos}"
        f":shadowcolor=black@0.75"
        f":shadowx=3:shadowy=3"
    )


# --------------------- Render Engine ---------------------
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
) -> Tuple[int, int, int, list[dict], dict]:

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
        raise RuntimeError("Keine Medien gefunden. (Extern/Intern prüfen)")

    if status_cb:
        status_cb("Analysiere Musik…")
    if progress_cb:
        progress_cb(0.02)

    beat_times, music_dur_full = detect_beats(audio_path)
    music_dur = min(music_dur_full, SHORTS_MAX_SEC) if shorts_limited else music_dur_full

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

    start_offset = intro_len
    cut_points2: list[float] = []
    if start_offset < music_dur - 0.05:
        cut_points2 = [t for t in cut_points if t >= start_offset]
        if not cut_points2 or abs(cut_points2[0] - start_offset) > 1e-6:
            cut_points2 = [start_offset] + cut_points2
        if cut_points2[-1] < music_dur:
            cut_points2.append(music_dur)

    cuts_total = (1 if intro_src else 0) + max(0, len(cut_points2) - 1)

    if status_cb:
        fmt = "Shorts 9:16 · 58s" if shorts_limited else ("Shorts 9:16" if is_shorts else "Normal 16:9")
        src_txt = f"Extern(V:{len(ext_vid)}|F:{len(ext_ph)}) Intern({len(pool_vid)}) Mode:{source_mode}"
        status_cb(f"{fmt} | {src_txt} | Cuts: {cuts_total}")
    if progress_cb:
        progress_cb(0.05)

    temp_dir = Path(tempfile.mkdtemp(prefix="beatcut_"))

    # Segmente persistent speichern für Timeline-Editor
    for f in SEGMENTS_DIR.glob("seg*.mp4"):
        try: f.unlink()
        except Exception: pass
    seg_dir = SEGMENTS_DIR

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
                status_cb("Rendere Pre-Intro…")
            src_vorspann = prepared_path_video_only(vorspann_src)
            out_seg = seg_dir / f"seg{seg_index:04d}.mp4"
            seg_index += 1

            vf_vorspann = build_vf_base(out_w, out_h, "shorts" if is_shorts else "normal")
            vorspann_len = ffprobe_duration(vorspann_src)

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
            segments_files.append(out_seg)
            seg_durations.append(float(vorspann_len))
            seg_labels.append(f"Vorspann: {vorspann_src.stem}")

        # Intro
        if intro_src is not None and intro_len > 0.0:
            if status_cb:
                status_cb("Rendere Intro…")
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
            segments_files.append(out_seg)
            seg_durations.append(float(intro_len))
            seg_labels.append(f"Intro: {intro_src.stem}")

        if status_cb:
            status_cb("Rendere Segmente…")

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

            # Segment rendern
            if chosen_kind == "photo":
                img = chosen_path
                used_photos.add(img)

                cmd = [
                    ffmpeg_cmd(), "-y", "-hide_banner",
                    "-loop", "1",
                    "-i", str(img),
                    "-t", f"{seg_len_out:.4f}",
                    "-vf", vf,
                    "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "18", "-preset", "veryfast",
                    "-r", str(FPS), "-fps_mode", "cfr",
                    str(out_seg)
                ]
                run_hidden(cmd)
                seg_labels.append(img.stem)
            else:
                clip = chosen_path
                last_vid = clip
                used_videos.add(clip)

                src = prepared_path_video_only(clip)
                clip_dur = ffprobe_duration(src)
                max_start = max(0.0, clip_dur - seg_len_out - 0.05)
                start = random.uniform(0.0, max_start) if max_start > 0 else 0.0

                # Clip loopen falls er kürzer als das Segment ist
                loop_flag = ["-stream_loop", "-1"] if clip_dur < seg_len_out + 0.1 else []
                cmd = [
                    ffmpeg_cmd(), "-y", "-hide_banner", "-fflags", "+genpts",
                    ] + loop_flag + [
                    "-ss", f"{start:.4f}",
                    "-i", str(src),
                    "-t", f"{seg_len_out:.4f}",
                    "-vf", vf,
                    "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "18", "-preset", "veryfast",
                    "-fps_mode", "cfr",
                    str(out_seg)
                ]
                run_hidden(cmd)
                seg_labels.append(clip.stem)

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
                status_cb("Hänge Outro an…")

            outro_seg = SEGMENTS_DIR / f"seg{seg_index:04d}.mp4"
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

        if progress_cb:
            progress_cb(0.88)
        if status_cb:
            status_cb("Klebe Segmente…")

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
            status_cb("Muxe Audio…")
        if progress_cb:
            progress_cb(0.92)

        if shorts_limited:
            fade_start, fade_d = compute_fade(music_dur)
            a_chain = (
                f"atrim=0:{music_dur:.4f},"
                f"afade=t=out:st={fade_start:.4f}:d={fade_d:.4f},"
                "asetpts=PTS-STARTPTS"
            )
        else:
            a_chain = f"atrim=0:{music_dur:.4f},asetpts=PTS-STARTPTS"

        # Outro-Audio: nur wenn Outro am Ende liegt und eigenen Ton hat.
        # Es wird um music_dur verzögert, damit es NACH der Musik startet.
        outro_audio_path = None
        outro_delay_ms = 0
        if outro_src is not None and outro_src.exists() and has_audio_stream(outro_src):
            outro_audio_path = outro_src
            outro_delay_ms = int(music_dur * 1000)

        # Vorspann-Audio: läuft VOR der Musik. Die Musik wird um vorspann_len verzögert.
        vorspann_audio_path = None
        vorspann_delay_ms = 0
        music_delay_ms = 0
        if vorspann_src is not None and vorspann_src.exists() and has_audio_stream(vorspann_src):
            vorspann_audio_path = vorspann_src
            music_delay_ms = int(vorspann_len * 1000)

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
            status_cb(f"Fertig: {out_path.name}")

        seg_meta = [
            {
                "path":  str(p),
                "label": seg_labels[i] if i < len(seg_labels) else p.stem,
            }
            for i, p in enumerate(segments_files)
        ]
        # Titel-Info + Outro/Vorspann-Quelle mitgeben, damit Re-Concat alles wiederholen kann
        render_info = {
            "title_overlay": title_overlay,
            "title_text":    title_text,
            "intro_len":     intro_len,
            "out_w":         out_w,
            "out_h":         out_h,
            "shorts_limited":shorts_limited,
            "music_dur":     music_dur,
            "outro_src":     str(outro_src) if outro_src is not None else None,
            "vorspann_src":  str(vorspann_src) if vorspann_src is not None else None,
            "vorspann_len":  vorspann_len,
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

app = ctk.CTk()
app.title("Musik Video Generator  v2.3")
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

music_var = ctk.StringVar(value="(keine Musik gewählt)")
clips_var = ctk.StringVar(value=str(DEFAULT_CLIPS_DIR))
photos_var = ctk.StringVar(value=str(PHOTOS_DIR))

format_var = ctk.StringVar(value="normal")   # "normal" | "shorts" | "shorts58"
speed_var = ctk.StringVar(value="normal")

intro_var = ctk.BooleanVar(value=True)
outro_var = ctk.BooleanVar(value=True)    # ← default ON
vorspann_var = ctk.BooleanVar(value=False)  # ← default AUS
title_var = ctk.BooleanVar(value=False)  # default AUS

pool_on_var = ctk.BooleanVar(value=False)   # default AUS
external_var = ctk.BooleanVar(value=False)
photos_mode_var = ctk.BooleanVar(value=False)

pool_value_var = ctk.StringVar(value="Automatisch")
pool_hint_var = ctk.StringVar(value="Pool: 10 / 20 / 30 / ALLE")

status_var = ctk.StringVar(value="bereit")
out_name_var = ctk.StringVar(value="Output: (Musik auswählen)")
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


def is_shorts_mode() -> bool:
    return format_var.get() in ("shorts", "shorts58")

def is_shorts_limited() -> bool:
    return format_var.get() == "shorts58"


def selected_moods() -> list[str]:
    chosen = [m for m, v in mood_vars.items() if bool(v.get())]
    return chosen if chosen else list(MOODS)


def parse_pool_value() -> int:
    v = str(pool_value_var.get()).strip().upper()
    if v == "ALLE":
        return 10**9
    if v == "AUTOMATISCH":
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
        count_var.set(f"Clips: {v} | Fotos: {p}")
    else:
        count_var.set(str(v))


def update_output_label():
    if music_file:
        out = get_output_path_from_music(music_file)
        out_name_var.set(f"Output: {out.name}")
    else:
        out_name_var.set("Output: (Musik auswählen)")


def update_source_status():
    ext_on = bool(external_var.get()) or bool(photos_mode_var.get())
    int_on = bool(pool_on_var.get())

    if ext_on and not int_on:
        ui_status("Quelle: NUR EXTERN")
    elif int_on and not ext_on:
        ui_status("Quelle: NUR INTERN")
    elif int_on and ext_on:
        ui_status("Quelle: MIX (Extern bevorzugt)")
    else:
        ui_status("Quelle: (nichts gewählt)")


def update_path_status_lines():
    if bool(external_var.get()):
        clips_path_status_var.set(f"Clips: {clips_var.get()}")
    else:
        clips_path_status_var.set(f"Clips (intern): {DEFAULT_CLIPS_DIR}")

    if bool(photos_mode_var.get()):
        photos_path_status_var.set(f"Fotos: {photos_var.get()}")
    else:
        photos_path_status_var.set("")


def set_pool_controls_state():
    if not bool(pool_on_var.get()):
        pool_hint_var.set("Pool: AUS")
        opt_pool.configure(state="disabled")
        try:
            btn_manual.configure(state="disabled")
        except Exception:
            pass
    else:
        pool_hint_var.set("Pool: alle / 10 / 20 / 30")
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
    btn_standard.configure(fg_color=active if is_std else BTN_DEFAULT_FG)
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
    ui_status("Stimmung: alle" if not chosen else "Stimmung: " + " + ".join(chosen))


def choose_music():
    global music_file
    selected = filedialog.askopenfilename(title="Musik auswählen", filetypes=[("Audio", "*.mp3 *.wav")])
    if selected:
        music_file = selected
        music_var.set(music_file)
        update_output_label()
        ui_status("bereit")


def choose_external_clips_folder():
    external_var.set(True)
    on_external_toggle()
    selected = filedialog.askdirectory(title="Externer Clip Ordner")
    if selected:
        clips_var.set(str(Path(selected)))
        refresh_count()
        update_path_status_lines()
        ui_status("Externer Clip-Ordner gesetzt")
    else:
        external_var.set(False)
        on_external_toggle()


def choose_photos_folder():
    photos_mode_var.set(True)
    on_photos_toggle()
    selected = filedialog.askdirectory(title="Foto Ordner")
    if selected:
        photos_var.set(str(Path(selected)))
        refresh_count()
        update_path_status_lines()
        ui_status("Foto-Ordner gesetzt")
    else:
        photos_mode_var.set(False)
        on_photos_toggle()


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


def on_format_change():
    update_output_label()
    v = format_var.get()
    if v == "shorts58":
        ui_status("Shorts 9:16 · max 58s (Social Media)")
    elif v == "shorts":
        ui_status("Normal 9:16 · volle Länge")
    else:
        ui_status("Normal-Modus (16:9)")


def open_output_folder():
    open_path_portable(OUTPUT_DIR)


def open_last_video():
    if last_output_path and last_output_path.exists():
        open_path_portable(last_output_path)
    else:
        ui_status("kein Video vorhanden")


def open_youtube_upload(explicit_path: Optional[Path] = None):
    """Öffnet die allgemeine YouTube Upload-Seite im Browser — unabhängig vom Programmstatus."""
    import webbrowser
    webbrowser.open("https://www.youtube.com/upload")


def disable_controls(disabled: bool):
    state = "disabled" if disabled else "normal"
    for w in [
        btn_music, btn_reset, btn_help,
        rb_fmt_normal, rb_fmt_shorts, rb_fmt_shorts58,
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
            ui_status("Reset nicht möglich während Render läuft")
            return
    except Exception:
        pass  # Im Editor-Modus existiert btn_render nicht — Reset trotzdem erlauben

    # Cache-Größe ermitteln
    cache_files = list(CACHE_DIR.glob("*.mp4"))
    cache_count = len(cache_files)
    cache_mb = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)

    if cache_count > 0:
        msg = (
            f"Projekt zurücksetzen?\n\n"
            f"Cache enthält {cache_count} Datei(en) ({cache_mb:.1f} MB).\n"
            f"Cache ebenfalls leeren?"
        )
        result = messagebox.askyesnocancel("Projekt-Reset", msg)
        if result is None:       # Abbrechen
            return
        clear_cache = result     # Ja = True, Nein = False
    else:
        ok = messagebox.askokcancel("Projekt-Reset", "Projekt zurücksetzen?")
        if not ok:
            return
        clear_cache = False

    if clear_cache:
        for f in cache_files:
            try:
                f.unlink()
            except Exception:
                pass
        ui_status(f"Cache geleert ({cache_count} Dateien)")

    music_file = None
    last_output_path = None
    music_var.set("(keine Musik gewählt)")

    format_var.set("normal")
    speed_var.set("normal")
    intro_var.set(True)
    outro_var.set(True)
    vorspann_var.set(False)
    title_var.set(False)
    pool_on_var.set(False)
    external_var.set(False)
    photos_mode_var.set(False)
    pool_value_var.set("Automatisch")
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
        ui_status("bitte Musik auswählen")
        return

    shorts = is_shorts_mode()
    limited = is_shorts_limited()
    out_path = get_output_path_from_music(music_file)
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
        ui_status("keine externen Clips gefunden")
        return
    if ext_photos_on and (not photos_folder.exists() or count_image_files(photos_folder) < 1):
        ui_status("keine externen Fotos gefunden")
        return
    if pool_on and len(clips_from_moods(moods)) < 1 and not (ext_clips_on or ext_photos_on):
        ui_status("Pool: keine Clips in der gewählten Stimmung")
        return
    if not pool_on and not (ext_clips_on or ext_photos_on):
        ui_status("Keine Quelle aktiv")
        return

    disable_controls(True)
    ui_progress(0.0)
    ui_status("Starte…")

    def worker():
        try:
            _nv, _np, _ct, seg_meta, render_info = render_video(
                audio_path=music_file,
                clips_folder=clips_folder,
                photos_folder=photos_folder,
                out_path=out_path,
                use_intro=use_intro,
                use_outro=use_outro,
                use_vorspann=use_vorspann,
                speed=speed,
                pool_value=pool_value,
                pool_on=pool_on,
                external_clips_on=ext_clips_on,
                external_photos_on=ext_photos_on,
                is_shorts=shorts,
                shorts_limited=limited,
                moods_selected=moods,
                title_overlay=use_title,
                title_text=title_text,
                status_cb=lambda s: app.after(0, ui_status, s),
                progress_cb=lambda p: app.after(0, ui_progress, p),
            )
            app.after(0, play_done_sound)
            app.after(0, ui_status, f"Fertig: {out_path.name}")
            app.after(0, lambda: _after_render(seg_meta, render_info, music_file, out_path))
        except Exception as e:
            app.after(0, ui_status, f"Fehler: {e}")
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
        ui_status("Keine internen Clips gefunden")
        return

    moods_label = ", ".join(moods) if moods else "Alle"
    win = ctk.CTkToplevel(app)
    win.title(f"Manuelle Clip-Auswahl  —  Stimmung: {moods_label}")
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
    ctk.CTkLabel(header, text=f"Clips auswaehlen  ({moods_label})", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
    count_lbl = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=11))
    count_lbl.pack(side="right")

    def update_count():
        n = sum(1 for v in selected.values() if v.get())
        count_lbl.configure(text=f"{n} / {len(all_clips)} ausgewaehlt")
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

    ctk.CTkButton(btn_row, text="Alle", width=100, command=select_all).pack(side="left", padx=(0, 8))
    ctk.CTkButton(btn_row, text="Keine", width=100, command=select_none).pack(side="left")

    # OK / Abbrechen
    def on_ok():
        global manual_clip_selection, manual_clip_moods
        win_alive[0] = False
        chosen = [p for p, v in selected.items() if v.get()]
        if not chosen:
            manual_clip_selection = None
            ui_status("Manuelle Auswahl: alle Clips")
        elif len(chosen) == len(all_clips):
            manual_clip_selection = None
            ui_status("Manuelle Auswahl: alle Clips")
        else:
            manual_clip_selection = chosen
            manual_clip_moods = moods
            ui_status(f"Manuelle Auswahl: {len(chosen)} Clips")
        refresh_count()
        win.destroy()

    def on_cancel():
        win_alive[0] = False
        win.destroy()

    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=16, pady=(0, 12))
    btn_ok = ctk.CTkButton(foot, text="OK", width=130, command=on_ok, state="disabled")
    btn_ok.pack(side="right", padx=(8, 0))
    ctk.CTkButton(foot, text="Abbrechen", width=130, fg_color="gray40",
                  hover_color="gray30", command=on_cancel).pack(side="right")

    def refresh_ok_state():
        n = sum(1 for v in selected.values() if v.get())
        btn_ok.configure(state="normal" if n > 0 else "disabled")


# ===================== LAYOUT =====================
outer = ctk.CTkFrame(app, corner_radius=16)
outer.pack(fill="both", expand=True, padx=16, pady=10)

# ── Titel
ctk.CTkLabel(
    outer, text="Musik Video Generator",
    font=ctk.CTkFont(size=22, weight="bold")
).pack(pady=(12, 6))

# ── Musik-Zeile
sec_music = ctk.CTkFrame(outer, corner_radius=12)
sec_music.pack(fill="x", padx=16, pady=(0, 8))
sec_music.grid_columnconfigure(1, weight=1)

btn_music = ctk.CTkButton(sec_music, text="Musik wählen", width=160, command=choose_music)
btn_music.grid(row=0, column=0, padx=12, pady=10)

ctk.CTkLabel(sec_music, textvariable=music_var, wraplength=580, justify="left").grid(
    row=0, column=1, sticky="w", padx=8, pady=10
)

btn_reset = ctk.CTkButton(sec_music, text="Reset", width=100, command=on_reset_clicked)
btn_reset.grid(row=0, column=2, padx=(0, 6), pady=10)

btn_help = ctk.CTkButton(sec_music, text="?", width=36, command=open_help)
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

ctk.CTkLabel(sec_fmt, text="Format", font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 4)
)
rb_fmt_normal = ctk.CTkRadioButton(sec_fmt, text="Normal  16:9", variable=format_var,
                                    value="normal", command=on_format_change)
rb_fmt_normal.pack(anchor="w", padx=12, pady=(0, 4))
rb_fmt_shorts = ctk.CTkRadioButton(sec_fmt, text="Normal  9:16", variable=format_var,
                                    value="shorts", command=on_format_change)
rb_fmt_shorts.pack(anchor="w", padx=12, pady=(0, 4))
rb_fmt_shorts58 = ctk.CTkRadioButton(sec_fmt, text="Shorts  9:16 · 58s", variable=format_var,
                                      value="shorts58", command=on_format_change)
rb_fmt_shorts58.pack(anchor="w", padx=12, pady=(0, 10))

# Schnitt
sec_speed = ctk.CTkFrame(mid, corner_radius=12, width=190)
sec_speed.grid(row=0, column=1, sticky="ns", padx=6, pady=0)
sec_speed.pack_propagate(False)
sec_speed.grid_propagate(False)

ctk.CTkLabel(sec_speed, text="Schnitt", font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 4)
)
rb_gentle = ctk.CTkRadioButton(sec_speed, text="Sanft", variable=speed_var, value="gentle")
rb_gentle.pack(anchor="w", padx=12, pady=(0, 4))
rb_slow = ctk.CTkRadioButton(sec_speed, text="Langsam", variable=speed_var, value="slow")
rb_slow.pack(anchor="w", padx=12, pady=(0, 4))
rb_normal = ctk.CTkRadioButton(sec_speed, text="Normal", variable=speed_var, value="normal")
rb_normal.pack(anchor="w", padx=12, pady=(0, 4))
rb_fast = ctk.CTkRadioButton(sec_speed, text="Schnell", variable=speed_var, value="fast")
rb_fast.pack(anchor="w", padx=12, pady=(0, 4))
rb_random = ctk.CTkRadioButton(sec_speed, text="Random", variable=speed_var, value="random")
rb_random.pack(anchor="w", padx=12, pady=(0, 10))

# Clip-Pool
sec_pool = ctk.CTkFrame(mid, corner_radius=12)
sec_pool.grid(row=0, column=2, sticky="nsew", padx=(6, 0), pady=0)
sec_pool.grid_columnconfigure(0, weight=1)

ctk.CTkLabel(sec_pool, text="Interner Clip-Pool", font=ctk.CTkFont(size=13, weight="bold")).grid(
    row=0, column=0, sticky="w", padx=12, pady=(10, 2)
)
ctk.CTkLabel(sec_pool, text="Wähle deine Stimmung",
             text_color=("gray40", "gray70"), font=ctk.CTkFont(size=11)).grid(
    row=1, column=0, sticky="w", padx=12, pady=(0, 4)
)

mood_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
mood_row.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 6))

cb_tr  = ctk.CTkCheckBox(mood_row, text="Traurig",   variable=mood_vars["Traurig"],   command=on_moods_changed)
cb_neu = ctk.CTkCheckBox(mood_row, text="Neutral",   variable=mood_vars["Neutral"],   command=on_moods_changed)
cb_glu = ctk.CTkCheckBox(mood_row, text="Glücklich", variable=mood_vars["Glücklich"], command=on_moods_changed)
cb_ani = ctk.CTkCheckBox(mood_row, text="Animation", variable=mood_vars["Animation"], command=on_moods_changed)
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
btn_manual = ctk.CTkButton(pool_btn_row, text="Manuelle Auswahl", width=150, command=open_clip_selector)
btn_manual.grid(row=0, column=1)

# ── Clips & Quellen
sec_files = ctk.CTkFrame(outer, corner_radius=12)
sec_files.pack(fill="x", padx=16, pady=(0, 8))

ctk.CTkLabel(sec_files, text="Quellen & Optionen", font=ctk.CTkFont(size=13, weight="bold")).pack(
    anchor="w", padx=12, pady=(10, 6)
)

# Schalter-Zeile
sw_row = ctk.CTkFrame(sec_files, fg_color="transparent")
sw_row.pack(anchor="w", padx=12, pady=(0, 8))

sw_vorspann = ctk.CTkSwitch(sw_row, text="Pre-Intro", variable=vorspann_var)
sw_vorspann.grid(row=0, column=0, padx=(0, 16))

sw_intro = ctk.CTkSwitch(sw_row, text="Intro", variable=intro_var)
sw_intro.grid(row=0, column=1, padx=(0, 16))

sw_outro = ctk.CTkSwitch(sw_row, text="Outro", variable=outro_var, command=on_outro_toggle)
sw_outro.grid(row=0, column=2, padx=(0, 16))

sw_title = ctk.CTkSwitch(sw_row, text="Titel", variable=title_var)
sw_title.grid(row=0, column=3, padx=(0, 16))

sw_pool = ctk.CTkSwitch(sw_row, text="Interner Pool", variable=pool_on_var, command=on_pool_toggle)
sw_pool.grid(row=0, column=4, padx=(0, 16))

sw_external = ctk.CTkSwitch(sw_row, text="Externe Clips", variable=external_var, command=on_external_toggle)
sw_external.grid(row=0, column=5, padx=(0, 16))

sw_photos = ctk.CTkSwitch(sw_row, text="Externe Fotos", variable=photos_mode_var, command=on_photos_toggle)
sw_photos.grid(row=0, column=6)

# Ordner-Buttons
btn_row = ctk.CTkFrame(sec_files, fg_color="transparent")
btn_row.pack(fill="x", padx=12, pady=(0, 4))
btn_row.grid_columnconfigure(0, weight=1)
btn_row.grid_columnconfigure(1, weight=1)
btn_row.grid_columnconfigure(2, weight=1)

btn_standard = ctk.CTkButton(btn_row, text="Standard (intern)", command=use_standard_clips)
btn_standard.grid(row=0, column=0, sticky="ew", padx=(0, 6))
btn_external = ctk.CTkButton(btn_row, text="Externe Clips wählen…", command=choose_external_clips_folder)
btn_external.grid(row=0, column=1, sticky="ew", padx=(0, 6))
btn_photos = ctk.CTkButton(btn_row, text="Externe Fotos wählen…", command=choose_photos_folder)
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
ctk.CTkLabel(count_line, text="Anzahl:").grid(row=0, column=0, padx=(0, 8))
ctk.CTkLabel(count_line, textvariable=count_var).grid(row=0, column=1)

# ── Render + Clips verschieben nebeneinander
btn_row_render = ctk.CTkFrame(outer, fg_color="transparent")
btn_row_render.pack(pady=(4, 4))

btn_render = ctk.CTkButton(btn_row_render, text="▶  Render", width=240, height=42, command=do_render)
btn_render.grid(row=0, column=0, padx=(0, 8))

btn_edit = ctk.CTkButton(btn_row_render, text="✏  Clips verschieben", width=200, height=42,
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
status_box.insert("1.0", "bereit")
status_box.configure(state="disabled")
ctk.CTkButton(bottom, text="▶  Video anschauen", width=150, command=open_last_video).grid(
    row=1, column=1, sticky="e", padx=6, pady=(0, 10)
)
ctk.CTkButton(bottom, text="Output-Ordner", width=130, command=open_output_folder).grid(
    row=1, column=2, sticky="e", padx=6, pady=(0, 10)
)
ctk.CTkButton(bottom, text="YouTube", width=110, command=open_youtube_upload).grid(
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
        outer, text="Musik Video Generator",
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(pady=(12, 6))

    # ── Obere Zeile: Abbrechen-Button + Hinweis + Reset + Hilfe
    sec_top = ctk.CTkFrame(outer, corner_radius=12)
    sec_top.pack(fill="x", padx=16, pady=(0, 8))
    sec_top.grid_columnconfigure(1, weight=1)

    btn_abbrechen = ctk.CTkButton(
        sec_top, text="✕  Abbrechen", width=160,
        fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
        command=_cancel_editor
    )
    btn_abbrechen.grid(row=0, column=0, padx=12, pady=10)
    outer._btn_abbrechen = btn_abbrechen  # type: ignore[attr-defined]

    ctk.CTkLabel(
        sec_top,
        text="Ordne die Clips per Drag & Drop neu an und klicke auf Übernehmen",
        font=ctk.CTkFont(size=12),
        text_color=("gray30", "gray75"),
        anchor="w",
    ).grid(row=0, column=1, sticky="w", padx=8, pady=10)

    ctk.CTkButton(
        sec_top, text="Reset", width=100, command=on_reset_clicked
    ).grid(row=0, column=2, padx=(0, 6), pady=10)

    ctk.CTkButton(
        sec_top, text="?", width=36, command=open_help
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
        text="⏳  Vorschaubilder werden geladen…",
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
        outer, text="✔  Übernehmen", width=240, height=42,
        state="disabled",
        command=lambda: _do_reorder(audio_path, out_path)
    )
    btn_übernehmen.pack(pady=(4, 4))
    outer._btn_reorder = btn_übernehmen  # type: ignore[attr-defined]

    ctk.CTkLabel(
        outer,
        text="Ziehe Clips an die gewünschte Position",
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
    status_ed.insert("1.0", "Clips verschieben oder Übernehmen klicken")
    status_ed.configure(state="disabled")
    outer._status_ed = status_ed  # type: ignore[attr-defined]

    ctk.CTkButton(
        bottom_ed, text="▶  Video anschauen", width=150,
        command=lambda: open_path_portable(out_path)
    ).grid(row=1, column=1, sticky="e", padx=6, pady=(0, 10))

    ctk.CTkButton(
        bottom_ed, text="Output-Ordner", width=130,
        command=open_output_folder
    ).grid(row=1, column=2, sticky="e", padx=6, pady=(0, 10))

    ctk.CTkButton(
        bottom_ed, text="YouTube", width=110,
        command=lambda: open_youtube_upload(out_path)
    ).grid(row=1, column=3, sticky="e", padx=12, pady=(0, 10))


def _cancel_editor():
    """Zurück zum Hauptfenster — Reihenfolge wird zurückgesetzt, Einstellungen bleiben."""
    global in_editor_mode, _timeline_order, _tline_changed
    in_editor_mode   = False
    _timeline_order  = list(range(len(timeline_seg_meta)))
    _tline_changed   = False
    _rebuild_main_ui()


def _cancel_editor_keep_order():
    """Zurück nach Übernehmen — neue Reihenfolge bleibt erhalten."""
    global in_editor_mode, _tline_changed
    in_editor_mode = False
    _tline_changed = False
    _rebuild_main_ui()


def _rebuild_main_ui():
    """Baut das Hauptfenster neu auf."""
    for widget in outer.winfo_children():
        widget.destroy()

    # Titel
    ctk.CTkLabel(
        outer, text="Musik Video Generator",
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(pady=(12, 6))

    # Musik-Zeile
    global btn_music, btn_reset, btn_help
    sec_music = ctk.CTkFrame(outer, corner_radius=12)
    sec_music.pack(fill="x", padx=16, pady=(0, 8))
    sec_music.grid_columnconfigure(1, weight=1)
    btn_music = ctk.CTkButton(sec_music, text="Musik wählen", width=160, command=choose_music)
    btn_music.grid(row=0, column=0, padx=12, pady=10)
    ctk.CTkLabel(sec_music, textvariable=music_var, wraplength=580, justify="left").grid(
        row=0, column=1, sticky="w", padx=8, pady=10)
    btn_reset = ctk.CTkButton(sec_music, text="Reset", width=100, command=on_reset_clicked)
    btn_reset.grid(row=0, column=2, padx=(0, 6), pady=10)
    btn_help = ctk.CTkButton(sec_music, text="?", width=36, command=open_help)
    btn_help.grid(row=0, column=3, padx=(0, 12), pady=10)

    # Mittlere Sektion wiederherstellen — einfach neu initialisieren
    _init_mid_section()

    # Render + Clips verschieben nebeneinander
    global btn_render, btn_edit
    btn_row_render = ctk.CTkFrame(outer, fg_color="transparent")
    btn_row_render.pack(pady=(4, 4))
    btn_render = ctk.CTkButton(btn_row_render, text="▶  Render", width=240, height=42, command=do_render)
    btn_render.grid(row=0, column=0, padx=(0, 8))
    btn_edit = ctk.CTkButton(btn_row_render, text="✏  Clips verschieben", width=200, height=42,
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
    status_box.insert("1.0", "bereit")
    status_box.configure(state="disabled")
    ctk.CTkButton(bottom, text="▶  Video anschauen", width=150, command=open_last_video).grid(
        row=1, column=1, sticky="e", padx=6, pady=(0, 10))
    ctk.CTkButton(bottom, text="Output-Ordner", width=130, command=open_output_folder).grid(
        row=1, column=2, sticky="e", padx=6, pady=(0, 10))
    ctk.CTkButton(bottom, text="YouTube", width=110, command=open_youtube_upload).grid(
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
    global rb_fmt_normal, rb_fmt_shorts, rb_fmt_shorts58
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
    ctk.CTkLabel(sec_fmt, text="Format", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))
    rb_fmt_normal = ctk.CTkRadioButton(sec_fmt, text="Normal  16:9", variable=format_var, value="normal", command=on_format_change)
    rb_fmt_normal.pack(anchor="w", padx=12, pady=(0, 4))
    rb_fmt_shorts = ctk.CTkRadioButton(sec_fmt, text="Normal  9:16", variable=format_var, value="shorts", command=on_format_change)
    rb_fmt_shorts.pack(anchor="w", padx=12, pady=(0, 4))
    rb_fmt_shorts58 = ctk.CTkRadioButton(sec_fmt, text="Shorts  9:16 · 58s", variable=format_var, value="shorts58", command=on_format_change)
    rb_fmt_shorts58.pack(anchor="w", padx=12, pady=(0, 10))

    # Schnitt
    sec_speed = ctk.CTkFrame(mid, corner_radius=12, width=190)
    sec_speed.grid(row=0, column=1, sticky="ns", padx=6)
    sec_speed.pack_propagate(False)
    sec_speed.grid_propagate(False)
    ctk.CTkLabel(sec_speed, text="Schnitt", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))
    rb_gentle = ctk.CTkRadioButton(sec_speed, text="Sanft",    variable=speed_var, value="gentle")
    rb_gentle.pack(anchor="w", padx=12, pady=(0, 4))
    rb_slow   = ctk.CTkRadioButton(sec_speed, text="Langsam",  variable=speed_var, value="slow")
    rb_slow.pack(anchor="w", padx=12, pady=(0, 4))
    rb_normal = ctk.CTkRadioButton(sec_speed, text="Normal",   variable=speed_var, value="normal")
    rb_normal.pack(anchor="w", padx=12, pady=(0, 4))
    rb_fast   = ctk.CTkRadioButton(sec_speed, text="Schnell",  variable=speed_var, value="fast")
    rb_fast.pack(anchor="w", padx=12, pady=(0, 4))
    rb_random = ctk.CTkRadioButton(sec_speed, text="Random",   variable=speed_var, value="random")
    rb_random.pack(anchor="w", padx=12, pady=(0, 10))

    # Clip-Pool
    sec_pool = ctk.CTkFrame(mid, corner_radius=12)
    sec_pool.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
    sec_pool.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(sec_pool, text="Interner Clip-Pool", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(sec_pool, text="Wähle deine Stimmung", text_color=("gray40", "gray70"), font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
    mood_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
    mood_row.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 6))
    cb_tr  = ctk.CTkCheckBox(mood_row, text="Traurig",   variable=mood_vars["Traurig"],   command=on_moods_changed)
    cb_neu = ctk.CTkCheckBox(mood_row, text="Neutral",   variable=mood_vars["Neutral"],   command=on_moods_changed)
    cb_glu = ctk.CTkCheckBox(mood_row, text="Glücklich", variable=mood_vars["Glücklich"], command=on_moods_changed)
    cb_ani = ctk.CTkCheckBox(mood_row, text="Animation", variable=mood_vars["Animation"], command=on_moods_changed)
    cb_tr.grid(row=0, column=0, padx=(0, 12))
    cb_neu.grid(row=0, column=1, padx=(0, 12))
    cb_glu.grid(row=0, column=2, padx=(0, 12))
    cb_ani.grid(row=0, column=3)
    ctk.CTkLabel(sec_pool, textvariable=pool_hint_var, font=ctk.CTkFont(size=11)).grid(row=3, column=0, sticky="w", padx=12, pady=(0, 2))
    pool_btn_row = ctk.CTkFrame(sec_pool, fg_color="transparent")
    pool_btn_row.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 10))
    opt_pool   = ctk.CTkOptionMenu(pool_btn_row, values=POOL_LABELS, variable=pool_value_var, width=140)
    opt_pool.grid(row=0, column=0, padx=(0, 8))
    btn_manual = ctk.CTkButton(pool_btn_row, text="Manuelle Auswahl", width=150, command=open_clip_selector)
    btn_manual.grid(row=0, column=1)

    # Quellen & Optionen
    sec_files = ctk.CTkFrame(outer, corner_radius=12)
    sec_files.pack(fill="x", padx=16, pady=(0, 8))
    ctk.CTkLabel(sec_files, text="Quellen & Optionen", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12, pady=(10, 6))
    sw_row = ctk.CTkFrame(sec_files, fg_color="transparent")
    sw_row.pack(anchor="w", padx=12, pady=(0, 8))
    sw_vorspann = ctk.CTkSwitch(sw_row, text="Pre-Intro", variable=vorspann_var)
    sw_vorspann.grid(row=0, column=0, padx=(0, 16))
    sw_intro = ctk.CTkSwitch(sw_row, text="Intro", variable=intro_var)
    sw_intro.grid(row=0, column=1, padx=(0, 16))
    sw_outro = ctk.CTkSwitch(sw_row, text="Outro", variable=outro_var, command=on_outro_toggle)
    sw_outro.grid(row=0, column=2, padx=(0, 16))
    sw_title = ctk.CTkSwitch(sw_row, text="Titel", variable=title_var)
    sw_title.grid(row=0, column=3, padx=(0, 16))
    sw_pool = ctk.CTkSwitch(sw_row, text="Interner Pool", variable=pool_on_var, command=on_pool_toggle)
    sw_pool.grid(row=0, column=4, padx=(0, 16))
    sw_external = ctk.CTkSwitch(sw_row, text="Externe Clips", variable=external_var, command=on_external_toggle)
    sw_external.grid(row=0, column=5, padx=(0, 16))
    sw_photos = ctk.CTkSwitch(sw_row, text="Externe Fotos", variable=photos_mode_var, command=on_photos_toggle)
    sw_photos.grid(row=0, column=6)
    btn_row2 = ctk.CTkFrame(sec_files, fg_color="transparent")
    btn_row2.pack(fill="x", padx=12, pady=(0, 4))
    btn_row2.grid_columnconfigure(0, weight=1)
    btn_row2.grid_columnconfigure(1, weight=1)
    btn_row2.grid_columnconfigure(2, weight=1)
    btn_standard = ctk.CTkButton(btn_row2, text="Standard (intern)", command=use_standard_clips)
    btn_standard.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    BTN_DEFAULT_FG = btn_standard.cget("fg_color")
    btn_external = ctk.CTkButton(btn_row2, text="Externe Clips wählen…", command=choose_external_clips_folder)
    btn_external.grid(row=0, column=1, sticky="ew", padx=(0, 6))
    btn_photos = ctk.CTkButton(btn_row2, text="Externe Fotos wählen…", command=choose_photos_folder)
    btn_photos.grid(row=0, column=2, sticky="ew")
    path_frame = ctk.CTkFrame(sec_files, fg_color="transparent")
    path_frame.pack(fill="x", padx=12, pady=(4, 8))
    ctk.CTkLabel(path_frame, textvariable=clips_path_status_var, font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
    ctk.CTkLabel(path_frame, textvariable=photos_path_status_var, font=ctk.CTkFont(size=11), justify="left").pack(anchor="w")
    count_line = ctk.CTkFrame(sec_files, fg_color="transparent")
    count_line.pack(anchor="w", padx=12, pady=(0, 10))
    ctk.CTkLabel(count_line, text="Anzahl:").grid(row=0, column=0, padx=(0, 8))
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
                frm, text="🔒 Outro",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=("#c0851f", "#e0a53f"),
            ).pack(pady=(0, 3))
        elif is_vorspann:
            ctk.CTkLabel(
                frm, text="🔒 Pre-Intro",
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

        _tline_buttons.append(btn)


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


def _do_reorder(audio_path: str, out_path: Path):
    try:
        outer._btn_reorder.configure(state="disabled")  # type: ignore[attr-defined]
    except Exception:
        pass

    ri = timeline_render_info

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
            ordered_segs = [Path(timeline_seg_meta[i]["path"]) for i in _timeline_order]
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
                a_chain = (
                    f"atrim=0:{music_dur:.4f},"
                    f"afade=t=out:st={fade_start:.4f}:d={fade_d:.4f},"
                    "asetpts=PTS-STARTPTS"
                )
            else:
                a_chain = f"atrim=0:{music_dur:.4f},asetpts=PTS-STARTPTS"

            # Outro-Audio nur wenn das Outro-Segment das LETZTE ist (nicht verschoben)
            # UND die Original-Outro-Quelle noch existiert und Ton hat.
            last_seg = timeline_seg_meta[_timeline_order[-1]]
            last_label = last_seg.get("label", "")
            outro_audio_path = None
            outro_delay_ms = 0
            if last_label.startswith("Outro:"):
                orig_outro = ri.get("outro_src")
                if orig_outro:
                    orig_outro_path = Path(orig_outro)
                    if orig_outro_path.exists() and has_audio_stream(orig_outro_path):
                        outro_audio_path = orig_outro_path
                        outro_delay_ms = int(music_dur * 1000)

            # Vorspann-Audio nur wenn das Vorspann-Segment das ERSTE ist (nicht verschoben)
            # UND die Original-Vorspann-Quelle noch existiert und Ton hat.
            first_seg = timeline_seg_meta[_timeline_order[0]]
            first_label = first_seg.get("label", "")
            vorspann_audio_path = None
            music_delay_ms = 0
            if first_label.startswith("Vorspann:"):
                orig_vorspann = ri.get("vorspann_src")
                if orig_vorspann:
                    orig_vorspann_path = Path(orig_vorspann)
                    if orig_vorspann_path.exists() and has_audio_stream(orig_vorspann_path):
                        vorspann_audio_path = orig_vorspann_path
                        music_delay_ms = int(ri.get("vorspann_len", 0.0) * 1000)

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
            app.after(0, _set_status_ed, f"Fertig: {out_path.name}")
            app.after(0, lambda: outer._btn_reorder.configure(state="normal"))  # type: ignore[attr-defined]
            # Nach Übernehmen: Abbrechen-Button zu "Zurück" ändern (kein Reset der Reihenfolge mehr)
            app.after(0, lambda: outer._btn_abbrechen.configure(  # type: ignore[attr-defined]
                text="✕  Zurück",
                command=_cancel_editor_keep_order
            ))
        except Exception as e:
            app.after(0, _set_progress_ed, 0.0)
            app.after(0, _set_status_ed, f"Fehler: {e}")
            app.after(0, lambda: outer._btn_reorder.configure(state="normal"))  # type: ignore[attr-defined]

    threading.Thread(target=worker, daemon=True).start()


# ── Fenster-Schließen Handler
def on_closing():
    if in_editor_mode:
        ok = messagebox.askokcancel(
            "Beenden",
            "Du befindest dich im Editor. Wirklich beenden?"
        )
        if not ok:
            return
    app.destroy()

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
