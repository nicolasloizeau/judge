"""Development-only backend: a plain subprocess with rlimits. **Not a sandbox.**

It gives you the same wire protocol and the same verdict mapping without Docker,
which makes it useful for iterating on a harness and for running the parts of
the selftest suite that do not depend on real isolation. It does not contain
untrusted code: there is a writable filesystem, a reachable network, and no
kernel-attack-surface reduction whatsoever. Never point it at hostile input.

:func:`run_process` is the single-process primitive underneath
:meth:`LocalInsecureBackend.run`; ``judge-lean``'s local backend composes it
for its three stages, exactly as the gVisor one composes
:meth:`~judge.core.backends.gvisor.GvisorBackend.run_container`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import resource
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

from judge.core.protocol import (
    RawOutcome,
    Request,
    new_nonce,
    new_seed,
    truncate_detail,
    verdict_from_outcome,
)
from judge.core.types import Budget, Reason, Verdict

log = logging.getLogger(__name__)

DEFAULT_GRACE_S = 5.0


@dataclass(frozen=True, slots=True)
class ProcessLimits:
    """POSIX rlimits applied to a child before it ``exec``s.

    ``address_space_bytes=None`` leaves ``RLIMIT_AS`` alone: toolchains that
    ``mmap`` large read-only artifacts (Lean with Mathlib) need far more
    address space than resident memory, and the memory cap for them comes from
    elsewhere.
    """

    cpu_s: float
    address_space_bytes: int | None
    fsize_bytes: int = 0
    cpu_grace_s: int = 2
    rss_limit_bytes: int | None = None
    """Kill the process group once its summed resident size exceeds this.

    Polled from outside (``/proc``), so it is coarse; it exists for toolchains
    whose address space cannot be capped with ``RLIMIT_AS`` at all, and it is
    reported as an OOM kill."""

    @classmethod
    def from_budget(cls, budget: Budget, *, fsize_bytes: int = 0) -> ProcessLimits:
        return cls(
            cpu_s=budget.cpu_s,
            address_space_bytes=budget.mem_bytes,
            fsize_bytes=fsize_bytes,
        )

    def preexec(self) -> Callable[[], None]:
        """The hook that applies these limits inside the forked child."""

        def apply() -> None:  # pragma: no cover - runs in the forked child
            cpu = int(self.cpu_s) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + self.cpu_grace_s))
            if self.address_space_bytes is not None:
                limit = self.address_space_bytes
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            resource.setrlimit(resource.RLIMIT_FSIZE, (self.fsize_bytes, self.fsize_bytes))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        return apply


def run_process(
    argv: Sequence[str],
    *,
    budget: Budget,
    stdin: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    limits: ProcessLimits | None = None,
    grace_s: float = DEFAULT_GRACE_S,
    wall_s: float | None = None,
) -> RawOutcome:
    """Run ``argv`` to completion as a child of this process, under ``budget``.

    The child gets its own session (so stray grandchildren can be killed as a
    group), ``limits`` as rlimits (defaulting to the budget's CPU and address
    space, and zero writable file bytes), and a hard kill at ``wall_s`` (or
    ``budget.wall_s``) plus ``grace_s``. The child's own rusage (``wait4``) is
    reported as ``cpu_s`` / ``mem_bytes``.
    """
    limits = ProcessLimits.from_budget(budget) if limits is None else limits
    wall = (budget.wall_s if wall_s is None else wall_s) + grace_s

    stdout = ""
    stderr = ""
    exit_code: int | None = None
    timed_out = False
    oom_killed = False
    launch_failed = False
    detail = ""
    cpu_s = 0.0
    mem_bytes = 0
    started = time.monotonic()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=None if env is None else dict(env),
            start_new_session=True,  # own process group, so we can kill strays
            preexec_fn=limits.preexec(),
        )
        stdout, stderr, status, usage, watchdog = _communicate_wait4(
            proc, stdin, wall, limits.rss_limit_bytes
        )
        exit_code = _exit_code(status)
        timed_out = watchdog.timed_out
        oom_killed = watchdog.oom_killed
        cpu_s = usage.ru_utime + usage.ru_stime
        mem_bytes = max(usage.ru_maxrss * 1024, watchdog.peak_rss)  # Linux reports KiB
        if timed_out:
            detail = "killed by wall-clock timeout"
        elif oom_killed:
            detail = f"killed: resident size exceeded {limits.rss_limit_bytes} bytes"
        elif exit_code != 0 and stderr:
            detail = truncate_detail(stderr)
    except (OSError, ValueError) as exc:
        launch_failed = True
        detail = f"failed to launch {list(argv)!r}: {exc}"
    finally:
        if proc is not None:
            _kill_group(proc)  # reap anything the child forked off
    wall_used = time.monotonic() - started

    return RawOutcome(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        wall_s=wall_used,
        timed_out=timed_out,
        oom_killed=oom_killed,
        launch_failed=launch_failed,
        detail=detail,
        cpu_s=cpu_s,
        mem_bytes=mem_bytes,
    )


class _Watchdog(threading.Thread):
    """Enforce the wall clock and (optionally) a resident-size cap from outside."""

    POLL_S = 0.1

    def __init__(self, proc: subprocess.Popen[str], wall: float, rss_limit: int | None) -> None:
        super().__init__(daemon=True)
        self.proc = proc
        self.deadline = time.monotonic() + wall
        self.rss_limit = rss_limit
        self.timed_out = False
        self.oom_killed = False
        self.peak_rss = 0
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.POLL_S):
            if time.monotonic() >= self.deadline:
                self.timed_out = True
                _kill_group(self.proc, wait=False)
                return
            if self.rss_limit is not None:
                rss = _session_rss(self.proc.pid)
                self.peak_rss = max(self.peak_rss, rss)
                if rss > self.rss_limit:
                    self.oom_killed = True
                    _kill_group(self.proc, wait=False)
                    return

    def stop(self) -> None:
        self._stop.set()


def _session_rss(sid: int) -> int:
    """Summed resident size of every process in session ``sid``, best effort."""
    total = 0
    page = os.sysconf("SC_PAGE_SIZE")
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - no procfs
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                stat = fh.read()
        except OSError:
            continue
        # Field 1 (comm) may contain spaces; everything after ')' is well-formed.
        fields = stat[stat.rfind(b")") + 2 :].split()
        # After comm: state(0) ppid(1) pgrp(2) session(3) ... rss(21)
        if len(fields) > 21 and int(fields[3]) == sid:
            total += int(fields[21]) * page
    return total


def _communicate_wait4(
    proc: subprocess.Popen[str], stdin: str | None, wall: float, rss_limit: int | None
) -> tuple[str, str, int, resource.struct_rusage, _Watchdog]:
    """Feed stdin, drain both pipes, and reap with ``wait4`` for rusage.

    ``Popen.communicate`` reaps with ``waitpid`` and discards the rusage, so
    the pipes are pumped by threads and the child is reaped here instead.
    """
    chunks: dict[str, str] = {}

    def pump(name: str, stream: IO[str]) -> None:
        try:
            chunks[name] = stream.read()
        except (OSError, ValueError):  # pragma: no cover - pipe torn down
            chunks[name] = ""
        finally:
            with contextlib.suppress(OSError):
                stream.close()

    if proc.stdout is None or proc.stderr is None or proc.stdin is None:
        raise ValueError("run_process needs all three standard streams piped")
    readers = [
        threading.Thread(target=pump, args=("out", proc.stdout), daemon=True),
        threading.Thread(target=pump, args=("err", proc.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    with contextlib.suppress(OSError, ValueError):  # child may exit before reading
        if stdin:
            proc.stdin.write(stdin)
        proc.stdin.close()

    watchdog = _Watchdog(proc, wall, rss_limit)
    watchdog.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    finally:
        watchdog.stop()
    proc.returncode = _exit_code(status)  # let Popen know it has been reaped
    watchdog.join(timeout=5.0)
    for reader in readers:
        reader.join(timeout=5.0)
    return chunks.get("out", ""), chunks.get("err", ""), status, usage, watchdog


def _exit_code(status: int) -> int:
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


def _kill_group(proc: subprocess.Popen[str], *, wait: bool = True) -> None:
    # Unconditional: the child may be gone while its forks are not.
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGKILL)  # start_new_session => pid == pgid
    if wait and proc.returncode is None:
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            proc.wait(timeout=5)


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
        raw = run_process(
            self.argv,
            budget=budget,
            stdin=request.encode() + "\n",
            env=self._child_env(),
            grace_s=self.grace_s,
        )
        return verdict_from_outcome(
            stdout=raw.stdout,
            nonce=nonce,
            seed=seed,
            image_digest=self._image_digest,
            host=host,
            solution_bytes=solution_bytes,
            wall_s=raw.wall_s,
            exit_code=raw.exit_code,
            timed_out=raw.timed_out,
            launch_failed=raw.launch_failed,
            detail=raw.detail,
        )

    def _child_env(self) -> dict[str, str]:
        env = dict(self.env) if self.env is not None else dict(os.environ)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env.setdefault(var, "1")
        return env
