# Counter vision iterations

Acceptance criteria are fixed by AGENT_BRIEF. Synthetic tests do not establish live accuracy.

| Iteration | Hypothesis / change | Before | After | Decision |
|---|---|---|---|---|
| 0 | Reproducible Python 3.12 and environment-only credentials | Python 3.14, author keys embedded | Python 3.12.15; credentials excluded from source and git | Keep. API authentication still returns 401; no image transmitted. |
| 1 | A privacy-first recorder and uniform adapters make later comparisons reproducible | Single-screen anecdotal tests | Pending controlled tests; no holdout dataset yet | Infrastructure only; no model promotion. |

## Infrastructure iteration 2 — original authorized artifacts

The latest private environment credential passed HTTP 200 for both original
models. No image was transmitted. Original weights: player 12,378,339 bytes,
rank 103,617,142 bytes. Numeric rank placeholders required the authorized
CLASS_MAP sidecar; player resize is Stretch to, dealer Fit within/black border.

The pinned Python 3.12 environment excludes duplicate opencv-headless and does
not install PyTorch. Hosted calls fail before network upload without a reviewed,
masked table ROI. The legacy training crop saver defaults off. Windows OCR now
uses the installed es-MX engine instead of failing on missing English OCR.
Privacy/adapter/counting/OCR/validation/shoe suite: 53 passed, 0 failed.

Thirty frames from the repository's pre-existing ten-second clip were imported
as **diagnostic**, with HUD masks. This is not the live holdout. The two original
models emitted 124 / 214 boxes across those frames; these are repeated raw
observations, not correct or unique cards. Reviewed labels: zero. Accuracy: N/A.
Original player inference p95: 300 ms; original rank p95: 1,303 ms. Full app
cycle/soak remain unmeasured. Phase 0 is paused: no current approved table ROI.

The three previously downloaded generic models were re-run on the same 30
diagnostic ROIs using 640-native-pixel tiling, 25% overlap, 2x resampling and
same-code pip grouping (140 px). All emitted zero >=0.5-confidence boxes.
This is evidence about this clip/configuration, not a universal rejection of
those models or a conclusion about the user's current table.

## Counting iteration 1 — temporal confirmations (synthetic only)

Measure: `python -m tools.simulate_shoe --seeds 30 --consensus .8`.
Same seeded 8-deck shoes, 10 views per round; misses 10%, rank confusions 3%,
duplicate boxes 15%, 0.8 px jitter. 780 rounds per condition.

Hypothesis: three observations reject isolated rank noise better than one.
End-round running accuracy: 18.846% -> 52.436%; mean absolute end-shoe drift:
1.7 -> 0.9. Duplicate events: 0 in both. Keep three confirmations in candidate.
Unknown origin/ambiguous tracks are flagged; a cutting-card sighting alone
cannot reset the shoe. Three manual reset cycles passed. No live claim.

## Counting iteration 2 — consensus fraction (synthetic only)

Measure: same command/seeds with `--consensus .7`; confirmations remain 3.
Diagnosis: candidate .8 leaves on average 1.8 cards uncommitted per 416-card
shoe because a few noisy observations defeat the vote despite a correct
majority. Hypothesis: .7 reduces those missed commitments while keeping the
three-observation minimum and spatial duplicate guard.

End-round running accuracy: 52.436% -> 90.000%; mean absolute final drift:
0.9 -> 0.2. Mean cards short: 1.8 -> 0.233; duplicate events: 0.
Keep .7 as experimental candidate. It still fails the 95% criterion even on
this simulation. Do not lower it further to force approval; obtain real replay
labels and diagnose unknowns/motion. Production engine has not been replaced.

## Detection/latency iteration 1 — S8S8 QDQ

Single hypothesis: quantize the original range model's Conv nodes with static
S8S8 per-channel QDQ to reduce CPU latency. Calibration: only the historical
diagnostic clip, never a future live holdout. Run the same 30-frame manifest,
confidence .55 and NMS .45. Size: 103.6 MB -> 26.5 MB.

Detector p95: 1,302.87 -> 1,746.87 ms; worse. Raw boxes: 214 -> 212,
whose correctness is unknown. Reject this variant as the speed candidate;
retain its private artifact/evidence, do not activate it. Accuracy is unscored.

