import pytest

from multitap.layout.model import PRESETS, Layout, Rect


@pytest.mark.parametrize("count,grid", [(n, g) for n, gs in PRESETS.items() for g in gs])
def test_defaults_are_valid_and_cover_every_player(count, grid):
    layout = Layout.default(count, grid)
    assert layout.count == count
    assert layout.is_valid()
    assert (layout.cols, layout.rows) == grid


def test_three_players_default_leaves_a_blank_cell():
    layout = Layout.default(3)
    assert layout.rects == [Rect(0, 0), Rect(0, 1), Rect(1, 0)]
    assert layout.free_cells() == [(1, 1)]


def test_two_player_presets_are_side_by_side_and_stacked():
    assert PRESETS[2] == [(2, 1), (1, 2)]


def test_grow_into_blank_then_shrink():
    layout = Layout.default(3)
    assert layout.can_grow(1, "down")
    assert not layout.can_grow(0, "down")  # P3 is below
    assert layout.grow(2, "right")
    assert layout.rects[2] == Rect(1, 0, 1, 2) and layout.free_cells() == []
    layout.shrink(2)
    assert layout.free_cells() == [(1, 1)]


def test_move_to_blank_cell_and_swap():
    layout = Layout.default(3)
    layout.move_to_cell(2, 1, 1)
    assert layout.rects[2] == Rect(1, 1)
    assert layout.free_cells() == [(1, 0)]
    layout.move_to_cell(0, 0, 1)  # drop P1 on P2: swap
    assert layout.rects[0] == Rect(0, 1) and layout.rects[1] == Rect(0, 0)
    assert layout.is_valid()


def test_moving_a_large_tile_to_a_blank_cell_keeps_layout_valid():
    layout = Layout(2, 2, [Rect(0, 0, 2, 1), Rect(0, 1)])
    layout.move_to_cell(0, 1, 1)
    assert layout.is_valid()
    assert layout.rects[0] == Rect(1, 1)


def test_add_player_on_blank_cell_or_by_growing_the_grid():
    layout = Layout.default(3)
    assert layout.add_player((1, 1)) and layout.rects[3] == Rect(1, 1)
    assert not layout.add_player((1, 1))  # taken now
    assert layout.add_player()  # full 2x2 -> 3x2, new player at the top of the new column
    assert (layout.cols, layout.rows) == (3, 2) and layout.rects[4] == Rect(0, 2)
    assert layout.rects[:4] == [Rect(0, 0), Rect(0, 1), Rect(1, 0), Rect(1, 1)]


@pytest.mark.parametrize("grid", [(1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (2, 3)])
def test_adding_to_a_full_grid_keeps_existing_tiles(grid):
    n = grid[0] * grid[1]
    layout = Layout.default(n, grid)
    before = list(layout.rects)
    assert layout.add_player()
    assert layout.rects[:n] == before and layout.count == n + 1 and layout.is_valid()


def test_cannot_add_a_ninth_player():
    layout = Layout.default(8)
    assert not layout.add_player()


def test_remove_player_shrinks_the_grid_when_possible():
    layout = Layout.default(3)
    layout.remove_player(2)  # bottom-left gone: the top row fits side by side
    assert (layout.cols, layout.rows) == (2, 1) and layout.rects == [Rect(0, 0), Rect(0, 1)]
    layout = Layout.default(3)
    layout.remove_player(0)  # diagonal leftovers are repacked in reading order
    assert (layout.cols, layout.rows) == (2, 1)
    assert layout.rects == [Rect(0, 0), Rect(0, 1)]


def test_remove_keeps_hand_made_spans():
    layout = Layout(2, 2, [Rect(0, 0, 2, 1), Rect(0, 1), Rect(1, 1)])
    layout.remove_player(1)
    assert (layout.cols, layout.rows) == (2, 2) and layout.rects == [Rect(0, 0, 2, 1), Rect(1, 1)]


def test_pixel_rects_tile_the_screen():
    layout = Layout.default(4)
    rects = layout.pixel_rects(1920, 1080)
    assert rects == [(0, 0, 960, 540), (960, 0, 960, 540), (0, 540, 960, 540), (960, 540, 960, 540)]
    gapped = layout.pixel_rects(1920, 1080, gap=4)
    assert gapped[0] == (0, 0, 958, 538)
    assert Layout.default(3).cell_rects(100, 100)[(1, 1)] == (50, 50, 50, 50)


def test_round_trip_and_rejects_wrong_count():
    layout = Layout.default(5, (2, 3))
    layout.swap(0, 4)
    restored = Layout.from_dict(layout.to_dict(), 5)
    assert restored.rects == layout.rects
    assert Layout.from_dict(layout.to_dict(), 6).count == 6  # mismatched -> default
    assert Layout.from_dict({"garbage": 1}, 3).is_valid()
