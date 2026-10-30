"""NailIt configuration.

Loads a versionable YAML file with reasonable defaults. Maps the engine
parameters onto EngineParams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .scheduler import EngineParams, LearningMode


@dataclass
class Config:
    bank_path: str | None = None
    profile: str = "default"
    state_dir: str = ".nailit_state"
    engine: EngineParams = field(default_factory=EngineParams)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if path is None:
            return cls()
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        engine_data = data.get("engine", {}) or {}
        known = {f: engine_data[f] for f in EngineParams.__dataclass_fields__ if f in engine_data}
        if "learning_mode" in known:
            known["learning_mode"] = LearningMode(known["learning_mode"])
        return cls(
            bank_path=data.get("bank_path"),
            profile=data.get("profile", "default"),
            state_dir=data.get("state_dir", ".nailit_state"),
            engine=EngineParams(**known),
        )
