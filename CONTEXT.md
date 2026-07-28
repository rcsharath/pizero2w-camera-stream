# pizero2w-camera-stream

HTTP control server and hardware H.264 video engine for a fixed camera on a Raspberry Pi Zero 2W. Manages camera hardware state (resolution, exposure, orientation, crop) and reports its own health.

## Language

### State and change

**State** (`config`): the current settings for the camera and stream. One Python dict, persisted to disk, the single source of truth for what the camera is doing right now.
_Avoid_: settings, options

**Choke point**: the rule that every state-mutating code path funnels through one function, so logging and restart-triggering can never be forgotten when a new endpoint is added. `apply_change` is the choke point for config fields; `request_restart` is the choke point for the camera process.
_Avoid_: handler, setter

**Generation** (`gen`): the lifetime counter for one running `rpicam-vid` process. Increments every time the camera process restarts (e.g. after a config change). Events from an old process vs a new one are told apart by `gen`, never by timestamp.
_Avoid_: session, instance, run

**Run** (`run`): a 6-hex-char ID minted once when the server process starts. Makes `seq` unique across service restarts. Not the same thing as a camera generation.
_Avoid_: session, instance

**Sequence** (`seq`): a process-wide monotonic counter, one per emitted event. The identity of an event.

**Cause** (`cause`): the `seq` of the event that caused this one. Causality is tracked by reference to a `seq`, never by comparing timestamps.

**Reconciler**: a background loop that compares believed camera state against OS-level fact (`/proc`) and corrects drift. Exists because belief ("the camera is generation 4 and running") can silently diverge from reality.

**Closed vocabulary** (event catalog): the fixed, deliberately-maintained list of `ev` values a log event can carry. Adding a new event type is a deliberate act (documented in OBSERVABILITY.md), never a byproduct of adding a `print()`.

### Camera hardware

**Mode**: one of `day`, `night_indoor`, `night_outdoor`. A named bundle of exposure defaults (shutter, gain, AWB).
_Avoid_: preset (fine in UI copy, but code and docs should say mode)

**Manual override** (shutter / gain): an explicit numeric value that fully disables auto-exposure for that axis. Different from EV compensation, which biases auto-exposure without disabling it.

**EV compensation**: floating-point auto-exposure bias. Only affects an axis (shutter or gain) still under auto-exposure control. Has no visible effect if both shutter and gain are manually overridden.

**Metering**: which region of the frame auto-exposure measures from (`centre`, `spot`, `average`). Like EV, only affects axes still under auto-exposure.

**ROI** (crop): hardware region-of-interest crop applied in the VideoCore GPU ISP, expressed as `x`/`y`/`w`/`h` fractions (0.0-1.0) of the sensor. Stored as fractions so it survives a resolution change.
_Avoid_: zoom, viewport

**Throttled flags**: bitflags from `vcgencmd get_throttled` reporting undervoltage/thermal throttling now vs since boot. Primary signal for "camera died" being a hardware power issue, not a software bug.

**Snapshot**: a single on-demand full-resolution (5MP) still JPEG capture. A different pipeline from the live video stream, not a frame grabbed from it.

## Example dialogue

"Should the crop reset when I change resolution?"
"No. ROI is stored as fractions and re-aspected automatically, it survives a resolution change."

"I set the shutter manually but left gain on auto in `night_outdoor`. Why does EV still do something?"
"Because gain is still under AE. EV and metering affect whichever axis auto-exposure still controls, shutter won't move but gain will."

"The stream dropped after I changed fps, was that my change or something else?"
"Check `cause` on the `camera.exited` event. If it points back to the `seq` of your `state.changed` fps event, it was you."
