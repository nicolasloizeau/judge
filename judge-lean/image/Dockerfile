# Sandbox image for Lean verifiers.
#
# Build context is the monorepo root:
#   docker build -f judge-lean/image/Dockerfile -t judge-lean:0.1.0 .
#   (or: judge-lean build-image)
#
# Everything is pinned: the base image by manifest digest, elan by version, the
# Lean toolchain by image/project/lean-toolchain, Mathlib (and its
# dependencies) by commit in image/project/lake-manifest.json. The network is
# used only here, at build time, to fetch those exact things -- `lake exe cache
# get` downloads Mathlib's prebuilt oleans, the Lean analogue of `pip install`.
# Nothing is resolved to "latest". The resulting image id is what a Verdict
# records, so a verdict is replayable from
# (verifier_src, solution, budget, image_digest); there is no seed.
#
# Changing either pin changes the digest. Verdicts recorded under the old
# digest replay only with the old image.

# debian:bookworm-slim
ARG BASE=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

# ---------------------------------------------------------------------------
FROM ${BASE} AS build

ARG ELAN_VERSION=4.2.4

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

ENV ELAN_HOME=/opt/elan \
    PATH=/opt/elan/bin:/usr/local/bin:/usr/bin:/bin

WORKDIR /opt/judge/project

# The toolchain pin first, on its own, so a Mathlib bump does not re-download
# the toolchain and vice versa.
COPY judge-lean/image/project/lean-toolchain ./
RUN curl -sSfL "https://github.com/leanprover/elan/releases/download/v${ELAN_VERSION}/elan-x86_64-unknown-linux-gnu.tar.gz" \
      -o /tmp/elan.tar.gz \
 && tar xzf /tmp/elan.tar.gz -C /tmp \
 && /tmp/elan-init -y --no-modify-path --default-toolchain "$(cat lean-toolchain)" \
 && rm -f /tmp/elan.tar.gz /tmp/elan-init \
 && lean --version

# The dependency pins: Lake clones every package at the exact revision in the
# manifest, then Mathlib's cache tool downloads the prebuilt oleans for it.
COPY judge-lean/image/project/lakefile.toml judge-lean/image/project/lake-manifest.json ./
RUN lake exe cache get

# The trusted checker. Lean core only; builds in seconds.
COPY judge-lean/image/project/JudgeLeanCheck.lean ./
RUN lake build judge-lean-check \
 && .lake/build/bin/judge-lean-check 2>&1 | grep -q usage

# Assemble the runtime tree. One merged search path holds every dependency's
# oleans (and IR); the project's own build directory is deliberately *not*
# included, so nothing can `import JudgeLeanCheck`. The git checkouts go only
# now: Lake needs them above to know the packages are at the pinned revisions.
# The cache tool unpacks oleans without world-read permission; the stages run
# as a non-root uid that is not the owner, so everything is made readable.
RUN find .lake/packages -maxdepth 2 -name .git -type d -prune -exec rm -rf {} + \
 && mkdir -p /opt/judge/lib/lean /opt/judge/bin \
 && for pkg in .lake/packages/*; do \
      if [ -d "$pkg/.lake/build/lib/lean" ]; then \
        cp -al "$pkg/.lake/build/lib/lean/." /opt/judge/lib/lean/; \
      fi; \
    done \
 && find /opt/judge/lib \( -name '*.hash' -o -name '*.ilean' -o -name '*.trace' -o -name '*.c' \) -delete \
 && cp .lake/build/bin/judge-lean-check /opt/judge/bin/judge-lean-check \
 && mv "$(dirname "$(dirname "$(readlink -f "$(elan which lean)")")")" /opt/lean \
 && rm -rf /opt/lean/src /opt/lean/include /opt/lean/share \
           /opt/lean/lib/lean/*.a /opt/lean/lib/*.a /opt/lean/lib/clang \
           /opt/lean/lib/libLLVM* /opt/lean/lib/libclang-cpp* \
           /opt/lean/bin/clang /opt/lean/bin/ld.lld /opt/lean/bin/llvm-ar \
           /opt/lean/bin/leanc /opt/lean/bin/leanmake /opt/lean/bin/lake \
           /opt/lean/bin/leanchecker /opt/lean/bin/leantar \
 && chmod -R a+rX /opt/lean /opt/judge/lib /opt/judge/bin \
 && du -sh /opt/lean /opt/judge/lib /opt/judge/bin

# ---------------------------------------------------------------------------
FROM ${BASE}

COPY --from=build /opt/lean /opt/lean
COPY --from=build /opt/judge/lib /opt/judge/lib
COPY --from=build /opt/judge/bin /opt/judge/bin
COPY judge-lean/image/harness.sh /opt/judge/harness.sh
# The pins, for `docker run ... cat` style inspection of a running image.
COPY judge-lean/image/project/lean-toolchain judge-lean/image/project/lake-manifest.json /opt/judge/pins/

# Baked in so that no stage needs `lake`, `elan` or a writable directory:
# the search path, the sysroot the checker asks for, and the two binaries the
# harness runs. TMPDIR/HOME at a path that does not exist, as in judge-python.
ENV LEAN_SYSROOT=/opt/lean \
    LEAN_PATH=/opt/judge/lib/lean \
    JUDGE_LEAN=/opt/lean/bin/lean \
    JUDGE_LEAN_CHECK=/opt/judge/bin/judge-lean-check \
    PATH=/opt/lean/bin:/usr/local/bin:/usr/bin:/bin \
    TMPDIR=/nonexistent \
    HOME=/nonexistent

# Nonroot, matching SandboxPolicy.user (the Lean backend runs as the host's
# own non-root uid instead when it has one; see judge.lean.backend.lean_policy).
RUN /usr/sbin/groupadd --gid 65532 nonroot \
 && /usr/sbin/useradd --uid 65532 --gid 65532 --home-dir /nonexistent --no-create-home \
      --shell /usr/sbin/nologin nonroot

# Fail the build rather than ship an image that cannot verify the simplest
# pair end to end through the harness, as the backend will drive it.
RUN set -e; \
    mkdir -p /tmp/smoke/verifier /tmp/smoke/solution; \
    printf 'abbrev Spec : Prop := 2 + 2 = 4\n' > /tmp/smoke/verifier/Verifier.lean; \
    printf 'import Verifier\ntheorem answer : Spec := by decide\n' > /tmp/smoke/solution/Solution.lean; \
    sh /opt/judge/harness.sh elab 60 0 134217728 400000 2 /tmp/smoke/verifier /tmp/smoke/verifier Verifier; \
    sh /opt/judge/harness.sh check 60 0 /tmp/smoke/verifier verifier smoke propext,Quot.sound,Classical.choice | grep -q '"ok":true'; \
    sh /opt/judge/harness.sh elab 60 0 134217728 400000 2 /tmp/smoke/verifier:/tmp/smoke/solution /tmp/smoke/solution Solution; \
    sh /opt/judge/harness.sh check 60 0 /tmp/smoke/verifier:/tmp/smoke/solution solution smoke propext,Quot.sound,Classical.choice | grep -q '"ok":true'; \
    rm -rf /tmp/smoke

USER 65532:65532
WORKDIR /

ENTRYPOINT ["/bin/sh", "/opt/judge/harness.sh"]
