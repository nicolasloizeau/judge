# Build `judge`: sandboxed verification library

Build a Python monorepo implementing `judge`, a library that runs an untrusted verifier (Python source, a string) against an untrusted solution (a string) inside a sandbox with a resource budget, and returns a structured verdict. It will be used by a marketplace platform (Django) where posters publish verifiers and solvers submit solutions for bounties. Both inputs are hostile.

## Core contract

```python
run(verifier_src: str, solution: str, budget: Budget) -> Verdict
```

- The verifier module must define `verify(solution: str, rng: numpy.random.Generator) -> bool`.
- `ACCEPTED` iff `verify` returns exactly `True` within budget. Everything else — timeout, OOM, crash, exception, non-`True` return, sandbox violation — is a non-acceptance with a diagnostic `reason`. Single blunt budget covering verifier + solution together; no attribution.
- `rng` is platform-seeded per verification (CSPRNG-drawn seed, `np.random.default_rng(seed)`); the seed is recorded in the `Verdict`, so any verdict is reproducible from `(verifier_src, solution, budget, image_digest, seed)`.

## Repo layout

Monorepo, two independently installable packages sharing the `judge.` import root via PEP 420 native namespace packages (no `__init__.py` at the namespace root). Separate `pyproject.toml` per package. One CI config. Version both `0.1.0`.

```
judge/
  judge-core/     judge.core     — pure Python, stdlib only
  judge-python/   judge.python   — depends on judge-core
```

## judge-core

- **Types** (frozen dataclasses): `Budget(wall_s, cpu_s, mem_bytes, max_solution_bytes, build_s=None, build_mem=None)`; `Verdict(accepted: bool, reason: Reason, used: Budget, seed: int, image_digest: str, host: dict)`; `Reason` enum: `ACCEPTED, REJECTED, TIMEOUT, OOM, CRASH, VERIFIER_FAULT, BUILD_FAILED, SOLUTION_TOO_LARGE, INTERNAL_ERROR`. `build_*` and `BUILD_FAILED` are reserved for future compiled-language siblings; unused here.
- **`Backend` protocol**: `run(verifier_src, solution, budget) -> Verdict`.
- **Sandbox flag policy** as data: no network (`--network=none`), read-only rootfs, no mounts, no tmpfs, `--pids-limit`, all capabilities dropped, non-root uid, `no-new-privileges`, `PYTHONDONTWRITEBYTECODE=1`, `TMPDIR` pointing at a nonexistent path. Zero writable bytes anywhere.
- **`GvisorBackend`**: runs a language image under Docker with `--runtime=runsc` plus the flag policy; passes inputs via stdin (JSON), reads a JSON verdict line from stdout; enforces wall clock from outside with kill; maps container OOM-kill and nonzero exits to reasons. Records image digest and host diagnostics.
- **`LocalInsecureBackend`**: subprocess + `resource.setrlimit` (CPU, AS). Development only; constructor requires `i_understand_this_is_insecure=True` and logs a warning.
- **Selftest harness**: generic runner taking language-specific adversarial fixtures — `(name, verifier_src, solution, expected_reason)` — executing each against a backend and reporting pass/fail. This suite is the security claim; make it first-class.

## judge-python

- **Image**: Dockerfile building a pinned image — specific Python version, `numpy`, `scipy`, `sympy`, `mpmath`, exact versions in a committed manifest. Non-root user. Entrypoint is the harness. Expose the image digest.
- **In-sandbox harness**: reads JSON from stdin, enforces `max_solution_bytes`, seeds the RNG, executes `verifier_src` in a fresh module namespace, calls `verify(solution, rng)`, catches everything, writes one JSON verdict line to stdout. The harness never trusts the verifier: a verifier printing garbage to stdout must not be able to forge a verdict — frame the verdict line unambiguously (distinctive sentinel, last-line-wins).
- **Fixtures** for the selftest harness: fork bomb → `VERIFIER_FAULT`/`CRASH`; socket creation → fails, mapped consistently; file write attempt → fails; memory balloon → `OOM`; infinite loop → `TIMEOUT`; `os._exit(0)` and fake-verdict-on-stdout → must not yield `ACCEPTED`; verifier returning `1` (truthy, not `True`) → `REJECTED`; import of a missing package → `VERIFIER_FAULT`; honest accept and honest reject → `ACCEPTED`/`REJECTED`.
- **CLI** (`judge` entrypoint): `judge build-image`; `judge verify --verifier f.py --solution s.txt --budget ...` (pretty-prints the Verdict); `judge selftest [--backend gvisor|local]`.

## Testing & docs

- Unit tests for types, policy, harness parsing (no Docker required).
- Full selftest suite against `LocalInsecureBackend` always in CI; against `GvisorBackend` when Docker+runsc is available, skipping cleanly otherwise (target host: Ubuntu).
- README states verbatim: the blunt-timeout rule ("verifier and solution must together complete within budget; anything other than `verify` returning `True` is non-acceptance"), the no-network/zero-writable-bytes guarantee, the reproducibility tuple, and that a verifier which `exec`s the solution does so at its own risk.

## Style

Python ≥3.11, full type hints, `ruff` + `mypy` clean, minimal dependencies (core: stdlib only). Keep `judge-python` thin — anything language-agnostic belongs in core.
