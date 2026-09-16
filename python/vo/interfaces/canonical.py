"""
CanonicalRecord / AppendOnlyLog -- Phase 8's canonical object contract.

architecture/vo-phase-plan.md's Phase 8 row asks for "Month 1 ontology,
canonical object contracts, bitemporal fields, version stamps, append-only
object log" and brings gates G4 and G5 live. This module is the generic,
market-agnostic contract every later canonical object -- a swing (Phase 11),
a RegimeState/RegimeTransition (Phase 13), a liquidity reference (Phase 21),
a delivery inefficiency (Phase 27), a protraction event (Phase 28) -- will
be built on top of. It deliberately defines no Month 1-specific shape
itself: vo.month01.ontology (this phase's other half) registers *which*
concepts exist and where they come from, but the dataclass/enum shape for
each one belongs to the phase that already owns it as its stated deliverable
(RegimeState is Phase 13's, not this one's -- see vo-phase-plan.md's own
phase table). Defining those here would be exactly the kind of
build-order jump the project's "no drift" discipline exists to prevent.

BITEMPORAL, deliberately only two axes, named after what they answer:

  observed_at  -- valid time: when this was true IN THE MARKET.
  recorded_at  -- transaction time: when VO's own system ASSERTED this record.

recorded_at is never before observed_at -- you cannot record something
before it happened, whether the classification arrives on the next tick
(live) or years later (replay/backtest, where recorded_at is the replay
wall-clock, not the historical instant). This mirrors TimeContext's own
"says when, never what it means" discipline (vo.time.context) one level up:
CanonicalRecord says WHEN something was true and WHEN VO found out, never
what became of that fact afterward -- that is what supersession is for.

VERSION STAMPS: `methodology_version` is required, always an int, and lives
on a frozen dataclass -- so once a record is constructed, its version can
never silently drift. G4's fuller promise ("changing it bumps the version
and re-runs dependents") is a build-pipeline concern for whichever phase
first has real dependents to re-run; what this contract mechanically
guarantees today is the narrower, load-bearing half: a version, once
stamped, cannot be repurposed after the fact.

APPEND-ONLY: G5 says Canonical Memory has no UPDATE -- corrections append.
AppendOnlyLog enforces this the same way the MT5-import gate (G7) enforces
its rule: by not existing, not by convention. There is no update() and no
delete() method anywhere on the class. A correction is a brand-new record
whose `supersedes` names the object_id it revises; both stay in the log
permanently. Phase 26 ("Canonical Memory store -- queryable, bitemporal,
append-only") is where this becomes a real, indexed, multi-object store;
this class stays intentionally minimal so Phase 8 does not quietly build
Phase 26 early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class CanonicalRecordError(ValueError):
    """Raised when a CanonicalRecord is constructed with an invalid shape."""


@dataclass(frozen=True)
class CanonicalRecord:
    """
    One immutable, dated, versioned assertion about the market or about
    VO's own observation of it. Never mutated after construction -- a
    revision is a new CanonicalRecord whose `supersedes` names this one.
    """

    object_type: str
    """A stable category name, e.g. "REGIME", "SWING", "LIQUIDITY_REFERENCE"
    -- the same vocabulary G8 will later require on chart objects, so a
    chart annotation and its canonical record can always be cross-checked."""
    object_id: str
    """This record's own stable identity, e.g. "REGIME-000184". Two records
    correcting the same underlying fact get two different object_ids,
    linked by `supersedes` -- identity is per-record, not per-subject."""
    observed_at: datetime
    """Valid time: when this was true in the market."""
    recorded_at: datetime
    """Transaction time: when VO's system asserted this record."""
    methodology_version: int
    """Which version of the producing methodology made this assertion.
    Required -- there is no such thing as an unversioned canonical record."""
    supersedes: str | None = None
    """The object_id of the record this one corrects, if any. None for an
    original observation."""

    def __post_init__(self) -> None:
        if not self.object_type.strip():
            raise CanonicalRecordError("object_type cannot be blank")

        if not self.object_id.strip():
            raise CanonicalRecordError("object_id cannot be blank")

        if self.methodology_version < 0:
            raise CanonicalRecordError(
                f"methodology_version must be >= 0, got {self.methodology_version}"
            )

        if self.recorded_at < self.observed_at:
            raise CanonicalRecordError(
                "recorded_at cannot be before observed_at "
                f"({self.recorded_at!r} < {self.observed_at!r}) -- "
                "a record cannot be asserted before what it describes happened"
            )

        if self.supersedes is not None and self.supersedes == self.object_id:
            raise CanonicalRecordError("a record cannot supersede itself")


class AppendOnlyLog[T: CanonicalRecord]:
    """
    Records go in; nothing is ever mutated or removed. See the module
    docstring's G5 discussion -- the missing update()/delete() methods are
    the enforcement mechanism, not a documented convention to honor.
    """

    def __init__(self) -> None:
        self._records: list[T] = []

    def append(self, record: T) -> None:
        self._records.append(record)

    def all(self) -> tuple[T, ...]:
        return tuple(self._records)

    def get(self, object_id: str) -> T | None:
        """The record with this exact object_id, or None. Does not follow
        the supersession chain -- use `is_current`/`superseded_by` for that."""
        for record in self._records:
            if record.object_id == object_id:
                return record
        return None

    def superseded_by(self, object_id: str) -> T | None:
        """The record (if any) whose `supersedes` names `object_id` -- i.e.
        what corrected it. None if nothing in the log has superseded it."""
        for record in self._records:
            if record.supersedes == object_id:
                return record
        return None

    def is_current(self, object_id: str) -> bool:
        """True if `object_id` exists in the log and nothing has superseded
        it yet. False for an unknown id, exactly like for a superseded one --
        both mean "this is not something to build on now"."""
        return self.get(object_id) is not None and self.superseded_by(object_id) is None

    def __len__(self) -> int:
        return len(self._records)
