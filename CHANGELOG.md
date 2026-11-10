# Changelog

## Unreleased

- Frames are stamped with the camera's hardware clock, mapped to the host's monotonic clock by
  xpisode-device's `DeviceClock`; librealsense's global time (mapped to the wall clock) is off.
- Infrared is recorded as lossless FFV1 gray instead of zstd.

## 0.1.0

- D405 capture with synchronized, configurable color, depth and infrared fields.
- Optional inferred-depth backend interface and metadata-driven visualization.
- Exposure timestamps, counter validation and bounded frame processing.
- Standalone installation, wheel/source distributions and station setup tutorial.
