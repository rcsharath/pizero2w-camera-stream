@echo off
TITLE Open Pi Zero 2W Hardware H.264 Live Stream
cd /d "%~dp0"

echo ===================================================
echo   Opening Pi Zero 2W Live Stream in VLC Player
echo ===================================================

:: Check if FFmpeg recording relay is active on host PC
tasklist | findstr /i "ffmpeg.exe" >nul
if not errorlevel 1 (
    echo Mode: RECORDING RELAY ACTIVE
    echo Target: udp://@127.0.0.1:8889 (Host UDP Loopback)
    set TARGET_URL=udp://@127.0.0.1:8889
) else (
    echo Mode: DIRECT STANDALONE PREVIEW
    echo Target: tcp/h264://rcsharathpi.local:8888 (Pi Hardware H.264)
    set TARGET_URL=tcp/h264://rcsharathpi.local:8888
)

echo Caching: 300ms (Low Latency)
echo ===================================================
echo.

IF EXIST "C:\Program Files\VideoLAN\VLC\vlc.exe" (
    "C:\Program Files\VideoLAN\VLC\vlc.exe" %TARGET_URL% --network-caching=300
) ELSE IF EXIST "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe" (
    "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe" %TARGET_URL% --network-caching=300
) ELSE (
    start vlc %TARGET_URL% --network-caching=300
)
