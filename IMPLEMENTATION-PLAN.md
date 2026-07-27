# Implementation Plan: pizero2w-camera-stream

**Status:** DRAFT, awaiting review
**Written:** 2026-07-27
**Against:** review of commit `3bd2443`, plus a fresh state audit of `C:\Users\rcsha\Documents\AI Builds\Raspberry Pi Zero 2W`
**Target:** Pi Zero 2W, armv7l 32-bit, Raspbian 13 trixie, OV5647, Python 3.13.5

---

## 0. Decisions locked in grilling

| Question | Decision |
|---|---|
| MediaMTX on the Pi | **No.** Keep the current architecture, improve it. |
| picamera2 migration | **No.** Keep `rpicam-vid`. Preserve the low-CPU thesis. |
| Port 8888 ownership | **Python fans out to N clients.** Replaces `rpicam-vid --listen`. |
| Recording location | **PC.** MKV segments, no auto-prune, free-space guard only. |
| Pi's purpose | **Camera.** Hermes Agent shelved, Pi stays 32-bit, no reflash. |
| Repo layout | **`camera/` and `hermes/`** under the existing parent folder. |
| Deploy | Plan first. SSH deploy after approval. |

### Where this plan departs from the review

The review presented H1 (single-client `--listen`) as having exactly one fix: MediaMTX. It has a second, and it is a better fit for the constraint "current system but improved."

**Python owns port 8888.** `rpicam-vid` runs with `-o -` and writes H.264 to stdout. `stream_server.py` reads that pipe and copies bytes to every connected client. No decode, no re-encode, no new binary, no new package. Estimated cost ~1% CPU and ~2 MB RAM at 4 Mbps.

This resolves H1, H4, H5, and the entire UDP relay layer, which the review could only achieve by adding MediaMTX. What it does not give you that MediaMTX would: in-browser WebRTC preview, and RTSP timestamps. The dashboard therefore uses an on-demand still for the crop workflow, not live video. That is an accepted trade, not an oversight.

---

## 1. State audit findings not in the review

The review looked only at the seven files in `pizero2w-camera-stream/`. The parent folder holds more, and it conflicts.

**F1. The parent folder is a different project's git repo.** Three commits, all Hermes Agent planning. No remote. Nothing camera-related. `pizero2w-camera-stream/` is nested inside it and explicitly gitignored as "Legacy" (commit `7e7d965`).

**F2. Three tracked files are deleted in the working tree, uncommitted.** `docs/ADR-0001-runtime-choice.md`, `docs/01-provision.md`, `checks/0.4-falsifier.md`. Present in HEAD, absent from disk. One `git commit -a` from permanent.

**F3. ADR-0001 mandates a 64-bit reflash.** It requires Raspberry Pi OS Lite aarch64 because armv7l lacks prebuilt wheels. The Pi runs armv7l. Executing that ADR wipes the SD card the camera server lives on. Resolved in grilling: camera wins, ADR-0001 gets marked superseded rather than deleted.

**F4. The three `.bat` files exist twice**, byte-identical, at the parent root (untracked) and inside the nested repo (tracked). No way to tell which a user double-clicks.

**F5. `__pycache__` is `cpython-312`, the Pi runs 3.13.5.** That bytecode was produced by running `stream_server.py` on Windows. There is no local artifact proving what is deployed at `/home/rcsharath/camerastream/` matches `3bd2443`. **Phase 6 verifies this before overwriting anything.**

**F6. The dashboard never reflects real server state.** Every control renders its hardcoded default on page load regardless of what the server actually has applied. Combined with M8 (no persistence), the UI can confidently show "Day / Auto, full frame" while the camera runs night mode at a 60% crop. Fixed by the new `/config` endpoint in Phase 3.

**F7. Every API call is fire-and-forget into `console.log`.** No success indicator, no error surface, no pending state. When a setting change kills `rpicam-vid` into a crash loop (C3, C4), the dashboard reports nothing at all.

---

## 2. Target repo layout

