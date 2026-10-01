"""Locating, building and identifying the ``judge-lean`` sandbox image, and
locating the Lake project it is built from (which the local backend runs
directly)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEFAULT_IMAGE_TAG = "judge-lean:0.1.0"
DOCKERFILE_RELPATH = Path("judge-lean/image/Dockerfile")
PROJECT_RELPATH = Path("judge-lean/image/project")
HARNESS_RELPATH = Path("judge-lean/image/harness.sh")
CHECKER_RELPATH = Path(".lake/build/bin/judge-lean-check")
"""Where ``lake build judge-lean-check`` puts the checker, relative to the project."""


class ImageError(RuntimeError):
    """Raised when the image cannot be located, built or inspected."""


def repo_root() -> Path:
    """Walk up from this file to the monorepo checkout.

    The build context has to contain ``judge-core`` as well as this package,
    so a pip-installed ``judge-lean`` cannot build its own image; that is a
    source-checkout operation and says so.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / DOCKERFILE_RELPATH).is_file() and (parent / "judge-core").is_dir():
            return parent
    raise ImageError(
        "cannot find the judge monorepo checkout containing "
        f"{DOCKERFILE_RELPATH}; build the image from a source checkout"
    )


def project_dir() -> Path:
    """The committed Lake project: toolchain pin, manifest pin, checker source."""
    return repo_root() / PROJECT_RELPATH


def harness_path() -> Path:
    return repo_root() / HARNESS_RELPATH


def pinned_toolchain(project: Path | None = None) -> str:
    """The exact toolchain name from ``lean-toolchain`` (e.g. ``leanprover/lean4:v4.34.1``)."""
    root = project_dir() if project is None else project
    return (root / "lean-toolchain").read_text(encoding="utf-8").strip()


def package_search_path(project: Path | None = None) -> list[Path]:
    """The ``LEAN_PATH`` entries of every dependency in ``lake-manifest.json``.

    Deliberately *not* the project's own ``.lake/build/lib/lean``: the checker's
    module is not something a verifier should be able to import.
    """
    root = project_dir() if project is None else project
    manifest = json.loads((root / "lake-manifest.json").read_text(encoding="utf-8"))
    packages_dir = root / str(manifest.get("packagesDir", ".lake/packages"))
    return [
        packages_dir / str(pkg["name"]) / ".lake" / "build" / "lib" / "lean"
        for pkg in manifest.get("packages", [])
    ]


def build_image(
    *,
    tag: str = DEFAULT_IMAGE_TAG,
    docker: str = "docker",
    no_cache: bool = False,
    quiet: bool = False,
) -> str:
    """Build the pinned image and return its content-addressed id."""
    root = repo_root()
    args = [docker, "build", "-f", str(root / DOCKERFILE_RELPATH), "-t", tag]
    if no_cache:
        args.append("--no-cache")
    if quiet:
        args.append("--quiet")
    args.append(str(root))

    proc = subprocess.run(args, check=False, text=True)
    if proc.returncode != 0:
        raise ImageError(f"docker build failed (exit {proc.returncode})")
    return image_digest(tag, docker=docker)


def image_digest(tag: str = DEFAULT_IMAGE_TAG, *, docker: str = "docker") -> str:
    """The image id (``sha256:...``) of ``tag``.

    Locally built images have no registry digest, so the image *config* id is
    what pins reproducibility here; it changes whenever any layer changes --
    including whenever ``lean-toolchain`` or ``lake-manifest.json`` does.
    """
    proc = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ImageError(f"image {tag!r} is not present locally: {proc.stderr.strip()}")
    return proc.stdout.strip()


def image_exists(tag: str = DEFAULT_IMAGE_TAG, *, docker: str = "docker") -> bool:
    try:
        return bool(image_digest(tag, docker=docker))
    except ImageError:
        return False
