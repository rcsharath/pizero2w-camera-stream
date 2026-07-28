# Challenge O0-O4: Literalist Review of `implementation_plan.md`

**Reviewed:** Antigravity `implementation_plan.md` (observability code plan)
**Against:** `stream_server.py` @ `d2ad32a` (608 lines), `OBSERVABILITY.md` @ 2026-07-28
**Date:** 2026-07-28
**Method:** Literal read. Every claim checked against the line it cites.

Objective: identify (a) code in the plan that will not run as written, (b) code that runs but silently defeats the mechanism it implements, and (c) deviations from `OBSERVABILITY.md` that change behaviour rather than merely wording.

---

## A. Blockers, will not run

### A1. O0 crashes `camera_worker` on the first loop iteration

Plan item O0 #4 maps `stream_server.py:347` to:

```python
emit("camera.launched", gen=current_gen, pid=camera_process.pid, argv=cmd)
```

Line 347 executes **before** the `Popen` at line 351. On the first iteration `camera_process` is `None` (line 60), so `camera_process.pid` raises `AttributeError: 'NoneType' object has no attribute 'pid'`. `camera_worker` is a daemon thread with no exception handling until O4, so it dies silently at startup and the HTTP server keeps serving `/config` for a camera that never launched.

On every later iteration it reads the **previous** generation's PID, so even without the crash the field is wrong.

**Fix:** `camera.launched` cannot be a rename of line 347. It must be emitted after a successful `Popen`. Line 347 is a *pre*-launch fact; if you want it, it is `camera.launch_requested`, and it carries `argv` but no `pid`.

### A2. `apply_change()` deadlocks against the handlers

`camera_lock` is `threading.Lock()` (line 62), which is **not reentrant**. Every `/set_*` branch already holds it across validation and mutation (`:443`, `:463`, `:479`, `:494`, `:515`, `:542`). `apply_change()` is specified as "Applies state change under `camera_lock`" and opens with `with camera_lock:`.

The plan never says to remove the outer acquisition. Keep both and every single `/set_*` request hangs forever on the first call.

**Fix:** state the ownership explicitly, one of:

- `apply_change()` assumes the caller holds the lock (rename it `_apply_change_locked`), or
- handlers drop their `with camera_lock:` entirely and `apply_change` owns it, or
- `camera_lock = threading.RLock()`.

The second option has a consequence the plan does not address, see C1.

### A3. `current_gen += 1` without a `global` declaration

O2 adds `current_gen += 1` inside `camera_worker()`. Line 298 declares only:

```python
global camera_process, restart_requested
```

Assignment to `current_gen` without adding it makes `current_gen` a local, and reading it before assignment raises `UnboundLocalError` on the first pass.

**Fix:** `global camera_process, restart_requested, current_gen`. Same applies to `current_cause_seq` in `request_restart()`, which the plan does declare correctly.

### A4. `camera_thread` does not exist

O3's reconciler calls `camera_thread.is_alive()`. In `main()` the thread is bound to a local named `t` (line 592). There is no module-level `camera_thread`.

`NameError` on the first tick kills the reconciler thread. In O3, before the excepthooks land in O4, that death is silent. The thread built to detect dead threads dies undetected.

**Fix:** assign `global camera_thread` in `main()`. And land O4's `threading.excepthook` **before or with** O3, not after it.

### A5. O0's line-364 replacement references an undefined `pid`

O0 #7 maps line 364 to `emit("camera.exited", ..., pid=pid, ...)`. The `pid` local is introduced in O2. `NameError` in O0.

O0 is described as "None. No behaviour change." Three of its eight mapped replacements do not execute (A1, A5, and the `current_gen` read in A3). That characterisation is wrong as written.

---

## B. Runs, but defeats its own mechanism

### B1. `was_expected` is permanently `True` after the first config change

```python
was_expected = (restart_requested or (current_cause_seq is not None))
```

`current_cause_seq` is set in `request_restart()` and **never reset to `None`**. After the first `/set_*` call of the process lifetime it stays non-`None` forever.

