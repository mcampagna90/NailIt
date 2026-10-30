"""Tests for the selection engine — the heart of NailIt.

Checks verticalization, soon-repeat, prerequisites, balancing and priority for
overdue reviews. All deterministic by passing `now` and a seeded rng.
"""

import random

from nailit.models import Grade, Question, QuestionBank, QuestionState, UserState
from nailit.scheduler import (
    EngineParams,
    LearningMode,
    Scope,
    apply_grade,
    candidate_questions,
    chain_mastered,
    effective_mastery,
    select_next,
)


def _q(id, topic, level, chain=None, prereqs=None):
    return Question(
        id=id, domain="cyber", topic=topic, level=level, type="open",
        question=f"q-{id}", answer="a", level_chain=chain,
        prerequisites=prereqs or [],
    )


def _chain_bank():
    return QuestionBank(
        name="b",
        questions=[
            _q("l1", "acl", 1, chain="c"),
            _q("l2", "acl", 2, chain="c", prereqs=["l1"]),
            _q("l3", "acl", 3, chain="c", prereqs=["l2"]),
        ],
    )


def _state():
    return UserState(profile="p", bank_name="b")


def test_starts_at_level_one():
    bank = _chain_bank()
    scope = Scope(domain="cyber")
    q = select_next(bank, _state(), scope, now=0.0, rng=random.Random(1))
    assert q.id == "l1"


def test_know_unlocks_next_level():
    bank = _chain_bank()
    state = _state()
    l1 = bank.by_id()["l1"]
    # Knowing level 1 -> l2 becomes the current level of the chain.
    state = apply_grade(state, l1, Grade.KNOW, now=0.0)
    # now far enough ahead that l1 is no longer "due".
    nxt = select_next(bank, state, Scope(domain="cyber"), now=100.0, rng=random.Random(1))
    assert nxt.id == "l2"


def test_dont_know_repeats_soon_and_no_advance():
    bank = _chain_bank()
    state = _state()
    l1 = bank.by_id()["l1"]
    state = apply_grade(state, l1, Grade.DONT_KNOW, now=0.0)
    # Right after it is not due yet; at the soon-review time l1 comes back.
    params = EngineParams()
    nxt = select_next(bank, state, Scope(domain="cyber"),
                      now=params.interval_dont_know + 1, rng=random.Random(1))
    assert nxt.id == "l1"  # no advancement to l2


def test_prerequisite_blocks_advanced_question():
    bank = _chain_bank()
    state = _state()
    # Without passing l1, l2 must never be a candidate.
    cands = candidate_questions(bank, state, Scope(domain="cyber"), EngineParams())
    ids = {q.id for q in cands}
    assert ids == {"l1"}


def test_balancing_prefers_less_covered_topic():
    # Two separate topics; mastering 'acl' pushes toward 'crypto'.
    bank = QuestionBank(
        name="b",
        questions=[
            _q("acl1", "acl", 1),
            _q("cry1", "crypto", 1),
        ],
    )
    state = _state()
    acl1 = bank.by_id()["acl1"]
    state = apply_grade(state, acl1, Grade.KNOW_WELL, now=0.0)
    nxt = select_next(bank, state, Scope(domain="cyber"), now=100.0, rng=random.Random(1))
    assert nxt.id == "cry1"


def test_chain_mastered_requires_consolidation():
    bank = _chain_bank()
    state = _state()
    # A single "know it well" is NOT enough to master: it needs consolidation.
    for qid in ("l1", "l2", "l3"):
        state = apply_grade(state, bank.by_id()[qid], Grade.KNOW_WELL, now=0.0)
    assert not chain_mastered(bank, state, "c")
    # Repeating positive grades pushes mastery above the mastery threshold.
    for _ in range(3):
        for qid in ("l1", "l2", "l3"):
            state = apply_grade(state, bank.by_id()[qid], Grade.KNOW_WELL, now=0.0)
    assert chain_mastered(bank, state, "c")


