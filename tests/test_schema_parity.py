"""solver/schemas.py and studio/local_schemas.py independently define
PlayerIn and ObjectiveSetting - the two studio actually builds and validates
itself, kept separate from the solve-response models studio only ever stores
as opaque JSON (see studio/local_schemas.py's module docstring). Since
they're two Python classes rather than one shared import, nothing else would
catch a field added to one and forgotten on the other - these tests are that
catch."""

import schemas as solver_schemas  # solver/schemas.py - see conftest.py
from studio import local_schemas as studio_schemas


def test_player_in_fields_match_between_solver_and_studio():
    assert set(solver_schemas.PlayerIn.model_fields) == set(studio_schemas.PlayerIn.model_fields)


def test_objective_setting_fields_and_keys_match_between_solver_and_studio():
    assert set(solver_schemas.ObjectiveSetting.model_fields) == set(studio_schemas.ObjectiveSetting.model_fields)
    solver_keys = solver_schemas.ObjectiveSetting.model_fields["key"].annotation.__args__
    studio_keys = studio_schemas.ObjectiveSetting.model_fields["key"].annotation.__args__
    assert solver_keys == studio_keys


def test_default_objectives_match_between_solver_and_studio():
    solver_dump = [o.model_dump() for o in solver_schemas.DEFAULT_OBJECTIVES]
    studio_dump = [o.model_dump() for o in studio_schemas.DEFAULT_OBJECTIVES]
    assert solver_dump == studio_dump