Consequence: every subsequent camera death is logged `expected: true`, `health.unexplained` never fires, and `lvl` stays `info`. This nullifies B1/B2 from `OBSERVABILITY.md` section 1 and the entire section 5.2 alarm. It is the single most damaging bug in the plan, because the log will look healthy while reporting nothing.

It is also read outside `camera_lock`, racing `camera_worker`'s reset at line 310.

**Fix:** do not infer intent from global state. Capture the reason at the branch point inside the inner loop, where it is unambiguous:

```python
while True:
    time.sleep(0.5)
    with camera_lock:
        if restart_requested:
            break_reason, cause = "restart_requested", current_cause_seq
            break
        if camera_process.poll() is not None:
            break_reason, cause = "process_died", None
            break
```

Then `expected = (break_reason == "restart_requested")`. No global inspection, no staleness, no race.

### B2. The reconciler false-positives on every config change

O3 compares `/proc/<pid>/cmdline` against **live globals** (`current_res_key`). After any `/set_*`, the globals update immediately while the old `rpicam-vid` keeps running for up to ~3 seconds (`terminate()`, `wait(timeout=2)`, `time.sleep(1)` at `:376`).

A reconciler tick landing in that window emits `health.drift` at `lvl: error` for a completely normal restart. Every config change produces a spurious error event. Within a week you will filter `health.drift` out of your queries, and the mechanism is dead.

Note this is also a deviation: `OBSERVABILITY.md` section 5.1 says compare against "`argv[]` from last `camera.launched`."

**Fix:** keep comparing against live globals, that is the check with real value (it catches the swallowed-restart race), but gate it:

```python
if (not restart_requested
        and camera_alive
        and (time.monotonic() - gen_start_time) > 5.0):
    # ...then compare /proc/cmdline against current globals
```

Also compare the **full** argv, not just `--width`. A swallowed `--framerate`, `--roi`, or `--shutter` is exactly as invisible and more likely.

### B3. `vcgencmd` is wired into the 2-second polling path

The plan puts `get_throttled_flags()` "In `get_system_stats()`". `get_system_stats()` is the `/stats` handler (`:437`), which the dashboard polls every 2 seconds, per open question 2 in the plan's own section 1.

That forks `vcgencmd` roughly 43,000 times a day, per connected dashboard. On a Zero 2W each fork-exec is on the order of 5 to 15 ms of CPU. Against the stated 0.2% budget for the entire logging layer, this one call plausibly exceeds it by itself.

`OBSERVABILITY.md` section 4 places `throttled` on `system.sample`, which is the 0.1 Hz reconciler path.

**Fix:** move it to `reconciler_worker()`. Consider sampling at 60 s rather than 10 s, since the `_since_boot` sticky bits do not need 10-second resolution. Verify the service user is in the `video` group or `vcgencmd` will fail on every call.

### B4. `system.sample` is never emitted

The plan defines `get_throttled_flags()` but no code path emits `system.sample`. Temperature, free RAM, and load average are therefore never written to the journal at all, only served over HTTP to a dashboard that does not persist them.

Consequence: "the camera died at 3am" cannot be correlated with "the SoC was at 82 degrees at 2:58am." That correlation is one of the main reasons to build this.

**Fix:** emit `system.sample` from the reconciler alongside `health.tick`.

### B5. `emit()` can raise and kill its caller

`json.dumps(payload, separators=...)` raises `TypeError` on any non-serialisable value. The plan passes `argv=cmd` (fine), `config_snapshot=current_state_dict()` (fine today), and `**kwargs` from arbitrary call sites (not fine, one `error=e` instead of `error=str(e)` is enough).

A logging subsystem that can crash the thread it observes is a liability, and here it would crash `camera_worker`.

**Fix:**

```python
try:
    json_str = json.dumps(payload, separators=(',', ':'), default=str)
except Exception:
    json_str = json.dumps({"ts": ts_str, "run": RUN_ID, "seq": seq_num,
                           "lvl": "error", "ev": "log.emit_failed",
                           "orig_ev": ev}, separators=(',', ':'))
```

Never let `emit()` propagate. `default=str` alone handles most of it.

### B6. Lines can interleave out of sequence

`emit()` releases `seq_lock` before writing to stdout. Threads holding seq 5 and 6 can write in the order 6, 5. Recoverable when parsing (you sort by `seq`), but it breaks naive `tail -f` reading and makes the section 5.3 gap detector harder to eyeball.

