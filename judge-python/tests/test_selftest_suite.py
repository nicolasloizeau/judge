"""The adversarial suite, end to end.

Against ``LocalInsecureBackend`` always -- it exercises the harness, the framing
and the verdict mapping. Against ``GvisorBackend`` when Docker + runsc and the
image are present; skipped cleanly otherwise, because a green tick from a host
that never ran a container would be a lie.
"""

from __future__ import annotations

import pytest

from judge.core.backends.gvisor import GvisorBackend
from judge.core.backends.local import LocalInsecureBackend
from judge.core.selftest import DEFAULT_BUDGET, Fixture, run_selftest
from judge.core.types import Budget, Reason
from judge.python import local_backend
from judge.python.fixtures import ALL_FIXTURES


@pytest.fixture(scope="module")
def local() -> LocalInsecureBackend:
    return local_backend()


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
def test_fixture_against_local_backend(local: LocalInsecureBackend, fixture: Fixture) -> None:
    if fixture.requires_isolation:
        pytest.skip("fixture needs real isolation; local backend provides none")
    verdict = local.run(fixture.verifier_src, fixture.solution, fixture.budget or DEFAULT_BUDGET)
    assert verdict.reason in fixture.expected, f"detail: {verdict.detail}"
    assert verdict.accepted == (verdict.reason is Reason.ACCEPTED)


def test_local_suite_has_no_false_accepts(local: LocalInsecureBackend) -> None:
    report = run_selftest(local, ALL_FIXTURES)
    assert report.false_accepts == [], report.render()
    assert report.ok, report.render()


@pytest.mark.slow
@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
def test_fixture_against_gvisor_backend(gvisor_backend: GvisorBackend, fixture: Fixture) -> None:
    verdict = gvisor_backend.run(
        fixture.verifier_src, fixture.solution, fixture.budget or DEFAULT_BUDGET
    )
    assert verdict.reason in fixture.expected, f"detail: {verdict.detail}"


@pytest.mark.slow
def test_gvisor_suite_is_green(gvisor_backend: GvisorBackend) -> None:
    report = run_selftest(gvisor_backend, ALL_FIXTURES)
    assert report.false_accepts == [], report.render()
    assert report.ok, report.render()
    assert report.skipped == 0, "gVisor isolates, so nothing should be skipped"


def test_verdict_records_the_reproducibility_tuple(local: LocalInsecureBackend) -> None:
    src = "def verify(s, rng): return True"
    verdict = local.run(src, "x", DEFAULT_BUDGET)
    assert verdict.accepted
    assert 0 <= verdict.seed < 2**64
    assert verdict.image_digest
    assert verdict.host["backend"] == "local-insecure"


def test_seed_differs_between_runs(local: LocalInsecureBackend) -> None:
    src = "def verify(s, rng): return True"
    seeds = {local.run(src, "x", DEFAULT_BUDGET).seed for _ in range(3)}
    assert len(seeds) == 3


def test_recorded_seed_reproduces_the_draw(local: LocalInsecureBackend) -> None:
    """A verdict is replayable from (verifier_src, solution, budget, image, seed)."""
    src = "def verify(s, rng): return bool(rng.integers(0, 2**62) % 2 == 0)"
    first = local.run(src, "x", DEFAULT_BUDGET)

    import numpy as np

    from judge.python.harness import run_verifier

    replay = run_verifier(src, "x", np.random.default_rng(first.seed))
    assert (replay.reason is Reason.ACCEPTED) == first.accepted


def test_oversized_solution_is_rejected_without_starting_a_sandbox(
    local: LocalInsecureBackend,
) -> None:
    budget = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=256 * 1024 * 1024, max_solution_bytes=8)
    verdict = local.run("def verify(s, rng): return True", "x" * 100, budget)
    assert verdict.reason is Reason.SOLUTION_TOO_LARGE
    assert verdict.used.max_solution_bytes == 100


def test_local_backend_refuses_to_be_constructed_by_accident() -> None:
    with pytest.raises(ValueError, match="i_understand_this_is_insecure"):
        LocalInsecureBackend(["true"])
