import Lean
import Lean.Replay

/-!
# `judge-lean-check` -- the trusted half of a Lean verification

Everything that runs *before* this program is untrusted: `lean` elaborating the
verifier and the solution executes arbitrary code (`run_cmd`, macros, `#eval`,
`initialize`) with full `IO`. Nothing decided in those processes is believed.
This program runs afterwards, in a container with nothing writable, and treats
the two `.olean` files they produced as **data**: every declaration of the two
untrusted modules is sent back through the Lean kernel (`Kernel.Environment.replay`,
as `lean4checker` does), and only then is the question "is `answer : Spec`?"
asked -- again of the kernel, via `addDecl` of a fresh declaration.

Two modes, both printing exactly one framed JSON line on stdout and exiting 0
whether the answer is positive or negative. A nonzero exit means *this program*
failed (bad arguments, no toolchain), never "the solution is wrong".

* `--mode verifier`: import `Verifier` and replay it. `Spec` must exist at the
  root, be safe, have no universe parameters, and its type must reduce to a
  `Sort`. Every axiom any of the verifier's declarations depends on must be on
  the allowlist, and every allowlisted name must be an axiom of the *imported*
  (trusted) environment -- so a verifier cannot allowlist a name a solution
  could then declare for itself.
* `--mode solution`: import the trusted imports of both modules, replay
  `Verifier` then `Solution`, and check `answer`: it must exist at the root,
  be declared by `Solution`, be safe, have no universe parameters. Then a fresh
  theorem (if `Spec : Prop`) or definition (otherwise), named after the per-run
  nonce, of type `Spec` with value `answer` is sent to the kernel. If the
  kernel accepts it and its transitive axioms are all allowlisted, `ok` is
  true.

Only the two untrusted modules are replayed. Mathlib and the core library are
trusted as part of the digest-pinned image.

Depends on Lean core only -- never Mathlib -- so it builds in seconds and the
trusted surface stays small.
-/

open Lean

/-- What the checker reports back; serialised as one framed JSON line. -/
structure Report where
  ok : Bool
  why : String
  axioms : Array String
  specIsProp : Bool := false
  deriving ToJson

structure Args where
  mode : String
  nonce : String
  allow : Array Name

def SENTINEL := "@@JUDGE-VERDICT@@"

def usage : String :=
  "usage: judge-lean-check --mode verifier|solution --nonce NONCE --allow a,b,c"

def parseArgs : List String → Except String Args
  | args => go args none none none
