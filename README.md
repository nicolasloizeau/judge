# judge

Run an **untrusted verifier** against an **untrusted solution** inside a sandbox
with a resource budget, and get back a structured verdict.

📖 **[Documentation](https://nicolasloizeau.github.io/judge/)**

```python
from judge.core import Budget
from judge.python import run

budget = Budget(wall_s=10.0, cpu_s=10.0, mem_bytes=512 * 1024 * 1024,
                max_solution_bytes=64 * 1024)

verdict = run(verifier_src, solution, budget)
verdict.accepted      # bool
verdict.reason        # Reason.ACCEPTED | REJECTED | TIMEOUT | OOM | CRASH | ...
verdict.seed          # the CSPRNG-drawn seed this run used
verdict.image_digest  # exactly which image ran it
```

The verifier is a Python module (as a string) that must define:

```python
def verify(solution: str, rng: numpy.random.Generator) -> bool: ...
```

Both inputs are hostile. Nothing here trusts either of them.

## The rules

**The budget is blunt.** Verifier and solution must together complete within
budget; anything other than `verify` returning `True` is non-acceptance. There is
one budget covering both, no attribution between them, and no partial credit: a
verifier that spends the whole wall clock setting up leaves the solution nothing,
and that is the verifier author's problem. `ACCEPTED` requires `verify` to return
the `True` singleton — `1`, `numpy.True_` and any other truthy value are
`REJECTED`.

**No network, zero writable bytes.** The sandbox runs with `--network=none`, a
read-only root filesystem, no volumes, no bind mounts, no tmpfs and no
`/dev/shm`, with `TMPDIR` and `HOME` pointing at a path that does not exist. All
capabilities are dropped, `no-new-privileges` is set, the process runs as a
non-root uid under a pids limit, and `PYTHONDONTWRITEBYTECODE=1` keeps the
interpreter from trying to write anything either. There is nowhere in the
container a verifier or solution can persist a single byte, and nothing it can
reach off-host.

**Every verdict is reproducible.** Replay it from
`(verifier_src, solution, budget, image_digest, seed)`. The seed is drawn per
verification from the platform's CSPRNG, handed to the sandbox, used to build
`numpy.random.default_rng(seed)`, and recorded in the `Verdict`. Two runs of the
same tuple see the same random draws.

**A verifier which `exec`s the solution does so at its own risk.** That is a
supported and common pattern — most verifiers need to run the thing they are
judging — but it hands control of the verification process to the solution.
The solution can then crash it, hang it, or make it return something other than
`True`; all of those are non-acceptances, and judge will not tell you which side
caused them. The sandbox contains both equally; it does not protect the verifier
from the solution.

## Verdicts

| `Reason`             | What happened                                                      |
| -------------------- | ------------------------------------------------------------------ |
| `ACCEPTED`           | `verify` returned exactly `True`, within budget                    |
| `REJECTED`           | `verify` returned something that is not `True`                     |
| `TIMEOUT`            | wall clock or CPU budget exhausted                                 |
| `OOM`                | memory budget exhausted                                            |
| `CRASH`              | the sandbox died, or produced no verdict at all                    |
| `VERIFIER_FAULT`     | the verifier module raised, would not compile, or defines no `verify` |
| `SOLUTION_TOO_LARGE` | the solution exceeded `max_solution_bytes`                         |
| `INTERNAL_ERROR`     | judge itself failed (e.g. the sandbox would not launch)            |
| `BUILD_FAILED`       | reserved for future compiled-language siblings; unused here        |

`accepted` is always derived from `reason`, never read from anything the sandbox
printed.

## Layout

Two independently installable packages sharing the `judge.` import root via
[PEP 420](https://peps.python.org/pep-0420/) native namespace packages (there is
deliberately no `__init__.py` at the `judge/` root).

```
judge-core/     judge.core     pure Python, stdlib only: types, sandbox policy,
                               backends, the selftest harness
judge-python/   judge.python   the Python image, the in-sandbox harness,
                               adversarial fixtures, the CLI
```

Anything language-agnostic belongs in `judge.core`; `judge.python` stays thin so
that a `judge-rust` or `judge-c` sibling only has to bring an image, a harness
and a fixture set.

## Install

```bash
pip install -e judge-core -e judge-python
judge build-image                 # needs Docker; builds the pinned image
```

## CLI

```bash
judge build-image [--tag judge-python:0.1.0] [--no-cache]
judge verify --verifier f.py --solution s.txt [--wall-s 10] [--cpu-s 10] \
             [--mem-mib 512] [--max-solution-bytes 65536] [--json]
judge selftest [--backend gvisor|local] [--policy]
```

`judge verify` exits 0 on acceptance and 1 otherwise.

## Backends

**`GvisorBackend`** is the real one: one container per verification, the pinned
image under `--runtime=runsc`, inputs on stdin as JSON, a framed verdict line on
stdout. The wall clock is enforced *from outside* with a hard kill, because
anything inside can defeat an in-process timer. Container OOM-kills and nonzero
exits are mapped to reasons; the image digest and host diagnostics are recorded.

**`LocalInsecureBackend`** is a subprocess with `resource.setrlimit`, for
development only. Its constructor requires `i_understand_this_is_insecure=True`
and it logs a warning every time it is built. It does not sandbox anything: the
filesystem is writable, the network is reachable, and the kernel is fully
exposed. Never point it at hostile input.

## Why a verifier cannot forge acceptance

The verifier runs in the same process as the in-sandbox harness, so it can print
whatever it wants. Four things stand between that and a forged `ACCEPTED`:

1. the harness points fd 1 at `/dev/null` for the entire duration of verifier
   execution, so the verifier cannot write to the real stdout at all;
2. the verdict line is framed with a distinctive sentinel plus a per-run nonce
   that the backend generated and the verifier cannot guess;
3. the backend takes the **last** correctly framed line, and the harness writes
   its line as the very last thing it does before `os._exit`;
4. the backend derives `accepted` from `reason` and overrides both when it
   observed a timeout or an OOM-kill from outside.

Forged frames, `os._exit(0)`, and a missing verdict line all come out as
non-acceptance. None of this is the security boundary — the sandbox is. This
layer stops a verifier from lying about its own result.

## The selftest suite is the security claim

`judge selftest` runs an adversarial fixture set — fork bombs, thread bombs,
memory balloons, infinite loops that swallow signals, socket and DNS attempts,
file writes to `/tmp`, the cwd and `$HOME`, subprocess spawns, `os._exit(0)`,
forged verdict lines sprayed across every plausible file descriptor, truthy
non-`True` returns, missing imports — and asserts that each is contained, with a
separate check that nothing was accepted that should not have been.

```
$ judge selftest --backend gvisor
PASS  honest-accept                got=ACCEPTED           want=ACCEPTED    0.42s
PASS  fork-bomb                    got=VERIFIER_FAULT     want=CRASH|OOM|TIMEOUT|VERIFIER_FAULT   0.51s
PASS  fake-verdict-on-stdout       got=REJECTED           want=REJECTED    0.44s
...
```

Fixtures that depend on real isolation (network, filesystem) are **skipped**, not
faked, on backends that do not provide it — a green tick from a backend that
never contained anything would be worse than a red one.

If you can write a verifier that reaches `ACCEPTED` without `verify` returning
`True`, or that escapes the budget, it belongs in
`judge-python/src/judge/python/fixtures.py`.

## Tests

```bash
pytest                                    # unit tests + the suite on the local backend
pytest -m slow                            # the suite under gVisor (needs Docker + runsc)
ruff check . && ruff format --check .
mypy judge-core/src judge-python/src
```

CI runs lint, types, the unit tests and the full suite against
`LocalInsecureBackend` on every push, and the same suite under gVisor on an
Ubuntu runner with `runsc` installed.

## Limits worth knowing

- The harness and the verifier share one process. A verifier determined to walk
  Python frames can find the nonce in memory; the sandbox, not the framing, is
  what stops it from doing anything with that.
- `used.cpu_s` and `used.mem_bytes` are best-effort measurements reported by the
  harness (`getrusage`); `used.wall_s` is always measured from outside.
- Local image builds have no registry digest, so `image_digest` is the image
  config id. It changes whenever any layer does, which is what reproducibility
  needs.
- `Budget.build_s` / `build_mem` and `Reason.BUILD_FAILED` are reserved for
  future compiled-language siblings and are unused by `judge-python`.
