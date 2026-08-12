# Python API

Two importable roots sharing the `judge.` namespace: `judge.core` (stdlib only) and
`judge.python`. Both ship `py.typed`, so `mypy` sees full annotations.

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

Runs `verifier_src` against `solution` under `budget`. `ACCEPTED` iff
`verify(solution, rng)` returns exactly `True` within budget.

**Never raises for hostile input** — every failure mode comes back as a
non-accepting `Verdict`. It *does* raise for operator errors: `BudgetError` for an
unenforceable budget, `OSError` if Docker is missing.

```python
from judge.core import Budget
from judge.python import run

verdict = run(verifier_src, solution, Budget(10.0, 10.0, 512 << 20, 64 << 10))
```

### Reusing a backend

`backend` defaults to a fresh `GvisorBackend` per call. Pass one in to reuse it
across many verifications — it caches the image digest, saving a
`docker image inspect` each time:

```python
from judge.python import gvisor_backend

backend = gvisor_backend()
for solution in submissions:
    verdict = run(verifier_src, solution, budget, backend=backend)
```

There is still one fresh container per verification; nothing is reused inside the
sandbox.

## Backends

```python
gvisor_backend(image="judge-python:0.1.0", **kwargs)   # production
local_backend(**kwargs)                                # development only
default_backend()                                      # -> gvisor_backend()
```

| | `gvisor` | `local-insecure` |
| --- | --- | --- |
| Mechanism | container under `--runtime=runsc` | child process + `setrlimit` |
| Network | none | **reachable** |
| Filesystem | read-only, no mounts | **writable** |
| Needs Docker | yes | no |
| Speed | ~1.4 s per run | ~0.2 s per run |
| Use for | production | your own code only |

`default_backend()` is gVisor with no automatic fallback — a silent downgrade from
sandboxed to unsandboxed is exactly the bug you don't want.

`LocalInsecureBackend` refuses to construct without
`i_understand_this_is_insecure=True` and logs a warning every time one is built.
`local_backend()` sets that flag for you.

Check availability before trusting it:

```python
backend = gvisor_backend()
if not backend.available():        # Docker reachable AND runsc registered
    raise SystemExit("gVisor unavailable; refusing to run unsandboxed")
```

`gvisor_backend()` passes extra keyword arguments through to `GvisorBackend`:
`policy`, `runtime`, `docker`, `grace_s`.

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
    seed: int
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

`judge.core.protocol.verdict_from_outcome()` does the hard part — folding stdout,
exit code, timeout and OOM flags into a `Verdict` — so a new backend should call it
rather than mapping exit codes itself.

## Also in `judge.core`

Everything is re-exported from the package root.

| Name | What |
| --- | --- |
| `DEFAULT_POLICY`, `SandboxPolicy` | Sandbox restrictions, as a frozen dataclass |
| `describe_policy()`, `docker_run_args()` | Render the policy for humans / for Docker |
| `run_selftest()`, `Fixture`, `DEFAULT_BUDGET` | The adversarial suite |
| `Request`, `Result`, `extract_result()` | The backend↔sandbox wire protocol |

## Also in `judge.python`

| Name | What |
| --- | --- |
| `build_image()`, `image_digest()`, `image_exists()` | Manage the sandbox image |
| `ALL_FIXTURES` | The adversarial fixture set |
| `harness.run_verifier()` | Pure classification of one verifier — no I/O, handy in tests |
| `DEFAULT_IMAGE_TAG` | `"judge-python:0.1.0"` |

!!! note "Hand-maintained"

    Signatures here are transcribed from the source. The module docstrings are the
    authority, and `help()` on any of the above is more detailed than this page.
