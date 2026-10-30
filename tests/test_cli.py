"""Tests for the CLI commands (validate, topics)."""

from importlib import resources

from typer.testing import CliRunner

from nailit.cli import app

runner = CliRunner()


def _sample_path() -> str:
    return str(resources.files("nailit.data").joinpath("sample_cybersecurity.json"))


def test_validate_ok():
    result = runner.invoke(app, ["validate", _sample_path()])
    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()


def test_topics_lists_domains_and_topics():
    result = runner.invoke(app, ["topics", _sample_path()])
    assert result.exit_code == 0
    # domain, its topics and the level summary all show up
    assert "cybersecurity" in result.stdout
    assert "access-control" in result.stdout
    assert "cryptography" in result.stdout
    assert "network-security" in result.stdout
    assert "levels 1, 2, 3" in result.stdout


def test_topics_unknown_domain_errors():
    result = runner.invoke(app, ["topics", _sample_path(), "--domain", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_progress_no_state_hint(tmp_path):
    # A fresh state dir -> no progress -> friendly hint, exit 0.
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    result = runner.invoke(
        app, ["progress", _sample_path(), "--profile", "nobody", "--config", str(cfg)]
    )
    assert result.exit_code == 0
    assert "no progress" in result.stdout.lower()


def test_open_question_self_assessment_flow(tmp_path):
    """access-control level 1 (cyber-rbac-001) is an open question: Enter to reveal,
    then self-grade 1-4."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    r = runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "access-control", "--profile", "demo", "--config", str(cfg)],
        input="\n3\nq\n",  # reveal, grade "know it", quit
    )
    assert r.exit_code == 0
    assert "Answer:" in r.stdout
    assert "marked: know it" in r.stdout


def test_choice_question_is_graded_objectively(tmp_path):
    """cryptography level 1 (cyber-crypto-001) is true/false: pick an index, state
    confidence, and the engine tells you right/wrong and derives the grade."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    # Correct answer is index 0 (True). Answer 0, confident yes -> "know it".
    r = runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "cryptography", "--profile", "demo", "--config", str(cfg)],
        input="0\ny\nq\n",
    )
    assert r.exit_code == 0
    assert "Correct" in r.stdout
    assert "marked: know it" in r.stdout


def test_lucky_guess_marked_unsure_in_cli(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    # Correct (0) but not confident -> "unsure".
    r = runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "cryptography", "--profile", "demo", "--config", str(cfg)],
        input="0\nn\nq\n",
    )
    assert r.exit_code == 0
    assert "Correct" in r.stdout
    assert "marked: unsure" in r.stdout


def test_progress_reports_after_study(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    # access-control level 1 is open: Enter, grade "know it", quit.
    r1 = runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "access-control", "--profile", "demo", "--config", str(cfg)],
        input="\n3\nq\n",
    )
    assert r1.exit_code == 0
    r2 = runner.invoke(
        app,
        ["progress", _sample_path(), "--domain", "cybersecurity",
         "--profile", "demo", "--config", str(cfg)],
    )
    assert r2.exit_code == 0
    assert "questions seen" in r2.stdout
    assert "cybersecurity" in r2.stdout


def test_profiles_empty(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path / 'empty'}\n", encoding="utf-8")
    result = runner.invoke(app, ["profiles", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "no profiles yet" in result.stdout.lower()


def test_profiles_lists_after_study(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    # access-control level 1 is open: Enter, grade know it, quit.
    runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "access-control", "--profile", "alice", "--config", str(cfg)],
        input="\n3\nq\n",
    )
    result = runner.invoke(app, ["profiles", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "alice" in result.stdout
    assert "cybersecurity-basics" in result.stdout


def test_multiple_choice_hint_shown_without_duplication(tmp_path):
    """The multiple-choice question keeps its inline '(multiple answers)' hint and
    does NOT add a redundant second label line."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"state_dir: {tmp_path}\n", encoding="utf-8")
    # network-security: L1 single_choice (answer right+confident) -> L2 multiple_choice shown.
    result = runner.invoke(
        app,
        ["study", _sample_path(), "--domain", "cybersecurity",
         "--topic", "network-security", "--profile", "mc", "--config", str(cfg)],
        input="0\ny\nq\n",
    )
    assert result.exit_code == 0
    assert "(multiple answers)" in result.stdout
    # The old redundant label must be gone.
    assert "select all that apply" not in result.stdout.lower()


def test_print_question_fallback_tag_for_hintless_multiple_choice(capsys):
    """A multiple_choice question with no inline hint gets an orange tag."""
    from nailit.cli import _print_question
    from nailit.models import Question

    q = Question(
        id="x", domain="d", topic="t", level=1, type="multiple_choice",
        question="Pick the valid ones",  # no '(multiple answers)' in the text
        choices=["a", "b", "c"], correct=[0, 1],
    )
    _print_question(q)
    out = capsys.readouterr().out
    assert "Pick the valid ones" in out
    assert "(multiple answers)" in out


def test_print_question_plain_for_single_choice(capsys):
    from nailit.cli import _print_question
    from nailit.models import Question

    q = Question(
        id="x", domain="d", topic="t", level=1, type="single_choice",
        question="Pick one", choices=["a", "b"], correct=[0],
    )
    _print_question(q)
    out = capsys.readouterr().out
    assert "Pick one" in out
    assert "multiple answers" not in out.lower()
