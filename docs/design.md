# Design — NailIt

## Overview

NailIt is a **Python library** with a **CLI** on top. The central product is the
**question-bank format**: a JSON/YAML file conforming to a JSON Schema. On top of
that format the library offers two use cases that share the same content but
different state data:

- **Study mode** — the user picks a domain/topic, receives random questions,
  self-assesses, reveals the explanation. A **selection engine** (spaced
  repetition + per-level progression) decides the next question: it verticalizes
  when the user knows, and rebalances when the chain is mastered.
- **Assessment mode** — a company generates a balanced test from the same bank,
  runs it without self-assessment, and auto-grades the choice questions.

### Guiding principles
- **The data format is the contract:** a bank is valid only if it passes the schema.
- **Content / state / engine separation:** bank (content, shareable) + user state
  (progress, local) + engine (pure logic, no I/O).
- **Core without interface:** the logic lives in the library; the CLI and (phase 2)
  the API are thin adapters.
- **No invented content:** explanations/answers come only from the bank.
- **v1 local:** no external services. AI grading of `open` answers = future phase.

## Architecture

```
                 +------------------+
  CLI / (API p2) |   Use cases      |   study.py / assessment.py
                 +------------------+
                     |         |
             +-------+         +--------+
             v                          v
        +---------+               +-----------+
        | Loader  |  validates    | Scheduler |  pure logic (no I/O)
        | +Schema |  the bank     | (engine)  |  "given state+bank -> next"
        +---------+               +-----------+
             |                          |
             v                          v
        +-----------+             +-------------+
        | QuestionBank|           | UserState   |
        | (content)   |           | (progress)  |
        +-----------+             +-------------+
             ^                          ^
             |                          |
      bank JSON/YAML             state store (local JSON)
```

### Study-mode flow
1. The CLI reads the configuration (bank path, user profile, engine parameters).
2. The Loader loads and **validates** the bank against the JSON Schema; if invalid,
   readable errors and stop.
3. The Store loads the user's state (or creates a new one).
4. The user selects a domain and optional topic; the pool is filtered.
5. The Scheduler picks the next question from the pool based on mastery, review due
   dates, chain level and prerequisites.
6. The CLI shows the question, collects the self-assessment, shows the explanation
   if requested, updates the state through the Scheduler and persists it.
7. It repeats until the user quits or the pool is exhausted for this session.

### Assessment-mode flow
1. The Loader validates the bank (as above).
2. The generator builds a test: selection by domain/topic/level + balanced mix +
   number of questions.
3. The test is run without self-assessment; answers are collected.
4. The grader auto-scores the choice questions; `open` questions are marked "human
   review" (no automatic score in v1).
5. A result is produced with the score over the auto-gradable questions and the
   per-question detail.

## Components

### `models.py` (Pydantic)
Defines the domain types and is the first line of structural validation.
- `QuestionType` — enum: `open`, `single_choice`, `multiple_choice`, `true_false`.
- `Question` — the format fields (see Data model). Validators: for choice types a
  non-empty `choices` and valid `correct` indices; for `open` a present `answer`;
  `level >= 1`. Also the single source of truth for grading: `is_choice_correct`
  and `correct_choice_text`, reused by both study and assessment.
- `QuestionBank` — `name`, `version`, `questions: list[Question]`. Cross-item
  validators: unique ids, consistent `prerequisites`/`level_chain`.
- `Grade` — self-assessment enum: `dont_know`, `unsure`, `know`, `know_well`.
- `QuestionState` — per-question state: `mastery` (0..1), `due_at` (when to
  re-present), `times_seen`, `last_grade`.
- `UserState` — `profile`, `bank_name`, `states: dict[question_id, QuestionState]`.

### `schema/question_bank.schema.json` (JSON Schema)
The formal, stable contract of the format, independent of Python. Used by the
`validate` command for precise error messages (JSON path + reason) and as
documentation of the format for external authors (companies).

### `loader.py`
- `load_bank(path) -> QuestionBank`: reads JSON/YAML, validates against the JSON
  Schema, then builds the Pydantic models. Collects **all** errors (does not stop
  at the first) and reports them readably (id/field). Rejects invalid banks.

