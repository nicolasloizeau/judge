"""``judge.python`` -- Python-language support for :mod:`judge.core`.

Thin by design: an image, an in-sandbox harness, an adversarial fixture set and
a CLI. Anything language-agnostic belongs in ``judge.core``.

    from judge.python import run, default_backend
    verdict = run(verifier_src, solution, budget)
"""

from __future__ import annotations

import sys

from judge.core.backend import Backend
from judge.core.backends.gvisor import GvisorBackend
from judge.core.backends.local import LocalInsecureBackend
from judge.core.types import Budget, Verdict
from judge.python.fixtures import ALL_FIXTURES
from judge.python.image import DEFAULT_IMAGE_TAG, build_image, image_digest

__version__ = "0.1.0"

HARNESS_ARGV: tuple[str, ...] = (sys.executable, "-I", "-B", "-m", "judge.python.harness")
"""How to start the harness out-of-process on this interpreter."""


def gvisor_backend(image: str = DEFAULT_IMAGE_TAG, **kwargs: object) -> GvisorBackend:
    """The production backend: the pinned image under gVisor."""
    return GvisorBackend(image, **kwargs)  # type: ignore[arg-type]


def local_backend(**kwargs: object) -> LocalInsecureBackend:
    """The development backend. Does **not** sandbox anything."""
    kwargs.setdefault("i_understand_this_is_insecure", True)
    return LocalInsecureBackend(list(HARNESS_ARGV), **kwargs)  # type: ignore[arg-type]


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
    """Run ``verifier_src`` against ``solution`` under ``budget``.

    ``ACCEPTED`` iff the verifier's ``verify(solution, rng)`` returns exactly
    ``True`` within budget. Never raises for hostile input.
    """
    return (backend or default_backend()).run(verifier_src, solution, budget)


__all__ = [
    "ALL_FIXTURES",
    "DEFAULT_IMAGE_TAG",
    "HARNESS_ARGV",
    "__version__",
    "build_image",
    "default_backend",
    "gvisor_backend",
    "image_digest",
    "local_backend",
    "run",
]
