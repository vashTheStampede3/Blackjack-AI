# Windows installation and startup

## Requirements

- 64-bit Windows
- Python 3.12
- `uv` on `PATH` for first setup or repair
- The checked-in `uv.lock` and the active local player ONNX model plus sidecar
  in `models/`

The project environment lives in `.venv312`. Existing `.venv`, profiles,
recordings, models, and credentials are left in place. Runtime dependencies
come from the pinned lock; startup does not display environment variables or
settings contents.

## First setup

Open PowerShell in the project folder and run:

```powershell
./start.ps1
```

If `.venv312` is absent or a locked runtime package is missing, the script uses
`uv sync --frozen` to create or repair it. Otherwise it checks Python 3.12 and
starts without synchronizing packages. If `uv` is missing, install it and rerun
the script. The application uses the local model assets and does not require a
Roboflow key for inference. A dealer model is optional; without one the app
uses its manual shuffle path.

To check the environment without starting the GUI, use `./start.ps1 -CheckOnly`.

## Launching

Double-click **Open Blackjack AI.cmd** or the installed **Blackjack AI**
desktop shortcut. Both start through `scripts/launch_blackjack.py`, which
checks the project environment and local model files before loading the GUI.
Startup failures are logged to `logs/startup_errors.log`; if that folder cannot
be written, the log falls back to `%LOCALAPPDATA%\Blackjack-AI`.

To install or refresh the single desktop shortcut, run:

```powershell
./scripts/install_desktop_shortcut.ps1
```

The shortcut follows the effective Windows Desktop folder, including a
redirected Desktop location.
