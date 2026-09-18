import json

import pytest

from fake_backend import FakeBackend
from hire.llm import LLM
from hire import pipeline
from hire.cli import main
from hire.store import Opening


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRE_HOME", str(tmp_path))
    return tmp_path


def test_init_then_open(home, capsys):
    assert main(["init"]) == 0
    assert main([
        "open", "--id", "quant-qa",
        "--role", "QA engineer for trading strategies",
        "--brief", "Test backtests for look-ahead bias.",
        "--round1-candidates", "50", "--round1-advance", "5",
        "--variants-per-winner", "5", "--round2-advance", "5",
    ]) == 0
    spec = Opening("quant-qa", home).load()
    assert spec["funnel"]["round1_candidates"] == 50
    assert spec["funnel"]["round2_advance"] == 5
    settings = Opening("quant-qa", home).settings()
    assert settings.funnel.round2_candidates == 25
    assert "research" in spec["models"]
    assert "hire research quant-qa" in capsys.readouterr().out


def test_open_defaults_the_id_from_the_role(home):
    assert main(["open", "--role", "Product Manager!", "--brief", "b"]) == 0
    assert Opening("product-manager", home).exists()


def test_open_requires_a_brief(home, capsys):
    assert main(["open", "--role", "QA"]) == 2
    assert "brief is required" in capsys.readouterr().err


