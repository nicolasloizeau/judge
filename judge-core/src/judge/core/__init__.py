"""``judge.core`` -- language-agnostic sandboxed verification.

Stdlib only. Language packages (``judge.python`` and future siblings) supply an
image, an in-sandbox harness and a fixture set; everything about *policy*,
*verdicts* and *containment* lives here.
"""

from __future__ import annotations

from judge.core.backend import Backend
from judge.core.backends.gvisor import GvisorBackend
from judge.core.backends.local import LocalInsecureBackend
from judge.core.policy import DEFAULT_POLICY, SandboxPolicy, describe_policy, docker_run_args
from judge.core.protocol import (
    SENTINEL,
    Request,
    Result,
    extract_result,
    new_nonce,
    new_seed,
    verdict_from_outcome,
)
from judge.core.selftest import (
    DEFAULT_BUDGET,
    CaseResult,
    Fixture,
    SelftestReport,
    run_selftest,
)
from judge.core.types import Budget, BudgetError, Reason, Verdict

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_POLICY",
    "SENTINEL",
    "Backend",
    "Budget",
    "BudgetError",
    "CaseResult",
    "Fixture",
    "GvisorBackend",
    "LocalInsecureBackend",
    "Reason",
    "Request",
    "Result",
    "SandboxPolicy",
    "SelftestReport",
    "Verdict",
    "__version__",
    "describe_policy",
    "docker_run_args",
    "extract_result",
    "new_nonce",
    "new_seed",
    "run_selftest",
    "verdict_from_outcome",
]
