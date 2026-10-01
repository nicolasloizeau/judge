# Python API

Three importable roots sharing the `judge.` namespace: `judge.core` (stdlib
only), `judge.python` and `judge.lean`. All ship `py.typed`, so `mypy` sees full
annotations.

## `run`

```python
def run(
    verifier_src: str,
    solution: str,
    budget: Budget,
    *,
    backend: Backend | None = None,
) -> Verdict
```

Both language packages export a `run` of this shape. Runs `verifier_src` against
`solution` under `budget`. For Python, `ACCEPTED` iff `verify(solution, rng)`
returns exactly `True` within budget; for Lean, iff the kernel accepts
`answer : Spec` under the axiom allowlist, with `budget` applied to each stage.

**Never raises for hostile input** — every failure mode comes back as a
non-accepting `Verdict`. It *does* raise for operator errors: `BudgetError` for an
unenforceable budget, `OSError` if Docker is missing.

```python
from judge.core import Budget
from judge.python import run

verdict = run(verifier_src, solution, Budget(10.0, 10.0, 512 << 20, 64 << 10))
```

```python
from judge.core import Budget
from judge.lean import run

verdict = run(verifier_src, solution, Budget(300.0, 300.0, 8 << 30, 256 << 10))
```

### Reusing a backend

`backend` defaults to a fresh gVisor backend per call. Pass one in to reuse it
across many verifications — it caches the image digest, saving a
`docker image inspect` each time:

```python
from judge.python import gvisor_backend

backend = gvisor_backend()
for solution in submissions:
    verdict = run(verifier_src, solution, budget, backend=backend)
```

There is still one fresh container per verification (per stage, for Lean);
nothing is reused inside the sandbox. The Lean backend rebuilds the verifier
every time; a cache keyed by the verifier source and the image digest is a
planned, optional constructor argument.

## Backends

```python
# judge.python
gvisor_backend(image="judge-python:0.1.0", **kwargs)   # production
local_backend(**kwargs)                                # development only
default_backend()                                      # -> gvisor_backend()

# judge.lean
gvisor_backend(image="judge-lean:0.1.0", **kwargs)     # production: three containers
local_backend(project=None, **kwargs)                  # development only: ~/.elan + the checkout
default_backend()                                      # -> gvisor_backend()
```

| | `gvisor` | `local-insecure` |
| --- | --- | --- |
| Mechanism | container under `--runtime=runsc` | child process + `setrlimit` |
| Network | none | **reachable** |
| Filesystem | read-only; Lean adds one scratch mount per stage | **writable** |
| Needs Docker | yes | no (Lean: needs the toolchain and a built Lake project) |
| Speed (Python) | ~1.4 s per run | ~0.2 s per run |
| Speed (Lean, core only) | ~5 s per run | ~0.5 s per run |
| Use for | production | your own code only |

`default_backend()` is gVisor with no automatic fallback — a silent downgrade from
sandboxed to unsandboxed is exactly the bug you don't want.

The local backends refuse to construct without
`i_understand_this_is_insecure=True` and log a warning every time one is built.
`local_backend()` sets that flag for you.

Check availability before trusting it:

```python
backend = gvisor_backend()
if not backend.available():        # Docker reachable AND runsc registered
    raise SystemExit("gVisor unavailable; refusing to run unsandboxed")
```

`judge.python.gvisor_backend()` passes extra keyword arguments through to
`GvisorBackend`: `policy`, `runtime`, `docker`, `grace_s`.
`judge.lean.gvisor_backend()` takes the same plus the Lean knobs: `allow` (the
base axiom allowlist), `fsize_limit`, `max_scratch_bytes`, `max_heartbeats`,
`threads`, `scratch_root`.

## Types

```python
from judge.core import Budget, BudgetError, Reason, Verdict
```

```python
@dataclass(frozen=True, slots=True)
class Budget:
    wall_s: float
    cpu_s: float
    mem_bytes: int
    max_solution_bytes: int
    build_s: float | None = None      # reserved, unused
    build_mem: int | None = None      # reserved, unused
```

```python
@dataclass(frozen=True, slots=True)
class Verdict:
    accepted: bool
    reason: Reason
    used: Budget
    seed: int                         # 0 for Lean
    image_digest: str
    host: dict[str, Any]
    detail: str
```

Build verdicts with `Verdict.make(reason, ...)`, which derives `accepted` from
`reason` rather than trusting the two to stay consistent. See
[Verdicts](verdicts.md).

## Backend protocol

Any object of this shape qualifies — it is a `runtime_checkable` `Protocol`, so no
inheritance is needed:

```python
class Backend(Protocol):
    name: str
    provides_isolation: bool     # False for development backends

    def run(self, verifier_src: str, solution: str,
            budget: Budget) -> Verdict: ...
```

Two primitives underneath the shipped backends are reusable by a new language:

```python
GvisorBackend.run_container(argv, *, budget, stdin=None, policy=None) -> RawOutcome
run_process(argv, *, budget, stdin=None, cwd=None, env=None, limits=None) -> RawOutcome
```

Each runs *one* sandboxed process to completion and returns a `RawOutcome`
(stdout, stderr, exit code, measured wall, timeout / OOM / launch flags).
`RawOutcome.limit_reason()` answers "did a resource limit decide this?" in one
place, and `judge.core.protocol.verdict_from_outcome()` folds a single-process
harness run into a `Verdict`. A single-process language calls the latter; a
multi-stage one composes the primitives and maps each stage, as
`judge.lean.backend` does.

## Also in `judge.core`

Everything is re-exported from the package root.

| Name | What |
| --- | --- |
| `DEFAULT_POLICY`, `SandboxPolicy`, `BindMount` | Sandbox restrictions, as a frozen dataclass; the mount allowance |
| `describe_policy()`, `docker_run_args()` | Render the policy for humans / for Docker |
| `run_selftest()`, `Fixture`, `DEFAULT_BUDGET` | The adversarial suite |
| `Request`, `Result`, `extract_result()`, `extract_frame()` | The backend↔sandbox wire protocol |
| `RawOutcome`, `run_process()`, `ProcessLimits` | The single-process primitives |

## Also in `judge.python`

| Name | What |
| --- | --- |
| `build_image()`, `image_digest()`, `image_exists()` | Manage the sandbox image |
| `ALL_FIXTURES` | The adversarial fixture set |
| `harness.run_verifier()` | Pure classification of one verifier — no I/O, handy in tests |
| `DEFAULT_IMAGE_TAG` | `"judge-python:0.1.0"` |

## Also in `judge.lean`

| Name | What |
| --- | --- |
| `build_image()`, `image_digest()`, `project_dir()` | Manage the sandbox image and locate the Lake project |
| `ALL_FIXTURES`, `SELFTEST_BUDGET` | The adversarial fixture set and the budget the suite runs under |
| `DEFAULT_BUDGET` | The CLI's per-stage default, sized for `import Mathlib` |
| `DEFAULT_ALLOW`, `parse_allow_header()` | The axiom allowlist and the `-- judge: allow` header parser |
| `lean_policy()` | The sandbox policy with the file-size and uid adjustments Lean needs |
| `DEFAULT_IMAGE_TAG` | `"judge-lean:0.1.0"` |

!!! note "Hand-maintained"

    Signatures here are transcribed from the source. The module docstrings are the
    authority, and `help()` on any of the above is more detailed than this page.
