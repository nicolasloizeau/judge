# judge-core

Language-agnostic half of [`judge`](../README.md): the types, the sandbox flag
policy, the backends and their single-process primitives, and the adversarial
selftest harness. Pure Python, stdlib only, no dependencies.

```python
from judge.core import Budget, GvisorBackend, Reason, Verdict, run_selftest
```

- `Budget`, `Verdict`, `Reason` — frozen, JSON-round-trippable value types.
- `Backend` — the `run(verifier_src, solution, budget) -> Verdict` protocol.
- `SandboxPolicy` / `BindMount` / `docker_run_args` — the containment rules as
  data: no network, read-only rootfs, no `/dev/shm`, all capabilities dropped,
  non-root uid, `no-new-privileges`, pids limit; no mounts by default, and an
  explicit allowance of read-only bind mounts plus at most one read-write one
  for a language that has to write build products.
- `GvisorBackend` — Docker + `--runtime=runsc`, wall clock enforced from
  outside. `run_container()` is its single-container primitive.
- `LocalInsecureBackend` — development only; sandboxes nothing and says so.
  `run_process()` is its single-process primitive (rlimits, process group,
  wall-clock kill, optional RSS watchdog, `wait4` rusage).
- `RawOutcome` — what one sandboxed process did, with `limit_reason()` as the
  one place that maps exit codes and kill flags to reasons.
- `Fixture` / `run_selftest` — the generic adversarial runner. Language packages
  supply the fixtures.

A language package (`judge-python`, `judge-lean`, and future siblings) brings
an image, a harness and a fixture set; a single-process language uses the
backends as they are, a multi-stage one composes the primitives. Everything
else lives here.
