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

- Checked the existing Python 3.12 environment and frozen uv lock (37 packages); `start.ps1 -CheckOnly` succeeds without reinstalling packages. The desktop shortcut points to this checkout's pythonw and launcher.
- Launcher preflight reports a missing environment or local model; Tcl/Tk discovery uses the actual Python installation when Windows exposes an invalid alias. The CMD and PowerShell launch paths use the same guarded entry point.
- Validation: 29 launcher and Tk runtime unit tests passed. No dependencies or models were downloaded.
- Next: finish overlay, inspector and replay checkpoints; repeat full regression and Tk smoke.

## Mini Mesa window and capture checkpoint

- A permanent drag bar, position lock and opacity controls replace the edit-only drag. Position and lock are persisted through the GUI integration; a five-pixel threshold separates drags from card selection. Negative monitor coordinates and partial monitor overlap are handled.
- The canvas reuses items, avoids redundant native ShowWindow/topmost calls, and uses a verified Windows capture exclusion for MSS. Unsupported capture paths hide and restore on the Tk thread; external PrintWindow sources are independent of this window.
- Validation: 236 overlay, mode and GUI sections tests passed, including real Tk capture exclusion with synthetic pixels. Full continuous live capture and physical multi-monitor movement remain unmeasured.
- Next: finish the registry inspector and complete the original-code replay comparison.

## Identity corrections and inspection integration

- Main table and Mini Mesa route card selection to the engine by physical ID; the picker stays above the overlay and can manually confirm a pending reading. The inspector exposes confidence, valid observations, states, times and bounded transition events.
- Current-shoe history can be corrected with its original context. Omitted cards require an explicit native-image location in a calibrated region; ambiguous associations remain uncertain. Later detector observations cannot override a manual rank or unknown suit.
- Fixed source identity serialization after SQLite restore and kept late-suit observations consistent with the existing counter contribution. Hi-Lo and shoe composition changes remain atomic.
- Validation: 196 related presentation/identity tests, 109 memory/correction tests, and 26 real-Tk smoke checks with synthetic capture passed. SQLite round-trip and historical correction were tested. Full regression is running; live accuracy remains unverified.
- Recorded-video replay found 23/37 counted events and final RC +1 on the current pipeline, compared with 37/37 and RC 0 in an older report. A repeat from this mission starting revision is running because the pipelines differ.

## Full regression and recorded video comparison

- Complete tests/: 1573 passed and 37 subtests passed; zero failures or skips. An earlier run exposed four stale test fixtures, which were updated to reflect the actual memory and lock contracts.
- The exact 113-frame video replay using local ONNX and the same calibration/schedule produced 20/22 then 3/15 counted events and final RC +1 on both the starting revision and the stabilized working code. A much older report recorded 22/22 and 15/15, RC 0, using a different inference pipeline.
- Numeric trace: during the empty interval a below-threshold 4 box fills almost the entire mine crop and repeatedly prevents round clearance; second-hand detections are then associated with the prior round. This was already present at the starting revision. No confidence or geometry rule was changed based on this one recording. Use manual New Round if necessary and treat live accuracy as unverified.
- Next: finish native input-event smoke, publish the GUI/registry checkpoint and video replay repair; remove only task-owned temporary worktrees and test directories.

## Replay source boundary

- Fixed the local replay tool after its normal live `set_monitor` path correctly rejected the saved video calibration. Replay now has a separate binding path that requires an exact-resolution official layout explicitly reviewed on the same video, sets its source provenance to video, and never calls the live monitor gate. It does not substitute the old seven-seat layout.
- Validation: 3 replay contract tests reject missing/wrong source references and verify official-mode setup; 15 registry/replay tests pass. The 113-frame synthetic/user-video replay completed, but remained 23/37 and RC +1, matching the same result at the mission baseline.
