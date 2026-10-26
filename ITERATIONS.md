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

## Production-path local video replay — measurement plumbing

Added explicit provider injection, optional session persistence and exact named
seven-seat profile selection to DetectionEngine. Existing defaults are unchanged.
The replay tool uses the production capture/diff/inference/evidence/counting/
snapshot path with the actual local ONNX model and real-time video position.
Financial EV, OCR, phase/anchor detection, training and session writes are disabled
for this measurement; socket connections are blocked. Labels only delimit scoring,
never detection or round transitions. Only numeric traces are saved, no frames.
Nineteen focused replay/engine tests passed in 2.22 seconds with generated frames;
these verify plumbing, not recognition. The first real seven-seat run is separate.

## Separate table/dealer crops, same legacy memory

Added one local prediction over two native-pixel masked patches, compactly packed
without resizing them first. Dealer pixels are removed from the table patch when
the zones overlap, and mapped once to the original frame. The gap/account UI is
excluded. The controlled zones-only run retains the original seven-seat memory;
it intentionally does not claim that old seat limits have become global tracking.
Twenty-three focused plumbing/crop/regression tests passed in 2.52 seconds.
The replay now handles only a genuinely final undecodable frame as EOF, preserves
earlier decoder failures, blocks DNS and supports identical recorded frame schedules.
Real-video measurements are stored separately and are not test accuracy claims.

## Shoe uncertainty is persisted even with zero confirmed cards

CardCounter now retains structured unresolved issues and a shoe identity in its
serialized state. SessionStore must not discard a recent zero-card state when it
contains uncertainty. Two engine/SQLite checks failed before fixing the missing
zero-card save/load paths. A clean empty shoe is still ignored as before.
This does not change local recognition thresholds or the measured replay counts;
the video measurements disable all session persistence.

## User video — controlled three-mode comparison

Using saved profile `real2` at its exact 1280×720 video size, the marked seat
maps to J4/P4: the model counted 3/3 compatible ranks in round 1 and no cards
in J4 in round 2. Crupier uses the user's separate card graphic (1/4 to 2/4
compatible dealer ranks); the physical dealer cards were not compared. Typical
player detection boxes at model input grew from about 43×25 to 66×39 pixels,
without lowering the 55 confidence setting. The two new-zone runs each made
111 real model calls, returned identical detections and used the same 113 frames.
Capture lag beyond inference took at most 0.018 seconds. This verifies larger
input boxes, not sustained recognition accuracy.

Results: seven seats 17/22 (+3), then 9/15 (RC change 0); new zones with legacy
memory 19/22 (+4), then 12/15 (−1); new zones with the registry 21/22 (+5),
then 13/15 (−2). All ended RC +3 instead of 0. The registry produced 33 rank
and reference-seat occurrences compatible with labels, 4 missing occurrences,
and one extra 2 at J2. This is not an identity-verified score or a pass. Exact
first-visibility times remain unset. The extra 2 and missing 7 are in the same
seat; without inspecting the image, a rank error and a duplicate remain distinct
possibilities. The new trial reports uncertainty and cannot give clean advice.
Divisions, doubles, crossings, manual correction of an unprojected other seat,
stable false-positive sources, physical dealer recognition and live use remain
unverified. Simulated detections verify logic, not real-card reading.

The first incomplete replay was excluded: the decoder stopped at the video tail
and the result had incorrectly said `completed=true`; that end-of-stream record
was fixed. Final verification: 49 selected logic, layout, replay and SQLite
checks passed in an isolated temporary directory. User profiles, settings,
labels and session database bytes were unchanged. Passing software tests do not
establish card recognition accuracy.

## Count-state safety — stop/start and round boundaries

Two focused regressions failed before the change: a dealer-card picker opened
in one round could edit the next round, and New Shoe kept the old dealer hand
and extra cards in the advice projection after clearing the shoe count. Dealer
corrections now carry the picker-open round through the GUI and stale edits are
discarded. New Shoe clears the dealer hand/history projections. A rapid
Stop→Start now queues one restart until the current cycle releases capture.

Verification: 51 focused lifecycle, dealer, registry and memory checks passed
in 6.98 seconds using temporary output storage; the new blocked-cycle test saw
two sequential cycles and a maximum overlap of one. No app, casino, remote API,
image or model was used. Recognition quality and the full GUI callback flow
remain unverified. Keep these targeted guards; revert the individual commit if
a later lifecycle test shows a regression.

## Same-rank displacement — hold uncertain identity

