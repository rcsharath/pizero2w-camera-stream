#!/usr/bin/env python3
"""
Lightweight Hybrid H.264 & HTTP Management Server for Raspberry Pi Zero 2W
Optimized for 0% CPU Hardware H.264 video encoding over TCP (port 8888),
with Outdoor Night Mode presets, interactive cropping, color tuning, system telemetry dashboard,
and zero-overhead structured JSON observability.
"""

import os
import sys
import time
import json
import uuid
import signal
import atexit
import datetime
import hashlib
import traceback
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

# Per-resolution fps ceilings.
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
current_rotation = "0"  # "0", "180", "hflip", "vflip"

# Global process & camera state (Reentrant lock prevents handler deadlocks)
camera_process = None
restart_requested = False
camera_lock = threading.RLock()

# Observability Globals
RUN_ID = uuid.uuid4().hex[:6]
seq_lock = threading.Lock()
current_seq = 0
current_gen = 0
current_cause_seq = None
last_launched_argv = []
exit_timestamps = []
gen_start_time = None
camera_thread = None
reconciler_thread = None


def reserve_seq() -> int:
    """Reserves sequence number atomically for start of async operations."""
    global current_seq
    with seq_lock:
        current_seq += 1
        return current_seq


def emit(ev: str, lvl: str = "info", cause: int = None, gen: int = None, seq: int = None, **kwargs) -> int:
    """
    Emits a single-line JSON structured event to stdout.
    Exception-safe with fallback handling. Writes atomically under seq_lock to preserve line ordering.
    """
    global current_seq
    with seq_lock:
        if seq is None:
            current_seq += 1
            seq_num = current_seq
        else:
            seq_num = seq

        now = datetime.datetime.now(datetime.timezone.utc)
        ts_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        mono_val = round(time.monotonic(), 3)

        payload = {
            "ts": ts_str,
            "mono": mono_val,
            "run": RUN_ID,
            "seq": seq_num,
            "lvl": lvl,
            "ev": ev
        }

        if cause is not None:
            payload["cause"] = cause
        if gen is not None:
            payload["gen"] = gen

        payload.update(kwargs)

        try:
            json_str = json.dumps(payload, separators=(',', ':'), default=str)
        except Exception as e:
            json_str = json.dumps({
                "ts": ts_str, "mono": mono_val, "run": RUN_ID, "seq": seq_num,
                "lvl": "error", "ev": "log.emit_failed", "orig_ev": ev, "error": str(e)
            }, separators=(',', ':'))

        sys.stdout.write(json_str + "\n")
        sys.stdout.flush()
        return seq_num


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
        with camera_lock:
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
    path = get_state_file_path()
    try:
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
        emit("state.save_failed", lvl="error", path=path, error=str(e))


def request_restart(cause_seq=None, reason="config_change"):
    global restart_requested, current_cause_seq
    restart_requested = True
    if cause_seq is not None:
        current_cause_seq = cause_seq
    emit("camera.restart_requested", lvl="info", cause=cause_seq, gen=current_gen, reason=reason)


def apply_change(field_name: str, new_value, cause_seq: int = None) -> bool:
    """
    Applies state change under camera_lock, emits state.changed or state.unchanged,
    and saves state to disk. Caller is responsible for triggering request_restart().
    Returns True if changed, False otherwise.
    """
    global current_res_key, current_width, current_height, current_fps
    global current_mode, current_awb, current_red_gain, current_blue_gain, current_roi, current_rotation

    with camera_lock:
        if field_name == "resolution":
            old_val = current_res_key
        else:
            old_val = globals().get(f"current_{field_name}")

        if old_val == new_value:
            emit("state.unchanged", lvl="info", cause=cause_seq, field=field_name, value=new_value)
            return False

        if field_name == "resolution":
            current_res_key = new_value
            current_width, current_height, _ = RESOLUTIONS[new_value]
            cap = FPS_LIMITS.get(current_res_key, 30)
            if current_fps > cap:
                current_fps = cap
        elif field_name == "fps":
            current_fps = new_value
        elif field_name == "mode":
            current_mode = new_value
        elif field_name == "awb":
            current_awb = new_value
        elif field_name == "red_gain":
            current_red_gain = new_value
        elif field_name == "blue_gain":
            current_blue_gain = new_value
        elif field_name == "roi":
            current_roi = new_value
        elif field_name == "rotation":
            current_rotation = new_value

        save_state()
        emit("state.changed", lvl="info", cause=cause_seq, field=field_name, **{"from": old_val, "to": new_value})
        return True


