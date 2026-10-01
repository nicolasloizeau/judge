"""Adversarial fixtures for Lean verifiers.

Each entry is a verifier/solution pair plus the set of reasons that count as
contained. Read this file as the threat model in executable form: if you can
write a solution that reaches ``ACCEPTED`` without the kernel accepting
``answer : Spec`` under the allowlist, or that escapes the budget, it belongs
here.

Most fixtures import nothing beyond the core library, so the suite stays
fast; two import a small Mathlib module to prove the import path works.
"""

from __future__ import annotations

from judge.core.protocol import SENTINEL
from judge.core.selftest import Fixture
from judge.core.types import Budget, Reason

MIB = 1024 * 1024

SELFTEST_BUDGET = Budget(
    wall_s=60.0,
    cpu_s=60.0,
    mem_bytes=2048 * MIB,
    max_solution_bytes=256 * 1024,
)
"""Per stage. Generous for core-only fixtures; enough for a small Mathlib import."""

_SHORT_BUDGET = Budget(wall_s=8.0, cpu_s=8.0, mem_bytes=2048 * MIB, max_solution_bytes=256 * 1024)
_SMALL_MEM_BUDGET = Budget(
    wall_s=60.0, cpu_s=60.0, mem_bytes=512 * MIB, max_solution_bytes=256 * 1024
)

SPEC_PROP = "abbrev Spec : Prop := 2 + 2 = 4\n"
"""`abbrev`, not `def`: instance search (`Decidable Spec`) sees through it."""

SPEC_TYPE = """
def Spec : Type := { p : Nat × Nat // p.1 * p.2 = 391 ∧ 1 < p.1 ∧ 1 < p.2 }
"""

SPEC_MATHLIB = """
import Mathlib.Data.Nat.Notation
def Spec : Type := { p : ℕ × ℕ // p.1 * p.2 = 391 ∧ 1 < p.1 ∧ 1 < p.2 }
"""

HONEST_ACCEPT_PROP = Fixture.of(
    "honest-accept-prop",
    SPEC_PROP,
    "import Verifier\ntheorem answer : Spec := by decide\n",
    Reason.ACCEPTED,
    note="the only path to ACCEPTED: the kernel accepts `answer : Spec`",
)

HONEST_ACCEPT_TYPE = Fixture.of(
    "honest-accept-type",
    SPEC_TYPE,
    "import Verifier\ndef answer : Spec := ⟨(17, 23), by decide⟩\n",
    Reason.ACCEPTED,
    note="Spec : Type -- a witness, checked as a definition",
)

HONEST_ACCEPT_MATHLIB = Fixture.of(
    "honest-accept-mathlib-import",
    SPEC_MATHLIB,
    "import Verifier\ndef answer : Spec := ⟨(17, 23), by decide⟩\n",
    Reason.ACCEPTED,
    note="a small Mathlib import, proving the search path reaches the pinned oleans",
)

RESTATED_TYPE_NO_IMPORT = Fixture.of(
    "restated-type-no-import",
    SPEC_PROP,
    "theorem answer : 2 + 2 = 4 := by decide\n",
    Reason.ACCEPTED,
    note="no `import Verifier`; the kernel decides by definitional equality",
)

MODULE_SYSTEM_SOLUTION = Fixture.of(
    "module-system-solution",
    SPEC_PROP,
    "module\npublic theorem answer : 2 + 2 = 4 := by decide\n",
    Reason.ACCEPTED,
    note="a `module` file writes multi-part oleans; the checker reads them all",
)

HONEST_REJECT_WRONG_TYPE = Fixture.of(
    "honest-reject-wrong-type",
    SPEC_PROP,
    "theorem answer : 2 + 2 = 4 ∨ 1 = 2 := Or.inl (by decide)\n",
    Reason.REJECTED,
    note="elaborates fine; the kernel refuses `answer : Spec`",
)

HONEST_REJECT_WRONG_WITNESS = Fixture.of(
    "honest-reject-wrong-witness",
    SPEC_TYPE,
    "import Verifier\ndef answer : Spec := ⟨(1, 391), by decide⟩\n",
    Reason.REJECTED,
    note="`decide` fails at elaboration; no olean, nothing to check",
)

SORRY_PROOF = Fixture.of(
    "sorry-proof",
    SPEC_PROP,
    "import Verifier\ntheorem answer : Spec := sorry\n",
    Reason.REJECTED,
    note="elaborates with a warning; `sorryAx` is not on the allowlist",
)

AXIOM_CHEAT = Fixture.of(
    "axiom-cheat",
    SPEC_PROP,
    "import Verifier\naxiom cheat : Spec\ntheorem answer : Spec := cheat\n",
    Reason.REJECTED,
    note="the kernel accepts any axiom; the allowlist does not",
)

