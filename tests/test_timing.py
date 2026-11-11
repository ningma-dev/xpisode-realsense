"""Where a frame lands on the host clock, with librealsense played by fakes: no camera needed."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from xpisode_device import MonotonicClock, Reading
from xpisode_format import codecs
from xpisode_format.spec import Encoding

import xpisode_realsense.camera as camera_module
from xpisode_realsense import D405
from xpisode_realsense.camera import COUNTER_WRAP, rs, ticks

WIDTH, HEIGHT, FPS = 64, 48, 30
WALL_STEP_S = -11.136
"""The wall clock step that once stamped frames 11 s into the future."""


def test_camera_milliseconds_are_microsecond_ticks() -> None:
    assert ticks(1000.0) == 1_000_000
    assert ticks(4294967.295) == COUNTER_WRAP - 1


class Camera:
    """A D405 as librealsense shows it: a microsecond clock running 70 ppm fast from near
    its wrap, frames exposed 2 ms before they are handed over, and global time on until a
    sensor turns it off (then frames carry the camera clock, else the wall clock)."""

    def __init__(self, *, global_time_switch: bool = True) -> None:
        self.origin_ns = time.monotonic_ns()
        self.start_us = COUNTER_WRAP - 200_000  # wraps 0.2 s in
        self.global_time = 1.0
        self.switch = global_time_switch
        self.exposures: dict[int, int] = {}
        """Frame number: when it was exposed, on the host's monotonic clock."""
        self.stopped = False
        self.unstamped: set[int] = set()
        """Frame numbers whose metadata is missing: librealsense stamps them with the host's
        time instead, and says so in their domain."""
        self.garbled: set[int] = set()
        """Frame numbers whose hardware stamp is nonsense."""

    def clock_us(self, host_ns: int) -> float:
        return (self.start_us + (host_ns - self.origin_ns) / 1000 * (1 + 70e-6)) % COUNTER_WRAP

    def get_device_time_ms(self) -> float:
        return self.clock_us(time.monotonic_ns()) / 1000

    # rs.device

    def get_info(self, info: Any) -> str:
        return {
            rs.camera_info.product_id: "0B5B",
            rs.camera_info.name: "Intel RealSense D405",
            rs.camera_info.serial_number: "123",
            rs.camera_info.firmware_version: "5.15.1.55",
            rs.camera_info.usb_type_descriptor: "3.2",
        }[info]

    def query_sensors(self) -> list[Sensor]:
        return [Sensor(self)]

    def first_depth_sensor(self) -> Any:
        return SimpleNamespace(get_depth_scale=lambda: 1e-4)


class Sensor:
    def __init__(self, camera: Camera) -> None:
        self.camera = camera

    def supports(self, option: Any) -> bool:
        return option == rs.option.global_time_enabled and self.camera.switch

    def set_option(self, option: Any, value: float) -> None:
        assert option == rs.option.global_time_enabled
        self.camera.global_time = value

    def get_info(self, info: Any) -> str:
        return "Stereo Module"


class Frame:
    def __init__(self, camera: Camera, kind: Any, index: int, number: int, exposed: int) -> None:
        self.camera, self.kind, self.index = camera, kind, index
        self.number, self.exposed = number, exposed
        self.global_time = bool(camera.global_time)

    def get_profile(self) -> Any:
        return SimpleNamespace(
            stream_type=lambda: self.kind,
            stream_index=lambda: self.index,
            stream_name=lambda: str(self.kind),
        )

    def supports_frame_metadata(self, value: Any) -> bool:
        assert value == rs.frame_metadata_value.frame_timestamp
        return self.number not in self.camera.unstamped

    def get_frame_timestamp_domain(self) -> Any:
        domain = rs.timestamp_domain
        if self.number in self.camera.unstamped:
            return domain.system_time
        return domain.global_time if self.global_time else domain.hardware_clock

    def get_timestamp(self) -> float:
        if self.global_time or self.number in self.camera.unstamped:  # on the wall clock
            return time.time() * 1000 - 2
        if self.number in self.camera.garbled:  # half the counter away: 36 minutes off
            return (self.camera.clock_us(self.exposed) + COUNTER_WRAP / 2) % COUNTER_WRAP / 1000
        return self.camera.clock_us(self.exposed) / 1000

    def get_frame_number(self) -> int:
        return self.number

    def get_data(self) -> Any:
        rng = np.random.default_rng(self.number)
        if self.kind == rs.stream.color:
            return rng.integers(0, 256, (HEIGHT, WIDTH, 3), dtype=np.uint8)
        return rng.integers(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)


