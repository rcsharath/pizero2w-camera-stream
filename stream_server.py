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

# Supported GPU hardware resolutions (Width, Height, Display Label)
RESOLUTIONS = {
    "1920x1080": (1920, 1080, "1920×1080 (Full HD 16:9)"),
    "1296x972": (1296, 972, "1296×972 (Full Frame Native 4:3)"),
    "1280x720": (1280, 720, "1280×720 (Smooth HD 16:9)"),
    "640x480": (640, 480, "640×480 (Smooth Motion 4:3)"),
    "320x240": (320, 240, "320×240 (Ultra-Low Latency 4:3)")
}

# Per-resolution fps ceilings. 640x480, 1296x972, and 1920x1080 come from the
# documented OV5647 sensor mode table in Hardware.md, floored to int. 1280x720
# and 320x240 are software-scaled outputs with no documented native fps; they
# keep the previous flat 30 cap until verified on real hardware. Do not raise
# 1280x720 or 320x240 without an on-device check first.
FPS_LIMITS = {
    "1920x1080": 32,
    "1296x972": 46,
    "1280x720": 30,
    "640x480": 58,
    "320x240": 30,
}

VALID_MODES = frozenset({"day", "night_indoor", "night_outdoor"})
VALID_AWB = frozenset({"auto", "indoor", "incandescent", "tungsten", "custom"})
VALID_ROTATIONS = frozenset({"0", "180", "hflip", "vflip"})

current_res_key = "1296x972"
current_width, current_height, _ = RESOLUTIONS[current_res_key]
current_fps = 15

# Hardware ROI, AWB & Mode State
current_roi = None  # None or string "x,y,w,h" (normalized 0.0 to 1.0)
current_mode = "day"  # day, night_outdoor, night_indoor
current_awb = "auto"  # auto, indoor, incandescent, tungsten, custom
current_red_gain = 1.70
current_blue_gain = 1.40
current_rotation = "0"  # "0", "180", "hflip", "vflip" (hardware ISP sensor transforms)

# Global state
camera_process = None
restart_requested = False
camera_lock = threading.Lock()


def validate_res(res_val):
    if res_val not in RESOLUTIONS:
        raise ValueError(f"Invalid res parameter '{res_val}'; must be one of {sorted(list(RESOLUTIONS.keys()))}")
    return res_val


def validate_fps(fps_val, res_key=None):
    try:
        val = int(fps_val)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid fps parameter '{fps_val}'; must be an integer")
    if res_key is None:
        res_key = current_res_key
    cap = FPS_LIMITS.get(res_key, 30)
    if not (5 <= val <= cap):
        raise ValueError(f"Invalid fps parameter '{fps_val}'; must be between 5 and {cap} for resolution '{res_key}'")
    return val


def validate_mode(mode_val):
    if mode_val not in VALID_MODES:
        raise ValueError(f"Invalid mode parameter '{mode_val}'; must be one of {sorted(list(VALID_MODES))}")
    return mode_val


def validate_awb(awb_val):
    if awb_val not in VALID_AWB:
        raise ValueError(f"Invalid awb parameter '{awb_val}'; must be one of {sorted(list(VALID_AWB))}")
    return awb_val


def validate_rotation(rot_val):
    rot_str = str(rot_val)
    if rot_str not in VALID_ROTATIONS:
        raise ValueError(f"Invalid rot parameter '{rot_val}'; must be one of {sorted(list(VALID_ROTATIONS))}")
    return rot_str


def clamp_gain(gain_val, param_name="gain"):
    try:
        val = float(gain_val)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {param_name} parameter '{gain_val}'; must be a number")
    return max(1.0, min(3.0, val))


