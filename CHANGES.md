# CHANGES — Session 1 (2026-05-30)

One-liner per fix for the PFE supervisor / commit log.

## Pi side (`codeRaspberry/`)

- **fix(motion): release lock during rotation sleep + abort event**
  `MotionService.rotate_by_angle` used to hold the asyncio.Lock for the full
  3-5 s rotation, blocking every other Arduino command. Lock is now held
  only for the individual serial writes; new `request_abort()` cuts the
  sleep early for emergency stops.

- **fix(motion): bubble up Arduino errors as ArduinoSendError + MotionResult**
  Callers no longer have to assume "" means OK — failed sends raise an
  explicit exception, motion methods return `MotionResult(ok, command, error)`.

- **fix(face): track refresh task to prevent GC + duplicate captures**
  `fire_and_forget_refresh` keeps a strong reference to its task and refuses
  to spawn a second one while the first is in flight. Added
  `cancel_pending_refresh()` for clean shutdown.

- **fix(main): wake-word loop survives Vosk/Azure crashes**
  Iteration body extracted into `_wake_word_iteration`; outer loop wraps it
  in try/except so the robot stays alive after any single-turn failure.

- **fix(main): bootstrap_in_parallel tolerates individual failures**
  `asyncio.gather(..., return_exceptions=True)` + per-task logging. Bluetooth
  unavailable or backend down at boot no longer kills the whole start-up.

- **feat(logger): rotating file log at `~/.koda/logs/koda.log`**
  10 MB × 5 files rotation. Tunable via `KODA_LOG_FILE`, `KODA_LOG_MAX_BYTES`,
  `KODA_LOG_BACKUP_COUNT`. Disable with `KODA_LOG_FILE=0`.

- **feat(utils): subprocess registry + shutdown reaper**
  New `utils/subprocess_registry.py`. `RespeakerAdapter.stream_pcm` registers
  its sox/arecord. Shutdown kills tracked + `pkill -u $UID` orphans for
  `sox|arecord|rpicam-*|yt-dlp|ffmpeg`.

- **test: 7 regression tests for motion + face lifecycle**
  `tests/test_motion_concurrent.py` (4 cases) +
  `tests/test_face_recognition_lifecycle.py` (3 cases). All pass.

## Backend side (`Distributeur-Des-Service-Python/`)

- **feat(n8n): warn loudly when N8N_WEBHOOK_URL points to /webhook-test/**
  7-line boxed warning at boot with the production URL to switch to.
  Saves the next person from the "60 s hang" diagnosis we did this morning.

## Verification

```bash
# Pi
cd ~/codeRaspberry && source .venv/bin/activate
python -m pytest tests/ -v   # all green
python -m app.main           # loud loop, ms timestamps, file log starts

# Backend (PC)
cd C:\pfe\Distributeur-Des-Service-Python
python -m uvicorn src.main:app --port 8000 --reload
# Expect: loud 7-line warning if .env still has /webhook-test/

# After shutdown — verify no orphans
pgrep -af "sox\|arecord\|rpicam\|yt-dlp"   # expected: empty
```
