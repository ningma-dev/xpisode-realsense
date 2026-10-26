"""librealsense's intrinsics and extrinsics as the format's: no camera needed."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt
import pytest

from xpisode_realsense.camera import (
    PLUMB_BOB_TOLERANCE_PX,
    camera_of,
    plumb_bob_error_px,
    quaternion,
    transform_of,
)

COEFFS = [-0.0512, 0.0522, -0.0004, 0.0002, -0.0176]


def intrinsics(model: str) -> SimpleNamespace:
    """As librealsense 2.59.1 reports a D405's."""
    return SimpleNamespace(
        width=640,
        height=480,
        fx=393.14,
        fy=392.18,
        ppx=317.29,
        ppy=238.50,
        model=f"distortion.{model}",
        coeffs=COEFFS,
    )


def test_brown_conrady_is_plumb_bob() -> None:
    camera = camera_of(intrinsics("brown_conrady"))
    assert (camera.distortion_model, camera.distortion) == ("plumb_bob", tuple(COEFFS))
    assert (camera.width, camera.height, camera.cx, camera.cy) == (640, 480, 317.29, 238.50)


def test_inverse_brown_conrady_close_to_opencv_is_plumb_bob() -> None:
    # A D405's color coefficients: OpenCV's model is about 0.01 pixel from librealsense's.
    assert plumb_bob_error_px(intrinsics("inverse_brown_conrady")) < PLUMB_BOB_TOLERANCE_PX
    camera = camera_of(intrinsics("inverse_brown_conrady"))
    assert (camera.distortion_model, camera.distortion) == ("plumb_bob", tuple(COEFFS))


def test_inverse_brown_conrady_farther_off_keeps_its_name() -> None:
    far = intrinsics("inverse_brown_conrady")
    far.coeffs = [-0.3, 0.1, 0.01, 0.01, 0.0]
    assert plumb_bob_error_px(far) > PLUMB_BOB_TOLERANCE_PX
    assert camera_of(far).distortion_model == "realsense.inverse_brown_conrady"


def test_other_models_keep_librealsense_names() -> None:
    assert camera_of(intrinsics("ftheta")).distortion_model == "realsense.ftheta"


def test_no_distortion_has_no_coefficients() -> None:
    assert camera_of(intrinsics("none")).distortion == ()


def column_major(matrix: npt.NDArray[np.float64]) -> list[float]:
    return [float(v) for v in matrix.T.reshape(-1)]


def rotation(q: tuple[float, float, float, float]) -> npt.NDArray[np.float64]:
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


@pytest.mark.parametrize(
    "matrix",
    [
        np.eye(3),
        np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),  # 90 degrees about z
        np.diag([1.0, -1.0, -1.0]),  # 180 degrees about x: looking down
        np.diag([-1.0, 1.0, -1.0]),
        np.diag([-1.0, -1.0, 1.0]),
    ],
)
def test_quaternions_are_the_rotation(matrix: npt.NDArray[np.float64]) -> None:
    q = quaternion(column_major(matrix))
    assert math.hypot(*q) == pytest.approx(1)
    np.testing.assert_allclose(rotation(q), matrix, atol=1e-12)


def test_a_turn_about_z_reads_column_major() -> None:
    turn = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    q = quaternion(column_major(turn))
    assert q == pytest.approx((0, 0, math.sqrt(0.5), math.sqrt(0.5)))  # not the inverse turn


def test_extrinsics_become_the_child_relative_to_the_parent() -> None:
    extrinsics = SimpleNamespace(rotation=column_major(np.eye(3)), translation=[-1e-5, 1e-5, 0.0])
    t = transform_of(extrinsics, "color", "depth", "factory calibration")
    assert (t.parent, t.child, t.translation) == ("color", "depth", (-1e-5, 1e-5, 0.0))
    assert t.rotation == (0.0, 0.0, 0.0, 1.0)
