"""Incremental selection engine — the heart of NailIt.

Pure logic, no I/O: "given the state + the bank + a scope, pick the next
question" and "given a grade, update the state". Reusable from CLI, API or mobile.

Combined behaviours:
- Verticalization: within a chain (level_chain) you only advance a level once the
  user has shown they know the lower levels.
- Balancing: across different topics in the scope, prefer the least-covered one,
  so the user does not sink into a single theme and keeps the overview.
- Learning mode: depth (go deep fast), breadth (cover the basics of everything
  first), or balanced (the mix of both).
- Spaced repetition with decay: every grade moves the due date (due_at); overdue
  reviews get priority, and mastery *decays* the longer a question stays overdue,
  so a topic you stop reviewing stops counting as known.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .models import Grade, Question, QuestionBank, QuestionState, UserState


class LearningMode(str, Enum):
    """How to trade off going deep vs. staying broad."""

    DEPTH = "depth"        # verticalize as soon as a level is unlocked
    BREADTH = "breadth"    # cover the basics of every topic before going deeper
    BALANCED = "balanced"  # the default mix of verticalization and balancing


@dataclass(frozen=True)
class Scope:
    """What the user wants to revise."""

    domain: str
    topic: str | None = None

    def matches(self, q: Question) -> bool:
        if q.domain != self.domain:
            return False
        if self.topic is not None and q.topic != self.topic:
            return False
        return True


@dataclass(frozen=True)
class EngineParams:
    """Engine parameters, injectable from config. Defaults are reasonable.

    Intervals are in seconds; using "logical" time (an explicit `now`) keeps the
    engine deterministic and testable.
    """

    # review intervals per grade (seconds)
    interval_dont_know: float = 30.0
    interval_unsure: float = 120.0
    interval_know: float = 86400.0  # 1 day
    interval_know_well: float = 604800.0  # 7 days
    # mastery changes per grade
    mastery_up: float = 0.34
    mastery_down: float = 0.5  # multiplicative factor it drops to
    # threshold above which a level unlocks the next one in the chain
    # (just proving you know it: a single "know" crosses this threshold).
    advance_threshold: float = 0.3
    # threshold above which a chain is considered mastered/archivable
    # (requires consolidation through several positive reviews).
    mastery_threshold: float = 0.8
    # weight of balancing across topics (0 = verticalize only)
    balance_weight: float = 1.0
    # how deep vs. broad to study
    learning_mode: LearningMode = LearningMode.BALANCED
    # lazy mastery decay: once a question is overdue, its *effective* mastery
    # halves every `decay_half_life` seconds past due_at (0 disables decay).
    # The stored mastery is never modified; decay is computed at read time.
    decay_half_life: float = 604800.0  # 7 days


# ---------------------------------------------------------------------------
# Effective (decay-adjusted) mastery
# ---------------------------------------------------------------------------

def effective_mastery(st: QuestionState, now: float, params: EngineParams) -> float:
    """Mastery adjusted for time: a question left overdue loses mastery.

    Decay is *lazy* — it is computed here from how long the question has been past
    its due date, without ever writing back to the stored state. Before the due
    date (or with decay disabled) the stored mastery is returned unchanged.
    """
    if params.decay_half_life <= 0 or st.times_seen == 0:
        return st.mastery
    overdue = now - st.due_at
    if overdue <= 0:
        return st.mastery
    factor = 0.5 ** (overdue / params.decay_half_life)
    return st.mastery * factor


def _eff(state: UserState, qid: str, now: float, params: EngineParams) -> float:
    return effective_mastery(state.get(qid), now, params)


# ---------------------------------------------------------------------------
# Deriving a grade for choice questions (objective outcome + confidence)
# ---------------------------------------------------------------------------

def grade_from_outcome(is_correct: bool, was_confident: bool) -> Grade:
    """Combine the objective result of a choice question with the user's stated
    confidence into an honest grade.

    A right answer only counts as "know it" when the user was confident; a lucky
    guess becomes "unsure" so it is reviewed soon and does not unlock the next
    level. A wrong answer is always "don't know".
    """
    if not is_correct:
        return Grade.DONT_KNOW
    return Grade.KNOW if was_confident else Grade.UNSURE


# ---------------------------------------------------------------------------
# Selecting the next question
# ---------------------------------------------------------------------------

def _prereqs_met(
    q: Question, state: UserState, now: float, params: EngineParams
) -> bool:
    """A prerequisite is satisfied once the user has shown they know it (effective
    mastery above the advance threshold); it need not be fully consolidated. Decay
    can therefore re-lock a prerequisite the user has neglected for too long."""
    for pid in q.prerequisites:
        st = state.get(pid)
        if st.times_seen == 0 or _eff(state, pid, now, params) < params.advance_threshold:
            return False
    return True


def _is_current_chain_level(
    q: Question, bank: QuestionBank, state: UserState, now: float, params: EngineParams
) -> bool:
    """True if q is the 'current' level of its chain: on every lower level the
    user has already shown they know it (verticalization). Without a chain, always
    True."""
    if not q.level_chain:
        return True
    for other in bank.chain_questions(q.level_chain):
        if other.level < q.level:
            if _eff(state, other.id, now, params) < params.advance_threshold:
                return False
    return True


def _topic_coverage(
    bank: QuestionBank, state: UserState, scope: Scope, now: float, params: EngineParams
) -> dict[str, float]:
    """Average effective mastery per topic in scope: low = poorly covered."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for q in bank.questions:
        if not scope.matches(q):
            continue
        sums[q.topic] = sums.get(q.topic, 0.0) + _eff(state, q.id, now, params)
        counts[q.topic] = counts.get(q.topic, 0) + 1
    return {t: sums[t] / counts[t] for t in sums}


