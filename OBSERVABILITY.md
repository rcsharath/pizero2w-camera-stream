# Observability Design: pizero2w-camera-stream

**Status:** DRAFT, awaiting review
**Written:** 2026-07-28
**Against:** HEAD `d2ad32a`, `stream_server.py` (608 lines)
**Companion to:** `IMPLEMENTATION-PLAN.md`. Phase references below (P0 to P8) point at that document.
**Target:** Pi Zero 2W, armv7l, Raspbian 13 trixie, Python 3.13.5, user-level systemd

---

## 0. Decisions locked

| Question | Decision |
|---|---|
| Log sink | **journald only.** JSON lines to stdout, systemd captures. No file written by us. |
| Format | **One JSON object per line.** No prose, no multi-line records. |
| Causality model | **`cause` is always a `seq` number.** One ID namespace, not four. |
| Log levels | **Three only:** `info`, `warn`, `error`. No DEBUG sprawl. |
| Per-frame logging | **Never.** Not at any level, not behind a flag. |
| Untracked state | **Detected, not assumed.** A reconciler compares belief against `/proc`. |
| Dashboard log pane | Deferred. Revisit after P4 if journald-only proves painful. |

### Why journald only

The Zero 2W boots off an SD card. Every avoidable write is avoidable wear. journald is already running, already rotates, already timestamps, and already survives the service crashing. Writing our own `events.jsonl` would mean owning rotation, fsync policy, and disk-full behaviour for zero gain.

Read path is `journalctl --user -u camerastream -o cat | jq`.

**Two device checks required before this is trusted (P8):**

1. **Persistence.** If `/var/log/journal/` does not exist, journald runs in volatile mode and the entire log is lost on reboot. That makes overnight debugging impossible, which is the main use case. Verify with `journalctl --header | grep -i persistent`. Fix is `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`, then cap it with `SystemMaxUse=200M` in `journald.conf`.
2. **Rate limiting.** journald defaults to roughly `RateLimitBurst=10000` per `RateLimitIntervalSec=30s` per service. Exceed it and messages are **silently dropped**. That is precisely the failure this design exists to prevent. Either set `RateLimitIntervalSec=0` in the unit's `[Service]` section, or accept the cap and rely on the seq-gap detector in section 5 to reveal the loss. Prefer the former.

---

## 1. What is blind today

Every item is a real gap in `d2ad32a`, not a hypothetical.

| # | Gap | Consequence |
|---|---|---|
| B1 | `camera_process.poll()` result is discarded (`stream_server.py:363`) | Exit code and signal are never known. A `SIGKILL` from the OOM killer and a clean config restart look identical. |
| B2 | `rpicam-vid` stderr is inherited, unattributed, uncorrelated | libcamera's actual complaint lands in the journal with no `gen`, no config context, interleaved with our prints. |
| B3 | `rpicam-still` stderr goes to `DEVNULL` (`:566`) | Confirms `IMPLEMENTATION-PLAN.md` C5: snapshots do not work today, and the code is constructed so you cannot see why. |
| B4 | `log_message` returns early (`:394`) | Zero HTTP access record. No way to know a request even arrived. |
| B5 | `get_system_stats` has a bare `except: pass` (`:292`) | Telemetry can return `temp: 0.0` forever and the dashboard renders it as fact. |
| B6 | Nothing reads `vcgencmd get_throttled` | Undervoltage is the most common cause of "the camera randomly died" on this board, and it presents as a software bug. |
| B7 | No timestamp, no sequence, no correlation in any `print()` | "The stream dropped, was that my fps change or something else?" is unanswerable. |
| B8 | `camera_worker` can die and `serve_forever` keeps answering `/config` | Server reports healthy state for a camera thread that no longer exists. |
| B9 | Validation rejections at `:456` etc. return 400 to the client and log nothing | The crash-loop-inducing inputs described in C4 leave no server-side trace. |

---

## 2. Three design pillars