### `scheduler.py` (the heart, pure logic)
No I/O, fully testable. Main functions:
- `effective_mastery(state, now, params) -> float`
  - The decay-adjusted mastery used by **every** decision in the engine. Before a
    question's `due_at` (or with decay disabled) it returns the stored mastery;
    once overdue, the value halves every `decay_half_life` seconds. It never writes
    back — decay is computed lazily at read time.
- `select_next(bank, state, scope) -> Question | None`
  - `scope` = domain + optional topic.
  - Builds the pool: in-scope questions whose **prerequisites** are already passed
    (by effective mastery); within a chain it proposes the **current level** (does
    not jump ahead if lower levels are not known).
  - In `breadth` mode the pool is capped to the lowest level not yet broadly known
    (cover every topic's level 1 before anyone's level 2).
  - Prioritizes overdue reviews; across chains prefers the **least-covered**
    (balancing), weighted by the learning mode (`depth` = 0, `breadth` = strong,
    `balanced` = configured). Final choice is **random** among equivalents.
- `apply_grade(state, question, grade, params) -> UserState`
  - Changes are applied on top of the **effective** (decayed) mastery, so a grade
    after a long gap rebuilds from the decayed value, not the stale stored one.
  - `dont_know`/`unsure`: soon `due_at`, mastery down, no advancement.
  - `know`: mastery up, farther `due_at`, unlocks the next level in the chain (if
    it exists and prerequisites are ok).
  - `know_well`: like `know` but a longer interval; the chain tends to "archive",
    freeing room for rebalancing.
- `chain_mastered(...)` / per-topic coverage: feed the balancing, both computed on
  effective mastery so a neglected chain drops out of "mastered" over time.
  Parameters (intervals, thresholds, balance weight, learning mode, decay
  half-life) injected from config with reasonable defaults.
- `grade_from_outcome(is_correct, was_confident) -> Grade`: pure mapping used for
  choice questions in study mode — correct+confident → `know`, correct+guessing →
  `unsure`, wrong → `dont_know`. Keeps lucky guesses from faking mastery.

`LearningMode` is an enum (`depth` / `breadth` / `balanced`) carried on
`EngineParams`; the CLI exposes it as `--mode` and the config as `learning_mode`.

### `store.py`
- Persists the **user state** in local JSON, per profile
  (`<state_dir>/<profile>__<bank_name>.json`). `load_state` / `save_state`. The
  state is separate from the bank; questions are referenced by stable id, so an
  updated bank does not invalidate the state (new questions start without state).

### `study.py` (use case)
Orchestration of study mode: composes loader + store + scheduler, exposes
`start`/`next_question`/`answer` independent of the interface, so the CLI and API
reuse them. Also `summarize_progress` (pure) for the progress report.

The CLI branches on question type: **choice** questions are answered by picking an
option, the result is checked objectively against the bank, confidence is asked
*before* the outcome, and the grade comes from `grade_from_outcome`. **Open**
questions keep the 1–4 self-assessment. Both paths then show the `explanation`.

### `assessment.py` (use case)
- `build_test(bank, spec) -> Test`: selection + balanced mix + N questions.
- `grade_test(test, answers) -> Result`: auto-grades the choices; `open` →
  `needs_human_review`. Produces a score + per-question detail. Assessment data is
  separate from the study state.

### `cli.py`
Thin adapter (Typer):
- `nailit validate <bank>` — validates and prints readable errors or OK.
- `nailit profiles` — lists the profiles that have saved progress (and their banks)
  from the state directory.
- `nailit topics <bank> [--domain]` — lists the selectable domains, their topics
  and the levels available, without starting a session.
- `nailit study <bank> [--profile] [--domain] [--topic] [--mode]` — interactive
  session.
- `nailit progress <bank> [--domain] [--profile]` — shows saved results (per-topic
  coverage/mastery and mastered chains) from the persisted state; readable after
  quitting a session. Formats the pure `summarize_progress` use-case output.
- `nailit serve` — (phase 2) starts the API.

### `api.py` — phase 2
FastAPI exposing study and assessment on top of the same use cases. No duplicated
logic. The **mobile app is out of scope for v1** and will consume the API later.

