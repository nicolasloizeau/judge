# Sandbox image for Python verifiers.
#
# Build context is the monorepo root:
#   docker build -f judge-python/image/Dockerfile -t judge-python:0.1.0 .
#   (or: judge build-image)
#
# Everything is pinned: the base image by manifest digest, the scientific stack
# by exact version in image/requirements.txt. The resulting image id is what a
# Verdict records, so a verdict is replayable from
# (verifier_src, solution, budget, image_digest, seed).

# python:3.12.7-slim-bookworm
ARG BASE=python@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d

FROM ${BASE} AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /src
COPY judge-python/image/requirements.txt ./requirements.txt
COPY judge-core ./judge-core
COPY judge-python ./judge-python

# Into the default prefix, so the runtime stage needs no PYTHONPATH -- the
# entrypoint runs with -I (isolated), which ignores PYTHON* environment
# variables on purpose.
RUN python -m pip install -r requirements.txt \
 && python -m pip install --no-deps ./judge-core ./judge-python \
 && find /usr/local -name '__pycache__' -type d -prune -exec rm -rf {} +

FROM ${BASE}

COPY --from=build /usr/local /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    TMPDIR=/nonexistent \
    HOME=/nonexistent \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

# Nonroot, matching SandboxPolicy.user. No home directory, no shell.
RUN groupadd --gid 65532 nonroot \
 && useradd --uid 65532 --gid 65532 --home-dir /nonexistent --no-create-home \
      --shell /usr/sbin/nologin nonroot
USER 65532:65532
WORKDIR /

# Fail the build rather than ship an image whose stack does not import.
RUN python -I -c "import numpy, scipy, sympy, mpmath, judge.core, judge.python.harness"

# -I: isolated mode. -B: never write bytecode. The harness owns stdin/stdout.
ENTRYPOINT ["python", "-I", "-B", "-m", "judge.python.harness"]
