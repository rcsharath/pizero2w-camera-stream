#!/usr/bin/env python3
"""
Lightweight Hybrid H.264 & HTTP Management Server for Raspberry Pi Zero 2W
Optimized for 0% CPU Hardware H.264 video encoding over TCP (port 8888),
with Outdoor Night Mode presets, interactive cropping, color tuning, and system telemetry dashboard.
"""

import os
import sys
import time
import json
import subprocess
import threading
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

PORT = 8000
STREAM_TCP_PORT = 8888

# Supported GPU hardware resolutions (Width, Height, FPS, Quality, Display Label)
RESOLUTIONS = {
    "1920x1080_15": (1920, 1080, 15, 45, "1920×1080 @ 15 FPS (Full HD Hardware H.264)"),
    "1296x972_15": (1296, 972, 15, 45, "1296×972 @ 15 FPS (Full Frame Native H.264)"),
    "2592x1944_10": (2592, 1944, 10, 40, "2592×1944 @ 10 FPS (Full Frame Max 5MP H.264)"),
    "1280x720_30": (1280, 720, 30, 40, "1280×720 @ 30 FPS (Smooth HD)"),
    "640x480_30": (640, 480, 30, 45, "640×480 @ 30 FPS (Smooth Motion)"),
    "320x240_30": (320, 240, 30, 60, "320×240 @ 30 FPS (Ultra-Low Latency)")
}

current_res_key = "1296x972_15"
current_width, current_height, current_fps, current_quality, _ = RESOLUTIONS[current_res_key]

# Hardware ROI, AWB & Mode State
current_roi = None  # None or string "x,y,w,h" (normalized 0.0 to 1.0)
current_mode = "day"  # day, night_outdoor, night_indoor
current_awb = "auto"  # auto, indoor, incandescent, tungsten, custom
current_red_gain = 1.70
current_blue_gain = 1.40

# Global state
camera_process = None
restart_requested = False
camera_lock = threading.Lock()


def get_system_stats():
    """Retrieve system telemetry directly from procfs with 0 external dependencies."""
    stats = {"temp": 0.0, "ram_free_mb": 0, "ram_total_mb": 416, "cpu_load": "0.00"}
    try:
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                stats['temp'] = round(float(f.read().strip()) / 1000.0, 1)

        if os.path.exists('/proc/meminfo'):
            mem = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].split()[0].strip()
                        mem[key] = int(val)
            if 'MemTotal' in mem and 'MemAvailable' in mem:
                stats['ram_total_mb'] = mem['MemTotal'] // 1024
                stats['ram_free_mb'] = mem['MemAvailable'] // 1024

        if os.path.exists('/proc/loadavg'):
            with open('/proc/loadavg', 'r') as f:
                stats['cpu_load'] = f.read().split()[0]
    except Exception:
        pass
    return stats


