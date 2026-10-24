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
