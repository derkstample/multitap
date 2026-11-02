from PySide6.QtCore import Qt

from multitap.input.devices import pad_state_bits
from multitap.input.mapping import (KEYBOARD_PRESETS, PAD_DEFAULTS, RESERVED_KEYS, RETROPAD,
                                    validate_players)
from multitap.settings import PlayerSettings

PAD = {"kind": "pad", "guid": "abc", "ordinal": 0, "name": "Pad"}
PAD_KEY = "pad:abc:0"


def kbd(set_):
    return PlayerSettings(device={"kind": "keyboard", "set": set_})


def test_presets_avoid_reserved_keys_and_each_other():
    a, b = set(KEYBOARD_PRESETS["A"].values()), set(KEYBOARD_PRESETS["B"].values())
    assert not (a | b) & set(RESERVED_KEYS)
    assert not a & b


def test_two_keyboard_sets_are_ok():
    st = validate_players([kbd("A"), kbd("B")], [0, 1], set())
    assert all(s.ok for s in st.values())


def test_shared_device_and_missing_device_are_reported():
    st = validate_players([kbd("A"), kbd("A"), PlayerSettings()], [0, 1, 2], set())
    assert "Same device as Player 2" in st[0].problems
    assert "No input device assigned" in st[2].problems
    assert not any("Shares keys" in p for p in st[0].problems)  # no duplicate noise


def test_disconnected_pad():
    p = PlayerSettings(device=PAD)
    assert validate_players([p], [0], set())[0].problems == ["Controller not connected"]
    assert validate_players([p], [0], {PAD_KEY})[0].ok


def test_reserved_and_overlapping_keys():
    p1 = kbd("A")
    p1.kbd_bindings = dict(KEYBOARD_PRESETS["A"], Start=int(Qt.Key_P))
    p2 = kbd("B")
    p2.kbd_bindings = dict(KEYBOARD_PRESETS["B"], Up=int(Qt.Key_W))
    st = validate_players([p1, p2], [0, 1], set())
    assert any("reserved" in m for m in st[0].problems)
    assert any("Shares keys with Player 1" in m for m in st[1].problems)


def test_unbound_required_button():
    p = PlayerSettings(device=PAD, pad_bindings={k: v for k, v in PAD_DEFAULTS.items() if k != "Start"})
    st = validate_players([p], [0], {PAD_KEY})[0]
    assert st.problems == ["Unbound: Start"]


def test_pad_state_bits_buttons_triggers_and_stick():
    bits, analog = pad_state_bits({"a", "start"}, {"lefttrigger": 30000, "leftx": -32000}, PAD_DEFAULTS)
    expect = {"B", "Start", "L2", "Left"}  # SDL 'a' = RetroPad B; left stick doubles as d-pad
    assert bits == sum(1 << RETROPAD[b] for b in expect)
    assert analog[0] == -32000


def test_sparse_slots_report_slot_numbers():
    players = [kbd("A"), PlayerSettings(), kbd("A")]
    st = validate_players(players, [0, 2], set())
    assert set(st) == {0, 2}
    assert "Same device as Player 3" in st[0].problems