```
Raspberry Pi Zero 2W/                    umbrella, existing git repo, no remote
├── AGENTS.md                            routing, mirrors the vault convention
├── .gitignore
├── camera/                              ACTIVE. own repo, own GitHub remote
│   ├── LICENSE                          MIT, currently missing (M12)
│   ├── README.md                        rewritten (M14)
│   ├── IMPLEMENTATION-PLAN.md           this file
│   ├── stream_server.py                 ~380 lines after HTML split
│   ├── static/
│   │   └── index.html                   redesigned dashboard (M13)
│   ├── config.example.json              token, paths, defaults
│   ├── camerastream.service             fixed unit (H7)
│   ├── deploy.bat                       scp + service restart
│   └── pc/
│       ├── record_stream.bat            rebuilt (C1, C6, H6, H8, H9)
│       └── open_vlc_stream.bat          simplified, no mode guessing
└── hermes/                              SHELVED
    ├── SUPERSEDED.md                    why the Pi stayed 32-bit
    ├── docs/ADR-0001-runtime-choice.md  restored from HEAD
    ├── docs/01-provision.md             restored from HEAD
    └── checks/0.4-falsifier.md          restored from HEAD
```

`record_and_preview.bat` is **deleted**. Its only reason to exist was sequencing the recorder before VLC so they did not fight over the single `--listen` slot. With fan-out they can both just connect. Deleting it removes H4 entirely rather than patching it.

**Open item for your call:** `camera/` stays a nested repo with its own GitHub remote, gitignored by the umbrella. The alternative is making it a git submodule so the umbrella records which camera commit is current. Submodule is more correct, more friction. Default is nested-and-ignored unless you say otherwise.

---

## 3. Phase 0: Restructure

1. `git mv` the parent's tracked docs into `hermes/`, restore the three deleted files from HEAD first so nothing is lost.
2. Add `hermes/SUPERSEDED.md`: records that ADR-0001's 64-bit reflash requirement is not being executed, the Pi remains armv7l, and the camera is the active workload. Links back to the Setup Log entry.
3. `git mv pizero2w-camera-stream camera`, preserving the nested `.git` and its remote.
4. Delete the three duplicate `.bat` files at the parent root.
5. Update the umbrella `.gitignore`: `pizero2w-camera-stream/` becomes `camera/`. Drop the "Legacy" comment.
6. Inside `camera/`: `git rm --cached test_snapshot.jpg` (M11), add `LICENSE` (M12), add `recordings/` and `*.log` to `.gitignore`.
7. Write the umbrella `AGENTS.md` routing table.
8. Commit both repos so git shows the current truth, not a half-deleted tree.

**Done when:** `git status` is clean in both repos, no file exists twice, `hermes/` and `camera/` both hold complete working trees.

---

## 4. Phase 1: TCP fan-out (H1, H4, H5)

### Design

`rpicam-vid` launches with `-o -` instead of `--listen -o tcp://...`. A reader thread drains its stdout in 64 KB chunks. A `socketserver.ThreadingTCPServer` on 8888 accepts clients. Each client gets a writer thread and a bounded queue.

```
rpicam-vid  --(stdout pipe)-->  reader thread  --> [client queue] --> writer thread --> socket
                                      |          --> [client queue] --> writer thread --> socket
                                      |          --> [client queue] --> writer thread --> socket
                                      v
                                 GOP buffer (last keyframe onward)
```

### The three things that make this safe

**Never block the reader.** If Python stops draining stdout, `rpicam-vid` blocks on write and the stream stalls. So the reader never waits on a client. It appends to each client's queue and moves on.

**Bounded per-client queue, drop-oldest.** `deque` with a byte budget, default 4 MB, roughly 8 seconds at 4 Mbps. A client that falls behind loses old frames rather than growing RAM without limit. This is the same failure mode as the unbounded bytearray bug already fixed once on this project, so it gets designed out rather than patched later. A client still over budget after a grace period is disconnected and logged.

