# Verdicts

Every verification returns a `Verdict`. Hostile input never raises — it comes back
as a non-accepting verdict.

## Reasons

```python
verdict.reason      # this is the field to branch on
verdict.accepted    # shorthand for `reason is Reason.ACCEPTED`
```

| `Reason` | What happened |
| --- | --- |
| `ACCEPTED` | Python: `verify` returned exactly `True`. Lean: the kernel accepted `answer : Spec` under the allowlist. Within budget |
| `REJECTED` | Python: `verify` returned something that is not `True`. Lean: the solution did not elaborate, or the kernel or the allowlist refused it |
| `TIMEOUT` | wall clock or CPU budget exhausted (in any stage) |
| `OOM` | memory budget exhausted (in any stage) |
| `CRASH` | the sandbox died, or produced no verdict |
| `VERIFIER_FAULT` | Python: the verifier raised, would not compile, or defines no `verify` |
| `BUILD_FAILED` | Lean: the verifier did not elaborate, defines no usable `Spec`, uses a disallowed axiom, or has a bad `-- judge: allow` header |
| `SOLUTION_TOO_LARGE` | the solution exceeded `max_solution_bytes` |
| `INTERNAL_ERROR` | judge itself failed — e.g. Docker would not start, or the checker binary is missing |

`ACCEPTED` is the only accepting outcome. The other values are for diagnostics, not
policy — which one a given hostile input produces can depend on the runtime, so
don't build scoring rules that treat `TIMEOUT` differently from `REJECTED`.

`VERIFIER_FAULT` and `BUILD_FAILED` are the same situation — the verifier is
broken, the solution never got a fair run — for an interpreted and a compiled
language respectively. Treat them alike.

`Reason` is a `StrEnum`, so `verdict.reason == "ACCEPTED"` works and JSON
round-trips for free.

## Fields

```python
@dataclass(frozen=True, slots=True)
class Verdict:
    accepted: bool
    reason: Reason
    used: Budget          # measured usage
    seed: int             # the CSPRNG seed this run used (0 for Lean)
    image_digest: str     # exactly which image ran it
    host: dict            # backend, runtime, docker version, kernel, ...
    detail: str           # short diagnostic
```

`used`
:   Measured usage, in a `Budget` for symmetry. `used.wall_s` is measured from
    outside and is the reliable one; `cpu_s` and `mem_bytes` are best-effort
    (`getrusage` in Python; the harness' own accounting in Lean, where
    `used.wall_s` is the sum over stages and `used.mem_bytes` the maximum).
    `used.max_solution_bytes` carries the *actual* solution size.

`host`
:   Diagnostics about where it ran. Lean adds `stage` (which stage decided),
    `axioms` (what the accepted proof depended on) and `spec_is_prop`.

`detail`
:   Human-readable diagnostic — exception text, exit code, the repr of what
    `verify` returned, the kernel's message. **Derived from untrusted output: log
    it, never branch on it.**

Serialising: `as_dict()`, `to_json(indent=2)`, and `pretty()` for the CLI's
multi-line rendering.

## Budgets

```python
Budget(
    wall_s=10.0,                     # wall-clock seconds
    cpu_s=10.0,                      # CPU seconds
    mem_bytes=512 * 1024 * 1024,     # memory cap
    max_solution_bytes=64 * 1024,    # max solution size, UTF-8 bytes
)
```

Those values are `judge`'s defaults for Python and a reasonable starting point.
One budget covers the verifier and the solution together.

For Lean the same budget is applied to **each stage** independently — building
the verifier, elaborating the solution, checking — and the defaults are larger
(`judge-lean`'s CLI uses 300 s and 8 GiB) because importing Mathlib costs
seconds and gigabytes before any proof is looked at.

Two practical notes:

- **Container startup is not free.** A trivial verification takes about 1.3 s wall
  clock under gVisor (Python), and a Lean verification is three to four
  containers. Budgeting `wall_s=1.0` leaves a verifier no time at all.
- **`cpu_s` below `wall_s`** is the useful shape when a verifier may wait on
  something but should not spin.

`Budget.validate()` raises `BudgetError` for anything unenforceable — a
non-positive `wall_s`, `cpu_s`, `mem_bytes` or `max_solution_bytes`. Backends call
it for you on entry to `run()`.

The `build_s` and `build_mem` fields are reserved for a language that wants a
separate build envelope. Neither Python nor Lean uses them; leave them `None`.

## Reproducibility

A Python verdict is replayable from `(verifier_src, solution, budget,
image_digest, seed)`. The seed is drawn per run, used to build
`numpy.random.default_rng(seed)` inside the sandbox, and recorded. Same tuple,
same random draws.

A Lean verdict is replayable from `(verifier_src, solution, budget,
image_digest)`. There is no randomness; `seed` is recorded as `0` and unused.

What that does not promise: identical `used` measurements (wall clock and RSS vary),
or the same verdict from a verifier that reaches for entropy outside `rng`.

!!! note "Replay is not a one-liner yet"

    `judge.python.run()` always draws a fresh seed; there is no parameter to pass
    a recorded one back in. Everything needed is recorded, but replaying today
    means driving `judge.python.harness` yourself with a hand-built `Request`.
    For Lean, calling `run()` again with the same inputs and image *is* the replay.
