"""Thin wrapper over the Anthropic SDK: text calls, JSON calls, server-tool
research calls, a client-tool agentic loop, and a bounded thread pool.

Every call returns a :class:`Result` carrying usage so the caller can write a
cost ledger entry.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence, TypeVar

import anthropic

from hire.config import StageConfig, price

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

    @property
    def cost(self) -> float:
        return price(self.model, self.usage)


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


def make_client(max_retries: int = 6, timeout: float = 1800.0) -> anthropic.Anthropic:
    """Build a client. Credentials resolve from the environment or an `ant` profile."""
    return anthropic.Anthropic(max_retries=max_retries, timeout=timeout)


def has_credentials() -> bool:
    """True if some credential source looks present (env var or an `ant` profile)."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile_dir = os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(profile_dir)


class LLM:
    """Stage-aware caller. ``on_usage`` receives (stage, model, usage, cost, label)."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        on_usage: Callable[[str, str, dict, float, str], None] | None = None,
    ):
        self.client = client or make_client()
        self.on_usage = on_usage

    def _record(self, stage: str, result: Result, label: str) -> Result:
        if self.on_usage:
            self.on_usage(stage, result.model, result.usage, result.cost, label)
        return result

    # -- plain text -------------------------------------------------------- #
    def text(
        self,
        stage: str,
        cfg: StageConfig,
        *,
        system: str | list[dict],
        prompt: str,
        label: str = "",
        prefill_messages: list[dict] | None = None,
    ) -> Result:
        """One streamed request returning prose."""
        messages = list(prefill_messages or []) + [{"role": "user", "content": prompt}]
        with self.client.messages.stream(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": cfg.effort},
            messages=messages,
        ) as stream:
            message = stream.get_final_message()
        result = Result(
            text=_text_of(message.content),
            usage=_usage_dict(message.usage),
            model=cfg.model,
            stop_reason=message.stop_reason,
            blocks=list(message.content),
        )
        if message.stop_reason == "refusal":
            raise LLMError(f"[{stage}/{label}] the model declined this request")
        if not result.text:
            raise LLMError(f"[{stage}/{label}] empty response (stop_reason={message.stop_reason})")
        return self._record(stage, result, label)

    # -- structured -------------------------------------------------------- #
    def json(
        self,
        stage: str,
        cfg: StageConfig,
        *,
        system: str | list[dict],
        prompt: str,
        schema: dict,
        label: str = "",
    ) -> Result:
        """One request constrained to a JSON schema; ``result.data`` is parsed."""
        message = self.client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "refusal":
            raise LLMError(f"[{stage}/{label}] the model declined this request")
        text = _text_of(message.content)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - guarded by schema
            raise LLMError(f"[{stage}/{label}] response was not valid JSON: {exc}") from exc
        result = Result(
            text=text,
            usage=_usage_dict(message.usage),
            model=cfg.model,
            stop_reason=message.stop_reason,
            blocks=list(message.content),
            data=data,
        )
        return self._record(stage, result, label)

    # -- server tools (web search) ----------------------------------------- #
    def research(
        self,
        stage: str,
        cfg: StageConfig,
        *,
        system: str,
        prompt: str,
        max_uses: int = 12,
        max_restarts: int = 6,
        label: str = "",
    ) -> tuple[Result, list[dict]]:
        """Run a web-search-backed request, resuming across ``pause_turn``.

        Returns the final result plus the sources the search surfaced.
        """
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        total: dict[str, int] = {}
        sources: list[dict] = []
        final = None

        for _ in range(max_restarts + 1):
            with self.client.messages.stream(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.effort},
                tools=tools,
                messages=messages,
            ) as stream:
                message = stream.get_final_message()
            _add_usage(total, _usage_dict(message.usage))
            sources.extend(_collect_sources(message.content))
            final = message
            if message.stop_reason != "pause_turn":
                break
            # Paused mid-turn: append the partial turn and let the next request resume it.
            messages.append({"role": "assistant", "content": message.content})
        else:  # pragma: no cover - only on a pathological pause loop
            raise LLMError(f"[{stage}/{label}] still paused after {max_restarts} restarts")

        if final.stop_reason == "refusal":
            raise LLMError(f"[{stage}/{label}] the model declined this request")
        result = Result(
            text=_text_of(final.content),
            usage=total,
            model=cfg.model,
            stop_reason=final.stop_reason,
            blocks=list(final.content),
        )
        return self._record(stage, result, label), _dedupe_sources(sources)

    # -- client tools (round 2 execution) ---------------------------------- #
    def agent_loop(
        self,
        stage: str,
        cfg: StageConfig,
        *,
        system: str,
        prompt: str,
        tools: list[dict],
        execute: Callable[[str, dict], tuple[str, bool]],
        max_turns: int = 40,
        label: str = "",
        on_step: Callable[[int, str, dict], None] | None = None,
    ) -> tuple[Result, list[dict]]:
        """Drive a manual tool-use loop. ``execute`` returns (result_text, is_error).

        A manual loop rather than the SDK tool runner: each step is recorded for
        the transcript and each tool call is confined by the caller's executor.
        """
        messages: list[dict] = [{"role": "user", "content": prompt}]
        total: dict[str, int] = {}
        transcript: list[dict] = []
        message = None

        for turn in range(max_turns):
            with self.client.messages.stream(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.effort},
                tools=tools,
                messages=messages,
            ) as stream:
                message = stream.get_final_message()
            _add_usage(total, _usage_dict(message.usage))

            if message.stop_reason == "refusal":
                raise LLMError(f"[{stage}/{label}] the model declined this request")

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
                if on_step:
                    on_step(turn, block.name, tool_input)
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

        result = Result(
            text=_text_of(message.content) if message else "",
            usage=total,
            model=cfg.model,
            stop_reason=message.stop_reason if message else None,
            blocks=list(message.content) if message else [],
        )
        return self._record(stage, result, label), transcript


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
