# Phase 2 Falsifier Review: Server-side implementation

Reviewed Phase 2 Objective and `stream_server.py` changes.

## Objections

1. **Negative shutter duration boundary.** Passing a negative integer to `/set_exposure?shutter=-1000` must be rejected by `validate_shutter` rather than accepted or passed to `rpicam-vid`. Reproducing query string: `/set_exposure?shutter=-1000`.
2. **Invalid metering parameter rejection.** Passing an invalid metering mode such as `spotlight` must raise `ValueError` naming `metering` and return HTTP 400. Reproducing query string: `/set_exposure?metering=spotlight`.
3. **Invalid denoise mode rejection.** Passing an unsupported denoise mode like `ultra_hq` must raise `ValueError` naming `denoise` and return HTTP 400. Reproducing query string: `/set_exposure?denoise=ultra_hq`.
