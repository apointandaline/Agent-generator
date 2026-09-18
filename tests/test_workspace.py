import pytest

from hire.workspace import Workspace, tools_for


def test_write_read_list(tmp_path):
    ws = Workspace(tmp_path / "c01")
    assert ws.execute("write_file", {"path": "src/a.py", "content": "x = 1\n"}) == (
        "wrote src/a.py (6 chars)",
        False,
    )
    assert ws.execute("read_file", {"path": "src/a.py"})[0] == "x = 1\n"
    assert "src/a.py" in ws.execute("list_files", {})[0]


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "a/../../out.txt", "../"])
def test_paths_outside_the_workspace_are_refused(tmp_path, path):
    ws = Workspace(tmp_path / "c01")
    out, is_error = ws.execute("write_file", {"path": path, "content": "x"})
    assert is_error and "outside the workspace" in out
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "out.txt").exists()


def test_exec_is_off_by_default(tmp_path):
    ws = Workspace(tmp_path / "c01")
    out, is_error = ws.execute("run_command", {"command": "echo hi"})
    assert is_error and "disabled" in out
    assert tools_for(False) == tools_for(True)[:-1]


def test_exec_runs_in_the_workspace_when_enabled(tmp_path):
    ws = Workspace(tmp_path / "c01", allow_exec=True, exec_timeout=10)
    out, is_error = ws.execute("run_command", {"command": "pwd && echo ok"})
    assert not is_error
    assert "ok" in out and str(ws.root) in out
    assert ws.commands_run == ["pwd && echo ok"]


def test_exec_does_not_leak_api_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    ws = Workspace(tmp_path / "c01", allow_exec=True, exec_timeout=10)
    out, _ = ws.execute("run_command", {"command": "echo [$ANTHROPIC_API_KEY]"})
    assert "sk-ant-secret" not in out
    assert "[]" in out


def test_exec_times_out(tmp_path):
    ws = Workspace(tmp_path / "c01", allow_exec=True, exec_timeout=1)
    out, is_error = ws.execute("run_command", {"command": "sleep 5"})
    assert not is_error and "timed out" in out


def test_unknown_tool_and_missing_args_are_errors_not_crashes(tmp_path):
    ws = Workspace(tmp_path / "c01")
    assert ws.execute("teleport", {})[1] is True
    assert ws.execute("read_file", {})[1] is True
    assert ws.execute("read_file", {"path": "nope.txt"})[1] is True


def test_digest_leads_with_the_submission(tmp_path):
    ws = Workspace(tmp_path / "c01")
    ws.write("zzz.py", "print(1)")
    ws.write("SUBMISSION.md", "# What I built")
    digest = ws.digest()
    assert digest.index("SUBMISSION.md") < digest.index("zzz.py")
    assert ws.submission() == "# What I built"


def test_digest_is_bounded(tmp_path):
    ws = Workspace(tmp_path / "c01")
    for i in range(20):
        ws.write(f"f{i}.txt", "y" * 20_000)
    assert len(ws.digest(max_chars=10_000, per_file=1_000)) < 40_000


def test_reads_are_truncated_not_unbounded(tmp_path):
    ws = Workspace(tmp_path / "c01")
    ws.write("big.txt", "z" * 100_000)
    out, _ = ws.execute("read_file", {"path": "big.txt"})
    assert "truncated" in out and len(out) < 70_000