def validate_roi(x_val, y_val, w_val, h_val):
    try:
        x = float(x_val)
        y = float(y_val)
        w = float(w_val)
        h = float(h_val)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid ROI parameters x='{x_val}', y='{y_val}', w='{w_val}', h='{h_val}'; all must be numbers")

    if x < -1e-9:
        raise ValueError(f"Invalid ROI x='{x_val}'; must be >= 0.0")
    if y < -1e-9:
        raise ValueError(f"Invalid ROI y='{y_val}'; must be >= 0.0")
    if not (0.05 <= w <= 1.0):
        raise ValueError(f"Invalid ROI w='{w_val}'; must be between 0.05 and 1.0")
    if not (0.05 <= h <= 1.0):
        raise ValueError(f"Invalid ROI h='{h_val}'; must be between 0.05 and 1.0")
    if x + w > 1.0 + 1e-9:
        raise ValueError(f"Invalid ROI x='{x_val}' and w='{w_val}'; x + w ({x+w}) exceeds 1.0")
    if y + h > 1.0 + 1e-9:
        raise ValueError(f"Invalid ROI y='{y_val}' and h='{h_val}'; y + h ({y+h}) exceeds 1.0")

    return f"{x},{y},{w},{h}"


def current_state_dict():
    resolutions_map = {k: v[2] for k, v in RESOLUTIONS.items()}
    return {
        "resolution": current_res_key,
        "width": current_width,
        "height": current_height,
        "fps": current_fps,
        "mode": current_mode,
        "awb": current_awb,
        "red_gain": current_red_gain,
        "blue_gain": current_blue_gain,
        "roi": current_roi,
        "rotation": current_rotation,
        "resolutions": resolutions_map,
        "fps_limits": dict(FPS_LIMITS),
        "modes": ["day", "night_indoor", "night_outdoor"],
        "awb_modes": ["auto", "indoor", "incandescent", "tungsten", "custom"],
        "rotations": ["0", "180", "hflip", "vflip"],
        "stream_port": STREAM_TCP_PORT
    }


def get_state_file_path():
    state_dir = os.environ.get("CAMERASTREAM_STATE_DIR", os.path.expanduser("~/.config/camerastream"))
    return os.path.join(state_dir, "state.json")


def save_state():
    try:
        path = get_state_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "resolution": current_res_key,
            "fps": current_fps,
            "mode": current_mode,
            "awb": current_awb,
            "red_gain": current_red_gain,
            "blue_gain": current_blue_gain,
            "roi": current_roi,
            "rotation": current_rotation,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[State] Error saving state: {e}")


def load_state():
    global current_res_key, current_width, current_height, current_fps
    global current_mode, current_awb, current_red_gain, current_blue_gain, current_roi, current_rotation

    path = get_state_file_path()
    if not os.path.exists(path):
        print(f"[State] State file absent at {path}; using default state")
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[State] Failed to read/parse state file at {path} ({e}); using default state")
        return

    if not isinstance(data, dict):
        print(f"[State] State file contents at {path} are not a JSON object; using default state")
        return

    if "resolution" in data:
        try:
            res_val = validate_res(data["resolution"])
            current_res_key = res_val
            current_width, current_height, _ = RESOLUTIONS[current_res_key]
        except ValueError as e:
            print(f"[State] Dropping invalid 'resolution' key: {e}")

    if "fps" in data:
        try:
            current_fps = validate_fps(data["fps"], current_res_key)
        except ValueError as e:
            print(f"[State] Dropping invalid 'fps' key: {e}")

    if "mode" in data:
        try:
            current_mode = validate_mode(data["mode"])
        except ValueError as e:
            print(f"[State] Dropping invalid 'mode' key: {e}")

    if "awb" in data:
        try:
            current_awb = validate_awb(data["awb"])
        except ValueError as e:
            print(f"[State] Dropping invalid 'awb' key: {e}")

    if "red_gain" in data:
        try:
            current_red_gain = clamp_gain(data["red_gain"], "red_gain")
        except ValueError as e:
            print(f"[State] Dropping invalid 'red_gain' key: {e}")

    if "blue_gain" in data:
        try:
            current_blue_gain = clamp_gain(data["blue_gain"], "blue_gain")
        except ValueError as e:
            print(f"[State] Dropping invalid 'blue_gain' key: {e}")

    if "roi" in data:
        val = data["roi"]
        if val is None:
            current_roi = None
        elif isinstance(val, str):
            parts = val.split(",")
            if len(parts) == 4:
                try:
                    current_roi = validate_roi(parts[0], parts[1], parts[2], parts[3])
                except ValueError as e:
                    print(f"[State] Dropping invalid 'roi' key: {e}")
            else:
                print(f"[State] Dropping invalid 'roi' key: string '{val}' does not have 4 parts")
        else:
            print(f"[State] Dropping invalid 'roi' key: value '{val}' is not string or null")

    if "rotation" in data:
        try:
            current_rotation = validate_rotation(data["rotation"])
        except ValueError as e:
            print(f"[State] Dropping invalid 'rotation' key: {e}")


