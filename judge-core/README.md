# judge-core

Language-agnostic half of [`judge`](../README.md): the types, the sandbox flag
policy, the backends and the adversarial selftest harness. Pure Python, stdlib
only, no dependencies.

```python
from judge.core import Budget, GvisorBackend, Reason, Verdict, run_selftest
```

- `Budget`, `Verdict`, `Reason` — frozen, JSON-round-trippable value types.
- `Backend` — the `run(verifier_src, solution, budget) -> Verdict` protocol.
- `SandboxPolicy` / `docker_run_args` — the containment rules as data: no
  network, read-only rootfs, no mounts of any kind, no `/dev/shm`, all
  capabilities dropped, non-root uid, `no-new-privileges`, pids limit.
- `GvisorBackend` — Docker + `--runtime=runsc`, wall clock enforced from outside.
- `LocalInsecureBackend` — development only; sandboxes nothing and says so.
- `Fixture` / `run_selftest` — the generic adversarial runner. Language packages
  supply the fixtures.

A language package (`judge-python`, and future siblings) brings an image, an
in-sandbox harness and a fixture set. Everything else lives here.
