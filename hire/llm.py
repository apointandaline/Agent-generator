"""Stage-aware model caller.

:class:`LLM` is a thin facade over a :mod:`hire.backends` backend. It owns the
things every stage wants regardless of backend — usage accounting, empty/refusal
checks, progress logging and a bounded thread pool — and delegates the actual
model call.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence, TypeVar

from hire.backends import Backend, BackendError, BackendResult, CallSpec, make_backend
from hire.config import price

T = TypeVar("T")

_print_lock = threading.Lock()


def log(message: str) -> None:
    """Progress output on stderr, safe to call from worker threads."""
    with _print_lock:
        print(message, file=sys.stderr, flush=True)


class LLMError(RuntimeError):
    pass


@dataclass
class Result:
    """One model call: its text, any structured payload, and its usage."""

    text: str
    usage: dict[str, int]
    model: str
    stop_reason: str | None = None
    blocks: list[Any] = field(default_factory=list)
    data: Any = None
    cost_usd: float | None = None
    meta: dict = field(default_factory=dict)

    @property
    def cost(self) -> float:
        if self.cost_usd is not None:
            return self.cost_usd
        return price(self.model, self.usage)

    @classmethod
    def of(cls, result: BackendResult, fallback_model: str) -> "Result":
        return cls(
            text=result.text,
            usage=result.usage,
            model=result.model or fallback_model,
            stop_reason=result.stop_reason,
            data=result.data,
            cost_usd=result.cost_usd,
            meta=result.meta,
        )


# --------------------------------------------------------------------------- #
# Anthropic SDK helpers, shared with the API backend
# --------------------------------------------------------------------------- #

import anthropic  # noqa: E402  (imported after the light-weight definitions above)


def make_client(max_retries: int = 6, timeout: float = 1800.0) -> anthropic.Anthropic:
    """Build an SDK client. Credentials resolve from env or an `ant` profile."""
    return anthropic.Anthropic(max_retries=max_retries, timeout=timeout)


def has_credentials() -> bool:
    """True if the API backend looks usable."""
    from hire.backends import ApiBackend

    return ApiBackend.available()


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def _add_usage(into: dict[str, int], more: dict[str, int]) -> dict[str, int]:
    for key, value in more.items():
        into[key] = into.get(key, 0) + value
    return into


def _text_of(blocks: Sequence[Any]) -> str:
    return "\n".join(b.text for b in blocks if getattr(b, "type", None) == "text").strip()


def _collect_sources(blocks: Sequence[Any]) -> list[dict]:
    """Pull {title, url} out of web_search_tool_result blocks.

    Server-tool errors arrive as HTTP 200 with an error object in ``content``
    rather than a list, so branch on the shape before iterating.
    """
    found: list[dict] = []
    for block in blocks:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        if not isinstance(content, list):
            code = getattr(content, "error_code", None)
            if code:
                log(f"  ! web_search error: {code}")
            continue
        for item in content:
            url = getattr(item, "url", None)
            if url:
                found.append({"title": getattr(item, "title", "") or url, "url": url})
    return found


def _dedupe_sources(sources: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for source in sources:
        if source["url"] not in seen:
            seen.add(source["url"])
            out.append(source)
    return out


def run_tool_loop(
    client: anthropic.Anthropic,
    spec: CallSpec,
    *,
    tools: list[dict],
    execute: Callable[[str, dict], tuple[str, bool]],
    max_turns: int = 40,
) -> tuple[BackendResult, list[dict]]:
    """Manual tool-use loop for the API backend's round-2 build.

    A manual loop rather than the SDK tool runner: each step is recorded for the
    transcript and each tool call is confined by the caller's executor.
    """
    messages: list[dict] = [{"role": "user", "content": spec.prompt}]
    total: dict[str, int] = {}
    transcript: list[dict] = []
    message = None

    for turn in range(max_turns):
        with client.messages.stream(
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

        if message.stop_reason == "refusal":
            raise LLMError(f"[{spec.stage}/{spec.label}] the model declined this request")

        say = _text_of(message.content)
        if say:
            transcript.append({"turn": turn, "type": "say", "text": say})

        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        if message.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": message.content})
        results = []
        for block in message.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            # Inputs are parsed JSON from the SDK; never string-match them.
            tool_input = dict(block.input) if isinstance(block.input, dict) else {}
            output, is_error = execute(block.name, tool_input)
            transcript.append(
                {
                    "turn": turn,
                    "type": "tool",
                    "name": block.name,
                    "input": tool_input,
                    "output": output[:4000],
                    "is_error": is_error,
                }
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output or "(no output)",
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": results})
    else:
        transcript.append(
            {"turn": max_turns, "type": "note", "text": f"stopped at max_turns={max_turns}"}
        )

    return (
        BackendResult(
            text=_text_of(message.content) if message else "",
            usage=total,
            model=spec.cfg.model,
            stop_reason=message.stop_reason if message else None,
        ),
        transcript,
    )


# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #


class LLM:
    """Stage-aware caller. ``on_usage`` receives (stage, model, usage, cost, label)."""

    def __init__(
        self,
        backend: Backend | str = "auto",
        on_usage: Callable[[str, str, dict, float, str], None] | None = None,
        client: anthropic.Anthropic | None = None,
    ):
        if isinstance(backend, str):
            backend = make_backend(backend, **({"client": client} if client else {}))
        self.backend = backend
        self.on_usage = on_usage

    @property
    def name(self) -> str:
        return self.backend.name

    @property
    def concurrency(self) -> int:
        return self.backend.default_concurrency

    def _spec(self, stage, cfg, system, prompt, label, timeout=None) -> CallSpec:
        # A cached system prefix arrives as SDK content blocks; CLI backends want
        # plain text, so flatten when the backend is not the API.
        if not isinstance(system, str):
            if self.backend.name == "api":
                pass
            else:
                system = "\n\n".join(
                    block.get("text", "") for block in system if isinstance(block, dict)
                ).strip()
        spec = CallSpec(stage=stage, cfg=cfg, system=system, prompt=prompt, label=label)
        if timeout:
            spec.timeout = timeout
        return spec

    def _record(self, stage: str, result: Result, label: str) -> Result:
        if self.on_usage:
            self.on_usage(stage, result.model, result.usage, result.cost, label)
        return result

    def _check(self, stage: str, label: str, result: Result) -> Result:
        if result.stop_reason == "refusal":
            raise LLMError(f"[{stage}/{label}] the model declined this request")
        if not result.text:
            raise LLMError(f"[{stage}/{label}] empty response (stop_reason={result.stop_reason})")
        return result

    # -- plain text -------------------------------------------------------- #
    def text(self, stage, cfg, *, system, prompt, label="", timeout=None) -> Result:
        spec = self._spec(stage, cfg, system, prompt, label, timeout)
        result = Result.of(self.backend.complete(spec), cfg.model)
        return self._record(stage, self._check(stage, label, result), label)

    # -- structured -------------------------------------------------------- #
    def json(self, stage, cfg, *, system, prompt, schema, label="", timeout=None) -> Result:
        spec = self._spec(stage, cfg, system, prompt, label, timeout)
        try:
            result = Result.of(self.backend.complete_json(spec, schema), cfg.model)
        except BackendError as exc:
            raise LLMError(f"[{stage}/{label}] {exc}") from exc
        return self._record(stage, result, label)

    # -- web research ------------------------------------------------------ #
    def research(
        self, stage, cfg, *, system, prompt, max_uses=12, label="", timeout=None
    ) -> tuple[Result, list[dict]]:
        spec = self._spec(stage, cfg, system, prompt, label, timeout)
        backend_result, sources = self.backend.research(spec, max_uses)
        result = Result.of(backend_result, cfg.model)
        return self._record(stage, self._check(stage, label, result), label), sources

    # -- round-2 build ----------------------------------------------------- #
    def build(
        self, stage, cfg, *, system, prompt, workspace, allow_exec=False, max_turns=40,
        label="", timeout=None,
    ) -> tuple[Result, list[dict]]:
        """Have the candidate do real work in ``workspace``.

        The API backend drives a tool loop over the workspace's confined file
        tools; the CLI backends run their own agent with the workspace as cwd.
        """
        from hire.backends import BUILD_TIMEOUT

        spec = self._spec(stage, cfg, system, prompt, label, timeout or BUILD_TIMEOUT)
        backend_result, transcript = self.backend.build(spec, workspace, allow_exec, max_turns)
        result = Result.of(backend_result, cfg.model)
        return self._record(stage, result, label), transcript


def parallel(
    items: Sequence[T],
    worker: Callable[[T], Any],
    concurrency: int = 6,
    label: str = "task",
    name: Callable[[T], str] = str,
) -> list[tuple[T, Any, BaseException | None]]:
    """Run ``worker`` over ``items``, preserving input order in the results.

    ``name`` renders an item for the progress line, so callers can pass rich
    work items without dumping them to the terminal.

    Failures are captured rather than raised so one bad candidate cannot sink a
    50-candidate round.
    """
    results: list[tuple[T, Any, BaseException | None]] = [(item, None, None) for item in items]
    done = 0
    total = len(items)

    def run(index: int, item: T) -> None:
        nonlocal done
        try:
            value, error = worker(item), None
        except BaseException as exc:  # noqa: BLE001 - reported per item below
            value, error = None, exc
        results[index] = (item, value, error)
        with _print_lock:
            done += 1
            status = "ok" if error is None else f"FAILED: {type(error).__name__}: {error}"
            print(f"  [{done}/{total}] {label} {name(item)} {status}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for index, item in enumerate(items):
            pool.submit(run, index, item)
    return results