load_state()


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
                        val = parts[1].strip().split()[0]
                        mem[key] = int(val)
            if 'MemAvailable' in mem:
                stats['ram_free_mb'] = mem['MemAvailable'] // 1024
            elif 'MemFree' in mem:
                stats['ram_free_mb'] = mem['MemFree'] // 1024

        if os.path.exists('/proc/loadavg'):
            with open('/proc/loadavg', 'r') as f:
                stats['cpu_load'] = f.read().split()[0]
    except Exception:
        pass
    return stats


def camera_worker():
    global camera_process, restart_requested

    while True:
        with camera_lock:
            res_w, res_h = current_width, current_height
            fps = current_fps
            roi = current_roi
            mode = current_mode
            awb = current_awb
            red_g = current_red_gain
            blue_g = current_blue_gain
            rot = current_rotation
            restart_requested = False

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--inline",
            "--width", str(res_w),
            "--height", str(res_h),
            "--framerate", str(fps),
            "--codec", "h264",
            "--listen",
            "-o", f"tcp://0.0.0.0:{STREAM_TCP_PORT}"
        ]

        if mode == "night_outdoor":
            # Long exposure skews the OV5647's auto white balance cold/blue; 1.80/1.30
            # (red-heavy) compensates. Night mode presets always win over the AWB
            # dropdown below, so a user-selected AWB mode never fights this.
            cmd.extend(["--shutter", "200000", "--gain", "8.0", "--awbgains", "1.80,1.30"])
        elif mode == "night_indoor":
            cmd.extend(["--shutter", "66000", "--gain", "4.0", "--awbgains", "1.70,1.40"])
        else:
            if awb == "custom":
                cmd.extend(["--awb", "custom", "--awbgains", f"{red_g},{blue_g}"])
            elif awb != "auto":
                cmd.extend(["--awb", awb])

        if roi:
            cmd.extend(["--roi", roi])

        if rot == "180":
            cmd.extend(["--rotation", "180"])
        elif rot == "hflip":
            cmd.extend(["--hflip"])
        elif rot == "vflip":
            cmd.extend(["--vflip"])

        print(f"[CameraWorker] Launching: {' '.join(cmd)}")

        try:
            with camera_lock:
                camera_process = subprocess.Popen(cmd)
        except Exception as e:
            print(f"[CameraWorker] Failed to launch camera: {e}")
            time.sleep(5)
            continue

        while True:
            time.sleep(0.5)
            with camera_lock:
                if restart_requested:
                    print("[CameraWorker] Restart requested, terminating process...")
                    break
                if camera_process.poll() is not None:
                    print("[CameraWorker] Process died unexpectedly, restarting in 2s...")
                    time.sleep(2)
                    break

        with camera_lock:
            if camera_process and camera_process.poll() is None:
                camera_process.terminate()
                try:
                    camera_process.wait(timeout=2)
                except Exception:
                    pass

        time.sleep(1)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'index.html')
if not os.path.exists(HTML_PATH):
    raise RuntimeError(f"Static HTML dashboard missing at: {HTML_PATH}")

