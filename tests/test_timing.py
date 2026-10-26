"""Where a frame lands on the host clock: no camera needed."""

from xpisode_realsense.camera import mid_exposure_ns


def test_frames_are_stamped_at_the_middle_of_the_exposure() -> None:
    # A 32 ms exposure ending at the frame timestamp, 1000.000 ms of wall time; the host
    # clock reads 40 s less than the wall clock.
    stamped = mid_exposure_ns(1000.0, 118_479_253, 118_463_264, 40_000_000_000)
    assert stamped == 1_000_000_000 - 15_989_000 - 40_000_000_000


def test_sub_millisecond_global_time_is_kept() -> None:
    assert mid_exposure_ns(1.25, 0, 0, 0) == 1_250_000
