@echo off
TITLE Raspberry Pi Zero 2W - Continuous Stream Recorder
cd /d "%~dp0"

echo ===================================================
echo   Raspberry Pi Zero 2W Stream Recorder
echo ===================================================
echo Output Folder: %~dp0
echo Stream Source: http://rcsharathpi.local:8000/stream.mjpg
echo Segment Length: 90 Seconds (No Auto-Deletion)
echo ===================================================
echo Press Ctrl+C in this window to stop recording at any time.
echo.

ffmpeg -use_wallclock_as_timestamps 1 -i http://rcsharathpi.local:8000/stream.mjpg -c:v copy -f segment -segment_time 90 -reset_timestamps 1 -strftime 1 "rec_%%Y%%m%%d_%%H%%M%%S.mkv"

pause