At this event rate the cost of holding one lock across increment-and-write is nil.

**Fix:** move the `write` + `flush` inside the `with seq_lock:` block.

---

## C. Deviations from `OBSERVABILITY.md`

### C1. The causal chain is flattened

`OBSERVABILITY.md` section P2 specifies a chain:

```
seq 411  http.request
seq 412  state.changed        cause 411
seq 413  camera.restart_req   cause 412
seq 414  camera.exited        cause 413
```

The plan produces a star, every event pointing at `req_seq`, and `apply_change()` calls `request_restart()` **before** `emit("state.changed")`, so the restart is logged as happening before the change that caused it. The plan's own verification output confirms this (seq 412 `camera.restart_requested`, seq 413 `state.changed`).

This matters concretely at `/set_awb` (`:513-537`), which mutates **three** fields in one request. Under the plan that becomes three `apply_change()` calls, each invoking `request_restart()`, producing **three** `camera.restart_requested` events for **one** actual restart, all pointing at the same `req_seq`, with no way to tell which field was responsible.

**Fix:** separate mutation from restart. `apply_change()` emits `state.changed` and returns its `seq`. The handler collects the seqs and calls `request_restart(cause=<last changed seq>)` **once**. Emit order becomes changed-then-restart, matching the spec.

This also resolves the open question in A2: if handlers keep the lock and call a `_locked` variant, validation, mutation, restart request, and `current_state_dict()` for the response all stay inside one atomic hold. That preserves two properties the current code has and the plan would lose:

- `validate_fps(fps_raw, current_res_key)` is currently atomic with the mutation. Split the lock and resolution can change between validating fps and applying it.
- The response body currently reflects exactly the state that was applied. Split the lock and the dashboard can be told a state that was already superseded, which is finding F6 all over again.

### C2. `http.request` cannot carry `status` or `dur_ms`

Spec fields are `method`, `path`, `query`, `client_ip`, `status`, `dur_ms`. The plan emits at request **start**, where neither exists.

**Fix:** reserve the seq at the start and emit at the end:

```python
def reserve_seq() -> int:
    global current_seq
    with seq_lock:
        current_seq += 1
        return current_seq
```

`emit()` gains an optional `seq=` parameter. Downstream events use the reserved number as `cause`; the `http.request` record itself lands after the handler returns, complete. Note this means the log is not strictly seq-ordered by write time, see B6, which is fine once you parse by `seq`.

### C3. `atexit` hook dropped

`OBSERVABILITY.md` section 5.4 specifies four hooks. The plan implements three, omitting `atexit` with no rationale.

Minor, but `sys.exit(0)` from `shutdown_handler` is precisely the path where `atexit` fires, and it is the cheapest way to catch interpreter-level exits that no signal or excepthook sees.

### C4. `state.save_failed` never wired

`save_state()`'s failure path at `:180` is not in the plan's replacement list. The spec defines `state.save_failed` at `lvl: error`. Silent persistence failure is exactly the M8-adjacent bug that makes settings mysteriously revert after reboot.

### C5. The `print()` inventory is wrong in both documents

The plan says "all 8 existing raw `print()` calls," inherited from `OBSERVABILITY.md`. **`grep -c "print(" stream_server.py` returns 21.**

Full list: 180, 189, 196, 200, 209, 215, 221, 227, 233, 239, 251, 253, 255, 261, 347, 353, 361, 364, 595, 598, 602.

The plan's mapping covers 15 of them (item 3 enumerates 209 through 261 but omits **253** and **255**, the two ROI sub-cases). Uncovered: **180, 200, 253, 255, 598, 602**.

`OBSERVABILITY.md` section 8 must be corrected too. This is an error in the spec, not the plan.

---

## D. Smaller items

