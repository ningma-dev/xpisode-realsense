# Install a station

The adapter is public. The xpisode core and its SDKs are currently private and are not available on PyPI. To run a station, obtain either access to `ningma-dev/xpisode` or compatible core wheel files from the maintainer. Cloning this adapter alone does not provide the core.

Use Linux x86_64, Python 3.12, Git and uv.

Create a workspace for the packages and your station:

```bash
mkdir -p xpisode-workspace/wheels
cd xpisode-workspace
```

## Obtain the core packages

**With supplied wheels:** put the `0.1.0` wheels for `xpisode`, `xpisode-device`, `xpisode-format` and `xpisode-env` into `wheels/`, then continue to **Build the adapter and create a station** below. The `xpisode` wheel must include the built web interface and match your Python version and platform. This route needs no core repository login.

**With source access:** authenticate Git for your GitHub account before cloning the private repository. You also need a C/C++ compiler, libclang and Rust with Cargo. Build the core and its web interface:

```bash
git clone https://github.com/ningma-dev/xpisode.git
cd xpisode
uv sync --locked
cd frontend
uv run npm ci --no-audit --no-fund
uv run npm run build
cd ..
uv build --all-packages --out-dir ../wheels
cd ..
```

## Build the adapter and create a station

Build the adapter, then create a station using its supplied project template:

```bash
git clone https://github.com/ningma-dev/xpisode-realsense.git
cd xpisode-realsense
uv build --out-dir ../wheels
cd ..
cp -r xpisode-realsense/examples/station_project my-station
mkdir -p my-station/wheels my-station/stations/d405
cp wheels/*.whl my-station/wheels/
cp xpisode-realsense/examples/*.toml my-station/stations/d405/
cd my-station
uv sync --python 3.12
uv run xpisode --help
```

The template installs all xpisode packages from `wheels/`. The RealSense SDK and OpenCV are installed from PyPI; no inference model is needed for hardware depth.
The generated `uv.lock` records the installation. Keep it with the project when deploying another station. Build wheels on a system compatible with the destination; a locally built Linux wheel is not a manylinux release wheel.

Replace `YOUR_CAMERA_SERIAL` in `stations/d405/d405_station.toml` with your camera serial, then run `uv run xpisode run stations/d405/d405_station.toml`. The [recording walkthrough](../README.md#install-and-record) covers capture and shutdown.

Recordings go into `my-station/data/<station>/episodes/`. To inspect them, run `uv run xpisode serve` in this project and open the URL printed by the command. Stop the server with Ctrl+C when finished.

For annotation, review and dataset export, core repository collaborators can follow the [xpisode collection tutorial](https://github.com/ningma-dev/xpisode/blob/main/docs/tutorials/collection.md).
