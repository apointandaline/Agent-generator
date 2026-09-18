"""`hire` — the CEO's command line for running the agent hiring funnel."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from hire import backends, finalize, pipeline
from hire.config import DEFAULT_RUBRIC, STAGES, default_stage_models
from hire.kb import KnowledgeBase
from hire.llm import LLM
from hire.report import cost_summary, next_step, status_report
from hire.store import Opening, list_openings, now_iso, repo_root, slugify


class CLIError(RuntimeError):
    pass


def _opening(args) -> Opening:
    opening = Opening(args.opening, repo_root())
    if not opening.exists():
        known = list_openings()
        hint = f" Known openings: {', '.join(known)}" if known else ""
        raise CLIError(f"no opening '{args.opening}'.{hint}")
    return opening


def _llm(opening: Opening, args=None, dry_run: bool = False) -> LLM | None:
    """Build a usage-logging LLM on the requested backend, or None for a dry run."""
    if dry_run:
        return None
    choice = getattr(args, "backend", None) or os.environ.get("HIRE_BACKEND", "auto")
    try:
        llm = LLM(backend=choice, on_usage=opening.log_usage)
    except backends.BackendError as exc:
        raise CLIError(str(exc)) from exc
    return llm


def _concurrency(args, llm: LLM | None) -> int:
    """Honour an explicit --concurrency, else follow the backend's own default."""
    if getattr(args, "concurrency", None):
        return args.concurrency
    return llm.concurrency if llm else 4


