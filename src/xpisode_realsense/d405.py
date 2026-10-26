"""The RealSense D405: the one model this package drives.

Verified on D405s with firmware 5.15.1.55 and librealsense 2.59.1 (pyrealsense2 from PyPI),
over USB 3.2, on Ubuntu 22.04 with its stock kernel.
Its stereo module delivers every stream: color from the left imager, depth, and both imagers
in monochrome; its frames carry global time and the metadata that places the middle of each
exposure. Over a USB 2 link it offers 848x480 at no more than 10 fps.

Run it in its own process with xpisode's generic host::

    xpisode-device run --station lab --name wrist_camera \\
        --factory xpisode_realsense:D405 \\
        --config '{"serial": "YOUR_CAMERA_SERIAL", "frame_fields": ["color", "depth_hardware"]}'
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from xpisode_format.model import JsonValue
from xpisode_format.spec import Encoding

from xpisode_realsense.camera import Kind, RealSense, rs

STREAMS: dict[str, Kind] = {
    # Lossless, every frame alone, about half of QOI.
    "color": Kind(
        rs.stream.color, 0, rs.format.rgb8, "uint8", ("r", "g", "b"), "1", Encoding.FFV1, "color"
    ),
    "depth": Kind(
        rs.stream.depth,
        0,
        rs.format.z16,
        "uint16",
        (),
        "m",
        Encoding.ZSTD,
        "0 where the camera has no depth",
    ),
    "infrared_1": Kind(
        rs.stream.infrared,
        1,
        rs.format.y8,
        "uint8",
        (),
        "1",
        Encoding.ZSTD,
        "the left imager, monochrome",
    ),
    "infrared_2": Kind(
        rs.stream.infrared,
        2,
        rs.format.y8,
        "uint8",
        (),
        "1",
        Encoding.ZSTD,
        "the right imager, monochrome",
    ),
}
"""Capture streams offered by the D405, with lossless per-image encodings."""


class D405(RealSense):
    """A RealSense D405; 848x480 at 30 fps, color and depth, unless configured otherwise. The
    serial has no default: with several cameras, a forgotten one would open another camera."""

    driver = "realsense.d405"
    product_id = "0B5B"
    offers = STREAMS

    def __init__(
        self,
        *,
        serial: str,
        streams: Sequence[str] = ("color", "depth"),
        width: int = 848,
        height: int = 480,
        fps: int = 30,
        depth_backend: dict[str, Any] | None = None,
        frame_fields: Sequence[str] | None = None,
        legacy_topics: bool = False,
    ) -> None:
        super().__init__(
            serial=serial,
            streams=streams,
            width=width,
            height=height,
            fps=fps,
            depth_backend=depth_backend,
            frame_fields=frame_fields,
            legacy_topics=legacy_topics,
        )

    def identity(self, device: Any) -> dict[str, JsonValue]:
        """The USB link it came up on: a D405 on USB 2 offers less."""
        return {"usb": device.get_info(rs.camera_info.usb_type_descriptor)}