### P1. One choke point

Every state mutation goes through a single function:

```python
def apply_change(field, value, cause_seq):  # emits state.changed, returns bool changed
```

Today each `/set_*` branch mutates module globals inline (`:440` to `:553`). Adding an endpoint means remembering to log, which means one day forgetting to. Route all seven mutation paths through `apply_change` and the untracked path stops being a discipline problem and becomes structurally impossible.

Same principle for the camera: `request_restart(cause_seq)` is the only thing permitted to set `restart_requested`.

### P2. Causality by reference, not by clock

Correlating on timestamps is guesswork, especially with a 0.5s poll loop in `camera_worker`. Instead every event carries a `seq`, and any event with a cause carries `cause: <seq of the causing event>`.

One namespace. One counter. Walking a chain is a backwards traversal, not a time-window search:

```
seq 411  http.request        GET /set_fps?fps=30
seq 412  state.changed       fps 15 -> 30            cause 411
seq 413  camera.restart_req  gen 4                   cause 412
seq 414  camera.exited       gen 4 code -15 expected cause 413
seq 415  camera.launched     gen 5 pid 1994          cause 413
```

`gen` is the one separate counter, because a camera process is a lifetime rather than an event. Every event emitted during generation N carries `gen: N`, which makes "was this stderr from the old config or the new one" a lookup instead of an inference.

### P3. Facts, not prose

`"[CameraWorker] Process died unexpectedly, restarting in 2s..."` is a sentence. This is a fact:

```json
{"ts":"2026-07-28T04:12:09.481Z","run":"a3f91c","seq":414,"lvl":"warn",
 "ev":"camera.exited","gen":4,"cause":413,"pid":1892,
 "exit_code":null,"signal":"SIGKILL","uptime_s":93.2,"expected":false}
```

`expected` is the field that turns a log line into a diagnostic. It is the difference between "we asked it to stop" and "something killed it."

---

## 3. Event envelope

Reserved keys. Event-specific fields sit flat alongside them, never nested, so `jq 'select(.gen==4)'` works uniformly.

| Key | Type | Present | Meaning |
|---|---|---|---|
| `ts` | ISO8601 UTC, ms | always | Our clock. Compare against journald's own stamp to detect emit-path delay. |
| `run` | 6 hex chars | always | Minted at process start. Makes `seq` unique across service restarts. |
| `seq` | int from 1 | always | Process-wide monotonic. The identity of this event. |
| `lvl` | enum | always | `info`, `warn`, `error`. |
| `ev` | dotted string | always | Event type. Closed vocabulary, section 4. |
| `cause` | int | when caused | `seq` of the event that caused this one. |
| `gen` | int | when camera-scoped | Camera process generation. |

`seq` is assigned under a dedicated lock, not `camera_lock`. Emitting must never block on camera state, and `camera_lock` is already held across `save_state()` file I/O at `:452`.

---

## 4. Event catalog

Closed vocabulary. Adding an event type is a deliberate act, not a side effect of writing code.

### server

| Event | Level | Fields |
|---|---|---|
| `server.started` | info | `pid`, `git_sha`, `py_version`, `boot_id`, `config_snapshot` |
| `server.stopping` | info | `signal`, `reason` |
| `server.crashed` | error | `exc_type`, `exc_msg`, `traceback`, `thread` |

`server.started` carrying the full config snapshot means every log excerpt is self-contained. You never have to ask "what was it configured as at the time."

### http

| Event | Level | Fields |
|---|---|---|
| `http.request` | info | `method`, `path`, `query`, `client_ip`, `status`, `dur_ms` |
| `http.rejected` | warn | `path`, `param`, `value`, `reason` |

`http.rejected` closes B9. The `reason` string is already written by the validators at `:65` to `:133`, it is simply thrown at the client and forgotten. Capture it.

### state