with open(HTML_PATH, 'r', encoding='utf-8') as f:
    HTML_BYTES = f.read().encode('utf-8')


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP Management Server supporting zero-overhead H.264 GPU stream control, telemetry & snapshots."""

    def log_message(self, format, *args):
        return

    def _send_json(self, data, status=200, is_head=False):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, private')
        self.end_headers()
        if not is_head:
            self.wfile.write(body)

    def _send_error(self, reason, status=400, is_head=False):
        self._send_json({"error": reason}, status=status, is_head=is_head)

    def do_HEAD(self):
        self.handle_request(is_head=True)

    def do_GET(self):
        self.handle_request(is_head=False)

    def handle_request(self, is_head=False):
        """Dispatch HTTP GET and HEAD requests with atomic lock acquisition, validation, state mutation, state persistence, and response serialization."""
        global current_res_key, current_width, current_height, current_fps
        global current_roi, current_mode, current_awb, current_red_gain, current_blue_gain, current_rotation, restart_requested

        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(HTML_BYTES)))
            self.end_headers()
            if not is_head:
                self.wfile.write(HTML_BYTES)

        elif parsed.path == '/config':
            with camera_lock:
                state = current_state_dict()
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/stats':
            stats = get_system_stats()
            self._send_json(stats, status=200, is_head=is_head)

        elif parsed.path == '/set_resolution':
            query = parse_qs(parsed.query)
            res_val = query.get('res', [''])[0]
            with camera_lock:
                try:
                    res = validate_res(res_val)
                    if res != current_res_key:
                        current_res_key = res
                        current_width, current_height, _ = RESOLUTIONS[res]
                        cap = FPS_LIMITS.get(current_res_key, 30)
                        if current_fps > cap:
                            current_fps = cap
                        save_state()
                        restart_requested = True
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/set_fps':
            query = parse_qs(parsed.query)
            fps_raw = query.get('fps', [''])[0]
            with camera_lock:
                try:
                    fps_val = validate_fps(fps_raw, current_res_key)
                    if fps_val != current_fps:
                        current_fps = fps_val
                        save_state()
                        restart_requested = True
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/set_mode':
            query = parse_qs(parsed.query)
            mode_raw = query.get('mode', [''])[0]
            with camera_lock:
                try:
                    mode_val = validate_mode(mode_raw)
                    if mode_val != current_mode:
                        current_mode = mode_val
                        save_state()
                        restart_requested = True
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/set_crop':
            query = parse_qs(parsed.query)
            with camera_lock:
                try:
                    if 'reset' in query:
                        current_roi = None
                        save_state()
                        restart_requested = True
                    elif 'x' in query and 'y' in query and 'w' in query and 'h' in query:
                        roi_str = validate_roi(query['x'][0], query['y'][0], query['w'][0], query['h'][0])
                        current_roi = roi_str
                        save_state()
                        restart_requested = True
                    else:
                        raise ValueError("Missing ROI parameters x, y, w, h or reset")
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/set_awb':
            query = parse_qs(parsed.query)
            with camera_lock:
                try:
                    new_awb = current_awb
                    new_red = current_red_gain
                    new_blue = current_blue_gain

                    if 'mode' in query:
                        new_awb = validate_awb(query['mode'][0])
                    if 'red' in query:
                        new_red = clamp_gain(query['red'][0], 'red')
                    if 'blue' in query:
                        new_blue = clamp_gain(query['blue'][0], 'blue')

                    current_awb = new_awb
                    current_red_gain = new_red
                    current_blue_gain = new_blue
                    save_state()
                    restart_requested = True
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/set_rotation':
            query = parse_qs(parsed.query)
            rot_raw = query.get('rot', [''])[0]
            with camera_lock:
                try:
                    rot_val = validate_rotation(rot_raw)
                    if rot_val != current_rotation:
                        current_rotation = rot_val
                        save_state()
                        restart_requested = True
                    state = current_state_dict()
                except ValueError as e:
                    self._send_error(str(e), status=400, is_head=is_head)
                    return
            self._send_json(state, status=200, is_head=is_head)

        elif parsed.path == '/snapshot.jpg':
            with camera_lock:
                rot_str = str(current_rotation)
            try:
                cmd = ["rpicam-still", "--immediate", "--width", "2592", "--height", "1944", "-o", "-"]
                if rot_str == "180":
                    cmd.extend(["--rotation", "180"])
                elif rot_str == "hflip":
                    cmd.extend(["--hflip"])
                elif rot_str == "vflip":
                    cmd.extend(["--vflip"])
                jpeg_data = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(jpeg_data)))
                self.end_headers()
                if not is_head:
                    self.wfile.write(jpeg_data)
            except Exception as e:
                err_msg = f"Snapshot failed: {e}".encode('utf-8')
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(err_msg)))
                self.end_headers()
                if not is_head:
                    self.wfile.write(err_msg)

        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '14')
            self.end_headers()
            if not is_head:
                self.wfile.write(b"Page Not Found")


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