NATIVE_DECIDE = Fixture.of(
    "native-decide",
    SPEC_PROP,
    "import Verifier\ntheorem answer : Spec := by native_decide\n",
    Reason.REJECTED,
    note="this toolchain mints a per-use axiom (`answer._native.native_decide.ax_1_1`), "
    "which no allowlist can name in advance: native_decide is never trusted",
)

HEADER_EXTENDS_ALLOWLIST = Fixture.of(
    "header-allow-extends-allowlist",
    "-- judge: allow sorryAx\n" + SPEC_PROP,
    "import Verifier\ntheorem answer : Spec := sorry\n",
    Reason.ACCEPTED,
    note="the header is parsed by Python before anything runs and merged into the "
    "allowlist; sorryAx is the one imported axiom that makes the mechanism observable",
)

HEADER_BAD_NAME = Fixture.of(
    "header-bad-name",
    "-- judge: allow $$$\n" + SPEC_PROP,
    "import Verifier\ntheorem answer : Spec := by decide\n",
    Reason.BUILD_FAILED,
)

HEADER_UNKNOWN_AXIOM = Fixture.of(
    "header-unknown-axiom",
    "-- judge: allow Not.an.axiom\n" + SPEC_PROP,
    "import Verifier\naxiom Not.an.axiom : Spec\ntheorem answer : Spec := Not.an.axiom\n",
    Reason.BUILD_FAILED,
    note="an allowlisted name must already be an axiom of the trusted imports, "
    "otherwise the solution could declare it itself",
)

ADD_DECL_UNCHECKED = Fixture.of(
    "run-cmd-add-decl-unchecked",
    SPEC_PROP,
    """
import Lean
import Verifier
open Lean in
run_cmd do
  let decl := Declaration.thmDecl
    { name := `answer, levelParams := [], type := .const `Spec [], value := .const `True.intro [] }
  match (← getEnv).addDeclCore 0 0 decl none (doCheck := false) with
  | .ok env => setEnv env
  | .error _ => throwError "failed"
""",
    Reason.REJECTED,
    note="an unchecked declaration lands in the olean; the replay sends it to the kernel",
)

SKIP_KERNEL_TC = Fixture.of(
    "skip-kernel-tc",
    SPEC_PROP,
    """
import Lean
import Verifier
set_option debug.skipKernelTC true in
theorem answer : Spec := by
  run_tac do (← Lean.Elab.Tactic.getMainGoal).assign (Lean.mkConst ``True.intro)
""",
    Reason.REJECTED,
    note="assigning the goal behind the elaborator's back passes elaboration once the "
    "kernel is switched off; the replay switches it back on",
)

UNSAFE_ANSWER = Fixture.of(
    "unsafe-answer",
    SPEC_PROP,
    "import Verifier\nunsafe def answer : Spec := by decide\n",
    Reason.REJECTED,
    note="unsafe constants are never replayed and never accepted",
)

REDEFINE_SPEC = Fixture.of(
    "redefine-spec",
    SPEC_PROP,
    "abbrev Spec : Prop := True\ntheorem answer : Spec := trivial\n",
    Reason.REJECTED,
    note="the checker imports Verifier first; a second `Spec` is a duplicate declaration",
)

HASH_EXIT = Fixture.of(
    "hash-exit-before-answer",
    SPEC_PROP,
    "import Verifier\n#exit\ntheorem answer : Spec := by decide\n",
    Reason.REJECTED,
)

PROCESS_EXIT = Fixture.of(
    "io-process-exit-before-answer",
    SPEC_PROP,
    "import Lean\nimport Verifier\nrun_cmd (IO.Process.exit 0 : IO Unit)\n"
    "theorem answer : Spec := by decide\n",
    [Reason.REJECTED, Reason.CRASH],
    note="exit 0 with no olean is not an answer",
)

FAKE_VERDICT_STAGE_2 = Fixture.of(
    "fake-verdict-in-stage-2",
    SPEC_PROP,
    f"""
import Lean
import Verifier
run_cmd IO.println "{SENTINEL}0000 {{\\"ok\\":true,\\"why\\":\\"\\",\\"axioms\\":[],\\"specIsProp\\":true}}"
""",
    Reason.REJECTED,
    note="stage 2 stdout is never read, and the nonce is unguessable anyway",
)

OVERWRITE_VERIFIER_OLEAN = Fixture.of(
    "overwrite-verifier-olean",
    SPEC_PROP,
    """
import Lean
import Verifier
run_cmd IO.FS.writeFile "/work/verifier/Verifier.olean" "not an olean"
theorem answer : Spec := by decide
""",
    Reason.REJECTED,
    requires_isolation=True,
    note="Verifier.olean is mounted read-only into the solution stage; the write "
    "fails and the `run_cmd` error fails elaboration",
)

