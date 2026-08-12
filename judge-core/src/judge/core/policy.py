"""Sandbox flag policy, expressed as data.

The policy is a frozen dataclass rather than a hand-rolled argv string so that
it can be unit-tested, printed, diffed across releases, and reused by any
language backend. :func:`docker_run_args` is the only place that knows Docker's
spelling of it.

The guarantee the policy encodes: **zero writable bytes anywhere, and no
network.** Read-only rootfs, no volumes, no bind mounts, no tmpfs, no
``/dev/shm`` (via ``--ipc=none``), ``TMPDIR`` and ``HOME`` pointed at a path
that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from judge.core.types import Budget

NONEXISTENT_PATH = "/nonexistent"


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
    swap_disabled: bool = True
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

    def with_env(self, **extra: str) -> SandboxPolicy:
        merged = dict(self.env)
        merged.update(extra)
        return replace(self, env=tuple(sorted(merged.items())))


DEFAULT_POLICY = SandboxPolicy()


def docker_run_args(
    *,
    image: str,
    budget: Budget,
    policy: SandboxPolicy = DEFAULT_POLICY,
    runtime: str | None = "runsc",
    name: str | None = None,
    docker: str = "docker",
) -> list[str]:
    """Render ``policy`` + ``budget`` into a full ``docker run`` argv.

    No mounts and no ``--tmpfs`` are ever emitted; that is the point.
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
    args += ["--ulimit", "fsize=0:0"]

    for key, value in policy.env:
        args += ["--env", f"{key}={value}"]

    args.append(image)
    return args


def describe_policy(policy: SandboxPolicy = DEFAULT_POLICY) -> str:
    """Human-readable summary, used by ``judge selftest`` output and docs."""
    lines = [
        f"network            : {policy.network}",
        f"rootfs             : {'read-only' if policy.read_only_rootfs else 'WRITABLE'}",
        f"ipc                : {policy.ipc} (no /dev/shm)",
        "mounts             : none (no volumes, no bind mounts, no tmpfs)",
        f"pids-limit         : {policy.pids_limit}",
        f"capabilities       : {'all dropped' if policy.drop_all_capabilities else 'default'}",
        f"no-new-privileges  : {policy.no_new_privileges}",
        f"user               : {policy.user}",
        f"swap               : {'disabled' if policy.swap_disabled else 'enabled'}",
    ]
    lines += [f"env                : {k}={v}" for k, v in policy.env]
    return "\n".join(lines)
