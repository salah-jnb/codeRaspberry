# KODA Concurrency & Robustness Audit — Session 1

Date: 2026-05-30
Scope: Pi side (`codeRaspberry/`) + minimal backend (`Distributeur-Des-Service-Python/`)

This document lists every issue from the original audit prompt, whether it
was confirmed, and its status after Session 1 fixes.

## Legend

- ✅ Fixed in Session 1 with regression test
- 🟡 Fixed in Session 1 (no dedicated test yet, covered by import smoke)
- ⏸️ Deferred to Session 2 (scope reduction agreed with user)
- ❌ Not applicable (proposed fix was over-engineered or low impact)

---

## A — Thread / asyncio (critical)

### A1 — `MotionService.rotate_by_angle` holds lock during `asyncio.sleep` ✅
- **Confirmed at:** `services/motion/motion_service.py:156-167` (pre-fix)
- **Root cause:** `async with self._lock:` spanned the 3-5s rotation sleep
  → every other Arduino command (`hello`, `stop`, expression) blocked.
- **Fix:** lock now held ONLY for individual serial writes (direction + STOP).
  Sleep happens between the two writes, outside the lock. New
  `request_abort()` + `self._abort_event` cuts the sleep on emergency.
- **Test:** `tests/test_motion_concurrent.py::test_rotate_releases_lock_during_sleep`
  (asserts `hello()` returns in <200 ms while a 90° rotation is mid-flight).
- **Bonus:** `test_rotate_abort_event_cuts_sleep_short` validates the abort path.

### A2 — `MotionService._send` swallows exceptions ✅
- **Confirmed at:** `services/motion/motion_service.py:86-94` (pre-fix)
- **Root cause:** `except Exception: return ""` — callers can't tell if the
  motor moved or the USB cable was unplugged.
- **Fix:** `_send` now raises `ArduinoSendError(command, cause)`. Cleanup
  paths can use `_try_send(...) -> MotionResult` which never raises.
- **Test:** `tests/test_motion_concurrent.py::test_send_raises_on_adapter_failure`

### A3 — `FaceRecognitionService.fire_and_forget_refresh` orphan task ✅
- **Confirmed at:** `services/vision/face_recognition_service.py:82-92` (pre-fix)
- **Root cause:** `asyncio.create_task(...)` without keeping a reference →
  GC could collect mid-flight → "Task destroyed but pending" warnings +
  partial camera state leaks.
- **Fix:**
  - `self._refresh_task` keeps a strong ref.
  - At most ONE refresh in flight; second call returns immediately.
  - `_on_refresh_done` callback clears the slot + surfaces exceptions.
  - New `cancel_pending_refresh()` for clean shutdown.
- **Test:** `tests/test_face_recognition_lifecycle.py` (3 cases).

### A4 — `PcmBroadcaster` drop-oldest ⏸️
- **Confirmed.** Logged at DEBUG level when a drop happens.
- **Deferred:** The user agreed the adaptive back-pressure proposal was
  over-engineered. Current `logger.debug("dropped 1 chunk")` is enough to
  diagnose. Will revisit if drops are observed in production.

### A5 — `RespeakerAdapter._lock` leak on KeyboardInterrupt ⏸️
- **Partially fixed by D1:** subprocess registry ensures the leaked sox/
  arecord is killed at shutdown. The lock itself is reset on next adapter
  instantiation (process exit clears asyncio state).
- **Not addressed:** mid-run KeyboardInterrupt scenario — rare in production
  (systemd would restart anyway).

### A6 — Arduino/Nextion USB watchdog ⏸️
- **Confirmed real.** Deferred to Session 2: needs careful design of
  re-open logic + udev rule detection.

### A7 — `_bootstrap_in_parallel` aborts everything on one failure ✅
- **Confirmed at:** `app/main.py:223` (pre-fix `return_exceptions=False`)
- **Fix:** `return_exceptions=True` + per-task logging. Bluetooth failure
  no longer prevents KODA from booting.
- **Test:** covered by `tests/test_imports.py` (module loads).

---

## B — Main loops

### B1 — `_run_wake_word_loop` no try/except ✅
- **Confirmed at:** `app/main.py:370` (pre-fix)
- **Root cause:** Vosk crash / Azure WS reset / camera error inside an
  iteration killed the entire loop → robot mute until process restart.
- **Fix:**
  - Loop body extracted into `_wake_word_iteration(...)`.
  - Outer loop wraps it in `try/except Exception: log + sleep(2s) + continue`.
  - `asyncio.CancelledError` propagates for clean shutdown.

### B2 — Mic streamed during inter-turn pause ⏸️
- Deferred. Current behaviour: Vosk keeps recognizing during pause but the
  results are discarded. CPU waste of ~15% during 500ms pause — acceptable.

### B3 — Threadpool monitoring ❌
- Skipped per user agreement. Premature optimisation — add only if we observe
  pool saturation in real logs.