A new synthetic regression failed: after a counted 8 disappeared and another
8 appeared 20 pixels away, rank and position matching silently reused the old
identity. The registry now marks the old and new candidates uncertain when a
same-rank box shifts beyond 35% of the smaller box dimension. This avoids a
clean wrong count but can create a manual-review case from genuine movement or
unusual detector jitter. Simulated crossing 8s kept the Hi-Lo count unchanged;
split-like movement remained uncertain; an occluded card reappeared without a
second count. Thirteen focused checks passed. These invented detections verify
state logic only. A card missed entirely by the detector still cannot be
flagged by memory, and the registry remains outside the normal live path.

## New Shoe — clear old advice and guard visible-card resets

A regression showed that New Shoe cleared the arithmetic but kept old player
cards in the advice projection. It now clears player/dealer round projections,
invalidates open card pickers by advancing the round, and marks the new shoe
uncertain if the reset happened while cards were still recorded on the table.
The registry retains old identities as retired so an immediate stale frame
cannot count them into the new shoe. This favors an explicit uncertainty over
a clean but potentially contaminated count.

Verification: 56 focused lifecycle, dealer, registry and memory checks passed
in 10.09 seconds with isolated temporary output and two OpenCV threads. This
does not establish visual recognition quality; the recorded-video count gate
still fails, so overlay/layout work remains postponed.

## Local-only and executor-off policy

Two new checks failed before the change: missing local weights constructed a
hosted backend, and the executor had no application-level disabled state.
The configured app now raises a local model error instead of falling through
to Roboflow, including refresh through the same builder. Its executor instance
cannot change from Off, arm or confirm; the mode selector is disabled. Local
ONNX and optional local PT weights are unchanged. Thirty-eight focused policy,
executor and refresh checks passed in 2.11 seconds with fake dispatchers and
temporary output. No network, browser, casino, capture or model inference ran.

## Additional card-memory simulations

Thirteen focused logic checks passed. When an extra card is reported with a
rank and a rotated bounding box, it is counted once. An unread extra leaves
shoe uncertainty after round clear. Two equal ranks crossing between ownership
zones leave Hi-Lo unchanged and withhold advice. Split-like movement still
remains ambiguous. These are invented detections; they do not establish that
the local model recognizes rotated or physical cards.

## Live profile selection fails closed

Three focused regressions failed before the change. With a non-default profile
selected, live detection silently loaded seven-seat defaults when only a new
layout existed; a video-origin legacy layout could be applied to a live monitor;
and Default fallback was not announced. The engine now rejects the first two
cases before changing capture source, while the GUI leaves calibration available
and Start disabled. Default's scaled fallback logs a warning. Four profile/UI
checks and sixteen card-memory checks passed with temporary stores. This does
not activate the new layout in live detection; its recorded-video count gate
still fails.

## Local-video memory diagnosis after the ambiguity guard

Same exact 113 scheduled frames and 111 local ONNX calls; all prediction lists
were identical to the earlier `table_registry_01` run. An overly broad guard
made `table_registry_02` count 32/37, RC +2. Treating one-frame candidates as
provisional gave `table_registry_03` 33/37, RC +2. Numeric trace review then
found a dealer-HUD 7 at (1177,190) being considered the same physical card as
a table 7 at (773,580). Restricting possible movement by source and distance
gave `table_registry_04` 34/37, RC +3. Its counted-event list exactly matches
the earlier 34/37 result. Sixteen card-memory checks pass, including distant
same ranks, rotated extras when a rank is supplied, unread extras, crossing and
occlusion. None of these software checks demonstrate real recognition.

The `real2` saved Mesa occupies y≈545–709 and the selected dealer HUD occupies
y≈163–240, disjoint in profile coordinates. The assistant did not inspect any
video image, so the location of physical dealer cards relative to Mesa is not
confirmed. The user-label gate is still 37 cards and final RC 0; it failed.
Do not switch live detection to the registry or proceed with overlay changes.

## Local-only boundary for remaining image services

The active model provider was already local-only, but direct Roboflow inference,
Anthropic vision assist, and manual training upload still had network paths.
Three focused tests failed before the guard. All three now refuse to run while
`LOCAL_ONLY_INFERENCE` is true, before requests or optional SDK imports. Hosted
privacy tests explicitly disable that flag while mocking HTTP; they do not send
real requests. Nineteen focused tests passed. The video recognition gate remains
34/37 and RC +3 versus the labeled RC 0.

