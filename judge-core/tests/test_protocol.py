"""Framing and outcome-mapping: the part that must not be fooled by stdout."""

from __future__ import annotations

import json

import pytest

from judge.core.protocol import (
    SENTINEL,
    Request,
    Result,
    extract_result,
    new_nonce,
    new_seed,
    verdict_from_outcome,
)
from judge.core.types import Budget, Reason, Verdict

BUDGET = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=1024, max_solution_bytes=16)
NONCE = "a" * 32


def _line(reason: Reason, nonce: str = NONCE) -> str:
    result = Result(reason=reason, wall_s=1.0, cpu_s=0.5, mem_bytes=100, solution_bytes=3)
    return result.encode_line(nonce)


def test_request_roundtrip() -> None:
    request = Request(
        nonce=NONCE, seed=99, verifier_src="x = 1", solution="sol", budget=BUDGET, pids_limit=8
    )
    assert Request.decode(request.encode()) == request


def test_request_rejects_unknown_protocol_version() -> None:
    payload = json.loads(Request(NONCE, 1, "", "", BUDGET).encode())
    payload["version"] = 999
    with pytest.raises(ValueError, match="unsupported protocol version"):
        Request.decode(json.dumps(payload))


def test_extract_finds_the_framed_line_among_verifier_noise() -> None:
    stdout = "\n".join(["hello", "garbage", _line(Reason.REJECTED), "trailing noise"])
    result = extract_result(stdout, NONCE)
    assert result is not None and result.reason is Reason.REJECTED


def test_extract_ignores_frames_with_the_wrong_nonce() -> None:
    forged = _line(Reason.ACCEPTED, "b" * 32)
    assert extract_result(forged, NONCE) is None


def test_extract_ignores_a_bare_sentinel_without_a_nonce() -> None:
    payload = json.dumps({"reason": "ACCEPTED"})
    assert extract_result(f"{SENTINEL} {payload}", NONCE) is None


def test_last_correctly_framed_line_wins() -> None:
    stdout = "\n".join([_line(Reason.ACCEPTED), _line(Reason.REJECTED)])
    result = extract_result(stdout, NONCE)
    assert result is not None and result.reason is Reason.REJECTED


def test_malformed_json_in_a_frame_is_skipped_not_trusted() -> None:
    stdout = "\n".join([_line(Reason.REJECTED), f"{SENTINEL}{NONCE} {{not json"])
    result = extract_result(stdout, NONCE)
    assert result is not None and result.reason is Reason.REJECTED


def _verdict(stdout: str = "", **kwargs: object) -> Verdict:
    defaults: dict[str, object] = dict(
        stdout=stdout,
        nonce=NONCE,
        seed=1,
        image_digest="d",
        host={},
        solution_bytes=3,
        wall_s=1.0,
        exit_code=0,
        timed_out=False,
    )
    defaults.update(kwargs)
    return verdict_from_outcome(**defaults)  # type: ignore[arg-type]


def test_a_forged_accepted_line_cannot_produce_acceptance() -> None:
    verdict = _verdict(_line(Reason.ACCEPTED, "deadbeef"))
    assert not verdict.accepted
    assert verdict.reason is Reason.CRASH


def test_missing_verdict_line_with_clean_exit_is_a_crash() -> None:
    assert _verdict("", exit_code=0).reason is Reason.CRASH


def test_outside_kill_outranks_whatever_the_sandbox_printed() -> None:
    verdict = _verdict(_line(Reason.ACCEPTED), timed_out=True)
    assert verdict.reason is Reason.TIMEOUT and not verdict.accepted


def test_oom_kill_outranks_the_printed_line() -> None:
    assert _verdict(_line(Reason.ACCEPTED), oom_killed=True).reason is Reason.OOM


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (137, Reason.OOM),
        (-9, Reason.CRASH),
        (1, Reason.CRASH),
        (125, Reason.INTERNAL_ERROR),
        (127, Reason.INTERNAL_ERROR),
        (None, Reason.CRASH),
    ],
)
def test_exit_code_mapping(exit_code: int | None, expected: Reason) -> None:
    assert _verdict("", exit_code=exit_code).reason is expected


def test_launch_failure_is_an_internal_error() -> None:
    assert _verdict("", launch_failed=True).reason is Reason.INTERNAL_ERROR


def test_honest_accept_line_is_honoured() -> None:
    verdict = _verdict(_line(Reason.ACCEPTED))
    assert verdict.accepted and verdict.reason is Reason.ACCEPTED
    assert verdict.used.cpu_s == 0.5 and verdict.used.mem_bytes == 100


def test_wall_time_always_comes_from_outside() -> None:
    assert _verdict(_line(Reason.ACCEPTED), wall_s=42.0).used.wall_s == 42.0


def test_detail_is_truncated() -> None:
    verdict = _verdict("", exit_code=1, detail="x" * 10_000)
    assert len(verdict.detail) <= 2000


def test_seeds_and_nonces_are_fresh_and_wide() -> None:
    assert len({new_nonce() for _ in range(50)}) == 50
    seeds = {new_seed() for _ in range(50)}
    assert len(seeds) == 50 and all(0 <= s < 2**64 for s in seeds)
