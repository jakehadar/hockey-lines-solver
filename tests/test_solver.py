import os

import solver

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_ROSTER = os.path.join(REPO_ROOT, "rosters", "sample_roster.csv")


def unit_of(slot: str) -> str:
    return slot.split("_")[0]


def all_assignments(result):
    return [(a.player_id, a.slot, a.position) for fl in result.forward_lines for a in fl.slots] + \
           [(a.player_id, a.slot, a.position) for dp in result.defense_pairs for a in dp.slots]


def test_roster_without_new_columns_still_solves():
    """Backwards compatibility: a roster CSV with no unwilling_positions,
    optional_position_override, or optional_player_link columns must still
    parse and solve exactly as before."""
    players = solver.read_roster(SAMPLE_ROSTER)
    assert all(p.unwilling == [] and p.position_override is None and p.link is None for p in players)
    result = solver.solve_lines(players, 3, 3, 10)
    assert result.status in ("OPTIMAL", "FEASIBLE")


def test_unwilling_positions_is_a_hard_constraint():
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW;C;RW", "secondary_positions": "", "unwilling_positions": "LW;RW"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10)
    p1_positions = [pos for pid, _, pos in all_assignments(result) if pid == "P1"]
    assert p1_positions == ["C"]


def test_optional_position_override_beats_prefs_and_unwilling():
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "unwilling_positions": "C", "optional_position_override": "C"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    # allow_unwilling defaults to False as of that feature - explicitly
    # allow it here, since overriding into an unwilling position is exactly
    # what this test is about.
    result = solver.solve_lines(players, 1, 0, 10, allow_unwilling=True)
    p1_positions = [pos for pid, _, pos in all_assignments(result) if pid == "P1"]
    assert p1_positions == ["C"]

    # The override forced P1 onto a position it's marked unwilling to play -
    # that must be flagged as "unwilling", not misreported as "oop"/"secondary".
    p1_slot = next(a for fl in result.forward_lines for a in fl.slots if a.player_id == "P1")
    assert p1_slot.status == "unwilling"
    assert result.forward_lines[0].unwilling_count == 1
    assert result.summary.total_unwilling == 1


def test_optional_position_override_forces_assignment_and_can_cause_infeasibility():
    # An override doesn't just restrict *where* a player can go, it forces
    # *whether* - two players locked to the same position, with only one
    # slot for it, must surface as infeasible rather than quietly benching
    # one of them.
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_position_override": "LW"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_position_override": "LW"},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P4", "name": "D", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10)  # 1 forward line -> exactly one LW slot
    assert result.status == "NO_SOLUTION"


def test_optional_position_override_does_not_force_an_unavailable_player_onto_the_ice():
    # available beats override: marking a locked player unavailable is still
    # how you bench them for a what-if, rather than the override contradicting
    # availability and making the roster infeasible.
    rows = [
        {"id": "P1", "name": "A", "available": "0", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_position_override": "LW"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P4", "name": "D", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    assigned_ids = [pid for pid, _, _ in all_assignments(result)]
    assert "P1" not in assigned_ids


def test_optional_player_link_forces_same_forward_line():
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_player_link": "P4"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
        {"id": "P4", "name": "D", "available": "1", "experience": "1", "preferred_positions": "LD", "secondary_positions": ""},
        {"id": "P5", "name": "E", "available": "1", "experience": "1", "preferred_positions": "RD", "secondary_positions": ""},
        {"id": "P6", "name": "F", "available": "1", "experience": "1", "preferred_positions": "LD", "secondary_positions": ""},
        {"id": "P7", "name": "G", "available": "1", "experience": "1", "preferred_positions": "RD", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 2, 10)
    assignments = {pid: slot for pid, slot, _ in all_assignments(result)}
    assert "P1" in assignments and "P4" in assignments
    assert unit_of(assignments["P1"]) == unit_of(assignments["P4"])


def test_allow_oop_true_permits_filling_a_slot_with_an_untagged_player():
    # Nobody prefers or lists RW as secondary, and there are exactly enough
    # players to fill all 3 forward slots - someone has to play RW out of
    # position, and allow_oop's default (True) must permit that.
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10, allow_oop=True)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.summary.total_assigned == 3
    rw_slot = next(a for fl in result.forward_lines for a in fl.slots if a.position == "RW")
    assert rw_slot.status == "oop"


def test_allow_oop_false_forbids_filling_a_slot_with_an_untagged_player():
    # Identical roster to the allow_oop=True case above - the only feasible
    # solution there requires one player OOP at RW, which allow_oop=False
    # must now forbid outright, making the whole solve infeasible (there's
    # no one else who could take that slot instead).
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10, allow_oop=False)
    assert result.status == "NO_SOLUTION"


