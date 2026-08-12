"""The backend interface every sandbox implementation satisfies."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from judge.core.types import Budget, Verdict


@runtime_checkable
class Backend(Protocol):
    """Runs an untrusted verifier against an untrusted solution.

    Implementations never raise for hostile input: every failure mode is folded
    into a non-accepting :class:`~judge.core.types.Verdict`. They may raise for
    *operator* errors (a malformed budget, a missing Docker binary).
    """

    name: str
    provides_isolation: bool
    """False for development backends that do not actually contain the code.

    The selftest harness uses this to skip fixtures whose expected outcome
    depends on real isolation (e.g. "the network is unreachable").
    """

    def run(self, verifier_src: str, solution: str, budget: Budget) -> Verdict: ...
