"""On-disk layout: openings, candidates, transcripts, decisions, ledger.

Everything the workflow produces is a plain file you can read, edit by hand and
commit. Candidate agents are markdown with YAML frontmatter; the body of the
file *is* the agent's system prompt.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from hire.config import (
    DEFAULT_VARIANT_AXES,
    FunnelConfig,
    Settings,
    StageConfig,
    default_stage_models,
)

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def repo_root() -> Path:
    """Project root: $HIRE_HOME, else the cwd."""
    return Path(os.environ.get("HIRE_HOME", Path.cwd())).resolve()


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "untitled"


# --------------------------------------------------------------------------- #
# Markdown with YAML frontmatter
# --------------------------------------------------------------------------- #


@dataclass
class Doc:
    """A markdown document with YAML frontmatter."""

    meta: dict[str, Any]
    body: str

    def render(self) -> str:
        front = yaml.safe_dump(self.meta, sort_keys=False, allow_unicode=True).rstrip()
        return f"---\n{front}\n---\n\n{self.body.strip()}\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path


def parse_doc(text: str) -> Doc:
    """Split frontmatter from body. A file with no frontmatter yields empty meta."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return Doc(meta={}, body=text.strip())
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return Doc(meta=meta, body=match.group(2).strip())


def read_doc(path: Path) -> Doc:
    return parse_doc(Path(path).read_text(encoding="utf-8"))


def read_yaml(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def write_text(path: Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Openings
# --------------------------------------------------------------------------- #


class Opening:
    """Paths and metadata for one hiring opening."""

    def __init__(self, opening_id: str, root: Path | None = None):
        self.id = opening_id
        self.root = (root or repo_root()).resolve()
        self.dir = self.root / "openings" / opening_id

    # -- paths ------------------------------------------------------------- #
    @property
    def spec_path(self) -> Path:
        return self.dir / "opening.yaml"

    @property
    def research_dir(self) -> Path:
        return self.dir / "research"

    @property
    def ledger_path(self) -> Path:
        return self.dir / "ledger.jsonl"

    def round_dir(self, rnd: int) -> Path:
        return self.dir / f"round{rnd}"

    def candidates_dir(self, rnd: int) -> Path:
        return self.round_dir(rnd) / "candidates"

    def responses_dir(self, rnd: int) -> Path:
        return self.round_dir(rnd) / "responses"

    def workspaces_dir(self) -> Path:
        return self.round_dir(2) / "workspaces"

    def workspace(self, candidate_id: str) -> Path:
        return self.workspaces_dir() / candidate_id

    def prompt_path(self, rnd: int) -> Path:
        return self.round_dir(rnd) / ("prompt.md" if rnd == 1 else "project.md")

    def digests_path(self, rnd: int) -> Path:
        return self.round_dir(rnd) / "digests.json"

    def comparison_path(self, rnd: int) -> Path:
        return self.round_dir(rnd) / "comparison.md"

    def decision_path(self, rnd: int) -> Path:
        return self.round_dir(rnd) / "decision.yaml"

    @property
    def brief_path(self) -> Path:
        return self.round_dir(3) / "exec-brief.md"

    # -- spec -------------------------------------------------------------- #
    def exists(self) -> bool:
        return self.spec_path.exists()

    def load(self) -> dict:
        if not self.exists():
            raise FileNotFoundError(
                f"no opening '{self.id}' — run `hire open --id {self.id} ...` first"
            )
        return read_yaml(self.spec_path) or {}

    def save(self, spec: dict) -> Path:
        return write_yaml(self.spec_path, spec)

    def update(self, **fields: Any) -> dict:
        spec = self.load()
        spec.update(fields)
        spec["updated_at"] = now_iso()
        self.save(spec)
        return spec

    def settings(self) -> Settings:
        spec = self.load()
        stages = default_stage_models()
        for name, cfg in (spec.get("models") or {}).items():
            base = stages.get(name, StageConfig())
            stages[name] = StageConfig(
                model=cfg.get("model", base.model),
                effort=cfg.get("effort", base.effort),
                max_tokens=int(cfg.get("max_tokens", base.max_tokens)),
            )
        funnel_spec = spec.get("funnel") or {}
        funnel = FunnelConfig(
            round1_candidates=int(funnel_spec.get("round1_candidates", 50)),
            round1_advance=int(funnel_spec.get("round1_advance", 5)),
            variants_per_winner=int(funnel_spec.get("variants_per_winner", 5)),
            round2_advance=int(funnel_spec.get("round2_advance", 5)),
        )
        return Settings(
            stages=stages,
            funnel=funnel,
            variant_axes=spec.get("variant_axes") or list(DEFAULT_VARIANT_AXES),
        )

    # -- candidates -------------------------------------------------------- #
    def candidate_path(self, rnd: int, candidate_id: str) -> Path:
        return self.candidates_dir(rnd) / f"{candidate_id}.md"

    def candidate_ids(self, rnd: int) -> list[str]:
        directory = self.candidates_dir(rnd)
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.md"))

    def load_candidate(self, rnd: int, candidate_id: str) -> Doc:
        path = self.candidate_path(rnd, candidate_id)
        if not path.exists():
            raise FileNotFoundError(f"no candidate {candidate_id} in round {rnd}: {path}")
        return read_doc(path)

    def load_candidates(self, rnd: int, only: Iterable[str] | None = None) -> dict[str, Doc]:
        ids = list(only) if only else self.candidate_ids(rnd)
        return {cid: self.load_candidate(rnd, cid) for cid in ids}

    def response_path(self, rnd: int, candidate_id: str) -> Path:
        return self.responses_dir(rnd) / f"{candidate_id}.md"

    # -- decisions --------------------------------------------------------- #
    def load_decision(self, rnd: int) -> dict:
        data = read_yaml(self.decision_path(rnd))
        if not data:
            raise FileNotFoundError(
                f"round {rnd} has no recorded decision — run "
                f"`hire shortlist {self.id} --round {rnd} --advance <ids>`"
            )
        return data

    def advanced(self, rnd: int) -> list[str]:
        return list(self.load_decision(rnd).get("advance") or [])

    # -- ledger ------------------------------------------------------------ #
    def log_usage(self, stage: str, model: str, usage: dict, cost: float, label: str = "") -> None:
        entry = {
            "ts": now_iso(),
            "stage": stage,
            "model": model,
            "label": label,
            "usage": usage,
            "cost_usd": round(cost, 6),
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def ledger(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        entries = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries


def list_openings(root: Path | None = None) -> list[str]:
    base = (root or repo_root()) / "openings"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "opening.yaml").exists())


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