def load_state():
    global current_res_key, current_width, current_height, current_fps
    global current_mode, current_awb, current_red_gain, current_blue_gain, current_roi, current_rotation

    path = get_state_file_path()
    if not os.path.exists(path):
        emit("state.loaded", lvl="info", path=path, accepted=[], rejected=["absent"])
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        emit("state.loaded", lvl="warn", path=path, error=str(e))
        return

    if not isinstance(data, dict):
        emit("state.loaded", lvl="warn", path=path, error="Not a JSON object")
        return

    accepted = []
    rejected = []

    if "resolution" in data:
        try:
            res_val = validate_res(data["resolution"])
            current_res_key = res_val
            current_width, current_height, _ = RESOLUTIONS[current_res_key]
            accepted.append("resolution")
        except ValueError as e:
            rejected.append("resolution")
            emit("state.key_rejected", lvl="warn", key="resolution", value=data.get("resolution"), reason=str(e))

    if "fps" in data:
        try:
            current_fps = validate_fps(data["fps"], current_res_key)
            accepted.append("fps")
        except ValueError as e:
            rejected.append("fps")
            emit("state.key_rejected", lvl="warn", key="fps", value=data.get("fps"), reason=str(e))

    if "mode" in data:
        try:
            current_mode = validate_mode(data["mode"])
            accepted.append("mode")
        except ValueError as e:
            rejected.append("mode")
            emit("state.key_rejected", lvl="warn", key="mode", value=data.get("mode"), reason=str(e))

    if "awb" in data:
        try:
            current_awb = validate_awb(data["awb"])
            accepted.append("awb")
        except ValueError as e:
            rejected.append("awb")
            emit("state.key_rejected", lvl="warn", key="awb", value=data.get("awb"), reason=str(e))

    if "red_gain" in data:
        try:
            current_red_gain = clamp_gain(data["red_gain"], "red_gain")
            accepted.append("red_gain")
        except ValueError as e:
            rejected.append("red_gain")
            emit("state.key_rejected", lvl="warn", key="red_gain", value=data.get("red_gain"), reason=str(e))

    if "blue_gain" in data:
        try:
            current_blue_gain = clamp_gain(data["blue_gain"], "blue_gain")
            accepted.append("blue_gain")
        except ValueError as e:
            rejected.append("blue_gain")
            emit("state.key_rejected", lvl="warn", key="blue_gain", value=data.get("blue_gain"), reason=str(e))

    if "roi" in data:
        val = data["roi"]
        if val is None:
            current_roi = None
            accepted.append("roi")
        elif isinstance(val, str):
            parts = val.split(",")
            if len(parts) == 4:
                try:
                    current_roi = validate_roi(parts[0], parts[1], parts[2], parts[3])
                    accepted.append("roi")
                except ValueError as e:
                    rejected.append("roi")
                    emit("state.key_rejected", lvl="warn", key="roi", value=val, reason=str(e))
            else:
                rejected.append("roi")
                emit("state.key_rejected", lvl="warn", key="roi", value=val, reason="Does not have 4 parts")
        else:
            rejected.append("roi")
            emit("state.key_rejected", lvl="warn", key="roi", value=val, reason="Value is not string or null")

    if "rotation" in data:
        try:
            current_rotation = validate_rotation(data["rotation"])
            accepted.append("rotation")
        except ValueError as e:
            rejected.append("rotation")
            emit("state.key_rejected", lvl="warn", key="rotation", value=data.get("rotation"), reason=str(e))

    emit("state.loaded", lvl="info", path=path, accepted=accepted, rejected=rejected)


load_state()


