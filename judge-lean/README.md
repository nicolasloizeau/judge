# judge-lean

Lean-language support for [`judge`](../README.md): the pinned image (Lean +
Mathlib + the trusted checker), the three-stage backends, the adversarial
fixture set and the `judge-lean` CLI. Depends on `judge-core` and nothing else.

```python
from judge.core import Budget
from judge.lean import run

verdict = run(verifier_src, solution, Budget(300.0, 300.0, 8 << 30, 256 << 10))
```

A verifier is a Lean module defining a root-level `Spec : Sort u`; a solution is
a Lean module defining `answer`. `ACCEPTED` iff the Lean kernel accepts
`answer : Spec` and the proof depends only on allowlisted axioms. See the root
README for the three-stage design, the containment guarantee and the
reproducibility tuple, and [the docs](https://nicolasloizeau.github.io/judge/verifiers/lean/)
for how to write a verifier.

## Contents

- `image/Dockerfile` — the pinned image: a digest-pinned Debian base, `elan`
  installing exactly `image/project/lean-toolchain`, Mathlib's prebuilt oleans at
  exactly the commit in `image/project/lake-manifest.json`, the checker built
  from `image/project/JudgeLeanCheck.lean`, non-root, `harness.sh` as entrypoint.
- `image/harness.sh` — POSIX sh. Runs one program per stage (`lean` or the
  checker), applies best-effort limits, reports CPU and memory on stderr, never
  interprets output.
- `image/project/` — the Lake project. **`lean-toolchain` and
  `lake-manifest.json` are the pins.** `JudgeLeanCheck.lean` is the trusted
  checker; it imports Lean core only.
- `src/judge/lean/backend.py` — the stage pipeline and reason mapping;
  `LeanGvisorBackend` (three containers) and `LeanLocalInsecureBackend` (three
  host processes).
- `src/judge/lean/header.py` — the `-- judge: allow` header parser.
- `src/judge/lean/fixtures.py` — the threat model in executable form.
- `src/judge/lean/cli.py` — `judge-lean build-image`, `judge-lean verify`,
  `judge-lean selftest`.

## The pins

```
image/project/lean-toolchain      leanprover/lean4:v4.34.1
image/project/lake-manifest.json  Mathlib at tag v4.34.1 and its dependencies, by commit
```

The Dockerfile installs exactly what these two files say and never resolves
"latest". Changing either changes the image digest; verdicts recorded under the
old digest replay only with the old image; verifiers may need maintenance after
a Mathlib update. To move the pins, see
[Updating the Lean pins](https://nicolasloizeau.github.io/judge/development/#updating-the-lean-pins).

## Where this deviates from the brief

The design brief (`../judge-lean-brief.md`) was written against a general
picture of Lean; the pinned toolchain differs in four places, and in each the
working behaviour won:

- **`native_decide` cannot be allowlisted.** Lean 4.34 no longer proves
  `native_decide` goals via the global `Lean.ofReduceBool`; each use mints its
  own axiom (`<decl>._native.native_decide.ax_N_M : decide p = true`) asserting
  what the compiled code computed in the *untrusted* elaboration stage. No name
  can be allowlisted in advance and trusting such an axiom would mean trusting
  the solution's own process, so `native_decide` is always `REJECTED`. The
  `-- judge: allow` header still works for genuine imported axioms, and every
  allowlisted name must be an axiom of the trusted imports (so a solution
  cannot declare it itself). The fixture that exercises the header allows
  `sorryAx`, the one imported axiom that makes the mechanism observable.
- **No `ulimit -v`.** Lean maps gigabytes of read-only oleans and reserves
  large thread stacks; `RLIMIT_AS` at 8 GiB breaks `import Mathlib` and
  `RLIMIT_DATA` at 2 GiB breaks thread creation for a core-only file. The
  memory cap is the container's cgroup limit under gVisor and an RSS watchdog
  (polling `/proc`, killing the process group) on the local backend. The
  harness sets `ulimit -t` and `ulimit -f` only.
- **Axioms are collected over the kernel environment** by a small traversal in
  the checker rather than `Lean.collectAxioms`, which needs the elaborator's
  environment extensions loaded. Same traversal, 25 lines, and it reads exactly
  what the kernel checked.
- **The container runs as the host's uid**, not `65532`, when the host user is
  not root: files a gVisor container creates in a bind mount under uid 65532
  cannot be deleted afterwards by an unprivileged host user, and the scratch
  directory must never outlive the verification. It is still a non-root uid,
  and the mounts, not the uid, bound what a stage can touch.

Everything else — the three stages, the replay, the allowlist, the reason
mapping, the per-stage budget, the layout — is as specified.
