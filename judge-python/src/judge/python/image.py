"""Locating, building and identifying the ``judge-python`` sandbox image."""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_IMAGE_TAG = "judge-python:0.1.0"
DOCKERFILE_RELPATH = Path("judge-python/image/Dockerfile")


class ImageError(RuntimeError):
    """Raised when the image cannot be located, built or inspected."""


def repo_root() -> Path:
    """Walk up from this file to the monorepo checkout.

    The build context has to contain *both* packages, so a pip-installed
    ``judge-python`` cannot build its own image; that is a source-checkout
    operation and says so.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / DOCKERFILE_RELPATH).is_file() and (parent / "judge-core").is_dir():
            return parent
    raise ImageError(
        "cannot find the judge monorepo checkout containing "
        f"{DOCKERFILE_RELPATH}; build the image from a source checkout"
    )


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
    what pins reproducibility here; it changes whenever any layer changes.
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
