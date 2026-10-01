"""The three-stage Lean backends.

A Lean verification is not one process but three, each in its own sandbox:

1. **build-verifier** -- ``lean`` elaborates ``Verifier.lean`` into
   ``Verifier.olean`` (scratch ``verifier/`` read-write), then
   ``judge-lean-check --mode verifier`` replays it through the kernel and
   checks ``Spec`` and the allowlist (everything read-only).
2. **elaborate-solution** -- ``lean`` elaborates ``Solution.lean`` into
   ``Solution.olean`` (``verifier/`` read-only, ``solution/`` read-write).
   Its stdout is never read.
3. **check-solution** -- ``judge-lean-check --mode solution`` replays both
   modules and asks the kernel whether ``answer : Spec`` (nothing writable).

Why three: elaboration runs untrusted code with full ``IO``, so nothing decided
inside a process that elaborated untrusted source is believed. The verdict
comes from the checker alone, which runs only trusted code over the ``.olean``
files as data, and the verifier's olean is built before the solution exists
and is never writable again.

This module holds the stage pipeline and the reason mapping, shared by
:class:`LeanGvisorBackend` (three containers, one per stage, via
:meth:`~judge.core.backends.gvisor.GvisorBackend.run_container`) and
:class:`LeanLocalInsecureBackend` (three host processes via
:func:`~judge.core.backends.local.run_process`). Both render the same
``harness.sh`` arguments; only *where* the stage runs differs.

Scratch space: one temporary directory per verification holding ``verifier/``
and ``solution/``. Each is bind-mounted read-write into exactly the stage that
produces its output and read-only into the stages that consume it; the whole
directory is size-checked after every stage and deleted when ``run`` returns,
whatever happened.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from judge.core.backends.gvisor import DEFAULT_GRACE_S, GvisorBackend
from judge.core.backends.local import ProcessLimits, run_process
from judge.core.policy import DEFAULT_POLICY, BindMount, SandboxPolicy
from judge.core.protocol import RawOutcome, extract_frame, new_nonce, truncate_detail
from judge.core.types import Budget, Reason, Verdict
from judge.lean.header import DEFAULT_ALLOW, HeaderError, merged_allowlist, parse_allow_header
from judge.lean.image import (
    CHECKER_RELPATH,
    DEFAULT_IMAGE_TAG,
    harness_path,
    package_search_path,
    pinned_toolchain,
    project_dir,
)

log = logging.getLogger(__name__)

MIB = 1024 * 1024

DEFAULT_FSIZE_LIMIT = 128 * MIB
"""``RLIMIT_FSIZE`` for the stages that write: no single file may exceed this."""
DEFAULT_MAX_SCRATCH_BYTES = 512 * MIB
"""Total size the scratch directory may reach; checked after every stage."""
DEFAULT_MAX_HEARTBEATS = 400_000
"""``-D maxHeartbeats=`` handed to ``lean`` per declaration -- a courtesy
limit a file can raise with ``set_option``; the wall clock is the real one."""
DEFAULT_THREADS = 4
"""``--threads=`` for ``lean``. Threads count against the pids limit and the
CPU budget, so it is fixed rather than left at the host's core count."""

SEED_UNUSED = 0
"""There is no randomness in a Lean verification; ``Verdict.seed`` is 0."""

MODULE_VERIFIER = "Verifier"
MODULE_SOLUTION = "Solution"
CONTAINER_VERIFIER_DIR = "/work/verifier"
CONTAINER_SOLUTION_DIR = "/work/solution"


# -- stages -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stage:
    """One sandboxed step: what program, which module, which directory may be written."""

    name: str
    kind: str
    """``elab`` (run ``lean``) or ``check`` (run the checker)."""
    module: str
    """``Verifier`` / ``Solution`` for ``elab``; the checker ``--mode`` for ``check``."""
    writable: str | None
    """``verifier``, ``solution`` or ``None``: the scratch subdirectory this
    stage may write. Everything else it sees is read-only."""
    with_solution: bool
    """Whether the ``solution/`` directory exists yet and should be visible."""


