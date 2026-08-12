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

    if launch_failed:
        return make(Reason.INTERNAL_ERROR, detail or "failed to launch sandbox")

    # The outside-in kill is authoritative: a verifier can swallow the harness'
    # own in-process timeout, so a wall-clock kill outranks whatever it printed.
    if timed_out:
        return make(Reason.TIMEOUT, detail or "killed by wall-clock timeout")
    if oom_killed:
        return make(Reason.OOM, detail or "container was OOM-killed")

    if result is not None:
        return make(result.reason, result.detail)

    if exit_code is None:
        return make(Reason.CRASH, detail or "sandbox produced no verdict")
    if exit_code < 0:
        return make(Reason.CRASH, detail or f"killed by signal {-exit_code}")
    if exit_code == 137:
        return make(Reason.OOM, detail or "exit 137 (SIGKILL, usually the memory cap)")
    if exit_code in (125, 126, 127):
        return make(Reason.INTERNAL_ERROR, detail or f"sandbox launcher failed (exit {exit_code})")
    return make(
        Reason.CRASH,
        detail or f"sandbox exited {exit_code} without a verdict line",
    )
