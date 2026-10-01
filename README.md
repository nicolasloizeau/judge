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
verdict.seed          # the CSPRNG-drawn seed this run used (0 for Lean)
verdict.image_digest  # exactly which image ran it
```

Two languages. For **Python** the verifier is a module (as a string) that must
define:

```python
def verify(solution: str, rng: numpy.random.Generator) -> bool: ...
```

For **Lean** the verifier is a *type* and the checker is the Lean kernel: the
verifier is a module defining `Spec : Prop` (a theorem) or `Spec : Type` (a
witness to construct), the solution a module defining `answer`, and
`from judge.lean import run` accepts iff the kernel accepts `answer : Spec`.

Both inputs are hostile. Nothing here trusts either of them.

## The rules

**The budget is blunt.** Verifier and solution must together complete within
budget; anything other than `verify` returning `True` is non-acceptance. There is
one budget covering both, no attribution between them, and no partial credit: a
verifier that spends the whole wall clock setting up leaves the solution nothing,
and that is the verifier author's problem. `ACCEPTED` requires `verify` to return
the `True` singleton — `1`, `numpy.True_` and any other truthy value are
`REJECTED`. (For Lean the same budget applies to each of the stages below
independently, and acceptance is the kernel's, not a return value.)

**No network, zero writable bytes.** The sandbox runs with `--network=none`, a
read-only root filesystem, no volumes, no bind mounts, no tmpfs and no
`/dev/shm`, with `TMPDIR` and `HOME` pointing at a path that does not exist. All
capabilities are dropped, `no-new-privileges` is set, the process runs as a
non-root uid under a pids limit, and `PYTHONDONTWRITEBYTECODE=1` keeps the
interpreter from trying to write anything either. There is nowhere in the
container a verifier or solution can persist a single byte, and nothing it can
reach off-host. Lean has to write `.olean` files, so for Lean alone this is
relaxed to **one size-bounded scratch directory per verification** that is
bind-mounted read-write into exactly the stage that produces an output,
read-only into the stages that consume it, and deleted before `run()` returns;
see [Lean](#lean-the-verifier-is-a-type-the-checker-is-the-kernel).

**Every verdict is reproducible.** Replay a Python verdict from
`(verifier_src, solution, budget, image_digest, seed)`. The seed is drawn per
verification from the platform's CSPRNG, handed to the sandbox, used to build
`numpy.random.default_rng(seed)`, and recorded in the `Verdict`. Two runs of the
same tuple see the same random draws. A Lean verdict has no randomness and
replays from `(verifier_src, solution, budget, image_digest)`; `seed` is
recorded as `0`.

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
| `ACCEPTED`           | Python: `verify` returned exactly `True`. Lean: the kernel accepted `answer : Spec` under the axiom allowlist. Within budget |
| `REJECTED`           | Python: `verify` returned something that is not `True`. Lean: the solution did not elaborate, or the kernel or the allowlist refused it |
| `TIMEOUT`            | wall clock or CPU budget exhausted (in any stage)                  |
| `OOM`                | memory budget exhausted (in any stage)                             |
| `CRASH`              | the sandbox died, or produced no verdict at all                    |
| `VERIFIER_FAULT`     | Python: the verifier module raised, would not compile, or defines no `verify` |
| `BUILD_FAILED`       | Lean: the verifier did not elaborate, defines no usable `Spec`, or uses a disallowed axiom |
| `SOLUTION_TOO_LARGE` | the solution exceeded `max_solution_bytes`                         |
| `INTERNAL_ERROR`     | judge itself failed (e.g. the sandbox would not launch)            |

`accepted` is always derived from `reason`, never read from anything the sandbox
printed.

## Layout

Three independently installable packages sharing the `judge.` import root via
[PEP 420](https://peps.python.org/pep-0420/) native namespace packages (there is
deliberately no `__init__.py` at the `judge/` root).

```
judge-core/     judge.core     pure Python, stdlib only: types, sandbox policy,
                               backends and their single-process primitives,
                               the selftest harness
judge-python/   judge.python   the Python image, the in-sandbox harness,
                               adversarial fixtures, the `judge` CLI
judge-lean/     judge.lean     the Lean image (Lean + Mathlib + the trusted
                               checker), the three-stage backends, adversarial
                               fixtures, the `judge-lean` CLI
