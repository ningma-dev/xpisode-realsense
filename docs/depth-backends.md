# Add an inferred-depth backend

The adapter owns the RealSense pipeline, input timing checks, depth projection and publication. A backend receives rectified stereo infrared frames and returns optical Z in metres. It does not open the camera or publish a separate stream.

Install the backend and its model dependencies in the same environment as the camera process. Set `depth_backend.factory` to its importable `module:Class` and pass backend-specific keyword arguments in `options`:

```json
{
  "serial": "YOUR_CAMERA_SERIAL",
  "frame_fields": ["color", "depth_hardware", "depth_ffs"],
  "depth_backend": {
    "factory": "your_depth_package:Backend",
    "options": {}
  }
}
```

The class is constructed as `Backend(capture_info, **options)`, where `capture_info` is an `xpisode_device.DeviceInfo` describing the captured streams and transforms. It exposes:

| Member | Contract |
| --- | --- |
| `camera` | `xpisode_format.spec.Camera` describing the returned depth grid |
| `color_from_depth` | 4×4 transform mapping depth-camera coordinates to color-camera coordinates |
| `metadata` | JSON-compatible dictionary recorded in the device description |
| `submit(left, right)` | Accept two monochrome NumPy images from one accepted frameset; return a callable |
| returned callable | Wait for that pair's result and return `(metric_z, inference_ms)` |
| `close()` | Release inference resources and stop background work |

`metric_z` has shape `(camera.height, camera.width)`, contains optical Z in metres, and uses zero or non-finite values for invalid pixels. The driver projects it to color coordinates with nearest-surface occlusion handling and publishes it together with the original color frame.

The backend must finish or raise within the driver's shutdown timeout. Multi-camera backends can share a model or batch requests internally, but each result must remain attached to its submitted pair. Record model identity and relevant inference settings in `metadata`.

`depth_ffs` is the current inferred-depth field name. Consumers should read its metadata rather than assume a model or select it automatically. FFS integrations must be obtained from the project distributing the xpisode-compatible backend; installing the upstream model repository alone does not install this interface.
