#!/usr/bin/env python3
"""Prototype: a "degrees of freedom" score for a solved scenario.

The idea: `solve_lines()`'s OPTIMAL/FEASIBLE status describes the objective
value it found, not a single canonical lineup - many different assignments
can tie for that same best score. Counting the raw number of tied-optimal
solutions is a bad metric though (it blows up combinatorially with any
cluster of interchangeable players), so instead this measures, per position,
how many *other* available players could be swapped in without dropping the
objective below its current best - a count that scales linearly rather than
factorially, and means the same thing across roster sizes.

For each position with at least one filled slot in the baseline solve:
  - every OTHER available, non-linked, not-differently-overridden, not-
    unwilling-for-this-position candidate is tried by temporarily forcing
    their `position_override` to that position (reusing the existing
    override-forces-assignment solver behavior) and re-solving
  - a candidate "counts" if the re-solve still ties the baseline's
    (total_assigned, primary/secondary score) - the same two terms that
    dominate solve_lines()'s real objective (W1, W2 >> W3), so this ignores
    only the lowest-priority experience-balance tiebreaker
  - the position's flexibility score is that count, MINUS however many
    players already hold that position in the baseline (they trivially tie
    it by definition) - i.e. "how many *extra* options exist beyond who's
    already there"

The total score is the sum of per-position flexibility across every filled
position; `score / total_filled_slots` gives a per-slot average that's
comparable across differently-sized rosters/line configurations.

This is a prototype exploring the idea, not wired into the Studio UI/API -
run it directly against a roster CSV to see how it performs on real data:

    python dof.py --roster rosters/sample_roster.csv --forwards 3 --defense 3
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Dict, List, Tuple

import solver
from schemas import SolveResponse


@dataclass
class PositionFlexibility:
    position: str
    slots_filled: int
    extra_options: int
    candidates_checked: int


@dataclass
class DegreesOfFreedomResult:
    baseline: SolveResponse
    by_position: List[PositionFlexibility]
    total_extra_options: int
    total_filled_slots: int
    score_per_slot: float


# Same canonical order used throughout the app (forward line slots LW/C/RW,
# then defense pair slots LD/RD) - not alphabetical, so a position-breakdown
# UI can lay these out matching the line/pair cards it's describing.
_POSITION_ORDER = ("LW", "C", "RW", "LD", "RD")


def _objective_key(resp: SolveResponse) -> Tuple[int, int]:
    # A stand-in for solve_lines()'s real weighted objective (W1=1_000_000,
    # W2=10_000, W3=100): this omits the W3 experience-balance term
    # entirely, on purpose - it's the lowest-priority tiebreaker, three
    # orders of magnitude below W2, so treating two solves as "equally good"
    # even if experience balance differs slightly is the right call for a
    # flexibility metric, not an approximation error.
    s = resp.summary
    return (s.total_assigned, 2 * s.total_primary + s.total_secondary)


def _filled_positions(resp: SolveResponse) -> Dict[str, List[str]]:
    """position -> ids of players currently holding it in the baseline."""
    holders: Dict[str, List[str]] = defaultdict(list)
    for fl in resp.forward_lines:
        for a in fl.slots:
            holders[a.position].append(a.player_id)
    for dp in resp.defense_pairs:
        for a in dp.slots:
            holders[a.position].append(a.player_id)
    return holders


def _check_candidate(
    players: List[solver.Player],
    candidate_id: str,
    position: str,
    num_forwards_requested: int,
    num_defense_requested: int,
    time_limit: int,
    baseline_key: Tuple[int, int],
) -> bool:
    trial_players = [
        replace(p, position_override=position) if p.id == candidate_id else p for p in players
    ]
    resp = solver.solve_lines(trial_players, num_forwards_requested, num_defense_requested, time_limit)
    if resp.status == "NO_SOLUTION":
        return False
    return _objective_key(resp) == baseline_key


def compute_degrees_of_freedom(
    players: List[solver.Player],
    num_forwards_requested: int,
    num_defense_requested: int,
    time_limit: int = 5,
    max_workers: int = 8,
) -> DegreesOfFreedomResult | None:
    baseline = solver.solve_lines(players, num_forwards_requested, num_defense_requested, time_limit)
    if baseline.status == "NO_SOLUTION":
        return None

    baseline_key = _objective_key(baseline)
    holders_by_position = _filled_positions(baseline)

    # Work items: (position, candidate_id) pairs worth actually re-solving for.
    work: List[Tuple[str, str]] = []
    for position, holder_ids in holders_by_position.items():
        holder_set = set(holder_ids)
        for p in players:
            if p.id in holder_set:
                continue  # trivially ties the baseline by definition - counted separately below
            if p.available != 1:
                continue
            if position in p.unwilling:
                continue  # asking someone truly unwilling isn't a real "option"
            if p.position_override and p.position_override != position:
                continue  # already pinned elsewhere by the scenario itself
            if p.link:
                continue  # linked pairs need their own substitution logic - out of scope for v1
            work.append((position, p.id))

    outcomes: Dict[Tuple[str, str], bool] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _check_candidate, players, candidate_id, position,
                num_forwards_requested, num_defense_requested, time_limit, baseline_key,
            ): (position, candidate_id)
            for position, candidate_id in work
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()

    by_position: List[PositionFlexibility] = []
    total_extra = 0
    total_slots = 0
    for position, holder_ids in sorted(holders_by_position.items(), key=lambda kv: _POSITION_ORDER.index(kv[0])):
        checked = [cid for (pos, cid) in outcomes if pos == position]
        extra = sum(1 for cid in checked if outcomes[(position, cid)])
        by_position.append(
            PositionFlexibility(
                position=position,
                slots_filled=len(holder_ids),
                extra_options=extra,
                candidates_checked=len(checked),
            )
        )
        total_extra += extra
        total_slots += len(holder_ids)

    return DegreesOfFreedomResult(
        baseline=baseline,
        by_position=by_position,
        total_extra_options=total_extra,
        total_filled_slots=total_slots,
        score_per_slot=(total_extra / total_slots) if total_slots else 0.0,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roster", required=True, help="Path to a roster CSV")
    ap.add_argument("--forwards", type=int, default=3)
    ap.add_argument("--defense", type=int, default=3)
    ap.add_argument("--time-limit", type=int, default=5, help="Per-solve CP-SAT time limit, in seconds")
    args = ap.parse_args()

    players = solver.read_roster(args.roster)
    started = time.monotonic()
    result = compute_degrees_of_freedom(players, args.forwards, args.defense, args.time_limit)
    elapsed = time.monotonic() - started

    if result is None:
        print("Baseline solve was infeasible (NO_SOLUTION) - nothing to score.")
        return

    print(f"Baseline: {result.baseline.status}, {result.total_filled_slots} slots filled ({elapsed:.1f}s total)")
    print(f"Degrees of freedom: {result.total_extra_options} extra options "
          f"({result.score_per_slot:.2f} per filled slot)\n")
    for pf in result.by_position:
        print(
            f"  {pf.position:>2}: {pf.slots_filled} slot(s) filled, "
            f"{pf.extra_options}/{pf.candidates_checked} other candidates could also fill it "
            f"without dropping the objective"
        )


if __name__ == "__main__":
    main()
