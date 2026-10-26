"""Synchronized frame assembly; backend plugins never own camera acquisition.

A backend returns metric Z plus its calibration. Output depth uses color pixel
coordinates; infrared retains native calibration.
Inputs come from one SDK frameset and pass an exposure-time tolerance. One bounded
pending frameset prevents latency from growing when inference is slower than capture.
"""

from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
from xpisode_device import Clock, DeviceInfo, Reading, Stream
from xpisode_format.model import JsonObject
from xpisode_format.spec import (
    Camera,
    Counter,
    Encoding,
    Field,
    RecordStream,
    Role,
    TimeMeaning,
    Transform,
)

Array = npt.NDArray[np.generic]
Matrix = npt.NDArray[np.float64]


def matrix_of(transforms: Sequence[Transform], source: str, target: str) -> Matrix:
    graph: dict[str, list[tuple[str, Matrix]]] = {}
    for t in transforms:
        x, y, z, w = t.rotation
        matrix = np.eye(4)
        matrix[:3, :3] = [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
        matrix[:3, 3] = t.translation
        graph.setdefault(t.child, []).append((t.parent, matrix))
        graph.setdefault(t.parent, []).append((t.child, np.linalg.inv(matrix).astype(np.float64)))
    pending = [(source, np.eye(4))]
    visited: set[str] = set()
    while pending:
        node, matrix = pending.pop()
        if node == target:
            return matrix
        if node in visited:
            continue
        visited.add(node)
        pending.extend((name, edge @ matrix) for name, edge in graph.get(node, []))
    raise ValueError(f"No transform from {source} to {target}")


class DepthBackend(Protocol):
    camera: Camera
    color_from_depth: Matrix
    metadata: JsonObject

    def submit(self, left: Array, right: Array) -> Callable[[], tuple[Array, float]]:
        """Submit one stereo pair; return a callable waiting for (metric Z, milliseconds)."""
        ...

    def close(self) -> None: ...


class Projector:
    """Forward project optical Z with nearest-surface z-buffer; holes stay invalid."""

    def __init__(self, source: Camera, target: Camera, transform: Matrix) -> None:
        import cv2

        self.target = target
        for camera in (source, target):
            if camera.distortion_model not in ("none", "plumb_bob", "rational_polynomial"):
                raise ValueError(f"Unsupported frame distortion {camera.distortion_model}")
        y, x = np.indices((source.height, source.width), dtype=np.float32)
        k = np.array([[source.fx, 0, source.cx], [0, source.fy, source.cy], [0, 0, 1.0]])
        xy = cv2.undistortPoints(
            np.stack((x, y), axis=-1).reshape(-1, 1, 2),
            k,
            np.array(source.distortion or (0.0,) * 5),
        ).reshape(-1, 2)
        rays = np.column_stack((xy, np.ones(len(xy), np.float32)))
        self.rays = (rays @ transform[:3, :3].T).astype(np.float32)
        self.offset = transform[:3, 3].astype(np.float32)

    def __call__(self, depth: Array) -> npt.NDArray[np.float32]:
        c = self.target
        z0 = np.asarray(depth, dtype=np.float32).reshape(-1)
        points = self.rays * z0[:, None] + self.offset
        z = points[:, 2]
        valid = np.isfinite(z0) & (z0 > 0) & (z > 0)
        x = points[:, 0] / np.where(valid, z, 1)
        y = points[:, 1] / np.where(valid, z, 1)
        d = list(c.distortion) + [0.0] * 8
        k1, k2, p1, p2, k3, k4, k5, k6 = d[:8]
        r = x * x + y * y
        radial = (1 + k1 * r + k2 * r * r + k3 * r * r * r) / (
            1 + k4 * r + k5 * r * r + k6 * r * r * r
        )
        u = np.rint((x * radial + 2 * p1 * x * y + p2 * (r + 2 * x * x)) * c.fx + c.cx)
        v = np.rint((y * radial + p1 * (r + 2 * y * y) + 2 * p2 * x * y) * c.fy + c.cy)
        valid &= (
            np.isfinite(u) & np.isfinite(v) & (u >= 0) & (u < c.width) & (v >= 0) & (v < c.height)
        )
        index = v[valid].astype(np.int64) * c.width + u[valid].astype(np.int64)
        out = np.full(c.height * c.width, np.inf, np.float32)
        np.minimum.at(out, index, z[valid])
        out[~np.isfinite(out)] = 0
        return out.reshape(c.height, c.width)


class Frame:
    def __init__(
        self,
        info: DeviceInfo,
        config: dict[str, Any],
        clock: Clock,
        publish: Callable[[str, int, Callable[[], Reading]], None],
        fps: int,
        fields: Sequence[str],
        legacy_topics: bool = False,
    ) -> None:
        self.clock, self.publish = clock, publish
        self.stop = threading.Event()
        self.condition = threading.Condition()
        self.pending: list[Reading] | None = None
        self.failure: Exception | None = None
        self.ready = threading.Event()
        self.last_result = time.monotonic()
        self.last_sequence = -1
        self.last_counters: dict[str, int] = {}
        self.discarded = 0
        self.rejected = 0
        self.tolerance_ns = int(config.get("tolerance_ms", 5) * 1e6)
        factory = str(config.get("factory", "hardware"))
        self.outputs = tuple(fields)
        self.backend: DepthBackend | None = None
        color = info.streams["color"].spec.camera
        if color is None:
            raise ValueError("color capture must declare camera calibration")
        if "depth_hardware" in fields:
            raw = info.streams["depth"].spec
            if raw.camera is None:
                raise ValueError("depth capture must declare camera calibration")
            self.raw_scale = raw.fields[0].scale
            self.raw_project = Projector(
                raw.camera, color, matrix_of(info.transforms, "depth", "color")
            )
        if "depth_ffs" in fields:
            module, name = factory.split(":")
            backend: DepthBackend = getattr(importlib.import_module(module), name)(
                info, **config.get("options", {})
            )
            self.backend = backend
            self.project = Projector(backend.camera, color, backend.color_from_depth)
        inputs = ["color"]
        if "depth_hardware" in fields:
            inputs.append("depth")
        for output, source in (("infrared_left", "infrared_1"), ("infrared_right", "infrared_2")):
            if output in fields or self.backend:
                inputs.append(source)
        self.names = tuple(inputs)
        self.input_index = {n: i for i, n in enumerate(self.names)}
        descriptions: list[Field] = []
        for name in fields:
            if name in ("depth_hardware", "depth_ffs"):
                descriptions.append(
                    Field(
                        name=name,
                        dtype="float32",
                        shape=(color.height, color.width),
                        unit="m",
                        encoding=Encoding.ZSTD,
                        camera=color,
                        optical_frame="color",
                        source="hardware" if name == "depth_hardware" else factory,
                        description="Optical Z aligned to color; metres; 0 invalid",
                    )
                )
            else:
                source = {"infrared_left": "infrared_1", "infrared_right": "infrared_2"}.get(
                    name, name
                )
                spec = info.streams[source].spec
                descriptions.append(
                    spec.fields[0].model_copy(
                        update={
                            "name": name,
                            "camera": spec.camera,
                            "optical_frame": source,
                            "source": "hardware",
                        }
                    )
                )
        descriptions += [
            Field(name=f"{n}_sequence", dtype="uint64", shape=(), unit="1") for n in self.names
        ]
        descriptions += [
            Field(name=f"{n}_time_ns", dtype="int64", shape=(), unit="ns") for n in self.names
        ]
        descriptions += [
            Field(name=n, dtype="float64", shape=(), unit="ms")
            for n in ("inference_ms", "latency_ms")
        ]
        descriptions += [
            Field(name=n, dtype="uint64", shape=(), unit="1")
            for n in ("discarded_frames", "rejected_frames")
        ]
        stream = Stream(
            RecordStream(
                role=Role.OBSERVATION,
                time_meaning=TimeMeaning.ACQUISITION,
                counter=Counter.DEVICE,
                nominal_hz=None,
                fields=tuple(descriptions),
                camera=color,
                description="Synchronized frameset; each image declares its optical coordinates",
            ),
            poll_hz=fps * 4,
        )
        self.info = replace(
            info,
            streams={**(info.streams if legacy_topics else {}), "frame": stream},
            transforms=(
                *info.transforms,
                Transform(
                    parent="color",
                    child="frame",
                    translation=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    source="frame projected to color optical coordinates",
                ),
            ),
            device={
                **info.device,
                "depth_backend": factory,
                "frame_fields": list(self.outputs),
                "legacy_topics": legacy_topics,
                "frame_tolerance_ms": self.tolerance_ns / 1e6,
                "depth_backend_config": self.backend.metadata if self.backend else {},
            },
        )
        self.thread = threading.Thread(target=self._work, name="frame", daemon=True)
        self.thread.start()

    def offer(self, readings: Mapping[str, Reading]) -> None:
        if not all(n in readings for n in self.names):
            return
        sample = [readings[n] for n in self.names]
        sequence = sample[0].sequence
        if sequence is None or any(s.sequence is None for s in sample):
            self.rejected += 1
            return
        if sequence <= self.last_sequence:
            return
        self.last_sequence = sequence
        if (
            any(
                s.sequence is None or s.sequence <= self.last_counters.get(n, -1)
                for n, s in zip(self.names, sample, strict=True)
            )
            or (
                "infrared_1" in self.names
                and "infrared_2" in self.names
                and readings["infrared_1"].sequence != readings["infrared_2"].sequence
            )
            or max(s.time_ns for s in sample) - min(s.time_ns for s in sample) > self.tolerance_ns
        ):
            self.rejected += 1
            return
        self.last_counters = {
            n: s.sequence for n, s in zip(self.names, sample, strict=True) if s.sequence is not None
        }
        with self.condition:
            if self.pending is not None:
                self.discarded += 1
            self.pending = sample
            self.condition.notify()

    def _work(self) -> None:
        try:
            while not self.stop.is_set():
                with self.condition:
                    self.condition.wait_for(
                        lambda: self.pending is not None or self.stop.is_set(), timeout=1
                    )
                    if self.stop.is_set():
                        break
                    sample, self.pending = self.pending, None
                if sample is None:
                    continue
                inputs = dict(zip(self.names, sample, strict=True))
                pending = (
                    self.backend.submit(
                        np.asarray(inputs["infrared_1"].values[0]),
                        np.asarray(inputs["infrared_2"].values[0]),
                    )
                    if self.backend
                    else None
                )
                output = {"color": inputs["color"].values[0]}
                if "depth_hardware" in self.outputs:
                    output["depth_hardware"] = self.raw_project(
                        np.asarray(inputs["depth"].values[0], dtype=np.float32) * self.raw_scale
                    )
                ms = 0.0
                if pending:
                    depth, ms = pending()
                    output["depth_ffs"] = self.project(depth)
                for name, source in (
                    ("infrared_left", "infrared_1"),
                    ("infrared_right", "infrared_2"),
                ):
                    if name in self.outputs:
                        output[name] = inputs[source].values[0]
                latency = (self.clock.now_ns() - sample[0].time_ns) / 1e6
                values = [output[n] for n in self.outputs]
                values += [np.asarray(s.sequence, dtype=np.uint64) for s in sample]
                values += [np.asarray(s.time_ns, dtype=np.int64) for s in sample]
                values += [
                    np.asarray(ms),
                    np.asarray(latency),
                    np.asarray(self.discarded, dtype=np.uint64),
                    np.asarray(self.rejected, dtype=np.uint64),
                ]
                reading = Reading(sample[0].time_ns, tuple(values), sequence=sample[0].sequence)
                assert reading.sequence is not None
                self.publish("frame", reading.sequence, lambda reading=reading: reading)
                self.last_result = time.monotonic()
                self.ready.set()
        except Exception as error:  # noqa: BLE001 - forwarded to DeviceHost.read
            self.failure = error
            self.ready.set()

    def check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if time.monotonic() - self.last_result > 15:
            raise TimeoutError("frame observation stalled; no source fallback")

    def close(self) -> None:
        self.stop.set()
        with self.condition:
            self.condition.notify()
        self.thread.join(timeout=16)
        if self.backend:
            self.backend.close()
        if self.thread.is_alive():
            raise TimeoutError("frame worker failed to stop")
