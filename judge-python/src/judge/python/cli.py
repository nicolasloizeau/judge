"""``judge`` command line: build the image, verify one pair, run the selftest."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from judge.core.backend import Backend
from judge.core.policy import describe_policy
from judge.core.selftest import DEFAULT_BUDGET, CaseResult, run_selftest
from judge.core.types import Budget
from judge.python import gvisor_backend, local_backend
from judge.python.fixtures import ALL_FIXTURES
from judge.python.image import DEFAULT_IMAGE_TAG, ImageError, build_image

MIB = 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="judge",
        description="Run untrusted verifiers against untrusted solutions in a sandbox.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log at DEBUG level")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-image", help="build the pinned judge-python image")
    build.add_argument("--tag", default=DEFAULT_IMAGE_TAG)
    build.add_argument("--no-cache", action="store_true")
    build.add_argument("--quiet", action="store_true")

    verify = sub.add_parser("verify", help="verify one solution and print the verdict")
    verify.add_argument("--verifier", required=True, type=Path, help="path to the verifier module")
    verify.add_argument("--solution", required=True, type=Path, help="path to the solution")
    verify.add_argument("--json", action="store_true", help="emit the Verdict as JSON")
    _add_backend_args(verify)
    _add_budget_args(verify)

    selftest = sub.add_parser("selftest", help="run the adversarial fixture suite")
    selftest.add_argument("--policy", action="store_true", help="print the sandbox policy first")
    _add_backend_args(selftest)
    _add_budget_args(selftest, defaults=DEFAULT_BUDGET)

    return parser


def _add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("gvisor", "local"), default="gvisor")
    parser.add_argument("--image", default=DEFAULT_IMAGE_TAG)
    parser.add_argument("--runtime", default="runsc")


def _add_budget_args(parser: argparse.ArgumentParser, defaults: Budget | None = None) -> None:
    d = defaults or Budget(
        wall_s=10.0, cpu_s=10.0, mem_bytes=512 * MIB, max_solution_bytes=64 * 1024
    )
    parser.add_argument("--wall-s", type=float, default=d.wall_s)
    parser.add_argument("--cpu-s", type=float, default=d.cpu_s)
    parser.add_argument("--mem-mib", type=int, default=d.mem_bytes // MIB)
    parser.add_argument("--max-solution-bytes", type=int, default=d.max_solution_bytes)


def _budget_from_args(args: argparse.Namespace) -> Budget:
    budget = Budget(
        wall_s=args.wall_s,
        cpu_s=args.cpu_s,
        mem_bytes=args.mem_mib * MIB,
        max_solution_bytes=args.max_solution_bytes,
    )
    budget.validate()
    return budget


def _backend_from_args(args: argparse.Namespace) -> Backend:
    if args.backend == "local":
        return local_backend()
    return gvisor_backend(args.image, runtime=args.runtime)


def _cmd_build_image(args: argparse.Namespace) -> int:
    digest = build_image(tag=args.tag, no_cache=args.no_cache, quiet=args.quiet)
    print(f"built {args.tag}\nimage_digest: {digest}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    verifier_src = args.verifier.read_text(encoding="utf-8")
    solution = args.solution.read_text(encoding="utf-8")
    verdict = _backend_from_args(args).run(verifier_src, solution, _budget_from_args(args))
    print(verdict.to_json(indent=2) if args.json else verdict.pretty())
    return 0 if verdict.accepted else 1


def _cmd_selftest(args: argparse.Namespace) -> int:
    backend = _backend_from_args(args)
    if args.policy:
        print(describe_policy())
        print()

    def echo(result: CaseResult) -> None:
        print(result.describe(), flush=True)

    print(f"judge selftest -- backend: {backend.name}\n", flush=True)
    report = run_selftest(backend, ALL_FIXTURES, budget=_budget_from_args(args), on_result=echo)
    print(f"\n{report.passed} passed, {len(report.failed)} failed, {report.skipped} skipped")
    if report.false_accepts:
        names = ", ".join(r.fixture.name for r in report.false_accepts)
        print(f"!! FALSE ACCEPT in: {names}")
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        "build-image": _cmd_build_image,
        "verify": _cmd_verify,
        "selftest": _cmd_selftest,
    }
    try:
        return handlers[args.command](args)
    except (ImageError, OSError, ValueError) as exc:
        print(f"judge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