class Frameset(list[Frame]):
    def as_frameset(self) -> Frameset:
        return self


class StreamProfile:
    def get_extrinsics_to(self, other: StreamProfile) -> Any:
        return SimpleNamespace(translation=[0.0, 0.0, 0.0], rotation=[1, 0, 0, 0, 1, 0, 0, 0, 1])

    def as_video_stream_profile(self) -> StreamProfile:
        return self

    def get_intrinsics(self) -> Any:
        return SimpleNamespace(
            width=WIDTH,
            height=HEIGHT,
            fx=50.0,
            fy=50.0,
            ppx=32.0,
            ppy=24.0,
            model=rs.distortion.brown_conrady,
            coeffs=[0.0] * 5,
        )


class Pipeline:
    def __init__(self, camera: Camera) -> None:
        self.camera = camera
        self.thread: threading.Thread | None = None
        self.streams: list[tuple[Any, int]] = []

    def start(self, config: Any, callback: Callable[[Any], None]) -> Any:
        self.streams = config.streams
        self.thread = threading.Thread(target=self._stream, args=(callback,), daemon=True)
        self.thread.start()
        return SimpleNamespace(get_device=lambda: self.camera, get_stream=self.get_stream)

    def get_stream(self, kind: Any, index: int) -> StreamProfile:
        return StreamProfile()

    def _stream(self, callback: Callable[[Any], None]) -> None:
        number = 0
        while not self.camera.stopped:
            time.sleep(1 / FPS)
            number += 1
            exposed = time.monotonic_ns() - 2_000_000
            self.camera.exposures[number] = exposed
            callback(Frameset(Frame(self.camera, k, i, number, exposed) for k, i in self.streams))

    def stop(self) -> None:
        self.camera.stopped = True
        assert self.thread is not None
        self.thread.join()


class Config:
    def __init__(self) -> None:
        self.streams: list[tuple[Any, int]] = []

    def enable_device(self, serial: str) -> None:
        assert serial == "123"

    def enable_stream(self, kind: Any, index: int, *_: Any) -> None:
        self.streams.append((kind, index))


def ignore(callback: Callable[[Any], None]) -> None:
    pass


def librealsense(monkeypatch: pytest.MonkeyPatch, camera: Camera) -> Pipeline:
    pipeline = Pipeline(camera)
    fake = SimpleNamespace(
        config=Config,
        pipeline=lambda: pipeline,
        context=lambda: SimpleNamespace(set_devices_changed_callback=ignore),
        camera_info=rs.camera_info,
        option=rs.option,
        timestamp_domain=rs.timestamp_domain,
        frame_metadata_value=rs.frame_metadata_value,
        __version__=rs.__version__,
    )
    monkeypatch.setattr(camera_module, "rs", fake)
    return pipeline


def d405() -> D405:
    return D405(
        serial="123", width=WIDTH, height=HEIGHT, fps=FPS, frame_fields=["color", "infrared_left"]
    )


def frames_for(device: D405, seconds: float) -> list[Reading]:
    readings: list[Reading] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        reading = device.read("frame")
        if reading is not None:
            readings.append(reading)
        time.sleep(0.005)
    return readings