| # | Item | Note |
|---|---|---|
| D1 | `check_port_listening()` | Called in O3, never defined. Spec says scan `/proc/net/tcp` for `:22B8` (8888 = 0x22B8, correct). Needs a stated implementation. |
| D2 | Reconciler calls `proc.poll()` | `Popen` is owned by `camera_worker`. CPython's `_internal_poll` uses a non-blocking `_waitpid_lock` acquire and returns `None` under contention, so the reconciler can read "alive" spuriously. Read `/proc/<pid>/stat` instead. It is ground truth, which is the whole philosophy of the check. |
| D3 | `config_snapshot` read outside the lock | O2 calls `current_state_dict()` after releasing `camera_lock`. A concurrent request can mutate globals in between, so the snapshot may not describe the config that was launched. Snapshot inside the hold. |
| D4 | Registering a `SIGINT` handler | Makes the existing `except KeyboardInterrupt` at `:601` dead code. Remove it or the shutdown path forks confusingly. |
| D5 | `handle_uncaught_exception` swallows the traceback | Does not chain to `sys.__excepthook__`, so nothing reaches stderr and the exit code stays 0. systemd will not see a failure. Chain it, or `os._exit(1)` after emitting. |
| D6 | Verification example is wrong | The expected chain shows `camera.exited` with `exit_code: 0, signal: null`. That exit comes from `terminate()` at `:370`, so `returncode` is `-15` and the correct record is `exit_code: null, signal: "SIGTERM"`. As written this would be read as a test failure. |
| D7 | Unit test 2 | "contiguous from 1 to 1000" assumes no prior emits. `server.started` and `state.loaded` fire at import. Snapshot the starting seq first. |
| D8 | `req_seq` is undefined for skipped paths | `/stats` and `/config` are excluded from `http.request`, so `req_seq` must be explicitly `None` on those branches, and `emit()` already drops `cause=None`. Just needs stating. |
| D9 | `import signal` / `import traceback` inside functions | Harmless (cached), but move to module top for consistency with the existing import block. |

---

## E. What the plan gets right

Recording these so they are not re-litigated:

- Both open questions from `OBSERVABILITY.md` section 9 resolved, and resolved correctly. Excluding `/stats` and `/config` from `http.request` is the right call; adding `mono` alongside `ts` is the right call for a board with no RTC.
- `drain_stderr` using `iter(proc.stderr.readline, b'')` on a dedicated daemon thread is the correct shape and genuinely avoids the pipe-buffer deadlock flagged in `OBSERVABILITY.md` section 7.
- Signal decoding via `signal.Signals(-returncode).name` with a `ValueError` fallback is correct.
- The `vcgencmd get_throttled` bit masks are all correct: `0x1`, `0x2`, `0x4`, `0x10000`, `0x20000`, `0x40000`.
- The snapshot rewrite (`Popen` + `communicate()`, capture stderr, truncate to 2048) correctly fixes B3 and will immediately expose the C5 device-busy failure.
- Phase ordering, O0 through O2 before the `IMPLEMENTATION-PLAN.md` Phase 1 fan-out, is right.
- Check 3 (`kill -9 $(pgrep rpicam-vid)`) is a genuinely good acceptance test. It is also the test that would have caught B1.

---

## F. Required changes before implementation

Ordered by severity.

1. **A1** Move `camera.launched` after the `Popen`. Split out `camera.launch_requested` for line 347.
2. **A2** State lock ownership for `apply_change()` explicitly. Recommend caller-holds-lock, per C1.
3. **B1** Replace the `was_expected` inference with a `break_reason` captured at the branch point.
4. **A3, A4, A5** Fix the `global` declaration, promote `camera_thread` to module scope, remove the O0 `pid` reference.
5. **C1** Separate `apply_change()` from `request_restart()`. One restart per request. Emit `state.changed` before `camera.restart_requested` and chain `cause`.
6. **B5** Make `emit()` unable to raise.
7. **B2** Gate the drift check on `not restart_requested` and generation age > 5 s. Compare full argv.
8. **B3, B4** Move `vcgencmd` to the reconciler. Emit `system.sample`.
9. **C2** Add `reserve_seq()` so `http.request` can carry `status` and `dur_ms`.
10. **C5** Correct the `print()` inventory to 21 in both documents. Add the six uncovered lines.
11. **D6** Fix the verification example to `exit_code: null, signal: "SIGTERM"`.

Items 1 through 6 are prerequisites for O0 to O2 running at all. Items 7 through 9 are prerequisites for O3 being worth having.
