# pizero2w-camera-stream (AGENTS.md)

Camera control server and hardware H.264 stream engine for a Pi Zero 2W. See README.md for what it does, DEPLOY.md for how to ship it.

## Before making domain-level changes

Read `CONTEXT.md` first. It's the glossary: state, generation, sequence/cause, reconciler, choke point, mode, ROI, etc. Use those terms, don't invent new ones for the same concept.

If you introduce a new domain concept, or a term gets used in a way that conflicts with `CONTEXT.md`, stop and resolve it there before writing code. Update `CONTEXT.md` inline when a term is resolved, don't batch it.

## Architecture decisions

`docs/adr/` holds decisions that are hard to reverse, non-obvious, and the result of a real trade-off (journald-only logging, seq-based causality, the choke-point pattern, GPU-side ROI vs software crop). Check there before "fixing" something that looks wrong but was deliberate. Created lazily, doesn't exist until the first ADR is written.

## Docs map

| File | What's in it |
|---|---|
| `CONTEXT.md` | Domain glossary. Read first. |
| `docs/adr/` | Hard-to-reverse decisions and why. |
| `README.md` | Features, API reference, quick start. |
| `IMPLEMENTATION-PLAN.md` | Phased build plan (P0-P8). |
| `OBSERVABILITY.md` | Logging/event design in detail. |
| `DEPLOY.md` | Shipping to the Pi. |
