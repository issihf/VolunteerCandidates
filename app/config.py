"""Paths and constants.

Data lives outside the program folder so that replacing the program
(an update) never touches it. VC_DATA_DIR overrides the location, which
is how tests keep their data separate from real data.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "VolunteerCandidates"
APP_TITLE = "מועמדים למלווים"
TIMEZONE = "Asia/Jerusalem"
PAAMONIM_BASE_URL = os.environ.get("VC_BASE_URL", "https://app.paamonim.org.il")
EXTRA_OPTION_FAMILIES = "ליווי משפחות"
DEFAULT_FOLLOW_UP_DAYS = 5


def data_dir() -> Path:
    override = os.environ.get("VC_DATA_DIR")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "data.db"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def browser_profile_dir() -> Path:
    """The dedicated browser's profile (keeps the Paamonim login between runs)."""
    path = data_dir() / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path
