"""Where a frame lands on the host clock: no camera needed."""

import pytest

from xpisode_realsense.camera import host_ns, wall_minus_clock_ns


def test_frames_are_stamped_with_global_time_on_the_host_clock() -> None:
    # 1000.000 ms of wall time; the host clock reads 40 s less than the wall clock.
    assert host_ns(1000.0, 40_000_000_000) == 1_000_000_000 - 40_000_000_000


def test_sub_millisecond_global_time_is_kept() -> None:
    assert host_ns(1.25, 0) == 1_250_000


class SteppingClock:
    """A host clock whose reads are given: a long pause stands for a thread switch."""

    def __init__(self, reads: list[int]) -> None:
        self.reads = reads

    def now_ns(self) -> int:
        return self.reads.pop(0)


def test_the_offset_comes_from_the_closest_clock_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    walls = iter([1_000_000_000, 2_000_000_000, 3_000_000_000])
    monkeypatch.setattr("xpisode_realsense.camera.time.time_ns", lambda: next(walls))
    # The first reading was split by a 6 ms pause; the second was 2 us apart.
    clock = SteppingClock(
        [0, 6_000_000, 1_000_000_000, 1_000_002_000, 2_000_000_000, 2_000_010_000]
    )
    assert wall_minus_clock_ns(clock) == 2_000_000_000 - 1_000_001_000
