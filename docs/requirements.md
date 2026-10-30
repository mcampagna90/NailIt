# Requirements — NailIt

## Introduction

NailIt is an open-source Python framework to **revise for a technical interview**
and, using the same data format, to **run assessments** on candidates. The goal is
not to know everything, but to walk into the interview able to answer most
questions with confidence.

The core of the product is not the engine but the **standardized question-bank
format**: a clear, validatable contract. Anyone — an individual studying or a
company evaluating candidates — loads their own question bank conforming to the
format, and the framework provides two functions on top of it:

- **Study mode:** the user picks a domain (e.g. networking, cybersecurity) and
  optionally a topic, receives random questions, **self-assesses**, and if they do
  not know the answer, reveals the explanation. Learning is **incremental**: from
  simple questions to increasingly detailed ones as they master a chain
  (verticalization), but **balanced** to keep an overview.
- **Assessment mode:** a company builds an ad-hoc test from the same question bank
  and runs it on candidates. Here self-assessment does not exist: grading is
  objective and automatic on choice questions.

### Guiding principles
- **The data format is the contract:** a question bank is a file conforming to a
  formal schema, validatable by a command, with a bundled reference example.
- **Content / state / engine separation:** the question bank is reusable,
  shareable content; the user's learning state is separate; the engine consumes
  both.
- **Local and service-free for v1:** no dependency on paid services or the network
  to study. AI grading of open answers is explicit future work.
- **No invented content:** explanations and answers come from the question bank;
  the framework does not generate content.
- **Two modes, one format:** study and assessment consume the same question bank
  with no duplication of content.

## Requirements

### Requirement 1 — Standardized question-bank format
**User story:** As a content author (individual or company), I want a clear,
documented format for questions, so I can build my bank without ambiguity and
share it.

#### Acceptance criteria
1. The system SHALL define a **question-bank** format with, per question: a stable
   identifier, domain, topic, difficulty level, type, question text,
   answer/explanation and optional tags.
2. The system SHALL support the following question **types**: `open` (free answer),
   `single_choice` (one correct), `multiple_choice` (several correct) and
   `true_false`.
3. For choice types the system SHALL require the list of options and the correct
   answer(s); for `open` it SHALL require a textual answer/explanation.
4. The system SHALL support a **level chain** per topic (e.g. level 1 → 2 → 3 on
   the same chain) via a chain identifier, to enable incremental progression.
5. The system SHALL allow optional **prerequisites** between questions (references
   to existing ids), so an advanced question is not shown before the basics.
6. The format SHALL be serializable in **JSON** (and YAML) in **UTF-8**, with
   stable fields, so it is versionable and shareable across projects.

### Requirement 2 — Question-bank validation
**User story:** As an author, I want to know immediately whether my question bank
is correct, so I avoid errors when I load it into the framework.

#### Acceptance criteria
1. The system SHALL provide a **formal schema** (JSON Schema) of the format and a
   command to validate a file against it.
2. WHEN a question bank is loaded THEN the system SHALL check: unique ids,
   required fields present for the declared type, correct-answer indices valid
   against the options, levels as positive integers.
3. The system SHALL verify that every **prerequisite** and every **level-chain**
   reference points to an existing id in the same bank.
4. IF validation fails THEN the system SHALL report **readable errors** with the id
   and field involved, without stopping at the first error.
5. The system SHALL refuse to load an invalid bank into the engine, so it never
   operates on malformed data.
6. The system SHALL include a **reference example bank** (e.g. cybersecurity with a
   multi-level RBAC chain) conforming to the schema.

### Requirement 3 — Domain and topic selection (Study mode)
**User story:** As a studying user, I want to choose what to revise, so I focus on
what I need for the interview.

#### Acceptance criteria
1. The system SHALL allow selecting a **domain** (e.g. networking, cybersecurity)
   among those present in the loaded question bank.
2. The system SHALL optionally allow narrowing to a specific **topic** within the
   domain; if unspecified, it uses all topics.