## Overlapping dealer cards and detector-box jitter

The saved trace showed the dealer's revealed 10 in four consecutive model
outputs, but CardMemory matched it to the existing 7 and never created a 10.
A regression reproduced that association. Matching now reserves a remembered
card for a nearby observation of its own rank before considering a conflicting
rank. This alone left the real-video replay at 34/37, RC +3 (`table_registry_05`):
the 10 was created but its confirmation kept resetting.

Numeric crop analysis found that 1-pixel detector-box shifts changed resized
24x24 signatures by 13-22 gray levels on several stable-rank cards. Comparing
the same native screen pixels in the intersection of successive boxes avoids
that artifact without raising the 12-level image-change limit. A jitter
regression failed before this change; 33 memory/evidence tests passed after it.
The exact 113-frame, 111-call local replay (`table_registry_06`) then counted
22/22 and 15/15 with RC +3 and final RC 0. Rank multiset comparison still
found one missing 4 and one extra 2 in round 1; the matching Hi-Lo value hides
that error. The shoe remains marked uncertain. No image was viewed, so physical
identity at that overlap is not established.

The original local player model `dey022/1`, run on the same schedule with the
same memory, counted only 14/22 and 9/15. The unquantized rank model was also
checked on 11 saved frames around the 2/4 conflict: it reported the same 2 and
4 at the same locations. No model was promoted or replaced. These are video
measurements, not held-out or live accuracy claims.

## Confirmed cards withstand a transient unread frame

A confirmed card could be permanently marked doubtful after one unstable
observation, even if its rank and position recovered. A focused regression
failed first. The registry now ignores ambiguous or unstable observations for
cards already confirmed, while still requiring consecutive clean observations
for new cards and flagging a reliable rank conflict. Thirty-four focused tests
passed. The same 113-frame replay (`table_registry_07`) retained exactly the
37 counted events and RC 0 from `table_registry_06`; current-round doubtful or
pending cards fell from 10 to 5. The shoe remains uncertain because movement
ambiguities and the round-one 2/4 rank mismatch are unresolved.

A further attempt to preserve an extra pending candidate on every reliable
rank conflict was rejected. It exposed the missing 4 as pending, but also
blocked a later 9 in the same crowded hand. `table_registry_08` fell to
21/22 then 15/15, 36/37 overall; RC happened to stay 0. The code and its
synthetic-only test were reverted. The retained result is `table_registry_07`.

## Iteration 6 - Fullscreen ROI and overlapping index repair (2026-10-26)

Hypothesis: sharing a single network resize between packed calibrated patches
reduces small-index detail, global ONNX NMS removes different overlapping ranks,
and a 35%-of-box identity radius merges nearby cards. Fixed table geometry can
also hide a pending hero hand in a small window. These are code-inspection
findings; the user's Zen Q/K/J/9 report was not independently recorded here.

Change: run independent native 8-pixel-padded ROIs, partition accepted proposals
by calibrated owner, use class-aware NMS and an overlap-tolerant ROI threshold,
resolve unique opposed 6/9 corner pairs by the upper-left index, retain moderate
isolated 6/9 as pending, reduce per-frame duplicate radius, fit both hand rows
to actual canvas height, and preserve native DPI/client dimensions on capture.
Preview shares the ROI path and shows native model boxes/confidences.

Before: packed one-call inference and legacy fixture expectations were verified
by inspection; no pre-change live or held-out accuracy score was obtained. The
first broad regression after implementation exposed only old one-call metadata
expectations; they were migrated to ROI-local coordinates, preserving native-box
and frame-immutability checks.

After: 18 deterministic fullscreen tests passed; full regression 1540 passed
plus 37 subtests, no skips/exclusions. The real-Tk synthetic smoke passed 24
features, including pending dealer/hero visibility at 220/280/400 px. A real
DetectionEngine with synthetic detections counted the reported rank scenario as
four cards, RC -3, dealer 20, hero 19, with a physical recommendation available.

An additional temporal regression first failed with two active candidates when
a weak lower 6 appeared before the upper 9. Reusing the pending physical ID
when the unique corner pair becomes visible resolves it to one confirmed 9,
RC 0 and no remaining uncertainty; that regression now passes.

Decision: retain the repair. No model was replaced/trained and no desktop frame
was persisted. These are functional and geometric measurements, not live vision
accuracy. Opposing-corner geometry cannot prove orientation in every occlusion;
GPU-browser freshness, live CPU and cycle p95 still require actual source data.
