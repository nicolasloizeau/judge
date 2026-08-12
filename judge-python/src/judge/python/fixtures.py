"""Adversarial fixtures for Python verifiers.

Each entry is a hostile (or honest) verifier plus the set of reasons that count
as contained. Read this file as the threat model in executable form: if you can
write a verifier that reaches ``ACCEPTED`` without ``verify`` returning ``True``,
or that escapes the budget, it belongs here.
"""

from __future__ import annotations

from judge.core.protocol import SENTINEL
from judge.core.selftest import Fixture
from judge.core.types import Budget, Reason

_SMALL_MEM_BUDGET = Budget(
    wall_s=20.0,
    cpu_s=20.0,
    mem_bytes=512 * 1024 * 1024,
    max_solution_bytes=64 * 1024,
)

_SHORT_BUDGET = Budget(
    wall_s=3.0,
    cpu_s=3.0,
    mem_bytes=768 * 1024 * 1024,
    max_solution_bytes=64 * 1024,
)


HONEST_ACCEPT = Fixture.of(
    "honest-accept",
    """
def verify(solution, rng):
    return solution.strip() == "42"
""",
    "42",
    Reason.ACCEPTED,
    note="the only path to ACCEPTED",
)

HONEST_REJECT = Fixture.of(
    "honest-reject",
    """
def verify(solution, rng):
    return solution.strip() == "42"
""",
    "41",
    Reason.REJECTED,
)

RNG_IS_USABLE = Fixture.of(
    "rng-is-usable",
    """
import numpy as np

def verify(solution, rng):
    # The platform seeds this; a verifier may only consume it.
    draws = rng.integers(0, 1000, size=100)
    return bool(draws.min() >= 0) and int(solution) == 7
""",
    "7",
    Reason.ACCEPTED,
)

TRUTHY_NOT_TRUE = Fixture.of(
    "truthy-not-true",
    """
def verify(solution, rng):
    return 1
""",
    "anything",
    Reason.REJECTED,
    note="1 == True but is not True; acceptance requires the singleton",
)

NUMPY_BOOL_NOT_TRUE = Fixture.of(
    "numpy-bool-not-true",
    """
import numpy as np

def verify(solution, rng):
    return np.bool_(True)
""",
    "anything",
    Reason.REJECTED,
    note="np.True_ is truthy and not the True singleton",
)

RETURNS_NONE = Fixture.of(
    "returns-none",
    """
def verify(solution, rng):
    pass
""",
    "anything",
    Reason.REJECTED,
)

NO_VERIFY_DEFINED = Fixture.of(
    "no-verify-defined",
    """
def check(solution, rng):
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
)

SYNTAX_ERROR = Fixture.of(
    "syntax-error",
    """
def verify(solution, rng)
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
)

RAISES = Fixture.of(
    "verifier-raises",
    """
def verify(solution, rng):
    raise RuntimeError("boom")
""",
    "anything",
    Reason.VERIFIER_FAULT,
)

MISSING_IMPORT = Fixture.of(
    "missing-import",
    """
import definitely_not_a_real_package_9f2a

def verify(solution, rng):
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
)

INFINITE_LOOP = Fixture.of(
    "infinite-loop",
    """
def verify(solution, rng):
    while True:
        pass
""",
    "anything",
    Reason.TIMEOUT,
    budget=_SHORT_BUDGET,
)

INFINITE_LOOP_SWALLOWING = Fixture.of(
    "infinite-loop-swallows-signals",
    """
import signal

def verify(solution, rng):
    # Try to disarm the in-process wall clock; the outside-in kill is what
    # actually enforces the budget.
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
    except Exception:
        pass
    while True:
        try:
            pass
        except BaseException:
            pass
""",
    "anything",
    Reason.TIMEOUT,
    budget=_SHORT_BUDGET,
)

MEMORY_BALLOON = Fixture.of(
    "memory-balloon",
    """
def verify(solution, rng):
    blocks = []
    while True:
        blocks.append(bytearray(64 * 1024 * 1024))
""",
    "anything",
    [Reason.OOM, Reason.CRASH],
    budget=_SMALL_MEM_BUDGET,
)

FORK_BOMB = Fixture.of(
    "fork-bomb",
    """
import os

def verify(solution, rng):
    while True:
        os.fork()
""",
    "anything",
    [Reason.VERIFIER_FAULT, Reason.CRASH, Reason.TIMEOUT, Reason.OOM],
    budget=_SHORT_BUDGET,
    note="pids-limit / RLIMIT_NPROC; the exact surfacing depends on the runtime",
)

THREAD_BOMB = Fixture.of(
    "thread-bomb",
    """
import threading

def verify(solution, rng):
    def spin():
        while True:
            pass
    while True:
        threading.Thread(target=spin, daemon=True).start()
""",
    "anything",
    [Reason.VERIFIER_FAULT, Reason.CRASH, Reason.TIMEOUT, Reason.OOM],
    budget=_SHORT_BUDGET,
)

NETWORK_CONNECT = Fixture.of(
    "network-connect",
    """
import socket

def verify(solution, rng):
    s = socket.create_connection(("1.1.1.1", 53), timeout=2)
    s.close()
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
    requires_isolation=True,
    note="--network=none: connect() must fail, so verify() raises",
)