3. The system SHALL let the user **discover** the selectable domains, topics and
   levels of a bank without starting a session, so they know what to pass.
4. WHEN a selection contains no questions THEN the system SHALL warn clearly,
   without starting an empty session.
5. The system SHALL present questions in **random** order, compatible with the
   level and review constraints defined by the engine (Requirement 5).

### Requirement 4 — Answering, grading and explanation (Study mode)
**User story:** As a studying user, I want to know whether I actually answered
correctly (not just guess), see why, and have the system record an honest outcome.

#### Acceptance criteria
1. WHEN a question is asked THEN the system SHALL show only the question (and, for
   choice types, the options) and wait for the user's input before revealing the
   answer.
2. FOR **choice questions** (`single_choice`, `multiple_choice`, `true_false`) the
   system SHALL let the user **select an option**, then SHALL state objectively
   whether the answer was **correct or wrong** (showing the correct answer when
   wrong), using the bank's `correct` field. FOR `multiple_choice` the system SHALL
   **visually highlight** that more than one option may be correct, reusing an
   inline hint in the question text when present instead of duplicating it.
3. FOR choice questions the system SHALL ask the user's **confidence before
   revealing the outcome**, and SHALL **derive the grade** by combining result and
   confidence: correct+confident → "know it"; correct+guessing → "unsure";
   wrong → "don't know". A lucky guess therefore SHALL NOT count as fully known.
4. FOR **open questions** (`open`) the system SHALL reveal the answer on request and
   offer a **multi-level self-assessment**: "don't know", "unsure", "know it",
   "know it well".
5. AFTER answering the system SHALL show the question's **explanation** (the "why"),
   drawn only from the bank — the system SHALL NOT invent explanations.
6. The system SHALL record each outcome in the **user state**, without modifying the
   question bank.
7. The system SHALL let the user **review their saved results** at any time
   (including after quitting a session), showing per-topic coverage and mastery and
   which chains are mastered, based on the persisted state.

### Requirement 5 — Incremental, balanced learning (selection engine)
**User story:** As a studying user, I want to start from simple questions and rise
in detail as I master a topic, while keeping an overview, so I arrive confident but
not stuck on a single theme.

#### Acceptance criteria
1. The system SHALL track, per user and per question, a **mastery** level and the
   scheduling of the next **review**, with a spaced-repetition-style logic.
2. WHEN the user grades "don't know"/"unsure" THEN the system SHALL re-present the
   question **sooner** and SHALL NOT advance the level of the chain.
3. WHEN the user grades "know it" on a question in a chain THEN the system SHALL
   present the **next-level version** of the same chain, if it exists
   (verticalization), respecting any prerequisites.
4. WHEN a chain is mastered (high levels solved confidently) THEN the system SHALL
   **rebalance** toward less-covered topics in the same domain, to ensure the
   overview.
5. The system SHALL NOT present a question whose **prerequisites** have not yet
   been passed successfully.
