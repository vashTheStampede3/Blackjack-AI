# Counter vision task constraints

## Latest user scope

The user now wants a working app handoff: verify startup and UI features,
configure the local model and let them judge recognition quality. Do not block
that handoff on the earlier scientific accuracy/soak gates. Those measurements
remain unverified, and must not be presented as passed. Test GUI actions only
inside this app, with isolated test data; casino/VM/browser remains read-only.

- Casino/VM/browser: read only. Capture with mss. Never click, type, scroll,
  reload, or simulate mouse activity there. Keep executor off; no autonomy work.
- Process locally. Remote calls, if explicitly used, get only a reviewed table
  ROI with account HUD masked. Never persist or upload full desktop screenshots.
- Credentials only from environment. Never print them or use bundled author keys.
- Python 3.12 via uv, environment .venv312; preserve the old .venv.
- CPU ONNX Runtime, max two inference threads. Ask before >500 MB installation.
  Heavy training is outside this PC.
- Branch counter-vision; commit each measured iteration. Models/data/reports are
  ignored. Do not delete user files or silently overwrite recordings.
- Acceptance: held-out session, >=300 unique cards, rank >=98%, Hi-Lo bucket
  >=99%, every rank recall >=90%, >=40 rounds with running count correct >=95%,
  zero double count in property tests, 3-shoe reshuffle or tested manual fallback,
  cycle p95 <=800 ms, full app CPU <=35%, RAM <=1.5 GB, 30-minute live soak.
- Never substitute synthetic/source-video observations for live holdout evidence.
  Missing labels mean NOT MEASURED, not 0% or 100% accuracy.
- Work alone. Keep a measured hypothesis/change/before/after/decision log in
  ITERATIONS.md. At most 12 detection/count iterations; change strategy after
  three consecutive improvements <=1 percentage point.
- The latest private user key passed metadata authentication (HTTP 200 for both
  original models). Do not ask for it again. Local original artifacts are under
  models/roboflow/ and carry authoritative class/preprocessing sidecars.
- Current blockers and reports are in reports/FINAL.md. Do not claim completion
  until every acceptance criterion has evidence.
