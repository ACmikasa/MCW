# lsl_EEG Modular Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the `lsl_EEG` project into acquisition, data, processing, model, service, API, and web layers while keeping `python lsl_backend.py` runnable.

**Architecture:** Keep `lsl_backend.py` as a compatibility launcher. Move the FastAPI application into `app/main.py`, keep LSL receive behavior in `acquisition/lsl_receiver.py`, and add a small offline API backed by a deterministic baseline service. The existing pages remain available while the offline page switches from simulated progress to API-backed jobs.

**Tech Stack:** Python 3, FastAPI, Uvicorn, NumPy, pylsl, vanilla HTML/CSS/JavaScript.

**Spec:** `lsl_EEG/ARCHITECTURE.md`

## Global Constraints

- The existing `python lsl_backend.py` command must remain valid.
- Realtime WebSocket message shapes must remain compatible with `frontend.html`.
- Offline analysis starts with a deterministic baseline result and a model interface for future models.
- Uploaded files and generated results stay outside Git tracking.
- No model weights or EEG recordings are committed.

---

### Task 1: Create data and model contracts

**Files:**
- Create: `lsl_EEG/data/schemas.py`
- Create: `lsl_EEG/models/base.py`
- Create: `lsl_EEG/models/rule_based.py`

- [ ] Add typed result structures and a baseline model without changing the running app.
- [ ] Verify imports with `python -c`.

### Task 2: Extract LSL receiver

**Files:**
- Create: `lsl_EEG/acquisition/lsl_receiver.py`
- Modify: `lsl_EEG/lsl_backend.py`

- [ ] Move `LSLReceiver` into the acquisition module.
- [ ] Keep the old module exporting `LSLReceiver` for compatibility.
- [ ] Verify the module imports with installed dependencies.

### Task 3: Add offline service and API

**Files:**
- Create: `lsl_EEG/services/offline_service.py`
- Create: `lsl_EEG/api/offline_api.py`
- Create: `lsl_EEG/data/loaders.py`

- [ ] Accept CSV/EDF/BDF metadata safely, with CSV support first and clear unsupported errors for EDF/BDF until their dependencies are configured.
- [ ] Create in-memory jobs with `queued`, `running`, `completed`, and `failed` states.
- [ ] Return a stable result schema.

### Task 4: Add modular FastAPI app and preserve entrypoint

**Files:**
- Create: `lsl_EEG/app/main.py`
- Create: `lsl_EEG/app/config.py`
- Modify: `lsl_EEG/lsl_backend.py`

- [ ] Register realtime routes and offline routes in the new app.
- [ ] Serve both HTML files.
- [ ] Keep `lsl_backend.py` as the direct executable launcher.

### Task 5: Connect the offline page to the API

**Files:**
- Modify: `lsl_EEG/offline-detection.html`

- [ ] Upload the selected file to the offline API.
- [ ] Poll job status and render progress/results.
- [ ] Keep a clear error state.

### Task 6: Document and verify

**Files:**
- Create or update: `lsl_EEG/README.md`
- Create: `lsl_EEG/tests/test_offline_service.py`

- [ ] Document startup, architecture, API endpoints, and future model integration.
- [ ] Run compile checks, service tests, FastAPI route checks, and `git diff --check`.
