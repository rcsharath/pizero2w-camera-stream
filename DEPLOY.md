# Deployment Guide & Physical Verification Checklist

Follow these steps to deploy the updated `stream_server.py`, `static/` dashboard folder, and systemd unit file to the Raspberry Pi Zero 2W.

> [!NOTE]
> This service unit and deployment guide assume target user `rcsharath` and deployment directory `/home/rcsharath/camerastream/`.

## 1. Prepare Target Directories & Copy Files

Run the following SSH and `scp` commands from your local computer terminal:

```bash
# 1. Create target directories on the Pi if they do not exist
ssh -o "KexAlgorithms=curve25519-sha256" rcsharath@rcsharathpi.local "mkdir -p ~/.config/systemd/user /home/rcsharath/camerastream"

# 2. Copy Python server executable
scp -o "KexAlgorithms=curve25519-sha256" stream_server.py rcsharath@rcsharathpi.local:/home/rcsharath/camerastream/stream_server.py

# 3. Copy static dashboard directory containing index.html
scp -r -o "KexAlgorithms=curve25519-sha256" static rcsharath@rcsharathpi.local:/home/rcsharath/camerastream/static

# 4. Copy updated systemd user service unit
scp -o "KexAlgorithms=curve25519-sha256" camerastream.service rcsharath@rcsharathpi.local:/home/rcsharath/.config/systemd/user/camerastream.service
```

## 2. Reload and Restart Service on Pi

SSH into the Raspberry Pi Zero 2W and reload systemd user configuration:

```bash
ssh -o "KexAlgorithms=curve25519-sha256" rcsharath@rcsharathpi.local
```

Once logged into the Pi (or via non-interactive SSH with `export XDG_RUNTIME_DIR=/run/user/$(id -u)`), execute:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
systemctl --user restart camerastream.service
systemctl --user status camerastream.service
```

To view real-time unbuffered log output from the server:

```bash
journalctl --user -u camerastream.service -f
```

## 3. Post-Deploy Browser Verification Checklist

Open `http://rcsharathpi.local:8000` (or `http://<pi-ip-address>:8000`) on a desktop or mobile browser and execute the following physical checklist:

1. **Dashboard Hydration on Load:** Open the page and verify that all controls reflect active server state (e.g. resolution `<select>` populated from `/config`, correct active mode button highlighted, rotation dropdown matching current state).
2. **Stream URL Display:** Verify that the footer displays `tcp/h264://<hostname-or-ip>:8888` dynamically and that clicking "Copy Stream URL" displays a success toast.
3. **Resolution & FPS Control:** Change resolution to `1280x720` and FPS to `20`. Confirm a green success toast appears and VLC stream reconnects cleanly at `tcp/h264://<pi-ip>:8888`.
4. **Invalid Parameter Rejection:** Issue an invalid crop parameter or invalid FPS in the URL bar (e.g. `http://rcsharathpi.local:8000/set_fps?fps=99`). Confirm a red error toast appears on screen with an explicit error reason and the process does not crash.
5. **State Persistence Across Service Restart:** Change lighting mode to `Night Outdoor` and orientation to `180 (Inverted)`. Restart the service via SSH (`systemctl --user restart camerastream.service`). Reload the browser page and confirm `Night Outdoor` and `180 (Inverted)` persist.
6. **Mobile Layout Check:** Open the dashboard on a smartphone (or resize desktop browser width below 700px). Verify that the status strip, control cards, crop sliders, and footer stack cleanly into a single vertical column.
7. **Night Exposure Verification Checklist:**
   - Test all four lighting combinations (indoor/outdoor artificial light ON/OFF) against the real bird enclosure.
   - Apply a manual shutter cap and manual gain override from the dashboard and confirm the video feed visibly brightens or darkens accordingly.
   - Apply manual gain values of `10.0` and `12.0` in `night_outdoor` mode; inspect `journalctl --user -u camerastream -n 30` for `camera.launch_failed` or unexpected `camera.exited` non-zero exit codes.
   - Set an active hardware crop (ROI) and toggle metering mode between `centre`, `spot`, and `average` to test whether `--roi` influences AEC/AGC metering sampling on hardware.
