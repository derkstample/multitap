import json
import subprocess

from multitap.cores import discovery
from multitap.cores.probe import probe_argv, probe_env

INFO = '''display_name = "Nintendo - NES / Famicom (FCEUmm)"
systemname = "Nintendo Entertainment System"
supported_extensions = "fds|nes|unf|unif"
firmware_count = 1
firmware0_desc = "disksys.rom (Family Computer Disk System BIOS)"
firmware0_path = "disksys.rom"
firmware0_opt = "true"
'''


def test_parse_info():
    f = discovery.info_to_fields(discovery.parse_info(INFO))
    assert f["display_name"].startswith("Nintendo - NES")
    assert f["extensions"] == ["fds", "nes", "unf", "unif"]
    assert f["firmware"] == [{"path": "disksys.rom", "optional": True,
                              "desc": "disksys.rom (Family Computer Disk System BIOS)"}]
    assert not f["hw_render"]
    assert discovery.info_to_fields({"required_hw_api": "OpenGL Core >= 3.3"})["hw_render"]


def test_core_id():
    assert discovery.core_id_for("/x/genesis_plus_gx_libretro.so") == "genesis_plus_gx"


def test_describe_and_scan_custom_core(xdg, testcore):
    core = discovery.describe(testcore, cache={})
    assert core.display_name == "MultiTap Test Core"
    assert core.extensions == ["tst"] and not core.error
    found = discovery.scan([str(testcore)])
    assert any(c.path == str(testcore) and c.source == "custom" for c in found)


def test_non_core_library_is_reported(xdg, tmp_path):
    bogus = tmp_path / "bogus_libretro.so"
    bogus.write_bytes(b"not an elf")
    core = discovery.describe(bogus, cache={})
    assert core.error


def _trial(core, rom, sysdir):
    out = subprocess.run(probe_argv("trial", str(core), str(rom), str(sysdir)),
                         env=probe_env(), capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_trial_load(testcore, content, tmp_path):
    assert _trial(testcore, content, tmp_path)["ok"]
    bad = tmp_path / "bad.tst"
    bad.write_bytes(b"BAD")
    res = _trial(testcore, bad, tmp_path)
    assert not res["ok"] and "rejected" in res["error"]
