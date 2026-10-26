"""Against a real camera (``pytest -m hardware``): nothing moves, so it is safe anywhere."""

from __future__ import annotations

import threading
import time

import numpy as np
import numpy.typing as npt
import pytest
from xpisode_device import DeviceHost, InProcessTransport, MonotonicClock, check

from xpisode_realsense import D405
from xpisode_realsense.d405 import STREAMS

pytestmark = pytest.mark.hardware


@pytest.fixture
def serial() -> str:
    """A connected D405's serial: the driver opens no camera it was not told to."""
    rs = pytest.importorskip("pyrealsense2")
    for device in rs.context().query_devices():
        if device.get_info(rs.camera_info.product_id) == D405.product_id:
            return str(device.get_info(rs.camera_info.serial_number))
    pytest.fail("no D405 connected")


def test_frames_arrive_numbered_and_stamped_by_the_camera(serial: str) -> None:
    camera = D405(serial=serial, legacy_topics=True)
    clock = MonotonicClock()
    camera.open(clock)
    try:
        info = camera.describe()
        depth = info.streams["depth"].spec
        assert depth.fields[0].scale == pytest.approx(1e-4, rel=0.01)
        assert depth.camera is not None
        assert depth.camera.width == 848
        assert [(t.parent, t.child) for t in info.transforms] == [
            ("color", "depth"),
            ("color", "frame"),
        ]
        readings: dict[str, list[tuple[int, int]]] = {"color": [], "depth": []}
        values: dict[str, npt.NDArray[np.generic]] = {}
        deadline = time.monotonic() + 5
        while min(len(r) for r in readings.values()) < 60 and time.monotonic() < deadline:
            for stream, kept in readings.items():
                reading = camera.read(stream)
                if reading is not None:
                    assert reading.sequence is not None
                    kept.append((reading.sequence, reading.time_ns))
                    age_ns = clock.now_ns() - reading.time_ns
                    assert 0 < age_ns < 100_000_000  # exposure middle, then USB and copy
                    values[stream] = np.asarray(reading.values[0])
            time.sleep(0.002)
    finally:
        camera.close()
    assert values["color"].shape == (480, 848, 3)
    assert values["depth"].dtype == np.uint16
    for stream, kept in readings.items():
        numbers = [n for n, _ in kept]
        times = [t for _, t in kept]
        assert len(kept) >= 60, stream
        assert numbers == sorted(numbers)
        assert len(set(numbers)) == len(numbers)  # never the same frame twice
        assert times == sorted(times)
        intervals = np.diff(times) / np.diff(numbers)
        assert np.median(intervals) == pytest.approx(1e9 / 30, rel=0.02), stream


def test_every_stream_at_once_has_its_shape(serial: str) -> None:
    camera = D405(serial=serial, streams=list(STREAMS), width=640, height=480, legacy_topics=True)
    camera.open(MonotonicClock())
    try:
        assert list(camera.describe().streams) == [*STREAMS, "frame"]
        values: dict[str, npt.NDArray[np.generic]] = {}
        deadline = time.monotonic() + 5
        while len(values) < len(STREAMS) and time.monotonic() < deadline:
            for stream in STREAMS:
                reading = camera.read(stream)
                if reading is not None:
                    values[stream] = np.asarray(reading.values[0])
            time.sleep(0.002)
    finally:
        camera.close()
    assert values["color"].shape == (480, 640, 3)
    assert values["depth"].shape == (480, 640)
    assert values["depth"].dtype == np.uint16
    for infrared in ("infrared_1", "infrared_2"):
        assert values[infrared].shape == (480, 640)
        assert values[infrared].dtype == np.uint8


def test_the_camera_passes_the_device_contract_check(serial: str) -> None:
    transport = InProcessTransport()
    host = DeviceHost("camera", D405(serial=serial), transport, MonotonicClock())
    stop = threading.Event()
    running = threading.Thread(target=host.run, args=(stop,))
    running.start()
    try:
        report = check(transport, "camera", shutdown=True, timeout_s=15.0, observe_s=2.0)
    finally:
        stop.set()
        running.join(30)
    assert report.ok, report.problems
