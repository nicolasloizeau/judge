# Writing verifiers

A verifier is a program in the **target language**, handed to `judge` as a string,
that decides whether a solution is correct. The solution is also a string, also in
the target language, and both are untrusted.

Every language works the same way from the calling side:

```python
from judge.python import run       # or judge.rust, judge.c, ...

verdict = run(verifier_src, solution, budget)
```

Only the contents of `verifier_src` change. Pick your language:

| Language | Package | Verifier defines | Available inside |
| --- | --- | --- | --- |
| [Python](python.md) | `judge-python` | `verify(solution: str, rng) -> bool` | `numpy`, `scipy`, `sympy`, `mpmath` |

Adding a language means adding a package and a row here — see [Adding a
language](../development.md#adding-a-language).

## What every language guarantees

These hold regardless of which package you use, because they live in `judge.core`
rather than in any one language's support:

**Acceptance requires the language's exact true value.** Not "something truthy".
What that means in practice is language-specific — in Python it is the `True`
singleton, so `1` and `numpy.True_` are rejections — so check your language's page.

**One budget covers everything.** The verifier and the solution share the wall
clock, the CPU time and the memory. No attribution between them, no partial
credit. For compiled languages a build step will come out of the same envelope.

**No network, nothing writable.** No sockets, no DNS, no files, no `/tmp`, no
persistence between runs. One fresh container per verification.

**The return value is the only channel out.** Whatever the language's equivalent of
printing to stdout is, it goes nowhere — the harness detaches the real stdout
before running any untrusted code. There is no way to attach a message to a
rejection.

**Anything other than a true return is a non-acceptance.** A crash, an exception, a
hang, running out of memory — each gets its own `reason` for diagnostics, and none
of them accept. See [Verdicts](../verdicts.md).

## What differs between languages

| | Varies per language |
| --- | --- |
| The verifier's entry point | signature and return type |
| Available libraries | pinned per image |
| Sandbox image | one image and tag per language |
| The adversarial fixture set | each language can be attacked differently |
| A build step | compiled languages only; brings `BUILD_FAILED` into play |

Everything else — `run()`, `Budget`, `Verdict`, `Reason`, the sandbox policy, the
backends, the selftest harness, the wire protocol — is shared. If you have read one
language's page, the only thing you need from another is its entry point and its
library list.
