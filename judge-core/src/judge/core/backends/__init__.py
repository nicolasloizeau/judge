"""Backend implementations. See :mod:`judge.core.backend` for the interface."""

from __future__ import annotations

from judge.core.backends.gvisor import GvisorBackend
from judge.core.backends.local import LocalInsecureBackend

__all__ = ["GvisorBackend", "LocalInsecureBackend"]
