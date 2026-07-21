# guidance.md

## What was found (project audit)

| Item | Status |
|---|---|
| `main.py` | Present. FastAPI app object is named `app`. No `uvicorn.run()` call — launched via CLI. |
| `static/index.html` | Present. |
| `requirements.txt` | Present. `fastapi`, `uvicorn[standard]`, and `yt-dlp` all listed. |
| `.venv/` | Present. Virtual environment folder is named `.venv` (not `venv`). |
| `setup.bat` | present. |
| `run.bat` | present. |
| `setup.sh` / `run.sh` | Not present. |
| `reset.bat` / `reset.sh` | Not present. |
| Host / port | No hardcoded runner in `main.py`. App binds to `127.0.0.1:8000` via the CLI argument in `run.bat`. |
| ffmpeg validation | Not validated anywhere in the project. `run.bat` warns if it is absent. |

---

## What was created

**`setup.bat`** — Creates the `.venv` virtual environment and installs all packages from `requirements.txt`. Run this once before using `run.bat`.

**`run.bat`** — Activates `.venv`, checks for ffmpeg, starts the uvicorn server at `http://127.0.0.1:8000`, and opens that URL in your default browser ~2 seconds after launch.

---

## How to run the app

### First time only

1. Double-click `setup.bat`.
2. Wait for it to finish — you will see "Setup complete. Now run run.bat to start the app." and the window will pause before closing.

### Every time after that

1. Double-click `run.bat`.
2. A terminal window opens — **leave it open** while you use the app.
3. A browser tab opens automatically after ~2 seconds at `http://127.0.0.1:8000`.

### Stopping the app

Close the terminal window, or click inside it and press `Ctrl+C`.

---

## Desktop shortcut

Do **not** move `run.bat` to the Desktop — it uses relative paths and will break if moved.

Instead, create a shortcut:

- Right-click `run.bat` → **Send to** → **Desktop (create shortcut)**, **or**
- Hold `Alt` and drag `run.bat` to the Desktop (creates a shortcut, not a copy).

The shortcut points back to the real `run.bat` inside the project folder, so relative paths stay intact.

---

## Troubleshooting

**"No .venv\ found"**
Run `setup.bat` first. The virtual environment must exist before `run.bat` can start.

**Browser opens but shows an error / can't connect**
The server likely failed to start. Check the terminal window for a Python traceback and fix the reported error.

**ffmpeg warning shown at startup**
Video and audio downloads will silently fail without ffmpeg on PATH.

- Install via winget: `winget install ffmpeg`
- Or download manually: https://ffmpeg.org/download.html

After installing, close and reopen your terminal (or restart) so PATH is refreshed.