**GOP buffer for instant joins.** Raw H.264 has no header a late joiner can use, so a new client normally sees nothing until the next keyframe. The reader parses Annex-B start codes, tracks NAL types (SPS 7, PPS 8, IDR 5), and keeps every byte since the most recent SPS. A new client receives that buffer, then goes live. With `--intra 15` the buffer is about one second, roughly 500 KB at 4 Mbps. Bounded by construction.

### Consequences

- Recorder, VLC, and a phone can all connect at once.
- Closing VLC no longer terminates `rpicam-vid`, so no 3+ second hole in the recording.
- `open_vlc_stream.bat` stops guessing which URL to use. It always points at the Pi.
- `recorder.lock` and the `tasklist | findstr ffmpeg.exe` checks are deleted, not fixed.
- New telemetry becomes available and feeds the dashboard: client count, live bitrate, bytes served, stream uptime, dropped-chunk count.

### Risks

| Risk | Mitigation |
|---|---|
| CPU higher than the ~2.7% headline | Measure in Phase 8. Expect +0.5 to 1.5%. If it exceeds 5%, raise chunk size and revisit. |
| GIL contention with the HTTP server | Both paths are I/O bound and release the GIL on read/write. Low risk. |
| A malicious LAN client opens many sockets | Cap concurrent clients, default 4, reject beyond that. |
| Reader thread dies silently | Supervised by `camera_worker`, which restarts the whole pipeline. |

---

## 5. Phase 2: Camera correctness

### C2, night mode actually gets its long exposure

Lighting mode owns framerate. Today a 100 ms shutter request is silently clamped to ~66 ms by the frame duration limit, and the ISP compensates with gain, so you get night-mode noise without night-mode light.

```python
MODE_PRESETS = {
    "day":          {"shutter": None,  "gain": None, "fps_cap": None, "denoise": "cdn_fast"},
    "night_indoor": {"shutter": 60000, "gain": 4.0,  "fps_cap": 12,   "denoise": "cdn_hq"},
    "night_outdoor":{"shutter": 100000,"gain": 6.0,  "fps_cap": 8,    "denoise": "cdn_hq"},
}
effective_fps = min(preset_fps, cap) if cap else preset_fps
```

`--framerate {effective_fps}` and `--denoise cdn_hq` get added. ISP denoise runs in the ISP block, not on the ARM cores, so it is free. Once the shutter is genuinely honoured, outdoor night gain is worth retesting at 4.0: longer exposure at lower gain is a better SNR trade for a static camera. That is a Phase 8 measurement, not a guess made now.

### C3, remove the 5MP H.264 preset

2592x1944 is 19,764 macroblocks against a hardware ceiling of 8192, and wider than the 1920 px limit. It cannot encode. Because `camera_worker` restarts on exit, selecting it puts the Pi in an infinite crash loop, which is the real cause of "the stream disconnects when settings change."

Removed from `RESOLUTIONS` and from the dashboard. Video caps at 1920x1080 (8160 macroblocks, just under the limit). 5MP survives where it actually works, in `/snapshot.jpg` via `rpicam-still`, which uses a different hardware path.

### C5, snapshot stops hanging threads

Three stacked bugs today: `rpicam-vid` holds the sensor exclusively so `rpicam-still` blocks, there is no `timeout=`, and `ThreadingMixIn` means every click spawns another hung thread and another zombie process. On a 416 MB box that is a path to OOM.

Fix: a non-blocking `threading.Lock`, a hard `timeout=20`, and an explicit pause / capture / resume under `camera_lock`. Second concurrent request gets HTTP 409, not a queue. Expect 4 to 6 seconds of stream downtime per snapshot, which the dashboard states plainly before you click.

### H2 and H3, encoder flags

`quality` in the `RESOLUTIONS` tuple is read into `q` and never used. It is replaced by `bitrate`.

| Flag | Value | Why |
|---|---|---|
| `--profile high` | high | CABAC and 8x8 transform, 10-15% bitrate saving at equal quality. Free. |
| `--level` | 4.2 | Unlocks the bitrate ceiling high profile wants. **See open item below.** |
| `--bitrate` | 4000000 default at 1296x972@15 | Explicit control, exposed as a dashboard slider. |
| `--intra` | = effective fps | One keyframe per second. Exact segment cuts, instant joins, and it feeds the GOP buffer. |
| `--denoise` | `cdn_hq` at night | Hardware ISP, big win at high gain. |