6. The system SHALL expose this logic as a **reusable function/component** ("given
   the state + the bank, pick the next question"), independent of the interface.

### Requirement 5b — Learning mode (depth / breadth / balanced)
**User story:** As a studying user, I want to choose whether to go deep on a topic
or broaden my coverage across a whole domain, so the session matches my goal.

#### Acceptance criteria
1. The system SHALL support a configurable **learning mode** with at least:
   `depth`, `breadth` and `balanced` (default `balanced`).
2. IN `depth` mode the system SHALL verticalize freely: as soon as a level is
   unlocked, deeper levels of the same chain are eligible, with no pull toward
   other topics.
3. IN `breadth` mode the system SHALL cover the **basics of every topic** in scope
   before offering deeper levels: it SHALL NOT present a level-N+1 question while
   any level-N question in scope is still unknown.
4. IN `balanced` mode the system SHALL combine verticalization and cross-topic
   balancing (the default behaviour of Requirement 5).
5. The learning mode SHALL be selectable from configuration and overridable per
   session (e.g. a CLI option), without editing code.

### Requirement 5c — Mastery decay over time (no free lunch on neglect)
**User story:** As a studying user, I want topics I stop reviewing to stop counting
as fully known, so returning after a long gap re-surfaces what I have likely
forgotten rather than trusting stale mastery.

#### Acceptance criteria
1. The system SHALL apply a **time-based decay** to mastery: the longer a question
   stays past its review due date, the lower its **effective mastery**.
2. The decay SHALL be **lazy** — computed at selection time from elapsed time —
   and SHALL NOT modify the stored mastery of a question that was not answered.
3. WHEN effective mastery falls below the relevant threshold THEN the affected
   questions SHALL become eligible again, a chain MAY drop out of "mastered", and a
   neglected **prerequisite** MAY re-lock its dependent questions until refreshed.
4. The decay rate (half-life) SHALL be configurable, and decay SHALL be
   **disableable** (half-life of 0) with reasonable defaults otherwise.
5. WHEN the user answers a question after a gap THEN the grade SHALL be applied on
   top of the **decayed** effective mastery, not the stale stored value.

### Requirement 6 — Persistent, multi-user learning state
**User story:** As a user, I want my progress saved between sessions and kept
separate from others', so I can resume where I left off.

#### Acceptance criteria
1. The system SHALL persist the **learning state** (mastery, review due dates,
   outcome history) locally, separate from the question bank.
2. The system SHALL keep **distinct states per user/profile**.
3. WHEN a session resumes THEN the system SHALL restart from the saved state.
4. The system SHALL identify questions by **stable id**, so state is preserved even
   if the bank is updated (new questions = no state).
5. The system SHALL let the user **list the existing profiles** (and the banks they
   have progress on) without starting a session.

### Requirement 7 — Building and running tests (Assessment mode)
**User story:** As a company, I want to build an ad-hoc test from my question bank
and run it on candidates with automatic grading.

#### Acceptance criteria
1. The system SHALL allow generating a **test** by selecting domain(s), topic(s),
   levels and the number of questions, with a **balanced** mix per topic/level.
2. The system SHALL run the test **without self-assessment**: the candidate
   provides answers, not judgments about themselves.
3. The system SHALL **auto-grade** the choice questions (`single_choice`,
   `multiple_choice`, `true_false`) by comparing against the format's correct
   answers.
4. IF a test includes `open` questions THEN the system SHALL mark them as
   "requires human review" and SHALL NOT assign them an automatic score (AI
   grading = explicit future work).
5. The system SHALL produce a **result** with the score over the auto-gradable
   questions and the per-question detail.
6. The system SHALL keep assessment data **separate** from the study state, while
   sharing the same question bank.

### Requirement 8 — Interfaces (CLI first, API later)
**User story:** As a user, I want to use the framework from the command line to
validate and study right away, and as a developer I want to be able to build an API
on top of it.

#### Acceptance criteria
1. The system SHALL offer a **CLI** with at least these commands: validate a
   question bank, **list the selectable domains/topics/levels** of a bank, start a
   study session, and (phase 2) serve the API.
2. The system SHALL expose the engine and use cases as an importable **library**,
   independent of the interface, so it is reusable from CLI, API or mobile.
3. The system SHALL provide, as **phase 2**, an **API** (FastAPI) exposing study
   and assessment on top of the same library, without duplicating the logic.
4. The **mobile app** is explicitly **out of scope** for v1 and will consume the
   API later.

### Requirement 9 — Configuration
**User story:** As a user, I want to configure the question-bank path, the user
profile and the engine parameters in a versionable way.

#### Acceptance criteria
1. The system SHALL read from **configuration**: the question-bank path, the active
   profile/user, the state path and the engine parameters (e.g. review intervals,
   mastery thresholds, degree of balancing).
2. The system SHALL work with **reasonable defaults** if the configuration does not
   specify the engine parameters.
3. The configuration SHALL be a versionable file (e.g. YAML) with a bundled
   example.
