"""``judge.lean`` -- Lean-language support for :mod:`judge.core`.

The verifier is a Lean module defining a type ``Spec``; the solution is a Lean
module defining ``answer``; the checker is the Lean kernel. ``ACCEPTED`` iff
the kernel accepts ``answer : Spec`` and the proof depends only on allowlisted
axioms -- decided in a separate, trusted process after both untrusted modules
have been replayed through the kernel.

    from judge.lean import run
    verdict = run(verifier_src, solution, budget)

There is no randomness: ``Verdict.seed`` is always ``0`` and a verdict is
reproducible from ``(verifier_src, solution, budget, image_digest)``.
"""

from __future__ import annotations

from pathlib import Path

from judge.core.backend import Backend
from judge.core.types import Budget, Verdict
from judge.lean.backend import (
    DEFAULT_FSIZE_LIMIT,
    DEFAULT_MAX_HEARTBEATS,
    SEED_UNUSED,
    LeanGvisorBackend,
    LeanLocalInsecureBackend,
    lean_policy,
)
from judge.lean.fixtures import ALL_FIXTURES, SELFTEST_BUDGET
from judge.lean.header import DEFAULT_ALLOW, parse_allow_header
from judge.lean.image import DEFAULT_IMAGE_TAG, build_image, image_digest, project_dir

__version__ = "0.1.0"

MIB = 1024 * 1024

DEFAULT_BUDGET = Budget(
    wall_s=300.0,
    cpu_s=300.0,
    mem_bytes=8192 * MIB,
    max_solution_bytes=256 * 1024,
)
"""Applied to *each stage* independently. Sized for ``import Mathlib``: a
full import maps about 6.5 GB of oleans into resident memory and takes some
seconds cold; a core-only verification needs a small fraction of this."""


def gvisor_backend(image: str = DEFAULT_IMAGE_TAG, **kwargs: object) -> LeanGvisorBackend:
    """The production backend: three containers of the pinned image under gVisor."""
    return LeanGvisorBackend(image, **kwargs)  # type: ignore[arg-type]


def local_backend(project: Path | None = None, **kwargs: object) -> LeanLocalInsecureBackend:
    """The development backend. Does **not** sandbox anything.

    Needs the pinned toolchain in ``~/.elan`` and the Lake project built
    (``lake exe cache get && lake build judge-lean-check`` in
    ``judge-lean/image/project``).
    """
    kwargs.setdefault("i_understand_this_is_insecure", True)
    return LeanLocalInsecureBackend(project, **kwargs)  # type: ignore[arg-type]


def default_backend() -> Backend:
    """gVisor. There is deliberately no automatic fallback to the local backend."""
    return gvisor_backend()


def run(
    verifier_src: str,
    solution: str,
    budget: Budget,
    *,
    backend: Backend | None = None,
) -> Verdict:
    """Run ``verifier_src`` against ``solution`` under ``budget`` (per stage).

    ``ACCEPTED`` iff the kernel accepts ``answer : Spec`` within the allowlist.
    Never raises for hostile input.
    """
    return (backend or default_backend()).run(verifier_src, solution, budget)


__all__ = [
    "ALL_FIXTURES",
    "DEFAULT_ALLOW",
    "DEFAULT_BUDGET",
    "DEFAULT_FSIZE_LIMIT",
    "DEFAULT_IMAGE_TAG",
    "DEFAULT_MAX_HEARTBEATS",
    "SEED_UNUSED",
    "SELFTEST_BUDGET",
    "LeanGvisorBackend",
    "LeanLocalInsecureBackend",
    "__version__",
    "build_image",
    "default_backend",
    "gvisor_backend",
    "image_digest",
    "lean_policy",
    "local_backend",
    "parse_allow_header",
    "project_dir",
    "run",
]
