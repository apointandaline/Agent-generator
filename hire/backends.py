"""Model backends.

The funnel can be driven three ways:

* ``api``        — the Anthropic SDK, billed to an ANTHROPIC_API_KEY.
* ``claude-cli`` — a ``claude -p`` subprocess, using whatever credentials the
  Claude Code CLI already has. No API key of your own required.
* ``codex-cli``  — a ``codex exec`` subprocess, same idea for OpenAI's CLI.

The CLI backends cost more per call than the raw API (each subprocess re-sends
the agent's own system prompt, roughly 20k tokens with ``--restricted``), but
they run anywhere the CLI is already signed in. For round 2 they are arguably
better: the CLI has native file tools and does the build in the workspace
directory itself, so no tool loop has to be simulated.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from hire.config import StageConfig, price

DEFAULT_TIMEOUT = 900
BUILD_TIMEOUT = 2400

#: Tools the Claude CLI must not touch during a text-only call.
_TEXT_DISALLOWED = (
    "Bash,WebFetch,WebSearch,Read,Write,Edit,Glob,Grep,Task,TodoWrite,NotebookEdit"
)


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class BackendError(RuntimeError):
    pass


class RetryableBackendError(BackendError):
    """A failure worth another attempt: rate limits, session limits, 5xx."""


#: HTTP statuses the CLI backends retry with backoff. The SDK does this itself.
RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 5


@dataclass
class CallSpec:
    """One unit of work handed to a backend."""

    stage: str
    cfg: StageConfig
    system: str
    prompt: str
    label: str = ""
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class BackendResult:
    text: str
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    cost_usd: float | None = None
    stop_reason: str | None = None
    data: Any = None
    meta: dict = field(default_factory=dict)

    @property
    def cost(self) -> float:
        """Reported cost when the backend gives one, else priced from usage."""
        if self.cost_usd is not None:
            return self.cost_usd
        return price(self.model, self.usage)


class Backend(ABC):
    """Something that can turn a prompt into text, JSON, research or a build."""

    name: str = "backend"
    #: Recommended parallelism. Subprocess backends spawn a whole CLI per call.
    default_concurrency: int = 6

    @abstractmethod
    def complete(self, spec: CallSpec) -> BackendResult: ...

    @abstractmethod
    def complete_json(self, spec: CallSpec, schema: dict) -> BackendResult: ...

    @abstractmethod
    def research(self, spec: CallSpec, max_uses: int) -> tuple[BackendResult, list[dict]]: ...

    @abstractmethod
    def build(
        self, spec: CallSpec, workspace, allow_exec: bool, max_turns: int
    ) -> tuple[BackendResult, list[dict]]: ...

    @staticmethod
    def available() -> bool:  # pragma: no cover - trivial
        return False

    def describe(self) -> str:
        return self.name


# --------------------------------------------------------------------------- #
# JSON coaxing, for backends with no schema enforcement
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Pull a JSON value out of prose, a code fence, or a bare object.

    CLI backends cannot be constrained to a schema the way the API can, so their
    output has to be recovered. Raises :class:`BackendError` if nothing parses.
    """
    text = (text or "").strip()
    if not text:
        raise BackendError("empty response where JSON was expected")

    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)
    # Fall back to the outermost brace/bracket span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise BackendError(f"could not parse JSON from response: {text[:300]}…")


def json_instructions(schema: dict) -> str:
    return (
        "\n\n---\n\n"
        "Reply with a single JSON object and nothing else — no prose before or "
        "after it, no markdown code fence. It must validate against this JSON "
        "Schema:\n\n"
        f"{json.dumps(schema, indent=2)}"
    )


# --------------------------------------------------------------------------- #
# Anthropic API
# --------------------------------------------------------------------------- #


