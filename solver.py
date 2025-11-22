#!/usr/bin/env python3
"""Hockey lines solver using OR-Tools CP-SAT.

Usage:
  - Activate your venv: `source ./venv/bin/activate`
  - Install deps: `pip install -r requirements.txt`
  - Run: `python solver.py --roster roster_sample.csv --forwards 3 --defense 3`

CSV format (header): id,name,available,experience,preferred_positions
  - `available` is 1 or 0
  - `experience` integer 1..3
  - `preferred_positions` is semicolon-separated, e.g. "LW;C"

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


def read_roster(path: str) -> List[Player]:
    players: List[Player] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            pid = r.get("id") or r.get("name")
            name = r.get("name") or pid
            avail = int(r.get("available", "1"))
            exp = int(r.get("experience", "1"))
            prefs_raw = r.get("preferred_positions", "")
            prefs = [p.strip().upper() for p in prefs_raw.replace("|", ";").split(";") if p.strip()]
            players.append(Player(pid, name, avail, exp, prefs))
    return players


def build_slots(num_forwards: int, num_defense: int) -> Tuple[List[Tuple[str, str]], List[str]]:
    # returns (list of (slot_name, pos)), and list of forward slot names
    slots: List[Tuple[str, str]] = []
    forward_slot_names: List[str] = []
    for l in range(1, num_forwards + 1):
        for pos in ["LW", "C", "RW"]:
            s = f"F{l}_{pos}"
            slots.append((s, pos))
            forward_slot_names.append(s)
    for d in range(1, num_defense + 1):
        for pos in ["LD", "RD"]:
            s = f"D{d}_{pos}"
            slots.append((s, pos))
    return slots, forward_slot_names


def solve(roster_path: str, num_forwards: int, num_defense: int, allow_oop: bool, time_limit: int):
    players = read_roster(roster_path)
    slots, forward_slots = build_slots(num_forwards, num_defense)

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

    # preference score (soft)
    pref_terms = []
    for p in players:
        for s_name, s_pos in slots:
            prefers = 1 if s_pos in p.prefs else 0
            if not prefers and not allow_oop:
                # forbid playing out-of-position
                model.Add(x[(p.id, s_name)] == 0)
            else:
                # contributes to preference score (1 if preferred)
                pref_terms.append(prefers * x[(p.id, s_name)])

    assigned = []
    for p in players:
        assigned.append(sum(x[(p.id, s_name)] for s_name, _ in slots))

    # Experience balancing across forward lines (L1 deviation)
    # Create per-line experience sums and total
    per_line_exp_vars = []
    total_exp_forwards_terms = []
    slots_by_line = defaultdict(list)
    for s_name, s_pos in slots:
        if s_name in forward_slots:
            # extract line number
            line = s_name.split("_")[0]  # e.g., F1
            slots_by_line[line].append((s_name, s_pos))

    for line, line_slots in slots_by_line.items():
        # exp sum var for this line
        max_exp_line = 3 * len(line_slots)
        v = model.NewIntVar(0, max_exp_line, f"exp_{line}")
        expr = sum(p.experience * x[(p.id, s_name)] for p in players for s_name, _ in line_slots)
        model.Add(v == expr)
        per_line_exp_vars.append(v)
        total_exp_forwards_terms.append(v)

    total_exp_forwards = model.NewIntVar(0, 3 * len(forward_slots), "total_exp_forwards")
    model.Add(total_exp_forwards == sum(total_exp_forwards_terms))

    # dev variables: devF_l >= |F * exp_line_l - total_exp_forwards|
    dev_vars = []
    F = num_forwards
    if F > 0:
        # safe bound for dev is F * max_exp_line
        max_dev = F * 3 * 3  # 3 exp * 3 slots * F; still small
        for v in per_line_exp_vars:
            dev = model.NewIntVar(0, max_dev, f"dev_{v.Name()}")
            model.Add(dev >= F * v - total_exp_forwards)
            model.Add(dev >= total_exp_forwards - F * v)
            dev_vars.append(dev)

    # Objective weights (large numbers to emulate lexicographic priorities)
    W1 = 1_000_000  # maximize number of assigned players
    W2 = 1_000      # maximize position preferences
    W3 = 1          # minimize experience imbalance (sum of dev vars)

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
        # print forward lines
        print("\nForwards:")
        for l in range(1, num_forwards + 1):
            line_slots = [f"F{l}_LW", f"F{l}_C", f"F{l}_RW"]
            assigned_players = []
            exp_sum = 0
            pref_ok = 0
            for s in line_slots:
                for p in players:
                    if solver.Value(x[(p.id, s)]) == 1:
                        assigned_players.append((s, p))
                        exp_sum += p.experience
                        if s.split("_")[1] in p.prefs:
                            pref_ok += 1
            print(f"Line {l}: {', '.join([f'{p.name}({s})' for s,p in assigned_players])} | exp_sum={exp_sum} | prefs={pref_ok}/{len(line_slots)}")

        print("\nDefense:")
        for d in range(1, num_defense + 1):
            pair_slots = [f"D{d}_LD", f"D{d}_RD"]
            assigned_players = []
            for s in pair_slots:
                for p in players:
                    if solver.Value(x[(p.id, s)]) == 1:
                        assigned_players.append((s, p))
            print(f"Pair {d}: {', '.join([f'{p.name}({s})' for s,p in assigned_players])}")

        # overall stats
        total_assigned = sum(1 for p in players if any(solver.Value(x[(p.id, s_name)]) == 1 for s_name, _ in slots))
        total_pref = sum(1 for p in players for s_name, s_pos in slots if solver.Value(x[(p.id, s_name)]) == 1 and s_pos in p.prefs)
        print(f"\nTotal assigned: {total_assigned}")
        print(f"Preference-satisfied assignments: {total_pref}")
    else:
        print("No solution found.")


def main():
    ap = argparse.ArgumentParser(description="Hockey lines solver")
    ap.add_argument("--roster", required=True, help="Roster CSV path")
    ap.add_argument("--forwards", type=int, default=3, help="Number of forward lines")
    ap.add_argument("--defense", type=int, default=3, help="Number of defensive pairs")
    ap.add_argument("--allow-oop", action="store_true", help="Allow out-of-preference assignments (penalized)")
    ap.add_argument("--time-limit", type=int, default=20, help="Solver time limit in seconds")
    args = ap.parse_args()

    solve(args.roster, args.forwards, args.defense, args.allow_oop, args.time_limit)


if __name__ == "__main__":
    main()
