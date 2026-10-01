from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge.lean.backend import LeanLocalInsecureBackend
from judge.lean.cli import _budget_from_args, build_parser, main
from judge.lean.fixtures import SPEC_PROP


def test_budget_flags_are_parsed_into_bytes() -> None:
    args = build_parser().parse_args(
        ["verify", "--verifier", "V.lean", "--solution", "S.lean", "--mem-mib", "128"]
    )
    assert _budget_from_args(args).mem_bytes == 128 * 1024 * 1024


def test_defaults_are_larger_than_pythons() -> None:
    args = build_parser().parse_args(["verify", "--verifier", "V.lean", "--solution", "S.lean"])
    budget = _budget_from_args(args)
    assert budget.wall_s >= 300 and budget.mem_bytes >= 6 * 1024**3
    assert budget.max_solution_bytes == 256 * 1024


def test_verify_requires_both_inputs() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["verify", "--verifier", "V.lean"])


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["selftest", "--backend", "firecracker"])


def test_missing_verifier_file_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lean_local: LeanLocalInsecureBackend
) -> None:
    code = main(
        [
            "verify",
            "--backend",
            "local",
            "--verifier",
            str(tmp_path / "nope.lean"),
            "--solution",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "judge-lean:" in capsys.readouterr().err


def test_verify_exit_code_tracks_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lean_local: LeanLocalInsecureBackend
) -> None:
    verifier = tmp_path / "Verifier.lean"
    verifier.write_text(SPEC_PROP)
    solution = tmp_path / "Solution.lean"
    common = [
        "verify",
        "--backend",
        "local",
        "--verifier",
        str(verifier),
        "--solution",
        str(solution),
    ]

    solution.write_text("theorem answer : 2 + 2 = 4 := by decide\n")
    assert main(common) == 0
    assert "ACCEPTED" in capsys.readouterr().out

    solution.write_text("theorem answer : 2 + 2 = 4 := sorry\n")
    assert main([*common, "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "REJECTED" and payload["seed"] == 0
    assert set(payload) == {"accepted", "reason", "used", "seed", "image_digest", "host", "detail"}
