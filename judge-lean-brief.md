# Add `judge-lean` to the `judge` monorepo

You are working in the `judge` repository (https://github.com/nicolasloizeau/judge). Read `README.md`, `judge-brief.md`, `judge-core/`, `judge-python/` and `docs/` first. `judge-python` is the reference: `judge-lean` must feel like its sibling in layout, naming, CLI shape, test style, docs style and tone. Keep `judge-python` byte-for-byte unchanged in behaviour; its tests must keep passing.

## What changes conceptually

In `judge-python` a verifier is a program that inspects the solution. In Lean the verifier is a **type** and the checker is the Lean kernel:

- The verifier is a Lean module named `Verifier` defining one root-level constant `Spec : Sort u` (so `Spec : Prop` for a theorem, `Spec : Type` for a witness, typically a `Subtype`). Anything else in the file is optional scaffolding the solver may use.
- The solution is a Lean module named `Solution` defining a root-level constant `answer`. It may `import Verifier` and write `answer : Spec`, or restate the type verbatim; the kernel decides by definitional equality.
- `ACCEPTED` iff: `Solution.lean` elaborates; `answer` exists; the kernel accepts `answer : Spec` (via `Environment.addDecl` of a fresh declaration, not `Meta.isDefEq`); the transitive axioms of `answer` are within the allowlist; both untrusted modules survive an independent kernel replay. Everything else is non-acceptance.
- There is no `rng` and no seed. The verdict is reproducible from `(verifier_src, solution, budget, image_digest)`. `Verdict.seed` is recorded as `0` and documented as unused for this language.
- Default axiom allowlist: `propext`, `Classical.choice`, `Quot.sound`. This rejects `sorryAx`, user `axiom`s, and `native_decide` (`Lean.ofReduceBool`). A verifier may extend it with a header comment on its first lines, e.g. `-- judge: allow Lean.ofReduceBool`. Python parses that header as plain text (before anything runs) and passes the merged list to the checker. The verifier's own declarations are held to the same allowlist.

## Threat model, and why the design is three containers

Elaboration runs untrusted code: `run_cmd`, macros, `#eval`, `initialize` execute at compile time with full `IO` and `unsafe`. A solution can call `addDeclWithoutChecking`, set `debug.skipKernelTC`, read the source text, write files in its scratch area, or `IO.Process.exit 0`. Therefore:

1. Nothing decided inside the process that elaborated untrusted source is trusted. The verdict comes from a separate process (`judge-lean-check`) that runs only trusted code and treats the compiled `.olean` files as data, re-running the kernel over every declaration of the two untrusted modules (`Lean.Environment.replay`, as `lean4checker` does).
2. The verifier is compiled in its own container, before the solution exists, and its `.olean` is mounted read-only into the stages that consume it. A solution can rewrite anything in its own scratch area; it cannot touch `Verifier.olean` or Mathlib.
3. Lean has to write `.olean` files, so the zero-writable-bytes rule of `judge-python` is relaxed to: one per-verification host directory, bind-mounted read-write only into the stage that produces an output, read-only into its consumers, size-bounded, deleted after the run. Stage 3 has no writable mount at all.

Stages, all from the same image, each under the full sandbox flag policy (gVisor, no network, all caps dropped, non-root, pids limit, no-new-privileges):

```
stage 1  build verifier    lean  Verifier.lean  -> /work/Verifier.olean
                           then judge-lean-check --mode verifier   (Spec exists; allowlist over every decl)
         mounts: /work rw
stage 2  elaborate solution  lean  Solution.lean -> /work/Solution.olean
         mounts: /work/Verifier.olean ro, /work/out rw (or equivalent split)
         stdout of this stage is never read
stage 3  check             judge-lean-check --mode solution ... -> one framed JSON line on stdout
         mounts: everything ro, nothing writable
```

Rebuild the verifier in every `run()` (no caching in v1). `run()` stays stateless, like `judge-python`. Leave the interface such that a cache keyed by `sha256(verifier_src) + image_digest` can be added later as an optional constructor argument without changing `run()`.

## Reason mapping

| Situation | Reason |
| --- | --- |
| kernel accepts `answer : Spec`, axioms allowed | `ACCEPTED` |
| solution fails to elaborate, no `answer`, wrong type, disallowed axiom, replay failure, `unsafe answer`, universe params on `answer`, duplicate declaration at import | `REJECTED` (stderr/checker message in `detail`) |
| verifier fails to elaborate, no `Spec`, `Spec` not a sort or universe-polymorphic, verifier uses a disallowed axiom (e.g. `sorry` in a helper lemma) | `BUILD_FAILED` |
| wall clock or CPU exhausted in any stage | `TIMEOUT` |
| memory exhausted in any stage (container OOM-kill) | `OOM` |
| a stage died without producing its output and nothing above applies | `CRASH` |
| solution exceeds `max_solution_bytes` | `SOLUTION_TOO_LARGE` |
| Docker/runsc/checker binary missing, host-side failure | `INTERNAL_ERROR` |

`accepted` derives from `reason`, never from anything printed. The wall clock is enforced from outside with a hard kill, as in `GvisorBackend`. `-D maxHeartbeats=` is also passed to `lean` as a courtesy limit, documented as defeatable in-file and therefore not the real limit.

**Budget**: one `Budget`, applied to each stage independently (`wall_s`, `cpu_s`, `mem_bytes` each per stage). `build_s`/`build_mem` stay `None` and unused. `used.wall_s` is the sum over stages; `used.mem_bytes` the max. `max_solution_bytes` applies to `Solution.lean`; the verifier size is not limited (same as Python). Pick larger CLI defaults than Python, since importing Mathlib costs seconds and gigabytes: something like `wall_s=300`, `cpu_s=300`, `mem_bytes=6 GiB`, `max_solution_bytes=256 KiB`; measure and adjust.

## Repo changes

### `judge-core` (minimal, additive)

- Extract from `GvisorBackend` a reusable single-container primitive (e.g. `run_container(image, argv, *, mounts, budget, stdin, env, nonce) -> RawOutcome` with exit code, stdout, stderr, oom-killed flag, measured wall) so a language backend can compose several stages. `GvisorBackend.run` is re-expressed on top of it with identical behaviour.
- Extend the sandbox policy data with an explicit, documented mount allowance: read-only bind mounts and at most one read-write bind mount, present only when a language backend asks for them. The default policy (what `judge-python` uses) is unchanged: no mounts.
- Do the same for `LocalInsecureBackend`: a `run_process(argv, cwd, env, budget, stdin)` primitive with `setrlimit`, reused by the Python harness path unchanged.
- `Fixture`, `selftest`, `Reason`, `Budget`, `Verdict`, `protocol` are untouched except for docstrings mentioning the Lean sibling. If the framing helper needs a tiny generalisation to be reused by the checker, keep it backward compatible.

### `judge-lean/` (new package `judge.lean`, version `0.1.0`)

```
judge-lean/
  pyproject.toml                judge-lean, depends on judge-core==0.1.0; console script `judge-lean`
  README.md
  image/
    Dockerfile                  elan + pinned toolchain, the Lake project below, `lake exe cache get`
                                (Mathlib's prebuilt oleans; network at image build only, like pip install),
                                `lake build judge-lean-check`; bake LEAN_PATH and the checker path into ENV
                                so no stage needs `lake` or a writable directory at run time; non-root user;
                                harness.sh as entrypoint
    harness.sh                  POSIX sh. Args: stage name + paths. Sets `ulimit -f` (bounds each written
                                file), `ulimit -v`, runs `lean` or the checker, propagates the exit code.
                                Never interprets output.
    project/
      lean-toolchain            THE pinned Lean version
      lakefile.lean / lakefile.toml
      lake-manifest.json        THE pinned Mathlib commit
      JudgeLeanCheck/           the trusted checker (see below)
  src/judge/lean/
    __init__.py                 run(), gvisor_backend(), local_backend(), DEFAULT_BUDGET
    backend.py                  LeanGvisorBackend / LeanLocalInsecureBackend: three stages, mounts,
                                header parsing, reason mapping, scratch dir lifecycle (tempfile, always deleted)
    header.py                   parse `-- judge: allow ...` lines; pure, unit-tested
    image.py                    mirror of judge.python.image (tag `judge-lean:0.1.0`, digest, repo_root)
    fixtures.py                 adversarial fixtures (below)
    cli.py                      `judge-lean build-image`, `judge-lean verify --verifier V.lean --solution S.lean
                                [--wall-s --cpu-s --mem-mib --max-solution-bytes --json]`,
                                `judge-lean selftest [--backend gvisor|local] [--policy]`; exit 0 iff accepted
    py.typed
  tests/                        unit tests (header parsing, reason mapping from synthetic stage outcomes,
                                checker JSON parsing) needing neither Docker nor Lean; selftest suite on the
                                local backend, skipped cleanly when no toolchain; gvisor-marked suite as in judge-python
```

**Pinning.** `image/project/lean-toolchain` and `image/project/lake-manifest.json` are the settings files that fix the Lean version and the Mathlib commit. Take the current stable Lean release and the matching Mathlib commit at first build, commit them, and never resolve "latest" at build time. Document in the README that updating them changes the image digest, that verdicts recorded under an old digest replay only with the old image, and that verifiers may need maintenance after a Mathlib update. The `Dockerfile` must install exactly what those two files say.

**Module names.** `lean` derives the module name from the file path relative to `--root`; make sure the produced oleans are importable as `Verifier` and `Solution`. The checker imports `Verifier` first regardless of whether the solution imported it, so a solution that redefines `Spec` or `answer` under its own module fails at import with a duplicate declaration, which maps to `REJECTED`.

### `judge-lean-check` (Lean executable in `image/project`, trusted)

Depends on Lean core only (`Lean.Replay`, `Lean.CollectAxioms`), not on Mathlib, so it builds fast and has a small trusted surface. Two modes, both taking `--allow a,b,c` and the framing nonce, writing one framed JSON line to stdout (`deriving ToJson`), exit code 0 even on a negative result (a nonzero exit means the checker itself failed, which Python maps to `CRASH`/`INTERNAL_ERROR`, never to a verdict):

- `--mode verifier`: import `Verifier` (plus its imports), replay its constants through the kernel, check `Spec` exists at root, has zero universe params, and its type is a `Sort`; check every constant of the module is within the allowlist. Output `{ ok, why, axioms }`.
- `--mode solution`: import `Verifier` and `Solution`, replay both modules' constants, check `answer` exists at root, is not `unsafe`, has zero universe params; build `thmDecl` (if `Spec : Prop`) or `defnDecl` (otherwise) named with a reserved, unguessable-enough name (`_judge.check` plus the nonce) of type `Spec` with value `answer`; `Environment.addDecl` it (kernel type-check); collect axioms of the new declaration; compare to the allowlist. Output `{ ok, why, axioms, specIsProp }`.

Only the two untrusted modules are replayed; Mathlib's oleans are trusted as part of the image (digest-pinned, mounted read-only). Say so in the docs.

### Fixtures (`judge.lean.fixtures`)

Keep most of them Mathlib-free (core `Nat`, `decide`) so the suite is fast; one or two import a small Mathlib module to prove the import path works. Each is `Fixture.of(name, verifier_src, solution, expected, note=...)`:

- `honest-accept-prop`: `Spec : Prop := 2 + 2 = 4`, `theorem answer : Spec := by decide` → `ACCEPTED`
- `honest-accept-type`: `Spec := { p : ℕ × ℕ // p.1 * p.2 = 391 ∧ 1 < p.1 ∧ 1 < p.2 }`, `def answer : Spec := ⟨(17, 23), by decide⟩` → `ACCEPTED`
- `restated-type-no-import`: solution restates the type verbatim without `import Verifier` → `ACCEPTED`
- `honest-reject-wrong-type`: `answer : 2 + 2 = 5` → `REJECTED`
- `sorry` in the proof → `REJECTED`
- `axiom cheat : Spec; def answer := cheat` → `REJECTED`
- `native_decide` with default allowlist → `REJECTED`; the same with `-- judge: allow Lean.ofReduceBool` in the verifier → `ACCEPTED`
- `run_cmd` adding `answer` via `addDeclWithoutChecking` → `REJECTED`
- `set_option debug.skipKernelTC true` with a bogus proof → `REJECTED`
- `run_cmd` that writes to `/work` and tries to overwrite `Verifier.olean` → `REJECTED` (ro mount); skipped on the local backend (`provides_isolation` False)
- `run_cmd` doing `IO.Process.exit 0` before `answer` → `REJECTED` or `CRASH`
- `#exit` before `answer` → `REJECTED`
- `unsafe def answer` → `REJECTED`
- solution redefining `Spec` in its own module → `REJECTED`
- verifier with `sorry` in a helper lemma → `BUILD_FAILED`
- verifier without `Spec` → `BUILD_FAILED`
- infinite loop at elaboration time (e.g. `#eval` of a non-terminating `partial def`) with a short budget → `TIMEOUT`
- memory balloon at elaboration time with a small budget → `OOM`
- solution larger than `max_solution_bytes` → `SOLUTION_TOO_LARGE`
- fake framed verdict printed by `run_cmd` during stage 2 → `REJECTED` (stage 2 stdout is never read)

The selftest harness's separate "nothing unexpectedly ACCEPTED" check applies as it does for Python.

### Tests, CI, docs

- `pytest` without Docker or Lean: unit tests pass, Lean-dependent suites skip with a clear message.
- CI: add `judge-lean` to the install, lint, mypy and pytest steps of the `check` job. Add a `lean` job that installs `elan`, builds `image/project` (`lake exe cache get`, `lake build`), and runs `judge-lean selftest --backend local`; cache `~/.elan` and `.lake` by the hash of `lean-toolchain` + `lake-manifest.json`. Extend the `gvisor` job with `judge-lean build-image` and `judge-lean selftest --backend gvisor --policy` (expect a long image build; cache Docker layers if practical).
- Docs: `docs/verifiers/lean.md` in the style of `python.md` (the `Spec`/`answer` contract, Prop vs Type with the factorisation example, the allowlist and header, what is and is not available, what will not work, the budget notes, a checklist); update `docs/verifiers/index.md`, `docs/cli.md`, `docs/verdicts.md` (the `BUILD_FAILED` row is now used; `seed` unused for Lean), `mkdocs.yml` nav, and the root `README.md` layout section. The README must state plainly: the three-stage design and why; that the sandbox's zero-writable-bytes guarantee is relaxed for Lean to one size-bounded scratch directory that never outlives the verification; that only the two untrusted modules are replayed and Mathlib is trusted as part of the pinned image; the reproducibility tuple without a seed.
- Update `judge-brief.md`'s remark about `BUILD_FAILED`/`build_*` being unused, or add a pointer to this brief (commit this file as `judge-lean-brief.md`).

## Style

Python ≥3.11, full type hints, `ruff` + `mypy` clean, `judge-core` stays stdlib-only, `judge-lean` has no dependency beyond `judge-core`. Lean code idiomatic for the pinned version. Keep `judge-lean` thin: anything language-agnostic goes to `judge.core`. When a design detail here conflicts with what the pinned Lean version actually offers (flag names, `Lean.Replay` API shape), prefer what works and note the deviation in the README.

Work in this order: core refactor with Python tests green → `image/project` with the checker, tested by hand against the fixtures' Lean sources → Python backend + local backend → fixtures and selftest green locally → image and gVisor → docs and CI.
