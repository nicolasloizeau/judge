# judge-python

Python-language support for [`judge`](../README.md): the pinned sandbox image,
the in-sandbox harness, the adversarial fixture set and the `judge` CLI.
Depends on `judge-core`.

```python
from judge.core import Budget
from judge.python import run

verdict = run(verifier_src, solution, Budget(10.0, 10.0, 512 << 20, 64 << 10))
```

A verifier is a Python module defining:

```python
def verify(solution: str, rng: numpy.random.Generator) -> bool: ...
```

`ACCEPTED` iff it returns exactly `True` within budget. See the root README for
the budget rule, the containment guarantee and the reproducibility tuple.

## Contents

- `image/Dockerfile`, `image/requirements.txt` — the pinned image: a
  digest-pinned Python base plus exact `numpy` / `scipy` / `sympy` / `mpmath`
  versions, non-root, harness as entrypoint.
- `harness.py` — runs inside the sandbox: reads the request, enforces
  `max_solution_bytes`, applies rlimits, blinds stdout, execs the verifier in a
  fresh namespace, writes one framed verdict line.
- `fixtures.py` — the threat model in executable form.
- `cli.py` — `judge build-image`, `judge verify`, `judge selftest`.
