# Writing verifiers

A verifier is a program in the **target language**, handed to `judge` as a string,
that decides whether a solution is correct. The solution is also a string, also in
the target language, and both are untrusted.

Every language works the same way from the calling side:

```python
from judge.python import run       # or judge.lean, ...

verdict = run(verifier_src, solution, budget)
```

Only the contents of `verifier_src` change. Pick your language:

| Language | Package | Verifier defines | Available inside |
| --- | --- | --- | --- |
| [Python](python.md) | `judge-python` | `verify(solution: str, rng) -> bool` | `numpy`, `scipy`, `sympy`, `mpmath` |
| [Lean](lean.md) | `judge-lean` | `Spec : Sort u`; the solution defines `answer : Spec` | Lean core, Mathlib |

Adding a language means adding a package and a row here — see [Adding a
language](../development.md#adding-a-language).

## What every language guarantees

These hold regardless of which package you use, because they live in `judge.core`
rather than in any one language's support:

**Acceptance requires the language's exact notion of "yes".** Not "something
truthy". In Python it is the `True` singleton, so `1` and `numpy.True_` are
rejections; in Lean it is the kernel accepting `answer : Spec` under the axiom
allowlist. Check your language's page.

**One budget covers everything.** The verifier and the solution share the wall
clock, the CPU time and the memory. No attribution between them, no partial
credit. A language with several stages applies the same budget to each stage.

**No network, nothing writable beyond what the language's build needs.** No
sockets, no DNS, no persistence between runs. One fresh container per
verification — or per stage.

**Output is never the result.** Whatever the language's equivalent of printing to
stdout is, it goes nowhere: the harness detaches the real stdout before running
any untrusted code, or never reads the stage's output at all. There is no way to
attach a message to a rejection.

**Anything other than a yes is a non-acceptance.** A crash, an exception, a hang,
running out of memory — each gets its own `reason` for diagnostics, and none of
them accept. See [Verdicts](../verdicts.md).

## What differs between languages

| | Varies per language |
| --- | --- |
| The verifier's contract | a function to call, or a type to inhabit |
| Available libraries | pinned per image |
| Sandbox image | one image and tag per language |
| The adversarial fixture set | each language can be attacked differently |
| Build stages | compiled languages bring `BUILD_FAILED` into play |
| Randomness | Python draws a seed per run; Lean has none and records `seed = 0` |

Everything else — `run()`, `Budget`, `Verdict`, `Reason`, the sandbox policy, the
backends, the selftest harness, the wire protocol — is shared. If you have read one
language's page, the only thing you need from another is its contract and its
library list.