def get_throttled_flags():
    """Queries vcgencmd get_throttled and decodes bitwise flags. Called only by reconciler thread."""
    try:
        if not os.path.exists('/usr/bin/vcgencmd') and not os.path.exists('/bin/vcgencmd'):
            return 0, []
        out = subprocess.check_output(["vcgencmd", "get_throttled"]).decode('utf-8').strip()
        val = int(out.split('=')[1], 16)
        flags = []
        if val & 0x1: flags.append("under_voltage_now")
        if val & 0x2: flags.append("freq_capped_now")
        if val & 0x4: flags.append("throttled_now")
        if val & 0x10000: flags.append("under_voltage_since_boot")
        if val & 0x20000: flags.append("freq_capped_since_boot")
        if val & 0x40000: flags.append("throttled_since_boot")
        return val, flags
    except Exception as e:
        emit("system.probe_failed", lvl="warn", source="vcgencmd", error=str(e))
        return 0, []


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
    except Exception as e:
        emit("system.probe_failed", lvl="warn", source="get_system_stats", error=str(e))
    return stats


def drain_stderr(proc, gen: int, pid: int):
    """
    Reads stderr line-by-line in a dedicated daemon thread to prevent pipe buffer deadlocks.
    Emits camera.stderr events with a burst rate cap (20 lines max, then 1 line / 5s).
    """
    line_count = 0
    last_emitted_time = 0.0
    suppressed_count = 0

    if not proc.stderr:
        return

    for line in iter(proc.stderr.readline, b''):
        if not line:
            break
        text = line.decode('utf-8', errors='replace').strip()
        if not text:
            continue

        now = time.monotonic()
        line_count += 1

        if line_count <= 20 or (now - last_emitted_time) >= 5.0:
            if suppressed_count > 0:
                emit("log.suppressed", lvl="warn", gen=gen, count=suppressed_count)
                suppressed_count = 0
            emit("camera.stderr", lvl="warn", gen=gen, pid=pid, line=text)
            last_emitted_time = now
        else:
            suppressed_count += 1

    if suppressed_count > 0:
        emit("log.suppressed", lvl="warn", gen=gen, count=suppressed_count)


def camera_worker():
    global camera_process, restart_requested, current_gen, last_launched_argv, current_cause_seq, exit_timestamps, gen_start_time

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
            cause_for_launch = current_cause_seq

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

        with camera_lock:
            current_gen += 1
            gen = current_gen
            last_launched_argv = list(cmd)

        emit("camera.launch_requested", lvl="info", cause=cause_for_launch, gen=gen, argv=cmd)

        try:
            with camera_lock:
                camera_process = subprocess.Popen(cmd, stderr=subprocess.PIPE)
                pid = camera_process.pid
                gen_start_time = time.monotonic()
                start_time = gen_start_time
        except Exception as e:
            emit("camera.launch_failed", lvl="error", cause=cause_for_launch, gen=gen, error=str(e))
            time.sleep(5)
            continue

        stderr_thread = threading.Thread(target=drain_stderr, args=(camera_process, gen, pid), daemon=True)
        stderr_thread.start()

        with camera_lock:
            config_snap = current_state_dict()

        emit("camera.launched", lvl="info", cause=cause_for_launch, gen=gen, pid=pid, argv=cmd, config_snapshot=config_snap)

        break_reason = None
        exit_cause = None

        while True:
            time.sleep(0.5)
            with camera_lock:
                if restart_requested:
                    break_reason = "restart_requested"
                    exit_cause = current_cause_seq
                    current_cause_seq = None
                    break
                if camera_process.poll() is not None:
                    break_reason = "process_died"
                    exit_cause = None
                    break

        with camera_lock:
            if camera_process and camera_process.poll() is None:
                camera_process.terminate()
                try:
                    camera_process.wait(timeout=2)
                except Exception:
                    pass

            return_code = camera_process.poll() if camera_process else None

        uptime = round(time.monotonic() - start_time, 1)
        exit_code = None
        signal_name = None

        if return_code is not None:
            if return_code < 0:
                try:
                    signal_name = signal.Signals(-return_code).name
                except ValueError:
                    signal_name = f"SIG_{abs(return_code)}"
            else:
                exit_code = return_code

        was_expected = (break_reason == "restart_requested")

        emit("camera.exited",
             lvl="info" if was_expected else "warn",
             cause=exit_cause,
             gen=gen,
             pid=pid,
             exit_code=exit_code,
             signal=signal_name,
             uptime_s=uptime,
             expected=was_expected)

        now_t = time.monotonic()
        exit_timestamps.append(now_t)
        exit_timestamps = [t for t in exit_timestamps if (now_t - t) <= 60.0]

        if len(exit_timestamps) >= 3:
            emit("camera.crash_loop", lvl="error", gen=gen, exits_in_window=len(exit_timestamps), window_s=60)

        if not was_expected:
            emit("health.unexplained", lvl="error", what="camera_exit", gen=gen, detail="no restart_requested in generation")
            time.sleep(2)

        time.sleep(1)


