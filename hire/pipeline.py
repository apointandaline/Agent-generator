"""The hiring pipeline: research, candidate generation, interviews, screening."""

from __future__ import annotations

import json
from pathlib import Path

from hire import prompts
from hire.config import CALL_SHAPES, price
from hire.kb import KnowledgeBase
from hire.llm import LLM, log, parallel
from hire.report import leaderboard_md, rank
from hire.store import Doc, Opening, now_iso, read_doc, write_text, write_yaml
from hire.workspace import Workspace


def _research_text(opening: Opening, limit: int = 30_000) -> str:
    path = opening.research_dir / "findings.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:limit]


def estimate(stage: str, calls: int, model: str) -> float:
    """Rough USD estimate for --dry-run, from the per-stage token shapes."""
    shape = CALL_SHAPES.get(stage, (5_000, 3_000))
    usage = {"input_tokens": shape[0] * calls, "output_tokens": shape[1] * calls}
    return price(model, usage)


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #


def run_research(
    opening: Opening,
    llm: LLM,
    *,
    focus: str = "",
    max_uses: int = 12,
    dry_run: bool = False,
) -> Path | None:
    spec = opening.load()
    cfg = opening.settings().stage("research")
    if dry_run:
        log(f"[dry-run] research: 1 web-search call on {cfg.model}, "
            f"~${estimate('research', 1, cfg.model):.2f}")
        return None

    log(f"Researching '{spec['role']}' on the web…")
    result, sources = llm.research(
        "research",
        cfg,
        system=prompts.RESEARCH_SYSTEM,
        prompt=prompts.research_prompt(
            spec["role"], spec.get("brief", ""), spec.get("qualifications", ""), focus
        ),
        max_uses=max_uses,
        label=opening.id,
    )

    header = [
        f"# Role research — {spec['role']}",
        "",
        f"Opening: `{opening.id}` · generated {now_iso()} · model {cfg.model}",
        "",
    ]
    if sources:
        header += ["## Sources", ""] + [f"- [{s['title']}]({s['url']})" for s in sources] + [""]
    else:
        header += [
            "> No web sources were returned for this run — the briefing below rests on",
            "> model knowledge alone. Treat it as weaker evidence and consider re-running.",
            "",
        ]
    path = write_text(
        opening.research_dir / "findings.md", "\n".join(header) + "---\n\n" + result.text + "\n"
    )
    write_yaml(opening.research_dir / "sources.yaml", {"sources": sources})

    # File the same briefing in the knowledge base so it is reusable and editable.
    kb = KnowledgeBase(opening.root)
    entry = kb.add(
        title=f"Role research: {spec['role']}",
        body=result.text,
        role=spec.get("kb_role", opening.id),
        tags=["research", "role-brief", opening.id],
        source=f"web research for opening {opening.id}",
        origin="hire research",
        entry_id=f"{opening.id}-role-research",
        sources=sources,
    )
    log(f"→ {path}")
    log(f"→ knowledge base entry `{entry.id}` ({len(sources)} sources)")
    return path


# --------------------------------------------------------------------------- #
# Candidate generation
# --------------------------------------------------------------------------- #


def build_slate(
    opening: Opening, llm: LLM, count: int, batch_size: int = 10
) -> list[dict]:
    """Generate ``count`` distinct archetypes, in batches that see what came before."""
    spec = opening.load()
    cfg = opening.settings().stage("slate")
    kb_context = KnowledgeBase(opening.root).context_for(spec.get("kb_role", opening.id))
    research = _research_text(opening)

    archetypes: list[dict] = []
    while len(archetypes) < count:
        want = min(batch_size, count - len(archetypes))
        log(f"  slate batch: {len(archetypes)}/{count} → +{want}")
        result = llm.json(
            "slate",
            cfg,
            system=prompts.SLATE_SYSTEM,
            prompt=prompts.slate_prompt(
                spec["role"], spec.get("brief", ""), spec.get("qualifications", ""),
                research, kb_context, want, archetypes,
            ),
            schema=prompts.SLATE_SCHEMA,
            label=f"batch{len(archetypes)}",
        )
        batch = result.data.get("archetypes", [])
        if not batch:
            raise RuntimeError("slate generation returned no archetypes")
        seen = {a["handle"] for a in archetypes}
        for item in batch:
            if item["handle"] in seen:
                item["handle"] = f"{item['handle']}-{len(archetypes)}"
            archetypes.append(item)
            seen.add(item["handle"])
    return archetypes[:count]