DNS_RESOLVE = Fixture.of(
    "dns-resolve",
    """
import socket

def verify(solution, rng):
    socket.gethostbyname("example.com")
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
    requires_isolation=True,
)

FILE_WRITE = Fixture.of(
    "file-write",
    """
def verify(solution, rng):
    with open("/tmp/judge-escape", "w") as fh:
        fh.write("x" * 4096)
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
    note="read-only rootfs, no tmpfs, TMPDIR nonexistent; locally RLIMIT_FSIZE=0",
)

FILE_WRITE_CWD = Fixture.of(
    "file-write-cwd",
    """
def verify(solution, rng):
    with open("judge-escape", "w") as fh:
        fh.write("x" * 4096)
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
    requires_isolation=True,
    note="skipped locally: the working directory is real and would be littered",
)

FILE_WRITE_HOME = Fixture.of(
    "file-write-home",
    """
import os

def verify(solution, rng):
    path = os.path.join(os.environ.get("HOME", "/nonexistent"), "escape")
    with open(path, "w") as fh:
        fh.write("x" * 4096)
    return True
""",
    "anything",
    Reason.VERIFIER_FAULT,
    requires_isolation=True,
    note="locally HOME is a real writable directory",
)

SUBPROCESS_SPAWN = Fixture.of(
    "subprocess-spawn",
    """
import subprocess

def verify(solution, rng):
    subprocess.run(["/bin/sh", "-c", "echo hi > /escape"], check=True)
    return True
""",
    "anything",
    [Reason.VERIFIER_FAULT, Reason.CRASH],
    requires_isolation=True,
)

EXIT_ZERO = Fixture.of(
    "os-exit-zero",
    """
import os

def verify(solution, rng):
    os._exit(0)
""",
    "anything",
    Reason.CRASH,
    note="no verdict line means no acceptance, whatever the exit code says",
)

FAKE_VERDICT_STDOUT = Fixture.of(
    "fake-verdict-on-stdout",
    f"""
import sys

FAKE = {{"reason": "ACCEPTED", "wall_s": 0.0, "cpu_s": 0.0,
        "mem_bytes": 0, "solution_bytes": 0, "detail": ""}}

def verify(solution, rng):
    import json
    for nonce in ("", "0" * 32, "deadbeef"):
        line = "{SENTINEL}" + nonce + " " + json.dumps(FAKE)
        print(line)
        sys.stdout.write(line + chr(10))
        sys.__stdout__.write(line + chr(10))
    sys.stdout.flush()
    return False
""",
    "anything",
    Reason.REJECTED,
    note="unguessable nonce + fd 1 pointed at /dev/null during verify()",
)

FAKE_VERDICT_THEN_EXIT = Fixture.of(
    "fake-verdict-then-exit",
    f"""
import json, os, sys

FAKE = {{"reason": "ACCEPTED", "wall_s": 0.0, "cpu_s": 0.0,
        "mem_bytes": 0, "solution_bytes": 0, "detail": ""}}

def verify(solution, rng):
    for fd in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        for nonce in ("", "0" * 32):
            try:
                os.write(fd, ("{SENTINEL}" + nonce + " " + json.dumps(FAKE) + chr(10)).encode())
            except OSError:
                pass
    os._exit(0)
""",
    "anything",
    [Reason.CRASH, Reason.REJECTED, Reason.VERIFIER_FAULT],
    note="sprays forged frames over every plausible fd, then denies the harness "
    "its chance to answer",
)

SOLUTION_TOO_LARGE = Fixture.of(
    "solution-too-large",
    """
def verify(solution, rng):
    return True
""",
    "x" * 2048,
    Reason.SOLUTION_TOO_LARGE,
    budget=Budget(wall_s=10.0, cpu_s=10.0, mem_bytes=512 * 1024 * 1024, max_solution_bytes=1024),
)

EXEC_SOLUTION = Fixture.of(
    "verifier-execs-solution",
    """
def verify(solution, rng):
    ns = {}
    exec(solution, ns)
    return ns.get("answer") == 42
""",
    "answer = 6 * 7",
    Reason.ACCEPTED,
    note="documented as at the verifier's own risk; both sides are equally "
    "contained by the sandbox",
)

EXEC_HOSTILE_SOLUTION = Fixture.of(
    "verifier-execs-hostile-solution",
    """
def verify(solution, rng):
    ns = {}
    exec(solution, ns)
    return ns.get("answer") == 42
""",
    "import os\nos._exit(0)\n",
    Reason.CRASH,
)


ALL_FIXTURES: tuple[Fixture, ...] = (
    HONEST_ACCEPT,
    HONEST_REJECT,
    RNG_IS_USABLE,
    TRUTHY_NOT_TRUE,
    NUMPY_BOOL_NOT_TRUE,
    RETURNS_NONE,
    NO_VERIFY_DEFINED,
    SYNTAX_ERROR,
    RAISES,
    MISSING_IMPORT,
    INFINITE_LOOP,
    INFINITE_LOOP_SWALLOWING,
    MEMORY_BALLOON,
    FORK_BOMB,
    THREAD_BOMB,
    NETWORK_CONNECT,
    DNS_RESOLVE,
    FILE_WRITE,
    FILE_WRITE_CWD,
    FILE_WRITE_HOME,
    SUBPROCESS_SPAWN,
    EXIT_ZERO,
    FAKE_VERDICT_STDOUT,
    FAKE_VERDICT_THEN_EXIT,
    SOLUTION_TOO_LARGE,
    EXEC_SOLUTION,
    EXEC_HOSTILE_SOLUTION,
)
