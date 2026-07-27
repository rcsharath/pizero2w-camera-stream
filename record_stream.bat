@echo off
TITLE Raspberry Pi Zero 2W - Continuous H.264 Stream Recorder & Local UDP Relay
cd /d "%~dp0"

:: Stale Lockfile Detection & Auto-Cleanup
if exist "%~dp0recorder.lock" (
    tasklist | findstr /i "ffmpeg.exe" >nul
    if errorlevel 1 (
        echo [INFO] Removing stale lockfile from previous session...
        del "%~dp0recorder.lock" 2>nul
    ) else (
        echo ===================================================
        echo WARNING: Recording is ALREADY active in another process!
        echo Delete '%~dp0recorder.lock' if previous process crashed.
        echo ===================================================
        pause
        exit /b
    )
)

:: Create lock file
echo %date% %time% > "%~dp0recorder.lock"

echo ===================================================
echo   Raspberry Pi Zero 2W H.264 Stream Recorder & Relay
echo ===================================================
echo Output Folder : %~dp0
echo Stream Source : tcp://rcsharathpi.local:8888 (Hardware H.264)
echo UDP Loopback   : udp://127.0.0.1:8889 (Local VLC Preview)
echo MP4 Segment   : 90 Seconds per file
echo Lock State    : ACTIVE
echo ===================================================
echo Press Ctrl+C in this window to stop recording at any time.
echo.

:: Run FFmpeg to capture H.264 stream into 90-second MP4 files AND broadcast local UDP loopback stream for VLC
ffmpeg -i tcp://rcsharathpi.local:8888 ^
  -c copy -f segment -segment_time 90 -reset_timestamps 1 -strftime 1 "rec_%%Y%%m%%d_%%H%%M%%S.mp4" ^
  -c copy -f mpegts "udp://127.0.0.1:8889?pkt_size=1316"

:: Cleanup lock file on exit
del "%~dp0recorder.lock" 2>nul
pause