**Open item:** the review says the encoder is Level 4.1 in C3 and then recommends `--level 4.2` in H2. Those cannot both be right. Plan is to make `--level` configurable, default 4.2, and verify on device in Phase 8. If `rpicam-vid` rejects it, fall back to 4.1 with a one-line note in the README.

---

## 6. Phase 3: Control plane

### C4, ROI validation server-side

The sliders can produce `x=0.80, w=1.00`, which violates `x+w <= 1.0`, kills `rpicam-vid` on launch, and triggers the restart loop. Today `/set_crop` accepts the strings verbatim and returns `{"status":"ok"}`. **The API reports success for settings that crash the camera.** That is the worst possible failure signature and it is the top priority in this phase.

Validation rejects: malformed, out of `[0,1]`, width or height below 0.05, and `x+w > 1.0` or `y+h > 1.0`. Each returns HTTP 400 with the specific reason, which the dashboard shows as a toast. The UI additionally clamps `cropW.max` to `100 - cropX.value` dynamically, but the server never trusts it.

### M2, whitelists everywhere

`VALID_MODES` and `VALID_AWB` as sets. Unknown values return 400 instead of silently falling through to the day branch while reporting success. Gains clamp to `[1.0, 3.0]`. `float()` calls get wrapped so bad input returns 400 rather than a traceback and a 500.

There is no shell injection risk today, `subprocess.Popen` is correctly called in list form with no `shell=True`. That stays.

### M3, M4, M5, M6, M7

- All shared-state writes move under `camera_lock`. Today handlers write without it while the worker reads with it, so two concurrent requests can interleave a half-applied config into the next launch.
- `json.dumps()` replaces every hand-rolled f-string. On reset, `current_roi` is `None`, so the current code emits the string `"None"` instead of JSON `null`.
- `do_HEAD` mirrors GET routing instead of returning 200 for every path including 404s.
- `protocol_version = "HTTP/1.1"` plus `Content-Length` on every response enables keep-alive. Polling drops to 5s and pauses on `document.visibilityState !== 'visible'`. A tab left open for a day currently opens ~28,800 connections.
- `HTML_BYTES` precomputed once at module level. Today every page load does two full UTF-8 encodes of a 20 KB string.

### M8, persistence

State persists to `~/.config/camerastream/state.json` on every change and loads at startup, falling back to defaults if missing or malformed. Today a reboot silently reverts a carefully tuned crop and night preset back to `1296x972_15` day mode, full frame.

### M10, auth

You had no preference, so taking the recommendation: shared-secret token, read from `~/.config/camerastream/config.json`, required on every mutating endpoint and on `/snapshot.jpg`. `GET /` and `/stats` stay open so the dashboard loads. Token lives in the vault, never in git; `config.example.json` ships with a placeholder.

This replaces the README's current claim that "zero credentials" is a security feature. It is not. It means there is no access control, and anyone on the WiFi can pull 5MP stills of your property or DoS the camera with crash-inducing ROI values.

If you would rather not carry a token, say so and I will instead document the no-auth posture as an explicit decision. What will not survive is the current claim.

### New: `/config` endpoint

Returns the full current server state. The dashboard calls it on load and hydrates every control from it, fixing F6. Also consumed by `record_stream.bat` to read the real framerate rather than hardcoding 15.

---

## 7. Phase 4: Dashboard redesign

### What is wrong with the current one

It is a status page pretending to be a control panel. Controls show defaults rather than reality (F6). Actions vanish into `console.log` with no feedback (F7). Cropping is four blind sliders with no visual reference, which is the single worst ergonomic problem in the product. The hostname is hardcoded so the page is wrong if reached any other way (M9). It claims "Ice-Cold (~48°C / 2% CPU)" as static text that is not measured. It offers a preset that crash-loops the camera (C3). And it is a 540-line string inside a Python file (M13).

