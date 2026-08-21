#!/usr/bin/env python3
"""Hockey lines solver using OR-Tools CP-SAT.

Usage:
  - Activate your venv: `source ./venv/bin/activate`
  - Install deps: `pip install -r requirements.txt`
  - Run: `python solver.py --roster roster_sample.csv --forwards 3 --defense 3`

CSV format (header): id,name,available,experience,preferred_positions,secondary_positions
    - `available` is 1 or 0
    - `experience` integer 1..5
    - `preferred_positions` is semicolon-separated, e.g. "LW;C"
    - `secondary_positions` is semicolon-separated positions a player will play if needed

Optional columns (omit entirely for backwards compatibility with older rosters):
    - `unwilling_positions`: semicolon-separated positions this player may never be
      assigned to (hard constraint).
    - `optional_position_override`: a single position; if set, it's the *only*
      position this player may be assigned to, overriding preferred/secondary/
      unwilling. For quick what-if tweaks without editing a player's preferences.
    - `optional_player_link`: another player's id; forces both players onto the
      same forward line or defense pair (or both benched together). Works for
      forward lines and defense pairs alike.

This script builds a CP-SAT model that:
  - assigns players to forward and defense slots
  - enforces availability and unique assignment
  - favors preferred positions and balances experience across forward lines

`solve_lines()` is the reusable core (no I/O) and also backs the FastAPI
service in api.py; `read_roster()`/`players_from_rows()` are shared CSV
parsing helpers used by both the CLI and the API's file-upload endpoint.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from ortools.sat.python import cp_model
except Exception as e:
    print("Error importing OR-Tools:", e)
    print("Please install with: pip install ortools")
    sys.exit(1)

from schemas import (
    DefensePair,
    ForwardLine,
    PlayerIn,
    SlotAssignment,
    SolveResponse,
    SolveSummary,
)


@dataclass
class Player:
    id: str
    name: str
    available: int
    experience: int
    prefs: List[str]
    secondary: List[str]
    unwilling: List[str] = field(default_factory=list)
    position_override: Optional[str] = None
    link: Optional[str] = None


def players_from_rows(rows: Iterable[dict]) -> List[Player]:
    players: List[Player] = []
    for r in rows:
        pid = (r.get("id") or r.get("name") or "").strip()
        # skip empty rows
        if not pid:
            continue
        name = r.get("name") or pid
        avail = int(r.get("available", "1"))
        exp = int(r.get("experience", "1"))
        prefs_raw = r.get("preferred_positions", "")
        prefs = [p.strip().upper() for p in prefs_raw.replace("|", ";").split(";") if p.strip()]
        sec_raw = r.get("secondary_positions", "")
        secondary = [p.strip().upper() for p in sec_raw.replace("|", ";").split(";") if p.strip()]
        unwilling_raw = r.get("unwilling_positions", "") or ""
        unwilling = [p.strip().upper() for p in unwilling_raw.replace("|", ";").split(";") if p.strip()]
        override_raw = (r.get("optional_position_override", "") or "").strip().upper()
        position_override = override_raw or None
        link_raw = (r.get("optional_player_link", "") or "").strip()
        link = link_raw or None
        players.append(Player(pid, name, avail, exp, prefs, secondary, unwilling, position_override, link))
    return players


def players_from_player_in(players_in: Iterable[PlayerIn]) -> List[Player]:
    return [
        Player(
            id=p.id,
            name=p.name,
            available=p.available,
            experience=p.experience,
            prefs=[pos.upper() for pos in p.preferred_positions],
            secondary=[pos.upper() for pos in p.secondary_positions],
            unwilling=[pos.upper() for pos in p.unwilling_positions],
            position_override=p.optional_position_override.upper() if p.optional_position_override else None,
            link=p.optional_player_link or None,
        )
        for p in players_in
    ]


def read_roster(path: str) -> List[Player]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return players_from_rows(reader)


def build_slots(num_forwards_used: int, num_def_pairs_used: int, last_pair_partial: bool) -> Tuple[List[Tuple[str, str]], List[str]]:
    # returns (list of (slot_name, pos)), and list of forward slot names
    slots: List[Tuple[str, str]] = []
    forward_slot_names: List[str] = []
    for l in range(1, num_forwards_used + 1):
        for pos in ["LW", "C", "RW"]:
            s = f"F{l}_{pos}"
            slots.append((s, pos))
            forward_slot_names.append(s)
    # defense pairs: create full pairs up to num_def_pairs_used, but if last pair is partial, only add LD
    for d in range(1, num_def_pairs_used + 1):
        s_ld = f"D{d}_LD"
        slots.append((s_ld, "LD"))
        if not (last_pair_partial and d == num_def_pairs_used):
            s_rd = f"D{d}_RD"
            slots.append((s_rd, "RD"))
    return slots, forward_slot_names


def solve_lines(players: List[Player], num_forwards_requested: int, num_defense_requested: int, time_limit: int) -> SolveResponse:
    available_count = sum(1 for p in players if p.available == 1)

    # Prioritize full forward lines: use as many full forward lines (3 players) as possible
    num_forwards_used = min(num_forwards_requested, available_count // 3)
    remaining_after_forwards = available_count - (3 * num_forwards_used)

    # Allocate remaining players to defense slots (defense may be uneven)
    max_def_slots_requested = num_defense_requested * 2
    def_slots_to_allocate = min(max_def_slots_requested, remaining_after_forwards)
    # number of pairs to create (last pair may be partial)
    num_def_pairs_used = (def_slots_to_allocate + 1) // 2
    last_pair_partial = (def_slots_to_allocate % 2 == 1)

    slots, forward_slots = build_slots(num_forwards_used, num_def_pairs_used, last_pair_partial)

    summary_base = dict(
        available_players=available_count,
        forwards_requested=num_forwards_requested,
        forwards_used=num_forwards_used,
        defense_requested=num_defense_requested,
        defense_pairs_used=num_def_pairs_used,
        defense_last_partial=last_pair_partial,
    )

    model = cp_model.CpModel()

    # variables x[p_id, slot] -> 0/1
    x: Dict[Tuple[str, str], cp_model.IntVar] = {}
    for p in players:
        for s_name, s_pos in slots:
            x[(p.id, s_name)] = model.NewBoolVar(f"x_{p.id}_{s_name}")

    # availability: unavailable players cannot be assigned
    for p in players:
        if p.available == 0:
            for s_name, _ in slots:
                model.Add(x[(p.id, s_name)] == 0)

    # each slot must be filled by exactly one player
    for s_name, _ in slots:
        model.Add(sum(x[(p.id, s_name)] for p in players) == 1)

    # each player at most one slot
    for p in players:
        model.Add(sum(x[(p.id, s_name)] for s_name, _ in slots) <= 1)

    # optional_position_override: restrict this player to exactly one position,
    # overriding preferred/secondary/unwilling entirely, and force them into
    # that slot rather than leaving them benchable - available beats override,
    # so a benched/unavailable player stays out even with one set, but an
    # available overridden player must be seated, surfacing infeasibility
    # honestly (e.g. more overrides at a position than slots for it) rather
    # than silently dropping the override to make the roster fit.
    # unwilling_positions: hard-forbid these positions (skipped if overridden).
    for p in players:
        if p.position_override:
            for s_name, s_pos in slots:
                if s_pos != p.position_override:
                    model.Add(x[(p.id, s_name)] == 0)
            if p.available:
                model.Add(sum(x[(p.id, s_name)] for s_name, _ in slots) == 1)
        elif p.unwilling:
            for s_name, s_pos in slots:
                if s_pos in p.unwilling:
                    model.Add(x[(p.id, s_name)] == 0)

    # optional_player_link: force linked players onto the same forward line or
    # defense pair together (or both benched together). Units are derived from
    # the slot-name prefix ("F1", "D2", ...), which covers forwards and defense.
    units: Dict[str, List[str]] = defaultdict(list)
    for s_name, _ in slots:
        units[s_name.split("_")[0]].append(s_name)

    presence: Dict[Tuple[str, str], cp_model.IntVar] = {}
    players_by_id = {p.id: p for p in players}
    linked_ids = {p.id for p in players if p.link and p.link in players_by_id}
    linked_ids |= {p.link for p in players if p.link and p.link in players_by_id}
    for p in players:
        if p.id not in linked_ids:
            continue
        for unit, unit_slots in units.items():
            v = model.NewBoolVar(f"pres_{p.id}_{unit}")
            model.Add(v == sum(x[(p.id, s_name)] for s_name in unit_slots))
            presence[(p.id, unit)] = v

    for p in players:
        if p.link and p.link in players_by_id:
            for unit in units:
                model.Add(presence[(p.id, unit)] == presence[(p.link, unit)])

    # preference score (soft) with primary and secondary positions
    # primary weight = 2, secondary weight = 1, OOP weight = 0
    pref_terms = []
    for p in players:
        for s_name, s_pos in slots:
            s_pos_up = s_pos.upper()
            if s_pos_up in p.prefs:
                weight = 2
            elif s_pos_up in p.secondary:
                weight = 1
            else:
                weight = 0
            # allow all positions (OOP allowed) but OOP has zero weight
            pref_terms.append(weight * x[(p.id, s_name)])

    assigned = []
    for p in players:
        assigned.append(sum(x[(p.id, s_name)] for s_name, _ in slots))

    # Experience balancing across forward lines (L1 deviation)
    # Experience values now range 1..5. Create per-line experience sums and total.
    per_line_exp_vars = []
    total_exp_forwards_terms = []
    slots_by_line = defaultdict(list)
    for s_name, s_pos in slots:
        if s_name in forward_slots:
            # extract line number
            line = s_name.split("_")[0]  # e.g., F1
            slots_by_line[line].append((s_name, s_pos))

    for line, line_slots in slots_by_line.items():
        # exp sum var for this line (max 5 per player)
        max_exp_line = 5 * len(line_slots)
        v = model.NewIntVar(0, max_exp_line, f"exp_{line}")
        expr = sum(p.experience * x[(p.id, s_name)] for p in players for s_name, _ in line_slots)
        model.Add(v == expr)
        per_line_exp_vars.append(v)
        total_exp_forwards_terms.append(v)
    total_exp_forwards = model.NewIntVar(0, 5 * len(forward_slots), "total_exp_forwards")
    model.Add(total_exp_forwards == sum(total_exp_forwards_terms))

    # dev variables: devF_l >= |F * exp_line_l - total_exp_forwards|
    dev_vars = []
    F = num_forwards_used
    if F > 0:
        # safe bound for dev is F * max_exp_line
        max_dev = F * 5 * 3  # 5 exp * 3 slots * F
        for v in per_line_exp_vars:
            dev = model.NewIntVar(0, max_dev, f"dev_{v.Name()}")
            model.Add(dev >= F * v - total_exp_forwards)
            model.Add(dev >= total_exp_forwards - F * v)
            dev_vars.append(dev)

    # Objective weights (large numbers to emulate lexicographic priorities)
    # Objective weights: ensure lexicographic-like priorities W1 >> W2 >> W3
    W1 = 1_000_000  # maximize number of assigned players
    W2 = 10_000     # maximize position preferences (prioritized over balance)
    W3 = 100        # minimize experience imbalance (smaller priority)

    objective_terms = []
    # assigned players (sum of assigned list)
    objective_terms.append(W1 * sum(assigned))
    # preference satisfaction
    objective_terms.append(W2 * sum(pref_terms))
    # penalty for imbalance
    if dev_vars:
        objective_terms.append(-W3 * sum(dev_vars))

    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8

    result = solver.Solve(model)

    if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResponse(
            status="NO_SOLUTION",
            summary=SolveSummary(**summary_base, total_assigned=0, total_primary=0, total_secondary=0, total_oop=0, total_unwilling=0),
            forward_lines=[],
            defense_pairs=[],
        )

    status_name = "OPTIMAL" if result == cp_model.OPTIMAL else "FEASIBLE"

    def classify(pos: str, p: Player) -> str:
        # Checked first: unwilling is a hard constraint under normal
        # circumstances, so a position landing here at all only happens via
        # optional_position_override forcing it - that's worth flagging even
        # if the position also happens to be in prefs/secondary.
        if pos in p.unwilling:
            return "unwilling"
        elif pos in p.prefs:
            return "primary"
        elif pos in p.secondary:
            return "secondary"
        return "oop"

    def build_assignment(s_name: str) -> SlotAssignment | None:
        pos = s_name.split("_")[1]
        for p in players:
            if solver.Value(x[(p.id, s_name)]) == 1:
                status = classify(pos, p)
                return SlotAssignment(
                    slot=s_name, position=pos, player_id=p.id, player_name=p.name,
                    experience=p.experience, status=status,
                )
        return None

    forward_lines: List[ForwardLine] = []
    for l in range(1, num_forwards_used + 1):
        line_slot_names = [f"F{l}_LW", f"F{l}_C", f"F{l}_RW"]
        slot_assignments = [a for a in (build_assignment(s) for s in line_slot_names) if a is not None]
        exp_sum = sum(a.experience for a in slot_assignments)
        primary_count = sum(1 for a in slot_assignments if a.status == "primary")
        secondary_count = sum(1 for a in slot_assignments if a.status == "secondary")
        oop_count = sum(1 for a in slot_assignments if a.status == "oop")
        unwilling_count = sum(1 for a in slot_assignments if a.status == "unwilling")
        forward_lines.append(ForwardLine(
            line_number=l, slots=slot_assignments, exp_sum=exp_sum,
            primary_count=primary_count, secondary_count=secondary_count, oop_count=oop_count,
            unwilling_count=unwilling_count,
        ))

    defense_pairs: List[DefensePair] = []
    for d in range(1, num_def_pairs_used + 1):
        partial = last_pair_partial and d == num_def_pairs_used
        pair_slot_names = [f"D{d}_LD"] if partial else [f"D{d}_LD", f"D{d}_RD"]
        slot_assignments = [a for a in (build_assignment(s) for s in pair_slot_names) if a is not None]
        primary_count = sum(1 for a in slot_assignments if a.status == "primary")
        secondary_count = sum(1 for a in slot_assignments if a.status == "secondary")
        oop_count = sum(1 for a in slot_assignments if a.status == "oop")
        unwilling_count = sum(1 for a in slot_assignments if a.status == "unwilling")
        defense_pairs.append(DefensePair(
            pair_number=d, slots=slot_assignments,
            primary_count=primary_count, secondary_count=secondary_count, oop_count=oop_count,
            unwilling_count=unwilling_count,
            partial=partial,
        ))

    total_assigned = sum(1 for p in players if any(solver.Value(x[(p.id, s_name)]) == 1 for s_name, _ in slots))
    status_counts = {"primary": 0, "secondary": 0, "oop": 0, "unwilling": 0}
    for p in players:
        for s_name, s_pos in slots:
            if solver.Value(x[(p.id, s_name)]) == 1:
                status_counts[classify(s_pos, p)] += 1
    total_primary = status_counts["primary"]
    total_secondary = status_counts["secondary"]
    total_oop = status_counts["oop"]
    total_unwilling = status_counts["unwilling"]

    return SolveResponse(
        status=status_name,
        summary=SolveSummary(
            **summary_base,
            total_assigned=total_assigned,
            total_primary=total_primary,
            total_secondary=total_secondary,
            total_unwilling=total_unwilling,
            total_oop=total_oop,
        ),
        forward_lines=forward_lines,
        defense_pairs=defense_pairs,
    )


def print_solve_result(result: SolveResponse) -> None:
    s = result.summary
    print(f"Available players: {s.available_players}")
    print(f"Requested forwards: {s.forwards_requested}, forwards used: {s.forwards_used}")
    print(f"Requested defense pairs: {s.defense_requested}, defense pairs used: {s.defense_pairs_used}, last_partial={s.defense_last_partial}")

    if result.status == "NO_SOLUTION":
        print("No solution found.")
        return

    slot_names = [a.slot for fl in result.forward_lines for a in fl.slots] + \
                 [a.slot for dp in result.defense_pairs for a in dp.slots]
    print(f"Total slots created: {len(slot_names)}")
    print(f"Slots: {slot_names}")
    print(f"Status: {result.status}")

    def fmt(a: SlotAssignment) -> str:
        tag = ""
        if a.status == "unwilling":
            tag = "(UNWILLING!)"
        elif a.status == "secondary":
            tag = "(secondary)"
        elif a.status == "oop":
            tag = "(OOP)"
        return f"{a.player_name}({a.slot}){tag}"

    print(f"\nForwards: requested={s.forwards_requested} used={s.forwards_used}")
    for fl in result.forward_lines:
        print(f"Line {fl.line_number}: {', '.join(fmt(a) for a in fl.slots)} | exp_sum={fl.exp_sum} | primary={fl.primary_count} secondary={fl.secondary_count} oop={fl.oop_count} unwilling={fl.unwilling_count}")

    print(f"\nDefense: requested_pairs={s.defense_requested} pairs_used={s.defense_pairs_used} (last_partial={s.defense_last_partial})")
    for dp in result.defense_pairs:
        print(f"Pair {dp.pair_number}: {', '.join(fmt(a) for a in dp.slots)} | primary={dp.primary_count} secondary={dp.secondary_count} oop={dp.oop_count} unwilling={dp.unwilling_count}")

    print(f"\nTotal assigned: {s.total_assigned}")
    print(f"Primary-position assignments: {s.total_primary}")
    print(f"Secondary-position assignments: {s.total_secondary}")
    print(f"Out-of-position (OOP) assignments: {s.total_oop}")
    print(f"Unwilling-position assignments: {s.total_unwilling}")


def main():
    ap = argparse.ArgumentParser(description="Hockey lines solver")
    ap.add_argument("--roster", required=True, help="Roster CSV path")
    ap.add_argument("--forwards", type=int, default=3, help="Requested number of forward lines")
    ap.add_argument("--defense", type=int, default=3, help="Requested number of defensive pairs")
    ap.add_argument("--time-limit", type=int, default=20, help="Solver time limit in seconds")
    args = ap.parse_args()

    players = read_roster(args.roster)
    result = solve_lines(players, args.forwards, args.defense, args.time_limit)
    print_solve_result(result)


if __name__ == "__main__":
    main()
