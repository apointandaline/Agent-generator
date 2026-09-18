"""Backend selection, JSON recovery, and the CLI argv/parsing surface."""

from __future__ import annotations

import json

import pytest

from hire.backends import (
    BackendError,
    CallSpec,
    ClaudeCliBackend,
    CodexCliBackend,
    _sources_from_markdown,
    detect,
    extract_json,
    make_backend,
)
from hire.config import StageConfig

CFG = StageConfig(model="claude-opus-5", effort="high", max_tokens=16000)


def spec(**kw):
    base = dict(stage="candidate", cfg=CFG, system="You are a candidate.", prompt="Do the thing.")
    base.update(kw)
    return CallSpec(**base)


# -- JSON recovery ---------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Sure, here you go:\n\n{"a": 1}\n\nHope that helps!', {"a": 1}),
        ('[{"a": 1}]', [{"a": 1}]),
        ('Here:\n```json\n{"nested": {"b": [1, 2]}}\n```\nDone.', {"nested": {"b": [1, 2]}}),
    ],
)
def test_extract_json_recovers_from_prose_and_fences(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "no json at all", "{unbalanced"])
def test_extract_json_raises_on_garbage(text):
    with pytest.raises(BackendError):
        extract_json(text)


def test_sources_are_recovered_from_markdown_links():
    text = "See [CME specs](https://cme.com/a) and [again](https://cme.com/a) and [b](https://x.io/b)."
    assert _sources_from_markdown(text) == [
        {"title": "CME specs", "url": "https://cme.com/a"},
        {"title": "b", "url": "https://x.io/b"},
    ]


# -- selection -------------------------------------------------------------- #


def test_unknown_backend_is_rejected():
    with pytest.raises(BackendError, match="unknown backend"):
        make_backend("telepathy")


def test_auto_raises_helpfully_when_nothing_is_available(monkeypatch):
    monkeypatch.setattr("hire.backends.detect", lambda: [])
    with pytest.raises(BackendError, match="no usable backend"):
        make_backend("auto")


def test_auto_prefers_the_api_when_it_is_available(monkeypatch):
    monkeypatch.setattr("hire.backends.ApiBackend.available", staticmethod(lambda: True))
    monkeypatch.setattr("hire.backends.ClaudeCliBackend.available", classmethod(lambda cls: True))
    assert detect()[0] == "api"


def test_cli_backends_are_detected_by_binary_on_path(monkeypatch):
    monkeypatch.setattr("hire.backends.shutil.which", lambda name: "/usr/bin/" + name)
    assert ClaudeCliBackend.available() and CodexCliBackend.available()
    monkeypatch.setattr("hire.backends.shutil.which", lambda name: None)
    assert not ClaudeCliBackend.available() and not CodexCliBackend.available()


# -- claude CLI argv + parsing ---------------------------------------------- #