def camera_worker():
    """Background thread managing Hardware VideoCore H.264 GPU Stream Server."""
    global camera_process, restart_requested
    
    while True:
        with camera_lock:
            w, h, fps, q = current_width, current_height, current_fps, current_quality
            roi = current_roi
            mode = current_mode
            awb = current_awb
            rgain = current_red_gain
            bgain = current_blue_gain
            restart_requested = False

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--codec", "h264",
            "--width", str(w),
            "--height", str(h),
            "--framerate", str(fps),
            "--inline",
            "--listen",
            "-o", f"tcp://0.0.0.0:{STREAM_TCP_PORT}",
            "--nopreview",
            "-v", "0"
        ]

        # Mode Specific Presets (Outdoor Night vs Day)
        if mode == "night_outdoor":
            cmd.extend([
                "--shutter", "100000",   # 100ms long exposure
                "--gain", "6.0",         # High analog gain boost
                "--awbgains", "1.80,1.30"# Night outdoor white balance
            ])
        elif mode == "night_indoor":
            cmd.extend([
                "--shutter", "60000",    # 60ms exposure
                "--gain", "4.0",
                "--awbgains", "1.70,1.40"
            ])
        else:
            # Day / Standard Mode
            if awb == "custom":
                cmd.extend(["--awbgains", f"{rgain:.2f},{bgain:.2f}"])
            elif awb and awb != "auto":
                cmd.extend(["--awb", awb])

        if roi:
            cmd.extend(["--roi", roi])

        try:
            print(f"[Camera H.264 GPU] Launching TCP Server on port {STREAM_TCP_PORT} ({w}x{h} @ {fps}fps, mode={mode}, awb={awb})...")
            camera_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            while not restart_requested:
                if camera_process.poll() is not None:
                    print("[Camera] rpicam-vid process exited. Restarting in 3s...")
                    time.sleep(3)
                    break
                time.sleep(1)

        except Exception as e:
            print(f"[Camera Error] {e}")
            time.sleep(5)

        # Clean shutdown before restart
        if camera_process:
            try:
                camera_process.terminate()
                camera_process.wait(timeout=2)
            except Exception:
                pass

        time.sleep(1)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pi Zero 2W H.264 Control Dashboard</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --text-muted: #94a3b8;
            --accent-color: #38bdf8;
            --success-color: #22c55e;
            --warning-color: #f59e0b;
            --danger-color: #ef4444;
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
            flex-wrap: wrap;
            gap: 0.75rem;
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

        .stats-group {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            flex-wrap: wrap;
        }

        .stat-badge {
            background-color: #1e293b;
            border: 1px solid var(--border-color);
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 500;
            color: var(--text-muted);
            transition: all 0.3s ease;
        }

        .stat-badge span {
            color: var(--text-color);
            font-weight: 600;
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

        .stream-info-banner {
            background-color: #0f172a;
            padding: 1.25rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .banner-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 0.5rem;
        }

        .code-box {
            background-color: #1e293b;
            border: 1px solid var(--border-color);
            padding: 0.5rem 0.75rem;
            border-radius: 6px;
            font-family: monospace;
            font-size: 0.85rem;
            color: var(--accent-color);
            word-break: break-all;
        }

        .controls-bar {
            padding: 1rem 1.25rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
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
            width: 95px;
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

        .btn-night {
            background-color: #8b5cf6;
            color: #ffffff;
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
            <h1>Pi Zero 2W H.264 Camera</h1>
            <div class="badge">
                <div class="badge-dot"></div>
                GPU H.264 ACTIVE
            </div>
        </div>

        <!-- System Telemetry Badges (0% Overhead) -->
        <div class="stats-group">
            <div class="stat-badge" id="tempBadge">🌡️ <span id="tempVal">--</span>°C</div>
            <div class="stat-badge">🧠 <span id="ramVal">--</span> MB free</div>
            <div class="stat-badge">⚡ Load: <span id="loadVal">--</span></div>
        </div>

        <div class="header-actions">
            <a href="/snapshot.jpg" download="snapshot.jpg" class="btn">📷 Take 5MP Snapshot</a>
        </div>
    </header>

    <div class="main-card">
        <div class="stream-info-banner">
            <div class="banner-row">
                <strong style="color: var(--accent-color);">🚀 Hardware H.264 Video Engine Output (Port 8888)</strong>
                <span style="font-size: 0.75rem; color: var(--success-color);">Ice-Cold (~48°C / 2% CPU)</span>
            </div>
            <div class="code-box">
                VLC / OBS Stream URL: <strong>tcp/h264://rcsharathpi.local:8888</strong>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted);">
                Open <code>open_vlc_stream.bat</code> on your PC to view live, or run <code>record_stream.bat</code> to record 24/7 in MP4.
            </div>
        </div>

        <div class="controls-bar">
            <div class="info-group">
                <label for="resSelect">Resolution & FPS:</label>
                <select id="resSelect" onchange="changeResolution(this.value)">
                    <option value="1296x972_15">1296×972 @ 15 FPS (Full Frame Native H.264)</option>
                    <option value="1920x1080_15">1920×1080 @ 15 FPS (Full HD Hardware H.264)</option>
                    <option value="2592x1944_10">2592×1944 @ 10 FPS (Full Frame Max 5MP H.264)</option>
                    <option value="1280x720_30">1280×720 @ 30 FPS (Smooth HD)</option>
                    <option value="640x480_30">640×480 @ 30 FPS (Smooth Motion)</option>
                    <option value="320x240_30">320×240 @ 30 FPS (Ultra-Low Latency)</option>
                </select>
                <div class="info-item">Current Mode: <span id="modeDisplay" style="color:var(--accent-color); font-weight:600;">Day / Auto</span></div>
            </div>
        </div>
    </div>

    <!-- Advanced Controls Panel -->
    <div class="tools-panel">
        <!-- Day / Outdoor Night Mode Presets -->
        <div class="panel-box">
            <div class="panel-title">
                🌙 Exposure & Lighting Modes
                <span style="font-weight: normal; font-size: 0.75rem; color: var(--text-muted);">Hardware Shutter/Gain</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.75rem;">
                Select lighting preset for outdoor/night surveillance:
            </div>
            <div class="btn-group" style="flex-wrap: wrap;">
                <button class="btn" onclick="setLightingMode('day')">☀️ Day / Standard</button>
                <button class="btn btn-night" onclick="setLightingMode('night_outdoor')">🌙 Outdoor Night Mode (Long Exp)</button>
                <button class="btn btn-secondary" onclick="setLightingMode('night_indoor')">💡 Indoor Dim Mode</button>
            </div>
        </div>

        <!-- Color Balance Tuning -->
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
        Zero-Overhead GPU Hardware H.264 Encoder &bull; Raspberry Pi Zero 2W Stream Server
    </footer>

    <script>
        function changeResolution(val) {
            fetch('/set_resolution?res=' + val)
                .then(res => res.json())
                .then(data => {
                    console.log('Resolution set:', data);
                });
        }

        function setLightingMode(mode) {
            fetch('/set_mode?mode=' + mode)
                .then(res => res.json())
                .then(data => {
                    const label = (mode === 'night_outdoor') ? '🌙 Outdoor Night Mode' : (mode === 'night_indoor' ? '💡 Indoor Dim Mode' : '☀️ Day / Standard');
                    document.getElementById('modeDisplay').innerText = label;
                });
        }

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
                });
        }

        /* System Telemetry Polling (0% Overhead) */
        function pollStats() {
            fetch('/stats')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('tempVal').innerText = data.temp;
                    document.getElementById('ramVal').innerText = data.ram_free_mb;
                    document.getElementById('loadVal').innerText = data.cpu_load;

                    const tempBadge = document.getElementById('tempBadge');
                    if (data.temp > 78) {
                        tempBadge.style.borderColor = '#ef4444';
                        tempBadge.style.color = '#ef4444';
                    } else if (data.temp > 68) {
                        tempBadge.style.borderColor = '#f59e0b';
                        tempBadge.style.color = '#f59e0b';
                    } else {
                        tempBadge.style.borderColor = '#334155';
                        tempBadge.style.color = 'var(--text-muted)';
                    }
                }).catch(() => {});
        }

        window.addEventListener('DOMContentLoaded', () => {
            setInterval(pollStats, 3000);
            pollStats();
        });
    </script>