```

Anything language-agnostic belongs in `judge.core`; the language packages stay
thin so that a `judge-rust` or `judge-c` sibling only has to bring an image, a
harness and a fixture set.

## Install

```bash
pip install -e judge-core -e judge-python -e judge-lean
judge build-image                 # needs Docker; builds the pinned Python image
judge-lean build-image            # needs Docker; downloads Mathlib's oleans, takes a while
```

## CLI

```bash
judge build-image [--tag judge-python:0.1.0] [--no-cache]
judge verify --verifier f.py --solution s.txt [--wall-s 10] [--cpu-s 10] \
             [--mem-mib 512] [--max-solution-bytes 65536] [--json]
judge selftest [--backend gvisor|local] [--policy]

judge-lean build-image [--tag judge-lean:0.1.0] [--no-cache]
judge-lean verify --verifier Verifier.lean --solution Solution.lean [--wall-s 300] \
             [--cpu-s 300] [--mem-mib 8192] [--max-solution-bytes 262144] [--json]
judge-lean selftest [--backend gvisor|local] [--policy]
```

`verify` exits 0 on acceptance and 1 otherwise.

## Backends

**`GvisorBackend`** is the real one: one container per verification, the pinned
image under `--runtime=runsc`, inputs on stdin as JSON, a framed verdict line on
stdout. The wall clock is enforced *from outside* with a hard kill, because
anything inside can defeat an in-process timer. Container OOM-kills and nonzero
exits are mapped to reasons; the image digest and host diagnostics are recorded.
Its single-container primitive, `run_container()`, is what the Lean backend
composes three times over.

**`LocalInsecureBackend`** is a subprocess with `resource.setrlimit`, for
development only. Its constructor requires `i_understand_this_is_insecure=True`
and it logs a warning every time it is built. It does not sandbox anything: the
filesystem is writable, the network is reachable, and the kernel is fully
exposed. Never point it at hostile input. Its primitive, `run_process()`, is
likewise composed by the Lean local backend.

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

## Lean: the verifier is a type, the checker is the kernel

```lean
-- Verifier.lean
abbrev Spec : Prop := 2 + 2 = 4
```

```lean
-- Solution.lean
import Verifier
theorem answer : Spec := by decide
```

`ACCEPTED` iff `Solution.lean` elaborates, `answer` exists at the root, the
kernel accepts `answer : Spec` (via `addDecl` of a fresh declaration, not the
elaborator's `isDefEq`), the transitive axioms of `answer` are within the
allowlist, and both untrusted modules survive an independent kernel replay.
Everything else is non-acceptance. The default allowlist is `propext`,
`Classical.choice`, `Quot.sound`, which rejects `sorry`, user `axiom`s and
`native_decide`; a verifier may extend it with a `-- judge: allow ...` header
comment that Python parses as text before anything runs, and every name on it
must be an axiom of the trusted imports.

**Why three containers.** Elaborating Lean runs untrusted code: `run_cmd`,
macros, `#eval` and `initialize` execute at compile time with full `IO`. A
solution can add declarations without checking, switch the kernel off with
`debug.skipKernelTC`, write files, or exit 0. Therefore nothing decided inside
a process that elaborated untrusted source is trusted:

```
stage 1  build verifier      lean Verifier.lean  -> verifier/Verifier.olean      verifier/ rw
         check verifier      judge-lean-check --mode verifier                    everything ro
stage 2  elaborate solution  lean Solution.lean  -> solution/Solution.olean      verifier/ ro, solution/ rw
                             (stdout of this stage is never read)
stage 3  check               judge-lean-check --mode solution -> one framed line nothing writable
```

Every stage is a fresh container of the same image under the full sandbox
policy (gVisor, no network, all capabilities dropped, non-root, pids limit,
`no-new-privileges`). The verifier is compiled before the solution exists and
its olean is mounted read-only into the stages that consume it; a solution can
rewrite anything in its own scratch directory and nothing else. The verdict
comes from `judge-lean-check` alone — a Lean program that imports Lean core
only, runs with nothing writable, treats the two `.olean` files as data,
replays every declaration of the two untrusted modules through the kernel
(`Lean.Kernel.Environment.replay`, as `lean4checker` does), and only then asks
the kernel whether `answer : Spec`. **Only the two untrusted modules are
replayed; Mathlib and the core library are trusted as part of the
digest-pinned image.** The checker prints one framed JSON line and exits 0
whether its answer is yes or no; a nonzero exit means the checker itself failed
and maps to `CRASH` or `INTERNAL_ERROR`, never to a verdict.

The verifier is rebuilt on every `run()`; a cache keyed by
`sha256(verifier_src) + image_digest` can be added later as an optional
constructor argument without changing `run()`.

