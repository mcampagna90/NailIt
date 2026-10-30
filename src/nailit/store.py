"""Persistence of the learning state.

The state is kept separate from the question bank: questions are referenced by a
stable id, so updating the bank does not invalidate the state (new questions
simply start without state). One JSON file per profile+bank pair.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import UserState


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def state_path(state_dir: str | Path, profile: str, bank_name: str) -> Path:
    return Path(state_dir) / f"{_safe(profile)}__{_safe(bank_name)}.json"


def load_state(state_dir: str | Path, profile: str, bank_name: str) -> UserState:
    """Load the state, or create an empty one if missing/corrupt."""
    path = state_path(state_dir, profile, bank_name)
    if not path.exists():
        return UserState(profile=profile, bank_name=bank_name)
    try:
        return UserState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        # Corrupt state: do not lose the bank, start fresh.
        return UserState(profile=profile, bank_name=bank_name)


def save_state(state_dir: str | Path, state: UserState) -> Path:
    path = state_path(state_dir, state.profile, state.bank_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path


def list_states(state_dir: str | Path) -> list[UserState]:
    """Load every saved state in the directory. Used to list existing profiles.

    Reads the actual `profile`/`bank_name` from each file rather than parsing
    filenames, so it is robust to sanitized names. Unreadable files are skipped.
    """
    d = Path(state_dir)
    if not d.is_dir():
        return []
    out: list[UserState] = []
    for path in sorted(d.glob("*.json")):
        try:
            out.append(UserState.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
