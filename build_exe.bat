@echo off
echo.
echo  ============================================
echo   Musik Video Generator v2.3 - EXE Build
echo  ============================================
echo.

py -3.12 -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
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
    echo  WICHTIG: MVG_Bedienungsanleitung.pdf manuell in den dist-Ordner
    echo  kopieren, damit sie neben der EXE liegt und ohne Programmstart
    echo  geoeffnet werden kann.
) else (
    echo  Build fehlgeschlagen! Siehe Fehler oben.
)
echo.
pause
