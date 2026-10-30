"""Tests for the loader and schema validation."""

import json
from importlib import resources

from nailit.loader import load_bank, validate_bank


def _sample_path() -> str:
    return str(resources.files("nailit.data").joinpath("sample_cybersecurity.json"))


def _write(tmp_path, data):
    p = tmp_path / "bank.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_sample_bank_is_valid():
    assert validate_bank(_sample_path()) == []
    bank = load_bank(_sample_path())
    assert bank.name == "cybersecurity-basics"
    assert len(bank.questions) == 7


def test_duplicate_ids_rejected(tmp_path):
    data = {
        "name": "b",
        "questions": [
            {"id": "x", "domain": "d", "topic": "t", "level": 1, "type": "open",
             "question": "q", "answer": "a"},
            {"id": "x", "domain": "d", "topic": "t", "level": 1, "type": "open",
             "question": "q2", "answer": "a2"},
        ],
    }
    errors = validate_bank(_write(tmp_path, data))
    assert errors
    assert any("duplicate" in e.lower() for e in errors)


def test_correct_out_of_range_rejected(tmp_path):
    data = {
        "name": "b",
        "questions": [
            {"id": "x", "domain": "d", "topic": "t", "level": 1,
             "type": "single_choice", "question": "q",
             "choices": ["a", "b"], "correct": [5]},
        ],
    }
    errors = validate_bank(_write(tmp_path, data))
    assert errors
    assert any("range" in e.lower() for e in errors)


def test_missing_prerequisite_rejected(tmp_path):
    data = {
        "name": "b",
        "questions": [
            {"id": "x", "domain": "d", "topic": "t", "level": 1, "type": "open",
             "question": "q", "answer": "a", "prerequisites": ["does-not-exist"]},
        ],
    }
    errors = validate_bank(_write(tmp_path, data))
    assert errors
    assert any("unknown prerequisite" in e.lower() for e in errors)


def test_choice_without_choices_rejected(tmp_path):
    data = {
        "name": "b",
        "questions": [
            {"id": "x", "domain": "d", "topic": "t", "level": 1,
             "type": "single_choice", "question": "q"},
        ],
    }
    errors = validate_bank(_write(tmp_path, data))
    assert errors


def test_is_choice_correct_and_text():
    bank = load_bank(_sample_path())
    q = bank.by_id()["cyber-net-002"]  # multiple_choice, correct [0,1,3]
    assert q.is_choice_correct([0, 1, 3])
    assert q.is_choice_correct([3, 1, 0])  # order independent
    assert not q.is_choice_correct([0, 1])
    assert not q.is_choice_correct([0, 1, 2, 3])
    # correct_choice_text lists the right options
    text = q.correct_choice_text()
    assert "Network segmentation" in text
    assert "single strong password" not in text