BUILD_VERIFIER = Stage("build-verifier", "elab", MODULE_VERIFIER, "verifier", False)
CHECK_VERIFIER = Stage("check-verifier", "check", "verifier", None, False)
ELABORATE_SOLUTION = Stage("elaborate-solution", "elab", MODULE_SOLUTION, "solution", True)
CHECK_SOLUTION = Stage("check-solution", "check", "solution", None, True)
STAGES: tuple[Stage, ...] = (BUILD_VERIFIER, CHECK_VERIFIER, ELABORATE_SOLUTION, CHECK_SOLUTION)


def harness_args(
    stage: Stage,
    *,
    budget: Budget,
    search_dirs: Sequence[str],
    work_dir: str,
    nonce: str,
    allow: Sequence[str],
    fsize_limit: int,
    max_heartbeats: int,
    threads: int,
) -> list[str]:
    """Render a stage into the argument vector ``harness.sh`` understands."""
    search = ":".join(search_dirs)
    cpu = str(int(budget.cpu_s))
    mem = str(budget.mem_bytes)
    if stage.kind == "elab":
        return [
            "elab",
            cpu,
            mem,
            str(fsize_limit),
            str(max_heartbeats),
            str(threads),
            search,
            work_dir,
            stage.module,
        ]
    return ["check", cpu, mem, search, stage.module, nonce, ",".join(allow)]


# -- the checker's report -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckerReport:
    """What ``judge-lean-check`` printed, framed with the run's nonce."""

    ok: bool
    why: str
    axioms: tuple[str, ...]
    spec_is_prop: bool

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> CheckerReport:
        ok = data["ok"]
        if ok is not True and ok is not False:
            raise ValueError("checker report: 'ok' is not a boolean")
        axioms = data.get("axioms", [])
        if not isinstance(axioms, list):
            raise ValueError("checker report: 'axioms' is not a list")
        return cls(
            ok=ok,
            why=truncate_detail(str(data.get("why", ""))),
            axioms=tuple(str(a) for a in axioms),
            spec_is_prop=data.get("specIsProp") is True,
        )


def extract_checker_report(stdout: str, nonce: str) -> CheckerReport | None:
    """The last well-formed framed report in ``stdout``, or ``None``."""
    payload = extract_frame(stdout, nonce)
    if payload is None:
        return None
    try:
        return CheckerReport.from_payload(payload)
    except (KeyError, ValueError, TypeError):
        return None


# -- usage lines the harness prints on stderr --------------------------------

_MEM_RE = re.compile(r"^@@judge-mem (\d+)\s*$", re.MULTILINE)
_TIMES_RE = re.compile(r"^@@judge-times (\d+)m([\d.]+)s (\d+)m([\d.]+)s\s*$", re.MULTILINE)
_USAGE_LINE_RE = re.compile(r"^@@judge-(?:mem|times) .*$\n?", re.MULTILINE)


def absorb_usage(raw: RawOutcome) -> RawOutcome:
    """Pull the harness' ``@@judge-*`` lines out of stderr into the outcome.

    Advisory only: the lines come from inside the sandbox. The stripped stderr
    is what diagnostics are built from.
    """
    mem = max((int(m.group(1)) for m in _MEM_RE.finditer(raw.stderr)), default=0)
    cpu = 0.0
    for m in _TIMES_RE.finditer(raw.stderr):
        cpu = max(
            cpu,
            60 * int(m.group(1)) + float(m.group(2)) + 60 * int(m.group(3)) + float(m.group(4)),
        )
    stderr = _USAGE_LINE_RE.sub("", raw.stderr)
    # A detail the runner derived from stderr is re-derived from the cleaned text.
    detail = truncate_detail(stderr) if raw.detail == truncate_detail(raw.stderr) else raw.detail
    return replace(
        raw,
        stderr=stderr,
        detail=detail,
        cpu_s=max(raw.cpu_s, cpu),
        mem_bytes=max(raw.mem_bytes, mem),
    )


