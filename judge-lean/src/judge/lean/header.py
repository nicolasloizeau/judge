"""The ``-- judge: allow ...`` header of a Lean verifier. Pure, no I/O.

A verifier may extend the axiom allowlist with one or more comment lines at the
very top of the file::

    -- judge: allow Lean.ofReduceNat
    -- judge: allow Some.other, Yet.another
    import Mathlib.Tactic
    abbrev Spec : Prop := ...

Python parses this as plain text *before anything runs*, merges it with the
default list and hands the result to the trusted checker, which in turn only
accepts names that are axioms of the imported (trusted) environment. The
header is therefore never executed and cannot be rewritten by the verifier's
own ``run_cmd`` or by the solution.

Only the leading block of blank lines and ``--`` line comments is scanned; the
first ``import``, declaration or block comment ends the header.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_ALLOW: tuple[str, ...] = ("propext", "Classical.choice", "Quot.sound")
"""Axioms every Lean verdict may depend on: the three the kernel itself
introduces for classical mathematics. ``sorryAx``, user ``axiom``s and the
per-use axioms that ``native_decide`` mints are all outside it."""

HEADER_PREFIX = "-- judge: allow"

_LINE_RE = re.compile(r"^--\s*judge:\s*allow\b(?P<rest>.*)$")
_NAME_RE = re.compile(r"^[^\W\d][\w'!?]*(?:\.[^\W\d][\w'!?]*)*$")
_SEPARATORS = re.compile(r"[,\s]+")


class HeaderError(ValueError):
    """The header exists but cannot be understood. Maps to ``BUILD_FAILED``."""


def is_lean_name(name: str) -> bool:
    """A dotted identifier such as ``Classical.choice`` or ``Lean.ofReduceNat``."""
    return bool(_NAME_RE.match(name))


def parse_allow_header(verifier_src: str) -> tuple[str, ...]:
    """Names from every ``-- judge: allow`` line of the leading comment block.

    Names are separated by commas and/or whitespace. Returns them in order of
    first appearance, without duplicates. Raises :class:`HeaderError` for a
    line that names nothing or names something that is not a Lean identifier.
    """
    found: list[str] = []
    for lineno, raw in enumerate(verifier_src.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("--"):
            break  # end of the header block
        match = _LINE_RE.match(line)
        if match is None:
            continue  # an ordinary comment
        names = [n for n in _SEPARATORS.split(match.group("rest").strip()) if n]
        if not names:
            raise HeaderError(f"line {lineno}: '{HEADER_PREFIX}' names no axiom")
        for name in names:
            if not is_lean_name(name):
                raise HeaderError(f"line {lineno}: {name!r} is not a Lean identifier")
            if name not in found:
                found.append(name)
    return tuple(found)


def merged_allowlist(base: Iterable[str], extra: Iterable[str]) -> tuple[str, ...]:
    """``base`` then ``extra``, deduplicated, order preserved."""
    merged: list[str] = []
    for name in (*base, *extra):
        if name not in merged:
            merged.append(name)
    return tuple(merged)
