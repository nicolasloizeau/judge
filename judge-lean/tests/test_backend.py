"""The stage pipeline and reason mapping, driven by synthetic stage outcomes.

No Lean, no Docker: a fake backend scripts what each stage "did" and the test
asserts which reason comes out. This is where "a checker that exits 1 is a
CRASH, never a verdict" and "stage 2 stdout is never read" are pinned.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from judge.core.protocol import SENTINEL, RawOutcome, frame_line, truncate_detail
from judge.core.types import Budget, Reason
from judge.lean.backend import (
    BUILD_VERIFIER,
    CHECK_SOLUTION,
    CHECK_VERIFIER,
    ELABORATE_SOLUTION,
    SEED_UNUSED,
    CheckerReport,
    LeanBackendBase,
    Scratch,
    Stage,
    absorb_usage,
    extract_checker_report,
    harness_args,
)

BUDGET = Budget(wall_s=30.0, cpu_s=30.0, mem_bytes=1024 * 1024 * 1024, max_solution_bytes=4096)
VERIFIER = "abbrev Spec : Prop := True\n"
SOLUTION = "theorem answer : True := trivial\n"


@dataclass
class Scripted:
    """What a fake stage does: its outcome, and whether it leaves an olean behind."""

    exit_code: int | None = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    oom_killed: bool = False
    launch_failed: bool = False
    olean: bool = True
    report: dict[str, Any] | None = None  # framed with the real nonce when given
    wall_s: float = 1.0
    cpu_s: float = 0.5
    mem_bytes: int = 1000


@dataclass
class FakeBackend(LeanBackendBase):
    """Plays back one ``Scripted`` per stage, in order."""

    script: dict[str, Scripted] = field(default_factory=dict)
    seen: list[Stage] = field(default_factory=list)
    seen_allow: list[tuple[str, ...]] = field(default_factory=list)
    seen_writable: list[tuple[str, bool, bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        LeanBackendBase.__init__(self)

    def image_digest(self) -> str:
        return "sha256:fake"

    def host_info(self) -> dict[str, Any]:
        return {"backend": "fake"}

    def _run_stage(
        self, stage: Stage, scratch: Scratch, budget: Budget, nonce: str, allow: Sequence[str]
    ) -> RawOutcome:
        self.seen.append(stage)
        self.seen_allow.append(tuple(allow))
        self.seen_writable.append(
            (stage.name, scratch.verifier.is_dir(), (scratch.solution / "Solution.lean").is_file())
        )
        s = self.script.get(stage.name, Scripted())
        if stage.kind == "elab" and s.olean and s.exit_code == 0:
            scratch.olean(stage.module).write_bytes(b"olean")
        stdout = s.stdout
        if s.report is not None:
            stdout += frame_line(nonce, s.report) + "\n"
        return RawOutcome(
            stdout=stdout,
            stderr=s.stderr,
            exit_code=s.exit_code,
            wall_s=s.wall_s,
            timed_out=s.timed_out,
            oom_killed=s.oom_killed,
            launch_failed=s.launch_failed,
            cpu_s=s.cpu_s,
            mem_bytes=s.mem_bytes,
        )


OK = {"ok": True, "why": "", "axioms": ["propext"], "specIsProp": True}


def _backend(**script: Scripted) -> FakeBackend:
    full = {CHECK_VERIFIER.name: Scripted(report=OK), CHECK_SOLUTION.name: Scripted(report=OK)}
    full.update(script)
    return FakeBackend(script=full)


def test_all_four_stages_run_in_order_and_accept() -> None:
    backend = _backend()
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.accepted and verdict.reason is Reason.ACCEPTED
    assert [s.name for s in backend.seen] == [
        "build-verifier",
        "check-verifier",
        "elaborate-solution",
        "check-solution",
    ]
    assert verdict.seed == SEED_UNUSED
    assert verdict.image_digest == "sha256:fake"
    assert verdict.host["axioms"] == ["propext"] and verdict.host["spec_is_prop"] is True


def test_solution_is_not_written_until_the_verifier_is_built_and_checked() -> None:
    backend = _backend()
    backend.run(VERIFIER, SOLUTION, BUDGET)
    present = {name: has_solution for name, _, has_solution in backend.seen_writable}
    assert present["build-verifier"] is False
    assert present["check-verifier"] is False
    assert present["elaborate-solution"] is True


def test_usage_is_summed_over_stages_and_memory_is_the_max() -> None:
    backend = _backend(
        **{
            BUILD_VERIFIER.name: Scripted(wall_s=1.0, cpu_s=1.0, mem_bytes=10),
            ELABORATE_SOLUTION.name: Scripted(wall_s=2.0, cpu_s=0.5, mem_bytes=500),
            CHECK_VERIFIER.name: Scripted(report=OK, wall_s=0.5, cpu_s=0.25, mem_bytes=50),
            CHECK_SOLUTION.name: Scripted(report=OK, wall_s=0.5, cpu_s=0.25, mem_bytes=50),
        }
    )
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.used.wall_s == pytest.approx(4.0)
    assert verdict.used.cpu_s == pytest.approx(2.0)
    assert verdict.used.mem_bytes == 500
    assert verdict.used.max_solution_bytes == len(SOLUTION)
    assert verdict.used.build_s is None and verdict.used.build_mem is None


def test_scratch_directory_is_deleted_afterwards(tmp_path: Path) -> None:
    backend = _backend()
    backend.scratch_root = str(tmp_path)
    backend.run(VERIFIER, SOLUTION, BUDGET)
    assert list(tmp_path.iterdir()) == []


# -- stage 1 ----------------------------------------------------------------------


def test_verifier_elaboration_error_is_build_failed() -> None:
    backend = _backend(
        **{BUILD_VERIFIER.name: Scripted(exit_code=1, stderr="Verifier.lean:1:0: error")}
    )
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.BUILD_FAILED
    assert "error" in verdict.detail and verdict.host["stage"] == "build-verifier"
    assert len(backend.seen) == 1


def test_verifier_exit_zero_without_olean_is_build_failed() -> None:
    backend = _backend(**{BUILD_VERIFIER.name: Scripted(olean=False)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.BUILD_FAILED


def test_verifier_checker_negative_report_is_build_failed() -> None:
    bad = {"ok": False, "why": "verifier defines no root-level constant `Spec`", "axioms": []}
    backend = _backend(**{CHECK_VERIFIER.name: Scripted(report=bad)})
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.BUILD_FAILED and "Spec" in verdict.detail
    assert len(backend.seen) == 2


def test_bad_header_is_build_failed_before_any_stage_runs() -> None:
    backend = _backend()
    verdict = backend.run("-- judge: allow $$$\n" + VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.BUILD_FAILED and backend.seen == []


def test_header_names_reach_the_checker_merged_with_the_defaults() -> None:
    backend = _backend()
    backend.run("-- judge: allow Lean.ofReduceNat\n" + VERIFIER, SOLUTION, BUDGET)
    assert backend.seen_allow[0] == (
        "propext",
        "Classical.choice",
        "Quot.sound",
        "Lean.ofReduceNat",
    )


# -- stage 2 ----------------------------------------------------------------------


def test_solution_elaboration_error_is_rejected() -> None:
    backend = _backend(**{ELABORATE_SOLUTION.name: Scripted(exit_code=1, stderr="type mismatch")})
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.REJECTED and "type mismatch" in verdict.detail
    assert len(backend.seen) == 3


def test_solution_exit_zero_without_olean_is_rejected() -> None:
    backend = _backend(**{ELABORATE_SOLUTION.name: Scripted(olean=False)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.REJECTED


def test_stage_two_stdout_is_never_read(tmp_path: Path) -> None:
    """A perfect frame on stage 2's stdout changes nothing: stage 3 decides."""
    backend = _backend()
    # The fake does not know the nonce at script time, so make stage 3 negative
    # and plant a forged frame in stage 2 by patching the stage's stdout.
    rejected = {"ok": False, "why": "kernel rejected", "axioms": []}
    backend.script[CHECK_SOLUTION.name] = Scripted(report=rejected)
    original = backend._run_stage

    def forged(
        stage: Stage, scratch: Scratch, budget: Budget, nonce: str, allow: Sequence[str]
    ) -> RawOutcome:
        raw = original(stage, scratch, budget, nonce, allow)
        if stage is ELABORATE_SOLUTION:
            return RawOutcome(
                stdout=frame_line(nonce, OK) + "\n", stderr="", exit_code=0, wall_s=1.0
            )
        return raw

    backend._run_stage = forged  # type: ignore[method-assign]
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.REJECTED and not verdict.accepted