# -- scratch directory ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scratch:
    """The per-verification host directory: ``verifier/`` and ``solution/``."""

    root: Path

    @property
    def verifier(self) -> Path:
        return self.root / "verifier"

    @property
    def solution(self) -> Path:
        return self.root / "solution"

    def dir_for(self, module: str) -> Path:
        return self.verifier if module == MODULE_VERIFIER else self.solution

    def olean(self, module: str) -> Path:
        return self.dir_for(module) / f"{module}.olean"

    def size_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:  # pragma: no cover - racing a deletion
                continue
        return total

    @classmethod
    def create(cls, scratch_root: str | None, *, world_writable: bool) -> Scratch:
        root = Path(tempfile.mkdtemp(prefix="judge-lean-", dir=scratch_root))
        scratch = cls(root)
        mode = 0o777 if world_writable else 0o755
        for sub in (scratch.verifier, scratch.solution):
            sub.mkdir(mode=mode)
            sub.chmod(mode)
        root.chmod(0o755)
        return scratch

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.root)
        except OSError as exc:  # pragma: no cover - permission trouble on the host
            log.warning("could not delete scratch directory %s: %s", self.root, exc)


# -- the pipeline -----------------------------------------------------------


@dataclass
class _Usage:
    wall_s: float = 0.0
    cpu_s: float = 0.0
    mem_bytes: int = 0

    def add(self, raw: RawOutcome) -> None:
        self.wall_s += raw.wall_s
        self.cpu_s += raw.cpu_s
        self.mem_bytes = max(self.mem_bytes, raw.mem_bytes)

    def as_budget(self, solution_bytes: int) -> Budget:
        return Budget(
            wall_s=self.wall_s,
            cpu_s=self.cpu_s,
            mem_bytes=self.mem_bytes,
            max_solution_bytes=solution_bytes,
        )


