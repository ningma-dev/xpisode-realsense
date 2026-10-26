"""librealsense's pipeline as an xpisode device peer, for any model; a model's module (one
per model, such as ``d405``) says which camera it is and what it offers.

Everything camera-specific stays in librealsense: the pipeline opens the device and streams
what is asked; its global time maps the camera clock to the host's; its frame metadata says
when each exposure happened; it says when a camera is unplugged. This module only states what
the camera offers in the xpisode device protocol, so a station records it without knowing it
is a RealSense.

The configuration chooses the streams, from the model's ``offers``, all at one width, height,
and frame rate. Each is numbered by the camera's own frame counter, so a frame the camera or
the USB bus dropped shows as missing in the Episode, and each frame is stamped at the middle
of its exposure, on the host's monotonic clock.
"""

from __future__ import annotations

import importlib
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
from xpisode_device import Clock, Device, DeviceInfo, Reading, Stream
from xpisode_format import codecs
from xpisode_format.model import JsonValue
from xpisode_format.spec import (
    Axis,
    Camera,
    Counter,
    DType,
    Encoding,
    Field,
    RecordStream,
    Role,
    TimeMeaning,
    Transform,
)

from xpisode_realsense.frame import Frame

rs: Any = importlib.import_module("pyrealsense2")


@dataclass(frozen=True)
class Kind:
    """A stream librealsense delivers, and the field it is recorded as."""

    stream: Any
    """``rs.stream``."""
    index: int
    format: Any
    """``rs.format``."""
    dtype: DType
    channels: tuple[str, ...]
    """Labels of the last axis; empty for a single-channel image."""
    unit: str
    encoding: Encoding
    description: str


POLLS_PER_FRAME = 4
"""How often the host asks for a new frame, per frame interval."""
WARMUP_FRAMES = 5
"""Frames ``open`` waits for: the camera is streaming and its metadata is there."""
FIRST_FRAME_TIMEOUT_MS = 5000


def mid_exposure_ns(
    global_ms: float, frame_timestamp_us: int, sensor_timestamp_us: int, wall_minus_clock_ns: int
) -> int:
    """The middle of a frame's exposure on the host clock.

    librealsense's global time is the camera clock mapped to the host's wall clock, at the
    frame timestamp; the frame metadata's sensor timestamp is the middle of the exposure,
    on the camera clock. ``wall_minus_clock_ns`` turns wall time into host clock time.
    """
    exposure_half_ns = (frame_timestamp_us - sensor_timestamp_us) * 1000
    return round(global_ms * 1e6) - exposure_half_ns - wall_minus_clock_ns


PLUMB_BOB_TOLERANCE_PX = 0.05
"""How far, at most, OpenCV's model may put a pixel from where librealsense's
inverse_brown_conrady does, for the coefficients to be declared as OpenCV's. Finding a corner
in an image is good to about 0.1 pixel at best; 0.05 more adds at most 12% to that, and only
at the image's edges. Three D405s' color at 640x480: 0.009 to 0.020."""


def _project(model: str, coeffs: Sequence[float], x: Any, y: Any) -> tuple[Any, Any]:
    """Normalized image points distorted as ``rs2_project_point_to_pixel`` does in
    librealsense 2.59.1 (src/rs.cpp): brown_conrady is OpenCV's model; inverse_brown_conrady
    scales by the radial terms first and takes the tangential ones from the scaled point."""
    k1, k2, p1, p2, k3 = coeffs
    r2 = x * x + y * y
    f = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    tx, ty = (x * f, y * f) if model == "inverse_brown_conrady" else (x, y)
    return (
        x * f + 2 * p1 * tx * ty + p2 * (r2 + 2 * tx * tx),
        y * f + 2 * p2 * tx * ty + p1 * (r2 + 2 * ty * ty),
    )


def plumb_bob_error_px(intrinsics: Any) -> float:
    """The farthest OpenCV's model puts a pixel of the image from where librealsense's
    inverse_brown_conrady does, with the same coefficients."""
    columns = np.arange(int(intrinsics.width), dtype=np.float64)
    rows = np.arange(int(intrinsics.height), dtype=np.float64)
    x, y = np.meshgrid(
        (columns - float(intrinsics.ppx)) / float(intrinsics.fx),
        (rows - float(intrinsics.ppy)) / float(intrinsics.fy),
    )
    coeffs = [float(c) for c in intrinsics.coeffs]
    ix, iy = _project("inverse_brown_conrady", coeffs, x, y)
    ox, oy = _project("brown_conrady", coeffs, x, y)
    return float(np.max(np.hypot((ix - ox) * intrinsics.fx, (iy - oy) * intrinsics.fy)))


