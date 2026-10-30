"""NailIt domain models.

They separate three things:
- the question bank (shareable content): QuestionBank / Question
- the study-mode self-assessment: Grade
- the user's learning progress (local state): UserState / QuestionState

The Pydantic validators are the first line of structural validation; the JSON
Schema under schema/ remains the formal contract used by the `validate` command.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class QuestionType(str, Enum):
    """Question type. Only choice types are auto-gradable."""

    OPEN = "open"
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"


#: Choice types: they have options and a correct answer, so they can be auto-graded.
CHOICE_TYPES = {
    QuestionType.SINGLE_CHOICE,
    QuestionType.MULTIPLE_CHOICE,
    QuestionType.TRUE_FALSE,
}


class Grade(str, Enum):
    """Study-mode self-assessment (worst to best)."""

    DONT_KNOW = "dont_know"
    UNSURE = "unsure"
    KNOW = "know"
    KNOW_WELL = "know_well"


class Question(BaseModel):
    """A single question in the bank.

    Choice types require `choices` and `correct`; `open` requires `answer`.
    `level_chain` links the rising levels of the same topic (incremental
    progression); `prerequisites` lists ids that must be passed first.
    """

    id: str
    domain: str
    topic: str
    level: int = Field(ge=1, description="Difficulty, starts at 1")
    type: QuestionType
    question: str
    answer: str | None = None
    choices: list[str] | None = None
    correct: list[int] | None = None
    explanation: str | None = None
    tags: list[str] = Field(default_factory=list)
    level_chain: str | None = None
    prerequisites: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_by_type(self) -> "Question":
        if self.type in CHOICE_TYPES:
            if not self.choices:
                raise ValueError(
                    f"question '{self.id}': choice types require a non-empty 'choices'"
                )
            if not self.correct:
                raise ValueError(
                    f"question '{self.id}': choice types require a non-empty 'correct'"
                )
            n = len(self.choices)
            bad = [i for i in self.correct if i < 0 or i >= n]
            if bad:
                raise ValueError(
                    f"question '{self.id}': 'correct' indices out of range {bad} "
                    f"(there are {n} options)"
                )
            if self.type in (QuestionType.SINGLE_CHOICE, QuestionType.TRUE_FALSE):
                if len(self.correct) != 1:
                    raise ValueError(
                        f"question '{self.id}': {self.type.value} requires exactly "
                        f"one correct answer"
                    )
            if self.type == QuestionType.TRUE_FALSE and n != 2:
                raise ValueError(
                    f"question '{self.id}': true_false requires exactly 2 options"
                )
        else:  # OPEN
            if not self.answer:
                raise ValueError(
                    f"question '{self.id}': 'open' questions require 'answer'"
                )
        return self

    def explanation_text(self) -> str | None:
        """Text to show when the user asks for the explanation."""
        if self.explanation:
            return self.explanation
        return self.answer

    def is_choice_correct(self, given: list[int]) -> bool:
        """For a choice question, whether the given indices match the correct set.
        The single source of truth used by both study and assessment modes."""
        return set(given) == set(self.correct or [])

    def correct_choice_text(self) -> str:
        """Human-readable correct answer(s) for a choice question."""
        if not self.choices or not self.correct:
            return ""
        return ", ".join(self.choices[i] for i in self.correct if 0 <= i < len(self.choices))


class QuestionBank(BaseModel):
    """A collection of questions with metadata. The framework's shared content."""

    name: str
    version: str = "1"
    questions: list[Question]

    @model_validator(mode="after")
    def _check_integrity(self) -> "QuestionBank":
        ids = [q.id for q in self.questions]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate ids in the bank: {dupes}")
        id_set = set(ids)
        for q in self.questions:
            missing_prereq = [p for p in q.prerequisites if p not in id_set]
            if missing_prereq:
                raise ValueError(
                    f"question '{q.id}': unknown prerequisites {missing_prereq}"
                )
        return self

    # --- convenience helpers for the engine and use cases ---------------------

    def by_id(self) -> dict[str, Question]:
        return {q.id: q for q in self.questions}

    def domains(self) -> list[str]:
        return sorted({q.domain for q in self.questions})

    def topics(self, domain: str) -> list[str]:
        return sorted({q.topic for q in self.questions if q.domain == domain})

    def chain_questions(self, chain: str) -> list[Question]:
        """Questions of a chain, ordered by ascending level."""
        qs = [q for q in self.questions if q.level_chain == chain]
        return sorted(qs, key=lambda q: q.level)


class QuestionState(BaseModel):
    """Learning state for a single question and user."""

    mastery: float = 0.0  # 0..1
    due_at: float = 0.0  # epoch timestamp; 0 = available immediately
    times_seen: int = 0
    last_grade: Grade | None = None


class UserState(BaseModel):
    """A profile's progress on a specific bank. Kept separate from the content."""

    profile: str
    bank_name: str
    states: dict[str, QuestionState] = Field(default_factory=dict)

    def get(self, question_id: str) -> QuestionState:
        return self.states.get(question_id, QuestionState())
