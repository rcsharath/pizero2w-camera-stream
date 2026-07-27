@echo off
TITLE Raspberry Pi Zero 2W - Continuous H.264 Stream Recorder
cd /d "%~dp0"

echo ===================================================
echo   Raspberry Pi Zero 2W H.264 Stream Recorder
echo ===================================================
echo Output Folder: %~dp0
echo Stream Source: tcp://rcsharathpi.local:8888 (Hardware H.264)
echo Segment Length: 90 Seconds (MP4 Container)
echo ===================================================
echo Press Ctrl+C in this window to stop recording at any time.
echo.

ffmpeg -i tcp://rcsharathpi.local:8888 -c copy -f segment -segment_time 90 -reset_timestamps 1 -strftime 1 "rec_%%Y%%m%%d_%%H%%M%%S.mp4"

pause
