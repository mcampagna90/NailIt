"""Assessment mode — test generation and grading.

Unlike study mode there is no self-assessment: the candidate answers, the system
auto-grades the choice questions. 'open' questions are flagged for human review
(AI grading = future work). Assessment data is kept separate from the study state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .models import CHOICE_TYPES, Question, QuestionBank


@dataclass
class TestSpec:
    """Criteria to build a test."""

    domain: str
    topics: list[str] | None = None
    levels: list[int] | None = None
    num_questions: int = 10


@dataclass
class Test:
    spec: TestSpec
    questions: list[Question]


@dataclass
class QuestionResult:
    question_id: str
    correct: bool | None  # None = requires human review (open)
    needs_human_review: bool
    given: list[int] | str | None


@dataclass
class TestResult:
    details: list[QuestionResult] = field(default_factory=list)
    auto_scored: int = 0
    auto_correct: int = 0
    needs_review: int = 0

    @property
    def score(self) -> float:
        """Percentage over the auto-gradable questions only (0..1)."""
        if self.auto_scored == 0:
            return 0.0
        return self.auto_correct / self.auto_scored


def build_test(
    bank: QuestionBank, spec: TestSpec, rng: random.Random | None = None
) -> Test:
    """Select the questions with a balanced mix across topics."""
    rng = rng or random
    pool = [
        q
        for q in bank.questions
        if q.domain == spec.domain
        and (spec.topics is None or q.topic in spec.topics)
        and (spec.levels is None or q.level in spec.levels)
    ]
    if not pool:
        return Test(spec=spec, questions=[])

    # Group by topic and draw round-robin to balance the mix.
    by_topic: dict[str, list[Question]] = {}
    for q in pool:
        by_topic.setdefault(q.topic, []).append(q)
    for qs in by_topic.values():
        rng.shuffle(qs)

    selected: list[Question] = []
    topics = list(by_topic.keys())
    rng.shuffle(topics)
    i = 0
    while len(selected) < spec.num_questions and any(by_topic.values()):
        topic = topics[i % len(topics)]
        if by_topic[topic]:
            selected.append(by_topic[topic].pop())
        i += 1
    return Test(spec=spec, questions=selected)


def grade_test(test: Test, answers: dict[str, list[int] | str]) -> TestResult:
    """Grade the test. `answers`: id -> chosen indices (choice) or text (open)."""
    result = TestResult()
    for q in test.questions:
        given = answers.get(q.id)
        if q.type in CHOICE_TYPES:
            is_correct = isinstance(given, list) and q.is_choice_correct(given)
            result.details.append(
                QuestionResult(
                    question_id=q.id,
                    correct=is_correct,
                    needs_human_review=False,
                    given=given,
                )
            )
            result.auto_scored += 1
            if is_correct:
                result.auto_correct += 1
        else:  # OPEN: no automatic score in v1
            result.details.append(
                QuestionResult(
                    question_id=q.id,
                    correct=None,
                    needs_human_review=True,
                    given=given,
                )
            )
            result.needs_review += 1
    return result