def camera_of(intrinsics: Any) -> Camera:
    """librealsense's intrinsics of a video stream as the format's.

    librealsense's brown_conrady is OpenCV's model, its coefficients in the same order
    (k1, k2, p1, p2, k3): ``rs2_project_point_to_pixel`` in librealsense 2.59.1. Its
    inverse_brown_conrady, the D405's color model, differs only in where it takes the
    tangential terms from: with a D405's coefficients, by 0.02 pixel at most in a 640x480
    image. Within ``PLUMB_BOB_TOLERANCE_PX`` it is declared as OpenCV's; its other models,
    and it farther off, keep librealsense's names.
    """
    model = str(intrinsics.model).removeprefix("distortion.")
    coefficients = tuple(float(c) for c in intrinsics.coeffs)
    if model == "none":
        distortion_model, coefficients = "none", ()
    elif model == "brown_conrady" or (
        model == "inverse_brown_conrady" and plumb_bob_error_px(intrinsics) < PLUMB_BOB_TOLERANCE_PX
    ):
        distortion_model = "plumb_bob"
    else:
        distortion_model = f"realsense.{model}"
    return Camera(
        width=intrinsics.width,
        height=intrinsics.height,
        fx=float(intrinsics.fx),
        fy=float(intrinsics.fy),
        cx=float(intrinsics.ppx),
        cy=float(intrinsics.ppy),
        distortion_model=distortion_model,
        distortion=coefficients,
    )


