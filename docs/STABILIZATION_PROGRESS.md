# Functional stabilization progress

- Branch: counter-vision
- Starting revision: edfdb55940c5746f828b8c1f2ed1792618056d8c
- Baseline: clean worktree; full tests passed: 1540 tests and 37 subtests, no skips.
- Live path: calibrated source -> native ROI inference -> CardEvidence -> CardMemory -> CardCounter -> shared TablePresentation -> main table and Mini Mesa.
- Audit findings: CaptureGuard currently hides/restores the overlay on every capture; movement requires editing mode; CardMemory clears physical records at round end; the picker does not explicitly rise above the topmost overlay; no usable history/omitted-card workflow exists. Current identity corrections and Hi-Lo arithmetic are already implemented and will be reused.
- Completed: baseline audit and regression.
- Active: overlay/window repair delegated to one agent; principal implementing shoe history, state instrumentation, manual add/history correction and GUI integration.
- Ownership: overlay agent only round_overlay.py, capture_guard.py and overlay tests. Principal owns engine/memory/picker/main GUI and integration tests. Installer work follows when the agent slot becomes available.
- Constraints: casino/browser/VM read-only, executor off, inference local, no model replacement, no private frames persisted. Existing user data preserved.
- Next: validate and checkpoint each completed implementation stage; run source replays when available, full regression and real-Tk smoke before handoff.
- Accuracy/performance: real live precision and soak criteria remain unmeasured.

## Registry checkpoint

- Implemented persistent in-shoe card archive, context-bound historical correction, manual addition from an explicit native bounding box, and bounded per-card transition events/confidence/confirmation timestamps. Active stale selections still reject after round changes.
- Counter contribution replacement is atomic under one counter lock; no Tk widget computes Hi-Lo. New-round archive survives state restore and source reconfiguration; New Shoe clears the historical correction space.
- Tests: 132 related engine/memory/counting tests passed, including randomized repeated corrections, suit composition, historical pending confirmation and false-positive retraction, restore without recount, and manual-add deduplication.
- Next: connect the inspector/history/manual-location picker to the main GUI, then integrate overlay changes.

## Startup and installation checkpoint

- Checked the existing Python 3.12 environment and frozen uv lock (37 packages); `start.ps1 -CheckOnly` succeeds without reinstalling packages. The desktop shortcut points to this checkout?s pythonw and launcher.
- Launcher preflight reports a missing environment or local model; Tcl/Tk discovery uses the actual Python installation when Windows exposes an invalid alias. The CMD and PowerShell launch paths use the same guarded entry point.
- Validation: 29 launcher and Tk runtime unit tests passed. No dependencies or models were downloaded.
- Next: finish overlay, inspector and replay checkpoints; repeat full regression and Tk smoke.