def _write_candidate(
    opening: Opening, rnd: int, cid: str, archetype: dict, body: str, extra: dict | None = None
) -> Path:
    spec = opening.load()
    meta = {
        "id": cid,
        "round": rnd,
        "opening": opening.id,
        "role": spec["role"],
        "handle": archetype.get("handle", cid),
        "display_name": archetype.get("display_name", cid),
        "archetype": archetype.get("background", ""),
        "thesis": archetype.get("thesis", ""),
        "specialties": archetype.get("specialties", []),
        "differentiator": archetype.get("differentiator", ""),
        "declared_weakness": archetype.get("weakness", ""),
        "axis": archetype.get("axis", ""),
        "generated_at": now_iso(),
    }
    meta.update(extra or {})
    return Doc(meta=meta, body=body).write(opening.candidate_path(rnd, cid))


def generate_round1(
    opening: Opening, llm: LLM, count: int, concurrency: int = 6, dry_run: bool = False
) -> list[str]:
    spec = opening.load()
    settings = opening.settings()
    cfg = settings.stage("candidate")
    if dry_run:
        slate_cost = estimate("slate", max(1, count // 10), settings.stage("slate").model)
        cand_cost = estimate("candidate", count, cfg.model)
        log(f"[dry-run] round 1: ~{max(1, count // 10)} slate calls + {count} candidate "
            f"calls, ~${slate_cost + cand_cost:.2f}")
        return []

    log(f"Designing a slate of {count} archetypes…")
    archetypes = build_slate(opening, llm, count)
    write_yaml(opening.round_dir(1) / "slate.yaml", {"archetypes": archetypes})

    kb_context = KnowledgeBase(opening.root).context_for(spec.get("kb_role", opening.id))
    research = _research_text(opening)
    width = max(2, len(str(count)))
    indexed = [(f"c{str(i + 1).zfill(width)}", a) for i, a in enumerate(archetypes)]

    def build(item: tuple[str, dict]) -> str:
        cid, archetype = item
        result = llm.text(
            "candidate",
            cfg,
            system=prompts.CANDIDATE_SYSTEM,
            prompt=prompts.candidate_prompt(
                spec["role"], spec.get("brief", ""), archetype, research, kb_context
            ),
            label=cid,
        )
        _write_candidate(opening, 1, cid, archetype, result.text)
        return cid

    log(f"Writing {count} candidate agents via {llm.name} (concurrency {concurrency})…")
    outcomes = parallel(
        indexed, build, concurrency=concurrency, label="candidate", name=lambda item: item[0]
    )
    made = [value for _, value, error in outcomes if error is None]
    failed = [item[0] for item, _, error in outcomes if error is not None]
    if failed:
        log(f"! {len(failed)} candidates failed to generate: {', '.join(failed)}")
    opening.update(status="round1-candidates-ready")
    return made


def generate_round2(
    opening: Opening, llm: LLM, concurrency: int = 6, dry_run: bool = False
) -> list[str]:
    """Regenerate variants seeded from the round-1 winners the CEO picked."""
    spec = opening.load()
    settings = opening.settings()
    cfg = settings.stage("candidate")
    decision = opening.load_decision(1)
    winners = list(decision.get("advance") or [])
    if not winners:
        raise RuntimeError("round 1 decision records no winners")

    per = settings.funnel.variants_per_winner
    axes = settings.variant_axes
    if len(axes) < per:
        axes = (axes * ((per // max(1, len(axes))) + 1))[:per]

    if dry_run:
        log(f"[dry-run] round 2: {len(winners) * per} candidate calls, "
            f"~${estimate('candidate', len(winners) * per, cfg.model):.2f}")
        return []

    notes = decision.get("notes") or {}
    kb_context = KnowledgeBase(opening.root).context_for(spec.get("kb_role", opening.id))
    research = _research_text(opening)

    jobs: list[tuple[str, str, dict]] = []
    for winner in winners:
        parent_doc = opening.load_candidate(1, winner)
        answer_path = opening.response_path(1, winner)
        answer = read_doc(answer_path).body if answer_path.exists() else ""
        for index in range(per):
            cid = f"{winner}-v{index + 1}"
            parent = {
                "body": parent_doc.body,
                "round1": answer,
                "notes": notes.get(winner, decision.get("general_notes", "")),
                "axis": axes[index],
            }
            archetype = {
                "handle": f"{parent_doc.meta.get('handle', winner)}-v{index + 1}",
                "display_name": f"{parent_doc.meta.get('display_name', winner)} "
                                f"(v{index + 1}: {axes[index].split(':')[0]})",
                "background": parent_doc.meta.get("archetype", ""),
                "thesis": parent_doc.meta.get("thesis", ""),
                "specialties": parent_doc.meta.get("specialties", []),
                "differentiator": parent_doc.meta.get("differentiator", ""),
                "weakness": parent_doc.meta.get("declared_weakness", ""),
                "axis": axes[index],
            }
            jobs.append((cid, winner, {"archetype": archetype, "parent": parent}))

    def build(job: tuple[str, str, dict]) -> str:
        cid, parent_id, payload = job
        result = llm.text(
            "candidate",
            cfg,
            system=prompts.CANDIDATE_SYSTEM,
            prompt=prompts.candidate_prompt(
                spec["role"], spec.get("brief", ""), payload["archetype"],
                research, kb_context, parent=payload["parent"],
            ),
            label=cid,
        )
        _write_candidate(
            opening, 2, cid, payload["archetype"], result.text,
            extra={"parent": parent_id, "axis": payload["parent"]["axis"]},
        )
        return cid

    log(f"Generating {len(jobs)} round-2 candidates from {len(winners)} winners…")
    outcomes = parallel(
        jobs, build, concurrency=concurrency, label="variant", name=lambda job: job[0]
    )
    made = [value for _, value, error in outcomes if error is None]
    failed = [job[0] for job, _, error in outcomes if error is not None]
    if failed:
        log(f"! {len(failed)} variants failed: {', '.join(failed)}")
    opening.update(status="round2-candidates-ready")
    return made


# --------------------------------------------------------------------------- #
# Round 1 — discussion
# --------------------------------------------------------------------------- #


def run_round1(
    opening: Opening,
    llm: LLM,
    prompt_file: Path,
    *,
    only: list[str] | None = None,
    concurrency: int = 6,
    dry_run: bool = False,
) -> list[str]:
    spec = opening.load()
    cfg = opening.settings().stage("round1")
    mock_project = Path(prompt_file).read_text(encoding="utf-8").strip()
    if not mock_project:
        raise RuntimeError(f"{prompt_file} is empty — it should hold your mock project prompt")
    write_text(opening.prompt_path(1), mock_project + "\n")

    candidates = opening.load_candidates(1, only)
    if not candidates:
        raise RuntimeError("no round-1 candidates — run `hire generate <id> --round 1` first")
    if dry_run:
        log(f"[dry-run] round 1: {len(candidates)} interview calls, "
            f"~${estimate('round1', len(candidates), cfg.model):.2f}")
        return []

    question = prompts.round1_prompt(spec["role"], mock_project)

    def interview(cid: str) -> str:
        doc = candidates[cid]
        result = llm.text("round1", cfg, system=doc.body, prompt=question, label=cid)
        Doc(
            meta={
                "candidate": cid,
                "round": 1,
                "opening": opening.id,
                "display_name": doc.meta.get("display_name", cid),
                "model": cfg.model,
                "interviewed_at": now_iso(),
            },
            body=result.text,
        ).write(opening.response_path(1, cid))
        return cid

    log(f"Round 1: interviewing {len(candidates)} candidates via {llm.name} "
        f"(concurrency {concurrency})…")
    outcomes = parallel(sorted(candidates), interview, concurrency=concurrency, label="interview")
    done = [value for _, value, error in outcomes if error is None]
    failed = [item for item, _, error in outcomes if error is not None]
    if failed:
        log(f"! {len(failed)} interviews failed: {', '.join(failed)}")
    opening.update(status="round1-interviewed")
    return done


# --------------------------------------------------------------------------- #
# Round 2 — technical, executed
# --------------------------------------------------------------------------- #


def _workspace_note(backend: str) -> str:
    """How the candidate reaches its files depends on which backend runs it."""
    if backend == "api":
        return (
            "Your workspace is the current directory. Use `write_file`, `read_file` and "
            "`list_files` with paths relative to it. You cannot reach anything outside it."
        )
    return (
        "Your workspace is the current working directory. Create files there with your "
        "normal file tools, using paths relative to it. Stay inside it."
    )


def run_round2(
    opening: Opening,
    llm: LLM,
    project_file: Path,
    *,
    only: list[str] | None = None,
    concurrency: int = 4,
    allow_exec: bool = False,
    exec_timeout: int = 120,
    max_turns: int = 40,
    dry_run: bool = False,
) -> list[str]:
    spec = opening.load()
    cfg = opening.settings().stage("round2")
    project = Path(project_file).read_text(encoding="utf-8").strip()
    if not project:
        raise RuntimeError(f"{project_file} is empty — it should hold your technical project")
    write_text(opening.prompt_path(2), project + "\n")

    candidates = opening.load_candidates(2, only)
    if not candidates:
        raise RuntimeError("no round-2 candidates — run `hire generate <id> --round 2` first")
    if dry_run:
        log(f"[dry-run] round 2: {len(candidates)} agent sessions up to {max_turns} turns, "
            f"~${estimate('round2', len(candidates), cfg.model):.2f} "
            f"(highly variable — sessions that use every turn cost several times this)")
        return []

    def session(cid: str) -> str:
        doc = candidates[cid]
        ws = Workspace(
            opening.workspace(cid), allow_exec=allow_exec, exec_timeout=exec_timeout
        )
        note = _workspace_note(llm.name)
        result, transcript = llm.build(
            "round2",
            cfg,
            system=doc.body,
            prompt=prompts.round2_prompt(spec["role"], project, allow_exec, note),
            workspace=ws,
            allow_exec=allow_exec,
            max_turns=max_turns,
            label=cid,
        )
        submission = ws.submission()
        write_text(
            opening.round_dir(2) / "transcripts" / f"{cid}.json",
            json.dumps(transcript, indent=2),
        )
        Doc(
            meta={
                "candidate": cid,
                "round": 2,
                "opening": opening.id,
                "display_name": doc.meta.get("display_name", cid),
                "model": cfg.model,
                "files_produced": len(ws.files()),
                "commands_run": len(ws.commands_run),
                "tool_calls": sum(1 for step in transcript if step["type"] == "tool"),
                "stop_reason": result.stop_reason,
                "submitted": bool(submission),
                "interviewed_at": now_iso(),
            },
            body=(submission or result.text or "(no submission produced)"),
        ).write(opening.response_path(2, cid))
        return cid

    log(f"Round 2: {len(candidates)} candidates building for real via {llm.name} "
        f"(concurrency {concurrency})…")
    if allow_exec:
        log("! run_command is ENABLED — candidate-written shell commands will execute on this machine")
    outcomes = parallel(sorted(candidates), session, concurrency=concurrency, label="build")
    done = [value for _, value, error in outcomes if error is None]
    failed = [item for item, _, error in outcomes if error is not None]
    if failed:
        log(f"! {len(failed)} sessions failed: {', '.join(failed)}")
    opening.update(status="round2-interviewed")
    return done


# --------------------------------------------------------------------------- #
# Screening (advisory)
# --------------------------------------------------------------------------- #


def run_screen(
    opening: Opening,
    llm: LLM,
    rnd: int,
    *,
    concurrency: int = 6,
    dry_run: bool = False,
) -> dict[str, dict]:
    spec = opening.load()
    settings = opening.settings()
    cfg = settings.stage("screen")
    rubric = settings.rubric
    task_path = opening.prompt_path(rnd)
    if not task_path.exists():
        raise RuntimeError(
            f"round {rnd} has not been run yet — no {task_path.name} to screen against"
        )
    task = task_path.read_text(encoding="utf-8")

    responses = sorted(opening.responses_dir(rnd).glob("*.md")) if opening.responses_dir(rnd).exists() else []
    if not responses:
        raise RuntimeError(f"no round-{rnd} responses to screen — run the round first")
    if dry_run:
        log(f"[dry-run] screen round {rnd}: {len(responses)} calls, "
            f"~${estimate('screen', len(responses), cfg.model):.2f}")
        return {}

    schema = prompts.screen_schema(rubric)
    # The rubric and task are identical across candidates — cache that prefix.
    system = [
        {"type": "text", "text": prompts.SCREEN_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    ids = [p.stem for p in responses]

    def assess(cid: str) -> dict:
        answer = read_doc(opening.response_path(rnd, cid)).body
        candidate = opening.load_candidate(rnd, cid)
        artifacts = ""
        if rnd == 2:
            ws_dir = opening.workspace(cid)
            if ws_dir.exists():
                artifacts = Workspace(ws_dir).digest()
        result = llm.json(
            "screen",
            cfg,
            system=system,
            prompt=prompts.screen_prompt(
                rnd, spec["role"], spec.get("brief", ""), task,
                candidate.body, answer, rubric, artifacts,
            ),
            schema=schema,
            label=cid,
        )
        return result.data

    log(f"Screening {len(ids)} round-{rnd} submissions (advisory)…")
    outcomes = parallel(ids, assess, concurrency=concurrency, label="screen")
    scorecards = {item: value for item, value, error in outcomes if error is None}
    failed = [item for item, _, error in outcomes if error is not None]
    if failed:
        log(f"! {len(failed)} screenings failed: {', '.join(failed)}")

    write_text(opening.scorecards_path(rnd), json.dumps(scorecards, indent=2))
    advance_target = (
        settings.funnel.round1_advance if rnd == 1 else settings.funnel.round2_advance
    )
    board = leaderboard_md(
        opening.id, rnd, spec["role"], scorecards, rubric, advance_target
    )
    write_text(opening.leaderboard_path(rnd), board)
    log(f"→ {opening.leaderboard_path(rnd)}")

    top = rank(scorecards, rubric)[: advance_target * 2]
    log("")
    log(f"Top {len(top)} by advisory score — you decide who actually advances:")
    for index, (cid, score, card) in enumerate(top, start=1):
        log(f"  {index:>2}. {cid:<12} {score:>5}  {card.get('one_line', '')[:80]}")
    return scorecards


def record_decision(
    opening: Opening,
    rnd: int,
    advance: list[str],
    *,
    notes: dict[str, str] | None = None,
    general_notes: str = "",
) -> Path:
    """Record the CEO's picks. This is the only thing that moves a round forward."""
    available = set(opening.candidate_ids(rnd))
    unknown = [cid for cid in advance if cid not in available]
    if unknown:
        raise RuntimeError(
            f"not round-{rnd} candidates: {', '.join(unknown)}\n"
            f"available: {', '.join(sorted(available))}"
        )
    expected = (
        opening.settings().funnel.round1_advance
        if rnd == 1
        else opening.settings().funnel.round2_advance
    )
    if len(advance) != expected:
        log(f"! advancing {len(advance)} candidates; the funnel expects {expected} — proceeding anyway")

    data = {
        "round": rnd,
        "decided_by": "CEO",
        "decided_at": now_iso(),
        "advance": advance,
        "notes": notes or {},
        "general_notes": general_notes,
    }
    path = write_yaml(opening.decision_path(rnd), data)
    opening.update(status=f"round{rnd}-decided")
    return path
