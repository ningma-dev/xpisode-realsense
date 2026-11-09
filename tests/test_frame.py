"""Geometry and atomic frame acceptance, without emulating camera hardware."""

import threading
import time
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from xpisode_device import Reading
from xpisode_format.spec import Camera

from xpisode_realsense.frame import STALL_S, Drops, Frame, Projector


def test_projection_identity_and_nearest_surface():
    camera = Camera(
        width=4, height=3, fx=2.0, fy=2.0, cx=1.5, cy=1.0, distortion_model="none", distortion=()
    )
    project = Projector(camera, camera, np.eye(4))
    depth = np.full((3, 4), 0.2375, np.float32)
    np.testing.assert_allclose(project(depth), depth)
    depth[0, 0] = 0
    depth[1, 1] = np.nan
    result = project(depth)
    assert result[0, 0] == 0
    assert result[1, 1] == 0
    assert np.all(np.isfinite(result))


def test_rgbd_rejects_mismatched_frame_and_exposure_and_bounds_pending():
    state = SimpleNamespace(
        names=("color", "depth"),
        last_sequence=-1,
        last_counters={},
        rejected=0,
        discarded=0,
        pending=None,
        condition=threading.Condition(),
        drops=Drops(),
    )

    def offer(sequence: int, depth_sequence: int, skew: int = 0) -> None:
        Frame.offer(
            cast(Frame, state),
            {
                "color": Reading(10_000_000, (), sequence),
                "depth": Reading(10_000_000 + skew, (), depth_sequence),
            },
        )

    offer(1, 20)
    assert state.pending[0].sequence == 1
    state.pending = None
    offer(2, 2, 6_000_000)
    assert state.pending is None
    assert state.rejected == 1
    offer(3, 21)
    offer(4, 22)
    assert state.pending is not None
    assert state.pending[0].sequence == 4
    assert state.discarded == 1
    offer(4, 22)
    assert state.discarded == 1
    Frame.offer(
        cast(Frame, state),
        {"color": Reading(10_000_000, (), 5), "depth": Reading(10_000_000, (), None)},
    )
    assert state.rejected == 2
    assert state.last_sequence == 4


def test_projection_occlusion_keeps_nearest_surface():
    source = Camera(
        width=4, height=3, fx=2.0, fy=2.0, cx=1.5, cy=1.0, distortion_model="none", distortion=()
    )
    target = Camera(
        width=1,
        height=1,
        fx=0.001,
        fy=0.001,
        cx=0.0,
        cy=0.0,
        distortion_model="none",
        distortion=(),
    )
    project = Projector(source, target, np.eye(4))
    depths = np.full((3, 4), 0.5, np.float32)
    depths[2, 3] = 0.2
    np.testing.assert_allclose(project(depths), [[0.2]])


def test_stereo_counters_must_match_but_color_has_its_own_origin():
    state = SimpleNamespace(
        names=("color", "depth", "infrared_1", "infrared_2"),
        last_sequence=-1,
        last_counters={},
        rejected=0,
        discarded=0,
        pending=None,
        condition=threading.Condition(),
        drops=Drops(),
    )
    values = {
        n: Reading(10_000_000, (), sequence)
        for n, sequence in [("color", 6), ("depth", 22), ("infrared_1", 21), ("infrared_2", 20)]
    }
    Frame.offer(cast(Frame, state), values)
    assert state.rejected == 1
    values = {
        n: Reading(43_333_333, (), sequence)
        for n, sequence in [("color", 7), ("depth", 23), ("infrared_1", 22), ("infrared_2", 22)]
    }
    Frame.offer(cast(Frame, state), values)
    assert state.pending[0].sequence == 7


def test_each_dropped_frame_set_is_counted_by_reason_and_reported_on_stall():
    state = SimpleNamespace(
        names=("color", "infrared_1", "infrared_2"),
        last_sequence=-1,
        last_counters={},
        rejected=0,
        discarded=0,
        pending=None,
        condition=threading.Condition(),
        drops=Drops(),
        last_result=0.0,
    )

    def offer(color: int, left: int, right: int, *, drop: str = "") -> None:
        readings = {
            "color": Reading(10_000_000, (), color),
            "infrared_1": Reading(10_000_000, (), left),
            "infrared_2": Reading(10_000_000, (), right),
        }
        if drop:
            del readings[drop]
        Frame.offer(cast(Frame, state), readings)

    offer(1, 10, 10)
    state.drops.frame_published()
    offer(2, 11, 11, drop="infrared_2")
    offer(1, 11, 11)
    offer(3, 10, 10)
    offer(4, 12, 13)
    drops = state.drops
    assert drops.offered == 5
    assert drops.accepted == 1
    assert drops.counts == {
        "incomplete": 1,
        "color_repeated": 1,
        "no_sequence": 0,
        "stream_repeated": 1,
        "infrared_mismatch": 1,
    }
    assert state.rejected == 2
    text = Frame.diagnosis(cast(Frame, state))
    assert "offered 5 frame sets" in text
    assert "accepted 1, published 1" in text
    assert (
        "dropped since the last frame: incomplete 1, color_repeated 1, stream_repeated 1, "
        "infrared_mismatch 1" in text
    )
    assert "last offered numbers: color 4, infrared_1 12, infrared_2 13" in text
    assert "last accepted numbers: color 1, infrared_1 10, infrared_2 10" in text


def test_frames_whose_exposures_differ_are_kept_as_librealsense_paired_them():
    """A D405's color frame can report a stale exposure; the SDK's pairing is what counts."""
    state = SimpleNamespace(
        names=("color", "infrared_1", "infrared_2"),
        last_sequence=-1,
        last_counters={},
        rejected=0,
        discarded=0,
        pending=None,
        condition=threading.Condition(),
        drops=Drops(),
    )
    Frame.offer(
        cast(Frame, state),
        {
            "color": Reading(10_000_000, (), 1),
            "infrared_1": Reading(25_740_000, (), 1),
            "infrared_2": Reading(25_740_000, (), 1),
        },
    )
    assert state.pending is not None
    assert state.rejected == 0


def test_a_stall_before_any_frame_set_says_librealsense_offered_none():
    state = SimpleNamespace(
        names=("color",),
        last_counters={},
        drops=Drops(),
        last_result=0.0,
    )
    text = Frame.diagnosis(cast(Frame, state))
    assert "librealsense offered no frame set" in text
    assert "dropped since the last frame: none" in text


def test_a_second_without_a_frame_faults_the_camera():
    state = SimpleNamespace(
        names=("color",),
        last_counters={},
        drops=Drops(),
        failure=None,
        last_result=time.monotonic(),
    )
    state.diagnosis = lambda: Frame.diagnosis(cast(Frame, state))
    Frame.check(cast(Frame, state))
    state.last_result -= STALL_S + 0.1
    with pytest.raises(TimeoutError, match=r"stalled.*1\.1 s since the last frame"):
        Frame.check(cast(Frame, state))