| Event | Level | Fields |
|---|---|---|
| `state.loaded` | info | `path`, `accepted[]`, `rejected[]` |
| `state.key_rejected` | warn | `key`, `value`, `reason` |
| `state.changed` | info | `field`, `from`, `to` |
| `state.unchanged` | info | `field`, `value` |
| `state.save_failed` | error | `path`, `error` |

`state.changed` is the causal root of nearly everything downstream. `state.unchanged` exists because "I clicked the button and nothing happened" needs to distinguish between the request never arriving and the request arriving as a no-op (the `if res != current_res_key` guard at `:447`).

### camera

| Event | Level | Fields |
|---|---|---|
| `camera.restart_requested` | info | `gen`, `reason` |
| `camera.launched` | info | `gen`, `pid`, `argv[]`, `config_snapshot` |
| `camera.launch_failed` | error | `gen`, `error` |
| `camera.stderr` | warn | `gen`, `pid`, `line` |
| `camera.exited` | info/warn | `gen`, `pid`, `exit_code`, `signal`, `uptime_s`, `expected` |
| `camera.crash_loop` | error | `gen`, `exits_in_window`, `window_s` |

`argv` on `camera.launched` is what makes the drift check in section 5 possible. It is also the single most useful field for reproducing a problem by hand over SSH.

`camera.crash_loop` fires when three or more generations exit unexpectedly inside 60 seconds. Without it, C4's invalid-ROI crash loop is a wall of identical events with no summary line saying "this is now a pattern."

### snapshot

| Event | Level | Fields |
|---|---|---|
| `snapshot.requested` | info | `rotation` |
| `snapshot.ok` | info | `bytes`, `dur_ms` |
| `snapshot.failed` | error | `returncode`, `stderr`, `dur_ms` |

Closes B3 directly. Expect `snapshot.failed` to fire on every attempt until C5 lands, with libcamera's device-busy message finally visible in `stderr`.

### system

| Event | Level | Fields |
|---|---|---|
| `system.sample` | info | `temp_c`, `ram_free_mb`, `load1`, `throttled`, `throttled_flags[]` |
| `system.probe_failed` | warn | `source`, `error` |

`throttled` comes from `vcgencmd get_throttled`, decoded into flags: `under_voltage_now`, `freq_capped_now`, `throttled_now`, plus the four sticky `_since_boot` bits. Closes B6.

`system.probe_failed` replaces the bare `except: pass` at `:292`. If procfs reads start failing we need to know that the zeros are missing data, not a cold quiet Pi.

### health

| Event | Level | Fields |
|---|---|---|
| `health.tick` | info | `tick_seq`, `camera_alive`, `port_8888_listening` |
| `health.drift` | error | `field`, `believed`, `observed`, `source` |
| `health.unexplained` | error | `what`, `gen`, `detail` |

Section 5 covers these.

### stream

Blocked until `IMPLEMENTATION-PLAN.md` **P1** puts Python in charge of port 8888. While `rpicam-vid --listen` owns the socket, client behaviour is unobservable from our process, full stop. Once the fan-out lands, these become available and are worth having:

| Event | Level | Fields |
|---|---|---|
| `stream.client_connected` | info | `client_ip`, `client_count` |
| `stream.client_disconnected` | info | `client_ip`, `duration_s`, `bytes_sent`, `reason` |
| `stream.client_slow` | warn | `client_ip`, `queue_depth`, `dropped_chunks` |

`stream.client_slow` is the one that matters. In a fan-out design a slow reader either backs pressure onto the whole pipeline or gets its chunks dropped. Whichever policy P1 picks, the event is how you find out it happened.

---

## 5. Knowing what is not tracked

Forward logging only ever records what you remembered to instrument. These four mechanisms cover the rest.

### 5.1 Reconciler, belief versus ground truth

A watchdog thread, every 10 seconds, compares what the server *believes* against what the OS *reports*:

| Check | Believed | Observed | Emits on mismatch |
|---|---|---|---|
| Camera argv | `argv[]` from last `camera.launched` | `/proc/<pid>/cmdline` | `health.drift` |
| Camera liveness | `camera_process` is not None | `/proc/<pid>` exists | `health.drift` |
| Persisted state | in-memory globals | SHA of `state.json` on disk | `health.drift` |
| Stream socket | should be listening | `/proc/net/tcp` scan for `:22B8` | `health.drift` |
| Worker thread | thread should be alive | `thread.is_alive()` | `health.drift` (closes B8) |

The argv check is the important one. It catches the entire class of bug where a restart request is swallowed by the race window around `restart_requested` (`:310` sets it False under lock, a concurrent request may set it True immediately before) and the dashboard confidently reports 30fps while the sensor genuinely runs at 15. No amount of forward logging finds that. Only comparison against ground truth does.

**Gating rule for argv drift check:** To prevent false-positive drift errors during normal 3-second restart windows, compare `/proc/<pid>/cmdline` against `last_launched_argv` only when `not restart_requested`, `camera_alive`, and `(time.monotonic() - gen_start_time) > 5.0` seconds. Compare full `argv`.

The `state.json` hash check catches out-of-band edits, which matters because you will eventually SSH in and hand-edit that file.

### 5.2 Unexplained-cause alarm

Every `camera.exited` must resolve to a `camera.restart_requested` via `cause`. If a process dies with no request behind it, that is not a warning buried in a stream of info lines, it is its own event:

```json
{"ev":"health.unexplained","what":"camera_exit","gen":4,
 "detail":"no restart_requested in this generation","lvl":"error"}
```

**Absence of a cause is itself the signal.** This is the mechanism that answers "if something is not being tracked, I still want to know," because it fires on the gap rather than on the thing.

Same pattern applies to `state.changed` with no `cause`: something mutated state outside the HTTP path, which by design should be impossible.

### 5.3 Sequence gap detection

Two counters, both cheap, both revealing loss:

- **`seq`** is contiguous by construction. A reader seeing 411 then 415 knows three records were dropped between them, and journald rate limiting is the likely culprit. Verifiable with `jq -s 'reduce ...'` or a five-line reader script.
- **`tick_seq`** on `health.tick` increments every 10s regardless of activity. A jump from 41 to 47 means 60 seconds vanished. Either the process stalled (SD card I/O blocking, memory pressure) or the log lost records. Both are things you want to know, and neither is discoverable from event content alone.

This is the cheapest unknown-unknown detector available. A heartbeat with a counter turns silence into evidence.

### 5.4 Nothing dies quietly

Four hooks, each emitting a terminal event before the process goes:

| Hook | Emits |
|---|---|
| `sys.excepthook` | `server.crashed` |
| `threading.excepthook` | `server.crashed` with `thread` set |
| `atexit` | `server.stopping` with `reason: atexit` |
| `SIGTERM` / `SIGINT` handler | `server.stopping` with `signal` |

`threading.excepthook` matters most here. `camera_worker` runs as a daemon thread (`:592`) and an unhandled exception in it currently prints a traceback and vanishes, while `serve_forever` carries on serving `/config` as though the camera were fine.

The SIGTERM handler also gives you clean attribution on `systemctl restart` and on OOM kills, which is otherwise indistinguishable from a crash.

---

## 6. Cost budget

The whole point of this project is the ~2.7% CPU figure. Observability must not move it.

| Source | Rate | Estimated cost |
|---|---|---|
| `system.sample` | 0.1 Hz | negligible |
| `health.tick` + reconciler | 0.1 Hz | two small procfs reads, negligible |
| `http.request` | human-driven, bursty | negligible |
| `state.changed`, `camera.*` | human-driven | negligible |
| `camera.stderr` | **unbounded, the risk** | see below |

`camera.stderr` is the only unbounded source. Mitigation: cap at 20 lines per generation plus 1 line per 5 seconds thereafter, and emit `log.suppressed {count}` when the cap engages so suppression is itself a tracked fact rather than a silent hole.

