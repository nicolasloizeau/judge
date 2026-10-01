from __future__ import annotations

import pytest

from judge.lean.backend import LeanGvisorBackend, LeanLocalInsecureBackend, Toolchain
from judge.lean.image import DEFAULT_IMAGE_TAG, ImageError, image_exists


def _local_skip_reason() -> str | None:
    try:
        toolchain = Toolchain.resolve()
    except (ImageError, OSError, ValueError) as exc:
        return f"no Lake project checkout: {exc}"
    missing = toolchain.missing()
    if missing:
        return "Lean toolchain not ready: " + "; ".join(missing)
    return None


def _gvisor_skip_reason() -> str | None:
    backend = LeanGvisorBackend(DEFAULT_IMAGE_TAG)
    if not backend.available():
        return "Docker with the runsc runtime is not available on this host"
    if not image_exists(DEFAULT_IMAGE_TAG):
        return f"image {DEFAULT_IMAGE_TAG} is not built (run: judge-lean build-image)"
    return None


@pytest.fixture(scope="session")
def lean_local() -> LeanLocalInsecureBackend:
    reason = _local_skip_reason()
    if reason:
        pytest.skip(reason)
    return LeanLocalInsecureBackend(i_understand_this_is_insecure=True)


@pytest.fixture(scope="session")
def lean_gvisor() -> LeanGvisorBackend:
    reason = _gvisor_skip_reason()
    if reason:
        pytest.skip(reason)
    return LeanGvisorBackend(DEFAULT_IMAGE_TAG)