### Structure

```
┌──────────────────────────────────────────────────────────────────┐
│  Pi Zero 2W Camera          ● STREAMING     47°C  268MB  0.31    │
│                                             2 clients  3.9 Mbps   │
├────────────────────────────────────┬─────────────────────────────┤
│                                    │  [Video][Light][Colour][Sys]│
│   ┌──────────────────────────┐     │                             │
│   │                          │     │  Resolution   [1296x972 15▾]│
│   │   still frame            │     │  Bitrate      ──●───  4.0Mb │
│   │   with draggable         │     │  Profile      high / L4.2   │
│   │   crop rectangle         │     │  Keyframe     every 1.0s    │
│   │                          │     │                             │
│   └──────────────────────────┘     │  ┌───────────────────────┐  │
│   [Refresh still]  4-6s blip       │  │ Apply    (2 pending)  │  │
│   ROI  x .12  y .08  w .70  h .64  │  └───────────────────────┘  │
├────────────────────────────────────┴─────────────────────────────┤
│  Stream  tcp/h264://<location.hostname>:8888          [copy]     │
└──────────────────────────────────────────────────────────────────┘
```

### Changes that matter

**Drag-to-crop over a real still.** Click Refresh, the server pauses the stream, captures, resumes, returns a JPEG. You drag a rectangle on it. The rectangle is the ROI. The button states the 4 to 6 second cost up front, and a capture already in flight gets 409 and a toast rather than a second hung thread.

**Every control hydrated from `/config` on load.** The panel shows what the camera is actually doing, including after a reboot.

**Explicit apply with a pending count.** Settings that need a camera restart batch into one apply instead of one restart per slider nudge. The button says how many changes are queued. Live-safe settings apply immediately. This is the honest version of what picamera2 would have given for free, without the RAM cost.

**Toasts on every response.** Success, and specifically the 400 reasons from Phase 3, so an invalid crop tells you "region exceeds sensor" instead of failing silently into a crash loop.

**Real stream telemetry**, newly possible because Python owns the socket: connected clients, live bitrate, stream uptime, bytes served, drops. The static "2% CPU" boast is replaced with the measured number.

**Connection state machine.** STREAMING, RESTARTING, CAMERA DOWN, SERVER UNREACHABLE. Controls disable during RESTARTING instead of accepting input that goes nowhere.

Ships as `static/index.html`, read once at startup into a cached bytes object, so zero per-request cost. Mobile layout collapses to a single column, because a phone is the realistic device for checking a camera.

---

## 8. Phase 5: PC recorder

### C1, the regression that matters most

Raw Annex-B H.264 carries no timestamps. FFmpeg assumes 25 fps, your stream is 15. **Every recording currently plays back about 1.67x too fast, and a "90 second" segment is 54 seconds of wall time.** Commit `9494b88` fixed this. The flag was dropped in the `3bd2443` rewrite.

Restored, and the framerate is read from the Pi's `/config` at recorder start via `curl.exe` rather than hardcoded, so it cannot desync from the preset again.

### C6, MKV instead of MP4

The MP4 segment muxer finalises the moov atom at segment close. Graceful Ctrl+C is handled; power loss, PC crash, or a Task Manager kill leaves the current file unplayable and loses up to 90 seconds. MKV plays right up to the last written frame. Remux to MP4 later with `-c copy` for free if needed.

### H6, reconnect loop

One WiFi blip currently ends 24/7 recording silently: FFmpeg exits, the batch hits `pause`, and that is it until a human notices. Note `-reconnect 1` is an HTTP-protocol option and does nothing for `tcp://`, so the retry lives in the batch file. A `recorder.log` line on every connect and drop gives you a way to audit whether a night of footage is actually complete.

### H8 and H9

Free-space guard refuses to start below a configurable floor, default 20 GB, so you fail loudly rather than corrupting a segment mid-write. **No auto-prune, per your decision.** Output moves to a configurable `RECORD_DIR` outside the git working tree.

