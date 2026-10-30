"""Tests for store (state persistence) and assessment (test + grading)."""

import random
from importlib import resources

from nailit.assessment import TestSpec, build_test, grade_test
from nailit.loader import load_bank
from nailit.models import Grade, QuestionState, UserState
from nailit.scheduler import apply_grade
from nailit.store import load_state, save_state


def _bank():
    return load_bank(str(resources.files("nailit.data").joinpath("sample_cybersecurity.json")))


# --- store -----------------------------------------------------------------

def test_state_round_trip(tmp_path):
    state = UserState(profile="alice", bank_name="b")
    state.states["q1"] = QuestionState(mastery=0.5, times_seen=2, last_grade=Grade.KNOW)
    save_state(tmp_path, state)
    loaded = load_state(tmp_path, "alice", "b")
    assert loaded.states["q1"].mastery == 0.5
    assert loaded.states["q1"].last_grade == Grade.KNOW


def test_profiles_isolated(tmp_path):
    a = UserState(profile="alice", bank_name="b")
    a.states["q1"] = QuestionState(mastery=0.9)
    save_state(tmp_path, a)
    bob = load_state(tmp_path, "bob", "b")
    assert bob.states == {}


def test_missing_state_starts_empty(tmp_path):
    s = load_state(tmp_path, "nobody", "b")
    assert s.states == {}
    assert s.profile == "nobody"


# --- assessment ------------------------------------------------------------

def test_build_test_respects_count_and_domain():
    bank = _bank()
    spec = TestSpec(domain="cybersecurity", num_questions=4)
    test = build_test(bank, spec, rng=random.Random(1))
    assert len(test.questions) == 4
    assert all(q.domain == "cybersecurity" for q in test.questions)


def test_grade_choice_questions():
    bank = _bank()
    # cyber-net-001 is single_choice with correct [0].
    spec = TestSpec(domain="cybersecurity", topics=["network-security"], levels=[1],
                    num_questions=1)
    test = build_test(bank, spec, rng=random.Random(1))
    q = test.questions[0]
    result = grade_test(test, {q.id: list(q.correct)})
    assert result.auto_scored == 1
    assert result.auto_correct == 1
    assert result.score == 1.0


def test_open_question_needs_human_review():
    bank = _bank()
    spec = TestSpec(domain="cybersecurity", topics=["access-control"], levels=[1],
                    num_questions=1)
    test = build_test(bank, spec, rng=random.Random(1))
    q = test.questions[0]  # cyber-rbac-001 is open
    result = grade_test(test, {q.id: "a free-form answer"})
    assert result.needs_review == 1
    assert result.auto_scored == 0
    assert result.details[0].needs_human_review is True
