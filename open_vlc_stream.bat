@echo off
TITLE Open Pi Zero 2W Hardware H.264 Live Stream
echo ===================================================
echo   Opening Pi Zero 2W Live Stream in VLC Player
echo ===================================================
echo Target: tcp/h264://rcsharathpi.local:8888
echo Caching: 300ms (Low Latency)
echo ===================================================
echo.

IF EXIST "C:\Program Files\VideoLAN\VLC\vlc.exe" (
    "C:\Program Files\VideoLAN\VLC\vlc.exe" tcp/h264://rcsharathpi.local:8888 --network-caching=300
) ELSE IF EXIST "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe" (
    "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe" tcp/h264://rcsharathpi.local:8888 --network-caching=300
) ELSE (
    start vlc tcp/h264://rcsharathpi.local:8888 --network-caching=300
)
