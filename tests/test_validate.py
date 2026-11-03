import zipfile

from multitap.roms.validate import GB_LOGO, quick_check


def nes_rom():
    return b"NES\x1a\x01\x01" + bytes(10) + bytes(16384 + 8192)


def test_wrong_extension(tmp_path):
    f = tmp_path / "game.txt"
    f.write_bytes(b"hi")
    check = quick_check(f, ["nes"])
    assert not check.ok and "aren't supported" in check.errors[0]


def test_text_renamed_to_nes_is_rejected(tmp_path):
    f = tmp_path / "fake.nes"
    f.write_text("definitely not a rom" * 10)
    assert not quick_check(f, ["nes"]).ok


def test_valid_nes(tmp_path):
    f = tmp_path / "ok.nes"
    f.write_bytes(nes_rom())
    assert quick_check(f, ["nes"]).ok


def test_gb_and_gba_headers(tmp_path):
    gb = bytearray(0x8000)
    gb[0x104:0x134] = GB_LOGO
    (tmp_path / "a.gb").write_bytes(bytes(gb))
    assert quick_check(tmp_path / "a.gb", ["gb"]).ok
    (tmp_path / "b.gb").write_bytes(bytes(0x8000 - 1) + b"\x01")
    assert not quick_check(tmp_path / "b.gb", ["gb"]).ok
    gba = bytearray(0x200)
    gba[0xB2] = 0x96
    (tmp_path / "c.gba").write_bytes(bytes(gba))
    assert quick_check(tmp_path / "c.gba", ["gba"]).ok


def test_zip_contents_are_checked(tmp_path):
    z = tmp_path / "game.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "x")
        zf.writestr("game.nes", nes_rom())
    assert quick_check(z, ["nes"]).ok

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "x")
    assert not quick_check(empty, ["nes"]).ok


def test_missing_firmware_is_a_warning(tmp_path):
    f = tmp_path / "ok.nes"
    f.write_bytes(nes_rom())
    fw = [{"path": "disksys.rom", "desc": "FDS BIOS", "optional": False}]
    check = quick_check(f, ["nes"], firmware=fw, system_dir=tmp_path)
    assert check.ok and "Required firmware missing" in check.warnings[0]