**Pinning.** `judge-lean/image/project/lean-toolchain` and `lake-manifest.json`
fix the Lean version (`v4.34.1`) and the Mathlib commit (tag `v4.34.1`). The
Dockerfile installs exactly those and never resolves "latest"; Mathlib's
prebuilt oleans are downloaded at image build only, like `pip install`.
Updating either pin changes the image digest, verdicts recorded under the old
digest replay only with the old image, and verifiers may need maintenance
after a Mathlib update.

**Where the pinned toolchain forced a deviation from the design**, the working
behaviour won and `judge-lean/README.md` lists each one: `native_decide` cannot
be allowlisted in Lean 4.34 (each use mints its own axiom in the untrusted
stage), `RLIMIT_AS` cannot be applied to Lean at all (the memory cap is the
cgroup under gVisor and an RSS watchdog locally), and the containers run as the
host's own non-root uid so the scratch directory stays deletable.

## The selftest suites are the security claim

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

`judge-lean selftest` does the same for Lean — `sorry`, user axioms,
`native_decide`, unchecked `addDecl`, `debug.skipKernelTC`, `unsafe answer`,
a solution redefining `Spec`, `#exit` and `IO.Process.exit` before `answer`, a
forged verdict printed during elaboration, an attempt to overwrite the
verifier's olean, a verifier with `sorry` in a helper, infinite loops and
memory balloons at elaboration time — and additionally checks the honest
paths through `Prop`, `Type`, a restated type, a `module`-system solution and
a Mathlib import.

```
$ judge-lean selftest --backend local
PASS  honest-accept-prop           got=ACCEPTED           want=ACCEPTED   0.47s
PASS  sorry-proof                  got=REJECTED           want=REJECTED   0.44s
        detail: `answer` depends on disallowed axioms: #[sorryAx]
PASS  skip-kernel-tc               got=REJECTED           want=REJECTED   1.22s
        detail: while replaying declaration 'answer': (kernel) declaration type mismatch ...
...
28 passed, 0 failed, 1 skipped
```

Fixtures that depend on real isolation (network, filesystem, read-only mounts)
are **skipped**, not faked, on backends that do not provide it — a green tick
from a backend that never contained anything would be worse than a red one.

If you can write a verifier that reaches `ACCEPTED` without `verify` returning
`True`, a Lean solution the kernel would not accept that is nonetheless
`ACCEPTED`, or anything that escapes the budget, it belongs in the language's
`fixtures.py`.

## Tests

```bash
pytest                                    # unit tests + both suites on the local backends
pytest -m slow                            # both suites under gVisor (needs Docker + runsc + images)
ruff check . && ruff format --check .
mypy judge-core/src judge-python/src judge-lean/src judge-core/tests judge-python/tests
mypy judge-lean/tests
```

The Lean suite on the local backend needs the pinned toolchain in `~/.elan` and
the Lake project built once (`lake exe cache get && lake build judge-lean-check`
in `judge-lean/image/project`); it skips cleanly otherwise.

CI runs lint, types, the unit tests and the Python suite against
`LocalInsecureBackend` on every push; the Lean suite against its local backend
with the toolchain and oleans cached by the pins' hash; and both suites under
gVisor on an Ubuntu runner with `runsc` installed.

## Limits worth knowing

- The harness and the verifier share one process. A verifier determined to walk
  Python frames can find the nonce in memory; the sandbox, not the framing, is
  what stops it from doing anything with that.
- `used.cpu_s` and `used.mem_bytes` are best-effort measurements reported by the
  harness (`getrusage`); `used.wall_s` is always measured from outside. For Lean,
  `used.wall_s` is the sum over stages and `used.mem_bytes` the maximum.
- Local image builds have no registry digest, so `image_digest` is the image
  config id. It changes whenever any layer does, which is what reproducibility
  needs.
- `Budget.build_s` / `build_mem` are reserved and unused by both languages;
  `judge-lean` applies one budget per stage instead.
- The Lean checker reads `.olean` files produced by an untrusted process. Lean's
  compacted-region loader is not hardened against adversarial files, so a
  crafted olean that subverts the stage-3 process rather than merely crashing
  it is the residual risk of this design — the same class as a kernel bug, and
  contained by the sandbox like everything else. A crash maps to `CRASH`.
- `-D maxHeartbeats=` is handed to `lean` as a courtesy limit on each
  declaration; a file can raise it with `set_option`. The wall clock and the
  memory cap, enforced from outside, are the real limits.
