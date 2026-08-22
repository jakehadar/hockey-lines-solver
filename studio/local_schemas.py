"""Pydantic models studio/app.py validates locally.

Named local_schemas.py, not schemas.py, on purpose: both solver/ and this
directory are flat (not packages) and both sit on sys.path during tests (see
conftest.py), so a same-named schemas.py in each would make a bare `import
schemas` ambiguous - whichever came first on sys.path would silently shadow
the other for everything, including solver/solver.py's own `from schemas
import ...`.

Deliberately NOT a copy of solver/schemas.py, beyond the name: studio never
inspects a solve result's internals - scenarios store `result`/`dof` as
opaque JSON blobs, and /solve and /degrees-of-freedom requests are forwarded
to the solver service as-is (it's the sole authority on that shape; see
studio/app.py's _call_solver). Only the two models studio actually builds
and validates itself - a roster player, and a scenario snapshot wrapping one
- live here.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ObjectiveSetting(BaseModel):
    key: Literal["assigned", "preference", "balance"] = Field(...)
    enabled: bool = Field(True)


# Mirrors solver/schemas.py's DEFAULT_OBJECTIVES - the historical fixed order
# (assigned >> preference >> balance), used as studio's fallback when a
# scenario predates the objectives feature.
DEFAULT_OBJECTIVES: List[ObjectiveSetting] = [
    ObjectiveSetting(key="assigned", enabled=True),
    ObjectiveSetting(key="preference", enabled=True),
    ObjectiveSetting(key="balance", enabled=True),
]

_OBJECTIVE_KEYS = {"assigned", "preference", "balance"}


def _validate_objectives(value: List[ObjectiveSetting]) -> List[ObjectiveSetting]:
    keys = [o.key for o in value]
    if set(keys) != _OBJECTIVE_KEYS or len(keys) != len(_OBJECTIVE_KEYS):
        raise ValueError(f"objectives must contain exactly one entry for each of {sorted(_OBJECTIVE_KEYS)}.")
    if not any(o.enabled for o in value):
        raise ValueError("at least one objective must remain enabled.")
    return value


class PlayerIn(BaseModel):
    id: str = Field(..., description="Unique player identifier.")
    name: str = Field(..., description="Player display name.")
    available: int = Field(1, description="1 if the player is available for this game, 0 otherwise.")
    experience: int = Field(..., ge=1, le=5, description="Experience level, 1 (beginner) to 5 (advanced).")
    preferred_positions: List[str] = Field(default_factory=list)
    secondary_positions: List[str] = Field(default_factory=list)
    unwilling_positions: List[str] = Field(default_factory=list)
    optional_position_override: Optional[str] = Field(None)
    optional_player_link: Optional[str] = Field(None)


class ScenarioUpdate(BaseModel):
    """Overwriting an already-loaded scenario in place: same shape as a
    fresh scenario save, minus title/description/parent."""

    players: List[PlayerIn] = Field(..., description="Full roster for this scenario.")
    forwards: int = Field(3, ge=0)
    defense: int = Field(3, ge=0)
    time_limit: int = Field(20, ge=1)
    allow_oop: bool = Field(True)
    allow_unwilling: bool = Field(False)
    objectives: List[ObjectiveSetting] = Field(default_factory=lambda: list(DEFAULT_OBJECTIVES))
    result: dict[str, Any] = Field(..., description="Opaque SolveResponse blob, as returned by the solver service - stored, never re-validated.")
    dof: Optional[dict[str, Any]] = Field(None, description="Opaque DofSummary blob, if a degrees-of-freedom analysis had finished computing client-side before this save.")

    _validate_objectives = field_validator("objectives")(_validate_objectives)


class ScenarioSave(ScenarioUpdate):
    """Studio's Save/Branch/Save as scenario: a named, described snapshot
    alongside its already-computed result."""

    title: str = Field(..., min_length=1, description="Scenario name.")
    description: str = Field("", description="Optional freeform notes.")
    parent_scenario_id: Optional[int] = Field(None, description="Scenario this one was branched from, if any.")
