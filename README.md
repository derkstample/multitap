# MultiTap

Play one game for 1–8 players at once. Each player gets their own emulator instance, tiled
across one fullscreen window. MultiTap is a small libretro frontend: the "emulator" you choose is
a libretro core, and every player's core runs in a separate process.

## Setup

Build requirements: a C++20 compiler (GCC 11+ or Clang 14+), CMake 3.24+ (pip fetches one if
the system has none), pkg-config, and the libarchive, SDL2 and OpenGL development headers. On
Debian/Ubuntu:

```bash
sudo apt install build-essential cmake pkg-config libsdl2-dev libarchive-dev libgl-dev
```

Optional: [librashader](https://github.com/SnowflakePowered/librashader) (`librashader.so` on the
library path) for RetroArch shader presets. Without it, games show without the preset.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m multitap
```

`pip install` also builds the native runtime with CMake (scikit-build-core), downloading
nlohmann/json, doctest and Dear ImGui if they aren't installed, and puts the binaries in
`multitap/bin/`.
`.venv/bin/python -m multitap.native` reports which binaries were found. Re-run the
`pip install` line after changing anything under `native/`.

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
  - **Config** holds **Graphics…** (scaling filter, integer scaling, aspect ratio, overscan crop,
    a RetroArch `.slangp` shader preset, vsync), **On-screen display** (what it shows and in which
    corner), **Audio…** (backend, output device, volume, how often the audible player switches)
    and the BIOS folder.
- Every setting is remembered in `~/.config/multitap/settings.json`.

## In game

| Key | Action |
| --- | --- |
| Esc | Back to the launcher (saves are flushed). Press twice quickly to quit. |
| R | Reset every player |
| P | Pause or resume every player |
| F1 | Show or hide the on-screen display |

- Player names show bottom-left in that player's colour.
- Only one player is audible at a time. A speaker icon marks who, and it rotates every N seconds
  (set in the launcher, default 120).
- If a controller disconnects mid-game, everyone pauses.
- The on-screen display shows, per player: core and display frame rates, frame time with a graph
  of frame intervals, live inputs, audio status and the time to the next switch, and the core,
  game and frame format.
- The session's log is kept in `~/.local/state/multitap/session.log`.

Saves are per player slot: `~/.local/share/multitap/slots/p<N>/<core>/saves/`. BIOS and firmware
go in `~/.local/share/multitap/system/`, or point **BIOS folder…** elsewhere.

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/fixtures/testcore.c` is a tiny libretro core that the tests build with gcc. It drives the
worker, probe and SRAM tests without needing any ROMs. `tests/test_native_worker.py` runs the same
checks against the C++ `multitap-worker`, and `tests/test_native_session.py` runs
`multitap-session` headless (SDL's offscreen driver) and checks its screenshots. Both skip when
the binaries aren't built.

The native unit tests (shared-memory triple buffer, audio ring, session config and geometry) run
with ctest from the build folder that `pip install` used:

```bash
.venv/bin/ctest --test-dir build/cp312-cp312-linux_x86_64
```

## Native runtime

The game runtime is C++ (`native/`); the Python launcher stays. When a game starts, the launcher
writes `$XDG_RUNTIME_DIR/multitap/session.json` and runs `multitap-session` (SDL2, OpenGL 3.3,
Dear ImGui), which spawns one `multitap-worker` per player. Workers talk to the session through
shared memory (`native/common/shm.hpp` documents the layout) and a JSON control socket
(`native/worker/main.cpp`). Every worker loads paused and they all start on the same frame.

`MULTITAP_PYTHON_SESSION=1` uses the older Python session instead (it is also used when
`multitap-session` isn't built).