def test_scope_filters_by_topic():
    bank = QuestionBank(
        name="b",
        questions=[_q("a1", "acl", 1), _q("c1", "crypto", 1)],
    )
    q = select_next(bank, _state(), Scope(domain="cyber", topic="crypto"),
                    now=0.0, rng=random.Random(1))
    assert q.id == "c1"


def test_empty_scope_returns_none():
    bank = QuestionBank(name="b", questions=[_q("a1", "acl", 1)])
    q = select_next(bank, _state(), Scope(domain="cyber", topic="missing"), now=0.0)
    assert q is None


# ---------------------------------------------------------------------------
# Learning modes: depth / breadth / balanced
# ---------------------------------------------------------------------------

def _two_chains_bank():
    """Two topics, each a 2-level chain, so we can see breadth vs depth."""
    return QuestionBank(
        name="b",
        questions=[
            _q("a1", "acl", 1, chain="ca"),
            _q("a2", "acl", 2, chain="ca", prereqs=["a1"]),
            _q("c1", "crypto", 1, chain="cc"),
            _q("c2", "crypto", 2, chain="cc", prereqs=["c1"]),
        ],
    )


def test_breadth_mode_covers_basics_before_going_deeper():
    bank = _two_chains_bank()
    state = _state()
    params = EngineParams(learning_mode=LearningMode.BREADTH)
    # Know acl level 1. In breadth mode the next question must NOT be acl level 2;
    # crypto level 1 is still uncovered, so it comes first.
    state = apply_grade(state, bank.by_id()["a1"], Grade.KNOW, params, now=0.0)
    nxt = select_next(bank, state, Scope(domain="cyber"), params,
                      now=100.0, rng=random.Random(1))
    assert nxt.id == "c1"
    # Only after every level-1 is known does level 2 become eligible.
    state = apply_grade(state, bank.by_id()["c1"], Grade.KNOW, params, now=100.0)
    cands = candidate_questions(bank, state, Scope(domain="cyber"), params, now=200.0)
    assert {q.id for q in cands} == {"a2", "c2"}


def test_depth_mode_verticalizes_immediately():
    bank = _two_chains_bank()
    state = _state()
    params = EngineParams(learning_mode=LearningMode.DEPTH)
    # Know acl level 1; in depth mode balancing is off, so acl level 2 is eligible
    # and preferred (same lowest effective mastery, no pull toward crypto).
    state = apply_grade(state, bank.by_id()["a1"], Grade.KNOW, params, now=0.0)
    cands = candidate_questions(bank, state, Scope(domain="cyber"), params, now=100.0)
    assert "a2" in {q.id for q in cands}


# ---------------------------------------------------------------------------
# Lazy mastery decay
# ---------------------------------------------------------------------------

def test_effective_mastery_decays_past_due():
    params = EngineParams(decay_half_life=100.0)
    st = QuestionState(mastery=0.8, due_at=0.0, times_seen=1, last_grade=Grade.KNOW)
    # At due date: no decay.
    assert effective_mastery(st, now=0.0, params=params) == 0.8
    # One half-life past due: halved.
    assert effective_mastery(st, now=100.0, params=params) == 0.4
    # Two half-lives: quartered.
    assert round(effective_mastery(st, now=200.0, params=params), 4) == 0.2


def test_decay_disabled_keeps_mastery():
    params = EngineParams(decay_half_life=0.0)
    st = QuestionState(mastery=0.9, due_at=0.0, times_seen=1)
    assert effective_mastery(st, now=10_000.0, params=params) == 0.9


def test_chain_drops_out_of_mastered_after_long_gap():
    bank = _chain_bank()
    state = _state()
    params = EngineParams(decay_half_life=100.0)
    # Consolidate the whole chain to mastered.
    for _ in range(5):
        for qid in ("l1", "l2", "l3"):
            state = apply_grade(state, bank.by_id()[qid], Grade.KNOW_WELL, params, now=0.0)
    assert chain_mastered(bank, state, "c", params, now=0.0)
    # Long after the reviews fall due, decay pulls it out of "mastered".
    assert not chain_mastered(bank, state, "c", params, now=1_000_000.0)


