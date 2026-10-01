# Lean verifiers

A verifier is a Lean module — handed to `judge` as a string — defining one
root-level constant:

```lean
abbrev Spec : Prop := ...      -- a theorem the solution must prove
def Spec : Type := ...         -- or a type the solution must inhabit
```

The solution is a Lean module defining `answer`, and the checker is the **Lean
kernel**: the verdict is `ACCEPTED` iff the kernel accepts `answer : Spec` and
the proof depends on no axiom outside the allowlist. There is no `verify`
function and nothing to return. The rules that hold for every language are on the
[overview](index.md); this page is the Lean specifics.

## Example: a theorem

```lean
-- Verifier.lean
abbrev Spec : Prop := 2 + 2 = 4
```

```lean
-- Solution.lean
import Verifier
theorem answer : Spec := by decide
```

`abbrev` rather than `def`: instance search sees through an `abbrev`, so
`decide` finds `Decidable Spec`. With a plain `def` the solver has to unfold it
first (`by unfold Spec; decide` or `show 2 + 2 = 4; decide`), which works too but
is a needless hurdle.

The solution may also restate the type instead of importing the verifier:

```lean
theorem answer : 2 + 2 = 4 := by decide
```

The kernel decides by definitional equality, so a restated type that unfolds to
`Spec` is accepted and one that merely *looks* similar is not.

## Example: a witness

For a search problem, make `Spec` a type — usually a subtype — and the solution
constructs an element of it:

```lean
-- Verifier.lean
def Spec : Type := { p : Nat × Nat // p.1 * p.2 = 391 ∧ 1 < p.1 ∧ 1 < p.2 }
```

```lean
-- Solution.lean
import Verifier
def answer : Spec := ⟨(17, 23), by decide⟩
```

The witness is checked as a definition, the proof obligation inside it as part
of the term. A wrong witness (`⟨(1, 391), by decide⟩`) fails at `decide` and is
`REJECTED`.

## Example: with Mathlib

Mathlib is available at a pinned commit. Import what you need:

```lean
-- Verifier.lean
import Mathlib.Data.Nat.Notation
def Spec : Type := { p : ℕ × ℕ // p.1 * p.2 = 391 ∧ 1 < p.1 ∧ 1 < p.2 }
```

