#!/usr/bin/env python3
"""Hockey lines solver using OR-Tools CP-SAT.

Usage:
  - Activate your venv: `source ./venv/bin/activate`
  - Install deps: `pip install -r requirements.txt`
  - Run: `python solver.py --roster roster_sample.csv --forwards 3 --defense 3`

CSV format (header): id,name,available,experience,preferred_positions,secondary_positions
    - `available` is 1 or 0
    - `experience` integer 1..3
    - `preferred_positions` is semicolon-separated, e.g. "LW;C"
    - `secondary_positions` is semicolon-separated positions a player will play if needed

This script builds a CP-SAT model that:
  - assigns players to forward and defense slots
  - enforces availability and unique assignment
  - favors preferred positions and balances experience across forward lines
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

try:
    from ortools.sat.python import cp_model
except Exception as e:
    print("Error importing OR-Tools:", e)
    print("Please install with: pip install ortools")
    sys.exit(1)


@dataclass
class Player:
    id: str
    name: str
    available: int
    experience: int
    prefs: List[str]
    secondary: List[str]


def read_roster(path: str) -> List[Player]:
    players: List[Player] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
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
            players.append(Player(pid, name, avail, exp, prefs, secondary))
    return players


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


def solve(roster_path: str, num_forwards_requested: int, num_defense_requested: int, time_limit: int):
    players = read_roster(roster_path)
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

    # Debug: report allocation decisions
    total_slots = len(slots)
    print(f"Available players: {available_count}")
    print(f"Requested forwards: {num_forwards_requested}, forwards used: {num_forwards_used}")
    print(f"Requested defense pairs: {num_defense_requested}, defense pairs used: {num_def_pairs_used}, last_partial={last_pair_partial}")
    print(f"Total slots created: {total_slots}")
    print(f"Slots: {[s for s,_ in slots]}")

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

    if result in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Status: {solver.StatusName(result)}")
        # helper formatter
        def fmt(a):
            s, p, status = a
            tag = ""
            if status == "secondary":
                tag = "(secondary)"
            elif status == "oop":
                tag = "(OOP)"
            return f"{p.name}({s}){tag}"

        # print forward lines (only those used)
        print(f"\nForwards: requested={num_forwards_requested} used={num_forwards_used}")
        for l in range(1, num_forwards_used + 1):
            line_slots = [f"F{l}_LW", f"F{l}_C", f"F{l}_RW"]
            assigned_players = []
            exp_sum = 0
            primary_count = 0
            secondary_count = 0
            oop_count = 0
            for s in line_slots:
                for p in players:
                    if solver.Value(x[(p.id, s)]) == 1:
                        pos = s.split("_")[1]
                        exp_sum += p.experience
                        if pos in p.prefs:
                            status = "primary"
                            primary_count += 1
                        elif pos in p.secondary:
                            status = "secondary"
                            secondary_count += 1
                        else:
                            status = "oop"
                            oop_count += 1
                        assigned_players.append((s, p, status))

            print(f"Line {l}: {', '.join([fmt(a) for a in assigned_players])} | exp_sum={exp_sum} | primary={primary_count} secondary={secondary_count} oop={oop_count}")

        # Defense: may be partial in last pair
        print(f"\nDefense: requested_pairs={num_defense_requested} pairs_used={num_def_pairs_used} (last_partial={last_pair_partial})")
        for d in range(1, num_def_pairs_used + 1):
            pair_slots = [f"D{d}_LD"]
            if not (last_pair_partial and d == num_def_pairs_used):
                pair_slots.append(f"D{d}_RD")
            assigned_players = []
            primary_count = secondary_count = oop_count = 0
            for s in pair_slots:
                for p in players:
                    if solver.Value(x[(p.id, s)]) == 1:
                        pos = s.split("_")[1]
                        if pos in p.prefs:
                            status = "primary"
                            primary_count += 1
                        elif pos in p.secondary:
                            status = "secondary"
                            secondary_count += 1
                        else:
                            status = "oop"
                            oop_count += 1
                        assigned_players.append((s, p, status))
            print(f"Pair {d}: {', '.join([fmt(a) for a in assigned_players])} | primary={primary_count} secondary={secondary_count} oop={oop_count}")

        # overall stats
        total_assigned = sum(1 for p in players if any(solver.Value(x[(p.id, s_name)]) == 1 for s_name, _ in slots))
        total_primary = sum(1 for p in players for s_name, s_pos in slots if solver.Value(x[(p.id, s_name)]) == 1 and s_pos in p.prefs)
        total_secondary = sum(1 for p in players for s_name, s_pos in slots if solver.Value(x[(p.id, s_name)]) == 1 and s_pos in p.secondary)
        total_oop = total_assigned - total_primary - total_secondary
        print(f"\nTotal assigned: {total_assigned}")
        print(f"Primary-position assignments: {total_primary}")
        print(f"Secondary-position assignments: {total_secondary}")
        print(f"Out-of-position (OOP) assignments: {total_oop}")
    else:
        print("No solution found.")


def main():
    ap = argparse.ArgumentParser(description="Hockey lines solver")
    ap.add_argument("--roster", required=True, help="Roster CSV path")
    ap.add_argument("--forwards", type=int, default=3, help="Requested number of forward lines")
    ap.add_argument("--defense", type=int, default=3, help="Requested number of defensive pairs")
    ap.add_argument("--time-limit", type=int, default=20, help="Solver time limit in seconds")
    args = ap.parse_args()

    solve(args.roster, args.forwards, args.defense, args.time_limit)


if __name__ == "__main__":
    main()