Target: under 0.2% CPU and under 2 MB RSS for the entire logging layer. Measured in P8, not assumed.

---

## 7. Implementation gotchas

**Draining stderr is mandatory.** Setting `stderr=subprocess.PIPE` on the `rpicam-vid` Popen without a thread continuously reading it will fill the 64KB pipe buffer, block `rpicam-vid` on write, and freeze the camera. This is a hard requirement, not a nicety. One reader thread per generation, joined on exit.

**Emit must never block on `camera_lock`.** That lock is held across `save_state()` file I/O (`:452`, `:468`, and five more). Give the seq counter its own lock and keep `emit()` free of any other lock acquisition.

**`print()` to a systemd-captured stdout is line-buffered at best.** The unit already passes `-u` for unbuffered, which is correct and must be preserved. Verify it survives any unit-file changes in P6.

**Do not log `argv` blindly once auth lands.** P3 introduces a token (M10). Redact it in `camera.launched` and `http.request.query` before it reaches the journal.

**`snapshot.failed.stderr` may be large.** Truncate to 2KB.

---

## 8. Phasing

Deliberately ordered so each phase is independently shippable and each one pays for itself.

| Phase | Content | Risk | Value |
|---|---|---|---|
| **O0** | `emit()`, `run` id, `seq` counter, replace the 21 existing `print()` calls | None. No behaviour change. | Structured baseline |
| **O1** | `cause` plumbing, `gen` counter, `apply_change()` choke point | Low, touches the seven `/set_*` branches | Causality |
| **O2** | `rpicam-vid` stderr capture + reader thread, exit code and signal decoding, `snapshot.failed.stderr` | Medium, pipe deadlock is the hazard | **Highest.** Closes B1, B2, B3 |
| **O3** | Reconciler thread, `health.drift`, `health.tick`, `vcgencmd get_throttled` | Low, read-only probes | Unknown-unknowns |
| **O4** | Excepthooks, signal handlers, `camera.crash_loop`, `log.suppressed` | Low | No silent deaths |

O0 and O1 belong in one commit. They touch the same call sites and splitting them means editing `handle_request` twice.

**Recommended interleave with `IMPLEMENTATION-PLAN.md`:** run O0 to O2 *before* its Phase 1 fan-out rewrite. Rewriting the socket layer without exit-code visibility means debugging the rewrite blind. The fan-out is the largest single change in that plan and it is exactly where you want instrumentation already in place.

---

## 9. Open questions

1. **Dashboard log pane.** Deferred above. If journald-only proves painful in practice, the cheapest fix is a 500-event `collections.deque` and a `/events` endpoint. Roughly 40 lines. Decide after living with O0 to O2 for a week.
2. **`http.request` volume.** The dashboard polls `/stats`. At a 2s poll that is 43,200 `http.request` events per day, which will hit journald rate limits and drown everything else. Options: exclude `/stats` and `/config` from `http.request`, or log them at a 1-in-30 sample. Leaning toward excluding both and letting `health.tick` carry the liveness signal instead.
3. **Clock accuracy.** The Zero 2W has no RTC. Before NTP syncs, `ts` is wrong, possibly by years, which corrupts any time-based reading of the log. Proposal: `server.started` records `ntp_synced` from `timedatectl`, and every event carries `mono` (a `time.monotonic()` float) alongside `ts` so ordering stays valid even when wall-clock time is not.

---

## 10. What I need from you

- Sign-off on the closed event vocabulary in section 4. Adding types later is fine, but the point of a closed list is that it stays deliberate.
- A decision on open question 2. It changes O0.
- Confirmation that O0 to O2 should land before `IMPLEMENTATION-PLAN.md` Phase 1, per section 8.
- Device checks from section 0 run over SSH before any of this is written: journald persistence and rate-limit settings. If persistence is off, everything here evaporates on reboot.