class ApiBackend(Backend):
    """The Anthropic SDK. Needs ANTHROPIC_API_KEY or an `ant` profile."""

    name = "api"
    default_concurrency = 6

    def __init__(self, client=None):
        from hire.llm import make_client  # imported lazily: SDK is optional

        self.client = client or make_client()

    @staticmethod
    def available() -> bool:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        return os.path.isdir(os.path.expanduser("~/.config/anthropic"))

    def complete(self, spec: CallSpec) -> BackendResult:
        from hire.llm import _text_of, _usage_dict

        with self.client.messages.stream(
            model=spec.cfg.model,
            max_tokens=spec.cfg.max_tokens,
            system=spec.system,
            thinking={"type": "adaptive"},
            output_config={"effort": spec.cfg.effort},
            messages=[{"role": "user", "content": spec.prompt}],
        ) as stream:
            message = stream.get_final_message()
        return BackendResult(
            text=_text_of(message.content),
            usage=_usage_dict(message.usage),
            model=spec.cfg.model,
            stop_reason=message.stop_reason,
        )

    def complete_json(self, spec: CallSpec, schema: dict) -> BackendResult:
        from hire.llm import _text_of, _usage_dict

        message = self.client.messages.create(
            model=spec.cfg.model,
            max_tokens=spec.cfg.max_tokens,
            system=spec.system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": spec.cfg.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": spec.prompt}],
        )
        text = _text_of(message.content)
        return BackendResult(
            text=text,
            usage=_usage_dict(message.usage),
            model=spec.cfg.model,
            stop_reason=message.stop_reason,
            data=extract_json(text),
        )

    def research(self, spec: CallSpec, max_uses: int) -> tuple[BackendResult, list[dict]]:
        from hire.llm import _add_usage, _collect_sources, _dedupe_sources, _text_of, _usage_dict

        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
        messages: list[dict] = [{"role": "user", "content": spec.prompt}]
        total: dict[str, int] = {}
        sources: list[dict] = []
        final = None

        for _ in range(7):
            with self.client.messages.stream(
                model=spec.cfg.model,
                max_tokens=spec.cfg.max_tokens,
                system=spec.system,
                thinking={"type": "adaptive"},
                output_config={"effort": spec.cfg.effort},
                tools=tools,
                messages=messages,
            ) as stream:
                message = stream.get_final_message()
            _add_usage(total, _usage_dict(message.usage))
            sources.extend(_collect_sources(message.content))
            final = message
            if message.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": message.content})

        return (
            BackendResult(
                text=_text_of(final.content),
                usage=total,
                model=spec.cfg.model,
                stop_reason=final.stop_reason,
            ),
            _dedupe_sources(sources),
        )

    def build(self, spec, workspace, allow_exec, max_turns):
        """Drive a manual tool loop against the workspace's own file tools."""
        from hire.llm import run_tool_loop
        from hire.workspace import tools_for

        return run_tool_loop(
            self.client,
            spec,
            tools=tools_for(allow_exec),
            execute=workspace.execute,
            max_turns=max_turns,
        )


# --------------------------------------------------------------------------- #
# Subprocess CLIs
# --------------------------------------------------------------------------- #