def check_port_listening(port: int) -> bool:
    """Scans /proc/net/tcp or /proc/net/tcp6 for hex port."""
    hex_port = f"{port:04X}"
    for proc_file in ['/proc/net/tcp', '/proc/net/tcp6']:
        if os.path.exists(proc_file):
            try:
                with open(proc_file, 'r') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 4 and parts[3] == '0A':
                            local_addr = parts[1]
                            if local_addr.endswith(':' + hex_port):
                                return True
            except Exception:
                pass
    return False


def reconciler_worker():
    tick_seq = 0
    state_file_path = get_state_file_path()
    last_state_hash = None

    while True:
        time.sleep(10)
        tick_seq += 1

        with camera_lock:
            proc = camera_process
            gen = current_gen
            is_restart_req = restart_requested
            argv_believed = list(last_launched_argv)
            gen_start_t = gen_start_time

        camera_alive = (proc is not None and proc.poll() is None)
        port_listening = check_port_listening(STREAM_TCP_PORT)

        emit("health.tick", lvl="info", tick_seq=tick_seq, camera_alive=camera_alive, port_8888_listening=port_listening)

        # Emit system.sample telemetry every 10s to journald
        stats = get_system_stats()
        throttled_val, flags = get_throttled_flags()
        emit("system.sample", lvl="info", temp_c=stats['temp'], ram_free_mb=stats['ram_free_mb'], load1=stats['cpu_load'], throttled=throttled_val, throttled_flags=flags)

        if camera_thread is not None and not camera_thread.is_alive():
            emit("health.drift", lvl="error", field="worker_thread", believed="alive", observed="dead", source="thread.is_alive")

        # Gated drift check (not restart_requested, camera_alive, generation_age > 5.0s)
        if not is_restart_req and camera_alive and gen_start_t is not None and (time.monotonic() - gen_start_t) > 5.0:
            try:
                cmdline_path = f"/proc/{proc.pid}/cmdline"
                if os.path.exists(cmdline_path):
                    with open(cmdline_path, "rb") as f:
                        raw_cmd = f.read().decode('utf-8', errors='replace').split('\x00')
                        raw_cmd = [x for x in raw_cmd if x]
                    if argv_believed and raw_cmd != argv_believed:
                        emit("health.drift", lvl="error", field="camera_argv", believed=argv_believed, observed=raw_cmd, source="/proc/cmdline")
            except Exception as e:
                emit("health.drift", lvl="warn", field="proc_check", error=str(e))

        if os.path.exists(state_file_path):
            try:
                with open(state_file_path, "rb") as f:
                    curr_hash = hashlib.sha256(f.read()).hexdigest()
                if last_state_hash is not None and curr_hash != last_state_hash:
                    emit("health.drift", lvl="info", field="state_file", believed="unmodified", observed="changed_on_disk", source="hash_check")
                last_state_hash = curr_hash
            except Exception:
                pass


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
        parsed = urlparse(self.path)
        req_seq = None
        start_t = time.monotonic()

        # Reserve seq for logged endpoints (excluding high-frequency /stats and /config polling)
        if parsed.path not in ('/stats', '/config'):
            req_seq = reserve_seq()

        try:
            if parsed.path == '/' or parsed.path == '/index.html':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(HTML_BYTES)))
                self.end_headers()
                if not is_head:
                    self.wfile.write(HTML_BYTES)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

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
                try:
                    res = validate_res(res_val)
                    if apply_change("resolution", res, cause_seq=req_seq):
                        request_restart(cause_seq=req_seq, reason="state_changed:resolution")
                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="res", value=res_val, reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/set_fps':
                query = parse_qs(parsed.query)
                fps_raw = query.get('fps', [''])[0]
                try:
                    fps_val = validate_fps(fps_raw)
                    if apply_change("fps", fps_val, cause_seq=req_seq):
                        request_restart(cause_seq=req_seq, reason="state_changed:fps")
                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="fps", value=fps_raw, reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/set_mode':
                query = parse_qs(parsed.query)
                mode_raw = query.get('mode', [''])[0]
                try:
                    mode_val = validate_mode(mode_raw)
                    if apply_change("mode", mode_val, cause_seq=req_seq):
                        request_restart(cause_seq=req_seq, reason="state_changed:mode")
                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="mode", value=mode_raw, reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/set_crop':
                query = parse_qs(parsed.query)
                try:
                    changed = False
                    if 'reset' in query:
                        changed = apply_change("roi", None, cause_seq=req_seq)
                    elif 'x' in query and 'y' in query and 'w' in query and 'h' in query:
                        roi_str = validate_roi(query['x'][0], query['y'][0], query['w'][0], query['h'][0])
                        changed = apply_change("roi", roi_str, cause_seq=req_seq)
                    else:
                        raise ValueError("Missing ROI parameters x, y, w, h or reset")
                    if changed:
                        request_restart(cause_seq=req_seq, reason="state_changed:roi")
                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="crop", value=str(query), reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/set_awb':
                query = parse_qs(parsed.query)
                try:
                    c_awb = False
                    c_red = False
                    c_blue = False
                    if 'mode' in query:
                        c_awb = apply_change("awb", validate_awb(query['mode'][0]), cause_seq=req_seq)
                    if 'red' in query:
                        c_red = apply_change("red_gain", clamp_gain(query['red'][0], 'red'), cause_seq=req_seq)
                    if 'blue' in query:
                        c_blue = apply_change("blue_gain", clamp_gain(query['blue'][0], 'blue'), cause_seq=req_seq)

                    # Single restart event for single or multi-field AWB changes
                    if c_awb or c_red or c_blue:
                        request_restart(cause_seq=req_seq, reason="state_changed:awb")

                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="awb", value=str(query), reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/set_rotation':
                query = parse_qs(parsed.query)
                rot_raw = query.get('rot', [''])[0]
                try:
                    rot_val = validate_rotation(rot_raw)
                    if apply_change("rotation", rot_val, cause_seq=req_seq):
                        request_restart(cause_seq=req_seq, reason="state_changed:rotation")
                    with camera_lock:
                        state = current_state_dict()
                except ValueError as e:
                    emit("http.rejected", lvl="warn", cause=req_seq, path=parsed.path, param="rot", value=rot_raw, reason=str(e))
                    self._send_error(str(e), status=400, is_head=is_head)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=400, dur_ms=dur_ms)
                    return
                self._send_json(state, status=200, is_head=is_head)
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)

            elif parsed.path == '/snapshot.jpg':
                with camera_lock:
                    rot_str = str(current_rotation)
                snap_seq = emit("snapshot.requested", lvl="info", cause=req_seq, rotation=rot_str)
                snap_t = time.monotonic()
                try:
                    cmd = ["rpicam-still", "--immediate", "--width", "2592", "--height", "1944", "-o", "-"]
                    if rot_str == "180":
                        cmd.extend(["--rotation", "180"])
                    elif rot_str == "hflip":
                        cmd.extend(["--hflip"])
                    elif rot_str == "vflip":
                        cmd.extend(["--vflip"])

                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout_data, stderr_data = proc.communicate()
                    snap_dur = int((time.monotonic() - snap_t) * 1000)

                    if proc.returncode == 0:
                        emit("snapshot.ok", lvl="info", cause=snap_seq, bytes=len(stdout_data), dur_ms=snap_dur)
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', str(len(stdout_data)))
                        self.end_headers()
                        if not is_head:
                            self.wfile.write(stdout_data)
                        dur_ms = int((time.monotonic() - start_t) * 1000)
                        emit("http.request", lvl="info", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=200, dur_ms=dur_ms)
                    else:
                        err_text = stderr_data.decode('utf-8', errors='replace')[:2048]
                        emit("snapshot.failed", lvl="error", cause=snap_seq, returncode=proc.returncode, stderr=err_text, dur_ms=snap_dur)
                        err_msg = f"Snapshot failed: {err_text}".encode('utf-8')
                        self.send_response(500)
                        self.send_header('Content-Type', 'text/plain')
                        self.send_header('Content-Length', str(len(err_msg)))
                        self.end_headers()
                        if not is_head:
                            self.wfile.write(err_msg)
                        dur_ms = int((time.monotonic() - start_t) * 1000)
                        emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=500, dur_ms=dur_ms)
                except Exception as e:
                    snap_dur = int((time.monotonic() - snap_t) * 1000)
                    emit("snapshot.failed", lvl="error", cause=snap_seq, returncode=-1, stderr=str(e), dur_ms=snap_dur)
                    err_msg = f"Snapshot failed: {e}".encode('utf-8')
                    self.send_response(500)
                    self.send_header('Content-Type', 'text/plain')
                    self.send_header('Content-Length', str(len(err_msg)))
                    self.end_headers()
                    if not is_head:
                        self.wfile.write(err_msg)
                    dur_ms = int((time.monotonic() - start_t) * 1000)
                    emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=500, dur_ms=dur_ms)

            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', '14')
                self.end_headers()
                if not is_head:
                    self.wfile.write(b"Page Not Found")
                dur_ms = int((time.monotonic() - start_t) * 1000)
                emit("http.request", lvl="warn", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=404, dur_ms=dur_ms)
        except Exception as e:
            dur_ms = int((time.monotonic() - start_t) * 1000)
            if req_seq is not None:
                emit("http.request", lvl="error", seq=req_seq, method=self.command, path=parsed.path, query=parsed.query, client_ip=self.client_address[0], status=500, dur_ms=dur_ms, error=str(e))
            raise