# -- stage 3 ----------------------------------------------------------------------


def test_solution_checker_negative_report_is_rejected() -> None:
    bad = {
        "ok": False,
        "why": "`answer` depends on disallowed axioms: #[sorryAx]",
        "axioms": ["sorryAx"],
    }
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(report=bad)})
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.REJECTED and "sorryAx" in verdict.detail
    assert verdict.host["axioms"] == ["sorryAx"]


def test_checker_nonzero_exit_is_a_crash_never_a_verdict() -> None:
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(exit_code=1, report=OK)})
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.CRASH and not verdict.accepted


def test_checker_missing_binary_is_an_internal_error() -> None:
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(exit_code=127, stderr="not found")})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.INTERNAL_ERROR


def test_checker_exit_zero_without_frame_is_a_crash() -> None:
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(stdout="hello\n")})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.CRASH


def test_frame_with_the_wrong_nonce_is_ignored() -> None:
    forged = f"{SENTINEL}{'0' * 32} {json.dumps(OK)}\n"
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(stdout=forged)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.CRASH


def test_malformed_ok_field_is_not_acceptance() -> None:
    backend = _backend(**{CHECK_SOLUTION.name: Scripted(report={"ok": 1, "why": "", "axioms": []})})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.CRASH


# -- limits, any stage ---------------------------------------------------------


