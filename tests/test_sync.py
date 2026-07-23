import pytest

from lyrisync.sync import current_line_index

LINES = [(10.0, "one"), (20.0, "two"), (30.0, "three")]


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (0.0, -1),     # before first line
        (9.99, -1),
        (10.0, 0),     # exactly on a boundary → that line starts
        (15.0, 0),
        (20.0, 1),
        (29.99, 1),
        (30.0, 2),
        (95.0, 2),     # after last line → last line stays current
    ],
)
def test_boundaries(position, expected):
    assert current_line_index(LINES, position) == expected


def test_empty_lyrics():
    assert current_line_index([], 12.0) == -1


def test_single_line():
    assert current_line_index([(5.0, "only")], 0.0) == -1
    assert current_line_index([(5.0, "only")], 5.0) == 0
    assert current_line_index([(5.0, "only")], 500.0) == 0
