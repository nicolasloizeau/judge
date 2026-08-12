"""Docker + gVisor (``--runtime=runsc``) backend.

One container per verification, no reuse. Inputs go in on stdin as one JSON
line; the verdict comes back framed on stdout. The wall clock is enforced from
*outside* with a hard kill, because anything running inside can defeat an
in-process timer.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import time
import uuid
from typing import Any

from judge.core.policy import DEFAULT_POLICY, SandboxPolicy, docker_run_args
from judge.core.protocol import Request, new_nonce, new_seed, truncate_detail, verdict_from_outcome
from judge.core.types import Budget, Reason, Verdict

log = logging.getLogger(__name__)

DEFAULT_GRACE_S = 5.0
"""Extra wall time before the outside-in kill, covering container startup."""


class GvisorBackend:
    """Run verifications in a gVisor-sandboxed container."""

    name = "gvisor"
    provides_isolation = True

    def __init__(
        self,
        image: str,
        *,
        policy: SandboxPolicy = DEFAULT_POLICY,
        runtime: str = "runsc",
        docker: str = "docker",
        grace_s: float = DEFAULT_GRACE_S,
    ) -> None:
        self.image = image
        self.policy = policy
        self.runtime = runtime
        self.docker = docker
        self.grace_s = grace_s
        self._image_digest: str | None = None

    # -- diagnostics ---------------------------------------------------------

    def image_digest(self) -> str:
        """Content-addressed id of the image actually used (cached)."""
        if self._image_digest is None:
            self._image_digest = self._inspect_image_id()
        return self._image_digest

    def _inspect_image_id(self) -> str:
        proc = self._docker(["image", "inspect", "--format", "{{.Id}}", self.image])
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    def host_info(self) -> dict[str, Any]:
        proc = self._docker(["version", "--format", "{{.Server.Version}}"])
        return {
            "backend": self.name,
            "runtime": self.runtime,
            "image": self.image,
            "docker_version": proc.stdout.strip() if proc.returncode == 0 else "unknown",
            "kernel": platform.release(),
            "platform": platform.platform(),
        }

    def available(self) -> bool:
        """True when Docker is reachable and the configured runtime exists."""
        proc = self._docker(["info", "--format", "{{json .Runtimes}}"])
        return proc.returncode == 0 and f'"{self.runtime}"' in proc.stdout

    # -- the contract --------------------------------------------------------

    def run(self, verifier_src: str, solution: str, budget: Budget) -> Verdict:
        budget.validate()
        seed = new_seed()
        nonce = new_nonce()
        host = self.host_info()
        digest = self.image_digest()
        solution_bytes = len(solution.encode("utf-8", "surrogatepass"))

        if solution_bytes > budget.max_solution_bytes:
            return Verdict.make(
                Reason.SOLUTION_TOO_LARGE,
                used=Budget(0.0, 0.0, 0, solution_bytes),
                seed=seed,
                image_digest=digest,
                host=host,
                detail=f"{solution_bytes} > max_solution_bytes={budget.max_solution_bytes}",
            )

        request = Request(
            nonce=nonce,
            seed=seed,
            verifier_src=verifier_src,
            solution=solution,
            budget=budget,
            pids_limit=self.policy.pids_limit,
        )
        container = f"judge-{uuid.uuid4().hex}"
        args = docker_run_args(
            image=self.image,
            budget=budget,
            policy=self.policy,
            runtime=self.runtime,
            name=container,
            docker=self.docker,
        )

        stdout = ""
        exit_code: int | None = None
        timed_out = False
        launch_failed = False
        detail = ""
        started = time.monotonic()
        try:
            proc = subprocess.run(
                args,
                input=request.encode() + "\n",
                capture_output=True,
                text=True,
                timeout=budget.wall_s + self.grace_s,
                check=False,
            )
            stdout, exit_code = proc.stdout, proc.returncode
            if exit_code != 0 and proc.stderr:
                detail = truncate_detail(proc.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _as_text(exc.stdout)
            self._kill_container(container)
        except FileNotFoundError as exc:
            launch_failed = True
            detail = f"docker not found: {exc}"
        except OSError as exc:  # pragma: no cover - environment dependent
            launch_failed = True
            detail = f"failed to launch docker: {exc}"
        wall_s = time.monotonic() - started

        oom_killed = False
        if not launch_failed:
            state = self._inspect_state(container)
            oom_killed = bool(state.get("oom_killed"))
            if exit_code is None and isinstance(state.get("exit_code"), int):
                exit_code = state["exit_code"]
            # An empty state means the container never came up (unreachable
            # daemon, bad image); do not warn about failing to remove it.
            self._remove_container(container, quiet=not state)

        return verdict_from_outcome(
            stdout=stdout,
            nonce=nonce,
            seed=seed,
            image_digest=digest,
            host=host,
            solution_bytes=solution_bytes,
            wall_s=wall_s,
            exit_code=exit_code,
            timed_out=timed_out,
            oom_killed=oom_killed,
            launch_failed=launch_failed,
            detail=detail,
        )

    # -- docker plumbing -----------------------------------------------------

    def _docker(self, argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.docker, *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(argv, 125, "", str(exc))

    def _inspect_state(self, container: str) -> dict[str, Any]:
        proc = self._docker(
            ["inspect", "--format", "{{.State.OOMKilled}} {{.State.ExitCode}}", container]
        )
        if proc.returncode != 0:
            return {}
        parts = proc.stdout.split()
        if len(parts) != 2:
            return {}
        oom, code = parts
        try:
            return {"oom_killed": oom == "true", "exit_code": int(code)}
        except ValueError:  # pragma: no cover - defensive
            return {"oom_killed": oom == "true"}

    def _kill_container(self, container: str) -> None:
        proc = self._docker(["kill", container], timeout=20.0)
        if proc.returncode != 0:
            log.warning("docker kill %s failed: %s", container, proc.stderr.strip())

    def _remove_container(self, container: str, *, quiet: bool = False) -> None:
        proc = self._docker(["rm", "--force", container], timeout=20.0)
        if proc.returncode != 0 and not quiet:
            log.warning("docker rm %s failed: %s", container, proc.stderr.strip())


def _as_text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):  # pragma: no cover - text=True normally wins
        return raw.decode("utf-8", "replace")
    return raw