def test_frames_keep_their_stamps_when_the_wall_clock_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident: the wall clock stepped 11 s back while three cameras streamed."""
    camera = Camera()
    pipeline = librealsense(monkeypatch, camera)
    device = d405()
    host = MonotonicClock()
    device.open(host)
    try:
        before = frames_for(device, 0.4)
        wall = time.time
        monkeypatch.setattr(time, "time", lambda: wall() + WALL_STEP_S)
        monkeypatch.setattr(time, "time_ns", lambda: round((wall() + WALL_STEP_S) * 1e9))
        after = frames_for(device, 0.4)
        now = host.now_ns()
    finally:
        device.close()
    assert camera.global_time == 0
    assert device._time is not None  # pyright: ignore[reportPrivateUsage]
    assert device._time.wraps == 1  # pyright: ignore[reportPrivateUsage]
    readings = before + after
    assert len(before) >= 5
    assert len(after) >= 5
    times = [r.time_ns for r in readings]
    assert times == sorted(times)
    assert max(times) < now
    for reading in readings:
        assert reading.sequence is not None
        assert reading.time_ns == pytest.approx(camera.exposures[reading.sequence], abs=500_000)
    assert pipeline.thread is not None
    assert not pipeline.thread.is_alive()


def test_the_wall_clock_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = Camera()
    librealsense(monkeypatch, camera)
    device = d405()

    def wall(*_: object) -> float:
        raise AssertionError("the wall clock was read")

    monkeypatch.setattr(time, "time", wall)
    monkeypatch.setattr(time, "time_ns", wall)
    device.open(MonotonicClock())
    try:
        readings = frames_for(device, 0.3)
    finally:
        device.close()
    assert readings


def test_open_starts_the_clock_keeper_and_close_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = Camera()
    librealsense(monkeypatch, camera)
    device = d405()
    device.open(MonotonicClock())
    try:
        assert any(t.name == "device-clock" and t.is_alive() for t in threading.enumerate())
        info = device.describe()
        facts = info.device["time"]
        assert isinstance(facts, dict)
        assert facts["unit_ns"] == 1000
        assert facts["wrap_ticks"] == COUNTER_WRAP
        assert isinstance(facts["accepted"], int)
        assert facts["accepted"] >= 8
        assert facts["broken"] is None
        frame = info.streams["frame"].spec
        infrared = next(f for f in frame.fields if f.name == "infrared_left")
        assert infrared.encoding is Encoding.FFV1
        assert infrared.shape == (HEIGHT, WIDTH)
        assert info.streams["frame"].spec.description
        # A station may require the frame stream, and judges a required stream by its rate.
        assert frame.nominal_hz == FPS
        reading = frames_for(device, 0.2)[-1]
        index = [f.name for f in frame.fields].index("infrared_left")
        image = np.asarray(reading.values[index])
        assert np.array_equal(codecs.decode(infrared, codecs.encode(infrared, image)), image)
    finally:
        device.close()
    device.close()  # again: nothing left to stop
    time.sleep(0.05)
    assert not any(t.name == "device-clock" and t.is_alive() for t in threading.enumerate())


def test_a_camera_whose_global_time_stays_on_fails_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = Camera(global_time_switch=False)
    pipeline = librealsense(monkeypatch, camera)
    device = d405()
    with pytest.raises(RuntimeError, match="not the camera's hardware clock"):
        device.open(MonotonicClock())
    assert camera.stopped
    assert pipeline.thread is not None
    assert not pipeline.thread.is_alive()
    time.sleep(0.05)
    assert not any(t.name == "device-clock" and t.is_alive() for t in threading.enumerate())


def test_a_frame_without_its_hardware_stamp_is_dropped_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame that lost its metadata carries the host's wall time; one with a stamp far off
    the mapping, once, is not a reset. Both are dropped; the camera goes on."""
    camera = Camera()
    librealsense(monkeypatch, camera)
    device = d405()
    device.open(MonotonicClock())
    try:
        first = frames_for(device, 0.3)
        assert first
        assert first[-1].sequence is not None
        start = first[-1].sequence + 3
        camera.unstamped |= {start, start + 1, start + 2}
        camera.garbled |= {start + 6}
        after = frames_for(device, 0.6)
        diagnostics = device.diagnostics()
    finally:
        device.close()
    numbers = {r.sequence for r in after}
    assert not numbers & {start, start + 1, start + 2, start + 6}
    assert max(n for n in numbers if n is not None) > start + 6  # it went on
    assert device.unstamped == 3
    assert device.outliers == 1
    assert diagnostics is not None
    assert (diagnostics["unstamped"], diagnostics["outliers"]) == (3, 1)
    assert diagnostics["no_metadata"] == 3
    time_facts = diagnostics["time"]
    assert isinstance(time_facts, dict)
    assert (time_facts["outliers"], time_facts["resets"], time_facts["broken"]) == (1, 0, None)
    keeper = diagnostics["keeper"]
    assert isinstance(keeper, dict)
    assert keeper["failures"] == 0
    times = [r.time_ns for r in first + after]
    assert times == sorted(times)
