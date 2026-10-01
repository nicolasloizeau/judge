# CLI

Each language package installs its own entry point with the same three
subcommands: `judge` (Python) and `judge-lean` (Lean).

```bash
judge build-image                                          # build the sandbox image
judge verify --verifier f.py --solution s.txt              # verify one pair
judge selftest                                             # run the adversarial suite

judge-lean build-image
judge-lean verify --verifier Verifier.lean --solution Solution.lean
judge-lean selftest
```

Add `-v` for `DEBUG` logging. Operator errors — a missing image, an unenforceable
budget, an unreadable file — print `judge: <message>` (or `judge-lean: <message>`)
on stderr and exit `2`.

## `verify`

```
judge verify --verifier PATH --solution PATH
             [--json]
             [--backend {gvisor,local}] [--image IMAGE] [--runtime RUNTIME]
             [--wall-s S] [--cpu-s S] [--mem-mib MIB] [--max-solution-bytes N]
```

| Flag | `judge` default | `judge-lean` default | |
| --- | --- | --- | --- |
| `--verifier` | *required* | *required* | Path to the verifier source |
| `--solution` | *required* | *required* | Path to the solution |
| `--json` | off | off | Emit the verdict as JSON instead of the human rendering |
| `--backend` | `gvisor` | `gvisor` | `local` skips Docker but **does not sandbox anything** |
| `--image` | `judge-python:0.1.0` | `judge-lean:0.1.0` | Image tag |
| `--runtime` | `runsc` | `runsc` | Docker runtime |
| `--project` | – | the checkout's | Lake project the local backend runs from |
| `--wall-s` | `10.0` | `300.0` | Wall-clock budget (per stage for Lean) |
| `--cpu-s` | `10.0` | `300.0` | CPU budget (per stage for Lean) |
| `--mem-mib` | `512` | `8192` | Memory budget, MiB (per stage for Lean) |
| `--max-solution-bytes` | `65536` | `262144` | Max solution size |

**Exit status is the verdict**: `0` accepted, `1` not accepted, `2` operator error.

```bash
if judge verify --verifier v.py --solution s.txt > /dev/null; then
    echo accepted
fi
```

```console
$ judge verify --verifier verifier.py --solution solution.txt
ACCEPTED  (ACCEPTED)
  seed          : 4545743040626105188
  image_digest  : sha256:7e087273820365fa3f0eb54d6510f8bdeb06c29016260d37f1086a60f58074f1
  wall_s        : 1.321
  cpu_s         : 0.950
  mem_bytes     : 78422016
  solution_bytes: 3
  host.backend  : gvisor
  host.docker_version: 28.3.3
  host.image    : judge-python:0.1.0
  host.kernel   : 6.15.0-061500-generic
  host.platform : Linux-6.15.0-061500-generic-x86_64-with-glibc2.39
  host.runtime  : runsc
```

A non-acceptance leads with `NOT ACCEPTED` and adds a `detail` line:

```console
$ judge verify --verifier verifier.py --solution wrong.txt
NOT ACCEPTED  (REJECTED)
  ...
  detail        : verify() returned False, not True
```

A Lean verdict looks the same, with `seed : 0`, the deciding `stage` and the
axioms the proof used:

```console
$ judge-lean verify --verifier Verifier.lean --solution Solution.lean
ACCEPTED  (ACCEPTED)
  seed          : 0
  image_digest  : sha256:...
  wall_s        : 5.812
  cpu_s         : 1.930
  mem_bytes     : 412745728
  solution_bytes: 57
  host.axioms   : []
  host.backend  : gvisor
  host.language : lean
  host.spec_is_prop: True
  host.stage    : check-solution
  ...
```

With `--json`:

```json
{
  "accepted": true,
  "detail": "",
  "host": {"backend": "gvisor", "runtime": "runsc", "...": "..."},
  "image_digest": "sha256:7e087273820365fa3f0eb54d6510f8bdeb06c29016260d37f1086a60f58074f1",
  "reason": "ACCEPTED",
  "seed": 2132798009332043095,
  "used": {
    "build_mem": null,
    "build_s": null,
    "cpu_s": 0.97,
    "max_solution_bytes": 3,
    "mem_bytes": 78651392,
    "wall_s": 1.3450952310013236
  }
}
```

In `used`, `max_solution_bytes` is the *actual* solution size, and values are
unrounded — `pretty()` formats to three decimals, `to_json()` does not.

## `build-image`

```
judge build-image [--tag TAG] [--no-cache] [--quiet]
judge-lean build-image [--tag TAG] [--no-cache] [--quiet]
```

```console
$ judge build-image
built judge-python:0.1.0
image_digest: sha256:7e087273820365fa3f0eb54d6510f8bdeb06c29016260d37f1086a60f58074f1
```

Must be run from a source checkout — the Docker build context needs
`judge-core` too, so a pip-installed package cannot build its own image and
says so rather than guessing.

Any change to a Dockerfile, a pinned requirement, `lean-toolchain`,
`lake-manifest.json` or either package changes the image id, which is what every
verdict records. The Lean image downloads Mathlib's prebuilt oleans at build
time and takes a while and several gigabytes of disk.

## `selftest`

Runs an adversarial fixture suite and asserts each case is contained.

```
judge selftest [--policy] [--backend {gvisor,local}] [--wall-s S] ...
judge-lean selftest [--policy] [--backend {gvisor,local}] [--wall-s S] ...
```

```console
$ judge selftest --backend gvisor
judge selftest -- backend: gvisor

PASS  honest-accept                got=ACCEPTED           want=ACCEPTED   1.44s
...
27 passed, 0 failed, 0 skipped
```

```console
$ judge-lean selftest --backend local
judge-lean selftest -- backend: local-insecure

PASS  honest-accept-prop           got=ACCEPTED           want=ACCEPTED   0.47s
PASS  honest-accept-type           got=ACCEPTED           want=ACCEPTED   0.51s
PASS  sorry-proof                  got=REJECTED           want=REJECTED   0.44s
        detail: `answer` depends on disallowed axioms: #[sorryAx]
...
28 passed, 0 failed, 1 skipped
```

Exit `0` when every case passed or skipped, `1` otherwise. `--policy` prints the
sandbox configuration first.

On the local backend, fixtures that need real isolation are **skipped** rather
than faked: five of the Python fixtures (`22 passed, 0 failed, 5 skipped`) and
one of the Lean fixtures.

This is the suite that backs the sandbox claims; run it after changing anything
about the sandbox. Details in the [repository
README](https://github.com/nicolasloizeau/judge).
