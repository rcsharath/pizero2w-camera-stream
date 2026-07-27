@echo off
TITLE Launch Pi Zero 2W Stream Recorder & Live VLC Preview
cd /d "%~dp0"

echo ===================================================
echo   1-Click Stream Recorder & Live VLC Preview
echo ===================================================
echo Launching FFmpeg Multi-Output Stream Recorder...
echo ===================================================

:: Start record_stream.bat in a separate console window
start "Pi Stream Recorder" cmd /c "%~dp0record_stream.bat"

echo Waiting 3 seconds for stream relay buffer initialization...
timeout /t 3 /nobreak >nul

echo Opening Live VLC Preview Window...
start "" cmd /c "%~dp0open_vlc_stream.bat"

echo ===================================================
echo Active! Stream is recording into MP4 files and
echo live previewing in VLC with 0% extra Pi CPU load.
echo Adjust crop, AWB, and presets via http://rcsharathpi.local:8000
echo ===================================================
timeout /t 5
