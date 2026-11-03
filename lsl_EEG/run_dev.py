#!/usr/bin/env python3
"""Development launcher for the LSL EEG app.

``python -m app.main`` (and running ``app/main.py`` directly) imports pylsl,
which calls ``platform.system()``. On this machine the Python 3.13 WMI query
behind ``platform.system()`` hangs, so the process starts but never responds.
Disabling ``platform._wmi`` makes platform fall back to
``sys.getwindowsversion()``, which returns immediately.

Usage (from ``lsl_EEG/``):

    ..\\.venv\\Scripts\\python.exe run_dev.py --host 127.0.0.1 --port 8081
"""

from __future__ import annotations

import os
import platform
import sys

# Avoid OpenBLAS spawning one thread per core on low-memory machines.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Work around the hanging WMI lookup on Windows (see module docstring).
if sys.platform == "win32" and hasattr(platform, "_wmi"):
    platform._wmi = None

from app.main import run  # noqa: E402  (must come after the WMI workaround)


if __name__ == "__main__":
    run()
