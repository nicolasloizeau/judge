# Development

```bash
git clone https://github.com/nicolasloizeau/judge
cd judge
python -m venv .venv && source .venv/bin/activate
pip install -e judge-core -e judge-python
pip install pytest ruff mypy
judge build-image          # needs Docker
```

## Checks

```bash
pytest                                       # unit tests + the suite on the local backend
pytest -m slow                               # the suite under gVisor (needs Docker + runsc)
ruff check . && ruff format --check .
mypy judge-core/src judge-python/src judge-core/tests judge-python/tests
```

A full run with gVisor available gives `136 passed, 5 skipped`. Without `runsc` the
`slow` tests skip too: `108 passed, 33 skipped` — a clean skip, never a false
green.

## Layout

```
judge-core/     judge.core     types, sandbox policy, backends, selftest harness
judge-python/   judge.python   the Python image, in-sandbox harness, fixtures, CLI
docs/                          this site
```

Two independently installable packages sharing the `judge.` import root via
[PEP 420](https://peps.python.org/pep-0420/) namespace packages. **There is
deliberately no `__init__.py` at the `judge/` root** — adding one breaks the
namespace.

The dividing line: **anything language-agnostic belongs in `judge.core`.**
`judge.python` stays thin so a `judge-rust` sibling need only bring an image, a
harness and a fixture set.

## Style

- Python ≥3.11, full type hints, `ruff` and `mypy` clean
- `judge-core` has **no dependencies**; keep it that way
- Frozen dataclasses with `slots=True` for value types
- Policy as data, not strings — so it can be tested, printed and diffed

## Adding a language

A language package is a **Python** package. Only the verifier and solution strings
are in the target language; the caller is always a Python developer.

`judge-python` is the reference implementation: 1025 lines, of which 435 are
fixtures and 312 are the harness. Copy its shape.

### What you must not duplicate

Everything below already exists in `judge.core` and must be reused, not
reimplemented:

`Budget`, `Verdict`, `Reason`
:   The value types. A new language adds no new reason — `BUILD_FAILED` and
    `Budget.build_s` / `build_mem` are already reserved for the compiled case.

`SandboxPolicy`, `docker_run_args()`
:   The container restrictions. Your image must be runnable under the default
    policy: non-root uid `65532:65532`, read-only rootfs, nothing writable.

`GvisorBackend`, `LocalInsecureBackend`
:   The backends are language-agnostic — they take an image and speak the protocol.
    You should not need a new one.

`Request`, `Result`, `extract_result()`, `verdict_from_outcome()`
:   The wire protocol. Call `verdict_from_outcome()` rather than mapping exit codes
    yourself, so "what does exit 137 mean" stays answered in one place.

`run_selftest()`, `Fixture`
:   The selftest runner. You supply fixtures; the runner is shared.

If you find yourself needing a change in `judge.core` to support your language,
that is a signal the change belongs there — make it there rather than working
around it locally.

### What the package brings

1. **`pyproject.toml`** — name it `judge-<lang>`, depend on `judge-core==<version>`,
   and set `include = ["judge.<lang>*"]` with `namespaces = true`. **No
   `__init__.py` at the `judge/` root.**

2. **A pinned image** under `judge-<lang>/image/` — base image by manifest digest,
   toolchain versions exact in a committed manifest, non-root user matching
   `SandboxPolicy.user`, `TMPDIR` and `HOME` at `/nonexistent`, entrypoint is your
   harness. End with a build-time check that the toolchain imports or links, so a
   broken image fails the build instead of shipping.

3. **An in-sandbox harness**, entrypoint of the image, doing exactly this:

    1. read one JSON line from stdin, decode a `Request`, close stdin
    2. enforce `budget.max_solution_bytes` against the UTF-8 size of the solution
    3. apply resource limits and arm a wall-clock timer
    4. save the real stdout to another fd, then point fds 0/1/2 at `/dev/null`
    5. build the language's RNG from `request.seed`, then run the verifier
    6. classify into a `Reason` — **accept only on the language's exact true value**
    7. restore the saved fd, write one framed `Result` line, exit immediately

    Only the process that read the request may write the result line; forked
    children must not. `judge/python/harness.py` is ~300 lines and is the model.

4. **A fixture set** — the adversarial suite for your language. Start by porting
   `judge/python/fixtures.py` case by case: honest accept and reject, truthy-not-true,
   broken verifier, infinite loop, memory balloon, fork bomb, network, file write,
   subprocess, forged verdict lines, oversized solution, verifier-runs-solution.
   Then add whatever is specific to your language — unsafe blocks, FFI, a build
   step that writes outside its directory.

5. **Registration** so the CLI can find it. Note that `judge-python` currently
   claims the bare `judge` script name, so a second package cannot also claim it —
   settle this before merging a sibling. The clean fix is to move the CLI into
   `judge-core` with a language registry and have each package register itself.

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

Do **not** add language-specific content to `verdicts.md`, `cli.md` or `api.md`.
Those pages are shared, and the reason they are short is that they stay that way.
If a language genuinely changes shared behaviour — the first compiled language
making `BUILD_FAILED` live, for instance — change the shared page for everyone
rather than adding a caveat for one language.

### Checklist

- [ ] `judge-<lang>` depends on `judge-core` and adds nothing language-specific to it
- [ ] Image pinned by digest, toolchain versions exact and committed
- [ ] Image runs under the unmodified `DEFAULT_POLICY`
- [ ] Harness detaches stdout before running untrusted code
- [ ] Acceptance requires the language's exact true value, not truthiness
- [ ] Verdict line framed with the request's nonce, written last, by the main process only
- [ ] Fixture set ported, plus language-specific attacks
- [ ] `judge selftest` green under gVisor, with fixtures that need isolation skipping cleanly on the local backend
- [ ] CI job added, mirroring the `check` and `gvisor` jobs
- [ ] `docs/verifiers/<lang>.md`, nav line, hub table row

## Changing the sandbox

`judge selftest --backend gvisor` must stay at `27 passed, 0 failed, 0 skipped`.
Tightening the policy is safe; loosening it is a security change and the suite is
what tells you whether containment broke. `test_policy.py` also asserts no
`--volume`, `--mount` or `--tmpfs` is ever emitted.

If you can write a verifier that reaches `ACCEPTED` without `verify` returning
`True`, or that escapes the budget, add it to
`judge-python/src/judge/python/fixtures.py`.

## CI

`.github/workflows/ci.yml` has two jobs. **`check`** runs lint, types, unit tests
and the suite against the local backend on Python 3.12 and 3.13 — no Docker needed.
**`gvisor`** installs `runsc`, builds the image, and runs the suite under real
isolation.

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
suite live in the repository `README.md`; keep the deep material there rather than
duplicating it here.
