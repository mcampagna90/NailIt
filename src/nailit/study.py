"""Study mode — an interface-independent use case.

Composes loader + store + scheduler. The CLI (and the API in phase 2) call these
methods without knowing the engine internals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Config
from .loader import load_bank
from .models import Grade, Question, QuestionBank, UserState
from .scheduler import (
    EngineParams,
    Scope,
    apply_grade,
    chain_mastered,
    effective_mastery,
    select_next,
)
from .store import load_state, save_state


class StudySession:
    """A revision session over a scope (domain + optional topic)."""

    def __init__(self, bank: QuestionBank, state: UserState, scope: Scope, config: Config):
        self.bank = bank
        self.state = state
        self.scope = scope
        self.config = config

    @classmethod
    def start(
        cls,
        bank_path: str,
        domain: str,
        topic: str | None = None,
        config: Config | None = None,
    ) -> "StudySession":
        config = config or Config()
        bank = load_bank(bank_path)
        if domain not in bank.domains():
            raise ValueError(
                f"domain '{domain}' not found. Available: {', '.join(bank.domains())}"
            )
        if topic is not None and topic not in bank.topics(domain):
            raise ValueError(
                f"topic '{topic}' not found in domain '{domain}'. "
                f"Available: {', '.join(bank.topics(domain))}"
            )
        state = load_state(config.state_dir, config.profile, bank.name)
        return cls(bank, state, Scope(domain=domain, topic=topic), config)

    def next_question(self, now: float | None = None) -> Question | None:
        """The next question, or None if the scope is exhausted for now."""
        now = time.time() if now is None else now
        return select_next(self.bank, self.state, self.scope, self.config.engine, now=now)

    def answer(self, question: Question, grade: Grade, now: float | None = None) -> None:
        """Record the self-assessment and persist the state."""
        now = time.time() if now is None else now
        self.state = apply_grade(self.state, question, grade, self.config.engine, now=now)
        save_state(self.config.state_dir, self.state)

    @staticmethod
    def explanation(question: Question) -> str | None:
        """Text shown when the user does not know / asks for the explanation."""
        return question.explanation_text()


# ---------------------------------------------------------------------------
# Progress summary (used by `nailit progress`)
# ---------------------------------------------------------------------------

@dataclass
class TopicProgress:
    topic: str
    total: int = 0           # questions in this topic
    seen: int = 0            # questions answered at least once
    avg_mastery: float = 0.0  # average effective mastery over all questions


@dataclass
class ChainProgress:
    chain: str
    mastered: bool


@dataclass
class ProgressSummary:
    profile: str
    bank_name: str
    domain: str
    total: int = 0
    seen: int = 0
    avg_mastery: float = 0.0
    topics: list[TopicProgress] = field(default_factory=list)
    chains: list[ChainProgress] = field(default_factory=list)


def summarize_progress(
    bank: QuestionBank,
    state: UserState,
    domain: str,
    params: EngineParams | None = None,
    now: float = 0.0,
) -> ProgressSummary:
    """Pure function: build a readable summary of a profile's progress on a domain.

    Mastery is the *effective* (decay-adjusted) value, so the summary reflects what
    the engine currently believes, not stale stored numbers.
    """
    params = params or EngineParams()
    qs = [q for q in bank.questions if q.domain == domain]
    summary = ProgressSummary(
        profile=state.profile, bank_name=state.bank_name, domain=domain, total=len(qs)
    )
    if not qs:
        return summary

    by_topic: dict[str, list[Question]] = {}
    for q in qs:
        by_topic.setdefault(q.topic, []).append(q)

    mastery_sum = 0.0
    for topic in sorted(by_topic):
        tqs = by_topic[topic]
        tp = TopicProgress(topic=topic, total=len(tqs))
        t_sum = 0.0
        for q in tqs:
            st = state.get(q.id)
            m = effective_mastery(st, now, params)
            t_sum += m
            mastery_sum += m
            if st.times_seen > 0:
                tp.seen += 1
                summary.seen += 1
        tp.avg_mastery = round(t_sum / len(tqs), 3)
        summary.topics.append(tp)

    summary.avg_mastery = round(mastery_sum / len(qs), 3)

    for chain in sorted({q.level_chain for q in qs if q.level_chain}):
        summary.chains.append(
            ChainProgress(chain=chain, mastered=chain_mastered(bank, state, chain, params, now))
        )
    return summary
