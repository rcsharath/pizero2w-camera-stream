#!/usr/bin/env python3
"""
Lightweight HTTP MJPEG Streaming Server for Raspberry Pi Zero 2W
Optimized for consistency, zero CPU overhead resolution adjustments, and low RAM consumption (~15MB).
"""

import os
import sys
import time
import subprocess
import threading
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

PORT = 8000

# Supported GPU hardware resolutions (Width, Height, FPS, Quality, Display Label)
RESOLUTIONS = {
    "640x480_15": (640, 480, 15, 50, "640×480 @ 15 FPS (Default - Balanced)"),
    "640x480_30": (640, 480, 30, 45, "640×480 @ 30 FPS (Smooth Motion)"),
    "1280x720_15": (1280, 720, 15, 45, "1280×720 @ 15 FPS (HD Detail)"),
    "1280x720_30": (1280, 720, 30, 40, "1280×720 @ 30 FPS (Smooth HD)"),
    "320x240_30": (320, 240, 30, 60, "320×240 @ 30 FPS (Ultra-Low Latency)"),
    "1920x1080_10": (1920, 1080, 10, 40, "1920×1080 @ 10 FPS (Full HD)")
}

current_res_key = "640x480_15"
current_width, current_height, current_fps, current_quality, _ = RESOLUTIONS[current_res_key]

# Global state for latest JPEG frame and lock
current_frame = None
frame_lock = threading.Lock()
frame_event = threading.Event()
camera_process = None
restart_requested = False


def camera_worker():
    """Background thread running rpicam-vid using VideoCore GPU hardware scaling."""
    global current_frame, camera_process, restart_requested
    
    while True:
        with frame_lock:
            w, h, fps, q = current_width, current_height, current_fps, current_quality
            restart_requested = False

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--codec", "mjpeg",
            "--width", str(w),
            "--height", str(h),
            "--framerate", str(fps),
            "-q", str(q),
            "--inline",
            "-o", "-",
            "--nopreview",
            "-v", "0"
        ]

        try:
            print(f"[Camera] Launching GPU hardware encoder ({w}x{h} @ {fps}fps, q={q})...")
            camera_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )

            buf = bytearray()
            while not restart_requested:
                chunk = camera_process.stdout.read(4096)
                if not chunk:
                    print("[Camera] Camera stdout closed.")
                    break
                buf.extend(chunk)

                # Find JPEG boundary tags
                start = buf.find(b'\xff\xd8')
                end = buf.find(b'\xff\xd9')

                if start != -1 and end != -1 and end > start:
                    jpeg = bytes(buf[start:end+2])
                    buf = buf[end+2:]

                    with frame_lock:
                        current_frame = jpeg
                    frame_event.set()
                    frame_event.clear()

        except Exception as e:
            print(f"[Camera Error] {e}")

        # Kill process if restarting or crashed
        if camera_process:
            try:
                camera_process.terminate()
                camera_process.wait(timeout=2)
            except Exception:
                pass

        time.sleep(1)  # Brief pause before restarting with new GPU mode


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pi Zero 2W Live Stream</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --text-muted: #94a3b8;
            --accent-color: #38bdf8;
            --success-color: #22c55e;
            --border-color: #334155;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-color);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 1.5rem;
        }

        header {
            width: 100%;
            max-width: 800px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .title-group {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        h1 {
            font-size: 1.5rem;
            font-weight: 600;
        }

        .badge {
            background-color: rgba(34, 197, 94, 0.15);
            color: var(--success-color);
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }

        .badge-dot {
            width: 6px;
            height: 6px;
            background-color: var(--success-color);
            border-radius: 50%;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.3; }
            100% { opacity: 1; }
        }

        .main-card {
            width: 100%;
            max-width: 800px;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        }

        .video-container {
            width: 100%;
            aspect-ratio: 4/3;
            background-color: #000;
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative;
        }

        .video-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }

        .controls-bar {
            padding: 1rem 1.25rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-top: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 0.75rem;
        }

        .info-group {
            display: flex;
            align-items: center;
            gap: 1rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            flex-wrap: wrap;
        }

        .info-item span {
            color: var(--text-color);
            font-weight: 500;
        }

        select {
            background-color: #0f172a;
            color: var(--text-color);
            border: 1px solid var(--border-color);
            padding: 0.4rem 0.6rem;
            border-radius: 6px;
            font-size: 0.85rem;
            cursor: pointer;
            outline: none;
        }

        select:focus {
            border-color: var(--accent-color);
        }

        .btn {
            background-color: var(--accent-color);
            color: #0f172a;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
        }

        .btn:hover {
            opacity: 0.9;
            transform: translateY(-1px);
        }

        .btn-secondary {
            background-color: #334155;
            color: var(--text-color);
        }

        footer {
            margin-top: 2rem;
            font-size: 0.8rem;
            color: var(--text-muted);
            text-align: center;
        }
    </style>
