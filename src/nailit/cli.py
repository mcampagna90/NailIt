"""NailIt CLI.

A thin adapter over the use cases: bank validation and the interactive study
mode. The API (phase 2) will reuse the same use cases.
"""

from __future__ import annotations

import dataclasses
import re
import time
from importlib import resources

import typer

from .config import Config
from .loader import BankValidationError, load_bank, validate_bank
from .models import CHOICE_TYPES, Grade, Question, QuestionType
from .scheduler import LearningMode, grade_from_outcome
from .store import list_states, load_state
from .study import StudySession, summarize_progress

app = typer.Typer(help="NailIt — revise and walk into your technical interview with confidence.")

# Maps the shortcut keys to the self-assessment grade.
_GRADE_KEYS = {
    "1": Grade.DONT_KNOW,
    "2": Grade.UNSURE,
    "3": Grade.KNOW,
    "4": Grade.KNOW_WELL,
}
_GRADE_LABEL = {
    Grade.DONT_KNOW: "don't know",
    Grade.UNSURE: "unsure",
    Grade.KNOW: "know it",
    Grade.KNOW_WELL: "know it well",
}


def _default_bank() -> str:
    return str(resources.files("nailit.data").joinpath("sample_cybersecurity.json"))


@app.command()
def validate(
    bank: str = typer.Argument(..., help="Path to the question bank (JSON/YAML).")
) -> None:
    """Validate a question bank against the schema."""
    errors = validate_bank(bank)
    if errors:
        typer.secho(f"Bank is INVALID ({len(errors)} errors):", fg=typer.colors.RED)
        for e in errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)
    typer.secho("Bank is valid.", fg=typer.colors.GREEN)


@app.command()
def profiles(
    config_path: str = typer.Option(None, "--config", "-c", help="Configuration file."),
) -> None:
    """List the profiles that have saved progress, and on which banks.

    A profile is just the name you pass to `study --profile`; the default is
    'default'. This shows the ones that already exist in your state directory.
    """
    config = Config.load(config_path)
    states = list_states(config.state_dir)
    if not states:
        typer.secho(
            f"No profiles yet in '{config.state_dir}'. One is created the first "
            f"time you run `study` (default profile: 'default').",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(f"Profiles with saved progress (in '{config.state_dir}'):\n", bold=True)
    by_profile: dict[str, list[str]] = {}
    for st in states:
        by_profile.setdefault(st.profile, []).append(st.bank_name)
    for profile in sorted(by_profile):
        banks = ", ".join(sorted(by_profile[profile]))
        typer.secho(f"  • {profile}", fg=typer.colors.CYAN, nl=False)
        typer.echo(f"   (banks: {banks})")


@app.command()
def topics(
    bank: str = typer.Argument(None, help="Question bank; defaults to the bundled sample."),
    domain: str = typer.Option(
        None, "--domain", "-d", help="Only show the topics of this domain."
    ),
) -> None:
    """List what you can study: domains, their topics, and the levels available.

    Use this to discover the values to pass to `study --domain/--topic`.
    """
    bank_path = bank or _default_bank()
    try:
        loaded = load_bank(bank_path)
    except BankValidationError as exc:
        typer.secho("Could not load the bank:", fg=typer.colors.RED)
        for e in exc.errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)

    domains = [domain] if domain else loaded.domains()
    if domain and domain not in loaded.domains():
        typer.secho(f"Domain '{domain}' not found.", fg=typer.colors.RED)
        typer.echo("Available domains: " + ", ".join(loaded.domains()))
        raise typer.Exit(code=1)

    typer.secho(f"Bank: {loaded.name} ({len(loaded.questions)} questions)\n", bold=True)
    for dom in domains:
        dom_qs = [q for q in loaded.questions if q.domain == dom]
        typer.secho(f"{dom}  ({len(dom_qs)} questions)", fg=typer.colors.CYAN, bold=True)
        for top in loaded.topics(dom):
            top_qs = [q for q in dom_qs if q.topic == top]
            levels = sorted({q.level for q in top_qs})
            levels_str = ", ".join(str(lvl) for lvl in levels)
            typer.echo(f"  • {top}  ({len(top_qs)} questions, levels {levels_str})")
        typer.echo("")


def _mastery_bar(m: float, width: int = 10) -> str:
    filled = round(m * width)
    return "█" * filled + "░" * (width - filled)


