# judge

Run an untrusted verifier against an untrusted solution in a sandbox, with a
resource budget. Get back a structured verdict.

```python
from judge.core import Budget
from judge.python import run

verifier_src = """
def verify(solution: str, rng) -> bool:
    return solution.strip() == "42"
"""

budget = Budget(
    wall_s=10.0,
    cpu_s=10.0,
    mem_bytes=512 * 1024 * 1024,
    max_solution_bytes=64 * 1024,
)

verdict = run(verifier_src, "42", budget)

verdict.accepted   # True
verdict.reason     # ACCEPTED
```

Give it a wrong solution and you get the same shape back, never an exception:

```python
verdict = run(verifier_src, "41", budget)

verdict.accepted   # False
verdict.reason     # REJECTED
verdict.detail     # 'verify() returned False, not True'
```

That is the whole API. Everything else is detail.

## Install

```bash
git clone https://github.com/nicolasloizeau/judge
cd judge
pip install -e judge-core -e judge-python
judge build-image        # needs Docker
```

The sandbox runs under [gVisor](https://gvisor.dev), so Docker needs the `runsc`
runtime registered. Check with:

```bash
docker info --format '{{json .Runtimes}}' | grep -o runsc     # should print runsc
```

??? note "Installing gVisor on Ubuntu"

    ```bash
    curl -fsSL https://gvisor.dev/archive.key \
      | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
      | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y runsc
    sudo runsc install
    sudo systemctl restart docker
    ```

    Same steps CI runs; see `.github/workflows/ci.yml`.

## From the command line

```bash
judge verify --verifier verifier.py --solution solution.txt
```

```
ACCEPTED  (ACCEPTED)
  seed          : 4545743040626105188
  image_digest  : sha256:7e087273820365fa3f0eb54d6510f8bdeb06c29016260d37f1086a60f58074f1
  wall_s        : 1.321
  cpu_s         : 0.950
  mem_bytes     : 78422016
  solution_bytes: 3
  ...
```

Exits `0` on acceptance, `1` otherwise. See [CLI](cli.md).

## The verifier

A Python module, passed in as a string, that defines one function:

```python
def verify(solution: str, rng: numpy.random.Generator) -> bool: ...
```

Return `True` to accept. `numpy`, `scipy`, `sympy` and `mpmath` are available
inside. Use `rng` for anything random — it is seeded per run and the seed is
recorded, so verdicts replay.

More in [Writing verifiers](verifiers/index.md), or straight to
[Python verifiers](verifiers/python.md).

## Four things to know

**Return `True`, not something truthy.** `judge` checks `result is True`. Returning
`1` or `numpy.True_` gives you `REJECTED`. Wrap computed results in `bool(...)`.

**One budget covers both sides.** Verifier and solution share the wall clock, the
CPU time and the memory. No attribution, no partial credit. A verifier that
spends the whole budget setting up leaves the solution nothing.

**The sandbox has no network and nothing writable.** No sockets, no files, no
`/tmp`, no subprocess that can persist anything. `print()` goes to `/dev/null` —
the return value is the only channel out.

**Anything other than `True` is a non-acceptance.** Timeout, crash, exception,
out-of-memory — each gets its own `reason` for diagnostics, but none of them
accept. See [Verdicts](verdicts.md).

## Reading on

<div class="grid cards" markdown>

- :material-pencil-ruler: **[Writing verifiers](verifiers/index.md)**

    Worked examples, including how to run the submitted solution.

- :material-clipboard-check: **[Verdicts](verdicts.md)**

    Every `reason`, and how to set a budget.

- :material-console: **[CLI](cli.md)**

    `build-image`, `verify`, `selftest`.

- :material-language-python: **[Python API](api.md)**

    `run`, backends, and the types.

</div>

## Layout

Two installable packages sharing the `judge.` namespace:

| Package | Import | What |
| --- | --- | --- |
| `judge-core` | `judge.core` | Types, sandbox policy, backends, selftest harness. Stdlib only. |
| `judge-python` | `judge.python` | Python image, in-sandbox harness, fixtures, CLI. |

The [repository README](https://github.com/nicolasloizeau/judge) covers the
sandbox guarantees, the threat model and the adversarial selftest suite in full.