</head>
<body>
    <header>
        <div class="title-group">
            <h1>Pi Zero 2W Camera</h1>
            <div class="badge">
                <div class="badge-dot"></div>
                LIVE
            </div>
        </div>
        <a href="/snapshot.jpg" download="snapshot.jpg" class="btn">
            📷 Take Snapshot
        </a>
    </header>

    <div class="main-card">
        <div class="video-container">
            <img src="/stream.mjpg" id="streamImg" alt="Live Stream Feed">
        </div>
        <div class="controls-bar">
            <div class="info-group">
                <label for="resSelect">Mode (Res & FPS):</label>
                <select id="resSelect" onchange="changeResolution(this.value)">
                    <option value="640x480_15">640×480 @ 15 FPS (Default - Balanced)</option>
                    <option value="640x480_30">640×480 @ 30 FPS (Smooth Motion)</option>
                    <option value="1280x720_15">1280×720 @ 15 FPS (HD Detail)</option>
                    <option value="1280x720_30">1280×720 @ 30 FPS (Smooth HD)</option>
                    <option value="320x240_30">320×240 @ 30 FPS (Ultra-Low Latency)</option>
                    <option value="1920x1080_10">1920×1080 @ 10 FPS (Full HD)</option>
                </select>
                <div class="info-item">Current: <span id="fpsDisplay">640×480 @ 15 FPS</span></div>
            </div>
            <button class="btn btn-secondary" onclick="reloadStream()">🔄 Refresh Stream</button>
        </div>
    </div>

    <footer>
        Zero-Overhead GPU Hardware Encoder &bull; Raspberry Pi Zero 2W Stream Server
    </footer>

    <script>
        function changeResolution(val) {
            fetch('/set_resolution?res=' + val)
                .then(res => res.json())
                .then(data => {
                    console.log('Mode changed:', data);
                    document.getElementById('fpsDisplay').innerText = data.width + '×' + data.height + ' @ ' + data.fps + ' FPS';
                    setTimeout(reloadStream, 1500);
                });
        }

        function reloadStream() {
            const img = document.getElementById('streamImg');
            img.src = '/stream.mjpg?t=' + new Date().getTime();
        }
    </script>
</body>
</html>
"""


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP Handler supporting zero-overhead resolution & framerate adjustment."""

    def log_message(self, format, *args):
        return

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(HTML_PAGE.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif parsed.path == '/set_resolution':
            query = parse_qs(parsed.query)
            res = query.get('res', ['640x480_15'])[0]

            global current_res_key, current_width, current_height, current_fps, current_quality, restart_requested

            if res in RESOLUTIONS and res != current_res_key:
                current_res_key = res
                current_width, current_height, current_fps, current_quality, _ = RESOLUTIONS[res]
                restart_requested = True
                if camera_process:
                    try:
                        camera_process.terminate()
                    except Exception:
                        pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","res":"{current_res_key}","width":{current_width},"height":{current_height},"fps":{current_fps}}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/snapshot.jpg':
            with frame_lock:
                frame = current_frame

            if frame:
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
            else:
                self.send_error(503, "Camera frame loading...")

        elif parsed.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()

            try:
                while True:
                    with frame_lock:
                        frame = current_frame
                        fps = current_fps

                    if frame:
                        header = f"--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
                        self.wfile.write(header.encode('utf-8'))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")

                    time.sleep(1.0 / fps)
            except (ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                pass
        else:
            self.send_error(404, "Page Not Found")


def main():
    t = threading.Thread(target=camera_worker, daemon=True)
    t.start()

    print("[Main] Initializing camera feed...")
    frame_event.wait(timeout=10)

    server = ThreadedHTTPServer(('0.0.0.0', PORT), StreamHandler)
    print(f"[Main] Stream Server running on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Main] Server shutting down...")
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
