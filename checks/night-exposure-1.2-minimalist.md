# Phase 1 Minimalist Review: Night exposure controls

Reviewed Objective and `checks/night-exposure-1.1-approach.md`.

## Objections

1. **EV compensation and Metering mode redundancy when shutter and gain are both set to manual.** When a user sets both manual shutter and manual gain, AEC/AGC is completely disabled in libcamera, making EV compensation and Metering mode 100% inert. If users primarily run night mode in full manual (fixed shutter + fixed gain), EV and Metering provide zero utility and add unnecessary UI/server clutter. However, when either shutter or gain remains automatic, EV and Metering do affect the auto-adjusting axis. Thus, after checking whether EV, metering, and denoise each earn their place, they do earn their place for hybrid manual/auto modes, provided their inert nature in full manual is clearly documented.
