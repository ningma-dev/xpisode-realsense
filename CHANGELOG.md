# Changelog

## Unreleased

- A frameset with a frame not in the hardware clock, or with one stamp far off the mapping, is
  dropped and counted instead of faulting the camera; the counts, the mapping's and its clock
  reader's facts go out every 10 s as a `device.diagnostics` event.
- Frames are stamped with the camera's hardware clock, mapped to the host's monotonic clock by
  xpisode-device's `DeviceClock`; librealsense's global time (mapped to the wall clock) is off.
- Infrared is recorded as lossless FFV1 gray instead of zstd.
- librealsense puts framesets into a queue of its own (`pipeline.start(config, queue=...)`, by
  keyword: by position pyrealsense2 takes the queue as a Python callback) and a thread here takes
  them, so librealsense's thread never waits for the interpreter.
- `device.diagnostics` says where frames missing from an Episode went: `skipped` (color frame
  numbers librealsense never handed over), `drops` (frame sets dropped here), `replaced` (read
  too late by the host), and `queue_max`, the most frame sets that waited in the queue.
- The `frame` stream declares its rate (the configured fps), so a station can list it in
  `required_streams`.

## 0.1.0

- D405 capture with synchronized, configurable color, depth and infrared fields.
- Optional inferred-depth backend interface and metadata-driven visualization.
- Exposure timestamps, counter validation and bounded frame processing.
- Standalone installation, wheel/source distributions and station setup tutorial.
