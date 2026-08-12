"""In-sandbox harness for Python verifiers. This runs *inside* the container.

Life of a verification:

1. read one JSON request line from stdin, then close stdin;
2. enforce ``max_solution_bytes``;
3. apply rlimits (CPU, address space, file size, process count) and arm a
   wall-clock alarm -- a second line of defence behind the container limits and
   the backend's outside-in kill;
4. point fd 1 and fd 2 at ``/dev/null`` so the verifier physically cannot write
   to the real stdout;
5. ``exec`` the verifier source in a fresh module namespace and call
   ``verify(solution, rng)``;
6. restore the saved stdout, write exactly one framed result line, ``os._exit``.

Everything the verifier can do -- raising, exiting, ballooning, spinning,
printing forged verdicts, forking -- has to come out as a non-accepting reason.
The only path to ``ACCEPTED`` is ``verify`` returning the ``True`` singleton.
"""

from __future__ import annotations

import builtins
import contextlib
import os
import resource
import signal
import sys
import time
import traceback
import types
from dataclasses import dataclass
from typing import Any, NoReturn

import numpy as np

from judge.core.protocol import Request, Result, truncate_detail
from judge.core.types import Reason

VERIFIER_MODULE_NAME = "judge_verifier"
CPU_GRACE_S = 2
"""Gap between the RLIMIT_CPU soft limit (catchable) and the hard one (SIGKILL)."""


class _WallTimeout(BaseException):
    """Raised from SIGALRM. Inherits BaseException so ``except Exception`` misses it."""


class _CpuTimeout(BaseException):
    """Raised from SIGXCPU."""


@dataclass(frozen=True, slots=True)
class Outcome:
    reason: Reason
    detail: str = ""


def run_verifier(verifier_src: str, solution: str, rng: np.random.Generator) -> Outcome:
    """Execute one verifier and classify what happened.

    Pure: no I/O, no process state. Unit-testable without a sandbox.
    """
    module = types.ModuleType(VERIFIER_MODULE_NAME)
    module.__dict__["__builtins__"] = builtins
    module.__dict__["__name__"] = VERIFIER_MODULE_NAME

    try:
        code = compile(verifier_src, "<verifier>", "exec")
    except (SyntaxError, ValueError) as exc:
        return Outcome(Reason.VERIFIER_FAULT, f"could not compile verifier: {_exc_text(exc)}")

    try:
        exec(code, module.__dict__)  # executing untrusted code is the whole job
    except MemoryError as exc:
        return Outcome(Reason.OOM, f"verifier module exhausted memory: {_exc_text(exc)}")
    except (_WallTimeout, _CpuTimeout) as exc:
        return Outcome(Reason.TIMEOUT, str(exc))
    except BaseException as exc:  # untrusted code: catch absolutely everything
        return Outcome(Reason.VERIFIER_FAULT, f"verifier module raised: {_exc_text(exc)}")

    verify = module.__dict__.get("verify")
    if not callable(verify):
        return Outcome(Reason.VERIFIER_FAULT, "verifier module defines no callable verify()")

    try:
        result = verify(solution, rng)
    except MemoryError as exc:
        return Outcome(Reason.OOM, f"verify() exhausted memory: {_exc_text(exc)}")
    except (_WallTimeout, _CpuTimeout) as exc:
        return Outcome(Reason.TIMEOUT, str(exc))
    except BaseException as exc:  # untrusted code: catch absolutely everything
        return Outcome(Reason.VERIFIER_FAULT, f"verify() raised: {_exc_text(exc)}")

    # Exactly True. `1`, `numpy.True_`, a truthy object -- all non-acceptance.
    if result is True:
        return Outcome(Reason.ACCEPTED)
    return Outcome(Reason.REJECTED, f"verify() returned {_short_repr(result)}, not True")


def main(argv: list[str] | None = None) -> int:
    """Entrypoint of the sandbox image. Returns only on protocol failure."""
    del argv
    started = time.monotonic()

    raw = sys.stdin.read()
    try:
        request = Request.decode(raw)
    except (ValueError, KeyError, TypeError) as exc:
        # No nonce means no way to frame a trustworthy answer; let the backend
        # map the exit code instead.
        print(f"judge-harness: malformed request: {exc}", file=sys.stderr)
        return 2

    solution_bytes = len(request.solution.encode("utf-8", "surrogatepass"))
    saved_stdout = _detach_stdio()

    if solution_bytes > request.budget.max_solution_bytes:
        _emit_and_exit(
            saved_stdout,
            request,
            Outcome(
                Reason.SOLUTION_TOO_LARGE,
                f"{solution_bytes} > max_solution_bytes={request.budget.max_solution_bytes}",
            ),
            solution_bytes=solution_bytes,
            wall_s=time.monotonic() - started,
        )

    _apply_rlimits(request)
    _arm_timers(request.budget.wall_s)

    try:
        rng = np.random.default_rng(request.seed)
        outcome = run_verifier(request.verifier_src, request.solution, rng)
    except (_WallTimeout, _CpuTimeout) as exc:
        outcome = Outcome(Reason.TIMEOUT, str(exc))
    except MemoryError:
        outcome = Outcome(Reason.OOM, "harness exhausted memory")
    except BaseException as exc:  # never let the harness itself escape
        outcome = Outcome(Reason.INTERNAL_ERROR, f"harness error: {_exc_text(exc)}")
    finally:
        _disarm_timers()

    _emit_and_exit(
        saved_stdout,
        request,
        outcome,
        solution_bytes=solution_bytes,
        wall_s=time.monotonic() - started,
    )


# -- process plumbing --------------------------------------------------------


def _detach_stdio() -> int:
    """Move the real stdout out of reach and blind fds 0/1/2.

    Returns the fd the framed result must be written to. After this call the
    verifier's ``print`` goes to ``/dev/null`` -- including through any
    reference to ``sys.stdout`` it captured earlier, because the redirection
    happens at the fd level.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    os.set_inheritable(saved, False)
    null_r = os.open(os.devnull, os.O_RDONLY)
    null_w = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null_r, 0)
    os.dup2(null_w, 1)
    os.dup2(null_w, 2)
    os.close(null_r)
    os.close(null_w)
    return saved


def _apply_rlimits(request: Request) -> None:
    """Belt-and-braces limits inside the sandbox.

    The container already caps memory and pids; these make the *local* backend
    survivable and give clean, catchable failures (``MemoryError``, ``SIGXCPU``)
    instead of an opaque SIGKILL.
    """
    budget = request.budget
    _set_limit(resource.RLIMIT_CPU, int(budget.cpu_s) + 1, int(budget.cpu_s) + 1 + CPU_GRACE_S)
    _set_limit(resource.RLIMIT_AS, budget.mem_bytes, budget.mem_bytes)
    _set_limit(resource.RLIMIT_FSIZE, 0, 0)
    _set_limit(resource.RLIMIT_CORE, 0, 0)

    # RLIMIT_NPROC counts every task of this uid system-wide, so cap it relative
    # to what is already running rather than at an absolute number that a busy
    # development host would already exceed.
    nproc = _own_task_count() + max(request.pids_limit, 8)
    _set_limit(resource.RLIMIT_NPROC, nproc, nproc)

    # Writing past RLIMIT_FSIZE raises SIGXFSZ, whose default action is death;
    # ignoring it turns a file write into a clean OSError the verifier sees.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    signal.signal(signal.SIGXCPU, _on_sigxcpu)


def _set_limit(which: int, soft: int, hard: int) -> None:
    try:
        _, cur_hard = resource.getrlimit(which)
        if cur_hard != resource.RLIM_INFINITY:
            soft = min(soft, cur_hard)
            hard = min(hard, cur_hard)
        resource.setrlimit(which, (soft, hard))
    except (ValueError, OSError):  # pragma: no cover - platform dependent
        pass


def _own_task_count() -> int:
    """Number of tasks owned by this uid, best effort."""
    uid = os.getuid()
    count = 0
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - no procfs
        return 64
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid == uid:
                count += 1
        except OSError:
            continue
    return count


def _on_sigalrm(signum: int, frame: types.FrameType | None) -> NoReturn:
    del signum, frame
    raise _WallTimeout("wall-clock budget exhausted inside the sandbox")


def _on_sigxcpu(signum: int, frame: types.FrameType | None) -> NoReturn:
    del signum, frame
    raise _CpuTimeout("CPU budget exhausted inside the sandbox")


def _arm_timers(wall_s: float) -> None:
    signal.signal(signal.SIGALRM, _on_sigalrm)
    signal.setitimer(signal.ITIMER_REAL, max(wall_s, 0.001))


def _disarm_timers() -> None:
    with contextlib.suppress(OSError, ValueError):  # pragma: no cover - defensive
        signal.setitimer(signal.ITIMER_REAL, 0.0)


def _emit_and_exit(
    fd: int,
    request: Request,
    outcome: Outcome,
    *,
    solution_bytes: int,
    wall_s: float,
) -> NoReturn:
    """Write the one framed result line and leave immediately.

    Forked children never write: only the process that read the request may
    answer, so a fork bomb cannot race extra verdict lines onto stdout.
    """
    if os.getpid() != _MAIN_PID:
        os._exit(3)

    usage = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = Result(
        reason=outcome.reason,
        wall_s=wall_s,
        cpu_s=(usage.ru_utime + usage.ru_stime + children.ru_utime + children.ru_stime),
        mem_bytes=max(usage.ru_maxrss, children.ru_maxrss) * 1024,  # Linux: KiB
        solution_bytes=solution_bytes,
        detail=truncate_detail(outcome.detail),
    )
    payload = (result.encode_line(request.nonce) + "\n").encode("utf-8", "replace")
    try:
        _write_all(fd, payload)
    except OSError:  # pragma: no cover - stdout is gone; nothing left to do
        os._exit(4)
    os._exit(0)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _exc_text(exc: BaseException) -> str:
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return truncate_detail(text)


def _short_repr(value: Any, limit: int = 120) -> str:
    try:
        text = repr(value)
    except BaseException:  # __repr__ is untrusted code too
        return f"<unreprable {type(value).__name__}>"
    return text if len(text) <= limit else text[: limit - 3] + "..."


_MAIN_PID = os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
