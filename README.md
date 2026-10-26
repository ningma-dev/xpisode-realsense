# xpisode-realsense

Record RealSense D405 cameras with [xpisode](https://github.com/ningma-dev/xpisode). Each camera publishes one synchronized `frame` observation containing color and the selected depth or infrared fields. A consumer reads one observation without matching separate image topics.

## Requirements

- A RealSense D405 connected through USB 3.
- Linux with permission to access the camera and a librealsense backend that provides global timestamps and exposure metadata.
- Python 3.10 or later for the adapter; Python 3.12 for a complete xpisode station.

D405 is the supported model. Other RealSense models are rejected during opening. The driver has been exercised with librealsense 2.59.1 and D405 firmware 5.15.1.55. Install the [librealsense device permissions](https://github.com/realsenseai/librealsense/blob/master/doc/distribution_linux.md) appropriate to your system if the SDK cannot open the camera.

## Install and record

Running a station requires access to the private xpisode core repository or core wheel files supplied by the maintainer. The adapter is public; the core and its SDKs are not on PyPI.

Follow [Install a station](docs/installation.md) to build the core and adapter and create a station project. List connected camera serial numbers from that project:

```bash
uv run python -c 'import pyrealsense2 as rs; print("\n".join(d.get_info(rs.camera_info.serial_number) for d in rs.context().query_devices()))'
```

Replace `YOUR_CAMERA_SERIAL` in `stations/d405/d405_station.toml` with the chosen serial, then start the station:

```bash
uv run xpisode run stations/d405/d405_station.toml
```

In another terminal in the same project:

```bash
uv run xpisode status --station d405
uv run xpisode record start --station d405 --task demo
# Collect the sequence, then:
uv run xpisode record stop --station d405
uv run xpisode shutdown --station d405
uv run xpisode verify data/d405/episodes
```

To review recordings, run `uv run xpisode serve`, open **Episodes**, and select the image or depth fields to display. The viewer uses recorded field metadata; the user chooses which depth produces a point cloud.

## Select fields

The factory is `xpisode_realsense:D405`. Example device options:

```json
{
  "serial": "YOUR_CAMERA_SERIAL",
  "width": 640,
  "height": 480,
  "fps": 30,
  "frame_fields": ["color", "depth_hardware", "infrared_left", "infrared_right"]
}
```

| Field | Contents |
| --- | --- |
| `color` | RGB image; required |
| `depth_hardware` | Camera depth projected into color coordinates, float32 optical Z in metres; 0 is invalid |
| `depth_ffs` | Optional backend depth in the same color coordinates and units |
| `infrared_left`, `infrared_right` | Original monochrome images with their own camera calibration |

Each optional field can be enabled independently. Without `frame_fields`, the default is color and hardware depth; if a depth backend is configured, the default is color and inferred depth. Captured inputs are derived from the requested outputs. For example, inference captures both infrared inputs even when they are not recorded.

| Option | Default | Meaning |
| --- | --- | --- |
| `serial` | required | Explicit device identity; cameras are not chosen by discovery order |
| `width`, `height` | 848, 480 | Capture resolution shared by all inputs |
| `fps` | 30 | Requested camera rate; delivered frames can have gaps |
| `frame_fields` | as above | Fields included in each observation |
| `depth_backend` | hardware | Optional inference plugin and its options |
| `legacy_topics` | `false` | Also publish independent image topics for compatibility |
| `streams` | `["color", "depth"]` | Independent outputs when compatibility topics are enabled |

The station example uses 640×480 at 30 Hz. Choose a resolution/rate combination supported by the camera and USB link. With several cameras, add a device entry for each serial. Opening a camera in another process at the same time can fail with a USB or `VIDIOC_S_FMT` error; stop the previous owner before retrying.

## Timing and data representation

Each observation comes from a single SDK frameset. Accepted inputs have exposure timestamps within 5 ms, increasing counters, and matching left/right infrared counters. Color and depth counters may have independent origins. The observation timestamp is the middle of the color exposure, expressed on the host monotonic clock; its sequence is the color counter.

This aligns fields within one camera. It does not provide hardware-trigger synchronization between different cameras. A bounded pending frame prevents an inference backlog; dropped frames remain visible in sequence gaps. A backend failure faults the device instead of silently substituting another depth source.

Both depth fields are aligned metric depth, so `depth_hardware` is not a bit-exact copy of the original uint16 sensor grid. Enable the independent `depth` topic explicitly when that representation is required. Compatibility topics duplicate publication and storage; they are off by default.

Every image declares its intrinsics, optical frame and provenance. Color uses lossless FFV1; aligned depth and infrared use zstd. Source counters, exposure times, latency and discard diagnostics are included in `frame`.

## Optional inferred depth

An inference plugin is installed separately and selected with `depth_backend.factory`. This package contains no neural model or PyTorch dependency; ordinary hardware RGB-D needs neither. See the [backend interface](docs/depth-backends.md) for the plugin contract and configuration. The `depth_ffs` field requires an installed plugin; enabling the field alone does not download a model.

## Develop and test

The locked development environment fetches the xpisode SDKs from the private core repository. Authenticate Git with an account that has access before syncing.

```bash
uv sync --python 3.12
uv run pytest                 # geometry, timing and configuration; no camera
uv run pytest -m hardware     # opens a connected D405, then closes it
```

For the hardware suite, ensure no station or viewer process owns the camera. See [Contributing](CONTRIBUTING.md) for all checks and packaging.

MIT licensed. librealsense and optional inference backends have their own licenses.