def test_allow_oop_false_takes_precedence_over_an_override_to_an_untagged_position():
    # P1 is overridden to C, a position it never ranked at all (not
    # preferred, secondary, or unwilling) - allow_oop=False must block that
    # override outright rather than honoring it, per the documented
    # precedence (available > allow_oop > override > preferences).
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_position_override": "C"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result_allowed = solver.solve_lines(players, 1, 0, 10, allow_oop=True)
    assert result_allowed.status in ("OPTIMAL", "FEASIBLE")

    result_forbidden = solver.solve_lines(players, 1, 0, 10, allow_oop=False)
    assert result_forbidden.status == "NO_SOLUTION"


def test_allow_oop_false_does_not_affect_an_override_to_an_unwilling_position():
    # allow_oop only ever concerns truly untagged positions - an override to
    # a position the player marked *unwilling* is a separate, unaffected
    # axis, and must still be honored even with allow_oop=False, as long as
    # allow_unwilling explicitly permits it (its own default is False).
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "unwilling_positions": "C", "optional_position_override": "C"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10, allow_oop=False, allow_unwilling=True)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    p1_slot = next(a for fl in result.forward_lines for a in fl.slots if a.player_id == "P1")
    assert p1_slot.position == "C"
    assert p1_slot.status == "unwilling"


def test_allow_unwilling_defaults_to_false_and_blocks_an_override_to_it():
    # Default behavior as of this feature: an override into a position the
    # player marked unwilling is forbidden unless explicitly allowed - a
    # real behavior change from this app's history, made deliberately.
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "unwilling_positions": "C", "optional_position_override": "C"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result_default = solver.solve_lines(players, 1, 0, 10)
    assert result_default.status == "NO_SOLUTION"

    result_explicit_false = solver.solve_lines(players, 1, 0, 10, allow_unwilling=False)
    assert result_explicit_false.status == "NO_SOLUTION"

    result_allowed = solver.solve_lines(players, 1, 0, 10, allow_unwilling=True)
    assert result_allowed.status in ("OPTIMAL", "FEASIBLE")
    p1_slot = next(a for fl in result_allowed.forward_lines for a in fl.slots if a.player_id == "P1")
    assert p1_slot.position == "C"
    assert p1_slot.status == "unwilling"


def test_allow_unwilling_false_does_not_affect_non_overridden_players():
    # A non-overridden player was already hard-blocked from their unwilling
    # positions before this feature existed - allow_unwilling changes
    # nothing for them either way, it only ever gates the override escape
    # hatch.
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW;RW", "secondary_positions": "", "unwilling_positions": "C"},
        {"id": "P2", "name": "B", "available": "1", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW;LW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10, allow_unwilling=False)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    p1_positions = [pos for pid, _, pos in all_assignments(result) if pid == "P1"]
    assert p1_positions == ["LW"] or p1_positions == ["RW"]


def test_optional_player_link_benches_together_when_unsatisfiable():
    """If the linked partner can't be seated at all (roster too small / no
    slots left), the model must bench both rather than split them up."""
    rows = [
        {"id": "P1", "name": "A", "available": "1", "experience": "3", "preferred_positions": "LW", "secondary_positions": "", "optional_player_link": "P2"},
        {"id": "P2", "name": "B", "available": "0", "experience": "3", "preferred_positions": "C", "secondary_positions": ""},
        {"id": "P3", "name": "C", "available": "1", "experience": "3", "preferred_positions": "RW", "secondary_positions": ""},
    ]
    players = solver.players_from_rows(rows)
    result = solver.solve_lines(players, 1, 0, 10)
    assigned_ids = {pid for pid, _, _ in all_assignments(result)}
    assert "P1" not in assigned_ids
    assert "P2" not in assigned_ids