class SubprocessBackend(Backend):
    """Shared plumbing for CLI-driven backends."""

    binary = ""
    default_concurrency = 4

    @classmethod
    def available(cls) -> bool:
        return shutil.which(cls.binary) is not None

    def _run(
        self,
        argv: Sequence[str],
        timeout: int,
        cwd: Path | None = None,
        allow_failure: bool = False,
    ) -> tuple[str, str, int]:
        """Run the CLI once. Returns (stdout, stderr, returncode).

        With ``allow_failure`` the caller inspects a non-zero exit itself — the
        CLIs report API errors as structured output *and* a non-zero status, so
        the payload is worth reading before deciding what went wrong.
        """
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        except FileNotFoundError as exc:
            raise BackendError(f"{self.binary} is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RetryableBackendError(f"{self.binary} timed out after {timeout}s") from exc
        if proc.returncode != 0 and not allow_failure:
            detail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise BackendError(f"{self.binary} exited {proc.returncode}: {detail}")
        return proc.stdout, proc.stderr, proc.returncode

    def _with_retries(self, call, label: str = ""):
        """Retry retryable failures with exponential backoff and jitter."""
        last: BaseException | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return call()
            except RetryableBackendError as exc:
                last = exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                delay = min(2 ** attempt * 5, 120) + random.uniform(0, 3)
                _log(f"  … {label or self.name}: {exc}; retrying in {delay:.0f}s "
                     f"({attempt + 2}/{MAX_ATTEMPTS})")
                time.sleep(delay)
        raise last


class ClaudeCliBackend(SubprocessBackend):
    """Drives `claude -p`, reusing the Claude Code CLI's own credentials."""

    name = "claude-cli"
    binary = "claude"

    def __init__(self, binary: str | None = None, extra_args: Sequence[str] = ()):
        self.binary = binary or os.environ.get("HIRE_CLAUDE_BIN", "claude")
        self.extra_args = list(extra_args)

    def _model(self, cfg: StageConfig) -> list[str]:
        # The CLI takes full model ids and aliases alike, so pass ours straight
        # through; a non-Anthropic id means the opening was configured for another
        # backend, in which case let the CLI pick its own default.
        if cfg.model and cfg.model.startswith("claude"):
            return ["--model", cfg.model]
        return []

    def _base(self, cfg: StageConfig, system: str) -> list[str]:
        argv = [self.binary, "-p", "--output-format", "json", "--system-prompt", system]
        argv += self._model(cfg)
        if cfg.effort:
            argv += ["--effort", cfg.effort]
        return argv + self.extra_args

    def _invoke(
        self,
        argv: Sequence[str],
        prompt: str,
        timeout: int,
        cwd: Path | None = None,
        cfg: StageConfig | None = None,
        label: str = "",
    ):
        return self._with_retries(
            lambda: self._invoke_once(argv, prompt, timeout, cwd, cfg), label=label
        )

    def _invoke_once(
        self,
        argv: Sequence[str],
        prompt: str,
        timeout: int,
        cwd: Path | None = None,
        cfg: StageConfig | None = None,
    ):
        stdout, stderr, code = self._run(
            list(argv) + [prompt], timeout=timeout, cwd=cwd, allow_failure=True
        )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = (stderr or stdout or "").strip()[-400:]
            if code != 0:
                raise BackendError(f"claude exited {code}: {detail}") from exc
            raise BackendError(f"claude returned unparseable output: {detail}…") from exc
        if payload.get("is_error") or code != 0:
            # The CLI puts the human-readable reason in `result`; the raw JSON
            # tail is noise. Surface the reason, and retry if it is transient.
            reason = (payload.get("result") or "").strip()[:300] or f"exit {code}"
            status = payload.get("api_error_status")
            if status in RETRYABLE_STATUSES:
                raise RetryableBackendError(f"claude: {reason} (HTTP {status})")
            raise BackendError(f"claude reported an error: {reason}")
        usage = payload.get("usage") or {}
        model_usage = payload.get("modelUsage") or {}
        return BackendResult(
            text=(payload.get("result") or "").strip(),
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            },
            model=_dominant_model(model_usage, fallback=cfg.model if cfg else ""),
            cost_usd=payload.get("total_cost_usd"),
            stop_reason=payload.get("stop_reason"),
            meta={
                "num_turns": payload.get("num_turns"),
                "session_id": payload.get("session_id"),
                "permission_denials": payload.get("permission_denials") or [],
                "model_usage": sorted(model_usage),
            },
        )

    def complete(self, spec: CallSpec) -> BackendResult:
        # Text-only: strip the CLI's tools so the persona cannot wander off and
        # so each call carries less scaffolding.
        argv = self._base(spec.cfg, spec.system) + [
            "--restricted",
            "--disallowed-tools",
            _TEXT_DISALLOWED,
            "--disable-slash-commands",
        ]
        return self._invoke(argv, spec.prompt, spec.timeout, cfg=spec.cfg, label=spec.label)

    def complete_json(self, spec: CallSpec, schema: dict) -> BackendResult:
        result = self.complete(
            CallSpec(
                stage=spec.stage,
                cfg=spec.cfg,
                system=spec.system,
                prompt=spec.prompt + json_instructions(schema),
                label=spec.label,
                timeout=spec.timeout,
            )
        )
        result.data = extract_json(result.text)
        return result

    def research(self, spec: CallSpec, max_uses: int) -> tuple[BackendResult, list[dict]]:
        argv = self._base(spec.cfg, spec.system) + [
            "--allowed-tools",
            "WebSearch,WebFetch",
            "--disable-slash-commands",
        ]
        result = self._invoke(argv, spec.prompt, spec.timeout, cfg=spec.cfg, label=spec.label)
        return result, _sources_from_markdown(result.text)

    def build(self, spec: CallSpec, workspace, allow_exec: bool, max_turns: int):
        """Let the CLI build natively inside the workspace directory."""
        tools = "Read,Write,Edit,Glob,Grep" + (",Bash" if allow_exec else "")
        argv = self._base(spec.cfg, spec.system) + [
            "--allowed-tools",
            tools,
            "--permission-mode",
            "acceptEdits" if allow_exec else "acceptEdits",
            "--disable-slash-commands",
            "--add-dir",
            str(workspace.root),
        ]
        if not allow_exec:
            argv += ["--disallowed-tools", "Bash,WebFetch,WebSearch"]
        result = self._invoke(
            argv, spec.prompt, spec.timeout, cwd=workspace.root, cfg=spec.cfg, label=spec.label
        )
        transcript = [
            {
                "turn": 0,
                "type": "note",
                "text": f"built by {self.name} in {workspace.root} "
                f"({result.meta.get('num_turns')} turns, exec={'on' if allow_exec else 'off'})",
            },
            {"turn": 0, "type": "say", "text": result.text},
        ]
        return result, transcript


