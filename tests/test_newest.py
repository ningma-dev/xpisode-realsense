"""Which frames the host gets: no camera needed."""

import numpy as np
from xpisode_device import Reading

from xpisode_realsense import Newest


def reading(number: int) -> Reading:
    return Reading(number * 33_000_000, (np.zeros(1),), sequence=number)


def test_the_host_gets_each_frame_once_and_the_newest() -> None:
    newest = Newest()
    newest.offer("depth", 7, lambda: reading(7))
    newest.offer("depth", 8, lambda: reading(8))  # read too late: frame 7 is missed
    taken = newest.take("depth")
    assert taken is not None
    assert taken.sequence == 8
    assert newest.take("depth") is None


def test_a_frame_offered_again_is_ignored_without_building_it() -> None:
    newest = Newest()
    newest.offer("depth", 8, lambda: reading(8))
    assert newest.take("depth") is not None

    def must_not_build() -> Reading:
        raise AssertionError("a repeated frame was built")

    newest.offer("depth", 8, must_not_build)  # the syncer repeats a stream's last frame
    newest.offer("depth", 6, must_not_build)
    assert newest.take("depth") is None


def test_streams_are_apart() -> None:
    newest = Newest()
    newest.offer("color", 30, lambda: reading(30))
    newest.offer("depth", 44, lambda: reading(44))
    color, depth = newest.take("color"), newest.take("depth")
    assert color is not None
    assert depth is not None
    assert (color.sequence, depth.sequence) == (30, 44)
