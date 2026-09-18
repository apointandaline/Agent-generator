"""Model selection, pricing and per-stage generation defaults."""

from __future__ import annotations

from dataclasses import dataclass, field

# Default model for every stage. Override per stage in an opening's
# ``models:`` block, or globally with HIRE_MODEL.
DEFAULT_MODEL = "claude-opus-5"

# USD per 1M tokens, as published for the first-party API. These are a cached
# snapshot used only for the local cost ledger — they are not authoritative and
# drift when Anthropic changes pricing. `hire cost` prints this caveat.
PRICING: dict[str, dict[str, float]] = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 1.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.1},
}

# Rough per-call token expectations, used only by --dry-run estimates.
CALL_SHAPES: dict[str, tuple[int, int]] = {
    "research": (3_000, 6_000),
    "slate": (4_000, 5_000),
    "candidate": (6_000, 4_000),
    "round1": (5_000, 3_500),
    "round2": (40_000, 20_000),
    "digest": (8_000, 1_500),
    "aggregate": (20_000, 6_000),
    "brief": (30_000, 8_000),
    "refine": (12_000, 5_000),
}

#: Stages whose model and effort can be tuned per opening.
STAGES = (
    "research", "slate", "candidate", "round1", "round2", "digest", "aggregate",
    "brief", "refine",
)

#: Axes used to diversify the round-2 descendants of each round-1 winner.
DEFAULT_VARIANT_AXES = [
    "deeper_specialist: narrow the scope and go far deeper on the hardest "
    "technical core of the role",
    "systems_owner: widen ownership to the surrounding pipeline, interfaces and "
    "operational health",
    "adversarial_skeptic: lead with falsification — hunt for the ways the work "
    "silently produces wrong answers",
    "tooling_builder: bias toward building durable internal tooling and "
    "automation rather than one-off analyses",
    "business_translator: keep the work anchored to P&L, risk limits and "
    "decisions the CEO actually has to make",
]


@dataclass
class StageConfig:
    """Model settings for a single pipeline stage."""

    model: str = DEFAULT_MODEL
    effort: str = "high"
    max_tokens: int = 16_000

    def to_dict(self) -> dict:
        return {"model": self.model, "effort": self.effort, "max_tokens": self.max_tokens}


@dataclass
class FunnelConfig:
    """Candidate counts for each round of the funnel."""

    round1_candidates: int = 50
    round1_advance: int = 5
    variants_per_winner: int = 5
    round2_advance: int = 5

    @property
    def round2_candidates(self) -> int:
        return self.round1_advance * self.variants_per_winner

    def to_dict(self) -> dict:
        return {
            "round1_candidates": self.round1_candidates,
            "round1_advance": self.round1_advance,
            "variants_per_winner": self.variants_per_winner,
            "round2_advance": self.round2_advance,
        }


@dataclass
class Settings:
    """Resolved settings for one opening."""

    stages: dict[str, StageConfig] = field(default_factory=dict)
    funnel: FunnelConfig = field(default_factory=FunnelConfig)
    variant_axes: list[str] = field(default_factory=lambda: list(DEFAULT_VARIANT_AXES))

    def stage(self, name: str) -> StageConfig:
        return self.stages.get(name, StageConfig())


def default_stage_models() -> dict[str, StageConfig]:
    """Every stage on the default model; round 2 gets more room to work."""
    stages = {name: StageConfig() for name in STAGES}
    stages["round2"] = StageConfig(max_tokens=32_000, effort="xhigh")
    stages["brief"] = StageConfig(max_tokens=24_000)
    stages["research"] = StageConfig(max_tokens=24_000)
    return stages


def price(model: str, usage: dict) -> float:
    """Cost in USD for one call's usage, 0.0 for models we have no price for."""
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    return (
        usage.get("input_tokens", 0) * rates["input"]
        + usage.get("output_tokens", 0) * rates["output"]
        + usage.get("cache_creation_input_tokens", 0) * rates["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * rates["cache_read"]
    ) / 1_000_000