class Recorder:
    """Stands in for subprocess.run, capturing argv and returning canned stdout."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.argv = None
        self.cwd = None

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.cwd = kwargs.get("cwd")

        class Proc:
            pass

        proc = Proc()
        proc.stdout, proc.stderr, proc.returncode = self.stdout, self.stderr, self.returncode
        return proc


def claude_payload(result="hello", **extra):
    payload = {
        "result": result,
        "is_error": False,
        "num_turns": 3,
        "session_id": "abc",
        "total_cost_usd": 0.42,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 19000,
            "cache_read_input_tokens": 5,
        },
        "modelUsage": {"claude-opus-5": {}},
    }
    payload.update(extra)
    return json.dumps(payload)


def test_claude_text_call_strips_tools_and_reports_usage(monkeypatch):
    rec = Recorder(claude_payload("the answer"))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    result = ClaudeCliBackend().complete(spec())

    assert result.text == "the answer"
    assert result.cost == 0.42, "the CLI's own cost figure is authoritative"
    assert result.usage["cache_creation_input_tokens"] == 19000
    assert result.model == "claude-opus-5"
    assert result.meta["num_turns"] == 3

    argv = rec.argv
    assert argv[:2] == ["claude", "-p"]
    assert "--restricted" in argv
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--system-prompt") + 1] == "You are a candidate."
    assert argv[argv.index("--model") + 1] == "claude-opus-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[-1] == "Do the thing.", "the prompt is the trailing positional"
    assert "Bash" in argv[argv.index("--disallowed-tools") + 1]


def test_claude_build_runs_in_the_workspace_and_gates_bash(monkeypatch, tmp_path):
    from hire.workspace import Workspace

    ws = Workspace(tmp_path / "c01")
    rec = Recorder(claude_payload("built it"))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)

    result, transcript = ClaudeCliBackend().build(spec(stage="round2"), ws, False, 40)
    assert result.text == "built it"
    assert rec.cwd == str(ws.root)
    allowed = rec.argv[rec.argv.index("--allowed-tools") + 1]
    assert "Write" in allowed and "Bash" not in allowed
    assert "Bash" in rec.argv[rec.argv.index("--disallowed-tools") + 1]
    assert any(step["type"] == "note" for step in transcript)

    rec2 = Recorder(claude_payload("built it"))
    monkeypatch.setattr("hire.backends.subprocess.run", rec2)
    ClaudeCliBackend().build(spec(stage="round2"), ws, True, 40)
    assert "Bash" in rec2.argv[rec2.argv.index("--allowed-tools") + 1]


def test_claude_json_call_appends_schema_instructions(monkeypatch):
    rec = Recorder(claude_payload('```json\n{"scores": {"a": 7}}\n```'))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    schema = {"type": "object", "properties": {"scores": {"type": "object"}}}
    result = ClaudeCliBackend().complete_json(spec(stage="digest"), schema)
    assert result.data == {"scores": {"a": 7}}
    assert "JSON Schema" in rec.argv[-1]


def test_claude_research_allows_web_tools(monkeypatch):
    rec = Recorder(claude_payload("See [CME](https://cme.com/x)."))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    result, sources = ClaudeCliBackend().research(spec(stage="research"), 12)
    assert sources == [{"title": "CME", "url": "https://cme.com/x"}]
    assert "WebSearch" in rec.argv[rec.argv.index("--allowed-tools") + 1]


def test_claude_reported_errors_surface(monkeypatch):
    rec = Recorder(claude_payload("rate limited", is_error=True))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    with pytest.raises(BackendError, match="rate limited"):
        ClaudeCliBackend().complete(spec())


def test_nonzero_exit_surfaces_stderr(monkeypatch):
    rec = Recorder("", returncode=1, stderr="not logged in")
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    with pytest.raises(BackendError, match="not logged in"):
        ClaudeCliBackend().complete(spec())


def test_unparseable_stdout_surfaces(monkeypatch):
    rec = Recorder("<html>login</html>")
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    with pytest.raises(BackendError, match="unparseable"):
        ClaudeCliBackend().complete(spec())


def test_non_anthropic_model_is_not_forced_on_the_claude_cli(monkeypatch):
    rec = Recorder(claude_payload())
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    ClaudeCliBackend().complete(spec(cfg=StageConfig(model="gpt-5-codex")))
    assert "--model" not in rec.argv


# -- codex CLI argv --------------------------------------------------------- #


def test_codex_prepends_the_persona_and_picks_a_sandbox(monkeypatch, tmp_path):
    from hire.workspace import Workspace

    rec = Recorder("the answer")
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    backend = CodexCliBackend(model="gpt-5-codex")

    backend.complete(spec())
    assert rec.argv[:2] == ["codex", "exec"]
    assert rec.argv[rec.argv.index("--sandbox") + 1] == "read-only"
    assert rec.argv[rec.argv.index("--model") + 1] == "gpt-5-codex"
    assert "--skip-git-repo-check" in rec.argv
    prompt = rec.argv[-1]
    assert "You are a candidate." in prompt and "Do the thing." in prompt

    ws = Workspace(tmp_path / "c01")
    backend.build(spec(stage="round2"), ws, False, 40)
    assert rec.argv[rec.argv.index("--sandbox") + 1] == "workspace-write"
    assert rec.cwd == str(ws.root)

    backend.build(spec(stage="round2"), ws, True, 40)
    assert rec.argv[rec.argv.index("--sandbox") + 1] == "danger-full-access"


def test_codex_falls_back_to_stdout_when_the_message_file_is_empty(monkeypatch):
    rec = Recorder("final answer on stdout")
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    assert CodexCliBackend().complete(spec()).text == "final answer on stdout"


def test_codex_records_no_invented_usage(monkeypatch):
    rec = Recorder("answer")
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    result = CodexCliBackend().complete(spec())
    assert result.usage == {} and result.cost == 0.0


def test_the_dominant_model_is_reported_not_a_side_model(monkeypatch):
    """The CLI lists every model a session touched; pick the one that worked."""
    rec = Recorder(
        claude_payload(
            modelUsage={
                "claude-haiku-4-5": {"inputTokens": 2, "outputTokens": 8},
                "claude-opus-5": {"inputTokens": 5000, "outputTokens": 3000},
            }
        )
    )
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    result = ClaudeCliBackend().complete(spec())
    assert result.model == "claude-opus-5"
    assert result.meta["model_usage"] == ["claude-haiku-4-5", "claude-opus-5"]


def test_model_falls_back_to_the_requested_one(monkeypatch):
    rec = Recorder(claude_payload(modelUsage={}))
    monkeypatch.setattr("hire.backends.subprocess.run", rec)
    assert ClaudeCliBackend().complete(spec()).model == "claude-opus-5"


# -- retry and error reporting ---------------------------------------------- #


def _rate_limited(monkeypatch, then=None):
    """A recorder that 429s on the first call, then succeeds."""
    calls = {"n": 0}
    payload_429 = claude_payload(
        "You've hit your session limit · resets 6:40am (UTC)",
        is_error=True,
        api_error_status=429,
    )

    def run(argv, **kwargs):
        calls["n"] += 1

        class Proc:
            pass

        proc = Proc()
        if calls["n"] == 1:
            proc.stdout, proc.returncode = payload_429, 1
        else:
            proc.stdout, proc.returncode = then or claude_payload("recovered"), 0
        proc.stderr = ""
        return proc

    monkeypatch.setattr("hire.backends.subprocess.run", run)
    monkeypatch.setattr("hire.backends.time.sleep", lambda s: None)
    return calls


def test_a_rate_limit_is_retried_not_failed(monkeypatch):
    calls = _rate_limited(monkeypatch)
    result = ClaudeCliBackend().complete(spec())
    assert result.text == "recovered"
    assert calls["n"] == 2, "the 429 should have been retried once"


def test_a_persistent_rate_limit_eventually_raises_the_real_reason(monkeypatch):
    from hire.backends import RetryableBackendError

    payload = claude_payload("You've hit your session limit", is_error=True, api_error_status=429)
    monkeypatch.setattr("hire.backends.subprocess.run", Recorder(payload, returncode=1))
    monkeypatch.setattr("hire.backends.time.sleep", lambda s: None)
    with pytest.raises(RetryableBackendError, match="session limit"):
        ClaudeCliBackend().complete(spec())


def test_a_non_retryable_error_is_not_retried(monkeypatch):
    calls = {"n": 0}
    payload = claude_payload("invalid model name", is_error=True, api_error_status=400)

    def run(argv, **kwargs):
        calls["n"] += 1

        class Proc:
            pass

        proc = Proc()
        proc.stdout, proc.stderr, proc.returncode = payload, "", 1
        return proc

    monkeypatch.setattr("hire.backends.subprocess.run", run)
    with pytest.raises(BackendError, match="invalid model name"):
        ClaudeCliBackend().complete(spec())
    assert calls["n"] == 1


def test_the_error_message_is_the_reason_not_a_json_dump(monkeypatch):
    """A failing call must not bury the reason under the payload tail."""
    payload = claude_payload("Overloaded", is_error=True, api_error_status=400)
    monkeypatch.setattr("hire.backends.subprocess.run", Recorder(payload, returncode=1))
    with pytest.raises(BackendError) as excinfo:
        ClaudeCliBackend().complete(spec())
    message = str(excinfo.value)
    assert "Overloaded" in message
    assert "subagent_stats" not in message and "ephemeral" not in message
    assert len(message) < 200


def test_a_timeout_is_treated_as_retryable(monkeypatch):
    import subprocess as sp

    from hire.backends import RetryableBackendError

    def boom(argv, **kwargs):
        raise sp.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr("hire.backends.subprocess.run", boom)
    monkeypatch.setattr("hire.backends.time.sleep", lambda s: None)
    with pytest.raises(RetryableBackendError, match="timed out"):
        ClaudeCliBackend().complete(spec())
