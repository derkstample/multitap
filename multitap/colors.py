"""Fixed, distinct colors for players 1-8 (used in the launcher, preview and session)."""

PLAYER_COLORS = (
    "#E53935",  # P1 red
    "#1E88E5",  # P2 blue
    "#43A047",  # P3 green
    "#FDD835",  # P4 yellow
    "#8E24AA",  # P5 purple
    "#FB8C00",  # P6 orange
    "#00ACC1",  # P7 cyan
    "#EC407A",  # P8 pink
)

MAX_PLAYERS = len(PLAYER_COLORS)


def player_color(index: int) -> str:
    return PLAYER_COLORS[index % MAX_PLAYERS]
