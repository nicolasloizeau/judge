# Writing verifiers

A verifier is a Python module — handed to `judge` as a string — defining:

```python
def verify(solution: str, rng: numpy.random.Generator) -> bool: ...
```

Return `True` to accept. Anything else — a different return value, an exception, a
hang, a crash — is a non-acceptance.

## Example: check an answer

```python
def verify(solution: str, rng) -> bool:
    return solution.strip() == "42"
```

## Example: run the submitted solution

The common case. The solution is code; the verifier executes it and spot-checks
the results against a reference.

```python
import types


def reference(n: int) -> int:
    return sum(range(n + 1))


def verify(solution: str, rng) -> bool:
    module = types.ModuleType("submission")
    exec(compile(solution, "<solution>", "exec"), module.__dict__)

    solve = module.__dict__.get("solve")
    if not callable(solve):
        return False

    for _ in range(50):
        n = int(rng.integers(1, 1000))
        if solve(n) != reference(n):
            return False
    return True
```

A solution defining `def solve(n): return n * (n + 1) // 2` is `ACCEPTED`; one
returning the wrong number is `REJECTED`.

!!! warning "The solution runs in your process"

    A solution that raises takes the verifier down with it, and the verdict reads
    `VERIFIER_FAULT` — attributed to *you*. Catch it if you want a crashing
    solution to count as a wrong answer:

    ```python
        try:
            got = solve(n)
        except Exception:
            return False     # REJECTED instead of VERIFIER_FAULT
    ```

    You cannot defend against the solution burning the clock or the memory; those
    are enforced from outside and come back as `TIMEOUT` or `OOM`. Leave it room.

## Example: probabilistic check

`rng` is a `numpy.random.Generator`, seeded per verification from the platform's
CSPRNG. The seed is recorded in the verdict, so the same inputs replay to the same
draws.

```python
def verify(solution: str, rng) -> bool:
    n = int(solution.strip())
    if n < 5:
        return False
    for _ in range(64):
        a = int(rng.integers(2, n - 1))
        if pow(a, n - 1, n) != 1:     # Fermat witness
            return False
    return True
```

Use `rng` and nothing else. `random.random()`, `numpy.random.rand()` and
`os.urandom()` draw entropy that is not recorded, so a verdict that depended on
them cannot be replayed.

## Return `True`, exactly

`judge` checks `result is True`, not `bool(result)`:

```python
return 1                     # REJECTED  (1 == True, but is not True)
return np.all(a == b)        # REJECTED  (np.True_ is truthy, not True)
return bool(np.all(a == b))  # correct
```

`bool(x)` on an untrusted object calls untrusted `__bool__`, and an array of the
wrong shape should not accept a submission by accident. Wrap computed results in
`bool(...)`.

## What is available

Python 3.12.7, the standard library, and a pinned scientific stack:

| Package | Version |
| --- | --- |
| `numpy` | 2.1.3 |
| `scipy` | 1.14.1 |
| `sympy` | 1.13.3 |
| `mpmath` | 1.3.0 |

An import of anything else is a `VERIFIER_FAULT`. BLAS and OpenMP are pinned to one
thread, so parallel NumPy will not help you.

## What will not work

| Attempt | What happens |
| --- | --- |
| Sockets, DNS | Fail — the sandbox has no network |
| Writing any file | Fails — read-only filesystem, no `/tmp`, `RLIMIT_FSIZE=0` |
| `print()` | Succeeds, goes to `/dev/null`. The return value is the only channel out |
| Spawning processes | Starts, achieves nothing — same restrictions, capped at 64 pids |
| Caching between runs | Nothing persists. One fresh container per verification |

There is also no way to attach a message to a `REJECTED` verdict. `Verdict.detail`
carries a diagnostic, but it is derived from untrusted output — log it, never
branch on it.

## Iterate quickly

The local backend skips Docker, so a run takes ~0.2 s instead of ~1.4 s. It does
**not** sandbox anything, so only point it at code you wrote yourself:

```python
from judge.python import local_backend, run

verdict = run(verifier_src, solution, budget, backend=local_backend())
```

Then confirm against the real backend before publishing — the local one cannot
tell you whether your verifier accidentally relies on a writable `/tmp` or a
reachable network:

```bash
judge verify --verifier verifier.py --solution solution.txt
```

## Checklist

- [ ] `verify(solution, rng)` defined at module level
- [ ] Accepting path returns `True` exactly — `bool(...)`-wrapped if computed
- [ ] Randomness comes from `rng`
- [ ] Solution exceptions caught, if they should read as `REJECTED`
- [ ] Setup leaves the solution enough budget to run
- [ ] Passes against the gVisor backend, not just the local one
