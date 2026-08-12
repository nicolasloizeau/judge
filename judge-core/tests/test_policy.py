"""The policy is the security claim in flag form; assert it flag by flag."""

from __future__ import annotations

import pytest

from judge.core.policy import DEFAULT_POLICY, docker_run_args
from judge.core.types import Budget

BUDGET = Budget(wall_s=5.0, cpu_s=5.0, mem_bytes=256 * 1024 * 1024, max_solution_bytes=1024)


@pytest.fixture
def args() -> list[str]:
    return docker_run_args(image="img:tag", budget=BUDGET, name="judge-test")


@pytest.mark.parametrize(
    "flag",
    [
        "--network=none",
        "--ipc=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pids-limit=64",
        "--runtime=runsc",
        "--memory=268435456b",
        "--memory-swap=268435456b",
    ],
)
def test_required_flag_present(args: list[str], flag: str) -> None:
    assert flag in args


def test_no_new_privileges_and_nonroot_user(args: list[str]) -> None:
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert args[args.index("--user") + 1] == "65532:65532"


def test_zero_writable_bytes_means_no_mounts_of_any_kind(args: list[str]) -> None:
    forbidden = ("--volume", "-v", "--mount", "--tmpfs", "--volumes-from")
    assert not [a for a in args if a.split("=")[0] in forbidden]


def test_environment_pins_no_bytecode_and_nonexistent_tmpdir(args: list[str]) -> None:
    envs = {args[i + 1] for i, a in enumerate(args) if a == "--env"}
    assert "PYTHONDONTWRITEBYTECODE=1" in envs
    assert "TMPDIR=/nonexistent" in envs
    assert "HOME=/nonexistent" in envs


def test_image_is_last_so_nothing_can_be_read_as_a_flag(args: list[str]) -> None:
    assert args[-1] == "img:tag"


def test_container_is_not_auto_removed_so_oom_state_is_readable(args: list[str]) -> None:
    # OOMKilled lives in `docker inspect`; --rm would race the read.
    assert "--rm=false" in args
    assert "--rm" not in args


def test_runtime_can_be_omitted_for_runtimeless_hosts() -> None:
    args = docker_run_args(image="img", budget=BUDGET, runtime=None)
    assert not [a for a in args if a.startswith("--runtime")]


def test_with_env_overrides_and_keeps_the_rest() -> None:
    policy = DEFAULT_POLICY.with_env(TMPDIR="/nope", EXTRA="1")
    env = dict(policy.env)
    assert env["TMPDIR"] == "/nope"
    assert env["EXTRA"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert dict(DEFAULT_POLICY.env)["TMPDIR"] == "/nonexistent"  # unchanged
