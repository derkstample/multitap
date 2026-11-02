from multitap import settings as sm
from multitap.colors import MAX_PLAYERS


def test_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = sm.Settings(active=[0, 2, 5], audio_rotation_s=45)
    s.players[0].name = "Alice"
    s.players[1].device = {"kind": "keyboard", "set": "B"}
    s.layout = {"cols": 2, "rows": 2, "rects": [[0, 0, 1, 2], [1, 0, 1, 1], [1, 1, 1, 1]]}
    s.add_recent("fceumm", "/games/a.nes")
    sm.save(s, path)
    loaded = sm.load(path)
    assert loaded == s


def test_recents_are_deduplicated_and_capped():
    s = sm.Settings()
    for i in range(15):
        s.add_recent("core", f"/g/{i}.nes")
    s.add_recent("core", "/g/3.nes")
    recent = [r["path"] for r in s.recent_for("core")]
    assert recent[0] == "/g/3.nes"
    assert len(recent) == sm.MAX_RECENTS and len(set(recent)) == len(recent)
    assert s.last_rom["core"] == "/g/3.nes"


def test_corrupt_or_partial_files_fall_back(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert sm.load(path) == sm.Settings()
    path.write_text('{"active": [3, 3, 99, "x", 1], "players": [{"name": "x", "bogus": 1}], "unknown": 3}')
    s = sm.load(path)
    assert s.active == [3, 1]
    assert s.players[0].name == "x" and len(s.players) == MAX_PLAYERS
