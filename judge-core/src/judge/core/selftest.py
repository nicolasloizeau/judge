"""Adversarial selftest harness.

This suite *is* the security claim: a list of hostile verifiers, each with the
set of reasons that count as "contained". It is language-agnostic -- the
fixtures come from a language package (``judge.python.fixtures`` and, later,
siblings) and are executed against any :class:`~judge.core.backend.Backend`.

A fixture passes when the observed reason is in ``expected``. Several fixtures
allow more than one reason on purpose: a fork bomb may surface as the verifier
seeing ``OSError`` (``VERIFIER_FAULT``) or as the process dying
(``CRASH``), and which one you get depends on the runtime. What must never
happen -- and what the harness always checks separately -- is an unexpected
``ACCEPTED``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from judge.core.backend import Backend
from judge.core.types import Budget, Reason, Verdict

DEFAULT_BUDGET = Budget(
    wall_s=10.0,
    cpu_s=10.0,
    mem_bytes=768 * 1024 * 1024,
    max_solution_bytes=64 * 1024,
)


@dataclass(frozen=True, slots=True)
class Fixture:
    """One adversarial case."""

    name: str
    verifier_src: str
    solution: str
    expected: frozenset[Reason]
    requires_isolation: bool = False
    """Skip on backends that do not actually isolate (network, filesystem)."""
    budget: Budget | None = None
    note: str = ""

    @classmethod
    def of(
        cls,
        name: str,
        verifier_src: str,
        solution: str,
        expected: Reason | Iterable[Reason],
        *,
        requires_isolation: bool = False,
        budget: Budget | None = None,
        note: str = "",
    ) -> Fixture:
        reasons = frozenset([expected] if isinstance(expected, Reason) else expected)
        if not reasons:
            raise ValueError(f"fixture {name!r} has no expected reasons")
        return cls(
            name=name,
            verifier_src=verifier_src.strip() + "\n",
            solution=solution,
            expected=reasons,
            requires_isolation=requires_isolation,
            budget=budget,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class CaseResult:
    fixture: Fixture
    verdict: Verdict | None
    elapsed_s: float
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        if self.skipped:
            return True
        return self.verdict is not None and self.verdict.reason in self.fixture.expected

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"

    def describe(self) -> str:
        if self.skipped or self.verdict is None:
            return f"SKIP  {self.fixture.name:<28} ({self.skip_reason})"
        want = "|".join(sorted(r.value for r in self.fixture.expected))
        line = (
            f"{self.status}  {self.fixture.name:<28} "
            f"got={self.verdict.reason.value:<18} want={want}  {self.elapsed_s:5.2f}s"
        )
        if self.verdict.detail:
            line += f"\n        detail: {self.verdict.detail}"
        return line


@dataclass(frozen=True, slots=True)
class SelftestReport:
    backend_name: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed and not r.skipped)

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)

    @property
    def false_accepts(self) -> list[CaseResult]:
        """Fixtures that were accepted when acceptance was not expected.

        Tracked separately because this is the only failure class that is a
        soundness bug rather than a diagnostics mismatch.
        """
        return [
            r
            for r in self.results
            if not r.skipped
            and r.verdict is not None
            and r.verdict.accepted
            and Reason.ACCEPTED not in r.fixture.expected
        ]

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        lines = [f"judge selftest -- backend: {self.backend_name}", ""]
        lines += [r.describe() for r in self.results]
        lines += [
            "",
            f"{self.passed} passed, {len(self.failed)} failed, {self.skipped} skipped",
        ]
        if self.false_accepts:
            names = ", ".join(r.fixture.name for r in self.false_accepts)
            lines.append(f"!! FALSE ACCEPT in: {names}")
        return "\n".join(lines)


def run_selftest(
    backend: Backend,
    fixtures: Sequence[Fixture],
    *,
    budget: Budget = DEFAULT_BUDGET,
    on_result: Callable[[CaseResult], None] | None = None,
) -> SelftestReport:
    """Execute every fixture against ``backend`` and collect the results.

    ``on_result``, if given, is called with each :class:`CaseResult` as soon as
    it lands so callers can stream progress.
    """
    results: list[CaseResult] = []
    for fixture in fixtures:
        if fixture.requires_isolation and not backend.provides_isolation:
            result = CaseResult(
                fixture=fixture,
                verdict=None,
                elapsed_s=0.0,
                skipped=True,
                skip_reason=f"{backend.name} provides no isolation",
            )
        else:
            started = time.monotonic()
            verdict = backend.run(fixture.verifier_src, fixture.solution, fixture.budget or budget)
            result = CaseResult(
                fixture=fixture, verdict=verdict, elapsed_s=time.monotonic() - started
            )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return SelftestReport(backend_name=backend.name, results=results)