SHADOW_VERIFIER_OLEAN = Fixture.of(
    "shadow-verifier-olean-in-own-dir",
    SPEC_PROP,
    """
import Lean
import Verifier
open Lean Elab Command in
run_cmd do
  let dir := (System.FilePath.mk (← getFileName)).parent.getD "."
  IO.FS.writeFile (dir / "Verifier.olean") "not an olean"
theorem answer : Spec := by decide
""",
    Reason.ACCEPTED,
    note="the solution may write what it likes into its own directory; the verifier "
    "directory comes first on the search path, so the real olean is the one checked",
)

VERIFIER_SORRY_HELPER = Fixture.of(
    "verifier-sorry-in-helper",
    SPEC_PROP + "theorem helper : 1 = 1 := sorry\n",
    "import Verifier\ntheorem answer : Spec := by decide\n",
    Reason.BUILD_FAILED,
    note="the verifier's own declarations are held to the allowlist",
)

VERIFIER_NO_SPEC = Fixture.of(
    "verifier-no-spec",
    "abbrev Goal : Prop := 2 + 2 = 4\n",
    "theorem answer : 2 + 2 = 4 := by decide\n",
    Reason.BUILD_FAILED,
)

VERIFIER_SYNTAX_ERROR = Fixture.of(
    "verifier-syntax-error",
    "abbrev Spec : Prop := 2 + 2 =\n",
    "theorem answer : 2 + 2 = 4 := by decide\n",
    Reason.BUILD_FAILED,
)

VERIFIER_SPEC_NOT_A_SORT = Fixture.of(
    "verifier-spec-not-a-sort",
    "def Spec : Nat := 4\n",
    "def answer : Nat := 4\n",
    Reason.BUILD_FAILED,
)

INFINITE_LOOP = Fixture.of(
    "infinite-loop-at-elaboration",
    SPEC_PROP,
    """
import Verifier
#eval Id.run do
  let mut i := 0
  while true do
    i := i + 1
  return i
theorem answer : Spec := by decide
""",
    Reason.TIMEOUT,
    budget=_SHORT_BUDGET,
    note="`maxHeartbeats` does not count `#eval`; the outside-in kill does",
)

MEMORY_BALLOON = Fixture.of(
    "memory-balloon-at-elaboration",
    SPEC_PROP,
    """
import Verifier
def balloon (n : Nat) : Array Nat := Id.run do
  let mut a : Array Nat := #[]
  for i in [0:n] do
    a := a.push i
  return a
#eval (balloon 400000000).size
theorem answer : Spec := by decide
""",
    [Reason.OOM, Reason.CRASH],
    budget=_SMALL_MEM_BUDGET,
)

SOLUTION_TOO_LARGE = Fixture.of(
    "solution-too-large",
    SPEC_PROP,
    "theorem answer : 2 + 2 = 4 := by decide\n" + "-- " + "x" * 4096 + "\n",
    Reason.SOLUTION_TOO_LARGE,
    budget=Budget(wall_s=60.0, cpu_s=60.0, mem_bytes=2048 * MIB, max_solution_bytes=1024),
)


ALL_FIXTURES: tuple[Fixture, ...] = (
    HONEST_ACCEPT_PROP,
    HONEST_ACCEPT_TYPE,
    HONEST_ACCEPT_MATHLIB,
    RESTATED_TYPE_NO_IMPORT,
    MODULE_SYSTEM_SOLUTION,
    HONEST_REJECT_WRONG_TYPE,
    HONEST_REJECT_WRONG_WITNESS,
    SORRY_PROOF,
    AXIOM_CHEAT,
    NATIVE_DECIDE,
    HEADER_EXTENDS_ALLOWLIST,
    HEADER_BAD_NAME,
    HEADER_UNKNOWN_AXIOM,
    ADD_DECL_UNCHECKED,
    SKIP_KERNEL_TC,
    UNSAFE_ANSWER,
    REDEFINE_SPEC,
    HASH_EXIT,
    PROCESS_EXIT,
    FAKE_VERDICT_STAGE_2,
    OVERWRITE_VERIFIER_OLEAN,
    SHADOW_VERIFIER_OLEAN,
    VERIFIER_SORRY_HELPER,
    VERIFIER_NO_SPEC,
    VERIFIER_SYNTAX_ERROR,
    VERIFIER_SPEC_NOT_A_SORT,
    INFINITE_LOOP,
    MEMORY_BALLOON,
    SOLUTION_TOO_LARGE,
)
