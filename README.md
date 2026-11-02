# MultiTap

Play one game for 1–8 players at once. Each player gets their own emulator instance, tiled
across one fullscreen window. MultiTap is a small libretro frontend: the "emulator" you choose is
a libretro core, and every player's core runs in a separate process.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m multitap
```

Cores are found automatically in:

- system package directories (`/usr/lib/x86_64-linux-gnu/libretro`, …)
- RetroArch's core folders, including flatpak RetroArch
- MultiTap's own folder, which **Get cores…** downloads into from buildbot.libretro.com

**Browse…** adds any `*_libretro.so` from disk. v1 supports software-rendered cores (NES, SNES,
Mega Drive, GB/GBC/GBA, PC Engine, …). OpenGL-only cores are listed but greyed out.

## Launcher

- **Emulator / Game:** the game box is a text field and a dropdown of recently played games for
  that core. **Browse…** filters to the core's file types. Each game is checked first: extension,
  header sniffing (iNES, GB/GBA, Genesis, SNES), then a trial load in a subprocess. START only
  enables once the game is confirmed playable.
- **Players:** each player is a card, tiled the way the screens will be split.
  - Click a **+** cell, or the thin **+** column beside a full grid, to add a player. The **✕**
    on a card removes that player. A player keeps their colour and save slot (P1–P8).
  - Drag a card onto another to swap them, or onto a blank cell to move it. Hover a card and
    click an arrow to grow it into blank space; right-click to grow or shrink.
  - **Split** switches between the grids available for the player count, and **Reset layout**
    puts everyone back in a single cell each.
- **Input:**
  - Each card picks *Keyboard A* (WASD, J/K/U/I, Q/E, C=Select, V=Start), *Keyboard B* (arrows,
    `,` `.` L `;`, `[` `]`, Backspace=Select, Enter=Start), or any connected controller.
  - The controller picture on each card matches the loaded core, and its buttons light up red
    as that player presses them, so every player can check their input at the same time.
  - **Configure** rebinds buttons. START is blocked if a device is missing, disconnected or
    shared, if a required button is unbound, or if keys overlap or use a hotkey.
- **Menu bar:**
  - **File** repeats the emulator and game pickers (Ctrl+O opens a game, Ctrl+Return starts,
    Ctrl+Q quits), with recent games and **Get cores…**.
  - **Inputs** has each player's device and **Configure buttons…**, plus auto-assign.
  - **Config** holds **Graphics…** (scaling filter, integer scaling, aspect ratio, overscan crop;
    shader presets and vsync are saved for the upcoming native renderer), **On-screen display**,
    **Audio…** (backend, output device, volume, how often the audible player switches) and the
    BIOS folder.
- Every setting is remembered in `~/.config/multitap/settings.json`.

## In game

| Key | Action |
| --- | --- |
| Esc | Back to the launcher (saves are flushed). Press twice quickly to quit. |
| R | Reset every player |
| P | Pause or resume every player |

- Player names show bottom-left in that player's colour.
- Only one player is audible at a time. A speaker icon marks who, and it rotates every N seconds
  (set in the launcher, default 120).
- If a controller disconnects mid-game, everyone pauses.

Saves are per player slot: `~/.local/share/multitap/slots/p<N>/<core>/saves/`. BIOS and firmware
go in `~/.local/share/multitap/system/`, or point **BIOS folder…** elsewhere.

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/fixtures/testcore.c` is a tiny libretro core that the tests build with gcc. It drives the
worker, probe and SRAM tests without needing any ROMs.