def quaternion(column_major: Sequence[float]) -> tuple[float, float, float, float]:
    """A rotation matrix, column-major as librealsense's extrinsics hold it, as x, y, z, w."""
    m = [[float(column_major[c * 3 + r]) for c in range(3)] for r in range(3)]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 2 * math.sqrt(1 + trace)
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, s / 4)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = 2 * math.sqrt(1 + m[0][0] - m[1][1] - m[2][2])
        q = (s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = 2 * math.sqrt(1 + m[1][1] - m[0][0] - m[2][2])
        q = ((m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = 2 * math.sqrt(1 + m[2][2] - m[0][0] - m[1][1])
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4, (m[1][0] - m[0][1]) / s)
    return q


def transform_of(extrinsics: Any, parent: str, child: str, source: str) -> Transform:
    """librealsense's extrinsics from ``child`` to ``parent`` (``get_extrinsics_to``), which
    map points in the child stream to the parent's, as the format's transform."""
    x, y, z = (float(t) for t in extrinsics.translation)
    return Transform(
        parent=parent,
        child=child,
        translation=(x, y, z),
        rotation=quaternion(extrinsics.rotation),
        source=source,
    )


class Newest:
    """The newest frame of each stream that the host has not read yet; librealsense offers on
    its thread, the host takes on its own.

    A newer frame replaces an unread one, which then shows as missing by its frame number.
    A frame number already offered is ignored: librealsense's syncer puts the last frame of
    a stream that missed one into the next frameset again (seen on a D405, librealsense
    2.59.1), and the host must never get a frame twice. ``make`` runs only for new frames.
    """

    def __init__(self) -> None:
        self._unread: dict[str, Reading] = {}
        self._last: dict[str, int] = {}
        self._lock = threading.Lock()

    def offer(self, stream: str, number: int, make: Callable[[], Reading]) -> None:
        with self._lock:
            if number <= self._last.get(stream, -1):
                return
            self._last[stream] = number
            self._unread[stream] = make()

    def take(self, stream: str) -> Reading | None:
        with self._lock:
            return self._unread.pop(stream, None)


class RealSense(Device):
    """One RealSense camera: the chosen streams at one resolution and frame rate. A model
    subclass sets the class attributes and the defaults, and adds to ``identity``."""

    driver: ClassVar[str]
    product_id: ClassVar[str]
    """librealsense's ``camera_info.product_id``: opening another model fails."""
    offers: ClassVar[dict[str, Kind]]
    """The streams a configuration may name."""

    def __init__(
        self,
        *,
        serial: str,
        streams: Sequence[str],
        width: int,
        height: int,
        fps: int,
        depth_backend: dict[str, Any] | None = None,
        frame_fields: Sequence[str] | None = None,
        legacy_topics: bool = False,
    ) -> None:
        if not streams or any(name not in self.offers for name in streams):
            raise ValueError(f"streams {list(streams)}: choose from {list(self.offers)}")
        default_depth = (
            "depth_ffs"
            if depth_backend and depth_backend.get("factory") != "hardware"
            else "depth_hardware"
        )
        fields = tuple(frame_fields) if frame_fields is not None else ("color", default_depth)
        allowed = {"color", "depth_hardware", "depth_ffs", "infrared_left", "infrared_right"}
        if (
            not fields
            or "color" not in fields
            or len(set(fields)) != len(fields)
            or set(fields) - allowed
        ):
            raise ValueError(
                f"frame_fields must contain color and unique fields from {sorted(allowed)}"
            )
        if "depth_ffs" in fields and (
            not depth_backend
            or not isinstance(depth_backend.get("factory"), str)
            or ":" not in depth_backend["factory"]
        ):
            raise ValueError("depth_ffs requires an inference depth_backend factory")
        self.frame_fields = fields
        self.legacy_topics = legacy_topics
        self.depth_backend = depth_backend or {"factory": "hardware"}
        self._frame: Frame | None = None
        required = ["color"]
        if "depth_hardware" in fields:
            required.append("depth")
        for field, source in (("infrared_left", "infrared_1"), ("infrared_right", "infrared_2")):
            if field in fields or "depth_ffs" in fields:
                required.append(source)
        streams = tuple(dict.fromkeys((*streams, *required))) if legacy_topics else tuple(required)
        self.serial = serial
        self.streams = tuple(dict.fromkeys(streams))
        self.width, self.height, self.fps = width, height, fps
        self._names = {(self.offers[n].stream, self.offers[n].index): n for n in self.streams}
        self._clock: Clock | None = None
        self._pipeline: Any = None
        self._info: DeviceInfo | None = None
        self._newest = Newest()
        self._context: Any = None
        self._removed = threading.Event()
        self._warm = threading.Event()
        self._seen = 0
        self._first: list[Any] = []
        self._failure: Exception | None = None
        self._device: Any = None
        self._removal = ""

    def open(self, clock: Clock) -> None:
        config = rs.config()
        config.enable_device(self.serial)
        for name in self.streams:
            kind = self.offers[name]
            config.enable_stream(
                kind.stream, kind.index, self.width, self.height, kind.format, self.fps
            )
        self._clock = clock
        pipeline = rs.pipeline()
        # librealsense hands over every frameset on its own thread. Polling for the newest
        # one from the host's thread, between encoding frames, lost a third of them with
        # color and depth from three D405s.
        profile = pipeline.start(config, self._on_frames)
        try:
            self._check_model(profile.get_device())
            if not self._warm.wait(FIRST_FRAME_TIMEOUT_MS / 1000):
                raise RuntimeError(f"no frames within {FIRST_FRAME_TIMEOUT_MS} ms")
            for frame in self._first:
                self._check_timing(frame)
            self._first = []
            self._info = self._describe(profile)
            if self.depth_backend:
                self._frame = Frame(
                    self._info,
                    self.depth_backend,
                    clock,
                    self._newest.offer,
                    self.fps,
                    self.frame_fields,
                    self.legacy_topics,
                )
                self._info = self._frame.info
                deadline = time.monotonic() + 30
                while not self._frame.ready.wait(0.1):
                    if self._failure is not None:
                        raise self._failure
                    if time.monotonic() > deadline:
                        raise TimeoutError("No synchronized frame within 30 seconds")
                self._frame.check()
        except BaseException:
            if self._frame is not None:
                self._frame.close()
            pipeline.stop()
            raise
        self._pipeline = pipeline
        # librealsense says when a camera goes; polling for frames alone only goes quiet.
        device = profile.get_device()
        serial = device.get_info(rs.camera_info.serial_number)
        self._device = device
        self._context = rs.context()
        self._context.set_devices_changed_callback(self._devices_changed)
        self._removal = f"librealsense reports camera {serial} removed"

    def _on_frames(self, frame: Any) -> None:
        """librealsense's callback, on its own thread: each frameset as it arrives."""
        try:
            frames = frame.as_frameset()
            if not self._warm.is_set():
                self._seen += 1
                if self._seen >= WARMUP_FRAMES:
                    self._first = [f for _, f in self._frames(frames)]
                    self._warm.set()
                return
            if self._info is None:  # open is still checking
                return
            readings = {name: self._reading(f) for name, f in self._frames(frames)}
            if self.legacy_topics:
                for name, reading in readings.items():
                    assert reading.sequence is not None
                    self._newest.offer(name, reading.sequence, lambda reading=reading: reading)
            if self._frame is not None:
                self._frame.offer(readings)
        except Exception as error:  # noqa: BLE001 - raised to the host by the next read
            self._failure = error

    def _devices_changed(self, info: Any) -> None:
        """librealsense's callback, on its own thread: mark this camera removed."""
        if info.was_removed(self._device):
            self._removed.set()

    def _check_model(self, device: Any) -> None:
        product = device.get_info(rs.camera_info.product_id)
        if product != self.product_id:
            name = device.get_info(rs.camera_info.name)
            serial = device.get_info(rs.camera_info.serial_number)
            raise RuntimeError(
                f"camera {serial} is a {name} (product id {product}); "
                f"{type(self).__name__} drives product id {self.product_id}"
            )

    def identity(self, device: Any) -> dict[str, JsonValue]:
        """What the model records of the camera beyond what every model has."""
        return {}

    def _frames(self, frames: Any) -> Iterator[tuple[str, Any]]:
        """The frames of a frameset that belong to the chosen streams, with their names."""
        for frame in frames:
            profile = frame.get_profile()
            name = self._names.get((profile.stream_type(), profile.stream_index()))
            if name is not None:
                yield name, frame

    def _check_timing(self, frame: Any) -> None:
        domain = frame.get_frame_timestamp_domain()
        if domain != rs.timestamp_domain.global_time:
            raise RuntimeError(
                f"{frame.get_profile().stream_name()} frames are in {domain}, "
                "not global time: the host cannot place them on its clock"
            )
        metadata = rs.frame_metadata_value
        for key in (metadata.frame_timestamp, metadata.sensor_timestamp):
            if not frame.supports_frame_metadata(key):
                raise RuntimeError(
                    f"frames lack the {key} metadata needed to find the exposure "
                    "(librealsense backend or kernel without frame metadata)"
                )

    def _describe(self, profile: Any) -> DeviceInfo:
        device = profile.get_device()
        depth_scale = float(device.first_depth_sensor().get_depth_scale())
        profiles = {
            n: profile.get_stream(self.offers[n].stream, self.offers[n].index) for n in self.streams
        }
        # Every other stream relative to the first, as the camera's factory calibration has it.
        first = self.streams[0]
        source = f"factory calibration, librealsense {rs.__version__}"
        transforms = tuple(
            transform_of(profiles[name].get_extrinsics_to(profiles[first]), first, name, source)
            for name in self.streams[1:]
        )
        info: dict[str, JsonValue] = {
            "driver": self.driver,
            "name": device.get_info(rs.camera_info.name),
            "serial": device.get_info(rs.camera_info.serial_number),
            "firmware": device.get_info(rs.camera_info.firmware_version),
            "librealsense": rs.__version__,
            "streams": list(self.streams),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            **self.identity(device),
        }
        streams = {
            name: Stream(
                RecordStream(
                    role=Role.OBSERVATION,
                    time_meaning=TimeMeaning.ACQUISITION,
                    fields=(self._field(name, depth_scale),),
                    counter=Counter.DEVICE,
                    nominal_hz=float(self.fps),
                    description="stamped at the middle of the exposure",
                    camera=camera_of(profiles[name].as_video_stream_profile().get_intrinsics()),
                ),
                poll_hz=self.fps * POLLS_PER_FRAME,
            )
            for name in self.streams
        }
        return DeviceInfo(device=info, streams=streams, transforms=transforms)

    def _field(self, name: str, depth_scale: float) -> Field:
        kind = self.offers[name]
        pixels = (Axis(name="y"), Axis(name="x"))
        if kind.channels:
            shape: tuple[int, ...] = (self.height, self.width, len(kind.channels))
            axes = (*pixels, Axis(name="channel", labels=kind.channels))
        else:
            shape, axes = (self.height, self.width), pixels
        return Field(
            name=name,
            dtype=kind.dtype,
            shape=shape,
            unit=kind.unit,
            scale=depth_scale if name == "depth" else 1.0,
            encoding=kind.encoding,
            codec_config=(
                codecs.ffv1_config(self.height, self.width)
                if kind.encoding is Encoding.FFV1
                else None
            ),
            axes=axes,
            description=kind.description,
        )

    def describe(self) -> DeviceInfo:
        assert self._info is not None, "describe after open"
        return self._info

    def read(self, stream: str) -> Reading | None:
        if self._pipeline is None:  # the host may ask while open is still running
            return None
        if self._removed.is_set():
            raise RuntimeError(self._removal)
        if self._failure is not None:
            raise self._failure
        if self._frame is not None:
            self._frame.check()
        return self._newest.take(stream)

    def _reading(self, frame: Any) -> Reading:
        assert self._clock is not None
        wall_minus_clock_ns = time.time_ns() - self._clock.now_ns()
        time_ns = mid_exposure_ns(
            frame.get_timestamp(),
            frame.get_frame_metadata(rs.frame_metadata_value.frame_timestamp),
            frame.get_frame_metadata(rs.frame_metadata_value.sensor_timestamp),
            wall_minus_clock_ns,
        )
        # A copy: librealsense reuses the frame's memory once the frame is released.
        value = np.asanyarray(frame.get_data()).copy()
        return Reading(time_ns, (value,), sequence=frame.get_frame_number())

    def close(self) -> None:
        rgbd, self._frame = self._frame, None
        try:
            if rgbd is not None:
                rgbd.close()
        finally:
            self._context = None
            if self._pipeline is not None:
                self._pipeline.stop()
                self._pipeline = None
