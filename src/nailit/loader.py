"""Loading and validation of question banks.

Pipeline: JSON/YAML file -> JSON Schema validation -> Pydantic models.
Collects all readable errors and rejects invalid banks, so the engine never
operates on malformed data.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from .models import QuestionBank


class BankValidationError(Exception):
    """Raised when a bank is not conformant. Holds all errors."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Invalid question bank:\n" + "\n".join(errors))


def _load_schema() -> dict[str, Any]:
    text = (
        resources.files("nailit.schema")
        .joinpath("question_bank.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _read_raw(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _schema_errors(data: dict[str, Any]) -> list[str]:
    validator = Draft7Validator(_load_schema())
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"[{loc}] {err.message}")
    return errors


def validate_bank(path: str | Path) -> list[str]:
    """Validate a bank without building the models. Returns the list of errors
    (empty if valid)."""
    try:
        data = _read_raw(path)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return [f"file could not be parsed: {exc}"]
    except OSError as exc:
        return [f"could not read the file: {exc}"]

    if not isinstance(data, dict):
        return ["the file does not contain a question-bank object"]

    errors = _schema_errors(data)

    # If the schema passes, the Pydantic validators catch the cross-item rules
    # (unique ids, existing prerequisites, correct/choices consistency per type).
    if not errors:
        try:
            QuestionBank.model_validate(data)
        except Exception as exc:  # pydantic ValidationError or ValueError
            errors.append(str(exc))

    return errors


def load_bank(path: str | Path) -> QuestionBank:
    """Load a validated bank. Raises BankValidationError if not conformant."""
    errors = validate_bank(path)
    if errors:
        raise BankValidationError(errors)
    return QuestionBank.model_validate(_read_raw(path))
