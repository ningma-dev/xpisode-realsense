"""Geometry and atomic frame acceptance, without emulating camera hardware."""

import threading
from types import SimpleNamespace
from typing import cast

import numpy as np
from xpisode_device import Reading
from xpisode_format.spec import Camera

from xpisode_realsense.frame import Frame, Projector


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
        tolerance_ns=5_000_000,
        condition=threading.Condition(),
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
        tolerance_ns=5_000_000,
        condition=threading.Condition(),
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