class LeanBackendBase:
    """The stage pipeline and reason mapping. Subclasses say where a stage runs.

    ``run`` is stateless: the verifier is rebuilt every time. A later version
    may take an optional cache keyed by ``sha256(verifier_src) + image_digest``
    as a constructor argument without changing ``run``.
    """

    name: str = "lean"
    provides_isolation: bool = False

    def __init__(
        self,
        *,
        allow: Sequence[str] = DEFAULT_ALLOW,
        fsize_limit: int = DEFAULT_FSIZE_LIMIT,
        max_scratch_bytes: int = DEFAULT_MAX_SCRATCH_BYTES,
        max_heartbeats: int = DEFAULT_MAX_HEARTBEATS,
        threads: int = DEFAULT_THREADS,
        scratch_root: str | None = None,
    ) -> None:
        self.allow = tuple(allow)
        self.fsize_limit = fsize_limit
        self.max_scratch_bytes = max_scratch_bytes
        self.max_heartbeats = max_heartbeats
        self.threads = threads
        self.scratch_root = scratch_root

    # -- what a subclass provides -------------------------------------------

    def image_digest(self) -> str:
        raise NotImplementedError

    def host_info(self) -> dict[str, Any]:
        raise NotImplementedError

    def _scratch_world_writable(self) -> bool:
        return False

    def _run_stage(
        self, stage: Stage, scratch: Scratch, budget: Budget, nonce: str, allow: Sequence[str]
    ) -> RawOutcome:
        raise NotImplementedError

    # -- the contract ---------------------------------------------------------

    def run(self, verifier_src: str, solution: str, budget: Budget) -> Verdict:
        budget.validate()
        nonce = new_nonce()
        host = self.host_info()
        digest = self.image_digest()
        solution_bytes = len(solution.encode("utf-8", "surrogatepass"))
        usage = _Usage()

        def verdict(reason: Reason, detail: str, **extra: Any) -> Verdict:
            host_info = dict(host)
            host_info.update(extra)
            return Verdict.make(
                reason,
                used=usage.as_budget(solution_bytes),
                seed=SEED_UNUSED,
                image_digest=digest,
                host=host_info,
                detail=truncate_detail(detail),
            )

        if solution_bytes > budget.max_solution_bytes:
            return verdict(
                Reason.SOLUTION_TOO_LARGE,
                f"{solution_bytes} > max_solution_bytes={budget.max_solution_bytes}",
            )

        try:
            allow = merged_allowlist(self.allow, parse_allow_header(verifier_src))
        except HeaderError as exc:
            return verdict(Reason.BUILD_FAILED, f"bad '-- judge: allow' header: {exc}")

        scratch = Scratch.create(self.scratch_root, world_writable=self._scratch_world_writable())
        try:
            _write_source(scratch.verifier / f"{MODULE_VERIFIER}.lean", verifier_src)

            # Stage 1a: elaborate the verifier.
            raw = self._stage(BUILD_VERIFIER, scratch, budget, nonce, allow, usage)
            if (limited := raw.limit_reason()) is not None:
                return verdict(*limited, stage=BUILD_VERIFIER.name)
            failure = self._elab_failure(raw, scratch, MODULE_VERIFIER)
            if failure is not None:
                return verdict(Reason.BUILD_FAILED, failure, stage=BUILD_VERIFIER.name)

            # Stage 1b: replay it and check Spec + the allowlist.
            raw = self._stage(CHECK_VERIFIER, scratch, budget, nonce, allow, usage)
            if (limited := raw.limit_reason()) is not None:
                return verdict(*limited, stage=CHECK_VERIFIER.name)
            report = self._checker_report(raw, nonce)
            if isinstance(report, tuple):
                return verdict(*report, stage=CHECK_VERIFIER.name)
            if not report.ok:
                return verdict(Reason.BUILD_FAILED, report.why, stage=CHECK_VERIFIER.name)

            # Stage 2: elaborate the solution. Its stdout is never read.
            _write_source(scratch.solution / f"{MODULE_SOLUTION}.lean", solution)
            raw = self._stage(ELABORATE_SOLUTION, scratch, budget, nonce, allow, usage)
            if (limited := raw.limit_reason()) is not None:
                return verdict(*limited, stage=ELABORATE_SOLUTION.name)
            failure = self._elab_failure(raw, scratch, MODULE_SOLUTION)
            if failure is not None:
                return verdict(Reason.REJECTED, failure, stage=ELABORATE_SOLUTION.name)

            # Stage 3: the kernel decides.
            raw = self._stage(CHECK_SOLUTION, scratch, budget, nonce, allow, usage)
            if (limited := raw.limit_reason()) is not None:
                return verdict(*limited, stage=CHECK_SOLUTION.name)
            report = self._checker_report(raw, nonce)
            if isinstance(report, tuple):
                return verdict(*report, stage=CHECK_SOLUTION.name)
            extra = {
                "stage": CHECK_SOLUTION.name,
                "axioms": list(report.axioms),
                "spec_is_prop": report.spec_is_prop,
            }
            if report.ok:
                return verdict(Reason.ACCEPTED, "", **extra)
            return verdict(Reason.REJECTED, report.why, **extra)
        finally:
            scratch.cleanup()

    # -- helpers ----------------------------------------------------------------

    def _stage(
        self,
        stage: Stage,
        scratch: Scratch,
        budget: Budget,
        nonce: str,
        allow: Sequence[str],
        usage: _Usage,
    ) -> RawOutcome:
        raw = absorb_usage(self._run_stage(stage, scratch, budget, nonce, allow))
        usage.add(raw)
        log.debug("stage %s: exit=%s wall=%.2fs", stage.name, raw.exit_code, raw.wall_s)
        return raw

    def _elab_failure(self, raw: RawOutcome, scratch: Scratch, module: str) -> str | None:
        """Why an elaboration stage counts as failed, or ``None`` if it succeeded."""
        messages = truncate_detail(raw.stderr) or raw.crash_detail("lean")
        if raw.exit_code != 0:
            return f"lean exited {raw.exit_code}: {messages}"
        if not scratch.olean(module).is_file():
            return f"lean produced no {module}.olean: {messages}"
        size = scratch.size_bytes()
        if size > self.max_scratch_bytes:
            return f"scratch directory grew to {size} bytes (limit {self.max_scratch_bytes})"
        return None

    def _checker_report(self, raw: RawOutcome, nonce: str) -> CheckerReport | tuple[Reason, str]:
        """The checker's report, or the reason there is none.

        The checker exits 0 whether its answer is yes or no. A nonzero exit or
        a missing frame means the checker itself did not run to completion --
        a host problem if the binary is missing, otherwise something the
        untrusted olean did to it -- and is never a verdict.
        """
        if raw.exit_code != 0:
            return Reason.CRASH, f"checker exited {raw.exit_code}: {raw.crash_detail('checker')}"
        report = extract_checker_report(raw.stdout, nonce)
        if report is None:
            return Reason.CRASH, "checker exited 0 without a framed report"
        return report


