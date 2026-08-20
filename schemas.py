"""Pydantic models shared by solver.py (return type) and api.py (request/response validation)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlayerIn(BaseModel):
    id: str = Field(..., description="Unique player identifier.")
    name: str = Field(..., description="Player display name.")
    available: int = Field(1, description="1 if the player is available for this game, 0 otherwise.")
    experience: int = Field(..., ge=1, le=5, description="Experience level, 1 (beginner) to 5 (advanced).")
    preferred_positions: List[str] = Field(
        default_factory=list,
        description="Primary positions this player prefers, e.g. ['LW', 'C']. Valid positions: LW, C, RW, LD, RD.",
    )
    secondary_positions: List[str] = Field(
        default_factory=list,
        description="Positions this player will play if needed, at lower priority than preferred_positions.",
    )
    unwilling_positions: List[str] = Field(
        default_factory=list,
        description="Positions this player must never be assigned to (hard constraint).",
    )
    optional_position_override: Optional[str] = Field(
        None,
        description="If set, the only position this player may be assigned to, overriding preferred/secondary/unwilling. For quick what-if tweaks.",
    )
    optional_player_link: Optional[str] = Field(
        None,
        description="Player id that must be assigned to the same forward line or defense pair as this player (or both benched together). For quick what-if tweaks.",
    )


class SolveRequest(BaseModel):
    players: List[PlayerIn] = Field(..., description="Full roster for this game.")
    forwards: int = Field(3, ge=0, description="Requested number of forward lines.")
    defense: int = Field(3, ge=0, description="Requested number of defensive pairs.")
    time_limit: int = Field(20, ge=1, description="Solver time limit in seconds.")


class SlotAssignment(BaseModel):
    slot: str = Field(..., description="Slot identifier, e.g. 'F1_LW' or 'D1_LD'.")
    position: str = Field(..., description="Position for this slot, e.g. 'LW', 'C', 'RW', 'LD', 'RD'.")
    player_id: str = Field(..., description="ID of the player assigned to this slot.")
    player_name: str = Field(..., description="Name of the player assigned to this slot.")
    experience: int = Field(..., description="Experience level of the assigned player.")
    status: Literal["primary", "secondary", "oop"] = Field(
        ..., description="Whether this slot's position is the player's primary, secondary, or out-of-position (oop) assignment."
    )


class ForwardLine(BaseModel):
    line_number: int = Field(..., description="1-indexed forward line number.")
    slots: List[SlotAssignment] = Field(..., description="Assignments for this line (LW, C, RW).")
    exp_sum: int = Field(..., description="Sum of experience across this line's players.")
    primary_count: int = Field(..., description="Number of players assigned to their primary position on this line.")
    secondary_count: int = Field(..., description="Number of players assigned to their secondary position on this line.")
    oop_count: int = Field(..., description="Number of players assigned out-of-position on this line.")


class DefensePair(BaseModel):
    pair_number: int = Field(..., description="1-indexed defense pair number.")
    slots: List[SlotAssignment] = Field(..., description="Assignments for this pair (LD, and RD unless partial).")
    primary_count: int = Field(..., description="Number of players assigned to their primary position on this pair.")
    secondary_count: int = Field(..., description="Number of players assigned to their secondary position on this pair.")
    oop_count: int = Field(..., description="Number of players assigned out-of-position on this pair.")
    partial: bool = Field(..., description="True if this pair only has an LD (no RD partner) because players ran out.")


class SolveSummary(BaseModel):
    available_players: int = Field(..., description="Number of players with available=1 in the roster.")
    forwards_requested: int = Field(..., description="Forward lines requested by the caller.")
    forwards_used: int = Field(..., description="Forward lines actually built, given available players.")
    defense_requested: int = Field(..., description="Defensive pairs requested by the caller.")
    defense_pairs_used: int = Field(..., description="Defensive pairs actually built, given available players.")
    defense_last_partial: bool = Field(..., description="True if the last defensive pair only has an LD slot.")
    total_assigned: int = Field(..., description="Total number of players assigned to a slot.")
    total_primary: int = Field(..., description="Total assignments to a player's primary position.")
    total_secondary: int = Field(..., description="Total assignments to a player's secondary position.")
    total_oop: int = Field(..., description="Total out-of-position assignments.")


class SolveResponse(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "NO_SOLUTION"] = Field(
        ..., description="Solver outcome. NO_SOLUTION means the CP-SAT model could not be solved for this input."
    )
    summary: SolveSummary = Field(..., description="Aggregate stats for this solve.")
    forward_lines: List[ForwardLine] = Field(..., description="Forward line assignments, in line order.")
    defense_pairs: List[DefensePair] = Field(..., description="Defense pair assignments, in pair order.")


class RosterSave(SolveRequest):
    """Studio's Save (to roster): the client already solved this exact
    players+settings snapshot, so it sends the result along rather than
    having the server re-solve just to cache it."""

    result: SolveResponse = Field(..., description="The solve result this exact snapshot produced.")


class ScenarioSave(RosterSave):
    """Studio's Save as scenario: a named, described snapshot alongside its
    cached result, independent of the roster's own saved baseline."""

    title: str = Field(..., min_length=1, description="Scenario name.")
    description: str = Field("", description="Optional freeform notes.")