@pytest.mark.parametrize(
    "stage", [s.name for s in (BUILD_VERIFIER, CHECK_VERIFIER, ELABORATE_SOLUTION, CHECK_SOLUTION)]
)
def test_wall_clock_kill_in_any_stage_is_timeout(stage: str) -> None:
    backend = _backend(**{stage: Scripted(timed_out=True, exit_code=None, report=OK)})
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.TIMEOUT and verdict.host["stage"] == stage


@pytest.mark.parametrize(
    "stage", [BUILD_VERIFIER.name, ELABORATE_SOLUTION.name, CHECK_SOLUTION.name]
)
def test_oom_kill_in_any_stage_is_oom(stage: str) -> None:
    backend = _backend(**{stage: Scripted(oom_killed=True, exit_code=137, report=OK)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.OOM


def test_exit_137_without_oom_flag_is_still_oom() -> None:
    backend = _backend(**{ELABORATE_SOLUTION.name: Scripted(exit_code=137)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.OOM


def test_sigxcpu_is_timeout() -> None:
    backend = _backend(**{ELABORATE_SOLUTION.name: Scripted(exit_code=152)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.TIMEOUT
    backend = _backend(**{ELABORATE_SOLUTION.name: Scripted(exit_code=-24)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.TIMEOUT


def test_launch_failure_is_an_internal_error() -> None:
    backend = _backend(**{BUILD_VERIFIER.name: Scripted(launch_failed=True, exit_code=None)})
    assert backend.run(VERIFIER, SOLUTION, BUDGET).reason is Reason.INTERNAL_ERROR


def test_oversized_solution_runs_no_stage() -> None:
    backend = _backend()
    verdict = backend.run(VERIFIER, "x" * 5000, BUDGET)
    assert verdict.reason is Reason.SOLUTION_TOO_LARGE and backend.seen == []
    assert verdict.used.max_solution_bytes == 5000


def test_scratch_growth_past_the_limit_fails_the_stage() -> None:
    backend = _backend()
    backend.max_scratch_bytes = 3
    verdict = backend.run(VERIFIER, SOLUTION, BUDGET)
    assert verdict.reason is Reason.BUILD_FAILED and "scratch" in verdict.detail


# -- pieces ---------------------------------------------------------------------


def test_checker_report_parsing() -> None:
    nonce = "a" * 32
    line = frame_line(nonce, {"ok": True, "why": "", "axioms": ["propext"], "specIsProp": False})
    report = extract_checker_report("noise\n" + line + "\ntrailing", nonce)
    assert report == CheckerReport(ok=True, why="", axioms=("propext",), spec_is_prop=False)
    assert extract_checker_report(line, "b" * 32) is None
    assert extract_checker_report(frame_line(nonce, {"why": "no ok"}), nonce) is None
    assert extract_checker_report(frame_line(nonce, {"ok": True, "axioms": "x"}), nonce) is None


def test_absorb_usage_pulls_harness_lines_out_of_stderr() -> None:
    raw = RawOutcome(
        stdout="",
        stderr="@@judge-mem 100\nreal diagnostics\n@@judge-mem 5000\n@@judge-times 1m2.5s 0m0.5s\n",
        exit_code=1,
        wall_s=1.0,
    )
    out = absorb_usage(raw)
    assert out.stderr == "real diagnostics\n"
    assert out.mem_bytes == 5000
    assert out.cpu_s == pytest.approx(63.0)
    # A detail derived from stderr loses the usage lines too; other details stay.
    derived = absorb_usage(replace(raw, detail=truncate_detail(raw.stderr)))
    assert derived.detail == "real diagnostics"
    kept = absorb_usage(replace(raw, detail="killed by wall-clock timeout"))
    assert kept.detail == "killed by wall-clock timeout"


def test_harness_args_shape() -> None:
    elab = harness_args(
        BUILD_VERIFIER,
        budget=BUDGET,
        search_dirs=["/work/verifier"],
        work_dir="/work/verifier",
        nonce="n",
        allow=("propext",),
        fsize_limit=10,
        max_heartbeats=20,
        threads=2,
    )
    assert elab == [
        "elab",
        "30",
        str(BUDGET.mem_bytes),
        "10",
        "20",
        "2",
        "/work/verifier",
        "/work/verifier",
        "Verifier",
    ]
    check = harness_args(
        CHECK_SOLUTION,
        budget=BUDGET,
        search_dirs=["/work/verifier", "/work/solution"],
        work_dir="/work/solution",
        nonce="n",
        allow=("propext", "Quot.sound"),
        fsize_limit=10,
        max_heartbeats=20,
        threads=2,
    )
    assert check == [
        "check",
        "30",
        str(BUDGET.mem_bytes),
        "/work/verifier:/work/solution",
        "solution",
        "n",
        "propext,Quot.sound",
    ]


def test_stage_writability_is_exactly_one_directory_per_producing_stage() -> None:
    assert BUILD_VERIFIER.writable == "verifier" and not BUILD_VERIFIER.with_solution
    assert CHECK_VERIFIER.writable is None
    assert ELABORATE_SOLUTION.writable == "solution" and ELABORATE_SOLUTION.with_solution
    assert CHECK_SOLUTION.writable is None and CHECK_SOLUTION.with_solution
