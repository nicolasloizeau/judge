"""Sandbox flag policy, expressed as data.

The policy is a frozen dataclass rather than a hand-rolled argv string so that
it can be unit-tested, printed, diffed across releases, and reused by any
language backend. :func:`docker_run_args` is the only place that knows Docker's
spelling of it.

The guarantee the default policy encodes: **zero writable bytes anywhere, and
no network.** Read-only rootfs, no volumes, no bind mounts, no tmpfs, no
``/dev/shm`` (via ``--ipc=none``), ``TMPDIR`` and ``HOME`` pointed at a path
that does not exist.

Mounts
------
A compiled language has to write its build products somewhere, so the policy
carries an explicit, bounded *mount allowance*: any number of read-only bind
mounts and **at most one** read-write bind mount, present only when a language
backend asks for them. ``DEFAULT_POLICY`` -- what ``judge-python`` runs under --
has no mounts, and the Python test-suite asserts that no ``--volume``,
``--mount`` or ``--tmpfs`` flag is ever emitted for it. ``judge-lean`` is the
first consumer: see :mod:`judge.lean.backend` for how it uses one read-write
scratch directory per stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from judge.core.types import Budget

NONEXISTENT_PATH = "/nonexistent"


@dataclass(frozen=True, slots=True)
class BindMount:
    """One host directory (or file) made visible inside the container.

    ``read_only`` mounts are the way to hand a stage the *output of an earlier
    stage* without letting it rewrite it. A read-write mount is the one place
    a stage may leave bytes behind; a policy permits at most one.
    """

    host: str
    container: str
    read_only: bool = True

    def docker_arg(self) -> str:
        spec = f"type=bind,src={self.host},dst={self.container}"
        if self.read_only:
            spec += ",readonly"
        return spec


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Container-level restrictions applied to every verification."""

    network: str = "none"
    read_only_rootfs: bool = True
    ipc: str = "none"  # also means: /dev/shm is not mounted at all
    pids_limit: int = 64
    drop_all_capabilities: bool = True
    no_new_privileges: bool = True
    user: str = "65532:65532"  # nonroot; matches the judge-python image
    nofile_limit: int = 256
    core_limit: int = 0
    fsize_limit: int = 0
    """``RLIMIT_FSIZE`` in bytes. ``0`` means no file may grow past zero bytes,
    which with no writable mount is the "zero writable bytes" guarantee. A
    language backend that needs a writable scratch directory raises this to
    bound the size of *each* file it may write."""
    swap_disabled: bool = True
    mounts: tuple[BindMount, ...] = ()
    """Bind mounts. Empty by default; at most one may be read-write."""
    env: tuple[tuple[str, str], ...] = field(
        default_factory=lambda: (
            ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONUNBUFFERED", "1"),
            ("PYTHONHASHSEED", "0"),
            ("TMPDIR", NONEXISTENT_PATH),
            ("HOME", NONEXISTENT_PATH),
            # Keep BLAS/OpenMP single-threaded: thread pools inflate address
            # space and process counts for no benefit to a verifier.
            ("OMP_NUM_THREADS", "1"),
            ("OPENBLAS_NUM_THREADS", "1"),
            ("MKL_NUM_THREADS", "1"),
            ("NUMEXPR_NUM_THREADS", "1"),
        )
    )

    def __post_init__(self) -> None:
        writable = [m for m in self.mounts if not m.read_only]
        if len(writable) > 1:
            targets = ", ".join(m.container for m in writable)
            raise ValueError(f"a SandboxPolicy allows at most one read-write mount; got {targets}")
        if self.fsize_limit < 0:
            raise ValueError(f"fsize_limit must be >= 0, got {self.fsize_limit}")

    @property
    def writable_mount(self) -> BindMount | None:
        """The single read-write mount, if any."""
        for mount in self.mounts:
            if not mount.read_only:
                return mount
        return None

    def with_env(self, **extra: str) -> SandboxPolicy:
        merged = dict(self.env)
        merged.update(extra)
        return replace(self, env=tuple(sorted(merged.items())))

    def with_mounts(self, *mounts: BindMount) -> SandboxPolicy:
        """A copy with exactly these mounts (replacing, not appending)."""
        return replace(self, mounts=tuple(mounts))


DEFAULT_POLICY = SandboxPolicy()


def docker_run_args(
    *,
    image: str,
    budget: Budget,
    policy: SandboxPolicy = DEFAULT_POLICY,
    runtime: str | None = "runsc",
    name: str | None = None,
    docker: str = "docker",
    argv: Sequence[str] = (),
) -> list[str]:
    """Render ``policy`` + ``budget`` into a full ``docker run`` argv.

    Only the bind mounts listed in ``policy.mounts`` are emitted; no ``--tmpfs``
    or ``--volume`` ever is. ``argv`` is appended after the image and is handed
    to the image's entrypoint.
    """
    args: list[str] = [docker, "run", "--rm=false", "--interactive"]
    if name:
        args += ["--name", name]
    if runtime:
        args += [f"--runtime={runtime}"]

    args += [f"--network={policy.network}", f"--ipc={policy.ipc}"]
    if policy.read_only_rootfs:
        args.append("--read-only")
    if policy.drop_all_capabilities:
        args.append("--cap-drop=ALL")
    if policy.no_new_privileges:
        args += ["--security-opt", "no-new-privileges"]
    args += ["--user", policy.user]
    args += [f"--pids-limit={policy.pids_limit}"]

    args += [f"--memory={budget.mem_bytes}b"]
    if policy.swap_disabled:
        # memory-swap == memory means "no swap on top of the memory cap".
        args += [f"--memory-swap={budget.mem_bytes}b"]

    args += ["--ulimit", f"nofile={policy.nofile_limit}:{policy.nofile_limit}"]
    args += ["--ulimit", f"core={policy.core_limit}:{policy.core_limit}"]
    args += ["--ulimit", f"fsize={policy.fsize_limit}:{policy.fsize_limit}"]

    for mount in policy.mounts:
        args += ["--mount", mount.docker_arg()]

    for key, value in policy.env:
        args += ["--env", f"{key}={value}"]

    args.append(image)
    args.extend(argv)
    return args


def describe_policy(policy: SandboxPolicy = DEFAULT_POLICY) -> str:
    """Human-readable summary, used by ``judge selftest`` output and docs."""
    if policy.mounts:
        mounts = "; ".join(
            f"{m.host} -> {m.container} ({'ro' if m.read_only else 'RW'})" for m in policy.mounts
        )
    else:
        mounts = "none (no volumes, no bind mounts, no tmpfs)"
    lines = [
        f"network            : {policy.network}",
        f"rootfs             : {'read-only' if policy.read_only_rootfs else 'WRITABLE'}",
        f"ipc                : {policy.ipc} (no /dev/shm)",
        f"mounts             : {mounts}",
        f"fsize-limit        : {policy.fsize_limit} bytes per file",
        f"pids-limit         : {policy.pids_limit}",
        f"capabilities       : {'all dropped' if policy.drop_all_capabilities else 'default'}",
        f"no-new-privileges  : {policy.no_new_privileges}",
        f"user               : {policy.user}",
        f"swap               : {'disabled' if policy.swap_disabled else 'enabled'}",
    ]
    lines += [f"env                : {k}={v}" for k, v in policy.env]
    return "\n".join(lines)
