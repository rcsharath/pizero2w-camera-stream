# Raspberry Pi Zero 2W Camera Live View Stream Server 🎥

A lightweight, zero-dependency HTTP MJPEG streaming server and web dashboard designed specifically for the **Raspberry Pi Zero 2W** (OV5647 5MP Camera).

Offloads video scaling and frame encoding directly to the **Raspberry Pi VideoCore GPU hardware pipeline**, maintaining rock-solid 24/7 stream consistency with **0% added CPU overhead** during resolution switches.

---

## Key Features ✨

- **Zero-Dependency Python HTTP Server:** Runs on standard Python 3 standard library (`http.server` & `socketserver`).
- **GPU Hardware Resolution Switcher:** Supports dynamic GPU mode switching between:
  - `640×480 @ 30 FPS` *(Smooth Motion - Recommended)*
  - `1280×720 @ 30 FPS` *(Smooth HD)*
  - `640×480 @ 15 FPS` *(Balanced)*
  - `1280×720 @ 15 FPS` *(HD Detail)*
  - `320×240 @ 30 FPS` *(Ultra-Low Latency)*
  - `1920×1080 @ 10 FPS` *(Full HD Stills)*
- **Ultra-Low Memory Footprint:** Consumes only **~14.7 MB RAM** for Python and **~18 MB RAM** for the camera driver (~31 MB total), leaving >290 MB free RAM on Pi Zero 2W.
- **Responsive Dark-Mode Dashboard:** Browser UI with live video stream, snapshot capture button, and resolution switcher.
- **Autostart Systemd Service:** User-level `systemd` service (`camerastream.service`) for automatic launch on boot without root privileges.

---

## Quick Start 🚀

### 1. Clone & Run
```bash
git clone https://github.com/rcsharath/pizero2w-camera-stream.git
cd pizero2w-camera-stream
python3 stream_server.py
```

### 2. Access Web Dashboard
Open `http://<pi-ip>:8000` or `http://rcsharathpi.local:8000` in any web browser.

### 3. API Endpoints
- **Stream URL:** `http://<pi-ip>:8000/stream.mjpg`
- **Snapshot URL:** `http://<pi-ip>:8000/snapshot.jpg`
- **Set Resolution API:** `curl "http://<pi-ip>:8000/set_resolution?res=640x480_30"`

---

## Systemd Autostart Setup ⚙️

Copy `camerastream.service` to `~/.config/systemd/user/`:

```bash
mkdir -p ~/.config/systemd/user
cp camerastream.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now camerastream.service
```

---

## License 📜

MIT License. Free to use and customize!