class CodexCliBackend(SubprocessBackend):
    """Drives `codex exec`.

    Codex has no ``--system-prompt``, so the persona is prepended to the prompt
    under a heading. Its sandbox is coarser than the API backend's: writing files
    requires ``workspace-write``, which also permits commands inside the
    workspace — so a Codex round 2 can never be strictly no-execution.
    """

    name = "codex-cli"
    binary = "codex"

    def __init__(self, binary: str | None = None, model: str | None = None):
        self.binary = binary or os.environ.get("HIRE_CODEX_BIN", "codex")
        self.model = model or os.environ.get("HIRE_CODEX_MODEL") or None

    def _model_args(self, cfg: StageConfig) -> list[str]:
        model = self.model
        if not model and cfg.model and not cfg.model.startswith("claude"):
            model = cfg.model
        return ["--model", model] if model else []

    @staticmethod
    def _compose(system: str, prompt: str) -> str:
        return (
            "# Your operating instructions\n\n"
            f"{system}\n\n"
            "Follow the instructions above for everything below.\n\n"
            "---\n\n"
            f"{prompt}"
        )

    def _invoke(
        self, prompt: str, cfg: StageConfig, timeout: int, sandbox: str, cwd: Path | None = None
    ) -> BackendResult:
        out_file = None
        argv = [self.binary, "exec", "--sandbox", sandbox, "--skip-git-repo-check"]
        argv += self._model_args(cfg)
        if cwd:
            argv += ["--cd", str(cwd)]
        try:
            import tempfile

            handle = tempfile.NamedTemporaryFile(
                "w+", suffix=".txt", delete=False, encoding="utf-8"
            )
            handle.close()
            out_file = Path(handle.name)
            argv += ["--output-last-message", str(out_file)]
            stdout, _stderr, _code = self._with_retries(
                lambda: self._run(argv + [prompt], timeout=timeout, cwd=cwd)
            )
            text = out_file.read_text(encoding="utf-8").strip() or stdout.strip()
        finally:
            if out_file and out_file.exists():
                out_file.unlink()
        if not text:
            raise BackendError("codex produced no final message")
        # Codex does not report token usage on stdout; the ledger records the
        # call with zero usage rather than inventing numbers.
        return BackendResult(text=text, usage={}, model=self.model or cfg.model, cost_usd=0.0)

    def complete(self, spec: CallSpec) -> BackendResult:
        return self._invoke(
            self._compose(spec.system, spec.prompt), spec.cfg, spec.timeout, "read-only"
        )

    def complete_json(self, spec: CallSpec, schema: dict) -> BackendResult:
        result = self._invoke(
            self._compose(spec.system, spec.prompt + json_instructions(schema)),
            spec.cfg,
            spec.timeout,
            "read-only",
        )
        result.data = extract_json(result.text)
        return result

    def research(self, spec: CallSpec, max_uses: int) -> tuple[BackendResult, list[dict]]:
        result = self._invoke(
            self._compose(spec.system, spec.prompt),
            spec.cfg,
            spec.timeout,
            "danger-full-access",  # research needs network egress
        )
        return result, _sources_from_markdown(result.text)

    def build(self, spec: CallSpec, workspace, allow_exec: bool, max_turns: int):
        result = self._invoke(
            self._compose(spec.system, spec.prompt),
            spec.cfg,
            spec.timeout,
            "danger-full-access" if allow_exec else "workspace-write",
            cwd=workspace.root,
        )
        transcript = [
            {
                "turn": 0,
                "type": "note",
                "text": f"built by {self.name} in {workspace.root} "
                f"(exec={'full' if allow_exec else 'workspace-write'})",
            },
            {"turn": 0, "type": "say", "text": result.text},
        ]
        return result, transcript


