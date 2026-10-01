"""Wire protocol between a backend (outside) and a harness (inside the sandbox).

The backend writes one JSON *request* line to the harness' stdin and reads the
harness' *result* line back from stdout.

Trust model
-----------
The verifier runs in the same process as the harness, so it can print whatever
it likes on stdout. The result line is therefore framed with

    ``@@JUDGE-VERDICT@@<nonce> {json}``

where ``nonce`` is a fresh CSPRNG token the backend generated for this run and
sent in over stdin. A verifier that prints a plausible-looking verdict line
cannot guess the nonce, and even if it could, backends take the *last* matching
line, and the harness writes its line as the very last thing it does before
``os._exit``. On top of that the harness points fd 1 at ``/dev/null`` for the
whole duration of verifier execution, so in practice a verifier cannot write to
the real stdout at all.

None of this is the security boundary -- the sandbox is. It defends against a
verifier forging an ``ACCEPTED``, not against arbitrary code execution.

The same framing is reused by ``judge-lean``'s trusted checker, whose payload is
not a :class:`Result` but a small checker report; :func:`extract_frame` is the
payload-agnostic half that both share.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from judge.core.types import Budget, Reason, Verdict

SENTINEL = "@@JUDGE-VERDICT@@"
"""Distinctive prefix of the framed result line."""

PROTOCOL_VERSION = 1

MAX_DETAIL_CHARS = 2000
"""Diagnostics come from untrusted code; keep them short."""

SIGXCPU = 24
SIGKILL = 9


def new_nonce() -> str:
    """A fresh unguessable frame token for one verification."""
    return secrets.token_hex(16)


def new_seed() -> int:
    """CSPRNG-drawn RNG seed, recorded in the verdict for reproducibility."""
    return secrets.randbits(64)


def truncate_detail(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass(frozen=True, slots=True)
class Request:
    """What the harness receives on stdin."""

    nonce: str
    seed: int
    verifier_src: str
    solution: str
    budget: Budget
    pids_limit: int = 64
    version: int = PROTOCOL_VERSION

    def encode(self) -> str:
        payload = {
            "version": self.version,
            "nonce": self.nonce,
            "seed": self.seed,
            "verifier_src": self.verifier_src,
            "solution": self.solution,
            "budget": self.budget.as_dict(),
            "pids_limit": self.pids_limit,
        }
        return json.dumps(payload)

    @classmethod
    def decode(cls, raw: str) -> Request:
        data = json.loads(raw)
        version = int(data.get("version", PROTOCOL_VERSION))
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {version}")
        return cls(
            nonce=str(data["nonce"]),
            seed=int(data["seed"]),
            verifier_src=str(data["verifier_src"]),
            solution=str(data["solution"]),
            budget=Budget.from_dict(data["budget"]),
            pids_limit=int(data.get("pids_limit", 64)),
            version=version,
        )


@dataclass(frozen=True, slots=True)
class Result:
    """What the harness reports back: a reason plus measured usage."""

    reason: Reason
    wall_s: float
    cpu_s: float
    mem_bytes: int
    solution_bytes: int
    detail: str = ""

    def encode_line(self, nonce: str) -> str:
        payload = {
            "reason": self.reason.value,
            "wall_s": round(self.wall_s, 6),
            "cpu_s": round(self.cpu_s, 6),
            "mem_bytes": self.mem_bytes,
            "solution_bytes": self.solution_bytes,
            "detail": truncate_detail(self.detail),
        }
        return f"{SENTINEL}{nonce} {json.dumps(payload)}"

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Result:
        return cls(
            reason=Reason(str(data["reason"])),
            wall_s=float(data.get("wall_s", 0.0)),
            cpu_s=float(data.get("cpu_s", 0.0)),
            mem_bytes=int(data.get("mem_bytes", 0)),
            solution_bytes=int(data.get("solution_bytes", 0)),
            detail=truncate_detail(str(data.get("detail", ""))),
        )


def frame_line(nonce: str, payload: dict[str, Any]) -> str:
    """Frame an arbitrary JSON object the way a harness or checker does."""
    return f"{SENTINEL}{nonce} {json.dumps(payload)}"


def extract_frame(stdout: str, nonce: str) -> dict[str, Any] | None:
    """Return the last correctly framed JSON object in ``stdout``, or ``None``.

    Payload-agnostic: callers decide what the object has to contain. A frame
    whose JSON does not parse, or is not an object, is skipped and the search
    continues further back.
    """
    prefix = f"{SENTINEL}{nonce} "
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix) :])
        except ValueError:
            continue  # malformed frame: keep looking further back
        if isinstance(payload, dict):
            return payload
    return None


def extract_result(stdout: str, nonce: str) -> Result | None:
    """Return the harness' result from ``stdout``, or ``None`` if absent.

    Last correctly framed line wins: the harness writes its line last and exits
    immediately afterwards.
    """
    prefix = f"{SENTINEL}{nonce} "
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix) :])
            if not isinstance(payload, dict):
                continue
            return Result.from_payload(payload)
        except (ValueError, KeyError):
            continue  # malformed frame: keep looking further back
    return None


@dataclass(frozen=True, slots=True)
class RawOutcome:
    """What one sandboxed process did, before any interpretation.

    Produced by :meth:`judge.core.backends.gvisor.GvisorBackend.run_container`
    and :func:`judge.core.backends.local.run_process`; consumed either by
    :func:`verdict_from_outcome` (the single-process harness protocol) or by a
    multi-stage language backend that maps each stage itself.

    ``cpu_s`` and ``mem_bytes`` are best-effort measurements of the process
    (``wait4`` rusage locally; whatever the container reports otherwise) and
    are ``0`` when unknown. ``wall_s`` is always measured from outside.
    """

    stdout: str
    stderr: str
    exit_code: int | None
    wall_s: float
    timed_out: bool = False
    oom_killed: bool = False
    launch_failed: bool = False
    detail: str = ""
    cpu_s: float = 0.0
    mem_bytes: int = 0

    @property
    def signal(self) -> int | None:
        """The signal that killed the process, if the exit status says so."""
        if self.exit_code is None:
            return None
        if self.exit_code < 0:
            return -self.exit_code
        if self.exit_code > 128:
            return self.exit_code - 128  # shell / docker convention
        return None

    def limit_reason(self) -> tuple[Reason, str] | None:
        """The reason a *resource limit or the host* explains this outcome.

        ``None`` means the process ran to completion on its own terms and the
        caller must interpret its exit code and output. This is the one place
        that knows "137 means the memory cap" and "SIGXCPU means the CPU
        budget", so every backend and every stage agrees.
        """
        if self.launch_failed:
            return Reason.INTERNAL_ERROR, self.detail or "failed to launch sandbox"
        # The outside-in kill is authoritative: anything inside can swallow its
        # own timers, so a wall-clock kill outranks whatever was printed.
        if self.timed_out:
            return Reason.TIMEOUT, self.detail or "killed by wall-clock timeout"
        if self.oom_killed:
            return Reason.OOM, self.detail or "container was OOM-killed"
        if self.signal == SIGXCPU:
            return Reason.TIMEOUT, self.detail or "killed by SIGXCPU (CPU budget exhausted)"
        if self.exit_code == 137:
            return Reason.OOM, self.detail or "exit 137 (SIGKILL, usually the memory cap)"
        if self.exit_code in (125, 126, 127):
            return (
                Reason.INTERNAL_ERROR,
                self.detail or f"sandbox launcher failed (exit {self.exit_code})",
            )
        return None

    def crash_detail(self, what: str = "sandbox") -> str:
        """A default diagnostic for a process that died without an answer."""
        if self.detail:
            return self.detail
        if self.exit_code is None:
            return f"{what} produced no result"
        if self.exit_code < 0:
            return f"{what} killed by signal {-self.exit_code}"
        return f"{what} exited {self.exit_code} without a result"


def verdict_from_outcome(
    *,
    stdout: str,
    nonce: str,
    seed: int,
    image_digest: str,
    host: dict[str, Any],
    solution_bytes: int,
    wall_s: float,
    exit_code: int | None,
    timed_out: bool,
    oom_killed: bool = False,
    launch_failed: bool = False,
    detail: str = "",
) -> Verdict:
    """Fold a finished sandbox process into a :class:`Verdict`.

    Shared by every backend so that "what does exit code 137 mean" is answered
    in exactly one place. A missing or unframed result line is never accepting.
    """
    raw = RawOutcome(
        stdout=stdout,
        stderr="",
        exit_code=exit_code,
        wall_s=wall_s,
        timed_out=timed_out,
        oom_killed=oom_killed,
        launch_failed=launch_failed,
        detail=detail,
    )
    result = extract_result(stdout, nonce)
    used = Budget(
        wall_s=wall_s,
        cpu_s=result.cpu_s if result else 0.0,
        mem_bytes=result.mem_bytes if result else 0,
        max_solution_bytes=result.solution_bytes if result else solution_bytes,
    )

    def make(reason: Reason, why: str) -> Verdict:
        return Verdict.make(
            reason,
            used=used,
            seed=seed,
            image_digest=image_digest,
            host=host,
            detail=truncate_detail(why),
        )

    limited = raw.limit_reason()
    if limited is not None:
        return make(*limited)

    if result is not None:
        return make(result.reason, result.detail)

    if exit_code is None:
        return make(Reason.CRASH, detail or "sandbox produced no verdict")
    if exit_code < 0:
        return make(Reason.CRASH, detail or f"killed by signal {-exit_code}")
    return make(
        Reason.CRASH,
        detail or f"sandbox exited {exit_code} without a verdict line",
    )
