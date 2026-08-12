from __future__ import annotations

import json

import pytest

from judge.core.types import Budget, BudgetError, Reason, Verdict

GOOD = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=1024, max_solution_bytes=16)


def test_budget_roundtrips_through_json() -> None:
    restored = Budget.from_dict(json.loads(json.dumps(GOOD.as_dict())))
    assert restored == GOOD


def test_budget_is_frozen() -> None:
    with pytest.raises(AttributeError):
        GOOD.wall_s = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wall_s": 0},
        {"wall_s": -1},
        {"cpu_s": 0},
        {"mem_bytes": 0},
        {"mem_bytes": 1.5},
        {"max_solution_bytes": -3},
        {"build_s": 0},
        {"build_mem": -1},
    ],
)
def test_validate_rejects_unenforceable_budgets(kwargs: dict[str, object]) -> None:
    from dataclasses import replace

    with pytest.raises(BudgetError):
        replace(GOOD, **kwargs).validate()  # type: ignore[arg-type]


def test_build_fields_are_optional_and_default_to_none() -> None:
    assert GOOD.build_s is None and GOOD.build_mem is None
    GOOD.validate()


def test_make_derives_accepted_from_reason() -> None:
    assert Verdict.make(Reason.ACCEPTED, used=GOOD, seed=1, image_digest="d").accepted
    for reason in Reason:
        if reason is Reason.ACCEPTED:
            continue
        assert not Verdict.make(reason, used=GOOD, seed=1, image_digest="d").accepted


def test_verdict_json_is_stable_and_complete() -> None:
    verdict = Verdict.make(
        Reason.TIMEOUT, used=GOOD, seed=7, image_digest="sha256:x", host={"backend": "t"}
    )
    payload = json.loads(verdict.to_json())
    assert payload == {
        "accepted": False,
        "reason": "TIMEOUT",
        "used": GOOD.as_dict(),
        "seed": 7,
        "image_digest": "sha256:x",
        "host": {"backend": "t"},
        "detail": "",
    }


def test_reason_values_are_the_documented_set() -> None:
    assert {r.value for r in Reason} == {
        "ACCEPTED",
        "REJECTED",
        "TIMEOUT",
        "OOM",
        "CRASH",
        "VERIFIER_FAULT",
        "BUILD_FAILED",
        "SOLUTION_TOO_LARGE",
        "INTERNAL_ERROR",
    }


def test_pretty_mentions_reason_and_seed() -> None:
    text = Verdict.make(Reason.OOM, used=GOOD, seed=42, image_digest="d").pretty()
    assert "OOM" in text and "42" in text and "NOT ACCEPTED" in text