def _render_domain_progress(loaded, state, domains, engine, now: float) -> None:
    """Print the per-domain learning score. Shared by `progress` and the
    end-of-session summary so both show the exact same thing."""
    for dom in domains:
        s = summarize_progress(loaded, state, dom, engine, now=now)
        typer.secho(
            f"{dom}  —  {s.seen}/{s.total} questions seen, "
            f"avg mastery {s.avg_mastery:.0%}",
            fg=typer.colors.CYAN,
            bold=True,
        )
        for tp in s.topics:
            typer.echo(
                f"  {_mastery_bar(tp.avg_mastery)}  {tp.avg_mastery:>4.0%}  "
                f"{tp.topic}  ({tp.seen}/{tp.total} seen)"
            )
        if s.chains:
            mastered = [c.chain for c in s.chains if c.mastered]
            in_progress = [c.chain for c in s.chains if not c.mastered]
            if mastered:
                typer.secho("  mastered chains: " + ", ".join(mastered), fg=typer.colors.GREEN)
            if in_progress:
                typer.echo("  still working on: " + ", ".join(in_progress))
        typer.echo("")


@app.command()
def progress(
    bank: str = typer.Argument(None, help="Question bank; defaults to the bundled sample."),
    domain: str = typer.Option(None, "--domain", "-d", help="Domain to report on."),
    profile: str = typer.Option("default", "--profile", "-p", help="User profile."),
    config_path: str = typer.Option(None, "--config", "-c", help="Configuration file."),
) -> None:
    """Show your saved results: how much of each topic you have covered and
    mastered. Run this after a study session (even if you quit with 'q')."""
    config = Config.load(config_path)
    config.profile = profile
    bank_path = bank or config.bank_path or _default_bank()

    try:
        loaded = load_bank(bank_path)
    except BankValidationError as exc:
        typer.secho("Could not load the bank:", fg=typer.colors.RED)
        for e in exc.errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)

    domains = [domain] if domain else loaded.domains()
    if domain and domain not in loaded.domains():
        typer.secho(f"Domain '{domain}' not found.", fg=typer.colors.RED)
        typer.echo("Available domains: " + ", ".join(loaded.domains()))
        raise typer.Exit(code=1)

    state = load_state(config.state_dir, profile, loaded.name)
    if not state.states:
        typer.secho(
            f"No progress yet for profile '{profile}' on bank '{loaded.name}'. "
            f"Start with: nailit study --domain {loaded.domains()[0]}",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(f"Progress — profile '{profile}', bank '{loaded.name}'\n", bold=True)
    _render_domain_progress(loaded, state, domains, config.engine, now=time.time())


class _Quit(Exception):
    """Raised by the question helpers when the user types 'q'."""


def _show_explanation(q: Question) -> None:
    expl = StudySession.explanation(q)
    if expl:
        typer.secho("Why: ", fg=typer.colors.YELLOW, nl=False)
        typer.echo(expl)


# A parenthetical in the question text that already hints "more than one answer".
_MULTI_HINT = re.compile(r"\((?:[^()]*\b(?:multiple answers|select all|choose all)\b[^()]*)\)",
                         re.IGNORECASE)


def _print_question(q: Question) -> None:
    """Print the question. For multiple-choice, highlight the 'multiple answers'
    hint in orange — reusing the one already in the text if present, instead of
    adding a redundant second line."""
    if q.type == QuestionType.MULTIPLE_CHOICE:
        m = _MULTI_HINT.search(q.question)
        if m:
            # Color only the existing hint, keep the rest of the question bold.
            before, hint, after = q.question[:m.start()], m.group(0), q.question[m.end():]
            typer.secho(before, bold=True, nl=False)
            typer.secho(hint, fg=typer.colors.BRIGHT_YELLOW, bold=True, nl=False)
            typer.secho(after, bold=True)
            return
        # No hint in the text: show the question, then a short orange tag.
        typer.secho(q.question, bold=True)
        typer.secho("(multiple answers)", fg=typer.colors.BRIGHT_YELLOW, bold=True)
        return
    typer.secho(q.question, bold=True)


def _ask_choice_question(q: Question) -> Grade:
    """Choice question: the machine knows the answer, so it tells you right/wrong.

    Confidence is asked *before* revealing the outcome, so a lucky guess cannot be
    re-labelled as "I knew it". The grade combines the objective result with that
    confidence.
    """
    multi = q.type == QuestionType.MULTIPLE_CHOICE
    hint = "indices separated by commas" if multi else "the index"
    while True:
        raw = typer.prompt(f"\nYour answer ({hint}, q = quit)").strip().lower()
        if raw == "q":
            raise _Quit
        try:
            given = [int(tok) for tok in raw.replace(",", " ").split()]
        except ValueError:
            typer.echo("Enter the option number(s), e.g. 0 or 1,2.")
            continue
        if given and all(0 <= i < len(q.choices or []) for i in given):
            break
        typer.echo(f"Pick from 0..{len(q.choices or []) - 1}.")

    # Confidence BEFORE the outcome — this is what keeps guessing honest.
    conf = typer.prompt("Were you confident? [y/n]").strip().lower()
    was_confident = conf.startswith("y")

    is_correct = q.is_choice_correct(given)
    if is_correct:
        typer.secho("Correct ✓", fg=typer.colors.GREEN)
    else:
        typer.secho("Wrong ✗", fg=typer.colors.RED, nl=False)
        typer.echo(f" — the correct answer was: {q.correct_choice_text()}")
    _show_explanation(q)
    return grade_from_outcome(is_correct, was_confident)


def _ask_open_question(q: Question) -> Grade:
    """Open question: no objective answer to check, so you self-assess 1-4."""
    cmd = typer.prompt("\n(Enter = show answer, q = quit)", default="", show_default=False)
    if cmd.strip().lower() == "q":
        raise _Quit

    answer = q.answer or ""
    if answer:
        typer.secho("Answer: ", fg=typer.colors.YELLOW, nl=False)
        typer.echo(answer)
    expl = StudySession.explanation(q)
    if expl and expl != answer:
        _show_explanation(q)

    while True:
        key = typer.prompt("How did it go? [1/2/3/4] (q = quit)").strip().lower()
        if key == "q":
            raise _Quit
        grade = _GRADE_KEYS.get(key)
        if grade is not None:
            return grade
        typer.echo("Enter 1, 2, 3 or 4.")


@app.command()
def study(
    bank: str = typer.Argument(None, help="Question bank; defaults to the bundled sample."),
    domain: str = typer.Option(None, "--domain", "-d", help="Domain to revise."),
    topic: str = typer.Option(None, "--topic", "-t", help="Specific topic (optional)."),
    profile: str = typer.Option("default", "--profile", "-p", help="User profile."),
    mode: LearningMode = typer.Option(
        None, "--mode", "-m",
        help="Learning mode: depth (go deep), breadth (cover basics first), "
             "or balanced. Overrides the config.",
    ),
    config_path: str = typer.Option(None, "--config", "-c", help="Configuration file."),
) -> None:
    """Start an interactive revision session (study mode)."""
    config = Config.load(config_path)
    config.profile = profile
    if mode is not None:
        config.engine = dataclasses.replace(config.engine, learning_mode=mode)
    bank_path = bank or config.bank_path or _default_bank()

    try:
        loaded = load_bank(bank_path)
    except BankValidationError as exc:
        typer.secho("Could not load the bank:", fg=typer.colors.RED)
        for e in exc.errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)

    if domain is None:
        typer.echo("Available domains: " + ", ".join(loaded.domains()))
        domain = typer.prompt("Which domain do you want to revise?")
    if domain not in loaded.domains():
        typer.secho(f"Domain '{domain}' not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        session = StudySession.start(bank_path, domain, topic, config)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    scope_label = f"{domain}" + (f" / {topic}" if topic else "")
    typer.secho(f"\nRevision session: {scope_label} (profile: {profile})", bold=True)
    typer.echo("Choice questions are checked automatically; open questions you grade yourself.")
    typer.echo("Type 'q' at any prompt to quit.\n")

    answered = 0
    while True:
        q = session.next_question()
        if q is None:
            typer.secho(
                "No more questions to review for now. Great work!",
                fg=typer.colors.GREEN,
            )
            break

        typer.secho(f"[{q.domain}/{q.topic} · level {q.level}]", fg=typer.colors.CYAN)
        _print_question(q)
        if q.choices:
            for idx, choice in enumerate(q.choices):
                typer.echo(f"  {idx}) {choice}")

        try:
            if q.type in CHOICE_TYPES:
                grade = _ask_choice_question(q)
            else:
                grade = _ask_open_question(q)
        except _Quit:
            typer.echo("See you soon!")
            break

        session.answer(q, grade)
        answered += 1
        typer.secho(f"  → marked: {_GRADE_LABEL[grade]}\n", fg=typer.colors.BLUE)

    # End-of-session learning score, shown automatically (also after quitting).
    if answered:
        typer.secho(
            f"\nSession summary — {answered} answered "
            f"(profile '{profile}', bank '{loaded.name}')",
            bold=True,
        )
        _render_domain_progress(loaded, session.state, [domain], config.engine, now=time.time())
        typer.echo("Tip: see this anytime with  nailit progress "
                   f"--domain {domain} --profile {profile}")


if __name__ == "__main__":
    app()