def _write_source(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", errors="surrogatepass")
    path.chmod(0o644)


# -- gVisor ---------------------------------------------------------------------


def lean_policy(
    base: SandboxPolicy = DEFAULT_POLICY, *, fsize_limit: int = DEFAULT_FSIZE_LIMIT
) -> SandboxPolicy:
    """The default policy, adapted for a toolchain that has to write files.

    Two deliberate differences from what ``judge-python`` runs under:

    * ``fsize_limit`` is raised from 0 so the stage that produces an olean can
      write one (bounded per file; the scratch directory is bounded as a
      whole by the backend).
    * the container runs as the *host* user when that user is not root. The
      writable scratch directory is a host directory, and files a container
      creates there under uid 65532 could not be deleted by an unprivileged
      host user afterwards. Running as the host uid keeps the directory
      deletable; it is still a non-root uid, and the mounts, not the uid,
      are what bound what the container can touch. A root host keeps the
      image's own ``65532:65532``.

    Mounts are added per stage by :class:`LeanGvisorBackend`.
    """
    user = base.user if os.getuid() == 0 else f"{os.getuid()}:{os.getgid()}"
    return replace(base, fsize_limit=fsize_limit, user=user)


class LeanGvisorBackend(LeanBackendBase):
    """Three gVisor containers per verification."""

    name = "gvisor"
    provides_isolation = True

    def __init__(
        self,
        image: str = DEFAULT_IMAGE_TAG,
        *,
        policy: SandboxPolicy | None = None,
        runtime: str = "runsc",
        docker: str = "docker",
        grace_s: float = DEFAULT_GRACE_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.policy = lean_policy(fsize_limit=self.fsize_limit) if policy is None else policy
        self.containers = GvisorBackend(
            image, policy=self.policy, runtime=runtime, docker=docker, grace_s=grace_s
        )

    @property
    def image(self) -> str:
        return self.containers.image

    def image_digest(self) -> str:
        return self.containers.image_digest()

    def host_info(self) -> dict[str, Any]:
        info = self.containers.host_info()
        info["language"] = "lean"
        return info

    def available(self) -> bool:
        return self.containers.available()

    def _scratch_world_writable(self) -> bool:
        return self.policy.user != f"{os.getuid()}:{os.getgid()}"

    def _run_stage(
        self, stage: Stage, scratch: Scratch, budget: Budget, nonce: str, allow: Sequence[str]
    ) -> RawOutcome:
        mounts = [
            BindMount(
                str(scratch.verifier),
                CONTAINER_VERIFIER_DIR,
                read_only=stage.writable != "verifier",
            )
        ]
        search_dirs = [CONTAINER_VERIFIER_DIR]
        if stage.with_solution:
            mounts.append(
                BindMount(
                    str(scratch.solution),
                    CONTAINER_SOLUTION_DIR,
                    read_only=stage.writable != "solution",
                )
            )
            search_dirs.append(CONTAINER_SOLUTION_DIR)
        work_dir = (
            CONTAINER_VERIFIER_DIR if stage.module == MODULE_VERIFIER else CONTAINER_SOLUTION_DIR
        )
        argv = harness_args(
            stage,
            budget=budget,
            search_dirs=search_dirs,
            work_dir=work_dir,
            nonce=nonce,
            allow=allow,
            fsize_limit=self.fsize_limit,
            max_heartbeats=self.max_heartbeats,
            threads=self.threads,
        )
        return self.containers.run_container(
            argv, budget=budget, policy=self.policy.with_mounts(*mounts)
        )


# -- local ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Toolchain:
    """Where the local backend finds ``lean``, the checker and the oleans."""

    lean: Path
    sysroot: Path
    checker: Path
    harness: Path
    search_path: tuple[Path, ...]
    toolchain: str

    def missing(self) -> list[str]:
        problems = []
        if not self.lean.is_file():
            problems.append(f"lean binary not found at {self.lean}")
        if not self.checker.is_file():
            problems.append(
                f"checker not built at {self.checker} (run: lake build judge-lean-check)"
            )
        if not self.harness.is_file():
            problems.append(f"harness not found at {self.harness}")
        # Not every manifest entry has oleans (`Cli` only serves executables);
        # Mathlib's directory is the one that proves `lake exe cache get` ran.
        mathlib = [p for p in self.search_path if "mathlib" in p.parts]
        if not mathlib or not mathlib[0].is_dir():
            where = mathlib[0] if mathlib else "the Lake project"
            problems.append(f"Mathlib oleans missing at {where} (run: lake exe cache get)")
        return problems

    @classmethod
    def resolve(cls, project: Path | None = None, *, elan_home: Path | None = None) -> Toolchain:
        project = project_dir() if project is None else project
        name = pinned_toolchain(project)
        home = elan_home or Path(os.environ.get("ELAN_HOME") or Path.home() / ".elan")
        sysroot = home / "toolchains" / name.replace("/", "--").replace(":", "---")
        return cls(
            lean=sysroot / "bin" / "lean",
            sysroot=sysroot,
            checker=project / CHECKER_RELPATH,
            harness=harness_path(),
            search_path=tuple(package_search_path(project)),
            toolchain=name,
        )


class LeanLocalInsecureBackend(LeanBackendBase):
    """Three host processes per verification. Development only.

    Runs the pinned toolchain from ``~/.elan`` and the checker from the
    checked-out Lake project. Nothing is sandboxed: the solution's ``run_cmd``
    has your filesystem and your network, and the verifier's olean *is*
    writable by the solution stage. Never point it at hostile input.
    """

    name = "local-insecure"
    provides_isolation = False

    def __init__(
        self,
        project: Path | None = None,
        *,
        i_understand_this_is_insecure: bool = False,
        toolchain: Toolchain | None = None,
        grace_s: float = DEFAULT_GRACE_S,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not i_understand_this_is_insecure:
            raise ValueError(
                "LeanLocalInsecureBackend does not sandbox anything; pass "
                "i_understand_this_is_insecure=True to acknowledge that."
            )
        log.warning(
            "LeanLocalInsecureBackend is in use: untrusted Lean runs unsandboxed as "
            "this user, with filesystem and network access. Development only."
        )
        super().__init__(**kwargs)
        self.toolchain = Toolchain.resolve(project) if toolchain is None else toolchain
        self.grace_s = grace_s
        self.env = env

    def available(self) -> bool:
        return not self.toolchain.missing()

    def image_digest(self) -> str:
        return "local-insecure"

    def host_info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "language": "lean",
            "runtime": "none (subprocess + setrlimit + rss watchdog)",
            "toolchain": self.toolchain.toolchain,
            "kernel": platform.release(),
            "platform": platform.platform(),
        }

    def _run_stage(
        self, stage: Stage, scratch: Scratch, budget: Budget, nonce: str, allow: Sequence[str]
    ) -> RawOutcome:
        search_dirs = [str(scratch.verifier)]
        if stage.with_solution:
            search_dirs.append(str(scratch.solution))
        argv = harness_args(
            stage,
            budget=budget,
            search_dirs=search_dirs,
            work_dir=str(scratch.dir_for(stage.module)),
            nonce=nonce,
            allow=allow,
            fsize_limit=self.fsize_limit,
            max_heartbeats=self.max_heartbeats,
            threads=self.threads,
        )
        env = dict(self.env) if self.env is not None else dict(os.environ)
        env.update(
            {
                "LEAN_PATH": ":".join(str(p) for p in self.toolchain.search_path),
                "LEAN_SYSROOT": str(self.toolchain.sysroot),
                "JUDGE_LEAN": str(self.toolchain.lean),
                "JUDGE_LEAN_CHECK": str(self.toolchain.checker),
            }
        )
        limits = ProcessLimits(
            cpu_s=budget.cpu_s,
            address_space_bytes=None,  # Lean maps gigabytes of oleans; see harness.sh
            fsize_bytes=self.fsize_limit if stage.kind == "elab" else 0,
            rss_limit_bytes=budget.mem_bytes,
        )
        return run_process(
            ["/bin/sh", str(self.toolchain.harness), *argv],
            budget=budget,
            cwd=str(scratch.root),
            env=env,
            limits=limits,
            grace_s=self.grace_s,
        )
