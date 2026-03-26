from __future__ import annotations

import pytest

from pdf_toolkit.pdf_ops.ranges import build_every_n_groups, parse_page_range_spec, parse_split_range_groups


def test_parse_page_range_spec_preserves_order_without_duplicates() -> None:
    assert parse_page_range_spec("1,3-5,4,2", 10) == [1, 3, 4, 5, 2]


def test_parse_split_range_groups_supports_newlines() -> None:
    assert parse_split_range_groups("1-2\n3-4", 10) == [[1, 2], [3, 4]]


def test_build_every_n_groups() -> None:
    assert build_every_n_groups(7, 3) == [[1, 2, 3], [4, 5, 6], [7]]


def test_parse_page_range_rejects_descending_range() -> None:
    with pytest.raises(ValueError):
        parse_page_range_spec("5-2", 10)
