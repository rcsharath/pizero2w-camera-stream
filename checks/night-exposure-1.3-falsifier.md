# Phase 1 Falsifier Review: Night exposure controls

Reviewed Objective and `checks/night-exposure-1.1-approach.md`.

## Objections

1. **Shutter duration exceeding frame period on FPS change.** If `current_shutter` is set to 200,000 µs (0.2s) while running at 5 FPS (frame period 200,000 µs), and the user subsequently changes FPS to 30 FPS (frame period 33,333 µs), passing `--shutter 200000` to `rpicam-vid` at 30 FPS will violate frame period bounds and may cause framerate drop or process error if not automatically re-clamped during FPS changes.
2. **Gain value exceeding OV5647 hardware ceiling.** Setting manual gain to an unverified upper bound like 12.0 might cause `rpicam-vid` to exit with a non-zero status or fail camera initialization if OV5647 or libcamera driver clamps/rejects gains above 8.0.
3. **Inert EV/Metering user expectation failure.** When both manual shutter and manual gain are active, EV compensation and Metering sliders remain interactive on the dashboard but produce no visible change in camera output, leading users to report UI bugs.
