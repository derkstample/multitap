"""The C++ multitap-worker, driven through tests/native_harness.py (mirrors test_worker.py)."""

import json
import struct
import subprocess
import sys
import time
import zipfile

import pytest

from multitap import native
from multitap.cores.probe import probe_argv, probe_env

from native_harness import (STATE_PAUSED, STATE_RUNNING, NativeWorker, stop_all, wait_for)

WORKER = native.find("multitap-worker")
pytestmark = pytest.mark.skipif(WORKER is None, reason="multitap-worker is not built "
                                                       "(pip install -e .)")

BUTTON_B, BUTTON_START = 1 << 0, 1 << 3
FRAME_SAMPLES = 735  # testcore audio frames per video frame


def spawn(slot, core, rom, tmp_path, **kw):
    return NativeWorker.spawn(WORKER, slot, core, rom, tmp_path / "system", tmp_path / f"p{slot}",
                              **kw)


def start_all(workers, delay_s=0.1):
    t0 = time.monotonic_ns() + int(delay_s * 1e9)  # CLOCK_MONOTONIC, as in the worker
    for w in workers:
        w.start(t0)
    return t0


def test_two_workers_input_pause_reset_and_sram(testcore, content, tmp_path):
    workers = [spawn(i, testcore, content, tmp_path) for i in range(2)]
    try:
        assert all(w.wait_loaded() for w in workers), [w.error for w in workers]
        assert workers[0].info["name"] == "MultiTap Test Core"
        assert workers[0].info["geometry"]["max_width"] == 320
        start_all(workers)
        assert wait_for(lambda: all(w.video.frame_no() > 5 for w in workers))
        frame = workers[0].video.read_frame()
        assert (frame.width, frame.height, frame.pitch, frame.fmt) == (320, 240, 1280, 1)
        assert len(frame.pixels) == 240 * 1280

        workers[1].input.set_held(BUTTON_B | BUTTON_START)  # player 2 only
        time.sleep(0.2)

        for w in workers:
            w.send("pause")
        assert wait_for(lambda: all(w.video.state() == STATE_PAUSED for w in workers))
        time.sleep(0.05)  # let a frame already in flight finish
        before = [w.video.frame_no() for w in workers]
        time.sleep(0.3)
        assert [w.video.frame_no() for w in workers] == before

        for w in workers:
            w.send("reset")
            w.send("resume")
        assert wait_for(lambda: all(w.video.state() == STATE_RUNNING for w in workers))
        assert wait_for(lambda: all(w.video.frame_no() > b + 5 for w, b in zip(workers, before)))
    finally:
        stop_all(workers)

    for i, w in enumerate(workers):
        assert w.exited and w.proc.returncode == 0
        assert not w.shm_path("video").exists()
        sram = (tmp_path / f"p{i}" / "game.srm").read_bytes()
        mask, = struct.unpack_from("<H", sram, 1)
        resets, = struct.unpack_from("<I", sram, 8)
        assert sram[0] == 0xAB
        assert resets == 1
        assert mask == ((BUTTON_B | BUTTON_START) if i == 1 else 0)


def test_bad_content_reports_error(testcore, tmp_path):
    bad = tmp_path / "bad.tst"
    bad.write_bytes(b"BAD")
    w = spawn(0, testcore, bad, tmp_path)
    try:
        assert not w.wait_loaded()
        assert "rejected" in w.error
        assert w.proc.wait(3) == 1
        assert not w.shm_path("video").exists()
    finally:
        w.stop()


def test_synchronized_start(testcore, content, tmp_path):
    workers = [spawn(i, testcore, content, tmp_path) for i in range(2)]
    try:
        assert all(w.wait_loaded() for w in workers)
        time.sleep(0.2)
        assert [w.video.frame_no() for w in workers] == [0, 0]  # nothing runs before start
        start_all(workers, delay_s=0.2)
        assert wait_for(lambda: all(w.video.frame_no() > 20 for w in workers))
        for _ in range(30):
            a, b = (w.video.frame_no() for w in workers)
            assert abs(a - b) <= 1, (a, b)
            time.sleep(0.01)
        assert wait_for(lambda: all((w.poll(), w.stats)[1] for w in workers), timeout=3)
        stats = workers[0].stats[-1]
        assert stats["frame_ms"] > 0 and 50 < stats["fps"] < 70
    finally:
        stop_all(workers)


def test_short_tap_is_latched_for_one_frame(testcore, content, tmp_path):
    w = spawn(0, testcore, content, tmp_path, audible=True)
    try:
        assert w.wait_loaded()
        assert w.audio.sample_rate() == 44100
        w.start()
        assert wait_for(lambda: w.video.frame_no() > 5)
        w.audio.skip_all()
        time.sleep(0.1)
        assert not any(w.audio.read())  # testcore is silent with no buttons down

        w.input.tap(BUTTON_B)  # pressed and released between two worker polls
        assert wait_for(lambda: w.input.pressed() == 0)
        time.sleep(0.1)
        samples = w.audio.read()
        loud_frames = sum(1 for i in range(0, len(samples), 2) if samples[i])
        assert loud_frames == FRAME_SAMPLES  # exactly one frame saw the button
    finally:
        w.stop()


# ---- probe parity with multitap/cores/probe.py ------------------------------------------

def last_json(argv, env=None) -> dict:
    res = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
    lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
    return json.loads(lines[-1])


def both_probes(*args):
    return (last_json([str(WORKER), "--probe", *args]),
            last_json(probe_argv(*args), env=probe_env()))


def test_probe_info_parity(testcore, tmp_path):
    native_info, python_info = both_probes("info", str(testcore))
    assert native_info == python_info
    assert native_info["valid_extensions"] == ["tst"]

    not_a_core = tmp_path / "x_libretro.so"
    not_a_core.write_text("nope")
    native_info, python_info = both_probes("info", str(not_a_core))
    assert native_info == python_info
    assert native_info["error"].startswith("Cannot load core:")


def test_probe_trial_parity(testcore, content, tmp_path):
    system = str(tmp_path / "system")
    native_res, python_res = both_probes("trial", str(testcore), str(content), system)
    assert native_res == python_res
    assert native_res["ok"] and native_res["frames"] == 10

    bad = tmp_path / "bad.tst"
    bad.write_bytes(b"BAD")
    native_res, python_res = both_probes("trial", str(testcore), str(bad), system)
    assert native_res == python_res == {"ok": False, "error": "The core rejected this file"}


def test_probe_trial_zip_parity(testcore, tmp_path):
    system = str(tmp_path / "system")
    good = tmp_path / "game.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("readme.txt", "hi")
        zf.writestr("roms/game.tst", b"Z")
    native_res, python_res = both_probes("trial", str(testcore), str(good), system)
    assert native_res == python_res and native_res["ok"]

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "hi")
    native_res, python_res = both_probes("trial", str(testcore), str(empty), system)
    assert native_res == python_res
    assert native_res["error"] == "Archive contains no file this core can load"


def test_native_report_lists_worker():
    res = subprocess.run([sys.executable, "-m", "multitap.native"], capture_output=True,
                         text=True)
    assert res.returncode == 0 and str(WORKER) in res.stdout
