@echo off
echo.
echo  ============================================
echo   Musik Video Generator v4.3 - EXE Build
echo  ============================================
echo.

echo  Abhaengigkeiten werden geprueft ...
py -3.12 -m pip install --upgrade "yt-dlp[default]" tkinterdnd2 pyinstaller
echo.

py -3.12 -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --collect-all yt_dlp ^
  --collect-all tkinterdnd2 ^
  --icon="C:\Projekt\icon.ico" ^
  --name="MVG" ^
  --add-data="C:\Projekt\ffmpeg;ffmpeg" ^
  --add-data="C:\Projekt\Sound;Sound" ^
  --add-data="C:\Projekt\splash.png;." ^
  --add-data="C:\Projekt\MVG_Bedienungsanleitung.pdf;." ^
  --add-data="C:\Projekt\MVG_Manual_EN.pdf;." ^
  --add-data="C:\Projekt\MVG_Manual_ES.pdf;." ^
  "C:\Projekt\gui_step3.py"

echo.
if %ERRORLEVEL% == 0 (
    echo  Build erfolgreich! EXE liegt in: dist\MVG.exe
    echo.
    echo  Die drei PDF-Anleitungen sind in die EXE eingebaut.
    echo  Optional koennen sie zusaetzlich neben die EXE gelegt werden,
    echo  damit man sie auch ohne Programmstart oeffnen kann.
) else (
    echo  Build fehlgeschlagen! Siehe Fehler oben.
)
echo.
pause
