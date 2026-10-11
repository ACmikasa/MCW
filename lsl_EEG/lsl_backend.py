#!/usr/bin/env python3
"""Compatibility launcher for the modular LSL EEG application.

Keep using ``python lsl_backend.py`` from this directory. The implementation
now lives in ``app/``, ``acquisition/``, ``api/`` and ``services/``.
"""

from __future__ import annotations

from acquisition.lsl_receiver import LSLReceiver
from app.main import app, receiver, run

__all__ = ["app", "receiver", "LSLReceiver"]


if __name__ == "__main__":
    run()