## Detection/latency iteration 2 — AVX2 U8S8 reduced range

Diagnosis: S8S8 conversion overhead on this CPU outweighed arithmetic savings.
Hypothesis: U8S8 with reduced-range per-channel Conv quantization is a better
AVX2 candidate. Rationale: the ONNX Runtime quantization documentation describes
reduced range for AVX2/non-VNNI saturation handling; performance remains measured,
not assumed: https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html

Same source/calibration and 30-frame replay, .55 confidence, .45 NMS. Initial
bounded-calibration option hit ORT's `No data is collected` behavior; removed
that option after inspecting the installed collector. An explicit 1.3 GB RSS
guard remains, and all calibration/inference sessions are capped at 2 threads.

Detector p95: FP32 1,302.87 -> U8S8 766.87 ms; CPU 16.30% of machine; peak
sampled RSS 307.25 MB; artifact 26,531,567 bytes. Raw boxes 214 -> 215 does not
establish recall. Keep U8S8 **only as a candidate awaiting accuracy labels**.
The complete live-cycle p95, app memory/CPU and soak are not measured.

## Baseline fidelity correction

Expose NMS IoU explicitly. Original players `overlap=100` means IoU 1.0 and
no suppression; its exact .70/1.0 run emitted 1,054 raw boxes, p95 299 ms.
The previously recorded .70/.45 candidate emitted 124 boxes, p95 300 ms.
These are separate named runs, neither with correctness labels. No silent
threshold change and no accuracy claim. Sidecar/class-map/privacy/Hi-Lo/shoe/
OCR/validation suite now reports 54 passing tests; live holdout remains absent.

## Recorder verification checkpoint

58 tests now pass. Added a fake-mss/clock recording lifecycle test: unchanged
frames are skipped, only ROI coordinates are requested, masks precede return,
occluded-anchor frames are not persisted, session names cannot escape data/,
and the Windows keep-awake state is restored. No desktop data was used by
these tests. Real recording remains blocked by an unapproved current ROI.

## Counting iteration 3 — bounded vote history (synthetic only)

Single hypothesis: limit votes to the last 5 observations so old outliers do
not indefinitely block a later stable card. Keep confirmations=3, consensus=.7,
confidence=.5, geometry/duplicates unchanged. The committed-card health flag
uses the same consensus instead of treating one late outlier as a contradiction.
No automatic recount or rank correction is introduced.

Same 30 seeds / 780 rounds / exact generated detections as iteration 2.
Running-count round accuracy: 90.0000% -> 94.7436% (739/780); mean absolute
end-shoe drift: 0.2000 -> 0.0667; duplicate events 0. Keep the improvement as
an experimental candidate. **Still below 95%; no rounding to pass.**

61 tests pass, including stale-outlier expiry, isolated post-lock noise and
sustained contradictions becoming unknown without a second count. New data
with real motion, stable card IDs and human-reviewed ranges is required before
further tuning or production integration. Evidence: count_simulation_window5.json.

## User-directed functional handoff

The user replaced the acceptance target with: make the app open and its UI
features work; they will evaluate model/recognition quality. The earlier
scientific accuracy/soak gates remain unverified and no longer block this handoff.

Installed the original 13-rank U8S8 model as player_cards.onnx (with its class/
preprocessing sidecar); previous weights and settings are backed up privately.
Use the original rank detector's .55 confidence and .45 NMS, CPU 2 threads.
Added suit-neutral rank images. Local-only setups avoid a useless remote
cutting-card request; New Shoe remains manual. Bounded predeal worker count
to one, disabled the expensive exact pre-deal sweep in this user's profile,
and shut down app worker pools on close. Executor remains Off.

Functional test: 26 real Tk button/callback workflows passed with no callback
errors in an isolated profile and synthetic capture. Includes calibration
save paths, settings save/apply, CSV export, bounded Monte Carlo, practice
deck, HUD, manual cards/dealer/split, resets, preview and local detection
start/stop. The OS click helper was unavailable; these are programmatic Tk
interaction tests, not a claim of physical Windows clicks. 62 regression
tests pass. Recognition accuracy was deliberately not assessed here.