</body>
</html>
"""


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP Management Server supporting zero-overhead H.264 GPU stream control, telemetry & snapshots."""

    def log_message(self, format, *args):
        return

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        global current_res_key, current_width, current_height, current_fps, current_quality
        global current_roi, current_mode, current_awb, current_red_gain, current_blue_gain, restart_requested

        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(HTML_PAGE.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif parsed.path == '/stats':
            stats = get_system_stats()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, private')
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode('utf-8'))

        elif parsed.path == '/set_resolution':
            query = parse_qs(parsed.query)
            res = query.get('res', ['1296x972_15'])[0]

            if res in RESOLUTIONS and res != current_res_key:
                current_res_key = res
                current_width, current_height, current_fps, current_quality, _ = RESOLUTIONS[res]
                restart_requested = True

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","res":"{current_res_key}","width":{current_width},"height":{current_height},"fps":{current_fps}}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/set_mode':
            query = parse_qs(parsed.query)

            if 'mode' in query:
                current_mode = query['mode'][0]
                restart_requested = True

            if 'mode' in query:
                current_mode = query['mode'][0]
                restart_requested = True

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","mode":"{current_mode}"}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/set_crop':
            query = parse_qs(parsed.query)

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

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","roi":"{current_roi}"}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/set_awb':
            query = parse_qs(parsed.query)

            if 'mode' in query:
                current_awb = query['mode'][0]
            if 'red' in query:
                current_red_gain = float(query['red'][0])
            if 'blue' in query:
                current_blue_gain = float(query['blue'][0])

            restart_requested = True

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = f'{{"status":"ok","awb":"{current_awb}","red":{current_red_gain},"blue":{current_blue_gain}}}'
            self.wfile.write(resp.encode('utf-8'))

        elif parsed.path == '/snapshot.jpg':
            # Capture full 5MP still image using rpicam-still on demand
            try:
                cmd = ["rpicam-still", "--immediate", "--width", "2592", "--height", "1944", "-o", "-"]
                jpeg_data = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(jpeg_data)))
                self.end_headers()
                self.wfile.write(jpeg_data)
            except Exception as e:
                self.send_error(500, f"Snapshot failed: {e}")

        else:
            self.send_error(404, "Page Not Found")


def main():
    t = threading.Thread(target=camera_worker, daemon=True)
    t.start()

    print("[Main] Initializing H.264 GPU Stream Engine & Web Server...")

    server = ThreadedHTTPServer(('0.0.0.0', PORT), StreamHandler)
    print(f"[Main] Control Server running on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Main] Server shutting down...")
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