def _breadth_level_cap(
    candidates: list[Question], state: UserState, now: float, params: EngineParams
) -> list[Question]:
    """Breadth mode: keep only the lowest level that is not yet broadly known.

    We look at the candidate levels and pick the smallest level that still has a
    question below the advance threshold; then we keep only candidates at that
    level. This covers every topic's level 1 before anyone's level 2.
    """
    levels = sorted({q.level for q in candidates})
    for lvl in levels:
        at_level = [q for q in candidates if q.level == lvl]
        if any(_eff(state, q.id, now, params) < params.advance_threshold for q in at_level):
            return at_level
    # Everything at every level is known: fall back to the highest level present.
    top = levels[-1]
    return [q for q in candidates if q.level == top]


def candidate_questions(
    bank: QuestionBank,
    state: UserState,
    scope: Scope,
    params: EngineParams,
    now: float = 0.0,
) -> list[Question]:
    """Pool of eligible questions: in scope, prerequisites satisfied, current chain
    level, and (in breadth mode) capped to the broadest level still to learn. Does
    not yet apply the review due date."""
    out = []
    for q in bank.questions:
        if not scope.matches(q):
            continue
        if not _prereqs_met(q, state, now, params):
            continue
        if not _is_current_chain_level(q, bank, state, now, params):
            continue
        out.append(q)

    if params.learning_mode == LearningMode.BREADTH and out:
        out = _breadth_level_cap(out, state, now, params)
    return out


def _effective_balance_weight(params: EngineParams) -> float:
    """Resolve how hard balancing pulls, from the learning mode.

    depth -> no balancing (verticalize); breadth -> strong balancing; balanced ->
    the configured balance_weight.
    """
    if params.learning_mode == LearningMode.DEPTH:
        return 0.0
    if params.learning_mode == LearningMode.BREADTH:
        return max(params.balance_weight, 1.0) * 3.0
    return params.balance_weight


def select_next(
    bank: QuestionBank,
    state: UserState,
    scope: Scope,
    params: EngineParams | None = None,
    now: float = 0.0,
    rng: random.Random | None = None,
) -> Question | None:
    """Pick the next question, or None if the scope is exhausted.

    Priority:
    1. among "due" candidates (due_at <= now), prefer the least-covered topic
       (balancing, weighted by the learning mode) and, on a tie, the lowest
       effective mastery;
    2. the final choice among equivalent candidates is random.
    """
    rng = rng or random
    params = params or EngineParams()

    candidates = candidate_questions(bank, state, scope, params, now=now)
    if not candidates:
        return None

    due = [q for q in candidates if state.get(q.id).due_at <= now]
    pool = due or candidates  # if nothing is due, still consider the candidates

    coverage = _topic_coverage(bank, state, scope, now, params)
    weight = _effective_balance_weight(params)

    def priority(q: Question) -> tuple[float, float]:
        # smaller = picked first: least-covered topic, then lowest effective mastery
        cov = coverage.get(q.topic, 0.0) * weight
        return (cov, _eff(state, q.id, now, params))

    pool.sort(key=priority)
    best = priority(pool[0])
    top = [q for q in pool if priority(q) == best]
    return rng.choice(top)


# ---------------------------------------------------------------------------
# Applying a grade
# ---------------------------------------------------------------------------

def apply_grade(
    state: UserState,
    question: Question,
    grade: Grade,
    params: EngineParams | None = None,
    now: float = 0.0,
) -> UserState:
    """Return a new UserState with the question's state updated.

    Mastery changes are applied on top of the *effective* (decay-adjusted) mastery,
    so a positive grade after a long gap rebuilds from the decayed value rather
    than the stale stored one.

    - dont_know/unsure: mastery down, soon review (no advancement).
    - know: mastery up, far review, unlocks the next level in the chain.
    - know_well: like know but a longer interval (the chain tends to archive).
    """
    params = params or EngineParams()
    prev = state.get(question.id)
    base = effective_mastery(prev, now, params)

    if grade == Grade.DONT_KNOW:
        mastery = base * params.mastery_down
        due = now + params.interval_dont_know
    elif grade == Grade.UNSURE:
        mastery = base * ((1 + params.mastery_down) / 2)
        due = now + params.interval_unsure
    elif grade == Grade.KNOW:
        mastery = min(1.0, base + params.mastery_up)
        due = now + params.interval_know
    else:  # KNOW_WELL
        mastery = min(1.0, base + params.mastery_up * 1.5)
        due = now + params.interval_know_well

    new_state = QuestionState(
        mastery=round(mastery, 4),
        due_at=due,
        times_seen=prev.times_seen + 1,
        last_grade=grade,
    )
    new_states = dict(state.states)
    new_states[question.id] = new_state
    return UserState(profile=state.profile, bank_name=state.bank_name, states=new_states)


# ---------------------------------------------------------------------------
# Coverage / mastery helpers (also used by the UI and tests)
# ---------------------------------------------------------------------------

def chain_mastered(
    bank: QuestionBank,
    state: UserState,
    chain: str,
    params: EngineParams | None = None,
    now: float = 0.0,
) -> bool:
    """True if every level of a chain is mastered, after decay. A chain you stop
    reviewing therefore drops out of 'mastered' over time."""
    params = params or EngineParams()
    qs = bank.chain_questions(chain)
    if not qs:
        return False
    return all(
        effective_mastery(state.get(q.id), now, params) >= params.mastery_threshold
        for q in qs
    )
