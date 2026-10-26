"""Validate version, local documentation links, and release archives."""

import json
import os
import re
import tarfile
import zipfile
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "xpisode-realsense"
MODULE = NAME.replace("-", "_")
VERSION = version(NAME)


def main() -> None:
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        assert os.environ["GITHUB_REF_NAME"] == f"v{VERSION}", "tag/version mismatch"
    for path in [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md")]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" not in target and not target.startswith("#"):
                assert (path.parent / target.split("#", 1)[0]).exists(), (path, target)
    wheels = list((ROOT / "dist").glob(f"{MODULE}-{VERSION}-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        assert f"{MODULE}/__init__.py" in archive.namelist()
        assert f"{MODULE}/py.typed" in archive.namelist()
        metadata = archive.read(f"{MODULE}-{VERSION}.dist-info/METADATA").decode()
        assert f"Version: {VERSION}\n" in metadata
        assert "file:///" not in metadata
        assert "0.1.0.dev0" not in metadata
        assert any(n.endswith("/LICENSE") for n in archive.namelist())
    source = ROOT / "dist" / f"{MODULE}-{VERSION}.tar.gz"
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for path in ("README.md", "LICENSE", "examples/station_project/pyproject.toml"):
            assert any(n.endswith("/" + path) for n in names), path
    print(
        json.dumps(
            {
                "package": NAME,
                "version": VERSION,
                "wheel": wheels[0].name,
                "sdist": source.name,
                "status": "verified",
            }
        )
    )


if __name__ == "__main__":
    main()