### Deletions

`record_and_preview.bat` is deleted. The lockfile, the stale-lock cleanup, and the `tasklist | findstr ffmpeg.exe` checks are deleted. The UDP loopback output is deleted. All of it existed to work around single-client `--listen`, which no longer exists after Phase 1. `open_vlc_stream.bat` becomes about eight lines that point VLC at the Pi.

---

## 9. Phase 6: Service and deploy

### H7, the orphan bug

`KillMode=process` kills only the Python process. `rpicam-vid` survives holding the sensor, the service restarts, the new `rpicam-vid` cannot open the camera, and only a reboot or manual `pkill` clears it. This is why restarts sometimes never recover.

Unit changes: `KillMode=control-group` to reap the whole cgroup, `python3 -u` so `print()` reaches `journalctl` instead of sitting in a block buffer (which is why the logs currently look empty), `Restart=on-failure` with `StartLimitBurst=6` so a crash loop cannot cook the SD card, `ExecStopPost=/usr/bin/pkill -f rpicam-vid` as a belt-and-braces reap, `TimeoutStopSec=15`, and a corrected `Description` which currently still says "MJPEG".

Plus SIGTERM and SIGINT handlers in Python that terminate the camera process and close client sockets, so shutdown is clean rather than merely fast.

### deploy.bat

Because my sandbox cannot reach your LAN, deployment runs from your PC. `deploy.bat` will, in order: back up the current `/home/rcsharath/camerastream/` to a timestamped folder, diff the deployed `stream_server.py` against the repo so **F5 gets resolved before anything is overwritten**, scp the new files, install the systemd unit, `daemon-reload`, restart, and print `systemctl --user status` plus the last 30 journal lines.

Rollback is a one-liner against the timestamped backup.

---

## 10. Phase 7: Docs and vault

**README (M14).** Two claims come out. "Captures 5MP stills without interrupting the stream" is false, it does not work at all today and after C5 it costs a 4 to 6 second blip. The 2592x1944 H.264 preset is listed as a working feature and is physically impossible. Also removed: the "zero credentials is a security feature" framing. Added: the fan-out architecture diagram, honest CPU numbers measured in Phase 8, and the auth setup step.

**M9, hostname.** `PI_HOST` variable at the top of each batch file, `location.hostname` in the dashboard. Currently `rcsharathpi.local` is hardcoded in five places.

**Vault, `C:\Users\rcsha\Dropbox\Obsidian\pizero2w`.** Per its AGENTS.md routing rules:

- `Hardware.md`: update the two code paths, which currently point at the pre-restructure locations and at a "New Agent Code" directory that is now `hermes/`.
- `Setup Log.md`: **append only**, one dated entry recording the restructure, the fan-out change, and the deploy.
- `Experiments/IP Camera Stream.md`: update status, link the plan, correct the findings section which still describes the old MJPEG server at 640x480.
- `AGENTS.md`: the routing table's "Code for a project" row points at the parent folder. Update it to name `camera/` and `hermes/` explicitly.

---

## 11. Phase 8: Verification on device

Nothing in this plan is believed until measured on the Pi. Runs after deploy.

| Check | Method | Pass condition |
|---|---|---|
| Fan-out works | Connect VLC and FFmpeg at once, then close VLC | Both get video; recorder shows no gap |
| CPU cost is acceptable | `top -b -n 5` during a 3-client stream | Under 5% total |
| RAM headroom holds | `free -m` steady state | Over 200 MB available |
| Recording plays at 1.0x | `ffprobe -show_entries format=duration` vs stopwatch | Within 2% of 90s |
| Night shutter honoured | `rpicam-vid -v 2` metadata, ExposureTime | ~100000us, not clamped |
| No orphaned processes | `systemctl --user restart` then `pgrep -a rpicam-vid` | Exactly one, owned by the new unit |
| Logs reach journal | `journalctl --user -u camerastream -n 30` | Startup lines present |
| Invalid ROI rejected | `curl '/set_crop?x=0.8&y=0&w=1.0&h=1.0'` | HTTP 400, camera keeps streaming |
| 5MP preset gone | Dashboard dropdown | Absent |
| Snapshot concurrency | Two simultaneous requests | One 200, one 409, no zombies |
| Slow client eviction | Connect a client, SIGSTOP it, watch RSS | RSS flat, client dropped after grace |
| Settings survive reboot | Set crop and night mode, reboot, reload dashboard | State restored |

