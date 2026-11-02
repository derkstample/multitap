import struct
import time

from multitap.worker.host import spawn_worker, stop_workers
from multitap.worker.shm import STATE_PAUSED, STATE_RUNNING


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_two_workers_input_pause_reset_and_sram(testcore, content, tmp_path):
    handles = [spawn_worker(i, {
        "core_path": str(testcore), "rom_path": str(content),
        "system_dir": str(tmp_path / "system"), "save_dir": str(tmp_path / f"p{i}"),
        "no_audio": True,
    }, tag=f"test{i}") for i in range(2)]
    try:
        assert wait_for(lambda: all((h.poll(), h.loaded)[1] for h in handles))
        assert wait_for(lambda: all(h.video.header()[0] > 5 for h in handles))
        seq, w, h_, pitch, fmt, pixels = handles[0].video.read_frame()
        assert (w, h_, pitch, fmt) == (320, 240, 1280, 1) and len(pixels) == 240 * 1280

        handles[1].input.write(0b1001)  # B + Start for player 2 only
        time.sleep(0.2)

        for h in handles:
            h.send("pause")
        assert wait_for(lambda: all(h.video.header()[2] == STATE_PAUSED for h in handles))
        before = [h.video.header()[0] for h in handles]
        time.sleep(0.3)
        assert [h.video.header()[0] for h in handles] == before

        for h in handles:
            h.send("reset")
            h.send("resume")
        assert wait_for(lambda: all(h.video.header()[2] == STATE_RUNNING for h in handles))
        time.sleep(0.2)
    finally:
        stop_workers(handles)

    for i in range(2):
        sram = (tmp_path / f"p{i}" / "game.srm").read_bytes()
        mask, = struct.unpack_from("<H", sram, 1)
        resets, = struct.unpack_from("<I", sram, 8)
        assert sram[0] == 0xAB
        assert resets == 1
        assert mask == (0b1001 if i == 1 else 0)


def test_bad_content_reports_error(testcore, tmp_path):
    bad = tmp_path / "bad.tst"
    bad.write_bytes(b"BAD")
    h = spawn_worker(0, {"core_path": str(testcore), "rom_path": str(bad),
                         "system_dir": str(tmp_path), "save_dir": str(tmp_path / "s"),
                         "no_audio": True}, tag="testbad")
    try:
        assert wait_for(lambda: (h.poll(), h.error)[1])
        assert "rejected" in h.error and not h.loaded
    finally:
        stop_workers([h])