def test_neglected_prerequisite_relocks_advanced_question():
    bank = _chain_bank()
    state = _state()
    params = EngineParams(decay_half_life=100.0)
    # Know l1 so l2 unlocks. due_at is now + interval_know (1 day).
    state = apply_grade(state, bank.by_id()["l1"], Grade.KNOW, params, now=0.0)
    due = state.get("l1").due_at
    cands = candidate_questions(bank, state, Scope(domain="cyber"), params, now=due)
    assert "l2" in {q.id for q in cands}
    # Long past due, l1's effective mastery decays below the advance threshold,
    # so l2 is locked again until l1 is refreshed. 0.34 halves every 100s past due;
    # it crosses below 0.3 after ~1 half-life.
    cands_late = candidate_questions(bank, state, Scope(domain="cyber"), params,
                                     now=due + 300.0)
    assert {q.id for q in cands_late} == {"l1"}


# ---------------------------------------------------------------------------
# Progress summary
# ---------------------------------------------------------------------------

def test_summarize_progress_counts_and_averages():
    from nailit.study import summarize_progress

    bank = _chain_bank()  # domain 'cyber', topic 'acl', chain 'c', 3 levels
    state = _state()
    # Know l1 only.
    state = apply_grade(state, bank.by_id()["l1"], Grade.KNOW, now=0.0)
    s = summarize_progress(bank, state, "cyber", now=0.0)
    assert s.total == 3
    assert s.seen == 1
    # one topic 'acl' with 3 questions, only l1 at 0.34 -> avg 0.34/3
    acl = next(t for t in s.topics if t.topic == "acl")
    assert acl.seen == 1
    assert acl.total == 3
    assert round(acl.avg_mastery, 3) == round(0.34 / 3, 3)
    # chain not yet mastered
    assert any(c.chain == "c" and not c.mastered for c in s.chains)


def test_summarize_progress_marks_mastered_chain():
    from nailit.study import summarize_progress

    bank = _chain_bank()
    state = _state()
    for _ in range(5):
        for qid in ("l1", "l2", "l3"):
            state = apply_grade(state, bank.by_id()[qid], Grade.KNOW_WELL, now=0.0)
    s = summarize_progress(bank, state, "cyber", now=0.0)
    assert any(c.chain == "c" and c.mastered for c in s.chains)


# ---------------------------------------------------------------------------
# Grade derived from a choice outcome + confidence
# ---------------------------------------------------------------------------

def test_grade_from_outcome_known():
    from nailit.scheduler import grade_from_outcome

    # Right + confident -> know it.
    assert grade_from_outcome(True, True) == Grade.KNOW
    # Right but not confident (lucky guess) -> unsure, so it comes back soon.
    assert grade_from_outcome(True, False) == Grade.UNSURE
    # Wrong -> don't know, regardless of claimed confidence.
    assert grade_from_outcome(False, True) == Grade.DONT_KNOW
    assert grade_from_outcome(False, False) == Grade.DONT_KNOW


def test_lucky_guess_does_not_unlock_next_level():
    """A correct-but-unsure answer on a true/false must not verticalize."""
    from nailit.scheduler import grade_from_outcome

    bank = _chain_bank()
    state = _state()
    # Simulate a lucky guess on l1: correct, not confident.
    grade = grade_from_outcome(is_correct=True, was_confident=False)
    state = apply_grade(state, bank.by_id()["l1"], grade, now=0.0)
    # l1 mastery stays below advance threshold -> l2 still locked.
    cands = candidate_questions(bank, state, Scope(domain="cyber"), EngineParams(), now=0.0)
    assert {q.id for q in cands} == {"l1"}
