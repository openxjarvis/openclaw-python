"""Dreaming config models.

Mirrors TypeScript memory-host-sdk/dreaming.ts DreamingConfig.

The dreaming system runs periodic background memory consolidation
in three phases: light (daily digest), deep (long-term synthesis),
and REM (pattern recognition).
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

DreamSpeed = Literal["fast", "balanced", "slow"]
DreamThinking = Literal["low", "medium", "high"]
DreamBudget = Literal["cheap", "medium", "expensive"]
DreamSource = Literal["daily", "sessions", "recall", "memory", "logs", "deep"]


class DreamExecutionConfig(BaseModel):
    """Execution overrides for a dreaming phase or global defaults."""

    speed: DreamSpeed = "balanced"
    thinking: DreamThinking = "medium"
    budget: DreamBudget = "medium"
    model: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    timeout_ms: int | None = None


class DreamStorageConfig(BaseModel):
    """Storage configuration for dream outputs."""

    mode: Literal["inline", "separate", "both"] = "inline"
    separate_reports: bool = False


class LightPhaseConfig(BaseModel):
    """Light dreaming phase — daily digest from recent activity."""

    enabled: bool = True
    cron: str = "0 2 * * *"        # 2 AM daily
    lookback_days: int = 1
    limit: int = 50
    dedupe_similarity: float = 0.85
    sources: list[DreamSource] = Field(default_factory=lambda: ["daily", "sessions", "recall"])
    execution: DreamExecutionConfig | None = None


class DeepRecoveryConfig(BaseModel):
    """Recovery config for the deep dreaming phase."""

    enabled: bool = True
    trigger_below_health: float = 0.7
    lookback_days: int = 30
    max_recovered_candidates: int = 20
    min_recovery_confidence: float = 0.6
    auto_write_min_confidence: float = 0.8


class DeepPhaseConfig(BaseModel):
    """Deep dreaming phase — long-term memory synthesis."""

    enabled: bool = True
    cron: str = "0 3 * * 0"        # 3 AM on Sundays
    limit: int = 30
    min_score: float = 0.6
    min_recall_count: int = 2
    min_unique_queries: int = 2
    recency_half_life_days: float = 14.0
    max_age_days: int | None = None
    sources: list[DreamSource] = Field(
        default_factory=lambda: ["daily", "memory", "sessions", "logs", "recall"]
    )
    recovery: DeepRecoveryConfig = Field(default_factory=DeepRecoveryConfig)
    execution: DreamExecutionConfig | None = None


class RemPhaseConfig(BaseModel):
    """REM dreaming phase — pattern and relationship discovery."""

    enabled: bool = True
    cron: str = "0 4 * * 1"        # 4 AM on Mondays
    lookback_days: int = 14
    limit: int = 20
    min_pattern_strength: float = 0.5
    sources: list[DreamSource] = Field(default_factory=lambda: ["memory", "daily", "deep"])
    execution: DreamExecutionConfig | None = None


class DreamingPhasesConfig(BaseModel):
    """All three dreaming phase configurations."""

    light: LightPhaseConfig = Field(default_factory=LightPhaseConfig)
    deep: DeepPhaseConfig = Field(default_factory=DeepPhaseConfig)
    rem: RemPhaseConfig = Field(default_factory=RemPhaseConfig)


class DreamingConfig(BaseModel):
    """Full dreaming configuration.

    Mirrors TS DreamingConfig from memory-host-sdk/dreaming.ts.
    """

    enabled: bool = False
    frequency: str = "0 3 * * *"   # default cron (overridden by phase crrons)
    timezone: str | None = None
    verbose_logging: bool = False
    plugin: str | None = None       # memory plugin id to use
    storage: DreamStorageConfig = Field(default_factory=DreamStorageConfig)
    execution: DreamExecutionConfig = Field(default_factory=DreamExecutionConfig)
    phases: DreamingPhasesConfig = Field(default_factory=DreamingPhasesConfig)


def resolve_dreaming_config(config: Any) -> DreamingConfig:
    """Extract and validate dreaming config from the main gateway config.

    Returns a default (disabled) config if not configured.
    """
    try:
        dreaming_raw = None
        if hasattr(config, "dreaming"):
            dreaming_raw = config.dreaming
        elif isinstance(config, dict):
            dreaming_raw = config.get("dreaming")

        if dreaming_raw is None:
            return DreamingConfig()

        if isinstance(dreaming_raw, DreamingConfig):
            return dreaming_raw

        if hasattr(dreaming_raw, "model_dump"):
            return DreamingConfig(**dreaming_raw.model_dump())

        if isinstance(dreaming_raw, dict):
            return DreamingConfig(**dreaming_raw)

    except Exception:
        pass
    return DreamingConfig()
