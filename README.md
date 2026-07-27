# Raspberry Pi Zero 2W - Hardware H.264 Camera Stream & Edge Node 🤖

A lightweight, zero-dependency HTTP management server and VideoCore GPU Hardware H.264 video stream engine designed specifically for the **Raspberry Pi Zero 2W** (OV5647 5MP Camera).

Offloads video encoding 100% to the **Raspberry Pi VideoCore GPU hardware block**, maintaining rock-solid 24/7 stream consistency with **~2.7% CPU overhead** and low operating temperatures (**~48°C – 52°C**).

---

## 🌟 Key Features

- **🚀 Zero-Overhead Hardware H.264 Video Engine (Port 8888):** Hardware GPU stream server broadcasting over TCP (`tcp://rcsharathpi.local:8888`). Operates at **~2.7% CPU load** on Pi Zero 2W.
- **🌙 Outdoor Night Mode & Exposure Presets:** One-click exposure and gain tuning for low-light outdoor surveillance (100ms long shutter, 6.0x analog gain boost, low-light AWB).
- **✂️ Interactive Hardware Crop (ROI):** Dynamic hardware region-of-interest cropping (`--roi`) applied directly inside the VideoCore GPU ISP.
- **🎨 Color Balance & White Balance Tuning:** Hardware ISP AWB presets (Indoor, Incandescent, Tungsten) and custom Red/Blue gain multipliers (`--awbgains`).
- **📊 Real-Time System Telemetry:** Header metrics badges polling directly from procfs with zero overhead (`/stats` — CPU Temp °C, Free RAM MB, System Load).
- **🎬 1-Click Windows Desktop Launchers:**
  - `record_and_preview.bat` — **Primary Entry Point:** Simultaneously records H.264 video into timestamped `.mp4` chunks AND launches live low-latency preview in VLC via PC-side UDP relay with 0% extra Pi CPU.
  - `open_vlc_stream.bat` — Smart viewer script that connects to host UDP relay when recording, or falls back to direct Pi TCP stream when standalone.
  - `record_stream.bat` — Multi-output FFmpeg recorder saving 90-second `.mp4` segments while broadcasting local UDP loopback (`udp://127.0.0.1:8889`).
- **🔒 Single-Instance Protection & Auto-Cleanup:** Automated lockfile handling (`recorder.lock`) with active process checking (`ffmpeg.exe`) to prevent duplicate runs and auto-clear stale locks.
- **📷 On-Demand 5MP Snapshot Engine (`/snapshot.jpg`):** Captures native 2592×1944 high-resolution stills on demand without interrupting the stream.
- **⚙️ Autostart Systemd Service:** User-level `systemd` service (`camerastream.service`) for automatic boot launch without root privileges.

---

## 📊 Performance & Thermal Benchmarks

| Metric | Previous Software MJPEG | Hardware H.264 Engine | Improvement |
| :--- | :--- | :--- | :--- |
| **Pi CPU Usage** | **190%** (2 ARM cores maxed) | **~2.7%** | **~98.5% CPU Reduction** |
| **Core Temperature** | **81.6°C** (Thermal Throttling) | **~48°C – 52°C** | **~31°C Cooler** |
| **24-Hour Recording Size** | ~28.8 GB | **~6.5 GB** | **75% Disk Savings** |
| **Network Protocol** | HTTP MJPEG (`:8000`) | Hardware TCP H.264 (`:8888`) | Zero CPU encoding |

---

## 📐 System Architecture (PC-Side Stream Duplication & Preview)

```
   ┌─────────────────────────────────────────────────────────────┐
   │                   RASPBERRY PI ZERO 2W                      │
   │                                                             │
   │   1. HTTP Control Server (Port 8000)                        │
   │      - System Telemetry (/stats)                            │
   │      - Lighting Presets & Color Balance (/set_mode, /set_awb)│
   │      - Hardware ROI Cropping (/set_crop)                    │
   │      - On-demand 5MP Snapshots (/snapshot.jpg)              │
   │                                                             │
   │   2. Hardware H.264 Video Engine (Port 8888)                │
   │      - rpicam-vid --codec h264 (VideoCore GPU Hardware)     │
   │      - Consumes ~2.7% CPU / Temp: ~48°C                     │
   └──────────────┬──────────────────────────────┬───────────────┘
                  │                              │
                  │ (Single H.264 TCP Stream)    │ (HTTP Control API)
                  ▼                              ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                      WINDOWS DESKTOP PC                     │
   │                                                             │
   │  FFmpeg Multi-Output Stream Relay:                          │
   │  ├── Output 1: Segmented MP4 Files (rec_YYYYMMDD_HHMMSS.mp4)│
   │  └── Output 2: UDP Loopback Stream (udp://127.0.0.1:8889)   │
   │                                                             │
   │  Live Viewers & Controls:                                  │
   │  ├── VLC Media Player (Previewing udp://@127.0.0.1:8889)    │
   │  └── Web Dashboard (http://rcsharathpi.local:8000)          │
   │      - Change color, crop, or exposure live while recording! │
   └─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start Guide

### 1. Raspberry Pi Setup
Clone the repository and start the server:

```bash
git clone https://github.com/rcsharath/pizero2w-camera-stream.git
cd pizero2w-camera-stream
python3 stream_server.py
```

### 2. Autostart on Boot (Systemd)
Enable the background service to start automatically on boot:

```bash
mkdir -p ~/.config/systemd/user
cp camerastream.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now camerastream.service
loginctl enable-linger $USER
```

### 3. Windows Desktop Usage (Simultaneous Recording & Live Preview)
* **1-Click Record & Live Preview (Recommended):** Double-click `record_and_preview.bat`. Launches FFmpeg stream recorder in background and opens VLC live preview instantly.
* **Standalone Live Viewer:** Double-click `open_vlc_stream.bat`. Automatically uses host UDP relay if recording, or connects directly to Pi TCP stream if not recording.
* **Web Management Dashboard:** Open `http://rcsharathpi.local:8000` in Chrome to tweak color balance, ROI crop, lighting presets, or system stats on the fly while recording.

---

## 🛠️ Web API Reference

| Endpoint | Method | Parameters | Description |
| :--- | :--- | :--- | :--- |
| `/stats` | `GET` | None | Returns live JSON system telemetry (`temp`, `ram_free_mb`, `cpu_load`). |
| `/set_mode` | `GET` | `mode=day` / `night_outdoor` / `night_indoor` | Configures shutter exposure & gain presets. |
| `/set_crop` | `GET` | `x`, `y`, `w`, `h` (0.0 to 1.0) or `reset=1` | Applies hardware region-of-interest crop in VideoCore GPU. |
| `/set_awb` | `GET` | `mode` (auto, indoor, etc.) & `red`, `blue` | Applies white balance & custom red/blue gain multipliers. |
| `/set_resolution` | `GET` | `res=1296x972_15` | Changes resolution & FPS mode. |
| `/snapshot.jpg` | `GET` | None | Captures and downloads a full 5MP (2592×1944) JPEG image. |

---

## 🛡️ Security & Privacy Notice

This repository contains **zero credentials, hardcoded passwords, or private keys**. All communication relies on standard local network mDNS (`rcsharathpi.local`) and user-configurable ports.

---

## 📜 License

MIT License. Free to use, modify, and distribute!
