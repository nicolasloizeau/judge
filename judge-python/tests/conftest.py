from __future__ import annotations

import pytest

from judge.core.backends.gvisor import GvisorBackend
from judge.python.image import DEFAULT_IMAGE_TAG, image_exists


def _gvisor_skip_reason() -> str | None:
    backend = GvisorBackend(DEFAULT_IMAGE_TAG)
    if not backend.available():
        return "Docker with the runsc runtime is not available on this host"
    if not image_exists(DEFAULT_IMAGE_TAG):
        return f"image {DEFAULT_IMAGE_TAG} is not built (run: judge build-image)"
    return None


@pytest.fixture(scope="session")
def gvisor_backend() -> GvisorBackend:
    reason = _gvisor_skip_reason()
    if reason:
        pytest.skip(reason)
    return GvisorBackend(DEFAULT_IMAGE_TAG)