def test_open_refuses_to_clobber(home, capsys):
    main(["open", "--id", "x", "--role", "QA", "--brief", "b"])
    assert main(["open", "--id", "x", "--role", "QA", "--brief", "b"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert main(["open", "--id", "x", "--role", "QA", "--brief", "b2", "--force"]) == 0


def test_unknown_opening_lists_the_known_ones(home, capsys):
    main(["open", "--id", "quant-qa", "--role", "QA", "--brief", "b"])
    assert main(["status", "typo"]) == 2
    assert "quant-qa" in capsys.readouterr().err


def test_commands_refuse_to_run_with_no_usable_backend(home, capsys, monkeypatch):
    monkeypatch.setattr("hire.backends.detect", lambda: [])
    main(["open", "--id", "x", "--role", "QA", "--brief", "b"])
    assert main(["research", "x"]) == 2
    err = capsys.readouterr().err
    assert "no usable backend" in err
    assert "ANTHROPIC_API_KEY" in err and "claude" in err


def test_an_unavailable_backend_says_why(home, capsys, monkeypatch):
    monkeypatch.setattr("hire.backends.ApiBackend.available", staticmethod(lambda: False))
    main(["open", "--id", "x", "--role", "QA", "--brief", "b"])
    assert main(["research", "x", "--backend", "api"]) == 2
    assert "no Anthropic credentials" in capsys.readouterr().err


def test_dry_run_needs_no_backend(home, capsys, monkeypatch):
    monkeypatch.setattr("hire.backends.detect", lambda: [])
    main(["open", "--id", "x", "--role", "QA", "--brief", "b"])
    assert main(["generate", "x", "--round", "1", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().err


def test_backends_command_reports_availability(home, capsys, monkeypatch):
    monkeypatch.setattr("hire.backends.detect", lambda: ["claude-cli"])
    assert main(["backends"]) == 0
    out = capsys.readouterr().out
    assert "claude-cli" in out
    assert "would choose: claude-cli" in out


def test_backend_choice_can_come_from_the_environment(home, monkeypatch):
    from hire.backends import BackendError

    monkeypatch.setenv("HIRE_BACKEND", "codex-cli")
    monkeypatch.setattr("hire.backends.CodexCliBackend.available", classmethod(lambda cls: False))
    main(["open", "--id", "x", "--role", "QA", "--brief", "b"])
    # The env var must be what gets rejected, proving it was consulted.
    assert main(["research", "x"]) == 2


def test_status_shows_the_funnel_and_the_next_step(home, capsys):
    main(["open", "--id", "quant-qa", "--role", "QA", "--brief", "b"])
    assert main(["status", "quant-qa"]) == 0
    out = capsys.readouterr().out
    assert "Round 1: generate 50, advance 5" in out
    assert "5 variants per winner = 25" in out
    assert "hire research quant-qa" in out


def test_status_with_no_argument_lists_openings(home, capsys):
    main(["open", "--id", "a", "--role", "QA", "--brief", "b"])
    main(["open", "--id", "b", "--role", "PM", "--brief", "b"])
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "a" in out and "b" in out


def test_kb_add_list_show_search(home, capsys, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("Walk-forward validation beats a single train/test split.")
    assert main(["kb", "add", "--file", str(note), "--title", "Validation",
                 "--role", "quant-qa", "--tags", "validation,backtest"]) == 0
    capsys.readouterr()

    assert main(["kb", "list", "--role", "quant-qa"]) == 0
    assert "quant-qa" in capsys.readouterr().out

    assert main(["kb", "search", "walk-forward"]) == 0
    assert "Validation" in capsys.readouterr().out

    assert main(["kb", "show", "quant-qa-validation"]) == 0
    out = capsys.readouterr().out
    assert "Walk-forward" in out and "role: quant-qa" in out


def test_kb_list_is_not_an_error_when_empty(home, capsys):
    assert main(["kb", "list"]) == 0
    assert "empty" in capsys.readouterr().out


def test_kb_show_unknown_entry_fails_cleanly(home, capsys):
    assert main(["kb", "show", "nope"]) == 1
    assert "no knowledge-base entry" in capsys.readouterr().err


def test_shortlist_and_show_and_cost(home, capsys, tmp_path):
    main(["open", "--id", "quant-qa", "--role", "QA", "--brief", "b",
          "--round1-candidates", "4", "--round1-advance", "2"])
    opening = Opening("quant-qa", home)
    llm = LLM(backend=FakeBackend(), on_usage=opening.log_usage)
    pipeline.generate_round1(opening, llm, count=4, concurrency=2)
    prompt = tmp_path / "m.md"
    prompt.write_text("mock project")
    pipeline.run_round1(opening, llm, prompt, concurrency=2)
    capsys.readouterr()

    assert main(["shortlist", "quant-qa", "--round", "1",
                 "--advance", "c01, c02", "--notes", "Both were rigorous."]) == 0
    assert opening.advanced(1) == ["c01", "c02"]
    assert opening.load_decision(1)["general_notes"] == "Both were rigorous."

    assert main(["show", "quant-qa", "--candidate", "c01"]) == 0
    assert "## Positioning" in capsys.readouterr().out

    assert main(["show", "quant-qa", "--candidate", "c01", "--response"]) == 0
    assert "round1 output" in capsys.readouterr().out

    assert main(["cost", "quant-qa"]) == 0
    assert "total" in capsys.readouterr().out


def test_shortlist_rejects_unknown_ids(home, capsys):
    main(["open", "--id", "q", "--role", "QA", "--brief", "b"])
    opening = Opening("q", home)
    pipeline.generate_round1(opening, LLM(backend=FakeBackend()), count=2, concurrency=1)
    capsys.readouterr()
    assert main(["shortlist", "q", "--round", "1", "--advance", "c99"]) == 1
    assert "not round-1 candidates" in capsys.readouterr().err


def test_shortlist_notes_file_may_be_json_per_candidate(home, capsys, tmp_path):
    main(["open", "--id", "q", "--role", "QA", "--brief", "b"])
    opening = Opening("q", home)
    pipeline.generate_round1(opening, LLM(backend=FakeBackend()), count=2, concurrency=1)
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({"c01": "Liked the data hygiene focus."}))
    capsys.readouterr()
    assert main(["shortlist", "q", "--round", "1", "--advance", "c01",
                 "--notes-file", str(notes)]) == 0
    assert opening.load_decision(1)["notes"]["c01"] == "Liked the data hygiene focus."


def test_show_missing_file_fails_cleanly(home, capsys):
    main(["open", "--id", "q", "--role", "QA", "--brief", "b"])
    assert main(["show", "q", "--candidate", "c01"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_model_override_applies_to_every_stage(home):
    main(["open", "--id", "q", "--role", "QA", "--brief", "b", "--model", "claude-sonnet-5"])
    settings = Opening("q", home).settings()
    assert all(settings.stage(name).model == "claude-sonnet-5" for name in ("round1", "round2", "screen"))