Local pre-deploy checks: `python -m py_compile`, a `--selftest` flag that exercises validation and fan-out against a synthetic byte source with no camera present, and HTML validation.

---

## 12. Findings coverage

Every finding in the review, and where it lands.

| ID | Finding | Phase | Notes |
|---|---|---|---|
| C1 | Recording plays 1.67x too fast | 5 | Framerate read from `/config`, not hardcoded |
| C2 | Night mode shutter clamped | 2 | Mode owns framerate |
| C3 | 5MP H.264 preset impossible | 2 | Removed |
| C4 | ROI never validated | 3 | 400 with reason |
| C5 | Snapshot hangs threads | 2 | Lock, timeout, 409 |
| C6 | MP4 unplayable after hard kill | 5 | MKV |
| H1 | `--listen` single-client | 1 | Python fan-out, not MediaMTX |
| H2 | No bitrate or profile control | 2 | Slider, `quality` replaced |
| H3 | `--intra` unset | 2 | Set to fps |
| H4 | Stream-stealing race | 1, 5 | `record_and_preview.bat` deleted |
| H5 | `tasklist findstr` false positives | 1, 5 | Whole mechanism deleted |
| H6 | No reconnect loop | 5 | Loop plus `recorder.log` |
| H7 | `KillMode=process` orphans | 6 | `control-group`, SIGTERM handler |
| H8 | No disk retention | 5 | Free-space guard. **No prune, your call** |
| H9 | Segments in the git tree | 5 | Configurable `RECORD_DIR` |
| M1 | Duplicated `/set_mode` block | 3 | Deleted |
| M2 | No validation on mode or AWB | 3 | Whitelists, clamps |
| M3 | Shared state written unlocked | 3 | Under `camera_lock` |
| M4 | Hand-rolled JSON emits `"None"` | 3 | `json.dumps` |
| M5 | `do_HEAD` returns 200 always | 3 | Mirrors GET |
| M6 | `/stats` polling cost | 3, 4 | Keep-alive, visibility gate, 5s |
| M7 | HTML re-encoded per request | 3 | Precomputed bytes |
| M8 | Settings lost on restart | 3 | `state.json` |
| M9 | Hostname in five places | 7 | `PI_HOST`, `location.hostname` |
| M10 | No auth on control API | 3 | Token. **Confirm or override** |
| M11 | `test_snapshot.jpg` committed | 0 | `git rm --cached` |
| M12 | No LICENSE | 0 | MIT added |
| M13 | 540-line HTML in Python | 0, 4 | `static/index.html` |
| M14 | README overstates two things | 7 | Rewritten |
| A1 | picamera2 migration | — | **Declined.** RAM and CPU cost |
| A2 | MediaMTX | — | **Declined.** Replaced by Phase 1 |
| A3 | Suggested ordering | — | Reordered around the above |
| F1-F7 | State audit findings | 0, 3, 4, 6 | See section 1 |

---

## 13. What I need from you

1. **Approve or amend.** Particularly section 2's nested-repo-versus-submodule open item, the auth decision in 6, and the `--level` contradiction in 5.
2. **Confirm the deploy path.** I cannot reach the Pi from my sandbox, verified. Either you run `deploy.bat`, or you tell me the SSH route you want me to drive and I will work within the tier restrictions on terminal apps.
3. **One thing I would push back on.** You chose "keep everything" for retention. At ~6.5 GB/day that is ~200 GB per month with no ceiling, and the free-space guard turns a slow problem into a hard stop that silently ends recording. Worth at least deciding now where `RECORD_DIR` points, because it should not be the system drive.
