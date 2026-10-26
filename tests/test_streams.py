"""The configuration chooses the streams: no camera needed."""

from __future__ import annotations

import pytest

from xpisode_realsense import D405
from xpisode_realsense.d405 import STREAMS


def test_a_stream_the_driver_does_not_offer_is_refused_with_what_it_offers() -> None:
    with pytest.raises(ValueError, match="choose from"):
        D405(serial="0", streams=["color", "thermal"])
    with pytest.raises(ValueError, match="choose from"):
        D405(serial="0", streams=[])


def test_streams_are_kept_in_order_once_each() -> None:
    camera = D405(serial="0", streams=["depth", "color", "depth"], legacy_topics=True)
    assert camera.streams == ("depth", "color")
    assert set(STREAMS) == {"color", "depth", "infrared_1", "infrared_2"}


def test_a_camera_librealsense_reports_removed_fails_its_next_read() -> None:
    camera = D405(serial="0")
    # As open leaves it, and as librealsense's callback marks a removed camera.
    camera._pipeline = object()  # pyright: ignore[reportPrivateUsage]
    camera._removal = "librealsense reports camera 123 removed"  # pyright: ignore[reportPrivateUsage]
    camera._removed.set()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(RuntimeError, match="librealsense reports camera 123 removed"):
        camera.read("color")


@pytest.mark.parametrize(
    ("fields", "inputs"),
    [
        (["color"], ("color",)),
        (["color", "depth_hardware"], ("color", "depth")),
        (["color", "infrared_right"], ("color", "infrared_2")),
        (["color", "depth_ffs"], ("color", "infrared_1", "infrared_2")),
    ],
)
def test_frame_outputs_control_capture_without_publishing_inputs(
    fields: list[str], inputs: tuple[str, ...]
) -> None:
    camera = D405(serial="0", frame_fields=fields, depth_backend={"factory": "plugin:Backend"})
    assert camera.streams == inputs
    assert not camera.legacy_topics
    assert camera.frame_fields == tuple(fields)


@pytest.mark.parametrize(
    "fields", [[], ["depth_hardware"], ["color", "color"], ["color", "unknown"]]
)
def test_invalid_frame_fields_fail_before_open(fields: list[str]) -> None:
    with pytest.raises(ValueError, match="frame_fields"):
        D405(serial="0", frame_fields=fields)


def test_inferred_depth_requires_backend():
    with pytest.raises(ValueError, match="requires"):
        D405(serial="0", frame_fields=["color", "depth_ffs"])
