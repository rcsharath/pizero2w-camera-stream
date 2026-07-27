# Raspberry Pi Zero 2W - Hardware H.264 Camera Stream & Edge Node 🤖

A lightweight, zero-dependency HTTP management server and VideoCore GPU Hardware H.264 video stream engine designed for the **Raspberry Pi Zero 2W** (OV5647 5MP Camera).

Offloads video encoding 100% to the **Raspberry Pi VideoCore GPU hardware pipeline**, running full-frame H.264 at **~2% CPU overhead** and maintaining cold operating temperatures (**~48°C**).

---

## Key Features ✨

- **Hardware H.264 Video Engine (Port 8888):** 100% GPU hardware encoding over TCP socket (`tcp://rcsharathpi.local:8888`). Consumes only **~2–5% CPU** on Pi Zero 2W.
- **Outdoor Night Mode Preset:** One-click exposure and gain adjustment for low-light outdoor surveillance (100ms exposure, 6.0x analog gain boost).
- **Zero-Dependency Control Dashboard (Port 8000):** Real-time web control panel for switching resolutions, color balance tuning, shutter/gain presets, and viewing system metrics telemetry (CPU Temp, RAM free, Load).
- **1-Click Windows Launchers:**
  - `open_vlc_stream.bat` — Launches low-latency live H.264 stream playback in VLC.
  - `record_stream.bat` — Continuous 24/7 recording into timestamped 90-second `.mp4` chunks (75% disk space savings vs MJPEG).
- **On-Demand 5MP Snapshot Engine (`/snapshot.jpg`):** Captures native 2592×1944 high-resolution stills on demand.
- **Autostart Systemd Service:** User-level `systemd` service (`camerastream.service`) for automatic launch on boot.

---

## Quick Start 🚀

### 1. Run Control Server on Pi
```bash
git clone https://github.com/rcsharath/pizero2w-camera-stream.git
cd pizero2w-camera-stream
python3 stream_server.py
```

### 2. Live Stream Viewing & Recording on Desktop
* **View Live in VLC:** Double-click `open_vlc_stream.bat` or open `tcp/h264://rcsharathpi.local:8888` in VLC.
* **Record Continuously:** Double-click `record_stream.bat` to save 90-second timestamped `.mp4` clips.
* **Web Dashboard:** Open `http://rcsharathpi.local:8000` in Chrome to control camera modes & view telemetry.

---

## API Endpoints ⚙️

- **System Telemetry API:** `http://rcsharathpi.local:8000/stats`
- **Lighting Mode API:** `http://rcsharathpi.local:8000/set_mode?mode=night_outdoor` (`day` | `night_outdoor` | `night_indoor`)
- **Snapshot Capture API:** `http://rcsharathpi.local:8000/snapshot.jpg`
- **Set Resolution API:** `http://rcsharathpi.local:8000/set_resolution?res=1296x972_15`
- **Color Balance Tuning API:** `http://rcsharathpi.local:8000/set_awb?mode=indoor`

---

## License 📜

MIT License. Free to use and customize!
