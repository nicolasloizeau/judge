"""Core value types: budgets, reasons, verdicts.

Pure stdlib. Everything here is JSON-round-trippable so the same types can cross
the sandbox boundary (harness -> backend) unchanged.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Reason(StrEnum):
    """Why a verification ended the way it did.

    ``ACCEPTED`` is the only accepting outcome. Everything else is a
    non-acceptance; the distinctions exist for diagnostics, not for policy.
    """

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    CRASH = "CRASH"
    VERIFIER_FAULT = "VERIFIER_FAULT"
    BUILD_FAILED = "BUILD_FAILED"
    SOLUTION_TOO_LARGE = "SOLUTION_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class BudgetError(ValueError):
    """Raised when a caller supplies a budget that cannot be enforced."""


@dataclass(frozen=True, slots=True)
class Budget:
    """A blunt resource envelope for one verification.

    The same type is reused for *measured* usage in :attr:`Verdict.used`; in that
    role ``max_solution_bytes`` carries the actual solution size and the
    ``build_*`` fields stay ``None``.

    ``build_s`` / ``build_mem`` are reserved for future compiled-language
    siblings (which must build the solution before running it) and are unused by
    ``judge-python``.
    """

    wall_s: float
    cpu_s: float
    mem_bytes: int
    max_solution_bytes: int
    build_s: float | None = None
    build_mem: int | None = None

    def validate(self) -> None:
        """Reject budgets that are not enforceable. Not called on ``used``."""
        for name in ("wall_s", "cpu_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value <= 0:
                raise BudgetError(f"{name} must be a positive number, got {value!r}")
        for name in ("mem_bytes", "max_solution_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise BudgetError(f"{name} must be a positive int, got {value!r}")
        for name in ("build_s", "build_mem"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise BudgetError(f"{name} must be positive or None, got {value!r}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        return cls(
            wall_s=float(data["wall_s"]),
            cpu_s=float(data["cpu_s"]),
            mem_bytes=int(data["mem_bytes"]),
            max_solution_bytes=int(data["max_solution_bytes"]),
            build_s=None if data.get("build_s") is None else float(data["build_s"]),
            build_mem=None if data.get("build_mem") is None else int(data["build_mem"]),
        )


@dataclass(frozen=True, slots=True)
class Verdict:
    """The structured result of one verification.

    ``accepted`` is redundant with ``reason == Reason.ACCEPTED`` by construction:
    backends derive it rather than trusting anything that came out of the
    sandbox.

    ``detail`` is a short human-readable diagnostic (exception text, exit code,
    ...). It is advisory only and always derived from untrusted output, so never
    branch on it.
    """

    accepted: bool
    reason: Reason
    used: Budget
    seed: int
    image_digest: str
    host: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @classmethod
    def make(
        cls,
        reason: Reason,
        *,
        used: Budget,
        seed: int,
        image_digest: str,
        host: dict[str, Any] | None = None,
        detail: str = "",
    ) -> Verdict:
        """Build a verdict with ``accepted`` derived from ``reason``."""
        return cls(
            accepted=reason is Reason.ACCEPTED,
            reason=reason,
            used=used,
            seed=seed,
            image_digest=image_digest,
            host=dict(host or {}),
            detail=detail,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason.value,
            "used": self.used.as_dict(),
            "seed": self.seed,
            "image_digest": self.image_digest,
            "host": self.host,
            "detail": self.detail,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)

    def pretty(self) -> str:
        """Multi-line human-readable rendering for the CLI."""
        mark = "ACCEPTED" if self.accepted else "NOT ACCEPTED"
        lines = [
            f"{mark}  ({self.reason.value})",
            f"  seed          : {self.seed}",
            f"  image_digest  : {self.image_digest or '-'}",
            f"  wall_s        : {self.used.wall_s:.3f}",
            f"  cpu_s         : {self.used.cpu_s:.3f}",
            f"  mem_bytes     : {self.used.mem_bytes}",
            f"  solution_bytes: {self.used.max_solution_bytes}",
        ]
        if self.detail:
            lines.append(f"  detail        : {self.detail}")
        for key in sorted(self.host):
            lines.append(f"  host.{key:<9}: {self.host[key]}")
        return "\n".join(lines)
