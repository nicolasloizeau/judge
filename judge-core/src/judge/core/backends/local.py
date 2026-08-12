"""Development-only backend: a plain subprocess with rlimits. **Not a sandbox.**

It gives you the same wire protocol and the same verdict mapping without Docker,
which makes it useful for iterating on a harness and for running the parts of
the selftest suite that do not depend on real isolation. It does not contain
untrusted code: there is a writable filesystem, a reachable network, and no
kernel-attack-surface reduction whatsoever. Never point it at hostile input.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import resource
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from judge.core.protocol import Request, new_nonce, new_seed, truncate_detail, verdict_from_outcome
from judge.core.types import Budget, Reason, Verdict

log = logging.getLogger(__name__)

DEFAULT_GRACE_S = 5.0


class LocalInsecureBackend:
    """Run the harness as a child process of this one. Development only."""

    name = "local-insecure"
    provides_isolation = False

    def __init__(
        self,
        argv: Sequence[str],
        *,
        i_understand_this_is_insecure: bool = False,
        env: Mapping[str, str] | None = None,
        grace_s: float = DEFAULT_GRACE_S,
        image_digest: str = "local-insecure",
    ) -> None:
        if not i_understand_this_is_insecure:
            raise ValueError(
                "LocalInsecureBackend does not sandbox anything; pass "
                "i_understand_this_is_insecure=True to acknowledge that."
            )
        log.warning(
            "LocalInsecureBackend is in use: untrusted code runs unsandboxed as "
            "this user, with filesystem and network access. Development only."
        )
        self.argv = list(argv)
        self.env = dict(env) if env is not None else None
        self.grace_s = grace_s
        self._image_digest = image_digest

    def host_info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "runtime": "none (subprocess + setrlimit)",
            "argv": " ".join(self.argv),
            "kernel": platform.release(),
            "platform": platform.platform(),
        }

    def run(self, verifier_src: str, solution: str, budget: Budget) -> Verdict:
        budget.validate()
        seed = new_seed()
        nonce = new_nonce()
        host = self.host_info()
        solution_bytes = len(solution.encode("utf-8", "surrogatepass"))

        if solution_bytes > budget.max_solution_bytes:
            return Verdict.make(
                Reason.SOLUTION_TOO_LARGE,
                used=Budget(0.0, 0.0, 0, solution_bytes),
                seed=seed,
                image_digest=self._image_digest,
                host=host,
                detail=f"{solution_bytes} > max_solution_bytes={budget.max_solution_bytes}",
            )

        request = Request(
            nonce=nonce,
            seed=seed,
            verifier_src=verifier_src,
            solution=solution,
            budget=budget,
        )

        stdout = ""
        exit_code: int | None = None
        timed_out = False
        launch_failed = False
        detail = ""
        started = time.monotonic()
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._child_env(),
                start_new_session=True,  # own process group, so we can kill strays
                preexec_fn=_rlimit_setter(budget),
            )
            try:
                stdout, stderr = proc.communicate(
                    request.encode() + "\n", timeout=budget.wall_s + self.grace_s
                )
                exit_code = proc.returncode
                if exit_code != 0 and stderr:
                    detail = truncate_detail(stderr)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_group(proc)
                stdout, _ = proc.communicate()
        except (OSError, ValueError) as exc:
            launch_failed = True
            detail = f"failed to launch harness {self.argv!r}: {exc}"
        finally:
            if proc is not None:
                self._kill_group(proc)  # reap anything the child forked off
        wall_s = time.monotonic() - started

        return verdict_from_outcome(
            stdout=stdout,
            nonce=nonce,
            seed=seed,
            image_digest=self._image_digest,
            host=host,
            solution_bytes=solution_bytes,
            wall_s=wall_s,
            exit_code=exit_code,
            timed_out=timed_out,
            launch_failed=launch_failed,
            detail=detail,
        )

    def _child_env(self) -> dict[str, str]:
        env = dict(self.env) if self.env is not None else dict(os.environ)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env.setdefault(var, "1")
        return env

    @staticmethod
    def _kill_group(proc: subprocess.Popen[str]) -> None:
        # Unconditional: the child may be gone while its forks are not.
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)  # start_new_session => pid == pgid
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def _rlimit_setter(budget: Budget) -> Callable[[], None]:
    """Build the preexec hook applying ``budget`` as POSIX rlimits.

    Same limits the in-sandbox harness sets for itself; applying them here too
    means they hold even for a harness that never gets far enough to read its
    request.
    """

    def apply() -> None:  # pragma: no cover - runs in the forked child
        cpu = int(budget.cpu_s) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
        resource.setrlimit(resource.RLIMIT_AS, (budget.mem_bytes, budget.mem_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply
