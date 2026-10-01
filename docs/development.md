# Development

```bash
git clone https://github.com/nicolasloizeau/judge
cd judge
python -m venv .venv && source .venv/bin/activate
pip install -e judge-core -e judge-python -e judge-lean
pip install pytest ruff mypy
judge build-image          # needs Docker
judge-lean build-image     # needs Docker; downloads Mathlib's oleans, takes a while
```

For the Lean *local* backend and its tests, build the Lake project once with
the pinned toolchain (`elan` picks it up from `lean-toolchain`):

```bash
cd judge-lean/image/project
lake exe cache get          # Mathlib's prebuilt oleans, ~7 GB
lake build judge-lean-check
```

## Checks

```bash
pytest                                       # unit tests + both suites on the local backends
pytest -m slow                               # both suites under gVisor (needs Docker + runsc + images)
ruff check . && ruff format --check .
mypy judge-core/src judge-python/src judge-lean/src judge-core/tests judge-python/tests
mypy judge-lean/tests                        # separately: two `conftest.py` modules
```

Everything Lean-dependent skips with a clear message when the toolchain, the
built project or the image is missing — a clean skip, never a false green.

## Layout

```
judge-core/     judge.core     types, sandbox policy, backends and their primitives, selftest harness
judge-python/   judge.python   the Python image, in-sandbox harness, fixtures, CLI
judge-lean/     judge.lean     the Lean image, the Lake project with the trusted checker,
                               the three-stage backends, fixtures, CLI
docs/                          this site
```

Three independently installable packages sharing the `judge.` import root via
[PEP 420](https://peps.python.org/pep-0420/) namespace packages. **There is
deliberately no `__init__.py` at the `judge/` root** — adding one breaks the
namespace.

The dividing line: **anything language-agnostic belongs in `judge.core`.**
The language packages stay thin so that the next sibling need only bring an
image, a harness and a fixture set.

## Style

- Python ≥3.11, full type hints, `ruff` and `mypy` clean
- `judge-core` has **no dependencies**; `judge-lean` depends on nothing beyond it
- Frozen dataclasses with `slots=True` for value types
- Policy as data, not strings — so it can be tested, printed and diffed
- Lean code idiomatic for the pinned toolchain; the checker imports Lean core only

## Adding a language

A language package is a **Python** package. Only the verifier and solution strings
are in the target language; the caller is always a Python developer.

`judge-python` is the reference for a language whose verifier is a *program*;
`judge-lean` for one whose verifier is *compiled* and checked in stages. Copy
the shape of the closer one.

### What you must not duplicate

Everything below already exists in `judge.core` and must be reused, not
reimplemented:

`Budget`, `Verdict`, `Reason`
:   The value types. A new language adds no new reason — `VERIFIER_FAULT` is the
    interpreted "broken verifier", `BUILD_FAILED` the compiled one.

`SandboxPolicy`, `BindMount`, `docker_run_args()`
:   The container restrictions. Your image must be runnable under the default
    policy: non-root uid, read-only rootfs, nothing writable. If a build step
    *has* to write, the policy's mount allowance — read-only bind mounts and at
    most one read-write one — is the only door, and it is per stage.

`GvisorBackend`, `LocalInsecureBackend`
:   The backends for a single-process language: they take an image and speak
    the protocol. A multi-stage language composes
    `GvisorBackend.run_container()` and `run_process()` instead, as
    `judge.lean.backend` does, and maps each stage's `RawOutcome` itself.

`Request`, `Result`, `extract_result()`, `extract_frame()`, `verdict_from_outcome()`
:   The wire protocol. Call `verdict_from_outcome()` rather than mapping exit
    codes yourself; `RawOutcome.limit_reason()` is where "what does exit 137
    mean" stays answered in one place.

`run_selftest()`, `Fixture`
:   The selftest runner. You supply fixtures; the runner is shared.

If you find yourself needing a change in `judge.core` to support your language,
that is a signal the change belongs there — make it there rather than working
around it locally. The Lean sibling added the mount allowance, the two
primitives and `RawOutcome` that way.

### What the package brings

1. **`pyproject.toml`** — name it `judge-<lang>`, depend on `judge-core==<version>`,
   and set `include = ["judge.<lang>*"]` with `namespaces = true`. **No
   `__init__.py` at the `judge/` root.** Claim your own console script
   (`judge-<lang>`); `judge-python` keeps the bare `judge`.

2. **A pinned image** under `judge-<lang>/image/` — base image by manifest digest,
   toolchain versions exact in committed files, non-root user matching
   `SandboxPolicy.user`, `TMPDIR` and `HOME` at `/nonexistent`, entrypoint is your
   harness. End with a build-time check that the toolchain imports or links, so a
   broken image fails the build instead of shipping. Network at image build only.

3. **An in-sandbox harness**, entrypoint of the image. For a single-process
   language, exactly this:

    1. read one JSON line from stdin, decode a `Request`, close stdin
    2. enforce `budget.max_solution_bytes` against the UTF-8 size of the solution
    3. apply resource limits and arm a wall-clock timer
    4. save the real stdout to another fd, then point fds 0/1/2 at `/dev/null`
    5. build the language's RNG from `request.seed`, then run the verifier
    6. classify into a `Reason` — **accept only on the language's exact true value**
    7. restore the saved fd, write one framed `Result` line, exit immediately

    Only the process that read the request may write the result line; forked
    children must not. `judge/python/harness.py` is ~300 lines and is the model.

    For a staged language the harness is thinner — `judge-lean/image/harness.sh`
    runs one program per stage and never interprets output — and the
    decision is made by a separate trusted program over the stages' artifacts.

4. **A fixture set** — the adversarial suite for your language. Start by porting
   `judge/python/fixtures.py` case by case: honest accept and reject, truthy-not-true,
   broken verifier, infinite loop, memory balloon, fork bomb, network, file write,
   subprocess, forged verdict lines, oversized solution, verifier-runs-solution.
   Then add whatever is specific to your language — unsafe blocks, FFI, a build
   step that writes outside its directory. `judge/lean/fixtures.py` shows the
   staged version: attacks on the build, on the checker's inputs, on the mounts.

5. **A CLI** — the same three subcommands, same flags, your defaults.

### What the docs need

Three edits, by design — nothing else moves:

1. **`docs/verifiers/<lang>.md`** — the page. Skeleton:

    ```markdown
    # <Language> verifiers

    A verifier is a <language> <module/crate/file> — handed to `judge` as a
    string — defining:

    <the entry point signature>

    The rules that hold for every language are on the [overview](index.md); this
    page is the <language> specifics.

    ## Example: check an answer
    ## Example: run the submitted solution
    ## Example: probabilistic check
    ## Returning true, exactly
    ## What is available          <- pinned toolchain + libraries, with versions
    ## What will not work         <- how the sandbox limits surface in this language
    ## Iterate quickly
    ## Checklist
    ```

    Keep it standalone. A verifier author writing this language may never touch the
    Python API, so do not make them read another page to get started.

2. **`mkdocs.yml`** — one line under `Writing verifiers`.

3. **`docs/verifiers/index.md`** — one row in the language table.

Do **not** add language-specific content to `verdicts.md`, `cli.md` or `api.md`
unless the language genuinely changes shared behaviour — the first compiled
language making `BUILD_FAILED` live did, and those pages now say so for
everyone rather than carrying a caveat for one language.

### Checklist

- [ ] `judge-<lang>` depends on `judge-core` and adds nothing language-specific to it
- [ ] Image pinned by digest, toolchain versions exact and committed
- [ ] Image runs under `DEFAULT_POLICY`, or under it plus a documented mount allowance
- [ ] Harness detaches stdout before running untrusted code, or the decision is made elsewhere
- [ ] Acceptance requires the language's exact true value, not truthiness
- [ ] Verdict line framed with the request's nonce, written last, by a trusted process only
- [ ] Fixture set ported, plus language-specific attacks
- [ ] `selftest` green under gVisor, with fixtures that need isolation skipping cleanly on the local backend
- [ ] CI jobs added, mirroring the `check` and `gvisor` jobs
- [ ] `docs/verifiers/<lang>.md`, nav line, hub table row

## Changing the sandbox

`judge selftest --backend gvisor` must stay at `27 passed, 0 failed, 0 skipped`
and `judge-lean selftest --backend gvisor` at `29 passed, 0 failed, 0 skipped`.
Tightening the policy is safe; loosening it is a security change and the suites
are what tell you whether containment broke. `test_policy.py` in `judge-core`
asserts the default policy emits no `--volume`, `--mount` or `--tmpfs`; its
sibling in `judge-lean` asserts the Lean policy differs only in the file-size
limit, the uid, and per-stage bind mounts with at most one read-write.

If you can write a verifier that reaches `ACCEPTED` without `verify` returning
`True`, or a Lean solution the kernel would not accept, or anything that escapes
the budget, add it to the language's `fixtures.py`.

## Updating the Lean pins

`judge-lean/image/project/lean-toolchain` and `lake-manifest.json` are the only
two places that say which Lean and which Mathlib. To move them: edit
`lean-toolchain`, set the Mathlib `rev` in `lakefile.toml` to the matching tag,
run `lake update` in the project to regenerate the manifest, rebuild, run both
suites, commit all three files together. The image digest changes; verdicts
recorded under the old digest replay only with the old image; verifiers that
leaned on Mathlib names that moved will need maintenance.

## CI

`.github/workflows/ci.yml` has three jobs. **`check`** runs lint, types, unit
tests and the Python suite against the local backend on Python 3.12 and 3.13 —
no Docker, no Lean. **`lean`** installs `elan`, fetches Mathlib's oleans
(cached by the hash of the two pin files), builds the checker and runs the Lean
suite against the local backend. **`gvisor`** installs `runsc`, builds both
images, and runs both suites under real isolation; the Lean image build is the
slow step.

## Docs

[MkDocs](https://www.mkdocs.org) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme; sources in `docs/`,
config in `mkdocs.yml`.

```bash
pip install -r docs/requirements.txt
mkdocs serve            # preview on http://127.0.0.1:8000
mkdocs build --strict   # what CI runs; warnings, dead links and stale #anchors fail
```

`.github/workflows/docs.yml` builds on pull requests and deploys to GitHub Pages on
push to `main`. One-time setup: **Settings → Pages → Source → GitHub Actions**. No
`gh-pages` branch involved.

These pages cover *usage*. The sandbox guarantees, threat model and adversarial
suites live in the repository `README.md`; keep the deep material there rather than
duplicating it here.
