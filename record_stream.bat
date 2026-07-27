@echo off
TITLE Raspberry Pi Zero 2W - Continuous H.264 Stream Recorder
cd /d "%~dp0"

:: Single-Instance Lock Check
if exist "%~dp0recorder.lock" (
    echo ===================================================
    echo WARNING: Recording is ALREADY active in another window!
    echo Delete '%~dp0recorder.lock' if previous process crashed.
    echo ===================================================
    pause
    exit /b
)

:: Create lock file
echo %date% %time% > "%~dp0recorder.lock"

echo ===================================================
echo   Raspberry Pi Zero 2W H.264 Stream Recorder
echo ===================================================
echo Output Folder: %~dp0
echo Stream Source: tcp://rcsharathpi.local:8888 (Hardware H.264)
echo Segment Length: 90 Seconds (MP4 Container)
echo Single Instance Lock: ACTIVE
echo ===================================================
echo Press Ctrl+C in this window to stop recording at any time.
echo.

:: Run FFmpeg to capture H.264 stream into 90-second MP4 files
ffmpeg -i tcp://rcsharathpi.local:8888 -c copy -f segment -segment_time 90 -reset_timestamps 1 -strftime 1 "rec_%%Y%%m%%d_%%H%%M%%S.mp4"

:: Cleanup lock file on exit
del "%~dp0recorder.lock" 2>nul
pause