A full `import Mathlib` works but is expensive: about 6.5 GB of resident memory
and several seconds per stage. Import the modules you need and size the budget
accordingly (see [Budget](#budget)).

## Prop or Type

| `Spec : Prop` | `Spec : Type` (or any `Sort u`) |
| --- | --- |
| The solution proves a theorem | The solution exhibits a witness |
| `answer` is checked as a `theorem` | `answer` is checked as a `def` |
| The kernel checks the type is a proposition | The kernel checks `answer`'s type is definitionally `Spec` |

Either way `Spec` must be a root-level constant named exactly `Spec`, must not
be `unsafe`, must have no universe parameters, and its type must reduce to a
`Sort`. `answer` must likewise be root-level, safe, universe-monomorphic, and
declared by the solution module itself. A `module`-system solution must make it
`public`.

Anything else in the verifier is optional scaffolding — definitions, lemmas,
notation — that the solution may use after `import Verifier`. The verifier's
own declarations are held to the same axiom allowlist as the solution: a
`sorry` in a helper lemma makes the verifier `BUILD_FAILED`.

## Axioms and the allowlist

A proof may depend on exactly these axioms by default:

```
propext   Classical.choice   Quot.sound
```

Everything else is a rejection, including:

- `sorry` (the `sorryAx` axiom);
- any `axiom` the solution declares;
- `native_decide`. In this toolchain every use mints its own axiom
  (`answer._native.native_decide.ax_1_1 : decide Spec = true`) that asserts what
  the compiled code computed *during elaboration* — the untrusted stage. No
  allowlist can name such an axiom in advance, and trusting it would mean
  trusting the solution's own process, so `native_decide` is never accepted.
  `decide` and `decide +kernel` are fine: the kernel does the work.

A verifier can extend the list with a header comment on its first lines:

```lean
-- judge: allow Lean.ofReduceNat
import Mathlib.Tactic
abbrev Spec : Prop := ...
```

Names are separated by commas or spaces; several lines merge. The header is
parsed as plain text by `judge` before any Lean runs, so neither module can
rewrite it, and every name must be an axiom of the *imported* environment —
otherwise a solution could declare the allowlisted name itself. An unknown name
is `BUILD_FAILED`.

The axioms a verdict actually depended on are reported in
`verdict.host["axioms"]`.

## What is available

| | Pinned to |
| --- | --- |
| Lean | `leanprover/lean4:v4.34.1` (`judge-lean/image/project/lean-toolchain`) |
| Mathlib | tag `v4.34.1` (`judge-lean/image/project/lake-manifest.json`) |
| plus Mathlib's dependencies | `batteries`, `aesop`, `Qq`, `proofwidgets`, `plausible`, `importGraph`, `LeanSearchClient` at the manifest's revisions |

The core library, `Lean` itself (metaprogramming, `run_cmd`, `#eval`) and
everything above can be imported. Nothing else is on the search path: no
`lake`, no project of your own, no other packages.

## What will not work

| Attempt | What happens |
| --- | --- |
| Sockets, DNS | Fail — the sandbox has no network |
| `#eval`, `IO.println`, `run_cmd` output | Goes nowhere. Stage output is never read as a result |
| Writing files | Only the stage's own scratch directory is writable; the verifier's olean and Mathlib are read-only |
| `sorry`, `axiom`, `native_decide` | Elaborate, then `REJECTED` by the allowlist |
| `unsafe def answer` | `REJECTED` — unsafe constants are never replayed |
| `set_option debug.skipKernelTC`, `addDeclCore (doCheck := false)` | Pass elaboration, then `REJECTED`: the checker replays every declaration through the kernel in a separate process |
| Redefining `Spec` in the solution | `REJECTED` — duplicate declaration when the checker imports `Verifier` first |
| `#exit` or `IO.Process.exit` before `answer` | `REJECTED` — no `answer` in the olean, or no olean at all |
| `module` solution with `import Verifier` | `REJECTED` — the verifier is not a `module`; restate the type instead |
| Caching between runs | Nothing persists. The verifier is rebuilt every time |

There is also no way to attach a message to a `REJECTED` verdict beyond what
`Verdict.detail` carries — the kernel's or Lean's own message, truncated,
derived from untrusted output. Log it, never branch on it.

## Budget

One `Budget` applies to **each stage independently** — building the verifier,
elaborating the solution, and the two checker runs — so `wall_s` is a
per-stage limit and `used.wall_s` is the sum. `used.mem_bytes` is the maximum
over stages.

| CLI default | |
| --- | --- |
| `--wall-s 300`, `--cpu-s 300` | per stage |
| `--mem-mib 8192` | enough for `import Mathlib`; a core-only verifier needs under 1 GiB |
| `--max-solution-bytes 262144` | the solution source; the verifier is not limited |

Things to know:

- `lean` gets `-D maxHeartbeats=400000` as a courtesy limit on each
  declaration. A file can raise it with `set_option`; the wall clock, enforced
  from outside with a kill, is the real limit.
- `lean` runs with `--threads=4`. The CPU budget counts all threads.
- Elaboration-time computation (`#eval`, `run_cmd`) is not bounded by
  heartbeats at all; only the wall clock and the memory cap stop it.
- A memory cap below what the imports need shows up as `OOM` in the *verifier*
  stage, before any solution runs.

## Iterate quickly

The local backend runs the pinned toolchain from `~/.elan` and the checker from
the checkout, without Docker. It does **not** sandbox anything, so only point it
at code you wrote yourself. It needs the Lake project built once:

```bash
cd judge-lean/image/project
lake exe cache get          # Mathlib's prebuilt oleans, ~7 GB
lake build judge-lean-check
```

```python
from judge.lean import local_backend, run

verdict = run(verifier_src, solution, budget, backend=local_backend())
```

```bash
judge-lean verify --backend local --verifier Verifier.lean --solution Solution.lean
```

Then confirm against the real backend before publishing:

```bash
judge-lean verify --verifier Verifier.lean --solution Solution.lean
```

## Checklist

- [ ] `Spec` defined at the root, as an `abbrev` when it is a `Prop` you expect to be decided
- [ ] `Spec` is a `Sort`: a proposition or a type, not a value
- [ ] No `sorry` anywhere in the verifier, helpers included
- [ ] Imports limited to what the problem needs; budget sized for them
- [ ] Any `-- judge: allow` names are axioms of the imported environment
- [ ] A reference solution reaches `ACCEPTED` against the gVisor backend, not just the local one
