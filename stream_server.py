#!/usr/bin/env python3
"""
Lightweight HTTP MJPEG Streaming Server for Raspberry Pi Zero 2W
Optimized for consistency, zero CPU overhead resolution adjustments, interactive cropping, and AWB color balance tuning.
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
    "1296x972_15": (1296, 972, 15, 45, "1296×972 @ 15 FPS (Full Frame Native)"),
    "2592x1944_10": (2592, 1944, 10, 40, "2592×1944 @ 10 FPS (Full Frame Max 5MP)"),
    "320x240_30": (320, 240, 30, 60, "320×240 @ 30 FPS (Ultra-Low Latency)"),
    "1920x1080_10": (1920, 1080, 10, 40, "1920×1080 @ 10 FPS (Full HD)")
}

current_res_key = "1296x972_15"
current_width, current_height, current_fps, current_quality, _ = RESOLUTIONS[current_res_key]

# Hardware ROI & AWB Color Tuning State (0% CPU Overhead)
current_roi = None  # None or string "x,y,w,h" (normalized 0.0 to 1.0)
current_awb = "auto"  # auto, indoor, incandescent, tungsten, custom
current_red_gain = 1.70
current_blue_gain = 1.40

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
            roi = current_roi
            awb = current_awb
            rgain = current_red_gain
            bgain = current_blue_gain
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

        if awb == "custom":
            cmd.extend(["--awbgains", f"{rgain:.2f},{bgain:.2f}"])
        elif awb and awb != "auto":
            cmd.extend(["--awb", awb])

        if roi:
            cmd.extend(["--roi", roi])

        try:
            print(f"[Camera] Launching GPU hardware encoder ({w}x{h} @ {fps}fps, awb={awb}, roi={roi})...")
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
                    print("[Camera] Camera stdout closed. Retrying in 5s...")
                    time.sleep(5)
                    break
                buf.extend(chunk)

                # Safety cap: prevent infinite memory growth and CPU thrashing if non-JPEG data
                if len(buf) > 2000000:
                    print("[Camera Warning] Buffer exceeded 2MB without JPEG EOF. Clearing buffer.")
                    buf.clear()

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
            time.sleep(5)

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
            --warning-color: #f59e0b;
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
            max-width: 850px;
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
            max-width: 850px;
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
            overflow: hidden;
        }

        .video-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }

        /* Interactive Crop Box Overlay (0% Pi Overhead) */
        #cropBox {
            position: absolute;
            border: 2px dashed #38bdf8;
            background-color: rgba(56, 189, 248, 0.15);
            pointer-events: none;
            display: none;
            box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.4);
            z-index: 10;
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

        .tools-panel {
            width: 100%;
            max-width: 850px;
            margin-top: 1rem;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
        }

        .panel-box {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 1rem;
        }

        .panel-title {
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 0.75rem;
            color: var(--accent-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .slider-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            font-size: 0.85rem;
            margin-bottom: 0.75rem;
        }

        .slider-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.5rem;
        }

        .slider-row label {
            width: 90px;
            color: var(--text-muted);
        }

        .slider-row input[type=range] {
            flex: 1;
            accent-color: var(--accent-color);
        }

        .slider-row span {
            width: 45px;
            text-align: right;
            font-family: monospace;
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
            padding: 0.45rem 0.85rem;
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

        .btn-warning {
            background-color: var(--warning-color);
            color: #0f172a;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .btn-group {
            display: flex;
            gap: 0.5rem;
            margin-top: 0.5rem;
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
        <div class="header-actions">
            <button class="btn btn-secondary" onclick="rotateCamera()">🔄 Rotate 90° (<span id="rotLabel">0°</span>)</button>
            <a href="/snapshot.jpg" download="snapshot.jpg" class="btn">📷 Snapshot</a>
        </div>
    </header>

    <div class="main-card">
        <div class="video-container" id="videoContainer">
            <img src="/stream.mjpg" id="streamImg" alt="Live Stream Feed">
            <div id="cropBox"></div>
        </div>
        <div class="controls-bar">
            <div class="info-group">
                <label for="resSelect">Mode (Res & FPS):</label>
                <select id="resSelect" onchange="changeResolution(this.value)">
                    <option value="1296x972_15">1296×972 @ 15 FPS (Full Frame Native - Default)</option>
                    <option value="2592x1944_10">2592×1944 @ 10 FPS (Full Frame Max 5MP)</option>
                    <option value="1280x720_30">1280×720 @ 30 FPS (Smooth HD)</option>
                    <option value="1280x720_15">1280×720 @ 15 FPS (HD Detail)</option>
                    <option value="640x480_30">640×480 @ 30 FPS (Smooth Motion)</option>
                    <option value="640x480_15">640×480 @ 15 FPS (Balanced)</option>
                    <option value="320x240_30">320×240 @ 30 FPS (Ultra-Low Latency)</option>
                    <option value="1920x1080_10">1920×1080 @ 10 FPS (Full HD)</option>
                </select>
                <div class="info-item">Current: <span id="fpsDisplay">1296×972 @ 15 FPS</span></div>
            </div>
            <button class="btn btn-secondary" onclick="reloadStream()">🔄 Refresh</button>
        </div>
    </div>

    <!-- Advanced Zero-Overhead Controls -->
    <div class="tools-panel">
        <!-- Interactive Crop Preview (0% Pi Overhead) -->
        <div class="panel-box">
            <div class="panel-title">
                ✂️ Interactive Crop Preview
                <span style="font-weight: normal; font-size: 0.75rem; color: var(--text-muted);">0% Pi Overhead</span>
            </div>
            <div class="slider-group">
                <div class="slider-row">
                    <label>Left Offset:</label>
                    <input type="range" id="cropX" min="0" max="80" value="0" oninput="updateCropPreview()">
                    <span id="cropValX">0%</span>
                </div>
                <div class="slider-row">
                    <label>Top Offset:</label>
                    <input type="range" id="cropY" min="0" max="80" value="0" oninput="updateCropPreview()">
                    <span id="cropValY">0%</span>
                </div>
                <div class="slider-row">
                    <label>Width:</label>
                    <input type="range" id="cropW" min="20" max="100" value="100" oninput="updateCropPreview()">
                    <span id="cropValW">100%</span>
                </div>
                <div class="slider-row">
                    <label>Height:</label>
                    <input type="range" id="cropH" min="20" max="100" value="100" oninput="updateCropPreview()">
                    <span id="cropValH">100%</span>
                </div>
            </div>
            <div class="btn-group">
                <button class="btn" onclick="applyHardwareCrop()">✂️ Apply Hardware Crop</button>
                <button class="btn btn-secondary" onclick="resetCrop()">🔄 Reset Full Frame</button>
            </div>
        </div>

        <!-- Color Tuning / White Balance -->
        <div class="panel-box">
            <div class="panel-title">
                🎨 Color Balance Tuning
                <span style="font-weight: normal; font-size: 0.75rem; color: var(--text-muted);">Hardware ISP</span>
            </div>
            <div class="slider-group">
                <div class="slider-row">
                    <label for="awbSelect">AWB Mode:</label>
                    <select id="awbSelect" style="flex:1;" onchange="toggleAwbMode(this.value)">
                        <option value="auto">Auto (Default)</option>
                        <option value="indoor">Indoor (Warm Neutral)</option>
                        <option value="incandescent">Incandescent</option>
                        <option value="tungsten">Tungsten</option>
                        <option value="custom">Custom Red/Blue Gains</option>
                    </select>
                </div>
                <div id="customGainsGroup" style="display: none;">
                    <div class="slider-row" style="margin-top: 0.5rem;">
                        <label>Red Gain:</label>
                        <input type="range" id="redGain" min="1.0" max="3.0" step="0.05" value="1.70" oninput="updateGainLabels()">
                        <span id="redGainVal">1.70</span>
                    </div>
                    <div class="slider-row">
                        <label>Blue Gain:</label>
                        <input type="range" id="blueGain" min="1.0" max="3.0" step="0.05" value="1.40" oninput="updateGainLabels()">
                        <span id="blueGainVal">1.40</span>
                    </div>
                </div>
            </div>
            <div class="btn-group" style="margin-top: 1rem;">
                <button class="btn" onclick="applyColorBalance()">🎨 Apply Color Tuning</button>
            </div>
        </div>
    </div>

    <footer>
        Zero-Overhead GPU Hardware Encoder &bull; Raspberry Pi Zero 2W Stream Server
    </footer>

    <script>
        let currentRotation = parseInt(localStorage.getItem('stream_rotation') || '0', 10);

        function applyRotation() {
            const img = document.getElementById('streamImg');
            const rotLabel = document.getElementById('rotLabel');
            if (rotLabel) rotLabel.innerText = currentRotation + '°';
            
            if (currentRotation === 90 || currentRotation === 270) {
                img.style.transform = `rotate(${currentRotation}deg) scale(0.75)`;
            } else {
                img.style.transform = `rotate(${currentRotation}deg) scale(1)`;
            }
        }

        function rotateCamera() {
            currentRotation = (currentRotation + 90) % 360;
            localStorage.setItem('stream_rotation', currentRotation);
            applyRotation();
        }

        function changeResolution(val) {
            fetch('/set_resolution?res=' + val)
                .then(res => res.json())
                .then(data => {
                    document.getElementById('fpsDisplay').innerText = data.width + '×' + data.height + ' @ ' + data.fps + ' FPS';
                    setTimeout(reloadStream, 1500);
                });
        }

        /* Interactive Visual Crop Preview (100% Browser Client-Side) */
        function updateCropPreview() {
            const x = parseInt(document.getElementById('cropX').value);
            const y = parseInt(document.getElementById('cropY').value);
            const w = parseInt(document.getElementById('cropW').value);
            const h = parseInt(document.getElementById('cropH').value);

            document.getElementById('cropValX').innerText = x + '%';
            document.getElementById('cropValY').innerText = y + '%';
            document.getElementById('cropValW').innerText = w + '%';
            document.getElementById('cropValH').innerText = h + '%';

            const cropBox = document.getElementById('cropBox');
            if (x === 0 && y === 0 && w === 100 && h === 100) {
                cropBox.style.display = 'none';
            } else {
                cropBox.style.display = 'block';
                cropBox.style.left = x + '%';
                cropBox.style.top = y + '%';
                cropBox.style.width = Math.min(w, 100 - x) + '%';
                cropBox.style.height = Math.min(h, 100 - y) + '%';
            }
        }

        function applyHardwareCrop() {
            const x = (parseInt(document.getElementById('cropX').value) / 100.0).toFixed(2);
            const y = (parseInt(document.getElementById('cropY').value) / 100.0).toFixed(2);
            const w = (parseInt(document.getElementById('cropW').value) / 100.0).toFixed(2);
            const h = (parseInt(document.getElementById('cropH').value) / 100.0).toFixed(2);

            fetch(`/set_crop?x=${x}&y=${y}&w=${w}&h=${h}`)
                .then(res => res.json())
                .then(data => {
                    console.log('Crop applied:', data);
                    setTimeout(reloadStream, 1500);
                });
        }

        function resetCrop() {
            document.getElementById('cropX').value = 0;
            document.getElementById('cropY').value = 0;
            document.getElementById('cropW').value = 100;
            document.getElementById('cropH').value = 100;
            updateCropPreview();

            fetch('/set_crop?reset=1')
                .then(res => res.json())
                .then(data => {
                    console.log('Crop reset:', data);
                    setTimeout(reloadStream, 1500);
                });
        }

        /* Color Balance Controls */
        function toggleAwbMode(val) {
            const gainsGroup = document.getElementById('customGainsGroup');
            gainsGroup.style.display = (val === 'custom') ? 'block' : 'none';
        }

        function updateGainLabels() {
            document.getElementById('redGainVal').innerText = parseFloat(document.getElementById('redGain').value).toFixed(2);
            document.getElementById('blueGainVal').innerText = parseFloat(document.getElementById('blueGain').value).toFixed(2);
        }

        function applyColorBalance() {
            const mode = document.getElementById('awbSelect').value;
            const red = parseFloat(document.getElementById('redGain').value).toFixed(2);
            const blue = parseFloat(document.getElementById('blueGain').value).toFixed(2);

            fetch(`/set_awb?mode=${mode}&red=${red}&blue=${blue}`)
                .then(res => res.json())
                .then(data => {
                    console.log('Color balance updated:', data);
                    setTimeout(reloadStream, 1500);
                });
        }

        function reloadStream() {
            const img = document.getElementById('streamImg');
            img.src = '/stream.mjpg?t=' + new Date().getTime();
        }

        window.addEventListener('DOMContentLoaded', () => {
            applyRotation();
            updateCropPreview();
        });
    </script>
</body>
</html>
"""


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP Handler supporting zero-overhead resolution, crop, & color balance adjustment."""

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
            res = query.get('res', ['1296x972_15'])[0]

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

        elif parsed.path == '/set_crop':
            query = parse_qs(parsed.query)
            global current_roi

            if 'reset' in query:
                current_roi = None
                restart_requested = True
            elif 'x' in query and 'y' in query and 'w' in query and 'h' in query:
                x = query['x'][0]
                y = query['y'][0]
                w = query['w'][0]
                h = query['h'][0]
                current_roi = f"{x},{y},{w},{h}"
                restart_requested = True

            if restart_requested and camera_process:
                try:
                    camera_process.terminate()
                except Exception:
                    pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","roi":"{current_roi}"}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/set_awb':
            query = parse_qs(parsed.query)
            global current_awb, current_red_gain, current_blue_gain

            if 'mode' in query:
                current_awb = query['mode'][0]
            if 'red' in query:
                current_red_gain = float(query['red'][0])
            if 'blue' in query:
                current_blue_gain = float(query['blue'][0])

            restart_requested = True
            if camera_process:
                try:
                    camera_process.terminate()
                except Exception:
                    pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","awb":"{current_awb}","red":{current_red_gain},"blue":{current_blue_gain}}}'
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