def handle_uncaught_exception(exc_type, exc_value, exc_tb):
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    emit("server.crashed", lvl="error", exc_type=exc_type.__name__, exc_msg=str(exc_value), traceback=tb_str, thread="main")
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def handle_thread_exception(args):
    tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    emit("server.crashed", lvl="error", exc_type=args.exc_type.__name__, exc_msg=str(args.exc_value), traceback=tb_str, thread=args.thread.name)


def on_exit():
    emit("server.stopping", lvl="info", reason="atexit")


def setup_crash_hooks():
    sys.excepthook = handle_uncaught_exception
    if hasattr(threading, 'excepthook'):
        threading.excepthook = handle_thread_exception

    atexit.register(on_exit)

    def shutdown_handler(signum, frame):
        try:
            sig_name = signal.Signals(signum).name
        except ValueError:
            sig_name = str(signum)
        emit("server.stopping", lvl="info", signal=sig_name, reason="signal_received")
        atexit.unregister(on_exit)
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)
    except (ValueError, AttributeError):
        pass


def main():
    global camera_thread, reconciler_thread
    setup_crash_hooks()

    camera_thread = threading.Thread(target=camera_worker, daemon=True, name="CameraWorker")
    camera_thread.start()

    reconciler_thread = threading.Thread(target=reconciler_worker, daemon=True, name="ReconcilerWorker")
    reconciler_thread.start()

    emit("server.started", lvl="info", pid=os.getpid(), py_version=sys.version.split()[0], boot_id=RUN_ID, config_snapshot=current_state_dict())

    server = ThreadedHTTPServer(('0.0.0.0', PORT), StreamHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        emit("server.stopping", lvl="info", reason="keyboard_interrupt")
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