def _dominant_model(model_usage: dict, fallback: str) -> str:
    """The model that did the bulk of the work.

    The Claude CLI reports every model a session touched, including small ones
    used for incidental side tasks, so the first key is not reliably the one that
    answered. Pick whichever consumed the most tokens.
    """
    best, best_tokens = "", -1
    for name, stats in (model_usage or {}).items():
        if not isinstance(stats, dict):
            continue
        tokens = sum(
            int(stats.get(key, 0) or 0)
            for key in ("inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens")
        )
        if tokens > best_tokens:
            best, best_tokens = name, tokens
    return best or fallback


_URL_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _sources_from_markdown(text: str) -> list[dict]:
    """Recover cited sources from a CLI backend's markdown links."""
    seen: set[str] = set()
    out: list[dict] = []
    for title, url in _URL_RE.findall(text or ""):
        if url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

BACKENDS: dict[str, type[Backend]] = {
    "api": ApiBackend,
    "claude-cli": ClaudeCliBackend,
    "codex-cli": CodexCliBackend,
}

#: Preference order for ``--backend auto``. The API is tried first because it is
#: cheaper per call; the CLIs need no key of your own, so they are the fallback.
AUTO_ORDER = ("api", "claude-cli", "codex-cli")


def detect() -> list[str]:
    """Backend names that look usable right now."""
    return [name for name in AUTO_ORDER if BACKENDS[name].available()]


def make_backend(name: str = "auto", **kwargs) -> Backend:
    if name == "auto":
        for candidate in detect():
            return BACKENDS[candidate](**kwargs)
        raise BackendError(
            "no usable backend found. Either set ANTHROPIC_API_KEY, or install the "
            "Claude Code CLI (`claude`) or the Codex CLI (`codex`) and sign in."
        )
    if name not in BACKENDS:
        raise BackendError(f"unknown backend '{name}' (choose from {', '.join(BACKENDS)})")
    cls = BACKENDS[name]
    if not cls.available():
        raise BackendError(
            f"backend '{name}' is not usable here — "
            + ("no Anthropic credentials found" if name == "api" else f"`{cls.binary}` not on PATH")
        )
    return cls(**kwargs)
