"""Runtime configuration loaded from environment and CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # Optional when environment variables are injected by the host.
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "uploads"
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
SYSTEM_DATA_DIR = DATA_DIR / "system"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def stream_name() -> str | None:
    return os.environ.get("LSL_STREAM_NAME") or None


def stream_type() -> str:
    return os.environ.get("LSL_STREAM_TYPE", "EEG")


def auth_data_dir() -> Path:
    """Directory containing portable authentication CSV files."""
    configured = os.environ.get("AUTH_DATA_DIR")
    return Path(configured).expanduser() if configured else DATA_DIR / "auth"


def session_hours() -> int:
    try:
        return max(1, int(os.environ.get("AUTH_SESSION_HOURS", "12")))
    except ValueError:
        return 12
