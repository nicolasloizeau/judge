"""The Lean policy is the Python policy plus a bounded mount allowance; pin the difference."""

from __future__ import annotations

import os

import pytest

from judge.core.policy import DEFAULT_POLICY, BindMount, SandboxPolicy, docker_run_args
from judge.core.types import Budget
from judge.lean.backend import DEFAULT_FSIZE_LIMIT, lean_policy

BUDGET = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=256 * 1024 * 1024, max_solution_bytes=1024)


def test_lean_policy_differs_from_the_default_only_in_fsize_and_user() -> None:
    policy = lean_policy()
    for name in SandboxPolicy.__slots__:
        if name in ("fsize_limit", "user"):
            continue
        assert getattr(policy, name) == getattr(DEFAULT_POLICY, name), name
    assert policy.fsize_limit == DEFAULT_FSIZE_LIMIT
    assert policy.mounts == ()


def test_lean_policy_runs_as_a_non_root_uid() -> None:
    uid = lean_policy().user.split(":")[0]
    assert uid != "0"
    if os.getuid() != 0:
        assert uid == str(os.getuid())


def test_at_most_one_read_write_mount() -> None:
    rw = BindMount("/h1", "/c1", read_only=False)
    rw2 = BindMount("/h2", "/c2", read_only=False)
    with pytest.raises(ValueError, match="at most one read-write"):
        DEFAULT_POLICY.with_mounts(rw, rw2)
    policy = DEFAULT_POLICY.with_mounts(rw, BindMount("/h2", "/c2"))
    assert policy.writable_mount == rw


def test_mounts_render_as_bind_mounts_with_readonly_flag() -> None:
    policy = lean_policy().with_mounts(
        BindMount("/h/verifier", "/work/verifier"),
        BindMount("/h/solution", "/work/solution", read_only=False),
    )
    args = docker_run_args(image="img", budget=BUDGET, policy=policy, argv=["elab", "x"])
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
    assert mounts == [
        "type=bind,src=/h/verifier,dst=/work/verifier,readonly",
        "type=bind,src=/h/solution,dst=/work/solution",
    ]
    assert not [a for a in args if a.split("=")[0] in ("--volume", "-v", "--tmpfs")]
    assert "--read-only" in args
    assert args[args.index("img") + 1 :] == ["elab", "x"]
    assert "--ulimit" in args and f"fsize={DEFAULT_FSIZE_LIMIT}:{DEFAULT_FSIZE_LIMIT}" in args


def test_default_policy_still_emits_no_mounts_and_zero_fsize() -> None:
    args = docker_run_args(image="img", budget=BUDGET)
    assert "--mount" not in args
    assert "fsize=0:0" in args
