"""In-process tests of the harness' classification logic. No Docker needed."""

from __future__ import annotations

import numpy as np
import pytest

from judge.core.types import Reason
from judge.python.harness import run_verifier

RNG = np.random.default_rng(0)


def _reason(src: str, solution: str = "s") -> Reason:
    return run_verifier(src, solution, RNG).reason


def test_true_is_the_only_acceptance() -> None:
    assert _reason("def verify(s, rng): return True") is Reason.ACCEPTED


@pytest.mark.parametrize(
    "expr",
    ["1", "'yes'", "[1]", "None", "False", "np.bool_(True)", "object()"],
)
def test_truthy_is_not_acceptance(expr: str) -> None:
    src = f"import numpy as np\ndef verify(s, rng): return {expr}"
    assert _reason(src) is Reason.REJECTED


def test_solution_and_rng_are_passed_through() -> None:
    src = (
        "def verify(s, rng):\n    return s == 'abc' and rng.integers(0, 10, size=3).shape == (3,)\n"
    )
    assert run_verifier(src, "abc", np.random.default_rng(1)).reason is Reason.ACCEPTED


def test_same_seed_gives_the_same_draws() -> None:
    src = "def verify(s, rng): return int(rng.integers(0, 2**31)) == int(s)"
    first = np.random.default_rng(12345).integers(0, 2**31)
    assert run_verifier(src, str(first), np.random.default_rng(12345)).reason is Reason.ACCEPTED


def test_syntax_error_is_a_verifier_fault() -> None:
    assert _reason("def verify(s, rng)\n  return True") is Reason.VERIFIER_FAULT


def test_missing_verify_is_a_verifier_fault() -> None:
    assert _reason("x = 1") is Reason.VERIFIER_FAULT


def test_non_callable_verify_is_a_verifier_fault() -> None:
    assert _reason("verify = 3") is Reason.VERIFIER_FAULT


def test_module_level_exception_is_a_verifier_fault() -> None:
    outcome = run_verifier("raise ValueError('nope')", "s", RNG)
    assert outcome.reason is Reason.VERIFIER_FAULT
    assert "nope" in outcome.detail


def test_missing_import_is_a_verifier_fault() -> None:
    src = "import not_a_real_module_zzz\ndef verify(s, rng): return True"
    assert _reason(src) is Reason.VERIFIER_FAULT


def test_exception_inside_verify_is_a_verifier_fault() -> None:
    outcome = run_verifier("def verify(s, rng): raise RuntimeError('boom')", "s", RNG)
    assert outcome.reason is Reason.VERIFIER_FAULT
    assert "boom" in outcome.detail


def test_wrong_signature_is_a_verifier_fault() -> None:
    assert _reason("def verify(): return True") is Reason.VERIFIER_FAULT


def test_memory_error_maps_to_oom_not_to_a_fault() -> None:
    src = "def verify(s, rng): raise MemoryError()"
    assert _reason(src) is Reason.OOM


def test_systemexit_does_not_escape_the_harness() -> None:
    assert _reason("def verify(s, rng): raise SystemExit(0)") is Reason.VERIFIER_FAULT


def test_keyboardinterrupt_does_not_escape_the_harness() -> None:
    assert _reason("def verify(s, rng): raise KeyboardInterrupt()") is Reason.VERIFIER_FAULT


def test_unreprable_return_value_still_rejects() -> None:
    src = (
        "class Evil:\n"
        "    def __repr__(self): raise RuntimeError('no repr for you')\n"
        "def verify(s, rng): return Evil()\n"
    )
    outcome = run_verifier(src, "s", RNG)
    assert outcome.reason is Reason.REJECTED
    assert "unreprable" in outcome.detail


def test_verifier_gets_a_fresh_namespace_each_run() -> None:
    src = (
        "import sys\n"
        "seen = getattr(sys, '_judge_marker', 0)\n"
        "sys._judge_marker = seen + 1\n"
        "def verify(s, rng): return seen == 0\n"
    )
    assert _reason(src) is Reason.ACCEPTED
    # The module namespace is new even though sys is shared process state; a
    # verifier can only observe that by poking at the interpreter on purpose.
    assert _reason(src) is Reason.REJECTED