---

## C — Backend FastAPI

### C1 — `service = ApiServiceImpl()` at module level ⏸️
- Confirmed. Mitigation via `Depends(get_service)` is a bigger refactor.
- Workaround for now: backend boots fine with Supabase down (caches gracefully
  return None). Real crash would only happen on first DB-dependent endpoint.

### C2 — Cache thread-safety ⏸️
- Confirmed. `_setting_cache`, `_MOTION_TTS_CACHE` are dict at instance/class
  level, accessed from threadpool workers. Worst case = double fetch (waste),
  not crash. Adding `threading.Lock` is straightforward → Session 2.

### C3 — Parallelize TTS announcement with yt-dlp ⏸️
- Optimization. Deferred — n8n is still the long pole (5-10s vs TTS 2s).

### C4 — yt-dlp sync subprocess in threadpool 🟡
- Currently runs inside `audio_to_n8n_to_action` which IS in `asyncio.to_thread`
  (fixed earlier this session). Worker blocked but event loop free. Acceptable.

### C5 — `N8N_WEBHOOK_URL` validation at boot ✅
- **Fix:** `N8NClient._warn_if_test_url()` logs a loud 7-line warning if
  `/webhook-test/` is detected, with the production URL to switch to.

---

## D — Subprocess & resources

### D1 — Subprocess cleanup at shutdown ✅
- **Fix:**
  - New `utils/subprocess_registry.py` with `track_subprocess` / `untrack_subprocess` / `kill_tracked_subprocesses` / `pkill_orphans`.
  - `RespeakerAdapter.stream_pcm` registers its sox/arecord subprocess.
  - `_shutdown` calls both `kill_tracked_subprocesses()` + `pkill_orphans()`
    (belt-and-suspenders for code paths that don't track).
- **Verification command (after shutdown):**
  `pgrep -af "sox\|arecord\|rpicam\|yt-dlp"` → 0 lines.

### D2 — File logging ✅
- **Fix:** `utils/logger.py` now attaches a `RotatingFileHandler` to root.
  - Default path: `~/.koda/logs/koda.log`
  - Default rotation: 10 MB × 5 files (50 MB on disk total)
  - Configurable via `KODA_LOG_FILE`, `KODA_LOG_MAX_BYTES`, `KODA_LOG_BACKUP_COUNT`
  - Disable with `KODA_LOG_FILE=0`.
- File log uses the verbose format (`%(asctime)s %(levelname)s [%(name)s]`)
  even when console uses the compact format — full context for post-mortem.

---

## E — Configuration

### E1 — Backend mDNS auto-discovery ⏸️
- Deferred. Better fix: assign static IP to the PC backend, or improve
  Avahi config on the Pi. Auto-scanning the subnet is a 60s+ workaround.

### E2 — ALSA card dynamic detection ⏸️
- Deferred. `RESPEAKER_DEVICE=plughw:3,0` is stable in practice because the
  Pi has only one USB audio device. Migration to `hw:CARD=ArrayUAC10` is
  trivial when needed (one-line `.env` change).

---

## Other findings (not in original audit)

### Sox `remix` ignored on `-t raw -` output ✅ (fixed earlier today)
- See `memory/project_sox_remix_raw_bug.md` for the full diagnostic. Both
  `RespeakerAdapter._sox_capture_cmd` and `ContinuousListenerService` now
  re-declare `-c 1` on the output side to force the remix to apply.

### MotionService now returns `MotionResult` ✅
- `rotate_by_angle` now returns `MotionResult(ok, command, error)` instead
  of `None`. Callers can branch on `result.ok` for fallback behaviour.

---

## Session 1 fix summary

| Fix | File(s) | Test |
|-----|---------|------|
| A1 motion lock | `services/motion/motion_service.py` | ✅ |
| A2 motion exceptions | `services/motion/motion_service.py` | ✅ |
| A3 face task tracking | `services/vision/face_recognition_service.py` | ✅ |
| A7 bootstrap resilience | `app/main.py` | smoke |
| B1 wake-word loop restart | `app/main.py` | smoke |
| C5 n8n URL warning | `src/infrastructure/n8n_client.py` | smoke |
| D1 subprocess registry | `utils/subprocess_registry.py`, `adapters/respeaker_adapter.py`, `app/main.py` | smoke |
| D2 file logging | `utils/logger.py` | smoke |

**Test results:** 7/7 regression tests pass, all imports load cleanly on both Pi and backend.

## Deferred to Session 2

A4, A5, A6, B2, B3, C1, C2, C3, E1, E2 — listed above with rationale.

Recommended order for Session 2:
1. A6 USB watchdog (Arduino/Nextion re-open on disappear)
2. C2 cache locks (small, isolated)
3. A5 RespeakerAdapter lock cleanup hardening
4. B2 mic idle between turns
5. Integration test: 30 consecutive conversations
