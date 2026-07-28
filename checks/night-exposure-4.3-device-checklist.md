# On-Device Physical Verification Checklist: Night Exposure Controls

Sharath: Please complete these two steps on the physical Raspberry Pi Zero 2W after deploying the new `stream_server.py` and `static/` files.

1. **Does the analogue gain clamp of `[1.0, 12.0]` hold on real hardware above 8.0?**
   - **Action:** Switch lighting mode to `Night Outdoor`. Set manual gain to `10.0`, then `12.0` via the dashboard or `/set_exposure?gain=10.0`.
   - **Expected observation:** The video feed brightens without camera crash, or if libcamera / OV5647 hardware cannot accept gain > 8.0, `camera.launch_failed` or `camera.exited` appears in `journalctl --user -u camerastream -n 30`.
   - **Record observation here:** __________________________________________________

2. **Does `--roi` change what the AEC/AGC metering samples from?**
   - **Action:** In `Night Outdoor` or `Night Indoor` mode with auto shutter or gain enabled, apply an active crop (e.g. 50% width/height centered on a bright or dark spot). Toggle metering mode between `centre`, `spot`, and `average`.
   - **Expected observation:** Observe whether metering adjustments evaluate brightness relative to the cropped area or the full sensor frame.
   - **Record observation here:** __________________________________________________