## Window capture ? user requested application selection
Hypothesis: desktop coordinates cause OCR calibration to drift when a target
window moves or gets covered. Add a read-only client-area PrintWindow source
alongside mss monitor capture, selected from a refreshable Window dropdown.
All calibration, preview and detection reuse ScreenCapture and window-local
coordinates. Normalize resized frames to the selected client size; document
that page reflow/zoom requires recalibration. No desktop fallback, activation,
input, refresh or executor change. Minimized/closed/black sources report errors.

Evidence: before, monitor capture only. After, native test-owned window capture
is pixel-identical after moving and covering, preserves normalized landmarks
on resize, rejects minimized/closed sources, and never falls back to desktop.
64 targeted regression tests pass; 27 isolated real Tk functional workflows
pass (0 callback/error dialogs), including Window selection, refresh/empty list,
OCR client dimensions and switching back to Monitor. Recognition quality of
casino video and compatibility of every accelerated renderer remain unverified.
Decision: keep. Native borders can change antialiasing on resize; the resize
test checks calibrated interior landmarks rather than requiring edge identity.


## Application enumeration fix ? no interface tests requested
Diagnosis from read-only enumeration: VirtualBox Manager is visible/minimized
but has a 0x0 client area; the dimensions filter excluded it. Vcore9.2,
Obsidian and Zen were already enumerated. Retain minimized entries independent
of size, label with process/application plus window title and minimized status,
and require restoration before initializing capture geometry. Exclude shell
input infrastructure. Handle windows closing during enumeration.
Verification limited to enumeration, no interface tests/clicks/screenshots:
VirtualBox.exe, VirtualBoxVM.exe (Vcore9.2), Obsidian.exe, zen.exe all present;
VirtualBox Manager correctly marked minimized. Keep this targeted change.

## Local video calibration ? frame reader

Added a local-only frame reader with two decoder threads, native-size frozen frames, and named profile source references. No recognition, model settings, casino capture or remote calls. Verified 28 selected reader/profile checks using generated video and temporary profile stores. This is a calibration utility change, not a recognition accuracy result.

## Local video calibration ? user-chosen frame and regions

Reused RegionEditor and the profile store in a local video-frame picker. The user chooses the timestamp and draws seven seat regions plus dealer; frozen native-size geometry and frame reference are saved, without image files or inference. Verified 32 selected reader/editor/profile tests with generated video, isolated stores and network calls forbidden in UI tests. No screenshots were viewed for this verification. Existing user profiles, model parameters, executor settings and recognition logic were not changed.

## Local video calibration — three selectable layout modes

Hypothesis: table-wide counting needs an explicit Mesa polygon and ownership
zones without overwriting the existing seven-seat reference. Added the table
and single-seat modes to the same viewer/editor, with separate exact-resolution
profiles, native coordinates, optional polygon vertices, and persisted source
references. Reject missing, crossed, out-of-frame, overlapping or non-contained
zones before saving. The saved layout loader tags native-coordinate positions
without inference or counting; production detection and card memory are unchanged.

Before: new-mode checks could not load because the layout module did not exist.
After: 55 focused reader/editor/geometry/profile checks passed in 2.37 seconds,
using generated frames and isolated stores. Existing user profile and label bytes
were unchanged. OpenCV processing and the video decoder are capped at two threads.
No assistant image inspection, remote calls or recognition run. Decision: keep
the viewer change and stop for the user to draw zones. Card sizes, false positives,
dealer representation and all 37-card counting comparisons remain unmeasured.

## External dealer card graphic — correct the layout restriction

The user selects the casino's separate card graphic as the dealer source.
The containment rule rejected that legitimate layout; four geometry/editor
checks reproduced the rejection before the fix. Only Mi asiento must now be
inside Mesa. Crupier can be elsewhere in the frame, remains a distinct dealer
source when tagging positions, and does not enlarge Mesa or include the gap.
The editor explains that selecting the graphic requires excluding the physical
dealer cards from Mesa. This is configuration support, not measured deduplication.

After: 60 focused checks passed in 3.21 seconds, including external-dealer save,
reload and ownership in both new modes; out-of-frame and overlapping zones are
still rejected. User profile and label bytes were unchanged. No recognition,
remote requests or assistant image inspection. The running editor is left open
until restart approval so its unsaved drawing is not discarded automatically.
