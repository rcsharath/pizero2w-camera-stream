# Phase 3 Successor Review: Dashboard implementation

Reviewed Phase 3 Objective and `static/index.html`.

## Objections

1. **Slider drag behavior documentation for future maintainers.** `shutterMs`, `manualGain`, and `evSlider` update their label displays on `oninput` but do not call `applyNightExposure()`. A successor agent or human developer might attempt to wire `applyNightExposure()` directly to `oninput`, which would cause rapid `request_restart` events and thrash the `rpicam-vid` worker process. The explicit `Apply Night Exposure` button must remain the sole trigger.
2. **Gain naming convention rationale.** The slider label reads "Manual gain (auto if blank)", not "gain ceiling" or "max gain". A successor developer might assume this is a ceiling slider and rename it to "max gain". `rpicam-vid` `--gain` pins analogue+digital gain fixedly, so calling it a ceiling would mislead users.
