import os
import threading
import time

import dof
import solver

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_ROSTER = os.path.join(REPO_ROOT, "rosters", "sample_roster.csv")


def test_job_registry_cancel_is_a_harmless_noop_for_an_unknown_id():
    assert dof.cancel_job("no-such-job") is False


def test_job_registry_register_cancel_unregister_round_trip():
    event = dof.register_job("job-1")
    assert not event.is_set()
    assert dof.cancel_job("job-1") is True
    assert event.is_set()
    dof.unregister_job("job-1")
    # Once unregistered, cancelling the same id again is a no-op, not an
    # error - the job already finished (or never existed) either way.
    assert dof.cancel_job("job-1") is False


def test_compute_degrees_of_freedom_short_circuits_when_already_cancelled():
    players = solver.read_roster(SAMPLE_ROSTER)
    event = threading.Event()
    event.set()
    result = dof.compute_degrees_of_freedom(players, 3, 3, time_limit=5, cancel_event=event)
    assert result is None


def test_compute_degrees_of_freedom_skips_remaining_work_once_cancelled(monkeypatch):
    # ThreadPoolExecutor.submit() doesn't block, so essentially every
    # candidate gets queued well before a cancel signal could realistically
    # arrive - the actual saving comes from _check_candidate() bailing out at
    # the *top* of its own task, before paying for the real (here, slowed
    # down) solve. Patching solver.solve_lines (not _check_candidate itself)
    # preserves that ordering: only tasks that get past their own
    # already-cancelled check pay the artificial delay.
    players = solver.read_roster(SAMPLE_ROSTER)
    event = threading.Event()
    real_solve_lines = solver.solve_lines

    def slow_solve_lines(*args, **kwargs):
        time.sleep(0.05)
        return real_solve_lines(*args, **kwargs)

    monkeypatch.setattr(solver, "solve_lines", slow_solve_lines)

    def cancel_soon():
        time.sleep(0.15)
        event.set()

    threading.Thread(target=cancel_soon, daemon=True).start()
    started = time.monotonic()
    result = dof.compute_degrees_of_freedom(players, 3, 3, time_limit=5, max_workers=2, cancel_event=event)
    elapsed_cancelled = time.monotonic() - started

    started = time.monotonic()
    full_run = dof.compute_degrees_of_freedom(players, 3, 3, time_limit=5, max_workers=2)
    elapsed_full = time.monotonic() - started

    assert result is not None  # the baseline solve itself completes regardless of cancellation
    assert full_run is not None
    assert elapsed_cancelled < elapsed_full
