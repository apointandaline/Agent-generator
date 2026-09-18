"""The file/command tools a round-2 candidate uses to actually do the work.

Every path is resolved and checked against the workspace root, so a candidate
cannot read or write outside its own directory. ``run_command`` is a real shell
and is therefore **off by default** — see the note on :class:`Workspace.run`.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_READ_CHARS = 60_000
MAX_OUTPUT_CHARS = 20_000
MAX_LISTED_FILES = 400


class PathEscape(ValueError):
    """Raised when a candidate names a path outside its workspace."""


TOOLS: list[dict] = [
    {
        "name": "write_file",
        "description": (
            "Create or overwrite a file in your workspace. Parent directories are "
            "created automatically. Use this for every artifact you produce."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from your workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_files",
        "description": "List files under a directory in your workspace (recursive).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Defaults to the workspace root."}},
            "required": [],
            "additionalProperties": False,
        },
    },
]

RUN_COMMAND_TOOL: dict = {
    "name": "run_command",
    "description": (
        "Run a shell command from your workspace root to build or test your work. "
        "Output is truncated and there is a per-command timeout."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
        "additionalProperties": False,
    },
}


def tools_for(allow_exec: bool) -> list[dict]:
    return TOOLS + ([RUN_COMMAND_TOOL] if allow_exec else [])


@dataclass
class Workspace:
    """A candidate's sandboxed working directory."""

    root: Path
    allow_exec: bool = False
    exec_timeout: int = 120
    commands_run: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path safety ------------------------------------------------------- #
    def resolve(self, relative: str) -> Path:
        """Resolve a candidate-supplied path, refusing anything outside the root."""
        candidate = (self.root / (relative or ".")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathEscape(f"path '{relative}' is outside the workspace")
        return candidate

    # -- tools ------------------------------------------------------------- #
    def write(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {self.rel(target)} ({len(content)} chars)"

    def read(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n…[truncated at {MAX_READ_CHARS} chars]"
        return text

    def list(self, path: str = ".") -> str:
        target = self.resolve(path or ".")
        if not target.exists():
            return "(empty)"
        if target.is_file():
            return self.rel(target)
        found = sorted(p for p in target.rglob("*") if p.is_file())
        if not found:
            return "(empty)"
        lines = [f"{self.rel(p)}\t{p.stat().st_size}B" for p in found[:MAX_LISTED_FILES]]
        if len(found) > MAX_LISTED_FILES:
            lines.append(f"…and {len(found) - MAX_LISTED_FILES} more")
        return "\n".join(lines)

    def run(self, command: str) -> str:
        """Run a shell command in the workspace.

        This executes model-written code on the host. It is process-isolated and
        cwd-confined but it is **not** a sandbox: a command can reach the network
        and the wider filesystem. Enable it only for projects you are willing to
        run, ideally inside a container or VM.
        """
        if not self.allow_exec:
            raise PermissionError("command execution is disabled for this interview")
        self.commands_run.append(command)
        env = dict(os.environ)
        # Don't hand the interview's API credentials to candidate-written code.
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(key, None)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=self.exec_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"command timed out after {self.exec_timeout}s"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + f"\n…[truncated at {MAX_OUTPUT_CHARS} chars]"
        return f"exit={proc.returncode}\n{out.strip() or '(no output)'}"

    # -- dispatch ---------------------------------------------------------- #
    def execute(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool call. Returns (output, is_error) — errors go back to the
        candidate as a tool_result rather than aborting the interview."""
        try:
            if name == "write_file":
                return self.write(args["path"], args.get("content", "")), False
            if name == "read_file":
                return self.read(args["path"]), False
            if name == "list_files":
                return self.list(args.get("path", ".")), False
            if name == "run_command":
                return self.run(args["command"]), False
            return f"unknown tool '{name}'", True
        except KeyError as exc:
            return f"missing required argument: {exc}", True
        except (PathEscape, PermissionError, FileNotFoundError, OSError) as exc:
            return f"{type(exc).__name__}: {exc}", True

    # -- inspection -------------------------------------------------------- #
    def rel(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))

    def files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*") if p.is_file())

    def submission(self) -> str:
        path = self.root / "SUBMISSION.md"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def digest(self, max_chars: int = 40_000, per_file: int = 8_000) -> str:
        """A readable dump of the workspace for the assessor and the CEO."""
        files = self.files()
        if not files:
            return "(the candidate produced no files)"
        tree = "\n".join(f"{self.rel(p)}\t{p.stat().st_size}B" for p in files[:MAX_LISTED_FILES])
        chunks = [f"### File tree\n```\n{tree}\n```"]
        used = len(tree)
        # SUBMISSION.md first — it is the candidate's own account of the work.
        ordered = sorted(files, key=lambda p: (p.name != "SUBMISSION.md", str(p)))
        for path in ordered:
            if used >= max_chars:
                chunks.append(f"…[remaining files omitted at {max_chars} chars]")
                break
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                chunks.append(f"### {self.rel(path)}\n(binary or unreadable, {path.stat().st_size}B)")
                continue
            if len(text) > per_file:
                text = text[:per_file] + f"\n…[truncated at {per_file} chars]"
            chunk = f"### {self.rel(path)}\n```\n{text}\n```"
            chunks.append(chunk)
            used += len(chunk)
        return "\n\n".join(chunks)
