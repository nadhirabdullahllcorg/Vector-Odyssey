"""
ExecutionConfig -- versioned Phase 16 parameters (config/settings/
execution.yaml), never hardcoded. Mirrors risk_config.py/swing_config.py's
own reasoning: the magic number, order comment, and price-deviation
tolerance are operational choices expected to be revisited, not settled
constants, so they live in a versioned YAML file rather than in code.

magic_number/comment_prefix exist specifically so vo.execution.
reconciliation can tell "did VO open this position" apart from a manual
or pre-existing one on restart -- the exact gap architecture/
vo-phase-plan.md's Phase 16 gate ("unexpected pre-existing position
detected, not ignored") names, and one no earlier phase's docs ever
defined a scheme for (see architecture/vo-phase-plan.md SS16-notes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ExecutionConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionConfig:
    version: int
    magic_number: int
    comment_prefix: str
    deviation_points: int

    def __post_init__(self) -> None:
        if self.magic_number <= 0:
            raise ExecutionConfigError(f"magic_number must be positive, got {self.magic_number}")
        if not self.comment_prefix.strip():
            raise ExecutionConfigError("comment_prefix cannot be blank")
        if len(self.comment_prefix) > 26:
            # MT5 order comments are capped at 31 chars; leave room for a
            # short suffix (e.g. a strategy_id fragment) rather than
            # eating the whole budget on the prefix alone.
            raise ExecutionConfigError(
                f"comment_prefix is {len(self.comment_prefix)} chars; MT5 comments are "
                "capped near 31 chars, so keep the configured prefix well under that"
            )
        if self.deviation_points < 0:
            raise ExecutionConfigError(
                f"deviation_points cannot be negative, got {self.deviation_points}"
            )

    def comment_for(self, strategy_id: str) -> str:
        """The exact comment string stamped on a VO-placed order --
        `{comment_prefix}:{strategy_id}`, truncated to MT5's ~31-char
        comment limit rather than silently sent oversized and rejected or
        truncated unpredictably by the terminal."""
        raw = f"{self.comment_prefix}:{strategy_id}"
        return raw[:31]


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def load_execution_config(path: str | Path) -> ExecutionConfig:
    """Load and validate config/settings/execution.yaml. Raises
    ExecutionConfigError for anything malformed rather than silently
    substituting a default -- the same discipline as load_risk_config."""
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="execution config")

    required = ("version", "magic_number", "comment_prefix", "deviation_points")
    for key in required:
        if key not in top:
            raise ExecutionConfigError(f"execution config requires '{key}'")

    return ExecutionConfig(
        version=int(top["version"]),
        magic_number=int(top["magic_number"]),
        comment_prefix=str(top["comment_prefix"]),
        deviation_points=int(top["deviation_points"]),
    )