where
  go : List String → Option String → Option String → Option (Array Name) → Except String Args
    | [], some mode, some nonce, allow =>
      if mode == "verifier" || mode == "solution" then
        pure { mode, nonce, allow := allow.getD #[] }
      else
        throw s!"unknown mode {mode}\n{usage}"
    | [], _, _, _ => throw usage
    | "--mode" :: m :: rest, _, nonce, allow => go rest (some m) nonce allow
    | "--nonce" :: n :: rest, mode, _, allow => go rest mode (some n) allow
    | "--allow" :: a :: rest, mode, nonce, _ =>
      let names := (a.splitOn ",").filterMap fun s =>
        let s := s.trimAscii.copy
        if s.isEmpty then none else some s.toName
      go rest mode nonce (some names.toArray)
    | arg :: _, _, _, _ => throw s!"unexpected argument {arg}\n{usage}"

/-- Name of the declaration the kernel is asked to accept. Reserved and
per-run, so an untrusted module cannot pre-declare it. -/
def checkName (nonce : String) : Name :=
  Name.mkStr (Name.mkStr .anonymous "_judge") s!"check_{nonce}"

/-- Read every part of a module's `.olean` from the search path, without
importing it. Mirrors the private `readModuleDataPartsOfMod` in core. -/
def readParts (mod : Name) : IO (Array (ModuleData × CompactedRegion)) := do
  let mFile ← findOLean mod
  unless (← mFile.pathExists) do
    throw <| IO.userError s!"module {mod}: object file '{mFile}' does not exist"
  let mut fnames := #[mFile]
  let main ← readModuleData mFile
  if main.1.isModule then
    let sFile := OLeanLevel.server.adjustFileName mFile
    let pFile := OLeanLevel.private.adjustFileName mFile
    unless (← sFile.pathExists) && (← pFile.pathExists) do
      throw <| IO.userError s!"module {mod}: `module` olean is missing its parts"
    fnames := fnames ++ #[sFile, pFile]
  readModuleDataParts fnames

/-- The constants a module contributes. The last ("most private") part
subsumes the earlier ones. -/
def constantsOf (parts : Array (ModuleData × CompactedRegion)) :
    Std.HashMap Name ConstantInfo := Id.run do
  let mut m := {}
  if h : parts.size > 0 then
    let data := parts[parts.size - 1].1
    for name in data.constNames, ci in data.constants do
      m := m.insert name ci
  return m

/-- Axioms a constant transitively depends on, computed over the *kernel*
environment, i.e. over what was actually type-checked. Same traversal as
`Lean.collectAxioms`, without the elaborator-side caches it needs. -/
partial def collectAxioms (env : Kernel.Environment) (root : Name) : Array Name := Id.run do
  let mut visited : NameSet := {}
  let mut axioms : NameSet := {}
  let mut stack : Array Name := #[root]
  while h : stack.size > 0 do
    let c := stack[stack.size - 1]
    stack := stack.pop
    if visited.contains c then continue
    visited := visited.insert c
    let push (s : Array Name) (e : Expr) : Array Name := s ++ e.getUsedConstants
    match env.find? c with
    | some (.axiomInfo v)  => axioms := axioms.insert c; stack := push stack v.type
    | some (.defnInfo v)   => stack := push (push stack v.type) v.value
    | some (.thmInfo v)    => stack := push (push stack v.type) v.value
    | some (.opaqueInfo v) => stack := push (push stack v.type) v.value
    | some (.quotInfo _)   => pure ()
    | some (.ctorInfo v)   => stack := push stack v.type
    | some (.recInfo v)    => stack := push stack v.type
    | some (.inductInfo v) => stack := push stack v.type ++ v.ctors.toArray
    | none                 => pure ()
  return axioms.toArray.qsort Name.lt

def kernelMessage (env : Kernel.Environment) (ex : Kernel.Exception) : IO String := do
  let msg ← (ex.toMessageData {}).toString
  let _ := env
  return msg

/-- `Sort u` after kernel reduction, or an explanation. -/
def sortOfSpec (kenv : Kernel.Environment) (spec : ConstantInfo) : Except String Level := do
  match Kernel.whnf (.ofKernelEnv kenv) {} spec.type with
  | .error _ => throw "the type of `Spec` does not reduce"
  | .ok (.sort u) => pure u
  | .ok _ => throw "`Spec` must be a type or a proposition (`Spec : Sort u`)"

/-- Axioms outside the allowlist, or outside the trusted imported environment. -/
def disallowed (base : Kernel.Environment) (allow : Array Name) (axioms : Array Name) :
    Array Name :=
  axioms.filter fun a =>
    !allow.contains a || !(base.find? a matches some (.axiomInfo _))

/-- Shared setup: import the trusted modules, then replay the untrusted ones. -/
structure Loaded where
  base : Kernel.Environment
  env : Kernel.Environment
  verifierConsts : Std.HashMap Name ConstantInfo
  solutionConsts : Std.HashMap Name ConstantInfo

def load (withSolution : Bool) : IO Loaded := do
  let vParts ← readParts `Verifier
  let sParts ← if withSolution then readParts `Solution else pure #[]
  let untrusted : NameSet := NameSet.empty.insert `Verifier |>.insert `Solution
  let mut imports : Array Import := #[]
  for part in vParts ++ sParts do
    for imp in part.1.imports do
      if !untrusted.contains imp.module && !imports.any (·.module == imp.module) then
        imports := imports.push { module := imp.module }
  let env ← withImporting do
    let (_, s) ← importModulesCore imports |>.run
    finalizeImport s imports {} (trustLevel := 0) (leakEnv := false) (loadExts := false)
  let base := env.toKernelEnv
  let verifierConsts := constantsOf vParts
  let solutionConsts := constantsOf sParts
  let mut kenv ← base.replay verifierConsts
  if withSolution then
    kenv ← kenv.replay solutionConsts
  return { base, env := kenv, verifierConsts, solutionConsts }

def checkSpec (l : Loaded) : Except String (ConstantInfo × Level) := do
  let some spec := l.env.find? `Spec
    | throw "verifier defines no root-level constant `Spec`"
  unless l.verifierConsts.contains `Spec do
    throw "`Spec` is not declared by the Verifier module itself"
  if spec.isUnsafe then throw "`Spec` must not be `unsafe`"
  unless spec.levelParams.isEmpty do
    throw "`Spec` must not be universe-polymorphic"
  let u ← sortOfSpec l.env spec
  return (spec, u)

def checkVerifier (allow : Array Name) : IO Report := do
  let l ← load (withSolution := false)
  match checkSpec l with
  | .error why => return { ok := false, why, axioms := #[] }
  | .ok (_, u) =>
    for a in allow do
      unless l.base.find? a matches some (.axiomInfo _) do
        return { ok := false, axioms := #[]
                 why := s!"allowlisted name `{a}` is not an axiom of the imported environment" }
    let mut used : NameSet := {}
    for (n, _) in l.verifierConsts.toList do
      for a in collectAxioms l.env n do
        used := used.insert a
    let axioms := used.toArray.qsort Name.lt
    let bad := disallowed l.base allow axioms
    if bad.isEmpty then
      return { ok := true, why := "", axioms := axioms.map toString, specIsProp := u.isZero }
    return { ok := false, axioms := axioms.map toString, specIsProp := u.isZero
             why := s!"verifier depends on disallowed axioms: {bad}" }

def checkSolution (nonce : String) (allow : Array Name) : IO Report := do
  let l ← load (withSolution := true)
  let (_, u) ← match checkSpec l with
    | .error why => return { ok := false, why, axioms := #[] }
    | .ok r => pure r
  let specIsProp := u.isZero
  let some answer := l.env.find? `answer
    | return { ok := false, why := "solution defines no root-level constant `answer`",
               axioms := #[], specIsProp }
  unless l.solutionConsts.contains `answer do
    return { ok := false, why := "`answer` is not declared by the Solution module itself",
             axioms := #[], specIsProp }
  if answer.isUnsafe then
    return { ok := false, why := "`answer` must not be `unsafe`", axioms := #[], specIsProp }
  unless answer.levelParams.isEmpty do
    return { ok := false, why := "`answer` must not be universe-polymorphic",
             axioms := #[], specIsProp }
  let name := checkName nonce
  let val : ConstantVal := { name, levelParams := [], type := .const `Spec [] }
  let decl : Declaration :=
    if specIsProp then
      .thmDecl { val with value := .const `answer [], all := [name] }
    else
      .defnDecl { val with value := .const `answer [], hints := .opaque, safety := .safe,
                           all := [name] }
  match l.env.addDeclCore 0 0 decl none with
  | .error ex =>
    let msg ← kernelMessage l.env ex
    return { ok := false, why := s!"kernel rejected `answer : Spec`: {msg}", axioms := #[],
             specIsProp }
  | .ok env' =>
    let axioms := collectAxioms env' name
    let bad := disallowed l.base allow axioms
    if bad.isEmpty then
      return { ok := true, why := "", axioms := axioms.map toString, specIsProp }
    return { ok := false, why := s!"`answer` depends on disallowed axioms: {bad}",
             axioms := axioms.map toString, specIsProp }

def emit (nonce : String) (r : Report) : IO Unit := do
  let out ← IO.getStdout
  out.putStrLn s!"{SENTINEL}{nonce} {(toJson r).compress}"
  out.flush

def main (argv : List String) : IO UInt32 := do
  let args ← match parseArgs argv with
    | .ok a => pure a
    | .error e => IO.eprintln e; return 2
  initSearchPath (← findSysroot)
  -- Anything the untrusted inputs can cause -- an olean that will not load, an
  -- import that is not there, a declaration the kernel rejects -- is a negative
  -- *result*, reported on stdout with exit 0. Only this program's own failures
  -- (above) exit nonzero.
  let report ← try
      if args.mode == "verifier" then checkVerifier args.allow
      else checkSolution args.nonce args.allow
    catch e =>
      pure { ok := false, why := s!"{e}", axioms := #[] }
  emit args.nonce report
  return 0