def _ids(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.replace(",", " ").split() if part.strip()]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_init(args) -> int:
    root = repo_root()
    for sub in ("openings", "agents", "kb/entries"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    print(f"Initialised hiring workspace at {root}")
    print("Next: hire open --id <opening-id> --role '<role>' --brief '<what you want>'")
    return 0


def cmd_open(args) -> int:
    opening_id = slugify(args.id or args.role)
    opening = Opening(opening_id, repo_root())
    if opening.exists() and not args.force:
        raise CLIError(f"opening '{opening_id}' already exists (use --force to overwrite its spec)")

    brief = args.brief
    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8").strip()
    if not brief:
        raise CLIError("a brief is required: --brief '<text>' or --brief-file <path>")

    spec = {
        "id": opening_id,
        "role": args.role,
        "brief": brief,
        "qualifications": args.qualifications or "",
        "kb_role": args.kb_role or opening_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "open",
        "funnel": {
            "round1_candidates": args.round1_candidates,
            "round1_advance": args.round1_advance,
            "variants_per_winner": args.variants_per_winner,
            "round2_advance": args.round2_advance,
        },
        "models": {name: cfg.to_dict() for name, cfg in default_stage_models().items()},
        "rubric": [dict(item) for item in DEFAULT_RUBRIC],
    }
    if args.model:
        for name in STAGES:
            spec["models"][name]["model"] = args.model
    opening.save(spec)
    print(f"Created openings/{opening_id}/opening.yaml")
    print(f"Next: {next_step(opening)}")
    return 0


def cmd_research(args) -> int:
    opening = _opening(args)
    pipeline.run_research(
        opening,
        _llm(opening, args, args.dry_run),
        focus=args.focus or "",
        max_uses=args.max_uses,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_generate(args) -> int:
    opening = _opening(args)
    llm = _llm(opening, args, args.dry_run)
    if args.round == 1:
        count = args.count or opening.settings().funnel.round1_candidates
        pipeline.generate_round1(opening, llm, count, _concurrency(args, llm), args.dry_run)
    elif args.round == 2:
        pipeline.generate_round2(opening, llm, _concurrency(args, llm), args.dry_run)
    else:
        raise CLIError("--round must be 1 or 2 (round 3 has no candidate generation)")
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_round1(args) -> int:
    opening = _opening(args)
    prompt_file = Path(args.prompt_file)
    if not prompt_file.exists():
        raise CLIError(f"no such file: {prompt_file}")
    llm = _llm(opening, args, args.dry_run)
    pipeline.run_round1(
        opening,
        llm,
        prompt_file,
        only=_ids(args.only),
        concurrency=_concurrency(args, llm),
        dry_run=args.dry_run,
    )
    if not args.dry_run and not args.no_screen:
        pipeline.run_screen(opening, llm, 1, concurrency=_concurrency(args, llm))
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_round2(args) -> int:
    opening = _opening(args)
    project_file = Path(args.project_file)
    if not project_file.exists():
        raise CLIError(f"no such file: {project_file}")
    if args.allow_exec and not args.dry_run and not args.yes:
        print(
            "\n--allow-exec lets candidate-written shell commands run on this machine.\n"
            "Commands are confined to each candidate's workspace directory and run with\n"
            "your API keys stripped from the environment, but they are NOT sandboxed:\n"
            "they can reach the network and the rest of the filesystem.\n"
            "Prefer running this inside a container or a throwaway VM.\n",
            file=sys.stderr,
        )
        if input("Enable command execution? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted. Re-run without --allow-exec for a no-execution round.")
            return 1
    llm = _llm(opening, args, args.dry_run)
    pipeline.run_round2(
        opening,
        llm,
        project_file,
        only=_ids(args.only),
        concurrency=_concurrency(args, llm),
        allow_exec=args.allow_exec,
        exec_timeout=args.exec_timeout,
        max_turns=args.max_turns,
        dry_run=args.dry_run,
    )
    if not args.dry_run and not args.no_screen:
        pipeline.run_screen(opening, llm, 2, concurrency=_concurrency(args, llm))
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_screen(args) -> int:
    opening = _opening(args)
    llm = _llm(opening, args, args.dry_run)
    pipeline.run_screen(
        opening, llm, args.round,
        concurrency=_concurrency(args, llm), dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_shortlist(args) -> int:
    opening = _opening(args)
    advance = _ids(args.advance) or []
    if not advance:
        raise CLIError("--advance is required, e.g. --advance c07,c19,c31,c42,c48")
    notes: dict[str, str] = {}
    if args.notes_file:
        raw = Path(args.notes_file).read_text(encoding="utf-8")
        try:
            notes = json.loads(raw)
        except json.JSONDecodeError:
            notes = {}
            general = raw
        else:
            general = args.notes or ""
    else:
        general = args.notes or ""
    path = pipeline.record_decision(
        opening, args.round, advance, notes=notes, general_notes=general
    )
    print(f"Recorded your round-{args.round} decision: {', '.join(advance)}")
    print(f"→ {path}")
    print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_round3(args) -> int:
    opening = _opening(args)
    finalize.run_round3(opening, _llm(opening, args, args.dry_run), dry_run=args.dry_run)
    if not args.dry_run:
        print(f"\nNext: {next_step(opening)}")
    return 0


def cmd_hire(args) -> int:
    opening = _opening(args)
    notes = args.notes or ""
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8").strip()
    refine = not args.no_refine
    paths = finalize.hire_candidate(
        opening,
        _llm(opening, args, args.dry_run) if refine else None,
        args.candidate,
        args.name,
        notes=notes,
        refine=refine,
        description=args.description or "",
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(f"\nHired. The agent's system prompt is {paths['agent'].relative_to(repo_root())}")
        print(f"Add skills to {paths['skills'].relative_to(repo_root())}/ as you use it.")
    return 0


def cmd_backends(args) -> int:
    usable = backends.detect()
    print("Backend        available  notes")
    rows = {
        "api": "Anthropic SDK; needs ANTHROPIC_API_KEY or an `ant` profile",
        "claude-cli": "`claude -p` subprocess; uses the Claude Code CLI's own login",
        "codex-cli": "`codex exec` subprocess; uses the Codex CLI's own login",
    }
    for name, note in rows.items():
        mark = "yes" if name in usable else "no "
        print(f"{name:<14} {mark:<10} {note}")
    print()
    if usable:
        print(f"`--backend auto` would choose: {usable[0]}")
    else:
        print("No backend is usable here. Install a CLI and sign in, or set ANTHROPIC_API_KEY.")
    return 0


def cmd_status(args) -> int:
    if not args.opening:
        openings = list_openings()
        if not openings:
            print("No openings yet. Start with: hire open --id <id> --role '<role>' --brief '…'")
            return 0
        for oid in openings:
            opening = Opening(oid, repo_root())
            spec = opening.load()
            print(f"{oid:<24} {spec.get('status', '?'):<24} {spec.get('role', '')}")
        return 0
    print(status_report(_opening(args)), end="")
    return 0


def cmd_cost(args) -> int:
    print(cost_summary(_opening(args)))
    return 0


def cmd_show(args) -> int:
    opening = _opening(args)
    if args.response:
        path = opening.response_path(args.round, args.candidate)
    else:
        path = opening.candidate_path(args.round, args.candidate)
    if not path.exists():
        raise CLIError(f"no such file: {path}")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


# -- knowledge base --------------------------------------------------------- #


def cmd_kb(args) -> int:
    kb = KnowledgeBase(repo_root())

    if args.kb_command == "list":
        entries = kb.list(role=args.role, tag=args.tag)
        if not entries:
            print("Knowledge base is empty for that filter.")
            return 0
        print(f"{'id':<40} {'role':<18} {'tags':<28} summary")
        for entry in entries:
            print(
                f"{entry.id:<40} {entry.role:<18} {','.join(entry.tags)[:26]:<28} "
                f"{entry.summary(70)}"
            )
        return 0

    if args.kb_command == "show":
        entry = kb.get(args.entry)
        print(f"# {entry.title}\n")
        print(f"role: {entry.role}\ntags: {', '.join(entry.tags)}\nsource: {entry.source}")
        print(f"file: {entry.path}\n")
        print(entry.body)
        return 0

    if args.kb_command == "search":
        hits = kb.search(args.query, role=args.role)
        if not hits:
            print("No matches.")
            return 0
        for entry, score in hits:
            print(f"{score:>4}  {entry.id:<40} {entry.title}")
        return 0

    if args.kb_command == "add":
        if args.file:
            body = Path(args.file).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            body = sys.stdin.read()
        else:
            raise CLIError("provide --file <path> or pipe the entry body on stdin")
        title = args.title or (Path(args.file).stem if args.file else "untitled")
        entry = kb.add(
            title=title,
            body=body,
            role=args.role or "general",
            tags=_ids(args.tags) or [],
            source=args.source or "",
            origin="manual",
            entry_id=args.id,
        )
        print(f"Added {entry.id} → {entry.path}")
        return 0

    if args.kb_command == "path":
        print(kb.dir)
        return 0

    raise CLIError("unknown kb subcommand")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hire",
        description="Run a three-round interview funnel that produces a specialised AI agent.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def add_common(sub, dry: bool = True, conc: int | None = 6):
        if dry:
            sub.add_argument("--dry-run", action="store_true", help="estimate cost, call nothing")
        if conc is not None:
            sub.add_argument("--concurrency", type=int, default=None,
                             help="parallel calls (default: the backend's own, "
                                  "6 for api, 4 for the CLI backends)")
        sub.add_argument("--backend", choices=["auto", *backends.BACKENDS],
                         help="which model backend to drive (default: auto, or $HIRE_BACKEND)")

    p = subs.add_parser("init", help="create the directory layout")
    p.set_defaults(func=cmd_init)

    p = subs.add_parser("open", help="define a new opening")
    p.add_argument("--id", help="short id (defaults to a slug of --role)")
    p.add_argument("--role", required=True, help="e.g. 'QA engineer for trading strategies'")
    p.add_argument("--brief", help="what you want from this agent")
    p.add_argument("--brief-file", help="read the brief from a file instead")
    p.add_argument("--qualifications", help="specific qualifications you require")
    p.add_argument("--kb-role", help="knowledge-base role tag to draw on (defaults to the id)")
    p.add_argument("--model", help="override the model for every stage")
    p.add_argument("--round1-candidates", type=int, default=50)
    p.add_argument("--round1-advance", type=int, default=5)
    p.add_argument("--variants-per-winner", type=int, default=5)
    p.add_argument("--round2-advance", type=int, default=5)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_open)

    p = subs.add_parser("research", help="research the role on the web, file it in the KB")
    p.add_argument("opening")
    p.add_argument("--focus", help="extra angle you want the research to cover")
    p.add_argument("--max-uses", type=int, default=12, help="web search budget")
    add_common(p, conc=None)
    p.set_defaults(func=cmd_research)

    p = subs.add_parser("generate", help="generate candidates for a round")
    p.add_argument("opening")
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    p.add_argument("--count", type=int, help="round 1 only; defaults to the funnel setting")
    add_common(p)
    p.set_defaults(func=cmd_generate)

    p = subs.add_parser("round1", help="run round 1 — candidates talk through your mock project")
    p.add_argument("opening")
    p.add_argument("--prompt-file", required=True, help="markdown file with your mock project prompt")
    p.add_argument("--only", help="limit to these candidate ids")
    p.add_argument("--no-screen", action="store_true", help="skip the advisory scoring pass")
    add_common(p)
    p.set_defaults(func=cmd_round1)

    p = subs.add_parser("round2", help="run round 2 — candidates build your technical project")
    p.add_argument("opening")
    p.add_argument("--project-file", required=True, help="markdown file with the technical project")
    p.add_argument("--only", help="limit to these candidate ids")
    p.add_argument("--allow-exec", action="store_true",
                   help="let candidates run shell commands (NOT sandboxed — read the prompt)")
    p.add_argument("--yes", action="store_true", help="skip the --allow-exec confirmation")
    p.add_argument("--exec-timeout", type=int, default=120, help="per-command timeout in seconds")
    p.add_argument("--max-turns", type=int, default=40, help="tool-use turns per candidate")
    p.add_argument("--no-screen", action="store_true")
    add_common(p, conc=4)
    p.set_defaults(func=cmd_round2)

    p = subs.add_parser("screen", help="re-run the advisory scoring for a round")
    p.add_argument("opening")
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    add_common(p)
    p.set_defaults(func=cmd_screen)

    p = subs.add_parser("shortlist", help="record YOUR decision on who advances")
    p.add_argument("opening")
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    p.add_argument("--advance", required=True, help="comma- or space-separated candidate ids")
    p.add_argument("--notes", help="why — this is fed into the next round's generation")
    p.add_argument("--notes-file", help="notes as text, or JSON mapping candidate id → note")
    p.set_defaults(func=cmd_shortlist)

    p = subs.add_parser("round3", help="build the executive brief over the finalists")
    p.add_argument("opening")
    add_common(p, conc=None)
    p.set_defaults(func=cmd_round3)

    p = subs.add_parser("hire", help="hire a finalist and emit its agent file")
    p.add_argument("opening")
    p.add_argument("--candidate", required=True, help="round-2 candidate id")
    p.add_argument("--name", help="agent name / filename (defaults to the candidate's handle)")
    p.add_argument("--description", help="one-line description for the agent's frontmatter")
    p.add_argument("--notes", help="your notes, applied as binding instructions in refinement")
    p.add_argument("--notes-file")
    p.add_argument("--no-refine", action="store_true",
                   help="emit the candidate file as-is instead of refining it")
    add_common(p, conc=None)
    p.set_defaults(func=cmd_hire)

    p = subs.add_parser("backends", help="show which model backends are usable here")
    p.set_defaults(func=cmd_backends)

    p = subs.add_parser("status", help="where an opening stands, and what to run next")
    p.add_argument("opening", nargs="?")
    p.set_defaults(func=cmd_status)

    p = subs.add_parser("cost", help="estimated API spend for an opening")
    p.add_argument("opening")
    p.set_defaults(func=cmd_cost)

    p = subs.add_parser("show", help="print a candidate file or an interview answer")
    p.add_argument("opening")
    p.add_argument("--candidate", required=True)
    p.add_argument("--round", type=int, default=1, choices=[1, 2])
    p.add_argument("--response", action="store_true", help="show the answer instead of the agent")
    p.set_defaults(func=cmd_show)

    p = subs.add_parser("kb", help="inspect and extend the local knowledge base")
    kb_subs = p.add_subparsers(dest="kb_command", required=True)

    k = kb_subs.add_parser("list", help="list entries")
    k.add_argument("--role")
    k.add_argument("--tag")
    k = kb_subs.add_parser("show", help="print one entry")
    k.add_argument("entry")
    k = kb_subs.add_parser("search", help="search entries")
    k.add_argument("query")
    k.add_argument("--role")
    k = kb_subs.add_parser("add", help="add an entry from a file or stdin")
    k.add_argument("--file")
    k.add_argument("--title")
    k.add_argument("--role", help="role tag — how candidate generation finds it")
    k.add_argument("--tags", help="comma-separated")
    k.add_argument("--source", help="where this came from")
    k.add_argument("--id", help="explicit entry id")
    kb_subs.add_parser("path", help="print the entries directory")
    p.set_defaults(func=cmd_kb)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
