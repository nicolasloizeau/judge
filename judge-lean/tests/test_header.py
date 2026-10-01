"""The allow header is parsed as text before anything runs; pin its grammar."""

from __future__ import annotations

import pytest

from judge.lean.header import (
    DEFAULT_ALLOW,
    HeaderError,
    is_lean_name,
    merged_allowlist,
    parse_allow_header,
)


def test_no_header_means_no_extra_names() -> None:
    assert parse_allow_header("abbrev Spec : Prop := True\n") == ()


def test_single_line() -> None:
    src = "-- judge: allow Lean.ofReduceNat\nabbrev Spec : Prop := True\n"
    assert parse_allow_header(src) == ("Lean.ofReduceNat",)


def test_commas_and_whitespace_both_separate() -> None:
    src = "-- judge: allow A.b, C.d  E.f\n"
    assert parse_allow_header(src) == ("A.b", "C.d", "E.f")


def test_several_lines_merge_in_order_without_duplicates() -> None:
    src = "-- judge: allow A.b\n\n-- a comment\n-- judge: allow C.d A.b\nimport Mathlib\n"
    assert parse_allow_header(src) == ("A.b", "C.d")


def test_header_ends_at_first_non_comment_line() -> None:
    src = "import Mathlib\n-- judge: allow Sneaky.axiom\nabbrev Spec : Prop := True\n"
    assert parse_allow_header(src) == ()


def test_header_ends_at_a_block_comment() -> None:
    src = "/- doc -/\n-- judge: allow Sneaky.axiom\n"
    assert parse_allow_header(src) == ()


def test_spacing_around_the_keyword_is_flexible() -> None:
    assert parse_allow_header("--judge:allow X.y\n") == ("X.y",)
    assert parse_allow_header("--   judge:   allow   X.y   \n") == ("X.y",)


def test_empty_allow_line_is_an_error() -> None:
    with pytest.raises(HeaderError, match="names no axiom"):
        parse_allow_header("-- judge: allow\n")


@pytest.mark.parametrize("bad", ["$$$", "a..b", ".x", "x.", "1abc", "a b;c"])
def test_non_identifier_is_an_error(bad: str) -> None:
    with pytest.raises(HeaderError, match="not a Lean identifier"):
        parse_allow_header(f"-- judge: allow {bad}\n")


@pytest.mark.parametrize("good", ["propext", "Classical.choice", "Lean.ofReduceBool", "x'", "α.β"])
def test_identifier_grammar(good: str) -> None:
    assert is_lean_name(good)


def test_merged_allowlist_keeps_defaults_first() -> None:
    merged = merged_allowlist(DEFAULT_ALLOW, ("Extra.one", "propext"))
    assert merged == (*DEFAULT_ALLOW, "Extra.one")


def test_defaults_are_exactly_the_classical_three() -> None:
    assert set(DEFAULT_ALLOW) == {"propext", "Classical.choice", "Quot.sound"}
