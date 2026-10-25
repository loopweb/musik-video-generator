@echo off
echo.
echo  ============================================
echo   Musik Video Generator v3.0 - EXE Build
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
  "C:\Projekt\gui_step3.py"

echo.
if %ERRORLEVEL% == 0 (
    echo  Build erfolgreich! EXE liegt in: dist\MVG.exe
    echo.
    echo  WICHTIG: alle drei PDF-Anleitungen (MVG_Bedienungsanleitung.pdf, MVG_Manual_EN.pdf, MVG_Manual_ES.pdf) manuell in den dist-Ordner
    echo  kopieren, damit sie neben der EXE liegt und ohne Programmstart
    echo  geoeffnet werden kann.
) else (
    echo  Build fehlgeschlagen! Siehe Fehler oben.
)
echo.
pause
