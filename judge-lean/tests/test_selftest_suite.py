"""The adversarial suite, end to end.

Against ``LeanLocalInsecureBackend`` when the pinned toolchain and the built
Lake project are present -- it exercises the harness, the checker and the
reason mapping. Against ``LeanGvisorBackend`` when Docker + runsc and the image
are present. Both skip cleanly otherwise, because a green tick from a host
that never ran Lean would be a lie.
"""

from __future__ import annotations

import pytest

from judge.core.selftest import Fixture, run_selftest
from judge.core.types import Budget, Reason
from judge.lean.backend import LeanGvisorBackend, LeanLocalInsecureBackend
from judge.lean.fixtures import ALL_FIXTURES, SELFTEST_BUDGET, SPEC_PROP


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
def test_fixture_against_local_backend(
    lean_local: LeanLocalInsecureBackend, fixture: Fixture
) -> None:
    if fixture.requires_isolation:
        pytest.skip("fixture needs real isolation; local backend provides none")
    verdict = lean_local.run(
        fixture.verifier_src, fixture.solution, fixture.budget or SELFTEST_BUDGET
    )
    assert verdict.reason in fixture.expected, f"detail: {verdict.detail}"
    assert verdict.accepted == (verdict.reason is Reason.ACCEPTED)


def test_local_suite_has_no_false_accepts(lean_local: LeanLocalInsecureBackend) -> None:
    report = run_selftest(lean_local, ALL_FIXTURES, budget=SELFTEST_BUDGET)
    assert report.false_accepts == [], report.render()
    assert report.ok, report.render()


@pytest.mark.slow
@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
def test_fixture_against_gvisor_backend(lean_gvisor: LeanGvisorBackend, fixture: Fixture) -> None:
    verdict = lean_gvisor.run(
        fixture.verifier_src, fixture.solution, fixture.budget or SELFTEST_BUDGET
    )
    assert verdict.reason in fixture.expected, f"detail: {verdict.detail}"


@pytest.mark.slow
def test_gvisor_suite_is_green(lean_gvisor: LeanGvisorBackend) -> None:
    report = run_selftest(lean_gvisor, ALL_FIXTURES, budget=SELFTEST_BUDGET)
    assert report.false_accepts == [], report.render()
    assert report.ok, report.render()
    assert report.skipped == 0, "gVisor isolates, so nothing should be skipped"


def test_verdict_records_the_reproducibility_tuple(lean_local: LeanLocalInsecureBackend) -> None:
    verdict = lean_local.run(
        SPEC_PROP, "theorem answer : 2 + 2 = 4 := by decide\n", SELFTEST_BUDGET
    )
    assert verdict.accepted
    assert verdict.seed == 0, "no randomness in a Lean verification"
    assert verdict.image_digest
    assert verdict.host["backend"] == "local-insecure"
    assert verdict.host["axioms"] == [] and verdict.host["spec_is_prop"] is True
    assert verdict.used.wall_s > 0 and verdict.used.cpu_s > 0 and verdict.used.mem_bytes > 0


def test_same_inputs_give_the_same_verdict(lean_local: LeanLocalInsecureBackend) -> None:
    solution = "theorem answer : 2 + 2 = 4 := by decide\n"
    first = lean_local.run(SPEC_PROP, solution, SELFTEST_BUDGET)
    second = lean_local.run(SPEC_PROP, solution, SELFTEST_BUDGET)
    assert (first.reason, first.host["axioms"]) == (second.reason, second.host["axioms"])


def test_oversized_solution_is_rejected_without_starting_a_stage(
    lean_local: LeanLocalInsecureBackend,
) -> None:
    budget = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=256 * 1024 * 1024, max_solution_bytes=8)
    verdict = lean_local.run(SPEC_PROP, "x" * 100, budget)
    assert verdict.reason is Reason.SOLUTION_TOO_LARGE
    assert verdict.used.max_solution_bytes == 100 and verdict.used.wall_s == 0.0


def test_local_backend_refuses_to_be_constructed_by_accident() -> None:
    with pytest.raises(ValueError, match="i_understand_this_is_insecure"):
        LeanLocalInsecureBackend()
