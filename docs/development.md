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
