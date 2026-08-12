from __future__ import annotations

from pathlib import Path

import pytest

from judge.python.cli import build_parser, main


def test_budget_flags_are_parsed_into_bytes() -> None:
    args = build_parser().parse_args(
        ["verify", "--verifier", "v.py", "--solution", "s.txt", "--mem-mib", "128"]
    )
    from judge.python.cli import _budget_from_args

    assert _budget_from_args(args).mem_bytes == 128 * 1024 * 1024


def test_verify_requires_both_inputs() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["verify", "--verifier", "v.py"])


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["selftest", "--backend", "firecracker"])


def test_verify_exit_code_tracks_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = tmp_path / "v.py"
    verifier.write_text("def verify(solution, rng):\n    return solution.strip() == '42'\n")
    solution = tmp_path / "s.txt"

    solution.write_text("42")
    assert (
        main(
            [
                "verify",
                "--backend",
                "local",
                "--verifier",
                str(verifier),
                "--solution",
                str(solution),
            ]
        )
        == 0
    )
    assert "ACCEPTED" in capsys.readouterr().out

    solution.write_text("41")
    assert (
        main(
            [
                "verify",
                "--backend",
                "local",
                "--verifier",
                str(verifier),
                "--solution",
                str(solution),
            ]
        )
        == 1
    )
    assert "REJECTED" in capsys.readouterr().out


def test_verify_json_output_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    verifier = tmp_path / "v.py"
    verifier.write_text("def verify(solution, rng):\n    return True\n")
    solution = tmp_path / "s.txt"
    solution.write_text("x")

    main(
        [
            "verify",
            "--backend",
            "local",
            "--json",
            "--verifier",
            str(verifier),
            "--solution",
            str(solution),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["reason"] == "ACCEPTED"
    assert set(payload) == {"accepted", "reason", "used", "seed", "image_digest", "host", "detail"}


def test_missing_verifier_file_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "verify",
            "--backend",
            "local",
            "--verifier",
            str(tmp_path / "nope.py"),
            "--solution",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "judge:" in capsys.readouterr().err
