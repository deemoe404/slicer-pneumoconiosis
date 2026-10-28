"""Prepare pinned official ExtraMarkups binaries inside the project or offline payload."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging/extra-markups.json"


def prepare(destination=None, platform_key=None):
    if platform_key is None:
        os_name = {"Darwin": "macosx", "Windows": "win"}.get(platform.system())
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        platform_key = f"{os_name}-{arch}"
    assets = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if platform_key not in assets:
        raise RuntimeError("No pinned ExtraMarkups package for " + platform_key)
    asset = assets[platform_key]
    destination = Path(destination or ROOT / "local-data/extensions/ExtraMarkups")
    stamp = destination / "package.json"
    if stamp.exists():
        if json.loads(stamp.read_text(encoding="utf-8")) != asset:
            raise RuntimeError("Existing ExtraMarkups package differs from the pinned version: " + str(destination))
        return destination
    if destination.exists():
        raise RuntimeError("Refusing to overwrite an incomplete extension directory: " + str(destination))
    cache = ROOT / "local-data/extension-downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset["filename"]
    if not archive.exists() or hashlib.sha512(archive.read_bytes()).hexdigest() != asset["sha512"]:
        response = requests.get(asset["url"], timeout=(30, 120))
        response.raise_for_status()
        if hashlib.sha512(response.content).hexdigest() != asset["sha512"]:
            raise ValueError("ExtraMarkups download checksum mismatch")
        archive.write_bytes(response.content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(temporary)
        else:
            with tarfile.open(archive) as package:
                package.extractall(temporary, filter="data")
        # Official macOS archives contain a Slicer.app prefix; Windows archives do not.
        modules = list(Path(temporary).rglob("qt-loadable-modules"))
        if len(modules) != 1 or not any(modules[0].glob("*qSlicerShapeModule.*")):
            raise ValueError("Official archive does not contain the Shape module")
        payload = modules[0].parents[2]
        shutil.copy2(ROOT / "packaging/licenses/ExtraMarkups-LICENSE.txt", payload / "LICENSE.txt")
        (payload / "package.json").write_text(json.dumps(asset, indent=2) + "\n", encoding="utf-8")
        shutil.move(str(payload), str(destination))
    return destination


if __name__ == "__main__":
    print(prepare())