### `config.py`
Loads the configuration (YAML) with defaults: bank path, profile, `state_dir`,
engine parameters (review intervals, mastery thresholds, balance weight).
Reasonable defaults if absent. Bundled example (`config.example.yaml`).

## Data model

### `Question`
| Field | Type | Notes |
|---|---|---|
| id | str | stable, unique id in the bank |
| domain | str | e.g. `cybersecurity`, `networking` |
| topic | str | topic within the domain |
| level | int | difficulty, `>= 1` |
| type | enum | `open` / `single_choice` / `multiple_choice` / `true_false` |
| question | str | the question text |
| answer | str \| null | answer/explanation for `open`; optional for choices |
| choices | list[str] \| null | options (required for choice types) |
| correct | list[int] \| null | indices of the correct answers (choice types) |
| explanation | str \| null | deeper explanation shown after the answer |
| tags | list[str] | free tags |
| level_chain | str \| null | id of the chain linking the levels of the same topic |
| prerequisites | list[str] | ids of questions to pass first |

### `QuestionBank`
`name`, `version`, `questions: list[Question]`.

### `QuestionState` / `UserState`
`QuestionState`: `mastery`, `due_at`, `times_seen`, `last_grade`.
`UserState`: `profile`, `bank_name`, `states: {question_id: QuestionState}`.

## Incremental engine — selection rules (operational summary)
- **Verticalization:** within a `level_chain`, the proposed question is the one at
  the user's current level; `know`/`know_well` unlock the next level (if
  prerequisites are satisfied).
- **Balancing:** across chains/topics in scope, preference goes to the ones with
  the lowest coverage, so the user keeps the overview instead of sinking into a
  single theme. Weight adjustable from config and the learning mode.
- **Learning mode:** `depth` turns balancing off and verticalizes freely;
  `breadth` caps the pool to the lowest level still broadly unknown, covering every
  topic's basics before going deeper; `balanced` (default) mixes both.
- **Review (spaced repetition):** each outcome updates `due_at`; overdue reviews
  have priority. Intervals grow with mastery.
- **Decay:** mastery is read through `effective_mastery`, which lowers it the
  longer a question stays overdue (lazy, no write-back). Neglected topics resurface
  and can lose "mastered"/prerequisite status until refreshed.
- **Prerequisites:** a question does not enter the pool if its prerequisites have
  not been passed (by effective mastery).

## Advancement vs mastery (two distinct thresholds)
The engine separates two concepts that are easy to conflate:
- **`advance_threshold`** — proving you know a level (a single "know it" crosses
  it) is enough to unlock the next level in the chain and to satisfy a
  prerequisite.
- **`mastery_threshold`** — a higher bar reached only by consolidating through
  several positive reviews; it drives whether a chain is "mastered"/archivable and
  the balancing.

## Error handling
- Bank not conformant to the schema: list of readable errors (path + reason), no
  partial load into the engine.
- Scope with no questions: clear warning, no empty session.
- Missing/corrupt state: restart from a fresh state, without losing the bank.
- `open` question in a test: marked `needs_human_review`, never an automatic score.

## Testing strategy
- **Scheduler (priority):** pure unit tests on `select_next`/`apply_grade`:
  verticalization (know → level up), soon-repeat on `dont_know`, respect of
  prerequisites, balancing across topics, priority for overdue reviews.
- **Loader/Schema:** valid banks pass; malformed banks (duplicate ids, `correct`
  out of range, missing prerequisite, missing `choices`) fail with expected,
  complete errors (not just the first).
- **Store:** save/load round-trip of the state; distinct profiles isolated; an
  updated bank does not invalidate the existing state.
- **Assessment:** correct auto-grading for each choice type; `open` marked for
  review; balanced mix respects the test spec.
- **CLI:** `validate` returns a consistent exit code; `study` runs on a sample bank
  with simulated input.
- **Fixtures:** the cybersecurity sample bank (multi-level RBAC chain) used in the
  tests, no network dependency.

## Scope v1 vs later phases
- **v1:** format + schema + validation, models, loader, scheduler, store, study
  mode via CLI, assessment mode with auto-grading of choices, sample bank,
  configuration.
- **Phase 2:** FastAPI API on top of the same use cases.
- **Phase 3+:** AI grading of `open` answers, a mobile app consuming the API.
